"""Variables: every input of the module is a named variable with the sheet value as default, and changing it moves the outputs.

Classes covered, one test each (plus a guard that no scalar variable ships without a mover):
  scalar variables (SETTING_SPECS)   test_each_scalar_variable_moves_an_output
  vendor lead time / minimum order   test_vendor_*   price and deposit   test_price_*   reorder parameters   test_reorder_*   unit cost   test_product_cost_*
  purchase-order rows                test_po_*       FBA transfers       test_transfer_*   scenario isolation   test_*_isolation (API)
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

import inv_supply_support as S
from app.projection.interfaces import Scenario
from app.projection.inventory_supply import calc, loaders, overlay, planner
from app.projection.inventory_supply.model import SETTING_SPECS, Settings

TODAY = date(2026, 11, 3)
NS = overlay.NS


def params(**kw) -> dict:
    return {NS: kw}


def ov(inp, **kw):
    return overlay.apply_overlay(inp, params(**kw))


def run(inp, per_month=None, settings=None, today=TODAY, mtd=None):
    dem = S.flat_demand(per_month if per_month is not None else {("revamp", "bull"): 3000.0, ("amazon", "bull"): 1000.0, ("intl", "bull"): 2000.0},
                        start=today.replace(day=1), mtd=mtd)
    return calc.compute_supply(inp, dem, today, settings)


def row(res, loc, prod):
    return next(r for r in res.reorder if r["location"] == loc and r["product"] == prod)


# ---------------------------------------------------------------------------------------------------------------- scalar variables

def _plan(inp, settings, per=None):
    dem = S.flat_demand(per or {("revamp", "bull"): 3000.0, ("amazon", "bull"): 1000.0}, start=date(2026, 11, 1))
    return planner.suggest(inp, dem, TODAY, settings)


def _plan_inputs(moq: float | None = None):
    inp = S.mini_inputs(stock_bull=5000.0, allocated=0.0)
    inp.stock[("amazon", "bull")].on_hand = 500.0
    if moq is not None:
        inp.vendors["KeyStone"].moq_units = moq
    return inp


def _two_close_pos():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=5000, land=date(2027, 3, 1)), S.po(2, units=5000, land=date(2027, 3, 15))]            # 14 days apart
    return inp


def _lead_probe(loc, prod):
    def probe(s):
        inp = S.mini_inputs(stock_bull=100.0)
        res = run(inp, per_month={(loc, prod): 500.0}, settings=s)
        return row(res, loc, prod)["lead_days"]
    return probe


MOVERS = {
    # key: (value that differs from the default, probe(settings) -> comparable output)
    "as_of": (None, None),                                                                                          # handled separately (reference day)
    "horizon_months": (12, lambda s: run(S.mini_inputs(), settings=s).horizon),
    "stale_stock_days": (1, lambda s: "stock_stale" in [w["code"] for w in run(_aged_stock(), settings=s).warnings]),
    "safety_days": (30, lambda s: row(run(S.mini_inputs(), mtd={("revamp", "bull"): 100.0}, settings=s), "revamp", "bull")["serial"]["must_land_by"]),
    "default_safety_days": (30, lambda s: row(run(S.mini_inputs(), per_month={("revamp", "belle"): 100.0}, settings=s), "revamp", "belle")["safety_days"]),
    "default_lead_days_revamp": (45, _lead_probe("revamp", "belle")),
    "default_lead_days_intl": (20, _lead_probe("intl", "flow")),
    "order_soon_days": (5, lambda s: _soon(s)),
    "reorder_cover_months": (6, lambda s: row(run(S.mini_inputs(), settings=s), "revamp", "bull")["suggested_po_units"]),
    "out_below_units": (3000, lambda s: sum(x["months_out"] for x in run(S.mini_inputs(), settings=s).stats)),
    "low_cover_months": (3.0, lambda s: sum(1 for v in run(S.mini_inputs(), settings=s).status_at[("revamp", "bull")].values() if v == "LOW")),
    "fba_target_months": (3.0, lambda s: run(S.mini_inputs(), settings=s).burn[("amazon", "bull", "to_send")][date(2026, 11, 1)]),
    "cash_window_days": (20, lambda s: run(_cash_inputs(), settings=s).tldr["cash_next_90_days"]),
    "plan_cover_months": (6, lambda s: _plan(_plan_inputs(), s)["summary"]["added_units"]),
    "plan_safety_days": (60, lambda s: [p["units"] for p in _plan(_plan_inputs(moq=0.0), s)["pos"]][:3]),
    "po_round_units": (7000, lambda s: [p["units"] for p in _plan(_plan_inputs(moq=0.0), s)["pos"]][:3]),
    "transfer_round_units": (3000, lambda s: [t["units"] for t in _plan(_amazon_inputs(), s, {("amazon", "bull"): 1000.0})["transfers"]][:2]),
    "transfer_min_units": (9000, lambda s: [t["units"] for t in _plan(_amazon_inputs(), s, {("amazon", "bull"): 1000.0})["transfers"]][:2]),
    "fba_restore_months": (5.0, lambda s: [t["units"] for t in _plan(_amazon_inputs(), s, {("amazon", "bull"): 1000.0})["transfers"]][:2]),
    "transfer_lag_months": (2, lambda s: [t["ship_date"] for t in _plan(_amazon_inputs(), s, {("amazon", "bull"): 1000.0})["transfers"]][:2]),
    "days_per_month": (28.0, lambda s: [t["units"] for t in _plan(_amazon_inputs(), s, {("amazon", "bull"): 1000.0})["transfers"]][:3]),
    "duplicate_window_days": (10, lambda s: "duplicate_po" in [w["code"] for w in run(_two_close_pos(), settings=s).warnings]),
}


def _aged_stock():
    inp = S.mini_inputs()
    inp.stock[("amazon", "bull")].as_of = date(2026, 10, 20)                                                        # 14 days old
    return inp


def _soon(s):
    inp = S.mini_inputs()
    inp.reorder[("revamp", "bull")].lead_days = 70                                                                   # order-by about 12 days out
    return row(run(inp, mtd={("revamp", "bull"): 100.0}, settings=s), "revamp", "bull")["status"]


def _cash_inputs():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=10000, land=date(2027, 1, 15), order_date=date(2026, 11, 20))]                          # deposit day 17, balance day 73
    return inp


def _amazon_inputs():
    inp = S.mini_inputs()
    inp.stock[("amazon", "bull")].on_hand = 500.0
    return inp


def test_every_scalar_variable_has_a_mover_and_a_sheet_default():
    assert set(MOVERS) == {s.key for s in SETTING_SPECS}
    for spec in SETTING_SPECS:
        assert spec.label and spec.unit and spec.source and spec.group
        if spec.key != "as_of":
            assert getattr(Settings(), spec.key) == spec.default


@pytest.mark.parametrize("key", [k for k in MOVERS if k != "as_of"])
def test_each_scalar_variable_moves_an_output(key):
    value, probe = MOVERS[key]
    default = probe(Settings())
    moved = probe(Settings.from_params(params(**{key: value})))              # through the scenario-params path the engine uses
    assert moved != default, f"{key}={value} did not change the probed output ({default!r})"


def test_the_reference_day_variable_moves_the_start_month():
    res = run(S.mini_inputs(), today=loaders.resolve_today(params(as_of="2027-01-05")))
    assert res.start == date(2027, 1, 1) and loaders.resolve_today(params(as_of="2027-01-05")) == date(2027, 1, 5)


def test_out_of_range_or_mistyped_variables_fall_back_to_the_sheet_default():
    s = Settings.from_params(params(fba_target_months=-1, po_round_units=0, transfer_lag_months="2", horizon_months=True, plan_cover_months=None))
    assert (s.fba_target_months, s.po_round_units, s.transfer_lag_months, s.horizon_months, s.plan_cover_months) == (1.5, 1000, 1, 36, 3)


# ---------------------------------------------------------------------------------------------------------------- tables: vendors, prices, reorder, costs

def test_vendor_lead_time_override_moves_the_order_date_and_the_deposit_month():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=10000, land=date(2027, 3, 1))]
    base = run(inp)
    moved = run(ov(inp, vendors={"China": {"lead_weeks": 4}}))
    assert base.po_rows[0]["order_date"] == "2027-01-04" and moved.po_rows[0]["order_date"] == "2027-02-01"
    assert base.deposits[date(2027, 1, 1)] > 0 and moved.deposits[date(2027, 1, 1)] == 0 and moved.deposits[date(2027, 2, 1)] > 0


def test_vendor_minimum_order_override_moves_the_plan():
    inp = _plan_inputs()
    a = _plan(inp, Settings())["pos"][0]["units"]
    b = _plan(ov(inp, vendors={"KeyStone": {"moq_units": 60000}}), Settings())["pos"][0]["units"]
    assert 20000 <= a < 60000 and b == 60000


def test_price_unit_cost_and_deposit_overrides_move_value_and_cash():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=10000, land=date(2027, 3, 1))]
    moved = run(ov(inp, prices={"China|bull": {"unit_cost": 3.0, "deposit_pct": 0.1}}))
    row0 = moved.po_rows[0]
    assert row0["po_value"] == pytest.approx(30000) and row0["down_payment"] == pytest.approx(3000)
    assert moved.deposits[date(2027, 1, 1)] == pytest.approx(3000) and moved.balances[date(2027, 3, 1)] == pytest.approx(27000)
    assert run(inp).po_rows[0]["po_value"] == pytest.approx(22600)


def test_reorder_parameter_overrides_move_the_order_by_date_and_the_suggested_order():
    inp = S.mini_inputs()
    base = row(run(inp, mtd={("revamp", "bull"): 100.0}), "revamp", "bull")
    moved = row(run(ov(inp, reorder={"revamp|bull": {"lead_days": 30, "safety_days": 5, "moq_units": 50000}}), mtd={("revamp", "bull"): 100.0}), "revamp", "bull")
    assert moved["serial"]["order_by"] > base["serial"]["order_by"] and moved["serial"]["must_land_by"] > base["serial"]["must_land_by"]
    assert moved["suggested_po_units"] == 50000 and base["suggested_po_units"] < 50000


def test_product_cost_override_moves_inventory_value():
    inp = S.mini_inputs()
    a, b = run(inp), run(ov(inp, product_costs={"bull": {"china_unit_cost": 5.0}}))
    assert b.value[date(2026, 11, 1)] == pytest.approx(a.value[date(2026, 11, 1)] * 5.0 / 2.26)


# ---------------------------------------------------------------------------------------------------------------- rows

def _with_pos():
    inp = S.mini_inputs()
    inp.pos = [S.po(7, warehouse="revamp", vendor="KeyStone", units=5000, land=date(2026, 12, 15), status="ordered", sheet_row=7)]
    return inp


def test_po_row_override_moves_inbound_cash_and_marks_the_changed_fields():
    inp = _with_pos()
    out = ov(inp, pos={"7": {"units": 12000, "land_date": "2027-01-20"}})
    p = out.pos[0]
    assert (p.units, p.land_date, p.ref, p.overridden) == (12000.0, date(2027, 1, 20), "7", ("land_date", "units"))
    res = run(out)
    assert res.burn[("revamp", "bull", "inbound")][date(2027, 1, 1)] == 12000.0 and res.burn[("revamp", "bull", "inbound")].get(date(2026, 12, 1), 0) == 0
    assert res.po_rows[0]["overridden"] == ["land_date", "units"] and res.po_rows[0]["ref"] == "7"
    assert inp.pos[0].units == 5000.0                                                                                   # the stored row is untouched


def test_po_row_can_be_removed_and_scenario_only_rows_can_be_added():
    inp = _with_pos()
    gone = run(ov(inp, pos={"7": {"deleted": True}}))
    assert gone.po_rows == [] and gone.burn[("revamp", "bull", "inbound")].get(date(2026, 12, 1), 0) == 0
    added = run(ov(inp, pos={"new:2": {"product": "bull", "vendor": "KeyStone", "warehouse": "revamp", "units": 8000, "land_date": "2027-02-10", "status": "planned"}}))
    new = next(r for r in added.po_rows if r["ref"] == "new:2")
    assert new["scenario_only"] is True and new["id"] == -2 and new["po_value"] == pytest.approx(8000 * 3.92)
    assert added.burn[("revamp", "bull", "inbound")][date(2027, 2, 1)] == 8000.0 and len(added.po_rows) == 2


def test_transfer_overrides_move_the_fba_inbound_and_revamp_outflow():
    inp = S.mini_inputs()
    inp.transfers = [S.transfer(5, units=2000, ship=date(2026, 11, 20), receive=date(2026, 12, 5), sheet_row=5)]
    out = run(ov(inp, transfers={"5": {"units": 3500, "receive_date": "2027-01-10"},
                                 "new:1": {"product": "bull", "units": 900, "ship_date": "2026-12-01", "receive_date": "2026-12-20"}}))
    assert out.burn[("amazon", "bull", "inbound")][date(2027, 1, 1)] == 3500.0 and out.burn[("amazon", "bull", "inbound")][date(2026, 12, 1)] == 900.0
    assert out.burn[("revamp", "bull", "out")][date(2026, 11, 1)] == 3500.0 and out.burn[("revamp", "bull", "out")][date(2026, 12, 1)] == 900.0
    assert {r["ref"] for r in out.transfer_rows} == {"5", "new:1"}


def test_a_change_that_no_longer_fits_is_skipped_and_shown_as_a_data_check():
    inp = _with_pos()
    out = ov(inp, pos={"99": {"units": 5}, "7": {"units": -5}, "new:x": {"units": 1}}, vendors={"China": {"lead_weeks": 0}}, prices={"Nobody|bull": {"unit_cost": 1}})
    assert out.pos[0].units == 5000.0 and len(out.notes) == 5
    res = run(out)
    assert "scenario_change_ignored" in [w["code"] for w in res.warnings]


def test_strict_mode_rejects_what_the_tolerant_mode_skips():
    from app.queries import ApiError
    inp = _with_pos()
    for bad in (params(pos={"7": {"units": 0}}), params(pos={"7": {"land_date": "2020-01-01"}}), params(pos={"99": {"units": 5}}),
                params(transfers={"new:1": {"product": "bull", "units": 5, "ship_date": "2027-02-01", "receive_date": "2027-01-01"}}),
                params(prices={"China|unicorn": {"unit_cost": 1}}), params(reorder={"amazon|bull": {"lead_days": 5}})):
        with pytest.raises(ApiError):
            overlay.apply_overlay(inp, bad, strict=True)


def test_overlay_is_empty_by_default_and_leaves_the_inputs_object_alone():
    inp = _with_pos()
    assert overlay.apply_overlay(inp, {}) is inp and overlay.apply_overlay(inp, params(fba_target_months=3)) is inp
    assert overlay.apply_overlay(inp, params(pos={"7": {"units": 9}})) is not inp and inp.pos[0].units == 5000.0


# ---------------------------------------------------------------------------------------------------------------- change lists

def test_build_patch_sets_reverts_and_prunes_empty_branches():
    own: dict = {}
    p1 = overlay.build_patch(own, [{"kind": "lever", "name": "fba_target_months", "value": 2.0}, {"kind": "po", "id": "7", "field": "units", "value": 9000},
                                   {"kind": "vendor", "vendor": "China", "field": "lead_weeks", "value": 6}])
    assert p1 == {"fba_target_months": 2.0, "pos": {"7": {"units": 9000}}, "vendors": {"China": {"lead_weeks": 6}}}
    own2 = {"fba_target_months": 2.0, "pos": {"7": {"units": 9000}}}
    p2 = overlay.build_patch(own2, [{"kind": "lever", "name": "fba_target_months", "value": None}, {"kind": "po", "id": "7", "field": "units", "value": None}])
    assert p2 == {"fba_target_months": None, "pos": None}                                                                # reverting the last field removes the row entry


@pytest.mark.parametrize("item", [
    {"kind": "lever", "name": "nope", "value": 1}, {"kind": "lever", "name": "fba_target_months", "value": -1}, {"kind": "lever", "name": "fba_target_months", "value": "2"},
    {"kind": "lever", "name": "transfer_lag_months", "value": 1.5}, {"kind": "lever", "name": "fba_target_months", "value": True}, {"kind": "lever", "name": "as_of", "value": 5},
    {"kind": "vendor", "vendor": "", "field": "lead_weeks", "value": 1}, {"kind": "vendor", "vendor": "China", "field": "color", "value": 1},
    {"kind": "price", "vendor": "China", "product": "x", "field": "unit_cost", "value": 1}, {"kind": "reorder", "location": "amazon", "product": "bull", "field": "lead_days", "value": 1},
    {"kind": "po", "id": "abc", "field": "units", "value": 1}, {"kind": "po", "id": "7", "field": "bogus", "value": 1}, {"kind": "po", "id": "7", "field": None, "value": 5},
    {"kind": "bogus"}, "not an object"])
def test_build_patch_rejects_malformed_changes(item):
    from app.queries import ApiError
    with pytest.raises(ApiError):
        overlay.build_patch({}, [item])


def test_the_catalog_lists_every_variable_with_default_origin_and_source():
    inp = _with_pos()
    row_ = {"params": params(fba_target_months=2.0)}
    eff = params(fba_target_months=2.0, default_safety_days=10, pos={"7": {"units": 9}}, vendors={"China": {"lead_weeks": 6}})
    cat = overlay.catalog(inp, row_, eff)
    levers = {x["key"]: x for x in cat["levers"]}
    assert set(levers) == {s.key for s in SETTING_SPECS}
    assert levers["fba_target_months"]["origin"] == "scenario" and levers["fba_target_months"]["value"] == 2.0 and levers["fba_target_months"]["default"] == 1.5
    assert levers["default_safety_days"]["origin"] == "inherited" and levers["po_round_units"]["origin"] == "default" and levers["po_round_units"]["value"] == 1000
    assert all(x["source"] and x["unit"] for x in levers.values())
    china = next(v for v in cat["tables"]["vendors"] if v["key"] == "China")
    assert china["overridden"] == ["lead_weeks"] and china["fields"]["lead_weeks"] == {"value": 6.0, "default": 8.0}
    assert cat["changes"] == [{"table": "pos", "key": "7", "fields": ["units"], "values": {"units": 9}}] or cat["inherited_changes"] >= 1
