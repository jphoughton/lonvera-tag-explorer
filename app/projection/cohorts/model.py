"""Module `cohorts`: load the cohort table, compute the curves and the repeat-revenue waterfall for a scenario.

load_inputs(conn, scn) reads the tables; compute(scn, inputs, nc_rev=...) is pure. Levers come from the scenario's params['cohorts'].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..freshness import today_pacific
from ..interfaces import MONTHS, CohortResult, Scenario, Series
from . import engine as eng
from . import store

_AXIS = set(MONTHS)


def today() -> date:
    """The date every month-closed rule runs against: today in Pacific time (the shop's calendar). Tests patch this one function."""
    return today_pacific()


@dataclass
class CohortInputs:
    cohorts: list[eng.Cohort] = field(default_factory=list)
    goal: list[float] = field(default_factory=lambda: [0.0] * eng.CURVE_AGES)
    goal_snapshot: date | None = None
    today: date = field(default_factory=lambda: today())


@dataclass
class CohortResultX(CohortResult):
    """CohortResult plus what the page and the reconcile need. The engine only reads the declared CohortResult fields."""
    curve_used: list[float] = field(default_factory=list)              # the curve that drives repeat_rev (Live, Goal or a blend), capped at max_age
    curve_points: list[eng.AgePoint] = field(default_factory=list)     # Live curve with the cohorts behind each age
    levers: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    twelve_month_multiple: float = 0.0                                 # sum of the used curve, ages 1-12 (sheet Curves!C66)
    lifetime_live: float = 0.0
    lifetime_goal: float = 0.0
    twelve_live: float = 0.0
    twelve_goal: float = 0.0
    goal_snapshot: date | None = None
    data_through: date | None = None                                   # last fully closed cohort month loaded
    observed_through: date | None = None                               # last closed calendar month a loaded cumulative cell lands in (actual repeat exists up to here)
    repeat_by_year: dict[int, Series] = field(default_factory=dict)    # cohort year -> repeat $ by month
    first_order_rev: Series = field(default_factory=dict)              # new-customer revenue by acquisition month (table rows and forward months)
    repeat_rev_sheet_basis: Series = field(default_factory=dict)       # same waterfall on the sheet's typed new-customer revenue (tie-out)
    new_customers: Series = field(default_factory=dict)
    cohort_table: list[eng.Cohort] = field(default_factory=list)       # the loaded cohort rows (heatmap)


def load_inputs(conn, scn: Scenario | None = None) -> CohortInputs:
    goal, snap = store.load_goal(conn)
    return CohortInputs(cohorts=store.load_cohorts(conn), goal=goal, goal_snapshot=snap, today=today())


def _axis_only(s: Series) -> Series:
    return {m: v for m, v in s.items() if m in _AXIS}


def compute(scn: Scenario, inputs: CohortInputs, nc_rev: Series | None = None) -> CohortResultX:
    levers = eng.parse_levers((scn.params or {}).get("cohorts"), strict=False)
    cohorts = inputs.cohorts
    points = eng.live_curve(cohorts, levers)
    live = [p.value for p in points]
    goal = list(inputs.goal) + [0.0] * (eng.CURVE_AGES - len(inputs.goal))
    used, warning = eng.choose_curve(live, goal, levers)

    wf_inputs = eng.cohort_inputs(cohorts, nc_rev)
    total, by_year, _by_cohort = eng.waterfall(wf_inputs, used, MONTHS, levers.max_age)
    actual = _axis_only(eng.actual_repeat(cohorts))

    ref_by_month = {c.month: c.nc_rev_reference for c in cohorts if c.nc_rev_reference is not None}
    sheet_basis: Series = {}
    if ref_by_month:
        ref_inputs = [eng.WaterfallCohort(w.month, ref_by_month[w.month], "reference") if w.month in ref_by_month else w for w in wf_inputs]
        sheet_basis = eng.waterfall(ref_inputs, used, MONTHS, levers.max_age)[0]

    by_month = {w.month: w for w in wf_inputs}
    rows = []
    for c in cohorts:
        w = by_month.get(c.month)
        rows.append({"month": c.month.isoformat(), "new_customers": c.new_customers, "first_order": c.first_order,
                     "first_order_rev": w.nc_rev if w else c.first_order_rev, "source": w.source if w else "table",
                     "repeat_90d": c.repeat_90d, "cac": c.cac, "payback_months": c.payback_months,
                     "last_observed_age": c.last_observed_age(),
                     "repeat_actual_total": sum(c.increment(a) * c.new_customers for a in range(1, eng.COHORT_AGES + 1)
                                                if c.increment(a) is not None)})
    live_goal_warning = [warning] if warning else []
    return CohortResultX(
        curve_live=live, curve_goal=goal, lifetime_multiple=eng.curve_sum(used), repeat_rev=total, repeat_rev_actual=actual, cohort_rows=rows,
        curve_used=used, curve_points=points, levers=levers.to_dict(), warnings=live_goal_warning,
        twelve_month_multiple=eng.curve_sum(used, 12), lifetime_live=eng.curve_sum(live), lifetime_goal=eng.curve_sum(goal),
        twelve_live=eng.curve_sum(live, 12), twelve_goal=eng.curve_sum(goal, 12), goal_snapshot=inputs.goal_snapshot,
        data_through=eng.data_through(cohorts, inputs.today), observed_through=eng.observed_through(cohorts, inputs.today), repeat_by_year=by_year,
        first_order_rev={m: w.nc_rev for m, w in by_month.items() if m in _AXIS},
        repeat_rev_sheet_basis=sheet_basis, new_customers={c.month: c.new_customers for c in cohorts if c.month in _AXIS},
        cohort_table=list(cohorts))
