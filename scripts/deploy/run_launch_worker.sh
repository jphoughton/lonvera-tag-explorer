#!/bin/sh
# Launch worker entrypoint (NOT wired to any Railway service yet): creates PAUSED Meta ads for /priorities production. Does nothing, and exits 0, unless META_LAUNCH_ENABLED=1
# and META_LAUNCH_TOKEN are set. Every job is a dry run unless META_LAUNCH_DRY_RUN=0 and the job itself was queued as a real launch.
# Usage: run_launch_worker.sh            -> migrate, then poll the queue forever (LAUNCH_WORKER_POLL_SECONDS, default 15)
#        run_launch_worker.sh --once     -> migrate, drain the queue once, exit
#        run_launch_worker.sh --dry-run  -> never write to Meta whatever the job says (combine with --once)
set -eu

python -m etl.migrate
exec python -m etl.launch_worker "$@"
