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
    assert client.get("/api/google/trend?grain=month").status_code == 400
    assert client.get("/api/google/rollup?group=type&source=bogus").status_code == 400
    meta = get_ok(client, f"/api/products?envelope=1&{W}")
    assert meta["window_info"]["source"]["key"] == "meta" and "channel" not in meta["window_info"]


def test_status_reports_google(google, client):
    g = get_ok(client, "/api/status")["sources"]["google"]
    assert g["available"] is True and g["through"] == THROUGH.isoformat() and g["upstack_first_day"] == DAYS[0].isoformat()
