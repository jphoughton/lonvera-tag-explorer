"""One place for what a drivers series cell may hold. Used by PUT /drivers/series (the Spend plan page saves through it), POST /drivers/patterns,
and registered as `MODULE.validate_series` so the core can call it from PUT /series.

Rules
  * bounds per line: marketing spend 0 to 50,000,000; CPA and AOV above 0 and at most 1,000 (a CPA of 0 divides, a typo like 500000350000 is absurd);
    revenue and override lines have their own generous ceilings
  * a typed plan input (spend, CPA, AOV, Amazon, TikTok) is refused for a closed month that already holds loaded data, because the engine would
    ignore it; clearing such a cell is allowed, and so is a typed override
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from ...queries import ApiError
from ..interfaces import Month
from .model import OVERRIDE_KEYS, PLAN_KEYS, SERIES_PREFIX, DriverInputs, DriversResult


@dataclass(frozen=True)
class Bound:
    label: str
    lo: float
    hi: float
    lo_open: bool = False                     # True: the value must be above lo (CPA and AOV divide)

    def ok(self, v: float) -> bool:
        return math.isfinite(v) and (v > self.lo if self.lo_open else v >= self.lo) and v <= self.hi

    def rule(self) -> str:
        if self.lo_open:
            return f"above {self.lo:,.0f} and at most {self.hi:,.0f}"
        return f"between {self.lo:,.0f} and {self.hi:,.0f}"


BOUNDS: dict[str, Bound] = {
    "spend_total": Bound("Marketing spend", 0.0, 50_000_000.0),
    "nc_cpa": Bound("New-customer CPA", 0.0, 1000.0, lo_open=True),
    "nc_aov": Bound("New-customer AOV", 0.0, 1000.0, lo_open=True),
    "amazon_rev": Bound("Amazon revenue", 0.0, 100_000_000.0),
    "tiktok_rev": Bound("TikTok revenue", 0.0, 100_000_000.0),
    "ad_spend": Bound("Ad spend", 0.0, 50_000_000.0),
    "nc_orders": Bound("New-customer orders", 0.0, 5_000_000.0),
    "nc_rev": Bound("New-customer revenue", 0.0, 100_000_000.0),
    "repeat_rev": Bound("Repeat revenue", 0.0, 100_000_000.0),
}
assert set(BOUNDS) == set(PLAN_KEYS) | set(OVERRIDE_KEYS)


def bound_error(name: str, month: Month, value: float) -> str | None:
    b = BOUNDS[name]
    if b.ok(value):
        return None
    return f"{b.label} for {month:%b %Y} would be {value:,.2f}; it must be {b.rule()}"


def fmt_months(ms: list[Month]) -> str:
    return ", ".join(f"{m:%b %Y}" for m in ms[:4]) + (f" and {len(ms) - 4} more" if len(ms) > 4 else "")


def locked_months(name: str, months: list[Month], base: DriversResult, inp: DriverInputs) -> list[Month]:
    """Months among `months` where a typed plan value for line `name` would do nothing because loaded data holds the month."""
    cutoff = inp.today.replace(day=1)
    out: list[Month] = []
    for m in months:
        if name == "spend_total" and base.source.get(m) != "plan":
            out.append(m)
        elif name in ("nc_cpa", "nc_aov") and (base.nc_basis.get(m) != "plan" or base.source.get(m) != "plan"):
            out.append(m)
        elif name == "amazon_rev" and m < cutoff and m in inp.amazon_actual:
            out.append(m)
        elif name == "tiktok_rev" and m < cutoff and m in inp.tiktok_actual:
            out.append(m)
    return out


def check_items(items, base: DriversResult, inp: DriverInputs, *, only_drivers: bool = False) -> None:
    """Raise ApiError(400, plain message) for the first problem in parsed series items (series.Item). Items that are not drivers lines are ignored
    (another module's validator owns them) unless only_drivers is set, which rejects them."""
    closed: dict[str, list[Month]] = {}
    for it in items:
        if not it.line_key.startswith(SERIES_PREFIX):
            if only_drivers:
                raise ApiError(400, f"{it.line_key} is not a Spend plan line")
            continue
        name = it.line_key[len(SERIES_PREFIX):]
        if name not in BOUNDS:
            raise ApiError(400, f"{it.line_key} is not a Spend plan line")
        if it.value is None:                                   # clearing a cell is always fine
            continue
        err = bound_error(name, it.month, float(it.value))
        if err:
            raise ApiError(400, err)
        if name in PLAN_KEYS and it.kind == "input" and locked_months(name, [it.month], base, inp):
            closed.setdefault(name, []).append(it.month)
    if closed:
        name, ms = next(iter(closed.items()))
        raise ApiError(400, f"{BOUNDS[name].label}: {fmt_months(sorted(ms))} hold loaded data, so the plan applies to planned months only")


def validate_series(conn, scenario, items) -> None:
    """The core's hook: `MODULE.validate_series(conn, scenario, items)`; raises ApiError(400). Other modules' lines pass through untouched."""
    from . import loaders, model
    if not any(it.line_key.startswith(SERIES_PREFIX) for it in items):
        return
    inp = loaders.load_inputs(conn, scenario, loaders.current_date())
    check_items(items, model.compute_drivers(scenario.params, inp), inp)
