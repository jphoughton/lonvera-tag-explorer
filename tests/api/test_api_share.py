"""v1.3: spend_share (% of spend) on every metric block."""
import pytest

from .helpers import get_ok

TOL = 2e-5


def shares(blocks, key="spend_share"):
    return [b[key] for b in blocks]


def check_flat(blocks, total, with_cmp=False, expect_sum=1.0):
    ts = total["spend"]
    for b in blocks:
        assert b["spend_share"] == (pytest.approx(b["spend"] / ts, abs=1e-5) if ts else None)
    if ts:
        assert sum(b["spend_share"] for b in blocks) == pytest.approx(expect_sum, abs=TOL * max(len(blocks), 1))
        assert total["spend_share"] == 1.0
    else:
        assert total["spend_share"] is None
    if with_cmp:
        tb = total["cmp"]["spend"]
        for b in blocks:
            want = b["cmp"]["spend"] / tb if tb else None
            assert b["cmp"]["spend_share"] == (pytest.approx(want, abs=1e-5) if tb else None)
            if tb and ts:
                assert b["delta"]["spend_share_pp"] == pytest.approx((b["spend_share"] - b["cmp"]["spend_share"]) * 100, abs=1e-3)
            else:
                assert b["delta"]["spend_share_pp"] is None
        if tb:
            assert sum(b["cmp"]["spend_share"] for b in blocks) == pytest.approx(1.0, abs=TOL * max(len(blocks), 1))


@pytest.mark.parametrize("extra", ["", "window=7d", "window=30d&compare=prev", "window=14d&compare=30d&stage=testing,scaling", "stage=unclassified"])
def test_rollup_levels_sum_to_one(client, extra):
    cmp = "compare" in extra
    d = get_ok(client, f"/api/products/vita-bull/rollup?level=cluster&{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)
    d = get_ok(client, f"/api/products/vita-bull/rollup?level=icp&cluster=Alpha Cluster&{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)
    d = get_ok(client, f"/api/products/vita-bull/rollup?level=pain&cluster=Alpha Cluster&icp=ICP One&{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)
    d = get_ok(client, f"/api/products/beef-liver/rollup?level=cluster&{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)


@pytest.mark.parametrize("extra", ["", "window=7d", "window=30d&compare=prev"])
def test_families_products_sum_to_one(client, extra):
    cmp = "compare" in extra
    d = get_ok(client, f"/api/products/vita-bull/families?{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)
    d = get_ok(client, f"/api/products/vita-bull/families?cluster=Alpha Cluster&{extra}")
    check_flat([r["m"] for r in d["rows"]], d["total"]["m"], cmp)
    prods = get_ok(client, f"/api/products?{extra}")
    total_spend = sum(p["m"]["spend"] for p in prods)
    assert sum(p["m"]["spend_share"] for p in prods) == pytest.approx(1.0, abs=1e-4)
    for p in prods:
        assert p["m"]["spend_share"] == pytest.approx(p["m"]["spend"] / total_spend, abs=1e-5)
        if cmp:
            assert "spend_share" in p["m"]["cmp"] and "spend_share_pp" in p["m"]["delta"]


def test_zero_spend_total_gives_null_shares(client):
    d = get_ok(client, "/api/products/boneflex/rollup")
    assert d["total"]["m"]["spend"] == 0 and d["total"]["m"]["spend_share"] is None and d["rows"][0]["m"]["spend_share"] is None
    d = get_ok(client, "/api/products/vita-bull/rollup?level=pain&cluster=Beta Cluster&icp=ICP Three&window=1d&cfrom=2026-05-06&cto=2026-05-06")
    assert all("spend_share" in r["m"] for r in d["rows"])


@pytest.mark.parametrize("extra", ["", "window=14d&compare=prev", "window=launch&stage=scaling"])
def test_ads_share_of_family_and_selection(client, extra):
    d = get_ok(client, f"/api/products/vita-bull/ads?{extra}")
    check_flat(d["rows"], d["total"], "compare" in extra)
    d = get_ok(client, f"/api/products/vita-bull/ads?family_key=vb-fam-a&cluster=Alpha Cluster&icp=ICP One&pain=Pain A&{extra}")
    assert d["rows"]
    check_flat(d["rows"], d["total"], "compare" in extra)                    # "% of family": rows add up to the family


def test_campaigns_and_unmatched_shares(client):
    d = get_ok(client, "/api/products/vita-bull/campaigns?window=30d&compare=prev")
    assert d["total"]["spend_share"] == 1.0
    assert sum(r["spend_share"] for r in d["rows"]) <= 1.0 + 1e-4              # spend without a campaign (sheet-only days) is not a campaign row
    assert sum(c["spend_share"] for c in d["by_stage"]) == pytest.approx(1.0, abs=1e-4)
    for r in d["rows"]:
        assert r["spend_share"] == pytest.approx(r["spend"] / d["total"]["spend"], abs=1e-5)
        assert "spend_share" in r["cmp"] and "spend_share_pp" in r["delta"]
    u = get_ok(client, "/api/products/vita-bull/unmatched?window=30d")
    check_flat(u["rows"], u["total"])


def test_detail_campaign_table_shares(client):
    d = get_ok(client, "/api/ads/1000?window=30d")
    assert sum(c["spend_share"] for c in d["campaigns"]) == pytest.approx(1.0, abs=1e-4)
    check_flat(d["meta_ads"], {"spend": sum(a["spend"] for a in d["meta_ads"]), "spend_share": 1.0})
