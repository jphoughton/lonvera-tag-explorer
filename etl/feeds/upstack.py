"""Upstack feeds (Shopify orders, ad-platform spend, last-click attribution, pixel traffic, any-click new customers by campaign stage).

Ported from lonvera-upstack-sync (sync.py, upstack_traffic_daily.py, upstack_anyclick_campaign.py) so the pulls produce the rows the sheet's raw tabs
hold today: Pacific days, never the partial current day (traffic is the one exception: the sheet includes today).
Auth: static key. Env UPSTACK_API_KEY and UPSTACK_PIXEL_ID (sent as headers x-api-key / x-pixel-id; never logged).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from .common import Ctx, FeedEmpty, Http, Span, days_between, num, round2, trailing_span
from .feed import Feed, Metric

BASE = "https://api.upstackdata.com/db-mgmt"
CLI_VERSION = "1.0.7"                     # sent as x-upstack-cli-version, as the sync scripts do
TIMEZONE = "America/Los_Angeles"
ATTRIBUTION_WINDOW = 7                    # required parameter on /api/query-attribution (7-day click lookback)
ANYCLICK_WINDOW = 30
AD_SOURCES = ("Meta Ads", "Google Ads")
PLATFORM_NAMES = {"googleAdsIntegration": "Google Ads", "facebookIntegration": "Meta (Facebook/Instagram)", "tiktokAdsIntegration": "TikTok Ads"}
ORDER_MEASURES = ["orders.new_customer_total_revenue", "orders.returning_customer_total_revenue", "orders.new_customers",
                  "orders.total_revenue", "orders.count", "orders.tax", "orders.refund_amount"]
CHANNEL_MEASURES = ["channel.spend", "channel.purchases", "channel.purchases_value", "channel.new_customer_purchases",
                    "channel.new_customer_purchases_value", "channel.link_clicks", "channel.clicks"]
TRAFFIC_MEASURES = ["tracked.new_visitors", "tracked.visitors", "tracked.sessions"]
ANYCLICK_FILTER = {"and": [{"field": "attribution.utm_source", "op": "equals", "value": "Meta Ads"}]}
CHUNK_DAYS = 60


class Upstack:
    def __init__(self, ctx: Ctx):
        key, pixel = ctx.require("UPSTACK_API_KEY"), ctx.require("UPSTACK_PIXEL_ID")
        self.pixel = pixel
        self._headers = {"x-api-key": key, "x-pixel-id": pixel, "x-upstack-cli-version": CLI_VERSION, "Content-Type": "application/json"}
        self.http = Http(ctx, "upstack", min_interval=0.2, retries=6, backoff=2.0, max_backoff=60.0, timeout=180)

    def post(self, path: str, body: dict) -> dict:
        return self.http.post(BASE + path, headers=self._headers, json_body=body, what=path)

    def query(self, start: dt.date, end: dt.date, measures: list[str]) -> list[dict]:
        body = {"pixelId": self.pixel, "granularity": "day", "timezone": TIMEZONE,
                "dateRange": {"start": start.isoformat(), "end": end.isoformat()}, "measures": [{"measure": m} for m in measures]}
        res = self.post("/api/query", body).get("results") or []
        return ((res[0].get("data") or {}).get("primary") or []) if res else []

    def channel_details(self, start: dt.date, end: dt.date) -> list[dict]:
        body = {"pixelId": self.pixel, "granularity": "day", "timezone": TIMEZONE, "limit": 20, "offset": 0,
                "sortBy": "sourceUpdatedAt", "sortDirection": "desc",
                "dateRange": {"start": start.isoformat(), "end": end.isoformat()}, "measures": [{"measure": m} for m in CHANNEL_MEASURES]}
        return self.post("/api/query-channel-details", body).get("results") or []

    def attribution(self, day: dt.date, dimension: str, model: str, window: int, filters: dict | None = None) -> list[dict]:
        body = {"pixelId": self.pixel, "dimension": dimension, "dateRange": {"start": day.isoformat(), "end": day.isoformat()},
                "attributionModel": model, "attributionWindow": window, "timezone": TIMEZONE, "valueSource": "orders"}
        if filters:
            body["filters"] = filters
        res = self.post("/api/query-attribution", body).get("results") or []
        return ((res[0].get("data") or {}).get("primary") or []) if res else []


def _day(p: dict) -> dt.date:
    return dt.date.fromisoformat(str(p["dt"])[:10])


def _chunks(span: Span, size: int = CHUNK_DAYS):
    d = span.since
    while d <= span.until:
        e = min(d + dt.timedelta(days=size - 1), span.until)
        yield d, e
        d = e + dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------------------------------- pulls

def pull_orders(ctx: Ctx, span: Span) -> list[tuple]:
    up = Upstack(ctx)
    rows = []
    for a, b in _chunks(span):
        for p in up.query(a, b, ORDER_MEASURES):
            rows.append((_day(p), round2(num(p.get("orders.new_customer_total_revenue"))), round2(num(p.get("orders.returning_customer_total_revenue"))),
                         num(p.get("orders.new_customers")), round2(num(p.get("orders.total_revenue"))), num(p.get("orders.count")),
                         round2(num(p.get("orders.tax"))), round2(num(p.get("orders.refund_amount")))))
    if not rows:
        raise FeedEmpty("upstack orders: no rows for the window")
    return sorted(rows)


def pull_platform(ctx: Ctx, span: Span) -> list[tuple]:
    up = Upstack(ctx)
    agg: dict[tuple, list[float]] = {}
    for a, b in _chunks(span):
        for r in up.channel_details(a, b):
            platform = PLATFORM_NAMES.get((r.get("basicDetails") or {}).get("resourceSource"), (r.get("basicDetails") or {}).get("resourceSource"))
            if not platform:
                continue
            for p in (r.get("data") or {}).get("primary") or []:
                vals = agg.setdefault((_day(p), platform), [0.0] * 7)
                for i, m in enumerate(CHANNEL_MEASURES):
                    vals[i] += num(p.get(m))
    if not agg:
        raise FeedEmpty("upstack platform: no rows for the window")
    return sorted((d, pf, round2(v[0]), v[1], round2(v[2]), v[3], round2(v[4]), v[5], v[6]) for (d, pf), v in agg.items())


def pull_attribution(ctx: Ctx, span: Span) -> list[tuple]:
    up = Upstack(ctx)
    agg: dict[tuple, list[float]] = {}
    for day in days_between(span.since, span.until):
        for p in up.attribution(day, "ad_source", "last_click", ATTRIBUTION_WINDOW):
            src = p.get("utm_source")
            if src not in AD_SOURCES:
                continue
            v = agg.setdefault((day, src), [0.0] * 4)
            v[0] += num(p.get("orders.new_customers")); v[1] += num(p.get("orders.new_customer_total_revenue"))
            v[2] += num(p.get("orders.count")); v[3] += num(p.get("orders.total_revenue"))
    if not agg:
        raise FeedEmpty("upstack attribution: no rows for the window")
    return sorted((d, s, v[0], round2(v[1]), v[2], round2(v[3])) for (d, s), v in agg.items())


def pull_traffic(ctx: Ctx, span: Span) -> list[tuple]:
    up = Upstack(ctx)
    rows = {}
    for a, b in _chunks(span):
        for p in up.query(a, b, TRAFFIC_MEASURES):
            rows[_day(p)] = tuple(int(num(p.get(m))) for m in TRAFFIC_MEASURES)
    if not rows:
        raise FeedEmpty("upstack traffic: no rows for the window")
    return [(d, *v) for d, v in sorted(rows.items())]


def classify_stage(name: str | None) -> str:
    return "Testing" if "test" in str(name or "").lower() else "Scaling"


def pull_anyclick(ctx: Ctx, span: Span) -> list[tuple]:
    up = Upstack(ctx)
    agg: dict[tuple, list[float]] = {}
    for day in days_between(span.since, span.until):
        for x in up.attribution(day, "utm_campaign", "any_click", ANYCLICK_WINDOW, ANYCLICK_FILTER):
            nc, ncr = num(x.get("orders.new_customers")), num(x.get("orders.new_customer_total_revenue"))
            if not (nc or ncr or num(x.get("orders.count")) or num(x.get("orders.total_revenue"))):
                continue                                   # the sheet job drops all-zero campaign rows
            v = agg.setdefault((day, classify_stage(x.get("utm_campaign"))), [0.0, 0.0])
            v[0] += nc; v[1] += ncr
    if not agg:
        raise FeedEmpty("upstack any-click: no rows for the window")
    return sorted((d, s, v[0], round2(v[1])) for (d, s), v in agg.items())


def traffic_span(ctx: Ctx, days: int) -> Span:
    return trailing_span(ctx.today, days, include_today=True)       # the sheet's Traffic - Daily includes today (partial)


# ---------------------------------------------------------------------------------------------------------------- feeds

_MONEY = dict(abs_tol=1.0, rel_tol=0.001)
_COUNT = dict(abs_tol=0.01)

FEEDS = [
    Feed("orders", "Shopify orders: new-customer and returning revenue (Upstack)", "upstack", "proj_feed_orders", "proj_feedx_orders",
         ("day", "nc_rev", "rc_rev", "nc_orders", "total_rev", "total_orders", "tax", "refunds"), ("day",), "day", pull=pull_orders, days=14,
         metrics=(Metric("nc_rev", **_MONEY), Metric("rc_rev", **_MONEY), Metric("nc_orders", **_COUNT), Metric("total_rev", **_MONEY),
                  Metric("total_orders", **_COUNT), Metric("tax", **_MONEY), Metric("refunds", **_MONEY)),
         sheet_tab="Orders - History", sheet_parser=("etl.projection_pacing", "parse_orders")),
    Feed("platform", "Google and TikTok spend (Upstack)", "upstack", "proj_feed_platform", "proj_feedx_platform",
         ("day", "platform", "spend", "purchases", "purchase_value", "nc_purchases", "nc_purchase_value", "link_clicks", "clicks"), ("day", "platform"),
         "day", pull=pull_platform, days=14, settle_days=14,       # Google Ads restates conversion value for days after the day (Oct 30 moved +7.8% five days later)
         metrics=(Metric("spend", 1.0, 0.005), Metric("purchases", 1.0, 0.005), Metric("purchase_value", 1.0, 0.005), Metric("nc_purchases", 1.0, 0.005),
                  Metric("nc_purchase_value", 1.0, 0.005), Metric("link_clicks", 2.0, 0.005), Metric("clicks", 2.0, 0.005)),
         sheet_tab="Platform - History", sheet_parser=("etl.projection_pacing", "parse_platform")),
    Feed("attribution", "Last-click attribution by ad source (Upstack)", "upstack", "proj_feed_attribution", "proj_feedx_attribution",
         ("day", "ad_source", "nc_orders", "nc_rev", "orders", "rev"), ("day", "ad_source"), "day", pull=pull_attribution, days=14,
         metrics=(Metric("nc_orders", **_COUNT), Metric("nc_rev", **_MONEY), Metric("orders", **_COUNT), Metric("rev", **_MONEY)),
         sheet_tab="Attribution - History", sheet_parser=("etl.projection_pacing", "parse_attribution")),
    Feed("traffic", "Site traffic: new visitors (Upstack)", "upstack", "proj_feed_traffic", "proj_feedx_traffic",
         ("day", "new_visitors", "visitors", "sessions"), ("day",), "day", pull=pull_traffic, days=14, span=traffic_span,
         metrics=(Metric("new_visitors", 2.0, 0.005), Metric("visitors", 2.0, 0.005), Metric("sessions", 2.0, 0.005)),
         sheet_tab="Traffic - Daily", sheet_parser=("etl.projection_pacing", "parse_traffic")),
    Feed("anyclick", "New customers by campaign stage, any-click (Upstack)", "upstack", "proj_feed_anyclick", "proj_feedx_anyclick",
         ("day", "stage", "nc", "nc_rev"), ("day", "stage"), "day", pull=pull_anyclick, days=7,
         metrics=(Metric("nc", **_COUNT), Metric("nc_rev", **_MONEY)),
         sheet_tab="Upstack Any-Click - By Campaign", sheet_parser=("etl.projection_pacing", "parse_anyclick")),
]
