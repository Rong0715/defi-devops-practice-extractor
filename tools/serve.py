#!/usr/bin/env python3
"""Serve site/ for local review, with caching turned off.

    python3 tools/serve.py            # http://127.0.0.1:8000
    python3 tools/serve.py --port 8080

app.js, style.css and data/data.js are rewritten on every extractor run. Python's
stock http.server lets the browser reuse them without revalidating, so a rebuild
looks like "nothing changed" until a hard refresh. This sends no-store on every
response, so an ordinary reload always shows the current build.
"""

import argparse
import contextlib
import functools
import http.server
import socket
import socketserver
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):   # one line per request is enough
        if not self.path.startswith("/favicon"):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if not (SITE / "index.html").exists():
        raise SystemExit(f"{SITE}/index.html not found -- run tools/build_site_data.py first")
    handler = functools.partial(NoCache, directory=str(SITE))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", a.port), handler)
    except OSError as e:
        raise SystemExit(f"port {a.port} is busy ({e}); stop the other server or pass --port")
    print(f"serving {SITE} at http://127.0.0.1:{a.port}  (no-store; plain reload is enough)")
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()
    httpd.server_close()


if __name__ == "__main__":
    main()
