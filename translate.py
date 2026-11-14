"""Tails the caption transcript and translates new Japanese lines to English with Claude.

New lines are batched briefly (speakers pause, ASR splits sentences), sent to
Claude together with the last few translated lines as context, streamed to the
terminal, and appended to the translation file.

Usage:
    python translate.py                                  # transcript.txt -> translation.txt
    python translate.py --notes notes.txt                # add glossary / meeting background
    python translate.py --from-start                     # also translate what's already in the file
    python translate.py --model claude-haiku-5-5         # faster/cheaper
"""

import argparse
import sys
import time
from collections import deque
from pathlib import Path

import anthropic

SYSTEM_PROMPT = """\
You are a live interpreter for a Google Meet call held in Japanese. You receive \
automatic speech-recognition captions and translate them into natural, accurate English \
for an English speaker following along in real time.

The captions are noisy: expect missing punctuation, misrecognized words (often \
homophones), sentences split across lines, and occasionally repeated text where the \
recognizer revised itself. Use the earlier conversation to resolve ambiguity and fix \
obvious recognition errors, but don't invent content that isn't there. If text is \
duplicated from the previous lines, don't translate it twice.

Output one line per caption line in <new_captions>, in order, keeping the \
"[HH:MM:SS] Speaker:" prefix exactly as given (leave speaker names as written). If a \
line only continues the previous one or is pure filler (e.g. えーと, あの), you may \
translate it briefly. Output only the translated lines - no notes or commentary."""


class TranscriptTail:
    """Yields complete new lines appended to a file, tolerating truncation and rotation."""

    def __init__(self, path: Path, from_start: bool):
        self.path = path
        self.pos = 0 if from_start or not path.exists() else path.stat().st_size
        self.partial = ""

    def read_new_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.pos:  # file was truncated or replaced
            self.pos, self.partial = 0, ""
        if size == self.pos:
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            chunk = f.read()
            self.pos = f.tell()
        *lines, self.partial = (self.partial + chunk).split("\n")
        return [line for line in lines if line.strip()]


class Translator:
    def __init__(self, model: str, effort: str, notes: str | None, context_lines: int):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.system = SYSTEM_PROMPT
        if notes:
            self.system += f"\n\n<meeting_notes>\n{notes.strip()}\n</meeting_notes>"
        self.source_history: deque[str] = deque(maxlen=context_lines)
        self.english_history: deque[str] = deque(maxlen=context_lines)

    def _prompt(self, lines: list[str]) -> str:
        parts = []
        if self.source_history:
            parts.append("<previous_captions>\n" + "\n".join(self.source_history) + "\n</previous_captions>")
            parts.append("<previous_translation>\n" + "\n".join(self.english_history) + "\n</previous_translation>")
        parts.append("<new_captions>\n" + "\n".join(lines) + "\n</new_captions>")
        return "\n\n".join(parts)

    def translate(self, lines: list[str], on_text) -> str | None:
        """Streams the translation of `lines` through on_text; returns the full text, or None if refused."""
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=self.system,
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": self._prompt(lines)}],
        ) as stream:
            for text in stream.text_stream:
                on_text(text)
            final = stream.get_final_message()

        if final.stop_reason == "refusal":
            return None
        english = "".join(b.text for b in final.content if b.type == "text").strip()
        self.source_history.extend(lines)
        self.english_history.extend(english.splitlines())
        return english


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transcript", default="transcript.txt")
    parser.add_argument("--out", default="translation.txt")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"],
                        help="lower is faster; low is usually plenty for translation")
    parser.add_argument("--notes", help="file with meeting background, names, or a glossary")
    parser.add_argument("--from-start", action="store_true", help="translate lines already in the transcript")
    parser.add_argument("--debounce", type=float, default=1.5,
                        help="seconds of caption silence before translating a batch")
    parser.add_argument("--max-wait", type=float, default=6.0,
                        help="translate a batch after this many seconds even if captions keep coming")
    parser.add_argument("--context-lines", type=int, default=30)
    args = parser.parse_args()

    notes = Path(args.notes).read_text(encoding="utf-8") if args.notes else None
    tail = TranscriptTail(Path(args.transcript), args.from_start)
    translator = Translator(args.model, args.effort, notes, args.context_lines)
    out_path = Path(args.out)

    print(f"Watching {Path(args.transcript).resolve()} -> {out_path.resolve()} ({args.model})", flush=True)

    pending: list[str] = []
    first_at = last_at = 0.0
    while True:
        new = tail.read_new_lines()
        now = time.monotonic()
        if new:
            if not pending:
                first_at = now
            pending.extend(new)
            last_at = now

        if pending and (now - last_at >= args.debounce or now - first_at >= args.max_wait):
            batch, pending = pending, []
            try:
                english = translator.translate(batch, lambda t: print(t, end="", flush=True))
            except anthropic.AuthenticationError:
                sys.exit("Authentication failed: set ANTHROPIC_API_KEY (or run `ant auth login`).")
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
                print(f"\n[translate error, will retry: {exc}]", file=sys.stderr, flush=True)
                pending = batch + pending
                time.sleep(3)
                continue
            print(flush=True)

            if english is None:
                print("[Claude declined to translate this batch; skipped]", file=sys.stderr, flush=True)
                continue
            with out_path.open("a", encoding="utf-8") as f:
                f.write(english + "\n")

        time.sleep(0.25)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
