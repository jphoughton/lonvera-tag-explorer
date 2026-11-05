"""Pacing feeds: read the sheet's raw feed tabs (READ-ONLY, service account) and the app's own ad_daily into proj_feed_* tables.

Daily hook (called by the projection runner after etl.run_daily has refreshed ad_daily):

    refresh(conn, days=14) -> {"rows": int, "data_through": "YYYY-MM-DD", "feeds": {...}}
    freshness(conn)        -> [{"feed", "label", "cadence": "daily", "data_through": date | None, "stale_after_days": 2}, ...]

`refresh` replaces the trailing `days` of every sheet-fed feed (idempotent: running it twice gives the same rows; a feed table that is still empty
is loaded in full) and rebuilds the Meta aggregates from ad_daily for the same window. Meta is never pulled here. It commits when it is done.
A feed tab that is missing, unreadable or yields no rows raises FeedError naming the tab; nothing from that feed is written.

CLI:  python -m etl.projection_pacing refresh [--days 14] [--snapshot-dir DIR]
      python -m etl.projection_pacing backfill [--snapshot-dir DIR]      full reload of every feed + Meta history before ad_daily begins
      python -m etl.projection_pacing seed-hourly [--snapshot-dir DIR]   Meta time-of-day curve (typed input of the sheet; refresh also loads it when the table is empty)
Live reads use GOOGLE_SA_JSON (or the local key file) through etl.sheets_io; --snapshot-dir reads the frozen snapshot instead.

Server-side feeds (docs/projection/FEEDS_PLAN.md, MODULE_feeds.md): each of the ten sheet-fed feeds above has a twin in etl/feeds with a per-feed mode in
proj_feed_config. `sheet` (the default) behaves exactly as described here. `direct` pulls the feed from its source API into the same table and never reads
its sheet tab, unless the pull fails: then the tab is read as before and the result carries a warning. `shadow` is the same as `sheet` here (the `feeds`
service fills the shadow tables). Backfill and --snapshot-dir always read the sheet.

Feed tabs and columns relied on (sheet 1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI) are listed in docs/projection/MODULE_pacing.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from psycopg.rows import tuple_row

from app.projection.pacing.feedspec import FEED_SPECS, FeedSpec, freshness as _freshness

from . import db, sheets_io
from . import feeds as feed_hub

DEFAULT_SNAPSHOT = Path(os.environ.get("PROJECTION_SNAPSHOT_DIR", "~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03")).expanduser()


class FeedError(RuntimeError):
    """A feed tab is missing, unreadable or empty."""


# ---------------------------------------------------------------------------------------------------------------- classification (pure)

# Classification rules. The values below are the sheet's rules and the defaults of proj_pacing_setting; the ETL reads the table at the start of a refresh so an edit
# there applies to the days the refresh loads (backfill reloads every day).
DEFAULT_RULES: dict[str, Any] = {
    "stage_keyword": "test",
    "product_rules": {"VitaBull": "vita ?bull|vital ?bull|^bull", "Beef Liver": "beef ?liver|^liver", "Boneflex": "bone ?flex|^bone\\b"},
    "product_level_rules": {"VitaBull": "vita ?bull|vital ?bull|(?<![a-z])bull(?![a-z])", "Beef Liver": "beef ?liver|(?<![a-z])liver(?![a-z])",
                            "Boneflex": "bone ?flex|(?<![a-z])bone(?![a-z])",
                            "Other": "vital ?belle|vita ?belle|vitalbelle|vitabelle|vital ?flow|vitaflow|vitalflow"},
}
RULE_NOTES = {
    "stage_keyword": "Testing when the campaign name contains this (sheet: meta_campaign_history.classify_stage)",
    "product_rules": "Campaign-name patterns for the Meta campaign groups (sheet: meta_campaign_history.classify_product); exactly one product must match",
    "product_level_rules": "Campaign then ad set name patterns for spend by product (sheet: Meta Spend - By Product sync); exactly one product must match",
}


class Rules:
    """Compiled classification rules."""

    def __init__(self, raw: dict | None = None):
        r = {**DEFAULT_RULES, **(raw or {})}
        self.stage_keyword = str(r["stage_keyword"]).lower()
        self.product = [(p, re.compile(rx)) for p, rx in r["product_rules"].items()]
        self.level = [(p, re.compile(rx)) for p, rx in r["product_level_rules"].items()]


DEFAULT_COMPILED = Rules()


def load_rules(conn) -> Rules:
    """The stored rules (proj_pacing_setting), the sheet's defaults for any key not stored."""
    try:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT key, value FROM proj_pacing_setting")
            raw = {k: v for k, v in cur.fetchall() if k in DEFAULT_RULES}
        return Rules(raw)
    except Exception:                                                    # noqa: BLE001  (table not migrated yet: use the sheet's rules)
        conn.rollback()
        return DEFAULT_COMPILED


def classify_campaign_product(name: str | None, rules: Rules = DEFAULT_COMPILED) -> str:
    """Product from a campaign name: exactly one of the product patterns, else Unclassified."""
    n = str(name or "").lower()
    hits = [p for p, rx in rules.product if rx.search(n)]
    return hits[0] if len(hits) == 1 else "Unclassified"


def classify_stage(name: str | None, rules: Rules = DEFAULT_COMPILED) -> str:
    """Testing when the campaign name contains the stage keyword, else Scaling."""
    return "Testing" if rules.stage_keyword in str(name or "").lower() else "Scaling"


def _one_product(text: str | None, rules: Rules) -> str | None:
    t = str(text or "").lower()
    hits = [p for p, rx in rules.level if rx.search(t)]
    return hits[0] if len(hits) == 1 else None


def classify_product_by_level(campaign: str | None, adset: str | None, rules: Rules = DEFAULT_COMPILED) -> str:
    """Product for spend-by-product: campaign name first, then ad set name, else Other (ad names are not read)."""
    for text in (campaign, adset):
        p = _one_product(text, rules)
        if p:
            return p
    return "Other"


# ---------------------------------------------------------------------------------------------------------------- sheet reading

def _num(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return sheets_io.num(v)


def _day(v: Any) -> dt.date | None:
    if isinstance(v, bool) or v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return sheets_io.serial_to_date(v) if v > 20000 else None
    return sheets_io.serial_to_date(v)


def read_grid(tab: str, snapshot_dir: str | os.PathLike | None = None) -> list[list]:
    """Rows of one tab. Live: values.get through the read-only service account. Snapshot: values (full tabs) or head + tail (large tabs)."""
    if snapshot_dir:
        sd = Path(snapshot_dir)
        manifest = json.loads((sd / "_manifest.json").read_text())
        entry = next((e for e in manifest["tabs"] if e["title"].strip() == tab.strip()), None)
        if entry is None or entry.get("mode") == "skipped":
            raise FeedError(f"{tab}: not in the snapshot")
        obj = json.loads((sd / f"{entry['stem']}.values.json").read_text())
        return obj["values"] if "values" in obj else list(obj["head"]) + list(obj["tail"])
    try:
        res = sheets_io._service().spreadsheets().values().get(
            spreadsheetId=sheets_io.PROJ_SHEET, range=f"'{tab}'!A1:ZZ", valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="SERIAL_NUMBER").execute()
    except Exception as e:                                                  # noqa: BLE001
        raise FeedError(f"{tab}: cannot read the sheet tab ({db.scrub(e)[:200]})") from None
    return res.get("values", [])


def _data_rows(grid: list[list], first_col: Callable[[Any], bool]) -> list[list]:
    return [r for r in grid if r and first_col(r[0])]


def _is_serial(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 20000


def _c(r: list, i: int) -> Any:
    return r[i] if i < len(r) else None


# ---------------------------------------------------------------------------------------------------------------- parsers (grid -> rows)

def parse_orders(grid):
    return [(_day(r[0]), *[_num(_c(r, i)) for i in range(1, 8)]) for r in _data_rows(grid, _is_serial)]


def parse_amazon(grid):
    return [(_day(r[0]), *[_num(_c(r, i)) for i in range(1, 10)]) for r in _data_rows(grid, _is_serial)]


def parse_platform(grid):
    out: dict[tuple, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        key = (_day(r[0]), str(_c(r, 1) or "").strip())
        vals = [_num(_c(r, i)) for i in range(2, 9)]
        cur = out.setdefault(key, [0.0] * 7)
        out[key] = [a + b for a, b in zip(cur, vals)]
    return [(d, p, *v) for (d, p), v in out.items() if p]


def parse_attribution(grid):
    out: dict[tuple, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        if str(_c(r, 1) or "last_click") != "last_click":
            continue
        key = (_day(r[0]), str(_c(r, 2) or "").strip())
        vals = [_num(_c(r, i)) for i in range(3, 7)]
        cur = out.setdefault(key, [0.0] * 4)
        out[key] = [a + b for a, b in zip(cur, vals)]
    return [(d, s, *v) for (d, s), v in out.items() if s]


def parse_traffic(grid):
    return [(_day(r[0]), *[_num(_c(r, i)) for i in range(1, 4)]) for r in _data_rows(grid, _is_serial)]


def _grain_rows(grid):
    return [r for r in grid if r and str(r[0]) in ("day", "week", "month")]


def parse_meta_reach(grid):
    out: dict[tuple, list] = {}
    for r in _grain_rows(grid):
        key = (str(r[0]), str(_c(r, 1)))
        cur = out.setdefault(key, [_day(_c(r, 2)), _day(_c(r, 3)), 0.0, 0.0, 0.0, 0.0, 0.0])
        for k, i in enumerate((6, 7, 9, 10, 11)):
            cur[2 + k] += _num(_c(r, i))
    return [(g, k, *v) for (g, k), v in out.items() if v[0] is not None and v[1] is not None]


def parse_product_reach(grid):
    out: dict[tuple, list] = {}
    for r in _grain_rows(grid):
        key = (str(r[0]), str(_c(r, 1)), str(_c(r, 4)))
        cur = out.setdefault(key, [_day(_c(r, 2)), _day(_c(r, 3)), 0.0])
        cur[2] += _num(_c(r, 5))
    return [(g, k, v[0], v[1], p, v[2]) for (g, k, p), v in out.items() if v[0] is not None and v[1] is not None]


def parse_nc_product(grid):
    out: dict[tuple, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        key = (_day(r[0]), str(_c(r, 1) or "").strip())
        cur = out.setdefault(key, [0.0, 0.0])
        cur[0] += _num(_c(r, 2))
        cur[1] += _num(_c(r, 3))
    return [(d, p, v[0], v[1]) for (d, p), v in out.items() if p]


def parse_anyclick(grid):
    out: dict[tuple, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        stage = str(_c(r, 4) or "").strip().capitalize()
        if stage not in ("Scaling", "Testing"):
            continue
        cur = out.setdefault((_day(r[0]), stage), [0.0, 0.0])
        cur[0] += _num(_c(r, 5))
        cur[1] += _num(_c(r, 6))
    return [(d, s, v[0], v[1]) for (d, s), v in out.items()]


def parse_bids(grid):
    out: dict[dt.date, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        cur = out.setdefault(_day(r[0]), [0.0] * 5)
        for k, i in enumerate((8, 11, 12, 13, 14)):
            cur[k] += _num(_c(r, i))
    return [(d, *v) for d, v in out.items()]


def parse_meta_campaign_history(grid, rules: Rules = DEFAULT_COMPILED):
    out = []
    for r in _data_rows(grid, _is_serial):
        name = str(_c(r, 3) or "")
        out.append((_day(r[0]), str(_c(r, 2) or ""), name, classify_campaign_product(name, rules), classify_stage(name, rules),
                    _num(_c(r, 6)), _num(_c(r, 7)), _num(_c(r, 8)), _num(_c(r, 9)), _num(_c(r, 10))))
    return out


def parse_meta_product_spend(grid):
    out: dict[tuple, list[float]] = {}
    for r in _data_rows(grid, _is_serial):
        cur = out.setdefault((_day(r[0]), str(_c(r, 1) or "").strip()), [0.0, 0.0])
        cur[0] += _num(_c(r, 2))
        cur[1] += _num(_c(r, 4))
    return [(d, p, v[0], v[1]) for (d, p), v in out.items() if p]


def parse_hourly(grid):
    """Automated Pacing L23:M46: hour (fraction of a day) and the 30-day spend in that hour."""
    out = []
    for r in grid[22:46]:
        h, s = _c(r, 11), _c(r, 12)
        if isinstance(h, (int, float)) and isinstance(s, (int, float)):
            out.append((int(round(h * 24)), float(s)))
    return out


# ---------------------------------------------------------------------------------------------------------------- feed table

class Feed:
    """One sheet-fed table: spec (tab, table, columns, window column) plus the parser for the tab's grid."""

    def __init__(self, spec: FeedSpec, parser: Callable):
        self.spec, self.parser = spec, parser
        self.key, self.label, self.tab, self.table = spec.key, spec.label, spec.tab, spec.table
        self.cols, self.window_col = list(spec.cols), spec.window_col

    def window_of(self, row: tuple) -> dt.date:
        return row[self.cols.index(self.window_col)]


_PARSERS = {"orders": parse_orders, "amazon": parse_amazon, "platform": parse_platform, "attribution": parse_attribution, "traffic": parse_traffic,
            "meta_reach": parse_meta_reach, "product_reach": parse_product_reach, "nc_product": parse_nc_product, "anyclick": parse_anyclick,
            "bids": parse_bids}
FEEDS: list[Feed] = [Feed(s, _PARSERS[s.key]) for s in FEED_SPECS]
FEED_BY_KEY = {f.key: f for f in FEEDS}

# Meta history that predates ad_daily (loaded by backfill only)
META_HISTORY_TAB = "Meta Campaign - History"
META_PRODUCT_TAB = "Meta Spend - By Product"


def _scalar(conn, sql: str, params: tuple = ()):
    """First column of the first row, whatever row factory the connection uses (the daily runner hands over dict rows)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
    return r[0] if r else None


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def _write(conn, feed: Feed, rows: list[tuple], since: dt.date | None) -> int:
    """Replace the feed's rows whose window date is >= since (everything when since is None) with the parsed rows in that window."""
    wcol = feed.window_col
    if since is None:
        conn.execute(f"DELETE FROM {feed.table}")
        keep = rows
    else:
        conn.execute(f"DELETE FROM {feed.table} WHERE {wcol} >= %s", (since,))
        keep = [r for r in rows if feed.window_of(r) >= since]
    if not keep:
        return 0
    sql = f"INSERT INTO {feed.table} ({', '.join(feed.cols)}) VALUES ({_placeholders(len(feed.cols))})"
    with conn.cursor() as cur:
        cur.executemany(sql, keep)
    return len(keep)


def _table_last_day(conn, feed: Feed) -> dt.date | None:
    return _scalar(conn, f"SELECT max({feed.window_col}) FROM {feed.table}")


def _record(conn, feed_key: str, source: str, rows: int, first: dt.date | None, last: dt.date | None, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO proj_feed_runs (feed, source, loaded_at, rows_loaded, first_day, last_day, note) VALUES (%s, %s, now(), %s, %s, %s, %s) "
        "ON CONFLICT (feed) DO UPDATE SET source = excluded.source, loaded_at = now(), rows_loaded = excluded.rows_loaded, "
        "first_day = excluded.first_day, last_day = excluded.last_day, note = excluded.note",
        (feed_key, source, rows, first, last, note))


def load_feed(conn, feed: Feed, grid: list[list], days: int | None) -> dict:
    rows = feed.parser(grid)
    rows = [r for r in rows if r[0] is not None]
    if not rows:
        raise FeedError(f"{feed.tab}: the tab has no data rows")
    last = max(feed.window_of(r) for r in rows)
    since = None
    if days is not None and _table_last_day(conn, feed) is not None:
        since = last - dt.timedelta(days=days - 1)
    n = _write(conn, feed, rows, since)
    first = min(feed.window_of(r) for r in rows if since is None or feed.window_of(r) >= since)
    _record(conn, feed.key, f"sheet:{feed.tab}", n, first, last, None if since is None else f"trailing {days} days from {since.isoformat()}")
    return {"rows": n, "data_through": last.isoformat(), "since": since.isoformat() if since else None}


# ---------------------------------------------------------------------------------------------------------------- Meta from ad_daily

def refresh_meta_from_app(conn, since: dt.date | None, rules: Rules | None = None) -> dict:
    """Rebuild proj_feed_meta_day / proj_feed_meta_product (source 'app') from ad_daily for days >= since (all days when None)."""
    rules = rules or load_rules(conn)
    params: tuple = ()
    where = ""
    if since is not None:
        where, params = "WHERE d.day >= %s", (since,)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
        "SELECT d.day, d.ad_id, d.campaign_id, c.name AS cname, a.adset_name, a.ad_name, d.meta_family, "
        "       coalesce(d.spend,0), coalesce(d.impressions,0), coalesce(d.link_clicks,0), coalesce(d.purchases,0), coalesce(d.conv_value,0) "
        f"FROM ad_daily d LEFT JOIN meta_campaigns c ON c.campaign_id = d.campaign_id LEFT JOIN meta_ads a ON a.ad_id = d.ad_id {where}", params)
        rows = cur.fetchall()
    camp: dict[tuple, list] = {}
    prod: dict[tuple, list] = {}
    memo: dict[tuple, str] = {}
    for day, ad_id, cid, cname, adset, adname, fam, sp, imp, lc, pu, cv in rows:
        cname = cname or fam or ""
        c = camp.setdefault((day, cid or ""), [cname, classify_campaign_product(cname, rules), classify_stage(cname, rules), 0.0, 0.0, 0.0, 0.0, 0.0])
        for i, v in enumerate((sp, imp, lc, pu, cv)):
            c[3 + i] += float(v)
        mk = (cname, adset or "")
        p = memo.get(mk)
        if p is None:
            p = memo[mk] = classify_product_by_level(cname, adset, rules)
        pr = prod.setdefault((day, p), [0.0, 0.0])
        pr[0] += float(sp)
        pr[1] += float(lc)
    if since is None:
        conn.execute("DELETE FROM proj_feed_meta_day WHERE source = 'app'")
        conn.execute("DELETE FROM proj_feed_meta_product WHERE source = 'app'")
    else:
        conn.execute("DELETE FROM proj_feed_meta_day WHERE source = 'app' AND day >= %s", (since,))
        conn.execute("DELETE FROM proj_feed_meta_product WHERE source = 'app' AND day >= %s", (since,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value) "
            "VALUES (%s, %s, 'app', %s, %s, %s, %s, %s, %s, %s, %s)",
            [(day, cid, v[0], v[1], v[2], round(v[3], 2), round(v[4], 2), round(v[5], 2), round(v[6], 2), round(v[7], 2)) for (day, cid), v in camp.items()])
        cur.executemany("INSERT INTO proj_feed_meta_product (day, product, source, spend, link_clicks) VALUES (%s, %s, 'app', %s, %s)",
                        [(day, p, round(v[0], 2), round(v[1], 2)) for (day, p), v in prod.items()])
    days = sorted({k[0] for k in camp})
    first, last = (days[0], days[-1]) if days else (None, None)
    _record(conn, "meta_ad_daily", "ad_daily", len(camp), first, last,
            None if since is None else f"trailing from {since.isoformat()}")
    return {"rows": len(camp), "data_through": last.isoformat() if last else None, "first_app_day": first.isoformat() if first else None}


def load_meta_history_from_sheet(conn, history_grid: list[list], product_grid: list[list], rules: Rules | None = None) -> dict:
    """Days before ad_daily begins: the sheet's campaign history and spend-by-product (source 'sheet'). Replaces all 'sheet' rows."""
    camp = parse_meta_campaign_history(history_grid, rules or load_rules(conn))
    prod = parse_meta_product_spend(product_grid)
    if not camp or not prod:
        raise FeedError(f"{META_HISTORY_TAB} / {META_PRODUCT_TAB}: no data rows")
    agg: dict[tuple, list] = {}
    for day, cid, name, product, stage, sp, imp, lc, pu, cv in camp:
        c = agg.setdefault((day, cid), [name, product, stage, 0.0, 0.0, 0.0, 0.0, 0.0])
        for i, v in enumerate((sp, imp, lc, pu, cv)):
            c[3 + i] += v
    conn.execute("DELETE FROM proj_feed_meta_day WHERE source = 'sheet'")
    conn.execute("DELETE FROM proj_feed_meta_product WHERE source = 'sheet'")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value) "
            "VALUES (%s, %s, 'sheet', %s, %s, %s, %s, %s, %s, %s, %s)",
            [(d, cid, v[0], v[1], v[2], round(v[3], 2), round(v[4], 2), round(v[5], 2), round(v[6], 2), round(v[7], 2)) for (d, cid), v in agg.items()])
        cur.executemany("INSERT INTO proj_feed_meta_product (day, product, source, spend, link_clicks) VALUES (%s, %s, 'sheet', %s, %s)",
                        [(d, p, round(s, 2), round(lc, 2)) for d, p, s, lc in prod])
    first, last = min(k[0] for k in agg), max(k[0] for k in agg)
    _record(conn, "meta_sheet_history", f"sheet:{META_HISTORY_TAB}", len(agg), first, last, "days before ad_daily begins")
    return {"rows": len(agg), "first": first.isoformat(), "last": last.isoformat()}


def seed_hourly(conn, grid: list[list]) -> int:
    rows = parse_hourly(grid)
    if len(rows) != 24:
        raise FeedError(f"Automated Pacing: expected 24 hourly spend cells (L23:M46), found {len(rows)}")
    conn.execute("DELETE FROM proj_pacing_hourly")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_pacing_hourly (hour, spend_30d, days_in_curve, note) VALUES (%s, %s, 30, %s)",
                        [(h, s, "Meta Ads Manager time-of-day report, last 30 days, typed in the sheet (Automated Pacing L23:M46)") for h, s in rows])
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------------------------------------------- public hooks

def refresh(conn, days: int = 14, snapshot_dir: str | os.PathLike | None = None, backfill: bool = False, *, hub_ctx: Any = None) -> dict:
    """Replace the trailing `days` of every feed (everything when backfill=True or a feed table is empty) and rebuild Meta from ad_daily.

    A feed whose mode (etl.feeds, proj_feed_config) is `direct` is pulled from its source API instead of the sheet; its tab is read only if that pull fails
    (the result then carries a warning). `hub_ctx` is the etl.feeds context for those pulls (tests inject fake HTTP and a fake warehouse through it)."""
    if days < 1:
        raise ValueError("days must be >= 1")
    out: dict[str, Any] = {}
    warnings: list[str] = []
    total = 0
    use_hub = not backfill and snapshot_dir is None                       # a backfill reloads full history and a snapshot is the sheet: both read the sheet
    modes = {f.key: (feed_hub.mode_of(conn, f.key) if use_hub else "sheet") for f in FEEDS}
    grids = {}
    for feed in FEEDS:                                                   # read every sheet tab this run needs first: a failure writes nothing
        if modes[feed.key] != "direct":
            grids[feed.key] = read_grid(feed.tab, snapshot_dir)
    ctx = hub_ctx
    for feed in FEEDS:
        d = None if backfill else days

        def sheet(feed=feed, d=d):
            return load_feed(conn, feed, grids[feed.key] if feed.key in grids else read_grid(feed.tab, snapshot_dir), d)   # a direct feed's tab is read only on fallback
        if modes[feed.key] == "sheet":
            res = sheet()
        else:
            ctx = ctx or feed_hub.make_ctx()                             # one context for the run: the ExpandFi feeds share one warehouse credential
            res = feed_hub.load(feed.key, conn, d, sheet, ctx, module=True)
            warn = feed_hub.fallback_warning(feed.key, res)
            if warn:
                warnings.append(warn)
        out[feed.key] = res
        total += res["rows"]
    # Meta from the app's own ad_daily. Sheet history only on backfill (days before ad_daily begins).
    first_run = _scalar(conn, "SELECT 1 FROM proj_feed_meta_day WHERE source = 'sheet' LIMIT 1") is None          # first deploy: load the history once, no manual backfill
    if backfill or first_run:
        out["meta_sheet_history"] = load_meta_history_from_sheet(conn, read_grid(META_HISTORY_TAB, snapshot_dir), read_grid(META_PRODUCT_TAB, snapshot_dir))
        total += out["meta_sheet_history"]["rows"]
    last_app = _scalar(conn, "SELECT max(day) FROM ad_daily")
    have_app_rows = _scalar(conn, "SELECT 1 FROM proj_feed_meta_day WHERE source = 'app' LIMIT 1") is not None
    since = None if backfill or not have_app_rows or last_app is None else last_app - dt.timedelta(days=days - 1)
    if last_app is not None:
        out["meta_ad_daily"] = refresh_meta_from_app(conn, since)
        total += out["meta_ad_daily"]["rows"]
    elif backfill:                                                       # ad_daily is empty: nothing of the app's may linger
        conn.execute("DELETE FROM proj_feed_meta_day WHERE source = 'app'")
        conn.execute("DELETE FROM proj_feed_meta_product WHERE source = 'app'")
    if _scalar(conn, "SELECT count(*) FROM proj_pacing_hourly") == 0:           # first run: load the typed hourly Meta curve once (no manual seed needed)
        try:
            out["hourly_curve"] = {"rows": seed_hourly(conn, read_grid("Automated Pacing ", snapshot_dir))}
        except FeedError as e:                                                 # a missing curve must not stop the daily feeds: the page says what is missing
            out["hourly_curve"] = {"rows": 0, "warning": db.scrub(e)[:200]}
    conn.commit()
    thr = [dt.date.fromisoformat(v["data_through"]) for k, v in out.items() if isinstance(v, dict) and v.get("data_through") and k != "meta_sheet_history"]
    res = {"rows": total, "data_through": min(thr).isoformat() if thr else None, "feeds": out}
    if warnings:
        res["warnings"] = warnings
    return res


def freshness(conn) -> list[dict]:
    """One entry per feed: the last day held in the table (read from the table itself). Hook for the global /api/projection/freshness.
    A feed in `direct` mode gets the feed's own stale-after limit (etl.feeds registry) and says so in its label; every other mode is unchanged."""
    return [feed_hub.direct_chip(conn, e, e["feed"]) if e["feed"] in FEED_BY_KEY else e for e in _freshness(conn)]


# ---------------------------------------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("refresh", "backfill", "seed-hourly"):
        p = sub.add_parser(name)
        p.add_argument("--snapshot-dir", default=None)
        if name == "refresh":
            p.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)
    snap = args.snapshot_dir
    with db.connect() as conn:
        if args.cmd == "refresh":
            res = refresh(conn, args.days, snap)
        elif args.cmd == "backfill":
            res = refresh(conn, 14, snap, backfill=True)
        else:
            res = {"hours": seed_hourly(conn, read_grid("Automated Pacing ", snap))}
    db.log(json.dumps(res, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
