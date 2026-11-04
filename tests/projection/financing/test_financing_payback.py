"""Payback function, spend x ROAS grid and the scale-payback payout schedule (pure functions)."""
from __future__ import annotations

import math

import pytest

from app.projection.financing import calc

LIVE = [0.1920160302407477, 0.2899985729511289, 0.162543966044943, 0.10806020914041027, 0.049588161155446335, 0.07561894876931764,
        0.048215842971944145]


def curve(n=60, tail=0.01):
    return LIVE + [tail] * (n - len(LIVE))


def test_payback_matches_the_sheet_inputs():
    p = calc.payback(69.15600858369098, 105.4916738197425, 0.1726, 5.53, LIVE + [0.043, 0.04] + [0.01] * 49, 36)
    assert p.product_cost == pytest.approx(11.936327081545063)
    assert p.cm_first == pytest.approx(51.689681502145916)
    assert p.cm_pct == pytest.approx(0.7474358708772539)
    assert p.gap == pytest.approx(53.801992317596586)
    assert p.cumulative[0] == pytest.approx(51.689681502145916) and p.cumulative[1] == pytest.approx(61.614928948596585)
    assert len(p.cumulative) == 37 and p.retention[0] is None


def test_payback_month_is_the_first_age_that_covers_cac():
    c = [0.5] * 60
    p = calc.payback(100.0, 130.0, 0.2, 0.0, c, 36)                  # CM 80 on the first order, +40 a month
    assert p.cumulative[:3] == [80.0, 120.0, 160.0]
    assert p.months == 2
    assert p.months_interpolated == pytest.approx(1.25)              # 120 at age 1, 160 at age 2: CAC 130 is a quarter of the way in


def test_payback_on_the_first_order_and_never():
    now = calc.payback(100.0, 50.0, 0.0, 0.0, [0.1] * 60)
    assert now.months == 0 and now.months_interpolated == 0.0
    never = calc.payback(100.0, 500.0, 0.2, 0.0, [0.01] * 60, 36)
    assert never.months is None and never.months_interpolated is None
    assert never.surplus < 0 and never.final_cumulative == pytest.approx(never.cumulative[-1])


def test_payback_zero_cac_and_short_curve():
    p = calc.payback(100.0, 0.0, 0.3, 5.0, [], 12)                    # no curve: repeat months add nothing
    assert p.months == 0 and math.isinf(p.roas)
    assert p.cumulative[-1] == p.cumulative[0]
    assert len(p.cumulative) == 13


@pytest.mark.parametrize("args", [(0, 10, 0.2, 5), (-3, 10, 0.2, 5), (50, -1, 0.2, 5), (50, 10, 1.5, 5), (50, 10, -0.1, 5), (50, 10, 0.2, -5),
                                  (float("nan"), 10, 0.2, 5), (50, float("inf"), 0.2, 5)])
def test_payback_rejects_bad_inputs(args):
    with pytest.raises(ValueError):
        calc.payback(*args, [0.1] * 60)


def test_payback_cm_can_be_negative_and_then_never_pays_back():
    p = calc.payback(10.0, 20.0, 0.9, 5.0, [0.5] * 60)                # cost 14 > AOV 10
    assert p.cm_first < 0 and p.months is None


# ------------------------------------------------------------------------------------------------------------------ matrix

def test_cumulative_retention_is_one_plus_the_curve_to_the_target_month():
    assert calc.cumulative_retention(LIVE, 0) == 1.0
    assert calc.cumulative_retention(LIVE, 3) == pytest.approx(1 + 0.1920160302407477 + 0.2899985729511289 + 0.162543966044943)
    assert calc.cumulative_retention(LIVE, 99) == pytest.approx(1 + sum(LIVE))


def test_matrix_profit_and_marginal_breakeven_roas():
    cm_rate = 0.7474358708772539
    m = calc.spend_roas_matrix(curve=LIVE, cm_rate=cm_rate, target_month=3, basis="cm", fixed_cost=110_000, spends=[200_000, 1_000_000], roas=[0.4, 1.0])
    mult = calc.cumulative_retention(LIVE, 3) * cm_rate
    assert m.breakeven_roas == pytest.approx(1 / mult)
    assert m.profit[0][0] == pytest.approx(200_000 * 0.4 * mult - 200_000)
    assert m.profit[1][1] == pytest.approx(1_000_000 * 1.0 * mult - 1_000_000)
    at_breakeven = calc.spend_roas_matrix(curve=LIVE, cm_rate=cm_rate, target_month=3, basis="cm", fixed_cost=0, spends=[1_000_000], roas=[1 / mult])
    assert at_breakeven.profit[0][0] == pytest.approx(0, abs=1e-6)


def test_matrix_loaded_basis_subtracts_fixed_cost_and_gives_breakeven_spend():
    m = calc.spend_roas_matrix(curve=[0.5] * 60, cm_rate=0.8, target_month=2, basis="loaded", fixed_cost=100_000, spends=[100_000], roas=[1.0, 0.4])
    mult = 2.0 * 0.8                                                   # cumulative retention 1 + 0.5 + 0.5 = 2.0
    assert m.profit[0][0] == pytest.approx(100_000 * mult - 100_000 - 100_000)
    assert m.breakeven_spend[0] == pytest.approx(100_000 / (1.0 * mult - 1))
    assert m.breakeven_spend[1] is None                                # 0.4 x 1.6 < 1: no spend level covers the fixed cost
    cm = calc.spend_roas_matrix(curve=[0.5] * 60, cm_rate=0.8, target_month=2, basis="cm", fixed_cost=100_000, spends=[1], roas=[1.0, 0.4])
    assert cm.breakeven_spend == [0.0, None]


def test_matrix_with_no_margin():
    m = calc.spend_roas_matrix(curve=LIVE, cm_rate=0.0, target_month=3, basis="cm", fixed_cost=0, spends=[1000], roas=[1.0])
    assert m.breakeven_roas is None and m.profit[0][0] == -1000


# ------------------------------------------------------------------------------------------------------------------ payout

GOAL7 = [0.21616751323705444, 0.2935961421475032, 0.18737292927085783, 0.11461414103264435, 0.08018243193136824, 0.07698328547417052,
         0.05542415497359655]
COLS = [{"payback": 4, "first_scale": 850_000, "first_bonus": 2_000.0, "step_up": 0.15, "step_above": 0.10},
        {"payback": 3, "first_scale": 800_000, "first_bonus": 3_000.0, "step_up": 0.16, "step_above": 0.12},
        {"payback": 2, "first_scale": 800_000, "first_bonus": 3_500.0, "step_up": 0.15, "step_above": 0.10}]
SCALES = [800_000, 850_000, 900_000, 950_000, 1_000_000, 1_100_000, 1_200_000]


def block(**kw):
    base = dict(media_spend=800_000, cpa=90, aov=73, lazar=5_000, designer=3_000, samples_count=150, sample_cost=10, creative_production_rate=0.09,
                cogs_rate=0.14, fulfillment_rate=0.09, repeat=GOAL7, scales=SCALES, breakpoint=1_000_000, columns=COLS)
    base.update(kw)
    return calc.payout_block(**base)


def test_payout_model_matches_the_sheet():
    p = block()
    assert p.nc_rev == pytest.approx(648_888.8888888889) and p.roas == pytest.approx(0.8111111111111111)
    assert p.marketing_expenses == pytest.approx(81_500)               # 5,000 + 3,000 + 1,500 samples + 72,000 creative production
    assert p.creative_production == pytest.approx(72_000)
    assert p.cm == pytest.approx(-381_855.55555555556)
    assert p.running_cm[0] == pytest.approx(-273_848.6584972904)
    assert p.running_cm[3] == pytest.approx(23_731.1848082297)
    assert p.payback_month == 4


def test_payout_bonus_steps_and_total_comp():
    p = block()
    assert p.bonus[4][:3] == [None, 2_000.0, pytest.approx(2_300.0)]    # no bonus at 800K for payback 4; 15% steps after
    assert p.bonus[3][:2] == [3_000.0, pytest.approx(3_480.0)]
    assert p.bonus[3][5] == pytest.approx(5431.91808 * 1.12)             # above the $1.0M breakpoint the step is 12%
    assert p.bonus[2][5] == pytest.approx(6121.521875 * 1.10)
    assert p.total_comp[3][0] == 8_000.0 and p.total_comp[4][0] is None  # total comp = bonus + the retainer; nothing where there is no bonus
    assert block(lazar=7_000).total_comp[3][0] == 10_000.0


def test_payout_selected_cell():
    p = block()
    assert p.selected["payback_month"] == 4 and p.selected["column"] == 4 and p.selected["scale"] == 800_000
    assert p.selected["bonus"] is None                                   # payback 4 starts paying at 850K
    q = block(media_spend=1_050_000, cpa=70)                              # faster payback at a higher scale
    assert q.selected["scale"] == 1_000_000 and q.selected["column"] in (2, 3, 4)
    assert q.selected["bonus"] == q.bonus[q.selected["column"]][SCALES.index(1_000_000)]
    assert block(media_spend=500_000).selected["scale"] is None


def test_payout_payback_outside_the_schedule():
    slow = block(repeat=[0.01] * 7)
    assert slow.payback_month is None and slow.selected["column"] is None and slow.selected["bonus"] is None
    first_order = block(cpa=10)                                            # first order alone covers everything (ROAS above 7)
    assert first_order.cm > 0 and first_order.payback_month == 0 and first_order.selected["column"] == 2
    with pytest.raises(ValueError):
        block(cpa=0)
