"""Trends conversion band: ratios of the period's sums, dashes for zero denominators and for periods before the funnel data starts, week and month bucketing,
an uncovered day inside a period. Pure (no database): app.trends.assemble over a small made-up day frame."""
import datetime as dt

import pytest

from app import trends as T

D = dt.date
TODAY = D(2026, 11, 12)
LAST = D(2026, 11, 11)
FUNNEL_FIRST = D(2026, 10, 5)               # a Monday


def day_row(d):
    k = d.day
    return {"ms": 100.0, "gs": 10.0, "mi": 10000.0, "mc": 100.0 + k, "mp_own": 4.0 + k % 3, "onc_ord": 2.0 + k % 2, "onc_rev": 150.0, "otot": 200.0, "orc": 50.0,
            "lpv": 80.0 + k, "atc": 10.0 + k % 4, "ic": 6.0 + k % 3}


def build(first=FUNNEL_FIRST, holes=(), funnel_last=LAST):
    fr = {}
    for d in T.daterange(D(2026, 9, 1), LAST):
        row = day_row(d)
        if d < first or d in holes:
            for k in ("lpv", "atc", "ic"):
                row.pop(k)
        fr[d] = row
    cov = {k: T.Cov(D(2025, 1, 1), LAST) for k in T.SOURCE_LABELS}
    cov["funnel"] = T.Cov(first, funnel_last, {d for d in T.daterange(first, funnel_last) if d not in holes})
    return fr, cov


def payload(fr, cov, interval, n=4):
    opt = {"interval": interval, "periods": n, "numbers": "own", "as_of": None}
    through = T.through_of(cov, TODAY)
    return T.assemble(opt, TODAY, cov, through, T.build_periods(interval, TODAY, through, n), fr, {})


def S(fr, a, b):
    return T.sums(fr, a, b)


def test_week_ratios_are_ratios_of_the_weeks_sums_not_averages_of_daily_ratios():
    fr, cov = build()
    p = payload(fr, cov, "week")
    row = next(r for r in p["rows"] if r["key"] == "2026-10-26")                     # a closed week, Mon-Sun
    s = S(fr, D(2026, 10, 26), D(2026, 11, 1))
    v = row["v"]
    assert v["f_lpv"] == pytest.approx(s["lpv"] / s["mc"], abs=1e-6)
    assert v["f_lc_atc"] == pytest.approx(s["atc"] / s["mc"], abs=1e-6)
    assert v["f_atc_ic"] == pytest.approx(s["ic"] / s["atc"], abs=1e-6)
    assert v["f_ic_p"] == pytest.approx(s["mp_own"] / s["ic"], abs=1e-6)
    assert v["f_atc_p"] == pytest.approx(s["mp_own"] / s["atc"], abs=1e-6)
    assert v["f_nc_lc"] == pytest.approx(s["onc_ord"] / s["mc"], abs=1e-6)
    daily = sum((day_row(d)["atc"] / day_row(d)["mc"]) for d in T.daterange(D(2026, 10, 26), D(2026, 11, 1))) / 7
    assert abs(v["f_lc_atc"] - daily) > 1e-5                                          # the average of daily ratios would differ


def test_change_against_the_previous_week_is_in_percentage_points():
    fr, cov = build()
    p = payload(fr, cov, "week")
    row = next(r for r in p["rows"] if r["key"] == "2026-10-26")
    prev = next(r for r in p["rows"] if r["key"] == "2026-10-19")
    assert row["d"]["f_lc_atc"]["t"] == "pp"
    assert row["d"]["f_lc_atc"]["v"] == pytest.approx(row["v"]["f_lc_atc"] - prev["v"]["f_lc_atc"], abs=1e-5)


def test_month_bucket_sums_the_whole_month_and_the_period_before_the_data_dashes():
    fr, cov = build()
    p = payload(fr, cov, "month", 3)
    open_m = p["rows"][0]
    assert open_m["state"] == "open"
    s = S(fr, D(2026, 11, 1), D(2026, 11, 11))
    assert open_m["v"]["f_atc_p"] == pytest.approx(s["mp_own"] / s["atc"], abs=1e-6)
    # October starts before the first funnel day (Oct 5): a dash with the reason; September too
    oct_ = next(r for r in p["rows"] if r["key"] == "2026-10-01")
    for k in ("f_lpv", "f_lc_atc", "f_atc_ic", "f_ic_p", "f_atc_p"):
        assert oct_["v"][k] is None
        assert "starts Oct 5, 2026" in oct_["why"][k]
    assert oct_["v"]["f_nc_lc"] is not None                                              # actual orders over link clicks does not need the funnel events
    assert p["captions"]["funnel"].endswith("Funnel data starts Oct 5, 2026; earlier periods show a dash.")


def test_zero_denominator_is_a_dash_not_zero():
    fr, cov = build()
    for d in T.daterange(D(2026, 10, 26), D(2026, 11, 1)):
        fr[d]["atc"] = 0.0
        fr[d]["mc"] = 0.0
    p = payload(fr, cov, "week")
    row = next(r for r in p["rows"] if r["key"] == "2026-10-26")
    for k in ("f_lpv", "f_lc_atc", "f_atc_ic", "f_atc_p", "f_nc_lc"):
        assert row["v"][k] is None, k
        assert "denominator is zero" in row["why"][k]


def test_a_day_without_funnel_events_inside_a_closed_week_dashes_the_week():
    hole = D(2026, 10, 28)
    fr, cov = build(holes=(hole,))
    p = payload(fr, cov, "week")
    row = next(r for r in p["rows"] if r["key"] == "2026-10-26")
    assert row["v"]["f_lc_atc"] is None and "no data for Oct 28, 2026" in row["why"]["f_lc_atc"]
    full = next(r for r in p["rows"] if r["key"] == "2026-10-19")
    assert full["v"]["f_lc_atc"] is not None


def test_open_week_runs_through_the_last_day_the_funnel_holds_with_a_like_for_like_base():
    fr, cov = build(funnel_last=D(2026, 11, 9))
    p = payload(fr, cov, "week")
    row = p["rows"][0]
    assert row["state"] == "open" and row["covered_end"] == "2026-11-11"
    s = S(fr, D(2026, 11, 9), D(2026, 11, 9))
    assert row["v"]["f_lc_atc"] == pytest.approx(s["atc"] / s["mc"], abs=1e-6) and row["thru"]["f_lc_atc"] == "2026-11-09"


def test_layout_has_a_conversion_band_in_its_own_chip_and_the_labels_are_plain_words():
    fr, cov = build()
    p = payload(fr, cov, "week")
    band = next(b for b in p["layout"] if b["key"] == "funnel")
    assert band["group"] == "funnel" and band["title"] == "CONVERSION"
    labels = [c["label"] for c in band["cols"]]
    assert labels == ["Link click to landing page view", "Link click to add to cart", "Add to cart to checkout", "Checkout to purchase", "Add to cart to purchase", "New customers per link click"]
    assert not any("—" in x or "–" in x for x in labels)
    g = next(g for g in p["groups"] if g["key"] == "funnel")
    assert g["label"] == "Conversion" and g["default"] is True
    assert {m["key"]: m["change"] for m in p["metrics"] if m["key"].startswith("f_")} == dict.fromkeys(["f_lpv", "f_lc_atc", "f_atc_ic", "f_ic_p", "f_atc_p", "f_nc_lc"], "pp")
