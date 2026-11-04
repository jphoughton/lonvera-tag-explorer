"""Source flip (Meta | Upstack): seeds Upstack attribution rows for fixture ads, runs the real build (etl.upstack_build.build) and checks the API.
Meta numbers must be untouched by the flip; Upstack numbers must conserve what was pulled (mapped + unmapped = raw) and share Meta's spend."""
import datetime as dt

import psycopg
import pytest

from etl import upstack_build

from . import fixture_data as F
from .conftest import DB_URL
from .helpers import THROUGH, get_ok

DAYS = [THROUGH - dt.timedelta(days=i) for i in (2, 1, 0)]
# (utm campaign, utm ad, nc, nc_rev, orders, rev) per day; names are the fixture's Meta campaign / ad names
RAW = [("VB_Scaling_US", "VB-Fam-A", 3, 240.0, 4, 340.0),
       ("VB_Scaling_US", "VB-Fam-B", 1, 80.0, 2, 150.0),
       ("VB_Testing_Cambium", "VB-Fam-D", 2, 150.0, 2, 150.0),
       ("BL_Scaling", "BL-Fam-1", 2, 130.0, 3, 210.0),
       ("(not set)", "(not set)", 1, 50.0, 1, 50.0),                    # unmapped: no identifiable ad
       ("VB_Scaling_US", "A Deleted Ad", 1, 60.0, 1, 60.0)]             # unmapped: ad name matches nothing


@pytest.fixture(scope="module")
def upstack(fx):
    with psycopg.connect(DB_URL) as conn:
        for t in ("upstack_attr_days", "upstack_attr_daily", "upstack_campaign_daily", "upstack_channel_daily"):
            conn.execute(f"TRUNCATE {t}")
        for d in DAYS:
            conn.execute("INSERT INTO upstack_attr_days (day) VALUES (%s)", (d,))
            for camp, ad, nc, ncr, o, r in RAW:
                conn.execute("INSERT INTO upstack_attr_daily (day, campaign, ad, nc, nc_rev, orders, rev) VALUES (%s,%s,%s,%s,%s,%s,%s)", (d, camp, ad, nc, ncr, o, r))
            conn.execute("INSERT INTO upstack_campaign_daily SELECT day, campaign, sum(nc), sum(nc_rev), sum(orders), sum(rev) FROM upstack_attr_daily WHERE day = %s GROUP BY 1, 2", (d,))
            conn.execute("INSERT INTO upstack_channel_daily (day, campaign_id, campaign_name, spend) SELECT day, campaign_id, 'x', sum(spend) FROM ad_daily WHERE day = %s AND campaign_id <> '' GROUP BY 1, 2", (d,))
        stats = upstack_build.build(conn)
        conn.commit()
    return stats


W = "from=%s&to=%s" % (DAYS[0], DAYS[-1])


def tot(client, extra="", path="/api/products?envelope=1"):
    return get_ok(client, f"{path}&{W}{extra}")


def test_build_conserves_what_was_pulled(upstack, pg):
    raw_orders = len(DAYS) * sum(r[4] for r in RAW)
    mapped = float(pg.execute("SELECT sum(purchases) FROM ad_daily_upstack").fetchone()[0])
    unm = float(pg.execute("SELECT sum(orders) FROM upstack_unmapped_daily").fetchone()[0])
    assert mapped + unm == raw_orders
    assert unm == len(DAYS) * 2                                              # the two junk rows, orders 1 + 1 per day
    nc = float(pg.execute("SELECT sum(nc_purchases) FROM ad_daily_upstack").fetchone()[0])
    assert nc == len(DAYS) * (3 + 1 + 2 + 2)
    # spend in the Upstack table is Meta's spend, every ad and day
    a = pg.execute("SELECT sum(spend) FROM ad_daily WHERE day = ANY(%s)", (DAYS,)).fetchone()[0]
    b = pg.execute("SELECT sum(spend) FROM ad_daily_upstack").fetchone()[0]
    assert a == b


def test_default_is_meta_and_flip_does_not_leak(upstack, client):
    base = tot(client)
    assert base["window_info"]["source"]["key"] == "meta"
    up = tot(client, "&source=upstack")
    assert up["window_info"]["source"]["key"] == "upstack" and up["window_info"]["source"]["basis"] == "nc"
    again = tot(client)                                                       # same thread, next request: back on Meta, byte for byte
    assert again == base
    assert up != base


def test_upstack_shares_meta_spend_and_counts_attribution(upstack, client):
    meta_t = tot(client)["total"]["m"]
    nc = tot(client, "&source=upstack&basis=nc")["total"]["m"]
    al = tot(client, "&source=upstack&basis=all")["total"]["m"]
    assert nc["spend"] == meta_t["spend"] == al["spend"]
    assert nc["impressions"] == meta_t["impressions"] and nc["link_clicks"] == meta_t["link_clicks"]
    assert al["purchases"] >= nc["purchases"] > 0
    assert nc["roas"] == pytest.approx(nc["revenue"] / nc["spend"], abs=0.01)
    assert nc["cpa"] == pytest.approx(nc["spend"] / nc["purchases"], abs=0.01)


def test_labels_and_statement_follow_the_source(upstack, client):
    nc = tot(client, "&source=upstack&basis=nc")["window_info"]["source"]
    assert nc["labels"] == {"purchases": "New customers", "revenue": "NC revenue", "roas": "NC ROAS", "cpa": "NC CPA"}
    assert "any-click" in nc["model"] and "30-day" in nc["model"] and "Spend, clicks and impressions are Meta's" in nc["summary"]
    al = tot(client, "&source=upstack&basis=all")["window_info"]["source"]
    assert al["labels"]["purchases"] == "Orders" and al["label"] == "Upstack · all orders"
    meta = tot(client)["window_info"]["source"]
    assert meta["labels"]["purchases"] == "Purchases" and meta["basis"] is None


def test_every_level_and_endpoint_reads_the_upstack_tables(upstack, client):
    q = f"{W}&source=upstack&basis=all"
    totals = get_ok(client, f"/api/products/vita-bull/rollup?level=cluster&{q}")
    assert round(sum(r["m"]["purchases"] for r in totals["rows"]), 2) == round(totals["total"]["m"]["purchases"], 2) > 0
    fams = get_ok(client, f"/api/products/vita-bull/families?{q}")
    assert round(sum(r["m"]["purchases"] for r in fams["rows"]), 2) == round(totals["total"]["m"]["purchases"], 2)
    tree = get_ok(client, f"/api/products/vita-bull/tree?root=cluster&depth=3&{q}")
    assert round(sum(r["m"]["purchases"] for r in tree["rows"] if r["level"] == 0), 2) == round(totals["total"]["m"]["purchases"], 2)
    camps = get_ok(client, f"/api/products/vita-bull/campaigns?{q}")
    assert camps["window_info"]["source"]["key"] == "upstack" and camps["total"]["purchases"] > 0
    ads = get_ok(client, f"/api/products/vita-bull/ads?{q}")
    assert ads["total"]["purchases"] > 0
    trend = get_ok(client, f"/api/products/vita-bull/trend?{q}")
    assert round(sum(p["purchases"] for p in trend["points"]), 2) == round(totals["total"]["m"]["purchases"], 2)
    unm = get_ok(client, f"/api/products/vita-bull/unmatched?{q}")
    assert unm["window_info"]["source"]["key"] == "upstack"


def test_compare_and_stage_filters_work_in_upstack_mode(upstack, client):
    d = get_ok(client, f"/api/products?envelope=1&window=7d&compare=prev&stage=testing&source=upstack")
    assert d["window_info"]["source"]["key"] == "upstack" and d["compare_info"] is not None


def test_since_launch_in_upstack_means_days_held(upstack, client):
    d = get_ok(client, "/api/products?envelope=1&source=upstack&basis=all")
    info = d["window_info"]
    assert info["kind"] == "launch" and "Upstack history from" in info["label"] and "since then" in info["note"]
    held = tot(client, "&source=upstack&basis=all")["total"]["m"]
    assert d["total"]["m"]["purchases"] == held["purchases"] and d["total"]["m"]["spend"] == held["spend"]


def test_source_parameters_are_validated(upstack, client):
    for bad in ("source=google", "source=upstack&basis=weekly", "basis=x"):
        r = client.get(f"/api/products?{bad}")
        assert r.status_code == 400, bad


def test_tieout(upstack, client, pg):
    d = get_ok(client, f"/api/source/tieout?{W}")
    assert d["days"] == 3 and d["mapped"]["spend"] == d["meta"]["spend"]
    assert d["mapped"]["orders"] + d["unmapped"]["orders"] == d["upstack_ads"]["orders"] == len(DAYS) * sum(r[4] for r in RAW)
    assert d["unmapped"]["nc"] == len(DAYS) * 2
    assert d["upstack_feed"]["spend_vs_meta_pct"] == pytest.approx(0, abs=0.001)
    assert {r["reason"] for r in d["unmapped"]["by_reason"]} == {"not_identified", "no_ad"}
    assert len(d["daily"]) == 3
    assert d["tags_pages"]["upstack_nc"]["spend"] == d["tags_pages"]["meta"]["spend"]


def test_status_reports_the_sources(upstack, client):
    s = get_ok(client, "/api/status")["sources"]
    assert s["default"] == "meta" and s["upstack"]["available"] is True and s["upstack"]["through"] == THROUGH.isoformat()


def test_coverage_follows_the_source(upstack, client):
    d = get_ok(client, f"/api/products/vita-bull/coverage?{W}&source=upstack&basis=all")
    assert d["window_info"]["source"]["key"] == "upstack" and d["available"] is True
    meta = get_ok(client, f"/api/products/vita-bull/coverage?{W}")
    assert {r["key"] for r in d["rows"] if r["state"] == "no_ads"} == {r["key"] for r in meta["rows"] if r["state"] == "no_ads"}      # no ads: never source-dependent


# ---- platform level: the all-products Campaigns view and the overview follow the flip like every other page

@pytest.mark.parametrize("basis", ["nc", "all"])
def test_platform_campaigns_total_ties_to_the_products_table_in_upstack_mode(upstack, client, basis):
    x = f"&source=upstack&basis={basis}"
    d = get_ok(client, f"/api/campaigns?{W}{x}")
    all_ = tot(client, x)["total_all"]["m"]
    assert d["total"]["spend"] == pytest.approx(all_["spend"], abs=0.011) and d["total"]["purchases"] == pytest.approx(all_["purchases"], abs=0.011)
    assert d["total"]["revenue"] == pytest.approx(all_["revenue"], abs=0.011)
    assert d["window_info"]["source"]["key"] == "upstack" and d["window_info"]["source"]["basis"] == basis
    meta_d = get_ok(client, f"/api/campaigns?{W}")
    assert d["total"]["spend"] == meta_d["total"]["spend"]                                       # spend is Meta's in both modes
    assert get_ok(client, f"/api/campaigns?{W}") == meta_d                                       # and the flip does not leak into the next request


def test_overview_meta_rows_follow_the_basis(upstack, client):
    d = get_ok(client, f"/api/platforms/overview?{W}")
    meta = next(p for p in d["platforms"] if p["key"] == "meta")
    by = {r["basis"]: r for r in meta["rows"]}
    assert {"reported", "nc", "all"} <= set(by)
    for basis in ("nc", "all"):
        want = tot(client, f"&source=upstack&basis={basis}")["total_all"]["m"]
        assert by[basis]["m"]["purchases"] == pytest.approx(want["purchases"], abs=0.011) and by[basis]["m"]["revenue"] == pytest.approx(want["revenue"], abs=0.011)
    assert by["reported"]["m"]["spend"] == by["nc"]["m"]["spend"] == by["all"]["m"]["spend"]
    assert by["all"]["m"]["purchases"] >= by["nc"]["m"]["purchases"] > 0
