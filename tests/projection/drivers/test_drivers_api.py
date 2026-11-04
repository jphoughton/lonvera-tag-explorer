"""HTTP API of the drivers module: table, levers, patterns, compare, guard rails, audit and undo."""
from __future__ import annotations

import json
import math
from datetime import date

import pytest
from conftest import months, put_actuals, put_cells, small_plan

from app.projection.drivers.model import DEFAULT_SHARES

BASE = "/api/projection/drivers"


def M(y, m):
    return date(y, m, 1)


@pytest.fixture()
def world(rw, stub_world):
    """Books Jan-Jun 2026, plan Jul-26 .. Dec-27 on 'current'."""
    return small_plan(rw)


def table(client, scenario="current"):
    r = client.get(f"{BASE}/table", query_string={"scenario": scenario})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def row(t, key):
    return next(r for r in t["rows"] if r["key"] == key)


def at(t, key, month):
    return row(t, key)["values"][[m for m in t["months"]].index(month.isoformat())]


def audit_rows(pg, action=None):
    q = "SELECT * FROM proj_audit" + (" WHERE action = %s" if action else "") + " ORDER BY id"
    return pg.execute(q, (action,) if action else ()).fetchall()


# ---------------------------------------------------------------------------------------------------------------- table

def test_table_uses_the_shells_grid_vocabulary(client, world):
    t = table(client)
    assert {r["kind"] for r in t["rows"]} == {"header", "input", "calc", "subtotal", "total", "ratio"}
    assert {r["fmt"] for r in t["rows"]} <= {"usd", "usd0", "num", "x", "pct", "text"}
    assert all(r["kind"] == "input" for r in t["rows"] if r["input"]) and not any(r["input"] for r in t["rows"] if r["kind"] != "input")


def test_table_shape_and_input_rows(client, world):
    t = table(client)
    assert len(t["months"]) == 60 and len(t["status"]) == 60 and t["months"][0] == "2025-01-01" and t["months"][-1] == "2029-12-01"
    assert t["status"][t["months"].index("2026-06-01")] == "actual" and t["status"][t["months"].index("2026-07-01")] == "projected"
    assert t["actual_through"] == "2026-06-01" and t["scenario"]["slug"] == "current" and t["captions"]
    inputs = [r["key"] for r in t["rows"] if r["input"]]
    assert inputs == ["drivers.spend_total", "drivers.nc_cpa", "drivers.nc_aov", "drivers.amazon_rev", "drivers.tiktok_rev",
                      "drivers.ad_spend", "drivers.nc_orders", "drivers.nc_rev", "drivers.repeat_rev"]
    assert all(len(r["values"]) == 60 for r in t["rows"])
    assert {"spend_next_12", "nc_rev_next_12", "nc_roas_next_12", "payback_months", "posture", "fy"} <= set(t["summary"])
    json.dumps(t, allow_nan=False)                                                  # strict JSON: no NaN / Infinity anywhere


def test_table_values_are_the_engine_values(client, world):
    t = table(client)
    s = DEFAULT_SHARES
    m = M(2026, 12)
    assert at(t, "drivers.spend_total", m) == 300000.0
    assert at(t, "ad_spend", m) == pytest.approx(300000 * (s["meta"] + s["google"]))
    assert at(t, "nc_orders", m) == pytest.approx(at(t, "ad_spend", m) / 95)
    assert at(t, "nc_rev", m) == pytest.approx(at(t, "ad_spend", m) / 95 * 70)
    assert at(t, "nc_roas", m) == pytest.approx(70 / 95)
    assert at(t, "spend_meta", m) == pytest.approx(300000 * s["meta"])
    assert at(t, "drivers.spend_total", M(2026, 3)) == 442000.0                       # an actual month shows the loaded total
    assert at(t, "source", M(2026, 3)) == "Books" and at(t, "source", m) == "Plan"
    assert at(t, "sales_tax", M(2026, 3)) == 0 and at(t, "sales_tax", m) < 0            # books months carry no tax estimate
    assert at(t, "total_revenue", m) == pytest.approx(at(t, "dtc_rev", m) + at(t, "rev_amazon", m) + at(t, "rev_tiktok", m) + at(t, "sales_tax", m))


def test_cellmeta_marks_typed_carried_and_actual_cells(client, world, rw):
    from app.projection import series
    series.apply_items(rw, "current", series.parse_items([{"line_key": "drivers.spend_total", "month": "2027-12-01", "value": None}]), actor="test")
    rw.commit()
    t = table(client)
    cm = t["cellmeta"]
    assert cm["drivers.spend_total"]["2026-12-01"] == "typed" and cm["drivers.spend_total"]["2026-03-01"] == "actual"
    assert cm["drivers.spend_total"]["2027-12-01"] == "typed" or cm["drivers.spend_total"]["2027-12-01"] == "carried"
    assert cm["drivers.spend_total"]["2028-06-01"] == "carried" and cm["drivers.nc_cpa"]["2028-06-01"] == "carried"


def test_typed_overrides_show_on_their_own_rows(client, world, rw):
    put_cells(rw, "drivers.nc_rev", {M(2026, 12): 111111.0}, kind="override")
    t = table(client)
    assert at(t, "drivers.nc_rev", M(2026, 12)) == 111111.0 and at(t, "drivers.nc_rev", M(2027, 1)) is None
    assert at(t, "nc_rev", M(2026, 12)) == 111111.0 and t["cellmeta"]["drivers.nc_rev"]["2026-12-01"] == "override"


def test_meta_feed_gap_row_appears_only_when_both_sources_exist(client, world, rw):
    assert "meta_gap" not in [r["key"] for r in table(client)["rows"]]
    put_actuals(rw, "spend.meta_feed", {M(2026, 3): 405000.0})
    t = table(client)
    assert at(t, "meta_gap", M(2026, 3)) == 5000.0


def test_payback_follows_the_plan_and_blank_when_never(client, world):
    t = table(client)
    assert row(t, "payback")["fmt"] == "num"
    assert t["summary"]["plan_cpa"] == 95.0 and t["summary"]["plan_aov"] == 70.0


def test_summary_reads_growth_harvest_hold(client, rw, stub_world):
    hist = months(2025, 7, 12)
    put_actuals(rw, "spend.meta", {m: 100000.0 for m in hist})
    put_cells(rw, "drivers.spend_total", {m: 50000.0 for m in months(2026, 7, 12)})
    assert table(client)["summary"]["posture"] == "harvest"
    put_cells(rw, "drivers.spend_total", {m: 200000.0 for m in months(2026, 7, 12)})
    s = table(client)["summary"]
    assert s["posture"] == "growth" and s["spend_next_12"] == pytest.approx(2400000.0) and s["spend_change"] > 0.1
    put_cells(rw, "drivers.spend_total", {m: 105000.0 for m in months(2026, 7, 12)})
    assert table(client)["summary"]["posture"] == "hold"


def test_table_for_unknown_scenario_is_404(client, world):
    assert client.get(f"{BASE}/table?scenario=nope").status_code == 404


def test_table_without_any_data_still_renders(client, stub_world):
    t = table(client)
    assert len(t["rows"]) > 20 and t["actual_through"] is None and t["summary"]["posture"] == "unknown"


# ---------------------------------------------------------------------------------------------------------------- auth and guard rails

def test_login_required_when_the_app_login_is_on(anon, world):
    assert anon.get(f"{BASE}/table").status_code == 401
    assert anon.post(f"{BASE}/patterns", json={}).status_code == 401
    assert anon.put(f"{BASE}/levers", json={}).status_code == 401
    assert anon.get(f"{BASE}/compare").status_code == 401


def test_open_when_the_app_login_is_off(app_factory, world):
    c = app_factory(AUTH_DISABLED=True).test_client()
    assert c.get(f"{BASE}/table").status_code == 200
    assert c.get(f"{BASE}/levers").status_code == 200


def test_write_guard_rails_apply_to_the_module_endpoints(client, world):
    assert client.post(f"{BASE}/patterns", data="x", content_type="text/plain").status_code == 415
    r = client.post(f"{BASE}/patterns", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.get_json()["error"] == "origin_mismatch"
    assert client.put(f"{BASE}/levers", data="{}", content_type="text/plain").status_code == 415


# ---------------------------------------------------------------------------------------------------------------- editing through the core series API

def test_a_cell_edit_through_the_core_api_moves_the_plan_and_is_audited(client, world, pg):
    r = client.put("/api/projection/series", json={"scenario": "current", "items": [
        {"line_key": "drivers.spend_total", "month": "2027-01-01", "value": 600000, "kind": "input", "note": "test edit"}]})
    assert r.status_code == 200 and r.get_json()["changed"] == 1
    t = table(client)
    assert at(t, "drivers.spend_total", M(2027, 1)) == 600000.0 and at(t, "drivers.spend_total", M(2027, 2)) == 300000.0
    assert at(t, "nc_rev", M(2027, 1)) > 1.9 * at(t, "nc_rev", M(2026, 12))
    a = audit_rows(pg, "series.update")[-1]
    assert a["line_key"] == "drivers.spend_total" and a["new"]["value"] == 600000.0 and a["old"]["value"] == 300000.0


# ---------------------------------------------------------------------------------------------------------------- patterns

def pattern(client, **kw):
    body = {"scenario": "current", "target": "spend_total", "from": "2027-01-01", "to": "2027-06-01", "pattern": {"type": "flat", "value": 500000}}
    body.update(kw)
    return client.post(f"{BASE}/patterns", json=body)


def test_preview_changes_nothing_and_reports_before_and_after(client, world, pg):
    v0 = table(client)["scenario"]["version"]
    n0 = len(audit_rows(pg))
    r = pattern(client, preview=True)
    assert r.status_code == 200
    j = r.get_json()
    assert j["preview"] is True and j["months"] == 6 and len(j["cells"]) == 6
    assert j["before"]["spend_total"] == 6 * 300000 and j["after"]["spend_total"] == 6 * 500000
    assert j["after"]["nc_rev"] == pytest.approx(j["before"]["nc_rev"] * 500 / 300) and j["after"]["nc_roas"] == pytest.approx(j["before"]["nc_roas"])
    assert j["cells"][0] == {"month": "2027-01-01", "before": 300000.0, "after": 500000.0}
    assert table(client)["scenario"]["version"] == v0 and len(audit_rows(pg)) == n0


def test_apply_writes_cells_with_a_note_one_version_and_one_audit_row_per_cell(client, world, pg):
    v0 = table(client)["scenario"]["version"]
    r = pattern(client)
    j = r.get_json()
    assert r.status_code == 200 and j["preview"] is False and j["changed"] == 6 and j["version"] == v0 + 1
    t = table(client)
    assert [at(t, "drivers.spend_total", m) for m in months(2027, 1, 7)] == [500000.0] * 6 + [300000.0]       # July 2027 keeps its own typed value
    rows = audit_rows(pg, "series.update")
    assert len(rows) == 6 and all(a["new"]["note"].startswith("flat 500,000.00, Jan 2027 - Jun 2027") for a in rows)
    assert rows[0]["actor"] == "JoshHoughton" and rows[0]["version"] == v0 + 1


def test_applying_the_same_pattern_twice_is_a_noop(client, world):
    pattern(client)
    v = table(client)["scenario"]["version"]
    j = pattern(client).get_json()
    assert j["changed"] == 0 and j["version"] == v


def test_a_pattern_is_undone_by_restoring_the_previous_version(client, world):
    before = table(client)
    v0 = before["scenario"]["version"]
    pattern(client, pattern={"type": "ramp", "value": 100000, "end_value": 900000})
    assert at(table(client), "drivers.spend_total", M(2027, 6)) == 900000.0
    r = client.post("/api/projection/scenarios/current/restore", json={"version": v0})
    assert r.status_code == 200
    after = table(client)
    assert row(after, "drivers.spend_total")["values"] == row(before, "drivers.spend_total")["values"]


def test_growth_step_and_ramp_shape_the_plan(client, world):
    pattern(client, pattern={"type": "growth", "value": 100000, "rate": 0.05})
    t = table(client)
    v = [at(t, "drivers.spend_total", m) for m in months(2027, 1, 6)]
    assert v[0] == 100000 and v[5] == pytest.approx(100000 * 1.05 ** 5, abs=0.01)
    pattern(client, target="nc_cpa", pattern={"type": "step", "value": 90, "then_value": 120, "step_month": "2027-04-01"})
    t = table(client)
    assert at(t, "drivers.nc_cpa", M(2027, 3)) == 90 and at(t, "drivers.nc_cpa", M(2027, 4)) == 120
    assert at(t, "nc_roas", M(2027, 4)) < at(t, "nc_roas", M(2027, 3))


@pytest.mark.parametrize("body,status,fragment", [
    ({"target": "rent"}, 400, "target"),
    ({"pattern": {"type": "flat", "value": -5}}, 400, "Marketing spend"),
    ({"pattern": {"type": "nope", "value": 1}}, 400, "type"),
    ({"pattern": "flat"}, 400, "object"),
    ({"from": "2027-02-01", "to": "2027-01-01"}, 400, "before"),
    ({"from": "2027-01-15"}, 400, "first-of-month"),
    ({"to": "2031-01-01"}, 400, "inside"),
    ({"from": None}, 400, "first-of-month"),
    ({"scenario": ""}, 400, "scenario"),
    ({"scenario": "nope"}, 404, "not found"),
    ({"target": "nc_cpa", "pattern": {"type": "flat", "value": 0}}, 400, "CPA"),
    ({"expected_version": "x"}, 400, "integer"),
])
def test_pattern_validation(client, world, body, status, fragment):
    r = pattern(client, **body)
    assert r.status_code == status and fragment in r.get_json()["error"]


def test_a_pattern_cannot_touch_months_that_hold_actuals(client, world):
    r = pattern(client, **{"from": "2026-05-01", "to": "2026-08-01"})
    assert r.status_code == 400 and "May 2026" in r.get_json()["error"] and "actuals" in r.get_json()["error"]
    r = pattern(client, target="amazon_rev", **{"from": "2026-05-01", "to": "2026-08-01"})
    assert r.status_code == 400
    r = pattern(client, target="nc_cpa", **{"from": "2026-05-01", "to": "2026-08-01"}, pattern={"type": "flat", "value": 100})
    assert r.status_code == 400


def test_expected_version_conflict(client, world):
    v = table(client)["scenario"]["version"]
    pattern(client)
    r = pattern(client, expected_version=v, pattern={"type": "flat", "value": 1})
    assert r.status_code == 409


def test_roas_slide_is_a_lever_that_governs_the_range(client, world, pg):
    body = {"scenario": "current", "target": "nc_roas", "from": "2027-01-01", "to": "2027-06-01", "pattern": {"type": "roas_slide", "start": 0.80, "end": 0.60}}
    pv = client.post(f"{BASE}/patterns", json={**body, "preview": True}).get_json()
    assert pv["after"]["nc_roas"] < pv["before"]["nc_roas"] and len(pv["cells"]) == 6
    assert table(client)["levers"]["roas_slide"]["enabled"] is False                  # preview wrote nothing
    r = client.post(f"{BASE}/patterns", json=body)
    assert r.status_code == 200 and r.get_json()["changed"] == 1
    t = table(client)
    assert t["levers"]["roas_slide"] == {"enabled": True, "from": "2027-01-01", "to": "2027-06-01", "start": 0.8, "end": 0.6}
    assert at(t, "nc_roas", M(2027, 1)) == pytest.approx(0.80) and at(t, "nc_roas", M(2027, 6)) == pytest.approx(0.60)
    assert t["cellmeta"]["drivers.nc_cpa"]["2027-03-01"] == "slid"
    assert at(t, "nc_roas", M(2027, 7)) == pytest.approx(70 / 95)                      # typed CPA applies again after the window
    assert [a["action"] for a in audit_rows(pg)][-1] == "scenario.params"
    assert client.post(f"{BASE}/patterns", json=body).get_json()["changed"] == 0       # same slide again: nothing to write


def test_a_cpa_override_inside_the_slide_window_wins(client, world, rw):
    client.post(f"{BASE}/patterns", json={"scenario": "current", "target": "nc_roas", "from": "2027-01-01", "to": "2027-06-01",
                                          "pattern": {"type": "roas_slide", "start": 0.8, "end": 0.6}})
    put_cells(rw, "drivers.nc_cpa", {M(2027, 3): 200.0}, kind="override")
    t = table(client)
    assert at(t, "drivers.nc_cpa", M(2027, 3)) == 200.0 and t["cellmeta"]["drivers.nc_cpa"]["2027-03-01"] == "typed"
    assert at(t, "drivers.nc_cpa", M(2027, 4)) != 95.0 and t["cellmeta"]["drivers.nc_cpa"]["2027-04-01"] == "slid"


def test_roas_slide_bad_specs(client, world):
    def go(spec, **kw):
        return client.post(f"{BASE}/patterns", json={"scenario": "current", "target": "nc_roas", "from": "2027-01-01", "to": "2027-06-01", "pattern": spec, **kw})
    assert go({"type": "flat", "value": 1}).status_code == 400
    assert go({"type": "roas_slide", "start": 0, "end": 0.6}).status_code == 400
    assert go({"type": "roas_slide", "start": 0.8, "end": 40}).status_code == 400


def test_a_pattern_on_a_clone_leaves_the_current_plan_alone(client, world):
    assert client.post("/api/projection/scenarios", json={"name": "Growth", "clone_from": "current"}).status_code == 201
    r = pattern(client, scenario="growth", pattern={"type": "flat", "value": 900000})
    assert r.status_code == 200
    assert at(table(client, "growth"), "drivers.spend_total", M(2027, 2)) == 900000.0
    assert at(table(client, "current"), "drivers.spend_total", M(2027, 2)) == 300000.0


# ---------------------------------------------------------------------------------------------------------------- levers

def test_levers_get_has_defaults_and_effective_values(client, world):
    j = client.get(f"{BASE}/levers").get_json()
    assert j["levers"]["channel_share"] == DEFAULT_SHARES and j["levers"]["sales_tax_rate"] == 0.07 and j["defaults"]["payback"]["product_cost_pct"] == 0.1726


def test_levers_put_changes_the_split_and_is_audited(client, world, pg):
    shares = {"amazon": 0.0, "meta": 0.9, "google": 0.1, "bing": 0.0, "tiktok": 0.0}
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"channel_share": shares, "sales_tax_rate": 0.0}})
    assert r.status_code == 200 and r.get_json()["levers"]["channel_share"] == shares
    t = table(client)
    assert at(t, "spend_meta", M(2026, 12)) == pytest.approx(270000) and at(t, "spend_amazon_ads", M(2026, 12)) == 0
    assert at(t, "sales_tax", M(2026, 12)) == 0
    a = audit_rows(pg, "scenario.params")[-1]
    assert a["new"]["params"]["drivers"]["channel_share"]["meta"] == 0.9


@pytest.mark.parametrize("levers,fragment", [
    ({"channel_share": {"amazon": 0.1, "meta": 0.5, "google": 0.1, "bing": 0, "tiktok": 0}}, "100%"),
    ({"channel_share": {"meta": 1}}, "exactly"),
    ({"channel_share": {"amazon": 0, "meta": 1.5, "google": 0, "bing": 0, "tiktok": 0}}, "fraction"),
    ({"sales_tax_rate": 0.9}, "sales_tax_rate"),
    ({"payback": {"product_cost_pct": 1.2}}, "product_cost_pct"),
    ({"payback": {"nope": 1}}, "payback takes"),
    ({"roas_slide": {"enabled": True, "start": 0.8, "end": 0.6, "from": "2027-06-01", "to": "2027-01-01"}}, "not be before"),
    ({"roas_slide": "on"}, "object"),
    ({"mystery": 1}, "unknown lever"),
    ({}, "non-empty"),
])
def test_levers_put_validation(client, world, levers, fragment):
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": levers})
    assert r.status_code == 400 and fragment in r.get_json()["error"]


def test_levers_put_needs_a_scenario_and_it_must_exist(client, world):
    assert client.put(f"{BASE}/levers", json={"levers": {"sales_tax_rate": 0.1}}).status_code == 400
    assert client.put(f"{BASE}/levers", json={"scenario": "nope", "levers": {"sales_tax_rate": 0.1}}).status_code == 404


def test_turning_the_slide_off_brings_the_typed_cpa_back(client, world):
    client.post(f"{BASE}/patterns", json={"scenario": "current", "target": "nc_roas", "from": "2027-01-01", "to": "2027-06-01",
                                          "pattern": {"type": "roas_slide", "start": 0.8, "end": 0.6}})
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"roas_slide": {"enabled": False}}})
    assert r.status_code == 200 and r.get_json()["levers"]["roas_slide"]["enabled"] is False
    t = table(client)
    assert at(t, "drivers.nc_cpa", M(2027, 3)) == 95.0 and t["cellmeta"]["drivers.nc_cpa"]["2027-03-01"] == "typed"


# ---------------------------------------------------------------------------------------------------------------- compare

def test_compare_lists_each_scenario_with_fy_numbers(client, world):
    client.post("/api/projection/scenarios", json={"name": "Growth", "clone_from": "current"})
    pattern(client, scenario="growth", pattern={"type": "flat", "value": 600000})
    j = client.get(f"{BASE}/compare?scenarios=current,growth").get_json()
    assert [s["slug"] for s in j["scenarios"]] == ["current", "growth"] and len(j["months"]) == 60
    cur, gro = j["scenarios"]
    assert gro["series"]["spend_total"][j["months"].index("2027-03-01")] == 600000.0 and cur["series"]["spend_total"][j["months"].index("2027-03-01")] == 300000.0
    assert gro["fy"]["2027"]["spend_total"] > cur["fy"]["2027"]["spend_total"] and gro["fy"]["2027"]["nc_rev"] > cur["fy"]["2027"]["nc_rev"]
    assert all(len(v) == 60 for s in j["scenarios"] for v in s["series"].values())
    json.dumps(j, allow_nan=False)


def test_compare_defaults_to_all_scenarios_and_caps_at_six(client, world):
    assert len(client.get(f"{BASE}/compare").get_json()["scenarios"]) == 1
    for i in range(6):
        client.post("/api/projection/scenarios", json={"name": f"S{i}", "clone_from": "current"})
    assert client.get(f"{BASE}/compare?scenarios=a,b,c,d,e,f,g").status_code == 400
    assert client.get(f"{BASE}/compare?scenarios=nope").status_code == 404


def test_numbers_in_the_payload_are_finite(client, world):
    t = table(client)
    for r in t["rows"]:
        assert all(v is None or isinstance(v, str) or math.isfinite(v) for v in r["values"]), r["key"]
