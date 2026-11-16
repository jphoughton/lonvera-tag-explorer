#!/bin/sh
# `tagging` service entrypoint (Railway cron, DAILY `0 18 * * *` UTC, after the nightly etl): 
#   every day  : transcribe the video ads on Ready to launch cards (etl.transcribe_ads: Drive download + faster-whisper, up to 20 new ads, stored once, so a backlog clears over a few nights).
#   Sundays UTC: also tag NEW competitor ads (etl.competitor_tags) and NEW own ad families (etl.own_ad_tags), after the transcripts so the tagger reads them. Incremental: both skip anything already
#                tagged at the current tag version, so a quiet week finds 0 to a few. TAGGING_FORCE=1 runs them on any day (manual re-run).
# Runs on the `tagging` image flavor (Dockerfile IMAGE_FLAVOR=tagging: jobs + faster-whisper + yt-dlp + baked whisper model), never on the nightly etl image.
# Auth is CLAUDE_CODE_OAUTH_TOKEN only (the local-login switches OWN_TAGS_LOCAL_CLAUDE_LOGIN / LP_LOCAL_CLAUDE_LOGIN are unset here on purpose).
# Every step that applies always runs; the exit code is non-zero if the preflight, any step, or (when it is the only failure) migrate failed, so Railway shows Failed and etl_runs / alerts carry it
# (jobs transcribe_ads, competitor_tags, own_ad_tags). Extra arguments go to every module (e.g. --dry-run).
# Usage: run_tagging.sh [--dry-run]
set -u
unset OWN_TAGS_LOCAL_CLAUDE_LOGIN LP_LOCAL_CLAUDE_LOGIN
rc=0
python -m etl.migrate || { mig_rc=$?; echo "[run_tagging] etl.migrate failed (exit ${mig_rc})" >&2; rc=$mig_rc; }
tag_day=0
if [ "${TAGGING_FORCE:-0}" = "1" ] || [ "$(date -u +%u)" = "7" ]; then tag_day=1; fi
# Preflight: a broken image must fail loudly, not silently fall back to picture-only tagging.
pre_rc=0
python -c "import faster_whisper" || pre_rc=$?
command -v ffmpeg >/dev/null 2>&1 || { echo "[run_tagging] ffmpeg missing" >&2; pre_rc=1; }
if [ "$tag_day" -eq 1 ]; then
  command -v yt-dlp >/dev/null 2>&1 || { echo "[run_tagging] yt-dlp missing" >&2; pre_rc=1; }
  command -v claude >/dev/null 2>&1 || { echo "[run_tagging] claude CLI missing" >&2; pre_rc=1; }
fi
[ "$pre_rc" -eq 0 ] || { echo "[run_tagging] preflight failed: image is missing the tagging extras" >&2; exit 97; }
tr_rc=0
python -m etl.transcribe_ads "$@" || tr_rc=$?
ct_rc=0
own_rc=0
if [ "$tag_day" -eq 1 ]; then
  python -m etl.competitor_tags --limit 80 "$@" || ct_rc=$?
  python -m etl.own_ad_tags --no-import --limit 150 "$@" || own_rc=$?
fi
[ "$tr_rc" -eq 0 ] || echo "[run_tagging] etl.transcribe_ads failed (exit ${tr_rc}); see etl_runs job transcribe_ads" >&2
[ "$ct_rc" -eq 0 ] || echo "[run_tagging] etl.competitor_tags failed (exit ${ct_rc}); see etl_runs job competitor_tags" >&2
[ "$own_rc" -eq 0 ] || echo "[run_tagging] etl.own_ad_tags failed (exit ${own_rc}); see etl_runs job own_ad_tags" >&2
if [ "$tr_rc" -ne 0 ]; then exit "$tr_rc"; fi
if [ "$ct_rc" -ne 0 ]; then exit "$ct_rc"; fi
if [ "$own_rc" -ne 0 ]; then exit "$own_rc"; fi
exit "$rc"
