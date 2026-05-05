#!/usr/bin/env python3
"""
run.py  –  Local CLI for the YouTube-to-Transcript GitHub Actions workflow.

Usage:
    python run.py <url1> [url2] [url3] ...

What it does:
  1. Validates that `gh` (GitHub CLI) is installed and authenticated.
  2. Detects the current GitHub repository from `git remote`.
  3. Triggers the `transcribe.yml` workflow_dispatch with your URLs.
  4. Polls the workflow run until it completes (or fails).
  5. Downloads the transcript artifact to ./downloads/<run-id>/.
  6. Prints a summary of every transcript file.

Requirements:
    pip install requests          (only used for optional direct API calls)
    gh CLI  (https://cli.github.com) – must be installed and `gh auth login` run.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Colour helpers (gracefully degrade on Windows / dumb terminals)
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty() and os.name != "nt"

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def ok(msg):   print(_c("32", f"[✓] {msg}"))
def info(msg): print(_c("36", f"[→] {msg}"))
def warn(msg): print(_c("33", f"[!] {msg}"))
def err(msg):  print(_c("31", f"[✗] {msg}"))


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], capture=True, check=True) -> subprocess.CompletedProcess:
    """Run a command, returning the CompletedProcess. Raises on non-zero exit if check=True."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )


def gh(*args, capture=True, check=True) -> subprocess.CompletedProcess:
    """Convenience wrapper for `gh` CLI calls."""
    return run(["gh", *args], capture=capture, check=check)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def check_gh_installed():
    if shutil.which("gh") is None:
        err("GitHub CLI (`gh`) is not installed.")
        print("  Install it from https://cli.github.com and run `gh auth login`.")
        sys.exit(1)
    ok("gh CLI found.")


def check_gh_auth():
    result = gh("auth", "status", check=False)
    if result.returncode != 0:
        err("Not authenticated with GitHub CLI.")
        print("  Run: gh auth login")
        sys.exit(1)
    ok("gh CLI authenticated.")


def detect_repo() -> str:
    """Return 'owner/repo' from the current git remote."""
    try:
        result = run(["git", "remote", "get-url", "origin"])
        remote = result.stdout.strip()
    except subprocess.CalledProcessError:
        err("Could not read git remote 'origin'. Are you inside the repo directory?")
        sys.exit(1)

    # SSH:   git@github.com:owner/repo.git
    # HTTPS: https://github.com/owner/repo.git
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote)
    if not match:
        err(f"Remote URL doesn't look like a GitHub repo: {remote}")
        sys.exit(1)

    repo = match.group(1)
    ok(f"Repository: {repo}")
    return repo


# ---------------------------------------------------------------------------
# Workflow trigger
# ---------------------------------------------------------------------------

def trigger_workflow(repo: str, urls: list[str]) -> str:
    """
    Trigger the transcribe workflow and return the new run ID.
    We capture the run list *before* and *after* to identify the new run.
    """
    workflow_file = "transcribe.yml"
    urls_input = " ".join(urls)

    # Snapshot existing run IDs so we can identify the new one
    before_ids = _list_run_ids(repo, workflow_file)

    info(f"Triggering workflow with {len(urls)} URL(s) …")
    gh(
        "workflow", "run", workflow_file,
        "--repo", repo,
        "--field", f"youtube_urls={urls_input}",
    )

    # Poll until a new run appears (GitHub takes a few seconds to register it)
    info("Waiting for the workflow run to be registered …")
    run_id = None
    for attempt in range(30):
        time.sleep(3)
        after_ids = _list_run_ids(repo, workflow_file)
        new_ids = [rid for rid in after_ids if rid not in before_ids]
        if new_ids:
            run_id = new_ids[0]
            break
        if attempt % 5 == 4:
            info(f"  Still waiting … ({(attempt+1)*3}s)")

    if not run_id:
        err("Timed out waiting for the workflow run to appear.")
        sys.exit(1)

    ok(f"Workflow run started: {run_id}")
    print(f"  View in browser: https://github.com/{repo}/actions/runs/{run_id}")
    return run_id


def _list_run_ids(repo: str, workflow_file: str) -> list[str]:
    result = gh(
        "run", "list",
        "--repo", repo,
        "--workflow", workflow_file,
        "--limit", "10",
        "--json", "databaseId",
        check=False,
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return [str(item["databaseId"]) for item in data]
    except (json.JSONDecodeError, KeyError):
        return []


# ---------------------------------------------------------------------------
# Poll until complete
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"completed", "failure", "cancelled", "timed_out", "skipped"}

def wait_for_run(repo: str, run_id: str) -> str:
    """
    Poll the run status every 15 seconds until it reaches a terminal state.
    Returns the final conclusion string (e.g. 'success', 'failure').
    """
    info(f"Polling run {run_id} …")
    spinner = ["|", "/", "-", "\\"]
    tick = 0
    start = time.time()

    while True:
        result = gh(
            "run", "view", run_id,
            "--repo", repo,
            "--json", "status,conclusion,updatedAt",
            check=False,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            status = data.get("status", "unknown")
            conclusion = data.get("conclusion") or ""
            elapsed = int(time.time() - start)
            print(
                f"\r  {spinner[tick % 4]}  Status: {status:<12}  "
                f"Elapsed: {elapsed}s   ",
                end="",
                flush=True,
            )
            tick += 1
            if status in TERMINAL_STATUSES or conclusion in TERMINAL_STATUSES:
                print()  # newline after spinner
                final = conclusion or status
                if final == "success":
                    ok(f"Workflow completed successfully ({elapsed}s).")
                else:
                    warn(f"Workflow finished with status: {final} ({elapsed}s).")
                return final
        else:
            warn(f"Could not fetch run status (will retry): {result.stderr.strip()[:120]}")

        time.sleep(15)


# ---------------------------------------------------------------------------
# Download artifacts
# ---------------------------------------------------------------------------

def download_artifacts(repo: str, run_id: str) -> Path:
    """Download all artifacts for the run into ./downloads/<run_id>/."""
    dest = Path("downloads") / run_id
    dest.mkdir(parents=True, exist_ok=True)

    info(f"Downloading artifacts to {dest} …")

    # List artifacts
    result = gh(
        "run", "download", run_id,
        "--repo", repo,
        "--dir", str(dest),
        check=False,
    )

    if result.returncode != 0:
        warn(f"gh run download reported an issue:\n  {result.stderr.strip()[:300]}")
    else:
        ok(f"Artifacts downloaded to: {dest.resolve()}")

    return dest


# ---------------------------------------------------------------------------
# Display summary
# ---------------------------------------------------------------------------

def print_summary(dest: Path, run_id: str):
    print()
    print("=" * 60)
    print(f"  TRANSCRIPT DOWNLOAD SUMMARY  (run {run_id})")
    print("=" * 60)

    txt_files = sorted(dest.rglob("*.txt"))
    json_files = sorted(dest.rglob("summary.json"))

    if not txt_files and not json_files:
        warn("No transcript files found in the downloaded artifacts.")
        return

    # Print JSON summary if present
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            print(f"\n  Summary ({jf}):")
            for item in data:
                status = "✓" if item.get("transcript") else "✗"
                method = item.get("method") or "failed"
                vid    = item.get("video_id") or "?"
                url    = item.get("url", "")
                print(f"    [{status}] {vid}  ({method})")
                if item.get("error"):
                    print(f"         Error: {item['error']}")
        except Exception:
            pass

    # List transcript text files
    if txt_files:
        print(f"\n  Transcript files:")
        for tf in txt_files:
            size = tf.stat().st_size
            print(f"    • {tf}  ({size:,} bytes)")

    print()
    ok(f"All files saved under: {dest.resolve()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Example:")
        print("  python run.py https://youtu.be/dQw4w9WgXcQ https://youtu.be/abc123")
        sys.exit(0)

    urls = [u.strip() for u in sys.argv[1:] if u.strip()]
    if not urls:
        err("No URLs provided.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  YouTube → Transcript  (GitHub Actions runner)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"\n  URLs to process ({len(urls)}):")
    for u in urls:
        print(f"    • {u}")
    print()

    # Pre-flight
    check_gh_installed()
    check_gh_auth()
    repo = detect_repo()

    # Trigger
    run_id = trigger_workflow(repo, urls)

    # Wait
    conclusion = wait_for_run(repo, run_id)

    # Download
    dest = download_artifacts(repo, run_id)

    # Summary
    print_summary(dest, run_id)

    sys.exit(0 if conclusion == "success" else 1)


if __name__ == "__main__":
    main()
