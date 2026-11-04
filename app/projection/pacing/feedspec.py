"""The pacing feeds as data: sheet tab, target table, columns, and how fresh each one is. Shared by the ETL (etl/projection_pacing.py) and the API."""
from __future__ import annotations

from dataclasses import dataclass

STALE_AFTER_DAYS = 2


@dataclass(frozen=True)
class FeedSpec:
    key: str
    label: str
    tab: str                       # sheet tab read read-only through the service account
    table: str
    cols: tuple[str, ...]
    day_col: str = "day"
    period_end_col: str | None = None   # grain feeds: window by the period end
    where: str = ""                # extra filter when reading freshness (grain feeds: the day grain)

    @property
    def window_col(self) -> str:
        return self.period_end_col or self.day_col


FEED_SPECS: tuple[FeedSpec, ...] = (
    FeedSpec("orders", "Shopify orders: new-customer and returning revenue (Upstack)", "Orders - History", "proj_feed_orders",
             ("day", "nc_rev", "rc_rev", "nc_orders", "total_rev", "total_orders", "tax", "refunds")),
    FeedSpec("amazon", "Amazon sales and ads (ExpandFi)", "Amazon - Daily", "proj_feed_amazon",
             ("day", "orders", "units", "revenue", "nc_orders", "nc_rev", "ad_spend", "attributed_sales", "clicks", "impressions")),
    FeedSpec("platform", "Google and TikTok spend (Upstack)", "Platform - History", "proj_feed_platform",
             ("day", "platform", "spend", "purchases", "purchase_value", "nc_purchases", "nc_purchase_value", "link_clicks", "clicks")),
    FeedSpec("attribution", "Last-click attribution by ad source (Upstack)", "Attribution - History", "proj_feed_attribution",
             ("day", "ad_source", "nc_orders", "nc_rev", "orders", "rev")),
    FeedSpec("traffic", "Site traffic: new visitors (Upstack)", "Traffic - Daily", "proj_feed_traffic", ("day", "new_visitors", "visitors", "sessions")),
    FeedSpec("meta_reach", "Meta reach and unique outbound clicks", "Meta Account - Reach", "proj_feed_meta_reach",
             ("grain", "period_key", "period_start", "period_end", "impressions", "reach", "unique_link_clicks", "link_clicks", "unique_outbound_clicks"),
             day_col="period_start", period_end_col="period_end", where="grain = 'day'"),
    FeedSpec("product_reach", "Meta unique outbound clicks by product", "Meta Product - Reach", "proj_feed_product_reach",
             ("grain", "period_key", "period_start", "period_end", "product", "unique_outbound_clicks"),
             day_col="period_start", period_end_col="period_end", where="grain = 'day'"),
    FeedSpec("nc_product", "New-customer revenue by product (ExpandFi)", "NC Rev - By Product", "proj_feed_nc_product", ("day", "product", "nc_rev", "nc_orders")),
    FeedSpec("anyclick", "New customers by campaign stage, any-click (Upstack)", "Upstack Any-Click - By Campaign", "proj_feed_anyclick", ("day", "stage", "nc", "nc_rev")),
    FeedSpec("bids", "Meta bid caps and ROAS floors", "Meta Bids - Daily", "proj_feed_bids", ("day", "spend", "capped_spend", "spend_x_cap", "floor_spend", "spend_x_floor")),
)
SPEC_BY_KEY = {s.key: s for s in FEED_SPECS}


def freshness(conn) -> list[dict]:
    """One entry per feed with the last day held in its table (read from the table), plus Meta's ad_daily itself."""
    out = []
    for s in FEED_SPECS:
        col = s.window_col
        r = conn.execute(f"SELECT max({col}) FROM {s.table}" + (f" WHERE {s.where}" if s.where else "")).fetchone()
        last = r[0] if not isinstance(r, dict) else next(iter(r.values()))
        out.append({"feed": s.key, "label": s.label, "cadence": "daily", "data_through": last, "stale_after_days": STALE_AFTER_DAYS})
    r = conn.execute("SELECT max(day) FROM ad_daily").fetchone()
    last = r[0] if not isinstance(r, dict) else next(iter(r.values()))
    out.append({"feed": "meta_ad_daily", "label": "Meta spend, clicks and purchases (ad_daily)", "cadence": "daily", "data_through": last,
                "stale_after_days": STALE_AFTER_DAYS})
    r = conn.execute("SELECT max(updated_at)::date AS d FROM proj_pacing_hourly").fetchone()
    last = r["d"] if isinstance(r, dict) else (r[0] if r else None)
    out.append({"feed": "hourly_curve", "label": "Meta hourly spend curve (typed in the sheet)", "cadence": "monthly-upload", "data_through": last, "stale_after_days": 45})
    return out
