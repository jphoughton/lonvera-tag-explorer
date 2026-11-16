#!/bin/sh
# `tagging` service entrypoint (Railway cron, Sundays only: `0 18 * * 0` UTC, after the nightly etl's Sunday competitor_ads snapshot): tag NEW competitor ads
# (etl.competitor_tags) and NEW own ad families (etl.own_ad_tags). Incremental: both skip anything already tagged at the current tag version, so a quiet week finds 0 to a few.
# Runs on the `tagging` image flavor (Dockerfile IMAGE_FLAVOR=tagging: jobs + faster-whisper + yt-dlp + baked whisper model), never on the nightly etl image.
# Auth is CLAUDE_CODE_OAUTH_TOKEN only (the local-login switches OWN_TAGS_LOCAL_CLAUDE_LOGIN / LP_LOCAL_CLAUDE_LOGIN are unset here on purpose).
# Both steps always run; the exit code is non-zero if the preflight, either step, or (when it is the only failure) migrate failed, so Railway shows Failed and etl_runs / alerts carry it
# (jobs competitor_tags, own_ad_tags). Extra arguments go to both modules (e.g. --dry-run).
# Usage: run_tagging.sh [--dry-run]
set -u
unset OWN_TAGS_LOCAL_CLAUDE_LOGIN LP_LOCAL_CLAUDE_LOGIN
rc=0
python -m etl.migrate || { mig_rc=$?; echo "[run_tagging] etl.migrate failed (exit ${mig_rc})" >&2; rc=$mig_rc; }
# Preflight: a broken image must fail loudly, not silently fall back to picture-only tagging.
pre_rc=0
python -c "import faster_whisper" || pre_rc=$?
command -v yt-dlp >/dev/null 2>&1 || { echo "[run_tagging] yt-dlp missing" >&2; pre_rc=1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "[run_tagging] ffmpeg missing" >&2; pre_rc=1; }
command -v claude >/dev/null 2>&1 || { echo "[run_tagging] claude CLI missing" >&2; pre_rc=1; }
[ "$pre_rc" -eq 0 ] || { echo "[run_tagging] preflight failed: image is missing the tagging extras" >&2; exit 97; }
ct_rc=0
python -m etl.competitor_tags --limit 80 "$@" || ct_rc=$?
own_rc=0
python -m etl.own_ad_tags --no-import --limit 150 "$@" || own_rc=$?
[ "$ct_rc" -eq 0 ] || echo "[run_tagging] etl.competitor_tags failed (exit ${ct_rc}); see etl_runs job competitor_tags" >&2
[ "$own_rc" -eq 0 ] || echo "[run_tagging] etl.own_ad_tags failed (exit ${own_rc}); see etl_runs job own_ad_tags" >&2
if [ "$ct_rc" -ne 0 ]; then exit "$ct_rc"; fi
if [ "$own_rc" -ne 0 ]; then exit "$own_rc"; fi
exit "$rc"
