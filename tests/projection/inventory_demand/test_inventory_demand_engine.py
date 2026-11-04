"""Engine: baseline, growth, seasonality, current-month blend, lags, gaps, history priority, totals, revenue tie. No database."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.projection.inventory_demand import engine
from app.projection.inventory_demand.engine import DemandInputs, compute_demand
from idemand_helpers import days

NOV3 = date(2026, 11, 3)
NOV = date(2026, 11, 1)
DEC = date(2026, 12, 1)


def flat(loc: str, prod: str, start: date, n: int, per_day: float) -> dict:
    return {(loc, prod): {d: per_day for d in days(start, n)}}


def inputs(*parts: dict, estimates=None, monthly=None) -> DemandInputs:
    daily: dict = {}
    for p in parts:
        daily.update(p)
    return DemandInputs(daily=daily, estimates=estimates or {}, monthly=monthly or {})


def levers(loc="revamp", **kw) -> dict:
    return {"inventory_demand": {"locations": {loc: kw}}}


def run(inp: DemandInputs, params: dict | None = None, as_of: date = NOV3, **kw):
    return compute_demand(inp, params or {}, as_of=as_of, **kw)


# ------------------------------------------------------------------------------------------------------------- baseline and blend

def test_baseline_is_the_trailing_average_of_complete_days():
    # 28 days ending Nov 2 at 10 a day, then older days at 1000 that must not count
    old = flat("revamp", "bull", date(2026, 9, 1), 20, 1000.0)
    new = flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)
    old[("revamp", "bull")].update(new[("revamp", "bull")])
    d = run(inputs(old))
    assert d.rates[("revamp", "bull")] == pytest.approx(10.0)
    assert d.window["revamp"] == (date(2026, 10, 6), date(2026, 11, 2))
    assert d.window_units[("revamp", "bull")] == pytest.approx(280.0)


def test_current_month_is_actual_to_date_plus_rate_times_remaining_days():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)))
    assert d.units[("revamp", "bull")][NOV] == pytest.approx(2 * 10 + 28 * 10)     # 2 actual days (Nov 1-2) + 28 forecast days
    assert d.units[("revamp_mtd", "bull")] == {NOV: pytest.approx(20.0)}
    assert d.mtd[("revamp", "bull")] == pytest.approx(20.0)
    assert d.actual_days["revamp"] == 2
    assert d.units[("revamp", "bull")][DEC] == pytest.approx(310.0)


def test_baseline_divides_by_the_window_even_when_the_data_is_shorter():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 20), 14, 10.0)))              # 14 days of data, window = 28
    assert d.rates[("revamp", "bull")] == pytest.approx(5.0)


def test_growth_compounds_from_the_as_of_month_not_before():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)), levers(monthly_growth=0.1))
    u = d.units[("revamp", "bull")]
    assert u[NOV] == pytest.approx(20 + 28 * 10)                                      # month 0: growth factor 1
    assert u[DEC] == pytest.approx(31 * 10 * 1.1)
    assert u[date(2027, 1, 1)] == pytest.approx(31 * 10 * 1.21)
    assert d.daily_rate[("revamp", "bull")][date(2027, 1, 1)] == pytest.approx(12.1)


def test_seasonality_is_by_calendar_month_and_repeats_each_year():
    season = [1.0] * 12
    season[11] = 2.0                                                                  # December
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)), levers(seasonality=season))
    u = d.units[("revamp", "bull")]
    assert u[DEC] == pytest.approx(31 * 10 * 2)
    assert u[date(2027, 12, 1)] == pytest.approx(31 * 10 * 2)
    assert u[date(2027, 1, 1)] == pytest.approx(31 * 10)


def test_lag_days_ignores_the_newest_loaded_days():
    base = flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)
    base[("revamp", "bull")][date(2026, 11, 2)] = 1000.0                               # a spike on the newest day
    d = run(inputs(base), levers(lag_days=1, baseline_days=7))
    assert d.last_full_day["revamp"] == date(2026, 11, 1)
    assert d.rates[("revamp", "bull")] == pytest.approx(10.0)                          # the spike is outside the window
    assert d.units[("revamp", "bull")][NOV] == pytest.approx(10 + 29 * 10)


def test_typed_rate_replaces_the_baseline_and_per_product_growth_beats_the_location():
    inp = inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0), flat("revamp", "liver", date(2026, 10, 6), 28, 10.0))
    params = levers(monthly_growth=0.5, products={"bull": {"rate": 4.0}, "liver": {"monthly_growth": 0.0}})
    d = run(inp, params)
    assert d.rates[("revamp", "bull")] == pytest.approx(4.0)
    assert d.units[("revamp", "bull")][DEC] == pytest.approx(31 * 4.0 * 1.5)
    assert d.units[("revamp", "liver")][DEC] == pytest.approx(31 * 10.0)               # per-product growth 0 beats the location's 50%


def test_bad_params_fall_back_to_defaults_instead_of_failing():
    params = {"inventory_demand": {"locations": {"revamp": {"baseline_days": "x", "monthly_growth": 99, "seasonality": [1, 2], "tie_to_dtc_revenue": "yes",
                                                              "products": {"bull": {"rate": -3}, "Bad Key": {}}}}}}
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)), params)
    assert d.rates[("revamp", "bull")] == pytest.approx(10.0)                          # 28-day default window
    assert d.units[("revamp", "bull")][DEC] == pytest.approx(310.0)


# ------------------------------------------------------------------------------------------------------------- gaps and boundaries

def test_stale_feed_fills_the_gap_months_with_the_forecast():
    # Amazon-style feed that ends Oct 9 while the as-of date is Nov 3
    inp = inputs(flat("amazon", "bull", date(2026, 9, 10), 30, 10.0))
    d = run(inp)
    u = d.units[("amazon", "bull")]
    assert d.last_full_day["amazon"] == date(2026, 10, 9)
    assert u[date(2026, 10, 1)] == pytest.approx(9 * 10 + 22 * 10)                     # 9 actual days + 22 forecast days
    assert u[NOV] == pytest.approx(300.0)                                              # pure forecast: the feed has nothing in November
    assert d.units[("amazon_mtd", "bull")] == {NOV: 0.0}
    assert d.actual_days["amazon"] == 0


def test_feed_ending_on_the_last_day_of_a_month_has_no_fill_in_that_month():
    inp = inputs(flat("intl", "bull", date(2026, 10, 3), 29, 10.0))                    # Oct 3 .. Oct 31
    d = run(inp, levers("intl", baseline_days=29), as_of=NOV3)
    u = d.units[("intl", "bull")]
    assert d.last_full_day["intl"] == date(2026, 10, 31)
    assert u[date(2026, 10, 1)] == pytest.approx(290.0)                                # actual only (Oct 1-2 had no rows)
    assert u[NOV] == pytest.approx(300.0)


def test_as_of_on_the_first_of_the_month_has_no_actual_days():
    inp = inputs(flat("revamp", "bull", date(2026, 10, 4), 28, 10.0))                  # Oct 4 .. Oct 31
    d = run(inp, as_of=NOV)
    assert d.actual_days["revamp"] == 0
    assert d.units[("revamp", "bull")][NOV] == pytest.approx(300.0)


def test_leap_february_has_29_days():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)))
    assert d.units[("revamp", "bull")][date(2028, 2, 1)] == pytest.approx(29 * 10.0)
    assert d.units[("revamp", "bull")][date(2027, 2, 1)] == pytest.approx(28 * 10.0)


def test_history_prefers_full_daily_coverage_then_monthly_then_a_partial_daily_sum():
    daily = {d: 1.0 for d in days(date(2026, 6, 17), 40)}                              # Jun 17 .. Jul 26
    daily.update({d: 2.0 for d in days(date(2026, 7, 27), 98)})                         # through Nov 2
    monthly = {date(2026, 6, 1): 999.0, date(2026, 7, 1): 888.0}
    d = run(DemandInputs(daily={("amazon", "bull"): daily}, monthly={("amazon", "bull"): monthly}))
    u = d.units[("amazon", "bull")]
    assert u[date(2026, 6, 1)] == 999.0                                                # daily starts mid-June: the monthly figure wins
    assert u[date(2026, 7, 1)] == pytest.approx(26 * 1.0 + 5 * 2.0)                    # fully covered by daily: daily wins over the monthly tab
    assert u[date(2026, 8, 1)] == pytest.approx(31 * 2.0)
    assert date(2026, 5, 1) not in u                                                    # nothing before the first data month


def test_each_product_is_judged_on_its_own_daily_coverage_not_the_locations():
    """One product has daily history from Jan, another only from Aug: months before Aug use that product's monthly figure."""
    bull = {d: 2.0 for d in days(date(2026, 1, 1), 306)}                      # Jan 1 .. Nov 2
    belle = {d: 1.0 for d in days(date(2026, 8, 1), 94)}                      # Aug 1 .. Nov 2
    monthly = {("amazon", "belle"): {date(2026, 3, 1): 41.0, date(2026, 8, 1): 9999.0}}
    d = run(DemandInputs(daily={("amazon", "bull"): bull, ("amazon", "belle"): belle}, monthly=monthly))
    assert d.units[("amazon", "belle")][date(2026, 3, 1)] == 41.0               # no daily rows for Belle in March: the monthly history stays
    assert d.units[("amazon", "belle")][date(2026, 8, 1)] == pytest.approx(31.0)  # daily covers August fully: daily beats the monthly figure
    assert d.units[("amazon", "bull")][date(2026, 3, 1)] == pytest.approx(62.0)


def test_a_partial_first_month_without_a_monthly_figure_sums_the_days_it_has():
    d = run(inputs(flat("revamp", "bull", date(2026, 7, 17), 109, 3.0)))               # Jul 17 .. Nov 2
    assert d.units[("revamp", "bull")][date(2026, 7, 1)] == pytest.approx(15 * 3.0)


def test_location_without_data_is_absent_but_a_typed_rate_still_forecasts():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)))
    assert not any(k[0] == "amazon" for k in d.units)
    assert d.last_full_day["amazon"] is None and d.window["amazon"] is None
    d2 = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)), levers("amazon", products={"bull": {"rate": 7.0}}))
    assert d2.units[("amazon", "bull")][NOV] == pytest.approx(30 * 7.0)
    assert d2.units[("amazon", "bull")] .get(date(2026, 10, 1)) is None


def test_no_data_at_all_gives_empty_results_without_dividing_by_zero():
    d = run(DemandInputs())
    assert d.units == {} and d.total == {} and d.share == {}
    assert d.current_month == NOV


# ------------------------------------------------------------------------------------------------------------- totals and estimates

def test_totals_and_shares_add_up_and_never_divide_by_zero():
    inp = inputs(flat("revamp", "bull", date(2026, 10, 4), 30, 10.0), flat("amazon", "bull", date(2026, 10, 4), 30, 5.0),
                 flat("intl", "flow", date(2026, 10, 4), 30, 5.0))
    d = run(inp)
    assert d.total[DEC] == pytest.approx(31 * 20)
    assert d.product_total["bull"][DEC] == pytest.approx(31 * 15)
    assert d.product_total["flow"][DEC] == pytest.approx(31 * 5)
    assert d.location_total["revamp"][DEC] == pytest.approx(310)
    assert sum(d.share[loc][DEC] for loc in d.share) == pytest.approx(1.0)
    assert d.share["revamp"][DEC] == pytest.approx(0.5)
    zero = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 0.0)))
    assert zero.share["revamp"][DEC] == 0.0


def test_estimate_days_inside_the_window_are_counted():
    inp = inputs(flat("intl", "bull", date(2026, 9, 13), 30, 10.0), estimates={"intl": {date(2026, 9, 16) + timedelta(days=i) for i in range(27)}})
    d = run(inp)
    assert d.last_full_day["intl"] == date(2026, 10, 12)
    assert d.estimate_days_in_window["intl"] == 27


# ------------------------------------------------------------------------------------------------------------- as-of date and revenue tie

def test_as_of_comes_from_the_pinned_param_then_the_environment_then_today(monkeypatch):
    monkeypatch.delenv("PROJECTION_TODAY", raising=False)
    assert engine.resolve_as_of({}, today=date(2026, 5, 6)) == date(2026, 5, 6)
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    assert engine.resolve_as_of({}, today=date(2026, 5, 6)) == NOV3
    assert engine.resolve_as_of({"inventory_demand": {"as_of": "2026-12-09"}}) == date(2026, 12, 9)
    monkeypatch.setenv("PROJECTION_TODAY", "not a date")
    assert engine.resolve_as_of({}, today=date(2026, 5, 6)) == date(2026, 5, 6)


def test_tie_to_revenue_scales_forward_months_by_revenue_per_day_against_the_baseline_window():
    inp = inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0))
    rev = {date(2026, 10, 1): 31000.0, NOV: 30000.0, DEC: 62000.0, date(2027, 1, 1): 15500.0}        # 1000 a day in the window, 2000 in Dec, 500 in Jan
    off = run(inp, levers(), dtc_revenue=rev)
    on = run(inp, levers(tie_to_dtc_revenue=True), dtc_revenue=rev)
    assert off.units[("revamp", "bull")][DEC] == pytest.approx(310.0)
    assert on.units[("revamp", "bull")][DEC] == pytest.approx(620.0)
    assert on.units[("revamp", "bull")][date(2027, 1, 1)] == pytest.approx(155.0)
    assert on.units[("revamp", "bull")][NOV] == pytest.approx(20 + 28 * 10)                         # factor 1 in November
    assert on.warnings == []


def test_tie_to_revenue_without_overlapping_revenue_changes_nothing_and_says_so():
    inp = inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0))
    d = run(inp, levers(tie_to_dtc_revenue=True), dtc_revenue={date(2028, 1, 1): 5000.0})
    assert d.units[("revamp", "bull")][DEC] == pytest.approx(310.0)
    assert d.warnings and "revamp" in d.warnings[0]
    assert run(inp, levers(tie_to_dtc_revenue=True)).warnings                                         # no revenue at all


def test_units_are_never_negative_with_extreme_decline():
    d = run(inputs(flat("revamp", "bull", date(2026, 10, 6), 28, 10.0)), levers(monthly_growth=-0.5))
    assert all(v >= 0 for v in d.units[("revamp", "bull")].values())
