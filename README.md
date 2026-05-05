# YouTube → Transcript (GitHub Actions)

Convert YouTube videos to text transcripts using GitHub Actions — completely free.

## How it works

1. You run `python run.py` locally with one or more YouTube URLs.
2. It triggers a GitHub Actions workflow in your repo.
3. The workflow tries three transcription methods in order:
   - **YouTube captions** (instant, free, no API key needed)
   - **Supadata API** (free tier: 50/day — for videos without captions)
   - **Local Whisper** (openai-whisper `base` model, runs on the Actions runner)
4. Transcripts are uploaded as a workflow artifact.
5. `run.py` downloads them to `./downloads/<run-id>/` on your machine.

---

## One-time setup

### Prerequisites
- [GitHub CLI](https://cli.github.com) installed and authenticated (`gh auth login`)
- Python 3.11+
- git

### Steps

```bash
# 1. Clone or create the repo, then run the setup script
bash setup.sh
```

The setup script will:
- Create a GitHub repository (if one doesn't exist yet)
- Push all files
- Optionally add your Supadata API key as a GitHub Secret

### Optional: Supadata API key

For videos that don't have official captions, the workflow uses [Supadata.ai](https://supadata.ai) (free tier: 50 transcriptions/day).

1. Sign up at https://supadata.ai and copy your API key.
2. Add it as a GitHub secret:
   ```bash
   gh secret set SUPADATA_API_KEY
   ```

If you skip this, the workflow will fall back to running Whisper locally on the Actions runner (slower but still free).

---

## Usage

```bash
# Single video
python run.py https://youtu.be/dQw4w9WgXcQ

# Multiple videos
python run.py https://youtu.be/dQw4w9WgXcQ https://youtu.be/abc123 https://youtu.be/xyz789
```

### What you get

```
downloads/
└── <run-id>/
    └── transcripts-<run-id>/
        ├── dQw4w9WgXcQ.txt      ← transcript text
        ├── abc123.txt
        └── summary.json         ← metadata (method used, errors, etc.)
```

---

## File overview

| File | Purpose |
|------|---------|
| `run.py` | Local CLI — triggers the workflow and downloads results |
| `transcribe_youtube.py` | Runs inside GitHub Actions — does the actual transcription |
| `.github/workflows/transcribe.yml` | The Actions workflow definition |
| `setup.sh` | One-time repo + secret setup |
| `requirements.txt` | Python deps (auto-installed by the workflow) |

---

## Transcription methods

| Method | Speed | Requires |
|--------|-------|---------|
| YouTube captions | Instant | Nothing |
| Supadata API | ~30s | Free API key |
| Local Whisper (base) | 2–10 min | Nothing (uses Actions runner CPU) |

---

## Privacy notes

- Audio is only uploaded to Supadata if the YouTube captions route fails and you have a `SUPADATA_API_KEY` set.
- Your API key is stored as an encrypted GitHub Secret and never appears in logs.
- Transcripts are stored as workflow artifacts for 7 days, then auto-deleted by GitHub.
