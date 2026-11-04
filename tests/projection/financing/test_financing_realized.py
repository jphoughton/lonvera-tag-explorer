"""Payback default inputs: typed, else what new customers really cost and spent over the last 28 days (the Spend plan page's feeds and window),
else the last closed month; both readings also at the spend plan's own inputs."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import fin_helpers as H
from app.projection import registry, scenarios
from app.projection.drivers import loaders as drivers_loaders, model as drivers_model, reference as drivers_reference
from app.projection.financing import calc, compute, load_inputs, model
from app.projection.financing import params as P
from app.projection.interfaces import DriverResult

API = "/api/projection"
F = f"{API}/financing"
TODAY = date(2026, 11, 3)


@pytest.fixture(autouse=True)
def only_financing(monkeypatch):
    real = registry.discover
    monkeypatch.setattr(registry, "discover", lambda package=None, refresh=False: {k: v for k, v in real(package, refresh).items() if k == "financing"})
    monkeypatch.setattr(drivers_loaders, "current_date", lambda: TODAY)


def closed_month_drivers():
    return DriverResult(nc_aov={date(2026, 7, 1): 61.39}, nc_cpa={date(2026, 7, 1): 78.41}, actual_through=date(2026, 7, 1))


def run(dconn, drivers, **kw):
    scn = scenarios.get_scenario(dconn, "current")
    if kw.get("typed"):
        scn.params = P.deep_merge(scn.params, {"financing": {"payback": kw["typed"]}})
    return compute(scn, load_inputs(dconn, scn), pnl=H.snapshot_pnl(), inventory=H.snapshot_inventory(), cohorts=H.snapshot_cohorts(), drivers=drivers)


def test_the_default_is_the_last_28_days_realized_not_the_best_closed_month(dconn, feeds):
    fin = run(dconn, closed_month_drivers())
    i = fin.payback_inputs
    assert i["cac"] == pytest.approx(100.0) and i["aov"] == pytest.approx(70.0)                  # the window Oct 5 .. Nov 1; September's $50 and $90 do not count
    assert i["aov_source"] == i["cac_source"] == "last 28 days"
    assert i["aov_source_label"] == "last 28 days (Oct 5 to Nov 1), Meta + Google + Shopify feeds"
    assert i["realized"]["from"] == "2026-10-05" and i["realized"]["to"] == "2026-11-01" and i["realized"]["new_customers"] == 280
    assert i["reference"]["aov"] == 61.39 and i["reference"]["cac"] == 78.41                      # the closed month is still shown, as a reference
    rosy = calc.payback(61.39, 78.41, 0.1726, 5.53, H.snapshot_cohorts().curve_live, 36)
    assert fin.payback.months > rosy.months                                                      # the realized default is the cautious one
    assert fin.summary["payback_basis"] == i["aov_source_label"]


def test_realized_inputs_are_the_spend_plan_pages_figure(dconn, feeds):
    scn = scenarios.get_scenario(dconn, "current")
    lv = drivers_model.parse_levers(scn.params)
    chip = drivers_reference.load_reference(dconn, lv.ad_channels, lv.reference_days, TODAY).cpa_window            # what "Plan $95 vs last 28 days $x" compares against
    assert model.realized_inputs(dconn, scn, TODAY)["cac"] == chip["cpa"]
    assert model.realized_inputs(dconn, scn, TODAY)["spend"] == chip["spend"]


def test_typed_values_override_and_each_input_falls_back_on_its_own(dconn, feeds):
    typed = run(dconn, closed_month_drivers(), typed={"aov": 80.0, "cac": 90.0}).payback_inputs
    assert typed["aov"] == 80.0 and typed["cac"] == 90.0 and typed["aov_source"] == "scenario" and typed["aov_source_label"] == "typed in this scenario"
    mixed = run(dconn, closed_month_drivers(), typed={"cac": 95.0}).payback_inputs
    assert mixed["cac"] == 95.0 and mixed["cac_source"] == "scenario" and mixed["aov"] == pytest.approx(70.0) and mixed["aov_source"] == "last 28 days"


def test_without_feeds_it_falls_back_to_the_last_closed_month_and_says_so(dconn, pg):
    pg.execute("DROP TABLE IF EXISTS proj_feed_orders")
    pg.execute("DELETE FROM ad_daily")
    i = run(dconn, closed_month_drivers()).payback_inputs
    assert i["aov"] == 61.39 and i["cac"] == 78.41 and i["aov_source"] == "last closed month"
    assert i["realized"]["cac"] is None and i["realized"]["aov"] is None
    assert i["aov_source_label"] == "last closed month (Jul 2026)"
    assert "not loaded" in i["aov_fallback_reason"] and "not loaded" in i["cac_fallback_reason"]                  # the panel says why


def test_a_failing_feed_read_never_takes_cash_and_debt_down(dconn, feeds, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("feed unreadable")
    monkeypatch.setattr(drivers_reference, "load_reference", boom)
    fin = run(dconn, closed_month_drivers())
    assert fin.payback_inputs["realized"] is None and fin.payback_inputs["aov_source"] == "last closed month"
    assert fin.payback_inputs["cac_fallback_reason"] is None                                     # nothing to explain: the read itself failed
    assert fin.lines["ending_cash"]


def test_plan_inputs_use_the_first_plan_month_from_the_open_month(dconn, feeds):
    drv = closed_month_drivers()
    drv.nc_aov = {date(2026, 11, 1): 66.0, date(2026, 12, 1): 69.0, date(2027, 1, 1): 72.0}
    drv.nc_cpa = {date(2026, 11, 1): 90.0, date(2026, 12, 1): 95.0, date(2027, 1, 1): 99.0}
    drv.nc_basis = {date(2026, 11, 1): "measured", date(2026, 12, 1): "plan", date(2027, 1, 1): "plan"}      # November is driven by measured customers
    fin = run(dconn, drv)
    assert fin.payback_inputs["plan"] == {"month": "2026-12-01", "aov": 69.0, "cac": 95.0}
    curve = H.snapshot_cohorts().curve_live
    assert fin.payback_plan.months == calc.payback(69.0, 95.0, 0.1726, 5.53, curve, 36).months
    assert fin.payback_plan_loaded is None or fin.payback_plan_loaded.basis == "all variable costs"
    plain = closed_month_drivers()
    assert run(dconn, plain).payback_inputs["plan"] is None and run(dconn, plain).payback_plan is None
    assert fin.summary["payback_months_plan"] == fin.payback_plan.months


def test_payback_api_shows_the_realized_headline_and_the_plan_row(client, feeds):
    j = client.get(f"{F}/payback").get_json()
    assert j["inputs"]["realized"]["cac"] == pytest.approx(100.0) and j["inputs"]["aov_source"] == "last 28 days"
    assert j["result"]["cac"] == pytest.approx(100.0) and j["result"]["aov"] == pytest.approx(70.0)
    assert j["plan"]["inputs"]["month"] and j["plan"]["result"]["basis"] == "product margin" and "loaded" in j["plan"]
    q = client.get(f"{F}/payback?aov=61.39&cac=78.41").get_json()
    assert q["inputs"]["aov_source"] == "query" and q["inputs"]["aov_source_label"] == "entered on this page" and q["result"]["aov"] == 61.39
    assert q["plan"]["inputs"] == j["plan"]["inputs"]                                             # the plan row ignores the what-if inputs


def test_the_sheets_typed_values_still_tie_the_reconcile_lines_with_feeds_present(dconn, feeds):
    import reconcile as rc  # noqa: F401  (scripts/projection on sys.path via fin_helpers)
    fin = H.compute_with_snapshot(dconn)                          # sheet_payback=True: Payback!B7 / B8 typed as an explicit override
    bad = [r.key for r in H.reconcile(fin) if r.status != "ok" and r.key.startswith(("payback", "payout", "bonus", "comp"))]
    assert bad == []
    assert fin.payback_inputs["aov_source"] == "scenario"


def test_a_gap_in_the_spend_feed_falls_back_for_the_cost_only_and_says_why(dconn, pg, feeds):
    pg.execute("DELETE FROM ad_daily WHERE day < %s", (date(2026, 10, 24),))                      # Meta has 9 of the 28 days
    i = run(dconn, closed_month_drivers()).payback_inputs
    assert i["cac"] == 78.41 and i["cac_source"] == "last closed month" and i["cac_source_label"] == "last closed month (Jul 2026)"
    assert "9 of 28 days" in i["cac_fallback_reason"] and "Meta" in i["cac_fallback_reason"]
    assert i["aov"] == pytest.approx(70.0) and i["aov_source"] == "last 28 days" and i["aov_fallback_reason"] is None   # the orders feed is complete


def test_an_implausible_realized_cost_is_not_used(dconn, pg, feeds):
    pg.execute("UPDATE ad_daily SET spend = 5")                                                    # $5 a day over 280 customers: not a believable $0.50 per customer
    i = run(dconn, closed_month_drivers()).payback_inputs
    assert i["cac"] == 78.41 and "outside" in i["cac_fallback_reason"]


def test_payback_api_carries_the_fallback_reason(client, pg, feeds):
    pg.execute("DELETE FROM ad_daily WHERE day < %s", (date(2026, 10, 24),))
    j = client.get(f"{F}/payback").get_json()
    assert "9 of 28 days" in j["inputs"]["cac_fallback_reason"] and j["inputs"]["cac_source"] == "last closed month"
