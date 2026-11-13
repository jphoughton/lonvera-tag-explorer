"""Amazon pages (/api/amazon/*, app/amazon.py): seeds the Amazon tables of migration 250 and checks every route against hand-computed numbers.

Seed facts (per day unless said): ORDERS Oct 25 - Nov 7 (14 days) on three ASINs; ADS Oct 31 - Nov 7 (8 days, three campaigns, two ASIN rows); TRAFFIC Oct 25 - Nov 5 (12 days);
SEARCH eight weeks (Sundays Sep 13 .. Nov 1). Windows are given as from/to so they do not depend on today's date."""
import datetime as dt

import psycopg
import pytest

from app.respcache import CACHEABLE

from .conftest import DB_URL, PASSWORD
from .helpers import get_ok

D = dt.date
ORD_DAYS = [D(2026, 10, 25) + dt.timedelta(days=i) for i in range(14)]            # Oct 25 .. Nov 7
ADS_DAYS = [D(2026, 10, 31) + dt.timedelta(days=i) for i in range(8)]             # Oct 31 .. Nov 7
TRF_DAYS = [D(2026, 10, 25) + dt.timedelta(days=i) for i in range(12)]            # Oct 25 .. Nov 5
WEEKS = [D(2026, 9, 13) + dt.timedelta(days=7 * i) for i in range(8)]             # Sundays Sep 13 .. Nov 1
TABLES = ("amazon_asin_products", "amazon_ads_campaign_daily", "amazon_ads_asin_daily", "amazon_sqp_weekly", "amazon_sqp_monthly", "amazon_orders_asin_daily", "amazon_traffic_daily",
          "amazon_market_baskets", "amazon_repeat_purchases", "amazon_settlement_fees_daily")
W_ADS = "from=2026-10-31&to=2026-11-07"                       # the 8 ads days; orders cover them all, traffic only the first 6
W_ALL = "window=launch"
W_SQP = "from=2026-10-11&to=2026-11-07"                       # full weeks starting Oct 11, 18, 25, Nov 1; the prior four start Sep 13 .. Oct 4
# (asin, channel, product, orders, units, revenue, nc_orders, nc_revenue)
ORDER_ROWS = [("A1", "Amazon.com", "vita-bull", 3, 4, 100.0, 1, 30.0), ("A1", "Amazon.com B2B", "vita-bull", 1, 1, 10.0, 0, 0.0), ("A2", "Amazon.com", "beef-liver", 2, 2, 60.0, 1, 30.0),
              ("A3", "Amazon.com", None, 1, 1, 20.0, 0, 0.0)]
REV_DAY, ORD_DAY, UNITS_DAY, NCREV_DAY, NCORD_DAY = 190.0, 7, 8, 60.0, 2
# (campaign, ad type, product, spend, impressions, clicks, sales_14d, purchases_14d, sales, purchases, sales_reported)
ADS_ROWS = [("c1", "sp", "vita-bull", 10.0, 1000, 50, 40.0, 2, None, None, 40.0), ("c2", "sb", None, 5.0, 500, 10, None, None, 8.0, 1, 8.0),
            ("c3", "sd", "beef-liver", 2.0, 200, 4, None, None, 3.0, 1, 3.0)]
SPEND_DAY, SALES_DAY, ORDERS_AD_DAY = 17.0, 51.0, 4


@pytest.fixture(scope="module")
def baseline(app, fx):
    """The numbers of the existing pages BEFORE any Amazon row exists."""
    with psycopg.connect(DB_URL) as c:
        for t in TABLES:
            c.execute(f"TRUNCATE {t}")
        c.commit()
    cl = app.test_client()
    assert cl.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return cl, {u: get_ok(cl, u) for u in URLS_UNCHANGED}


URLS_UNCHANGED = ["/api/platforms/kpis?window=30d", "/api/platforms/overview?window=30d", "/api/platforms/trend?window=30d&grain=week", "/api/campaigns/all?window=30d",
                  "/api/campaigns?window=30d", "/api/products?envelope=1&window=30d"]


def _sqp(asin, product, key, wk, i, vol, mt, ours, branded=False, text=None, stored=99.0):
    """one search-query row; mt = market (impressions, clicks, cart adds, purchases), ours = ours (same order); stored shares are deliberately wrong so a test proves they are recomputed"""
    return dict(period_start=wk, period_end=wk + dt.timedelta(days=6), marketplace_id="ATVPDKIKX0DER", marketplace="US", asin=asin, query_key=key, search_query=text or key, product_slug=product,
                product_name=product, is_branded=branded, search_query_volume=vol, total_impressions=mt[0], asin_impressions=ours[0], asin_impression_share=stored,
                total_clicks=mt[1], asin_clicks=ours[1], asin_click_share=stored, total_cart_adds=mt[2], asin_cart_adds=ours[2], asin_cart_add_share=stored,
                total_purchases=mt[3], asin_purchases=ours[3], asin_purchase_share=stored)


@pytest.fixture(scope="module")
def amazon(baseline):
    cl, _ = baseline
    with psycopg.connect(DB_URL) as c:
        def ins(table, **cols):
            c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(cols.values()))
        for asin, slug, name in (("A1", "vita-bull", "Vita Bull 60 ct"), ("A2", "beef-liver", "Beef Liver 90 ct"), ("A2B", "beef-liver", "Beef Liver 180 ct"), ("A3", None, "Mystery bundle")):
            ins("amazon_asin_products", asin=asin, sku="SKU-" + asin, product_slug=slug, product_name=name, friendly_name=name, refreshed_on=D(2026, 11, 7))
        for d in ORD_DAYS:
            for asin, ch, slug, o, u, rev, nco, ncr in ORDER_ROWS:
                ins("amazon_orders_asin_daily", day=d, asin=asin, sales_channel=ch, product_slug=slug, product_name=slug, orders=o, units=u, revenue=rev, nc_orders=nco, nc_revenue=ncr)
        for d in ADS_DAYS:
            for cid, typ, slug, sp, imp, clk, s14, p14, sales, pur, rep in ADS_ROWS:
                ins("amazon_ads_campaign_daily", day=d, marketplace="US", campaign_id=cid, ad_type=typ, api_version="v3", campaign_name=f"Campaign {cid} {slug or 'all products'}", campaign_status="ENABLED",
                    product_slug=slug, spend=sp, impressions=imp, clicks=clk, sales_14d=s14, purchases_14d=p14, sales=sales, purchases=pur, sales_reported=rep)
            ins("amazon_ads_asin_daily", day=d, marketplace="US", campaign_id="c1", ad_type="sp", asin="A1", product_slug="vita-bull", spend=10.0, impressions=1000, clicks=50, sales_14d=40.0,
                purchases_14d=2, sales_reported=40.0)
            ins("amazon_ads_asin_daily", day=d, marketplace="US", campaign_id="c3", ad_type="sd", asin="A2", product_slug="beef-liver", spend=2.0, impressions=200, clicks=4, sales=3.0,
                purchases=1, sales_reported=3.0)
        for d in TRF_DAYS:
            ins("amazon_traffic_daily", day=d, marketplace_id="ATVPDKIKX0DER", sessions=100, page_views=150)
        # search: "beef liver capsules" is shown for two Beef Liver ASINs and one Vita Bull ASIN every week (the market numbers repeat on each row)
        for i, wk in enumerate(WEEKS):
            vol, mt = 10000 + 100 * i, (200000, 5000, 1000, 400)
            for row in (_sqp("A2", "beef-liver", "beef liver capsules", wk, i, vol, mt, (20000, 500, 100, 40), text="Beef Liver Capsules"),
                        _sqp("A2B", "beef-liver", "beef liver capsules", wk, i, vol, mt, (10000, 250, 50, 20), text="Beef Liver Capsules"),
                        _sqp("A1", "vita-bull", "beef liver capsules", wk, i, vol, mt, (4000, 100, 20, 8), text="Beef Liver Capsules"),
                        _sqp("A2", "beef-liver", "lonvera beef liver", wk, i, 3000, (30000, 2000, 500, 300), (15000, 1000, 250, 150), branded=True),
                        _sqp("A1", "vita-bull", "vitamin bull", wk, i, 500, (5000, 200, 40, 20), (1000, 40, 8, 4))):
                ins("amazon_sqp_weekly", **row)
            if i in (2, 3, 4, 6):                                                      # a term that drops out of the top list in the other weeks
                ins("amazon_sqp_weekly", **_sqp("A2", "beef-liver", "liver pills", wk, i, 800 + i, (8000, 400, 80, 40), (800, 40, 8, 4)))
        for d in (D(2026, 10, 3), D(2026, 11, 3), D(2026, 11, 20)):
            for desc, amt in (("Commission", -30.0), ("FBA per unit fulfillment fee", -50.0), ("Cost of Advertising", -20.0)):
                ins("amazon_settlement_fees_daily", day=d, marketplace_name="Amazon.com", transaction_type="Order" if desc != "Cost of Advertising" else "ServiceFee", amount_type="ItemFees" if desc != "Cost of Advertising" else "Other",
                    amount_description=desc, lines=3, amount=amt, amount_usd=amt, currency="USD")
        c.commit()
    yield cl
    with psycopg.connect(DB_URL) as c:
        for t in TABLES:
            c.execute(f"TRUNCATE {t}")
        c.commit()


def a(client, path, qs=""):
    return get_ok(client, f"/api/amazon/{path}?{qs}")


# ---------------------------------------------------------------------------------------------------------------------------- status, routes
def test_status_reports_amazon_apart_from_the_spend_platforms(amazon):
    s = get_ok(amazon, "/api/status")["sources"]
    assert s["amazon"]["available"] is True and s["amazon"]["through"] == "2026-11-07" and s["amazon"]["first_day"] == "2026-10-25"
    assert {p["key"] for p in s["platforms"]} == {"meta", "google"}                     # Amazon is not a platform of the All views


def test_routes_need_a_session(anon):
    for path in ("overview", "trend", "campaigns", "campaign-trend", "products", "search-terms", "search-terms/history", "fees"):
        assert anon.get(f"/api/amazon/{path}").status_code == 401


def test_amazon_routes_are_cached_like_google():
    for path in ("overview", "trend", "campaigns", "campaign-trend", "products", "search-terms", "search-terms/history", "fees"):
        assert CACHEABLE.match(f"/api/amazon/{path}"), path


def test_existing_pages_are_unchanged_by_amazon_rows(amazon, baseline):
    cl, before = baseline
    for url, was in before.items():
        assert get_ok(cl, url) == was, url
    st = get_ok(cl, "/api/status")["sources"]["platforms"]
    assert [p["key"] for p in st] == ["meta", "google"]


# ---------------------------------------------------------------------------------------------------------------------------- overview
def test_overview_tiles_are_sums_and_ratios_of_sums(amazon):
    d = a(amazon, "overview", W_ADS)
    t, n = d["tiles"], len(ADS_DAYS)
    assert d["loaded"] is True and d["channel"] == "amazon" and d["window_info"]["channel"] == "amazon"
    assert t["revenue"] == n * REV_DAY and t["orders"] == n * ORD_DAY and t["units"] == n * UNITS_DAY and t["nc_revenue"] == n * NCREV_DAY and t["nc_orders"] == n * NCORD_DAY
    assert t["ad_spend"] == n * SPEND_DAY and t["ad_sales"] == n * SALES_DAY and t["ad_return"] == pytest.approx(SALES_DAY / SPEND_DAY, abs=1e-4)
    assert t["ad_share_of_revenue"] == pytest.approx(SPEND_DAY / REV_DAY, abs=1e-4)
    # traffic covers only the first 6 of the 8 days: sessions are those days', conversion divides the orders of those same days
    assert t["sessions"] == 6 * 100 and t["conversion"] == pytest.approx(6 * ORD_DAY / (6 * 100), abs=1e-5)
    assert any("Sessions and conversion cover" in x for x in d["notes"])


def test_overview_by_product_and_ad_type(amazon):
    d = a(amazon, "overview", W_ADS)
    bp = {r["key"]: r for r in d["by_product"]}
    n = len(ADS_DAYS)
    assert [r["key"] for r in d["by_product"]][:4] == ["vita-bull", "beef-liver", "boneflex", "vita-belle"] and bp["none"]["label"] == "No single product"
    assert bp["vita-bull"]["revenue"] == n * 110.0 and bp["vita-bull"]["orders"] == n * 4 and bp["vita-bull"]["spend"] == n * 10.0 and bp["vita-bull"]["ad_return"] == 4.0
    assert bp["beef-liver"]["revenue"] == n * 60.0 and bp["beef-liver"]["spend"] == n * 2.0 and bp["beef-liver"]["ad_sales"] == n * 3.0
    assert bp["none"]["revenue"] == n * 20.0 and bp["none"]["spend"] == n * 5.0                 # unmapped ASIN revenue and the all-products campaign spend
    assert bp["boneflex"]["revenue"] == 0.0 and bp["boneflex"]["spend"] == 0.0 and bp["boneflex"]["ad_return"] is None     # no activity is 0, but a ratio of 0 / 0 is blank
    assert round(sum(r["revenue"] for r in d["by_product"]), 2) == d["by_product_total"]["revenue"] == n * REV_DAY
    assert round(sum(r["spend"] for r in d["by_product"]), 2) == d["by_product_total"]["spend"]
    bt = {r["key"]: r for r in d["by_ad_type"]}
    assert list(bt) == ["sp", "sb", "sbv", "sd"] and bt["sp"]["label"] == "Sponsored Products" and bt["sbv"]["label"] == "Sponsored Brands video"
    assert bt["sp"]["spend"] == n * 10.0 and bt["sp"]["ad_sales"] == n * 40.0 and bt["sp"]["ad_orders"] == n * 2 and bt["sp"]["ctr"] == pytest.approx(0.05) and bt["sp"]["cpc"] == 0.2
    assert bt["sp"]["cost_per_order"] == 5.0 and bt["sb"]["ad_sales"] == n * 8.0 and bt["sb"]["ad_orders"] == n * 1 and bt["sbv"]["spend"] == 0.0 and bt["sbv"]["ad_return"] is None
    assert bt["sp"]["attribution"] == "14-day sales and orders" and bt["sb"]["attribution"] == "as Amazon reports"
    assert sum(r["spend_share"] for r in d["by_ad_type"] if r["spend_share"] is not None) == pytest.approx(1.0, abs=1e-3)


def test_overview_before_ads_started_blanks_the_ad_figures_and_uses_aligned_revenue(amazon):
    d = a(amazon, "overview", W_ALL)                                                           # Oct 25 .. Nov 7: ads only cover the last 8 days
    t = d["tiles"]
    assert t["revenue"] == 14 * REV_DAY and t["ad_spend"] == 8 * SPEND_DAY
    assert t["ad_share_of_revenue"] == pytest.approx(SPEND_DAY / REV_DAY, abs=1e-4)           # spend / revenue of the 8 days ads cover, not of all 14
    assert any("Ad figures cover Oct 31, 2026 to Nov 7, 2026 only" in x for x in d["notes"])
    early = a(amazon, "overview", "from=2026-10-25&to=2026-10-30")
    assert early["tiles"]["ad_spend"] is None and early["tiles"]["ad_return"] is None and early["tiles"]["ad_share_of_revenue"] is None and early["tiles"]["revenue"] == 6 * REV_DAY
    assert all(r["spend"] is None for r in early["by_product"]) and all(r["spend"] is None for r in early["by_ad_type"])


@pytest.mark.parametrize("grain,n", [("day", 8), ("week", 2), ("month", 2)])
def test_trend_buckets_sum_the_days(amazon, grain, n):
    d = a(amazon, "trend", f"{W_ADS}&grain={grain}")
    pts = d["points"]
    assert len(pts) == n and d["grain"] == grain
    assert round(sum(p["revenue"] for p in pts), 2) == 8 * REV_DAY and round(sum(p["ad_spend"] for p in pts), 2) == 8 * SPEND_DAY
    assert all(p["ad_return"] == pytest.approx(SALES_DAY / SPEND_DAY, abs=1e-4) for p in pts)
    if grain != "day":
        assert pts[0]["partial"] and pts[-1]["partial"]


def test_trend_rejects_a_bad_grain(amazon):
    assert amazon.get(f"/api/amazon/trend?{W_ADS}&grain=hour").status_code == 400


# ---------------------------------------------------------------------------------------------------------------------------- campaigns
def test_campaigns_rows_and_filters(amazon):
    d = a(amazon, "campaigns", W_ADS)
    n = len(ADS_DAYS)
    by = {r["campaign_id"]: r for r in d["rows"]}
    assert [r["campaign_id"] for r in d["rows"]] == ["c1", "c2", "c3"]                          # by spend, biggest first
    assert by["c1"]["ad_type"] == "sp" and by["c1"]["status"] == "enabled" and by["c1"]["product"] == "vita-bull" and by["c1"]["attribution"] == "14-day sales and orders"
    m = by["c1"]["m"]
    assert m["spend"] == n * 10.0 and m["impressions"] == n * 1000 and m["clicks"] == n * 50 and m["ctr"] == pytest.approx(0.05) and m["cpc"] == 0.2
    assert m["ad_orders"] == n * 2 and m["ad_sales"] == n * 40.0 and m["ad_return"] == 4.0 and m["cost_per_order"] == 5.0
    assert by["c2"]["m"]["ad_return"] == pytest.approx(1.6) and by["c2"]["product_label"] == "No single product" and by["c2"]["attribution"] == "as Amazon reports"
    assert d["total"]["m"]["spend"] == n * SPEND_DAY and d["total"]["m"]["ad_return"] == pytest.approx(SALES_DAY / SPEND_DAY, abs=1e-4)
    assert {f["key"] for f in d["facets"]["ad_types"]} == {"sp", "sb", "sd"} and "none" in {f["key"] for f in d["facets"]["products"]}
    only_sb = a(amazon, "campaigns", f"{W_ADS}&ad_type=sb")
    assert [r["campaign_id"] for r in only_sb["rows"]] == ["c2"] and only_sb["total"]["m"]["spend"] == n * 5.0
    assert [r["campaign_id"] for r in a(amazon, "campaigns", f"{W_ADS}&product=beef-liver")["rows"]] == ["c3"]
    assert [r["campaign_id"] for r in a(amazon, "campaigns", f"{W_ADS}&product=none")["rows"]] == ["c2"]
    assert [r["campaign_id"] for r in a(amazon, "campaigns", f"{W_ADS}&q=CAMPAIGN%20c3")["rows"]] == ["c3"]
    assert a(amazon, "campaigns", f"{W_ADS}&q=nothing")["rows"] == []
    assert amazon.get(f"/api/amazon/campaigns?{W_ADS}&ad_type=zz").status_code == 400
    assert amazon.get(f"/api/amazon/campaigns?{W_ADS}&product=zz").status_code == 400


def test_campaign_trend_is_one_campaigns_days(amazon):
    d = a(amazon, "campaign-trend", f"{W_ADS}&campaign_id=c1&grain=day")
    assert len(d["points"]) == 8 and all(p["spend"] == 10.0 and p["ad_return"] == 4.0 for p in d["points"])
    assert amazon.get(f"/api/amazon/campaign-trend?{W_ADS}").status_code == 400


# ---------------------------------------------------------------------------------------------------------------------------- products
def test_products_by_asin_with_ad_numbers_and_the_spend_that_has_no_product(amazon):
    d = a(amazon, "products", W_ADS)
    n = len(ADS_DAYS)
    by = {r["asin"]: r for r in d["rows"]}
    assert set(by) == {"A1", "A2", "A3"} and by["A1"]["name"] == "Vita Bull 60 ct" and by["A1"]["product"] == "vita-bull"
    assert by["A1"]["revenue"] == n * 110.0 and by["A1"]["units"] == n * 5 and by["A1"]["nc_revenue"] == n * 30.0
    assert by["A1"]["ad_spend"] == n * 10.0 and by["A1"]["ad_sales"] == n * 40.0 and by["A1"]["ad_return"] == 4.0 and by["A1"]["ad_share_of_revenue"] == pytest.approx(10.0 / 110.0, abs=1e-4)
    assert by["A3"]["ad_spend"] == 0.0 and by["A3"]["ad_return"] is None and by["A3"]["product_label"] == "No product"
    assert d["total"]["ad_spend"] == n * 12.0 and d["total"]["revenue"] == n * REV_DAY
    assert d["spend_without_product"] == n * 5.0                                                # the Sponsored Brands campaign has no ASIN grain
    assert "Sponsored Brands spend has no product grain" in d["note"]
    assert d["rows"][0]["asin"] == "A1"                                                         # biggest revenue first


# ---------------------------------------------------------------------------------------------------------------------------- search terms
def terms(client, qs=""):
    d = a(client, "search-terms", f"{W_SQP}&{qs}")
    return d, {r["key"]: r for r in d["rows"]}


def test_market_totals_take_the_max_and_ours_the_sum_across_asins(amazon):
    d, by = terms(amazon, "product=beef-liver")
    r = by["beef liver capsules"]
    # two Beef Liver ASIN rows per week repeat the market totals: volume 10,300..10,700 over the four weeks, not doubled
    assert r["weeks_present"] == 4 and r["weeks_in_range"] == 4 and r["volume_latest"] == 10700 and r["volume_latest_week"] == "2026-11-01"
    assert r["volume_avg"] == pytest.approx((10400 + 10500 + 10600 + 10700) / 4)
    assert r["our_clicks"] == 4 * 750 and r["our_purchases"] == 4 * 60 and r["our_impressions"] == 4 * 30000 and r["our_cart_adds"] == 4 * 150
    # shares are recomputed (our summed count / market total), not the stored 99 and not a sum of stored shares
    assert r["click_share"] == 15.0 and r["purchase_share"] == 15.0 and r["impression_share"] == 15.0 and r["cart_add_share"] == 15.0
    assert d["weeks"][0]["from"] == "2026-10-11" and d["weeks"][-1]["to"] == "2026-11-07" and d["prior_weeks"][0] == "2026-09-13"


def test_all_products_rolls_every_asin_into_one_share(amazon):
    _, by = terms(amazon)
    r = by["beef liver capsules"]
    assert r["click_share"] == 17.0 and r["our_clicks"] == 4 * 850 and r["purchase_share"] == pytest.approx(100 * 68 / 400, abs=0.01) and r["volume_latest"] == 10700
    _, vb = terms(amazon, "product=vita-bull")
    assert vb["beef liver capsules"]["click_share"] == 2.0 and set(vb) == {"beef liver capsules", "vitamin bull"}


def test_a_missing_week_is_a_gap_not_a_zero(amazon):
    d, by = terms(amazon, "product=beef-liver")
    r = by["liver pills"]
    # listed in the weeks starting Oct 11 and Oct 25 only (seed weeks i = 4 and 6), of the four weeks in the range
    assert r["weeks_present"] == 2 and r["weeks_in_range"] == 4 and r["volume_latest"] == 806 and r["volume_latest_week"] == "2026-10-25" and r["volume_avg"] == 805.0
    # the share is over the weeks it appeared (our 40 clicks of a market 400 each), never diluted by the weeks it was missing
    assert r["click_share"] == 10.0 and r["our_clicks"] == 80
    assert r["prior"] is not None and r["prior"]["weeks_present"] == 2                          # prior weeks Sep 13 .. Oct 4: listed in Sep 27 and Oct 4 only
    one = a(amazon, "search-terms", "from=2026-10-04&to=2026-10-10&product=beef-liver")
    assert "liver pills" in {r["key"] for r in one["rows"]} and one["weeks_in_range"] == 1


def test_change_vs_the_prior_equal_period(amazon):
    _, by = terms(amazon, "product=beef-liver")
    r = by["beef liver capsules"]
    assert r["prior"]["weeks_present"] == 4 and r["prior"]["volume_avg"] == pytest.approx((10000 + 10100 + 10200 + 10300) / 4)
    assert r["click_share_change"] == 0.0 and r["purchase_share_change"] == 0.0
    assert r["volume_change"] == pytest.approx((10550 - 10150) / 10150, abs=1e-4)
    gap = by["liver pills"]
    assert gap["prior"]["weeks_present"] == 2 and gap["prior"]["volume_avg"] == 802.5 and gap["click_share_change"] == 0.0 and gap["volume_change"] == pytest.approx((805 - 802.5) / 802.5, abs=1e-4)


def test_a_term_with_no_prior_weeks_has_no_change(amazon):
    d = a(amazon, "search-terms", "from=2026-09-13&to=2026-09-19&product=beef-liver")            # the first week held: no earlier week exists
    assert d["prior_available"] is False and d["note"] and all(r["prior"] is None and r["click_share_change"] is None for r in d["rows"])


def test_branded_filter_and_text_search(amazon):
    _, br = terms(amazon, "branded=branded")
    assert set(br) == {"lonvera beef liver"} and br["lonvera beef liver"]["branded"] is True and br["lonvera beef liver"]["click_share"] == 50.0
    _, nb = terms(amazon, "branded=nonbranded")
    assert "lonvera beef liver" not in nb and "beef liver capsules" in nb
    _, tx = terms(amazon, "q=%20Liver%20%20Pills")
    assert set(tx) == {"liver pills"}
    assert amazon.get(f"/api/amazon/search-terms?{W_SQP}&branded=maybe").status_code == 400
    assert amazon.get(f"/api/amazon/search-terms?{W_SQP}&product=zz").status_code == 400


def test_a_range_without_a_full_week_says_so(amazon):
    d = a(amazon, "search-terms", "from=2026-10-28&to=2026-11-03")
    assert d["rows"] == [] and d["weeks_in_range"] == 0 and "No full week" in d["note"]


def test_term_history_has_gaps_and_per_product_series(amazon):
    d = a(amazon, "search-terms/history", "term=%20Liver%20Pills%20")
    assert d["term"] == "liver pills" and len(d["weeks"]) == 8 and [v["volume"] for v in d["volume"]] == [None, None, 802, 803, 804, None, 806, None]
    assert [v["present"] for v in d["volume"]] == [False, False, True, True, True, False, True, False]
    s = {x["slug"]: x for x in d["series"]}
    assert set(s) == {"all", "beef-liver"}
    pts = s["beef-liver"]["points"]
    assert pts[0]["present"] is False and pts[0]["click_share"] is None and pts[0]["our_clicks"] is None and pts[2]["click_share"] == 10.0 and pts[2]["our_clicks"] == 40
    full = a(amazon, "search-terms/history", "term=beef+liver+capsules")
    assert d["weeks"] == full["weeks"] and {x["slug"] for x in full["series"]} == {"all", "beef-liver", "vita-bull"}
    sf = {x["slug"]: x for x in full["series"]}
    assert all(v["volume"] == 10000 + 100 * i for i, v in enumerate(full["volume"]))             # MAX across the three ASIN rows of a week
    assert sf["beef-liver"]["points"][0]["click_share"] == 15.0 and sf["vita-bull"]["points"][0]["click_share"] == 2.0 and sf["all"]["points"][0]["click_share"] == 17.0
    only = a(amazon, "search-terms/history", "term=beef+liver+capsules&product=vita-bull")
    assert [x["slug"] for x in only["series"]] == ["vita-bull"]
    assert amazon.get("/api/amazon/search-terms/history").status_code == 400


# ---------------------------------------------------------------------------------------------------------------------------- fees
def test_fees_by_type_and_month(amazon):
    d = a(amazon, "fees", "from=2026-10-01&to=2026-11-30")
    assert [m["month"] for m in d["months"]] == ["2026-10-01", "2026-11-01"]
    by = {r["label"]: r for r in d["rows"]}
    assert by["Commission"]["months"] == {"2026-10-01": -30.0, "2026-11-01": -60.0} and by["Commission"]["total"] == -90.0 and by["Commission"]["lines"] == 9      # Oct 3, Nov 3, Nov 20
    assert by["Cost of Advertising"]["transaction_type"] == "ServiceFee" and by["FBA per unit fulfillment fee"]["total"] == -150.0
    assert d["total"]["total"] == -300.0 and d["total"]["months"]["2026-10-01"] == -100.0 and d["total"]["months"]["2026-11-01"] == -200.0
    assert "not the Ads report spend" in d["note"]


# ---------------------------------------------------------------------------------------------------------------------------- windows and errors
def test_range_outside_the_data_is_a_400_like_the_other_pages(amazon):
    assert amazon.get("/api/amazon/overview?from=2025-01-01&to=2025-01-31").status_code == 400
    assert amazon.get("/api/amazon/overview?window=bogus").status_code == 400


def test_stage_source_and_compare_params_are_ignored(amazon):
    plain = a(amazon, "overview", W_ADS)
    odd = a(amazon, "overview", W_ADS + "&source=upstack&basis=all&stage=testing&compare=prev&active=1")
    assert odd["tiles"] == plain["tiles"] and odd["compare_info"] is None
