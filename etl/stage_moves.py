"""Rebuild the Test to scale tables (creative_stage_moves*, creative_scaling_placements*) from family_daily / ad_daily in ONE transaction.

Runs in the nightly etl after run_daily (family_daily, ad_daily) and upstack_daily (the Upstack variants), and by hand after a migration or a backfill:
    python -m etl.stage_moves
The SQL is the functions refresh_creative_stage_moves() (migration 016) and refresh_scaling_benchmarks() (migration 193); readers keep seeing the previous rows until this transaction commits. A failure is
recorded in etl_runs as 'failed' and exits 1, so the Railway deployment shows red; the page keeps serving the last good rows and says how old they are."""
from __future__ import annotations

import datetime as dt
import sys
import time

from . import db

TABLES = ("creative_stage_moves", "creative_stage_moves_upstack_nc", "creative_stage_moves_upstack_all",
          "creative_scaling_placements", "creative_scaling_placements_upstack_nc", "creative_scaling_placements_upstack_all", "scaling_benchmarks")


def refresh(conn) -> dict:
    """Run the refresh inside the caller's transaction (the caller commits). Returns row counts and the day the rows run through."""
    t = time.time()
    conn.execute("SELECT refresh_creative_stage_moves()")
    conn.execute("SELECT refresh_scaling_benchmarks()")                 # migration 193: the per-product scaling averages of the two simple views, same transaction
    stats = {t_: conn.execute(f"SELECT count(*) FROM {t_}").fetchone()[0] for t_ in TABLES}
    through = conn.execute("SELECT value FROM app_meta WHERE key = 'stage_moves_data_through'").fetchone()[0]
    stats["data_through"] = through
    stats["seconds"] = round(time.time() - t, 1)
    if stats["creative_stage_moves"] == 0:
        raise db.JobFailed("stage_moves refresh produced no creatives (family_daily empty?)")
    return stats


def main() -> int:
    with db.JobRun("stage_moves") as run, db.connect() as conn:
        stats = refresh(conn)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["creative_stage_moves"]
        run.data_through = dt.date.fromisoformat(stats["data_through"]) if stats["data_through"] else None
    return 0


if __name__ == "__main__":
    sys.exit(main())
