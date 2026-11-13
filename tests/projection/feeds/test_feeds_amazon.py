"""Amazon data layer feeds (etl/feeds/amazon_feeds.py). The real SQL runs against a throwaway schema (`exwa`) that mirrors the ExpandFi warehouse tables each feed reads;
no network, no credential. Covers the SQL shaping, the v3 > v2 > v1 dedupe, the SQP restatement dedupe, idempotency, empty pulls, the non-fatal run and the two
reconciliations with the existing amazon feed (daily ad spend; daily units, revenue and new-customer revenue)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import urllib.parse

import psycopg
import pytest

from etl import feeds
from etl.feeds import amazon_feeds as A
from etl.feeds import expandfi_feeds as E
from etl.feeds.common import FeedEmpty, FeedError, Span, make_ctx

D = dt.date
SPAN = Span(D(2026, 11, 1), D(2026, 11, 7))
AMZ, B2B = "Amazon.com", "Amazon.com B2B"
BL, VB = "B0CWG9ZRMB", "B0F3K9C46G"

DDL = """
DROP SCHEMA IF EXISTS exwa CASCADE;
CREATE SCHEMA exwa;
CREATE TABLE exwa.asin_skus (id serial, sku varchar, asin varchar, friendly_name varchar, channel varchar, stale boolean DEFAULT false);
CREATE TABLE exwa.customers (id bigint PRIMARY KEY, cohort_day varchar);
CREATE TABLE exwa.sales (id bigint PRIMARY KEY, amazon_order_id varchar, purchase_date date, quantity_shipped integer, total_amount_usd numeric, sales_channel varchar,
                         customer_id integer, asin varchar, sku varchar);
CREATE TABLE exwa.amazon_ads_campaign_data (id bigserial PRIMARY KEY, campaign_id varchar, campaign_name varchar, ad_group_id varchar, ad_group_name varchar, date date,
  marketplace varchar, api_report_type varchar, api_version varchar, cost_usd numeric, impressions integer, clicks integer, campaign_status varchar,
  campaign_budget_amount numeric, campaign_budget_type varchar, campaign_bidding_strategy varchar,
  sales_1d numeric, sales_7d numeric, sales_14d numeric, sales_30d numeric, purchases_1d integer, purchases_7d integer, purchases_14d integer, purchases_30d integer,
  units_sold_clicks_1d integer, units_sold_clicks_7d integer, units_sold_clicks_14d integer, units_sold_clicks_30d integer,
  sales numeric, purchases integer, units_sold integer, new_to_brand_purchases integer, new_to_brand_sales numeric, new_to_brand_units_sold integer,
  video_complete_views integer, video_5_second_views integer, video_first_quartile_views integer, video_midpoint_views integer, video_third_quartile_views integer, brand_searches integer);
CREATE TABLE exwa.amazon_ads_raw_data (id bigserial PRIMARY KEY, campaign_id varchar, campaign_name varchar, ad_group_id varchar, ad_group_name varchar, date date,
  marketplace varchar, api_report_type varchar, api_version varchar, asin varchar, sku varchar, cost_usd numeric, impressions integer, clicks integer,
  sales_1d numeric, sales_7d numeric, sales_14d numeric, sales_30d numeric, purchases_1d integer, purchases_7d integer, purchases_14d integer, purchases_30d integer,
  units_sold_clicks_1d integer, units_sold_clicks_7d integer, units_sold_clicks_14d integer, units_sold_clicks_30d integer, sales_aggregate numeric, purchases_aggregate integer);
CREATE TABLE exwa.brand_analytics_search_query_performances (id bigserial PRIMARY KEY, asin varchar, start_date date, end_date date, period_type varchar, marketplace_id varchar,
  search_query text, search_query_score integer, search_query_volume bigint, total_query_impression_count bigint, asin_impression_count bigint, asin_impression_share numeric,
  total_click_count bigint, total_click_rate numeric, asin_click_count bigint, asin_click_share numeric, total_median_click_price numeric, asin_median_click_price numeric,
  total_cart_add_count bigint, total_cart_add_rate numeric, asin_cart_add_count bigint, asin_cart_add_share numeric, total_median_cart_add_price numeric, asin_median_cart_add_price numeric,
  total_purchase_count bigint, total_purchase_rate numeric, asin_purchase_count bigint, asin_purchase_share numeric, total_median_purchase_price numeric, asin_median_purchase_price numeric,
  updated_at timestamp);
CREATE TABLE exwa.sales_and_traffic_reports (id bigserial PRIMARY KEY, marketplace_id varchar, date_granularity varchar, asin_granularity varchar, report_date date,
  sessions integer, browser_sessions integer, mobile_app_sessions integer, page_views integer, buy_box_percentage numeric, unit_session_percentage numeric, units_ordered integer,
  ordered_product_sales numeric, total_order_items integer, sessions_b2b integer, page_views_b2b integer, buy_box_percentage_b2b numeric, updated_at timestamp);
CREATE TABLE exwa.brand_analytics_market_baskets (id bigserial PRIMARY KEY, asin varchar, purchased_with_asin varchar, purchased_with_rank integer, combination_pct numeric,
  start_date date, end_date date, period_type varchar, marketplace_id varchar, updated_at timestamp);
CREATE TABLE exwa.brand_analytics_repeat_purchases (id bigserial PRIMARY KEY, asin varchar, start_date date, end_date date, period_type varchar, marketplace_id varchar,
  orders integer, unique_customers integer, repeat_customers_pct_total numeric, repeat_purchase_revenue numeric, repeat_purchase_revenue_pct_total numeric, updated_at timestamp);
CREATE TABLE exwa.amazon_settlement_reports (id bigserial PRIMARY KEY, posted_date date, marketplace_name varchar, transaction_type varchar, amount_type varchar,
  amount_description varchar, amount numeric, amount_usd numeric, currency varchar);
"""


@pytest.fixture()
def wh(feeds_db):
    with psycopg.connect(feeds_db, autocommit=True) as c:
        c.execute(DDL)
    dsn = feeds_db + ("&" if "?" in feeds_db else "?") + "options=" + urllib.parse.quote("-c search_path=exwa")
    yield E.Warehouse(dsn), feeds_db
    with psycopg.connect(feeds_db, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS exwa CASCADE")
        for f in A.FEEDS:
            c.execute(f"TRUNCATE {f.main}")


def put(url, table, rows):
    """rows: list of dicts (column -> value)."""
    with psycopg.connect(url, autocommit=True) as c:
        for r in rows:
            c.execute(f"INSERT INTO exwa.{table} ({', '.join(r)}) VALUES ({', '.join(['%s'] * len(r))})", list(r.values()))


def ctx_for(w):
    return make_ctx(today=D(2026, 11, 8), cache={E.CACHE_KEY: w})


def feed(key):
    return next(f for f in A.FEEDS if f.key == key)


def seed_asins(url):
    put(url, "asin_skus", [
        dict(sku="LV-BLC180", asin=BL, friendly_name="Lonvera Beef Liver", channel=AMZ),
        dict(sku="LV-VBULL", asin=VB, friendly_name="Lonvera Vital Bull", channel=AMZ),
        dict(sku="LV-VBELLE", asin="B0F3KWZZKT", friendly_name="Lonvera Vital Belle Bovine Liver", channel=AMZ),
        dict(sku="LV-BFLEX", asin="B0GL4VVRVR", friendly_name="Lonvera Boneflex", channel=AMZ),
        dict(sku="LV-BLC180", asin=BL, friendly_name="Lonvera Beef Liver", channel=B2B),       # other channel: not in the map
        dict(sku="SHOP-1", asin="B0SHOPIFY1", friendly_name="Shopify only", channel="Shopify"),
    ])


def ad(url, **kw):
    base = dict(marketplace="US", api_report_type="spCampaigns", api_version="v3", ad_group_id="", ad_group_name=None)
    put(url, "amazon_ads_campaign_data", [{**base, **kw}])


# ---------------------------------------------------------------------------------------------------------------- registration

def test_feeds_are_native_non_fatal_shadow_and_sort_after_the_other_expandfi_feeds(conn):
    reg = feeds.registry()
    keys = [f.key for f in A.FEEDS]
    assert len(keys) == 10 and set(keys) <= set(reg)
    for f in A.FEEDS:
        assert f.native and f.non_fatal and f.source == "expandfi" and f.default_mode == "shadow" and f.main == f.shadow == f.key and f.history_start
        assert set(f.key_cols) <= set(f.cols) and f.day_col in f.cols
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name <> 'loaded_at'", (f.key,)).fetchall()]
        assert set(cols) == set(f.cols), f.key                                   # the table and the feed agree on every column
        pk = {r[0] for r in conn.execute("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                                         "WHERE i.indrelid = %s::regclass AND i.indisprimary", (f.key,)).fetchall()}
        assert pk == set(f.key_cols), f.key
    order = list(reg)
    assert min(order.index(k) for k in keys) > order.index("nc_hourly") and min(order.index(k) for k in keys) > order.index("units_amazon")
    assert feeds.registry()["amazon"].default_mode == "sheet"                    # nothing else changed
    assert {e["key"] for e in feeds.freshness(conn)} >= set(keys)                # shadow-mode feeds are listed with their stale-after limit


def test_the_branded_rule_and_query_normalisation_live_in_one_place():
    for q in ("lonvera beef liver", "vital bull supplement", "vita bull", "vitabull", "vital belle", "vita-belle", "vital flow", "bone flex", "boneflex joint", "bone-flex"):
        assert A.is_branded(A.norm_query(q)), q
    for q in ("beef liver capsules", "bull testosterone", "joint supplement", "vital proteins", ""):
        assert not A.is_branded(A.norm_query(q)), q
    assert A.norm_query("  Beef   LIVER\tcapsules ") == "beef liver capsules"


def test_chunks_and_backfill_days():
    parts = list(A.chunks(Span(D(2026, 1, 1), D(2026, 1, 10)), 4))
    assert parts == [Span(D(2026, 1, 1), D(2026, 1, 4)), Span(D(2026, 1, 5), D(2026, 1, 8)), Span(D(2026, 1, 9), D(2026, 1, 10))]
    f = feed("amazon_sqp_weekly")
    assert feeds.backfill_days(f, D(2026, 11, 12)) == (D(2026, 11, 11) - D(2025, 7, 13)).days + 1
    assert feeds.backfill_days(f, D(2026, 11, 12), D(2026, 11, 1)) == f.days                    # a short --since never narrows the normal window
    assert feeds.backfill_days(feeds.registry()["orders"], D(2026, 11, 12)) == feeds.registry()["orders"].days


# ---------------------------------------------------------------------------------------------------------------- asin map

def test_asin_products_map_amazon_channel_only_with_app_product_slugs(wh):
    w, url = wh
    seed_asins(url)
    rows = A.pull_asin_products(ctx_for(w), SPAN)
    got = {r[0]: (r[2], r[3]) for r in rows}
    assert got == {BL: ("beef-liver", "Beef Liver"), VB: ("vita-bull", "Vita Bull"), "B0F3KWZZKT": ("vita-belle", "Vita Belle"), "B0GL4VVRVR": ("boneflex", "Boneflex")}
    assert all(r[5] == D(2026, 11, 8) and len(r) == len(feed("amazon_asin_products").cols) for r in rows)


# ---------------------------------------------------------------------------------------------------------------- ads by campaign

def seed_ads(url):
    seed_asins(url)
    d1, d2 = D(2026, 11, 2), D(2026, 11, 3)
    ad(url, campaign_id="X", campaign_name=f"{BL} - Beef Liver - SP - Exact", ad_group_id="g1", ad_group_name="AG1", date=d1, cost_usd=10, impressions=100, clicks=5,
       sales_1d=1, sales_7d=2, sales_14d=40, sales_30d=45, purchases_14d=4, units_sold_clicks_14d=5, sales=38, campaign_status="ENABLED", campaign_budget_amount=50, campaign_bidding_strategy="down")
    ad(url, campaign_id="X", campaign_name="x", ad_group_id="g2", date=d1, cost_usd=5, impressions=50, clicks=2, sales_14d=10, sales=9)         # second ad group, same v3 rank
    ad(url, campaign_id="X", campaign_name="x", api_version="v2", date=d1, cost_usd=99, impressions=999, clicks=99, sales_14d=99, sales=99)   # older generation: dropped
    ad(url, campaign_id="Y", campaign_name="SP - B0F3K9C46G - bull", api_version="v2", date=d1, cost_usd=4, impressions=40, clicks=1, sales_14d=8, sales=8)   # only v2: kept
    ad(url, campaign_id="Z", campaign_name="All Products - SB - Image", api_report_type="sbCampaigns", date=d1, cost_usd=20, impressions=1000, clicks=10, sales=60, sales_14d=None,
       new_to_brand_purchases=0, brand_searches=7, video_complete_views=3)
    ad(url, campaign_id="Z", campaign_name="All Products - SB - Image", api_report_type="sbCampaigns", api_version="v1", date=d1, cost_usd=77, sales=77)    # v1 sb is fenced out
    ad(url, campaign_id="V", campaign_name="Vid", api_report_type="sbvCampaigns", api_version="v2", date=d2, cost_usd=3, impressions=30, clicks=3, sales=6, sales_14d=6)
    ad(url, campaign_id="W", campaign_name="Full-Funnel campaign - 11/3", api_version="v1", marketplace=None, ad_group_id="gw", date=d2, cost_usd=6, impressions=60, clicks=6,
       sales=30, sales_14d=0)                                                                                                                          # v1: zeros in the windows
    ad(url, campaign_id="D", campaign_name=f"{BL} - SD - Retargeting", api_report_type="sdCampaigns", date=d2, cost_usd=2, impressions=20, clicks=2, sales=11,
       new_to_brand_purchases=1, new_to_brand_sales=11)
    ad(url, campaign_id="Q", campaign_name="Quiet", marketplace="BR", date=d2, cost_usd=0, impressions=0, clicks=0)                                     # empty rows are not stored
    ad(url, campaign_id="Q", campaign_name="Old", date=D(2026, 10, 1), cost_usd=500, impressions=1, clicks=1)                                           # outside the window


def test_ads_campaign_dedupes_generations_keeps_types_and_nulls_the_windows_outside_sp_v3(wh):
    w, url = wh
    seed_ads(url)
    ctx = ctx_for(w)
    rows = A.pull_ads_campaign(ctx, SPAN)
    cols = feed("amazon_ads_campaign_daily").cols
    by = {(r[cols.index("campaign_id")], r[cols.index("ad_group_id")]): dict(zip(cols, r)) for r in rows}
    assert set(by) == {("X", "g1"), ("X", "g2"), ("Y", ""), ("Z", ""), ("V", ""), ("W", "gw"), ("D", "")}      # v2 of X, v1 sb of Z, the empty BR row, October: gone
    x = by[("X", "g1")]
    assert x["ad_type"] == "sp" and x["api_version"] == "v3" and x["spend"] == 10 and x["sales_14d"] == 40 and x["sales_1d"] == 1 and x["sales_reported"] == 40
    assert x["product_slug"] == "beef-liver" and x["product_name"] == "Beef Liver" and x["campaign_status"] == "ENABLED" and x["budget_amount"] == 50
    assert by[("X", "g2")]["product_slug"] is None                                                                # the name has no ASIN
    assert by[("Y", "")]["product_slug"] == "vita-bull" and by[("Y", "")]["api_version"] == "v2" and by[("Y", "")]["sales_14d"] is None and by[("Y", "")]["sales_reported"] == 8
    z = by[("Z", "")]
    assert z["ad_type"] == "sb" and z["sales"] == 60 and z["sales_14d"] is None and z["sales_reported"] == 60 and z["brand_searches"] == 7 and z["video_complete_views"] == 3
    assert by[("V", "")]["ad_type"] == "sbv" and by[("D", "")]["ad_type"] == "sd" and by[("D", "")]["ntb_purchases"] == 1 and by[("D", "")]["ntb_sales"] == 11
    w_ = by[("W", "gw")]
    assert w_["marketplace"] == "US" and w_["api_version"] == "v1" and w_["sales_14d"] is None and w_["sales_reported"] == 30      # v1 zeros are not "no sales"


def test_ads_campaign_daily_sum_equals_the_existing_amazon_feeds_deduped_spend(wh):
    w, url = wh
    seed_ads(url)
    rows = A.pull_ads_campaign(ctx_for(w), SPAN)
    i = feed("amazon_ads_campaign_daily").cols.index("spend")
    mine = {}
    for r in rows:
        mine[r[0]] = mine.get(r[0], 0) + float(r[i])
    ref = {r[0]: float(r[1]) for r in w.query(E.ADS_SQL, {"since": SPAN.since, "until": SPAN.until})}
    assert ref and mine == pytest.approx(ref) and set(mine) == set(ref)
    assert ref[D(2026, 11, 2)] == 10 + 5 + 4 + 20 and ref[D(2026, 11, 3)] == 3 + 6 + 2


# ---------------------------------------------------------------------------------------------------------------- ads by ASIN

def test_ads_asin_reads_v3_advertised_products_only_and_ties_to_the_campaign_table(wh):
    w, url = wh
    seed_ads(url)
    base = dict(marketplace="US", api_version="v3", date=D(2026, 11, 2))
    put(url, "amazon_ads_raw_data", [
        {**base, "api_report_type": "spAdvertisedProduct", "campaign_id": "X", "campaign_name": f"{BL} - x", "ad_group_id": "g1", "asin": BL, "sku": "LV-BLC180", "cost_usd": 10,
         "impressions": 100, "clicks": 5, "sales_14d": 40, "sales_1d": 1, "purchases_14d": 4},
        {**base, "api_report_type": "spAdvertisedProduct", "campaign_id": "X", "campaign_name": "x", "ad_group_id": "g2", "asin": VB, "sku": "LV-VBULL", "cost_usd": 5, "impressions": 50, "clicks": 2, "sales_14d": 10},
        {**base, "api_report_type": "sdAdvertisedProduct", "campaign_id": "D", "campaign_name": "d", "ad_group_id": "gd", "asin": BL, "sku": "LV-BLC180", "cost_usd": 2, "impressions": 20,
         "clicks": 2, "sales_14d": 0, "sales_aggregate": 11, "purchases_aggregate": 1},
        {**base, "api_version": "v1", "api_report_type": "spAdvertisedProduct", "campaign_id": "X", "asin": BL, "cost_usd": 1000, "clicks": 1},          # the v1 copy
        {**base, "api_report_type": "spPurchasedProduct", "campaign_id": "X", "asin": BL, "cost_usd": 0, "clicks": 0},                                 # not advertised product
        {**base, "api_report_type": "spAdvertisedProduct", "campaign_id": "E", "asin": BL, "cost_usd": 0, "impressions": 0, "clicks": 0},              # empty
    ])
    rows = A.pull_ads_asin(ctx_for(w), SPAN)
    cols = feed("amazon_ads_asin_daily").cols
    got = {(r[cols.index("campaign_id")], r[cols.index("asin")], r[cols.index("ad_type")]): dict(zip(cols, r)) for r in rows}
    assert set(got) == {("X", BL, "sp"), ("X", VB, "sp"), ("D", BL, "sd")}
    assert got[("X", BL, "sp")]["sales_14d"] == 40 and got[("X", BL, "sp")]["sales_reported"] == 40 and got[("X", BL, "sp")]["product_slug"] == "beef-liver"
    sd = got[("D", BL, "sd")]
    assert sd["sales"] == 11 and sd["purchases"] == 1 and sd["sales_14d"] is None and sd["sales_reported"] == 11
    sp_spend = sum(float(g["spend"]) for g in got.values() if g["ad_type"] == "sp")
    camp = A.pull_ads_campaign(ctx_for(w), Span(D(2026, 11, 2), D(2026, 11, 2)))
    ci = feed("amazon_ads_campaign_daily").cols
    assert sp_spend == sum(float(r[ci.index("spend")]) for r in camp if r[ci.index("ad_type")] == "sp" and r[ci.index("api_version")] == "v3")


# ---------------------------------------------------------------------------------------------------------------- search query performance

def sqp(url, **kw):
    base = dict(asin=BL, start_date=D(2026, 10, 25), end_date=D(2026, 10, 31), period_type="WEEK", marketplace_id=A.US_MARKETPLACE_ID, search_query="beef liver", search_query_score=1,
                search_query_volume=1000, total_query_impression_count=206119, asin_impression_count=300, asin_impression_share=2.95, total_click_count=500, asin_click_count=40,
                asin_click_share=8.0, total_cart_add_count=60, asin_cart_add_count=6, asin_cart_add_share=10.0, total_purchase_count=30, asin_purchase_count=4, asin_purchase_share=13.33,
                asin_median_purchase_price=29.99, updated_at=dt.datetime(2026, 11, 1))
    put(url, "brand_analytics_search_query_performances", [{**base, **kw}])


def test_sqp_dedupes_restatements_normalises_queries_flags_brand_and_keeps_shares_as_percent(wh):
    w, url = wh
    seed_asins(url)
    sqp(url, search_query="Beef  Liver ", asin_purchase_count=1, updated_at=dt.datetime(2026, 11, 1))                       # older restatement of the same key
    sqp(url, search_query="beef liver", asin_purchase_count=4, updated_at=dt.datetime(2026, 11, 5))                         # latest wins
    sqp(url, asin=VB, search_query="beef liver", asin_purchase_count=2)                                                        # same query, other ASIN: its own row, same market totals
    sqp(url, search_query="Lonvera Beef Liver", asin_purchase_count=9)
    sqp(url, search_query="   ")                                                                                               # blank query: skipped
    sqp(url, start_date=D(2026, 10, 18), end_date=D(2026, 10, 24), search_query="old week")
    sqp(url, period_type="MONTH", start_date=D(2026, 10, 1), end_date=D(2026, 10, 31), search_query="month row")
    sqp(url, marketplace_id="A1AM78C64UM0Y8", search_query="beef liver")
    rows = A._pull_sqp("WEEK", "t", 182)(ctx_for(w), Span(D(2026, 10, 20), D(2026, 11, 7)))
    cols = feed("amazon_sqp_weekly").cols
    by = {(r[cols.index("asin")], r[cols.index("query_key")], r[0], r[2]): dict(zip(cols, r)) for r in rows}
    key = (BL, "beef liver", D(2026, 10, 25), A.US_MARKETPLACE_ID)
    assert set(by) == {key, (VB, "beef liver", D(2026, 10, 25), A.US_MARKETPLACE_ID), (BL, "lonvera beef liver", D(2026, 10, 25), A.US_MARKETPLACE_ID),
                       (BL, "beef liver", D(2026, 10, 25), "A1AM78C64UM0Y8")}                                              # the 10-18 week is before the window; month and blank rows are gone
    r = by[key]
    assert r["asin_purchases"] == 4 and r["search_query"] == "beef liver" and r["marketplace"] == "US" and r["is_branded"] is False and r["product_slug"] == "beef-liver"
    assert float(r["asin_impression_share"]) == 2.95 and r["total_impressions"] == 206119 and r["search_query_volume"] == 1000 and r["period_end"] == D(2026, 10, 31)
    assert by[(BL, "lonvera beef liver", D(2026, 10, 25), A.US_MARKETPLACE_ID)]["is_branded"] is True
    assert by[(BL, "beef liver", D(2026, 10, 25), "A1AM78C64UM0Y8")]["marketplace"] == "MX"
    assert sum(1 for x in by.values() if x["query_key"] == "beef liver" and x["marketplace_id"] == A.US_MARKETPLACE_ID) == 2   # market totals repeat on both ASIN rows (SUM would double them)
    monthly = A._pull_sqp("MONTH", "t", 366)(ctx_for(w), Span(D(2026, 9, 1), D(2026, 11, 7)))
    assert [(x[feed("amazon_sqp_monthly").cols.index("query_key")], x[0]) for x in monthly] == [("month row", D(2026, 10, 1))]


# ---------------------------------------------------------------------------------------------------------------- orders by ASIN

def test_orders_asin_daily_sums_equal_the_existing_amazon_revenue_units_and_new_customer_rule(wh):
    w, url = wh
    seed_asins(url)
    put(url, "customers", [dict(id=1, cohort_day="2026-11-01"), dict(id=2, cohort_day="2026-10-01"), dict(id=3, cohort_day="2026-11-02")])
    s = lambda i, o, d, q, amt, ch, c, asin: dict(id=i, amazon_order_id=o, purchase_date=d, quantity_shipped=q, total_amount_usd=amt, sales_channel=ch, customer_id=c, asin=asin)   # noqa: E731
    put(url, "sales", [s(1, "A1", D(2026, 11, 1), 2, 20, AMZ, 1, VB), s(2, "A1", D(2026, 11, 1), 1, 10, AMZ, 1, BL), s(3, "A2", D(2026, 11, 1), 1, 15, AMZ, 2, BL),
                       s(4, "A3", D(2026, 11, 2), 1, 12.5, AMZ, 3, "B0GL4VVRVR"), s(5, "A4", D(2026, 11, 2), 1, 50, B2B, 2, VB), s(6, "A5", D(2026, 11, 2), 5, 99, "Shopify", 1, VB),
                       s(7, "A6", D(2026, 11, 3), 1, 9, AMZ, None, BL), s(8, "A7", D(2026, 10, 20), 9, 900, AMZ, 2, BL)])
    rows = A.pull_orders_asin(ctx_for(w), SPAN)
    cols = feed("amazon_orders_asin_daily").cols
    by = {(r[0], r[1], r[2]): dict(zip(cols, r)) for r in rows}
    assert set(by) == {(D(2026, 11, 1), VB, AMZ), (D(2026, 11, 1), BL, AMZ), (D(2026, 11, 2), "B0GL4VVRVR", AMZ), (D(2026, 11, 2), VB, B2B)}   # Shopify, customer-less and old lines: out
    assert by[(D(2026, 11, 1), VB, AMZ)]["nc_revenue"] == 20 and by[(D(2026, 11, 1), VB, AMZ)]["nc_orders"] == 1 and by[(D(2026, 11, 1), BL, AMZ)]["nc_revenue"] == 10
    assert by[(D(2026, 11, 2), VB, B2B)]["nc_orders"] == 0 and by[(D(2026, 11, 2), "B0GL4VVRVR", AMZ)]["nc_orders"] == 1
    assert by[(D(2026, 11, 1), VB, AMZ)]["product_slug"] == "vita-bull"
    ref = {r[0]: r for r in w.query(E.REVENUE_SQL, {"since": SPAN.since, "until": SPAN.until, "channels": E.SALES_CHANNELS})}
    for day, r in ref.items():
        mine = [v for k, v in by.items() if k[0] == day]
        assert sum(v["units"] for v in mine) == r[2] and sum(v["revenue"] for v in mine) == pytest.approx(float(r[3]))
        assert sum(v["nc_revenue"] for v in mine) == pytest.approx(float(r[5] or 0)) and sum(v["nc_orders"] for v in mine) >= r[4]
    assert {k[0] for k in by} == set(ref)


# ---------------------------------------------------------------------------------------------------------------- traffic, baskets, repeat, settlements

def test_traffic_keeps_the_latest_snapshot_of_each_day_us_parent_rows_only(wh):
    w, url = wh
    t = lambda **kw: dict(marketplace_id=A.US_MARKETPLACE_ID, date_granularity="DAY", asin_granularity="PARENT", report_date=D(2026, 11, 2), sessions=300, page_views=400,   # noqa: E731
                          buy_box_percentage=98.5, updated_at=dt.datetime(2026, 11, 3), **kw)
    rows = [t(units_ordered=130), {**t(units_ordered=115), "updated_at": dt.datetime(2026, 11, 8)}, {**t(units_ordered=1), "sessions": 0, "updated_at": dt.datetime(2026, 11, 2)},
            {**t(units_ordered=5), "asin_granularity": "CHILD"}, {**t(units_ordered=6), "marketplace_id": "A1AM78C64UM0Y8"}, {**t(units_ordered=7), "date_granularity": "WEEK"}]
    put(url, "sales_and_traffic_reports", rows)
    out = A.pull_traffic(ctx_for(w), SPAN)
    assert len(out) == 1 and out[0][0] == D(2026, 11, 2) and out[0][2] == 300 and out[0][8] == 115 and float(out[0][6]) == 98.5


def test_baskets_repeat_and_settlements(wh):
    w, url = wh
    seed_asins(url)
    b = dict(asin=BL, purchased_with_asin="B0OTHER001", purchased_with_rank=1, combination_pct=12.5, start_date=D(2026, 11, 1), end_date=D(2026, 11, 7), period_type="WEEK",
             marketplace_id=A.US_MARKETPLACE_ID, updated_at=dt.datetime(2026, 11, 8))
    put(url, "brand_analytics_market_baskets", [b, {**b, "combination_pct": 1, "updated_at": dt.datetime(2026, 11, 1)}, {**b, "purchased_with_asin": VB, "purchased_with_rank": 2}, {**b, "period_type": "DAY"}])
    out = A.pull_baskets(ctx_for(w), SPAN)
    assert len(out) == 2 and {(r[3], r[6], float(r[9]), r[7]) for r in out} == {(BL, "B0OTHER001", 12.5, None), (BL, VB, 12.5, "vita-bull")}
    rp = dict(asin=BL, start_date=D(2026, 11, 1), end_date=D(2026, 11, 7), period_type="WEEK", marketplace_id=A.US_MARKETPLACE_ID, orders=10, unique_customers=8,
              repeat_customers_pct_total=25.0, repeat_purchase_revenue=100, repeat_purchase_revenue_pct_total=30.0, updated_at=dt.datetime(2026, 11, 8))
    put(url, "brand_analytics_repeat_purchases", [rp, {**rp, "orders": 99, "updated_at": dt.datetime(2026, 11, 1)}, {**rp, "period_type": "MONTH"}])
    out = A.pull_repeat(ctx_for(w), SPAN)
    assert len(out) == 1 and out[0][6] == 10 and out[0][4] == "beef-liver"
    s = lambda d, t, at, desc, amt, mk="Amazon.com": dict(posted_date=d, marketplace_name=mk, transaction_type=t, amount_type=at, amount_description=desc, amount=amt, amount_usd=amt, currency="USD")  # noqa: E731
    put(url, "amazon_settlement_reports", [s(D(2026, 11, 2), "Order", "ItemFees", "Commission", -3.0), s(D(2026, 11, 2), "Order", "ItemFees", "Commission", -2.5),
                                           s(D(2026, 11, 2), "Order", "ItemPrice", "Principal", 40.0), s(D(2026, 11, 3), "Refund", "ItemPrice", "Principal", -20.0, mk=None),
                                           s(D(2026, 9, 1), "Order", "ItemPrice", "Principal", 5)])
    out = A.pull_settlements(ctx_for(w), SPAN)
    got = {(r[0], r[2], r[3], r[4]): (r[5], float(r[6]), float(r[7])) for r in out}
    assert got == {(D(2026, 11, 2), "Order", "ItemFees", "Commission"): (2, -5.5, -5.5), (D(2026, 11, 2), "Order", "ItemPrice", "Principal"): (1, 40.0, 40.0),
                   (D(2026, 11, 3), "Refund", "ItemPrice", "Principal"): (1, -20.0, -20.0)}


# ---------------------------------------------------------------------------------------------------------------- writing: idempotent, empty pulls, non-fatal runs

def fake_pull(rows):
    def pull(ctx, span):
        if not rows:
            raise FeedEmpty("nothing for the window")
        return list(rows)
    return pull


def test_a_pull_is_idempotent_and_an_empty_pull_writes_and_deletes_nothing(conn):
    f = feed("amazon_traffic_daily")
    row = lambda d, s: (d, A.US_MARKETPLACE_ID, s, None, None, 400, 98.5, 12.0, 100, 5000.0, 90, None, None, None, None)   # noqa: E731
    rows = [row(D(2026, 11, 5), 300), row(D(2026, 11, 6), 310)]
    ctx = make_ctx(today=D(2026, 11, 8))
    f1 = dataclasses.replace(f, pull=fake_pull(rows))
    for _ in range(2):
        r = feeds.pull_and_write(conn, f1, "shadow", 14, ctx)
        assert r["rows"] == 2 and r["mode"] == "shadow"
        assert conn.execute("SELECT count(*), sum(sessions) FROM amazon_traffic_daily").fetchone() == (2, 610)
    with pytest.raises(FeedError):
        feeds.pull_and_write(conn, dataclasses.replace(f, pull=fake_pull([])), "shadow", 14, ctx)
    assert conn.execute("SELECT count(*), sum(sessions) FROM amazon_traffic_daily").fetchone() == (2, 610)             # nothing deleted, no zero rows
    assert conn.execute("SELECT status FROM proj_feed_attempts WHERE feed = 'amazon_traffic_daily' ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"
    f2 = dataclasses.replace(f, pull=fake_pull([row(D(2026, 11, 6), 999)]))                                           # a restated day replaces its row; the day before the window stays
    feeds.pull_and_write(conn, f2, "shadow", 2, ctx)
    assert dict(conn.execute("SELECT day, sessions FROM amazon_traffic_daily").fetchall()) == {D(2026, 11, 5): 300, D(2026, 11, 6): 999}
    conn.execute("TRUNCATE amazon_traffic_daily")
    conn.commit()


def test_asin_products_is_a_full_refresh(conn):
    f = feed("amazon_asin_products")
    ctx = make_ctx(today=D(2026, 11, 8))
    feeds.pull_and_write(conn, dataclasses.replace(f, pull=fake_pull([("B0A", "S1", "beef-liver", "Beef Liver", "x", D(2026, 11, 7))])), "shadow", 1, ctx)
    feeds.pull_and_write(conn, dataclasses.replace(f, pull=fake_pull([("B0B", "S2", None, None, "y", D(2026, 11, 8))])), "shadow", 1, ctx)
    assert [r[0] for r in conn.execute("SELECT asin FROM amazon_asin_products").fetchall()] == ["B0B"]
    conn.execute("TRUNCATE amazon_asin_products")
    conn.commit()


def test_run_never_fails_or_stops_for_a_non_fatal_amazon_feed_and_still_fails_for_a_fatal_one(conn, monkeypatch):
    ok_row = (D(2026, 11, 6), A.US_MARKETPLACE_ID, 300, None, None, 400, 98.5, 12.0, 100, 5000.0, 90, None, None, None, None)
    boom, good = dataclasses.replace(feed("amazon_ads_asin_daily"), pull=fake_pull([])), dataclasses.replace(feed("amazon_traffic_daily"), pull=fake_pull([ok_row]))
    monkeypatch.setattr(feeds, "registry", lambda: {boom.key: boom, good.key: good})
    lines: list[str] = []
    assert feeds.run(conn, out=lines.append, ctx=make_ctx(today=D(2026, 11, 8))) == 0
    assert any("amazon_ads_asin_daily" in l and "WARNING" in l for l in lines) and any("amazon_traffic_daily" in l and "ok, 1 rows" in l for l in lines)
    assert conn.execute("SELECT count(*) FROM amazon_traffic_daily").fetchone()[0] == 1                                 # the feed after the failing one still ran
    fatal = dataclasses.replace(boom, non_fatal=False)
    monkeypatch.setattr(feeds, "registry", lambda: {fatal.key: fatal, good.key: good})
    assert feeds.run(conn, out=lines.append, ctx=make_ctx(today=D(2026, 11, 8))) == 1
    conn.execute("TRUNCATE amazon_traffic_daily")
    conn.commit()


def test_run_backfill_and_since_widen_each_feeds_window(conn, monkeypatch):
    seen: dict[str, int] = {}

    def recorder(key):
        f = feed(key)

        def pull(ctx, span):
            seen[key] = (span.until - span.since).days + 1
            raise FeedEmpty("probe")
        return dataclasses.replace(f, pull=pull)
    fs = {k: recorder(k) for k in ("amazon_sqp_weekly", "amazon_traffic_daily")}
    monkeypatch.setattr(feeds, "registry", lambda: fs)
    ctx = make_ctx(today=D(2026, 11, 8))
    feeds.run(conn, out=lambda s: None, ctx=ctx)
    assert seen == {"amazon_sqp_weekly": 42, "amazon_traffic_daily": 14}
    feeds.run(conn, out=lambda s: None, ctx=ctx, backfill=True)
    assert seen == {"amazon_sqp_weekly": (D(2026, 11, 7) - D(2025, 7, 13)).days + 1, "amazon_traffic_daily": (D(2026, 11, 7) - D(2024, 4, 7)).days + 1}
    feeds.run(conn, out=lambda s: None, ctx=ctx, since=D(2026, 10, 1))
    assert seen == {"amazon_sqp_weekly": 42, "amazon_traffic_daily": 38}                      # never narrower than the feed's own window
