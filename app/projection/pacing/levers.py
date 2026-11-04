"""Pacing variables: every number or rule that drives the pacing outputs, as a named, scenario-aware lever with the sheet's value as its default.

Pacing owns the levers in `params['pacing']`. Four inputs belong to other modules and are read through (and edited through) the namespace that owns them:
the sales-tax estimate (`drivers.sales_tax_rate`, P&L row 21) and the Payback box inputs, which have ONE store, `params['drivers']['payback']`
(product_cost_pct, fulfillment_per_order, horizon_months; shared with the Spend plan and Cash & debt pages). `financing.payback.*` is read only as a legacy
fallback for scenarios restored from an older version. A lever set to its default is never stored: PUT with null removes the key. Validation lives here, one rule per lever.

Feed classification rules (product patterns, the testing keyword) apply when the ETL loads a day, not per scenario: they are global settings stored in
`proj_pacing_setting` (see etl/projection_pacing.py) and listed here as `scope: feed`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...queries import ApiError

NS = "pacing"
DRIVER_KEYS = ("total_rev", "dtc_rev", "amazon_rev", "fixed")


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    unit: str                           # fraction | days | weeks | months | dollars | month | rows | map
    default: Any
    source: str                         # where the sheet holds it (tab, cell or rule)
    path: tuple[str, ...]               # location inside scenario params
    owner: str = "pacing"
    lo: float | None = None
    hi: float | None = None
    integer: bool = False
    help: str = ""
    fallbacks: tuple[tuple[str, ...], ...] = ()      # other params paths read when `path` is not set


SPECS: tuple[Spec, ...] = (
    Spec("pace_good", "On-pace threshold", "fraction", 1.0, "Pacing brief: green at 100% of goal or more (the sheet shows the ratio without a color)", (NS, "pace_good"), lo=0.5, hi=2.0,
         help="Revenue and profit at or above this share of goal read as on pace."),
    Spec("pace_warn", "Watch threshold", "fraction", 0.90, "Pacing brief: amber from 90% to 100%, red below 90%", (NS, "pace_warn"), lo=0.1, hi=1.0,
         help="Revenue and profit below this share of goal read as well behind."),
    Spec("cost_warn", "Over-plan threshold for costs", "fraction", 1.10, "Pacing brief: costs read inversely (green at or under plan, amber up to 10% over)", (NS, "cost_warn"), lo=1.0, hi=3.0,
         help="Costs and cost per customer above this share of goal read as well over plan."),
    Spec("early_days", "Early-month notice, days", "days", 5, "Automated Pacing: the projection runs hot in the first days (renewals land on day 1)", (NS, "early_days"), lo=0, hi=15,
         integer=True, help="With fewer full days than this the page says projections swing."),
    Spec("cpa_window_days", "Cost-per-customer context window, days", "days", 28, "Skeptic review: 28-day realized cost per new customer shown beside the month's (no sheet cell)", (NS, "cpa_window_days"),
         lo=7, hi=90, integer=True, help="Days behind the 'last N days' cost per new customer shown beside the month-to-date figure."),
    Spec("last_days", "Recent-average window, days", "days", 7, "Automated Pacing column D: TODAY()-7 to TODAY()-1", (NS, "last_days"), lo=1, hi=31, integer=True,
         help="Days in the 'last 7-day average' column."),
    Spec("data_lag_days", "Days between today and the last complete day", "days", 1, "Automated Pacing B54: DAY(TODAY())-1 (feeds run through yesterday)", (NS, "data_lag_days"),
         lo=0, hi=3, integer=True, help="Days elapsed = days of the month up to today minus this lag."),
    Spec("rates_month", "Month whose cost rates price every day", "month", None, "P&L column B (trailing-window rates); empty = the month being paced", (NS, "rates_month"),
         help="Format YYYY-MM. Cost rates for the daily EBITDA estimate come from this month's plan."),
    Spec("below_ebitda_rows", "P&L rows below EBITDA", "rows", [62, 94], "Automated Pacing G31/G32: marketing influencers (row 62) and uncategorized expense (row 94)", (NS, "below_ebitda_rows"),
         help="Sheet rows added back to net operating income to get the EBITDA goal."),
    Spec("cost_drivers", "Cost line drivers", "map", {}, "Automated Pacing rows 13-29: each cost is a rate on Amazon, DTC or total revenue, or fixed dollars", (NS, "cost_drivers"),
         help="Overrides by line key (amazon_fees_var, amazon_fees_fixed, cogs_direct, fulfillment, inbound, payment, mkt_fixed, mkt_software, other_biz, headcount, ovh_fixed, ovh_dtc, ovh_total)."),
    Spec("payback_bad_months", "Payback well behind, months over goal", "months", 2, "Page convention (the sheet shows the two payback months without a color)", (NS, "payback_bad_months"), lo=0, hi=36,
         integer=True, help="Payback this many months or more beyond the goal reads as well behind; any lateness below it reads as behind."),
    Spec("stale_bad_days", "Feed stale after, days", "days", 5, "DESIGN.md: a feed over 2 days old is amber, over 5 days crimson", (NS, "stale_bad_days"), lo=1, hi=30, integer=True,
         help="Days behind yesterday at which a data feed chip turns from amber to red."),
    Spec("rollup_days", "Daily view window, days", "days", 60, "Pacing - Daily (180d) shows up to 180 days", (NS, "rollup_days"), lo=7, hi=400, integer=True, help="Days shown by default on the day grain."),
    Spec("rollup_weeks", "Weekly view window, weeks", "weeks", 26, "Rollup - Weekly (WoW) lists every ISO week since Jan 2025", (NS, "rollup_weeks"), lo=4, hi=110, integer=True,
         help="Weeks shown by default on the week grain."),
    Spec("sales_tax_rate", "Sales-tax estimate, share of DTC revenue", "fraction", 0.07, "P&L row 21: =-(DTC revenue x 0.07); edited with the spend plan", ("drivers", "sales_tax_rate"),
         owner="drivers", lo=0.0, hi=0.5, help="Deducted from DTC revenue in revenue pacing and the plan-basis revenue."),
    Spec("product_cost_pct", "Product cost, share of order value", "fraction", 0.1726, "Payback!D11 (typed)", ("drivers", "payback", "product_cost_pct"), owner="drivers", lo=0.0, hi=0.99,
         fallbacks=(("financing", "payback", "product_cost_pct"),), help="Payback box: first-order margin = order value x (1 - this) - fulfillment."),
    Spec("fulfillment_per_order", "Fulfillment per order", "dollars", 5.53, "Payback!B12 (typed)", ("drivers", "payback", "fulfillment_per_order"), owner="drivers", lo=0.0, hi=1000.0,
         fallbacks=(("financing", "payback", "fulfillment"),), help="Payback box: dollars of fulfillment cost per first order."),
    Spec("payback_horizon", "Payback horizon, months", "months", 36, "Automated Pacing M9: SCAN over Curves rows 4-39 (36 ages)", ("drivers", "payback", "horizon_months"), owner="drivers", lo=1, hi=60,
         integer=True, fallbacks=(("financing", "payback", "max_age"),), help="Months of repeat curve counted before the payback reads 'n+'."),
)
BY_KEY = {s.key: s for s in SPECS}


@dataclass
class Levers:
    pace_good: float = 1.0
    pace_warn: float = 0.90
    cost_warn: float = 1.10
    early_days: int = 5
    last_days: int = 7
    cpa_window_days: int = 28
    data_lag_days: int = 1
    rates_month: date | None = None
    below_ebitda_rows: tuple[int, ...] = (62, 94)
    cost_drivers: dict = field(default_factory=dict)
    payback_bad_months: int = 2
    stale_bad_days: int = 5
    rollup_days: int = 60
    rollup_weeks: int = 26
    sales_tax_rate: float = 0.07
    product_cost_pct: float = 0.1726
    fulfillment_per_order: float = 5.53
    payback_horizon: int = 36
    explicit_tax_rate: float | None = None          # set only when the scenario states a tax rate: pacing then applies it to measured DTC revenue


def _dig(params: dict | None, path: tuple[str, ...]):
    cur: Any = params or {}
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _read(params: dict | None, spec: Spec):
    for path in (spec.path, *spec.fallbacks):
        v = _dig(params, path)
        if v is not None:
            return v
    return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and abs(v) != float("inf") else None


def effective(params: dict | None) -> dict[str, Any]:
    """Every lever's effective value (stored value, else the sheet default)."""
    out: dict[str, Any] = {}
    for s in SPECS:
        v = _read(params, s)
        out[s.key] = copy.deepcopy(s.default) if v is None else v
    return out


def parse(params: dict | None) -> Levers:
    """A Levers object from scenario params; anything malformed falls back to the lever's default (validation happens on write)."""
    e = effective(params)
    L = Levers()
    for s in SPECS:
        v = e[s.key]
        if s.key == "rates_month":
            try:
                L.rates_month = date.fromisoformat(str(v)[:7] + "-01") if v else None
            except ValueError:
                L.rates_month = None
        elif s.key == "below_ebitda_rows":
            L.below_ebitda_rows = tuple(int(x) for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)) if isinstance(v, (list, tuple)) else tuple(s.default)
        elif s.key == "cost_drivers":
            L.cost_drivers = {k: d for k, d in v.items() if d in DRIVER_KEYS} if isinstance(v, dict) else {}
        else:
            n = _num(v)
            setattr(L, s.key, (int(n) if s.integer else n) if n is not None else s.default)
    explicit = _dig(params, ("drivers", "sales_tax_rate"))
    L.explicit_tax_rate = _num(explicit)
    return L


def describe(params: dict | None) -> list[dict]:
    """The lever list for the page and the central Variables page."""
    e = effective(params)
    out = []
    for s in SPECS:
        stored = _read(params, s) is not None
        out.append({"key": s.key, "label": s.label, "unit": s.unit, "value": e[s.key], "default": s.default, "is_default": e[s.key] == s.default or not stored,
                    "source": s.source, "owner": s.owner, "path": ".".join(s.path), "min": s.lo, "max": s.hi, "help": s.help})
    return out


def validate_patch(raw: Any) -> dict:
    """A levers patch ({key: value | None}) -> a params merge patch ({namespace: {...}}). ApiError 400 for an unknown key or a bad value. None removes the stored value."""
    if not isinstance(raw, dict) or not raw:
        raise ApiError(400, "levers must be a non-empty object")
    patch: dict = {}
    for key, v in raw.items():
        s = BY_KEY.get(key)
        if s is None:
            raise ApiError(400, f"unknown lever {key!r}")
        val = _check(s, v)
        node = patch
        for p in s.path[:-1]:
            node = node.setdefault(p, {})
        node[s.path[-1]] = val
    return patch


def _check(s: Spec, v: Any):
    if v is None:
        return None
    if s.key == "rates_month":
        try:
            d = date.fromisoformat(str(v)[:7] + "-01")
        except ValueError:
            raise ApiError(400, "rates_month must be a month like 2026-11") from None
        if not (date(2025, 1, 1) <= d <= date(2029, 12, 1)):
            raise ApiError(400, "rates_month must lie between 2025-01 and 2029-12")
        return d.strftime("%Y-%m")
    if s.key == "below_ebitda_rows":
        if not isinstance(v, list) or any(isinstance(x, bool) or not isinstance(x, int) or not 9 <= x <= 101 for x in v) or len(set(v)) != len(v):
            raise ApiError(400, "below_ebitda_rows must list distinct P&L sheet rows (9 to 101)")
        return list(v)
    if s.key == "cost_drivers":
        from .targets import LINE_BY_KEY                                       # lazy: targets imports nothing from here
        if not isinstance(v, dict) or any(k not in LINE_BY_KEY or d not in DRIVER_KEYS for k, d in v.items()):
            raise ApiError(400, f"cost_drivers maps a line key ({', '.join(LINE_BY_KEY)}) to one of {', '.join(DRIVER_KEYS)}")
        return dict(v)
    n = _num(v)
    if n is None:
        raise ApiError(400, f"{s.key} must be a number")
    if s.integer and n != int(n):
        raise ApiError(400, f"{s.key} must be a whole number")
    if s.lo is not None and n < s.lo or s.hi is not None and n > s.hi:
        raise ApiError(400, f"{s.key} must be between {s.lo} and {s.hi}")
    return int(n) if s.integer else n


def cross_check(values: dict[str, Any]) -> None:
    """Rules that span levers: the watch threshold must sit below the on-pace threshold."""
    if values["pace_warn"] > values["pace_good"]:
        raise ApiError(400, "pace_warn must not be above pace_good")


def rates_month(L: Levers, today: date) -> date:
    """The month whose plan rates price every day of the EBITDA estimate: the lever, else the month being paced (today's)."""
    return L.rates_month or today.replace(day=1)


def validate_params(params: dict | None) -> None:
    """Core hook for PATCH /scenarios: the `pacing` group may hold only pacing variables with valid values (the same rules as PUT /levers)."""
    known = {s.key: s for s in SPECS if s.path[0] == NS}
    values = {}
    for key, v in (params or {}).items():
        spec = known.get(key)
        if spec is None:
            raise ApiError(400, f"pacing.{key} is not a pacing variable")
        if v is not None:
            values[key] = _check(spec, v)
    eff = {**{k: s.default for k, s in known.items()}, **values}
    cross_check({"pace_good": eff["pace_good"], "pace_warn": eff["pace_warn"]})
