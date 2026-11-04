"""Every table has a Total: products envelope, rollup, tree, families, ads, campaigns, unmatched - with stage, traffic, cmp/delta, spark."""
import itertools

import pytest

from . import fixture_data as F
from .helpers import check_m, get_ok, qs, rows_by_product

TRAFFIC = ("impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")
WINDOWS = ["", "window=7d", "window=30d", "window=mtd", "from=2026-09-01&to=2026-09-30"]
STAGES = ["", "stage=testing", "stage=testing,scaling", "stage=unclassified"]


def total_m(resp):
    t = resp["total"]
    return t["m"] if "m" in t else t


def check_block(m, compare, ctx):
    assert {"spend", "purchases", "revenue", "roas", "cpa", "spend_share", *TRAFFIC} <= set(m), ctx
    if compare:
        assert set(TRAFFIC) <= set(m["cmp"]) and "spend_share" in m["cmp"] and {"ctr_pp", "conv_rate_pp", "spend_share_pp", *TRAFFIC} <= set(m["delta"]), ctx
    else:
        assert "cmp" not in m and "delta" not in m, ctx


# ---------------------------------------------------------------------------------------------------------------- products envelope
@pytest.mark.parametrize("extra", ["", "window=7d", "window=30d&stage=testing,scaling", "window=14d&compare=prev", "window=30d&compare=7d&stage=scaling,unclassified"])
def test_products_envelope_has_a_total_row(client, fx, extra):
    env = get_ok(client, "/api/products?" + qs(extra, "envelope=1"))
    assert set(env) == {"window_info", "compare_info", "scope", "rows", "total", "unmatched", "total_all"}
    rows, t = env["rows"], env["total"]["m"]
    assert get_ok(client, "/api/products?" + extra) == rows                           # the bare array form keeps working and equals envelope.rows
    assert env["total"]["rows"] == sum(r["rows"] for r in rows)
    assert t["unique_ads"] == sum(r["m"]["unique_ads"] for r in rows)                  # a family under two products counts twice, like the rows
    for k in ("spend", "purchases", "revenue", "live_now", "active_ads"):
        assert t[k] == pytest.approx(sum(r["m"][k] for r in rows), abs=0.02), k
    assert t["spend_share"] == (1.0 if t["spend"] else None)
    assert sum(r["m"]["spend_share"] or 0 for r in rows) == pytest.approx(1.0 if t["spend"] else 0.0, abs=1e-4)
    assert t["roas"] == (pytest.approx(t["revenue"] / t["spend"], abs=2e-4) if t["spend"] else None)       # ratios from sums
    check_block(t, "compare" in extra, extra)
    if "compare" in extra:
        assert t["cmp"]["spend"] == pytest.approx(sum(r["m"]["cmp"]["spend"] for r in rows), abs=0.02)
        assert t["cmp"]["spend_share"] == (1.0 if t["cmp"]["spend"] else None)
        assert t["delta"]["spend"] == pytest.approx(t["spend"] - t["cmp"]["spend"], abs=0.02)
    for k in ("impressions", "link_clicks"):
        assert (t[k] or 0) == sum(r["m"][k] or 0 for r in rows), k
    assert set(t["stage"]) == {"testing", "scaling", "unclassified"} and "testing_pct" in t


def test_products_envelope_total_matches_the_oracle(client, fx):
    env = get_ok(client, "/api/products?window=30d&envelope=1")
    import datetime as dt
    win = (F.THROUGH - dt.timedelta(days=29), F.THROUGH)
    rows = [r for slug in ("vita-bull", "beef-liver", "boneflex") for r in rows_by_product(fx, slug)]
    agg = F.aggregate(fx, rows, win, "all")
    agg["unique"] = sum(len({r["family_key"] for r in rows_by_product(fx, s)}) for s in ("vita-bull", "beef-liver", "boneflex"))
    check_m(env["total"]["m"], agg, "products total")


def test_products_envelope_spark_on_rows_and_total(client):
    env = get_ok(client, "/api/products?window=30d&envelope=1&spark=spend&spark_days=10")
    assert all(len(r["spark"]) == 10 for r in env["rows"]) and len(env["total"]["spark"]) == 10
    for i in range(10):
        assert sum(r["spark"][i] for r in env["rows"]) == pytest.approx(env["total"]["spark"][i], abs=0.02)
    assert "spark" not in get_ok(client, "/api/products?window=30d&envelope=1")["total"]
    bare = get_ok(client, "/api/products?window=30d&spark=revenue&spark_days=5")
    assert isinstance(bare, list) and all(len(r["spark"]) == 5 for r in bare)
    roas = get_ok(client, "/api/products?window=30d&envelope=1&spark=roas&spark_days=7")["total"]["spark"]
    assert len(roas) == 7 and any(v is not None for v in roas)
    assert client.get("/api/products?envelope=1&spark=nope").status_code == 400


# ---------------------------------------------------------------------------------------------------------------- every endpoint, every combination
ENDPOINTS = ["rollup?level=cluster", "rollup?level=icp&cluster=Alpha Cluster", "tree?root=cluster&depth=2", "families", "ads", "campaigns", "unmatched"]
COMBOS = [pytest.param(ep, w, st, cmp_, id=f"{ep}|{w or 'launch'}|{st or 'all'}|{'cmp' if cmp_ else 'nocmp'}")
          for ep, w, st, cmp_ in itertools.product(ENDPOINTS, WINDOWS, STAGES, (False, True)) if not (cmp_ and not w)]


@pytest.mark.parametrize("ep,w,st,cmp_", COMBOS)
def test_total_has_traffic_cmp_delta_everywhere(client, ep, w, st, cmp_):
    url = f"/api/products/vita-bull/{ep}" + ("&" if "?" in ep else "?") + qs(w, st, "compare=prev" if cmp_ else "")
    d = get_ok(client, url)
    assert "total" in d, url
    m = total_m(d)
    check_block(m, cmp_, url)
    rows = d["rows"]
    if m["spend"]:
        assert m["spend_share"] == 1.0, url
    for k in ("spend", "purchases", "revenue"):
        top = [r["m"][k] if "m" in r else r[k] for r in rows if r.get("level", 0) == 0]
        if ep.startswith("tree"):
            assert m[k] == pytest.approx(sum(top), abs=0.05), (url, k)


@pytest.mark.parametrize("ep", ["rollup?level=cluster", "tree?root=cluster&depth=1", "families", "ads", "campaigns"])
@pytest.mark.parametrize("w,st,cmp_", [("window=30d", "", False), ("window=14d", "stage=testing,scaling", True), ("", "stage=scaling,unclassified", False)])
def test_total_carries_spark_when_requested(client, ep, w, st, cmp_):
    base = f"/api/products/vita-bull/{ep}" + ("&" if "?" in ep else "?") + qs(w, st, "compare=prev" if cmp_ else "")
    d = get_ok(client, base + "&spark=spend&spark_days=9")
    t = d["total"]
    spark = t["spark"]
    assert len(spark) == 9 and all(isinstance(v, (int, float)) for v in spark), base
    assert "spark" not in get_ok(client, base)["total"], base
    rows = [r for r in d["rows"] if r.get("spark") and r.get("level", 0) == 0]
    if rows and not ep.startswith("ads"):                                 # a table's top rows add up to its total, day by day
        for i in range(9):
            assert sum(r["spark"][i] for r in rows) == pytest.approx(spark[i], abs=0.05), (base, i)


def test_unmatched_total_is_complete(client):
    d = get_ok(client, "/api/products/vita-bull/unmatched?window=30d&compare=prev&stage=testing,scaling")
    t = d["total"]
    check_block(t, True, "unmatched")
    assert t["families"] == len(d["rows"]) and t["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.02)
    assert t["cmp"]["spend"] == pytest.approx(sum(r["cmp"]["spend"] for r in d["rows"]), abs=0.02)
