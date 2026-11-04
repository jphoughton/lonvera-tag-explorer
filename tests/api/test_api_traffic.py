"""v1.4: impressions, link clicks, CTR, CPC (+ CPM, conversion rate) - NULL impressions, stage filter, compare deltas."""
import datetime as dt

import pytest

from . import fixture_data as F
from .helpers import check_traffic, get_ok, rows_by_product

TRAFFIC_KEYS = ("impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")


def fams(client, slug, extra):
    return {r["family_key"]: r["m"] for r in get_ok(client, f"/api/products/{slug}/families?{extra}")["rows"]}


def test_every_metric_block_carries_the_traffic_keys(client):
    checks = [
        get_ok(client, "/api/products")[0]["m"],
        get_ok(client, "/api/products/vita-bull/rollup?window=30d")["total"]["m"],
        get_ok(client, "/api/products/vita-bull/families?window=30d")["rows"][0]["m"],
        get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3&window=30d")["rows"][-1]["m"],
        get_ok(client, "/api/products/vita-bull/ads?window=30d")["rows"][0],
        get_ok(client, "/api/products/vita-bull/ads?window=30d")["total"],
        get_ok(client, "/api/products/vita-bull/campaigns?window=30d")["rows"][0],
        get_ok(client, "/api/products/vita-bull/campaigns?window=30d")["by_stage"][0],
        get_ok(client, "/api/products/vita-bull/campaigns?window=30d")["total"],
        get_ok(client, "/api/products/vita-bull/unmatched?window=30d")["rows"][0],
        get_ok(client, "/api/products/vita-bull/unmatched?window=30d")["total"],
        get_ok(client, "/api/ads/1000?window=30d")["metrics"]["family"]["window"],
        get_ok(client, "/api/ads/1000?window=30d")["metrics"]["family"]["launch"],
    ]
    for blk in checks:
        assert set(TRAFFIC_KEYS) <= set(blk), blk.keys()
    cmp = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=prev")["total"]["m"]
    assert set(TRAFFIC_KEYS) <= set(cmp["cmp"]) and set(TRAFFIC_KEYS) <= set(cmp["delta"]) and "ctr_pp" in cmp["delta"]
    assert set(TRAFFIC_KEYS) <= set(get_ok(client, "/api/products/vita-bull/ads?window=7d&compare=prev")["rows"][0]["cmp"])
    assert "impressions" in get_ok(client, "/api/ads/1000")["daily"][-1]


def test_unknown_impressions_give_null_ratios_not_zero_or_errors(client, fx):
    """BL-Fam-2 only has sheet-grain days: link clicks 0 (= unknown) and impressions NULL. Spend is real, traffic ratios must be null."""
    w = "from=2026-08-15&to=2026-08-20"
    f = fams(client, "beef-liver", w)["bl-fam-2"]
    assert f["spend"] > 0 and f["impressions"] is None and f["link_clicks"] is None          # unknown is null, not 0
    assert f["impressions_per_day"] is None and f["link_clicks_per_day"] is None
    assert f["ctr"] is None and f["cpc"] is None and f["cpm"] is None and f["conv_rate"] is None
    for blk in (get_ok(client, f"/api/products/beef-liver/rollup?{w}")["total"]["m"], get_ok(client, f"/api/products/beef-liver/tree?root=cluster&depth=3&{w}")["rows"][0]["m"]):
        assert blk["ctr"] is None and blk["cpc"] is None
    # a product with no spend at all is not an error either
    bf = get_ok(client, "/api/products/boneflex/rollup?window=7d")["total"]["m"]
    assert bf["spend"] == 0 and bf["impressions"] == 0 and bf["link_clicks"] == 0 and all(bf[k] is None for k in ("ctr", "cpc", "cpm", "conv_rate"))      # no activity: a real 0
    z = get_ok(client, "/api/products/vita-bull/rollup?level=pain&cluster=Beta Cluster&icp=ICP Three")["rows"][1]["m"]      # taxonomy line with no ads
    assert z["impressions"] == 0 and all(z[k] is None for k in ("ctr", "cpc", "cpm", "conv_rate"))      # nothing delivered: 0, not unknown


def test_sheet_days_have_clicks_but_no_impressions(client, fx):
    """Sheet-grain days know link clicks (so CPC works) but have no impressions (so CTR and CPM stay null for them)."""
    w = "from=2026-08-05&to=2026-08-20"
    f = fams(client, "vita-bull", w)["vb-fam-a"]
    assert f["spend"] > 0 and f["link_clicks"] > 0 and f["impressions"] is None            # clicks known, impressions unknown -> null
    assert f["ctr"] is None and f["cpm"] is None
    assert f["cpc"] == pytest.approx(f["spend"] / f["link_clicks"], rel=1e-3)
    assert f["conv_rate"] == pytest.approx(f["purchases"] / f["link_clicks"], rel=1e-3)


def test_cpc_and_cpm_use_only_spend_with_traffic_data(client, fx):
    """The product total of Sep 20-30 mixes BL-Fam-2 sheet days (clicks unknown) with API days: CPC must not divide that spend by clicks."""
    w = "from=2026-09-20&to=2026-09-30"
    rows = rows_by_product(fx, "beef-liver")
    m = get_ok(client, f"/api/products/beef-liver/rollup?{w}")["total"]["m"]
    agg = F.aggregate(fx, rows, (F.D(2026, 9, 20), F.D(2026, 9, 30)), "all")
    check_traffic(m, agg, "bl total")
    assert agg["sp_tr"] < agg["spend"]                                    # unknown-traffic spend exists in the window
    assert m["cpc"] == pytest.approx(float(agg["sp_tr"]) / agg["clk"], abs=1e-3)
    assert m["cpc"] < m["spend"] / m["link_clicks"]                       # a naive spend / clicks would be higher
    assert m["ctr"] == pytest.approx(agg["clk_tr"] / agg["imp"], abs=1e-5)


def test_stage_filter_applies_to_traffic_metrics(client, fx):
    base = "window=30d"
    tot = {s: get_ok(client, f"/api/products/vita-bull/rollup?{base}&stage={s}")["total"]["m"] for s in ("testing", "scaling", "unclassified", "all")}
    for k in ("impressions", "link_clicks"):
        assert tot["testing"][k] + tot["scaling"][k] + tot["unclassified"][k] == tot["all"][k]
    assert tot["testing"]["impressions"] > 0 and tot["testing"]["impressions"] < tot["all"]["impressions"]
    both = get_ok(client, f"/api/products/vita-bull/rollup?{base}&stage=testing,scaling")["total"]["m"]
    assert both["impressions"] == tot["testing"]["impressions"] + tot["scaling"]["impressions"]
    # CTR of one stage uses that stage's own sums
    agg = F.aggregate(fx, rows_by_product(fx, "vita-bull"), (F.THROUGH - dt.timedelta(days=29), F.THROUGH), "testing")
    assert tot["testing"]["ctr"] == pytest.approx(agg["clk_tr"] / agg["imp"], abs=1e-5)
    assert tot["testing"]["ctr"] != tot["scaling"]["ctr"]


def test_compare_deltas_for_ctr_cpc(client, fx):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev")["total"]["m"]
    a = F.aggregate(fx, rows_by_product(fx, "vita-bull"), (F.THROUGH - dt.timedelta(days=13), F.THROUGH), "all")
    b = F.aggregate(fx, rows_by_product(fx, "vita-bull"), (F.THROUGH - dt.timedelta(days=27), F.THROUGH - dt.timedelta(days=14)), "all")
    ctr_a, ctr_b = a["clk_tr"] / a["imp"], b["clk_tr"] / b["imp"]
    cpc_a, cpc_b = float(a["sp_tr"]) / a["clk"], float(b["sp_tr"]) / b["clk"]
    assert d["ctr"] == pytest.approx(ctr_a, abs=1e-5) and d["cmp"]["ctr"] == pytest.approx(ctr_b, abs=1e-5)
    assert d["delta"]["ctr"] == pytest.approx(ctr_a - ctr_b, abs=2e-5)
    assert d["delta"]["ctr_pp"] == pytest.approx((ctr_a - ctr_b) * 100, abs=2e-3)         # percentage points
    assert d["delta"]["ctr_pct"] == pytest.approx((ctr_a - ctr_b) / ctr_b, abs=5e-4)
    assert d["delta"]["cpc"] == pytest.approx(cpc_a - cpc_b, abs=2e-3) and d["delta"]["cpc_pct"] == pytest.approx((cpc_a - cpc_b) / cpc_b, abs=1e-3)
    assert d["delta"]["impressions"] == a["imp"] - b["imp"] and d["delta"]["link_clicks"] == a["clk"] - b["clk"]
    assert d["delta"]["impressions_pct"] == pytest.approx((a["imp"] - b["imp"]) / b["imp"], abs=2e-4)
    assert d["impressions_per_day"] == pytest.approx(a["imp"] / 14, abs=0.01) and d["cmp"]["link_clicks_per_day"] == pytest.approx(b["clk"] / 14, abs=0.01)
    # when window B has no traffic data at all the deltas are null (not 0, not an error)
    sheet = get_ok(client, "/api/products/beef-liver/rollup?window=7d&cfrom=2026-08-15&cto=2026-08-20")["total"]["m"]
    assert sheet["cmp"]["spend"] > 0 and sheet["cmp"]["impressions"] is None and sheet["cmp"]["link_clicks"] is None
    assert sheet["cmp"]["ctr"] is None and sheet["delta"]["ctr"] is None and sheet["delta"]["ctr_pp"] is None and sheet["delta"]["ctr_pct"] is None
    assert sheet["delta"]["impressions_pct"] is None and sheet["delta"]["cpm"] is None and sheet["delta"]["impressions"] is None
    assert sheet["delta"]["link_clicks"] is None and sheet["delta"]["conv_rate_pp"] is None
    # every row of a compared table has the same keys
    for r in get_ok(client, "/api/products/vita-bull/families?window=7d&compare=prev")["rows"][:5]:
        assert "ctr_pp" in r["m"]["delta"] and "ctr" in r["m"]["cmp"]


def test_window_info_notes_where_impressions_start(client, fx):
    from app import queries as q
    meta = q.DataMeta(F.D0, F.THROUGH, impressions_from=F.D(2026, 8, 12))
    w = q.parse_window({"from": "2026-07-01", "to": "2026-07-10"}, meta)
    assert w.info(meta)["note"] == "Impressions only from Aug 12."               # some days of the window have no impressions yet
    assert q.parse_window({"window": "7d"}, meta).info(meta)["note"] is None        # every day of the window has them
    assert q.parse_window({"from": "2026-08-01", "to": "2026-08-20", "compare": "prev"}, meta).compare_info(meta)["note"] == "Impressions only from Aug 12."
    none_loaded = q.DataMeta(F.D0, F.THROUGH, impressions_from=None)
    assert q.parse_window({"window": "7d"}, none_loaded).info(none_loaded)["note"] == "No impressions are loaded yet."
    assert q.parse_window({}, none_loaded).info(none_loaded)["note"] is None        # since launch = lifetime sums (impressions and clicks included)
    clamped = q.parse_window({"from": "2026-04-01", "to": "2026-05-20"}, meta)
    assert clamped.info(meta)["note"].startswith("Daily data covers") and clamped.info(meta)["note"].endswith("Impressions only from Aug 12.")
    # the fixture's first impression is on the first day of the daily range -> no note over HTTP
    assert get_ok(client, "/api/products/vita-bull/rollup?window=180d")["window_info"]["note"] is None
    assert get_ok(client, "/api/products/vita-bull/rollup")["window_info"]["note"] is None


def test_launch_traffic_comes_from_the_lifetime_sums(client, fx):
    p = get_ok(client, "/api/products")
    vb = next(x for x in p if x["slug"] == "vita-bull")["m"]
    agg = F.aggregate(fx, rows_by_product(fx, "vita-bull"), "launch", "all")
    check_traffic(vb, agg, "vb launch")
    assert vb["impressions"] > 0 and vb["ctr"] == pytest.approx(vb["link_clicks"] / vb["impressions"], abs=1e-5)       # all lifetime clicks have impressions
    assert vb["cpc"] == pytest.approx(vb["spend"] / vb["link_clicks"], abs=2e-3)                                         # spend base = lifetime spend
    assert vb["cpm"] == pytest.approx(vb["spend"] * 1000 / vb["impressions"], abs=2e-2)


def test_cpc_is_null_not_zero_when_the_traffic_spend_base_is_missing():
    """family_daily.spend_tr not filled yet (0) while clicks exist: CPC / CPM are unknown (null), never $0."""
    from app import queries as q
    side = {"spend": 111340, "purchases": 900, "revenue": 90000, "imp": 0, "clk": 50560, "clk_tr": 0, "sp_tr": 0, "purch_t": 900}
    f = q.side_fields(side, 30)
    assert f["cpc"] is None and f["cpm"] is None and f["ctr"] is None and f["impressions"] is None and f["link_clicks"] == 50560
    assert f["conv_rate"] == pytest.approx(900 / 50560, abs=1e-5)
    ok = q.side_fields(dict(side, sp_tr=111340), 30)
    assert ok["cpc"] == pytest.approx(111340 / 50560, abs=1e-3)
    # a genuine free click is a real $0 CPC
    free = q.side_fields({"spend": 0, "purchases": 0, "revenue": 0, "imp": 100, "clk": 5, "clk_tr": 5, "sp_tr": 0, "purch_t": 0}, 7)
    assert free["cpc"] == 0 and free["impressions"] == 100


def test_percentage_point_fields_are_points_not_fractions(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev")["total"]["m"]
    dl = d["delta"]
    assert dl["ctr_pp"] == pytest.approx(dl["ctr"] * 100, abs=2e-3) and dl["conv_rate_pp"] == pytest.approx(dl["conv_rate"] * 100, abs=2e-3)
    assert dl["spend_share_pp"] == pytest.approx((d["spend_share"] - d["cmp"]["spend_share"]) * 100, abs=1e-3)
    assert 0 <= d["testing_pct"] <= 1 and 0 <= d["spend_share"] <= 1                       # fractions
