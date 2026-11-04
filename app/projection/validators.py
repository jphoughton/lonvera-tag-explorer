"""One place that decides what may be saved: series cells (PUT /series, the pattern endpoints) and scenario params (PATCH /scenarios).

Every write path goes through `check_series` / `check_params`, so a module's rules apply no matter which endpoint carries the edit.

The core enforces generic sanity only, by explicit lists, never by guessing from a name (a `rate` can be a percentage or units per day):
  series   the key must start with a known prefix (a module key, or one added with `register_prefix`); calculated keys (READ_ONLY: the P&L's totals, NOI,
           ratios) are read-only; no value of 1e11 or more (the stray-paste class: 500000350000); keys under NON_NEGATIVE_PREFIXES / NON_NEGATIVE_KEYS cannot
           be negative; RANGES gives explicit bounds per key (drivers.spend_total 0..50,000,000; drivers.nc_cpa and drivers.nc_aov above 0, at most 1,000);
           a spend-plan input (kind 'input') cannot be typed on a closed month, kind 'override' can. Deleting a cell is always allowed.
  params   the group must be a module key; numbers must be finite and below 1e12.
Everything else is the module's rule. A key or group a module has no rule for is accepted. Add a bound with `register_range`, a sign rule with
`register_non_negative`.

Module hooks (called after the core rules; raise ApiError(400, plain message)). A hook is an attribute of the module's MODULE, of its package
(app.projection.<key>) or of its `params` / `validation` submodule; the core passes the values the function's parameters are named for, so declare only
what you need:
  validate_series(conn, scenario, items)                 items = the parsed series.Item list of the request (line_key, month, value | None, kind, note)
  validate_params(params, conn=None)                     params = the module's whole group after the patch; called for the module whose key is the group
                                                         (also accepted: (conn, scenario, namespace, params))
"""
from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal
from typing import Any

from ..queries import ApiError
from . import clock, registry
from .interfaces import MODULE_CATALOG, Scenario

ABSURD = Decimal("1e11")
PARAM_ABSURD = 1e12
EXTRA_PREFIXES: set[str] = set()                                 # series key prefixes besides the module keys (register_prefix)

READ_ONLY = re.compile(r"^pnl\.(total_[a-z0-9_]+|noi|noi_[a-z0-9_]+|gross_profit|gross_profit_[a-z0-9_]+|ttm_[a-z0-9_]+|ftm_[a-z0-9_]+|[a-z0-9_]+_(pct|mom|yoy|mom_6m|yoy_6m))$")
NON_NEGATIVE_PREFIXES: list[str] = ["drivers.", "inventory_demand.", "inventory_supply."]
NON_NEGATIVE_KEYS: set[str] = {"financing.principal_paid", "financing.equity_payback"}
# (key regex, low, high, low_is_exclusive, message)
RANGES: list[tuple[re.Pattern, float, float, bool, str]] = [
    (re.compile(r"^drivers\.(spend_total|ad_spend)$"), 0, 50_000_000, False, "must stay between 0 and 50,000,000"),
    (re.compile(r"^drivers\.(nc_cpa|nc_aov)$"), 0, 1000, True, "must be above 0 and at most 1,000"),
]
_PLAN_PREFIX = "drivers."


def register_range(key_pattern: str, low: float, high: float, *, low_exclusive: bool = False, message: str | None = None) -> None:
    """Bound a class of series keys (a regex over the whole key)."""
    RANGES.append((re.compile(key_pattern), low, high, low_exclusive, message or f"must be between {low:g} and {high:g}"))


def register_non_negative(prefix_or_key: str) -> None:
    if prefix_or_key.endswith("."):
        NON_NEGATIVE_PREFIXES.append(prefix_or_key)
    else:
        NON_NEGATIVE_KEYS.add(prefix_or_key)


def register_prefix(prefix: str) -> None:
    EXTRA_PREFIXES.add(prefix)


def known_prefixes() -> set[str]:
    return set(MODULE_CATALOG) | EXTRA_PREFIXES


def _month_label(m: date) -> str:
    return m.strftime("%b %Y")


def _first_open_month(today: date) -> date:
    return today.replace(day=1)


def _series_problem(it, today: date) -> str | None:
    key, v = it.line_key, it.value
    prefix = key.split(".", 1)[0]
    if "." not in key or prefix not in known_prefixes():
        return f"{key!r} is not a projection line (it must start with one of: {', '.join(sorted(known_prefixes()))})"
    if READ_ONLY.match(key):
        return f"{key} is calculated, not typed; change what it is calculated from"
    if abs(v) >= ABSURD:
        return f"{v:,} is out of range (a stray paste?)"
    for pattern, low, high, low_exclusive, message in RANGES:
        if pattern.match(key) and not ((v > low if low_exclusive else v >= low) and v <= high):
            return message
    if v < 0 and (key in NON_NEGATIVE_KEYS or any(key.startswith(p) for p in NON_NEGATIVE_PREFIXES)):
        return "cannot be negative"
    if key.startswith(_PLAN_PREFIX) and it.kind == "input" and it.month < _first_open_month(today):
        return (f"{_month_label(it.month)} is a closed month; plan inputs start in {_month_label(_first_open_month(today))} "
                "(use kind 'override' to correct a closed month)")
    return None


def check_series(conn, scn: Scenario, items: list, today: date | None = None) -> None:
    """Reject the whole request, naming the first bad cell (400)."""
    today = today or clock.today_pt()
    for i, it in enumerate(items):
        if it.value is None:
            continue                                            # a delete clears a cell: always allowed
        problem = _series_problem(it, today)
        if problem:
            raise ApiError(400, f"items[{i}] {it.line_key} {_month_label(it.month)}: {problem}")
    for _key, fn in registry.hooks("validate_series"):
        registry.run_hook(fn, conn=conn, scenario=scn, items=items)


# ---------------------------------------------------------------------------------------------------------------- params

def _walk(d: dict, path: tuple = ()):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _walk(v, path + (k,))
        else:
            yield path + (k,), v


def _param_problem(path: tuple, v: Any) -> str | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or abs(v) >= PARAM_ABSURD:
        return f"{'.'.join(path)}: {v} is out of range"
    return None


def check_params(conn, scn: Scenario, patch: dict, merged: dict) -> None:
    """`patch` = what the request changes, `merged` = the scenario's whole params after it."""
    for ns, change in patch.items():
        if ns not in MODULE_CATALOG:
            if change is None:
                continue                                        # removing a group that no module owns is harmless
            raise ApiError(400, f"{ns!r} is not a settings group (one of: {', '.join(MODULE_CATALOG)})")
        if change is None:
            continue
        group = merged.get(ns) or {}
        for path, v in _walk(group, (ns,)):
            problem = _param_problem(path, v)
            if problem:
                raise ApiError(400, problem)
        fn = registry.hook(ns, "validate_params")
        if fn is not None:
            registry.run_hook(fn, conn=conn, scenario=scn, namespace=ns, params=group)
