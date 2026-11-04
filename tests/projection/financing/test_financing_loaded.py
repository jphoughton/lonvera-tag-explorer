"""Payback after all variable costs, the monthly top-up and what drives the low point, parameter validation at compute and through the module hook."""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

import fin_helpers as H
from app.projection import engine, registry, stubs
from app.projection.financing import calc, compute, load_inputs, model, tables
from app.projection.financing import params as P
from app.projection.interfaces import MONTHS, InventoryResult, Module, PnlResult

P_ = "/api/projection"
F = f"{P_}/financing"
CURVE = [0.1920160302407477, 0.2899985729511289, 0.162543966044943, 0.10806020914041027, 0.049588161155446335, 0.07561894876931764,
         0.048215842971944145] + [0.04] * 53


def m(y, mo):
    return date(y, mo, 1)


@pytest.fixture()
def only_financing(monkeypatch):
    real = registry.discover
    monkeypatch.setattr(registry, "discover", lambda package=None, refresh=False: {k: v for k, v in real(package, refresh).items() if k == "financing"})


# ------------------------------------------------------------------------------------------------------------------ calc

def test_fully_loaded_payback_uses_the_variable_cost_rate_instead_of_product_cost_and_fulfillment():
    product = calc.payback(69.156, 105.49, 0.1726, 5.53, CURVE, 60)
    loaded = calc.payback(69.156, 105.49, 0.1726, 5.53, CURVE, 60, fully_loaded=True, loaded_rate=0.3307)
    assert product.basis == "product margin" and loaded.basis == "all variable costs"
    assert loaded.cm_first == pytest.approx(69.156 * (1 - 0.3307)) and loaded.cm_pct == pytest.approx(0.6693) and loaded.cost_pct == pytest.approx(0.3307)
    assert product.cost_pct == pytest.approx((0.1726 * 69.156 + 5.53) / 69.156)
    assert loaded.cumulative[1] == pytest.approx(loaded.cm_first * (1 + CURVE[0]))
    assert product.months == 10 and loaded.months is not None and loaded.months > product.months          # slower once all variable costs count


def test_payback_pair_returns_both_or_only_the_product_margin():
    both = calc.payback_pair(70.0, 100.0, 0.17, 5.5, CURVE, 0.33)
    assert both["product_margin"].basis == "product margin" and both["fully_loaded"].basis == "all variable costs"
    assert both["fully_loaded"].months >= both["product_margin"].months
    assert calc.payback_pair(70.0, 100.0, 0.17, 5.5, CURVE, None)["fully_loaded"] is None


@pytest.mark.parametrize("rate", [None, -0.1, 1.0, 1.5, float("nan")])
def test_fully_loaded_needs_a_rate_between_zero_and_one(rate):
    with pytest.raises(ValueError):
        calc.payback(70.0, 100.0, 0.17, 5.5, CURVE, 36, fully_loaded=True, loaded_rate=rate)


def test_the_default_call_is_unchanged():
    assert calc.payback(70.0, 100.0, 0.17, 5.5, CURVE).months == calc.payback(70.0, 100.0, 0.17, 5.5, CURVE, 36, fully_loaded=False).months


# ------------------------------------------------------------------------------------------------------------------ P&L rates by name

def fake_pnl(through=m(2026, 7)):
    months = [m(2026, 6), m(2026, 7), m(2026, 8), m(2026, 9), m(2026, 10)]
    dtc = {x: 1000.0 for x in months}
    lines = {"dtc_rev": dtc, "china_cogs": {x: 100.0 for x in months}, "fulfillment_services": {x: 130.0 for x in months},
             "inbound_shipping": {x: 10.0 for x in months}, "payment_processing": {x: 40.0 for x in months},
             "chargeback_management": {x: 5.0 for x in months}}
    return PnlResult(lines=lines) if through is None else _with(PnlResult(lines=lines), through)


def _with(r, through):
    r.actual_through = through
    return r


def test_loaded_rate_is_the_sum_of_the_pnl_lines_over_dtc_revenue_for_the_forward_months():
    info = model.loaded_rate_info(P.effective({}), fake_pnl())
    assert info["source"] == "P&L rates" and info["rate"] == pytest.approx(0.285)               # 10% + 13% + 1% + 4% + 0.5%
    assert [x["key"] for x in info["parts"]] == P.LOADED_LINES and info["parts"][1]["rate"] == pytest.approx(0.13) and info["parts"][1]["label"] == "Fulfillment"
    assert info["months"] == ["2026-08-01", "2026-10-01"]                                       # after the P&L's last actual month
    assert info["missing"] == []


def test_loaded_rate_window_follows_the_lever_and_a_typed_rate_wins():
    pnl = fake_pnl()
    pnl.lines["china_cogs"][m(2026, 9)] = 700.0                                                  # a higher product cost later in the window
    prm = P.effective({"financing": {"payback": {"loaded_months": 1}}})
    assert model.loaded_rate_info(prm, pnl)["rate"] == pytest.approx(0.285)                      # only Aug-26
    prm = P.effective({"financing": {"payback": {"loaded_months": 3}}})
    assert model.loaded_rate_info(prm, pnl)["rate"] == pytest.approx(0.285 + 0.2)                # 600 / 3000 more of product cost
    typed = model.loaded_rate_info(P.effective({"financing": {"payback": {"loaded_rate": 0.4}}}), pnl)
    assert typed["rate"] == 0.4 and typed["source"] == "typed"
    assert model.loaded_rate_info(P.effective({}), pnl, 0.5)["rate"] == 0.5                       # a query override


def test_a_missing_line_never_reads_as_a_lower_cost():
    pnl = fake_pnl()
    del pnl.lines["payment_processing"]
    info = model.loaded_rate_info(P.effective({}), pnl)
    assert info["rate"] is None and info["missing"] == ["payment_processing"]
    assert model.loaded_rate_info(P.effective({}), PnlResult(lines={}))["rate"] is None
    only = model.loaded_rate_info(P.effective({"financing": {"payback": {"loaded_lines": ["fulfillment_services"]}}}), pnl)
    assert only["rate"] == pytest.approx(0.13)                                                   # the list is a lever


def test_loaded_lines_lever_is_validated():
    for bad in ([], ["Bad Key"], "fulfillment_services", [1], ["a"] * 13):
        with pytest.raises(Exception):
            P.validate_patch({"payback": {"loaded_lines": bad}})
    P.validate_patch({"payback": {"loaded_lines": ["china_cogs", "fulfillment_services"], "loaded_months": 6, "loaded_rate": None}})


def test_compute_with_the_sheets_cost_lines_gives_both_paybacks(dconn):
    fin = H.compute_with_snapshot(dconn, costs=True)
    assert fin.payback.months == 10 and fin.payback.basis == "product margin"                    # the sheet's number is unchanged
    assert fin.payback_loaded is not None and fin.payback_loaded.basis == "all variable costs"
    info = fin.loaded_info
    pnl = H.snapshot_pnl_with_costs()
    fwd = [x for x in sorted(pnl.lines["dtc_rev"]) if x > date(2026, 7, 1)][:12]
    expect = sum(sum(pnl.lines[k][x] for x in fwd) for k in P.LOADED_LINES) / sum(pnl.lines["dtc_rev"][x] for x in fwd)
    assert info["rate"] == pytest.approx(expect) and 0.30 < expect < 0.36
    assert fin.payback_loaded.months > fin.payback.months
    assert 17 <= fin.payback_loaded.months <= 24                                                 # the skeptic reads 19 (23 at a higher cost per customer)
    s = fin.summary
    assert s["payback_months"] == 10 and s["payback_months_loaded"] == fin.payback_loaded.months and s["payback_loaded_available"] is True


def test_without_cost_lines_the_loaded_payback_is_unavailable_not_zero_cost(dconn):
    fin = H.compute_with_snapshot(dconn)
    assert fin.payback is not None and fin.payback_loaded is None
    assert fin.summary["payback_loaded_available"] is False and fin.loaded_info["missing"]


def test_matrix_margin_can_follow_the_loaded_cost(dconn, pg):
    fin = H.compute_with_snapshot(dconn, costs=True)
    curve = H.snapshot_cohorts().curve_live
    product = model.run_matrix(fin.params, fin.payback, curve, fin.payback_loaded)
    loaded = model.run_matrix(fin.params, fin.payback, curve, fin.payback_loaded, margin="loaded")
    assert product.margin == "product" and loaded.margin == "loaded"
    assert loaded.cm_rate == pytest.approx(fin.payback_loaded.cm_pct)
    assert loaded.cm_rate < product.cm_rate
    assert loaded.breakeven_roas > product.breakeven_roas


# ------------------------------------------------------------------------------------------------------------------ API payloads

def test_payback_api_carries_both_readings_and_says_why_the_loaded_one_is_missing(client, only_financing, sheet_payback):
    j = client.get(f"{F}/payback").get_json()
    assert j["result"]["basis"] == "product margin" and j["result"]["months"] == 10
    assert j["loaded"]["result"] is None and j["loaded"]["info"]["missing"]                      # the stub P&L has no cost lines
    j2 = client.get(f"{F}/payback?loaded_rate=0.33").get_json()
    assert j2["loaded"]["result"]["basis"] == "all variable costs" and j2["loaded"]["result"]["months"] > j2["result"]["months"]
    assert j2["loaded"]["info"]["source"] == "typed" and j2["loaded"]["result"]["cost_pct"] == pytest.approx(0.33)
    assert client.get(f"{F}/payback?loaded_rate=2").status_code == 400


def test_payback_payload_from_a_real_result(dconn):
    fin = H.compute_with_snapshot(dconn, costs=True)
    pl = tables.payback_payload(fin, fin.payback, fin.payback_inputs, "live", fin.payback_loaded, fin.loaded_info)
    assert pl["result"]["months"] == 10 and pl["loaded"]["result"]["months"] == fin.payback_loaded.months
    assert [p["key"] for p in pl["loaded"]["info"]["parts"]] == P.LOADED_LINES


def test_matrix_api_margin_choice(client, only_financing):
    j = client.get(f"{F}/matrix?margin=loaded&loaded_rate=0.4").get_json()["result"]
    assert j["margin"] == "loaded" and j["cm_rate"] == pytest.approx(0.6)
    assert client.get(f"{F}/matrix").get_json()["result"]["margin"] == "product"
    assert client.get(f"{F}/matrix?margin=other").status_code == 400


# ------------------------------------------------------------------------------------------------------------------ top-up and driver sentence

def wf(**kw):
    base = dict(close=date(2026, 9, 1), noi_before_inventory={}, loan_interest={}, loan_principal={}, seller_principal={}, seller_interest={}, tax={},
                equity={}, inventory={})
    base.update(kw)
    return base


def test_top_up_funds_the_close_shortfall_once_then_the_smallest_monthly_amount():
    noi = {m(2026, 9): -100.0, m(2026, 10): -100.0, m(2026, 11): 150.0}
    funding, x = calc.top_up_to_zero(MONTHS, **wf(noi_before_inventory=noi))
    assert funding == pytest.approx(100.0) and x == pytest.approx(100.0, abs=1e-6)
    ok = calc.cash_waterfall(MONTHS, injection=x, close_funding=funding, floor=0.0, **wf(noi_before_inventory=noi))
    assert ok.lowest_value == pytest.approx(0.0, abs=1e-6) and ok.lines["ending_cash"][m(2026, 9)] == pytest.approx(0.0)
    short = calc.cash_waterfall(MONTHS, injection=x - 1, close_funding=funding, floor=0.0, **wf(noi_before_inventory=noi))
    assert short.lowest_value < 0


def test_top_up_when_only_later_months_go_negative():
    noi = {m(2026, 9): 50.0, m(2026, 10): -100.0, m(2026, 11): -100.0, m(2026, 12): 50.0}
    funding, x = calc.top_up_to_zero(MONTHS, **wf(noi_before_inventory=noi))
    assert funding == 0.0 and x == pytest.approx(75.0, abs=1e-6)               # cumulative -150 by Nov takes 2 x 75


def test_top_up_is_zero_when_cash_never_goes_negative_and_respects_overrides():
    assert calc.top_up_to_zero(MONTHS, **wf(noi_before_inventory={m(2026, 9): 50.0})) == (0.0, 0.0)
    # a typed starting-cash reset in Oct erases the Sep funding, so Oct and later need their own monthly amount
    noi = {m(2026, 9): -100.0, m(2026, 10): -100.0}
    funding, x = calc.top_up_to_zero(MONTHS, **wf(noi_before_inventory=noi, opening_override={m(2026, 10): 0.0}))
    assert funding == pytest.approx(100.0) and x == pytest.approx(100.0, abs=1e-6)


def _wf(dconn):
    """The waterfall inputs behind the sheet-input run, for checking the top-up against the exact waterfall."""
    from app.projection import scenarios
    scn = scenarios.get_scenario(dconn, "current")
    inp = load_inputs(dconn, scn)
    pnl, inv = H.snapshot_pnl(), H.snapshot_inventory()
    close = date(2026, 9, 1)
    d = inp.params["debt"]
    debt = calc.debt_schedule(MONTHS, principal=d["principal"], rate=d["rate"], start=close, principal_paid=inp.series.get("financing.principal_paid"))
    noi = {x: inp.series["financing.noi_before_inventory"].get(x, pnl.lines["noi"].get(x, 0.0) + pnl.lines["china_cogs"].get(x, 0.0)) for x in MONTHS if x >= close}
    pays = [{"month": date.fromisoformat(p["month"]), "from": date.fromisoformat(p["from"]), "to": date.fromisoformat(p["to"])} for p in inp.params["tax"]["payments"]]
    tax, _ = calc.tax_payments(MONTHS, pays, pnl.lines["noi"], 0.21)
    return dict(close=close, noi_before_inventory=noi, loan_interest=calc.as_series(debt.months, debt.interest), loan_principal=calc.as_series(debt.months, debt.principal),
                seller_principal={}, seller_interest={}, tax=tax, equity={}, inventory={x: v for x, v in inv.outlay.items() if x >= close},
                opening_override=inp.series.get("financing.opening_cash"))


def test_the_low_point_driver_is_computed_from_the_waterfall(dconn):
    fin = H.compute_with_snapshot(dconn)
    t = fin.top_up
    assert t["through"] == "2028-12-01" and t["monthly_top_up"] > 0
    assert t["close_funding"] == pytest.approx(85_475, abs=1)                                    # Sep-26 ends at -$85,475; funded once at close
    ok = calc.cash_waterfall(MONTHS, injection=t["monthly_top_up"], close_funding=t["close_funding"], floor=0.0, **_wf(dconn))
    assert ok.lowest_value == pytest.approx(0.0, abs=0.01)
    assert calc.cash_waterfall(MONTHS, injection=t["monthly_top_up"] - 1, close_funding=t["close_funding"], floor=0.0, **_wf(dconn)).lowest_value < 0
    keyed = {o["key"]: o["amount"] for o in t["outflows"]}
    assert keyed["term_loan_principal"] == pytest.approx(2_999_000)                              # the typed schedule pays $2.999M by Dec-28
    assert t["outflows"] == sorted(t["outflows"], key=lambda o: -o["amount"])
    # the pieces add up: earned - paid out + typed starting-cash resets = ending cash at the low point
    ending = fin.lines["ending_cash"][m(2028, 12)]
    paid = sum(o["amount"] for o in t["outflows"])
    assert t["operating"] - paid + t["starting_cash_adjustments"] == pytest.approx(ending, abs=0.01)
    # and the top-up does what it says
    again = H.compute_with_snapshot(dconn)
    assert again.top_up["monthly_top_up"] == pytest.approx(t["monthly_top_up"])
    s = tables.driver_sentence(fin, date(2026, 7, 1))
    assert s["sentence"].startswith("Through Dec 2028* the plan earns ")
    assert "$2.999M term-loan principal (the typed schedule)" in s["sentence"]
    assert "cash needs about $85.5K at close and +$" in s["sentence"] and s["sentence"].endswith("a month after to stay at zero.")


def test_no_driver_sentence_when_cash_stays_positive(dconn):
    scn_fin = compute(__import__("app.projection.scenarios", fromlist=["x"]).get_scenario(dconn, "current"), None,
                      pnl=PnlResult(lines={"noi": {x: 1_000_000.0 for x in MONTHS}}), inventory=InventoryResult())
    assert scn_fin.top_up["monthly_top_up"] == 0.0 and scn_fin.top_up["outflows"] == []
    assert tables.driver_sentence(scn_fin, date(2026, 7, 1)) is None


def test_cash_api_exposes_the_driver_and_top_up(client, only_financing):
    j = client.get(f"{F}/cash").get_json()
    s = j["summary"]
    assert s["monthly_top_up"] >= 0
    if s["lowest_ending_cash"]["value"] < 0:
        assert s["low_driver"]["sentence"].endswith("to stay at zero.") and s["low_driver"]["monthly_top_up"] == pytest.approx(s["monthly_top_up"])
    else:
        assert s["low_driver"] is None


# ------------------------------------------------------------------------------------------------------------------ validation and degraded upstream

def test_validate_params_is_the_modules_hook():
    from app.projection import financing
    assert financing.validate_params is P.validate_params
    P.validate_params(P.effective({}))                       # the defaults themselves are valid
    with pytest.raises(Exception) as e:
        financing.validate_params({"tax": {"rate": 7}})
    assert "financing.tax.rate" in str(getattr(e.value, "message", e.value))
    fields = {f.name for f in dataclasses.fields(Module)}
    if "validate_params" in fields:                          # once the core has the field, the module registers it
        assert financing.MODULE.validate_params is P.validate_params


def test_the_generic_scenario_patch_cannot_leave_an_invalid_value_unnoticed(client, only_financing):
    r = client.patch(f"{P_}/scenarios/current", json={"params": {"financing": {"tax": {"rate": 7}}}})
    if r.status_code == 400:                                  # the core calls MODULE.validate_params: refused up front
        assert "financing.tax.rate" in r.get_json()["error"]
        return
    assert r.status_code == 200                               # a core without the hook: stored, but the module refuses to compute with it and says why
    cash = client.get(f"{F}/cash")
    body = cash.get_json()
    assert cash.status_code == 503 and body["error"] == "upstream_degraded"
    assert body["modules"][0]["module"] == "financing" and "financing.tax.rate" in body["modules"][0]["error"]
    assert client.put(f"{F}/inputs", json={"scenario": "current", "params": {"tax": {"rate": 7}}}).status_code == 400


def test_compute_requires_the_pnl_and_the_inventory_plan():
    assert engine._required_upstream(model.compute) == ["inventory", "pnl"]


def test_financing_is_degraded_not_zeros_when_the_pnl_is_unavailable(dconn, monkeypatch, client, only_financing):
    monkeypatch.setattr(stubs, "STUBS", {k: v for k, v in stubs.STUBS.items() if k != "pnl"})
    res = engine.run("current", dconn)
    assert res.results["financing"] is None and "pnl" in res.errors["financing"]
    r = client.get(f"{F}/cash")
    assert r.status_code == 503 and r.get_json()["error"] == "upstream_degraded"
    assert "upstream unavailable: pnl" in r.get_json()["modules"][0]["error"]
    for path in ("debt", "payback", "matrix", "payout"):                       # every read of the module says the same
        assert client.get(f"{F}/{path}").status_code == 503


def test_the_generic_scenario_patch_refuses_bad_financing_values_through_the_modules_hook(client):
    from app.projection import financing
    if "validate_params" not in {f.name for f in dataclasses.fields(Module)}:
        pytest.skip("the core has no Module.validate_params field")
    assert financing.MODULE.validate_params is P.validate_params
    for params, needle in (({"tax": {"rate": 7}}, "financing.tax.rate"), ({"payback": {"product_cost_pct": 1.4}}, "financing.payback.product_cost_pct")):
        r = client.patch(f"{P_}/scenarios/current", json={"params": {"financing": params}})
        assert r.status_code == 400 and needle in r.get_json()["error"]
