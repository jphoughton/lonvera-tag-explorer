"""Warehance feeds (Revamp Utah, the US 3PL): units shipped per day by product, the stock snapshot, and the international unit estimate.

Ported from warehance_sync.py (shipments, products) and inventory_rebalance.py (the international estimate from non-US orders). Auth: static key,
env WAREHANCE_API_KEY, header X-API-KEY. Day bases follow the sheet: shipments are dated in America/Denver, the international estimate in Pacific.
The international estimate = non-US ship-to orders (any status: Shopify routes them to the overseas warehouse and Warehance auto-cancels them)
x bundle multiplier from the SKU suffix x a calibration ratio per product fitted Aug 17 - Sep 15 against the 3PL's own export. It has no truth feed
after 2026-09-15; rows are flagged is_estimate and a real count is never overwritten.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from psycopg import sql

from .common import Ctx, DENVER, FeedEmpty, FeedError, Http, PT, Span, denver_today, trailing_span
from .feed import Feed, Metric

API_BASE = "https://api.warehance.com/v1"
PAGE_SLEEP = 0.2
SKU_TO_SLUG = {"LV-VBULL": "bull", "LV-BLC180": "liver", "DPCD1-GN187001": "boneflex", "LV-VBELLE": "belle"}
SHIP_PRODUCTS = ("bull", "liver", "boneflex", "belle")                 # sheet column order
INTL_ESTIMATED = ("bull", "liver", "boneflex")
INTL_PRODUCTS = ("bull", "liver", "boneflex", "flow")                  # the sheet's international tab carries Vital Flow (not estimated: written as 0)
INTL_ESTIMATE_START = dt.date(2026, 9, 16)                             # rows before this are the 3PL's own export
INTL_RATIOS = {"bull": 0.905, "liver": 0.895, "boneflex": 0.868}       # fallback only: the stored levers intl_ratio.<product> (proj_feed_levers) win
SKU_SUFFIX = re.compile(r"-(\d+)$")
LOCATION_SOURCE = {"revamp": "warehance", "intl": "estimate"}


class Warehance:
    def __init__(self, ctx: Ctx):
        self._headers = {"X-API-KEY": ctx.require("WAREHANCE_API_KEY")}
        self.ctx = ctx
        self.http = Http(ctx, "warehance", min_interval=PAGE_SLEEP, retries=5, backoff=2.0, max_backoff=60.0, timeout=60)

    def get(self, path: str, params: dict) -> dict:
        body = self.http.get(API_BASE + path, headers=self._headers, params=params, what=path)
        if body.get("errors"):
            raise FeedError(f"warehance {path}: the API returned errors")
        if "data" not in body:
            raise FeedError(f"warehance {path}: no data in the response")
        return body["data"]

    def paginate(self, path: str, params: dict, list_key: str) -> tuple[list[dict], int]:
        params = dict(params, limit=100)
        items: list[dict] = []
        pages = 0
        while True:
            data = self.get(path, params)
            items.extend(data.get(list_key) or [])
            pages += 1
            if not data.get("has_next_page") or not data.get("next_cursor"):
                return items, pages
            params["cursor"] = data["next_cursor"]


def parse_ts(s: str) -> dt.datetime:
    s = re.sub(r"\.\d+", "", s).replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def utc_midnight(d: dt.date) -> str:
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pt_midnight_utc(d: dt.date) -> str:
    return dt.datetime(d.year, d.month, d.day, tzinfo=PT).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------- shipments (US direct)

def aggregate_shipments(shipments: list[dict]) -> tuple[dict, dict, int, int]:
    """-> ({Denver date: {slug: units}}, {unmapped sku: units}, shipments seen, voided). Voided shipments and duplicate ids are dropped."""
    out: dict[dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unmapped: dict[str, int] = defaultdict(int)
    seen: set = set()
    voided = 0
    for s in shipments:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        if s.get("voided"):
            voided += 1
            continue
        d = parse_ts(s["created_at"]).astimezone(DENVER).date()
        for parcel in s.get("shipment_parcels") or []:
            for it in parcel.get("items") or []:
                sku = it.get("sku") or (it.get("product") or {}).get("sku")
                q = int(it.get("quantity") or 0)
                slug = SKU_TO_SLUG.get(sku)
                if slug:
                    out[d][slug] += q
                elif q:
                    unmapped[str(sku)] += q
    return out, dict(unmapped), len(seen), voided


def denver_span(ctx: Ctx, days: int) -> Span:
    until = denver_today(ctx.now_utc) - dt.timedelta(days=1)       # the current Denver day is partial: never stored
    return Span(until - dt.timedelta(days=days - 1), until)


def pull_units_revamp(ctx: Ctx, span: Span) -> list[tuple]:
    wh = Warehance(ctx)
    shipments, pages = wh.paginate("/shipments", {"created_at_date_start": utc_midnight(span.since), "sort_by": "created_at", "order_by": "asc"}, "shipments")
    agg, unmapped, n, voided = aggregate_shipments(shipments)
    if n < 1:
        raise FeedEmpty("warehance shipments: none returned for the window")
    if unmapped:
        ctx.warn("unmapped SKUs shipped: " + ", ".join(f"{k} x{v}" for k, v in sorted(unmapped.items())[:5]))
    rows = []
    d = span.since
    while d <= span.until:
        for slug in SHIP_PRODUCTS:
            rows.append(("revamp", slug, d, float(agg.get(d, {}).get(slug, 0)), "warehance", False))
        d += dt.timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------------------------------------------- stock snapshot

def pull_stock_revamp(ctx: Ctx, span: Span) -> list[tuple]:
    wh = Warehance(ctx)
    products, _ = wh.paginate("/products", {}, "products")
    stock: dict[str, tuple[float, float]] = {}
    for p in products:
        slug = SKU_TO_SLUG.get(p.get("sku"))
        if slug and not p.get("is_bundle"):
            stock[slug] = (float(p.get("on_hand") or 0), float(p.get("allocated") or 0))
    missing = [s for s in SHIP_PRODUCTS if s not in stock]
    if missing:
        raise FeedError(f"warehance products: no SKU for {missing}")
    return [("revamp", slug, span.until, oh, al, "warehance") for slug, (oh, al) in stock.items()]


def stock_span(ctx: Ctx, days: int) -> Span:
    d = denver_today(ctx.now_utc)
    return Span(d, d)


# ---------------------------------------------------------------------------------------------------------------- international estimate

def intl_product(sku: str | None, name: str | None = None) -> str | None:
    s = f"{sku or ''} {name or ''}".lower()
    if "bull" in s:
        return "bull"
    if "liver" in s or (sku or "") == "LV-BLC180" or "blc" in s:
        return "liver"
    if "bone" in s or (sku or "") == "DPCD1-GN187001" or "bflex" in s:
        return "boneflex"
    if "belle" in s:
        return "belle"
    if "flow" in s or "gn179001" in s:
        return "flow"
    return None


def line_units(item: dict) -> int:
    """Bottles in one order line: metadata quantity x the bundle multiplier in the SKU suffix (LV- / DPCD1- SKUs only)."""
    meta = item.get("metadata") or {}
    q = meta.get("quantity") or 0
    lsku = meta.get("line_item_sku") or ""
    m = SKU_SUFFIX.search(lsku)
    mult = int(m.group(1)) if m and lsku.startswith(("LV-", "DPCD1-")) else 1
    return int(q) * mult


def aggregate_intl(orders: list[dict]) -> tuple[dict, dict, int]:
    """-> ({(Pacific date, slug, country): bottles} for non-US ship-to orders in ANY status, {Pacific date: all orders}, orders without a country).
    Any status on purpose: Shopify routes non-US orders to the overseas warehouse and Warehance auto-cancels them, so `cancelled` there is not cancelled."""
    raw: dict[tuple, int] = defaultdict(int)
    per_day: dict[dt.date, int] = defaultdict(int)
    seen: set = set()
    unknown = 0
    for o in orders:
        if o.get("id") in seen:
            continue
        seen.add(o.get("id"))
        if not o.get("order_date"):
            continue
        d = parse_ts(o["order_date"]).astimezone(PT).date()
        per_day[d] += 1
        country = str((o.get("ship_to_address") or {}).get("country_code") or "").upper()
        if not country:
            unknown += 1
            continue
        if country == "US":
            continue
        for it in o.get("order_items") or []:
            p = intl_product(it.get("sku"), it.get("name"))
            if p:
                raw[(d, p, country)] += line_units(it)
    return dict(raw), dict(per_day), unknown


def ratios(ctx: Ctx) -> dict[str, float]:
    """Calibration ratio per estimated product: the stored lever intl_ratio.<product> (table proj_feed_levers), else the sheet's value as a fallback."""
    out = {}
    for p in INTL_ESTIMATED:
        out[p] = float(ctx.levers.get(f"intl_ratio.{p}", INTL_RATIOS[p]))
    return out


def intl_span(ctx: Ctx, days: int) -> Span:
    until = ctx.today - dt.timedelta(days=1)
    return Span(max(until - dt.timedelta(days=days - 1), INTL_ESTIMATE_START), until)


def intl_agg(ctx: Ctx, span: Span) -> tuple[dict, dict]:
    """One orders pull per run, shared by the `intl_estimate` (raw) and `units_intl` (calibrated) feeds. -> (raw by day/product/country, orders per day)."""
    key = ("intl_orders", span.since)
    if key not in ctx.cache:
        if span.since > span.until:
            raise FeedEmpty("international estimate: the window holds no estimate day yet")
        wh = Warehance(ctx)
        orders, _ = wh.paginate("/orders", {"sort_by": "order_date", "order_by": "asc", "order_date_start": pt_midnight_utc(span.since)}, "orders")
        if not orders:
            raise FeedEmpty("warehance orders: none returned for the window")
        raw, per_day, unknown = aggregate_intl(orders)
        if unknown:
            ctx.warn(f"{unknown} Warehance orders have no ship-to country and were skipped")
        ctx.cache[key] = (raw, per_day)
    return ctx.cache[key]


def complete_days(span: Span, per_day: dict) -> list[dt.date]:
    return [d for d in (span.since + dt.timedelta(days=i) for i in range((span.until - span.since).days + 1)) if per_day.get(d, 0) > 0]


def screen(totals: dict[dt.date, float]) -> dict[dt.date, str]:
    """Days to refuse: total 0, or outside 25%-400% of the mean of the other days in the pulled window (a partial or failed day)."""
    bad = {}
    for d, t in totals.items():
        others = [v for k, v in totals.items() if k != d]
        mean = sum(others) / len(others) if others else 0
        if mean <= 0:
            continue
        if t <= 0:
            bad[d] = f"total 0 while the window mean is {mean:.0f}"
        elif t < 0.25 * mean or t > 4 * mean:
            bad[d] = f"total {t:.0f} is outside 25%-400% of the window mean {mean:.0f}"
    return bad


def pull_intl_estimate(ctx: Ctx, span: Span) -> list[tuple]:
    """Raw non-US bottles per day, product and country (the input of the estimate; scaled by the stored ratio lever at read time)."""
    raw, per_day = intl_agg(ctx, span)
    days = set(complete_days(span, per_day))
    gaps = (span.until - span.since).days + 1 - len(days)
    if gaps:
        ctx.warn(f"{gaps} day(s) with no Warehance orders left out")
    rows = sorted((d, p, c, float(u)) for (d, p, c), u in raw.items() if d in days and u > 0)
    if not rows:
        raise FeedEmpty("international estimate: no non-US units in the window")
    return rows


def pull_units_intl(ctx: Ctx, span: Span) -> list[tuple]:
    """The estimate as the sheet writes it: raw x ratio per product, rounded, one row per day and product (Vital Flow written as 0)."""
    raw, per_day = intl_agg(ctx, span)
    by_dp: dict[tuple, int] = defaultdict(int)
    for (d, p, _c), u in raw.items():
        by_dp[(d, p)] += u
    ratio = ratios(ctx)
    est = {d: {p: int(round(by_dp.get((d, p), 0) * ratio[p])) for p in INTL_ESTIMATED} for d in complete_days(span, per_day)}
    gaps = (span.until - span.since).days + 1 - len(est)
    if gaps:
        ctx.warn(f"{gaps} day(s) with no Warehance orders left out")
    refused = screen({d: float(sum(v.values())) for d, v in est.items()})
    for d, why in sorted(refused.items()):
        ctx.warn(f"intl {d.isoformat()} not written ({why})")
    rows = []
    for d, v in sorted(est.items()):
        if d in refused:
            continue
        for p in INTL_PRODUCTS:
            rows.append(("intl", p, d, float(v.get(p, 0)), "estimate", True))
    if not rows:
        raise FeedEmpty("international estimate: every day was refused")
    return rows


# ---------------------------------------------------------------------------------------------------------------- sheet parsers (the sheet-fed path)

def _units_rows(location: str, grid: list[list]) -> list[tuple]:
    from etl.projection_inventory_demand import parse_daily_tab
    return [(r.location, r.product, r.day, r.units, r.source, r.is_estimate) for r in parse_daily_tab(location, grid).rows]


def sheet_rows_revamp(grid: list[list]) -> list[tuple]:
    return _units_rows("revamp", grid)


def sheet_rows_intl(grid: list[list]) -> list[tuple]:
    return _units_rows("intl", grid)


def sheet_rows_stock(grid: list[list]) -> list[tuple]:
    from etl.projection_inventory_demand import parse_stock
    _, items = parse_stock(grid)
    return [(loc, prod, as_of, oh, al, "warehance") for loc, prod, as_of, oh, al in items]


# ---------------------------------------------------------------------------------------------------------------- feeds

def _loc_where(location: str, only_estimates: bool = False, col: str = "day"):
    def fn(span: Span):
        extra = " AND is_estimate" if only_estimates else ""
        return sql.SQL("location = %s AND {} >= %s" + extra).format(sql.Identifier(col)), (location, span.since)
    return fn


_UNITS_COLS = ("location", "product", "day", "units", "source", "is_estimate")

FEEDS = [
    Feed("units_revamp", "Warehance shipments (US direct to consumer)", "warehance", "proj_units_daily", "proj_feedx_units_daily", _UNITS_COLS,
         ("location", "product", "day"), "day", pull=pull_units_revamp, days=5, stale_after=2, span=denver_span,
         delete_where=_loc_where("revamp"), scope_where="location = 'revamp'", metrics=(Metric("units", 0.01),),
         sheet_tab="Warehance Daily", sheet_parser=("etl.feeds.warehance", "sheet_rows_revamp"),
         notes="Denver-dated shipments, voided excluded; the current Denver day is never stored."),
    Feed("units_intl", "International units shipped (estimated from Warehance non-US orders)", "warehance", "proj_units_daily", "proj_feedx_units_daily",
         _UNITS_COLS, ("location", "product", "day"), "day", pull=pull_units_intl, days=21, stale_after=2, sheet_stale_after=4, span=intl_span,
         delete_where=_loc_where("intl", only_estimates=True), scope_where="location = 'intl'", conflict_nothing=True,
         metrics=(Metric("units", 3.0, 0.02),), sheet_tab="Intl Daily Units", sheet_parser=("etl.feeds.warehance", "sheet_rows_intl"),
         notes="Estimate days only (from 2026-09-16); Vital Flow is not estimated and is written as 0, as the sheet does."),
    Feed("intl_estimate", "International units shipped, estimated (Warehance non-US orders)", "warehance", "proj_feedx_intl_nonus_daily",
         "proj_feedx_intl_nonus_daily", ("day", "product", "country", "units_raw"), ("day", "product", "country"), "day", pull=pull_intl_estimate, days=21,
         stale_after=2, span=intl_span, native=True, metrics=(Metric("units_raw", 0.01),),
         notes="Raw non-US bottles by Pacific day, product and country; the estimate = units_raw x lever intl_ratio.<product> (view proj_feedx_intl_estimate)."),
    Feed("stock_revamp", "Warehance stock on hand (US direct to consumer)", "warehance", "proj_demand_snapshot", "proj_feedx_demand_snapshot",
         ("location", "product", "as_of", "on_hand", "allocated", "source"), ("location", "product", "as_of"), "as_of", pull=pull_stock_revamp, days=1,
         stale_after=2, span=stock_span, delete_where=_loc_where("revamp", col="as_of"), scope_where="location = 'revamp'", metrics=(Metric("on_hand", 5.0, 0.03), Metric("allocated", 5.0, 0.03)),
         settle_days=1, sheet_tab="Warehance Fcst Inputs", sheet_parser=("etl.feeds.warehance", "sheet_rows_stock"),
         notes="Snapshot of the day the job runs; compared with the sheet's snapshot only when both carry the same date."),
]
