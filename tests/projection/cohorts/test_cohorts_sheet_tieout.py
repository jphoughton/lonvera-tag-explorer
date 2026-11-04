"""The engine against the frozen sheet snapshot (2026-11-03): curves, the waterfall on the sheet's own inputs, and the explained differences."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from app.projection.cohorts import model
from app.projection.cohorts.engine import Cohort
from app.projection.interfaces import MONTHS, Scenario

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "projection"))
try:
    import snapshot_read as sr
    sr.cell("Curves", "C65")
except Exception as e:                                      # snapshot not on this machine
    pytest.skip(f"sheet snapshot not available: {e}", allow_module_level=True)

from etl import projection_cohorts as seeder                # noqa: E402

HW = "Hist Waterfall - Last 4 Months Curve"


@pytest.fixture(scope="module")
def result():
    rows, goal, snap = seeder.read_snapshot()
    cohorts = [Cohort(
        month=r.month, new_customers=r.new_customers, first_order=r.first_order, first_order_realized=r.first_order_realized,
        repeat_90d=r.repeat_90d, cac=r.cac, payback_months=r.payback_months, nc_rev_reference=r.nc_rev_reference,
        cum=tuple(r.cum.get(a) for a in range(1, 37))) for r in rows]
    nc = {sr.serial_to_date(m): v for m, v in zip(sr.row("P&L", 8, "C", "BJ"), sr.row("P&L", 12, "C", "BJ")) if isinstance(v, (int, float))}
    inputs = model.CohortInputs(cohorts=cohorts, goal=goal, goal_snapshot=snap, today=date(2026, 11, 3))
    return model.compute(Scenario(id=1, slug="current", name="Current plan", params={}), inputs, nc_rev=nc)


def _hw_row(r: int) -> dict:
    months = [sr.serial_to_date(x) for x in sr.row(HW, 5, "C", "BX")]
    return {m: (v if isinstance(v, (int, float)) else 0.0) for m, v in zip(months, sr.row(HW, r, "C", "BX")) if m in set(MONTHS)}


def test_live_curve_matches_curves_tab(result):
    for age, (eng_v, sheet_v) in enumerate(zip(result.curve_live, sr.col("Curves", "C", 4, 63)), start=1):
        assert eng_v == pytest.approx(sheet_v, rel=1e-5), f"age {age}"


def test_goal_curve_lifetime_and_twelve_month_match(result):
    assert result.curve_goal == pytest.approx(sr.col("Curves", "B", 4, 63), rel=1e-9)
    assert result.lifetime_multiple == pytest.approx(sr.cell("Curves", "C65"), rel=1e-5)
    assert result.twelve_month_multiple == pytest.approx(sr.cell("Curves", "C66"), rel=1e-5)
    assert result.lifetime_goal == pytest.approx(sr.cell("Curves", "B65"), rel=1e-9)


def test_waterfall_on_the_sheets_own_new_customer_revenue_ties_to_the_dollar(result):
    sheet = _hw_row(228)
    worst = max(abs(result.repeat_rev_sheet_basis[m] - sheet[m]) for m in MONTHS)
    assert worst < 1.0


def test_default_waterfall_differs_only_by_the_stale_typed_revenue_and_stays_within_two_percent(result):
    sheet = _hw_row(228)
    rel = {m: abs(result.repeat_rev[m] - sheet[m]) / max(result.repeat_rev[m], sheet[m]) for m in MONTHS if sheet[m]}
    assert max(rel.values()) < 0.02
    over = [m for m, r in rel.items() if r > 0.005 and abs(result.repeat_rev[m] - sheet[m]) > 1]
    assert 20 <= len(over) <= 35                              # documented: 29 months over 0.5% (MODULE_cohorts.md)
    # and the difference is entirely the typed revenue: replacing it removes every difference
    assert all(abs(result.repeat_rev_sheet_basis[m] - sheet[m]) < 1.0 for m in MONTHS)


def test_actual_repeat_ties_through_august_2026_and_the_sheet_layer_five_is_wrong_after(result):
    sheet = _hw_row(377)
    for m in MONTHS:
        if m <= date(2026, 8, 1):
            assert result.repeat_rev_actual.get(m, 0.0) == pytest.approx(sheet[m], rel=5e-3, abs=1.0), m
    assert abs(result.repeat_rev_actual.get(date(2026, 10, 1), 0.0) - sheet[date(2026, 10, 1)]) > 50_000     # sheet rows 336-339 point at the Jul-26 cohort


def test_seeded_snapshot_loads_33_cohorts_and_a_60_age_goal(result):
    assert len(result.cohort_rows) == 33 and result.cohort_rows[0]["month"] == "2024-02-01" and result.cohort_rows[-1]["month"] == "2026-10-01"
    assert len(result.curve_goal) == 60 and result.goal_snapshot == date(2026, 9, 29)
    assert result.data_through == date(2026, 10, 1)
