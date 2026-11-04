"""As-of curves: the Live-curve rule applied to only what was observable at the end of a month, the months that can be built, and the lever."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import build_history, make_cohort

from app.projection.cohorts import engine as eng
from app.projection.cohorts import model
from app.projection.interfaces import MONTHS, Scenario

D = date
TODAY = D(2026, 11, 3)


def scn(**cohort_params) -> Scenario:
    return Scenario(id=1, slug="current", name="Current plan", params={"cohorts": cohort_params} if cohort_params else {})


# ---------------------------------------------------------------------------------------------------------------- masking

def test_month_m_is_inclusive_and_its_own_cohort_has_no_repeat_yet():
    c = make_cohort(D(2026, 1, 1), n=100, first=50.0, ratios=[0.2, 0.1, 0.1, 0.1, 0.1])        # ages 1..5 = Feb..Jun 2026
    (m,) = eng.mask_asof([c], D(2026, 3, 1))                                                   # end of March: Feb and Mar have landed
    assert m.cum_at(1) is not None and m.cum_at(2) is not None and m.cum_at(3) is None
    (same_month,) = eng.mask_asof([c], D(2026, 1, 1))                                          # as of the cohort's own month: first order only
    assert same_month.first_order == 50.0 and all(same_month.cum_at(a) is None for a in range(1, 37))
    assert eng.mask_asof([c], D(2025, 12, 1)) == []                                            # a cohort one month too new is not there yet


def test_masking_never_changes_the_table_it_was_given():
    c = make_cohort(D(2026, 1, 1), ratios=[0.2, 0.1, 0.1])
    eng.mask_asof([c], D(2026, 2, 1))
    assert c.cum_at(3) is not None


def test_a_cell_landing_one_month_after_the_as_of_month_is_excluded_and_on_it_is_included():
    c = make_cohort(D(2026, 1, 1), n=100, first=50.0, ratios=[0.2, 0.4])
    live = eng.asof_curve([c], D(2026, 3, 1))
    assert live[1].source == "data" and live[1].value == pytest.approx(0.4)                    # age 2 lands in March: counted
    before = eng.asof_curve([c], D(2026, 2, 1))
    assert before[1].source in ("tail", "none") and before[1].value == pytest.approx(before[0].value * 0.95)    # one month earlier: decays from age 1


# ---------------------------------------------------------------------------------------------------------------- rule applied to the masked table

def test_the_last_n_cohorts_that_had_reached_each_age_as_of_m_are_averaged():
    cohorts = [make_cohort(D(2025, k, 1), n=100 * k, first=60.0, ratios=[0.1 * k, 0.05 * k, 0.02 * k]) for k in range(1, 7)]       # Jan..Jun 2025
    # as of Apr-25: Jan has ages 1..3, Feb 1..2, Mar 1, Apr none; n=2 -> age 1 reads Feb and Mar
    pts = eng.asof_curve(cohorts, D(2025, 4, 1), eng.Levers(n_cohorts=2))
    assert pts[0].cohorts_used == [D(2025, 2, 1), D(2025, 3, 1)]
    assert pts[0].value == pytest.approx((200 * 0.2 + 300 * 0.3) / 500)
    assert pts[1].cohorts_used == [D(2025, 1, 1), D(2025, 2, 1)]                              # age 2: only Jan and Feb had reached it
    assert pts[2].cohorts_used == [D(2025, 1, 1)]                                              # age 3: just Jan, fewer than n -> still reads it (min per age 1)


def test_fewer_cohorts_than_the_minimum_at_an_age_falls_back_to_decay_from_the_last_age_with_data():
    cohorts = [make_cohort(D(2025, k, 1), n=100, first=60.0, ratios=[0.1, 0.05, 0.02]) for k in range(1, 4)]
    lv = eng.Levers(n_cohorts=3, min_cohorts_per_age=3, tail_decay=0.9)
    pts = eng.asof_curve(cohorts, D(2025, 4, 1), lv)                                           # Jan has ages 1..3, Feb 1..2, Mar 1
    assert pts[0].source == "data" and pts[0].n_used == 3
    assert pts[1].source == "tail" and pts[1].value == pytest.approx(pts[0].value * 0.9)       # only two cohorts at age 2 (< 3)
    assert pts[2].value == pytest.approx(pts[1].value * 0.9) and pts[59].source == "tail"


def test_the_tail_starts_at_tail_from_age_or_at_the_last_observable_age_whichever_comes_first():
    cohorts = build_history(first=D(2024, 1, 1), last=D(2026, 6, 1), ratios=(0.2,) * 12)       # ages up to 12 observable for the oldest
    pts = eng.asof_curve(cohorts, D(2026, 6, 1), eng.Levers(n_cohorts=2, tail_decay=0.5))
    last_data = max(p.age for p in pts if p.source == "data")
    assert last_data == 12 and pts[12].source == "tail" and pts[12].value == pytest.approx(pts[11].value * 0.5)
    pts_late = eng.asof_curve(cohorts, D(2026, 6, 1), eng.Levers(n_cohorts=2, tail_from_age=8, tail_decay=0.5))
    assert pts_late[7].source == "tail" and pts_late[7].value == pytest.approx(pts_late[6].value * 0.5)       # tail_from_age wins when earlier


def test_the_curve_is_60_ages_and_max_age_still_caps_what_is_applied():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1))
    pts = eng.asof_curve(cohorts, D(2026, 10, 1))
    assert len(pts) == 60
    used, _ = eng.choose_curve([p.value for p in eng.live_curve(cohorts)], [0.0] * 60, eng.Levers(curve_source="asof", asof_month=D(2026, 10, 1), max_age=12),
                               [p.value for p in pts])
    assert set(used[12:]) == {0.0} and used[0] > 0


# ---------------------------------------------------------------------------------------------------------------- as of the latest month == Live

def test_as_of_the_latest_closed_month_reproduces_the_live_curve_exactly():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1))
    for lv in (eng.Levers(), eng.Levers(n_cohorts=6), eng.Levers(n_cohorts=2, min_cohorts_per_age=2, tail_decay=0.9, tail_from_age=5)):
        live = [p.value for p in eng.live_curve(cohorts, lv)]
        asof = [p.value for p in eng.asof_curve(cohorts, eng.observed_through(cohorts, TODAY), lv)]
        assert asof == live
        assert [p.cohorts_used for p in eng.asof_curve(cohorts, D(2026, 10, 1), lv)] == [p.cohorts_used for p in eng.live_curve(cohorts, lv)]


def test_the_model_result_for_the_latest_month_equals_the_current_curve_and_repeat_revenue():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1))
    inputs = model.CohortInputs(cohorts=cohorts, goal=[0.2, 0.1] + [0.01] * 58, goal_snapshot=D(2026, 9, 29), today=TODAY)
    nc = {m: 5000.0 for m in MONTHS}
    live = model.compute(scn(), inputs, nc_rev=nc)
    asof = model.compute(scn(curve_source="asof", asof_month="2026-10-01"), inputs, nc_rev=nc)
    assert asof.curve_used == live.curve_used and asof.repeat_rev == live.repeat_rev and asof.lifetime_multiple == live.lifetime_multiple
    assert asof.curve_label == "As of Oct 2026, last 3 cohorts" and live.curve_label == "Current, last 3 cohorts"


# ---------------------------------------------------------------------------------------------------------------- the lever through compute

def test_as_of_changes_repeat_revenue_and_leaves_live_and_goal_untouched():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1), ratios=(0.2, 0.3, 0.2, 0.1, 0.05))
    # make the newest cohorts weaker so an earlier as-of month reads differently from today
    weak = [eng.Cohort(**{**c.__dict__, "cum": tuple((v - 0.5 * (v - c.first_order) if v else v) for v in c.cum)}) if c.month >= D(2026, 6, 1) else c for c in cohorts]
    inputs = model.CohortInputs(cohorts=weak, goal=[0.2, 0.1] + [0.01] * 58, goal_snapshot=D(2026, 9, 29), today=TODAY)
    nc = {m: 5000.0 for m in MONTHS}
    base = model.compute(scn(), inputs, nc_rev=nc)
    earlier = model.compute(scn(curve_source="asof", asof_month="2026-05-01"), inputs, nc_rev=nc)
    assert earlier.curve_live == base.curve_live and earlier.curve_goal == base.curve_goal         # Live and Goal are never touched
    assert earlier.curve_used != base.curve_used and earlier.lifetime_multiple != base.lifetime_multiple
    assert earlier.curve_asof == earlier.curve_used and earlier.repeat_rev != base.repeat_rev
    assert earlier.curve_label == "As of May 2026, last 3 cohorts"
    goal = model.compute(scn(curve_source="goal"), inputs, nc_rev=nc)
    assert goal.curve_used == goal.curve_goal and goal.curve_label == "Goal, saved Sep 2026"


def test_the_default_is_unchanged_by_the_new_levers():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1))
    inputs = model.CohortInputs(cohorts=cohorts, goal=[0.2] * 60, goal_snapshot=D(2026, 9, 29), today=TODAY)
    nc = {m: 5000.0 for m in MONTHS}
    plain = model.compute(scn(), inputs, nc_rev=nc)
    explicit = model.compute(scn(curve_source="live", n_cohorts=3, asof_month="2026-08-01"), inputs, nc_rev=nc)       # a stored month does nothing while the source is live
    assert plain.repeat_rev == explicit.repeat_rev and plain.curve_used == explicit.curve_used


def test_an_as_of_month_with_no_data_warns_and_uses_the_current_curve():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1))
    inputs = model.CohortInputs(cohorts=cohorts, goal=[0.0] * 60, goal_snapshot=None, today=TODAY)
    res = model.compute(scn(curve_source="asof", asof_month="2024-06-01"), inputs, nc_rev={})
    assert res.curve_used == res.curve_live and res.warnings and "as-of" in res.warnings[0]


def test_legacy_curve_name_is_read_as_curve_source():
    assert eng.parse_levers({"curve": "goal"}).curve_source == "goal"
    assert eng.parse_levers({"curve": "goal", "curve_source": "live"}).curve_source == "live"       # the new name wins


# ---------------------------------------------------------------------------------------------------------------- which months can be built

def test_snapshots_list_each_month_with_whether_it_is_computable_and_its_multiples():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1), ratios=(0.2,) * 13)
    snaps = eng.asof_snapshots(cohorts, eng.Levers(), TODAY)
    assert snaps[0]["month"] == D(2025, 1, 1) and snaps[-1]["month"] == D(2026, 10, 1) and snaps[-1]["is_latest"]
    assert not snaps[0]["computable"] and snaps[0]["lifetime"] is None
    first_ok = next(s for s in snaps if s["computable"])
    assert first_ok["month"] == D(2026, 3, 1)                                  # needs 3 cohorts that reached month 12 (Jan, Feb, Mar 2025 + 12)
    assert all(s["computable"] for s in snaps if s["month"] >= first_ok["month"])
    assert snaps[-1]["lifetime"] == pytest.approx(eng.curve_sum([p.value for p in eng.live_curve(cohorts)]))
    assert snaps[-1]["twelve"] == pytest.approx(eng.curve_sum([p.value for p in eng.live_curve(cohorts)], 12))


def test_six_cohorts_need_a_later_start_than_three():
    cohorts = build_history(first=D(2025, 1, 1), last=D(2026, 10, 1), ratios=(0.2,) * 13)
    first3 = next(s["month"] for s in eng.asof_snapshots(cohorts, eng.Levers(n_cohorts=3), TODAY) if s["computable"])
    first6 = next(s["month"] for s in eng.asof_snapshots(cohorts, eng.Levers(n_cohorts=6), TODAY) if s["computable"])
    assert first6 > first3 and eng.N_COHORT_CHOICES == (3, 6)


def test_no_data_means_no_snapshots():
    assert eng.asof_snapshots([], eng.Levers(), TODAY) == []


# ---------------------------------------------------------------------------------------------------------------- labels

def test_labels_are_plain_english():
    lv = eng.Levers
    assert eng.curve_label(lv(), None) == "Current, last 3 cohorts"
    assert eng.curve_label(lv(n_cohorts=6), None) == "Current, last 6 cohorts"
    assert eng.curve_label(lv(curve_source="goal"), D(2026, 9, 29)) == "Goal, saved Sep 2026" and eng.curve_label(lv(curve_source="goal"), None) == "Goal"
    assert eng.curve_label(lv(curve_source="asof", asof_month=D(2026, 8, 1)), None) == "As of Aug 2026, last 3 cohorts"
    assert eng.curve_label(lv(curve_source="blend:0.25"), None) == "Blend: 25% Goal, 75% current"
