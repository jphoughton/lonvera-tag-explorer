"""Planner: proposes purchase orders and FBA transfers by the rebalance rules, never writes, reports what it cannot fix."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

import inv_supply_support as S
from app.projection.inventory_supply import calc, planner
from app.projection.inventory_supply.model import Inputs, Settings

TODAY = date(2026, 11, 3)


def inputs_with_revamp(stock=5000.0) -> Inputs:
    inp = S.mini_inputs(stock_bull=stock, allocated=0.0)
    return inp


def demand(rev=3000.0, amz=0.0, intl=0.0):
    per = {("revamp", "bull"): rev}
    if amz:
        per[("amazon", "bull")] = amz
    if intl:
        per[("intl", "bull")] = intl
    return S.flat_demand(per, start=date(2026, 11, 1))


def test_revamp_buy_lands_on_the_earliest_possible_date_is_sized_to_cover_and_rounds_up():
    inp = inputs_with_revamp()
    plan = planner.suggest(inp, demand(), TODAY, Settings())
    first = plan["pos"][0]
    # stock is short from December, but a KeyStone order (12 weeks) cannot land before 2027-01-26
    assert first["warehouse"] == "revamp" and first["vendor"] == "KeyStone" and first["land_date"] == "2027-01-26"
    assert first["units"] % 1000 == 0 and first["units"] >= 20000                         # vendor minimum 20,000
    assert first["status"] == "planned" and first["source"] == "auto-plan" and first["deposit_pct"] == 0.5 and first["unit_cost"] == 3.92
    assert first["order_date"] == "2026-11-03"                                            # land date minus 84 days
    assert plan["shortfalls"] and "earliest an order can land" in plan["shortfalls"][0]["reason"]
    assert plan["shortfalls"][0]["month"] == "2026-12-01" and plan["shortfalls"][0]["earliest_land"] == "2027-01-26"


def test_after_the_plan_stock_stays_above_safety_from_the_landing_month_on():
    inp = inputs_with_revamp()
    dem = demand()
    plan = planner.suggest(inp, dem, TODAY, Settings())
    pos = [S.po(-i - 1, warehouse=p["warehouse"], vendor=p["vendor"], units=p["units"], land=date.fromisoformat(p["land_date"]), source="auto-plan")
           for i, p in enumerate(plan["pos"])]
    res = calc.compute_supply(replace(inp, pos=pos), dem, TODAY, Settings())
    closing = res.burn[("revamp", "bull", "closing")]
    first_land = date.fromisoformat(plan["pos"][0]["land_date"]).replace(day=1)
    horizon = res.months[:res.horizon]
    assert all(closing[m] >= 14 * 3000 / 31 - 1 for m in horizon if m >= first_land)
    assert plan["summary"]["stockouts_before"]["revamp|bull"][0] == "2026-12-01"
    assert plan["summary"]["stockouts_after"]["revamp|bull"] == ["2026-12-01"]            # December cannot be saved: nothing can land in time
    assert plan["summary"]["added_value"] == pytest.approx(sum(p["units"] for p in plan["pos"]) * 3.92)


def test_rounding_moq_and_a_late_order_is_not_repeated():
    inp = inputs_with_revamp(stock=200000.0)                                              # no shortage in the window: nothing proposed
    plan = planner.suggest(inp, demand(), TODAY, Settings())
    assert plan["pos"] == [] and plan["shortfalls"] == []
    inp2 = inputs_with_revamp()
    inp2.vendors["KeyStone"].moq_units = 0.0
    p2 = planner.suggest(inp2, demand(), TODAY, Settings())
    assert all(p["units"] % 1000 == 0 for p in p2["pos"])
    lands = [p["land_date"] for p in p2["pos"]]
    assert len(lands) == len(set(lands))                                                  # one order per landing, not one per round


def test_intl_buy_uses_the_vendor_priced_for_that_location():
    inp = S.mini_inputs()
    inp.stock[("intl", "bull")].on_hand = 500.0
    plan = planner.suggest(inp, demand(rev=0.0, intl=2000.0), TODAY, Settings())
    assert plan["pos"] and {p["warehouse"] for p in plan["pos"]} == {"intl"} and {p["vendor"] for p in plan["pos"]} == {"China"}
    assert all(p["units"] >= 5000 and p["deposit_pct"] == 0.4 for p in plan["pos"])


def test_fba_transfer_ships_in_the_month_before_it_arrives_and_restores_cover():
    inp = S.mini_inputs()
    inp.stock[("amazon", "bull")].on_hand = 500.0
    plan = planner.suggest(inp, demand(rev=0.0, amz=1000.0), TODAY, Settings())
    t = plan["transfers"][0]
    assert t["receive_date"] == "2026-12-01" and t["ship_date"] == "2026-11-03" and t["units"] % 500 == 0
    assert any(s["location"] == "amazon" and "cannot arrive before Dec 2026" in s["reason"] for s in plan["shortfalls"])   # November itself is short
    assert all(date.fromisoformat(x["ship_date"]) < date.fromisoformat(x["receive_date"]) for x in plan["transfers"])
    # transfers leave Revamp stock, so the Revamp side is re-checked afterwards
    assert plan["summary"]["added_transfers"] == len(plan["transfers"])


def test_replace_auto_ignores_existing_auto_rows_and_lists_them_as_removed():
    inp = inputs_with_revamp()
    inp.pos = [S.po(7, warehouse="revamp", vendor="KeyStone", units=30000, land=date(2026, 12, 15), source="auto-plan", sheet_row=43),
               S.po(8, warehouse="revamp", vendor="KeyStone", units=1000, land=date(2026, 12, 20), source="manual")]
    keep = planner.suggest(inp, demand(), TODAY, Settings(), replace_auto=False)
    swap = planner.suggest(inp, demand(), TODAY, Settings(), replace_auto=True)
    assert keep["removed"]["pos"] == [] and [r["id"] for r in swap["removed"]["pos"]] == [7]
    assert keep["summary"]["added_units"] < swap["summary"]["added_units"] or keep["pos"] != swap["pos"]
    assert len(inp.pos) == 2                                                              # the proposal never edits the inputs


def test_missing_vendor_price_is_reported_not_guessed():
    inp = inputs_with_revamp()
    inp.vendor_costs.clear()
    plan = planner.suggest(inp, demand(), TODAY, Settings())
    assert plan["pos"] == [] and "No vendor price list entry" in plan["shortfalls"][0]["reason"]


def test_planner_safety_setting_changes_the_trigger():
    inp = inputs_with_revamp(stock=12000.0)                                               # 9000 / 6000 / 3000 / 0 / -3000 at the end of Nov..Mar
    a = planner.suggest(inp, demand(), TODAY, Settings(plan_safety_days=0))               # first month below zero: March -> landing on the 1st
    b = planner.suggest(inp, demand(), TODAY, Settings(plan_safety_days=60))              # 60 days of cover is breached in January -> earliest landing Jan 26
    assert a["pos"][0]["land_date"] == "2027-03-01" and b["pos"][0]["land_date"] == "2027-01-26"
