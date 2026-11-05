"""Loaders for the projection `inventory_demand` module: daily units for the US warehouse (Warehance), Amazon and the international warehouse.

    python -m etl.projection_inventory_demand seed [--snapshot]      one-time: all history, monthly Amazon history, country split, stock, levers
    python -m etl.projection_inventory_demand refresh [--days 14]    the daily job (also called by etl.projection_daily)
    python -m etl.projection_inventory_demand status                 what is loaded, per location

Sources are read-only: the sheet's `Warehance Daily`, `Amazon - Product Daily` (the daily-refreshed Amazon tab; `Amazon Daily Units` is the
fallback), `Intl Daily Units` and `Warehance Fcst Inputs` tabs, through the service account (env GOOGLE_SA_JSON, or the local key file), the
same way etl/sheets_io.py does. The sheet's own sync jobs keep writing those
tabs; nothing here calls Warehance or ExpandFi and nothing is ever written back to the sheet.

A day that is still filling in (Warehance's current day, Amazon's two reporting-lag days) is not loaded: everything stored is a complete day,
and `refresh` reports the days it held back. International days from the sheet's estimate date on are flagged `is_estimate`; a real count
(an uploaded 3PL export) is never replaced by an estimate.

Server-side feeds (docs/projection/FEEDS_PLAN.md): units_revamp, units_amazon, units_intl and stock_revamp each have a twin in etl/feeds with a per-feed
mode in proj_feed_config. `sheet` (the default) and `shadow` behave exactly as described above. `direct` pulls that feed from Warehance / the ExpandFi
warehouse into the same tables and never reads its sheet tab, unless the pull fails: then the tab is read as before and the result carries a warning.

Hook contract for the daily runner (app/projection/freshness.py):
    refresh(conn, days=14) -> {"rows", "data_through", ...}      idempotent, trailing-days upsert, raises FeedError when a tab is empty/unreadable
    freshness(conn)        -> [{"feed", "label", "cadence", "data_through", "stale_after_days"}, ...]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from app.projection.inventory_demand import constants as C
from app.projection.inventory_demand import store
from app.projection.inventory_demand.store import DailyRow

from . import db as etl_db
from . import feeds as feed_hub
from . import sheets_io

Reader = Callable[[str], list[list]]

STOCK_TAB = "Warehance Fcst Inputs"
DIRECT_FEED = {"revamp": "units_revamp", "amazon": "units_amazon", "intl": "units_intl"}      # location -> its etl.feeds twin
STOCK_FEED = "stock_revamp"
AMAZON_MONTHLY_TAB = "Amazon Monthly Units"
COUNTRY_TAB = "Intl By Country"
INPUT_TABS = {"revamp": "Warehance Fcst Inputs", "amazon": "Amazon Fcst Inputs", "intl": "Intl Fcst Inputs"}
SOURCE = {"revamp": ["warehance"], "amazon": ["expandfi-product-daily", "expandfi"], "intl": ["tracking-export", "estimate"]}
AMAZON_SOURCES = ("product-daily", "daily-units")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class FeedError(RuntimeError):
    """A sheet tab is empty, unreadable or does not look like the feed it should be. The message is safe to store (no credentials)."""


# ------------------------------------------------------------------------------------------------------------------ readers

def live_reader(sheet_id: str | None = None) -> Reader:
    """Reads a whole tab from the live sheet (read-only scope). Errors are re-raised as FeedError without the underlying text."""
    sid = sheet_id or sheets_io.PROJ_SHEET

    def read(tab: str) -> list[list]:
        try:
            res = sheets_io._service().spreadsheets().values().get(
                spreadsheetId=sid, range=f"'{tab}'!A1:Z", valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER").execute()
        except Exception as e:                                                 # noqa: BLE001
            raise FeedError(f"cannot read sheet tab '{tab}': {type(e).__name__}") from None
        return res.get("values", [])

    return read


def snapshot_reader() -> Reader:
    """Reads a tab from the frozen sheet snapshot (scripts/projection/snapshot_read.py)."""
    from scripts.projection import snapshot_read as sr

    def read(tab: str) -> list[list]:
        try:
            obj = sr.load(tab)
            rows = obj["values"] if "values" in obj else obj["head"][:2] + obj["tail"]      # a head/tail capture: caption and header, then the newest rows
            return [[None if v == "" else v for v in row] for row in rows]
        except Exception as e:                                                 # noqa: BLE001
            raise FeedError(f"cannot read snapshot tab '{tab}': {type(e).__name__}") from None

    return read


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _serial_date(v: Any) -> date | None:
    n = _num(v)
    return sheets_io.EPOCH + timedelta(days=int(n)) if n is not None and 20000 < n < 80000 else None


def _cell(row: list, i: int) -> Any:
    return row[i] if i < len(row) and row[i] != "" else None


# ------------------------------------------------------------------------------------------------------------------ daily tabs

@dataclass
class ParsedDaily:
    location: str
    rows: list[DailyRow] = field(default_factory=list)          # complete days only
    held_back: list[date] = field(default_factory=list)         # partial days left out
    estimate_from: date | None = None
    tab: str = ""                                                # the sheet tab the rows came from
    ignored_units: float = 0.0                                   # units on products this module does not carry (the sheet's "Other")

    @property
    def days(self) -> list[date]:
        return sorted({r.day for r in self.rows})


def _caption_rules(location: str, caption: str, days: list[date], default_tail: int | None = None) -> tuple[set[date], date | None]:
    """(days to hold back, first estimated day) from the tab's caption, falling back to the feed's usual partial tail."""
    newest = max(days)
    cut: date | None = None
    m = re.search(r"(\d{4}-\d{2}-\d{2}) is a partial day", caption)
    if m:
        cut = date.fromisoformat(m.group(1))
    else:
        m = re.search(r"[Ll]ast (\d+) days? (?:is|are) partial", caption)
        if m:
            n = int(m.group(1))
        elif re.search(r"[Tt]oday's row is partial", caption):
            n = 1
        else:
            n = C.PARTIAL_TAIL_DAYS.get(location, 0) if default_tail is None else default_tail
        cut = newest - timedelta(days=n - 1) if n > 0 else None
    held = {d for d in days if cut is not None and d >= cut}
    est = None
    m = re.search(r"[Rr]ows from (\d{4}-\d{2}-\d{2})\b.{0,40}?\bestimates?", caption)
    if m:
        est = date.fromisoformat(m.group(1))
    return held, est


def parse_daily_tab(location: str, values: list[list], *, tab: str | None = None, fallback: bool = False) -> ParsedDaily:
    """Rows of one wide daily tab (date, one column per product) -> DailyRow list. Raises FeedError for an empty tab or an unexpected layout.
    `fallback` marks the older Amazon tab, whose last two days are still filling in."""
    tab = tab or C.DAILY_TABS[location]
    if not values or not any(v not in (None, "") for r in values for v in r):
        raise FeedError(f"sheet tab '{tab}' is empty")
    hdr_i = next((i for i, r in enumerate(values) if r and str(r[0]).strip().lower() == "date"), None)
    if hdr_i is None:
        raise FeedError(f"sheet tab '{tab}' has no 'Date' header row")
    cols: dict[int, str] = {}
    for j, h in enumerate(values[hdr_i]):
        slug = C.HEADER_PRODUCT.get(str(h).strip().lower())
        if slug:
            cols[j] = slug
    if len(cols) < 3:
        raise FeedError(f"sheet tab '{tab}': expected product columns (Vital Bull, Beef Liver, Boneflex, ...), found {len(cols)}")
    parsed: dict[tuple[str, date], float] = {}
    for r in values[hdr_i + 1:]:
        day = _serial_date(_cell(r, 0))
        if day is None:
            continue
        for j, slug in cols.items():
            v = _cell(r, j)
            n = _num(v) if v is not None else 0.0
            if n is None:
                raise FeedError(f"sheet tab '{tab}': non-numeric units on {day.isoformat()}")
            if n < 0:
                raise FeedError(f"sheet tab '{tab}': negative units on {day.isoformat()}")
            parsed[(slug, day)] = n
    if not parsed:
        raise FeedError(f"sheet tab '{tab}' has a header but no dated rows")
    days = sorted({d for _, d in parsed})
    caption = str(values[0][0]) if values and values[0] else ""
    held, est_from = _caption_rules(location, caption, days, C.FALLBACK_PARTIAL_TAIL_DAYS if fallback else None)
    out = ParsedDaily(location=location, held_back=sorted(held), estimate_from=est_from, tab=tab)
    for (slug, day), n in sorted(parsed.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if day in held:
            continue
        is_est = est_from is not None and day >= est_from
        source = {"revamp": "warehance", "amazon": "expandfi"}.get(location) or ("estimate" if is_est else "tracking-export")
        out.rows.append(DailyRow(location=location, product=slug, day=day, units=n, source=source, is_estimate=is_est))
    if not out.rows:
        raise FeedError(f"sheet tab '{tab}' has no complete day (only the partial tail)")
    return out


def parse_product_daily(values: list[list]) -> ParsedDaily:
    """The long-format Amazon tab (one row per day and product: Date, Product, Revenue, Orders, Units) -> DailyRow list. A product with no
    row on a day sold nothing that day. The tab's caption says today's row is partial: it is held back."""
    tab = C.DAILY_TABS["amazon"]
    if not values or not any(v not in (None, "") for r in values for v in r):
        raise FeedError(f"sheet tab '{tab}' is empty")
    first = lambda c: str(c).split("\n")[0].strip().lower()                       # noqa: E731  (headers read 'Units\nExpandFi sales.quantity_shipped, ...')
    hdr_i = next((i for i, r in enumerate(values) if r and first(r[0]) == "date"), None)
    if hdr_i is None:
        raise FeedError(f"sheet tab '{tab}' has no 'Date' header row")
    names = [first(c) for c in values[hdr_i]]
    try:
        di, pi, ui = names.index("date"), names.index("product"), names.index("units")
    except ValueError:
        raise FeedError(f"sheet tab '{tab}': expected Date, Product and Units columns") from None
    sums: dict[tuple[str, date], float] = {}
    ignored = 0.0
    for r in values[hdr_i + 1:]:
        day = _serial_date(_cell(r, di))
        if day is None:
            continue
        n = _num(_cell(r, ui)) if _cell(r, ui) is not None else 0.0
        if n is None:
            raise FeedError(f"sheet tab '{tab}': non-numeric units on {day.isoformat()}")
        if n < 0:
            raise FeedError(f"sheet tab '{tab}': negative units on {day.isoformat()}")
        slug = C.HEADER_PRODUCT.get(str(_cell(r, pi) or "").strip().lower())
        if slug is None:
            ignored += n
            continue
        sums[(slug, day)] = sums.get((slug, day), 0.0) + n
    if not sums:
        raise FeedError(f"sheet tab '{tab}' has a header but no dated rows")
    days = sorted({d for _, d in sums})
    caption = " ".join(str(r[0]) for i, r in enumerate(values[:hdr_i + 2]) if i != hdr_i and r and isinstance(r[0], str))   # the caption sits under the header
    held, _ = _caption_rules("amazon", caption, days)
    out = ParsedDaily(location="amazon", held_back=sorted(held), tab=tab, ignored_units=ignored)
    for (slug, day), n in sorted(sums.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if day not in held:
            out.rows.append(DailyRow(location="amazon", product=slug, day=day, units=n, source="expandfi-product-daily"))
    if not out.rows:
        raise FeedError(f"sheet tab '{tab}' has no complete day (only the partial tail)")
    return out


def load_feed(location: str, reader: Reader, amazon_source: str = "product-daily") -> tuple[ParsedDaily, str | None]:
    """Parse one location's feed. Amazon reads `Amazon - Product Daily` and falls back to `Amazon Daily Units` only when the first cannot be
    used (returns a warning then); `amazon_source='daily-units'` forces the older tab (reconciling against the sheet's own formulas)."""
    if location != "amazon":
        return parse_daily_tab(location, reader(C.DAILY_TABS[location])), None
    if amazon_source not in AMAZON_SOURCES:
        raise ValueError(f"amazon_source must be one of {AMAZON_SOURCES}")
    fb = C.AMAZON_FALLBACK_TAB
    if amazon_source == "daily-units":
        return parse_daily_tab("amazon", reader(fb), tab=fb, fallback=True), None
    try:
        return parse_product_daily(reader(C.DAILY_TABS["amazon"])), None
    except FeedError as e:
        try:
            parsed = parse_daily_tab("amazon", reader(fb), tab=fb, fallback=True)
        except FeedError as e2:
            raise FeedError(f"{e}; the fallback tab failed too: {e2}") from None
        return parsed, f"{e}; loaded '{fb}' instead"


def refresh_location(conn, location: str, p: ParsedDaily, days: int | None) -> dict:
    """Upsert the trailing `days` complete days of one feed (all days when `days` is None) and drop window rows the feed no longer reports."""
    last = max(r.day for r in p.rows)
    first = (last - timedelta(days=days - 1)) if days else min(r.day for r in p.rows)
    window = [r for r in p.rows if first <= r.day <= last]
    seen, changed = store.upsert_daily(conn, window)
    removed = store.delete_missing_in_window(conn, location, SOURCE[location], first, last, {(r.product, r.day) for r in window})
    est = len({r.day for r in window if r.is_estimate})
    store.log_load(conn, location=location, source=p.tab, rows=seen, first_day=first, last_day=last, actor="system",
                   note=f"{changed} changed, {removed} removed, {len(p.held_back)} partial day(s) held back")
    return {"rows": seen, "changed": changed, "removed": removed, "first_day": first.isoformat(), "data_through": last.isoformat(),
            "held_back": [d.isoformat() for d in p.held_back], "estimate_days": est, "source": p.tab}


# ------------------------------------------------------------------------------------------------------------------ stock snapshot

def parse_stock(values: list[list]) -> tuple[date, list[tuple[str, str, date, float, float]]]:
    """The 'Current inventory' block of Warehance Fcst Inputs -> (snapshot date, [(location, product, date, on hand, allocated)])."""
    if not values:
        raise FeedError(f"sheet tab '{STOCK_TAB}' is empty")
    head = next((i for i, r in enumerate(values) if len(r) >= 3 and str(r[0]).strip() == "Product" and str(r[1]).strip().lower() == "on hand"), None)
    if head is None:
        raise FeedError(f"sheet tab '{STOCK_TAB}' has no 'Product / On hand' block")
    snap: date | None = None
    items: list[tuple[str, float, float]] = []
    for r in values[head + 1:]:
        label = str(_cell(r, 0) or "").strip()
        if not label:
            break
        if label.lower() == "snapshot date":
            snap = _serial_date(_cell(r, 1))
            break
        slug = C.HEADER_PRODUCT.get(label.lower())
        on_hand, allocated = _num(_cell(r, 1)), _num(_cell(r, 2))
        if slug and on_hand is not None:
            items.append((slug, on_hand, allocated or 0.0))
    if snap is None or not items:
        raise FeedError(f"sheet tab '{STOCK_TAB}': stock block has no snapshot date or no products")
    return snap, [("revamp", s, snap, oh, al) for s, oh, al in items]


def refresh_stock(conn, values: list[list]) -> dict:
    snap, rows = parse_stock(values)
    changed = store.upsert_stock(conn, rows, "warehance")
    return {"rows": len(rows), "changed": changed, "as_of": snap.isoformat()}


# ------------------------------------------------------------------------------------------------------------------ public hooks

def refresh(conn, days: int = 14, reader: Reader | None = None, *, hub_ctx: Any = None) -> dict:
    """The daily job: trailing-days upsert of the three daily feeds plus the US warehouse stock snapshot. Commits on success.

    Returns {"rows", "changed", "data_through" (the oldest of the three feeds' last complete day), "locations": {...}, "stock": {...}, "warnings": [...]}.
    Raises FeedError when a tab is empty or unreadable (nothing is written then: the transaction is rolled back).

    A feed whose mode (etl.feeds, proj_feed_config) is `direct` is pulled from its source instead of the sheet; its tab is read only if that pull fails
    (the warning says so). `hub_ctx` is the etl.feeds context for those pulls (tests inject fake HTTP and a fake warehouse through it)."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")
    reader = reader or live_reader()
    try:
        modes = {loc: feed_hub.mode_of(conn, DIRECT_FEED[loc]) for loc in C.LOCATIONS}
        stock_mode = feed_hub.mode_of(conn, STOCK_FEED)
        # read and parse everything the sheet path needs first: a bad tab must not leave a half-refreshed state (a direct feed's tab is read only on fallback)
        feeds = {loc: load_feed(loc, reader) for loc in C.LOCATIONS if modes[loc] != "direct"}
        stock_values = None
        if stock_mode != "direct":
            stock_values = reader(STOCK_TAB)
            parse_stock(stock_values)
        warnings = [w for _, w in feeds.values() if w]
        ctx = hub_ctx or (feed_hub.make_ctx() if "direct" in (*modes.values(), stock_mode) else None)

        def sheet_location(loc: str) -> dict:
            parsed, warn = feeds[loc] if loc in feeds else load_feed(loc, reader)
            if warn and loc not in feeds:
                warnings.append(warn)
            return refresh_location(conn, loc, parsed, days)

        out_locs = {}
        for loc in C.LOCATIONS:
            if modes[loc] == "sheet":
                out_locs[loc] = sheet_location(loc)
                continue
            r = out_locs[loc] = feed_hub.load(DIRECT_FEED[loc], conn, days, lambda loc=loc: sheet_location(loc), ctx, module=True)
            if r.get("mode") == "direct" and not r.get("fallback") and r.get("data_through"):
                store.log_load(conn, location=loc, source=r["source"], rows=r["rows"], first_day=date.fromisoformat(r["first_day"]),
                               last_day=date.fromisoformat(r["data_through"]), actor="system", note="direct feed")
            warn = feed_hub.fallback_warning(DIRECT_FEED[loc], r)
            if warn:
                warnings.append(warn)

        def sheet_stock() -> dict:
            values = stock_values
            if values is None:                                                 # a direct feed's tab is read only when its pull failed
                values = reader(STOCK_TAB)
                parse_stock(values)
            return refresh_stock(conn, values)

        if stock_mode == "sheet":
            stock = sheet_stock()
        else:
            stock = feed_hub.load(STOCK_FEED, conn, days, sheet_stock, ctx, module=True)
            if stock.get("mode") == "direct" and not stock.get("fallback"):
                stock["as_of"] = stock.get("data_through")
            warn = feed_hub.fallback_warning(STOCK_FEED, stock)
            if warn:
                warnings.append(warn)
        store.audit_load(conn, action="inventory_demand.refresh", summary={"locations": out_locs, "stock": stock, "warnings": warnings}, actor="system")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:                                                      # noqa: BLE001
            pass
        raise
    return {"rows": sum(v["rows"] for v in out_locs.values()), "changed": sum(v["changed"] for v in out_locs.values()),
            "data_through": min(v["data_through"] for v in out_locs.values()), "locations": out_locs, "stock": stock, "warnings": warnings}


def freshness(conn) -> list[dict]:
    """One entry per location: the newest complete day loaded.

    The international feed is a hand-uploaded tracking file (estimated days since Sep 16): reported as stale, never fatal to the daily run, until a
    server-side feed replaces it. While `units_intl` is in direct mode it is the server-side feed: cadence daily and the 2-day limit of the feed (a failed
    pull that fell back to the sheet then goes stale loudly). The other locations get the feed's limit while their feed is direct."""
    out = []
    for loc in C.LOCATIONS:
        _, last = store.data_range(conn, loc)
        cadence = "monthly-upload" if loc == "intl" else "daily"
        entry = {"feed": f"inventory_demand.{loc}", "label": C.FEED_LABELS[loc], "cadence": cadence, "data_through": last,
                 "stale_after_days": C.STALE_AFTER_DAYS[loc]}
        direct = feed_hub.direct_chip(conn, entry, DIRECT_FEED[loc])
        if direct is not entry:
            direct = {**direct, "cadence": "daily"}
        out.append(direct)
    return out


# ------------------------------------------------------------------------------------------------------------------ one-time seed

def parse_amazon_monthly(values: list[list]) -> list[tuple[str, str, date, float]]:
    """Amazon units per month by product; months the pull did not finish (the caption says when it was pulled) are left out."""
    if not values:
        raise FeedError(f"sheet tab '{AMAZON_MONTHLY_TAB}' is empty")
    caption = str(values[0][0]) if values and values[0] else ""
    pulled = None
    m = re.search(r"pulled (\d{4}-\d{2}-\d{2})", caption)
    if m:
        pulled = date.fromisoformat(m.group(1))
    head = next((i for i, r in enumerate(values) if r and str(r[0]).strip().lower() == "month"), None)
    if head is None:
        raise FeedError(f"sheet tab '{AMAZON_MONTHLY_TAB}' has no 'Month' header row")
    cols = {j: C.HEADER_PRODUCT[str(h).strip().lower()] for j, h in enumerate(values[head]) if str(h).strip().lower() in C.HEADER_PRODUCT}
    out = []
    for r in values[head + 1:]:
        month = _serial_date(_cell(r, 0))
        if month is None:
            continue
        month = month.replace(day=1)
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if pulled is not None and end > pulled:
            continue
        for j, slug in cols.items():
            n = _num(_cell(r, j))
            if n is not None and n >= 0:
                out.append(("amazon", slug, month, n))
    return out


def parse_countries(values: list[list]) -> list[tuple[date, str, float, int | None]]:
    """International units by destination country and month. Month headers may be spreadsheet dates ('Jul-26' parsed as a date) or text."""
    if not values:
        raise FeedError(f"sheet tab '{COUNTRY_TAB}' is empty")
    head = next((i for i, r in enumerate(values) if r and str(r[0]).strip().lower() == "country"), None)
    if head is None:
        raise FeedError(f"sheet tab '{COUNTRY_TAB}' has no 'Country' header row")
    months: dict[int, tuple[date, int | None]] = {}
    for j, h in enumerate(values[head]):
        if j == 0:
            continue
        d = _serial_date(h)
        if d is not None:
            months[j] = (d.replace(day=1), None)
            continue
        m = re.match(r"\s*([A-Za-z]{3})[a-z]*-(\d{2})(?:\s*\((\d+)d\))?", str(h))
        if m and m.group(1).lower() in _MONTHS:
            months[j] = (date(2000 + int(m.group(2)), _MONTHS[m.group(1).lower()], 1), int(m.group(3)) if m.group(3) else None)
    out = []
    for r in values[head + 1:]:
        name = str(_cell(r, 0) or "").strip()
        if not name or name.upper() == "TOTAL" or name.lower().startswith("us share"):
            continue
        for j, (month, d) in months.items():
            n = _num(_cell(r, j))
            if n is not None and n >= 0:
                out.append((month, name, n, d))
    return out


def parse_levers(location: str, values: list[list]) -> dict:
    """baseline window, monthly growth and the 12 seasonality indices from a Fcst Inputs tab (found by label, not by cell address)."""
    out: dict[str, Any] = {}
    season: dict[int, float] = {}
    for r in values or []:
        label = _cell(r, 0)
        if isinstance(label, str):
            low = label.strip().lower()
            if low.startswith("baseline window") and _num(_cell(r, 1)) is not None:
                out["baseline_days"] = int(_num(_cell(r, 1)))
            elif low.startswith("monthly growth") and _num(_cell(r, 1)) is not None:
                out["monthly_growth"] = _num(_cell(r, 1))
        elif _num(label) is not None and 1 <= int(label) <= 12 and _num(_cell(r, 1)) is not None:
            season.setdefault(int(label), _num(_cell(r, 1)))
    if len(season) == 12:
        out["seasonality"] = [season[i] for i in range(1, 13)]
    return out


def seed(conn, reader: Reader | None = None, *, with_levers: bool = True, amazon_source: str = "product-daily") -> dict:
    """Load all history from the sheet (or the snapshot reader): daily feeds, Amazon monthly history, country split, stock, and the levers of
    the default scenario (only when it holds none, so edits are never overwritten). Commits on success."""
    reader = reader or live_reader()
    result: dict[str, Any] = {"locations": {}, "warnings": []}
    try:
        for loc in C.LOCATIONS:
            parsed, warn = load_feed(loc, reader, amazon_source)
            if warn:
                result["warnings"].append(warn)
            result["locations"][loc] = refresh_location(conn, loc, parsed, None)
        monthly = parse_amazon_monthly(reader(AMAZON_MONTHLY_TAB))
        result["monthly_rows"] = store.upsert_monthly(conn, monthly, "expandfi-monthly")
        result["country_rows"] = store.replace_countries(conn, parse_countries(reader(COUNTRY_TAB)), "tracking-export")
        result["stock"] = refresh_stock(conn, reader(STOCK_TAB))
        if with_levers:
            result["levers"] = seed_levers(conn, reader)
        store.audit_load(conn, action="inventory_demand.seed", summary=result, actor="system")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def seed_levers(conn, reader: Reader) -> str:
    """Patch the default scenario's inventory_demand params from the Fcst Inputs tabs unless it already holds some. Returns what happened."""
    from app.projection import scenarios
    row = scenarios.get_row(conn, scenarios.default_slug(conn))
    if (row["params"] or {}).get("inventory_demand"):
        return "kept existing levers"
    locs = {}
    for loc in C.LOCATIONS:
        try:
            locs[loc] = parse_levers(loc, reader(INPUT_TABS[loc]))
        except FeedError:
            locs[loc] = {}
        locs[loc].setdefault("baseline_days", C.DEFAULT_BASELINE_DAYS[loc])
        locs[loc]["lag_days"] = 0
    scenarios.update(conn, row["slug"], params={"inventory_demand": {"locations": locs}}, actor="system")
    return "seeded from the Fcst Inputs tabs"


# ------------------------------------------------------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    import psycopg
    from psycopg.rows import dict_row

    ap = argparse.ArgumentParser(description="Load inventory demand feeds (read-only from the sheet).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--snapshot", action="store_true", help="read the frozen snapshot instead of the live sheet")
    s.add_argument("--amazon-source", choices=AMAZON_SOURCES, default="product-daily", help="'daily-units' = the older tab the sheet's own forecast used")
    r = sub.add_parser("refresh")
    r.add_argument("--days", type=int, default=14)
    sub.add_parser("status")
    args = ap.parse_args(argv)
    with psycopg.connect(etl_db.database_url(), row_factory=dict_row) as conn:
        if args.cmd == "seed":
            print(seed(conn, snapshot_reader() if args.snapshot else None, amazon_source=args.amazon_source))
        elif args.cmd == "refresh":
            print(refresh(conn, args.days))
        else:
            for f in freshness(conn):
                print(f["feed"], f["data_through"], f"(stale after {f['stale_after_days']} days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
