#!/usr/bin/env python3
"""
transcribe_youtube.py
Runs inside GitHub Actions. Accepts one or more YouTube URLs as arguments,
attempts to fetch official YouTube transcripts first, then falls back to
Supadata's free Whisper API for audio-based transcription.

Usage:
    python transcribe_youtube.py <url1> [url2] [url3] ...
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
    """Extract the YouTube video ID from a variety of URL formats."""
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


def safe_filename(video_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", video_id)


# ---------------------------------------------------------------------------
# Route 1 – Official YouTube transcript (free, no API key needed)
# ---------------------------------------------------------------------------

def fetch_youtube_transcript(video_id: str) -> str | None:
    """
    Try to pull an existing caption track from YouTube.
    Returns the full transcript as a plain string, or None if unavailable.
    Compatible with youtube-transcript-api v0.x and v1.x.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # v1.x API: fetch() returns a FetchedTranscript directly
        # Try English first, then fall back to any available language
        for lang_codes in [["en", "en-US", "en-GB"], None]:
            try:
                if lang_codes:
                    fetched = YouTubeTranscriptApi.fetch(video_id, languages=lang_codes)
                else:
                    # Get list of available transcripts and pick the first
                    transcript_list = YouTubeTranscriptApi.list(video_id)
                    first = next(iter(transcript_list))
                    fetched = first.fetch()

                # v1.x returns a FetchedTranscript object; iterate its snippets
                snippets = list(fetched)
                if snippets and hasattr(snippets[0], "text"):
                    text = " ".join(s.text for s in snippets)
                else:
                    # Fallback: treat as list of dicts (older shape)
                    text = " ".join(s["text"] for s in snippets)

                print(f"  [✓] Official YouTube transcript found ({len(text)} chars).")
                return text
            except Exception:
                continue

    except Exception as e:
        print(f"  [!] YouTube transcript unavailable: {e}")

    return None


# ---------------------------------------------------------------------------
# Route 2 – Supadata free-tier Whisper API (50 req/day, 5 req/10 s)
# ---------------------------------------------------------------------------

def transcribe_with_supadata(video_url: str, api_key: str) -> str | None:
    """
    Send the YouTube URL directly to Supadata's transcript endpoint.
    Docs: https://supadata.ai/documentation/youtube/get-transcript
    """
    # Try both known endpoint shapes
    endpoints = [
        ("GET",  "https://api.supadata.ai/v1/youtube/transcript",  {"url": video_url, "text": "true"}),
        ("POST", "https://api.supadata.ai/v1/youtube/transcript",  {"url": video_url, "text": True}),
        ("GET",  "https://api.supadata.ai/v1/transcript",          {"url": video_url, "text": "true"}),
    ]

    headers = {"x-api-key": api_key}

    print(f"  [→] Sending to Supadata API …")
    for method, endpoint, params_or_body in endpoints:
        try:
            if method == "GET":
                resp = requests.get(endpoint, headers=headers, params=params_or_body, timeout=120)
            else:
                resp = requests.post(endpoint, headers={**headers, "Content-Type": "application/json"},
                                     json=params_or_body, timeout=120)

            if resp.status_code == 404:
                continue  # try next endpoint shape

            resp.raise_for_status()
            data = resp.json()

            # Handle both flat-text and chunked responses
            text = data.get("content") or data.get("transcript") or data.get("text")
            if not text and isinstance(data.get("chunks"), list):
                text = " ".join(c.get("text", "") for c in data["chunks"])

            if text:
                print(f"  [✓] Supadata transcript received ({len(text)} chars).")
                return text

            print(f"  [!] Supadata returned unexpected shape: {json.dumps(data)[:300]}")
            return None

        except requests.HTTPError as e:
            print(f"  [!] Supadata HTTP error {e.response.status_code} ({method} {endpoint}): {e.response.text[:200]}")
        except Exception as e:
            print(f"  [!] Supadata request failed: {e}")

    print("  [!] All Supadata endpoint variants failed.")
    return None


# ---------------------------------------------------------------------------
# Route 3 – Local Whisper fallback (yt-dlp + openai-whisper)
# ---------------------------------------------------------------------------

def transcribe_locally(video_url: str, video_id: str) -> str | None:
    """
    Last-resort: download audio with yt-dlp and run openai-whisper locally
    inside the GitHub Actions runner. Uses the 'base' model to stay within
    the runner's ~14 GB disk limit.
    """
    import subprocess
    import tempfile

    print("  [→] Falling back to local yt-dlp + Whisper transcription …")
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"{video_id}.mp3")
        dl_cmd = [
            "yt-dlp",
            "--no-playlist",
            "--js-runtimes", "nodejs",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "5",          # lower quality = smaller file
            "-o", audio_path,
            video_url,
        ]
        result = subprocess.run(dl_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [!] yt-dlp failed:\n{result.stderr[:500]}")
            return None

        print(f"  [✓] Audio downloaded. Running Whisper (base model) …")
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(audio_path)
            text = result.get("text", "")
            print(f"  [✓] Local Whisper transcript ({len(text)} chars).")
            return text
        except Exception as e:
            print(f"  [!] Local Whisper failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_video(video_url: str, supadata_api_key: str | None, index: int) -> dict:
    """Process a single video URL and return a result dict."""
    print(f"\n{'='*60}")
    print(f"[{index}] Processing: {video_url}")
    print(f"{'='*60}")

    result = {
        "url": video_url,
        "video_id": None,
        "method": None,
        "transcript": None,
        "error": None,
    }

    try:
        video_id = extract_video_id(video_url)
        result["video_id"] = video_id
        print(f"  Video ID: {video_id}")
    except ValueError as e:
        result["error"] = str(e)
        print(f"  [✗] {e}")
        return result

    # --- Route 1: Official YouTube transcript ---
    transcript = fetch_youtube_transcript(video_id)
    if transcript:
        result["method"] = "youtube_transcript_api"
        result["transcript"] = transcript
        return result

    # --- Route 2: Supadata API ---
    if supadata_api_key:
        # Respect the 5 req / 10 s rate limit with a small delay
        if index > 1:
            time.sleep(2)
        transcript = transcribe_with_supadata(video_url, supadata_api_key)
        if transcript:
            result["method"] = "supadata_api"
            result["transcript"] = transcript
            return result
    else:
        print("  [!] SUPADATA_API_KEY not set — skipping Supadata route.")

    # --- Route 3: Local Whisper ---
    transcript = transcribe_locally(video_url, video_id)
    if transcript:
        result["method"] = "local_whisper"
        result["transcript"] = transcript
        return result

    result["error"] = "All transcription methods failed."
    print(f"  [✗] All transcription methods failed for {video_id}.")
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python transcribe_youtube.py <url1> [url2] ...")
        sys.exit(1)

    urls = sys.argv[1:]
    supadata_api_key = os.environ.get("SUPADATA_API_KEY")

    output_dir = Path("transcripts")
    output_dir.mkdir(exist_ok=True)

    summary = []

    for i, url in enumerate(urls, start=1):
        res = process_video(url.strip(), supadata_api_key, i)
        summary.append(res)

        if res["transcript"]:
            fname = output_dir / f"{safe_filename(res['video_id'])}.txt"
            fname.write_text(res["transcript"], encoding="utf-8")
            print(f"  [✓] Saved → {fname}")
        else:
            print(f"  [✗] No transcript produced for {url}")

    # Write a JSON summary for easy parsing by the local runner
    summary_path = Path("transcripts") / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[✓] Summary written to {summary_path}")

    # Exit with error code if any video failed
    failures = [r for r in summary if not r["transcript"]]
    if failures:
        print(f"\n[!] {len(failures)} video(s) failed to transcribe.")
        sys.exit(1)

    print(f"\n[✓] All {len(urls)} video(s) transcribed successfully.")


if __name__ == "__main__":
    main()
