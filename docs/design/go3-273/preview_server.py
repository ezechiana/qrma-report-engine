"""Loopback-only design preview. Serves two allowlisted static artifacts only."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FILES = {
    "/": (HERE / "prototype.html", "text/html; charset=utf-8"),
    "/prototype.html": (HERE / "prototype.html", "text/html; charset=utf-8"),
    "/app/static/brand/go360-v1/go360-wordmark-light.svg": (
        ROOT / "app/static/brand/go360-v1/go360-wordmark-light.svg", "image/svg+xml"),
}


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        artifact = FILES.get(urlsplit(self.path).path)
        if not artifact:
            self.send_error(404)
            return
        content = artifact[0].read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", artifact[1])
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Preview)
    args.info.write_text(json.dumps({"pid": os.getpid(), "url": f"http://127.0.0.1:{server.server_port}/prototype.html"}), encoding="utf-8")
    server.serve_forever()
