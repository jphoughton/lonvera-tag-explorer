"""One source of truth for the China landed cost per SKU: the scenario param `pnl.cogs.china_cost.<sku>`. Editing it in the P&L moves the inventory
supply module's product cost, and editing the product cost on the supply side moves the P&L's cost-of-goods model. The supply table
(proj_product_cost) is only the seed and fallback."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.pnl import store                              # noqa: E402
from test_pnl_api import P, audit_rows, data, DRIVERS            # noqa: E402,F401  (data is a fixture)

SUPPLY = "/api/projection/inventory-supply"


@pytest.fixture()
def shared(data):
    pg = data
    pg.execute("INSERT INTO proj_product_cost (product, sku, china_unit_cost, us_unit_cost) VALUES ('bull', 'LV-VBULL', 2.26, 3.92), "
               "('boneflex', 'DPCD1-GN187001', 2.59, 2.5) ON CONFLICT (product) DO UPDATE SET china_unit_cost = EXCLUDED.china_unit_cost, sku = EXCLUDED.sku")
    store.upsert_cogs_sku(pg, [{"sku": "LV-VBULL", "label": "Vital Bull", "china_unit_cost": 2.26, "us_unit_cost": 3.92, "sales_mix": 0.5},
                               {"sku": "DPCD1-GN187001", "label": "Boneflex", "china_unit_cost": 2.59, "us_unit_cost": 2.5, "sales_mix": 0.5}])
    store.upsert_settings(pg, [("cogs", {"us_share": 0.5, "price_per_bottle": 30.0, "current_price_per_bottle": 25.0, "mix": {}, "china_cost": {}, "us_cost": {}}, "test")])
    store.upsert_driver_defaults(pg, [("china_cogs", [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "cogs_model"}], "t", "t")])
    return pg


def supply_cost(client, product="bull"):
    d = client.get(f"{SUPPLY}/vendors?scenario=current").get_json()
    return next(c for c in d["product_costs"] if c["product"] == product)["china_unit_cost"]


def pnl_china(client, sku="LV-VBULL"):
    d = client.get(f"{P}/drivers").get_json()
    return next(s for s in d["cogs_model"]["all_skus"] if s["sku"] == sku)["china"], d["cogs_model"]["rate"]


def test_editing_the_cost_in_the_pnl_moves_the_inventory_supply_cost(client, shared):
    assert supply_cost(client) == 2.26 and pnl_china(client)[0] == 2.26
    r = client.put(f"{P}/settings", json={"scenario": "current", "settings": {"cogs": {"china_cost": {"LV-VBULL": 3.5}}}})
    assert r.status_code == 200
    assert supply_cost(client) == 3.5                                             # the supply module reads the P&L value
    assert supply_cost(client, "boneflex") == 2.59                                # other SKUs untouched
    client.put(f"{P}/settings", json={"scenario": "current", "settings": {"cogs": {"china_cost": {"LV-VBULL": None}}}})
    assert supply_cost(client) == 2.26                                            # back to the table seed


def test_editing_the_cost_on_the_supply_side_moves_the_pnl_cost_of_goods_model(client, shared):
    _, rate0 = pnl_china(client)
    r = client.put(f"{SUPPLY}/product-costs?scenario=current", json={"product": "bull", "china_unit_cost": 4.0})
    assert r.status_code == 200, r.get_data(as_text=True)
    china, rate1 = pnl_china(client)
    assert china == 4.0 and rate1 > rate0                                         # the P&L sees the same value and its COGS rate follows
    assert supply_cost(client) == 4.0
    own = client.get(f"{P}/drivers").get_json()["own_settings"]
    assert own["cogs"]["china_cost"] == {"LV-VBULL": 4.0}                          # one store: the scenario param, versioned and audited
    assert audit_rows(shared, "scenario.params")
