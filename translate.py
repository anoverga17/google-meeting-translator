"""Tails the caption transcript and translates new Japanese lines to English with Claude.

New lines are translated as soon as they land in the transcript (the extension
already waits for captions to settle). Lines that arrive while a translation is
streaming are sent together in the next request. Output streams to the terminal
and is appended to the translation file.

Usage:
    python translate.py                                  # transcript.txt -> translation.txt
    python translate.py --notes notes.txt                # add glossary / meeting background
    python translate.py --model claude-sonnet-5-5        # compare speed/quality
    python translate.py --backend claude-code            # use your Claude plan instead of an API key
"""

import argparse
import json
import subprocess
import sys
import tempfile
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

A line may end mid-sentence, before the verb. Translate what is actually there without \
guessing how the sentence ends (e.g. don't assume affirmative vs. negative); the rest \
will arrive in the next lines.

Output one line per caption line in <new_captions>, in order, keeping the \
"[HH:MM:SS] Speaker:" prefix exactly as given (leave speaker names as written). If a \
line only continues the previous one or is pure filler (e.g. えーと, あの), you may \
translate it briefly. Output only the translated lines - no XML tags, notes or commentary."""


def tagged(tag: str, lines) -> str:
    return f"<{tag}>\n" + "\n".join(lines) + f"\n</{tag}>"


def api_backend(model: str, effort: str, system: str, context_lines: int = 30):
    """Calls the Claude API directly (needs ANTHROPIC_API_KEY), with the last few lines as context."""
    client = anthropic.Anthropic()
    client.models.retrieve(model)  # fail on a bad key or model name now, not at the first caption
    captions, translations = deque(maxlen=context_lines), deque(maxlen=context_lines)

    def translate(lines: list[str]):
        parts = [tagged("new_captions", lines)]
        if captions:
            parts = [tagged("previous_captions", captions), tagged("previous_translation", translations)] + parts
        with client.beta.messages.stream(
            model=model,
            max_tokens=16000,
            system=system,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            print("\n[Claude declined to translate this batch; skipped]", file=sys.stderr, flush=True)
            return
        captions.extend(lines)
        translations.extend("".join(b.text for b in final.content if b.type == "text").strip().splitlines())

    return translate


def claude_code_backend(model: str, effort: str, system: str):
    """Translates through one long-running Claude Code session (uses your Claude plan, no API key).

    The session keeps the whole meeting as conversation history, so each turn
    only carries the new captions. Claude Code compacts the history itself if a
    meeting runs long.
    """
    proc = subprocess.Popen(
        [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", system,
            "--tools", "",
            "--model", model,
            "--effort", effort,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources", "",
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        cwd=tempfile.mkdtemp(prefix="meet-translate-"),  # outside the project, so no CLAUDE.md leaks in
    )

    def turn(content: str):
        message = {"type": "user", "message": {"role": "user", "content": content}}
        if proc.poll() is None:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        for raw in proc.stdout:
            event = json.loads(raw)
            if event.get("type") == "stream_event":
                delta = event["event"].get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta["text"]
            elif event.get("type") == "result":
                if event.get("is_error"):
                    raise RuntimeError(event.get("result") or event.get("subtype") or "unknown error")
                return
        sys.exit(f"Claude Code exited (code {proc.wait()})")

    # Start the session (and check the login and model name) before the first caption.
    list(turn("Setup check before the meeting starts. Reply with just: ready"))
    return lambda lines: turn(tagged("new_captions", lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", default="api", choices=["api", "claude-code"],
                        help="api: Claude API with ANTHROPIC_API_KEY; claude-code: your Claude plan via the claude CLI")
    parser.add_argument("--transcript", default="transcript.txt")
    parser.add_argument("--out", default="translation.txt")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"],
                        help="lower is faster; low is usually plenty for translation")
    parser.add_argument("--notes", help="file with meeting background, names, or a glossary")
    args = parser.parse_args()

    system = SYSTEM_PROMPT
    if args.notes:
        system += "\n\n" + tagged("meeting_notes", [Path(args.notes).read_text(encoding="utf-8").strip()])
    backend = api_backend if args.backend == "api" else claude_code_backend
    translate = backend(args.model, args.effort, system)

    Path(args.transcript).touch()
    print(f"Watching {args.transcript} -> {args.out} ({args.model})", flush=True)
    with open(args.transcript, encoding="utf-8") as transcript, open(args.out, "a", encoding="utf-8") as out:
        transcript.seek(0, 2)  # only translate what's said from now on
        lines, partial = [], ""
        while True:
            *new, partial = (partial + transcript.read()).split("\n")
            lines += [line for line in new if line.strip()]
            if not lines:
                time.sleep(0.1)
                continue
            english = ""
            try:
                for text in translate(lines):
                    print(text, end="", flush=True)
                    english += text
            except Exception as exc:
                # Retrying can't fix a bad request, key or model; anything else is assumed transient.
                if isinstance(exc, anthropic.APIStatusError) and exc.status_code < 500 and exc.status_code != 429:
                    raise
                print(f"\n[translate error, will retry: {exc}]", file=sys.stderr, flush=True)
                time.sleep(3)
                continue  # `lines` is kept, so the batch is retried along with anything new
            print(flush=True)
            if english.strip():
                out.write(english.strip() + "\n")
                out.flush()
            lines = []


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
