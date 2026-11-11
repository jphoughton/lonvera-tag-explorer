"""Trends: an open period never dashes because one source lags; each metric runs over the days all of its sources cover (thru), the base uses the same
number of days, and a CLOSED period with a gap still dashes. Pure (no database): app.trends.assemble over a small made-up day frame."""
import datetime as dt

from app import trends as T

D = dt.date
TODAY = D(2026, 11, 12)                 # Pacific today; Nov 11 is the newest day any source could hold
ORDERS_LAST, META_LAST = D(2026, 11, 10), D(2026, 11, 9)       # Meta lags a day behind the orders feed (the 2026-11-11 etl abort)


def day_row(d):
    return {"onc_rev": 100.0 + d.day, "onc_ord": 2.0, "otot": 150.0, "orc": 50.0, "ms": 40.0, "gs": 10.0}


def build(meta_present=None):
    fr = {d: day_row(d) for d in T.daterange(D(2026, 10, 1), ORDERS_LAST)}
    for d in T.daterange(META_LAST + dt.timedelta(days=1), ORDERS_LAST):          # no spend rows for the lagging days
        fr[d] = {k: v for k, v in fr[d].items() if k not in ("ms", "gs")}
    cov = {k: T.Cov(D(2025, 1, 1), None) for k in T.SOURCE_LABELS}
    cov["orders"] = T.Cov(D(2025, 1, 1), ORDERS_LAST)
    cov["meta"] = T.Cov(D(2025, 1, 1), META_LAST, meta_present)
    cov["google"] = T.Cov(D(2025, 1, 1), META_LAST)
    for k in ("meta_up", "google_up", "prod_spend", "prod_nc", "anyclick", "traffic", "reach"):
        cov[k] = T.Cov(D(2025, 1, 1), META_LAST)
    return fr, cov


def payload(fr, cov, interval="month", n=2):
    opt = {"interval": interval, "periods": n, "numbers": "own", "as_of": None}
    through = T.through_of(cov, TODAY)
    return T.assemble(opt, TODAY, cov, through, T.build_periods(interval, TODAY, through, n), fr, {})


def test_open_month_runs_each_metric_through_the_days_all_its_sources_cover():
    fr, cov = build()
    row = payload(fr, cov)["rows"][0]
    assert row["state"] == "open" and row["covered_end"] == "2026-11-10"
    # revenue-only metric: all 10 days, no hint
    assert row["v"]["nc_rev"] == sum(100.0 + d.day for d in T.daterange(D(2026, 11, 1), D(2026, 11, 10)))
    assert "nc_rev" not in row["thru"]
    # ratio over spend: Nov 1-9 on both sides, never Nov 10 revenue against no spend
    rev9 = sum(100.0 + d.day for d in T.daterange(D(2026, 11, 1), D(2026, 11, 9)))
    assert row["v"]["nc_roas"] == round(rev9 / (9 * 50.0), 6)
    assert row["thru"]["nc_roas"] == "2026-11-09" and row["thru"]["total_spend"] == "2026-11-09" and row["thru"]["nc_cpa"] == "2026-11-09"
    assert row["v"]["total_spend"] == 9 * 50.0 and row["v"]["mer"] == round(9 * 150.0 / (9 * 50.0), 6)
    assert row["v"]["nc_cpa"] == round(9 * 50.0 / (9 * 2.0), 6)


def test_base_uses_the_same_elapsed_days_as_the_shortened_window():
    fr, cov = build()
    row = payload(fr, cov)["rows"][0]
    oct9 = sum(100.0 + d.day for d in T.daterange(D(2026, 10, 1), D(2026, 10, 9)))
    base_roas = oct9 / (9 * 50.0)
    cur_roas = row["v"]["nc_roas"]
    assert row["d"]["nc_roas"]["v"] == round((cur_roas - base_roas) / base_roas, 6)
    # a metric on the full 10 days compares with Oct 1-10
    oct10 = sum(100.0 + d.day for d in T.daterange(D(2026, 10, 1), D(2026, 10, 10)))
    assert row["d"]["nc_rev"]["v"] == round((row["v"]["nc_rev"] - oct10) / oct10, 6)


def test_a_hole_inside_the_open_period_stops_the_window_before_it():
    fr, cov = build(meta_present={d for d in T.daterange(D(2025, 1, 1), META_LAST) if d != D(2026, 11, 5)})
    row = payload(fr, cov)["rows"][0]
    assert row["thru"]["total_spend"] == "2026-11-04" and row["v"]["total_spend"] == 4 * 50.0


def test_a_source_with_nothing_for_the_period_still_dashes_and_closed_periods_stay_strict():
    fr, cov = build()
    cov["meta"] = T.Cov(D(2026, 11, 20), D(2026, 11, 25))                                   # starts after the period: no coverage at all
    row = payload(fr, cov)["rows"][0]
    assert row["v"]["meta_spend"] is None and row["why"]["meta_spend"]
    # closed October, Meta missing one day in it: a dash, as before
    fr, cov = build(meta_present={d for d in T.daterange(D(2025, 1, 1), META_LAST) if d != D(2026, 10, 15)})
    p = payload(fr, cov)
    octr = p["rows"][1]
    assert octr["state"] == "closed" and octr["v"]["total_spend"] is None and "Oct 15" in octr["why"]["total_spend"]
    assert octr["thru"] == {}


def test_nothing_is_flagged_when_every_source_is_current():
    fr, cov = build()
    for k in cov:
        cov[k].last = ORDERS_LAST
    for d in T.daterange(META_LAST + dt.timedelta(days=1), ORDERS_LAST):
        fr[d] = day_row(d)
    row = payload(fr, cov)["rows"][0]
    assert row["thru"] == {} and row["v"]["total_spend"] == 10 * 50.0
