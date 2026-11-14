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
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

Load the extension in Chrome: `chrome://extensions` → enable **Developer mode** →
**Load unpacked** → select the `extension/` folder. (On WSL the path is
`\\wsl$\<distro>\home\...\translator\extension`; if Chrome refuses it, copy the
folder to a Windows path and load that.)

## Run

Terminal 1 — transcript server:

```bash
.venv/bin/python server.py                 # appends to ./transcript.txt
```

Terminal 2 — translator:

```bash
.venv/bin/python translate.py              # streams English, appends to ./translation.txt
.venv/bin/python translate.py --notes notes.txt   # names, project terms, glossary
```

Then join the Meet, turn on captions (CC) and set the caption language to
Japanese. The extension badge shows **ON** when captions reach the server and
**ERR** when the server isn't reachable (captions are buffered and retried).

Useful `translate.py` options: `--model claude-haiku-5-5` (faster, cheaper),
`--effort` (default `low` for latency), `--debounce` / `--max-wait` (how long to
batch lines before translating), `--from-start` (translate what's already in the file).

## How capturing works

Meet rewrites captions in place while someone is talking, and Japanese ASR revises
earlier characters often. `content.js` polls the captions panel every 500 ms and
only commits text once it has been unchanged for 1.5 s, or after 8 s of
continuous speech (holding back the last few still-changing characters). When the
recognizer revises text that was already sent, the revised sentence is re-sent and
the translator is told to skip duplicates.

## Troubleshooting

- **Nothing captured / badge never turns ON.** Open DevTools on the Meet tab and
  look for `[meet-captions]` logs. Meet's class names are obfuscated and change;
  update `SELECTORS` at the top of `extension/content.js` (`block` = one speaker's
  caption entry, `speaker` = name, `text` = caption text). If the block selector
  stops matching, it falls back to reading the whole captions region with speaker
  `Unknown`.
- **Badge shows ERR.** `server.py` isn't running or isn't reachable. With Chrome on
  Windows and the server in WSL2, localhost forwarding normally works; if not,
  run `server.py --host 0.0.0.0`.
- **After reloading the extension**, refresh the Meet tab.
