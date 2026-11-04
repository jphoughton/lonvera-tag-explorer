"""Payback hint shown next to the spend plan: months until the cumulative contribution of a new customer covers what it cost to acquire them.

Same arithmetic as the sheet's Payback tab: contribution per order = AOV x (1 - product cost %) - fulfillment per order; month 0 is the first
order, month k adds contribution x (repeat dollars per first-order dollar at age k, the cohort curve). Payback = first month where the running
total reaches the cost per acquisition (CPA). The financing module owns the full payback model; this is the quick read used while editing.
"""
from __future__ import annotations

from typing import Sequence

HORIZON = 36


def contribution_per_order(aov: float, product_cost_pct: float, fulfillment_per_order: float) -> float:
    return aov * (1.0 - product_cost_pct) - fulfillment_per_order


def payback_month(aov: float, cpa: float, curve: Sequence[float], product_cost_pct: float, fulfillment_per_order: float,
                  horizon: int = HORIZON) -> int | None:
    """Months after the first order at which cumulative contribution >= cpa (0 = the first order pays for itself); None = not within `horizon`."""
    if cpa is None or aov is None or cpa <= 0 or aov <= 0:
        return None
    cm = contribution_per_order(aov, product_cost_pct, fulfillment_per_order)
    cum = cm
    if cum >= cpa:
        return 0
    for age in range(1, horizon + 1):
        c = curve[age - 1] if age - 1 < len(curve) else 0.0
        cum += cm * (c or 0.0)
        if cum >= cpa:
            return age
    return None


def cumulative_contribution(aov: float, curve: Sequence[float], product_cost_pct: float, fulfillment_per_order: float,
                            horizon: int = HORIZON) -> list[float]:
    """Running contribution per new customer, age 0..horizon."""
    cm = contribution_per_order(aov, product_cost_pct, fulfillment_per_order)
    out = [cm]
    for age in range(1, horizon + 1):
        c = curve[age - 1] if age - 1 < len(curve) else 0.0
        out.append(out[-1] + cm * (c or 0.0))
    return out
