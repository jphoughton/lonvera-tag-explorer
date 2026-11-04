"""Cash waterfall (pure), seller note EBITDA-floor flag, summary wording, registry and engine integration."""
from __future__ import annotations

from datetime import date

import pytest

import fin_helpers as H
from app.projection import engine, registry, scenarios
from app.projection.financing import calc, compute, load_inputs, model
from app.projection.interfaces import MONTHS, PnlResult

CLOSE = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def only_financing(monkeypatch):
    """This module real, every other module on its fixture stub, so the numbers do not move as other modules land."""
    real = registry.discover
    monkeypatch.setattr(registry, "discover", lambda package=None, refresh=False: {k: v for k, v in real(package, refresh).items() if k == "financing"})


def m(y, mo):
    return date(y, mo, 1)


def wf(**kw):
    base = dict(close=CLOSE, noi_before_inventory={}, loan_interest={}, loan_principal={}, seller_principal={}, seller_interest={}, tax={}, equity={}, inventory={})
    base.update(kw)
    return calc.cash_waterfall(MONTHS, **base)


def test_waterfall_ending_cash_is_opening_plus_net_flow_and_carries_forward():
    c = wf(noi_before_inventory={m(2026, 9): 100.0, m(2026, 10): 50.0}, loan_interest={m(2026, 9): 10.0, m(2026, 10): 10.0}, loan_principal={m(2026, 10): 20.0},
           inventory={m(2026, 9): 30.0}, tax={m(2026, 10): 5.0}, equity={m(2026, 10): 1.0}, opening_first=1000.0)
    assert c.lines["opening_cash"][m(2026, 9)] == 1000.0
    assert c.lines["ending_cash"][m(2026, 9)] == 1000 + 100 - 10 - 30
    assert c.lines["opening_cash"][m(2026, 10)] == 1060.0
    assert c.lines["ending_cash"][m(2026, 10)] == 1060 + 50 - 10 - 20 - 5 - 1
    assert c.lines["loan_payment"][m(2026, 10)] == 30.0


def test_waterfall_months_before_close_are_absent_and_nothing_to_report_when_empty():
    c = wf()
    assert min(c.lines["ending_cash"]) == CLOSE and c.first_negative is None and c.lowest_value == 0.0 and c.negative_months == 0
    none = calc.cash_waterfall([], close=CLOSE, noi_before_inventory={}, loan_interest={}, loan_principal={}, seller_principal={}, seller_interest={}, tax={},
                               equity={}, inventory={})
    assert none.lowest_value is None and none.lines["ending_cash"] == {}


def test_waterfall_lowest_first_negative_and_floor():
    noi = {m(2026, 9): 100.0, m(2026, 10): -300.0, m(2026, 11): 150.0}
    c = wf(noi_before_inventory=noi, floor=-50.0)
    assert c.lowest_month == m(2026, 10) and c.lowest_value == -200.0 and c.first_negative == m(2026, 10)
    assert c.negative_months == 39                                          # Oct (-200) and every later month stays at -50
    assert c.below_floor_months == 1 and c.peak_shortfall == 150.0          # only Oct is below the -50 floor
    assert c.lines["headroom"][m(2026, 11)] == 0.0


def test_waterfall_opening_override_breaks_the_chain_for_one_month():
    c = wf(noi_before_inventory={m(2026, 9): -100.0, m(2026, 10): 10.0, m(2026, 11): 10.0}, opening_override={m(2026, 10): 0.0})
    assert c.lines["ending_cash"][m(2026, 9)] == -100.0 and c.lines["ending_cash"][m(2026, 10)] == 10.0 and c.lines["ending_cash"][m(2026, 11)] == 20.0


# ------------------------------------------------------------------------------------------------------------------ seller note flag

def test_seller_note_floor_flag_uses_trailing_year_noi_and_never_changes_cash(dconn, pg):
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(jsonb_set(params, '{financing,seller_note,enabled}', 'true'), '{financing,seller_note,ebitda_floor}', '1500000') "
               "WHERE slug = 'current'")
    with_floor = H.compute_with_snapshot(dconn)
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,seller_note,ebitda_floor}', 'null') WHERE slug = 'current'")
    without = H.compute_with_snapshot(dconn)
    flags = {f["month"]: f for f in with_floor.seller_flags}
    assert set(flags) == {m(2027, 9), m(2028, 9), m(2029, 9)}
    sheet_noi = H.snapshot_pnl().lines["noi"]
    expect = sum(sheet_noi[x] for x in MONTHS[MONTHS.index(m(2027, 9)) - 11: MONTHS.index(m(2027, 9)) + 1])
    assert flags[m(2027, 9)]["ttm_noi"] == pytest.approx(expect)
    assert flags[m(2027, 9)]["below_floor"] is (expect < 1_500_000)
    assert all(f["below_floor"] is True for f in with_floor.seller_flags[1:])    # NOI runs about $1.0M a year later on: under a $1.5M floor
    assert all(f["below_floor"] is None for f in without.seller_flags)           # no floor entered: nothing flagged
    assert with_floor.lines["ending_cash"] == without.lines["ending_cash"]       # the flag never gates the payment


# ------------------------------------------------------------------------------------------------------------------ wording

def test_negative_money_in_warnings_reads_minus_dollar(dconn):
    fin = H.compute_with_snapshot(dconn)
    w = next(x for x in fin.warnings if x.startswith("Ending cash"))
    assert "-$1,225,54" in w and "$-" not in w and "(" not in w


# ------------------------------------------------------------------------------------------------------------------ registry / engine

def test_the_module_is_discovered_and_runs_in_the_engine_in_order(dconn):
    info = registry.discover(refresh=True)["financing"]
    assert info.built and info.module.key == "financing" and info.module.ui == "financing.js" and info.module.blueprint is not None
    res = engine.run("current", dconn, strict=False)
    assert res.built["financing"] is True and res.stubbed.get("financing") is not True and "financing" not in res.errors
    fin = res.results["financing"]
    assert isinstance(fin, model.FinancingDetail) and fin.lines["ending_cash"]
    assert res.summary()["headline"]["lowest_ending_cash"]["month"]
    assert res.resolve("financing.lines.ending_cash") is fin.lines["ending_cash"]
    assert res.resolve("financing.debt.beginning")[0] == 3_000_000


def test_compute_signature_names_only_the_upstream_it_reads():
    import inspect
    params = list(inspect.signature(model.compute).parameters)
    assert params == ["scn", "inputs", "pnl", "inventory", "cohorts", "drivers"]


def test_payback_function_is_importable_for_the_pacing_module():
    from app.projection.financing.calc import payback
    p = payback(70.0, 100.0, 0.17, 5.5, [0.2] * 60)
    assert p.months is not None and len(p.cumulative) == 37
