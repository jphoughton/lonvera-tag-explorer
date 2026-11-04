"""Financing levers: defaults (the sheet's typed cells), merging with a scenario's `params['financing']`, and validation of a params patch.

Everything a scenario can change lives in `params['financing']` (scalars and short lists) or in `proj_series` under the `financing.` prefix
(monthly cells: principal paid, overrides). `DEFAULTS` is the "Current plan" as the sheet holds it, so an empty scenario still computes.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import date
from typing import Any, Callable

from ..interfaces import AXIS_END, AXIS_START, parse_month
from ...queries import ApiError

NS = "financing"

# Series keys (proj_series.line_key). Input rows of the grid use these as their row key.
S_PRINCIPAL = "financing.principal_paid"           # input: term-loan principal paid in the month (the sheet's typed column E)
S_NOI_BI = "financing.noi_before_inventory"        # override: replaces NOI + China COGS for one month
S_OPENING = "financing.opening_cash"               # override: replaces the carried-forward starting cash for one month
S_TAX = "financing.tax"                            # override: replaces the computed federal tax for one month
S_EQUITY = "financing.equity_payback"              # input: equity paid back in the month
SERIES_KEYS = (S_PRINCIPAL, S_NOI_BI, S_OPENING, S_TAX, S_EQUITY)

# Payout block: step-up bonus per scale tier, one column per payback month.
# P&L lines that are a cost of each new-customer order, read as a share of DTC revenue for the "after all variable costs" payback.
# Product cost (China COGS), fulfillment services, inbound shipping, payment processing and chargeback management.
LOADED_LINES = ["china_cogs", "fulfillment_services", "inbound_shipping", "payment_processing", "chargeback_management"]
LOADED_LABELS = {"china_cogs": "Product cost", "fulfillment_services": "Fulfillment", "inbound_shipping": "Inbound shipping",
                 "payment_processing": "Payment processing", "chargeback_management": "Chargeback management"}

PAYOUT_SCALES = [800_000, 850_000, 900_000, 950_000, 1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 1_500_000]

DEFAULTS: dict[str, Any] = {
    "close_month": "2026-09-01",                    # first month of buyer-owned cash; everything earlier belongs to the seller
    "debt": {"principal": 3_000_000.0, "rate": 0.15, "mode": "typed", "fixed_payment": 125_000.0},
    "seller_note": {"enabled": False, "principal": 1_500_000.0, "rate": 0.05, "first_payment": "2027-09-01", "installments": 3,
                    "ebitda_floor": None},
    "tax": {
        "rate": 0.21,
        "deduct_interest": False,
        "payments": [                                # each payment taxes the NOI of from..to, paid in `month`
            {"month": "2027-09-01", "from": "2026-09-01", "to": "2026-12-01", "label": "Sep-Dec 2026"},
            {"month": "2028-06-01", "from": "2027-01-01", "to": "2027-12-01", "label": "2027"},
            {"month": "2029-09-01", "from": "2028-01-01", "to": "2028-12-01", "label": "2028"},
        ],
    },
    "cash": {"opening": 0.0, "floor": 0.0},
    "payback": {"aov": None, "cac": None, "product_cost_pct": 0.1726, "fulfillment": 5.53, "max_age": 36, "curve": "live",
                "loaded_lines": list(LOADED_LINES), "loaded_months": 12, "loaded_rate": None},
    "matrix": {"target_month": 3, "basis": "cm", "margin": "product", "fixed_cost": 110_000.0,
               "spends": [200_000 + 100_000 * i for i in range(19)],
               "roas": [round(0.40 + 0.05 * i, 2) for i in range(13)]},
    "payout": {
        "media_spend": 800_000.0, "cpa": 90.0, "aov": 73.0,
        "lazar": 5_000.0, "designer": 3_000.0, "samples_count": 150, "sample_cost": 10.0,
        "creative_production_rate": 0.09, "cogs_rate": 0.14, "fulfillment_rate": 0.09,
        "repeat_source": "goal", "repeat_typed": [], "repeat_months": 7,
        "scales": PAYOUT_SCALES, "breakpoint": 1_000_000.0,
        "columns": [                                 # payback month, first scale tier that pays, bonus there, step-up per tier up to / above the breakpoint
            {"payback": 4, "first_scale": 850_000, "first_bonus": 2_000.0, "step_up": 0.15, "step_above": 0.10},
            {"payback": 3, "first_scale": 800_000, "first_bonus": 3_000.0, "step_up": 0.16, "step_above": 0.12},
            {"payback": 2, "first_scale": 800_000, "first_bonus": 3_500.0, "step_up": 0.15, "step_above": 0.10},
        ],
    },
}

# Where the defaults come from (shown next to the inputs; no build history, just the source cell).
SOURCES: dict[str, str] = {
    "debt.principal": "Term Debt Monthly Paydown B4",
    "debt.rate": "Term Debt Monthly Paydown B5",
    "debt.fixed_payment": "Term Debt Monthly Paydown B6 (used only in fixed-payment mode)",
    "tax.rate": "P&L row 120 (21% of NOI)",
    "payback.loaded_lines": "P&L lines read as a share of DTC revenue for the all-variable-costs payback",
    "payback.product_cost_pct": "Payback D11 (shared with the Spend plan page)",
    "payback.fulfillment": "Payback B12 (shared with the Spend plan page)",
    "matrix.fixed_cost": "Spend-ROAS Matrix B7",
    "payout.media_spend": "P&L F180", "payout.cpa": "P&L F182", "payout.aov": "P&L F183",
    "payout.lazar": "P&L G205", "payout.designer": "P&L G206", "payout.cogs_rate": "P&L G186", "payout.fulfillment_rate": "P&L G187",
    "payout.creative_production_rate": "P&L I208 (creative production cost, an expense)",
}


# ------------------------------------------------------------------------------------------------------------------ one store for the payback inputs

# The Spend plan page and this page edit the same three sheet cells (Payback D11, B12 and the 36-month table). The single store is
# params['drivers']['payback'] (read by the Spend plan, the pacing payback box and this module); `financing.payback.<key>` is only a legacy
# fallback for scenarios saved before the merge. financing key -> drivers key:
PAYBACK_SHARED = {"product_cost_pct": "product_cost_pct", "fulfillment": "fulfillment_per_order", "max_age": "horizon_months"}


def _shared_value(fkey: str, v: Any) -> float | int | None:
    """The drivers module's own ranges: a stored value outside them is ignored (it falls through to the legacy key or the default)."""
    x = opt_num(v)
    if x is None:
        return None
    if fkey == "product_cost_pct":
        return x if 0 <= x < 1 else None
    if fkey == "fulfillment":
        return x if 0 <= x <= 1000 else None
    return int(x) if 1 <= x <= 60 and x == int(x) else None


def payback_sources(scn_params: dict | None) -> dict[str, str]:
    """Where each shared payback input comes from in this scenario: 'drivers' (the single store), 'financing' (legacy copy) or 'default'."""
    params = scn_params or {}
    drv = (params.get("drivers") or {}).get("payback") if isinstance(params.get("drivers"), dict) else None
    fin = (params.get(NS) or {}).get("payback") if isinstance(params.get(NS), dict) else None
    out = {}
    for fkey, dkey in PAYBACK_SHARED.items():
        if isinstance(drv, dict) and _shared_value(fkey, drv.get(dkey)) is not None:
            out[fkey] = "drivers"
        elif isinstance(fin, dict) and fkey in fin and fin.get(fkey) is not None:
            out[fkey] = "financing"
        else:
            out[fkey] = "default"
    return out


def split_payback_patch(patch: dict) -> tuple[dict, dict]:
    """A financing merge patch -> (financing patch, drivers patch): the shared payback inputs move to params['drivers']['payback'] and the legacy
    financing copies are removed, so every page reads one value. A null clears the shared input (back to the default)."""
    pb = patch.get("payback")
    if not isinstance(pb, dict) or not any(k in pb for k in PAYBACK_SHARED):
        return patch, {}
    rest = {k: v for k, v in pb.items() if k not in PAYBACK_SHARED}
    fin = {**patch, "payback": {**rest, **{k: None for k in PAYBACK_SHARED}}}
    return fin, {"payback": {PAYBACK_SHARED[k]: pb[k] for k in PAYBACK_SHARED if k in pb}}


# ------------------------------------------------------------------------------------------------------------------ merge

def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def effective(scn_params: dict | None) -> dict:
    """DEFAULTS, then the scenario's `financing` params (the legacy copies of the shared payback inputs included), then the single store
    params['drivers']['payback'] for the shared inputs."""
    own = (scn_params or {}).get(NS)
    out = deep_merge(DEFAULTS, own if isinstance(own, dict) else {})
    drv = (scn_params or {}).get("drivers")
    drv = drv.get("payback") if isinstance(drv, dict) else None
    if isinstance(drv, dict):
        for fkey, dkey in PAYBACK_SHARED.items():
            v = _shared_value(fkey, drv.get(dkey))
            if v is not None:
                out["payback"][fkey] = v
    return out


# ------------------------------------------------------------------------------------------------------------------ tolerant readers

def num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return float(v)


def opt_num(v: Any) -> float | None:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) else float(v)


def to_month(v: Any, default: date | None = None) -> date | None:
    try:
        return parse_month(v)
    except (ValueError, TypeError):
        return default


# ------------------------------------------------------------------------------------------------------------------ validation

def _fail(path: str, msg: str):
    raise ApiError(400, f"{NS}.{path}: {msg}")


def _number(lo: float | None = None, hi: float | None = None, *, nullable: bool = False) -> Callable[[str, Any], None]:
    def check(path: str, v: Any) -> None:
        if v is None and nullable:
            return
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            _fail(path, "must be a number")
        if lo is not None and v < lo:
            _fail(path, f"must be at least {lo:g}")
        if hi is not None and v > hi:
            _fail(path, f"must be at most {hi:g}")
    return check


def _integer(lo: int, hi: int) -> Callable[[str, Any], None]:
    def check(path: str, v: Any) -> None:
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            _fail(path, f"must be a whole number from {lo} to {hi}")
    return check


def _boolean(path: str, v: Any) -> None:
    if not isinstance(v, bool):
        _fail(path, "must be true or false")


def _choice(*opts: str) -> Callable[[str, Any], None]:
    def check(path: str, v: Any) -> None:
        if v not in opts:
            _fail(path, "must be one of " + ", ".join(opts))
    return check


def _month(path: str, v: Any) -> None:
    try:
        parse_month(v)
    except ValueError as e:
        _fail(path, f"{e} (first of a month inside {AXIS_START:%Y-%m} .. {AXIS_END:%Y-%m})")


def _number_list(lo: float, hi: float, max_len: int, *, ascending: bool = False, min_len: int = 0) -> Callable[[str, Any], None]:
    def check(path: str, v: Any) -> None:
        if not isinstance(v, list) or not min_len <= len(v) <= max_len:
            _fail(path, f"must be a list of {min_len} to {max_len} numbers")
        for i, x in enumerate(v):
            _number(lo, hi)(f"{path}[{i}]", x)
        if ascending and any(b <= a for a, b in zip(v, v[1:])):
            _fail(path, "must be strictly ascending")
    return check


def _line_keys(path: str, v: Any) -> None:
    if not isinstance(v, list) or not 1 <= len(v) <= 12:
        _fail(path, "must be a list of 1 to 12 P&L line keys")
    for i, k in enumerate(v):
        if not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", k):
            _fail(f"{path}[{i}]", "must be a P&L line key such as fulfillment_services")


def _tax_payments(path: str, v: Any) -> None:
    if not isinstance(v, list) or len(v) > 24:
        _fail(path, "must be a list of at most 24 payments")
    for i, p in enumerate(v):
        w = f"{path}[{i}]"
        if not isinstance(p, dict):
            _fail(w, "must be an object {month, from, to, label?}")
        extra = set(p) - {"month", "from", "to", "label"}
        if extra:
            _fail(w, f"unknown field {sorted(extra)[0]!r}")
        for k in ("month", "from", "to"):
            if k not in p:
                _fail(w, f"{k} is required")
            _month(f"{w}.{k}", p[k])
        if p["from"] > p["to"]:
            _fail(w, "from must not be after to")
        if "label" in p and (not isinstance(p["label"], str) or len(p["label"]) > 60):
            _fail(f"{w}.label", "must be text of at most 60 characters")


def _payout_columns(path: str, v: Any) -> None:
    if not isinstance(v, list) or not 1 <= len(v) <= 6:
        _fail(path, "must be a list of 1 to 6 payback columns")
    seen = set()
    for i, c in enumerate(v):
        w = f"{path}[{i}]"
        if not isinstance(c, dict):
            _fail(w, "must be an object")
        need = {"payback", "first_scale", "first_bonus", "step_up", "step_above"}
        if set(c) != need:
            _fail(w, "must have exactly " + ", ".join(sorted(need)))
        _integer(0, 36)(f"{w}.payback", c["payback"])
        _number(0, 1e9)(f"{w}.first_scale", c["first_scale"])
        _number(0, 1e9)(f"{w}.first_bonus", c["first_bonus"])
        _number(-0.5, 5)(f"{w}.step_up", c["step_up"])
        _number(-0.5, 5)(f"{w}.step_above", c["step_above"])
        if c["payback"] in seen:
            _fail(w, "payback months must be distinct")
        seen.add(c["payback"])


SCHEMA: dict[str, Any] = {
    "close_month": _month,
    "debt": {"principal": _number(0, 1e10), "rate": _number(0, 1), "mode": _choice("typed", "fixed_payment"), "fixed_payment": _number(0, 1e9)},
    "seller_note": {"enabled": _boolean, "principal": _number(0, 1e10), "rate": _number(0, 1), "first_payment": _month,
                    "installments": _integer(1, 20), "ebitda_floor": _number(0, 1e10, nullable=True)},
    "tax": {"rate": _number(0, 1), "deduct_interest": _boolean, "payments": _tax_payments},
    "cash": {"opening": _number(-1e10, 1e10), "floor": _number(-1e10, 1e10)},
    "payback": {"aov": _number(0.01, 1e6, nullable=True), "cac": _number(0, 1e6, nullable=True), "product_cost_pct": _number(0, 0.99),
                "fulfillment": _number(0, 1000), "max_age": _integer(1, 60), "curve": _choice("live", "goal"),
                "loaded_lines": _line_keys, "loaded_months": _integer(1, 36), "loaded_rate": _number(0, 0.99, nullable=True)},
    "matrix": {"target_month": _integer(0, 60), "basis": _choice("cm", "loaded"), "margin": _choice("product", "loaded"), "fixed_cost": _number(0, 1e10),
               "spends": _number_list(1, 1e10, 40, min_len=1), "roas": _number_list(0.01, 100, 40, min_len=1)},
    "payout": {"media_spend": _number(0, 1e10), "cpa": _number(0.01, 1e6), "aov": _number(0.01, 1e6), "lazar": _number(0, 1e9),
               "designer": _number(0, 1e9), "samples_count": _integer(0, 100000), "sample_cost": _number(0, 1e6),
               "creative_production_rate": _number(0, 1), "cogs_rate": _number(0, 1), "fulfillment_rate": _number(0, 1),
               "repeat_source": _choice("goal", "live", "typed"), "repeat_typed": _number_list(0, 10, 36),
               "repeat_months": _integer(1, 36), "scales": _number_list(1, 1e10, 24, ascending=True, min_len=1),
               "breakpoint": _number(0, 1e10), "columns": _payout_columns},
}


def validate_patch(patch: Any, schema: dict | None = None, prefix: str = "") -> dict:
    """Validate a merge patch for `params['financing']`. A null leaf removes that key (back to the base scenario or the default)."""
    schema = SCHEMA if schema is None else schema
    if not isinstance(patch, dict):
        raise ApiError(400, f"{NS}{('.' + prefix) if prefix else ''}: must be an object")
    for k, v in patch.items():
        path = f"{prefix}.{k}" if prefix else k
        if k not in schema:
            _fail(path, "unknown input")
        rule = schema[k]
        if v is None:
            continue
        if isinstance(rule, dict):
            validate_patch(v, rule, path)
        else:
            rule(path, v)
    return patch


def validate_params(params: Any) -> None:
    """Validate this module's whole params namespace (what `params['financing']` holds, or any merge patch of it). Raises ApiError(400) naming the input.
    Registered as the module's hook so the generic PATCH /scenarios path refuses the same values as PUT /financing/inputs."""
    if params is None:
        return
    validate_patch(params)
