"""Manual loader for the Amazon Ads keyword layer (Sponsored Products): search-term and targeting reports plus keyword, target, negative keyword, product ad and campaign lists.

    python -m etl.amazon_dash_load --dir ~/lonvera-tag-explorer-data/amazon_dash [--dry-run] [--database-url URL]

The files are raw ExpandFi Data Dash pulls of Amazon Ads (see FORMAT.md in that folder). The loader reads every window of every report and every snapshot of every list under --dir
and upserts on the natural keys, so loading the same files twice leaves the same rows. It is deliberately NOT wired into any cron or the feeds run: the feeds service cannot reach Dash.

Tables (migration 252): amazon_ads_search_term_daily, amazon_ads_targeting_daily, amazon_ads_keywords_snapshot, amazon_ads_targets_snapshot,
amazon_ads_negative_keywords_snapshot, amazon_ads_campaigns_snapshot, amazon_ads_product_ads. Every id is stored as text (reports carry ints, lists carry strings).
Prints row counts and the first and last date per table, then a reconciliation of the daily search-term cost and targeting cost against amazon_ads_campaign_daily Sponsored Products
spend on the days both hold. --dry-run reads and checks the files and writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import sys
from pathlib import Path

from . import db

SEARCH_COLS = ["day", "campaign_id", "ad_group_id", "keyword_id", "match_type", "search_term", "campaign_name", "ad_group_name", "keyword", "search_term_key",
               "impressions", "clicks", "cost", "purchases_7d", "sales_7d"]
TARGET_COLS = ["day", "campaign_id", "ad_group_id", "keyword_id", "match_type", "targeting", "campaign_name", "ad_group_name", "keyword", "impressions", "clicks", "cost", "purchases_7d", "sales_7d"]
KW_COLS = ["snapshot_date", "keyword_id", "ad_group_id", "campaign_id", "keyword_text", "match_type", "bid", "state"]
TG_COLS = ["snapshot_date", "target_id", "ad_group_id", "campaign_id", "bid", "state", "expression_type", "expression_text", "expression", "resolved_expression"]
NEG_COLS = ["snapshot_date", "keyword_id", "ad_group_id", "campaign_id", "keyword_text", "match_type", "state"]
CAMP_COLS = ["snapshot_date", "campaign_id", "name", "state", "targeting_type", "start_date", "budget", "budget_type", "portfolio_id", "bidding_strategy", "placement_top_pct",
             "placement_product_page_pct", "placement_rest_of_search_pct", "dynamic_bidding"]
PA_COLS = ["ad_id", "ad_group_id", "campaign_id", "asin", "sku", "state", "snapshot_date"]
PLACEMENTS = {"PLACEMENT_TOP": "placement_top_pct", "PLACEMENT_PRODUCT_PAGE": "placement_product_page_pct", "PLACEMENT_REST_OF_SEARCH": "placement_rest_of_search_pct"}

# table -> (columns, conflict key columns, date column for the summary or None)
TABLES = {
    "amazon_ads_search_term_daily": (SEARCH_COLS, ["day", "campaign_id", "ad_group_id", "keyword_id", "match_type", "search_term"], "day"),
    "amazon_ads_targeting_daily": (TARGET_COLS, ["day", "campaign_id", "ad_group_id", "keyword_id", "match_type", "targeting"], "day"),
    "amazon_ads_keywords_snapshot": (KW_COLS, ["snapshot_date", "keyword_id"], "snapshot_date"),
    "amazon_ads_targets_snapshot": (TG_COLS, ["snapshot_date", "target_id"], "snapshot_date"),
    "amazon_ads_negative_keywords_snapshot": (NEG_COLS, ["snapshot_date", "keyword_id"], "snapshot_date"),
    "amazon_ads_campaigns_snapshot": (CAMP_COLS, ["snapshot_date", "campaign_id"], "snapshot_date"),
    "amazon_ads_product_ads": (PA_COLS, ["ad_id"], "snapshot_date"),
}
JSONB = {"expression", "resolved_expression", "dynamic_bidding"}


def norm_query(s) -> str:
    """Lower case, trimmed, runs of whitespace collapsed: the normalisation of amazon_sqp_weekly.query_key, so a search term joins to Search Query Performance."""
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _id(v) -> str:
    return "" if v is None else str(int(v)) if isinstance(v, float) and v == int(v) else str(v)


def _int(v):
    return None if v is None else int(round(float(v)))


def _num(v):
    return None if v is None else float(v)


def _txt(v):
    return None if v is None else str(v)


# ---------------------------------------------------------------------------------------------------------------- reading the files
def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _files(root: Path, dataset: str, pattern: str) -> list[str]:
    return sorted(glob.glob(str(root / dataset / "*" / pattern)))


def _snapshot_date(path: str) -> dt.date:
    m = re.search(r"snapshot_(\d{4}-\d{2}-\d{2})", path)
    if not m:
        raise SystemExit(f"cannot read a snapshot date from {path} (the folder must be named snapshot_YYYY-MM-DD)")
    return dt.date.fromisoformat(m.group(1))


def search_rows(root: Path) -> list[tuple]:
    out: dict[tuple, tuple] = {}
    for p in _files(root, "sp_search_term", "*.json"):
        for r in _load(p):
            term = str(r.get("searchTerm") or "")
            row = (dt.date.fromisoformat(r["date"]), _id(r["campaignId"]), _id(r["adGroupId"]), _id(r["keywordId"]), str(r.get("matchType") or ""), term,
                   _txt(r.get("campaignName")), _txt(r.get("adGroupName")), _txt(r.get("keyword")), norm_query(term),
                   _int(r.get("impressions")), _int(r.get("clicks")), _num(r.get("cost")), _int(r.get("purchases7d")), _num(r.get("sales7d")))
            out[row[:6]] = row                      # a later file wins (a restated re-pull)
    return list(out.values())


def targeting_rows(root: Path) -> list[tuple]:
    out: dict[tuple, tuple] = {}
    for p in _files(root, "sp_targeting", "*.json"):
        for r in _load(p):
            tgt = str(r.get("targeting") if r.get("targeting") is not None else (r.get("keyword") or ""))
            row = (dt.date.fromisoformat(r["date"]), _id(r["campaignId"]), _id(r["adGroupId"]), _id(r["keywordId"]), str(r.get("matchType") or ""), tgt,
                   _txt(r.get("campaignName")), _txt(r.get("adGroupName")), _txt(r.get("keyword")),
                   _int(r.get("impressions")), _int(r.get("clicks")), _num(r.get("cost")), _int(r.get("purchases7d")), _num(r.get("sales7d")))
            out[row[:6]] = row
    return list(out.values())


def _list_rows(root: Path, dataset: str, key: str, build) -> list[tuple]:
    out: dict[tuple, tuple] = {}
    for p in _files(root, dataset, "page_*.json"):
        snap = _snapshot_date(p)
        body = _load(p)
        for r in body.get(key) or []:
            row = build(snap, r)
            out[(row[0], row[1])] = row            # (snapshot_date, id), or (ad_id, ad_group_id) for product ads, which are keyed on ad_id alone below
    return list(out.values())


def keyword_rows(root: Path) -> list[tuple]:
    return _list_rows(root, "sp_keyword_entities", "keywords", lambda s, r: (s, _id(r["keywordId"]), _id(r["adGroupId"]), _id(r["campaignId"]), _txt(r.get("keywordText")),
                                                                              _txt(r.get("matchType")), _num(r.get("bid")), _txt(r.get("state"))))


def _expr_text(expr) -> str | None:
    """'ASIN_SAME_AS b07tyrty26' / 'QUERY_HIGH_REL_MATCHES' (several parts joined with '; ')."""
    parts = [" ".join(x for x in (e.get("type"), e.get("value")) if x) for e in (expr or [])]
    return "; ".join(parts) or None


def target_rows(root: Path) -> list[tuple]:
    return _list_rows(root, "sp_target_entities", "targetingClauses", lambda s, r: (
        s, _id(r["targetId"]), _id(r["adGroupId"]), _id(r["campaignId"]), _num(r.get("bid")), _txt(r.get("state")), _txt(r.get("expressionType")), _expr_text(r.get("expression")),
        json.dumps(r.get("expression")) if r.get("expression") is not None else None, json.dumps(r.get("resolvedExpression")) if r.get("resolvedExpression") is not None else None))


def negative_rows(root: Path) -> list[tuple]:
    return _list_rows(root, "sp_negative_keyword_entities", "negativeKeywords", lambda s, r: (s, _id(r["keywordId"]), _id(r["adGroupId"]), _id(r["campaignId"]),
                                                                                              _txt(r.get("keywordText")), _txt(r.get("matchType")), _txt(r.get("state"))))


def _campaign(s, r) -> tuple:
    dyn = r.get("dynamicBidding") or {}
    pl = {PLACEMENTS[p["placement"]]: _num(p.get("percentage")) for p in dyn.get("placementBidding") or [] if p.get("placement") in PLACEMENTS}
    b = r.get("budget") or {}
    return (s, _id(r["campaignId"]), _txt(r.get("name")), _txt(r.get("state")), _txt(r.get("targetingType")), dt.date.fromisoformat(r["startDate"]) if r.get("startDate") else None,
            _num(b.get("budget")), _txt(b.get("budgetType")), _txt(r.get("portfolioId")), _txt(dyn.get("strategy")), pl.get("placement_top_pct"), pl.get("placement_product_page_pct"),
            pl.get("placement_rest_of_search_pct"), json.dumps(dyn) if dyn else None)


def campaign_rows(root: Path) -> list[tuple]:
    return _list_rows(root, "sp_campaign_entities", "campaigns", _campaign)


def product_ad_rows(root: Path) -> list[tuple]:
    """The latest map: one row per ad id; when a later snapshot holds the same ad it replaces the earlier one."""
    out: dict[str, tuple] = {}
    for p in _files(root, "sp_product_ad_entities", "page_*.json"):
        snap = _snapshot_date(p)
        for r in _load(p).get("productAds") or []:
            row = (_id(r["adId"]), _id(r["adGroupId"]), _id(r["campaignId"]), _txt(r.get("asin")), _txt(r.get("sku")), _txt(r.get("state")), snap)
            if row[0] not in out or out[row[0]][6] <= snap:
                out[row[0]] = row
    return list(out.values())


def read_all(root: Path) -> dict[str, list[tuple]]:
    return {"amazon_ads_search_term_daily": search_rows(root), "amazon_ads_targeting_daily": targeting_rows(root), "amazon_ads_keywords_snapshot": keyword_rows(root),
            "amazon_ads_targets_snapshot": target_rows(root), "amazon_ads_negative_keywords_snapshot": negative_rows(root), "amazon_ads_campaigns_snapshot": campaign_rows(root),
            "amazon_ads_product_ads": product_ad_rows(root)}


# ---------------------------------------------------------------------------------------------------------------- writing
def upsert(conn, table: str, rows: list[tuple]) -> int:
    """Idempotent: COPY into a temp table, then INSERT ... ON CONFLICT (natural key) DO UPDATE. loaded_at moves to now() on every row written."""
    if not rows:
        return 0
    cols, key, _ = TABLES[table]
    tmp = "_tmp_" + table
    conn.execute(f"CREATE TEMP TABLE {tmp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    with conn.cursor() as cur:
        with cur.copy(f"COPY {tmp} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key) + ", loaded_at = now()"
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM {tmp} ON CONFLICT ({', '.join(key)}) DO UPDATE SET {sets}")
    return len(rows)


def summary_from_rows(table: str, rows: list[tuple]) -> dict:
    cols, _, datecol = TABLES[table]
    i = cols.index(datecol)
    ds = [r[i] for r in rows]
    return {"rows": len(rows), "first": min(ds) if ds else None, "last": max(ds) if ds else None}


def summary_from_db(conn, table: str) -> dict:
    _, _, datecol = TABLES[table]
    r = conn.execute(f"SELECT count(*), min({datecol}), max({datecol}) FROM {table}").fetchone()
    return {"rows": r[0], "first": r[1], "last": r[2]}


# ---------------------------------------------------------------------------------------------------------------- reconciliation
def reconcile(day_costs: dict[dt.date, float], campaign_spend: dict[dt.date, float]) -> dict:
    """Cost of a report vs Sponsored Products spend of amazon_ads_campaign_daily on the days both hold."""
    days = sorted(set(day_costs) & set(campaign_spend))
    if not days:
        return {"days": 0, "first": None, "last": None, "report_cost": None, "campaign_spend": None, "diff": None, "diff_pct": None}
    rc, cs = sum(day_costs[d] for d in days), sum(campaign_spend[d] for d in days)
    return {"days": len(days), "first": days[0], "last": days[-1], "report_cost": round(rc, 2), "campaign_spend": round(cs, 2), "diff": round(rc - cs, 2),
            "diff_pct": round(100.0 * (rc - cs) / cs, 3) if cs else None}


def db_day_costs(conn, table: str) -> dict[dt.date, float]:
    return {r[0]: float(r[1] or 0) for r in conn.execute(f"SELECT day, sum(cost) FROM {table} GROUP BY day").fetchall()}


def row_day_costs(table: str, rows: list[tuple]) -> dict[dt.date, float]:
    cols = TABLES[table][0]
    ci, di = cols.index("cost"), cols.index("day")
    out: dict[dt.date, float] = {}
    for r in rows:
        out[r[di]] = out.get(r[di], 0.0) + float(r[ci] or 0)
    return out


def campaign_spend(conn, v3_only: bool) -> dict[dt.date, float]:
    exists = conn.execute("SELECT to_regclass('public.amazon_ads_campaign_daily') IS NOT NULL").fetchone()[0]
    if not exists:
        return {}
    sql = "SELECT day, sum(spend) FROM amazon_ads_campaign_daily WHERE ad_type = 'sp'" + (" AND api_version = 'v3'" if v3_only else "") + " GROUP BY day"
    return {r[0]: float(r[1] or 0) for r in conn.execute(sql).fetchall()}


def _fmt(s: dict, other: str = "campaign spend") -> str:
    if not s["days"]:
        return "no day is in both tables"
    return (f"{s['days']} days {s['first']}..{s['last']}: report cost {s['report_cost']:,.2f} vs {other} {s['campaign_spend']:,.2f}, "
            f"difference {s['diff']:+,.2f} ({s['diff_pct']:+.3f}%)")


def run(root: Path, conn, dry_run: bool = False, out=print) -> dict:
    """Read the files, write them (unless dry_run) and print the report. Returns {table: summary, 'reconciliation': {...}} for tests."""
    data = read_all(root)
    result: dict = {}
    if not dry_run:
        for table, rows in data.items():
            upsert(conn, table, rows)
        conn.commit()
    out(("DRY RUN (nothing written): " if dry_run else "Loaded: ") + str(root))
    out(f"{'table':44} {'rows read':>10} {'rows in table':>14}  first .. last (in table)")
    for table, rows in data.items():
        read = len(rows)
        s = summary_from_rows(table, rows) if (dry_run or conn is None) else summary_from_db(conn, table)
        result[table] = {**s, "rows_read": read}
        out(f"{table:44} {read:>10,} {s['rows']:>14,}  {s['first']} .. {s['last']}")
    result["reconciliation"] = {}
    for table, label in (("amazon_ads_search_term_daily", "search-term cost"), ("amazon_ads_targeting_daily", "targeting cost")):
        costs = row_day_costs(table, data[table]) if (dry_run or conn is None) else db_day_costs(conn, table)
        for v3, vlabel in ((False, "Sponsored Products rows of every report version"), (True, "version v3 only")):
            spend = campaign_spend(conn, v3) if conn is not None else {}
            rec = reconcile(costs, spend)
            result["reconciliation"][(table, "v3" if v3 else "all")] = rec
            out(f"reconciliation {label} vs amazon_ads_campaign_daily ({vlabel}): {_fmt(rec)}")
    st = row_day_costs("amazon_ads_search_term_daily", data["amazon_ads_search_term_daily"]) if (dry_run or conn is None) else db_day_costs(conn, "amazon_ads_search_term_daily")
    tg = row_day_costs("amazon_ads_targeting_daily", data["amazon_ads_targeting_daily"]) if (dry_run or conn is None) else db_day_costs(conn, "amazon_ads_targeting_daily")
    result["reconciliation"][("search_term", "targeting")] = rec = reconcile(st, tg)
    out(f"reconciliation search-term cost vs targeting cost, same days (search terms list only the queries your ads served on): {_fmt(rec, "targeting cost")}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="folder with sp_search_term/, sp_targeting/, sp_*_entities/ (see FORMAT.md in it)")
    ap.add_argument("--dry-run", action="store_true", help="read and check the files, write nothing")
    ap.add_argument("--database-url")
    a = ap.parse_args(argv)
    root = Path(a.dir).expanduser()
    if not root.is_dir():
        sys.exit(f"{root} is not a folder")
    conn = db.connect(a.database_url)
    try:
        if a.dry_run:
            conn.execute("SET default_transaction_read_only = on")
        run(root, conn, dry_run=a.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
