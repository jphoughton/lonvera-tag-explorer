"""Cohort math: increments, Live curve selection and weighting, Goal/blend choice, waterfall age semantics, actual repeat, levers."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import build_history, make_cohort

from app.projection.cohorts import engine as eng
from app.projection.interfaces import MONTHS, add_months

D = date


# ---------------------------------------------------------------------------------------------------------------- increments

def test_increment_is_cum_difference_and_age_one_starts_at_first_order():
    c = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.2, 0.3])
    assert c.increment(1) == pytest.approx(10.0)            # 60 - 50
    assert c.increment(2) == pytest.approx(15.0)            # 75 - 60
    assert c.increment_ratio(1) == pytest.approx(0.2)
    assert c.increment_ratio(2) == pytest.approx(0.3)
    assert c.increment(3) is None                           # not observed


def test_zero_and_blank_cells_are_not_observed():
    cum = [60.0, 0.0, None, 90.0] + [None] * 32
    c = eng.Cohort(month=D(2025, 1, 1), new_customers=10, first_order=50.0, cum=tuple(cum))
    assert c.increment(1) == pytest.approx(10.0)
    assert c.increment(2) is None                           # explicit zero = no data (sheet: IF(OR(M="",M=0),""...))
    assert c.increment(4) is None                           # previous cell missing: undefined rather than the sheet's M4 - 0
    assert c.last_observed_age() == 4


def test_missing_first_order_gives_no_increment():
    c = eng.Cohort(month=D(2025, 1, 1), new_customers=10, first_order=None, cum=(60.0,) + (None,) * 35)
    assert c.increment(1) is None and c.increment_ratio(1) is None and c.first_order_rev == 0.0


# ---------------------------------------------------------------------------------------------------------------- Live curve

def test_live_curve_weighted_average_of_last_n_cohorts_by_new_customers():
    a = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.10])
    b = make_cohort(D(2025, 2, 1), n=300, first=80.0, ratios=[0.20])
    c = make_cohort(D(2025, 3, 1), n=100, first=40.0, ratios=[0.50])
    pts = eng.live_curve([a, b, c], eng.Levers(n_cohorts=2))
    assert pts[0].value == pytest.approx((300 * 0.20 + 100 * 0.50) / 400)       # only the last two cohorts
    assert pts[0].cohorts_used == [D(2025, 2, 1), D(2025, 3, 1)] and pts[0].source == "data"
    pts3 = eng.live_curve([a, b, c], eng.Levers(n_cohorts=3))
    assert pts3[0].value == pytest.approx((100 * 0.10 + 300 * 0.20 + 100 * 0.50) / 500)


def test_live_curve_uses_the_latest_cohorts_that_reached_each_age():
    old = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.2, 0.4])            # reached age 2
    new = make_cohort(D(2025, 6, 1), n=100, first=50.0, ratios=[0.1])                 # only age 1
    pts = eng.live_curve([old, new], eng.Levers(n_cohorts=1))
    assert pts[0].cohorts_used == [D(2025, 6, 1)] and pts[0].value == pytest.approx(0.1)
    assert pts[1].cohorts_used == [D(2025, 1, 1)] and pts[1].value == pytest.approx(0.4)


def test_age_with_fewer_cohorts_than_the_minimum_falls_back_to_decay():
    only = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.2, 0.4])
    lv = eng.Levers(n_cohorts=3, min_cohorts_per_age=2, tail_decay=0.9)
    pts = eng.live_curve([only], lv)
    assert pts[0].source == "none" and pts[0].value == 0.0           # age 1 with nothing usable
    two = [only, make_cohort(D(2025, 2, 1), n=100, first=50.0, ratios=[0.2])]
    pts2 = eng.live_curve(two, lv)
    assert pts2[0].source == "data"                                  # two cohorts at age 1
    assert pts2[1].source == "tail" and pts2[1].value == pytest.approx(pts2[0].value * 0.9)   # one cohort at age 2 < minimum


def test_tail_decay_starts_at_tail_from_age_and_runs_to_age_60():
    cohorts = build_history(ratios=(0.2,) * 30, last=D(2028, 1, 1))
    pts = eng.live_curve(cohorts, eng.Levers(tail_from_age=10, tail_decay=0.5))
    assert pts[8].source == "data" and pts[9].source == "tail"
    assert pts[9].value == pytest.approx(pts[8].value * 0.5)
    assert pts[59].value == pytest.approx(pts[8].value * 0.5 ** 51)
    assert len(pts) == 60


def test_zero_increment_is_excluded_like_the_sheet():
    flat = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.0, 0.3])           # no repeat in month 1: increment 0
    live = make_cohort(D(2025, 2, 1), n=100, first=50.0, ratios=[0.2])
    pts = eng.live_curve([flat, live], eng.Levers(n_cohorts=3))
    assert pts[0].cohorts_used == [D(2025, 2, 1)] and pts[0].value == pytest.approx(0.2)


def test_empty_cohort_table_gives_an_all_zero_curve():
    pts = eng.live_curve([], eng.Levers())
    assert [p.value for p in pts] == [0.0] * 60
    total, by_year, _ = eng.waterfall(eng.cohort_inputs([], {m: 1000.0 for m in MONTHS}), [p.value for p in pts])
    assert set(total.values()) == {0.0} and by_year == {}


def test_cohort_with_no_customers_never_feeds_the_curve():
    ghost = make_cohort(D(2025, 1, 1), n=0, first=50.0, ratios=[0.9])
    real = make_cohort(D(2025, 2, 1), n=100, first=50.0, ratios=[0.1])
    assert eng.live_curve([ghost, real], eng.Levers())[0].value == pytest.approx(0.1)


# ---------------------------------------------------------------------------------------------------------------- curve choice

def test_choose_curve_live_goal_blend_and_max_age_cap():
    live = [0.1] * 60
    goal = [0.3] * 60
    assert eng.choose_curve(live, goal, eng.Levers(curve_source="live"))[0] == live
    assert eng.choose_curve(live, goal, eng.Levers(curve_source="goal"))[0] == goal
    blended, warn = eng.choose_curve(live, goal, eng.Levers(curve_source="blend:0.25"))
    assert warn is None and blended[0] == pytest.approx(0.25 * 0.3 + 0.75 * 0.1)
    capped, _ = eng.choose_curve(live, goal, eng.Levers(curve_source="live", max_age=12))
    assert capped[:12] == live[:12] and set(capped[12:]) == {0.0}


def test_goal_choice_without_a_goal_curve_falls_back_to_live_and_says_so():
    used, warn = eng.choose_curve([0.1] * 60, [0.0] * 60, eng.Levers(curve_source="goal"))
    assert used == [0.1] * 60 and "Goal curve" in warn


def test_curve_sums():
    curve = [0.5] * 60
    assert eng.curve_sum(curve) == pytest.approx(30.0) and eng.curve_sum(curve, 12) == pytest.approx(6.0)


# ---------------------------------------------------------------------------------------------------------------- waterfall

def test_age_is_whole_months_between_first_of_month_dates_like_datedif():
    assert eng.months_between(D(2025, 12, 1), D(2026, 1, 1)) == 1                 # year boundary
    assert eng.months_between(D(2025, 1, 1), D(2029, 12, 1)) == 59
    assert eng.months_between(D(2026, 5, 1), D(2026, 5, 1)) == 0


def test_repeat_is_zero_in_the_acquisition_month_and_before_and_after_the_last_age():
    curve = [0.1 * (i + 1) for i in range(60)]
    f = eng.repeat_for
    assert f(D(2026, 1, 1), 1000.0, curve, D(2026, 1, 1)) == 0.0
    assert f(D(2026, 1, 1), 1000.0, curve, D(2025, 12, 1)) == 0.0
    assert f(D(2026, 1, 1), 1000.0, curve, D(2026, 2, 1)) == pytest.approx(100.0)            # age 1
    assert f(D(2026, 1, 1), 1000.0, curve, D(2031, 1, 1)) == pytest.approx(6000.0)           # age 60 = last
    assert f(D(2026, 1, 1), 1000.0, curve, D(2031, 2, 1)) == 0.0                              # age 61
    assert f(D(2026, 1, 1), 1000.0, curve, D(2026, 6, 1), max_age=3) == 0.0                   # beyond the max_age lever
    assert f(D(2026, 1, 1), -50.0, curve, D(2026, 2, 1)) == 0.0                               # sheet: MAX(0, ...)


def test_cohort_inputs_use_the_table_where_a_row_exists_and_drivers_revenue_elsewhere():
    rows = [make_cohort(D(2026, 9, 1), n=100, first=60.0, ratios=[0.1]), make_cohort(D(2026, 10, 1), n=200, first=70.0)]
    nc = {m: 5000.0 for m in MONTHS}
    inputs = {w.month: w for w in eng.cohort_inputs(rows, nc)}
    assert inputs[D(2026, 9, 1)].nc_rev == pytest.approx(6000.0) and inputs[D(2026, 9, 1)].source == "table"
    assert inputs[D(2026, 10, 1)].nc_rev == pytest.approx(14000.0)
    assert inputs[D(2026, 11, 1)].nc_rev == 5000.0 and inputs[D(2026, 11, 1)].source == "drivers"
    assert len(inputs) == 60                                                 # both table months are axis months: one cohort per month


def test_pre_axis_cohorts_still_repeat_into_the_axis_and_by_year_sums_to_total():
    early = make_cohort(D(2024, 11, 1), n=100, first=50.0)                   # before the 2025-01 axis start
    curve = [0.2, 0.1] + [0.0] * 58
    nc = {m: 1000.0 for m in MONTHS}
    total, by_year, by_cohort = eng.waterfall(eng.cohort_inputs([early], nc), curve)
    assert total[D(2025, 1, 1)] == pytest.approx(5000.0 * 0.1)                          # Nov-24 cohort at age 2
    assert total[D(2025, 2, 1)] == pytest.approx(1000.0 * 0.2)                          # Jan-25 cohort at age 1; Nov-24 is past the curve
    assert sum(sum(s.values()) for s in by_year.values()) == pytest.approx(sum(total.values()))
    assert set(by_year) <= {2024, 2025, 2026, 2027, 2028, 2029}
    assert D(2024, 11, 1) in by_cohort


def test_total_repeat_is_the_sum_over_cohorts_for_a_known_grid():
    curve = [0.5, 0.25] + [0.0] * 58
    nc = {D(2025, 1, 1): 1000.0, D(2025, 2, 1): 2000.0}
    wf = [eng.WaterfallCohort(m, nc.get(m, 0.0), "drivers") for m in MONTHS]
    total, _, _ = eng.waterfall(wf, curve)
    assert total[D(2025, 1, 1)] == 0.0
    assert total[D(2025, 2, 1)] == pytest.approx(500.0)                              # Jan cohort age 1
    assert total[D(2025, 3, 1)] == pytest.approx(1000.0 * 0.25 + 2000.0 * 0.5)       # Jan age 2 + Feb age 1
    assert total[D(2025, 4, 1)] == pytest.approx(2000.0 * 0.25)


# ---------------------------------------------------------------------------------------------------------------- actual repeat

def test_actual_repeat_is_increment_times_customers_by_calendar_month():
    c = make_cohort(D(2025, 11, 1), n=200, first=50.0, ratios=[0.2, 0.4])
    a = eng.actual_repeat([c])
    assert a[D(2025, 12, 1)] == pytest.approx(200 * 10.0)
    assert a[D(2026, 1, 1)] == pytest.approx(200 * 20.0)
    assert D(2026, 2, 1) not in a


def test_actual_repeat_sums_cohorts_landing_in_the_same_month():
    a1 = make_cohort(D(2025, 1, 1), n=100, first=50.0, ratios=[0.2, 0.2])
    a2 = make_cohort(D(2025, 2, 1), n=100, first=50.0, ratios=[0.2])
    out = eng.actual_repeat([a1, a2])
    assert out[D(2025, 3, 1)] == pytest.approx(100 * 10.0 + 100 * 10.0)


# ---------------------------------------------------------------------------------------------------------------- levers

def test_levers_default_and_validation():
    assert eng.parse_levers(None) == eng.DEFAULT_LEVERS
    lv = eng.parse_levers({"n_cohorts": 6, "tail_decay": 0.9, "curve_source": "BLEND:0.4"})
    assert lv.n_cohorts == 6 and lv.curve_source == "blend:0.4" and lv.curve == "blend:0.4" and lv.blend_weight == pytest.approx(0.4)
    for bad in ({"n_cohorts": 0}, {"n_cohorts": 2.5}, {"tail_decay": 1.5}, {"curve_source": "median"}, {"curve_source": "blend:2"}, {"curve_source": "asof"}, {"asof_month": "2026-08-15"}, {"asof_month": "soon"}, {"nope": 1},
                {"n_cohorts": True}, {"n_cohorts": 2, "min_cohorts_per_age": 3}, {"max_age": 61}, {"tail_from_age": 1}):
        with pytest.raises(ValueError):
            eng.parse_levers(bad)


def test_non_strict_levers_ignore_bad_cells_instead_of_stopping_the_engine():
    lv = eng.parse_levers({"n_cohorts": 999, "tail_decay": "x", "unknown": 1, "curve_source": "live"}, strict=False)
    assert lv == eng.DEFAULT_LEVERS
    lv2 = eng.parse_levers({"n_cohorts": 2, "min_cohorts_per_age": 5}, strict=False)
    assert lv2.n_cohorts == 2 and lv2.min_cohorts_per_age == 2


# ---------------------------------------------------------------------------------------------------------------- frontiers

def test_data_through_is_the_last_closed_cohort_month_with_data():
    cohorts = build_history(last=D(2026, 10, 1))
    assert eng.data_through(cohorts, today=D(2026, 11, 3)) == D(2026, 10, 1)
    assert eng.data_through(cohorts, today=D(2026, 10, 15)) == D(2026, 9, 1)          # Oct is still open
    assert eng.data_through([], today=D(2026, 11, 3)) is None


def test_observed_through_is_the_last_calendar_month_a_loaded_cell_lands_in():
    c = make_cohort(D(2026, 7, 1), n=100, first=50.0, ratios=[0.1, 0.1, 0.1])
    assert eng.observed_through([c], today=D(2026, 11, 3)) == D(2026, 10, 1)
    assert eng.observed_through([c], today=D(2026, 9, 3)) == D(2026, 8, 1)             # cells landing in Sep/Oct are not closed yet


def test_add_months_helper_crosses_years():
    assert add_months(D(2025, 12, 1), 1) == D(2026, 1, 1) and add_months(D(2026, 1, 1), -1) == D(2025, 12, 1)
