"""Tails the caption transcript and translates it from Japanese to English with Claude.

By default, every minute the new captions are turned into a readable English recap
(fragments merged, repeats dropped) and appended to recap.txt. With --recap 0, lines
are instead translated as soon as they land in the transcript and appended to
translation.txt; lines that arrive while a translation is streaming go in the next
request. Output also streams to the terminal.

Usage:
    python translate.py                                  # transcript.txt -> recap.txt every minute
    python translate.py --recap 0                        # live, line by line -> translation.txt
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

RECAP_PROMPT = """\
You help an English speaker follow a Google Meet call held in Japanese. They have a rough \
line-by-line live translation; every minute or so you receive the automatic \
speech-recognition captions since your last reply, and write the readable version they \
catch up from.

The captions are noisy: expect misrecognized words (often homophones), sentences split \
across lines, filler, and text repeated where the recognizer revised itself. Use the \
earlier conversation to resolve ambiguity and fix obvious recognition errors, but don't \
invent content that isn't there.

Reply in this format, with no XML tags or commentary:
[HH:MM:SS] Speaker: <what they said, in clean natural English>

Write one paragraph per speaker turn, using the timestamp of its first caption line. Merge \
fragments into whole sentences and drop fillers, false starts and repeats, but keep every \
point, question, decision, number and name - this is a cleaned-up translation, not a \
summary. If the captions end mid-sentence, leave that sentence out and begin your next \
reply with it."""

REPO_NOTE = """\
Documentation or code for the systems being discussed (names, components, terminology) \
is in the directory {repo}. Speech recognition often garbles system names, acronyms and technical \
terms: when the captions mention one you can't confidently identify, search that directory \
with Grep, Glob and Read to find the right term and understand what is being discussed. \
Keep it to a few targeted lookups per reply. Your reply must contain only the translation \
in the format above, with nothing about the lookups."""


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


def claude_code_backend(model: str, effort: str, system: str, repo: str | None = None):
    """Translates through one long-running Claude Code session (uses your Claude plan, no API key).

    The session keeps the whole meeting as conversation history, so each turn
    only carries the new captions. Claude Code compacts the history itself if a
    meeting runs long. With `repo`, Claude can search that directory (read-only)
    to identify system terms; only its final reply is shown, not the lookups.
    """
    lookup = "Read,Grep,Glob"
    tools = ["--tools", lookup, "--allowedTools", lookup, "--add-dir", repo] if repo else ["--tools", ""]
    proc = subprocess.Popen(
        [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", system,
            "--model", model,
            "--effort", effort,
            "--permission-mode", "dontAsk",  # never stop to ask; anything not allowed is denied
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources", "",
            *tools,
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
            if event.get("type") == "stream_event" and not repo:
                delta = event["event"].get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta["text"]
            elif event.get("type") == "result":
                if event.get("is_error"):
                    raise RuntimeError(event.get("result") or event.get("subtype") or "unknown error")
                if repo:  # streamed text would include any remarks made between lookups
                    yield event.get("result", "")
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
    parser.add_argument("--out", help="default: recap.txt, or translation.txt with --recap 0")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="lower is faster; default: medium for recaps, low for live")
    parser.add_argument("--notes", help="file with meeting background, names, or a glossary")
    parser.add_argument("--repo", help="your system's repo/docs, searched for system names and terms (claude-code only)")
    parser.add_argument("--recap", type=float, default=60, metavar="SECONDS",
                        help="write a readable recap every SECONDS (default 60); 0 translates live, line by line")
    args = parser.parse_args()
    args.out = args.out or ("recap.txt" if args.recap else "translation.txt")
    args.effort = args.effort or ("medium" if args.recap else "low")

    system = RECAP_PROMPT if args.recap else SYSTEM_PROMPT
    if args.notes:
        system += "\n\n" + tagged("meeting_notes", [Path(args.notes).read_text(encoding="utf-8").strip()])
    if args.repo:
        if args.backend != "claude-code" or not Path(args.repo).is_dir():
            parser.error("--repo needs --backend claude-code and an existing directory")
        args.repo = str(Path(args.repo).resolve())  # Claude Code runs from a temp dir
        system += "\n\n" + tagged("system_repo", [REPO_NOTE.format(repo=args.repo)])
    if args.backend == "api":
        translate = api_backend(args.model, args.effort, system)
    else:
        translate = claude_code_backend(args.model, args.effort, system, args.repo)

    Path(args.transcript).touch()
    print(f"Watching {args.transcript} -> {args.out} ({args.model})", flush=True)
    with open(args.transcript, encoding="utf-8") as transcript, open(args.out, "a", encoding="utf-8") as out:
        transcript.seek(0, 2)  # only translate what's said from now on
        lines, partial, sent_at = [], "", time.monotonic()
        while True:
            *new, partial = (partial + transcript.read()).split("\n")
            lines += [line for line in new if line.strip()]
            if not lines or time.monotonic() - sent_at < args.recap:
                time.sleep(0.1)
                continue
            sent_at = time.monotonic()
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
            gap = "\n\n" if args.recap else "\n"  # blank line between recaps
            print(end=gap, flush=True)
            if english.strip():
                out.write(english.strip() + gap)
                out.flush()
            lines = []


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
