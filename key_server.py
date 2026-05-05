#!/usr/bin/env python3
"""
key_server.py  –  Tiny local server that serves the SUPADATA_API_KEY
                  from the .env file to the Chrome extension.

Run once (stays in background):
    python3 key_server.py &

Or add to your shell profile so it starts automatically:
    echo "python3 /path/to/key_server.py &" >> ~/.zshrc

The extension fetches:  http://localhost:27182/apikey
Response:               {"apiKey": "sd_...", "savePath": "/path/to/downloads"}
"""

import http.server
import json
import os
import re
import sys
from pathlib import Path

PORT      = 27182
REPO_ROOT = Path(__file__).parent.resolve()
ENV_FILE  = REPO_ROOT / ".env"


def load_env() -> dict:
    """Read .env from the repo root. Falls back to environment variables."""
    values = {}

    # 1. Try .env file
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Z0-9_]+)\s*=\s*["\']?(.+?)["\']?\s*$', line)
            if m:
                values[m.group(1)] = m.group(2)

    # 2. Fall back to real environment variables
    for key in ["SUPADATA_API_KEY"]:
        if key not in values and os.environ.get(key):
            values[key] = os.environ[key]

    return values


class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence request logs

    def do_GET(self):
        if self.path != "/apikey":
            self._send(404, {"error": "not found"})
            return

        env = load_env()
        api_key  = env.get("SUPADATA_API_KEY", "")
        save_path = str(REPO_ROOT / "downloads")

        if not api_key:
            self._send(404, {"error": "SUPADATA_API_KEY not found in .env"})
            return

        self._send(200, {"apiKey": api_key, "savePath": save_path})

    def do_OPTIONS(self):
        # CORS preflight
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _cors_headers(self):
        # Allow the Chrome extension origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def main():
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    env    = load_env()
    key    = env.get("SUPADATA_API_KEY", "")

    print(f"[YT Transcript key server] Listening on http://127.0.0.1:{PORT}/apikey")
    if key:
        print(f"[YT Transcript key server] API key loaded: {key[:8]}…")
    else:
        print(f"[YT Transcript key server] WARNING: SUPADATA_API_KEY not found in {ENV_FILE}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[YT Transcript key server] Stopped.")


if __name__ == "__main__":
    main()
