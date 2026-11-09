"""DTC NC AOV (new-customer average order value) row in the REVENUE section of the month pacing table, right after DTC NC CPA.

NC AOV = new-customer revenue / new customers, month to date. Goal = the plan's new-customer revenue / the plan's new customers. Higher is better."""
import datetime as dt
import json

import pytest

from app.projection.pacing import feeds, month as M, periods
from app.projection.pacing.model import SHEET_ROWS
from pacing_support import SNAPSHOT_DAY
from test_pacing_fixwave import nov

BASE = "/api/projection/pacing"


def _row(res, key="dtc_nc_aov"):
    return {r["key"]: r for r in res["rows"]}[key]


def _mtd_sums(conn):
    r = conn.execute("SELECT COALESCE(SUM(nc_rev), 0) AS rev, COALESCE(SUM(nc_orders), 0) AS n FROM proj_feed_orders WHERE day BETWEEN %s AND %s",
                        (dt.date(2026, 11, 1), dt.date(2026, 11, 2))).fetchone()
    return float(r["rev"]), float(r["n"])


def test_row_sits_in_revenue_right_after_dtc_nc_cpa_with_the_up_direction(conn, pnl, cohorts):
    res, _ = nov(conn, pnl, cohorts)
    keys = [r["key"] for r in res["rows"]]
    assert keys[keys.index("nc_cpa") + 1] == "dtc_nc_aov"
    r = _row(res)
    assert (r["label"], r["group"], r["direction"], r["fmt"], r["indent"]) == ("DTC NC AOV", "REVENUE", "up", "money2", 1)
    assert "plan's AOV" in r["note"] and "new customers" in r["note"]


def test_actual_projection_goal_and_pace_match_a_plain_python_oracle(conn, pnl, cohorts):
    res, book = nov(conn, pnl, cohorts)
    r, cpa = _row(res), _row(res, "nc_cpa")
    rev, n = _mtd_sums(conn)
    assert n > 0 and r["mtd"] == pytest.approx(rev / n)
    assert r["projection"] == pytest.approx(rev / n)                                  # a ratio carries its month-to-date value to month end
    nc_rev_goal, orders_goal = book.goal("nc_rev", dt.date(2026, 11, 1)), book.goal("nc_orders", dt.date(2026, 11, 1))
    assert r["goal"] == pytest.approx(nc_rev_goal / orders_goal)
    assert r["pacing"] == pytest.approx((rev / n) / r["goal"])
    assert r["should_be"] == pytest.approx(r["goal"])
    assert r["last7"] is not None and cpa["mtd"] != r["mtd"]
    assert r["mtd"] == pytest.approx(_row(res, "nc_aov")["mtd"])                       # the same figure as the NEW CUSTOMERS (DTC) block's NC AOV


def test_the_goal_is_the_plans_aov_when_the_drivers_have_one(conn, pnl, cohorts):
    from types import SimpleNamespace
    m = dt.date(2026, 11, 1)
    drivers = SimpleNamespace(nc_rev={m: 72.0 * 1000}, nc_orders={m: 1000.0}, nc_aov={m: 72.0})
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    from app.projection.pacing.targets import TargetBook
    from snapshot_pnl import ROW_MAP
    book = TargetBook(pnl, drivers, cohorts, row_map=ROW_MAP)
    res = M.pacing_month(fr, m, SNAPSHOT_DAY, book, cohorts, {}, None, None)
    g = _row(res)["goal"]
    assert g == pytest.approx(book.goal("nc_rev", m) / book.goal("nc_orders", m))


@pytest.mark.parametrize("pace,status", [(1.20, "good"), (1.00, "good"), (0.95, "warn"), (0.80, "bad")])
def test_polarity_is_higher_is_better_not_the_cpa_wording(pace, status):
    assert M._status(pace, "up") == status                                              # same rule as ROAS and revenue
    assert M._status(1.20, "down") == "bad" and M._status(0.80, "down") == "good"       # a cost reads the other way: the same pace flips


def test_zero_new_customers_shows_a_dash_not_a_number_or_an_error():
    c = M.Ctx(dt.date(2026, 11, 1), None, {}, {}, 2, 30, 2 / 30)
    row = M.ratio_row(c, M.Spec("dtc_nc_aov", "DTC NC AOV", "REVENUE", "money2", "up", 1, "ratio"), M.Amt(0.0, 0.0, 7200.0), M.Amt(0.0, 0.0, 100.0))
    assert row["mtd"] is None and row["last7"] is None and row["projection"] is None and row["pacing"] is None and row["status"] == "none"
    assert row["goal"] == pytest.approx(72.0)


def test_partial_month_ratio_uses_only_the_days_with_data(conn, pnl, cohorts):
    res, _ = nov(conn, pnl, cohorts)
    assert res["days_elapsed"] == 2 and res["early"] is True
    rev, n = _mtd_sums(conn)
    assert _row(res)["mtd"] == pytest.approx(rev / n)                                  # not rev / (new customers over the whole month)


def test_28_day_figure_is_the_spend_plan_pages_aov(conn, pnl, cohorts):
    from app.projection.drivers import model as dm, reference as dref
    ref = dref.load_reference(conn, dm.parse_levers({}).ad_channels, 28, SNAPSHOT_DAY, dm.parse_levers({}))
    res, _ = nov(conn, pnl, cohorts)
    r = _row(res)
    c = r["context"]
    assert c["label"] == "Last 28 days" and c["value"] == pytest.approx(ref.aov_window["aov"]) and c["days"] >= 26
    assert c["pacing"] == pytest.approx(c["value"] / r["goal"]) and c["status"] in ("good", "warn", "bad")
    assert "context" not in _row(res, "nc_aov") and "context" not in _row(res, "nc_roas")


def test_28_day_figure_is_withheld_with_the_reason_and_never_shown_unguarded(conn, pnl, cohorts):
    res, _ = nov(conn, pnl, cohorts, {"drivers": {"reference_aov_min": 5, "reference_aov_max": 20}})
    c = _row(res)["context"]
    assert c["value"] is None and c["unavailable"].startswith("Realized AOV $") and "is outside the $5 to $20" in c["unavailable"]
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    from app.projection.pacing.targets import TargetBook
    from snapshot_pnl import ROW_MAP
    unread = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, TargetBook(pnl, None, cohorts, row_map=ROW_MAP), cohorts, {}, None, None)
    assert "context" not in _row(unread)


def test_the_row_is_app_only_and_does_not_disturb_the_sheet_layout():
    assert "dtc_nc_aov" not in SHEET_ROWS                                               # the sheet's Automated Pacing REVENUE block has no NC AOV
    assert SHEET_ROWS["nc_aov"] == 42 and SHEET_ROWS["nc_cpa"] == 9 and SHEET_ROWS["amazon_rev"] == 10


def test_month_endpoint_carries_the_row(api):
    j = api.get(f"{BASE}/month").get_json()
    keys = [x["key"] for x in j["rows"]]
    assert keys[keys.index("nc_cpa") + 1] == "dtc_nc_aov"
    r = {x["key"]: x for x in j["rows"]}["dtc_nc_aov"]
    assert r["direction"] == "up" and r["mtd"] and r["goal"] and r["pacing"] == pytest.approx(r["projection"] / r["goal"])
    assert r["status"] in ("good", "warn", "bad") and r["context"]["label"] == "Last 28 days"
    json.dumps(r)
