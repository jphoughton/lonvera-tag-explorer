#!/bin/sh
# `alerts` service entrypoint (Railway cron, `0 * * * *`): migrate (applies 197 once), then the read-only checks + Slack notifications (python -m etl.alerts).
# The alerts job runs even when migrate fails (it then reports the problem itself) and exits non-zero when either step failed, so Railway shows Failed.
# Usage: run_alerts.sh [--dry-run | --test]
set -u
mig_rc=0
python -m etl.migrate || mig_rc=$?
alerts_rc=0
python -m etl.alerts "$@" || alerts_rc=$?
[ "$mig_rc" -eq 0 ] || echo "[run_alerts] etl.migrate failed (exit ${mig_rc})" >&2
if [ "$alerts_rc" -ne 0 ]; then exit "$alerts_rc"; fi
exit "$mig_rc"
