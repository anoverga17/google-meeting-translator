"""Receives caption batches from the browser extension and appends them to a transcript file.

Each line is written as:  [HH:MM:SS] Speaker: text

Usage:
    python server.py                       # writes ./transcript.txt on 127.0.0.1:8765
    python server.py --out meeting.txt --host 0.0.0.0
"""

import argparse
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

write_lock = threading.Lock()


def format_line(entry: dict) -> str | None:
    text = " ".join(str(entry.get("text", "")).split())
    if not text:
        return None
    speaker = " ".join(str(entry.get("speaker") or "Unknown").split())
    ts = entry.get("ts")
    when = datetime.fromtimestamp(ts / 1000) if isinstance(ts, (int, float)) else datetime.now()
    return f"[{when:%H:%M:%S}] {speaker}: {text}"


def make_handler(out_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self._reply(200, {"ok": True, "file": str(out_path)})
            else:
                self._reply(404, {"ok": False})

        def do_POST(self):
            if self.path != "/captions":
                self._reply(404, {"ok": False})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                lines = [line for e in payload.get("lines", []) if (line := format_line(e))]
            except (ValueError, AttributeError) as exc:
                self._reply(400, {"ok": False, "error": str(exc)})
                return

            if lines:
                with write_lock, out_path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                for line in lines:
                    print(line, flush=True)
            self._reply(200, {"ok": True, "written": len(lines)})

        def _reply(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass  # keep the terminal for captions only

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="transcript.txt", help="transcript file to append to")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    out_path = Path(args.out).resolve()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(out_path))
    print(f"Listening on http://{args.host}:{args.port} -> appending to {out_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
