"""Daily refresh (Railway cron), API-driven and ATOMIC:
  1. tracker_sync (metadata only; a refusal is remembered and fails the run at the end, the Meta part still runs on the last good tracker rows)
  2. READ phase (no database writes): Meta ad x day for the trailing 28 days (Meta restates conversions for weeks), all-time ad lifetime ending at the last
     complete day, ad status/name/created for every ad with delivery in the last 60 days or a non-terminal status
  3. WRITE phase, ONE transaction: replace those days, replace lifetime, update ads, resync names, resolve new/unmatched Meta families, rebuild metrics
Any Meta failure (token rejected -> error 'meta token rejected', rate limit, reconcile failure ...) aborts before step 3: existing data stays untouched, the run is
recorded failed in etl_runs, exit code 1. There is NO sheet fallback. app_meta: last_ok_at = finish time of the last FULLY successful run, refresh_status
('ok'|'failed'), last_error (scrubbed).
Usage: python -m etl.run_daily [--days 28] [--until D] [--force-tracker]"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import build_metrics, db, lifetime_pull, meta_api, meta_pull, names, resolve_families, sheets_io, status_refresh, tracker_sync

DEFAULT_DAYS = 28


def mark(status: str, error: str = "") -> None:
    """Refresh bookkeeping on its own autocommit connection (works even if the main transaction failed)."""
    with db.connect(autocommit=True) as c:
        db.set_meta(c, "refresh_status", status)
        db.set_meta(c, "last_error", db.scrub(error)[:500])
        if status == "ok":
            db.set_meta(c, "last_ok_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))


def meta_failure(e: Exception, what: str) -> db.JobFailed:
    if isinstance(e, meta_api.TokenRejected) or "token rejected" in str(e):
        return db.JobFailed("meta token rejected")
    if isinstance(e, db.JobFailed):
        return e
    return db.JobFailed(f"{what} failed: {db.scrub(e)[:300]}")


def run(days: int = DEFAULT_DAYS, snapshot_dir=None, until: dt.date | None = None, force_tracker: bool = False) -> dict:
    results: dict = {}
    try:
        with db.JobRun("run_daily") as top, db.connect() as conn:
            top.stats = results
            tracker_error = None

            # ---- 1. tracker metadata (never blocks the Meta numbers) ----
            try:
                with db.JobRun("tracker_sync") as r:
                    st = tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snapshot_dir), force_tracker)
                    conn.commit()
                    r.stats = {k: v for k, v in st.items() if k != "unknown_products"}
                    r.rows_written = st["rows_in_sheet"]
                results["tracker_sync"] = {"ok": True, "rows": st["rows_in_sheet"], "inserted": st["inserted"], "deleted_now": st["deleted_now"]}
            except Exception as e:                                            # noqa: BLE001
                conn.rollback()
                tracker_error = db.scrub(e)[:300] if isinstance(e, db.JobFailed) else "tracker sync failed: " + db.scrub(e)[:300]
                results["tracker_sync"] = {"ok": False, "error": tracker_error}
                db.log(f"  {tracker_error} (continuing with the last good tracker rows; the run will be marked failed)")

            # ---- 2. READ phase: Graph only, nothing is written ----
            u = until or meta_pull.default_until()
            since = u - dt.timedelta(days=days - 1)
            d0 = db.get_meta(conn, "daily_start")
            if d0:
                since = max(since, dt.date.fromisoformat(d0))
            tok = None
            try:
                tok = meta_api.get_token()
                with db.JobRun("meta_pull") as r:
                    pst, _ = meta_pull.run_pull(conn, since, u, atomic=True, load=False, tok=tok)
                    if pst["chunks_failed"] or pst["chunks_loaded"] == 0:
                        raise db.JobFailed("meta pull incomplete: " + db.scrub(pst["chunks_failed"][:2]))
                    pending = pst.pop("_pending")
                    r.stats = {k: v for k, v in pst.items() if k != "recon"}
                    r.data_through = u
                results["meta_pull"] = {"ok": True, "days": days, "since": since.isoformat(), "until": u.isoformat()}
            except Exception as e:                                            # noqa: BLE001
                raise meta_failure(e, "meta pull") from None
            try:
                with db.JobRun("lifetime_pull") as r:
                    agg, recon = lifetime_pull.pull(tok, u)
                    lifetime_pull.check_recon(recon)
                    r.stats = {"ads": len(agg), "recon": recon}
                results["lifetime_pull"] = {"ok": True, "ads": len(agg)}
            except Exception as e:                                            # noqa: BLE001
                raise meta_failure(e, "lifetime pull") from None
            try:
                with db.JobRun("status_refresh") as r:
                    new_ids = {a["ad_id"] for agg_, _, _ in pending for a in agg_.values()} | set(agg)
                    srows, sst = status_refresh.fetch_updates(conn, tok, extra_ids=new_ids)
                    r.stats = sst
                results["status_refresh"] = {"ok": True, **{k: sst[k] for k in ("asked", "status_changed", "renamed", "created_changed")}}
            except Exception as e:                                            # noqa: BLE001
                raise meta_failure(e, "status refresh") from None

            # ---- 3. WRITE phase: one transaction ----
            with db.JobRun("build_metrics") as r:
                pending_stats = {"rows_inserted": 0, "rows_deleted": 0}
                meta_pull.apply_pending(conn, pending, pending_stats)
                lifetime_pull.load(conn, agg)
                status_refresh.apply_updates(conn, srows)
                names.resync(conn)
                results["resolve"] = {"ok": True, **resolve_families.resolve(conn)}
                st = build_metrics.rebuild(conn, commit=False)
                conn.commit()
                r.stats = {k: v for k, v in st.items() if k not in ("by_product", "unmatched_by_product")}
                r.data_through = dt.date.fromisoformat(st["data_through"])
                top.data_through = r.data_through
            results["build_metrics"] = {"ok": True, "data_through": st["data_through"], "launch_total": st["launch_total"],
                                        "rows_replaced": pending_stats["rows_inserted"]}
            if tracker_error:
                raise db.JobFailed(tracker_error)
    except Exception as e:                                                    # noqa: BLE001
        msg = str(e) if isinstance(e, db.JobFailed) else f"{type(e).__name__}: {e}"
        mark("failed", msg)
        raise
    mark("ok")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--snapshot-dir", help="read the tracker from a snapshot instead of live (dev/QA)")
    ap.add_argument("--until")
    ap.add_argument("--force-tracker", action="store_true", help="skip the tracker blank-family / row-count guards")
    a = ap.parse_args()
    try:
        res = run(a.days, a.snapshot_dir, dt.date.fromisoformat(a.until) if a.until else None, a.force_tracker)
    except Exception as e:                                                    # noqa: BLE001
        db.log("run_daily FAILED:", db.scrub(e))
        sys.exit(1)
    db.log("run_daily ok:", res)


if __name__ == "__main__":
    main()
