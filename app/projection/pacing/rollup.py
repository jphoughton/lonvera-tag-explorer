"""Day / ISO week / month rollups of the pacing frame, the EBITDA estimate, and the partial-period projection.

Rollup rule (the sheet's): sum the additive base measures over the period's days, take reach-type measures from the feed rows pulled at the period's
own grain, recompute every ratio from the sums. A period that is still running is flagged `partial` and projected as
`actual / days with data x days in the period` (the sheet multiplied by the number of days with data instead of the days in the month, so its
projection of a running month equalled the month-to-date).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from . import catalog
from .feeds import Frame, apply_reach
from .periods import add_months, days_in_month, iso_week_key, iter_days, month_end, month_key, month_start, week_start
from .targets import R_MKT_TIKTOK, R_NOI, TargetBook

AXIS_FIRST = date(2025, 1, 1)
AXIS_LAST = date(2029, 12, 31)


# ---------------------------------------------------------------------------------------------------------------- periods

@dataclass
class Period:
    grain: str
    key: str
    start: date
    full_end: date
    end: date                      # last day with data in the period (clipped to data_through)
    days: int                      # days with data
    days_in_period: int
    partial: bool

    @property
    def label(self) -> str:
        if self.grain == "day":
            return self.start.strftime("%b %-d")
        if self.grain == "week":
            return f"{self.key[-3:]} {self.start.strftime('%b %-d')}"
        return self.start.strftime("%b %Y")


def make_periods(grain: str, start: date, end: date, data_through: date) -> list[Period]:
    """Periods of `grain` touching [start, end] (end clipped to data_through), oldest first."""
    if grain not in ("day", "week", "month"):
        raise ValueError("grain must be day, week or month")
    end = min(end, data_through)
    if end < start:
        return []
    out: list[Period] = []
    if grain == "day":
        for d in iter_days(start, end):
            out.append(Period("day", d.isoformat(), d, d, d, 1, 1, False))
        return out
    cur = week_start(start) if grain == "week" else month_start(start)
    while cur <= end:
        full_end = cur + timedelta(days=6) if grain == "week" else month_end(cur)
        nominal = 7 if grain == "week" else days_in_month(cur)
        last = min(full_end, data_through)
        data_first = max(cur, AXIS_FIRST)
        n = max(0, (last - data_first).days + 1)
        out.append(Period(grain, iso_week_key(cur) if grain == "week" else month_key(cur), cur, full_end, last, n, nominal, data_through < full_end))
        cur = cur + timedelta(days=7) if grain == "week" else add_months(cur, 1)
    return out


# ---------------------------------------------------------------------------------------------------------------- costs by day

def apply_costs(fr: Frame, book: TargetBook | None, rates_month: date) -> None:
    """Add the day-level cost, target and tax-estimate bases to every day of the frame (the EBITDA estimate and the plan-basis revenue).

    Each cost line is a rate on a revenue driver or fixed dollars (the scenario's cost_drivers lever overrides the sheet's definitions). Rates come from the plan
    month `rates_month`; fixed dollars and targets come from the day's own month, spread evenly over its days.
    """
    if book is None:
        return
    rates = book.rates(rates_month)
    lines = book.lines_()
    cache: dict[date, dict] = {}

    def month_numbers(m: date) -> dict:
        if m not in cache:
            dim = days_in_month(m)
            has = book.row(R_NOI, m) is not None
            fixed = {ln.key: book.line_goal(ln, m) / dim for ln in lines if ln.driver == "fixed"}
            cache[m] = {
                "dim": dim, "has_noi": has, "fixed": fixed,
                "below": book.below_ebitda(m) / dim,
                "noi": (book.val(R_NOI, m) / dim) if has else 0.0,
                "tiktok": book.val(R_MKT_TIKTOK, m) / dim,
                "tax": book.tax_rate(m),
            }
        return cache[m]

    for d, b in fr.days.items():
        if not (AXIS_FIRST <= d <= AXIS_LAST):
            continue
        mn = month_numbers(month_start(d))
        dtc_net = b["o_total_rev"] - b["o_tax"] - b["o_refunds"]
        net = dtc_net + b["a_revenue"]
        drv = {"total_rev": net, "dtc_rev": dtc_net, "amazon_rev": b["a_revenue"]}
        cogs = var_opex = fixed_opex = 0.0
        for ln in lines:
            amount = mn["fixed"][ln.key] if ln.driver == "fixed" else rates.get(ln.key, 0.0) * drv[ln.driver]
            if ln.section == "cogs":
                cogs += amount
            elif ln.driver == "fixed":
                fixed_opex += amount
            else:
                var_opex += amount
        b["e_cogs"], b["e_var_opex"], b["e_fixed_opex"] = cogs, var_opex, fixed_opex
        b["e_below"] = mn["below"]
        b["e_noi_target"] = mn["noi"]
        b["e_target_days"] = 1.0 if mn["has_noi"] else 0.0
        b["e_tiktok_plan"] = mn["tiktok"]
        b["x_tax_est"] = -b["o_total_rev"] * mn["tax"]


# ---------------------------------------------------------------------------------------------------------------- aggregate

def aggregate(fr: Frame, p: Period) -> dict:
    """Summed bases of one period (defaultdict(float)) with the reach bases of the period's grain."""
    out: dict = defaultdict(float)
    if p.days:
        d = max(p.start, AXIS_FIRST) if p.grain != "day" else p.start
        stop = p.end
        while d <= stop:
            row = fr.days.get(d)
            if row:
                for k, v in row.items():
                    out[k] += v
            d += timedelta(days=1)
    apply_reach(out, fr, p.grain, p.key)
    return out


@dataclass
class PeriodResult:
    period: Period
    values: dict
    bases: dict


def rollup(fr: Frame, grain: str, start: date, end: date, data_through: date, view: str = "rollup") -> list[PeriodResult]:
    out = []
    for p in make_periods(grain, start, end, data_through):
        b = aggregate(fr, p)
        out.append(PeriodResult(p, catalog.evaluate(b, view), b))
    return out


def project(value: float | None, p: Period) -> float | None:
    """Run-rate projection of an additive period total: actual / days with data x days in the period. A finished period projects to itself."""
    if value is None:
        return None
    if not p.partial or p.days <= 0:
        return value
    return value / p.days * p.days_in_period
