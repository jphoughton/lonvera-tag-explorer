#!/bin/sh
# ETL service entrypoint (Railway cron): migrate, then the daily refresh. Exits non-zero on failure so the
# Railway deployment is marked failed (visible in the dashboard / notifications).
# Usage: run_etl.sh                -> python -m etl.run_daily, then python -m etl.projection_daily (both always run; non-zero if either failed)
#        run_etl.sh <module> [args] -> python -m etl.<module> [args]   (manual one-offs, e.g. `meta_pull --days 185`)
set -eu

python -m etl.migrate
if [ "$#" -eq 0 ]; then
  # run_daily first: it refreshes the ad data projection reads. A failure there must not skip the projection refresh.
  daily_rc=0
  python -m etl.run_daily || daily_rc=$?
  proj_rc=0
  python -m etl.projection_daily || proj_rc=$?
  [ "$daily_rc" -eq 0 ] || echo "[run_etl] etl.run_daily failed (exit ${daily_rc})" >&2
  [ "$proj_rc" -eq 0 ] || echo "[run_etl] etl.projection_daily failed (exit ${proj_rc})" >&2
  if [ "$daily_rc" -ne 0 ]; then exit "$daily_rc"; fi
  exit "$proj_rc"
fi
mod="$1"; shift
exec python -m "etl.${mod}" "$@"
