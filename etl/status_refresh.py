"""Refresh meta_ads from Graph on every run: effective_status (for every ad with delivery in the last 60 days and every ad whose stored status is not
terminal, i.e. not ARCHIVED/DELETED), the current ad name (a rename moves the ad's whole history to its new family), and created_time (stored exactly in
created_at; created_date is its PACIFIC calendar date). All Graph reads happen first and the table is updated in one go, so a failure leaves meta_ads untouched.
Sets app_meta.status_refreshed_at. Usage: python -m etl.status_refresh [--created-limit N] [--dry-run]"""
from __future__ import annotations

import argparse
import datetime as dt

from . import db, meta_api
from .resolver import fam

TERMINAL = ("ARCHIVED", "DELETED")


def targets(conn, data_through: dt.date, created_limit: int = 5000) -> dict[str, list[str]]:
    since = data_through - dt.timedelta(days=59)
    recent = [r[0] for r in conn.execute("SELECT DISTINCT ad_id FROM ad_daily WHERE day >= %s AND ad_id NOT LIKE 'fam:%%'", (since,)).fetchall()]
    live = [r[0] for r in conn.execute("SELECT ad_id FROM meta_ads WHERE status IS NULL OR status <> ALL(%s)", (list(TERMINAL),)).fetchall()]
    missing = [r[0] for r in conn.execute("SELECT ad_id FROM meta_ads WHERE created_at IS NULL AND ad_id NOT LIKE 'fam:%%' ORDER BY last_day DESC NULLS LAST LIMIT %s",
                                          (created_limit,)).fetchall()]
    return {"recent": recent, "non_terminal": live, "missing_created": missing}


def fetch_updates(conn, tok=None, created_limit: int = 5000, extra_ids=()) -> tuple[list[tuple], dict]:
    """Graph reads only (no writes). Raises on any API failure. Returns (update rows, stats). extra_ids: ads that are about to be written by the same run
    (new in the pulled days / lifetime); those the table does not know yet, or knows without a status, are fetched too."""
    tok = tok or meta_api.get_token()
    thr = conn.execute("SELECT max(day) FROM ad_daily").fetchone()[0] or dt.date.today()
    t = targets(conn, thr, created_limit)
    extra = [str(i) for i in extra_ids]
    known = {r[0] for r in conn.execute("SELECT ad_id FROM meta_ads WHERE ad_id = ANY(%s) AND status IS NOT NULL AND created_at IS NOT NULL", (extra,)).fetchall()} if extra else set()
    t["new_in_run"] = [i for i in extra if i not in known]
    ids = sorted(set(t["recent"]) | set(t["non_terminal"]) | set(t["missing_created"]) | set(t["new_in_run"]))
    got = meta_api.fetch_ad_meta(tok, ids, strict=True)
    old = {r[0]: r[1:] for r in conn.execute("SELECT ad_id, status, ad_name, created_date FROM meta_ads WHERE ad_id = ANY(%s)", (ids,)).fetchall()}
    rows, stats = [], {"asked": len(ids), "recent_delivery": len(t["recent"]), "non_terminal": len(t["non_terminal"]), "missing_created": len(t["missing_created"]), "new_in_run": len(t["new_in_run"]),
                       "status_changed": 0, "renamed": 0, "created_changed": 0, "errors": 0, "status_counts": {}}
    for ad_id, v in got.items():
        if "error" in v:
            stats["errors"] += 1
            continue
        ost, oname, ocr = old.get(ad_id, (None, None, None))
        name = v["name"] if v["name"] is not None else oname
        f = fam(name) if name is not None else None
        stats["status_changed"] += bool(v["status"] and v["status"] != ost)
        stats["renamed"] += bool(name is not None and oname is not None and name != oname)
        stats["created_changed"] += bool(v["created"] and ocr != v["created"])
        stats["status_counts"][v["status"]] = stats["status_counts"].get(v["status"], 0) + 1
        rows.append((v["status"], name, f.lower() if f is not None else None, f, v["created_at"], v["created"], ad_id))
    return rows, stats


def apply_updates(conn, rows) -> None:
    db.execmany(conn, """UPDATE meta_ads SET status = coalesce(%s, status), ad_name = coalesce(%s, ad_name), meta_family_key = coalesce(%s, meta_family_key),
                         family_name = coalesce(%s, family_name), created_at = coalesce(%s, created_at), created_date = coalesce(%s, created_date),
                         status_checked_at = now() WHERE ad_id = %s""", rows)
    db.set_meta(conn, "status_refreshed_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))


def refresh(conn, tok=None, created_limit: int = 5000, dry_run: bool = False) -> dict:
    rows, stats = fetch_updates(conn, tok, created_limit)
    if not dry_run:
        apply_updates(conn, rows)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--created-limit", type=int, default=5000, help="max ads with no created_at to (re)fetch this run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with db.JobRun("status_refresh") as run, db.connect() as conn:
        run.stats = refresh(conn, created_limit=a.created_limit, dry_run=a.dry_run)
        conn.commit()
        run.rows_written = run.stats["asked"]
        db.log(f"  {run.stats}")


if __name__ == "__main__":
    main()
