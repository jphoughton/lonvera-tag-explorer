"""Projection / cohorts loader: seed the cohort table and the Goal curve from the frozen sheet snapshot, and report feed freshness.

    DATABASE_URL=... PYTHONPATH=. python -m etl.projection_cohorts [--snapshot-dir DIR] [--dry-run]

Idempotent: re-running upserts the same rows. The cohort export itself is loaded from the page (POST /api/projection/cohorts/upload) once a
month; there is no daily pull, so `freshness()` reports a monthly-upload feed.

freshness(conn) -> [{"feed": "cohort_export", "label": "Cohort export", "cadence": "monthly-upload", "data_through": <last day of the last fully
closed cohort month with data>, "stale_after_days": 45, ...}] (shared hook contract: app/projection/freshness.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from app.projection import audit, rwdb
from app.projection.cohorts import store
from app.projection.cohorts.engine import COHORT_AGES, CURVE_AGES

SOURCE = "sheet snapshot 2026-11-03"
COHORT_TAB = "Cohort Data - Unified"
WATERFALL_TAB = "Hist Waterfall - Last 4 Months Curve"
CURVES_TAB = "Curves"
FIRST_ROW, LAST_ROW = 2, 34                      # cohort rows Feb-24 .. Oct-26 in the snapshot


def freshness(conn, today: date | None = None) -> list[dict]:
    return store.freshness(conn, today)


def _sr():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts" / "projection"))
    import snapshot_read                           # noqa: PLC0415
    return snapshot_read


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def read_snapshot() -> tuple[list[store.CohortRow], list[float], date]:
    """(cohort rows, Goal curve ages 1..60, Goal snapshot date) read from the snapshot tabs."""
    sr = _sr()
    reference: dict[date, float] = {}
    for r in range(6, 6 + 71):                        # Hist Waterfall layer 1: cohort month + typed new-customer revenue
        a, b = sr.cell(WATERFALL_TAB, f"A{r}"), _num(sr.cell(WATERFALL_TAB, f"B{r}"))
        if _num(a) is not None and b is not None:
            reference[sr.serial_to_date(a)] = b
    rows: list[store.CohortRow] = []
    for r in range(FIRST_ROW, LAST_ROW + 1):
        v = sr.row(COHORT_TAB, r, 1, 7 + COHORT_AGES)
        if _num(v[0]) is None:
            continue
        month = sr.serial_to_date(v[0])
        cum = {a: _num(x) for a, x in enumerate(v[7:7 + COHORT_AGES], start=1) if _num(x)}          # blank and zero = not observed
        rows.append(store.CohortRow(month=month, new_customers=_num(v[1]) or 0.0, first_order=_num(v[6]) or None,
                                    first_order_realized=_num(v[5]) or None, repeat_90d=_num(v[2]), cac=_num(v[3]), payback_months=_num(v[4]),
                                    nc_rev_reference=reference.get(month), cum=cum))
    goal = [_num(x) or 0.0 for x in sr.col(CURVES_TAB, "B", 4, 3 + CURVE_AGES)]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(sr.cell(CURVES_TAB, "B3") or ""))
    return rows, goal, date.fromisoformat(m.group(1)) if m else date(2026, 9, 29)


def seed(conn, rows=None, goal=None, snapshot_date=None, source: str = SOURCE) -> dict:
    """Upsert the cohort table and the Goal curve. Returns counts."""
    if rows is None:
        rows, goal, snapshot_date = read_snapshot()
    counts = store.upsert_cohorts(conn, rows, source)
    n_goal = store.replace_goal(conn, snapshot_date, goal, note=f"Curves tab, Goal curve ({source})") if goal else 0
    audit.record(conn, actor=None, scenario=None, action="cohorts.seed", note=source,
                 new={**counts, "goal_ages": n_goal, "goal_snapshot": snapshot_date.isoformat() if snapshot_date else None})
    return {**counts, "goal_ages": n_goal}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed projection cohort tables from the sheet snapshot.")
    ap.add_argument("--snapshot-dir", help="override PROJECTION_SNAPSHOT_DIR")
    ap.add_argument("--dry-run", action="store_true", help="read the snapshot and print counts; write nothing")
    args = ap.parse_args(argv)
    if args.snapshot_dir:
        import os
        os.environ["PROJECTION_SNAPSHOT_DIR"] = args.snapshot_dir
    rows, goal, snap = read_snapshot()
    print(f"snapshot: {len(rows)} cohorts ({rows[0].month} .. {rows[-1].month}), {sum(len(r.cum) for r in rows)} cells, goal curve {snap}")
    if args.dry_run:
        return 0
    with rwdb.get_rw() as conn:
        print(seed(conn, rows, goal, snap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
