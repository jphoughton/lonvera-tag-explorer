#!/bin/sh
# `live` service entrypoint (Railway cron, `0 * * * *`): migrate (applies 192 once), then one Upstack pull of today so far -> proj_live_today_snapshots, then the asset sync.
# Exits non-zero when Upstack fails or returns something unusable (nothing is written then; the last good snapshot stays), so Railway shows Failed.
# etl.asset_sync (copies media linked from Production tickets into Drive "Lonvera - Assets"; see its docstring) is NON-FATAL: its failure is logged (etl_runs job asset_sync) and never changes the exit code.
# Usage: run_live.sh [--dry-run]
set -u
python -m etl.migrate || exit $?
live_rc=0
python -m etl.live_today "$@" || live_rc=$?
if [ "$#" -eq 0 ]; then
  python -m etl.asset_sync || echo "[run_live] etl.asset_sync failed (exit $?), non-fatal; see etl_runs job asset_sync" >&2
fi
exit "$live_rc"
