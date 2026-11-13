"""The breakdown step's arithmetic on synthetic data: the mix / within-segment split is exact (also with new and vanished segments), and the noise labelling says 'inside noise'
for a wiggle in the segment's normal range and 'moved beyond noise' only when both controls agree. Pure functions: no database."""
from __future__ import annotations

import datetime as dt
import math
import random

import pytest

from brain import why_breakdowns as B

D = dt.date


def _check_exact(segs, scale=1.0):
    ss = B.shift_share(segs, scale)
    assert ss is not None
    assert math.isclose(ss["mix_total"] + ss["rate_total"], ss["delta"], rel_tol=1e-9, abs_tol=1e-12)
    assert math.isclose(sum(r["contribution"] for r in ss["segments"].values()), ss["delta"], rel_tol=1e-9, abs_tol=1e-12)
    return ss


def test_shift_share_is_exact_for_two_segments():
    # CPM: segment a cheap (spend 100 / 10k impressions), b dear; share moves to b and a's rate also rises
    segs = {"a": (100.0, 10_000.0, 150.0, 12_000.0), "b": (300.0, 10_000.0, 700.0, 20_000.0)}
    ss = _check_exact(segs, 1000.0)
    assert ss["R0"] == pytest.approx(400 / 20_000 * 1000) and ss["R1"] == pytest.approx(850 / 32_000 * 1000)
    a, b = ss["segments"]["a"], ss["segments"]["b"]
    assert b["w1"] > b["w0"] > a["w1"] and b["mix"] > 0 and a["mix"] > 0     # share moved from the cheap segment to the dear one: both halves of that move raise the average CPM
    assert a["rate"] > 0 and b["rate"] > 0                              # both segments' own CPM rose


def test_pure_mix_has_no_within_segment_effect_and_pure_rate_has_no_mix():
    mix_only = {"a": (10.0, 100.0, 40.0, 400.0), "b": (60.0, 100.0, 60.0, 100.0)}      # a keeps rate 0.1, b keeps 0.6; only the weights move
    ss = _check_exact(mix_only)
    assert ss["rate_total"] == pytest.approx(0.0, abs=1e-12) and ss["mix_total"] == pytest.approx(ss["delta"])
    rate_only = {"a": (10.0, 100.0, 20.0, 100.0), "b": (60.0, 100.0, 90.0, 100.0)}     # equal weights both windows; both rates rise
    ss = _check_exact(rate_only)
    assert ss["mix_total"] == pytest.approx(0.0, abs=1e-12) and ss["rate_total"] == pytest.approx(ss["delta"])


def test_new_and_vanished_segments_stay_exact():
    segs = {"old": (50.0, 500.0, 0.0, 0.0), "both": (100.0, 1000.0, 150.0, 1000.0), "new": (0.0, 0.0, 90.0, 300.0)}
    ss = _check_exact(segs)
    assert ss["segments"]["old"]["gone"] and ss["segments"]["new"]["new"]
    assert ss["segments"]["old"]["rate"] == 0 and ss["segments"]["new"]["rate"] == 0       # no like-for-like ratio on one side: the whole contribution is mix
    assert ss["segments"]["old"]["w1"] == 0 and ss["segments"]["new"]["w0"] == 0


def test_segment_with_no_denominator_either_window_is_ignored_and_empty_window_is_none():
    ss = _check_exact({"x": (0.0, 0.0, 0.0, 0.0), "y": (1.0, 10.0, 2.0, 10.0)})
    assert "x" not in ss["segments"]
    assert B.shift_share({"x": (1.0, 10.0, 0.0, 0.0)}) is None


def test_exactness_on_random_data_with_zero_cells():
    rnd = random.Random(7)
    for _ in range(200):
        segs = {}
        for i in range(rnd.randint(1, 9)):
            d0 = rnd.choice([0, 0, rnd.uniform(1, 1e5)])
            d1 = rnd.choice([0, rnd.uniform(1, 1e5), rnd.uniform(1, 1e5)])
            segs[f"s{i}"] = (d0 * rnd.uniform(0, 1), d0, d1 * rnd.uniform(0, 1), d1)
        if sum(v[1] for v in segs.values()) <= 0 or sum(v[3] for v in segs.values()) <= 0:
            continue
        _check_exact(segs, rnd.choice([1.0, 1000.0]))


# ---------------------------------------------------------------------------------------------------------- noise labelling
def _series(rate_fn, start=D(2026, 10, 1), n=45, den=1000.0):
    num, dd = {}, {}
    for i in range(n):
        d = start + dt.timedelta(days=i)
        dd[d] = den
        num[d] = den * rate_fn(i, d)
    return num, dd


def test_a_wiggle_inside_the_normal_range_is_labelled_noise():
    # daily rate alternates 0.020 / 0.024 / 0.016 (sd ~0.004); the one-day window sits at 0.025 against a 0.020 comparison
    pat = [0.020, 0.024, 0.016, 0.021, 0.019, 0.023, 0.017]
    num, den = _series(lambda i, d: pat[i % 7])
    day = D(2026, 11, 7)
    num[day] = 1000 * 0.025
    c = B.controls(num, den, day, day)
    v, z = B.verdict(c["cur"], 0.020, c)
    assert v == "inside noise" and c["daily_sd"] > 0.002 and c["noise_days"] == 28


def test_a_step_beyond_both_controls_is_flagged_and_one_control_is_not_enough():
    num, den = _series(lambda i, d: 0.020 + 0.002 * math.sin(i * 1.3))
    day = D(2026, 11, 7)
    num[day] = 1000 * 0.040                                              # double the normal rate on the target day
    c = B.controls(num, den, day, day)
    v, z = B.verdict(c["cur"], 0.020, c)
    assert v == "moved beyond noise" and z["z_weekday"] >= 2 and z["z_prior7"] >= 2
    # the prior 7 days already sat at the new level: the move is not new against that control
    num2, den2 = _series(lambda i, d: 0.020 + 0.002 * math.sin(i * 1.3))
    for k in range(1, 8):
        num2[day - dt.timedelta(days=k)] = 1000 * 0.040
    num2[day] = 1000 * 0.040
    c2 = B.controls(num2, den2, day, day)
    v2, _ = B.verdict(c2["cur"], 0.020, c2)
    assert v2 == "inside noise"


def test_same_weekday_median_is_the_four_prior_same_weekdays_for_a_one_day_window():
    num, den = _series(lambda i, d: 0.01 * (1 + d.weekday()))            # Monday 0.01 ... Sunday 0.07
    day = D(2026, 11, 9)                                                 # a Monday
    c = B.controls(num, den, day, day)
    assert c["weekday_windows"] == 4 and c["weekday_median"] == pytest.approx(0.01)
    assert c["prior7"] == pytest.approx(0.04)                            # mean of 0.01 .. 0.07, equal weights


def test_not_enough_history_is_said_not_guessed():
    num, den = _series(lambda i, d: 0.02, start=D(2026, 11, 1), n=8)
    day = D(2026, 11, 8)
    c = B.controls(num, den, day, day)
    assert B.verdict(c["cur"], 0.02, c)[0] == "not enough history"


def test_funnel_days_that_are_not_filled_are_excluded_not_zero():
    num, den = _series(lambda i, d: 0.1)
    bad = frozenset({D(2026, 11, 6)})
    c = B.controls(num, den, D(2026, 11, 6), D(2026, 11, 7), 1.0, bad)
    assert c["cur"] is None                                              # the window itself contains an unfilled day
    c = B.controls(num, den, D(2026, 11, 8), D(2026, 11, 8), 1.0, bad)
    assert c["cur"] == pytest.approx(0.1) and c["noise_days"] == 27      # the unfilled day drops out of the noise series


# ---------------------------------------------------------------------------------------------------------- a whole dimension
def _rows(rate_a, rate_b, share_b, start=D(2026, 10, 10), end=D(2026, 11, 12), step_day=None, step=None):
    rows = []
    d = start
    while d <= end:
        i = (d - start).days
        stepped = step_day is not None and d >= step_day
        wb = step[0] if stepped else share_b
        ra = step[1] if stepped else rate_a
        for seg, w, r in (("reels", wb, rate_b), ("feed", 1 - wb, ra)):
            imps = 100_000 * w
            noise = 1 + 0.03 * math.sin(i * 2.1 + (1 if seg == "feed" else 0))
            rows.append({"day": d, "seg": seg, "spend": imps * 0.05, "impressions": imps, "link_clicks": imps * r * noise, "atc": imps * r * 0.1 * noise, "ic": imps * r * 0.05 * noise,
                         "purchases": imps * r * 0.02 * noise, "funnel_ok": True})
        d += dt.timedelta(days=1)
    return rows


def test_analyze_dimension_flags_a_real_segment_step_and_stays_quiet_otherwise():
    quiet = B.analyze_dimension("platform_position", _rows(0.02, 0.01, 0.4), D(2026, 11, 12), D(2026, 11, 12), D(2026, 11, 11), D(2026, 11, 11))
    assert all(x["verdict"] != "moved beyond noise" for mb in quiet["metrics"].values() for x in mb["movers"])
    stepped = B.analyze_dimension("platform_position", _rows(0.02, 0.01, 0.4, step_day=D(2026, 11, 12), step=(0.7, 0.012)), D(2026, 11, 12), D(2026, 11, 12), D(2026, 11, 11), D(2026, 11, 11))
    ctr = stepped["metrics"]["ctr"]
    assert abs(ctr["exact_check"]) < 1e-9 and ctr["mix_total"] != 0
    flagged = [x for x in ctr["movers"] if x["verdict"] == "moved beyond noise"]
    assert flagged and flagged[0]["segment"] in ("reels", "feed")
    assert ctr["aggregate"]["verdict"] == "moved beyond noise"
    for mb in stepped["metrics"].values():
        assert abs(mb["exact_check"]) < 1e-9 * max(1.0, abs(mb["R0"] if "R0" in mb else mb["cmp"]))      # mix + within = aggregate move for every metric


def test_unfilled_funnel_skips_funnel_metrics_and_says_so():
    rows = _rows(0.02, 0.01, 0.4)
    for r in rows:
        if r["day"] == D(2026, 11, 12):
            r["funnel_ok"] = False
    res = B.analyze_dimension("age", rows, D(2026, 11, 12), D(2026, 11, 12), D(2026, 11, 11), D(2026, 11, 11))
    assert "cpm" in res["metrics"] and "link_to_atc" not in res["metrics"]
    assert any("not filled" in g for g in res["gaps"])


def test_headline_says_plainly_when_nothing_beats_noise():
    res = B.analyze_dimension("platform_position", _rows(0.02, 0.01, 0.4), D(2026, 11, 12), D(2026, 11, 12), D(2026, 11, 11), D(2026, 11, 11))
    ranked = B.rank_movers([res])
    assert ranked == [] or all(x["verdict"] != "moved beyond noise" for x in ranked)
    assert "No single breakdown segment moved beyond its normal day-to-day noise" in B.headline([res], ranked, "w")


def test_a_long_window_uses_adjacent_windows_and_skips_days_before_the_data_starts():
    num, den = _series(lambda i, d: 0.02 + 0.001 * math.sin(i), start=D(2026, 10, 12), n=33)       # data from Oct 12 through Nov 13
    avail = frozenset(den)
    start, end = D(2026, 11, 4), D(2026, 11, 11)                                                  # an 8-day window
    c = B.controls(num, den, start, end, 1.0, frozenset(), avail)
    assert c["weekday_windows"] == 2 and c["weekday_median"] is not None                         # Oct 27-Nov 3 and Oct 19-26; Oct 11-18 starts before the data and is skipped
    assert c["noise_days"] == 23 and c["days"] == 8
    only_one = B.controls(num, den, D(2026, 11, 12), D(2026, 11, 12), 1.0, frozenset(), frozenset(d for d in den if d >= D(2026, 11, 1)))
    assert only_one["weekday_median"] is None and B.verdict(only_one["cur"], 0.02, only_one)[0] == "not enough history"
