"""Every input of the drivers module is a named variable that moves the outputs: one case per variable (levers, plan series, overrides, actuals)."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import put_actuals, put_cells, small_plan

from app.projection.drivers.model import DEFAULT_SHARES, DriverInputs, Levers, compute_drivers, parse_levers

BASE = "/api/projection/drivers"
M = lambda y, m: date(y, m, 1)             # noqa: E731


@pytest.fixture()
def world(rw, stub_world):
    return small_plan(rw)


def table(client):
    r = client.get(f"{BASE}/table")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def val(t, key, month):
    return next(r for r in t["rows"] if r["key"] == key)["values"][t["months"].index(month.isoformat())]


def put_levers(client, levers):
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": levers})
    assert r.status_code == 200, r.get_json()


DEC = M(2026, 12)

# ---------------------------------------------------------------------------------------------------------------- levers (params.drivers)

LEVER_CASES = [
    # (lever patch, row key, month, expected relation to the baseline value)
    ({"channel_share": {"amazon": 0.0, "meta": 0.5, "google": 0.5, "bing": 0.0, "tiktok": 0.0}}, "spend_meta", DEC, "lower"),
    ({"channel_share": {"amazon": 0.0, "meta": 0.5, "google": 0.5, "bing": 0.0, "tiktok": 0.0}}, "spend_google", DEC, "higher"),
    ({"sales_tax_rate": 0.0}, "sales_tax", DEC, "higher"),                                   # tax is negative: zero is higher
    ({"sales_tax_rate": 0.10}, "total_revenue", DEC, "lower"),
    ({"ad_spend_channels": ["meta"]}, "ad_spend", DEC, "lower"),
    ({"ad_spend_channels": ["meta", "google", "amazon"]}, "nc_orders", DEC, "higher"),
    ({"payback": {"product_cost_pct": 0.30}}, "payback", DEC, "higher"),
    ({"payback": {"fulfillment_per_order": 15}}, "payback", DEC, "higher"),
]


@pytest.mark.parametrize("patch,key,month,relation", LEVER_CASES, ids=lambda v: str(v)[:40])
def test_a_lever_change_moves_the_output(client, world, patch, key, month, relation):
    before = val(table(client), key, month)
    put_levers(client, patch)
    after = val(table(client), key, month)
    assert after is not None and before is not None
    assert (after > before) if relation == "higher" else (after < before), (before, after)


def test_payback_horizon_cuts_the_search(client, world, rw):
    put_cells(rw, "drivers.nc_cpa", {DEC: 110.0})                       # a CPA that takes a year or so to earn back
    t = table(client)
    months_default = val(t, "payback", DEC)
    assert months_default is not None and months_default >= 6
    put_levers(client, {"payback": {"horizon_months": 1}})
    assert val(table(client), "payback", DEC) is None                   # not within a one-month horizon


def test_summary_window_and_band_lever_change_the_headline(client, world):
    t = table(client)
    assert t["summary"]["window_months"] == 12 and t["summary"]["posture_band"] == 0.10
    put_levers(client, {"summary_months": 6, "posture_band": 0.9})
    t2 = table(client)
    assert t2["summary"]["window_months"] == 6 and t2["summary"]["spend_next_12"] < t["summary"]["spend_next_12"]
    assert t2["summary"]["posture"] in ("hold", "unknown") or abs(t2["summary"]["spend_change"] or 0) >= 0.9


def test_roas_slide_is_a_lever_too(client, world):
    before = val(table(client), "nc_roas", M(2027, 3))
    put_levers(client, {"roas_slide": {"enabled": True, "from": "2027-01-01", "to": "2027-06-01", "start": 0.5, "end": 0.4}})
    assert val(table(client), "nc_roas", M(2027, 3)) < before


@pytest.mark.parametrize("levers,fragment", [
    ({"ad_spend_channels": []}, "ad_spend_channels"), ({"ad_spend_channels": ["meta", "meta"]}, "each once"), ({"ad_spend_channels": ["tv"]}, "ad_spend_channels"),
    ({"posture_band": 0}, "posture_band"), ({"posture_band": 2}, "posture_band"), ({"summary_months": 0}, "summary_months"), ({"summary_months": 2.5}, "summary_months"),
    ({"payback": {"horizon_months": 0}}, "horizon_months"), ({"payback": {"horizon_months": 61}}, "horizon_months"),
])
def test_new_levers_are_validated(client, world, levers, fragment):
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": levers})
    assert r.status_code == 400 and fragment in r.get_json()["error"]


def test_every_lever_has_a_default_and_round_trips():
    d = Levers().to_dict()
    assert set(d) == {"channel_share", "sales_tax_rate", "roas_slide", "payback", "ad_spend_channels", "posture_band", "summary_months"}
    assert d["channel_share"] == DEFAULT_SHARES and d["sales_tax_rate"] == 0.07 and d["ad_spend_channels"] == ["meta", "google"]
    assert d["payback"] == {"product_cost_pct": 0.1726, "fulfillment_per_order": 5.53, "horizon_months": 36}
    assert parse_levers({"drivers": d}).to_dict() == d


def test_ad_spend_channels_lever_in_the_pure_engine():
    plan = {"spend_total": {M(2027, 1): 1000.0}, "nc_cpa": {M(2027, 1): 100.0}, "nc_aov": {M(2027, 1): 50.0}}
    base = compute_drivers({}, DriverInputs(today=date(2026, 11, 3), plan=plan))
    wide = compute_drivers({"drivers": {"ad_spend_channels": ["meta", "google", "amazon", "tiktok"]}}, DriverInputs(today=date(2026, 11, 3), plan=plan))
    assert wide.ad_spend[M(2027, 1)] > base.ad_spend[M(2027, 1)] and wide.nc_orders[M(2027, 1)] > base.nc_orders[M(2027, 1)]
    assert wide.ad_spend[M(2027, 1)] == pytest.approx(1000 * (1 - DEFAULT_SHARES["bing"]))


# ---------------------------------------------------------------------------------------------------------------- plan series (proj_series, kind input)

SERIES_CASES = [("drivers.spend_total", "drivers.spend_total", 600000.0), ("drivers.nc_cpa", "nc_orders", 60.0), ("drivers.nc_aov", "nc_rev", 140.0),
                ("drivers.amazon_rev", "total_revenue", 400000.0), ("drivers.tiktok_rev", "total_revenue", 90000.0)]


@pytest.mark.parametrize("series_key,out_key,new", SERIES_CASES)
def test_a_plan_series_cell_moves_the_output(client, world, rw, series_key, out_key, new):
    before = val(table(client), out_key, DEC)
    put_cells(rw, series_key, {DEC: new})
    assert val(table(client), out_key, DEC) != before


# ---------------------------------------------------------------------------------------------------------------- overrides (proj_series, kind override)

OVERRIDE_CASES = [("drivers.ad_spend", "ad_spend", 123456.0), ("drivers.nc_orders", "nc_orders", 777.0), ("drivers.nc_rev", "nc_rev", 55555.0),
                  ("drivers.repeat_rev", "repeat_rev", 999999.0)]


@pytest.mark.parametrize("series_key,out_key,new", OVERRIDE_CASES)
def test_an_override_replaces_the_computed_value(client, world, rw, series_key, out_key, new):
    put_cells(rw, series_key, {DEC: new}, kind="override")
    assert val(table(client), out_key, DEC) == new


# ---------------------------------------------------------------------------------------------------------------- loaded actuals (proj_actuals)

ACTUAL_CASES = [("spend.meta", "spend_meta"), ("pnl.mkt_google_ads", "spend_google"), ("spend.amazon_ads", "spend_amazon_ads"), ("pnl.mkt_tiktok_ads", "spend_tiktok"),
                ("spend.bing", "spend_bing"), ("pnl.amazon_rev", "rev_amazon"), ("tiktok_rev", "rev_tiktok"), ("pnl.dtc_rev", "dtc_rev")]


@pytest.mark.parametrize("actual_key,out_key", ACTUAL_CASES)
def test_a_loaded_actual_moves_the_closed_month(client, world, rw, actual_key, out_key):
    mar = M(2026, 3)
    before = val(table(client), out_key, mar)
    put_actuals(rw, actual_key, {mar: 987654.0})
    assert val(table(client), out_key, mar) == 987654.0 != before
