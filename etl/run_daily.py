"""Daily refresh (Railway cron): tracker_sync -> meta_pull (trailing 7 days) -> lifetime_pull (all-time per ad) -> resolve new/unmatched Meta families -> build_metrics.
Every step logs to etl_runs (and the umbrella 'run_daily' row); exits non-zero if any step failed. If the Meta pull fails (token, rate limit)
and --source auto|sheet allows it, ad_daily's trailing days are refreshed from the sheet's Daily tab instead (family grain).
Usage: python -m etl.run_daily [--days 7] [--source auto|meta|sheet] [--snapshot-dir D]"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import backfill_sheet, build_metrics, db, lifetime_pull, meta_api, meta_pull, resolve_families, sheets_io, tracker_sync


def refresh_from_sheet(conn, snapshot_dir=None, days: int = 7) -> dict:
    """Fallback: replace the trailing `days` of ad_daily with the sheet's Daily rows (family grain, source='sheet')."""
    daily = backfill_sheet.parse_daily(sheets_io.read_tab("meta_daily", snapshot_dir))
    if not daily:
        raise RuntimeError("sheet Daily tab returned no rows")
    last = max(d[0] for d in daily)
    first = last - dt.timedelta(days=days - 1)
    rows = [d for d in daily if first <= d[0] <= last]
    conn.execute("DELETE FROM ad_daily WHERE day BETWEEN %s AND %s", (first, last))
    n = db.bulk_copy(conn, "ad_daily", ["ad_id", "day", "meta_family_key", "meta_family", "campaign_id", "spend", "purchases", "conv_value", "link_clicks", "source"],
                     [("fam:" + name.lower(), day, name.lower(), name, "", sp, pu, cv, cl, "sheet") for day, name, sp, pu, cv, cl in rows])
    return {"source": "sheet", "from": first.isoformat(), "to": last.isoformat(), "rows": n}


def step(name, fn, results, fatal=True):
    try:
        out = fn()
        results[name] = {"ok": True, **(out if isinstance(out, dict) else {})}
        return out
    except Exception as e:                                                  # noqa: BLE001
        results[name] = {"ok": False, "error": db.scrub(e)[:300]}
        db.log(f"  step {name} FAILED: {db.scrub(e)[:300]}")
        if fatal:
            raise
        return None


def run(days=7, source="auto", snapshot_dir=None, until=None) -> dict:
    """tracker -> Meta -> resolve -> metrics. Any Meta failure still lets the sheet fallback + metrics finish (data stays fresh) but the run
    is then FAILED loudly: etl_runs.status='failed' and error='meta token rejected' (Graph codes 190/102/463/467) or 'meta pull failed: ...'."""
    results: dict = {}
    meta_error = None
    with db.JobRun("run_daily") as top, db.connect() as conn:
        top.stats = results

        def s_tracker():
            with db.JobRun("tracker_sync") as r:
                st = tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snapshot_dir))
                conn.commit()
                r.stats = {k: v for k, v in st.items() if k != "unknown_products"}
                r.rows_written = st["rows_in_sheet"]
            return {"rows": st["rows_in_sheet"], "inserted": st["inserted"]}
        step("tracker_sync", s_tracker, results)

        def s_meta():
            u = until or meta_pull.default_until()
            since = u - dt.timedelta(days=days - 1)
            d0 = db.get_meta(conn, "daily_start")
            if d0:
                since = max(since, dt.date.fromisoformat(d0))
            with db.JobRun("meta_pull") as r:
                try:
                    st = meta_pull.pull_into_db(conn, since, u, snapshot_dir=snapshot_dir)
                except meta_api.TokenRejected:
                    raise db.JobFailed("meta token rejected") from None
                st.pop("_pulled", None)
                r.stats = st
                r.rows_written = st["rows_inserted"]
                r.data_through = u
                if st["chunks_failed"] or st["chunks_loaded"] == 0:
                    raise db.JobFailed("meta pull incomplete: " + db.scrub(st["chunks_failed"][:2]))
            return {"source": "meta_api", "days": days, "rows": st["rows_inserted"]}

        if source == "sheet":
            step("meta", lambda: _sheet_refresh(conn, snapshot_dir, days), results)
        else:
            try:
                step("meta", s_meta, results)
            except Exception as e:                                          # noqa: BLE001
                conn.rollback()
                meta_error = "meta token rejected" if isinstance(e, (db.JobFailed, meta_api.TokenRejected)) and "token rejected" in str(e) \
                    else "meta pull failed: " + db.scrub(e)[:200]
                if source == "meta":
                    raise db.JobFailed(meta_error) from None
                db.log(f"  {meta_error}; falling back to the sheet's Daily tab (run will still be marked failed)")
                step("meta_fallback", lambda: _sheet_refresh(conn, snapshot_dir, days), results)

        def s_lifetime():
            with db.JobRun("lifetime_pull") as r:
                try:
                    st = lifetime_pull.run(conn)
                except meta_api.TokenRejected:
                    raise db.JobFailed("meta token rejected") from None
                r.stats = st
                r.rows_written = st["inserted"]
            return {"ads": st["ads"], "spend": st["spend"]}
        if source != "sheet" and meta_error != "meta token rejected":       # a rejected token already failed the daily pull; don't retry the heavy call
            try:
                step("lifetime", s_lifetime, results)
            except Exception as e:                                          # noqa: BLE001
                conn.rollback()
                meta_error = meta_error or ("meta token rejected" if "token rejected" in str(e) else "lifetime pull failed: " + db.scrub(e)[:200])
                db.log(f"  {meta_error}; keeping the previous lifetime rows (run will be marked failed)")

        def s_resolve():
            st = resolve_families.resolve(conn)
            conn.commit()
            return st
        step("resolve", s_resolve, results)

        def s_metrics():
            with db.JobRun("build_metrics") as r:
                st = build_metrics.rebuild(conn)
                r.stats = {k: v for k, v in st.items() if k != "by_product"}
                r.data_through = dt.date.fromisoformat(st["data_through"])
            top.data_through = dt.date.fromisoformat(st["data_through"])
            return {"data_through": st["data_through"], "unmatched_families": st["unmatched_families"]}
        step("build_metrics", s_metrics, results)

        if meta_error:                                                      # loud failure AFTER the data was kept fresh from the sheet
            results["failure"] = meta_error
            raise db.JobFailed(meta_error)
    return results


def _sheet_refresh(conn, snapshot_dir, days):
    out = refresh_from_sheet(conn, snapshot_dir, days)
    conn.commit()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--source", choices=["auto", "meta", "sheet"], default="auto")
    ap.add_argument("--snapshot-dir", help="read the sheet from a snapshot instead of live (dev/QA)")
    ap.add_argument("--until")
    a = ap.parse_args()
    try:
        res = run(a.days, a.source, a.snapshot_dir, dt.date.fromisoformat(a.until) if a.until else None)
    except Exception as e:                                                  # noqa: BLE001
        db.log("run_daily FAILED:", db.scrub(e))
        sys.exit(1)
    db.log("run_daily ok:", res)


if __name__ == "__main__":
    main()
