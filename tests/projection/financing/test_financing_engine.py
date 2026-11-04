"""Financing engine on the seeded Current plan: reconcile against the frozen sheet, cash behaviour, scenario levers, defensive reads."""
from __future__ import annotations

from datetime import date

import pytest

import fin_helpers as H
from app.projection import scenarios
from app.projection.financing import calc, compute, load_inputs, model
from app.projection.financing import params as P
from app.projection.interfaces import DriverResult, InventoryResult, PnlResult, Scenario

sr = None


def snap():
    global sr
    if sr is None:
        import snapshot_read
        sr = snapshot_read
    return sr


def m(y, mo):
    return date(y, mo, 1)


def test_every_line_reconciles_except_the_known_tax_difference(dconn):
    fin = H.compute_with_snapshot(dconn)
    reps = H.reconcile(fin)
    assert len(reps) >= 40
    bad = {r.key for r in reps if r.status != "ok"}
    assert bad == {"tax", "starting_cash", "cash_after_inventory", "ending_cash"}, bad
    # every difference is the one sheet formula: P&L AI120 = ((BM100/12)*4)*0.21 with BM100 already the Sep-Dec 2026 total
    bm100 = snap().cell("P&L", "BM100")
    expected = bm100 * 0.21 - (bm100 / 12 * 4) * 0.21
    by = {r.key: r for r in reps}
    assert by["tax"].over == 1 and by["tax"].max_abs_at == "2027-09-01"
    for key in bad:
        assert by[key].max_abs == pytest.approx(expected, abs=0.01), key


def test_cash_ties_to_the_sheet_when_its_one_tax_cell_is_used(dconn, pg):
    sheet_tax = snap().cell("P&L", "AI120")
    pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) SELECT id, 'financing.tax', '2027-09-01', %s, 'override', 'sheet value' "
               "FROM proj_scenarios WHERE slug = 'current'", (sheet_tax,))
    fin = H.compute_with_snapshot(dconn)
    assert fin.lines["tax"][m(2027, 9)] == pytest.approx(sheet_tax)
    assert [r.key for r in H.reconcile(fin) if r.status != "ok"] == []


def test_cash_starts_at_the_close_month(dconn):
    fin = H.compute_with_snapshot(dconn)
    assert min(fin.lines["ending_cash"]) == m(2026, 9) and max(fin.lines["ending_cash"]) == m(2029, 12) and len(fin.lines["ending_cash"]) == 40
    assert m(2026, 8) not in fin.lines["ending_cash"]
    assert fin.lines["noi_before_inventory"][m(2026, 9)] == 150_000          # typed for the close month
    assert fin.lines["ending_cash"][m(2026, 9)] == pytest.approx(-85_475, abs=1)


def test_opening_override_replaces_the_carry_forward(dconn, pg):
    seeded = H.compute_with_snapshot(dconn)
    assert seeded.lines["opening_cash"][m(2026, 10)] == 0                       # the sheet types 0 for Oct-26
    pg.execute("DELETE FROM proj_series WHERE line_key = 'financing.opening_cash'")
    carried = H.compute_with_snapshot(dconn)
    shortfall = seeded.lines["ending_cash"][m(2026, 9)]
    assert carried.lines["opening_cash"][m(2026, 10)] == pytest.approx(shortfall)
    for mo in (m(2026, 10), m(2027, 6), m(2029, 12)):
        assert carried.lines["ending_cash"][mo] == pytest.approx(seeded.lines["ending_cash"][mo] + shortfall)    # the Sep-26 hole stays in every later month


def test_ending_cash_is_the_running_sum(dconn):
    fin = H.compute_with_snapshot(dconn)
    prev = None
    for mo, end in sorted(fin.lines["ending_cash"].items()):
        net = fin.lines["net_cash_flow"][mo]
        opening = fin.lines["opening_cash"][mo]
        assert end == pytest.approx(opening + net)
        if prev is not None and mo != m(2026, 10):
            assert opening == pytest.approx(prev)
        prev = end
    parts = ["loan_payment", "tax", "equity", "inventory_buys", "seller_principal", "seller_interest"]
    mo = m(2027, 5)
    assert fin.lines["net_cash_flow"][mo] == pytest.approx(fin.lines["noi_before_inventory"][mo] - sum(fin.lines[k].get(mo, 0.0) for k in parts))


def test_summary_headline(dconn):
    s = H.compute_with_snapshot(dconn).summary
    assert s["lowest_ending_cash"]["month"] == "2028-12-01" and s["lowest_ending_cash"]["value"] == pytest.approx(-1_225_546.24, abs=1)
    assert s["first_negative_month"] == "2026-09-01" and s["negative_months"] > 20
    assert s["payback_months"] == 10
    years = {y["year"]: y for y in s["debt_balance_year_end"]}
    assert years[2026]["balance"] == pytest.approx(2_620_000) and years[2027]["balance"] == pytest.approx(1_471_000)
    assert years[2028]["balance"] == pytest.approx(1_000) and years[2029]["balance"] == pytest.approx(1_000)
    assert s["total_interest"] == pytest.approx(575_900, abs=1)
    assert any("$1,000 is still outstanding" in w for w in s["warnings"])
    assert any("Ending cash is negative" in w for w in s["warnings"])


def test_floor_adds_headroom_and_counts_months_below(dconn, pg):
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,cash,floor}', '250000') WHERE slug = 'current'")
    fin = H.compute_with_snapshot(dconn)
    assert fin.lines["headroom"][m(2027, 1)] == pytest.approx(fin.lines["ending_cash"][m(2027, 1)] - 250_000)
    assert fin.cash.below_floor_months > fin.cash.negative_months
    assert fin.summary["peak_shortfall"] == pytest.approx(250_000 + 1_225_547, abs=1)


def test_equity_payback_and_seller_note_reduce_cash(dconn, pg):
    base = H.compute_with_snapshot(dconn)
    pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) SELECT id, 'financing.equity_payback', '2027-03-01', 100000, 'input' "
               "FROM proj_scenarios WHERE slug = 'current'")
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,seller_note,enabled}', 'true') WHERE slug = 'current'")
    fin = H.compute_with_snapshot(dconn)
    assert fin.lines["equity"][m(2027, 3)] == 100_000
    assert fin.lines["seller_principal"][m(2027, 9)] == pytest.approx(500_000) and fin.lines["seller_interest"][m(2027, 9)] == pytest.approx(75_000)
    assert fin.lines["ending_cash"][m(2027, 2)] == pytest.approx(base.lines["ending_cash"][m(2027, 2)])
    assert fin.lines["ending_cash"][m(2027, 3)] == pytest.approx(base.lines["ending_cash"][m(2027, 3)] - 100_000)
    assert fin.lines["ending_cash"][m(2029, 12)] == pytest.approx(base.lines["ending_cash"][m(2029, 12)] - 100_000 - 1_500_000 - 150_000)


def test_noi_override_replaces_one_month_only(dconn, pg):
    base = H.compute_with_snapshot(dconn)
    pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) SELECT id, 'financing.noi_before_inventory', '2027-06-01', 1000000, 'override' "
               "FROM proj_scenarios WHERE slug = 'current'")
    fin = H.compute_with_snapshot(dconn)
    assert fin.lines["noi_before_inventory"][m(2027, 6)] == 1_000_000
    assert fin.lines["noi_before_inventory"][m(2027, 7)] == base.lines["noi_before_inventory"][m(2027, 7)]
    assert fin.override_cells["financing.noi_before_inventory"] == [m(2026, 9), m(2027, 6)]


def test_loan_levers_flow_through(dconn, pg):
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,debt,rate}', '0.10') WHERE slug = 'current'")
    fin = H.compute_with_snapshot(dconn)
    assert fin.lines["interest"][m(2026, 9)] == pytest.approx(3_000_000 * 0.10 / 12)
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(jsonb_set(params, '{financing,debt,mode}', '\"fixed_payment\"'), '{financing,debt,fixed_payment}', '200000') "
               "WHERE slug = 'current'")
    fx = H.compute_with_snapshot(dconn)
    assert fx.lines["principal"][m(2026, 10)] == pytest.approx(200_000 - fx.lines["interest"][m(2026, 10)])
    assert fx.debt.payoff_month is not None


def test_close_month_moves_the_start(dconn, pg):
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,close_month}', '\"2027-01-01\"') WHERE slug = 'current'")
    fin = H.compute_with_snapshot(dconn)
    assert min(fin.lines["ending_cash"]) == m(2027, 1)
    assert fin.debt.months[0] == m(2027, 1)
    assert fin.lines["opening_cash"][m(2027, 1)] == 0


# ------------------------------------------------------------------------------------------------------------------ defensive reads

def test_bare_scenario_uses_the_code_defaults(bare, dconn):
    scn = scenarios.get_scenario(dconn, "current")
    fin = compute(scn, load_inputs(dconn, scn), pnl=H.snapshot_pnl(), inventory=H.snapshot_inventory(), cohorts=H.snapshot_cohorts())
    assert fin.lines["interest"][m(2026, 9)] == pytest.approx(37_500) and fin.lines["principal"][m(2026, 10)] == 0       # no typed principal: interest only
    assert fin.lines["noi_before_inventory"][m(2026, 9)] != 150_000                                                      # no override seeded
    assert fin.payback is None                                                                                          # no AOV or CAC and no drivers
    assert fin.payout.payback_month == 4


def test_compute_without_any_upstream_or_inputs():
    fin = compute(Scenario(id=1, slug="x", name="x"), None)
    assert fin.lines["ending_cash"][m(2026, 9)] == pytest.approx(-37_500)                        # only the interest
    assert fin.payback is None and fin.matrix is None
    assert any("Inventory buys read zero" in w for w in fin.warnings)


def test_garbage_in_params_does_not_crash():
    scn = Scenario(id=1, slug="x", name="x", params={"financing": {"debt": {"principal": "lots", "rate": None}, "tax": {"payments": [{"month": "bad"}]},
                                                                     "close_month": "nope"}})
    fin = compute(scn, None, pnl=PnlResult(lines={"noi": {m(2026, 9): 10.0}}))
    assert fin.close_month == date(2026, 9, 1)
    assert fin.tax_detail == []


def test_payback_inputs_fall_back_to_the_last_closed_month(bare, dconn):
    scn = scenarios.get_scenario(dconn, "current")
    drivers = DriverResult(nc_aov={m(2026, 5): 70.0, m(2026, 6): 72.0, m(2026, 7): 99.0}, nc_cpa={m(2026, 5): 90.0, m(2026, 6): 95.0, m(2026, 7): 1.0},
                           actual_through=m(2026, 6))
    fin = compute(scn, load_inputs(dconn, scn), cohorts=H.snapshot_cohorts(), drivers=drivers)
    assert fin.payback_inputs["aov"] == 72.0 and fin.payback_inputs["cac"] == 95.0                  # July is not closed
    assert fin.payback_inputs["aov_source"] == "last closed month"
    assert fin.payback is not None and fin.matrix is not None


def test_payout_repeat_source_and_fallback():
    prm = P.effective({})
    coh = H.snapshot_cohorts()
    goal, used = model.payout_repeat(prm, coh)
    assert used == "goal" and goal[0] == pytest.approx(0.21616751323705444) and len(goal) == 7
    prm["payout"]["repeat_source"] = "typed"
    prm["payout"]["repeat_typed"] = [0.5, 0.25]
    typed, used = model.payout_repeat(prm, coh)
    assert typed == [0.5, 0.25, 0, 0, 0, 0, 0] and used == "typed"
    prm["payout"]["repeat_source"] = "goal"
    none, used = model.payout_repeat(prm, type("C", (), {"curve_goal": [], "curve_live": [0.3] * 60})())
    assert used == "live" and none[0] == 0.3


def test_the_stub_pnl_line_key_for_cogs_is_accepted(dconn):
    scn = scenarios.get_scenario(dconn, "current")
    lines = {"noi": {m(2026, 10): 100.0}, "cogs": {m(2026, 10): 40.0}}
    fin = compute(scn, load_inputs(dconn, scn), pnl=PnlResult(lines=lines))
    assert fin.lines["noi_before_inventory"][m(2026, 10)] == 140.0


def test_scenario_clone_inherits_and_changes_independently(dconn, pg, app):
    from app.projection import rwdb
    with app.app_context(), rwdb.get_rw() as conn:
        scenarios.create(conn, "Higher rate", "current", actor="test")
        scenarios.update(conn, "higher-rate", params={"financing": {"debt": {"rate": 0.20}}}, actor="test")
    cur = H.compute_with_snapshot(dconn, "current")
    hi = H.compute_with_snapshot(dconn, "higher-rate")
    assert cur.lines["interest"][m(2026, 9)] == pytest.approx(37_500) and hi.lines["interest"][m(2026, 9)] == pytest.approx(50_000)
    assert hi.lines["principal"][m(2026, 10)] == 180_000                                         # the clone keeps the typed principal schedule
