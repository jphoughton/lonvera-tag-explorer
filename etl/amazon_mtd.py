"""Nightly Amazon month-to-date snapshot: complete the month's Amazon revenue and store one row in proj_amazon_mtd_snapshots.

    python -m etl.amazon_mtd [--dry-run] [--month YYYY-MM] [--lag 1]

Reads proj_feed_amazon (so run it AFTER the Amazon feed load), completes the month holding the last complete day with the rule in
app/projection/pacing/amazon_mtd.py (missing days projected at the trailing 7-day average of the reported days), and replaces today's row (idempotent per
Pacific day). Pacing reads the snapshot when it is current and computes live otherwise, so a failed run never leaves a wrong number on the page.
Writes nothing on any error (one transaction) and exits 1. A stale feed is a state worth recording (status 'stale'), not an error; the feed freshness
checks already report it. --dry-run prints and never writes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from app.projection.pacing import amazon_mtd as amz
from app.projection.pacing.levers import Levers
from app.projection.pacing.periods import data_through as dthru, month_start


def log(*a) -> None:
    print("[amazon_mtd]", *a, flush=True)


def run(conn, now_utc: dt.datetime | None = None, lag: int | None = None, month: dt.date | None = None, write: bool = True) -> dict:
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    today = amz.pacific_today(now)
    thr = dthru(today, lag if lag is not None else Levers().data_lag_days)
    series = amz.load_series(conn, thr)
    if not any(v > 0 for v in series.values()):
        raise RuntimeError("proj_feed_amazon holds no Amazon revenue in the last 45 days; nothing written")
    res = amz.compute(series, today, thr, month or month_start(thr))
    if write:
        amz.save_snapshot(conn, res, today, now)
    return res


def main(argv: list[str] | None = None, conn=None, now_utc: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--month")
    ap.add_argument("--lag", type=int)
    a = ap.parse_args(argv)
    try:
        month = dt.date.fromisoformat(a.month + "-01") if a.month else None
        if conn is not None:
            res = run(conn, now_utc, a.lag, month, write=not a.dry_run)
            if not a.dry_run:
                conn.commit()
        else:
            from etl import db
            with db.connect() as c:
                res = run(c, now_utc, a.lag, month, write=not a.dry_run)
                if not a.dry_run:
                    c.commit()
        log(res["note"] or res["status"], f"(month {res['month']}, projected MTD ${res['projected_mtd']:,.2f}, {'dry run, nothing written' if a.dry_run else 'saved'})")
        return 0
    except Exception as e:                                                  # noqa: BLE001  (no stack trace: it may carry a connection string)
        try:
            if conn is not None:
                conn.rollback()
        except Exception:                                                   # noqa: BLE001
            pass
        from etl.feeds.common import scrub
        print(f"[amazon_mtd] failed: {type(e).__name__}: {scrub(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
