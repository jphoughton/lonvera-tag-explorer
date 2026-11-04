"""One source of truth for the China landed cost per SKU: the scenario param `pnl.cogs.china_cost.<sku>` (the P&L reads and writes the same key).
The stored table row (proj_product_cost) is only the seed and fallback. Editing it in the P&L moves this module's product cost and stock value; editing it on
the supply side writes the P&L's key (versioned, audited, restorable), which moves the P&L's cost model."""
from __future__ import annotations

from datetime import date

import pytest

import inv_supply_support as S
from app.projection.inventory_supply import calc, overlay

P = "/api/projection"
B = f"{P}/inventory-supply"
TODAY = date(2026, 11, 3)


def pnl_params(**china) -> dict:
    return {"pnl": {"cogs": {"china_cost": dict(china)}}}


def run(inp):
    return calc.compute_supply(inp, S.flat_demand({("revamp", "bull"): 3000.0, ("amazon", "bull"): 1000.0, ("intl", "bull"): 2000.0}), TODAY)


def cost(client, product="bull", scenario="current"):
    d = client.get(f"{B}/vendors?scenario={scenario}").get_json()
    return next(c for c in d["product_costs"] if c["product"] == product)


def own_pnl(pg, slug="current") -> dict:
    return pg.execute("SELECT params FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["params"].get("pnl", {})


# ---------------------------------------------------------------------------------------------------------------- engine

def test_the_pnl_china_cost_wins_over_the_table_and_over_an_overlay_and_moves_stock_value():
    inp = S.mini_inputs()                                                      # table: bull 2.26 (LV-VBULL)
    base = run(inp)
    out = overlay.apply_overlay(inp, {**pnl_params(**{"LV-VBULL": 3.5}), "inventory_supply": {"product_costs": {"bull": {"china_unit_cost": 9.0}}}})
    pc = out.product_costs["bull"]
    assert (pc.china_unit_cost, pc.source) == (3.5, "pnl") and inp.product_costs["bull"].china_unit_cost == 2.26 and inp.product_costs["bull"].source == "table"
    moved = run(out)
    assert moved.value[date(2026, 11, 1)] == pytest.approx(base.value[date(2026, 11, 1)] * 3.5 / 2.26)
    assert moved.scalars["value_now.revamp"] == pytest.approx(10000 * 3.5) and base.scalars["value_now.revamp"] == pytest.approx(10000 * 2.26)


def test_without_a_pnl_value_the_table_and_the_overlay_apply_as_before():
    inp = S.mini_inputs()
    assert overlay.apply_overlay(inp, {}) is inp and overlay.apply_overlay(inp, pnl_params()) is inp
    assert overlay.apply_overlay(inp, pnl_params(**{"LV-OTHER": 5.0})) is inp                         # a SKU this module does not hold
    out = overlay.apply_overlay(inp, {"inventory_supply": {"product_costs": {"bull": {"china_unit_cost": 5.0}}}})
    assert (out.product_costs["bull"].china_unit_cost, out.product_costs["bull"].source) == (5.0, "table")


@pytest.mark.parametrize("bad", [None, -1, 1000.01, True, "3", float("nan")])
def test_a_missing_or_mistyped_pnl_value_is_ignored(bad):
    assert overlay.shared_china_costs(pnl_params(**{"LV-VBULL": bad})) == {}
    assert overlay.apply_overlay(S.mini_inputs(), pnl_params(**{"LV-VBULL": bad})).product_costs["bull"].china_unit_cost == 2.26
    assert overlay.shared_china_costs({"pnl": "x"}) == {} and overlay.shared_china_costs({"pnl": {"cogs": []}}) == {} and overlay.shared_china_costs(None) == {}


# ---------------------------------------------------------------------------------------------------------------- P&L side edits this module's cost

def test_editing_the_cost_in_the_pnl_moves_the_supply_cost_and_stock_value(client, pg, seeded, with_demand):
    before = client.get(f"{B}/summary").get_json()
    c0 = cost(client)
    assert (c0["china_unit_cost"], c0["source"], c0["sku"]) == (2.26, "table", "LV-VBULL") and cost(client, "liver")["source"] == "table"
    r = client.patch(f"{P}/scenarios/current", json={"params": pnl_params(**{"LV-VBULL": 3.5})})              # what the P&L's Settings card writes
    assert r.status_code == 200
    c1 = cost(client)
    assert (c1["china_unit_cost"], c1["source"], c1["default"]["china_unit_cost"]) == (3.5, "P&L cost model", 2.26)
    assert cost(client, "boneflex")["china_unit_cost"] == 2.59 and cost(client, "boneflex")["source"] == "table"          # other SKUs untouched
    after = client.get(f"{B}/summary").get_json()
    assert after["tldr"]["inventory_value_now"] > before["tldr"]["inventory_value_now"]
    assert {p["product"]: p["source"] for p in after["product_costs"]}["bull"] == "P&L cost model"
    v = {r["key"]: r for r in client.get(f"{B}/value").get_json()["rows"]}["revamp.bull"]["values"][0]
    assert v == pytest.approx(31581 * 3.5)                                                                           # Nov-26 closing at the P&L's cost
    client.patch(f"{P}/scenarios/current", json={"params": pnl_params(**{"LV-VBULL": None})})                         # back to the table seed
    assert cost(client)["china_unit_cost"] == 2.26 and cost(client)["source"] == "table"
    assert float(pg.execute("SELECT china_unit_cost FROM proj_product_cost WHERE product = 'bull'").fetchone()["china_unit_cost"]) == 2.26


# ---------------------------------------------------------------------------------------------------------------- supply side edits the P&L's key

def test_editing_the_cost_on_the_supply_page_writes_the_pnl_key_versioned_and_audited(client, pg, seeded):
    v0 = pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"]
    r = client.put(f"{B}/product-costs?scenario=current", json={"product": "bull", "china_unit_cost": 4.0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["scenario"] == {"slug": "current", "version": v0 + 1} and r.get_json()["source"] == "P&L cost model" and r.get_json()["sku"] == "LV-VBULL"
    assert own_pnl(pg) == {"cogs": {"china_cost": {"LV-VBULL": 4.0}}}                                              # the key the P&L reads
    assert float(pg.execute("SELECT china_unit_cost FROM proj_product_cost WHERE product = 'bull'").fetchone()["china_unit_cost"]) == 2.26   # the table stays the seed
    assert pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"].get("inventory_supply", {}).get("product_costs") is None
    assert cost(client)["china_unit_cost"] == 4.0 and cost(client)["source"] == "P&L cost model"
    a = pg.execute("SELECT * FROM proj_audit WHERE action = 'scenario.params' AND scenario_slug = 'current' ORDER BY id DESC").fetchone()
    assert a["version"] == v0 + 1 and a["actor"] == "JoshHoughton"
    # restorable through the core
    assert client.post(f"{P}/scenarios/current/restore", json={"version": v0}).status_code == 200
    assert own_pnl(pg).get("cogs", {}).get("china_cost", {}) == {} and cost(client)["china_unit_cost"] == 2.26
    # null goes back to the seed
    client.put(f"{B}/product-costs", json={"product": "bull", "china_unit_cost": 4.5})
    client.put(f"{B}/product-costs", json={"product": "bull", "china_unit_cost": None})
    assert own_pnl(pg) == {} and cost(client)["source"] == "table"


@pytest.mark.parametrize("value", [-1, 1000.5, "x", True])
def test_a_bad_cost_is_rejected_and_changes_nothing(client, pg, seeded, value):
    v0 = pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"]
    r = client.put(f"{B}/product-costs?scenario=current", json={"product": "bull", "china_unit_cost": value})
    assert r.status_code == 400 and own_pnl(pg) == {} and pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"] == v0
    assert client.put(f"{B}/product-costs", json={"product": "unicorn", "china_unit_cost": 1}).status_code == 400


def test_a_copy_keeps_its_own_cost_and_the_plan_of_record_is_untouched(client, pg, seeded):
    bear = client.post(f"{P}/scenarios", json={"name": "Bear", "clone_from": "current"}).get_json()["scenario"]["slug"]
    assert client.put(f"{B}/product-costs?scenario={bear}", json={"product": "liver", "china_unit_cost": 6.0}).status_code == 200
    assert (cost(client, "liver", bear)["china_unit_cost"], cost(client, "liver", bear)["source"]) == (6.0, "P&L cost model")
    assert cost(client, "liver", "current")["china_unit_cost"] == 4.16 and own_pnl(pg, "current") == {}
    assert own_pnl(pg, bear) == {"cogs": {"china_cost": {"LV-BLC180": 6.0}}}


def test_the_variables_endpoint_routes_a_cost_item_to_the_same_key(client, pg, seeded):
    r = client.put(f"{B}/variables", json={"scenario": "current", "items": [{"kind": "product_cost", "product": "boneflex", "field": "china_unit_cost", "value": 3.0},
                                                                            {"kind": "lever", "name": "fba_target_months", "value": 2.0}]})
    assert r.status_code == 200 and r.get_json()["changed"] == 2
    params = pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]
    assert params["pnl"]["cogs"]["china_cost"] == {"DPCD1-GN187001": 3.0} and params["inventory_supply"] == {"fba_target_months": 2.0}
    assert "product_costs" not in params["inventory_supply"] and cost(client, "boneflex")["china_unit_cost"] == 3.0
    cat = client.get(f"{B}/variables?scenario=current").get_json()["tables"]["product_costs"]
    assert {c["key"]: c["source"] for c in cat}["boneflex"] == "P&L cost model" and {c["key"]: c["source"] for c in cat}["bull"] == "table"
    assert client.put(f"{B}/variables", json={"scenario": "current", "items": [{"kind": "product_cost", "product": "bull", "field": "china_unit_cost", "value": -2}]}).status_code == 400


def test_a_product_without_a_sku_keeps_this_modules_own_storage(client, pg, seeded):
    pg.execute("UPDATE proj_product_cost SET sku = NULL WHERE product = 'flow'")
    assert client.put(f"{B}/product-costs", json={"product": "flow", "china_unit_cost": 1.25}).status_code == 200                # plan of record: the table row
    assert float(pg.execute("SELECT china_unit_cost FROM proj_product_cost WHERE product = 'flow'").fetchone()["china_unit_cost"]) == 1.25 and own_pnl(pg) == {}
    bear = client.post(f"{P}/scenarios", json={"name": "Bear", "clone_from": "current"}).get_json()["scenario"]["slug"]
    assert client.put(f"{B}/product-costs?scenario={bear}", json={"product": "flow", "china_unit_cost": 2.0}).status_code == 200      # a copy: this module's overlay
    assert cost(client, "flow", bear)["china_unit_cost"] == 2.0 and cost(client, "flow", bear)["source"] == "table" and own_pnl(pg, bear) == {}
