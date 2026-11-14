#!/bin/sh
# ETL service entrypoint (Railway cron): migrate, then the daily refresh. Exits non-zero on failure so the
# Railway deployment is marked failed (visible in the dashboard / notifications).
# Usage: run_etl.sh                -> python -m etl.run_daily, then etl.upstack_daily (Upstack source), then etl.stage_moves (Test to scale tables), then etl.projection_daily, then etl.amazon_mtd, then etl.inventory_snapshots (non-fatal), then etl.competitor_ads --if-sunday (non-fatal), then etl.assignment_tags (non-fatal), then etl.production_status (non-fatal), then etl.klaviyo --days 14 (non-fatal), then etl.meta_changes --days 3 (non-fatal), then etl.daily_story (non-fatal), then etl.auto_tag (non-fatal) (all always run; non-zero if any failed)
# Usage: run_etl.sh                -> python -m etl.run_daily, then etl.upstack_daily (Upstack source), then etl.stage_moves (Test to scale tables), then etl.projection_daily, then etl.amazon_mtd, then etl.creative_priorities --if-sunday (non-fatal; runs AFTER the competitor step when that exists, it reads the competitor_concepts view), then etl.assignment_tags (non-fatal), then etl.production_status (non-fatal), then etl.klaviyo --days 14 (non-fatal), then etl.meta_changes --days 3 (non-fatal), then etl.daily_story (non-fatal), then etl.auto_tag (non-fatal) (all always run; non-zero if any failed)
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
  # Daily stock snapshots for Amazon FBA and the international 3PL (the typed count carried forward minus units shipped since, plus landed receipts; real rows are never overwritten).
  # After projection_daily and amazon_mtd, which refresh the units it reads. NON-FATAL (etl_runs job inventory_snapshots).
  python -m etl.inventory_snapshots || echo "[run_etl] etl.inventory_snapshots failed (exit $?), non-fatal; see etl_runs job inventory_snapshots" >&2
  # Competitor ads (public Meta Ad Library via headless Chromium + Claude classification): weekly, `--if-sunday` makes it a no-op on other days (Pacific). Before any step
  # that snapshots the weekly Creative priorities (they read view competitor_concepts). NON-FATAL like auto_tag: failures land in etl_runs (job competitor_ads).
  python -m etl.competitor_ads --if-sunday || echo "[run_etl] etl.competitor_ads failed (exit $?), non-fatal; see etl_runs job competitor_ads" >&2
  # Weekly creative priorities one-sheeter: only does anything on a Pacific-time Sunday, takes seconds, NON-FATAL (recorded in etl_runs job creative_priorities). Before auto_tag so the long tagging run never delays it.
  python -m etl.creative_priorities --if-sunday || echo "[run_etl] etl.creative_priorities failed (exit $?), non-fatal; see etl_runs job creative_priorities" >&2
  # Assignment tags: ads linked to a production assignment get the assignment's line (never overwrites a tag; no-op without the table). Before auto_tag so linked ads get the planned line, not a model guess. NON-FATAL (etl_runs job assignment_tags).
  python -m etl.assignment_tags || echo "[run_etl] etl.assignment_tags failed (exit $?), non-fatal; see etl_runs job assignment_tags" >&2
  # Production board: cards move forward to what their linked Creative Tracker rows show (never backward). NON-FATAL (etl_runs job production_status); no-op before migration 245.
  python -m etl.production_status || echo "[run_etl] etl.production_status failed (exit $?), non-fatal; see etl_runs job production_status" >&2
  # Klaviyo flow + campaign metrics: trailing 14 Pacific days re-upserted so late opens / clicks / orders land. NON-FATAL (etl_runs job klaviyo); without KLAVIYO_API_KEY it logs one line and exits 0.
  python -m etl.klaviyo --days 14 || echo "[run_etl] etl.klaviyo failed (exit $?), non-fatal; see etl_runs job klaviyo" >&2
  # Meta change feeds: activity log of the last 3 days (overlap, deduped) + today's ad set settings snapshot. READ-ONLY Graph GETs. NON-FATAL (etl_runs job meta_changes).
  python -m etl.meta_changes --days 3 || echo "[run_etl] etl.meta_changes failed (exit $?), non-fatal; see etl_runs job meta_changes" >&2
  # Daily story: yesterday's facts, the short story and the pattern memory (docs/DAILY_STORY.md). After Klaviyo (klaviyo_daily) and meta_changes (the change ledger it reads), before auto_tag.
  # NON-FATAL (etl_runs job daily_story); writes a 'data not ready' row naming the source when a core source is not loaded; without ANTHROPIC_API_KEY / the claude CLI it writes the template story.
  # No Slack / email: deliver() is an empty hook.
  python -m etl.daily_story || echo "[run_etl] etl.daily_story failed (exit $?), non-fatal; see etl_runs job daily_story" >&2
  # Tickets added with only a name, a vendor and a brief: the system adds product, market cluster, ICP, pain point and new vs iteration (etl/ticket_tags.py, same headless Claude). Small and quick, before the long ad tagging. NON-FATAL (etl_runs job ticket_tags).
  python -m etl.ticket_tags || echo "[run_etl] etl.ticket_tags failed (exit $?), non-fatal; see etl_runs job ticket_tags" >&2
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
