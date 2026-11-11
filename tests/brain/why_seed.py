"""Seeds a throwaway Postgres with tables named like the analytics views, so the why tool's SQL runs against real columns and known data.
Scenario knobs reproduce Josh's example (one new ad takes the spend at low conversion), decoys, spikes, routing shifts and the optional views (Klaviyo, landing pages, Shopify orders, Meta breakdowns)."""
from __future__ import annotations

import datetime as dt
import math

import psycopg

from brain import rundb
from brain.sqlguard import check_select

D = dt.date
STEP = D(2026, 10, 18)
END = D(2026, 10, 31)
FIRST = D(2026, 8, 1)
AOV = 60.0
PAGE_Z, PAGE_Y, PAGE_BAD = "https://shop.lonvera.com/pages/good", "https://shop.lonvera.com/pages/new-page", "https://shop.lonvera.com/pages/bad-lp"

BASE = [("b%d" % i, "vita-bull" if i < 5 else "boneflex", "C1" if i < 3 else ("C3" if i < 5 else "C2")) for i in range(10)]

TABLES = {
    "data_coverage": "source text, first_day date, last_day date, days int, rows int, loaded_at timestamptz, note text",
    "orders_daily": "day date, new_customer_revenue numeric, new_customers numeric, returning_customer_revenue numeric, total_revenue numeric, orders numeric, loaded_at timestamptz",
    "platform_daily": "day date, platform text, spend numeric, impressions numeric, clicks numeric, own_purchases numeric, own_revenue numeric, upstack_new_customers numeric, upstack_new_customer_revenue numeric, upstack_orders numeric, upstack_order_revenue numeric",
    "meta_reach": "grain text, period_key text, period_start date, period_end date, impressions numeric, reach numeric, frequency numeric, unique_link_clicks numeric, link_clicks numeric, unique_outbound_clicks numeric, loaded_at timestamptz",
    "meta_ad_daily": "day date, ad_id text, ad_name text, product_slug text, campaign_id text, campaign_name text, stage text, adset_id text, adset_name text, account text, source text, spend numeric, impressions numeric, link_clicks numeric, meta_purchases numeric, meta_revenue numeric, upstack_new_customers numeric, upstack_new_customer_revenue numeric, upstack_orders numeric, upstack_order_revenue numeric",
    "meta_ads": "ad_id text, ad_name text, family_name text, family_key text, tracker_family_key text, product_slug text, campaign_id text, campaign_name text, stage text, adset_id text, adset_name text, status text, is_active boolean, created_date date, first_day date, last_day date",
    "meta_adset_daily": "day date, adset_id text, adset_name text, campaign_id text, campaign_name text, stage text, account text, spend numeric",
    "google_campaign_daily": "day date, campaign_key text, campaign_name text, campaign_type text, spend numeric, impressions numeric, clicks numeric, google_purchases numeric, google_revenue numeric, upstack_new_customers numeric, upstack_new_customer_revenue numeric",
    "creative_tracker": "family_key text, is_first_row boolean, cluster text, icp text, pain_point text, emotional_trigger text, ad_type text, style text, vendor text, hook text, primary_text text[], ad_description text, description text",
    "etl_runs": "id int, job text, started_at timestamptz, status text, error text",
}
OPTIONAL = {
    "klaviyo_daily": "day date, source text, parent_id text, message_id text, channel text, parent_name text, sends bigint, delivered bigint, bounced bigint, opens bigint, unique_opens bigint, clicks bigint, unique_clicks bigint, unsubscribes bigint, spam_complaints bigint, orders bigint, revenue numeric",
    "klaviyo_flows": "id text, name text, status text, trigger_type text, created_at timestamptz, updated_at timestamptz",
    "landing_pages": "id bigint, url text, host text, path text, last_fetched_at timestamptz, current_version_id bigint",
    "landing_page_versions": "id bigint, landing_page_id bigint, content_hash text, fetched_at timestamptz, title text, word_count int",
    "landing_page_ads": "ad_id text, landing_page_id bigint, raw_url text, source text, first_seen_date date, last_seen_date date",
    "shopify_orders": "order_id bigint, order_date date, is_new_customer boolean, is_cancelled boolean, is_test boolean, is_trybe_gift boolean, total_price numeric, total_discounts numeric",
    "shopify_order_lines": "order_id bigint, line_no int, quantity int, product text, line_net numeric",
    "meta_breakdown_daily": "day date, account text, campaign_id text, adset_id text, ad_id text, breakdown text, value text, spend numeric, impressions numeric, reach numeric, link_clicks numeric, unique_outbound_clicks numeric, purchases numeric, purchase_value numeric",
}


def days(a=FIRST, b=END):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def noise(d, k=0):
    return 1 + 0.012 * math.sin(d.toordinal() * 1.7 + k)


class Scenario:
    def __init__(self, x_mode=None, uniform_drop=False, spike=False, cluster_drop=False, routing=None, with_lp=False, with_klaviyo=False, with_breakdown=False, with_shopify=False, repeat_drop=False, optional_all=False):
        self.__dict__.update(locals())
        del self.__dict__["self"]


def ad_list(sc: Scenario):
    """[(ad_id, family, name, product, cluster, page, first_day)]"""
    ads = [("a_" + i, "fam-" + i, "Base " + i, p, c, PAGE_Y if sc.routing and i in ("b3", "b4") else PAGE_Z, FIRST) for i, p, c in BASE]
    if sc.x_mode == "new":
        ads.append(("a_x", "lonvera-x", "Lonvera-X", "vita-bull", "C1", PAGE_BAD, STEP))
        ads.append(("a_y", "lonvera-y", "Lonvera-Y", "boneflex", "C2", PAGE_Z, STEP))
    if sc.x_mode == "ramp":
        ads.append(("a_x", "lonvera-x", "Lonvera-X", "vita-bull", "C1", PAGE_BAD, STEP - dt.timedelta(days=14)))
    if sc.routing:
        ads.append(("a_n1", "lonvera-n1", "Lonvera-N1", "boneflex", "C2", PAGE_Y, STEP))
        ads.append(("a_n2", "lonvera-n2", "Lonvera-N2", "boneflex", "C2", PAGE_Y, STEP))
    return ads


def ad_day(sc: Scenario, ad, d):
    """(clicks, nc) for the ad on day d; spend = clicks (CPC 1.00)."""
    aid, fam, _n, prod, cl, page, first = ad
    after = d >= STEP
    if d < first:
        return 0.0, 0.0
    clicks, conv = 300.0, 0.03
    if aid == "a_x":
        clicks, conv = (2000.0, 0.005) if after else (200.0, 0.03)
    elif aid == "a_y":
        clicks, conv = 100.0, 0.03
    elif aid in ("a_n1", "a_n2"):
        clicks, conv = 450.0, 0.01
    else:
        if sc.x_mode and after and prod == "vita-bull":
            clicks = 100.0
        if sc.uniform_drop and after:
            conv *= 0.7
        if sc.cluster_drop and after:
            if prod == "boneflex":
                clicks = 100.0
            if cl == "C3":
                conv *= 0.5
        if sc.routing and after and prod == "boneflex":
            clicks = 120.0
        if sc.routing == "page_problem" and after and page == PAGE_Y:
            conv = 0.01
    if sc.routing == "page_problem" and after and page == PAGE_Y and aid not in ("a_n1", "a_n2"):
        conv = 0.01
    clicks *= noise(d, len(aid))
    return clicks, clicks * conv


def seed(url: str, sc: Scenario) -> None:
    ads = ad_list(sc)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("drop schema if exists analytics cascade")
        c.execute("create schema analytics")
        tabs = dict(TABLES)
        for k, v in OPTIONAL.items():
            if sc.optional_all or (k.startswith("klaviyo") and sc.with_klaviyo) or (k.startswith(("landing", )) and sc.with_lp) or (k.startswith("shopify") and sc.with_shopify) or (k == "meta_breakdown_daily" and sc.with_breakdown):
                tabs[k] = v
        for t, cols in tabs.items():
            c.execute(f"create table analytics.{t} ({cols})")
        ad_rows, as_rows, reach_rows, plat_rows, ord_rows = [], [], [], [], []
        for d in days():
            tc = tn = 0.0
            for ad in ads:
                clicks, nc = ad_day(sc, ad, d)
                if sc.spike and False:
                    pass
                if clicks <= 0:
                    continue
                aid, fam, name, prod, cl, page, first = ad
                imp = clicks * 40
                ad_rows.append((d, aid, name, prod, "camp-" + prod, "Scaling " + prod, "scaling", "as_" + aid, "Adset " + aid, "Lonvera Main", "meta_api", clicks, imp, clicks, nc * 1.3, nc * 1.3 * AOV, nc, nc * AOV, nc, nc * AOV))
                as_rows.append((d, "as_" + aid, "Adset " + aid, "camp-" + prod, "Scaling " + prod, "scaling", "Lonvera Main", clicks))
                tc += clicks
                tn += nc
            nc_total = tn
            if sc.spike and d == D(2026, 10, 29):
                nc_total = tn * 3
            rep_orders = 50.0 * noise(d, 3)
            rep_rev = rep_orders * 60
            if sc.repeat_drop and d >= STEP:
                rep_orders *= 0.7
                rep_rev = rep_orders * 60
            ord_rows.append((d, nc_total * AOV, nc_total, rep_rev, nc_total * AOV + rep_rev, nc_total + rep_orders, dt.datetime.now()))
            plat_rows.append((d, "meta", tc, tc * 40, tc, tn * 1.3, tn * 1.3 * AOV, tn, tn * AOV, tn, tn * AOV))
            plat_rows.append((d, "google", 100.0, 4000, 100, 0, 0, 0, 0, 0, 0))
            reach_rows.append(("day", str(d), d, d, tc * 40, tc * 40 / 3, 3.0, tc, tc, tc, dt.datetime.now()))
        ins = lambda t, rows: c.cursor().executemany(f"insert into analytics.{t} values ({','.join(['%s'] * len(rows[0]))})", rows)  # noqa: E731
        ins("meta_ad_daily", ad_rows)
        ins("meta_adset_daily", as_rows)
        ins("meta_reach", reach_rows)
        ins("platform_daily", plat_rows)
        ins("orders_daily", ord_rows)
        ins("google_campaign_daily", [(d, "g1", "Shopping", "shopping", 100.0, 4000, 100, 0, 0, 0, 0) for d in days()])
        ma, ct = [], []
        for aid, fam, name, prod, cl, page, first in ads:
            ma.append((aid, name, name, fam, fam, prod, "camp-" + prod, "Scaling " + prod, "scaling", "as_" + aid, "Adset " + aid, "ACTIVE", True, first, first, END))
            ct.append((fam, True, cl, "I1", "P1", "T1", "video", "s", "v", "h", ["Try it now https://shop.lonvera.com/pages/bad-lp today"] if aid == "a_x" else ["plain copy"], None, None))
        ins("meta_ads", ma)
        ins("creative_tracker", ct)
        ins("data_coverage", [(s, FIRST, END, 92, 1, dt.datetime.now(), None) for s in ("orders_daily", "platform_daily", "meta_reach")] + [("meta_ad_daily", D(2026, 5, 6) if False else FIRST, END, 92, 1, dt.datetime.now(), None)])
        if "landing_page_ads" in tabs:
            pages = {PAGE_Z: 1, PAGE_Y: 2, PAGE_BAD: 3}
            ins("landing_pages", [(i, u, "shop.lonvera.com", u.split("com")[1], dt.datetime(2026, 10, 31), i * 10) for u, i in pages.items()])
            ins("landing_page_versions", [(10, 1, "h1", dt.datetime(2026, 8, 1), "Good", 800), (20, 2, "h2", dt.datetime(2026, 10, 18), "New", 700), (30, 3, "h3", dt.datetime(2026, 10, 18), "Bad LP", 650), (31, 3, "h0", dt.datetime(2026, 9, 1), "Bad LP old", 640)])
            ins("landing_page_ads", [(a[0], pages[a[5]], a[5], "creative", FIRST, END) for a in ads])
        if "klaviyo_daily" in tabs:
            rows = []
            for d in days():
                bill = 300 if d >= STEP else 100
                rows.append((d, "flow", "f1", "m1", "email", "Billing reminder 3 days", bill, bill, 0, bill, bill // 3, bill // 6, bill // 8, 1, 0, 5, 300.0))
                rows.append((d, "flow", "f2", "m2", "email", "Failed payment recovery", 50, 50, 0, 30, 20, 10, 5, 0, 0, 3, 150.0))
                rows.append((d, "campaign", "c1", "m3", "email", "Weekly promo", 1000, 990, 0, 300, 200, 100, 50, 2, 0, 20, 900.0))
            ins("klaviyo_daily", rows)
            ins("klaviyo_flows", [("f1", "Billing reminder 3 days", "live", "list", dt.datetime(2026, 9, 14), dt.datetime(2026, 10, 17)), ("f2", "Failed payment recovery", "live", "metric", dt.datetime(2026, 8, 1), dt.datetime(2026, 8, 2))])
        if "shopify_orders" in tabs:
            ins("shopify_orders", [(i, d, True, False, False, False, 60.0, 5.0) for i, d in enumerate(days(D(2026, 10, 1), END), 1) for _ in [0]])
            ins("shopify_order_lines", [(i, 1, 2, "x", 55.0) for i in range(1, 32)])
        if "meta_breakdown_daily" in tabs:
            rows = []
            for d in days(D(2026, 10, 4), END):
                for ad in ads:
                    clicks, _nc = ad_day(sc, ad, d)
                    if clicks <= 0:
                        continue
                    aid, fam = ad[0], ad[1]
                    if aid == "a_x":
                        mix = (0.30, 0.65, 0.05) if d >= STEP else (0.65, 0.30, 0.05)
                    else:
                        mix = (0.70, 0.20, 0.10)
                    for v, share in zip(("new_audience", "engaged_audience", "existing_customers"), mix):
                        rows.append((d, "Lonvera Main", "camp-" + ad[3], "as_" + aid, aid, "audience_segment", v, clicks * share, clicks * 40 * share, clicks * 10 * share, clicks * share, clicks * share, 0.01 * clicks * share, 0.6 * clicks * share))
                rows.append((d, "Lonvera Main", "camp-vita-bull", None, None, "region", "California", 100.0, 4000, 1000, 100, 100, 1, 60))     # NULL adset / ad: aggregate at the level that exists
                rows.append((d, "Lonvera Main", None, None, None, "hourly", "14", 50.0, 2000, 500, 50, 50, 1, 60))
            ins("meta_breakdown_daily", rows)


def make_runner(url: str, log: list | None = None):
    def run(sql, limit):
        check_select(sql)
        if log is not None:
            log.append(sql)
        with psycopg.connect(url, options="-c default_transaction_read_only=on -c statement_timeout=30000") as c:
            c.read_only = True
            cur = c.execute(sql)
            cols = [d.name for d in cur.description]
            rows = [[rundb._clean(v) for v in r] for r in cur.fetchmany(limit + 1)]
        return {"columns": cols, "rows": rows[:limit]}
    return run
