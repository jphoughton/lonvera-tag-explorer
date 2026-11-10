#!/bin/sh
# ETL service entrypoint (Railway cron): migrate, then the daily refresh. Exits non-zero on failure so the
# Railway deployment is marked failed (visible in the dashboard / notifications).
# Usage: run_etl.sh                -> python -m etl.run_daily, then etl.upstack_daily (Upstack source), then etl.stage_moves (Test to scale tables), then etl.projection_daily, then etl.amazon_mtd, then etl.competitor_ads --if-sunday (non-fatal), then etl.auto_tag (non-fatal) (all always run; non-zero if any failed)
# Usage: run_etl.sh                -> python -m etl.run_daily, then etl.upstack_daily (Upstack source), then etl.stage_moves (Test to scale tables), then etl.projection_daily, then etl.amazon_mtd, then etl.creative_priorities --if-sunday (non-fatal; runs AFTER the competitor step when that exists, it reads the competitor_concepts view), then etl.auto_tag (non-fatal) (all always run; non-zero if any failed)
#        run_etl.sh <module> [args] -> python -m etl.<module> [args]   (manual one-offs, e.g. `meta_pull --days 185`)
set -eu

python -m etl.migrate
if [ "$#" -eq 0 ]; then
  # run_daily first: it refreshes the ad data projection reads. A failure there must not skip the projection refresh.
  daily_rc=0
  python -m etl.run_daily || daily_rc=$?
  # Upstack source (Meta | Upstack switch): own failure path, reads Meta's refreshed spend; its failure leaves the last good Upstack data in place
  up_rc=0
  python -m etl.upstack_daily || up_rc=$?
  # Test to scale tables: after BOTH data steps (they read family_daily and its Upstack twins); a failure keeps the last good rows and fails the run loudly
  sm_rc=0
  python -m etl.stage_moves || sm_rc=$?
  proj_rc=0
  python -m etl.projection_daily || proj_rc=$?
  # Amazon month-to-date snapshot (safety net for the feeds service's run): after projection_daily, which loads the Amazon feed when it is sheet-fed
  amz_rc=0
  python -m etl.amazon_mtd || amz_rc=$?
  # Competitor ads (public Meta Ad Library via headless Chromium + Claude classification): weekly, `--if-sunday` makes it a no-op on other days (Pacific). Before any step
  # that snapshots the weekly Creative priorities (they read view competitor_concepts). NON-FATAL like auto_tag: failures land in etl_runs (job competitor_ads).
  python -m etl.competitor_ads --if-sunday || echo "[run_etl] etl.competitor_ads failed (exit $?), non-fatal; see etl_runs job competitor_ads" >&2
  # Weekly creative priorities one-sheeter: only does anything on a Pacific-time Sunday, takes seconds, NON-FATAL (recorded in etl_runs job creative_priorities). Before auto_tag so the long tagging run never delays it.
  python -m etl.creative_priorities --if-sunday || echo "[run_etl] etl.creative_priorities failed (exit $?), non-fatal; see etl_runs job creative_priorities" >&2
  # Auto-tagging of new ads (headless Claude on the subscription token), LAST so a long run (up to ~150 ads) never delays the data steps above.
  # NON-FATAL by design: a failure is recorded in etl_runs (job auto_tag) and logged, never changes the exit code. Needs run_daily's tracker rows + metrics.
  python -m etl.auto_tag || echo "[run_etl] etl.auto_tag failed (exit $?), non-fatal; see etl_runs job auto_tag" >&2
  [ "$daily_rc" -eq 0 ] || echo "[run_etl] etl.run_daily failed (exit ${daily_rc})" >&2
  [ "$up_rc" -eq 0 ] || echo "[run_etl] etl.upstack_daily failed (exit ${up_rc})" >&2
  [ "$sm_rc" -eq 0 ] || echo "[run_etl] etl.stage_moves failed (exit ${sm_rc})" >&2
  [ "$proj_rc" -eq 0 ] || echo "[run_etl] etl.projection_daily failed (exit ${proj_rc})" >&2
  [ "$amz_rc" -eq 0 ] || echo "[run_etl] etl.amazon_mtd failed (exit ${amz_rc})" >&2
  if [ "$daily_rc" -ne 0 ]; then exit "$daily_rc"; fi
  if [ "$up_rc" -ne 0 ]; then exit "$up_rc"; fi
  if [ "$sm_rc" -ne 0 ]; then exit "$sm_rc"; fi
  if [ "$proj_rc" -ne 0 ]; then exit "$proj_rc"; fi
  exit "$amz_rc"
fi
mod="$1"; shift
exec python -m "etl.${mod}" "$@"
