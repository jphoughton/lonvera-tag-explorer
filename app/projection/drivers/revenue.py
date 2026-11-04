"""Revenue stack: repeat, DTC, sales tax and total company revenue (P&L rows 17, 18, 21, 22).

These need the cohort module's repeat revenue, which is computed after the drivers module, so they are a function the downstream modules call:

    from app.projection.drivers.revenue import revenue_lines
    rl = revenue_lines(drivers, cohorts.repeat_rev, dtc_actual=...)

Rules (the sheet's, made explicit):
  repeat(m) = the typed repeat override for the month if there is one (hand-typed months of row 17), else the cohorts module's repeat revenue.
  dtc(m)    = the loaded DTC revenue for the month (books, already net of sales tax) if there is one, else repeat + new-customer revenue.
  tax(m)    = 0 for a month with loaded DTC revenue, else -(sales tax rate x DTC)  (negative: it reduces total revenue).
  total(m)  = dtc + Amazon + TikTok + tax.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..interfaces import MONTHS, Series
from .model import DEFAULT_SALES_TAX


@dataclass
class RevenueLines:
    repeat_rev: Series = field(default_factory=dict)
    dtc_rev: Series = field(default_factory=dict)
    sales_tax: Series = field(default_factory=dict)
    total_revenue: Series = field(default_factory=dict)
    amazon_rev: Series = field(default_factory=dict)
    tiktok_rev: Series = field(default_factory=dict)


def revenue_lines(drivers, repeat_rev: Series | None, dtc_actual: Series | None = None, *, attach: bool = True) -> RevenueLines:
    """Final repeat / DTC / tax / total revenue per month. `repeat_rev` = CohortResult.repeat_rev; `dtc_actual` = loaded DTC revenue by month
    (the pnl module passes its QuickBooks series; the drivers' own loaded series is the fallback). With attach=True the four result series are
    also set on `drivers` (dtc_rev, repeat_rev, sales_tax, total_revenue) so later readers can use getattr; idempotent."""
    repeat_rev = repeat_rev or {}
    actual = dict(getattr(drivers, "dtc_actual", None) or {})
    actual.update(dtc_actual or {})
    override = getattr(drivers, "repeat_override", None) or {}
    rate = float(getattr(drivers, "sales_tax_rate", DEFAULT_SALES_TAX))
    out = RevenueLines(amazon_rev=dict(drivers.amazon_rev), tiktok_rev=dict(drivers.tiktok_rev))
    for m in MONTHS:
        rep = override[m] if m in override else repeat_rev.get(m, 0.0)
        out.repeat_rev[m] = rep
        if m in actual:
            dtc, tax = actual[m], 0.0
        else:
            dtc = rep + drivers.nc_rev.get(m, 0.0)
            tax = -rate * dtc
        out.dtc_rev[m] = dtc
        out.sales_tax[m] = tax
        out.total_revenue[m] = dtc + drivers.amazon_rev.get(m, 0.0) + drivers.tiktok_rev.get(m, 0.0) + tax
    if attach:
        for name in ("repeat_rev", "dtc_rev", "sales_tax", "total_revenue"):
            try:
                setattr(drivers, name, dict(getattr(out, name)))
            except AttributeError:                              # a frozen or slotted stand-in: skip, the return value is authoritative
                pass
    return out
