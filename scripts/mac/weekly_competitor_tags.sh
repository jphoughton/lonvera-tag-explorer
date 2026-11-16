#!/bin/zsh
# Weekly Mac routine (Sunday ~09:00 local): tag NEW competitor ads and NEW own ad families. Incremental: both modules skip anything already tagged at the current
# tag version, so a quiet week finds 0 to a few items. These need yt-dlp + faster-whisper, which are not in the Railway image, hence the Mac.
# Runs through `railway run -s etl` (etl service variables incl. CLAUDE_CODE_OAUTH_TOKEN, DATABASE_PUBLIC_URL) with DATABASE_URL = DATABASE_PUBLIC_URL. Under caffeinate.
# Exit code is non-zero if either step fails (the scheduled task reports it). Extra args are passed to both modules (e.g. --dry-run).
# Usage: scripts/mac/weekly_competitor_tags.sh [--dry-run]
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:/usr/local/bin:$PATH"
LINK="$HOME/lonvera-tag-explorer"                       # railway-linked checkout (project lonvera-tag-explorer, production)
RUN="$HOME/lonvera-tag-explorer-routine"                # detached worktree at origin/build/v1, refreshed every run
PY="$HOME/lonvera-tag-explorer/.venv/bin/python"
WH="$HOME/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-04-bl-linemap/whisperenv"
LOG="$HOME/lonvera-tag-explorer-data/logs"; mkdir -p "$LOG"
OUT="$LOG/weekly_competitor_tags_$(date +%Y-%m-%d_%H%M).log"
exec > >(tee -a "$OUT") 2>&1
echo "[weekly_competitor_tags] start $(date) args: $*"

cd "$LINK" || exit 90
git fetch -q origin build/v1 || { echo "git fetch failed"; exit 91; }
if [ ! -d "$RUN" ]; then git worktree add --detach "$RUN" origin/build/v1 || exit 92; else git -C "$RUN" checkout -q --detach origin/build/v1 || exit 93; fi
echo "[weekly_competitor_tags] code at $(git -C "$RUN" rev-parse --short HEAD)"

rc=0
step() {   # step <module> [args]
  echo "[weekly_competitor_tags] >>> $*"
  caffeinate -dimsu railway run -s etl -- /bin/zsh -c '
    cd "$1" && shift
    export DATABASE_URL="$DATABASE_PUBLIC_URL" PYTHONPATH=.:"'"$WH"'/lib/python3.14/site-packages" \
      OWN_TAGS_LOCAL_CLAUDE_LOGIN=1 LP_LOCAL_CLAUDE_LOGIN=1 YT_DLP_BIN="'"$WH"'/bin/yt-dlp"
    exec "'"$PY"'" -m "$@"' _ "$RUN" "$@"
  local r=$?
  if [ $r -ne 0 ]; then echo "[weekly_competitor_tags] FAILED: $1 exit $r" >&2; rc=$r; fi
}
step etl.competitor_tags --limit 80 "$@"
step etl.own_ad_tags --no-import --limit 150 "$@"
echo "[weekly_competitor_tags] done rc=$rc $(date)"
[ $rc -ne 0 ] && osascript -e 'display notification "weekly_competitor_tags failed, see ~/lonvera-tag-explorer-data/logs" with title "Lonvera tagging"' 2>/dev/null
exit $rc
