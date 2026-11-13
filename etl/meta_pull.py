"""Meta Graph API pull (level=ad, both Lonvera ad accounts) -> ad_daily at AD grain (source='meta_api'), meta_ads, meta_campaigns.
Replace-by-day: a 14-day chunk is only loaded when BOTH accounts pulled cleanly and each account's ad-level spend reconciles with its
account-level spend within 2%; the chunk's days are then replaced wholesale. Chunks that fail keep their existing ('sheet') rows.
Flags: --days N | --since D --until D | --dry-run (no DB writes; compares with the sheet rows) | --no-replace (writes ad/campaign metadata only).
Usage: python -m etl.meta_pull --days 7"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import db, meta_api, sheets_io
from .resolver import classify_stage, fam

PT = ZoneInfo("America/Los_Angeles")
TOL = 0.02


def default_until() -> dt.date:
    # Timezones: Graph buckets days by each ad ACCOUNT's own timezone (Lonvera Main = America/Los_Angeles, Lonvera BackUp = America/New_York); date_start values
    # are kept as returned and never re-based (see app_meta 'tz_note'). "Last complete day" is yesterday in Pacific, the later of the two zones.
    return dt.datetime.now(PT).date() - dt.timedelta(days=1)


def aggregate(rows: list[dict]) -> dict[tuple, dict]:
    """(ad_id, day) -> metrics + dims (an ad has one campaign / ad set)."""
    out: dict[tuple, dict] = {}
    for r in rows:
        k = (r["ad_id"], r["day"])
        a = out.get(k)
        if a is None:
            f = fam(r["ad_name"])
            out[k] = a = {**{x: r[x] for x in ("ad_id", "day", "ad_name", "campaign_id", "campaign_name", "adset_id", "adset_name", "account")},
                          "fam": f, "famkey": f.lower(), "spend": 0.0, "purchases": 0.0, "value": 0.0, "clicks": 0.0, "impressions": 0.0, "lpv": 0.0, "atc": 0.0, "ic": 0.0, "pai": 0.0}
        a["spend"] += r["spend"]; a["purchases"] += r["purchases"]; a["value"] += r["value"]; a["clicks"] += r["clicks"]
        a["impressions"] += r.get("impressions", 0.0)
        a["lpv"] += r.get("lpv", 0.0); a["atc"] += r.get("atc", 0.0)
        a["ic"] += r.get("ic", 0.0); a["pai"] += r.get("pai", 0.0)
    return out


def famday(agg: dict[tuple, dict]) -> dict[tuple, list]:
    out: dict[tuple, list] = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for a in agg.values():
        x = out[(a["day"], a["famkey"])]
        x[0] += a["spend"]; x[1] += a["purchases"]; x[2] += a["value"]; x[3] += a["clicks"]
    return out


def load_chunk(conn, agg: dict[tuple, dict], since: dt.date, until: dt.date, replace: bool = True) -> dict:
    """Replace [since, until] in ad_daily with ad-grain rows; upsert meta_ads + meta_campaigns. One transaction (caller commits)."""
    camps = {}
    for a in agg.values():
        if a["campaign_id"]:
            camps[a["campaign_id"]] = (a["campaign_id"], a["account"], a["campaign_name"], classify_stage(a["campaign_name"]))
    db.execmany(conn, """INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,%s,%s,%s)
                         ON CONFLICT (campaign_id) DO UPDATE SET account=EXCLUDED.account, name=EXCLUDED.name, stage=EXCLUDED.stage""", camps.values())
    ads = {}
    for a in agg.values():
        ads[a["ad_id"]] = (a["ad_id"], a["account"], a["ad_name"], a["famkey"], a["fam"], a["campaign_id"] or None, a["adset_id"] or None, a["adset_name"] or None)
    db.execmany(conn, """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, family_name, campaign_id, adset_id, adset_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET account=EXCLUDED.account, ad_name=EXCLUDED.ad_name, meta_family_key=EXCLUDED.meta_family_key,
                         family_name=EXCLUDED.family_name,
                         campaign_id=EXCLUDED.campaign_id, adset_id=EXCLUDED.adset_id, adset_name=EXCLUDED.adset_name""", ads.values())
    n = 0
    if replace:
        deleted = conn.execute("DELETE FROM ad_daily WHERE day BETWEEN %s AND %s", (since, until)).rowcount
        n = db.bulk_copy(conn, "ad_daily", ["ad_id", "day", "meta_family_key", "meta_family", "campaign_id", "spend", "purchases", "conv_value", "link_clicks", "impressions", "source", "landing_page_views", "add_to_carts", "initiated_checkouts", "payment_info_adds"],
                         [(a["ad_id"], a["day"], a["famkey"], a["fam"], a["campaign_id"], round(a["spend"], 2), round(a["purchases"], 2), round(a["value"], 2),
                           round(a["clicks"]), round(a["impressions"]), "meta_api", round(a["lpv"]), round(a["atc"]), round(a["ic"]), round(a["pai"])) for a in agg.values()])
    else:
        deleted = 0
    return {"deleted": deleted, "inserted": n, "ads": len(ads), "campaigns": len(camps)}


def refresh_spans(conn):
    conn.execute("""UPDATE meta_ads a SET first_day=s.f, last_day=s.l FROM (SELECT ad_id, min(day) f, max(day) l FROM ad_daily WHERE ad_id NOT LIKE 'fam:%' GROUP BY ad_id) s
                    WHERE a.ad_id=s.ad_id""")
    conn.execute("""UPDATE meta_campaigns c SET first_day=s.f, last_day=s.l FROM (SELECT campaign_id, min(day) f, max(day) l FROM ad_daily WHERE campaign_id <> '' GROUP BY campaign_id) s
                   WHERE c.campaign_id=s.campaign_id""")


def fill_ad_meta(conn, tok, limit: int = 3000) -> int:
    """Best effort (never fatal): created_at / Pacific created_date / status / name for ads that have none yet. The daily run uses etl.status_refresh instead."""
    ids = [r[0] for r in conn.execute("SELECT ad_id FROM meta_ads WHERE (created_at IS NULL OR status IS NULL) AND ad_id NOT LIKE 'fam:%%' ORDER BY last_day DESC NULLS LAST LIMIT %s", (limit,)).fetchall()]
    if not ids:
        return 0
    got = meta_api.fetch_ad_meta(tok, ids)
    db.execmany(conn, "UPDATE meta_ads SET created_at=coalesce(%s, created_at), created_date=coalesce(%s::date, created_date), status=coalesce(%s, status) WHERE ad_id=%s",
                [(v["created_at"], v["created"], v["status"], k) for k, v in got.items() if "error" not in v])
    return len(got)


def sheet_famday(conn, since, until, snapshot_dir=None) -> tuple[dict, str]:
    """Family x day totals from the SHEET for the window: DB rows with source='sheet', else the snapshot Daily tab."""
    rows = conn.execute("""SELECT day, meta_family_key, sum(spend), sum(purchases), sum(conv_value), sum(link_clicks) FROM ad_daily
                           WHERE day BETWEEN %s AND %s AND source='sheet' GROUP BY 1,2""", (since, until)).fetchall()
    if rows:
        return {(r[0], r[1]): [float(x) for x in r[2:]] for r in rows}, "db(sheet rows)"
    if snapshot_dir:
        from .backfill_sheet import parse_daily
        d = parse_daily(sheets_io.read_tab("meta_daily", snapshot_dir))
        return {(day, name.lower()): [sp, pu, cv, cl] for day, name, sp, pu, cv, cl in d if since <= day <= until}, "snapshot"
    return {}, "none"


def compare_famday(pulled: dict, sheet: dict) -> dict:
    per_day = collections.defaultdict(lambda: {"sheet": 0.0, "api": 0.0, "max_fam_diff": 0.0, "fams_diff": 0, "pur_sheet": 0.0, "pur_api": 0.0})
    for k in set(pulled) | set(sheet):
        p = pulled.get(k, [0, 0, 0, 0]); s = sheet.get(k, [0, 0, 0, 0])
        d = per_day[k[0]]
        d["sheet"] += s[0]; d["api"] += p[0]; d["pur_sheet"] += s[1]; d["pur_api"] += p[1]
        diff = abs(p[0] - s[0])
        d["max_fam_diff"] = max(d["max_fam_diff"], diff)
        if diff > 0.015:
            d["fams_diff"] += 1
    return dict(sorted(per_day.items()))


def print_compare(cmp: dict, src: str):
    print(f"  day-by-day: api (ad-level, summed to family) vs {src}")
    print(f"  {'day':<12}{'sheet $':>14}{'api $':>14}{'diff $':>12}{'diff %':>9}{'max fam diff $':>16}{'fams >1c':>10}{'purch sheet/api':>18}")
    for d, x in cmp.items():
        pct = (x["api"] - x["sheet"]) / x["sheet"] * 100 if x["sheet"] else 0.0
        print(f"  {d.isoformat():<12}{x['sheet']:>14,.2f}{x['api']:>14,.2f}{x['api'] - x['sheet']:>12,.2f}{pct:>8.2f}%{x['max_fam_diff']:>16,.2f}{x['fams_diff']:>10}{x['pur_sheet']:>10,.0f}/{x['pur_api']:,.0f}")


def reconcile_stage(conn, snapshot_dir=None, since=None, until=None) -> dict:
    """Per day, all-product Testing / Scaling spend from ad_daily (meta_api days) vs the Meta Campaign - History tab (must be within 1%)."""
    rows = sheets_io.read_tab("meta_campaign_history", snapshot_dir)
    sheet = collections.defaultdict(float)
    for r in rows[2:]:
        if len(r) < 7 or not isinstance(r[0], (int, float)):
            continue
        sheet[(sheets_io.serial_to_date(r[0]), str(r[5]).lower())] += float(r[6] or 0)
    q = """SELECT d.day, c.stage, sum(d.spend) FROM ad_daily d JOIN meta_campaigns c ON c.campaign_id=d.campaign_id WHERE d.source='meta_api'"""
    args = []
    if since:
        q += " AND d.day >= %s"; args.append(since)
    if until:
        q += " AND d.day <= %s"; args.append(until)
    mine = {(r[0], r[1]): float(r[2]) for r in conn.execute(q + " GROUP BY 1,2", args).fetchall()}
    days = sorted({k[0] for k in mine})
    worst, bad, out = 0.0, [], []
    for d in days:
        for st in ("testing", "scaling"):
            s, m = sheet.get((d, st), 0.0), mine.get((d, st), 0.0)
            pct = abs(m - s) / s if s >= 50 else 0.0
            worst = max(worst, pct)
            if pct > 0.01:
                bad.append((d.isoformat(), st, round(s, 2), round(m, 2), round(pct * 100, 2)))
            out.append((d, st, s, m))
    return {"days": len(days), "worst_pct": round(worst * 100, 3), "days_beyond_1pct": bad[:20], "n_beyond": len(bad),
            "total_sheet": round(sum(x[2] for x in out), 2), "total_db": round(sum(x[3] for x in out), 2)}


def fill_funnel(conn, since, until, workers=2, chunk_days=7, pause=5.0, attempts=4, only_missing=True, dry_run=False, sleep=time.sleep) -> dict:
    """History fill for the funnel columns landing_page_views / add_to_carts / initiated_checkouts / payment_info_adds: re-pull [since, until] in chunk_days chunks
    (read-only Meta GETs) and UPDATE only those four columns of the existing meta_api rows of ad_daily (matched on ad_id, day). FILL-ONLY: a column that already holds a
    value is kept (COALESCE) unless only_missing is False (--refill), which overwrites the four. Spend, impressions, link_clicks, purchases and conv_value are never written; the pulled spend and
    link_clicks are compared with the stored ones per chunk and reported (Meta restates, so recent weeks may drift). Resumable: with only_missing a chunk whose stored
    meta_api rows all have both columns set is skipped; a chunk that fails (rate limit) is retried with a growing wait, then reported and skipped."""
    tok = meta_api.get_token()
    chunks = meta_api.date_chunks(since, until, chunk_days)
    out = {"chunks": len(chunks), "done": 0, "skipped": 0, "failed": [], "updated": 0, "pulled_ad_days_without_row": 0, "stored_rows_without_pull": 0, "per_chunk": []}
    for ci, (cs, ce) in enumerate(chunks):
        todo = conn.execute("SELECT count(*) FROM ad_daily WHERE source = 'meta_api' AND day BETWEEN %s AND %s AND (landing_page_views IS NULL OR add_to_carts IS NULL OR initiated_checkouts IS NULL OR payment_info_adds IS NULL)", (cs, ce)).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM ad_daily WHERE source = 'meta_api' AND day BETWEEN %s AND %s", (cs, ce)).fetchone()[0]
        if only_missing and todo == 0:
            out["skipped"] += 1
            db.log(f"chunk {ci + 1}/{len(chunks)} {cs}..{ce}: already filled ({total} rows), skipped")
            continue
        res = None
        for attempt in range(1, attempts + 1):
            try:
                with ThreadPoolExecutor(max_workers=min(workers, len(meta_api.ACCOUNTS))) as ex:
                    res = [f.result() for f in [ex.submit(meta_api.pull_chunk, tok, acct, an, cs, ce) for acct, an in meta_api.ACCOUNTS]]
                break
            except meta_api.TokenRejected:
                raise
            except meta_api.GraphError as e:
                wait = 120 * attempt
                db.log(f"chunk {ci + 1}/{len(chunks)} {cs}..{ce} attempt {attempt}/{attempts} failed: {db.scrub(e)[:140]}; waiting {wait}s")
                sleep(wait)
        if res is None:
            out["failed"].append(f"{cs}..{ce}")
            continue
        agg = aggregate([r for rows in res for r in rows])
        stored = {(r[0], r[1]): r for r in conn.execute("SELECT ad_id, day, spend, link_clicks FROM ad_daily WHERE source = 'meta_api' AND day BETWEEN %s AND %s", (cs, ce))}
        upd, miss = [], 0
        for (ad, day), a in agg.items():
            if (ad, day) in stored:
                upd.append((round(a["lpv"]), round(a["atc"]), round(a["ic"]), round(a["pai"]), ad, day))
            else:
                miss += 1
        sp_p, sp_s = sum(a["spend"] for a in agg.values()), sum(float(r[2] or 0) for r in stored.values())
        cl_p, cl_s = sum(round(a["clicks"]) for a in agg.values()), sum(float(r[3] or 0) for r in stored.values())
        d_sp = (sp_p - sp_s) / sp_s * 100 if sp_s else 0.0
        d_cl = (cl_p - cl_s) / cl_s * 100 if cl_s else 0.0
        info = {"chunk": f"{cs}..{ce}", "rows_pulled": len(agg), "rows_stored": len(stored), "updated": len(upd), "pulled_without_stored_row": miss,
                "stored_without_pull": len(stored) - len(upd), "spend_pulled": round(sp_p, 2), "spend_stored": round(sp_s, 2), "spend_diff_pct": round(d_sp, 3),
                "link_clicks_pulled": cl_p, "link_clicks_stored": round(cl_s), "link_clicks_diff_pct": round(d_cl, 3),
                "lpv": round(sum(a["lpv"] for a in agg.values())), "atc": round(sum(a["atc"] for a in agg.values())),
                "ic": round(sum(a["ic"] for a in agg.values())), "pai": round(sum(a["pai"] for a in agg.values()))}
        out["per_chunk"].append(info)
        flag = "  <-- spend/link-click drift above 0.5%" if abs(d_sp) > 0.5 or abs(d_cl) > 0.5 else ""
        db.log(f"chunk {ci + 1}/{len(chunks)} {cs}..{ce}: updated {len(upd)}/{len(stored)} stored rows, spend {d_sp:+.3f}%, link_clicks {d_cl:+.3f}%, lpv {info['lpv']}, atc {info['atc']}, ic {info['ic']}, pai {info['pai']}{flag}")
        if not dry_run:
            with conn.transaction():
                if only_missing:
                    db.execmany(conn, "UPDATE ad_daily SET landing_page_views = COALESCE(landing_page_views, %s), add_to_carts = COALESCE(add_to_carts, %s), "
                                      "initiated_checkouts = COALESCE(initiated_checkouts, %s), payment_info_adds = COALESCE(payment_info_adds, %s) WHERE ad_id = %s AND day = %s AND source = 'meta_api'", upd)
                else:
                    db.execmany(conn, "UPDATE ad_daily SET landing_page_views = %s, add_to_carts = %s, initiated_checkouts = %s, payment_info_adds = %s WHERE ad_id = %s AND day = %s AND source = 'meta_api'", upd)
        out["done"] += 1; out["updated"] += len(upd)
        out["pulled_ad_days_without_row"] += miss; out["stored_rows_without_pull"] += len(stored) - len(upd)
        conn.commit()
        if ci + 1 < len(chunks):
            sleep(pause)
    return out


def pull_task(tok, acct, an, since, until):
    rows = meta_api.pull_chunk(tok, acct, an, since, until)
    total = sum(r["spend"] for r in rows)
    acct_total = meta_api.account_spend(tok, acct, since, until)
    return an, rows, total, acct_total


def apply_pending(conn, pending, stats, replace=True):
    for agg, cs, ce in pending:
        r = load_chunk(conn, agg, cs, ce, replace=replace)
        stats["rows_inserted"] += r["inserted"]; stats["rows_deleted"] += r["deleted"]
    refresh_spans(conn)


def run_pull(conn, since, until, dry_run=False, replace=True, snapshot_dir=None, workers=2, tok=None, atomic=False, load=True):
    """Pull [since, until] chunk by chunk. atomic=False (backfill): each clean chunk is committed as it arrives, failed chunks keep their old rows.
    atomic=True (daily run): NOTHING is written unless every chunk pulled cleanly and reconciled; then all chunks are replaced in one transaction.
    A rejected token always raises TokenRejected."""
    tok = tok or meta_api.get_token()
    chunks = meta_api.date_chunks(since, until)
    stats = {"chunks": len(chunks), "chunks_loaded": 0, "chunks_failed": [], "rows_inserted": 0, "rows_deleted": 0, "recon": []}
    pulled_all: dict = {}
    pending: list = []
    consecutive_fail = 0
    for ci, (cs, ce) in enumerate(chunks):
        db.log(f"chunk {ci + 1}/{len(chunks)}: {cs}..{ce}")
        try:
            with ThreadPoolExecutor(max_workers=min(workers, len(meta_api.ACCOUNTS))) as ex:
                futs = [ex.submit(pull_task, tok, acct, an, cs, ce) for acct, an in meta_api.ACCOUNTS]
                results = [f.result() for f in futs]
        except meta_api.TokenRejected:
            raise
        except meta_api.GraphError as e:
            stats["chunks_failed"].append({"chunk": f"{cs}..{ce}", "error": db.scrub(e)[:200]})
            db.log(f"  chunk FAILED: {db.scrub(e)[:160]}")
            if atomic:
                break
            consecutive_fail += 1
            if consecutive_fail >= 2:
                db.log("  two chunks failed in a row (rate limit?); stopping the pull early")
                break
            continue
        consecutive_fail = 0
        ok = True
        rows = []
        for an, r, total, acct_total in results:
            off = abs(total - acct_total) / acct_total if acct_total else 0.0
            stats["recon"].append({"chunk": f"{cs}..{ce}", "account": an, "ads_total": round(total, 2), "account_total": round(acct_total, 2), "pct_off": round(off * 100, 3)})
            db.log(f"  {an}: {len(r)} ad-days, ads ${total:,.2f} vs account ${acct_total:,.2f} ({off:.2%})")
            if off > TOL and abs(total - acct_total) > 5:
                ok = False
            rows += r
        if not ok:
            stats["chunks_failed"].append({"chunk": f"{cs}..{ce}", "error": "per-account spend does not reconcile within 2%"})
            db.log("  chunk NOT loaded: spend does not reconcile with account totals")
            if atomic:
                break
            continue
        agg = aggregate(rows)
        for k, v in famday(agg).items():
            pulled_all[k] = v
        if atomic:
            pending.append((agg, cs, ce))
        elif not dry_run:
            r = load_chunk(conn, agg, cs, ce, replace=replace)
            conn.commit()
            stats["rows_inserted"] += r["inserted"]; stats["rows_deleted"] += r["deleted"]
        stats["chunks_loaded"] += 1
    if atomic and not stats["chunks_failed"] and not dry_run and load:
        apply_pending(conn, pending, stats, replace)
        conn.commit()
    elif atomic and not stats["chunks_failed"] and not load:
        stats["_pending"] = pending                         # caller applies it (run_daily: one transaction with everything else)
    elif atomic and stats["chunks_failed"]:
        stats["chunks_loaded"] = 0                          # nothing was written
    return stats, pulled_all


def pull_into_db(conn, since, until, replace=True, snapshot_dir=None, workers=2, ad_meta=False, atomic=True) -> dict:
    """Pull [since, until] and load it. atomic=True (daily run): nothing is written unless every chunk pulled cleanly. atomic=False: clean chunks are
    committed one by one. Failed chunks are reported in stats['chunks_failed']; the caller decides whether that is fatal."""
    stats, pulled = run_pull(conn, since, until, replace=replace, snapshot_dir=snapshot_dir, workers=workers, atomic=atomic)
    refresh_spans(conn)
    if ad_meta:
        try:
            stats["ad_meta_filled"] = fill_ad_meta(conn, meta_api.get_token())
        except Exception as e:                                          # noqa: BLE001 — best effort
            stats["ad_meta_error"] = db.scrub(e)[:160]
    conn.commit()
    stats["_pulled"] = pulled
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--dry-run", action="store_true", help="no DB writes; compare with the sheet rows for the same days")
    ap.add_argument("--no-replace", action="store_true", help="write ads/campaigns metadata but leave ad_daily untouched")
    ap.add_argument("--snapshot-dir", help="sheet snapshot for comparisons (Daily tab, Meta Campaign - History)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--fill-funnel", action="store_true", help="history fill: fill-only UPDATE of landing_page_views / add_to_carts / initiated_checkouts / payment_info_adds of existing ad_daily rows (weekly chunks, resumable; --since/--until, default 2026-05-06..yesterday)")
    ap.add_argument("--refill", action="store_true", help="with --fill-funnel: re-pull chunks even when already filled")
    ap.add_argument("--ad-meta", action="store_true", help="also fill created date / status for ads that have none (best effort)")
    ap.add_argument("--atomic", action="store_true", help="write nothing unless every chunk pulled cleanly (the daily run always does this)")
    a = ap.parse_args(argv)
    until = dt.date.fromisoformat(a.until) if a.until else default_until()
    since = dt.date.fromisoformat(a.since) if a.since else until - dt.timedelta(days=a.days - 1)
    if a.fill_funnel:
        with db.connect() as conn:
            if not a.since:
                since = dt.date.fromisoformat(db.get_meta(conn, "daily_start") or "2026-05-06")
            import json as _json
            res = fill_funnel(conn, since, until, workers=a.workers, only_missing=not a.refill, dry_run=a.dry_run)
            print(_json.dumps({k: v for k, v in res.items() if k != "per_chunk"}))
            sys.exit(1 if res["failed"] else 0)
    with db.connect() as conn:
        d0 = db.get_meta(conn, "daily_start")
        if d0 and since < dt.date.fromisoformat(d0) and not a.since:
            db.log(f"clamping since {since} to daily_start {d0}")
            since = dt.date.fromisoformat(d0)
        if a.dry_run:
            db.log(f"DRY RUN {since}..{until} (no DB writes)")
            stats, pulled = run_pull(conn, since, until, dry_run=True, snapshot_dir=a.snapshot_dir, workers=a.workers)
            sheet, src = sheet_famday(conn, since, until, a.snapshot_dir)
            cmp = compare_famday(pulled, sheet)
            print_compare(cmp, src)
            print(f"  recon: {stats['recon']}")
            print(f"  chunks loaded(dry) {stats['chunks_loaded']}/{stats['chunks']}, failed {stats['chunks_failed']}")
            sys.exit(1 if stats["chunks_failed"] else 0)
        with db.JobRun("meta_pull") as run:
            stats = pull_into_db(conn, since, until, replace=not a.no_replace, snapshot_dir=a.snapshot_dir, workers=a.workers, ad_meta=a.ad_meta, atomic=a.atomic)
            pulled = stats.pop("_pulled")
            sheet, src = sheet_famday(conn, since, until, a.snapshot_dir)
            if sheet and not a.no_replace:
                cmp = compare_famday(pulled, sheet)
                print_compare(cmp, src)
                stats["famday_max_abs_day_diff"] = round(max((abs(x["api"] - x["sheet"]) for x in cmp.values()), default=0), 2)
            run.stats = stats
            run.rows_written = stats["rows_inserted"]
            run.data_through = until
            db.log(f"pull done: chunks loaded {stats['chunks_loaded']}/{stats['chunks']}, failed {len(stats['chunks_failed'])}, rows {stats['rows_inserted']}")
            if stats["chunks_failed"]:
                raise RuntimeError(f"{len(stats['chunks_failed'])} chunk(s) failed: {stats['chunks_failed'][:3]}")


if __name__ == "__main__":
    main()
