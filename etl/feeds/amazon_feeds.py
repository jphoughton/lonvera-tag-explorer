"""Amazon data layer (ExpandFi merchant 1452, read-only): ads by campaign and by ASIN, organic search (Brand Analytics Search Query Performance), orders by ASIN,
account traffic, market baskets, repeat purchases and settlement fees. Same Warehouse / credential / Feed machinery as etl.feeds.expandfi_feeds (one login per
run, shared through ctx.cache); every feed here is NATIVE (no sheet twin, default mode shadow, `main` is the table it writes) and NON-FATAL (a failed or empty
pull is logged in proj_feed_attempts and never turns the nightly run red or stops another feed). Docs: docs/AMAZON_DATA.md.

Rules (each one proven against the live warehouse, see the docs):
  ads campaign   amazon_ads_campaign_data de-duplicated v3 > v2 > v1 per campaign-day ACROSS report types, exactly as expandfi_feeds.ADS_SQL, so the daily spend
                 sum equals proj_feed_amazon.ad_spend. ad_type comes from api_report_type (sp / sb / sbv / sd). The 1/7/14/30-day windows are SP v3 only (older
                 generations hold zeros there, which would read as "no sales"); SB / SD carry their single reported sales and purchases. sales_reported = SP v3
                 sales_14d, everything else the bare `sales`.
  ads asin       amazon_ads_raw_data v3 spAdvertisedProduct / sdAdvertisedProduct only (the v1 copies are one report stored twice under two names).
  sqp            brand_analytics_search_query_performances, one row per (period, marketplace, ASIN, normalised query): duplicates (restated re-pulls) keep the
                 latest updated_at. Market totals are repeated on every ASIN row of a query: never SUM them across ASINs.
  orders         sales JOIN customers (inner, as expandfi_feeds.REVENUE_SQL), purchase_date basis, new customer = purchase_date equals customers.cohort_day.
  traffic        sales_and_traffic_reports, US, PARENT rows (each row carries the account total), latest snapshot per day.
  settlements    amazon_settlement_reports summed by posted day and fee / transaction type, signed as stored.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable, Iterator

from psycopg import sql

from . import expandfi_feeds as E
from .common import Ctx, FeedEmpty, FeedError, Span, num, round2
from .feed import Feed

US_MARKETPLACE_ID = "ATVPDKIKX0DER"
MARKETPLACES = {"ATVPDKIKX0DER": "US", "A1AM78C64UM0Y8": "MX", "A2EUQ1WTGCTBG2": "CA", "A2Q3Y263D00KWC": "BR"}
SALES_CHANNELS = E.SALES_CHANNELS
AD_TYPES = {"spCampaigns": "sp", "sbCampaigns": "sb", "sbvCampaigns": "sbv", "sdCampaigns": "sd",
            "spAdvertisedProduct": "sp", "sdAdvertisedProduct": "sd"}
MAP_KEY = "amazon.asin_map"

# The ONE place that decides whether a search query is a brand query (Lonvera, Vital / Vita Bull, Vital / Vita Belle, Vital Flow, Bone Flex / Boneflex).
# It runs on the normalised query_key (lower case, single spaces).
BRANDED_RE = re.compile(r"lonvera|vit(?:a|al)[ -]?bull|vit(?:a|al)[ -]?belle|vital[ -]?flow|bone[ -]?flex")
ASIN_IN_NAME = re.compile(r"\bB0[A-Z0-9]{8}\b")
PRODUCTS = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vita Belle", "vital-flow": "Vital Flow"}


def is_branded(query_key: str) -> bool:
    return bool(BRANDED_RE.search(query_key or ""))


def norm_query(q: str | None) -> str:
    """The same normalisation the SQL applies: lower case, trimmed, runs of whitespace collapsed to one space."""
    return re.sub(r"\s+", " ", (q or "").strip()).lower()


def product_slug_of(sku: str | None, name: str | None = None) -> str | None:
    """SKU prefix first, listing title as the fallback (the same order as expandfi_feeds.PRODUCT_CASE_SQL, with the app's product slugs)."""
    s, n = (sku or "").upper(), (name or "").lower()
    if s.startswith("LV-VBULL"):
        return "vita-bull"
    if s.startswith("LV-BLC"):
        return "beef-liver"
    if s.startswith("LV-BFLEX"):
        return "boneflex"
    if s.startswith("LV-VBELLE"):
        return "vita-belle"
    if "GN179001" in s or s.startswith("LV-V-FLOW"):
        return "vital-flow"
    for key, slug in (("bull", "vita-bull"), ("boneflex", "boneflex"), ("flow", "vital-flow"), ("belle", "vita-belle"), ("liver", "beef-liver")):
        if key in n:
            return slug
    return None


def chunks(span: Span, days: int) -> Iterator[Span]:
    """The span cut into windows of at most `days` days, oldest first (a backfill must fit the warehouse's 150 s statement timeout)."""
    d = span.since
    while d <= span.until:
        end = min(span.until, d + dt.timedelta(days=days - 1))
        yield Span(d, end)
        d = end + dt.timedelta(days=1)


def _n(x):
    return None if x is None else x


# ---------------------------------------------------------------------------------------------------------------- ASIN -> SKU -> product

ASIN_SQL = """
    SELECT asin, sku, friendly_name
    FROM asin_skus
    WHERE channel = 'Amazon.com' AND asin IS NOT NULL AND asin <> '' AND sku IS NOT NULL AND stale IS NOT TRUE
    ORDER BY asin, sku
"""


def asin_map(ctx: Ctx) -> dict[str, tuple[str | None, str | None]]:
    """{asin: (slug, sku)} from asin_skus (Amazon.com), read once per run."""
    m = ctx.cache.get(MAP_KEY)
    if m is None:
        rows = E.get_warehouse(ctx).query(ASIN_SQL)
        if not rows:
            raise FeedEmpty("amazon: asin_skus returned no Amazon.com ASIN; nothing written")
        m = {}
        for asin, sku, name in rows:
            m.setdefault(asin, (product_slug_of(sku, name), sku))
        ctx.cache[MAP_KEY] = m
    return m


def _prod(ctx: Ctx, asin: str | None) -> tuple[str | None, str | None]:
    slug = asin_map(ctx).get(asin or "", (None, None))[0]
    return slug, PRODUCTS.get(slug) if slug else None


def product_of_campaign(ctx: Ctx, name: str | None) -> tuple[str | None, str | None]:
    """Product from the ASIN a campaign name starts with ('B0CWG9ZRMB - Beef Liver - SB ...', 'SP - B0G161TR5T ...'); NULL when the name carries no known ASIN."""
    m = ASIN_IN_NAME.search(name or "")
    return _prod(ctx, m.group(0)) if m else (None, None)


ASIN_PRODUCTS_COLS = ("asin", "sku", "product_slug", "product_name", "friendly_name", "refreshed_on")


def pull_asin_products(ctx: Ctx, span: Span) -> list[tuple]:
    rows = E.get_warehouse(ctx).query(ASIN_SQL)
    if not rows:
        raise FeedEmpty("amazon_asin_products: asin_skus returned no Amazon.com ASIN; nothing written")
    out = []
    for asin, sku, name in rows:
        slug = product_slug_of(sku, name)
        out.append((asin, sku, slug, PRODUCTS.get(slug) if slug else None, (name or "")[:200] or None, ctx.today))
    return out


# ---------------------------------------------------------------------------------------------------------------- ads by campaign (v3 > v2 > v1)

# `{window}` columns are NULL unless the best generation is SP v3: older generations store zeros in the 1/7/14/30-day columns, which would read as "no sales".
ADS_CAMPAIGN_SQL = """
    WITH clean AS (
        SELECT *
        FROM amazon_ads_campaign_data
        WHERE date IS NOT NULL
          AND campaign_id IS NOT NULL
          AND api_report_type IN ('spCampaigns','sbCampaigns','sdCampaigns','sbvCampaigns')
          AND NOT (api_version = 'v1' AND api_report_type IN ('sbCampaigns','sdCampaigns'))
          AND date BETWEEN %(since)s AND %(until)s
    ),
    best AS (
        SELECT campaign_id, date,
               MIN(CASE api_version WHEN 'v3' THEN 1 WHEN 'v2' THEN 2 WHEN 'v1' THEN 3 ELSE 9 END) AS best_rank
        FROM clean
        GROUP BY campaign_id, date
    ),
    kept AS (
        SELECT c.*
        FROM clean c
        JOIN best b ON b.campaign_id = c.campaign_id AND b.date = c.date
         AND CASE c.api_version WHEN 'v3' THEN 1 WHEN 'v2' THEN 2 WHEN 'v1' THEN 3 ELSE 9 END = b.best_rank
    ),
    g AS (
        SELECT date AS day,
               COALESCE(marketplace, 'US') AS marketplace,
               campaign_id,
               COALESCE(ad_group_id, '') AS ad_group_id,
               api_report_type,
               MAX(api_version) AS api_version,
               MAX(campaign_name) AS campaign_name,
               MAX(ad_group_name) AS ad_group_name,
               MAX(campaign_status) AS campaign_status,
               MAX(campaign_budget_amount) AS budget_amount,
               MAX(campaign_budget_type) AS budget_type,
               MAX(campaign_bidding_strategy) AS bidding_strategy,
               SUM(cost_usd) AS spend,
               SUM(impressions) AS impressions,
               SUM(clicks) AS clicks,
               SUM(sales_1d) AS sales_1d, SUM(sales_7d) AS sales_7d, SUM(sales_14d) AS sales_14d, SUM(sales_30d) AS sales_30d,
               SUM(purchases_1d) AS purchases_1d, SUM(purchases_7d) AS purchases_7d, SUM(purchases_14d) AS purchases_14d, SUM(purchases_30d) AS purchases_30d,
               SUM(units_sold_clicks_1d) AS units_1d, SUM(units_sold_clicks_7d) AS units_7d, SUM(units_sold_clicks_14d) AS units_14d, SUM(units_sold_clicks_30d) AS units_30d,
               SUM(sales) AS sales, SUM(purchases) AS purchases, SUM(units_sold) AS units_sold,
               SUM(new_to_brand_purchases) AS ntb_purchases, SUM(new_to_brand_sales) AS ntb_sales, SUM(new_to_brand_units_sold) AS ntb_units,
               SUM(video_complete_views) AS video_complete_views, SUM(video_5_second_views) AS video_5s_views,
               SUM(video_first_quartile_views) AS video_q1_views, SUM(video_midpoint_views) AS video_midpoint_views, SUM(video_third_quartile_views) AS video_q3_views,
               SUM(brand_searches) AS brand_searches
        FROM kept
        GROUP BY 1, 2, 3, 4, 5
    )
    SELECT day, marketplace, campaign_id, ad_group_id, api_report_type, api_version, campaign_name, ad_group_name, campaign_status, budget_amount, budget_type, bidding_strategy,
           spend, impressions, clicks,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN sales_1d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN sales_7d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN sales_14d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN sales_30d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN purchases_1d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN purchases_7d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN purchases_14d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN purchases_30d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN units_1d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN units_7d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN units_14d END,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN units_30d END,
           sales, purchases, units_sold, ntb_purchases, ntb_sales, ntb_units,
           video_complete_views, video_5s_views, video_q1_views, video_midpoint_views, video_q3_views, brand_searches,
           CASE WHEN api_report_type = 'spCampaigns' AND api_version = 'v3' THEN COALESCE(sales_14d, sales) ELSE COALESCE(sales, sales_14d) END AS sales_reported
    FROM g
    WHERE COALESCE(spend, 0) <> 0 OR COALESCE(impressions, 0) <> 0 OR COALESCE(clicks, 0) <> 0
       OR COALESCE(sales, 0) <> 0 OR COALESCE(sales_14d, 0) <> 0
    ORDER BY 1, 2, 3, 4, 5
"""

ADS_CAMPAIGN_COLS = (
    "day", "marketplace", "campaign_id", "ad_group_id", "ad_type", "api_version", "campaign_name", "ad_group_name", "campaign_status", "budget_amount", "budget_type",
    "bidding_strategy", "product_slug", "product_name", "spend", "impressions", "clicks",
    "sales_1d", "sales_7d", "sales_14d", "sales_30d", "purchases_1d", "purchases_7d", "purchases_14d", "purchases_30d", "units_1d", "units_7d", "units_14d", "units_30d",
    "sales", "purchases", "units_sold", "ntb_purchases", "ntb_sales", "ntb_units",
    "video_complete_views", "video_5s_views", "video_q1_views", "video_midpoint_views", "video_q3_views", "brand_searches", "sales_reported")


def ads_campaign_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for r in rows:
        day, mp, cid, agid, rtype, ver, cname, agname, status, budget, btype, bidding = r[:12]
        slug, pname = product_of_campaign(ctx, cname)
        out.append((day, mp, cid, agid, AD_TYPES[rtype], ver, cname, agname, status, _n(budget), btype, bidding, slug, pname, *[_n(x) for x in r[12:]]))
    return out


def pull_ads_campaign(ctx: Ctx, span: Span) -> list[tuple]:
    w, out = E.get_warehouse(ctx), []
    for sp in chunks(span, 60):
        out += ads_campaign_rows(ctx, w.query(ADS_CAMPAIGN_SQL, {"since": sp.since, "until": sp.until}))
    if not out:
        raise FeedEmpty("amazon_ads_campaign_daily: no ad rows for the window; nothing written (spend would read as zero)")
    return out


# ---------------------------------------------------------------------------------------------------------------- ads by ASIN (v3 only)

ADS_ASIN_SQL = """
    SELECT date AS day, COALESCE(marketplace, 'US') AS marketplace, campaign_id, COALESCE(ad_group_id, '') AS ad_group_id, asin, api_report_type,
           MAX(sku) AS sku, MAX(campaign_name) AS campaign_name, MAX(ad_group_name) AS ad_group_name,
           SUM(cost_usd) AS spend, SUM(impressions) AS impressions, SUM(clicks) AS clicks,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(sales_1d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(sales_7d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(sales_14d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(sales_30d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(purchases_1d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(purchases_7d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(purchases_14d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(purchases_30d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(units_sold_clicks_1d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(units_sold_clicks_7d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(units_sold_clicks_14d) END,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(units_sold_clicks_30d) END,
           CASE WHEN api_report_type = 'sdAdvertisedProduct' THEN SUM(sales_aggregate) END AS sales,
           CASE WHEN api_report_type = 'sdAdvertisedProduct' THEN SUM(purchases_aggregate) END AS purchases,
           CASE WHEN api_report_type = 'spAdvertisedProduct' THEN SUM(sales_14d) ELSE SUM(sales_aggregate) END AS sales_reported
    FROM amazon_ads_raw_data
    WHERE api_version = 'v3'
      AND api_report_type IN ('spAdvertisedProduct', 'sdAdvertisedProduct')
      AND date IS NOT NULL AND campaign_id IS NOT NULL AND asin IS NOT NULL
      AND date BETWEEN %(since)s AND %(until)s
    GROUP BY date, COALESCE(marketplace, 'US'), campaign_id, COALESCE(ad_group_id, ''), asin, api_report_type
    HAVING COALESCE(SUM(cost_usd), 0) <> 0 OR COALESCE(SUM(impressions), 0) <> 0 OR COALESCE(SUM(clicks), 0) <> 0
        OR COALESCE(SUM(sales_14d), 0) <> 0 OR COALESCE(SUM(sales_aggregate), 0) <> 0
    ORDER BY 1, 2, 3, 4, 5, 6
"""

ADS_ASIN_COLS = ("day", "marketplace", "campaign_id", "ad_group_id", "asin", "ad_type", "sku", "campaign_name", "ad_group_name", "product_slug", "product_name",
                 "spend", "impressions", "clicks", "sales_1d", "sales_7d", "sales_14d", "sales_30d", "purchases_1d", "purchases_7d", "purchases_14d", "purchases_30d",
                 "units_1d", "units_7d", "units_14d", "units_30d", "sales", "purchases", "sales_reported")


def ads_asin_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for r in rows:
        slug, pname = _prod(ctx, r[4])
        out.append((*r[:5], AD_TYPES[r[5]], *r[6:9], slug, pname, *[_n(x) for x in r[9:]]))
    return out


def pull_ads_asin(ctx: Ctx, span: Span) -> list[tuple]:
    w, out = E.get_warehouse(ctx), []
    for sp in chunks(span, 60):
        out += ads_asin_rows(ctx, w.query(ADS_ASIN_SQL, {"since": sp.since, "until": sp.until}))
    if not out:
        raise FeedEmpty("amazon_ads_asin_daily: no advertised-product rows for the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- Search Query Performance (weekly, monthly)

# One row per (period, marketplace, ASIN, normalised query); the latest restatement wins (duplicates carry different content hashes).
SQP_SQL = """
    SELECT DISTINCT ON (asin, query_key, start_date, marketplace_id)
           start_date, end_date, marketplace_id, asin, query_key, search_query,
           search_query_score, search_query_volume,
           total_query_impression_count, asin_impression_count, asin_impression_share,
           total_click_count, total_click_rate, asin_click_count, asin_click_share, total_median_click_price, asin_median_click_price,
           total_cart_add_count, total_cart_add_rate, asin_cart_add_count, asin_cart_add_share, total_median_cart_add_price, asin_median_cart_add_price,
           total_purchase_count, total_purchase_rate, asin_purchase_count, asin_purchase_share, total_median_purchase_price, asin_median_purchase_price
    FROM (
        SELECT *, lower(regexp_replace(btrim(search_query), '\\s+', ' ', 'g')) AS query_key
        FROM brand_analytics_search_query_performances
        WHERE period_type = %(period)s
          AND start_date BETWEEN %(since)s AND %(until)s
          AND asin IS NOT NULL AND search_query IS NOT NULL AND btrim(search_query) <> ''
    ) q
    ORDER BY asin, query_key, start_date, marketplace_id, updated_at DESC NULLS LAST, id DESC
"""

SQP_COLS = ("period_start", "period_end", "marketplace_id", "marketplace", "asin", "query_key", "search_query", "product_slug", "product_name", "is_branded",
            "search_query_score", "search_query_volume",
            "total_impressions", "asin_impressions", "asin_impression_share",
            "total_clicks", "total_click_rate", "asin_clicks", "asin_click_share", "total_median_click_price", "asin_median_click_price",
            "total_cart_adds", "total_cart_add_rate", "asin_cart_adds", "asin_cart_add_share", "total_median_cart_add_price", "asin_median_cart_add_price",
            "total_purchases", "total_purchase_rate", "asin_purchases", "asin_purchase_share", "total_median_purchase_price", "asin_median_purchase_price")


def sqp_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for r in rows:
        start, end, mp_id, asin, qkey, qtext = r[:6]
        slug, pname = _prod(ctx, asin)
        out.append((start, end, mp_id, MARKETPLACES.get(mp_id, mp_id), asin, qkey, qtext, slug, pname, is_branded(qkey), *[_n(x) for x in r[6:]]))
    return out


def _pull_sqp(period: str, label: str, chunk_days: int) -> Callable[[Ctx, Span], list[tuple]]:
    def pull(ctx: Ctx, span: Span) -> list[tuple]:
        w, out = E.get_warehouse(ctx), []
        for sp in chunks(span, chunk_days):
            out += sqp_rows(ctx, w.query(SQP_SQL, {"period": period, "since": sp.since, "until": sp.until}))
        if not out:
            raise FeedEmpty(f"{label}: no search query rows for the window; nothing written")
        return out
    return pull


pull_sqp_weekly = _pull_sqp("WEEK", "amazon_sqp_weekly", 182)
pull_sqp_monthly = _pull_sqp("MONTH", "amazon_sqp_monthly", 366)


# ---------------------------------------------------------------------------------------------------------------- orders by ASIN

ORDERS_ASIN_SQL = """
    SELECT s.purchase_date::date AS day,
           COALESCE(s.asin, '') AS asin,
           s.sales_channel,
           COUNT(DISTINCT s.amazon_order_id) AS orders,
           SUM(s.quantity_shipped) AS units,
           SUM(s.total_amount_usd) AS revenue,
           COUNT(DISTINCT s.amazon_order_id) FILTER (WHERE s.purchase_date::text = c.cohort_day) AS nc_orders,
           SUM(s.total_amount_usd) FILTER (WHERE s.purchase_date::text = c.cohort_day) AS nc_revenue
    FROM sales s
    JOIN customers c ON c.id = s.customer_id
    WHERE s.purchase_date BETWEEN %(since)s AND %(until)s
      AND s.sales_channel = ANY(%(channels)s)
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3
"""

ORDERS_ASIN_COLS = ("day", "asin", "sales_channel", "product_slug", "product_name", "orders", "units", "revenue", "nc_orders", "nc_revenue")


def orders_asin_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for day, asin, channel, orders, units, revenue, nc_orders, nc_revenue in rows:
        slug, pname = _prod(ctx, asin)
        out.append((day, asin, channel, slug, pname, int(num(orders)), int(num(units)), round2(revenue), int(num(nc_orders)), round2(nc_revenue)))
    return out


def pull_orders_asin(ctx: Ctx, span: Span) -> list[tuple]:
    w, out = E.get_warehouse(ctx), []
    for sp in chunks(span, 180):
        out += orders_asin_rows(ctx, w.query(ORDERS_ASIN_SQL, {"since": sp.since, "until": sp.until, "channels": SALES_CHANNELS}))
    if not out:
        raise FeedEmpty("amazon_orders_asin_daily: no Amazon orders for the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- account traffic

TRAFFIC_SQL = """
    SELECT DISTINCT ON (report_date)
           report_date, marketplace_id, sessions, browser_sessions, mobile_app_sessions, page_views, buy_box_percentage, unit_session_percentage,
           units_ordered, ordered_product_sales, total_order_items, sessions_b2b, page_views_b2b, buy_box_percentage_b2b, updated_at
    FROM sales_and_traffic_reports
    WHERE marketplace_id = %(marketplace)s AND date_granularity = 'DAY' AND asin_granularity = 'PARENT'
      AND report_date BETWEEN %(since)s AND %(until)s
    ORDER BY report_date, updated_at DESC NULLS LAST, id DESC
"""

TRAFFIC_COLS = ("day", "marketplace_id", "sessions", "browser_sessions", "mobile_app_sessions", "page_views", "buy_box_pct", "unit_conversion_pct", "units_ordered",
                "ordered_product_sales", "total_order_items", "sessions_b2b", "page_views_b2b", "buy_box_pct_b2b", "source_updated_at")


def pull_traffic(ctx: Ctx, span: Span) -> list[tuple]:
    w, out = E.get_warehouse(ctx), []
    for sp in chunks(span, 400):
        out += [tuple(r) for r in w.query(TRAFFIC_SQL, {"marketplace": US_MARKETPLACE_ID, "since": sp.since, "until": sp.until})]
    if not out:
        raise FeedEmpty("amazon_traffic_daily: no traffic rows for the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- market baskets, repeat purchases (weekly)

BASKETS_SQL = """
    SELECT DISTINCT ON (start_date, marketplace_id, asin, purchased_with_asin)
           start_date, end_date, marketplace_id, asin, purchased_with_asin, purchased_with_rank, combination_pct
    FROM brand_analytics_market_baskets
    WHERE period_type = 'WEEK' AND start_date BETWEEN %(since)s AND %(until)s AND asin IS NOT NULL AND purchased_with_asin IS NOT NULL
    ORDER BY start_date, marketplace_id, asin, purchased_with_asin, updated_at DESC NULLS LAST, id DESC
"""
BASKETS_COLS = ("week_start", "week_end", "marketplace_id", "asin", "product_slug", "product_name", "purchased_with_asin", "purchased_with_slug", "purchased_with_rank", "combination_pct")


def baskets_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for start, end, mp, asin, other, rank, pct in rows:
        slug, pname = _prod(ctx, asin)
        out.append((start, end, mp, asin, slug, pname, other, _prod(ctx, other)[0], rank, pct))
    return out


def pull_baskets(ctx: Ctx, span: Span) -> list[tuple]:
    out = baskets_rows(ctx, E.get_warehouse(ctx).query(BASKETS_SQL, {"since": span.since, "until": span.until}))
    if not out:
        raise FeedEmpty("amazon_market_baskets: no weekly market basket rows for the window; nothing written")
    return out


REPEAT_SQL = """
    SELECT DISTINCT ON (start_date, marketplace_id, asin)
           start_date, end_date, marketplace_id, asin, orders, unique_customers, repeat_customers_pct_total, repeat_purchase_revenue, repeat_purchase_revenue_pct_total
    FROM brand_analytics_repeat_purchases
    WHERE period_type = 'WEEK' AND start_date BETWEEN %(since)s AND %(until)s AND asin IS NOT NULL
    ORDER BY start_date, marketplace_id, asin, updated_at DESC NULLS LAST, id DESC
"""
REPEAT_COLS = ("week_start", "week_end", "marketplace_id", "asin", "product_slug", "product_name", "orders", "unique_customers", "repeat_customers_pct_total",
               "repeat_purchase_revenue", "repeat_purchase_revenue_pct_total")


def repeat_rows(ctx: Ctx, rows: list[tuple]) -> list[tuple]:
    out = []
    for start, end, mp, asin, *rest in rows:
        slug, pname = _prod(ctx, asin)
        out.append((start, end, mp, asin, slug, pname, *rest))
    return out


def pull_repeat(ctx: Ctx, span: Span) -> list[tuple]:
    out = repeat_rows(ctx, E.get_warehouse(ctx).query(REPEAT_SQL, {"since": span.since, "until": span.until}))
    if not out:
        raise FeedEmpty("amazon_repeat_purchases: no weekly repeat purchase rows for the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- settlement fees

SETTLEMENT_SQL = """
    SELECT posted_date AS day,
           COALESCE(marketplace_name, '') AS marketplace_name,
           COALESCE(transaction_type, '') AS transaction_type,
           COALESCE(amount_type, '') AS amount_type,
           COALESCE(amount_description, '') AS amount_description,
           COUNT(*) AS lines,
           SUM(amount) AS amount,
           SUM(amount_usd) AS amount_usd,
           MAX(currency) AS currency
    FROM amazon_settlement_reports
    WHERE posted_date BETWEEN %(since)s AND %(until)s
    GROUP BY 1, 2, 3, 4, 5
    ORDER BY 1, 2, 3, 4, 5
"""
SETTLEMENT_COLS = ("day", "marketplace_name", "transaction_type", "amount_type", "amount_description", "lines", "amount", "amount_usd", "currency")


def pull_settlements(ctx: Ctx, span: Span) -> list[tuple]:
    w, out = E.get_warehouse(ctx), []
    for sp in chunks(span, 120):
        out += [tuple(r) for r in w.query(SETTLEMENT_SQL, {"since": sp.since, "until": sp.until})]
    if not out:
        raise FeedEmpty("amazon_settlement_fees_daily: no settlement lines for the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- feeds

def _all(span: Span):
    """delete_where of a full-refresh table: every row goes, the pulled rows replace them."""
    return sql.SQL("TRUE"), ()


def _feed(key: str, label: str, cols: tuple, keys: tuple, day_col: str, pull, days: int, stale_after: int, history_start: dt.date, notes: str, **kw) -> Feed:
    return Feed(key, label, "expandfi", key, key, cols, keys, day_col, pull=pull, days=days, stale_after=stale_after, native=True, non_fatal=True,
                history_start=history_start, notes=notes, **kw)


FEEDS = [
    _feed("amazon_asin_products", "Amazon ASIN to product map (ExpandFi)", ASIN_PRODUCTS_COLS, ("asin", "sku"), "refreshed_on", pull_asin_products, 1, 3, dt.date(2026, 1, 1),
          "Full refresh of the Amazon.com ASIN, SKU and product list on every run.", delete_where=_all),
    _feed("amazon_ads_campaign_daily", "Amazon ads by campaign (ExpandFi)", ADS_CAMPAIGN_COLS, ("day", "marketplace", "campaign_id", "ad_group_id", "ad_type"), "day",
          pull_ads_campaign, 21, 3, dt.date(2025, 12, 30), "Campaign x ad group x day, v3 > v2 > v1 de-duplicated; daily spend equals proj_feed_amazon.ad_spend."),
    _feed("amazon_ads_asin_daily", "Amazon ads by ASIN (ExpandFi)", ADS_ASIN_COLS, ("day", "marketplace", "campaign_id", "ad_group_id", "asin", "ad_type"), "day",
          pull_ads_asin, 21, 3, dt.date(2026, 1, 4), "Advertised product (SP, SD) x campaign x ad group x ASIN x day, v3 only."),
    _feed("amazon_sqp_weekly", "Amazon search queries, weekly (ExpandFi)", SQP_COLS, ("period_start", "marketplace_id", "asin", "query_key"), "period_start",
          pull_sqp_weekly, 42, 16, dt.date(2025, 7, 13), "Brand Analytics Search Query Performance, weekly; a week lands about 5 days after its Saturday."),
    _feed("amazon_sqp_monthly", "Amazon search queries, monthly (ExpandFi)", SQP_COLS, ("period_start", "marketplace_id", "asin", "query_key"), "period_start",
          pull_sqp_monthly, 105, 50, dt.date(2025, 6, 1), "Brand Analytics Search Query Performance, monthly; a month lands about 2 weeks after it ends."),
    _feed("amazon_orders_asin_daily", "Amazon orders by ASIN (ExpandFi)", ORDERS_ASIN_COLS, ("day", "asin", "sales_channel"), "day", pull_orders_asin, 21, 3,
          dt.date(2024, 9, 24), "Same orders, date basis and new-customer rule as proj_feed_amazon, split by ASIN and channel."),
    _feed("amazon_traffic_daily", "Amazon account traffic (ExpandFi)", TRAFFIC_COLS, ("day", "marketplace_id"), "day", pull_traffic, 14, 3, dt.date(2024, 4, 7),
          "Account-level sessions, page views and buy box share, US."),
    _feed("amazon_market_baskets", "Amazon market baskets, weekly (ExpandFi)", BASKETS_COLS, ("week_start", "marketplace_id", "asin", "purchased_with_asin"), "week_start",
          pull_baskets, 42, 16, dt.date(2026, 6, 28), "Brand Analytics market basket analysis, weekly."),
    _feed("amazon_repeat_purchases", "Amazon repeat purchases, weekly (ExpandFi)", REPEAT_COLS, ("week_start", "marketplace_id", "asin"), "week_start", pull_repeat, 42, 16,
          dt.date(2026, 6, 28), "Brand Analytics repeat purchase behaviour, weekly."),
    _feed("amazon_settlement_fees_daily", "Amazon settlement fees (ExpandFi)", SETTLEMENT_COLS,
          ("day", "marketplace_name", "transaction_type", "amount_type", "amount_description"), "day", pull_settlements, 45, 21, dt.date(2024, 3, 18),
          "Settlement lines summed by posted day and fee type, signed as stored; settlements arrive in batches, so the newest day can lag by two weeks."),
]
