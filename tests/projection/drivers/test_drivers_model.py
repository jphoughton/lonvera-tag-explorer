"""Engine math for the drivers module (pure: no database)."""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.projection.drivers.model import DEFAULT_SHARES, DriverInputs, RoasSlide, compute_drivers, parse_levers

TODAY = date(2026, 11, 3)


def M(y, m):
    return date(y, m, 1)


def inputs(**kw):
    kw.setdefault("today", TODAY)
    return DriverInputs(**kw)


def plan(**cells):
    return {k: dict(v) for k, v in cells.items()}


def test_planned_month_math_follows_the_sheet():
    inp = inputs(plan=plan(spend_total={M(2026, 12): 350000.0}, nc_cpa={M(2026, 12): 95.0}, nc_aov={M(2026, 12): 69.0}))
    d = compute_drivers({}, inp)
    m = M(2026, 12)
    sh = DEFAULT_SHARES
    assert d.spend_total[m] == 350000.0 and d.marketing_spend[m] == 350000.0
    assert d.spend_meta[m] == pytest.approx(350000 * sh["meta"])
    assert d.spend_google[m] == pytest.approx(350000 * sh["google"])
    assert d.ad_spend[m] == pytest.approx(350000 * (sh["meta"] + sh["google"]))      # Meta + Google only, not the total
    assert d.nc_orders[m] == pytest.approx(d.ad_spend[m] / 95)
    assert d.nc_rev[m] == pytest.approx(d.ad_spend[m] / 95 * 69)
    assert d.nc_roas[m] == pytest.approx(69 / 95)
    assert d.nc_cpa[m] == pytest.approx(95) and d.nc_aov[m] == pytest.approx(69)
    assert d.source[m] == "plan"


def test_channels_add_up_to_the_total():
    d = compute_drivers({}, inputs(plan=plan(spend_total={M(2027, 1): 123456.78})))
    m = M(2027, 1)
    parts = d.spend_amazon_ads[m] + d.spend_meta[m] + d.spend_google[m] + d.spend_bing[m] + d.spend_tiktok[m]
    assert parts == pytest.approx(123456.78)


def test_channel_share_lever_changes_the_split_not_the_total():
    p = {"drivers": {"channel_share": {"amazon": 0.1, "meta": 0.7, "google": 0.2, "bing": 0.0, "tiktok": 0.0}}}
    d = compute_drivers(p, inputs(plan=plan(spend_total={M(2027, 1): 1000.0}, nc_cpa={M(2027, 1): 100.0}, nc_aov={M(2027, 1): 80.0})))
    assert d.spend_meta[M(2027, 1)] == pytest.approx(700) and d.spend_google[M(2027, 1)] == pytest.approx(200)
    assert d.ad_spend[M(2027, 1)] == pytest.approx(900) and d.nc_orders[M(2027, 1)] == pytest.approx(9)


def test_carry_forward_all_plan_lines_and_flags():
    inp = inputs(plan=plan(spend_total={M(2026, 12): 100000.0}, nc_cpa={M(2026, 12): 90.0}, nc_aov={M(2026, 12): 70.0},
                           amazon_rev={M(2026, 12): 5000.0}, tiktok_rev={M(2026, 12): 0.0}))
    d = compute_drivers({}, inp)
    for m in (M(2027, 1), M(2027, 6), M(2029, 12)):
        assert d.spend_total[m] == 100000.0 and d.plan_cpa[m] == 90.0 and d.plan_aov[m] == 70.0 and d.amazon_rev[m] == 5000.0
    assert M(2027, 1) in d.carried["spend_total"] and M(2027, 1) in d.carried["nc_cpa"] and M(2027, 1) in d.carried["nc_aov"]
    assert M(2026, 12) not in d.carried["spend_total"]


def test_typed_cell_beats_carry_and_changes_everything_after():
    inp = inputs(plan=plan(spend_total={M(2026, 12): 100000.0, M(2027, 3): 50000.0}))
    d = compute_drivers({}, inp)
    assert d.spend_total[M(2027, 2)] == 100000.0 and d.spend_total[M(2027, 3)] == 50000.0 and d.spend_total[M(2027, 4)] == 50000.0


def test_roas_slide_is_linear_then_holds():
    slide = {"enabled": True, "from": "2026-12-01", "to": "2027-06-01", "start": 0.80, "end": 0.60}
    inp = inputs(plan=plan(spend_total={M(2026, 12): 100000.0}, nc_aov={M(2026, 12): 72.0}))
    d = compute_drivers({"drivers": {"roas_slide": slide}}, inp)
    assert d.nc_roas[M(2026, 12)] == pytest.approx(0.80)                       # CPA = AOV / ROAS -> ROAS comes back out
    assert d.plan_cpa[M(2026, 12)] == pytest.approx(72 / 0.80)
    assert d.nc_roas[M(2027, 6)] == pytest.approx(0.60) and d.nc_roas[M(2028, 1)] == pytest.approx(0.60)    # held after the end
    assert M(2026, 12) in d.slid and M(2028, 1) in d.slid
    assert d.nc_roas[M(2027, 1)] == pytest.approx(0.8 - 0.2 * 1 / 6)           # one sixth of the way down


def test_inside_the_slide_window_a_typed_input_is_hidden_and_an_override_wins():
    slide = {"enabled": True, "from": "2026-12-01", "to": "2027-06-01", "start": 0.80, "end": 0.60}
    cpa = {M(2027, 2): 111.0, M(2027, 3): 130.0, M(2027, 8): 120.0}
    info = {"nc_cpa": {M(2027, 2): {"kind": "input", "note": None, "origin": "x"}, M(2027, 3): {"kind": "override", "note": None, "origin": "x"},
                       M(2027, 8): {"kind": "input", "note": None, "origin": "x"}}}
    inp = inputs(plan=plan(spend_total={M(2026, 12): 100000.0}, nc_aov={M(2026, 12): 72.0}, nc_cpa=cpa), cell_info=info)
    d = compute_drivers({"drivers": {"roas_slide": slide}}, inp)
    assert d.plan_cpa[M(2027, 2)] == pytest.approx(72 / d.nc_roas[M(2027, 2)]) and M(2027, 2) in d.slid           # the input is superseded
    assert d.plan_cpa[M(2027, 3)] == 130.0 and M(2027, 3) not in d.slid                                           # an override pins its month
    assert d.plan_cpa[M(2027, 8)] == 120.0                                                                        # after the window typed cells count again
    assert d.plan_cpa[M(2027, 9)] == 120.0                                                                        # and carry on
    off = compute_drivers({}, inp)
    assert off.plan_cpa[M(2027, 2)] == 111.0 and not off.slid


def test_roas_slide_does_not_reach_back_and_is_inert_when_off():
    slide = {"enabled": False, "from": "2026-12-01", "to": "2027-06-01", "start": 0.8, "end": 0.6}
    inp = inputs(plan=plan(spend_total={M(2026, 11): 100000.0}, nc_cpa={M(2026, 11): 100.0}, nc_aov={M(2026, 11): 70.0}))
    off = compute_drivers({"drivers": {"roas_slide": slide}}, inp)
    assert off.plan_cpa[M(2027, 2)] == 100.0
    on = compute_drivers({"drivers": {"roas_slide": {**slide, "enabled": True}}}, inp)
    assert on.plan_cpa[M(2026, 11)] == 100.0 and on.plan_cpa[M(2026, 12)] == pytest.approx(70 / 0.8)


def test_overrides_win_over_everything_and_levers_follow_the_implied_value():
    m = M(2026, 12)
    inp = inputs(plan=plan(spend_total={m: 100000.0}, nc_cpa={m: 100.0}, nc_aov={m: 70.0}),
                 overrides=plan(ad_spend={m: 80000.0}, nc_orders={m: 1000.0}, nc_rev={m: 60000.0}))
    d = compute_drivers({}, inp)
    assert d.ad_spend[m] == 80000.0 and d.nc_orders[m] == 1000.0 and d.nc_rev[m] == 60000.0
    assert d.nc_cpa[m] == 80.0 and d.nc_aov[m] == 60.0 and d.nc_roas[m] == pytest.approx(0.75)
    assert d.nc_basis[m] == "override"
    assert d.plan_cpa[M(2027, 1)] == 80.0 and d.plan_aov[M(2027, 1)] == 60.0           # the next month carries what was measured


def test_cohort_actual_beats_the_plan_for_closed_months_but_not_an_override():
    inp = inputs(plan=plan(spend_total={M(2026, 10): 350000.0}, nc_cpa={M(2026, 10): 100.0}, nc_aov={M(2026, 10): 72.0}),
                 nc_orders_actual={M(2026, 10): 3614.0}, nc_rev_actual={M(2026, 10): 246904.35})
    d = compute_drivers({}, inp)
    assert d.nc_orders[M(2026, 10)] == 3614.0 and d.nc_rev[M(2026, 10)] == 246904.35 and d.nc_basis[M(2026, 10)] == "cohort"
    inp.overrides = plan(nc_rev={M(2026, 10): 240354.88})
    d = compute_drivers({}, inp)
    assert d.nc_orders[M(2026, 10)] == 3614.0 and d.nc_rev[M(2026, 10)] == 240354.88 and d.nc_basis[M(2026, 10)] == "override"
    assert d.nc_rev_cohort[M(2026, 10)] == 246904.35                                    # the cohort value stays visible for comparison


def test_cohort_actual_is_ignored_for_the_open_month():
    inp = inputs(plan=plan(spend_total={M(2026, 11): 100000.0}, nc_cpa={M(2026, 11): 100.0}, nc_aov={M(2026, 11): 70.0}),
                 nc_orders_actual={M(2026, 11): 5.0}, nc_rev_actual={M(2026, 11): 350.0})
    d = compute_drivers({}, inp)
    assert d.nc_basis[M(2026, 11)] == "plan" and d.nc_orders[M(2026, 11)] > 100


def test_books_month_is_actual_and_sets_actual_through():
    qb = {"meta": {M(2026, 6): 400000.0, M(2026, 7): 500000.0}, "google": {M(2026, 6): 20000.0, M(2026, 7): 30000.0},
          "amazon": {M(2026, 6): 10000.0}, "tiktok": {M(2026, 7): 5000.0}}
    d = compute_drivers({}, inputs(qb_spend=qb))
    assert d.actual_through == M(2026, 7)
    assert d.source[M(2026, 6)] == "books" and d.source[M(2026, 8)] == "plan"
    assert d.spend_total[M(2026, 6)] == 430000.0 and d.spend_total[M(2026, 7)] == 535000.0
    assert d.ad_spend[M(2026, 6)] == 420000.0                    # Meta + Google
    assert d.spend_bing[M(2026, 6)] == 0.0                       # blank = no spend


def test_feed_only_month_uses_meta_exactly_and_estimates_the_rest_from_the_mix():
    d = compute_drivers({}, inputs(meta_feed={M(2026, 10): 314002.33}))
    m = M(2026, 10)
    assert d.source[m] == "meta_feed" and d.actual_through == m
    assert d.spend_meta[m] == 314002.33
    assert d.spend_total[m] == pytest.approx(314002.33 / DEFAULT_SHARES["meta"])
    assert d.spend_google[m] == pytest.approx(d.spend_total[m] * DEFAULT_SHARES["google"])


def test_books_beat_the_feed_for_the_same_month():
    qb = {"meta": {M(2026, 9): 500000.0}}
    d = compute_drivers({}, inputs(qb_spend=qb, meta_feed={M(2026, 9): 520000.0}))
    assert d.source[M(2026, 9)] == "books" and d.spend_meta[M(2026, 9)] == 500000.0


def test_the_open_month_is_never_actual_even_with_data():
    d = compute_drivers({}, inputs(qb_spend={"meta": {M(2026, 11): 1.0}}, meta_feed={M(2026, 11): 2.0}))
    assert d.actual_through is None and d.source[M(2026, 11)] == "plan"


@pytest.mark.parametrize("today,closed", [(date(2026, 11, 1), M(2026, 10)), (date(2026, 11, 30), M(2026, 10)), (date(2026, 12, 1), M(2026, 11))])
def test_month_boundary_follows_today(today, closed):
    feed = {M(2026, 10): 100.0, M(2026, 11): 200.0}
    d = compute_drivers({}, DriverInputs(today=today, meta_feed=feed))
    assert d.actual_through == closed


def test_zero_denominators_never_raise():
    inp = inputs(plan=plan(spend_total={M(2026, 12): 0.0}, nc_cpa={M(2026, 12): 0.0}, nc_aov={M(2026, 12): 0.0}))
    d = compute_drivers({}, inp)
    m = M(2026, 12)
    assert d.nc_orders[m] == 0.0 and d.nc_rev[m] == 0.0 and d.nc_roas[m] == 0.0 and d.nc_cpa[m] == 0.0 and d.nc_aov[m] == 0.0
    d2 = compute_drivers({}, inputs(plan=plan(spend_total={m: 1000.0}, nc_cpa={m: 0.0}, nc_aov={m: 50.0})))
    assert d2.nc_orders[m] == 0.0 and all(math.isfinite(v) for v in d2.nc_roas.values())


def test_empty_inputs_give_a_full_axis_of_zeros():
    d = compute_drivers({}, inputs())
    assert len(d.spend_total) == 60 and set(d.spend_total.values()) == {0.0} and d.actual_through is None


def test_amazon_and_tiktok_use_loaded_actuals_for_closed_months_and_the_plan_after():
    inp = inputs(amazon_actual={M(2026, 6): 272505.0}, tiktok_actual={M(2026, 6): 3656.0},
                 plan=plan(amazon_rev={M(2026, 6): 1.0, M(2026, 12): 175000.0}, tiktok_rev={M(2026, 12): 0.0}))
    d = compute_drivers({}, inp)
    assert d.amazon_rev[M(2026, 6)] == 272505.0 and d.tiktok_rev[M(2026, 6)] == 3656.0      # actual wins over a typed cell
    assert d.amazon_rev[M(2026, 12)] == 175000.0 and d.amazon_rev[M(2027, 5)] == 175000.0
    assert d.tiktok_rev[M(2027, 1)] == 0.0


def test_levers_fall_back_to_defaults_when_malformed():
    lv = parse_levers({"drivers": {"channel_share": {"meta": -1, "google": "x", "amazon": float("nan")}, "sales_tax_rate": 7,
                                   "roas_slide": {"enabled": True, "start": 0.8, "end": 0.6, "from": "nope", "to": "2027-01-01"},
                                   "payback": {"product_cost_pct": 2, "fulfillment_per_order": -3, "horizon_months": 0}, "ad_spend_channels": ["tv"],
                                   "posture_band": 5, "summary_months": 99}})
    assert lv.shares == DEFAULT_SHARES and lv.sales_tax_rate == 0.07 and not lv.roas_slide.enabled
    assert lv.payback == {"product_cost_pct": 0.1726, "fulfillment_per_order": 5.53, "horizon_months": 36}
    assert lv.ad_channels == ("meta", "google") and lv.posture_band == 0.10 and lv.summary_months == 12
    assert parse_levers(None).shares == DEFAULT_SHARES and parse_levers({"cohorts": {}}).sales_tax_rate == 0.07


def test_levers_round_trip_to_dict():
    p = {"drivers": {"channel_share": {"amazon": 0.1, "meta": 0.8, "google": 0.1, "bing": 0, "tiktok": 0}, "sales_tax_rate": 0.05,
                     "roas_slide": {"enabled": True, "from": "2026-12-01", "to": "2027-12-01", "start": 0.7, "end": 0.6}}}
    lv = parse_levers(p)
    out = lv.to_dict()
    assert out["channel_share"]["meta"] == 0.8 and out["sales_tax_rate"] == 0.05 and out["roas_slide"]["from"] == "2026-12-01"
    assert parse_levers({"drivers": out}).to_dict() == out


def test_roas_slide_object_edge_cases():
    rs = RoasSlide(True, M(2026, 12), M(2026, 12), 0.8, 0.6)
    assert rs.roas(M(2026, 12)) == 0.6 and rs.roas(M(2026, 11)) is None and RoasSlide().roas(M(2027, 1)) is None


def test_edit_in_one_month_does_not_change_earlier_months():
    base = inputs(plan=plan(spend_total={M(2026, 12): 100.0, M(2027, 6): 100.0}))
    a = compute_drivers({}, base)
    base.plan["spend_total"][M(2027, 6)] = 999.0
    b = compute_drivers({}, base)
    assert all(a.spend_total[m] == b.spend_total[m] for m in a.spend_total if m < M(2027, 6))
    assert b.spend_total[M(2027, 6)] == 999.0
