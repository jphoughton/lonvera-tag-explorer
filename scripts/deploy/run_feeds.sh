#!/bin/sh
# `feeds` service entrypoint (Railway cron, 21:00 UTC): migrate, pull every feed that is in shadow or direct mode, then (during the dual-run) verify the
# shadow tables against the sheet-fed tables. Exits non-zero when a pull failed, a direct-mode feed is stale, or verification found a failed day, so the
# Railway deployment shows Failed and the dashboard notification fires. Idempotent: every pull replaces its trailing window.
# Usage: run_feeds.sh                       -> run, then verify (FEEDS_VERIFY=0 skips verify)
#        run_feeds.sh <subcommand> [args]   -> python -m etl.feeds <subcommand> [args]   (mode, status, dryrun, lever, verify, ...)
set -eu

python -m etl.migrate
if [ "$#" -eq 0 ]; then
  run_rc=0
  python -m etl.feeds run || run_rc=$?
  ver_rc=0
  if [ "${FEEDS_VERIFY:-1}" != "0" ]; then
    python -m etl.feeds verify || ver_rc=$?
  fi
  [ "$run_rc" -eq 0 ] || echo "[run_feeds] etl.feeds run failed (exit ${run_rc})" >&2
  [ "$ver_rc" -eq 0 ] || echo "[run_feeds] etl.feeds verify failed (exit ${ver_rc})" >&2
  if [ "$run_rc" -ne 0 ]; then exit "$run_rc"; fi
  exit "$ver_rc"
fi
exec python -m etl.feeds "$@"
