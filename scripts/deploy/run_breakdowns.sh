#!/bin/sh
# `breakdowns` service entrypoint (Railway cron, every 2 hours, `0 */2 * * *`): migrate (applies 240 once), then the Meta breakdown pull in service mode:
# `python -m etl.meta_breakdowns --auto` = continue the 365-day backfill (newest 30-day window first) until it is finished, then pull the trailing 7 days.
# Safe to run on a short schedule: a run never starts a request batch between 12:30 and 15:30 UTC (the main nightly Meta pull; override META_BREAKDOWNS_QUIET_UTC),
# stops after --max-minutes (110) with exit 0, and a Postgres advisory lock makes an overlapping run exit 0. Resume needs no arguments (the anchor is stored in the database).
# Exits non-zero only when migrate failed, the Meta token was rejected, or a rate limit outlasted the waits. Meta access is read only (GET; see docs/META_BREAKDOWNS.md).
# Usage: run_breakdowns.sh              -> migrate, then etl.meta_breakdowns --auto
#        run_breakdowns.sh [args]       -> migrate, then etl.meta_breakdowns --auto [args]   (e.g. --max-minutes 60, --restart, --max-days 365)
set -eu

python -m etl.migrate
exec python -m etl.meta_breakdowns --auto "$@"
