#!/bin/sh
# ETL service entrypoint (Railway cron): migrate, then the daily refresh. Exits non-zero on failure so the
# Railway deployment is marked failed (visible in the dashboard / notifications).
# Usage: run_etl.sh                -> python -m etl.run_daily
#        run_etl.sh <module> [args] -> python -m etl.<module> [args]   (manual one-offs, e.g. `meta_pull --days 185`)
set -eu

python -m etl.migrate
if [ "$#" -eq 0 ]; then
  exec python -m etl.run_daily
fi
mod="$1"; shift
exec python -m "etl.${mod}" "$@"
