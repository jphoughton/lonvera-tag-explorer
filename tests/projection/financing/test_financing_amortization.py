"""Term loan, seller note and tax: amortization arithmetic and edge cases (pure functions, no database)."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection.financing import calc
from app.projection.interfaces import MONTHS

START = date(2026, 9, 1)


def m(y, mo):
    return date(y, mo, 1)


def test_interest_is_beginning_balance_times_rate_over_twelve():
    d = calc.debt_schedule(MONTHS, principal=3_000_000, rate=0.15, start=START, principal_paid={m(2026, 10): 180_000})
    assert d.months[0] == START and d.months[-1] == m(2029, 12) and len(d.months) == 40
    assert d.beginning[0] == 3_000_000 and d.interest[0] == pytest.approx(37_500)
    assert d.principal[0] == 0 and d.ending[0] == 3_000_000          # month 1 has no typed principal: interest only
    assert d.principal[1] == 180_000 and d.ending[1] == 2_820_000
    assert d.interest[2] == pytest.approx(2_820_000 * 0.15 / 12)     # next month's interest is on the lower balance
    assert d.beginning[2] == d.ending[1]


def test_cumulative_columns_and_totals_add_up():
    paid = {m(2026, 10): 100_000, m(2026, 11): 50_000}
    d = calc.debt_schedule(MONTHS, principal=1_000_000, rate=0.12, start=START, principal_paid=paid)
    assert d.cum_principal[-1] == 150_000
    assert d.cum_interest[-1] == pytest.approx(sum(d.interest)) == pytest.approx(d.total_interest)
    assert d.total_payment[1] == pytest.approx(100_000 + d.interest[1])
    assert d.ending[-1] == 850_000 and d.payoff_month is None


def test_principal_is_capped_at_the_balance_owed_and_pays_off():
    d = calc.debt_schedule(MONTHS, principal=100_000, rate=0.12, start=START, principal_paid={m(2026, 10): 60_000, m(2026, 11): 60_000, m(2026, 12): 5_000})
    assert d.principal[1] == 60_000 and d.principal[2] == 40_000      # the second payment is cut back to what is owed
    assert d.ending[2] == 0.0 and d.payoff_month == m(2026, 11)
    assert d.principal[3] == 0 and d.interest[3] == 0                 # nothing accrues on a zero balance
    assert d.capped == [m(2026, 11), m(2026, 12)]
    assert d.cum_principal[-1] == 100_000


def test_negative_typed_principal_is_ignored():
    d = calc.debt_schedule(MONTHS, principal=500_000, rate=0.1, start=START, principal_paid={m(2026, 10): -10_000})
    assert d.principal[1] == 0 and d.ending[1] == 500_000


def test_fixed_payment_mode_amortizes_and_floors_in_the_last_month():
    d = calc.debt_schedule(MONTHS, principal=3_000_000, rate=0.15, start=START, mode="fixed_payment", fixed_payment=125_000)
    assert d.principal[0] == pytest.approx(125_000 - 37_500)
    assert d.total_payment[0] == pytest.approx(125_000)
    assert d.payoff_month is not None
    i = d.months.index(d.payoff_month)
    assert d.ending[i] == 0.0 and d.total_payment[i] <= 125_000 + 1e-6
    assert all(p == 0 for p in d.principal[i + 1:])                   # nothing after payoff
    assert d.cum_principal[-1] == pytest.approx(3_000_000)


def test_fixed_payment_below_interest_never_pays_principal():
    d = calc.debt_schedule(MONTHS, principal=1_000_000, rate=0.24, start=START, mode="fixed_payment", fixed_payment=10_000)   # interest is 20,000/mo
    assert set(d.principal) == {0}
    assert d.ending[-1] == 1_000_000


def test_zero_rate_zero_principal_and_late_start():
    z = calc.debt_schedule(MONTHS, principal=1_000, rate=0.0, start=START, principal_paid={m(2026, 10): 400})
    assert z.total_interest == 0 and z.ending[1] == 600
    none = calc.debt_schedule(MONTHS, principal=0, rate=0.15, start=START)
    assert none.payoff_month is None and none.total_interest == 0 and none.ending[-1] == 0
    late = calc.debt_schedule(MONTHS, principal=100, rate=0.12, start=m(2029, 11))
    assert late.months == [m(2029, 11), m(2029, 12)]
    off = calc.debt_schedule(MONTHS, principal=100, rate=0.12, start=m(2031, 1))
    assert off.months == []


def test_a_residual_balance_keeps_accruing_interest():
    # the sheet's schedule leaves $1,000 outstanding: 12.50 of interest a month at 15%
    d = calc.debt_schedule(MONTHS, principal=1_000, rate=0.15, start=START)
    assert d.interest[-1] == pytest.approx(12.5)


# ------------------------------------------------------------------------------------------------------------------ seller note

def test_seller_note_three_annual_installments_with_interest_on_the_balance():
    s = calc.seller_note(MONTHS, enabled=True, principal=1_500_000, rate=0.05, close=START, first_payment=m(2027, 9), installments=3)
    assert [p["month"] for p in s.payments] == [m(2027, 9), m(2028, 9), m(2029, 9)]
    assert [round(p["principal"]) for p in s.payments] == [500_000] * 3
    assert [round(p["interest"]) for p in s.payments] == [75_000, 50_000, 25_000]
    assert sum(p["interest"] for p in s.payments) == pytest.approx(150_000)
    assert s.balance[m(2027, 8)] == 1_500_000 and s.balance[m(2027, 9)] == 1_000_000 and s.balance[m(2029, 12)] == 0


def test_seller_note_is_off_unless_enabled_and_clips_to_the_axis():
    off = calc.seller_note(MONTHS, enabled=False, principal=1_500_000, rate=0.05, close=START, first_payment=m(2027, 9), installments=3)
    assert off.principal == {} and off.interest == {} and off.payments == []
    late = calc.seller_note(MONTHS, enabled=True, principal=900, rate=0.1, close=START, first_payment=m(2029, 9), installments=3)
    assert [p["month"] for p in late.payments] == [m(2029, 9)]       # later installments fall after the axis
    assert calc.seller_note(MONTHS, enabled=True, principal=0, rate=0.1, close=START, first_payment=m(2027, 9), installments=3).payments == []


# ------------------------------------------------------------------------------------------------------------------ tax

def test_tax_is_rate_times_period_noi_paid_in_its_month():
    noi = {m(2026, 9): 100.0, m(2026, 10): 200.0, m(2026, 11): 300.0, m(2026, 12): 400.0}
    pays = [{"month": m(2027, 9), "from": m(2026, 9), "to": m(2026, 12), "label": "stub"}]
    tax, detail = calc.tax_payments(MONTHS, pays, noi, 0.21)
    assert tax == {m(2027, 9): pytest.approx(210.0)}
    assert detail[0]["base"] == 1000.0 and detail[0]["amount"] == pytest.approx(210.0)


def test_tax_is_never_negative_and_can_deduct_interest():
    pays = [{"month": m(2027, 9), "from": m(2026, 9), "to": m(2026, 10)}]
    tax, _ = calc.tax_payments(MONTHS, pays, {m(2026, 9): -50.0, m(2026, 10): 20.0}, 0.21)
    assert tax[m(2027, 9)] == 0.0
    tax2, d2 = calc.tax_payments(MONTHS, pays, {m(2026, 9): 500.0, m(2026, 10): 500.0}, 0.2, interest={m(2026, 9): 100.0, m(2026, 10): 100.0},
                                 deduct_interest=True)
    assert tax2[m(2027, 9)] == pytest.approx(160.0) and d2[0]["base"] == 800.0


def test_two_payments_in_one_month_add_up():
    pays = [{"month": m(2027, 9), "from": m(2026, 9), "to": m(2026, 9)}, {"month": m(2027, 9), "from": m(2026, 10), "to": m(2026, 10)}]
    tax, _ = calc.tax_payments(MONTHS, pays, {m(2026, 9): 100.0, m(2026, 10): 100.0}, 0.5)
    assert tax[m(2027, 9)] == 100.0
