"""revenue_lines: repeat, DTC, sales tax, total company revenue."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection.drivers.model import DriverInputs, compute_drivers
from app.projection.drivers.revenue import revenue_lines


def M(y, m):
    return date(y, m, 1)


def drivers(**kw):
    inp = DriverInputs(today=date(2026, 11, 3), **kw)
    return compute_drivers({}, inp)


def base_inputs():
    return dict(plan={"spend_total": {M(2026, 12): 100000.0}, "nc_cpa": {M(2026, 12): 100.0}, "nc_aov": {M(2026, 12): 70.0},
                      "amazon_rev": {M(2026, 12): 5000.0}, "tiktok_rev": {M(2026, 12): 100.0}})


def test_projected_month_stack_and_negative_tax():
    d = drivers(**base_inputs())
    m = M(2026, 12)
    rl = revenue_lines(d, {m: 300000.0})
    nc = d.nc_rev[m]
    assert rl.repeat_rev[m] == 300000.0
    assert rl.dtc_rev[m] == pytest.approx(300000 + nc)
    assert rl.sales_tax[m] == pytest.approx(-0.07 * (300000 + nc)) and rl.sales_tax[m] < 0
    assert rl.total_revenue[m] == pytest.approx(300000 + nc + 5000 + 100 - 0.07 * (300000 + nc))


def test_loaded_dtc_month_takes_the_books_value_and_no_tax():
    m = M(2026, 6)
    d = drivers(dtc_actual={m: 1_000_000.0}, amazon_actual={m: 200_000.0}, tiktok_actual={m: 3_000.0}, qb_spend={"meta": {m: 1.0}})
    rl = revenue_lines(d, {m: 1.0})
    assert rl.dtc_rev[m] == 1_000_000.0 and rl.sales_tax[m] == 0.0 and rl.total_revenue[m] == 1_203_000.0


def test_explicit_dtc_actual_argument_wins_over_the_drivers_own():
    m = M(2026, 6)
    d = drivers(dtc_actual={m: 1.0})
    rl = revenue_lines(d, {}, dtc_actual={m: 2.0})
    assert rl.dtc_rev[m] == 2.0 and rl.sales_tax[m] == 0.0


def test_repeat_override_replaces_the_cohort_value_for_that_month_only():
    d = drivers(overrides={"repeat_rev": {M(2026, 8): 984286.67}}, **base_inputs())
    rl = revenue_lines(d, {M(2026, 8): 1.0, M(2026, 12): 2.0})
    assert rl.repeat_rev[M(2026, 8)] == 984286.67 and rl.repeat_rev[M(2026, 12)] == 2.0


def test_sales_tax_rate_lever_applies():
    inp = DriverInputs(today=date(2026, 11, 3), **base_inputs())
    d = compute_drivers({"drivers": {"sales_tax_rate": 0.0}}, inp)
    rl = revenue_lines(d, {M(2026, 12): 1000.0})
    assert rl.sales_tax[M(2026, 12)] == 0.0


def test_attach_sets_the_result_and_is_idempotent_and_opt_out():
    d = drivers(**base_inputs())
    rl = revenue_lines(d, {M(2026, 12): 10.0}, attach=False)
    assert d.dtc_rev == {} and d.total_revenue == {}
    revenue_lines(d, {M(2026, 12): 10.0})
    first = dict(d.total_revenue)
    revenue_lines(d, {M(2026, 12): 10.0})
    assert d.total_revenue == first == rl.total_revenue and d.sales_tax == rl.sales_tax and d.repeat_rev == rl.repeat_rev


def test_missing_repeat_is_zero_not_an_error():
    d = drivers(**base_inputs())
    rl = revenue_lines(d, None)
    assert rl.repeat_rev[M(2027, 1)] == 0.0 and rl.dtc_rev[M(2027, 1)] == pytest.approx(d.nc_rev[M(2027, 1)])
