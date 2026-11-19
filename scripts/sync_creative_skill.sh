#!/usr/bin/env bash
# Re-copy the Lonvera script-writing rules into docs/creative/ so the brain indexes them. Run when the sources change.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/creative"
SK="$HOME/.claude/skills/lonvera-brief-prep"
CY="$HOME/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-18-madre-rewrites"
D="$(date +%Y-%m-%d)"
mkdir -p "$OUT"
copy() { # src dest
  { echo "Source of truth: $1; copied $D; re-copy when it changes."; echo; cat "$1"; } > "$OUT/$2"
}
copy "$SK/SKILL.md" lonvera-brief-prep.md
copy "$SK/reference/script-voice.md" script-voice.md
copy "$CY/GRADER_BRIEF.md" script-grader-brief.md
copy "$CY/SELL_HARDER.md" script-sell-harder.md
echo "copied 4 files to $OUT"
