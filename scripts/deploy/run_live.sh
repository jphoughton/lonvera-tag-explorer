#!/bin/sh
# `live` service entrypoint (Railway cron, `0 * * * *`): migrate (applies 192 once), then one Upstack pull of today so far -> proj_live_today_snapshots.
# Exits non-zero when Upstack fails or returns something unusable (nothing is written then; the last good snapshot stays), so Railway shows Failed.
# Usage: run_live.sh [--dry-run]
set -eu
python -m etl.migrate
exec python -m etl.live_today "$@"
