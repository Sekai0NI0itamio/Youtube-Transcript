#!/usr/bin/env bash
# setup.sh
# One-time setup: initialises the GitHub repo and pushes the workflow.
# Run this once from the project root after cloning or creating the repo.
#
# Prerequisites:
#   - git installed
#   - gh CLI installed and authenticated (gh auth login)
#   - You are inside the project directory

set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${CYAN}[→]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }

# ---------------------------------------------------------------------------
echo ""
echo "=================================================="
echo "  YouTube-to-Transcript  –  One-time Setup"
echo "=================================================="
echo ""

# Check gh
if ! command -v gh &>/dev/null; then
    err "GitHub CLI (gh) is not installed."
    echo "  Install from: https://cli.github.com"
    exit 1
fi
ok "gh CLI found."

# Check auth
if ! gh auth status &>/dev/null; then
    err "Not authenticated. Run: gh auth login"
    exit 1
fi
ok "gh CLI authenticated."

# Check git
if ! command -v git &>/dev/null; then
    err "git is not installed."
    exit 1
fi
ok "git found."

# ---------------------------------------------------------------------------
# Ensure we have a git repo
if [ ! -d ".git" ]; then
    info "Initialising git repository …"
    git init
    git branch -M main
fi

# ---------------------------------------------------------------------------
# Create GitHub repo if no remote exists
if ! git remote get-url origin &>/dev/null; then
    echo ""
    read -rp "Enter a name for the new GitHub repository (e.g. yt-transcript): " REPO_NAME
    REPO_NAME="${REPO_NAME:-yt-transcript}"

    read -rp "Make it private? [Y/n]: " PRIVATE_CHOICE
    PRIVATE_CHOICE="${PRIVATE_CHOICE:-Y}"

    if [[ "$PRIVATE_CHOICE" =~ ^[Yy]$ ]]; then
        VISIBILITY="--private"
    else
        VISIBILITY="--public"
    fi

    info "Creating GitHub repository '$REPO_NAME' …"
    gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push
    ok "Repository created and code pushed."
else
    ok "Remote 'origin' already configured."
fi

# ---------------------------------------------------------------------------
# Remind about the Supadata secret
echo ""
echo "=================================================="
echo "  IMPORTANT: Add your Supadata API key as a secret"
echo "=================================================="
echo ""
warn "If you want AI-based transcription (for videos without captions),"
warn "you need to add your Supadata API key to GitHub Secrets."
echo ""
echo "  1. Sign up for a free key at: https://supadata.ai"
echo "  2. Then run:"
echo ""
echo "     gh secret set SUPADATA_API_KEY"
echo ""
echo "  (You will be prompted to paste the key securely.)"
echo ""

read -rp "Do you have a Supadata API key to add now? [y/N]: " ADD_SECRET
ADD_SECRET="${ADD_SECRET:-N}"

if [[ "$ADD_SECRET" =~ ^[Yy]$ ]]; then
    gh secret set SUPADATA_API_KEY
    ok "Secret SUPADATA_API_KEY saved."
else
    warn "Skipping. You can add it later with: gh secret set SUPADATA_API_KEY"
fi

# ---------------------------------------------------------------------------
# Commit and push any uncommitted files
echo ""
info "Committing and pushing project files …"

git add -A
if git diff --cached --quiet; then
    ok "Nothing new to commit."
else
    git commit -m "chore: add YouTube-to-Transcript workflow and scripts"
    git push -u origin main
    ok "Files pushed to GitHub."
fi

# ---------------------------------------------------------------------------
echo ""
echo "=================================================="
ok "Setup complete!"
echo "=================================================="
echo ""
echo "  To transcribe YouTube videos, run:"
echo ""
echo "    python run.py <url1> [url2] [url3] ..."
echo ""
echo "  Example:"
echo "    python run.py https://youtu.be/dQw4w9WgXcQ"
echo ""
