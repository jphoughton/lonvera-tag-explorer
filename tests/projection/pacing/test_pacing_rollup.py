"""Rollups against the sheet: Oct-2026 month, ISO weeks, single days, grain-specific reach, partial-period projection, EBITDA estimate."""
import datetime as dt
import math

import pytest

from app.projection.pacing import feeds, rollup
from app.projection.pacing.rollup import Period, apply_costs, project
from pacing_support import SNAPSHOT_DAY
import snapshot_read as sr

THR = dt.date(2026, 11, 2)
# Feed tabs held only in part by the snapshot (last 400 rows) or restated by Meta after the sheet read them: compared elsewhere, not here
FIXTURE_GAPS = {"meta_avg_cost_cap", "meta_avg_roas_floor", "meta_capped_spend_pct", "meta_pct_cost_cap", "meta_pct_troas_floor",
                "stage_nc_scaling", "stage_nc_testing", "stage_nc_total", "stage_cpa_scaling", "stage_cpa_testing", "stage_cpa_total", "stage_cpa_test_vs_scale"}
GAP_ALLOWANCE = {"meta_purchases": 0.012, "meta_purchase_value": 0.01, "meta_cvr": 0.012, "meta_aov": 0.01, "meta_roas": 0.01, "meta_cpa": 0.012,
                 "ebitda_gap": 0.02, "noi_gap": 0.02}


def sheet_row(tab, key, grain):
    for r in range(5, sr.load(tab)["rows"] + 1):
        v = sr.cell(tab, f"A{r}")
        if grain == "day" and isinstance(v, (int, float)) and sr.serial_to_date(v).isoformat() == key:
            return r
        if grain != "day" and v == key:
            return r
    raise AssertionError(f"{key} not in {tab}")


def compare(conn, book, grain, key, start, end, tab):
    fr = feeds.load_frame(conn, start, min(end, THR))
    apply_costs(fr, book, dt.date(2026, 11, 1))
    mine = rollup.rollup(fr, grain, start, end, THR)[0].values
    hdr = sr.row(tab, 3, 1, 200)
    r = sheet_row(tab, key, grain)
    checked, bad = 0, []
    for ci, k in enumerate(hdr):
        if k not in mine or k in FIXTURE_GAPS:
            continue
        sv = sr.cell(tab, f"{sr.col_letter(ci + 1)}{r}")
        if sv is None or isinstance(sv, str):
            continue
        mv = mine[k]
        if mv is None:
            bad.append((k, sv, mv))
            continue
        d, den = abs(mv - sv), max(abs(mv), abs(sv))
        if d > 1 and d / den > GAP_ALLOWANCE.get(k, 0.005):
            bad.append((k, sv, mv))
        checked += 1
    return checked, bad


def test_month_oct_2026_matches_the_sheet_per_metric(conn, book):
    checked, bad = compare(conn, book, "month", "2026-10", dt.date(2026, 10, 1), dt.date(2026, 10, 31), "Rollup - Monthly (MoM)")
    assert checked > 120 and not bad, bad


@pytest.mark.parametrize("key", ["2026-W42", "2026-W43", "2026-W44"])
def test_iso_weeks_match_the_sheet(conn, book, key):
    y, w = key.split("-W")
    start = dt.date.fromisocalendar(int(y), int(w), 1)
    checked, bad = compare(conn, book, "week", key, start, start + dt.timedelta(days=6), "Rollup - Weekly (WoW)")
    assert checked > 120 and not bad, bad


@pytest.mark.parametrize("day", ["2026-10-15", "2026-10-31", "2026-11-01", "2026-11-02"])
def test_days_match_the_sheet(conn, book, day):
    d = dt.date.fromisoformat(day)
    checked, bad = compare(conn, book, "day", day, d, d, "Pacing - Daily (180d)")
    assert checked > 120 and not bad, bad


def test_week_reach_is_read_at_week_grain_not_summed_from_days(conn, book):
    start = dt.date(2026, 10, 26)
    fr = feeds.load_frame(conn, start, dt.date(2026, 11, 1))
    week = rollup.rollup(fr, "week", start, dt.date(2026, 11, 1), THR)[0].values
    days = rollup.rollup(fr, "day", start, dt.date(2026, 11, 1), THR)
    assert week["meta_unique_outbound_clicks"] == 18146                                    # Rollup - Weekly 2026-W44
    assert sum(d.values["meta_unique_outbound_clicks"] for d in days) > week["meta_unique_outbound_clicks"]     # people clicking on several days
    assert week["meta_reach"] < sum(d.values["meta_reach"] for d in days)


def test_missing_reach_row_is_blank_not_zero(conn):
    fr = feeds.load_frame(conn, dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    v = rollup.rollup(fr, "day", dt.date(2025, 1, 2), dt.date(2025, 1, 2), THR)[0].values
    assert v["meta_unique_outbound_clicks"] is None or v["meta_unique_outbound_clicks"] > 0
    fr2 = feeds.load_frame(conn, dt.date(2026, 11, 2), dt.date(2026, 11, 2))
    fr2.reach.clear()
    v2 = rollup.rollup(fr2, "day", dt.date(2026, 11, 2), dt.date(2026, 11, 2), THR)[0].values
    assert v2["meta_impressions"] is None and v2["meta_cpm"] is None


def test_projection_uses_days_in_the_month_not_days_with_data():
    running = Period("month", "2026-11", dt.date(2026, 11, 1), dt.date(2026, 11, 30), THR, 2, 30, True)
    assert project(100.0, running) == 1500.0                                              # the sheet returned 100 here
    closed = Period("month", "2026-10", dt.date(2026, 10, 1), dt.date(2026, 10, 31), dt.date(2026, 10, 31), 31, 31, False)
    assert project(123.0, closed) == 123.0
    assert project(None, running) is None
    assert project(50.0, Period("month", "x", dt.date(2026, 11, 1), dt.date(2026, 11, 30), THR, 0, 30, True)) == 50.0    # no days: nothing to scale


def test_ebitda_estimate_uses_plan_rates_and_spreads_fixed_costs(conn, book):
    fr = feeds.load_frame(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    apply_costs(fr, book, dt.date(2026, 11, 1))
    b = fr.days[dt.date(2026, 11, 1)]
    assert b["e_target_days"] == 1.0
    assert math.isclose(b["e_noi_target"], book.val(100, dt.date(2026, 11, 1)) / 30)
    assert math.isclose(b["e_fixed_opex"], book.fixed_per_day(dt.date(2026, 11, 1), ("mkt_fixed", "headcount", "ovh_fixed")))


def test_apply_costs_without_a_plan_leaves_estimates_empty(conn):
    fr = feeds.load_frame(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    apply_costs(fr, None, dt.date(2026, 11, 1))
    v = rollup.rollup(fr, "day", dt.date(2026, 11, 1), dt.date(2026, 11, 1), THR)[0].values
    assert v["noi_target"] == 0 and v["ebitda_target"] is None and v["noi_gap"] is None
