#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# run_daily.sh  —  Wrapper for the Reddit pain-point scraper
#
# Usage:
#   ./run_daily.sh <subreddit>
#   ./run_daily.sh entrepreneur
#   ./run_daily.sh SaaS
#
# Set up as a daily cron job:
#   crontab -e
#   0 7 * * * /path/to/run_daily.sh entrepreneur >> /path/to/logs/scraper.log 2>&1
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBREDDIT="${1:-}"

if [[ -z "$SUBREDDIT" ]]; then
  echo "Usage: $0 <subreddit>"
  exit 1
fi

# ── Load API key from .env if present ────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "ERROR: GEMINI_API_KEY not set. Add it to .env or export it."
  exit 1
fi

# ── Activate virtualenv if it exists ─────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "=========================================="
echo "  Reddit Pain Point Scraper"
echo "  Subreddit : r/$SUBREDDIT"
echo "  Started   : $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

python "$SCRIPT_DIR/scraper.py" "$SUBREDDIT" --output-dir "$SCRIPT_DIR/output"

echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
