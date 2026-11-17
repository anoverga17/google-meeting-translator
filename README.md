# Meet caption translator

Captures Google Meet live captions (Japanese) into a raw transcript file, and
translates it to English with Claude in near real time.

```
Meet tab ──(content.js)──> background.js ──POST──> server.py ──> transcript.txt
                                                                      │ tail
                                                     translate.py <───┘ ──> terminal + translation.txt
```

The transcript is plain text (`[HH:MM:SS] Speaker: text`), so you can also point
any other agent (e.g. Claude Code) at `transcript.txt` instead of / alongside
`translate.py`.

## Setup

```bash
make install                         # creates .venv and installs requirements
export ANTHROPIC_API_KEY=sk-ant-...  # not needed for `make cc`
```

Load the extension in Chrome: `chrome://extensions` → enable **Developer mode** →
**Load unpacked** → select the `extension/` folder. (On WSL the path is
`\\wsl$\<distro>\home\...\translator\extension`; if Chrome refuses it, copy the
folder to a Windows path and load that.)

## Run

```bash
make run     # transcript server + translator via the Claude API
make cc      # same, but translate through Claude Code on your Claude plan
make live    # line-by-line live translation instead of the recap
```

Each starts the transcript server in the background (logging to `server.log`;
`tail -f server.log` to watch raw captions) and the translator in the foreground.
Ctrl+C stops both.

By default the translator writes a recap: every minute it turns the new captions
into readable English (one paragraph per speaker turn, fragments merged, fillers
dropped) and appends it to `recap.txt`. It's a cleaned-up translation, not a
summary, so nothing said is left out. It runs at medium effort, trading speed for
accuracy on technical talk.

`make live` instead translates each caption line as it arrives, at low effort, into
`translation.txt`: faster but choppier, since captions arrive in fragments. For
both at once, run `make translate RECAP=0` (add `BACKEND=claude-code` for your plan)
in a second terminal next to `make run` / `make cc`.

Then join the Meet, turn on captions (CC) and set the caption language to
Japanese. The extension badge shows **ON** when captions reach the server and
**ERR** when the server isn't reachable (captions are buffered and retried).

Options are make variables:

```bash
make run NOTES=notes.txt               # names, project terms, glossary
make cc REPO=path/to/docs              # let Claude look up names and terms there
make run RECAP=120                     # recap every 2 minutes instead of every minute
make run MODEL=claude-sonnet-5-5       # compare speed vs. quality
make run EFFORT=high HOST=0.0.0.0      # see Troubleshooting for HOST
```

`make server` / `make translate` run either half on its own (e.g. in two terminals).

`REPO` (Claude Code only) points at a repository or folder describing the systems
being discussed (docs, code, a glossary).
When the captions mention a system name or technical term Claude can't place
(speech recognition garbles these a lot), it searches that folder (read-only) to
find the right term. Each recap takes a few seconds longer.

`make cc` needs the `claude` CLI installed and logged in. It runs one
long-running Claude Code session for the whole meeting, counts against your plan's
usage limits, and keeps the whole meeting as context rather than the last 30 lines.

## How capturing works

Meet rewrites captions in place while someone is talking, and Japanese ASR revises
earlier characters often. `content.js` polls the captions panel every 250 ms and
commits text when it has settled:

- a completed sentence (ending in 。？！ with more text after it) once it's unchanged
  for 1 s, even if the speaker keeps talking. Meet puts a provisional 。 on the end of
  text that is still being spoken, so a mark at the very end doesn't count;
- everything once the speaker pauses for 1.5 s;
- as a backstop, after 6 s of speech with no sentence end (holding back the last few
  still-changing characters). Cutting mid-sentence hurts accuracy since Japanese puts
  the verb last, so this is deliberately not shorter.

When the recognizer revises text that was already sent, the revised sentence is
re-sent and the translator is told to skip duplicates. `translate.py` sends lines
as soon as they reach the file; lines arriving mid-translation go in the next batch.

## Troubleshooting

- **Nothing captured / badge never turns ON.** Open DevTools on the Meet tab and
  look for `[meet-captions]` logs. Meet's class names are obfuscated and change;
  update the selectors at the top of `extension/content.js` (`BLOCK` = one speaker's
  caption entry, `SPEAKER` = name, `TEXT` = caption text). If `BLOCK` stops
  matching, it falls back to reading the whole captions region with speaker
  `Unknown`.
- **Badge shows ERR.** `server.py` isn't running or isn't reachable. With Chrome on
  Windows and the server in WSL2, localhost forwarding normally works; if not,
  run `make run HOST=0.0.0.0`.
- **After reloading the extension**, refresh the Meet tab.
