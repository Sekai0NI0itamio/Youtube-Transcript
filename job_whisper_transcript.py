#!/usr/bin/env python3
"""
job_whisper_transcript.py
GitHub Actions Job 2 – Heavy audio-based transcription.
Downloads audio with yt-dlp, splits into segments, runs openai-whisper locally.

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
# Step 1 – Download best-audio stream with yt-dlp
# ---------------------------------------------------------------------------

def download_audio(video_url: str, out_path: str) -> bool:
    """
    Download the best audio quality stream (no video) as a WAV file.
    WAV is uncompressed so Whisper doesn't need to re-decode it.
    Falls back to mp3 if WAV post-processing fails.
    """
    print("  [→] Downloading audio with yt-dlp …")

    # Format selector: best audio-only stream, prefer opus/m4a/webm
    # bestaudio picks the highest-bitrate audio-only format available
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--js-runtimes", "nodejs",
        # Best audio quality, no video
        "-f", "bestaudio/best",
        # Extract and convert to WAV (lossless for Whisper)
        "-x", "--audio-format", "wav",
        "--audio-quality", "0",          # 0 = best quality
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",  # 16 kHz mono — Whisper's native rate
        "-o", out_path,
        "--no-progress",
        video_url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [!] yt-dlp stderr:\n{result.stderr[:600]}")
        return False

    print(f"  [✓] Audio downloaded: {out_path}")
    return True


# ---------------------------------------------------------------------------
# Step 2 – Split audio into segments Whisper can handle
# ---------------------------------------------------------------------------

SEGMENT_SECONDS = 600  # 10-minute chunks — well within Whisper's context


def get_audio_duration(audio_path: str) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def split_audio(audio_path: str, segment_dir: str, segment_secs: int = SEGMENT_SECONDS) -> list[str]:
    """
    Split audio_path into fixed-length WAV segments.
    Returns list of segment file paths in order.
    """
    duration = get_audio_duration(audio_path)
    if duration == 0:
        print("  [!] Could not determine audio duration — treating as single segment.")
        return [audio_path]

    n_segments = math.ceil(duration / segment_secs)
    print(f"  [→] Audio duration: {duration:.0f}s → splitting into {n_segments} segment(s) of {segment_secs}s …")

    if n_segments == 1:
        return [audio_path]

    os.makedirs(segment_dir, exist_ok=True)
    segments = []

    for i in range(n_segments):
        start = i * segment_secs
        seg_path = os.path.join(segment_dir, f"seg_{i:04d}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(start),
            "-t", str(segment_secs),
            "-ar", "16000", "-ac", "1",
            seg_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(seg_path):
            segments.append(seg_path)
        else:
            print(f"  [!] ffmpeg segment {i} failed: {result.stderr[:200]}")

    print(f"  [✓] Created {len(segments)} segment(s)")
    return segments


# ---------------------------------------------------------------------------
# Step 3 – Transcribe segments with Whisper
# ---------------------------------------------------------------------------

def transcribe_segments(segments: list[str], model_name: str = "base") -> str | None:
    """
    Load Whisper once and transcribe each segment, then join the results.
    Uses the 'base' model to stay within GitHub Actions runner disk/RAM limits.
    """
    try:
        import whisper
    except ImportError:
        print("  [!] openai-whisper not installed.")
        return None

    print(f"  [→] Loading Whisper model '{model_name}' …")
    try:
        model = whisper.load_model(model_name)
    except Exception as e:
        print(f"  [!] Failed to load Whisper model: {e}")
        return None

    parts = []
    for i, seg in enumerate(segments, 1):
        print(f"  [→] Transcribing segment {i}/{len(segments)}: {os.path.basename(seg)} …")
        try:
            result = model.transcribe(seg, fp16=False, language="en")
            text = result.get("text", "").strip()
            if text:
                parts.append(text)
                print(f"      {len(text):,} chars")
            else:
                print("      (empty)")
        except Exception as e:
            print(f"  [!] Whisper failed on segment {i}: {e}")

    if not parts:
        return None

    full_text = " ".join(parts)
    print(f"  [✓] Whisper total: {len(full_text):,} chars across {len(parts)} segment(s)")
    return full_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python job_whisper_transcript.py <url1> [url2] ...")
        sys.exit(1)

    urls = [u.strip() for u in sys.argv[1:] if u.strip()]

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
            results.append({"url": url, "video_id": None, "chars": 0, "error": str(e)})
            continue

        print(f"  Video ID: {video_id}")

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, f"{video_id}.wav")
            seg_dir    = os.path.join(tmpdir, "segments")

            # Download
            if not download_audio(url, audio_path):
                results.append({
                    "url": url, "video_id": video_id,
                    "chars": 0, "error": "yt-dlp download failed",
                })
                continue

            # Split
            segments = split_audio(audio_path, seg_dir)

            # Transcribe
            text = transcribe_segments(segments)

        if text:
            fname = out_dir / f"{safe_filename(video_id)}_whisper.txt"
            fname.write_text(text, encoding="utf-8")
            print(f"  [✓] Saved: {fname.name}")
            results.append({
                "url": url, "video_id": video_id,
                "chars": len(text), "error": None,
            })
            any_success = True
        else:
            print(f"  [✗] Whisper produced no output for {video_id}")
            results.append({
                "url": url, "video_id": video_id,
                "chars": 0, "error": "Whisper produced no output",
            })

    # Write summary
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[✓] Whisper summary → {summary_path}")

    sys.exit(0 if any_success else 1)


if __name__ == "__main__":
    main()
