"""Payback hint: same arithmetic as the sheet's Payback tab."""
from __future__ import annotations

import pytest

from app.projection.drivers import payback
from conftest import needs_snapshot


def test_first_order_pays_for_itself():
    assert payback.payback_month(100, 50, [0.1], 0.2, 5) == 0


def test_hand_worked_example():
    # cm = 100*(1-0.2)-10 = 70; cum: 70, +70*0.5=105, +70*0.5=140 -> cpa 130 is reached at month 2
    assert payback.payback_month(100, 130, [0.5, 0.5, 0.5], 0.2, 10) == 2
    assert payback.cumulative_contribution(100, [0.5, 0.5], 0.2, 10, horizon=2) == [70, 105, 140]


def test_never_pays_back_returns_none_and_bad_inputs_are_safe():
    assert payback.payback_month(100, 500, [0.1] * 60, 0.2, 5) is None
    assert payback.payback_month(0, 10, [0.1], 0.2, 5) is None and payback.payback_month(10, 0, [0.1], 0.2, 5) is None
    assert payback.payback_month(10, None, [], 0.2, 5) is None
    assert payback.payback_month(100, 100, [], 0.2, 5) is None                    # an empty curve repeats nothing


@needs_snapshot
def test_matches_the_sheets_payback_tab():
    from scripts.projection import snapshot_read as sr
    curve = [float(v) for v in sr.col("Curves", "C", 4, 63)]
    aov, cac = sr.cell("Payback", "B7"), sr.cell("Payback", "B8")
    months = sr.cell("Payback", "H8")
    assert payback.payback_month(aov, cac, curve, 0.1726, 5.53) == months == 10
    cum = payback.cumulative_contribution(aov, curve, 0.1726, 5.53)
    assert cum[36] == pytest.approx(sr.cell("Payback", "B59"), rel=1e-9)
