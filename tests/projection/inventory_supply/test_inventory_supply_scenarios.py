"""Scenario-aware variables over HTTP: a copy keeps its own changes (versioned, audited, restorable) and the plan of record is never touched by them."""
from __future__ import annotations

import pytest

import inv_supply_support as S

P = "/api/projection"
B = f"{P}/inventory-supply"


def clone(client, name="Bear case", src="current") -> str:
    r = client.post(f"{P}/scenarios", json={"name": name, "clone_from": src})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["scenario"]["slug"]


def own_params(pg, slug) -> dict:
    return pg.execute("SELECT params FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["params"].get("inventory_supply", {})


def version(pg, slug) -> int:
    return pg.execute("SELECT version FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["version"]


def pos(client, slug):
    return {p["ref"]: p for p in client.get(f"{B}/po?scenario={slug}").get_json()["pos"]}


def put_vars(client, slug, *items, **kw):
    return client.put(f"{B}/variables", json={"scenario": slug, "items": list(items), **kw})


def lever(name, value):
    return {"kind": "lever", "name": name, "value": value}


# ---------------------------------------------------------------------------------------------------------------- rows

def test_a_po_edit_in_a_copy_is_an_override_and_the_plan_of_record_keeps_its_value(client, pg, seeded):
    bear = clone(client)
    v0 = version(pg, bear)
    r = client.patch(f"{B}/po/7?scenario={bear}", json={"units": 12345, "status": "ordered"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["scenario"] == {"slug": bear, "version": v0 + 1} and r.get_json()["ref"] == "7"
    row = pos(client, bear)["7"]
    assert row["units"] == 12345.0 and row["status"] == "ordered" and row["overridden"] == ["status", "units"] and row["scenario_only"] is False
    assert pos(client, "current")["7"]["units"] == 20000.0 and pos(client, "current")["7"]["overridden"] == []
    assert float(pg.execute("SELECT units FROM proj_po WHERE id = 7").fetchone()["units"]) == 20000.0                    # the stored row is untouched
    assert own_params(pg, bear)["pos"]["7"] == {"units": 12345.0, "status": "ordered"}
    a = pg.execute("SELECT * FROM proj_audit WHERE scenario_slug = %s AND action = 'scenario.params' ORDER BY id DESC", (bear,)).fetchone()
    assert a["version"] == v0 + 1 and a["actor"] == "JoshHoughton"
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_supply.po.update'").fetchone()["n"] == 0     # no stored-row audit: nothing stored changed


def test_burn_and_cash_follow_the_copy_while_the_plan_of_record_does_not_move(client, pg, seeded, with_demand):
    bear = clone(client)
    before = client.get(f"{B}/outlay?scenario=current").get_json()
    client.patch(f"{B}/po/43?scenario={bear}", json={"units": 100000})                         # an auto-plan order, 23,000 units on the sheet
    o_bear = {r["key"]: r for r in client.get(f"{B}/outlay?scenario={bear}").get_json()["rows"]}["total"]["values"]
    o_cur = {r["key"]: r for r in before["rows"]}["total"]["values"]
    assert sum(o_bear) > sum(o_cur) + 100000
    assert {r["key"]: r for r in client.get(f"{B}/outlay?scenario=current").get_json()["rows"]}["total"]["values"] == o_cur


def test_rows_can_be_added_and_removed_in_a_copy_only(client, pg, seeded):
    bear = clone(client)
    body = {"product": "bull", "vendor": "KeyStone", "warehouse": "revamp", "units": 8000, "land_date": "2027-02-10"}
    r = client.post(f"{B}/po?scenario={bear}", json=body)
    assert r.status_code == 201 and r.get_json()["ref"] == "new:1"
    assert client.post(f"{B}/po?scenario={bear}", json=body).get_json()["ref"] == "new:2"
    rows = pos(client, bear)
    assert rows["new:1"]["scenario_only"] is True and rows["new:1"]["po_value"] == pytest.approx(8000 * 3.92) and rows["new:1"]["id"] == -1
    assert "new:1" not in pos(client, "current") and pg.execute("SELECT count(*) AS n FROM proj_po").fetchone()["n"] == 74
    assert client.delete(f"{B}/po/new:1?scenario={bear}").status_code == 200
    assert client.delete(f"{B}/po/8?scenario={bear}").status_code == 200                       # a stored row: removed for this scenario only
    assert "new:1" not in pos(client, bear) and "8" not in pos(client, bear) and "8" in pos(client, "current")
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE id = 8").fetchone()["n"] == 1
    assert client.delete(f"{B}/po/99999?scenario={bear}").status_code == 404 and client.delete(f"{B}/po/new:9?scenario={bear}").status_code == 404
    assert client.patch(f"{B}/po/abc", json={"units": 5}).status_code == 400                    # plan of record: a stored row is addressed by its number


@pytest.mark.parametrize("patch", [{"units": 0}, {"status": "bogus"}, {"land_date": "2020-01-01"}, {"order_date": "2030-01-01"}, {"bogus": 1}, {"deposit_pct": 3}])
def test_invalid_scenario_edits_are_rejected_and_change_nothing(client, pg, seeded, patch):
    bear = clone(client)
    v0 = version(pg, bear)
    r = client.patch(f"{B}/po/7?scenario={bear}", json=patch)
    assert r.status_code == 400 and "error" in r.get_json()
    assert version(pg, bear) == v0 and own_params(pg, bear) == {}


def test_transfers_follow_the_same_rules_in_a_copy(client, pg, seeded):
    bear = clone(client)
    assert client.patch(f"{B}/transfers/8?scenario={bear}", json={"units": 9999}).status_code == 200
    r = client.post(f"{B}/transfers?scenario={bear}", json={"product": "liver", "units": 700, "ship_date": "2027-01-10", "receive_date": "2027-01-25"})
    assert r.status_code == 201 and r.get_json()["ref"] == "new:1"
    assert client.post(f"{B}/transfers?scenario={bear}", json={"product": "liver", "units": 700, "ship_date": "2027-02-10", "receive_date": "2027-01-25"}).status_code == 400
    t = {x["ref"]: x for x in client.get(f"{B}/transfers?scenario={bear}").get_json()["transfers"]}
    assert t["8"]["units"] == 9999.0 and t["8"]["overridden"] == ["units"] and t["new:1"]["scenario_only"] is True
    cur = {x["ref"]: x for x in client.get(f"{B}/transfers?scenario=current").get_json()["transfers"]}
    assert cur["8"]["units"] == 8000.0 and "new:1" not in cur
    assert client.delete(f"{B}/transfers/8?scenario={bear}").status_code == 200 and "8" not in {x["ref"] for x in client.get(f"{B}/transfers?scenario={bear}").get_json()["transfers"]}


def test_vendor_price_reorder_and_cost_edits_in_a_copy_are_overrides_with_their_defaults_shown(client, pg, seeded):
    bear = clone(client)
    q = f"?scenario={bear}"
    assert client.put(f"{B}/vendors/China{q}", json={"lead_weeks": 6, "moq_units": 3000}).status_code == 200
    assert client.put(f"{B}/vendor-costs{q}", json={"vendor": "China", "product": "bull", "unit_cost": 2.5, "deposit_pct": 0.3}).status_code == 200
    assert client.put(f"{B}/reorder-params{q}", json={"location": "intl", "product": "bull", "lead_days": 45, "safety_days": 21}).status_code == 200
    assert client.put(f"{B}/product-costs{q}", json={"product": "bull", "china_unit_cost": 2.4}).status_code == 200
    assert client.put(f"{B}/vendor-costs{q}", json={"vendor": "Nobody", "product": "bull", "unit_cost": 1}).status_code == 400
    v = client.get(f"{B}/vendors{q}").get_json()
    china = next(x for x in v["vendors"] if x["vendor"] == "China")
    assert china["lead_weeks"] == 6.0 and china["overridden"] == ["lead_weeks", "moq_units"] and china["default"] == {"lead_weeks": 8.0, "moq_units": 5000.0}
    price = next(x for x in v["vendor_costs"] if x["vendor"] == "China" and x["product"] == "bull")
    assert price["unit_cost"] == 2.5 and price["default"]["unit_cost"] == 2.26 and price["overridden"] == ["deposit_pct", "unit_cost"]
    rp = next(x for x in v["reorder_params"] if x["location"] == "intl" and x["product"] == "bull")
    assert (rp["lead_days"], rp["safety_days"], rp["default"]["lead_days"]) == (45, 21, 60)
    assert next(x for x in v["product_costs"] if x["product"] == "bull")["china_unit_cost"] == 2.4
    cur = client.get(f"{B}/vendors?scenario=current").get_json()
    assert next(x for x in cur["vendors"] if x["vendor"] == "China")["lead_weeks"] == 8.0 and next(x for x in cur["vendors"] if x["vendor"] == "China")["overridden"] == []
    assert pg.execute("SELECT lead_weeks FROM proj_vendor_lead WHERE vendor = 'China'").fetchone()["lead_weeks"] == 8


# ---------------------------------------------------------------------------------------------------------------- variables endpoint

def test_variables_catalog_and_changes_with_revert_audit_and_history(client, pg, seeded, with_demand):
    bear = clone(client)
    cat = client.get(f"{B}/variables?scenario={bear}").get_json()
    assert cat["scenario"]["slug"] == bear and cat["scenario"]["is_default"] is False and len(cat["levers"]) == 22 and cat["changes"] == []
    fba = next(x for x in cat["levers"] if x["key"] == "fba_target_months")
    assert (fba["value"], fba["default"], fba["origin"], fba["unit"]) == (1.5, 1.5, "default", "months of next month's demand") and "B42" in fba["source"]
    to_send = lambda slug: {r["key"]: r for r in client.get(f"{B}/burn?location=amazon&scenario={slug}").get_json()["rows"]}["amazon.bull.to_send"]["values"][0]  # noqa: E731
    base_send = to_send(bear)
    v0 = version(pg, bear)
    r = put_vars(client, bear, lever("fba_target_months", 4.0), lever("po_round_units", 500))
    assert r.status_code == 200 and r.get_json()["scenario"] == {"slug": bear, "version": v0 + 1} and r.get_json()["changed"] == 2
    assert to_send(bear) > base_send and to_send("current") == base_send                                                    # moves the copy, not the plan of record
    cat = client.get(f"{B}/variables?scenario={bear}").get_json()
    assert {x["key"]: x["origin"] for x in cat["levers"]}["fba_target_months"] == "scenario" and {x["key"]: x["value"] for x in cat["levers"]}["po_round_units"] == 500
    assert {c["key"] for c in cat["changes"]} == set() and own_params(pg, bear) == {"fba_target_months": 4.0, "po_round_units": 500}
    # back to the default
    put_vars(client, bear, lever("fba_target_months", None))
    assert to_send(bear) == base_send and own_params(pg, bear) == {"po_round_units": 500}
    # history: the change is a version and can be restored
    hist = client.get(f"{P}/scenarios/{bear}/history").get_json()["history"]
    assert [h["version"] for h in hist][:3] == [v0 + 2, v0 + 1, v0]
    assert client.post(f"{P}/scenarios/{bear}/restore", json={"version": v0 + 1}).status_code == 200
    assert own_params(pg, bear) == {"fba_target_months": 4.0, "po_round_units": 500} and to_send(bear) > base_send
    acts = [a["action"] for a in pg.execute("SELECT action FROM proj_audit WHERE scenario_slug = %s ORDER BY id", (bear,)).fetchall()]
    assert acts.count("scenario.params") == 2 and "scenario.restore" in acts


@pytest.mark.parametrize("item", [lever("nope", 1), lever("fba_target_months", -1), lever("fba_target_months", 99), lever("fba_target_months", "x"), lever("transfer_lag_months", 1.5),
                                  lever("as_of", "tomorrow"), {"kind": "po", "id": "7", "field": "units", "value": 0}, {"kind": "price", "vendor": "Nobody", "product": "bull", "field": "unit_cost", "value": 1}])
def test_variable_changes_are_validated_and_write_nothing_when_one_is_bad(client, pg, seeded, item):
    bear = clone(client)
    v0 = version(pg, bear)
    r = put_vars(client, bear, lever("po_round_units", 500), item)
    assert r.status_code == 400 and "error" in r.get_json()
    assert version(pg, bear) == v0 and own_params(pg, bear) == {}


def test_variable_request_shape_limits_and_version_conflicts(client, pg, seeded):
    bear = clone(client)
    assert client.put(f"{B}/variables", json={"scenario": bear}).status_code == 400
    assert client.put(f"{B}/variables", json={"scenario": bear, "items": []}).status_code == 400
    assert client.put(f"{B}/variables", json={"scenario": bear, "items": [lever("po_round_units", 500)], "bogus": 1}).status_code == 400
    assert client.put(f"{B}/variables", json={"scenario": bear, "items": [lever("po_round_units", 500)] * 501}).status_code == 400
    assert client.put(f"{B}/variables", json={"scenario": "nope", "items": [lever("po_round_units", 500)]}).status_code == 404
    assert put_vars(client, bear, lever("po_round_units", 500), expected_version=version(pg, bear) + 5).status_code == 409
    assert put_vars(client, bear, lever("po_round_units", 500), expected_version=version(pg, bear)).status_code == 200
    n = version(pg, bear)
    assert put_vars(client, bear, lever("po_round_units", 500)).get_json()["changed"] == 0 and version(pg, bear) == n                # same value: no new version
    assert put_vars(client, "current", lever("fba_target_months", 2.0)).status_code == 200                                         # the plan of record's own levers are variables too
    assert own_params(pg, "current") == {"fba_target_months": 2.0}


def test_a_new_copy_starts_with_the_plan_of_records_variables_and_then_stands_alone(client, pg, seeded):
    put_vars(client, "current", lever("order_soon_days", 30), lever("transfer_lag_months", 2))
    bear = clone(client)                                                                          # copies are frozen: values are copied at clone time
    levers = {x["key"]: x for x in client.get(f"{B}/variables?scenario={bear}").get_json()["levers"]}
    assert (levers["order_soon_days"]["value"], levers["transfer_lag_months"]["value"]) == (30, 2)
    put_vars(client, "current", lever("order_soon_days", 45))                                      # a later change to the plan of record does not flow in
    put_vars(client, bear, lever("transfer_lag_months", 3))
    levers = {x["key"]: x for x in client.get(f"{B}/variables?scenario={bear}").get_json()["levers"]}
    assert (levers["order_soon_days"]["value"], levers["transfer_lag_months"]["value"], levers["transfer_lag_months"]["origin"]) == (30, 3, "scenario")
    cur = {x["key"]: x["value"] for x in client.get(f"{B}/variables?scenario=current").get_json()["levers"]}
    assert (cur["order_soon_days"], cur["transfer_lag_months"]) == (45, 2)


def test_variable_endpoints_need_the_apps_login_when_it_is_on(anon):
    assert anon.get(f"{B}/variables").status_code == 401
    assert anon.put(f"{B}/variables", json={"items": [lever("po_round_units", 500)]}).status_code == 401
    assert anon.patch(f"{B}/po/7?scenario=bear", json={"units": 5}).status_code == 401


# ---------------------------------------------------------------------------------------------------------------- plans and stale changes

def test_a_plan_accepted_into_a_copy_lives_in_the_copy_only(client, pg, seeded, with_demand):
    bear = clone(client)
    plan = client.get(f"{B}/plan/suggest?scenario={bear}").get_json()
    assert plan["summary"]["removed_pos"] == 36 and plan["pos"]
    body = {"pos": plan["pos"], "transfers": plan["transfers"], "replace_auto": True,
            "expect_replaced": {"pos": plan["summary"]["removed_pos"], "transfers": plan["summary"]["removed_transfers"]}}
    r = client.post(f"{B}/plan/accept?scenario={bear}", json=body)
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert out["removed_pos"] == 36 and len(out["added_pos"]) == len(plan["pos"]) and out["added_pos"][0] == "new:1"
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"] == 36                       # the stored auto-plan rows stay
    bear_rows = pos(client, bear)
    assert not any(p["source"] == "auto-plan" and not p["scenario_only"] for p in bear_rows.values())
    assert sum(1 for p in bear_rows.values() if p["scenario_only"]) == len(plan["pos"]) and len([p for p in pos(client, "current").values() if p["source"] == "auto-plan"]) == 36
    again = client.get(f"{B}/plan/suggest?scenario={bear}").get_json()
    assert again["summary"]["removed_pos"] == len(plan["pos"])
    stale = client.post(f"{B}/plan/accept?scenario={bear}", json={**body, "expect_replaced": {"pos": 1, "transfers": 0}})
    assert stale.status_code == 409 and "plan_stale" in stale.get_json()["error"]
    assert client.post(f"{B}/plan/accept?scenario={bear}", json={"pos": [], "transfers": []}).status_code == 400


def test_a_change_that_points_at_a_row_that_was_later_deleted_is_flagged_not_fatal(client, pg, seeded):
    bear = clone(client)
    client.patch(f"{B}/po/7?scenario={bear}", json={"units": 5555})
    assert client.delete(f"{B}/po/7").status_code == 200                                         # the plan of record deletes the stored row
    codes = {w["code"] for w in client.get(f"{B}/warnings?scenario={bear}").get_json()["warnings"]}
    assert "scenario_change_ignored" in codes
    assert client.get(f"{B}/summary?scenario={bear}").status_code == 200
    cat = client.get(f"{B}/variables?scenario={bear}").get_json()
    assert any("no longer exists" in n for n in cat["notes"]) and cat["changes"][0]["key"] == "7"
