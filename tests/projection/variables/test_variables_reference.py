"""The realized-reference guard levers of the Spend plan (drivers): each one changes whether a feed figure is offered, proven on synthetic feeds."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.projection.drivers import model as dm, reference
from app.projection.variables import access as A, registry as R

TODAY = date(2026, 11, 3)
START, END = date(2026, 9, 1), date(2026, 11, 2)


def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def feeds(conn, *, skip_meta=()):
    """Shopify new customers 100 a day at $70 (AOV 70), Meta $8,000 and Google $1,000 a day: realized cost per customer $90."""
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_feed_orders (day, nc_orders, nc_rev) VALUES (%s,100,7000)", [(d,) for d in _days(START, END)])
        cur.executemany("INSERT INTO proj_feed_platform (day, platform, spend) VALUES (%s,'Google Ads',1000)", [(d,) for d in _days(START, END)])
        cur.executemany("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES ('a', %s, 'f', 'F', 8000, 'meta_api')",
                        [(d,) for d in _days(START, END) if d not in set(skip_meta)])


def ref(conn, scn):
    lv = dm.parse_levers(scn.params)
    return reference.load_reference(conn, ("meta", "google"), lv.reference_days, TODAY, lv)


def moved(conn, vid, value):
    ctx = A.Context(conn, "current")
    return A.scenario_with(ctx, R.get(vid), value), ctx


def test_baseline_offers_both_figures(rw):
    feeds(rw)
    r = ref(rw, A.Context(rw, "current").scn)
    assert r.cpa_window["cpa"] == pytest.approx(90.0) and r.aov_window["aov"] == pytest.approx(70.0) and not r.unavailable


def test_gap_days_decides_whether_a_feed_with_missing_days_still_counts(rw):
    feeds(rw, skip_meta=[date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)])      # 25 of 28 Meta days in the window
    scn, _ = moved(rw, "drivers.reference_gap_days", 2)
    assert "Meta spend feed has 25 of 28 days" in ref(rw, scn).unavailable["cpa"]
    scn3, _ = moved(rw, "drivers.reference_gap_days", 3)
    r3 = ref(rw, scn3)
    assert r3.cpa_window is not None and "cpa" not in r3.unavailable


@pytest.mark.parametrize("vid,value,figure", [("drivers.reference_cpa_min", 100.0, "cpa"), ("drivers.reference_cpa_max", 60.0, "cpa"),
                                              ("drivers.reference_aov_min", 80.0, "aov"), ("drivers.reference_aov_max", 50.0, "aov")])
def test_bounds_withhold_a_figure_outside_the_believable_range(rw, vid, value, figure):
    feeds(rw)
    assert not ref(rw, A.Context(rw, "current").scn).unavailable
    scn, _ = moved(rw, vid, value)
    r = ref(rw, scn)
    assert "outside" in r.unavailable[figure] and (r.cpa_window is None if figure == "cpa" else r.aov_window is None)


def test_reference_days_changes_the_window(rw):
    feeds(rw)
    base = ref(rw, A.Context(rw, "current").scn)
    scn, _ = moved(rw, "drivers.reference_days", 14)
    short = ref(rw, scn)
    assert base.cpa_window["days"] == 28 and short.cpa_window["days"] == 14 and short.cpa_window["from"] != base.cpa_window["from"]


def test_tolerance_is_read_by_the_module(rw):
    scn, _ = moved(rw, "drivers.reference_tolerance", 0.15)
    assert dm.parse_levers(scn.params).reference_tolerance == 0.15


def test_a_bound_is_saved_with_its_partner_and_reset_clears_both(rw):
    spec = R.get("drivers.reference_cpa_min")
    A.set_value(rw, "current", spec, 30.0)
    p = A.Context(rw, "current").scn.params["drivers"]
    assert p["reference_cpa_min"] == 30.0 and p["reference_cpa_max"] == 500.0
    assert dm.parse_levers({"drivers": p}).reference_cpa_min == 30.0
    A.set_value(rw, "current", R.get("drivers.reference_cpa_max"), 400.0)
    p = A.Context(rw, "current").scn.params["drivers"]
    assert (p["reference_cpa_min"], p["reference_cpa_max"]) == (30.0, 400.0)
    A.reset(rw, "current", [spec])
    ctx = A.Context(rw, "current")
    assert A.dig(ctx.scn.params, ("drivers", "reference_cpa_min")) is A._ABSENT and A.dig(ctx.scn.params, ("drivers", "reference_cpa_max")) is A._ABSENT
    assert not A.resolve(R.get("drivers.reference_cpa_max"), ctx).changed
    with pytest.raises(Exception):
        A.set_value(rw, "current", R.get("drivers.reference_cpa_min"), 900.0)             # above the partner: the module refuses the pair


def test_loaded_payback_and_margin_inputs_move_the_cash_and_debt_result(conn):
    from proof_helpers import changed_outputs
    ctx = A.Context(conn, "current")
    for vid, cur in (("financing.payback.loaded_months", 12), ("financing.payback.loaded_rate", 0.3), ("financing.matrix.margin", "product"),
                     ("financing.payback.loaded_lines", ["china_cogs"])):
        spec = R.get(vid)
        out = changed_outputs(ctx, spec, A.resolve(spec, ctx).value if A.resolve(spec, ctx).value is not None else cur)
        assert out, vid
