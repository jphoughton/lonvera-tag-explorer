"""One store for the payback inputs: product cost, fulfillment and horizon live in params['drivers']['payback'] (the Spend plan page's store),
the legacy financing copies are a fallback only, and the average order value and cost per customer follow the last closed month until typed."""
from __future__ import annotations

from datetime import date

import pytest

import fin_helpers as H
from app.projection import registry, scenarios
from app.projection.drivers import model as drivers_model
from app.projection.financing import compute, load_inputs
from app.projection.financing import params as P
from etl import projection_financing as seed

@pytest.fixture(autouse=True)
def only_financing(monkeypatch):
    real = registry.discover
    monkeypatch.setattr(registry, "discover", lambda package=None, refresh=False: {k: v for k, v in real(package, refresh).items() if k == "financing"})


API = "/api/projection"
F = f"{API}/financing"


def sql_params(pg, slug="current"):
    return pg.execute("SELECT params FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["params"]


def set_params(pg, params, slug="current"):
    from psycopg.types.json import Jsonb
    pg.execute("UPDATE proj_scenarios SET params = %s WHERE slug = %s", (Jsonb(params), slug))


# ------------------------------------------------------------------------------------------------------------------ resolution

def test_the_single_store_wins_over_the_legacy_copy_which_wins_over_the_default():
    base = P.effective({})["payback"]
    assert (base["product_cost_pct"], base["fulfillment"], base["max_age"]) == (0.1726, 5.53, 36)
    legacy = P.effective({"financing": {"payback": {"product_cost_pct": 0.2, "fulfillment": 6.0, "max_age": 24}}})["payback"]
    assert (legacy["product_cost_pct"], legacy["fulfillment"], legacy["max_age"]) == (0.2, 6.0, 24)
    both = P.effective({"financing": {"payback": {"product_cost_pct": 0.2, "fulfillment": 6.0}},
                        "drivers": {"payback": {"product_cost_pct": 0.3, "fulfillment_per_order": 7.0, "horizon_months": 18}}})["payback"]
    assert (both["product_cost_pct"], both["fulfillment"], both["max_age"]) == (0.3, 7.0, 18)


def test_a_stored_value_outside_the_spend_plan_ranges_is_ignored():
    drivers = {"payback": {"product_cost_pct": 1.5, "fulfillment_per_order": -1, "horizon_months": 61}}
    got = P.effective({"financing": {"payback": {"product_cost_pct": 0.2}}, "drivers": drivers})["payback"]
    assert got["product_cost_pct"] == 0.2 and got["fulfillment"] == 5.53 and got["max_age"] == 36          # legacy, then default
    assert P.effective({"drivers": {"payback": {"horizon_months": 12.5}}})["payback"]["max_age"] == 36      # whole months only


def test_sources_say_where_each_shared_input_comes_from():
    assert P.payback_sources({}) == {"product_cost_pct": "default", "fulfillment": "default", "max_age": "default"}
    got = P.payback_sources({"financing": {"payback": {"fulfillment": 6.0}}, "drivers": {"payback": {"product_cost_pct": 0.2}}})
    assert got == {"product_cost_pct": "drivers", "fulfillment": "financing", "max_age": "default"}


def test_split_patch_moves_the_shared_inputs_and_clears_the_legacy_keys():
    fin, drv = P.split_payback_patch({"payback": {"product_cost_pct": 0.2, "fulfillment": None, "max_age": 24, "aov": 80.0}, "cash": {"floor": 1}})
    assert drv == {"payback": {"product_cost_pct": 0.2, "fulfillment_per_order": None, "horizon_months": 24}}
    assert fin["payback"] == {"aov": 80.0, "product_cost_pct": None, "fulfillment": None, "max_age": None} and fin["cash"] == {"floor": 1}
    untouched = {"payback": {"aov": 80.0, "curve": "goal"}}
    assert P.split_payback_patch(untouched) == (untouched, {})
    assert P.split_payback_patch({"tax": {"rate": 0.2}}) == ({"tax": {"rate": 0.2}}, {})


def test_the_module_computes_with_the_single_store(dconn, pg):
    base = H.compute_with_snapshot(dconn)
    assert base.payback_inputs["product_cost_pct"] == 0.1726 and base.payback_inputs["shared_sources"]["product_cost_pct"] == "default"
    pg.execute("UPDATE proj_scenarios SET params = params || %s::jsonb WHERE slug = 'current'",
               ('{"drivers": {"payback": {"product_cost_pct": 0.25, "fulfillment_per_order": 6.5, "horizon_months": 30}}}',))
    fin = H.compute_with_snapshot(dconn)
    assert (fin.payback_inputs["product_cost_pct"], fin.payback_inputs["fulfillment"], fin.payback_inputs["max_age"]) == (0.25, 6.5, 30)
    assert fin.payback.cm_first == pytest.approx(fin.payback.aov * 0.75 - 6.5) and len(fin.payback.cumulative) == 31
    assert fin.payback.months > base.payback.months and fin.payback_inputs["shared_sources"]["product_cost_pct"] == "drivers"


# ------------------------------------------------------------------------------------------------------------------ editing on either page moves both

def test_editing_on_cash_and_debt_moves_the_spend_plan(client, pg, sheet_payback):
    r = client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": {"product_cost_pct": 0.25, "fulfillment": 6.5, "max_age": 30}}})
    assert r.status_code == 200
    stored = sql_params(pg)
    assert stored["drivers"]["payback"] == {"product_cost_pct": 0.25, "fulfillment_per_order": 6.5, "horizon_months": 30}
    assert not {"product_cost_pct", "fulfillment", "max_age"} & set(stored["financing"]["payback"])          # no second copy
    lv = drivers_model.parse_levers(stored).to_dict()["payback"]                                      # what the Spend plan page reads
    assert (lv["product_cost_pct"], lv["fulfillment_per_order"], lv["horizon_months"]) == (0.25, 6.5, 30)
    j = client.get(f"{F}/payback").get_json()
    assert j["inputs"]["product_cost_pct"] == 0.25 and j["inputs"]["fulfillment"] == 6.5 and j["inputs"]["max_age"] == 30
    assert j["inputs"]["shared_sources"] == {"product_cost_pct": "drivers", "fulfillment": "drivers", "max_age": "drivers"}
    assert len(j["result"]["cumulative"]) == 31
    inputs = client.get(f"{F}/inputs").get_json()
    assert inputs["payback_sources"]["product_cost_pct"] == "drivers" and inputs["params"]["payback"]["product_cost_pct"] == 0.25


def test_editing_on_the_spend_plan_moves_cash_and_debt(client, sheet_payback):
    before = client.get(f"{F}/payback").get_json()
    r = client.put(f"{API}/drivers/levers", json={"scenario": "current", "levers": {"payback": {"product_cost_pct": 0.30, "horizon_months": 24}}})
    assert r.status_code == 200, r.get_json()
    after = client.get(f"{F}/payback").get_json()
    assert after["inputs"]["product_cost_pct"] == 0.30 and len(after["result"]["cumulative"]) == 25
    assert after["result"]["cm_first"] < before["result"]["cm_first"]
    assert client.get(f"{F}/inputs").get_json()["payback_sources"]["product_cost_pct"] == "drivers"
    # and the Spend plan reads the same value back
    assert client.get(f"{API}/drivers/levers").get_json()["levers"]["payback"]["product_cost_pct"] == 0.30


def test_clearing_on_cash_and_debt_returns_both_pages_to_the_default(client, pg):
    client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": {"product_cost_pct": 0.25}}})
    assert sql_params(pg)["drivers"]["payback"]["product_cost_pct"] == 0.25
    r = client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": {"product_cost_pct": None}}})
    assert r.status_code == 200
    assert "product_cost_pct" not in sql_params(pg).get("drivers", {}).get("payback", {})
    assert client.get(f"{F}/payback").get_json()["inputs"]["product_cost_pct"] == 0.1726
    assert drivers_model.parse_levers(sql_params(pg)).to_dict()["payback"]["product_cost_pct"] == 0.1726


@pytest.mark.parametrize("payback", [{"product_cost_pct": 1.0}, {"product_cost_pct": -0.1}, {"fulfillment": 1001}, {"max_age": 61}, {"max_age": 12.5}])
def test_shared_inputs_keep_the_spend_plan_ranges(client, pg, payback):
    r = client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": payback}})
    assert r.status_code == 400, payback
    assert "drivers" not in sql_params(pg)


def test_a_scenario_saved_before_the_merge_still_reads_its_financing_copy(client, pg):
    set_params(pg, {"financing": {"payback": {"product_cost_pct": 0.21}}})                  # an older version restored from history
    j = client.get(f"{F}/payback").get_json()
    assert j["inputs"]["product_cost_pct"] == 0.21 and j["inputs"]["shared_sources"]["product_cost_pct"] == "financing"
    client.put(f"{API}/drivers/levers", json={"scenario": "current", "levers": {"payback": {"product_cost_pct": 0.33}}})
    assert client.get(f"{F}/payback").get_json()["inputs"]["product_cost_pct"] == 0.33   # the single store takes over


# ------------------------------------------------------------------------------------------------------------------ average order value and cost per customer

def test_blank_order_value_and_cost_follow_the_last_closed_month_and_a_typed_value_overrides(dconn, pg):
    stored = sql_params(pg)
    assert "aov" not in stored["financing"]["payback"] and "cac" not in stored["financing"]["payback"]        # the seed leaves them blank
    drivers = H.snapshot_drivers()
    scn = scenarios.get_scenario(dconn, "current")
    fin = compute(scn, load_inputs(dconn, scn), pnl=H.snapshot_pnl(), inventory=H.snapshot_inventory(), cohorts=H.snapshot_cohorts(), drivers=drivers)
    last = date(2026, 6, 1)
    assert fin.payback_inputs["aov"] == drivers.nc_aov[last] and fin.payback_inputs["cac"] == drivers.nc_cpa[last]
    assert fin.payback_inputs["aov_source"] == "last closed month" == fin.payback_inputs["cac_source"]
    scn.params = P.deep_merge(scn.params, {"financing": {"payback": {"aov": 80.0, "cac": 90.0}}})
    typed = compute(scn, load_inputs(dconn, scn), pnl=H.snapshot_pnl(), inventory=H.snapshot_inventory(), cohorts=H.snapshot_cohorts(), drivers=drivers)
    assert typed.payback_inputs["aov"] == 80.0 and typed.payback_inputs["aov_source"] == "scenario"


# ------------------------------------------------------------------------------------------------------------------ migration 161

def run_migration(pg):
    pg.execute(seed.SINGLE_STORE_MIGRATION.read_text())


def test_migration_drops_seeded_copies_and_moves_changed_values_to_the_single_store(pg):
    set_params(pg, {"financing": {"payback": {"aov": 69.15600858369098, "cac": 105.4916738197425, "product_cost_pct": 0.1726, "fulfillment": 5.53, "max_age": 36,
                                              "curve": "goal"}, "cash": {"floor": 5}}})
    run_migration(pg)
    p = sql_params(pg)
    assert p["financing"]["payback"] == {"curve": "goal"} and p["financing"]["cash"] == {"floor": 5} and "drivers" not in p


def test_migration_keeps_what_somebody_changed(pg):
    set_params(pg, {"financing": {"payback": {"aov": 80.5, "cac": 99.0, "product_cost_pct": 0.2, "fulfillment": 6.0, "max_age": 24}},
                    "drivers": {"payback": {"horizon_months": 12}, "sales_tax_rate": 0.07}})
    run_migration(pg)
    p = sql_params(pg)
    assert p["financing"]["payback"] == {"aov": 80.5, "cac": 99.0}                                    # typed order value and cost stay
    assert p["drivers"]["payback"] == {"horizon_months": 24, "product_cost_pct": 0.2, "fulfillment_per_order": 6.0}   # changed values moved; the rest untouched
    assert p["drivers"]["sales_tax_rate"] == 0.07
    effective = P.effective(p)["payback"]
    assert (effective["product_cost_pct"], effective["fulfillment"], effective["max_age"], effective["aov"]) == (0.2, 6.0, 24, 80.5)


def test_migration_is_idempotent_and_skips_scenarios_without_a_payback_block(pg):
    set_params(pg, {"financing": {"payback": {"product_cost_pct": 0.2, "aov": 80.0}}})
    run_migration(pg)
    once = sql_params(pg)
    run_migration(pg)
    assert sql_params(pg) == once
    set_params(pg, {"financing": {"cash": {"floor": 1}}, "pnl": {"x": 1}})
    run_migration(pg)
    assert sql_params(pg) == {"financing": {"cash": {"floor": 1}}, "pnl": {"x": 1}}


def test_migration_applies_to_clones_too(pg):
    pg.execute("INSERT INTO proj_scenarios (slug, name, base_slug, params) VALUES ('copy', 'Copy', 'current', "
               "'{\"financing\": {\"payback\": {\"product_cost_pct\": 0.19, \"aov\": 69.15600858369098}}}')")
    run_migration(pg)
    p = sql_params(pg, "copy")
    assert p["financing"]["payback"] == {} and p["drivers"]["payback"] == {"product_cost_pct": 0.19}


def test_a_fresh_database_holds_one_store(pg):
    p = sql_params(pg)
    assert not {"aov", "cac", "product_cost_pct", "fulfillment", "max_age"} & set(p["financing"]["payback"])     # after migrations 160 and 161
    assert "drivers" not in p                                                                                    # nothing to move: the seeded values equal the defaults
