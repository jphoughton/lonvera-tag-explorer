"""Spend-plan patterns: fill a month range of one plan line from a short description. Pure functions.

    generate(pattern, months) -> {month: value}

Types
  flat    {value}                                   same value every month
  step    {value, then_value, step_month}           `value` before step_month, `then_value` from step_month on
  growth  {value, rate, cap?}                       value in the first month, then compounding by `rate` per month (0.03 = +3%/mo), optional cap
  ramp    {value, end_value}                        straight line from value (first month) to end_value (last month)
Targets: the five typed plan lines of the module. The ROAS slide is a lever, not a pattern (see api.py).
"""
from __future__ import annotations

import math
from typing import Any

from ..interfaces import Month, parse_month

TYPES = ("flat", "step", "growth", "ramp")
TARGETS = {                       # target -> (label, min, max); cells must satisfy min <= value <= max (min exclusive for rates that divide)
    "spend_total": ("Marketing spend", 0.0, 5e7),
    "nc_cpa": ("New-customer CPA", 0.01, 5e3),
    "nc_aov": ("New-customer AOV", 0.01, 5e3),
    "amazon_rev": ("Amazon revenue", 0.0, 1e8),
    "tiktok_rev": ("TikTok revenue", 0.0, 1e8),
}
MAX_MONTHS = 60


class PatternError(ValueError):
    pass


def _f(spec: dict, key: str, required: bool = True) -> float | None:
    v = spec.get(key)
    if v is None:
        if required:
            raise PatternError(f"{key} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PatternError(f"{key} must be a number")
    return float(v)


def generate(spec: Any, months: list[Month]) -> dict[Month, float]:
    """Values for `months` (sorted, consecutive or not) from the pattern. PatternError on a bad spec."""
    if not isinstance(spec, dict):
        raise PatternError("pattern must be an object")
    kind = spec.get("type")
    if kind not in TYPES:
        raise PatternError(f"pattern type must be one of {', '.join(TYPES)}")
    if not months:
        raise PatternError("no months in range")
    if len(months) > MAX_MONTHS:
        raise PatternError(f"at most {MAX_MONTHS} months")
    value = _f(spec, "value")
    out: dict[Month, float] = {}
    if kind == "flat":
        out = {m: value for m in months}
    elif kind == "step":
        then = _f(spec, "then_value")
        try:
            at = parse_month(spec.get("step_month"))
        except ValueError:
            raise PatternError("step_month must be a first-of-month date inside the plan (2025-01 .. 2029-12)") from None
        out = {m: (then if m >= at else value) for m in months}
    elif kind == "growth":
        rate = _f(spec, "rate")
        if not -0.5 <= rate <= 1.0:
            raise PatternError("rate is a monthly fraction between -0.5 and 1.0 (0.03 = +3% a month)")
        cap = _f(spec, "cap", required=False)
        for k, m in enumerate(months):
            v = value * (1.0 + rate) ** k
            if cap is not None:
                v = min(v, cap) if rate >= 0 else max(v, cap)
            out[m] = v
    elif kind == "ramp":
        end = _f(spec, "end_value")
        n = len(months)
        out = {m: (value if n == 1 else value + (end - value) * k / (n - 1)) for k, m in enumerate(months)}
    return {m: round(v, 2) for m, v in out.items()}


def check_bounds(target: str, values: dict[Month, float]) -> None:
    if target not in TARGETS:
        raise PatternError(f"target must be one of {', '.join(TARGETS)}")
    label, lo, hi = TARGETS[target]
    for m, v in values.items():
        if not math.isfinite(v) or v < lo or v > hi or (lo > 0 and v <= 0):
            raise PatternError(f"{label} for {m:%b %Y} would be {v:,.2f}; it must stay between {lo:,.2f} and {hi:,.0f}")


def describe(target: str, spec: dict, first: Month, last: Month) -> str:
    """Short note stored on each written cell, e.g. 'flat 350,000, Nov 2026 - Dec 2027'."""
    span = f"{first:%b %Y} - {last:%b %Y}"
    kind = spec.get("type")
    if kind == "flat":
        return f"flat {spec['value']:,.2f}, {span}"
    if kind == "step":
        return f"step {spec['value']:,.2f} to {spec['then_value']:,.2f} at {spec['step_month'][:7]}, {span}"
    if kind == "growth":
        return f"growth {spec['rate'] * 100:.1f}% a month from {spec['value']:,.2f}, {span}"
    return f"ramp {spec['value']:,.2f} to {spec['end_value']:,.2f}, {span}"
