"""Appends the caption lines POSTed by the browser extension to a transcript file.

Usage:
    python server.py                       # writes ./transcript.txt on 127.0.0.1:8765
    python server.py --out meeting.txt --host 0.0.0.0
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--out", default="transcript.txt", help="transcript file to append to")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        lines = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(lines)
        print(lines, end="", flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass  # keep the terminal for captions only


print(f"Listening on http://{args.host}:{args.port} -> appending to {args.out}", flush=True)
try:
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
except KeyboardInterrupt:
    pass
