#!/usr/bin/env python3
"""
job_whisper_transcript.py
GitHub Actions Job 2 – AI/Whisper-based transcription.

Strategy (in order):
  1. Supadata API with text=false  → chunked transcript with timestamps
     (Supadata runs Whisper on their servers — no YouTube download needed)
  2. yt-dlp audio download + local openai-whisper
     (only attempted if YOUTUBE_COOKIES is set and valid)

Usage:
    python job_whisper_transcript.py <url1> [url2] ...
"""

import os
import sys
import json
import re
import math
import subprocess
import tempfile
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
# Method 1 – Supadata chunked transcript (AI Whisper on their servers)
# ---------------------------------------------------------------------------

def transcribe_supadata_chunked(video_url: str, api_key: str) -> str | None:
    """
    Call Supadata with text=false to get timestamped chunks.
    This uses their AI Whisper backend — no local download needed.
    Returns the full transcript as plain text (chunks joined).
    """
    headers = {"x-api-key": api_key}

    attempts = [
        ("GET", "https://api.supadata.ai/v1/youtube/transcript",
         {"url": video_url, "text": "false"}),
        ("GET", "https://api.supadata.ai/v1/youtube/transcript",
         {"url": video_url, "text": "true"}),
        ("GET", "https://api.supadata.ai/v1/transcript",
         {"url": video_url, "text": "false"}),
    ]

    print("  [→] Supadata AI transcript (chunked) …")
    for method, endpoint, params in attempts:
        try:
            resp = requests.get(endpoint, headers=headers, params=params, timeout=180)

            if resp.status_code == 404:
                continue
            if resp.status_code == 429:
                print("  [!] Supadata rate limit — waiting 12s …")
                time.sleep(12)
                resp = requests.get(endpoint, headers=headers, params=params, timeout=180)

            resp.raise_for_status()
            data = resp.json()

            # text=false returns {"chunks": [{"text": "...", "offset": 0, "duration": 5}, ...]}
            # text=true  returns {"content": "full text ..."}
            chunks = data.get("chunks") or data.get("segments") or []
            if chunks:
                text = " ".join(
                    c.get("text", "").strip()
                    for c in chunks
                    if c.get("text", "").strip()
                )
                if text:
                    print(f"  [✓] Supadata chunked: {len(chunks)} chunks, {len(text):,} chars")
                    return text

            # Flat text fallback
            text = data.get("content") or data.get("transcript") or data.get("text")
            if text and text.strip():
                print(f"  [✓] Supadata flat text: {len(text):,} chars")
                return text.strip()

            print(f"  [!] Supadata returned no usable content: {json.dumps(data)[:200]}")
            return None

        except requests.HTTPError as e:
            print(f"  [!] Supadata HTTP {e.response.status_code}: {e.response.text[:150]}")
        except Exception as e:
            print(f"  [!] Supadata error: {e}")

    return None


# ---------------------------------------------------------------------------
# Method 2 – yt-dlp download + local openai-whisper (cookie-gated)
# ---------------------------------------------------------------------------

def download_audio(video_url: str, out_path: str, cookies_file: str) -> bool:
    """Download best-quality audio as 16kHz mono WAV for Whisper."""
    print("  [→] Downloading audio with yt-dlp …")

    base_cmd = [
        "yt-dlp",
        "--no-playlist",
        "--cookies", cookies_file,
        "-f", "bestaudio/best",
        "-x", "--audio-format", "wav",
        "--audio-quality", "0",
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
        "-o", out_path,
        "--no-progress",
        "--no-warnings",
    ]

    for client in ["web", "android_vr", "tv_embedded"]:
        print(f"  [→] Trying player_client={client} …")
        cmd = base_cmd + ["--extractor-args", f"youtube:player_client={client}", video_url]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(out_path):
            print(f"  [✓] Audio downloaded (client={client})")
            return True
        print(f"  [!] client={client}: {result.stderr.strip()[:200]}")

    print("  [✗] All yt-dlp clients failed.")
    return False


def get_audio_duration(audio_path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def split_audio(audio_path: str, segment_dir: str, segment_secs: int = 600) -> list[str]:
    duration = get_audio_duration(audio_path)
    if duration == 0:
        return [audio_path]

    n = math.ceil(duration / segment_secs)
    print(f"  [→] {duration:.0f}s audio → {n} segment(s) of {segment_secs}s")
    if n == 1:
        return [audio_path]

    os.makedirs(segment_dir, exist_ok=True)
    segments = []
    for i in range(n):
        seg = os.path.join(segment_dir, f"seg_{i:04d}.wav")
        cmd = ["ffmpeg", "-y", "-i", audio_path,
               "-ss", str(i * segment_secs), "-t", str(segment_secs),
               "-ar", "16000", "-ac", "1", seg]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(seg):
            segments.append(seg)
    print(f"  [✓] {len(segments)} segment(s) created")
    return segments


def transcribe_local_whisper(segments: list[str]) -> str | None:
    try:
        import whisper
    except ImportError:
        print("  [!] openai-whisper not installed")
        return None

    print("  [→] Loading Whisper 'base' model …")
    try:
        model = whisper.load_model("base")
    except Exception as e:
        print(f"  [!] Failed to load model: {e}")
        return None

    parts = []
    for i, seg in enumerate(segments, 1):
        print(f"  [→] Segment {i}/{len(segments)} …")
        try:
            result = model.transcribe(seg, fp16=False, language="en")
            text = result.get("text", "").strip()
            if text:
                parts.append(text)
                print(f"      {len(text):,} chars")
        except Exception as e:
            print(f"  [!] Whisper error on segment {i}: {e}")

    if not parts:
        return None
    full = " ".join(parts)
    print(f"  [✓] Local Whisper: {len(full):,} chars")
    return full


def transcribe_local(video_url: str, video_id: str) -> str | None:
    """Attempt local yt-dlp + Whisper pipeline. Requires valid YOUTUBE_COOKIES."""
    cookies_content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not cookies_content:
        print("  [!] YOUTUBE_COOKIES not set — skipping local Whisper")
        return None

    # Check cookies contain real auth tokens
    auth_tokens = {"SID", "SSID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID"}
    cookie_names = set()
    for line in cookies_content.splitlines():
        parts = line.split("\t")
        if len(parts) > 5:
            cookie_names.add(parts[5])

    missing = auth_tokens - cookie_names
    if missing:
        print(f"  [!] Cookies missing auth tokens {missing} — YouTube will reject them")
        print("      Log into YouTube in Chrome and re-run the local script")
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        cookies_file = os.path.join(tmpdir, "cookies.txt")
        Path(cookies_file).write_text(cookies_content, encoding="utf-8")

        audio_path = os.path.join(tmpdir, f"{video_id}.wav")
        seg_dir    = os.path.join(tmpdir, "segments")

        if not download_audio(video_url, audio_path, cookies_file):
            return None

        segments = split_audio(audio_path, seg_dir)
        return transcribe_local_whisper(segments)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python job_whisper_transcript.py <url1> [url2] ...")
        sys.exit(1)

    urls = [u.strip() for u in sys.argv[1:] if u.strip()]
    supadata_key = os.environ.get("SUPADATA_API_KEY", "").strip()

    out_dir = Path("transcripts-whisper")
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
            results.append({"url": url, "video_id": None, "method": None,
                            "chars": 0, "error": str(e)})
            continue

        print(f"  Video ID: {video_id}")
        text = None
        method = None

        # --- Method 1: Supadata AI (no download needed) ---
        if supadata_key:
            if i > 1:
                time.sleep(2)  # rate limit: 5 req / 10s
            text = transcribe_supadata_chunked(url, supadata_key)
            if text:
                method = "supadata_ai_whisper"
        else:
            print("  [!] SUPADATA_API_KEY not set — skipping Supadata")

        # --- Method 2: Local yt-dlp + Whisper (cookie-gated) ---
        if not text:
            print("  [→] Falling back to local yt-dlp + Whisper …")
            text = transcribe_local(url, video_id)
            if text:
                method = "local_whisper"

        if text:
            fname = out_dir / f"{safe_filename(video_id)}_whisper.txt"
            fname.write_text(text, encoding="utf-8")
            print(f"  [✓] Saved: {fname.name}")
            results.append({"url": url, "video_id": video_id,
                            "method": method, "chars": len(text), "error": None})
            any_success = True
        else:
            print(f"  [✗] All Whisper methods failed for {video_id}")
            results.append({"url": url, "video_id": video_id,
                            "method": None, "chars": 0,
                            "error": "All methods failed"})

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[✓] Whisper summary → {summary_path}")

    sys.exit(0 if any_success else 1)


if __name__ == "__main__":
    main()
