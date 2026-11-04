"""Marketing metrics, channel Google: seeds the Upstack Google tables for a window, runs the real build, checks the endpoints."""
import datetime as dt

import psycopg
import pytest

from etl import upstack_google

from .conftest import DB_URL
from .helpers import THROUGH, get_ok

DAYS = [THROUGH - dt.timedelta(days=i) for i in (6, 5, 4, 3, 2, 1, 0)]
# (campaign id, name, status) and per-day channel numbers (spend, impressions, clicks, purchases, value)
CAMPS = [("g1", "Standard Shopping | All Products | US | tROAS 190% | Branded | Hot", "ENABLED", (100.0, 5000, 200, 4, 400.0)),
         ("g2", "Brand Search | US | Hot", "ENABLED", (50.0, 1000, 80, 2, 150.0)),
         ("g3", "PMax | Beef Liver Products | Broad", "PAUSED", (40.0, 3000, 60, 1, 60.0)),
         ("g4", "Prospecting | Top 3 Products | US | Cold", "PAUSED", (10.0, 500, 10, 0, 0.0))]
# utm campaign -> (nc, nc_rev, orders, rev) per day; one utm value is no Google campaign
ATTR = {CAMPS[0][1]: (3, 240.0, 5, 450.0), CAMPS[1][1]: (1, 80.0, 2, 160.0), CAMPS[2][1]: (0, 0.0, 1, 70.0), "(not set)": (1, 50.0, 1, 50.0)}


@pytest.fixture(scope="module")
def google(fx):
    with psycopg.connect(DB_URL) as conn:
        for t in ("upstack_google_campaigns", "upstack_google_channel_daily", "upstack_google_attr_daily", "upstack_google_days", "google_daily"):
            conn.execute(f"TRUNCATE {t}")
        for cid, name, status, vals in CAMPS:
            conn.execute("INSERT INTO upstack_google_campaigns (campaign_id, name, status, bid_strategy, campaign_type) VALUES (%s,%s,%s,'X',%s)",
                         (cid, name, status, upstack_google.campaign_type(name)))
            for d in DAYS:
                conn.execute("INSERT INTO upstack_google_channel_daily (day, campaign_id, spend, impressions, clicks, purchases, purchases_value) VALUES (%s,%s,%s,%s,%s,%s,%s)", (d, cid, *vals))
        for d in DAYS:
            conn.execute("INSERT INTO upstack_google_days (day) VALUES (%s)", (d,))
            for camp, v in ATTR.items():
                conn.execute("INSERT INTO upstack_google_attr_daily (day, campaign, nc, nc_rev, orders, rev) VALUES (%s,%s,%s,%s,%s,%s)", (d, camp, *v))
        stats = upstack_google.build(conn)
        conn.commit()
    return stats


W = "from=%s&to=%s" % (DAYS[0], DAYS[-1])
N = len(DAYS)


def test_build_ties_to_the_raw_feeds(google, pg):
    assert google["spend"] == N * 200.0 and google["reported_purchases"] == N * 7 and google["orders"] == N * 9 and google["nc"] == N * 5
    assert google["unmapped_orders"] == N * 1 and google["unmapped_nc"] == N * 1                                  # the (not set) utm value
    assert pg.execute("SELECT count(*) FROM google_daily WHERE NOT in_feed").fetchone()[0] == N
    assert pg.execute("SELECT value FROM app_meta WHERE key = 'google_through'").fetchone()[0] == THROUGH.isoformat()


def test_types_rollup_google_reported(google, client):
    d = get_ok(client, f"/api/google/rollup?group=type&{W}")
    by = {r["key"]: r["m"] for r in d["rows"]}
    assert set(by) == {"Shopping", "Search", "Performance Max", "Other"}                                          # Demand Gen has no campaign here; the (not set) row is typed Other
    assert by["Shopping"]["spend"] == N * 100.0 and by["Shopping"]["purchases"] == N * 4 and by["Shopping"]["revenue"] == N * 400.0
    assert by["Shopping"]["roas"] == 4.0 and by["Shopping"]["cpa"] == 25.0 and by["Shopping"]["ctr"] == pytest.approx(0.04) and by["Shopping"]["cpc"] == 0.5
    assert d["total"]["m"]["spend"] == N * 200.0 and d["total"]["m"]["link_clicks"] == N * 350
    assert d["window_info"]["source"]["key"] == "meta" and d["window_info"]["source"]["label"] == "Google" and d["window_info"]["source"]["labels"]["revenue"] == "Conversion value"
    assert round(sum(r["m"]["spend_share"] for r in d["rows"]), 6) == 1


@pytest.mark.parametrize("basis,want_p,want_v", [("nc", 5, 370.0), ("all", 9, 730.0)])
def test_upstack_source_swaps_purchases_and_revenue_and_keeps_google_spend(google, client, basis, want_p, want_v):
    d = get_ok(client, f"/api/google/rollup?group=type&{W}&source=upstack&basis={basis}")
    t = d["total"]["m"]
    assert t["spend"] == N * 200.0 and t["link_clicks"] == N * 350 and t["impressions"] == N * 9500                 # spend, clicks, impressions: Google's, unchanged
    assert t["purchases"] == N * want_p and t["revenue"] == N * want_v
    src = d["window_info"]["source"]
    assert src["key"] == "upstack" and src["basis"] == basis and "Google Ads source" in src["model"] and src["labels"]["purchases"] == ("New customers" if basis == "nc" else "Orders")


def test_campaign_rollup_filters_by_type_and_names_the_unmapped_utm_row(google, client):
    d = get_ok(client, f"/api/google/rollup?group=campaign&{W}&source=upstack&basis=all")
    names = {r["label"]: r for r in d["rows"]}
    assert "(utm campaign not in the Google feed)" in names and names["(utm campaign not in the Google feed)"]["in_feed"] is False
    assert names["(utm campaign not in the Google feed)"]["utm_campaign"] == "(not set)" and names["(utm campaign not in the Google feed)"]["m"]["purchases"] == N * 1
    assert round(sum(r["m"]["purchases"] for r in d["rows"]), 2) == d["total"]["m"]["purchases"] == N * 9
    one = get_ok(client, f"/api/google/rollup?group=campaign&type=Search&{W}")
    assert [r["label"] for r in one["rows"]] == ["Brand Search | US | Hot"] and one["rows"][0]["status"] == "ENABLED"


def test_compare_prev_and_window_presets(google, client):
    d = get_ok(client, f"/api/google/rollup?group=type&from={DAYS[4]}&to={DAYS[-1]}&compare=prev")        # last 3 days vs the 3 before them (both inside the data)
    t = d["total"]["m"]
    assert d["compare_info"] is not None and t["cmp"]["spend"] == 3 * 200.0 and t["delta"]["spend"] == 0 and t["delta"]["spend_pct"] == 0
    clamped = get_ok(client, f"/api/google/rollup?group=type&from={DAYS[3]}&to={DAYS[-1]}&compare=prev")["total"]["m"]
    assert clamped["cmp"]["spend"] == 3 * 200.0                                                            # B ran off the start of the data: cut to the days held
    assert get_ok(client, "/api/google/rollup?group=type&window=7d")["total"]["m"]["spend"] == N * 200.0
    assert get_ok(client, "/api/google/rollup?group=type")["window_info"]["kind"] == "launch"


def test_trend_day_week_and_scope(google, client):
    d = get_ok(client, f"/api/google/trend?{W}")
    assert len(d["points"]) == N and all(p["spend"] == 200.0 for p in d["points"]) and d["points"][0]["roas"] == pytest.approx(3.05)
    wk = get_ok(client, f"/api/google/trend?{W}&grain=week")
    assert round(sum(p["spend"] for p in wk["points"]), 2) == N * 200.0 and any(p["partial"] for p in wk["points"])
    sc = get_ok(client, f"/api/google/trend?{W}&type=Shopping&source=upstack&basis=nc")
    assert sc["points"][0]["spend"] == 100.0 and sc["points"][0]["purchases"] == 3 and sc["window_info"]["source"]["key"] == "upstack"
    cp = get_ok(client, f"/api/google/trend?from={DAYS[4]}&to={DAYS[-1]}&compare=prev")
    assert len(cp["points"]) == 3 and len(cp["cmp_points"]) == 3 and cp["cmp_points"][0]["t"] == DAYS[1].isoformat()


def test_validation_and_no_leak_into_the_meta_pages(google, client):
    assert client.get("/api/google/rollup?group=nope").status_code == 400
    assert client.get("/api/google/rollup?group=type&type=Banner").status_code == 400
    assert client.get("/api/google/trend?grain=year").status_code == 400
    assert client.get("/api/google/rollup?group=type&source=bogus").status_code == 400
    meta = get_ok(client, f"/api/products?envelope=1&{W}")
    assert meta["window_info"]["source"]["key"] == "meta" and "channel" not in meta["window_info"]


def test_status_reports_google(google, client):
    g = get_ok(client, "/api/status")["sources"]["google"]
    assert g["available"] is True and g["through"] == THROUGH.isoformat() and g["upstack_first_day"] == DAYS[0].isoformat()


def test_overview_google_rows_are_the_rollup_totals_in_every_basis(google, client):
    d = get_ok(client, f"/api/platforms/overview?{W}")
    g = next(p for p in d["platforms"] if p["key"] == "google")
    by = {r["basis"]: r for r in g["rows"]}
    assert set(by) == {"reported", "nc", "all"} and g["note"] is None
    for basis, qsx in (("reported", ""), ("nc", "&source=upstack&basis=nc"), ("all", "&source=upstack&basis=all")):
        want = get_ok(client, f"/api/google/rollup?group=type&{W}{qsx}")["total"]["m"]
        for k in ("spend", "purchases", "revenue", "impressions", "link_clicks", "roas", "cpa"):
            assert by[basis]["m"][k] == want[k], (basis, k)
    assert by["reported"]["label"] == "Google's own reporting" and by["nc"]["labels"]["purchases"] == "New customers" and by["all"]["labels"]["purchases"] == "Orders"
    assert by["nc"]["window"]["from"] == str(DAYS[0]) and by["nc"]["window"]["to"] == str(DAYS[-1])
    # spend is Google's in all three rows
    assert by["reported"]["m"]["spend"] == by["nc"]["m"]["spend"] == by["all"]["m"]["spend"] == N * 200.0


def test_google_trend_month_grain_adds_up(google, client):
    day = get_ok(client, f"/api/google/trend?grain=day&{W}")["points"]
    month = get_ok(client, f"/api/google/trend?grain=month&{W}")["points"]
    assert round(sum(p["spend"] for p in month), 2) == round(sum(p["spend"] for p in day), 2) == N * 200.0
    assert all(p["t"].endswith("-01") for p in month) and len(month) >= 1


# ---- All platforms: the one table of every campaign and its trend

def all_camps(client, extra="", w=W):
    return get_ok(client, f"/api/campaigns/all?{w}{extra}")


def test_all_campaigns_rows_subtotals_and_totals(google, client):
    d = all_camps(client)
    meta = get_ok(client, f"/api/campaigns?{W}")["total"]
    g = get_ok(client, f"/api/google/rollup?group=campaign&{W}")["total"]["m"]
    assert set(d["subtotals"]) == {"meta", "google"} and d["numbers"] == "own"
    sm, sg = d["subtotals"]["meta"]["m"], d["subtotals"]["google"]["m"]
    assert sm["spend"] == meta["spend"] and sm["purchases"] == meta["purchases"] and sm["revenue"] == meta["revenue"]          # the Meta part is the Meta Campaigns total
    assert sg["spend"] == g["spend"] and sg["purchases"] == g["purchases"] and sg["revenue"] == g["revenue"]
    plats = {r["platform"] for r in d["rows"]}
    assert plats == {"meta", "google"}
    assert round(sum(r["m"]["spend"] for r in d["rows"]), 2) == pytest.approx(d["total"]["spend"], abs=0.02) == pytest.approx(sm["spend"] + sg["spend"], abs=0.02)
    assert [r["m"]["spend"] for r in d["rows"]] == sorted((r["m"]["spend"] for r in d["rows"]), reverse=True)                       # sorted by spend
    assert round(sum(r["m"]["spend_share"] for r in d["rows"]), 3) == pytest.approx(1.0, abs=0.003)                                # shares are of the combined spend
    t = d["total"]
    assert t["additive"] is False and t["link_clicks"] == (sm["link_clicks"] or 0) + sg["link_clicks"]                              # spend, impressions, clicks are added
    assert "purchases" not in t and "revenue" not in t and "roas" not in t                                                        # purchases and revenue are not added across platforms
    by = {r["platform"]: r for r in d["rows"]}
    assert by["meta"]["basis"] == "Meta's own reporting" and by["google"]["basis"] == "Google's own reporting"
    assert by["meta"]["group_kind"] == "product" and by["google"]["group_kind"] == "type" and by["google"]["group"] in ("Shopping", "Search", "Performance Max", "Other")


def test_all_campaigns_single_platform_is_that_platforms_table(google, client):
    only_m = all_camps(client, "&only=meta")
    meta = get_ok(client, f"/api/campaigns?{W}")["total"]
    assert set(only_m["subtotals"]) == {"meta"} and only_m["total"]["additive"] is True and {r["platform"] for r in only_m["rows"]} == {"meta"}
    assert only_m["total"]["spend"] == meta["spend"] and only_m["subtotals"]["meta"]["m"]["purchases"] == meta["purchases"]
    only_g = all_camps(client, "&only=google")
    assert set(only_g["subtotals"]) == {"google"} and {r["platform"] for r in only_g["rows"]} == {"google"}
    assert only_g["total"]["spend"] == N * 200.0


def test_all_campaigns_product_and_type_filters(google, client):
    vb = all_camps(client, "&product=vita-bull")
    assert {r["platform"] for r in vb["rows"]} == {"meta"} and set(vb["subtotals"]) == {"meta"}                                     # Google has no products
    assert vb["subtotals"]["meta"]["m"]["spend"] == get_ok(client, f"/api/campaigns?{W}&product=vita-bull")["total"]["spend"]
    ty = all_camps(client, "&type=Search")
    assert {r["platform"] for r in ty["rows"]} == {"google"} and {r["group"] for r in ty["rows"]} == {"Search"}
    assert client.get(f"/api/campaigns/all?{W}&product=vita-bull&type=Search").status_code == 400
    assert client.get(f"/api/campaigns/all?{W}&only=tiktok").status_code == 400
    assert client.get(f"/api/campaigns/all?{W}&numbers=bad").status_code == 400
    chips = all_camps(client)
    assert {p["key"] for p in chips["platforms"]} == {"meta", "google"} and chips["products"] and {t["key"] for t in chips["types"]} >= {"Shopping", "Search"}


def test_all_campaigns_since_launch_is_the_days_every_platform_has(google, client):
    d = get_ok(client, "/api/campaigns/all?window=launch")
    w = d["platform_windows"]
    assert w["meta"]["from"] == w["google"]["from"] == str(DAYS[0]) and w["meta"]["to"] == w["google"]["to"] == str(DAYS[-1])
    assert "every platform has data" in d["window_info"]["note"]
    o = get_ok(client, "/api/platforms/overview?window=launch")
    for pf in o["platforms"]:
        assert all(r["window"]["from"] == str(DAYS[0]) for r in pf["rows"] if r["basis"] == "reported"), pf["key"]


def test_platform_trend_adds_up_to_the_all_campaigns_table(google, client):
    d = all_camps(client)
    for grain in ("day", "week", "month"):
        t = get_ok(client, f"/api/platforms/trend?{W}&grain={grain}")
        g = {x["key"]: x for x in t["groups"]}
        assert set(g) == {"meta", "google"} and t["numbers"] == "own" and t["grain"] == grain
        for k in ("meta", "google"):
            assert round(sum((pt or {}).get("spend", 0) or 0 for pt in g[k]["points"]), 2) == pytest.approx(d["subtotals"][k]["m"]["spend"], abs=0.02), (grain, k)
            assert round(sum((pt or {}).get("purchases", 0) or 0 for pt in g[k]["points"]), 1) == pytest.approx(d["subtotals"][k]["m"]["purchases"], abs=0.11), (grain, k)
            assert len(g[k]["points"]) == len(t["labels"])
        assert round(sum(pt["spend"] or 0 for pt in t["points"]), 2) == pytest.approx(d["total"]["spend"], abs=0.02)                  # the combined series is spend only
        assert all(pt.get("purchases") is None for pt in t["points"])
        assert t["labels"] == sorted(t["labels"])
    one = get_ok(client, f"/api/platforms/trend?{W}&only=google")
    assert [x["key"] for x in one["groups"]] == ["google"]
    vb = get_ok(client, f"/api/platforms/trend?{W}&product=vita-bull")
    assert [x["key"] for x in vb["groups"]] == ["meta"] and round(sum(pt["spend"] for pt in vb["groups"][0]["points"]), 2) == pytest.approx(
        get_ok(client, f"/api/campaigns?{W}&product=vita-bull")["total"]["spend"], abs=0.02)
    assert client.get(f"/api/platforms/trend?{W}&grain=year").status_code == 400


def test_platform_trend_on_each_basis_ties_to_the_overview(google, client):
    o = get_ok(client, f"/api/platforms/overview?{W}")
    for pf in o["platforms"]:
        for r in pf["rows"]:
            t = get_ok(client, f"/api/platforms/trend?{W}&numbers={r['basis'] if r['basis'] != 'reported' else 'own'}&only={pf['key']}")
            pts = t["groups"][0]["points"]
            assert round(sum((p or {}).get("spend", 0) or 0 for p in pts), 2) == pytest.approx(r["m"]["spend"], abs=0.02), (pf["key"], r["basis"])
            assert round(sum((p or {}).get("purchases", 0) or 0 for p in pts), 1) == pytest.approx(r["m"]["purchases"], abs=0.11), (pf["key"], r["basis"])
            assert round(sum((p or {}).get("revenue", 0) or 0 for p in pts), 2) == pytest.approx(r["m"]["revenue"], abs=0.02), (pf["key"], r["basis"])
