"""Month pacing against the sheet's Automated Pacing tab at the snapshot date, plus edge cases, status colors, snapshot, payback and the intraday projection."""
import datetime as dt
import math

import pytest

from app.projection.pacing import feeds, month as M, periods
from app.projection.pacing.model import ROW0, ROW1, automated_columns, SHEET_ROWS
from app.projection.pacing.targets import TargetBook
from pacing_support import SNAPSHOT_DAY
import snapshot_read as sr

COLS = "BCDEFGH"
# Rows whose sheet value depends on a partial day of Amazon (the sheet's company-wide block includes today's row, the rest of its table excludes it)
COMPANY = {46, 47, 48, 49, 50}


@pytest.fixture()
def nov(conn, book, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    return M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts, {}), fr


def test_every_cell_of_automated_pacing_matches_the_sheet(nov):
    summary, _ = nov
    mine = automated_columns(summary)
    checked, bad = 0, []
    for r in range(ROW0, ROW1 + 1):
        if r in COMPANY:
            continue
        for c in COLS:
            sv = sr.cell("Automated Pacing", f"{c}{r}")
            mv = mine[c][r - ROW0]
            if sv is None or isinstance(sv, str):
                continue
            if mv is None:
                bad.append((f"{c}{r}", sv, None))
                continue
            d, den = abs(mv - sv), max(abs(mv), abs(sv))
            if d > 1 and d / den > 0.005:
                bad.append((f"{c}{r}", sv, mv))
            checked += 1
    assert checked > 230 and not bad, bad


def test_company_wide_block_excludes_todays_partial_amazon_row(nov):
    by = {r["key"]: r for r in nov[0]["rows"]}
    # the sheet's C46 / C47 add Amazon's partial row for the 3rd (122.97 / 3); the app counts complete days only
    assert math.isclose(by["co_nc_rev"]["mtd"], sr.cell("Automated Pacing", "C46") - 122.97, abs_tol=2.0)
    assert by["co_nc_count"]["mtd"] == sr.cell("Automated Pacing", "C47") - 3


def test_calendar_figures(nov):
    s = nov[0]
    assert (s["days_elapsed"], s["days_in_month"], s["days_remaining"]) == (2, 30, 28)
    assert math.isclose(s["share_elapsed"], 2 / 30) and s["early"] is True
    assert s["data_through"] == "2026-11-02" and s["month"] == "2026-11-01"


def test_projection_is_mtd_over_days_times_days_in_month(nov):
    by = {r["key"]: r for r in nov[0]["rows"]}
    assert math.isclose(by["total_rev"]["projection"], by["total_rev"]["mtd"] / 2 * 30)
    assert math.isclose(by["total_rev"]["pacing"], by["total_rev"]["projection"] / by["total_rev"]["goal"])
    assert math.isclose(by["total_rev"]["should_be"], by["total_rev"]["goal"] * 2 / 30)
    assert math.isclose(by["total_rev"]["daily_needed"], (by["total_rev"]["goal"] - by["total_rev"]["mtd"]) / 28)
    # a ratio carries its MTD value and needs an incremental ratio, not a daily amount
    r = by["nc_roas"]
    assert r["projection"] == r["mtd"] and r["kind"] == "ratio"
    assert math.isclose(r["daily_needed"], (by["nc_rev"]["goal"] - by["nc_rev"]["mtd"]) / (by["dtc_ad_spend"]["goal"] - by["dtc_ad_spend"]["mtd"]))


def test_status_reads_the_direction_of_each_metric(nov):
    by = {r["key"]: r for r in nov[0]["rows"]}
    assert by["total_rev"]["status"] == "good"                      # revenue above goal
    assert by["amazon_rev"]["status"] == "bad"                      # 88% of goal
    assert by["nc_roas"]["status"] == "warn" and by["nc_roas"]["direction"] == "up"
    assert by["advertising"]["status"] == "bad" and by["advertising"]["direction"] == "down"     # 111% of the ad budget
    assert by["nc_cpa"]["status"] == "bad" and by["nc_cpa"]["direction"] == "down"
    assert by["amazon_fees"]["status"] == "good"                    # below plan is good for a cost
    assert by["sales_tax"]["status"] == "none" and by["headcount"]["status"] == "none" and by["ad_tiktok"]["status"] == "none"


@pytest.mark.parametrize("p,direction,want", [(1.2, "up", "good"), (1.0, "up", "good"), (0.95, "up", "warn"), (0.9, "up", "warn"), (0.89, "up", "bad"),
                                              (0.8, "down", "good"), (1.0, "down", "good"), (1.05, "down", "warn"), (1.1, "down", "warn"), (1.11, "down", "bad"),
                                              (None, "up", "none"), (1.0, None, "none")])
def test_status_thresholds(p, direction, want):
    assert M._status(p, direction) == want


def test_goal_missing_or_zero_gives_no_pace():
    spec = M.Spec("x", "X", "G", "money", "up")
    assert M._row(spec, 1, 1, None, 5, 1, 1)["pacing"] is None
    assert M._row(spec, 1, 1, 0, 5, 1, 1)["pacing"] is None
    assert M._row(spec, 1, 1, -5, 5, 1, 1)["pacing"] is None             # a negative goal is not a pace (only no-status memo rows show it)
    memo = M.Spec("t", "Tax", "G", "money", None)
    assert M._row(memo, -1, -1, -5, -10, 1, 1)["pacing"] == 2.0


def test_first_of_the_month_shows_the_month_that_just_ended(conn, book, cohorts):
    today = dt.date(2026, 11, 1)
    month = periods.default_month(today)
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    s = M.pacing_month(fr, month, today, book, cohorts)
    assert month == dt.date(2026, 10, 1) and s["days_elapsed"] == 31 and s["early"] is False
    by = {r["key"]: r for r in s["rows"]}
    assert by["total_rev"]["projection"] == pytest.approx(by["total_rev"]["mtd"])          # a closed month is its own projection
    assert by["total_rev"]["daily_needed"] is None                                          # no days remaining


def test_future_month_has_no_rows(conn, book, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    s = M.pacing_month(fr, dt.date(2026, 12, 1), SNAPSHOT_DAY, book, cohorts)
    assert s["rows"] == [] and s["days_elapsed"] == 0 and s["tldr"] == [] and s["payback"] is None


def test_empty_feeds_give_zero_actuals_without_errors(clean_conn, book, cohorts):
    for t in ("proj_feed_orders", "proj_feed_amazon", "proj_feed_platform", "proj_feed_meta_day", "proj_feed_meta_product"):
        clean_conn.execute(f"DELETE FROM {t}")
    fr = feeds.load_frame(clean_conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    s = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts)
    by = {r["key"]: r for r in s["rows"]}
    assert by["total_rev"]["mtd"] == 0 and by["total_rev"]["projection"] == 0 and by["nc_cpa"]["mtd"] is None
    clean_conn.rollback()


def test_missing_plan_lines_are_reported_not_hidden(conn, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    empty = TargetBook(None, None, None, row_map={})
    s = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, empty, cohorts)
    assert 22 in s["missing_goal_rows"] and 100 in s["missing_goal_rows"]
    assert {r["key"]: r for r in s["rows"]}["total_rev"]["pacing"] is None


def test_tldr_and_sentence(nov):
    tl = nov[0]["tldr"]
    assert [t["key"] for t in tl] == ["total_rev", "noi", "nc_roas", "advertising", "nc_cpa"]
    line = M.tldr_sentence(tl)
    assert line.startswith("Revenue tracking 10") and line.endswith("of goal")


def test_payback_matches_the_sheet_and_the_function_edges(nov):
    pb = nov[0]["payback"]
    assert pb["months"]["mtd"] == sr.cell("Automated Pacing", "M9") == 10 and pb["months"]["goal"] == sr.cell("Automated Pacing", "N9") == 5
    assert math.isclose(pb["first_order_margin"]["mtd"], sr.cell("Automated Pacing", "M8"), rel_tol=1e-6)
    curve = [0.2, 0.1, 0.1, 0.1] + [0.0] * 32
    assert M.payback_months(100, 10, 0.2, 5, curve)["months"] == 0                     # the first order already pays for the customer
    assert M.payback_months(None, 10, 0.2, 5, curve)["label"] == "n/a"
    r = M.payback_months(100, 120, 0.2, 5, curve)                                       # cm = 75; cumulative 75 x (1 + .2, .3, .4, .5) = 90, 97.5, 105, 112.5
    assert r["first_order_margin"] == 75 and r["months"] is None and r["label"] == "36+"
    assert M.payback_months(100, 100, 0.2, 5, curve)["months"] == 3                     # 75 x 1.4 = 105 >= 100 in month 3
    assert M.payback_months(100, 90, 0.2, 5, curve)["months"] == 1


def test_payback_inputs_come_from_financing_params_when_set():
    assert M.payback_params({}) == (0.1726, 5.53, 36)                                    # the sheet's Payback!D11, B12 and the 36-age SCAN of Automated Pacing M9
    assert M.payback_params({"financing": {"payback": {"product_cost_pct": 0.2, "fulfillment": 6, "max_age": 24}}}) == (0.2, 6.0, 24)
    assert M.payback_params({"drivers": {"payback": {"product_cost_pct": 0.3}}}) == (0.3, 5.53, 36)   # the drivers module's copy is read when financing has none


def test_month_snapshot_uses_the_same_numbers_as_the_pacing_table(conn, book, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    snap = M.snapshot_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts, {})
    tab = {r["key"]: r for r in M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts)["rows"]}
    by = {r["key"]: r for r in snap["rows"]}
    assert snap["partial"] and snap["scale"] == 15 and snap["days_elapsed"] == 2
    assert by["noi"]["actual"] == tab["noi"]["mtd"] and by["noi"]["projected"] == tab["noi"]["projection"] and by["noi"]["target"] == tab["noi"]["goal"]
    assert by["nc_cpa"]["projected"] == by["nc_cpa"]["actual"]                           # a ratio does not scale
    assert by["dtc_rev"]["actual"] == pytest.approx(sr.cell("Month Snapshot", "B8"))
    assert by["dtc_rev"]["projected"] == pytest.approx(sr.cell("Month Snapshot", "C8"))
    assert by["nc_rev"]["target"] == pytest.approx(sr.cell("Month Snapshot", "D9"))
    assert snap["payback"]["months"]["actual"] == 10 and snap["payback"]["months"]["target"] == 5


def test_month_snapshot_for_a_closed_month(conn, book, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    snap = M.snapshot_month(fr, dt.date(2026, 10, 1), SNAPSHOT_DAY, book, cohorts, {})
    assert not snap["partial"] and snap["scale"] == 1.0 and snap["status_text"] == "Closed month"
    assert all(r["projected"] == r["actual"] for r in snap["rows"] if r["actual"] is not None)


def test_intraday_projection_follows_the_sheets_formula(conn):
    hourly = [0.0] * 24
    for r in conn.execute("SELECT hour, spend_30d FROM proj_pacing_hourly"):
        hourly[r["hour"]] = float(r["spend_30d"])
    h = sr.cell("Automated Pacing", "M16") * 24                                       # the time the sheet used when it was read
    out = M.intraday_projection(hourly, 7528, h)
    assert out["share"] == pytest.approx(sr.cell("Automated Pacing", "M17"), rel=1e-6)
    assert out["projected_eod"] == pytest.approx(sr.cell("Automated Pacing", "M18"), rel=1e-6)
    assert out["to_go"] == pytest.approx(sr.cell("Automated Pacing", "M19"), rel=1e-6)
    assert M.intraday_projection(hourly, 100, 24.0)["share"] == pytest.approx(1.0)
    assert M.intraday_projection(hourly, 100, 0.0)["projected_eod"] is None             # midnight: nothing to scale
    assert M.intraday_projection([0.0] * 24, 100, 12)["projected_eod"] is None
    assert M.intraday_projection([1.0] * 23, 100, 12)["share"] is None


def test_sheet_row_map_covers_every_table_row(nov):
    keys = {r["key"] for r in nov[0]["rows"]}
    assert set(SHEET_ROWS) - keys == set()
    assert len(set(SHEET_ROWS.values())) == len(SHEET_ROWS)
