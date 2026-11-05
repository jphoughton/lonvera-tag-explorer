"""The 'NC revenue today' curve and projection (app/projection/pacing/nc_curve.py): ratio-of-sums shares by Pacific hour, interpolation inside the hour,
today and out-of-window days excluded, the 23- and 25-hour days, thin data, the weekday check and the stale flag. Pure functions: no database."""
import datetime as dt

import pytest

from app.projection.pacing import nc_curve as C

D = dt.date
TODAY = D(2026, 11, 5)                                  # Thursday; the window is Oct 7 .. Nov 4


def day_rows(day, hours: dict, orders=1):
    return [(day, h, rev, orders) for h, rev in hours.items()]


def window(hours: dict, days=30, today=TODAY, orders=1):
    first, last = C.window_dates(today, days)
    out = []
    for i in range(days):
        out += day_rows(first + dt.timedelta(days=i), hours, orders)
    assert last == today - dt.timedelta(days=1)
    return out


def test_window_is_the_30_closed_days_ending_yesterday():
    assert C.window_dates(TODAY) == (D(2026, 10, 6), D(2026, 11, 4))


def test_share_is_a_ratio_of_sums_and_cumulative_ends_at_one():
    rows = window({8: 100.0, 12: 100.0, 20: 200.0})                     # 25% / 25% / 50% of every day
    cur = C.build(rows, TODAY, 12.0)
    assert cur["loaded"] and cur["window"]["days_used"] == 30
    shares = {c["hour"]: c["share"] for c in cur["curve"]}
    assert shares[8] == pytest.approx(0.25) and shares[20] == pytest.approx(0.5) and shares[3] == 0.0
    assert cur["curve"][23]["cum_end"] == pytest.approx(1.0)
    assert cur["share_of_day"] == pytest.approx(0.25)                    # hours 0-11 hold only hour 8: 25%
    cum = [c["cum_end"] for c in cur["curve"]]
    assert cum == sorted(cum)                                            # monotone, including across empty hours


def test_ratio_of_sums_differs_from_the_average_of_daily_ratios_on_uneven_days():
    first, last = C.window_dates(TODAY)
    rows = []
    for i in range(30):
        d = first + dt.timedelta(days=i)
        big = i == 0
        rows += [(d, 6, 900.0 if big else 10.0, 1), (d, 18, 100.0 if big else 90.0, 1)]   # a big early day among small late-skewed ones
    cur = C.build(rows, TODAY, 12.0)
    ros = 900 + 29 * 10
    ros_share = ros / (1000 + 29 * 100)
    assert cur["share_of_day"] == pytest.approx(ros_share)
    assert cur["mean_of_ratios_share"] == pytest.approx((0.9 + 29 * 0.1) / 30)
    assert cur["share_of_day"] != pytest.approx(cur["mean_of_ratios_share"], abs=0.01)


def test_interpolation_inside_the_hour_is_linear():
    rows = window({7: 50.0, 8: 100.0, 9: 50.0, 15: 100.0})                 # total 300: cum by 08:00 = 1/6, hour 8 holds 1/3
    at = lambda h: C.build(rows, TODAY, h, 1000.0)["share_of_day"]        # noqa: E731
    assert at(8.0) == pytest.approx(50 / 300)
    assert at(8.5) == pytest.approx((50 + 50) / 300)
    assert at(8.8) == pytest.approx((50 + 80) / 300)
    out = C.build(rows, TODAY, 8.8, 130.0)
    assert out["projected_eod"] == pytest.approx(130 / (130 / 300)) and out["to_go"] == pytest.approx(out["projected_eod"] - 130)


def test_hour_zero_hour_23_and_the_ends_of_the_day():
    rows = window({0: 20.0, 12: 60.0, 23: 20.0})
    assert C.build(rows, TODAY, 0.0, 10.0)["projected_eod"] is None                    # midnight: nothing is in yet
    assert "Too early" in C.build(rows, TODAY, 0.0, 10.0)["reason"]
    half_h0 = C.build(rows, TODAY, 0.5, 10.0)
    assert half_h0["share_of_day"] == pytest.approx(0.10) and half_h0["projected_eod"] == pytest.approx(100.0)
    h23 = C.build(rows, TODAY, 23.5, 80.0)
    assert h23["share_of_day"] == pytest.approx(0.90) and h23["projected_eod"] == pytest.approx(80 / 0.9)
    end = C.build(rows, TODAY, 24.0, 100.0)
    assert end["share_of_day"] == pytest.approx(1.0) and end["projected_eod"] == pytest.approx(100.0) and end["to_go"] == pytest.approx(0.0)
    assert C.share_at([0.0] * 25, [0.0] * 24, 99) == 0.0                              # clamped, not an index error


def test_zero_and_low_volume_hours_before_the_curve_starts():
    rows = window({9: 3.0, 10: 97.0})                                                # nothing before 09:00
    early = C.build(rows, TODAY, 8.0, 5.0)
    assert early["projected_eod"] is None and early["share_of_day"] == 0.0
    low = C.build(rows, TODAY, 9.1, 1.0)
    assert low["low_share"] is True and low["projected_eod"] == pytest.approx(1.0 / (0.03 * 0.1))   # swings: flagged, not hidden
    assert C.build(rows, TODAY, 12.0, 100.0)["low_share"] is False


def test_today_and_days_outside_the_window_never_enter_the_curve():
    rows = window({8: 100.0, 20: 100.0})
    base = C.build(rows, TODAY, 12.0, 100.0)
    noisy = rows + day_rows(TODAY, {1: 1e6}) + day_rows(D(2026, 10, 5), {2: 1e6}) + day_rows(TODAY + dt.timedelta(days=1), {3: 1e6})
    out = C.build(noisy, TODAY, 12.0, 100.0)
    assert out["share_of_day"] == base["share_of_day"] and out["window"]["total_nc_rev"] == base["window"]["total_nc_rev"]
    assert out["window"]["days_used"] == 30 and "2026-11-05" not in out["window"]["dates_used"]
    assert out["stale"]["newest_closed_day"] == "2026-11-04"


def test_dst_23_and_25_hour_days_keep_the_curve_in_wall_clock_hours():
    # 2026-03-08 had 23 hours (hour 2 does not exist); 2026-11-01 had 25 (the feed merges both 1 a.m. hours into hour 1)
    today = D(2026, 11, 2)
    rows = []
    for i in range(30):
        d = today - dt.timedelta(days=30 - i)
        hours = {0: 10.0, 1: 10.0, 2: 10.0, 3: 10.0}
        if d == D(2026, 11, 1):
            hours[1] = 20.0                                                         # two real hours in the merged bucket
        rows += day_rows(d, hours)
    cur = C.build(rows, today, 12.0, None)
    assert D(2026, 11, 1).isoformat() in cur["window"]["dates_used"]
    assert sum(c["share"] for c in cur["curve"]) == pytest.approx(1.0)
    assert cur["curve"][1]["share"] == pytest.approx((29 * 10 + 20) / (30 * 40 + 10))
    spring = [(D(2026, 3, 8), h, 10.0, 1) for h in (0, 1, 3, 4)] * 1                 # no hour 2 on the 23-hour day
    t2 = C.build(spring + [(D(2026, 3, 9) - dt.timedelta(days=i), 2, 10.0, 1) for i in range(2, 5)], D(2026, 3, 9), 6.0, None)
    assert t2["curve"][2]["share"] > 0 and sum(c["share"] for c in t2["curve"]) == pytest.approx(1.0)


def test_feed_rows_with_a_bad_hour_are_an_error_not_a_silent_skip():
    with pytest.raises(ValueError):
        C.build([(D(2026, 11, 3), 24, 1.0, 1)], TODAY, 12.0)


def test_no_data_and_too_few_days_give_no_projection_and_never_an_even_day():
    empty = C.build([], TODAY, 12.0, 100.0)
    assert empty["loaded"] is False and empty["projected_eod"] is None and empty["curve"] == [] and empty["stale"]["is_stale"] is True
    thin = C.build(day_rows(D(2026, 11, 4), {8: 50.0}) + day_rows(D(2026, 11, 3), {8: 50.0}), TODAY, 12.0, 100.0)
    assert thin["loaded"] is False and thin["projected_eod"] is None and "at least 3" in thin["reason"]


def test_without_spend_the_share_alone_is_returned():
    out = C.build(window({8: 100.0, 20: 100.0}), TODAY, 12.0)
    assert out["spent"] is None and out["projected_eod"] is None and out["share_of_day"] == pytest.approx(0.5)


def test_stale_flag_follows_the_newest_closed_day_against_yesterday():
    def newest(days_before_yesterday):
        last = D(2026, 11, 4) - dt.timedelta(days=days_before_yesterday)
        return [(last - dt.timedelta(days=i), 8, 10.0, 1) for i in range(10)]
    assert C.build(newest(0), TODAY, 12.0)["stale"] == {"newest_closed_day": "2026-11-04", "age_days": 0, "stale_after_days": 2, "is_stale": False}
    assert C.build(newest(2), TODAY, 12.0)["stale"]["is_stale"] is False
    s = C.build(newest(3), TODAY, 12.0)["stale"]
    assert s["is_stale"] is True and s["age_days"] == 3


def test_weekday_check_and_same_weekday_curve():
    first, _ = C.window_dates(TODAY)
    rows = []
    for i in range(30):
        d = first + dt.timedelta(days=i)
        rows += day_rows(d, {6: 50.0, 18: 50.0} if d.weekday() >= 5 else {6: 20.0, 18: 80.0})      # weekends front-loaded
    out = C.build(rows, TODAY, 12.0, 100.0)
    chk = out["dow_check"]
    assert chk["weekday_share"] == pytest.approx(0.2) and chk["weekend_share"] == pytest.approx(0.5)
    assert chk["gap_points"] == pytest.approx(-0.3) and chk["differs"] is True
    assert chk["weekday_days"] + chk["weekend_days"] == 30 and chk["same_weekday_days"] in (4, 5)
    same = C.build(rows, TODAY, 12.0, 100.0, dow="same")                                            # today is a Thursday: weekday shape only
    assert same["share_of_day"] == pytest.approx(0.2) and same["window"]["days_used"] == chk["same_weekday_days"] and same["weekday"] == "Thursday"
    flat = C.build(window({8: 100.0, 20: 100.0}), TODAY, 12.0)
    assert flat["dow_check"]["differs"] is False and abs(flat["dow_check"]["gap_points"]) < 1e-9


def test_a_missing_day_inside_the_range_is_listed_and_does_not_change_the_ratio():
    rows = window({8: 100.0, 20: 100.0})
    gap = D(2026, 10, 20)
    rows = [r for r in rows if r[0] != gap]
    out = C.build(rows, TODAY, 12.0, 100.0)
    assert out["window"]["missing_days"] == ["2026-10-20"] and out["window"]["days_used"] == 29 and out["share_of_day"] == pytest.approx(0.5)
