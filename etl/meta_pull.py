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
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import db, meta_api, sheets_io
from .resolver import classify_stage, fam

PT = ZoneInfo("America/Los_Angeles")
TOL = 0.02


def default_until() -> dt.date:
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
                          "fam": f, "famkey": f.lower(), "spend": 0.0, "purchases": 0.0, "value": 0.0, "clicks": 0.0, "impressions": 0.0}
        a["spend"] += r["spend"]; a["purchases"] += r["purchases"]; a["value"] += r["value"]; a["clicks"] += r["clicks"]
        a["impressions"] += r.get("impressions", 0.0)
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
        ads[a["ad_id"]] = (a["ad_id"], a["account"], a["ad_name"], a["famkey"], a["campaign_id"] or None, a["adset_id"] or None, a["adset_name"] or None)
    db.execmany(conn, """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name) VALUES (%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET account=EXCLUDED.account, ad_name=EXCLUDED.ad_name, meta_family_key=EXCLUDED.meta_family_key,
                         campaign_id=EXCLUDED.campaign_id, adset_id=EXCLUDED.adset_id, adset_name=EXCLUDED.adset_name""", ads.values())
    n = 0
    if replace:
        deleted = conn.execute("DELETE FROM ad_daily WHERE day BETWEEN %s AND %s", (since, until)).rowcount
        n = db.bulk_copy(conn, "ad_daily", ["ad_id", "day", "meta_family_key", "meta_family", "campaign_id", "spend", "purchases", "conv_value", "link_clicks", "impressions", "source"],
                         [(a["ad_id"], a["day"], a["famkey"], a["fam"], a["campaign_id"], round(a["spend"], 2), round(a["purchases"], 2), round(a["value"], 2),
                           round(a["clicks"]), round(a["impressions"]), "meta_api") for a in agg.values()])
    else:
        deleted = 0
    return {"deleted": deleted, "inserted": n, "ads": len(ads), "campaigns": len(camps)}


def refresh_spans(conn):
    conn.execute("""UPDATE meta_ads a SET first_day=s.f, last_day=s.l FROM (SELECT ad_id, min(day) f, max(day) l FROM ad_daily WHERE ad_id NOT LIKE 'fam:%' GROUP BY ad_id) s
                    WHERE a.ad_id=s.ad_id""")
    conn.execute("""UPDATE meta_campaigns c SET first_day=s.f, last_day=s.l FROM (SELECT campaign_id, min(day) f, max(day) l FROM ad_daily WHERE campaign_id <> '' GROUP BY campaign_id) s
                   WHERE c.campaign_id=s.campaign_id""")


def fill_ad_meta(conn, tok, limit: int = 3000) -> int:
    """Created date / status for Meta ads we only know from the API pull (best effort, never fatal)."""
    ids = [r[0] for r in conn.execute("SELECT ad_id FROM meta_ads WHERE (created_date IS NULL OR status IS NULL) AND ad_id NOT LIKE 'fam:%%' ORDER BY last_day DESC NULLS LAST LIMIT %s", (limit,)).fetchall()]
    if not ids:
        return 0
    got = meta_api.fetch_ad_meta(tok, ids)
    db.execmany(conn, "UPDATE meta_ads SET created_date=coalesce(%s::date, created_date), status=coalesce(%s, status) WHERE ad_id=%s",
                [(v["created"], v["status"], k) for k, v in got.items()])
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


def pull_task(tok, acct, an, since, until):
    rows = meta_api.pull_chunk(tok, acct, an, since, until)
    total = sum(r["spend"] for r in rows)
    acct_total = meta_api.account_spend(tok, acct, since, until)
    return an, rows, total, acct_total


def run_pull(conn, since, until, dry_run=False, replace=True, snapshot_dir=None, workers=2, tok=None):
    tok = tok or meta_api.get_token()
    chunks = meta_api.date_chunks(since, until)
    stats = {"chunks": len(chunks), "chunks_loaded": 0, "chunks_failed": [], "rows_inserted": 0, "rows_deleted": 0, "recon": []}
    pulled_all: dict = {}
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
            db.log(f"  chunk FAILED (existing rows for these days are kept): {db.scrub(e)[:160]}")
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
            continue
        agg = aggregate(rows)
        for k, v in famday(agg).items():
            pulled_all[k] = v
        if not dry_run:
            r = load_chunk(conn, agg, cs, ce, replace=replace)
            conn.commit()
            stats["rows_inserted"] += r["inserted"]; stats["rows_deleted"] += r["deleted"]
        stats["chunks_loaded"] += 1
    return stats, pulled_all


def pull_into_db(conn, since, until, replace=True, snapshot_dir=None, workers=2, ad_meta=True) -> dict:
    """Run the pull for [since, until] and load it (chunk by chunk). Returns stats; raises only if nothing could be loaded safely is NOT the case:
    failed chunks are reported in stats['chunks_failed'] and the caller decides whether that is fatal."""
    stats, pulled = run_pull(conn, since, until, replace=replace, snapshot_dir=snapshot_dir, workers=workers)
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
    ap.add_argument("--no-ad-meta", action="store_true")
    a = ap.parse_args(argv)
    until = dt.date.fromisoformat(a.until) if a.until else default_until()
    since = dt.date.fromisoformat(a.since) if a.since else until - dt.timedelta(days=a.days - 1)
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
            stats = pull_into_db(conn, since, until, replace=not a.no_replace, snapshot_dir=a.snapshot_dir, workers=a.workers, ad_meta=not a.no_ad_meta)
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
