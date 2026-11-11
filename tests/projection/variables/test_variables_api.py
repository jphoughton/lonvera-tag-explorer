"""/api/projection/variables: listing, editing through the audited path, reset, validation, sensitivity, auth, and agreement with each module's own page."""
from __future__ import annotations

import pytest

from app.projection.variables import registry as R, sensitivity

P = "/api/projection/variables"
M = "/api/projection"


def put(client, items, **kw):
    return client.put(P, json={"scenario": "current", "items": items, **kw})


def listing(client, **q):
    r = client.get(P, query_string={"scenario": "current", **q})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def by_id(data):
    return {v["id"]: v for v in data["variables"]}


def test_modules_endpoint_lists_the_variables_page(client):
    mods = {m["key"]: m for m in client.get(f"{M}/modules").get_json()["modules"]}
    assert mods["variables"] == {"key": "variables", "label": "Variables", "group": "Settings", "built": True, "ui": "variables.js"}


def test_listing_shape_and_counts(client):
    d = listing(client)
    assert d["scenario"]["slug"] == "current" and d["shown"] == len(d["variables"]) == len(R.all_specs())
    assert d["counts"]["_total"]["IMPLEMENTED"] > 200 and d["changed_count"] == 0
    v = by_id(d)["cohorts.tail_decay"]
    assert v["value"] == pytest.approx(0.95) and v["default"] == pytest.approx(0.95) and v["changed"] is False and v["editable_inline"] is True
    assert v["sheet_ref"] == "Curves!C2" and v["where_used"] and v["page"] == "cohorts" and v["origin"] == "default"
    s = by_id(d)["drivers.spend_total"]
    assert s["kind"] == "series" and s["summary"]["cells"] >= 40 and s["editable_inline"] is False and s["edit_link"] == "/projection/drivers"
    t = by_id(d)["inventory_supply.purchase_orders"]
    assert t["kind"] == "table" and t["summary"]["text"].endswith("orders")
    assert by_id(d)["pnl.payment_processing_rate"]["status"] == "MISSING"


def test_filters(client):
    d = listing(client, module="financing")
    assert d["shown"] == len(R.for_module("financing")) and {v["module"] for v in d["variables"]} == {"financing"}
    d = listing(client, q="tail decay")
    assert any(v["id"] == "cohorts.tail_decay" for v in d["variables"]) and d["shown"] < 10
    assert client.get(P, query_string={"module": "nope"}).status_code == 400
    assert client.get(P, query_string={"scenario": "nope"}).status_code == 404


def test_edit_changes_the_value_the_flag_the_version_and_writes_audit(client, restore):
    v0 = client.get(f"{M}/scenarios").get_json()["scenarios"][0]["version"]
    r = put(client, [{"id": "cohorts.tail_decay", "value": 0.9}])
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["scenario"]["version"] == v0 + 1 and body["variables"][0]["value"] == pytest.approx(0.9) and body["variables"][0]["changed"] is True
    d = listing(client, changed="1")
    assert [v["id"] for v in d["variables"]] == ["cohorts.tail_decay"] and d["changed_count"] == 1
    audit = client.get(f"{M}/audit", query_string={"scenario": "current", "limit": 5}).get_json()["audit"]
    row = next(a for a in audit if a["action"] == "scenario.params")
    assert row["new"]["params"]["cohorts"]["tail_decay"] == 0.9 and row["version"] == v0 + 1
    hist = client.get(f"{M}/scenarios/current/history").get_json()["history"]
    assert hist[0]["version"] == v0 + 1
    # undo through the core restore: the variable is back
    assert client.post(f"{M}/scenarios/current/restore", json={"version": v0}).status_code == 200
    assert listing(client, changed="1")["changed_count"] == 0


def test_validation_errors_are_plain_and_nothing_is_written(client, restore):
    before = listing(client)["scenario"]["version"]
    for items, status, word in (
            ([{"id": "cohorts.tail_decay", "value": 7}], 400, "at most"),
            ([{"id": "cohorts.n_cohorts", "value": 2.5}], 400, "whole number"),
            ([{"id": "cohorts.tail_decay", "value": "abc"}], 400, "number"),
            ([{"id": "financing.debt.mode", "value": "weekly"}], 400, "choose one of"),
            ([{"id": "no.such_variable", "value": 1}], 404, "unknown variable"),
            ([{"id": "cohorts.cohort_table", "value": 1}], 400, "own page"),
            ([{"id": "pnl.payment_processing_rate", "value": 0.05}], 400, "own page")):
        r = put(client, items)
        assert r.status_code == status and word in r.get_json()["error"], (items, r.get_json())
    assert client.put(P, json={"scenario": "current", "items": []}).status_code == 400
    assert client.put(P, json={"scenario": "current", "items": [{"id": "cohorts.tail_decay"}]}).status_code == 400
    assert client.put(P, json={"scenario": "current", "items": [{"id": "cohorts.tail_decay", "value": 0.9}], "bogus": 1}).status_code == 400
    assert put(client, [{"id": "cohorts.tail_decay", "value": 0.9}], expected_version=before + 5).status_code == 409
    assert listing(client)["scenario"]["version"] == before


def test_bulk_edit_is_one_version_and_reset_is_per_group(client, restore):
    v0 = listing(client)["scenario"]["version"]
    items = [{"id": "cohorts.tail_decay", "value": 0.9}, {"id": "cohorts.n_cohorts", "value": 6}, {"id": "pnl.headcount.base", "value": 20000}]
    assert put(client, items).status_code == 200
    d = listing(client)
    assert d["scenario"]["version"] == v0 + 1 and d["changed_count"] == 3
    r = client.post(f"{P}/reset", json={"scenario": "current", "module": "cohorts"})
    assert r.status_code == 200 and sorted(r.get_json()["reset"]) == ["cohorts.n_cohorts", "cohorts.tail_decay"]
    assert listing(client)["changed_count"] == 1
    r = client.post(f"{P}/reset", json={"scenario": "current", "all": True})
    assert r.get_json()["reset"] == ["pnl.headcount.base"] and listing(client)["changed_count"] == 0
    assert client.post(f"{P}/reset", json={"scenario": "current"}).status_code == 400


def test_single_variable_route_and_null_goes_back_to_the_sheet(client, restore):
    assert client.put(f"{P}/drivers.sales_tax_rate", json={"scenario": "current", "value": 0.09}).status_code == 200
    assert by_id(listing(client))["drivers.sales_tax_rate"]["changed"]
    assert client.put(f"{P}/drivers.sales_tax_rate", json={"scenario": "current", "value": None}).status_code == 200
    assert not by_id(listing(client))["drivers.sales_tax_rate"]["changed"]
    assert client.put(f"{P}/drivers.sales_tax_rate", json={"scenario": "current"}).status_code == 400


def test_a_copy_is_edited_without_touching_the_plan_of_record(client, restore):
    c = client.post(f"{M}/scenarios", json={"name": "Variables copy", "clone_from": "current"}).get_json()["scenario"]
    r = client.put(P, json={"scenario": c["slug"], "items": [{"id": "financing.tax.rate", "value": 0.3}]})
    assert r.status_code == 200
    copy = client.get(P, query_string={"scenario": c["slug"]}).get_json()
    assert by_id(copy)["financing.tax.rate"]["value"] == pytest.approx(0.3) and copy["changed_count"] == 1
    assert by_id(listing(client))["financing.tax.rate"]["value"] == pytest.approx(0.21)


def test_channel_shares_edit_rebalances_and_the_spend_plan_page_agrees(client, restore):
    assert put(client, [{"id": "drivers.channel_share.meta", "value": 0.8}]).status_code == 200
    lv = client.get(f"{M}/drivers/levers", query_string={"scenario": "current"}).get_json()["levers"]["channel_share"]
    assert sum(lv.values()) == pytest.approx(1.0, abs=1e-6) and lv["meta"] == pytest.approx(0.8)
    assert put(client, [{"id": "drivers.channel_share.meta", "value": 1.5}]).status_code == 400


def test_sales_tax_agrees_in_both_directions(client, restore):
    put(client, [{"id": "drivers.sales_tax_rate", "value": 0.08}])
    assert client.get(f"{M}/drivers/levers", query_string={"scenario": "current"}).get_json()["levers"]["sales_tax_rate"] == pytest.approx(0.08)
    r = client.put(f"{M}/drivers/levers", json={"scenario": "current", "levers": {"sales_tax_rate": 0.06}})
    assert r.status_code == 200
    assert by_id(listing(client))["drivers.sales_tax_rate"]["value"] == pytest.approx(0.06)


def test_cohort_levers_agree_in_both_directions(client, restore):
    put(client, [{"id": "cohorts.tail_decay", "value": 0.92}])
    assert client.get(f"{M}/cohorts/levers", query_string={"scenario": "current"}).get_json()["levers"]["tail_decay"] == pytest.approx(0.92)
    assert client.put(f"{M}/cohorts/levers", json={"scenario": "current", "levers": {"n_cohorts": 5}}).status_code == 200
    assert by_id(listing(client))["cohorts.n_cohorts"]["value"] == 5


def test_payback_inputs_agree_between_the_variables_page_the_spend_plan_and_cash_and_debt(client, restore):
    assert put(client, [{"id": "drivers.payback.product_cost_pct", "value": 0.2}]).status_code == 200
    lv = client.get(f"{M}/drivers/levers", query_string={"scenario": "current"}).get_json()["levers"]["payback"]
    assert lv["product_cost_pct"] == pytest.approx(0.2)
    inp = client.get(f"{M}/financing/inputs", query_string={"scenario": "current"}).get_json()
    assert "0.2" in str(inp)
    r = client.put(f"{M}/financing/inputs", json={"scenario": "current", "params": {"payback": {"fulfillment": 6.5}}})
    assert r.status_code == 200
    assert by_id(listing(client))["drivers.payback.fulfillment_per_order"]["value"] == pytest.approx(6.5)
    assert client.put(f"{M}/drivers/levers", json={"scenario": "current", "levers": {"payback": {"horizon_months": 24}}}).status_code == 200
    d = by_id(listing(client))
    assert d["drivers.payback.horizon_months"]["value"] == 24 and d["drivers.payback.horizon_months"]["changed"]
    assert d["financing.payback.aov"]["value"] is None and d["financing.payback.aov"]["computed"]


def test_demand_rate_override_saves_above_one_and_the_demand_page_agrees(client, restore):
    assert put(client, [{"id": "inventory_demand.revamp.bull.rate", "value": 280.0}]).status_code == 200
    d = by_id(listing(client))["inventory_demand.revamp.bull.rate"]
    assert d["value"] == 280.0 and d["changed"] and d["editable_inline"] and d["status"] == "IMPLEMENTED"
    assert "280" in str(client.get(f"{M}/inventory-demand/inputs", query_string={"scenario": "current"}).get_json())


def test_china_cost_edit_reaches_the_inventory_module(client, restore):
    assert put(client, [{"id": "pnl.cogs.china_cost.lv_vbull", "value": 2.5}]).status_code == 200
    v = client.get(f"{M}/inventory-supply/variables", query_string={"scenario": "current"}).get_json()
    assert str(v).count("2.5") >= 1


def test_demand_and_pnl_settings_agree(client, restore):
    put(client, [{"id": "inventory_demand.revamp.baseline_days", "value": 21}, {"id": "pnl.headcount.base", "value": 18000}])
    inp = client.get(f"{M}/inventory-demand/inputs", query_string={"scenario": "current"}).get_json()
    assert "21" in str(inp)
    assert client.put(f"{M}/pnl/settings", json={"scenario": "current", "settings": {"engine": {"rate_window_months": 3}}}).status_code == 200
    assert by_id(listing(client))["pnl.engine.rate_window_months"]["value"] == 3
    assert by_id(listing(client))["pnl.headcount.base"]["value"] == 18000


def test_supply_table_cells_go_through_the_module_overlay_and_agree(client, restore):
    r = put(client, [{"id": "inventory_supply.vendor.china.lead_weeks", "value": 10}, {"id": "inventory_supply.fba_target_months", "value": 2.0},
                     {"id": "inventory_supply.price.china.bull.unit_cost", "value": 2.5}])
    assert r.status_code == 200, r.get_json()
    v = client.get(f"{M}/inventory-supply/variables", query_string={"scenario": "current"}).get_json()
    china = next(x for x in v["tables"]["vendors"] if x["key"] == "China")
    assert china["fields"]["lead_weeks"]["value"] == 10 and china["overridden"] == ["lead_weeks"]
    assert next(x for x in v["levers"] if x["key"] == "fba_target_months")["value"] == 2.0
    d = by_id(listing(client))
    assert d["inventory_supply.vendor.china.lead_weeks"]["value"] == 10 and d["inventory_supply.vendor.china.lead_weeks"]["changed"]
    assert client.put(f"{M}/inventory-supply/variables", json={"scenario": "current", "items": [{"kind": "vendor", "vendor": "China", "field": "lead_weeks", "value": 6}]}).status_code == 200
    assert by_id(listing(client))["inventory_supply.vendor.china.lead_weeks"]["value"] == 6
    r = client.post(f"{P}/reset", json={"scenario": "current", "module": "inventory_supply"})
    assert r.status_code == 200 and listing(client)["changed_count"] == 0


def test_pnl_driver_edit_sets_a_fixed_amount_and_resets(client, restore):
    d0 = by_id(listing(client))["pnl.driver.insurance"]
    assert d0["value"] == pytest.approx(1200.0) and not d0["changed"]
    assert put(client, [{"id": "pnl.driver.insurance", "value": 2000}]).status_code == 200
    d1 = by_id(listing(client))["pnl.driver.insurance"]
    assert d1["value"] == pytest.approx(2000.0) and d1["changed"] and d1["origin"] == "scenario"
    drv = client.get(f"{M}/pnl/drivers", query_string={"scenario": "current"}).get_json()
    assert "2000" in str(drv)
    assert put(client, [{"id": "pnl.driver.insurance", "value": None}]).status_code == 200
    assert not by_id(listing(client))["pnl.driver.insurance"]["changed"]
    r = put(client, [{"id": "pnl.driver.fulfillment_services", "value": 0.05}])
    assert r.status_code == 200 and by_id(listing(client))["pnl.driver.fulfillment_services"]["value"] == pytest.approx(0.05)
    assert put(client, [{"id": "pnl.driver.other_business_expenses", "value": 0.01}]).status_code == 400          # the module's editor rejects a parent line


def test_pacing_levers_agree(client, restore):
    put(client, [{"id": "pacing.pace_good", "value": 1.05}])
    lv = client.get(f"{M}/pacing/levers", query_string={"scenario": "current"})
    assert lv.status_code == 200 and "1.05" in str(lv.get_json())


def test_sensitivity_shape_and_cache(client, restore):
    sensitivity.clear()
    d = client.get(f"{P}/sensitivity", query_string={"scenario": "current"}).get_json()
    assert d["tested"] >= 60 and d["moving"] >= 30 and d["truncated"] is False and d["year"] == 2027
    top = d["rows"][0]
    assert top["noi_swing"] >= d["rows"][5]["noi_swing"] and top["down"]["noi"] != 0
    ids = [r["id"] for r in d["rows"]]
    assert "drivers.channel_share.meta" in ids[:12] and "pnl.cogs.price_per_bottle" in ids[:12]
    again = client.get(f"{P}/sensitivity", query_string={"scenario": "current"}).get_json()
    assert again is not None and again["seconds"] == d["seconds"]                  # served from the cache
    put(client, [{"id": "cohorts.tail_decay", "value": 0.9}])
    after = client.get(f"{P}/sensitivity", query_string={"scenario": "current"}).get_json()
    assert after["version"] == d["version"] + 1 and after["base"]["noi"] != d["base"]["noi"]


def test_sensitivity_cap_truncates_and_says_so(vdb, restore):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(vdb, row_factory=dict_row) as conn:
        d = sensitivity.run(conn, "current", cap=0.0, use_cache=False)
    assert d["truncated"] is True and d["tested"] == 0 and d["rows"] == []


def test_sensitivity_matches_the_stored_change_exactly(client, restore):
    d = client.get(f"{P}/sensitivity", query_string={"scenario": "current"}).get_json()
    row = next(r for r in d["rows"] if r["id"] == "drivers.sales_tax_rate")
    up = row["up"]["value"]
    put(client, [{"id": "drivers.sales_tax_rate", "value": up}])
    run = client.get(f"{M}/run", query_string={"scenario": "current"}).get_json()["headline"]["fy"]["2027"]["noi"]
    assert run - d["base"]["noi"] == pytest.approx(row["up"]["noi"], abs=1.0)


def test_login_guard_covers_the_variables_routes(app_login):
    c = app_login.test_client()
    assert c.get(P).status_code == 401 and c.put(P, json={"items": []}).status_code == 401 and c.get(f"{P}/sensitivity").status_code == 401
    from conftest import PASSWORD
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    assert c.get(P).status_code == 200
    assert c.put(P, json={"scenario": "current", "items": [{"id": "cohorts.tail_decay", "value": 0.9}]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.put(P, data="x=1", content_type="application/x-www-form-urlencoded").status_code == 415


def test_page_is_served_and_noindex(client):
    r = client.get("/projection/variables")
    assert r.status_code == 200 and r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/static/js/projection/variables.js").status_code == 200


def test_modeler_ebitda_setting_agrees_with_the_modeler_page(client, restore):
    assert put(client, [{"id": "modeler.ebitda_addbacks", "value": ["interest_expense", "uncategorized"]}]).status_code == 200
    d = by_id(listing(client))["modeler.ebitda_addbacks"]
    assert d["value"] == ["interest_expense", "uncategorized"] and d["changed"] and d["module"] == "modeler"
    r = client.put(f"{M}/modeler/settings", json={"scenario": "current", "ebitda_addbacks": ["marketing_influencers"]})
    assert r.status_code == 200, r.get_json()
    assert by_id(listing(client))["modeler.ebitda_addbacks"]["value"] == ["marketing_influencers"]
    assert put(client, [{"id": "modeler.ebitda_addbacks", "value": ["rent"]}]).status_code == 400
    assert put(client, [{"id": "modeler.ebitda_addbacks", "value": None}]).status_code == 200
    assert not by_id(listing(client))["modeler.ebitda_addbacks"]["changed"]
