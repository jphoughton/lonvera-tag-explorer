"""Engine: burn arithmetic, inbound rules by status, transfers, purchase-order cash, reorder status, value, warnings, edge cases (no database)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import inv_supply_support as S
from app.projection.interfaces import DemandResult
from app.projection.inventory_supply import calc
from app.projection.inventory_supply.model import Inputs, Settings, SupplyResult

TODAY = date(2026, 11, 3)


def run(inp: Inputs, per_month: dict | None = None, mtd: dict | None = None, today: date = TODAY, settings: Settings | None = None) -> SupplyResult:
    dem = S.flat_demand(per_month if per_month is not None else {("revamp", "bull"): 3000.0, ("amazon", "bull"): 1000.0, ("intl", "bull"): 2000.0},
                        start=today.replace(day=1), mtd=mtd)
    return calc.compute_supply(inp, dem, today, settings)


def codes(res: SupplyResult) -> list[str]:
    return [w["code"] for w in res.warnings]


def test_burn_arithmetic_remaining_demand_in_the_first_month_and_out_flag():
    res = run(S.mini_inputs(), mtd={("revamp", "bull"): 100.0})
    # opening = on hand 10000 minus allocated 500; first month demand = 3000 forecast minus 100 shipped so far
    assert S.series_list(res, "revamp", "bull", "opening", 4) == [9500.0, 6600.0, 3600.0, 600.0]
    assert S.series_list(res, "revamp", "bull", "demand", 3) == [2900.0, 3000.0, 3000.0]
    assert S.series_list(res, "revamp", "bull", "closing", 5) == [6600.0, 3600.0, 600.0, -2400.0, -5400.0]
    st = res.status_at[("revamp", "bull")]
    assert st[date(2027, 2, 1)] == "OUT" and st[date(2027, 1, 1)] == "LOW" and st[date(2026, 12, 1)] == ""      # 0.2 months of cover reads LOW (< 1.0)
    assert res.burn[("revamp", "bull", "cover")][date(2026, 11, 1)] == pytest.approx(6600 / 3000)


def test_inbound_counts_open_orders_only_by_warehouse_and_land_month():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, warehouse="revamp", vendor="KeyStone", units=5000, land=date(2026, 12, 15), status="in transit"),
               S.po(2, warehouse="revamp", vendor="KeyStone", units=7000, land=date(2026, 12, 20), status="landed"),
               S.po(3, warehouse="revamp", vendor="KeyStone", units=9000, land=date(2026, 12, 20), status="cancelled"),
               S.po(4, warehouse="intl", units=1111, land=date(2026, 12, 2), status="ordered")]
    res = run(inp)
    assert res.burn[("revamp", "bull", "inbound")][date(2026, 12, 1)] == 5000.0
    assert res.burn[("intl", "bull", "inbound")][date(2026, 12, 1)] == 1111.0
    assert res.burn[("amazon", "bull", "inbound")].get(date(2026, 12, 1), 0.0) == 0.0


def test_transfer_status_rules_ship_month_out_receive_month_in():
    inp = S.mini_inputs()
    inp.transfers = [S.transfer(1, units=2000, status="planned"), S.transfer(2, units=300, status="shipped"),
                     S.transfer(3, units=400, status="received"), S.transfer(4, units=500, status="cancelled")]
    res = run(inp)
    assert res.burn[("revamp", "bull", "out")][date(2026, 11, 1)] == 2000.0          # only the planned one leaves Revamp stock
    assert res.burn[("amazon", "bull", "inbound")][date(2026, 12, 1)] == 2300.0       # planned + shipped arrive; received/cancelled do not
    assert res.burn[("amazon", "bull", "to_send")][date(2026, 11, 1)] >= 0


def test_purchase_order_value_deposit_balance_and_cash_months():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=10000, land=date(2027, 3, 1))]                        # China: 8 weeks, $2.26, deposit default 40%
    res = run(inp)
    row = res.po_rows[0]
    assert row["po_value"] == pytest.approx(22600) and row["down_payment"] == pytest.approx(9040) and row["balance"] == pytest.approx(13560)
    assert row["order_date"] == "2027-01-04" and row["order_date_derived"] is True
    assert res.deposits[date(2027, 1, 1)] == pytest.approx(9040) and res.balances[date(2027, 3, 1)] == pytest.approx(13560)
    assert res.outlay[date(2027, 1, 1)] == pytest.approx(9040) and res.outlay[date(2027, 3, 1)] == pytest.approx(13560)
    assert res.intl_outlay[date(2027, 1, 1)] == pytest.approx(9040)
    assert res.outlay_cumulative[date(2029, 12, 1)] == pytest.approx(22600)
    assert res.landing_value[date(2027, 3, 1)] == pytest.approx(22600) and res.landing_deposits[date(2027, 3, 1)] == pytest.approx(9040)


def test_typed_order_date_cost_override_and_deposit_override_win_cancelled_carries_no_cash():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=1000, land=date(2027, 3, 1), order_date=date(2027, 2, 1), unit_cost=10.0, deposit_pct=0.25),
               S.po(2, units=1000, land=date(2027, 4, 1), status="cancelled")]
    res = run(inp)
    assert res.deposits[date(2027, 2, 1)] == pytest.approx(2500) and res.balances[date(2027, 3, 1)] == pytest.approx(7500)
    assert res.outlay[date(2027, 4, 1)] == 0.0 and res.scalars["po.total_value"] == pytest.approx(10000)
    assert "cancelled_excluded" in codes(res)


def test_order_without_price_or_lead_time_is_flagged_not_silently_zeroed():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, vendor="Acme", units=1000, land=date(2027, 3, 1))]
    res = run(inp)
    assert res.po_rows[0]["po_value"] is None and res.outlay[date(2027, 3, 1)] == 0.0
    assert {"missing_unit_cost", "missing_vendor_lead"} <= set(codes(res))


@pytest.mark.parametrize("land,month", [(date(2026, 11, 30), date(2026, 11, 1)), (date(2026, 12, 1), date(2026, 12, 1)), (date(2026, 12, 31), date(2026, 12, 1)),
                                        (date(2027, 1, 1), date(2027, 1, 1))])
def test_month_boundaries_for_inbound_and_balance(land, month):
    inp = S.mini_inputs()
    inp.pos = [S.po(1, warehouse="revamp", vendor="KeyStone", units=1000, land=land, order_date=land - timedelta(days=84))]
    res = run(inp)
    assert res.burn[("revamp", "bull", "inbound")][month] == 1000.0
    assert res.balances[month] == pytest.approx(1000 * 3.92 * 0.5)


def test_reorder_status_levels_follow_lead_time():
    inp = S.mini_inputs()                                                        # revamp bull: first OUT is Feb 2027 (28 days), opening 600 that month
    res = run(inp, mtd={("revamp", "bull"): 100.0})
    r = next(x for x in res.reorder if x["location"] == "revamp")
    assert r["stockout_month"] == "2027-02-01" and r["opening_that_month"] == 600.0 and r["demand_that_month"] == 3000.0 and r["days_in_month"] == 28
    est = calc.serial(date(2027, 2, 1)) + 600 / 3000 * 28
    assert r["serial"]["est_stockout"] == pytest.approx(est) and r["serial"]["must_land_by"] == pytest.approx(est - 14)
    assert r["serial"]["order_by"] == pytest.approx(est - 14 - 90)
    assert r["serial"]["days_until_order"] == pytest.approx(est - 14 - 90 - calc.serial(TODAY))
    assert r["status"] == "OVERDUE"                                              # 90-day lead: order-by is already behind us
    for lead, want in ((30, "OK"), (70, "ORDER SOON"), (200, "OVERDUE")):
        inp.reorder[("revamp", "bull")].lead_days = lead
        assert next(x for x in run(inp, mtd={("revamp", "bull"): 100.0}).reorder if x["location"] == "revamp")["status"] == want
    assert r["suggested_po_units"] == pytest.approx(3 * r["avg_monthly_demand"])


def test_no_stockout_in_window_reads_ok_and_orders_sort_by_urgency():
    inp = S.mini_inputs(stock_bull=10_000_000)
    res = run(inp)
    r = next(x for x in res.reorder if x["location"] == "revamp")
    assert r["no_stockout"] is True and r["status"] == "OK" and r["est_stockout"] is None and r["status_text"].startswith("OK (no stockout")
    inp2 = S.mini_inputs()
    inp2.stock[("intl", "bull")].on_hand = 100.0                                 # intl runs out first -> listed first
    rows = run(inp2).reorder
    assert rows[0]["location"] == "intl" and rows[0]["status"] == "OVERDUE"


def test_cover_is_blank_when_next_month_demand_is_zero_and_stats_survive_zero_demand():
    res = run(S.mini_inputs(), per_month={("revamp", "bull"): 0.0})
    assert res.burn[("revamp", "bull", "cover")] == {}                            # 0 / 0: no figure rather than an error
    st = next(s for s in res.stats if s["location"] == "revamp" and s["product"] == "bull")
    assert st["avg_monthly_demand"] == 0.0 and st["months_cover_today"] is None and st["months_out"] == 0


def test_empty_inputs_and_missing_demand_do_not_crash_and_say_so():
    res = calc.compute_supply(Inputs(), None, TODAY)
    assert sum(res.outlay.values()) == 0 and res.reorder and all(r["no_stockout"] for r in res.reorder)
    assert {"demand_missing", "stock_missing"} <= set(codes(res))
    assert res.tldr["first_stockout"] is None and res.tldr["cash_next_90_days"] == 0


def test_reference_day_rolls_the_start_month():
    res = run(S.mini_inputs(), today=date(2027, 1, 15), mtd={("revamp", "bull"): 1500.0})
    assert res.start == date(2027, 1, 1) and res.months[0] == date(2027, 1, 1)
    assert res.burn[("revamp", "bull", "demand")][date(2027, 1, 1)] == 1500.0


def test_value_is_closing_stock_times_china_landed_cost_and_zero_cost_is_flagged():
    inp = S.mini_inputs()
    res = run(inp)
    close0 = res.burn[("revamp", "bull", "closing")][date(2026, 11, 1)]
    assert res.value_by_location[("revamp", "bull")][date(2026, 11, 1)] == pytest.approx(close0 * 2.26)
    total0 = sum(res.burn[(l, "bull", "closing")][date(2026, 11, 1)] for l in ("revamp", "amazon", "intl"))
    assert res.value[date(2026, 11, 1)] == pytest.approx(total0 * 2.26)
    inp.product_costs["bull"].china_unit_cost = 0.0
    assert "zero_product_cost" in codes(run(inp))


def test_total_burn_check_row_is_nonzero_only_while_units_are_in_transit_between_locations():
    inp = S.mini_inputs()
    inp.transfers = [S.transfer(1, units=2000, ship=date(2026, 11, 20), receive=date(2026, 12, 5))]
    res = run(inp)
    chk = res.burn[("total", "bull", "check")]
    assert chk[date(2026, 11, 1)] == pytest.approx(-2000) and chk[date(2026, 12, 1)] == pytest.approx(2000) and abs(chk[date(2027, 1, 1)]) < 1e-9
    inp.transfers = [S.transfer(1, units=2000, ship=date(2026, 11, 20), receive=date(2026, 11, 28))]
    assert all(abs(v) < 1e-9 for v in run(inp).burn[("total", "bull", "check")].values())


def test_amazon_units_to_send_and_low_status_use_settings():
    res = run(S.mini_inputs(), settings=Settings(fba_target_months=3.0, low_cover_months=2.0))
    # FBA 2000 on hand, 1000/month: closing 1000 after November, target 3 months of December demand
    assert res.burn[("amazon", "bull", "to_send")][date(2026, 11, 1)] == pytest.approx(3000 - 1000)
    assert res.status_at[("amazon", "bull")][date(2026, 11, 1)] == "LOW"


def test_warnings_cover_the_sheet_data_flags():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=5000, land=date(2026, 10, 15), status="in transit"),                     # past land date, not landed
               S.po(2, units=5000, land=date(2026, 10, 1), status="landed"),                          # same size 14 days apart: possible duplicate
               S.po(3, warehouse="revamp", vendor="KeyStone", units=20000, land=date(2026, 11, 1), status="in transit"),  # land date passed, counted inbound
               S.po(4, units=5000, land=date(2026, 12, 1), status="planned", order_date=date(2026, 11, 1))]               # planned, order date in the past
    inp.transfers = [S.transfer(1, ship=date(2026, 12, 1), receive=date(2026, 11, 1))]
    res = run(inp)
    got = {w["code"]: w for w in res.warnings}
    assert {"duplicate_po", "planned_order_in_past", "landed_still_inbound", "past_land_not_landed", "transfer_receive_before_ship"} <= set(got)
    assert [r["id"] for r in got["duplicate_po"]["refs"]] == [2, 1] or {r["id"] for r in got["duplicate_po"]["refs"]} == {1, 2}
    assert got["landed_still_inbound"]["refs"][0]["id"] == 3 and got["past_land_not_landed"]["refs"][0]["id"] == 1
    assert "stock_stale" not in got                                                                   # counts are dated the reference day


def test_old_stock_counts_are_flagged_stale_and_the_headline_names_the_oldest():
    inp = S.mini_inputs()
    inp.stock[("amazon", "bull")].as_of = date(2026, 10, 13)
    inp.stock[("intl", "bull")].estimate = True
    res = run(inp)
    assert "stock_stale" in codes(res)
    assert res.tldr["oldest_stock"] == {"location": "amazon", "as_of": "2026-10-13", "age_days": 21}


def test_headline_first_stockout_next_order_and_cash_due_within_90_days():
    inp = S.mini_inputs()
    inp.stock[("intl", "bull")].on_hand = 100.0
    inp.pos = [S.po(1, units=10000, land=date(2027, 1, 15), order_date=date(2026, 11, 20)),            # deposit 9040 inside 90 days, balance 13560 outside? Jan 15 is day 73
                S.po(2, units=10000, land=date(2027, 6, 1))]
    res = run(inp)
    t = res.tldr
    assert t["first_stockout"]["location"] == "intl" and t["first_stockout"]["product"] == "bull"
    assert t["next_order_by"]["status"] == "OVERDUE"
    assert t["cash_next_90_days"] == pytest.approx(9040 + 13560)                                     # both dates fall inside Nov 3 .. Feb 1
    assert t["inventory_value_now"] == pytest.approx((10000 + 2000 + 100) * 2.26)                    # units on hand at the three locations x China landed cost


def test_vendor_price_or_deposit_default_fills_new_orders():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, vendor="KeyStone", warehouse="revamp", units=1000, land=date(2027, 5, 1))]
    row = run(inp).po_rows[0]
    assert row["unit_cost"] == 3.92 and row["deposit_pct"] == 0.5 and row["lead_days"] == 84


# ---------------------------------------------------------------------------------------------------------------- settings, reference day, odd inputs

def test_settings_ignore_junk_and_apply_valid_overrides():
    s = Settings.from_params({"inventory_supply": {"fba_target_months": 2.0, "horizon_months": "x", "safety_days": -3, "order_soon_days": True, "reorder_cover_months": 4}})
    assert s.fba_target_months == 2.0 and s.reorder_cover_months == 4.0 and s.horizon_months == 36 and s.safety_days is None and s.order_soon_days == 21.0
    assert Settings.from_params(None).fba_target_months == 1.5 and Settings.from_params({}).horizon_months == 36


def test_reference_day_priority_is_scenario_param_then_env_then_clock(monkeypatch):
    from app.projection.inventory_supply import loaders
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    assert loaders.resolve_today({"inventory_supply": {"as_of": "2027-02-10"}}) == date(2027, 2, 10)
    assert loaders.resolve_today({}) == date(2026, 11, 3)
    monkeypatch.setenv("PROJECTION_TODAY", "not a date")
    assert loaders.resolve_today({"inventory_supply": {"as_of": 5}}) == date.today()
    monkeypatch.delenv("PROJECTION_TODAY")
    assert loaders.resolve_today(None) == date.today()


def test_negative_opening_stock_and_a_safety_override_flow_into_reorder():
    inp = S.mini_inputs(stock_bull=-500.0, allocated=0.0)
    res = run(inp, settings=Settings(safety_days=0.0))
    r = next(x for x in res.reorder if x["location"] == "revamp")
    assert r["stockout_month"] == "2026-11-01" and r["safety_days"] == 0
    assert r["serial"]["must_land_by"] == pytest.approx(r["serial"]["est_stockout"])           # no safety days: must land when it runs out
    assert r["serial"]["est_stockout"] < calc.serial(date(2026, 11, 1))                        # already out before the month began


def test_a_single_month_of_demand_larger_than_stock_goes_out_that_month_only_when_no_inbound_follows():
    inp = S.mini_inputs(stock_bull=1000.0, allocated=0.0)
    inp.pos = [S.po(1, warehouse="revamp", vendor="KeyStone", units=50000, land=date(2026, 12, 10), status="ordered")]
    res = run(inp)
    st = res.status_at[("revamp", "bull")]
    assert st[date(2026, 11, 1)] == "OUT" and st[date(2026, 12, 1)] == ""


def test_outlay_covers_the_whole_axis_and_ignores_dates_outside_it():
    inp = S.mini_inputs()
    inp.pos = [S.po(1, units=1000, land=date(2031, 1, 1), order_date=date(2030, 12, 1)), S.po(2, units=1000, land=date(2025, 1, 15), order_date=date(2024, 12, 1))]
    res = run(inp)
    from app.projection.interfaces import MONTHS
    assert set(res.outlay) == set(MONTHS)
    assert res.outlay[date(2025, 1, 1)] == pytest.approx(1000 * 2.26 * 0.6)                  # only the balance falls inside the window
    assert "outside_axis" in codes(res)


def test_demand_extra_keys_for_month_to_date_never_become_locations():
    dem = S.flat_demand({("revamp", "bull"): 3000.0}, mtd={("revamp", "bull"): 700.0})
    assert ("revamp_mtd", "bull") in dem.units
    res = calc.compute_supply(S.mini_inputs(), dem, TODAY)
    assert all(loc in ("revamp", "amazon", "intl") for loc, _ in res.combos)
    assert res.burn[("revamp", "bull", "demand")][date(2026, 11, 1)] == 2300.0
