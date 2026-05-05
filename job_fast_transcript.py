#!/usr/bin/env python3
"""
job_fast_transcript.py
GitHub Actions Job 1 – Fast transcript methods only.
Runs: YouTube captions API + Supadata API (if key available).
No heavy dependencies (no torch, no yt-dlp).

Usage:
    python job_fast_transcript.py <url1> [url2] ...
"""

import os
import sys
import json
import re
import time
import requests
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
        r"(?:shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


# ---------------------------------------------------------------------------
# Method A – YouTube captions (youtube-transcript-api)
# ---------------------------------------------------------------------------

def fetch_youtube_captions(video_id: str) -> str | None:
    """Pull official/auto-generated captions directly from YouTube. No API key needed."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        for lang_codes in [["en", "en-US", "en-GB"], None]:
            try:
                if lang_codes:
                    fetched = YouTubeTranscriptApi.fetch(video_id, languages=lang_codes)
                else:
                    transcript_list = YouTubeTranscriptApi.list(video_id)
                    first = next(iter(transcript_list))
                    fetched = first.fetch()

                snippets = list(fetched)
                if not snippets:
                    continue

                if hasattr(snippets[0], "text"):
                    text = " ".join(s.text for s in snippets)
                else:
                    text = " ".join(s["text"] for s in snippets)

                text = text.strip()
                if text:
                    print(f"  [✓] YouTube captions: {len(text):,} chars")
                    return text
            except Exception:
                continue

    except Exception as e:
        print(f"  [!] YouTube captions unavailable: {e}")

    return None


# ---------------------------------------------------------------------------
# Method B – Supadata API
# ---------------------------------------------------------------------------

def fetch_supadata(video_url: str, api_key: str) -> str | None:
    """Call Supadata's free-tier transcript endpoint with the YouTube URL."""
    headers = {"x-api-key": api_key}

    # Try GET first (current documented shape), then POST fallback
    attempts = [
        ("GET",  "https://api.supadata.ai/v1/youtube/transcript", {"url": video_url, "text": "true"}),
        ("GET",  "https://api.supadata.ai/v1/transcript",         {"url": video_url, "text": "true"}),
        ("POST", "https://api.supadata.ai/v1/youtube/transcript", {"url": video_url, "text": True}),
    ]

    print("  [→] Supadata API …")
    for method, endpoint, params in attempts:
        try:
            if method == "GET":
                resp = requests.get(endpoint, headers=headers, params=params, timeout=120)
            else:
                resp = requests.post(
                    endpoint,
                    headers={**headers, "Content-Type": "application/json"},
                    json=params,
                    timeout=120,
                )

            if resp.status_code == 404:
                continue

            resp.raise_for_status()
            data = resp.json()

            text = data.get("content") or data.get("transcript") or data.get("text")
            if not text and isinstance(data.get("chunks"), list):
                text = " ".join(c.get("text", "") for c in data["chunks"])

            if text:
                text = text.strip()
                print(f"  [✓] Supadata: {len(text):,} chars")
                return text

            print(f"  [!] Supadata unexpected response shape: {json.dumps(data)[:200]}")
            return None

        except requests.HTTPError as e:
            print(f"  [!] Supadata {e.response.status_code} ({method}): {e.response.text[:150]}")
        except Exception as e:
            print(f"  [!] Supadata error: {e}")

    print("  [!] All Supadata variants failed.")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python job_fast_transcript.py <url1> [url2] ...")
        sys.exit(1)

    urls = [u.strip() for u in sys.argv[1:] if u.strip()]
    supadata_key = os.environ.get("SUPADATA_API_KEY", "").strip()

    out_dir = Path("transcripts-fast")
    out_dir.mkdir(exist_ok=True)

    results = []
    any_success = False

    for i, url in enumerate(urls, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(urls)}] {url}")
        print("="*60)

        try:
            video_id = extract_video_id(url)
        except ValueError as e:
            print(f"  [✗] {e}")
            results.append({"url": url, "video_id": None, "sources": {}, "error": str(e)})
            continue

        print(f"  Video ID: {video_id}")
        sources = {}

        # --- YouTube captions ---
        print("  [→] Trying YouTube captions …")
        yt_text = fetch_youtube_captions(video_id)
        if yt_text:
            sources["youtube_captions"] = yt_text
            fname = out_dir / f"{safe_filename(video_id)}_youtube_captions.txt"
            fname.write_text(yt_text, encoding="utf-8")
            print(f"  [✓] Saved: {fname.name}")
        else:
            print("  [✗] YouTube captions: not available")

        # --- Supadata ---
        if supadata_key:
            if i > 1:
                time.sleep(2)  # respect 5 req/10s rate limit
            print("  [→] Trying Supadata …")
            sd_text = fetch_supadata(url, supadata_key)
            if sd_text:
                sources["supadata"] = sd_text
                fname = out_dir / f"{safe_filename(video_id)}_supadata.txt"
                fname.write_text(sd_text, encoding="utf-8")
                print(f"  [✓] Saved: {fname.name}")
            else:
                print("  [✗] Supadata: failed")
        else:
            print("  [!] SUPADATA_API_KEY not set — skipping Supadata")

        entry = {
            "url": url,
            "video_id": video_id,
            "sources": {k: len(v) for k, v in sources.items()},  # char counts only in summary
            "error": None if sources else "No fast methods succeeded",
        }
        results.append(entry)
        if sources:
            any_success = True

    # Write summary JSON
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[✓] Fast-transcript summary → {summary_path}")

    sys.exit(0 if any_success else 1)


if __name__ == "__main__":
    main()
