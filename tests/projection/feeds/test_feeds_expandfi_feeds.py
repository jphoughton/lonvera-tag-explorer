"""ExpandFi warehouse feeds: amazon, units_amazon, nc_product. The real SQL runs against a throwaway schema (`exwh`) that mirrors the warehouse tables
(sales, customers, amazon_ads_campaign_data, shopify_orders); no network and no ExpandFi credential: the OAuth and MCP steps are played by a FakeSession."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import urllib.parse

import psycopg
import pytest

from etl import feeds
from etl.feeds import expandfi_auth as X
from etl.feeds import expandfi_feeds as E
from etl.feeds import verify as V
from etl.feeds.common import AuthError, FeedEmpty, FeedError, Span, make_ctx
from feeds_helpers import FakeResponse, FakeSession

D = dt.date
SPAN = Span(D(2026, 11, 1), D(2026, 11, 3))
PASSWORD = "S3cretPassw0rd"
DSN = f"postgresql://ro_user_2026_11_04:{PASSWORD}@db.example.internal:5432/tenant_1452"
NOW = dt.datetime(2026, 11, 4, 20, tzinfo=dt.timezone.utc)


def feed(key):
    return next(f for f in E.FEEDS if f.key == key)


# ---------------------------------------------------------------------------------------------------------------- a throwaway warehouse schema

WAREHOUSE_DDL = """
DROP SCHEMA IF EXISTS exwh CASCADE;
CREATE SCHEMA exwh;
CREATE TABLE exwh.customers (id bigint PRIMARY KEY, cohort_day varchar, email varchar, sales_channel varchar);
CREATE TABLE exwh.sales (id bigint PRIMARY KEY, amazon_order_id varchar, purchase_date date, quantity_shipped integer, total_amount_usd numeric,
                         sales_channel varchar, customer_id integer, sku varchar, product_name varchar, cohort_day varchar);
CREATE TABLE exwh.amazon_ads_campaign_data (id bigserial PRIMARY KEY, campaign_id varchar, date date, api_report_type varchar, api_version varchar,
                                            cost_usd numeric, sales_14d numeric, clicks integer, impressions integer);
CREATE TABLE exwh.shopify_orders (id bigint PRIMARY KEY, shopify_customer_id varchar, email varchar, processed_at timestamp, total_price numeric, raw_data jsonb);
"""


@pytest.fixture()
def wh(feeds_db):
    """A Warehouse wired to the exwh schema of the test database (search_path set on the connection; read-only session as in production)."""
    with psycopg.connect(feeds_db, autocommit=True) as c:
        c.execute(WAREHOUSE_DDL)
    dsn = feeds_db + ("&" if "?" in feeds_db else "?") + "options=" + urllib.parse.quote("-c search_path=exwh")
    return E.Warehouse(dsn), feeds_db


def put(url, table, rows, cols):
    with psycopg.connect(url, autocommit=True) as c:
        for r in rows:
            c.execute(f"INSERT INTO exwh.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", r)


SALES_COLS = ("id", "amazon_order_id", "purchase_date", "quantity_shipped", "total_amount_usd", "sales_channel", "customer_id", "sku", "product_name")
AMZ, B2B = "Amazon.com", "Amazon.com B2B"


def seed_amazon(url):
    put(url, "customers", [(1, "2026-11-01", None, AMZ), (2, "2026-10-01", None, AMZ), (3, "2026-11-02", None, AMZ)], ("id", "cohort_day", "email", "sales_channel"))
    put(url, "sales", [
        (1, "A1", D(2026, 11, 1), 2, 20, AMZ, 1, "LV-VBULL-3", "Vital Bull"),                # two lines of one order: 1 order, 3 units
        (2, "A1", D(2026, 11, 1), 1, 10, AMZ, 1, "LV-VBULL-1", "Vital Bull"),
        (3, "A2", D(2026, 11, 1), 1, 15, AMZ, 2, "LV-BLC180-1", "Beef Liver"),               # returning customer (cohort day 2026-10-01)
        (4, "A3", D(2026, 11, 2), 1, 12, AMZ, 3, "LV-BFLEX-1", "Boneflex"),                  # first order on its cohort day
        (5, "A4", D(2026, 11, 2), 1, 50, B2B, 2, "LV-VBULL-1", "Vital Bull"),                # B2B counts
        (6, "A5", D(2026, 11, 2), 5, 99, "Shopify", 1, "LV-VBULL-1", "Vital Bull"),          # not an Amazon channel
        (7, "A6", D(2026, 11, 3), 1, 9, AMZ, None, "LV-VBELLE-1", "Vital Belle Bovine Liver, Heart & Kidney"),   # no customer: out of the daily join, in the units
        (8, "A7", D(2026, 11, 3), 3, 30, AMZ, 2, "XYZ", "Some grass-fed Liver thing"),       # product by name fallback
        (9, "A8", D(2026, 10, 28), 9, 900, AMZ, 2, "LV-VBULL-1", "Vital Bull"),              # outside the window
    ], SALES_COLS)


ADS_COLS = ("campaign_id", "date", "api_report_type", "api_version", "cost_usd", "sales_14d", "clicks", "impressions")


def seed_ads(url):
    put(url, "amazon_ads_campaign_data", [
        ("X", D(2026, 11, 1), "spCampaigns", "v3", 10, 40, 5, 100),
        ("X", D(2026, 11, 1), "spCampaigns", "v2", 12, 45, 6, 110),                           # same campaign-day, older generation: dropped
        ("Y", D(2026, 11, 1), "spCampaigns", "v2", 4, 8, 2, 50),                              # only v2: kept
        ("Z", D(2026, 11, 1), "sbCampaigns", "v1", 99, 99, 99, 999),                          # v1 sb/sd is fenced out
        ("W", D(2026, 11, 2), "spCampaigns", "v1", 3, 5, 1, 20),                              # v1 sp is allowed (rank 3)
        ("Q", D(2026, 11, 2), "spTargets", "v3", 77, 77, 77, 77),                             # not a campaign report
        ("X", D(2026, 10, 20), "spCampaigns", "v3", 500, 500, 500, 500),                      # outside the window
    ], ADS_COLS)


# ---------------------------------------------------------------------------------------------------------------- Amazon daily

def test_amazon_sql_sales_customers_channels_and_new_customer_split(wh):
    w, url = wh
    seed_amazon(url)
    rows = w.query(E.REVENUE_SQL, {"since": SPAN.since, "until": SPAN.until, "channels": E.SALES_CHANNELS})
    got = {r[0]: r[1:] for r in rows}
    assert got[D(2026, 11, 1)] == (2, 4, 45, 1, 30)           # orders, units, revenue, nc orders, nc revenue
    assert got[D(2026, 11, 2)] == (2, 2, 62, 1, 12)           # Shopify row excluded, B2B included
    assert got[D(2026, 11, 3)] == (1, 3, 30, 0, None)         # the customer-less order is out of the inner join
    assert D(2026, 10, 28) not in got


def test_amazon_ads_sql_dedupes_generations_per_campaign_day(wh):
    w, url = wh
    seed_ads(url)
    rows = {r[0]: r[1:] for r in w.query(E.ADS_SQL, {"since": SPAN.since, "until": SPAN.until})}
    assert rows[D(2026, 11, 1)] == (14, 48, 7, 150)           # X v3 (not v2) + Y v2; the v1 sbCampaigns row is fenced out
    assert rows[D(2026, 11, 2)] == (3, 5, 1, 20)              # v1 spCampaigns kept, spTargets ignored
    assert D(2026, 10, 20) not in rows


def test_pull_amazon_merges_days_and_warns_when_the_last_day_has_no_ads_yet(wh):
    w, url = wh
    seed_amazon(url)
    seed_ads(url)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    rows = E.pull_amazon(ctx, SPAN)
    assert rows == [(D(2026, 11, 1), 2.0, 4.0, 45.0, 1.0, 30.0, 14.0, 48.0, 7.0, 150.0),
                    (D(2026, 11, 2), 2.0, 2.0, 62.0, 1.0, 12.0, 3.0, 5.0, 1.0, 20.0),
                    (D(2026, 11, 3), 1.0, 3.0, 30.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]
    assert len(rows[0]) == len(E.AMAZON_COLS)
    assert any("2026-11-03" in m and "Ads" in m for m in ctx.warnings)


def test_pull_amazon_with_no_sales_or_no_ads_writes_nothing_instead_of_zeros(wh):
    w, url = wh
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    with pytest.raises(FeedEmpty, match="revenue"):
        E.pull_amazon(ctx, SPAN)
    seed_amazon(url)
    with pytest.raises(FeedEmpty, match="Ads"):                  # sales but no Ads rows: the desktop job aborts too (spend would read as zero)
        E.pull_amazon(ctx, SPAN)


# ---------------------------------------------------------------------------------------------------------------- Amazon units by product

def test_units_by_product_sku_first_name_fallback_and_vital_belle_is_not_beef_liver(wh):
    w, url = wh
    seed_amazon(url)
    rows = w.query(E.PRODUCT_SQL, {"since": SPAN.since, "until": SPAN.until, "channels": E.SALES_CHANNELS})
    got = {(r[0], r[1]): r[3] for r in rows}
    assert got[(D(2026, 11, 1), "Vital Bull")] == 3 and got[(D(2026, 11, 1), "Beef Liver")] == 1
    assert got[(D(2026, 11, 2), "Boneflex")] == 1 and got[(D(2026, 11, 2), "Vital Bull")] == 1
    assert got[(D(2026, 11, 3), "Vital Belle")] == 1                  # its title says "Bovine Liver" but the SKU wins
    assert got[(D(2026, 11, 3), "Beef Liver")] == 3                   # name fallback ('Liver' is checked last)


def test_pull_units_amazon_maps_slugs_in_the_inventory_modules_shape(wh):
    w, url = wh
    seed_amazon(url)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    rows = E.pull_units_amazon(ctx, SPAN)
    assert ("amazon", "bull", D(2026, 11, 1), 3.0, "expandfi-product-daily", False) in rows
    assert ("amazon", "belle", D(2026, 11, 3), 1.0, "expandfi-product-daily", False) in rows
    assert all(r[0] == "amazon" and r[5] is False and len(r) == len(E.UNITS_COLS) for r in rows)


def test_units_rows_drop_other_and_refuse_negative_units():
    assert E.units_rows([(D(2026, 11, 1), "Other", 3, 5, 10), (D(2026, 11, 1), "Boneflex", 1, 2, 5)]) == [("amazon", "boneflex", D(2026, 11, 1), 2.0, E.UNITS_SOURCE, False)]
    with pytest.raises(FeedError, match="negative"):
        E.units_rows([(D(2026, 11, 1), "Boneflex", 1, -1, 5)])
    with pytest.raises(FeedEmpty):
        class W:
            def query(self, *a, **k):
                return [(D(2026, 11, 1), "Other", 1, 1, 1)]
        E.pull_units_amazon(make_ctx(cache={E.CACHE_KEY: W()}), SPAN)


def test_product_slugs_match_the_inventory_modules_header_map():
    from app.projection.inventory_demand.constants import HEADER_PRODUCT
    assert {k.lower(): v for k, v in E.AMAZON_SLUG.items()} == HEADER_PRODUCT


# ---------------------------------------------------------------------------------------------------------------- new customers by product (Shopify first orders)

def line(sku, price, qty=1, disc=0):
    return {"sku": sku, "price": str(price), "quantity": qty, "total_discount": str(disc)}


def ln(sku, price, qty=1, disc=0):
    """A line as the first-orders query returns it (jsonb_build_object keys p / q / d), for tests that call nc_rows directly."""
    return {"sku": sku, "p": str(price), "q": str(qty), "d": str(disc)}


def order(oid, cid, email, ts, total, lines, test=None):
    raw = {"line_items": lines}
    if test is not None:
        raw["test"] = test
    return (oid, cid, email, ts, total, json.dumps(raw))


SHOP_COLS = ("id", "shopify_customer_id", "email", "processed_at", "total_price", "raw_data")
VB, BL, BF, BELLE, FLOW, OTHER = "LV-VBULL-1", "LV-BLC180-1", "DPCD1-GN187001", "LV-VBELLE-1", "DPCD1-GN179001", "ZZZ"


def seed_shopify(url):
    put(url, "shopify_orders", [
        order(1, "100", "c100@x.com", dt.datetime(2026, 11, 2, 3, 30), 100, [line(VB, 60), line(BL, 40)]),                  # 03:30Z = Nov 1 20:30 Pacific
        order(2, "100", "c100@x.com", dt.datetime(2026, 11, 3, 18, 0), 80, [line(VB, 80)]),                                 # not the first order
        order(3, None, "A@X.com", dt.datetime(2026, 11, 2, 18, 0), 90, [line(VB, 45), line(BELLE, 30), line(OTHER, 30)]),   # no customer id: keyed on lower(email)
        order(9, "", "a@x.com", dt.datetime(2026, 11, 3, 10, 0), 70, [line(VB, 70)]),                                       # same customer by email, later
        order(4, "", "", dt.datetime(2026, 11, 2, 19, 0), 55, [line(VB, 55)]),                                              # no customer key at all: skipped
        order(5, "200", "t@x.com", dt.datetime(2026, 11, 2, 19, 0), 60, [line(VB, 60)], test="true"),                       # a test order is the first order: customer not counted
        order(6, "200", "t@x.com", dt.datetime(2026, 11, 3, 19, 0), 60, [line(VB, 60)]),                                    # ... and the later real order is not a first order
        order(7, "300", "s@x.com", dt.datetime(2026, 11, 2, 20, 0), 90, [line(BELLE, 50), line(FLOW, 40)]),                 # Belle + Flow: all Other, one order
        order(8, "400", "e@x.com", dt.datetime(2026, 11, 2, 21, 0), 40, []),                                                # no lines: skipped
        order(10, "500", "d@x.com", dt.datetime(2026, 11, 3, 12, 0), 30, [line(BF, 30, 2, 30)]),                           # tie on time: lower id is the first order
        order(11, "500", "d@x.com", dt.datetime(2026, 11, 3, 12, 0), 99, [line(VB, 99)]),
        order(12, "600", "w@x.com", dt.datetime(2026, 10, 1, 12, 0), 50, [line(VB, 50)]),                                   # first order long before the window
        order(13, "600", "w@x.com", dt.datetime(2026, 11, 2, 12, 0), 50, [line(VB, 50)]),
    ], SHOP_COLS)


def test_first_orders_sql_and_split_match_the_desktop_rules(wh):
    w, url = wh
    seed_shopify(url)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    rows = E.pull_nc_product(ctx, SPAN)
    got = {(r[0], r[1]): (r[2], r[3]) for r in rows}
    assert got[(D(2026, 11, 1), "VitaBull")] == (60.0, 1.0) and got[(D(2026, 11, 1), "Beef Liver")] == (40.0, 0.0)      # Pacific day, split by net price, largest line gets the order
    assert got[(D(2026, 11, 2), "VitaBull")] == (round(90 * 45 / 105, 2), 1.0)                                          # email-keyed first order; Bull is the largest line
    assert got[(D(2026, 11, 2), "Other")] == (round(90 * 60 / 105 + 90, 2), 1.0)                                       # Belle + unmatched of order 3, plus all of order 7 (counted once)
    assert got[(D(2026, 11, 3), "Boneflex")] == (30.0, 1.0)                                                             # 2 x 30 - 30 discount = 30 net; id 10 beats id 11
    assert (D(2026, 11, 3), "VitaBull") not in got                                                                      # orders 2, 9, 11 and 6 are not first orders
    assert not any(d in (D(2026, 10, 1),) for d, _ in got)
    assert sum(v[1] for v in got.values()) == 4                                                                         # orders 1, 3, 7, 10: test / keyless / no-line orders skipped
    assert all(p in E.NC_PRODUCTS for _d, p in got)


def test_nc_rows_pick_the_largest_line_before_belle_and_flow_roll_into_other():
    d = D(2026, 11, 1)
    lines = [ln(VB, 45), ln(BELLE, 30), ln(OTHER, 30)]                                    # Other would be 60 after the roll-up, but Bull is the largest line first
    rows = E.nc_rows([(1, d, 105, None, lines)])
    assert {(r[1], r[3]) for r in rows} == {("VitaBull", 1.0), ("Other", 0.0)}
    rows = E.nc_rows([(2, d, 105, None, [ln(VB, 40), ln(BELLE, 65)])])                       # Belle is the largest line: the order belongs to Other
    assert {(r[1], r[3]) for r in rows} == {("VitaBull", 0.0), ("Other", 1.0)}


def test_nc_rows_skip_zero_value_orders_and_test_orders_and_floor_negative_lines_at_zero():
    d = D(2026, 11, 1)
    assert E.nc_rows([(1, d, 50, "true", [ln(VB, 50)]), (2, d, 50, None, None), (3, d, 0, None, [ln(VB, 10, 1, 10)])]) == []
    rows = E.nc_rows([(4, d, 60, None, [ln(VB, 60), ln(BL, 5, 1, 20)])])                   # the over-discounted line floors at 0, it does not take revenue
    assert rows == [(d, "VitaBull", 60.0, 1.0), (d, "Beef Liver", 0.0, 0.0)]                  # the desktop job writes the zero row for that product too


def test_product_of_prefixes():
    assert [E.product_of(s) for s in ("LV-VBULL-3", "LV-BLC180-2", "DPCD1-GN180001", "DPCD1-GN187001", "LV-VBELLE-1", "DPCD1-GN179001", "", None, "OTHER")] == [
        "VitaBull", "Beef Liver", "Beef Liver", "Boneflex", "Vital Belle", "Vital Flow", "Other", "Other", "Other"]


def test_pull_nc_product_empty_window_writes_nothing(wh):
    w, _ = wh
    with pytest.raises(FeedEmpty):
        E.pull_nc_product(make_ctx(cache={E.CACHE_KEY: w}), SPAN)


# ---------------------------------------------------------------------------------------------------------------- credentials: login, one per run, never leaked

def sse(msg, sid="sess-1"):
    return FakeResponse(200, text="event: message\ndata: " + json.dumps(msg) + "\n\n", headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": sid})


def rpc(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def expandfi_session(minted: list[str] | None = None, refreshes: list | None = None) -> FakeSession:
    s = FakeSession()
    s.on(lambda c: c["url"].endswith("oauth-authorization-server"), FakeResponse(200, {
        "authorization_endpoint": "https://app.expandfi.com/mcp/oauth/authorize", "token_endpoint": "https://app.expandfi.com/mcp/oauth/token",
        "registration_endpoint": "https://app.expandfi.com/mcp/oauth/register"}))
    s.on(lambda c: c["url"].endswith("/token"), FakeResponse(200, {"access_token": "AT-1", "refresh_token": "RT-2", "expires_in": 3600}))
    s.on(lambda c: (c["json"] or {}).get("method") == "initialize", sse(rpc({"protocolVersion": "2025-06-18", "serverInfo": {"name": "ExpandFi"}})))
    s.on(lambda c: (c["json"] or {}).get("method") == "notifications/initialized", FakeResponse(202))

    def tool(c):
        name = c["json"]["params"]["name"]
        if name == "readonly_db_connection":
            (minted if minted is not None else []).append(name)
            return sse(rpc({"content": [{"type": "text", "text": json.dumps({"connection_string": DSN})}]}))
        return sse(rpc({"content": [{"type": "text", "text": "ok"}]}))
    s.on(lambda c: (c["json"] or {}).get("method") == "tools/call", tool)
    return s


def ctx_for(feeds_db, session, key):
    return make_ctx(session=session, sleep=lambda s: None, now_utc=NOW, today=D(2026, 11, 4), env={"DATABASE_URL": feeds_db, "FEEDS_ENC_KEY": key})


def test_one_login_refresh_and_one_minted_credential_serve_every_feed_of_the_run(conn, feeds_db):
    key = X.new_key()
    X.store_tokens(conn, key, "cid", X.Tokens("AT-0", "RT-1", NOW, "mcp_read mcp_db"), "mcp_read mcp_db")
    minted: list[str] = []
    s = expandfi_session(minted)
    ctx = ctx_for(feeds_db, s, key)
    w1 = E.get_warehouse(ctx)
    w2 = E.get_warehouse(ctx)
    assert w1 is w2 and len(minted) == 1
    assert len([c for c in s.calls if c["url"].endswith("/token")]) == 1
    stored = conn.execute("SELECT enc_refresh_token FROM feed_oauth").fetchone()[0]
    assert X.decrypt(key, bytes(stored)) == "RT-2"                                          # the rotated token was stored
    assert w1._dsn == DSN and PASSWORD not in repr(w1.__dict__.get("queries"))


def test_a_second_process_refreshing_at_the_same_time_waits_then_fails_clearly(conn, feeds_db):
    key = X.new_key()
    X.store_tokens(conn, key, "cid", X.Tokens("AT-0", "RT-1", NOW, "mcp_read mcp_db"), "mcp_read mcp_db")
    holder = psycopg.connect(feeds_db)
    try:
        holder.execute("SELECT pg_advisory_lock(hashtext('feeds.expandfi.refresh'))")
        s = expandfi_session()
        with pytest.raises(FeedError, match="another process"):
            E.get_warehouse(ctx_for(feeds_db, s, key))
        assert not [c for c in s.calls if c["url"].endswith("/token")]                       # it never refreshed while the other process held the lock
    finally:
        holder.close()
    E.get_warehouse(ctx_for(feeds_db, expandfi_session(), key))                              # lock released with the connection: works again


def test_a_rejected_login_raises_auth_error_and_the_feed_writes_nothing_and_stays_stale(conn, feeds_db):
    key = X.new_key()
    X.store_tokens(conn, key, "cid", X.Tokens("AT-0", "RT-1", NOW, "mcp_read mcp_db"), "mcp_read mcp_db")
    s = expandfi_session()
    s.routes.insert(1, (lambda c: c["url"].endswith("/token"), FakeResponse(400, {"error": "invalid_grant"})))
    ctx = ctx_for(feeds_db, s, key)
    with pytest.raises(AuthError, match="login"):
        E.pull_amazon(ctx, SPAN)
    f = feed("amazon")
    with pytest.raises(FeedError):
        feeds.pull_and_write(conn, f, "shadow", 21, ctx_for(feeds_db, s, key))
    assert conn.execute("SELECT count(*) FROM proj_feedx_amazon").fetchone()[0] == 0          # no row, no zeros
    att = conn.execute("SELECT status, note FROM proj_feed_attempts WHERE feed = 'amazon' ORDER BY id DESC").fetchone()
    assert att[0] == "failed" and "invalid_grant" in att[1]
    fresh = feeds.freshness_of(conn, f, "shadow", D(2026, 11, 4))
    assert fresh["data_through"] is None and fresh["status"] == "unknown"


def test_a_failed_login_is_remembered_for_the_run_so_later_feeds_do_not_refresh_again(conn, feeds_db):
    key = X.new_key()
    X.store_tokens(conn, key, "cid", X.Tokens("AT-0", "RT-1", NOW, "mcp_read mcp_db"), "mcp_read mcp_db")
    s = expandfi_session()
    s.routes.insert(1, (lambda c: c["url"].endswith("/token"), FakeResponse(400, {"error": "invalid_grant"})))
    ctx = ctx_for(feeds_db, s, key)
    for f in E.FEEDS:                                                       # the three feeds of one run
        with pytest.raises(AuthError, match="login"):
            f.pull(ctx, SPAN)
    assert len([c for c in s.calls if c["url"].endswith("/token")]) == 1


def connect_raising(msg_for):
    def connect(dsn, timeout_ms):
        raise psycopg.OperationalError(msg_for(dsn))
    return connect


def test_an_expired_credential_is_replaced_once_and_a_second_rejection_is_an_auth_error():
    seen = []

    class Conn:
        def execute(self, stmt, params=None):
            class R:
                def fetchall(self_inner):
                    return [(1,)]
            return R()

        def close(self):
            pass

    def connect(dsn, timeout_ms):
        seen.append(dsn)
        if dsn == "old":
            raise psycopg.OperationalError('connection failed: FATAL:  password authentication failed for user "ro"')
        return Conn()
    w = E.Warehouse("old", remint=lambda: "new", connect=connect)
    assert w.query("SELECT 1") == [(1,)] and seen == ["old", "new"]
    always = E.Warehouse("old", remint=lambda: "old2", connect=connect_raising(lambda d: "password authentication failed"))
    with pytest.raises(AuthError, match="twice"):
        always.query("SELECT 1")
    none = E.Warehouse("old", connect=connect_raising(lambda d: "password authentication failed"))
    with pytest.raises(AuthError):
        none.query("SELECT 1")


def test_the_credential_never_appears_in_errors_or_the_attempt_log(conn):
    class Boom:
        _dsn = DSN

        def query(self, *a, **k):
            raise E.Warehouse(DSN, connect=connect_raising(lambda d: f"could not connect using {d} (password {PASSWORD})")).query("SELECT 1")
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: Boom()})
    with pytest.raises(FeedError) as e:
        feeds.pull_and_write(conn, feed("nc_product"), "shadow", 14, ctx)
    assert PASSWORD not in str(e.value) and "ro_user_2026_11_04" not in str(e.value)
    note = conn.execute("SELECT note FROM proj_feed_attempts WHERE feed = 'nc_product'").fetchone()[0]
    assert PASSWORD not in note and DSN not in note


def test_query_errors_are_scrubbed_and_timeouts_are_named(wh):
    w, _ = wh
    with pytest.raises(FeedError) as e:
        w.query("SELECT * FROM table_that_does_not_exist")
    assert "table_that_does_not_exist" in str(e.value) and "postgresql://" not in str(e.value)
    with pytest.raises(FeedError, match="exceeded"):
        w.query("SELECT pg_sleep(2)", timeout_ms=200)


def test_the_warehouse_session_is_read_only(wh):
    w, _ = wh
    with pytest.raises(FeedError, match="read-only"):
        w.query("CREATE TABLE exwh.nope (x int)")


# ---------------------------------------------------------------------------------------------------------------- window replace, scope, definitions

def fake_pull(rows):
    return lambda ctx, span: list(rows)


def test_pull_and_write_replaces_the_trailing_window_and_keeps_older_days(conn):
    f = dataclasses.replace(feed("nc_product"), pull=fake_pull([(D(2026, 11, 2), "VitaBull", 10.0, 1.0), (D(2026, 11, 3), "VitaBull", 20.0, 2.0)]))
    conn.execute("INSERT INTO proj_feedx_nc_product (day, product, nc_rev, nc_orders) VALUES ('2026-10-01', 'VitaBull', 5, 1), ('2026-11-03', 'Boneflex', 9, 9)")
    conn.commit()
    ctx = make_ctx(today=D(2026, 11, 4))
    r = feeds.pull_and_write(conn, f, "shadow", 14, ctx)
    assert r["rows"] == 2 and r["first_day"] == D(2026, 11, 2) and r["data_through"] == D(2026, 11, 3)
    f2 = dataclasses.replace(f, pull=fake_pull([(D(2026, 11, 2), "VitaBull", 11.0, 1.0)]))
    feeds.pull_and_write(conn, f2, "shadow", 14, ctx)                                          # a second, smaller pull replaces the window, not appends
    rows = conn.execute("SELECT day, product, nc_rev FROM proj_feedx_nc_product ORDER BY 1, 2").fetchall()
    assert rows == [(D(2026, 10, 1), "VitaBull", 5), (D(2026, 11, 2), "VitaBull", 11)]         # Oct 1 is before the window and kept; Nov 3 was inside it and is gone


def test_units_amazon_only_touches_its_own_location_rows(conn):
    conn.execute("INSERT INTO proj_feedx_units_daily (location, product, day, units, source) VALUES ('revamp', 'bull', '2026-11-03', 99, 'warehance'), "
                 "('amazon', 'bull', '2026-11-03', 1, 'old')")
    conn.commit()
    f = dataclasses.replace(feed("units_amazon"), pull=fake_pull([("amazon", "bull", D(2026, 11, 3), 7.0, E.UNITS_SOURCE, False)]))
    feeds.pull_and_write(conn, f, "shadow", 21, make_ctx(today=D(2026, 11, 4)))
    rows = conn.execute("SELECT location, product, units FROM proj_feedx_units_daily ORDER BY 1").fetchall()
    assert rows == [("amazon", "bull", 7), ("revamp", "bull", 99)]


def test_feeds_register_default_to_sheet_mode_and_match_their_tables(conn):
    reg = feeds.registry()
    for k in ("amazon", "nc_product", "units_amazon"):
        f = reg[k]
        assert f.source == "expandfi" and f.default_mode == "sheet" and not f.native
        assert set(f.key_cols) <= set(f.cols) and f.day_col in f.cols and all(m.col in f.cols for m in f.metrics) and f.sheet_tab and f.sheet_parser
        assert V.schema_drift(conn, f) == [], k                                                  # shadow columns equal the main table's
    assert list(reg).index("amazon") > list(reg).index("bids")                                   # expandfi feeds sort after the other sources
    assert feeds.common.get_mode(conn, "amazon", reg["amazon"].default_mode) == "sheet"
    assert feeds.run(conn, ["amazon", "nc_product", "units_amazon"], out=lambda s: None) == 0    # sheet mode: skipped, nothing pulled, no credential needed


def test_verify_compares_the_shadow_table_with_the_sheet_fed_one_and_tolerates_restated_recent_days(conn):
    f = feed("amazon")
    today = D(2026, 11, 4)
    old, recent = D(2026, 10, 15), D(2026, 11, 2)
    row = lambda d, rev, att: (d, 100.0, 110.0, rev, 10.0, 500.0, 60.0, att, 90.0, 9000.0)       # noqa: E731
    for tbl, vals in (("proj_feedx_amazon", [row(old, 5000.0, 900.0), row(recent, 5000.0, 900.0)]),
                      ("proj_feed_amazon", [row(old, 5000.0, 900.0), row(recent, 5000.0, 800.0)])):       # recent attributed sales still maturing in the sheet-fed copy
        for v in vals:
            conn.execute(f"INSERT INTO {tbl} ({', '.join(f.cols)}) VALUES ({', '.join(['%s'] * len(f.cols))})", v)
    conn.commit()
    res = V.verify_feed(conn, f, 30, today)
    assert res["failed"] == 0 and res["status"] == "ok" and res["soft"] == 1 and res["ok"] == 1
    conn.execute("UPDATE proj_feed_amazon SET revenue = 4000 WHERE day = %s", (old,))             # a matured day that really differs fails
    conn.commit()
    assert V.verify_feed(conn, f, 30, today)["status"] == "fail"


# ---------------------------------------------------------------------------------------------------------------- sheet parser (units)

def test_sheet_parser_for_units_reads_the_long_product_tab_and_holds_back_the_partial_day():
    grid = [["Source: ExpandFi. Today's row is partial (same-day capture)."],
            ["Date\nDate", "Product\nx", "Revenue\nx", "Orders\nx", "Units\nx"],
            [46327, "Vital Bull", 100.0, 3, 4], [46327, "Other", 5.0, 1, 1], [46328, "Boneflex", 12.0, 1, 2]]
    rows = E.sheet_rows_units_amazon(grid)
    assert rows == [("amazon", "bull", D(2026, 11, 1), 4.0, "expandfi-product-daily", False)]      # 'Other' is ignored; 2026-11-02 (the partial newest day) is held back
