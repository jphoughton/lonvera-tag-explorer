"""Trends: the projected row under the open (so far) period. Pure (no database): app.trends.assemble over a made-up day frame with a weekday rhythm.
Weeks scale the period so far by the weekday share of the last 8 complete weeks; months by days in the month over days with data (the Pacing page's rule);
ratios come from the projected sums; reach cannot be projected; the Day view has no projected row; the change is against the previous FULL period."""
import datetime as dt

from app import trends as T

D = dt.date
TODAY = D(2026, 11, 12)                      # Thursday; Nov 11 (Wednesday) is the newest loaded day: W46 has 3 of 7 days, November 11 of 30
LAST = D(2026, 11, 11)
WEIGHT = [1, 1, 1, 1, 1, 2, 2]               # revenue weekday rhythm Mon..Sun, 9 units a week; spend is flat


def day_row(d):
    return {"onc_rev": 100.0 * WEIGHT[d.weekday()], "onc_ord": 2.0 * WEIGHT[d.weekday()], "otot": 150.0, "orc": 50.0, "ms": 40.0, "gs": 10.0, "mi": 1000.0, "mc": 20.0}


def build():
    fr = {d: day_row(d) for d in T.daterange(D(2026, 8, 1), LAST)}
    cov = {k: T.Cov(D(2025, 1, 1), LAST) for k in T.SOURCE_LABELS}
    return fr, cov


def payload(interval, n=3):
    fr, cov = build()
    opt = {"interval": interval, "periods": n, "numbers": "own", "as_of": None}
    through = T.through_of(cov, TODAY)
    return T.assemble(opt, TODAY, cov, through, T.build_periods(interval, TODAY, through, n), fr, {})


def test_week_projection_uses_the_weekday_share_and_recomputes_ratios():
    rows = payload("week")["rows"]
    open_row = rows[0]
    assert open_row["state"] == "open" and open_row["days_cov"] == 3
    pj = open_row["projected"]
    assert pj["label"] == "W46 projected" and pj["method"] == "weekday"
    # Mon-Wed so far = 3 x 100; Mon-Wed are 3 of the 9 weekly units, so the factor is 3 and the week lands on 900 (what a whole week is)
    assert open_row["v"]["nc_rev"] == 300.0
    assert pj["factor"]["nc_rev"] == 3.0 and pj["v"]["nc_rev"] == 900.0
    # flat spend: 7 / 3 of 150
    assert pj["v"]["total_spend"] == 350.0 and abs(pj["factor"]["total_spend"] - 7 / 3) < 1e-3
    # the ratio comes from the projected sums, not from a scaled ratio
    assert pj["v"]["nc_roas"] == round(900.0 / 350.0, 6)
    assert pj["v"]["nc_cpa"] == round(350.0 / (2.0 * 9), 6)
    assert pj["v"]["m_cpm"] == 40.0   # Meta spend / impressions x 1000, both scaled by 7/3
    # change against the full previous week (900 revenue), not like for like
    assert pj["d"]["nc_rev"]["v"] == 0.0 and pj["compare"]["start"] == "2026-11-02" and pj["compare"]["end"] == "2026-11-08"
    # not counted as an actual: the open row keeps its own values
    assert open_row["v"]["total_spend"] == 150.0


def test_month_projection_is_the_pacing_rule():
    row = payload("month", 2)["rows"][0]
    pj = row["projected"]
    assert row["days_cov"] == 11 and pj["method"] == "pace" and pj["label"] == "Nov projected"
    mtd = row["v"]["nc_rev"]
    assert pj["v"]["nc_rev"] == round(mtd / 11 * 30, 6)           # Pacing: month to date / days with data x days in the month
    assert pj["factor"]["nc_rev"] == round(30 / 11, 4)
    assert pj["compare"]["start"] == "2026-10-01" and pj["compare"]["end"] == "2026-10-31"


def test_reach_and_closed_periods_have_no_projection():
    rows = payload("week")["rows"]
    pj = rows[0]["projected"]
    assert pj["v"]["reach"] is None and "de-duplicated" in pj["why"]["reach"]
    assert all("projected" not in r for r in rows[1:])


def test_day_has_no_projected_row():
    assert all("projected" not in r for r in payload("day", 5)["rows"])


def test_short_history_falls_back_to_plain_pace():
    fr, cov = build()
    for k in cov:
        cov[k] = T.Cov(D(2026, 10, 25), LAST)               # only 2 complete weeks before W46
    opt = {"interval": "week", "periods": 3, "numbers": "own", "as_of": None}
    through = T.through_of(cov, TODAY)
    pj = T.assemble(opt, TODAY, cov, through, T.build_periods("week", TODAY, through, 3), fr, {})["rows"][0]["projected"]
    assert pj["v"]["nc_rev"] == round(300.0 * 7 / 3, 6) and "nc_rev" in pj["fallback"]
