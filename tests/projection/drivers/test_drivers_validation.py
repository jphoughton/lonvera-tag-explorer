"""Bounds and closed-month refusal for Spend plan cells: PUT /drivers/series, POST /drivers/patterns, and the hook handed to the core."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import small_plan

from app.projection import scenarios, series
from app.projection.drivers import MODULE, validation
from app.queries import ApiError

BASE = "/api/projection/drivers"
M = lambda y, m: date(y, m, 1)                # noqa: E731


@pytest.fixture()
def world(rw, stub_world):
    return small_plan(rw)                      # books Jan-Jun 2026, plan Jul-26 .. Dec-27


def put(client, key, month, value, kind="input", **extra):
    return client.put(f"{BASE}/series", json={"scenario": "current", "items": [{"line_key": key, "month": month, "value": value, "kind": kind}], **extra})


def stored(pg, key, month):
    r = pg.execute("SELECT value FROM proj_series s JOIN proj_scenarios p ON p.id = s.scenario_id WHERE p.slug = 'current' AND line_key = %s AND month = %s",
                   (key, month)).fetchone()
    return float(r["value"]) if r else None


@pytest.mark.parametrize("key,value,fragment", [
    ("drivers.spend_total", -350000, "between 0 and 50,000,000"),
    ("drivers.spend_total", 500000350000, "between 0 and 50,000,000"),
    ("drivers.spend_total", 50000001, "between 0 and 50,000,000"),
    ("drivers.nc_cpa", 0, "above 0 and at most 1,000"),
    ("drivers.nc_cpa", -5, "above 0 and at most 1,000"),
    ("drivers.nc_cpa", 1000.01, "above 0 and at most 1,000"),
    ("drivers.nc_aov", 0, "above 0 and at most 1,000"),
    ("drivers.nc_aov", 5000, "above 0 and at most 1,000"),
    ("drivers.amazon_rev", -1, "Amazon revenue"),
    ("drivers.ad_spend", 9e9, "Ad spend"),
    ("drivers.nc_orders", -3, "orders"),
])
def test_out_of_range_values_are_refused_and_nothing_is_written(client, world, pg, key, value, fragment):
    kind = "override" if key in ("drivers.ad_spend", "drivers.nc_orders") else "input"
    v0 = pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"]
    r = put(client, key, "2027-03-01", value, kind)
    assert r.status_code == 400 and fragment in r.get_json()["error"] and "Mar 2027" in r.get_json()["error"]
    assert pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"] == v0


@pytest.mark.parametrize("key,value", [("drivers.spend_total", 0), ("drivers.spend_total", 50_000_000), ("drivers.nc_cpa", 0.01), ("drivers.nc_cpa", 1000),
                                       ("drivers.nc_aov", 1000), ("drivers.amazon_rev", 0), ("drivers.tiktok_rev", 0)])
def test_the_edges_of_the_ranges_are_accepted(client, world, pg, key, value):
    assert put(client, key, "2027-03-01", value).status_code == 200 and stored(pg, key, "2027-03-01") == value


def test_a_closed_month_is_refused_for_plan_inputs_on_every_line(client, world):
    for key, value in (("drivers.spend_total", 1000), ("drivers.nc_cpa", 100), ("drivers.nc_aov", 80), ("drivers.amazon_rev", 1000), ("drivers.tiktok_rev", 1)):
        r = put(client, key, "2026-03-01", value)
        assert r.status_code == 400 and "Mar 2026" in r.get_json()["error"] and "loaded data" in r.get_json()["error"], key


def test_clearing_a_closed_month_cell_and_typing_an_override_there_are_allowed(client, world, rw):
    assert put(client, "drivers.spend_total", "2026-03-01", None).status_code == 200
    assert put(client, "drivers.ad_spend", "2026-03-01", 123456, "override").status_code == 200
    assert put(client, "drivers.nc_rev", "2026-03-01", 55555, "override").status_code == 200


def test_one_bad_item_rejects_the_whole_batch(client, world, pg):
    r = client.put(f"{BASE}/series", json={"scenario": "current", "items": [
        {"line_key": "drivers.spend_total", "month": "2027-03-01", "value": 111, "kind": "input"},
        {"line_key": "drivers.spend_total", "month": "2027-04-01", "value": -1, "kind": "input"}]})
    assert r.status_code == 400 and stored(pg, "drivers.spend_total", "2027-03-01") == 300000.0


def test_other_lines_and_unknown_keys_are_not_spend_plan_lines(client, world):
    for key in ("pnl.noi", "zzz.unknown", "drivers.k0", "drivers.payback"):
        r = put(client, key, "2027-03-01", 1)
        assert r.status_code == 400 and "not a Spend plan line" in r.get_json()["error"], key


def test_a_valid_edit_writes_one_audit_row_and_one_version(client, world, pg):
    v0 = pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"]
    r = put(client, "drivers.spend_total", "2027-03-01", 410000)
    assert r.status_code == 200 and r.get_json() == {"version": v0 + 1, "changed": 1}
    a = pg.execute("SELECT * FROM proj_audit WHERE action = 'series.update' ORDER BY id DESC LIMIT 1").fetchone()
    assert a["new"]["value"] == 410000.0 and a["old"]["value"] == 300000.0 and a["actor"] == "JoshHoughton"


def test_expected_version_and_shape_errors(client, world):
    v = client.get(f"{BASE}/table").get_json()["scenario"]["version"]
    assert put(client, "drivers.spend_total", "2027-03-01", 1, expected_version=v + 5).status_code == 409
    assert client.put(f"{BASE}/series", json={"scenario": "current", "items": []}).status_code == 400
    assert client.put(f"{BASE}/series", json={"items": [{"line_key": "drivers.spend_total", "month": "2027-03-01", "value": 1}]}).status_code == 400
    assert client.put(f"{BASE}/series", json={"scenario": "nope", "items": [{"line_key": "drivers.spend_total", "month": "2027-03-01", "value": 1}]}).status_code == 404
    assert put(client, "drivers.spend_total", "2027-03-01", "350k").status_code == 400                       # a string is not a number


def test_the_pattern_endpoint_uses_the_same_bounds(client, world):
    def pat(target, spec):
        return client.post(f"{BASE}/patterns", json={"scenario": "current", "target": target, "from": "2027-01-01", "to": "2027-03-01", "pattern": spec})
    assert pat("nc_cpa", {"type": "flat", "value": 0}).status_code == 400
    assert pat("nc_cpa", {"type": "flat", "value": 1001}).status_code == 400
    assert pat("nc_aov", {"type": "flat", "value": 1001}).status_code == 400
    assert pat("spend_total", {"type": "flat", "value": 50_000_001}).status_code == 400
    assert pat("spend_total", {"type": "flat", "value": 50_000_000}).status_code == 200
    r = client.post(f"{BASE}/patterns", json={"scenario": "current", "target": "spend_total", "from": "2026-05-01", "to": "2026-08-01", "pattern": {"type": "flat", "value": 1}})
    assert r.status_code == 400 and "hold loaded actuals" in r.get_json()["error"]


def test_the_hook_for_the_core_validates_drivers_lines_and_ignores_the_rest(rw, world):
    scn = scenarios.get_scenario(rw, "current")
    items = series.parse_items([{"line_key": "drivers.nc_cpa", "month": "2027-03-01", "value": 0}])
    with pytest.raises(ApiError) as e:
        MODULE.validate_series(rw, scn, items)
    assert e.value.status == 400 and "CPA" in e.value.message
    MODULE.validate_series(rw, scn, series.parse_items([{"line_key": "pnl.noi", "month": "2027-03-01", "value": -9e9}]))      # not ours: left to its owner
    MODULE.validate_series(rw, scn, series.parse_items([{"line_key": "drivers.nc_cpa", "month": "2027-03-01", "value": 95}]))
    with pytest.raises(ApiError):
        MODULE.validate_series(rw, scn, series.parse_items([{"line_key": "drivers.spend_total", "month": "2026-03-01", "value": 5}]))


def test_bounds_table_covers_every_line():
    from app.projection.drivers.model import OVERRIDE_KEYS, PLAN_KEYS
    assert set(validation.BOUNDS) == set(PLAN_KEYS) | set(OVERRIDE_KEYS)
    assert validation.BOUNDS["nc_cpa"].lo_open and validation.BOUNDS["nc_cpa"].hi == 1000.0 and validation.BOUNDS["spend_total"].hi == 50_000_000.0
