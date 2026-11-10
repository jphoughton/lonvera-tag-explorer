#!/bin/sh
# `backup` service entrypoint (Railway cron, 08:30 UTC daily): dump -> Drive -> re-download -> restore into a scratch Postgres -> compare row counts -> prune.
# Exits non-zero (Railway shows Failed, etl_runs job 'backup' = failed, the alerts job flags it) when any step fails. No migrate step: it only reads the database.
# Usage: run_backup.sh [args for python -m etl.backup]   e.g. run_backup.sh --dry-run
set -eu
exec python -m etl.backup "$@"
