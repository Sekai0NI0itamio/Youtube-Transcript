#!/usr/bin/env python3
"""
transcribe.py  –  YouTube → Transcript via Supadata API (local script)

Usage:
    python transcribe.py <url1> [url2] [url3] ...

Output:
    downloads/<video_id>.txt   for each video

Requirements:
    pip install requests
    SUPADATA_API_KEY env var set, or stored in .env file in this directory
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("[✗] 'requests' is not installed. Run: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE    = "https://api.supadata.ai/v1/youtube/transcript"
DOWNLOADS   = Path("downloads")
RATE_LIMIT_DELAY = 2.2   # seconds between requests (5 req / 10s limit)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
_COLOR = sys.stdout.isatty()

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

def ok(msg):   print(_c("32", f"[✓] {msg}"))
def info(msg): print(_c("36", f"[→] {msg}"))
def warn(msg): print(_c("33", f"[!] {msg}"))
def err(msg):  print(_c("31", f"[✗] {msg}"))
def head(msg): print(_c("1",  f"\n{'='*60}\n  {msg}\n{'='*60}"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    """Read SUPADATA_API_KEY from env or .env file."""
    key = os.environ.get("SUPADATA_API_KEY", "").strip()
    if key:
        return key

    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("SUPADATA_API_KEY"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"').strip("'")

    err("SUPADATA_API_KEY not found.")
    print("  Set it in your environment:  export SUPADATA_API_KEY=your_key")
    print("  Or create a .env file with:  SUPADATA_API_KEY=your_key")
    sys.exit(1)


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
        r"(?:shorts/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract video ID from: {url}")


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


# ---------------------------------------------------------------------------
# Supadata transcript fetch
# ---------------------------------------------------------------------------

def fetch_transcript(url: str, api_key: str) -> tuple[str | None, str]:
    """
    Fetch transcript from Supadata.
    Returns (text, method) where method describes what was used.

    Supadata response shape:
      text=false → {"content": [{"text": "...", "offset": 0, "duration": 5}, ...], "lang": "en"}
      text=true  → {"content": "full plain text ...", "lang": "en"}
    """
    headers = {"x-api-key": api_key}

    for text_param, label in [("false", "chunked"), ("true", "flat")]:
        info(f"Fetching transcript ({label}) …")
        try:
            resp = requests.get(
                API_BASE,
                headers=headers,
                params={"url": url, "text": text_param},
                timeout=120,
            )

            if resp.status_code == 429:
                warn("Rate limited — waiting 15s …")
                time.sleep(15)
                resp = requests.get(
                    API_BASE,
                    headers=headers,
                    params={"url": url, "text": text_param},
                    timeout=120,
                )

            if resp.status_code == 404:
                warn(f"Endpoint not found ({label}) — trying next …")
                continue

            resp.raise_for_status()
            data = resp.json()

            content = data.get("content")

            # Chunked: content is a list of {"text": "...", "offset": N, "duration": N}
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "").strip()
                    for c in content
                    if isinstance(c, dict) and c.get("text", "").strip()
                )
                if text:
                    return text, f"supadata_{label}"
                warn(f"Supadata chunked list was empty")
                continue

            # Flat: content is a plain string
            if isinstance(content, str) and content.strip():
                return content.strip(), f"supadata_{label}"

            # Other field names as fallback
            for key in ("transcript", "text"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip(), f"supadata_{label}"

            warn(f"Supadata returned no usable content ({label}): {json.dumps(data)[:200]}")

        except requests.HTTPError as e:
            err(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except requests.RequestException as e:
            err(f"Request failed: {e}")

    return None, "failed"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    urls = [u.strip() for u in sys.argv[1:] if u.strip()]
    if not urls:
        err("No URLs provided.")
        sys.exit(1)

    api_key = load_api_key()

    DOWNLOADS.mkdir(exist_ok=True)

    head(f"YouTube → Transcript  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {len(urls)} URL(s) to process\n")

    results = []

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}")
        print("-" * 60)

        try:
            video_id = extract_video_id(url)
        except ValueError as e:
            err(str(e))
            results.append({"url": url, "video_id": None, "status": "error", "error": str(e)})
            continue

        print(f"  Video ID: {video_id}")

        # Rate limit between requests
        if i > 1:
            time.sleep(RATE_LIMIT_DELAY)

        text, method = fetch_transcript(url, api_key)

        if text:
            out_file = DOWNLOADS / f"{safe_filename(video_id)}.txt"
            out_file.write_text(text, encoding="utf-8")
            ok(f"Saved → {out_file}  ({len(text):,} chars, method: {method})")
            results.append({
                "url": url,
                "video_id": video_id,
                "status": "success",
                "method": method,
                "chars": len(text),
                "file": str(out_file),
            })
        else:
            err(f"Failed to get transcript for {video_id}")
            results.append({
                "url": url,
                "video_id": video_id,
                "status": "failed",
                "error": "All Supadata methods returned empty",
            })

    # Summary
    head("DONE")
    success = [r for r in results if r["status"] == "success"]
    failed  = [r for r in results if r["status"] != "success"]

    if success:
        print(f"  {len(success)} transcript(s) saved to {DOWNLOADS.resolve()}:\n")
        for r in success:
            print(f"    ✓  {r['video_id']}  ({r['chars']:,} chars)  →  {r['file']}")

    if failed:
        print()
        for r in failed:
            print(f"    ✗  {r.get('video_id') or r['url']}  —  {r.get('error', 'unknown error')}")

    print()
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
