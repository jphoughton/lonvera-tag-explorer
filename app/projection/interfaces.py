"""Interchange types for the projection engine (contract v1): month axis, module results, grid payload, Module.

Every module result is a plain dataclass of `Series` (sparse dict month -> float) so modules can be developed and tested alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

Month = date                                   # always the first of the month
Series = dict[Month, float]                    # sparse is fine; the engine reads a missing month as 0 unless a module says otherwise

AXIS_START = date(2025, 1, 1)
AXIS_LEN = 60
MONTHS: tuple[Month, ...] = tuple(date(AXIS_START.year + (AXIS_START.month - 1 + i) // 12, (AXIS_START.month - 1 + i) % 12 + 1, 1)
                                  for i in range(AXIS_LEN))                       # 2025-01 .. 2029-12
AXIS_END = MONTHS[-1]


# ---------------------------------------------------------------------------------------------------------------- month helpers

def month_index(d: date) -> int:
    """Index 0..59 of a first-of-month date on the axis. ValueError for any other date."""
    if not isinstance(d, date) or d.day != 1:
        raise ValueError(f"not a first-of-month date: {d!r}")
    i = (d.year - AXIS_START.year) * 12 + d.month - AXIS_START.month
    if not 0 <= i < AXIS_LEN:
        raise ValueError(f"month outside the {AXIS_START:%Y-%m} .. {AXIS_END:%Y-%m} axis: {d.isoformat()}")
    return i


def month_floor(d: date) -> Month:
    """First day of the month containing d."""
    return d.replace(day=1)


def add_months(d: date, n: int) -> Month:
    """First-of-month date n months after (or before, n < 0) the month containing d. Not limited to the axis."""
    k = d.year * 12 + d.month - 1 + n
    return date(k // 12, k % 12 + 1, 1)


def parse_month(s: Any) -> Month:
    """'YYYY-MM-DD' (first of month, on the axis) -> date. ValueError otherwise."""
    if not isinstance(s, str):
        raise ValueError("month must be an ISO date string")
    try:
        d = date.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"bad month {s!r}") from e
    month_index(d)
    return d


def is_actual(month: date, actual_through: date | None) -> bool:
    """True when `month` is covered by loaded actuals (actual_through = last month holding actual data)."""
    return actual_through is not None and month <= actual_through


def status_for(actual_through: date | None, months=MONTHS) -> list[str]:
    return ["actual" if is_actual(m, actual_through) else "projected" for m in months]


def series_to_list(s: Series | None, months=MONTHS, missing: float | None = 0.0) -> list[float | None]:
    s = s or {}
    return [s.get(m, missing) for m in months]


def zero_series(months=MONTHS) -> Series:
    return {m: 0.0 for m in months}


def _clean_num(v: Any) -> Any:
    """JSON has no NaN/Infinity: they become null."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


# ---------------------------------------------------------------------------------------------------------------- scenario

@dataclass
class Scenario:
    id: int
    slug: str
    name: str
    params: dict = field(default_factory=dict)      # params[<module key>] = that module's levers (engine passes the effective, base-merged view)
    base_slug: str | None = None
    version: int = 1
    is_default: bool = False


# ---------------------------------------------------------------------------------------------------------------- module results

@dataclass
class DriverResult:                                 # module `drivers`
    spend_total: Series = field(default_factory=dict)
    spend_meta: Series = field(default_factory=dict)
    spend_google: Series = field(default_factory=dict)
    spend_amazon_ads: Series = field(default_factory=dict)
    spend_tiktok: Series = field(default_factory=dict)
    nc_cpa: Series = field(default_factory=dict)
    nc_aov: Series = field(default_factory=dict)
    nc_orders: Series = field(default_factory=dict)
    nc_rev: Series = field(default_factory=dict)
    nc_roas: Series = field(default_factory=dict)
    amazon_rev: Series = field(default_factory=dict)
    tiktok_rev: Series = field(default_factory=dict)
    sales_tax: Series = field(default_factory=dict)
    actual_through: Month | None = None             # last month with actual data


@dataclass
class CohortResult:                                 # module `cohorts`
    curve_live: list[float] = field(default_factory=list)       # ages 1..60
    curve_goal: list[float] = field(default_factory=list)
    lifetime_multiple: float = 0.0
    repeat_rev: Series = field(default_factory=dict)            # modeled repeat $ per month, all cohorts incl. forward
    repeat_rev_actual: Series = field(default_factory=dict)
    cohort_rows: list[dict] = field(default_factory=list)


@dataclass
class PnlResult:                                    # module `pnl`
    lines: dict[str, Series] = field(default_factory=dict)      # keys = LINE_KEYS in app/projection/pnl/lines.py
    fy: dict[int, dict[str, float]] = field(default_factory=dict)
    ttm: Series = field(default_factory=dict)
    ftm: Series = field(default_factory=dict)
    ev: dict = field(default_factory=dict)


@dataclass
class DemandResult:                                 # module `inventory_demand`
    units: dict[tuple[str, str], Series] = field(default_factory=dict)   # (location, product) -> units per month; locations revamp|amazon|intl

    def get(self, location: str, product: str) -> Series:
        return self.units.get((location, product), {})


@dataclass
class InventoryResult:                              # module `inventory_supply`
    outlay: Series = field(default_factory=dict)                # cash by month
    value: Series = field(default_factory=dict)
    burn: dict = field(default_factory=dict)
    reorder: list[dict] = field(default_factory=list)


@dataclass
class FinancingResult:                              # module `financing`
    lines: dict[str, Series] = field(default_factory=dict)      # interest, principal, balance, tax, equity, cash_after_inventory, ending_cash


# ---------------------------------------------------------------------------------------------------------------- grid payload
# The shared grid's own vocabulary (docs/projection/MODULE_ui_shell.md). The names modules used before it existed are accepted as aliases and
# emitted as the grid names, so Table/Row output can go to the grid unchanged.

KINDS = ("input", "calc", "subtotal", "total", "ratio", "header")           # input: yellow, editable when input=True; ratio: italic; header: section band, no values
FMTS = ("usd", "usd0", "pct", "num", "x", "text", "date")                    # usd $1,234.50; usd0 $1,235; pct fraction (0.125 -> 12.5%); num 1,234; x 1.20x
AGGS = ("sum", "avg", "last", "none")                                        # how the FY column combines the months
KIND_ALIASES = {"line": "calc", "memo": "ratio"}
FMT_ALIASES: dict[str, tuple[str, int | None]] = {"money": ("usd0", None), "money2": ("usd", None), "int": ("num", 0), "num2": ("num", 2), "pct1": ("pct", 1)}
MAX_DIGITS = 6


def normalize_kind(kind: str) -> str:
    kind = KIND_ALIASES.get(kind, kind)
    if kind not in KINDS:
        raise ValueError(f"row kind must be one of {KINDS} (or the older {tuple(KIND_ALIASES)}), got {kind!r}")
    return kind


def normalize_fmt(fmt: str) -> tuple[str, int | None]:
    """(grid fmt, digits implied by an older name or None)."""
    if fmt in FMT_ALIASES:
        return FMT_ALIASES[fmt]
    if fmt not in FMTS:
        raise ValueError(f"row fmt must be one of {FMTS} (or the older {tuple(FMT_ALIASES)}), got {fmt!r}")
    return fmt, None


@dataclass
class Row:
    key: str
    label: str
    values: list[float | None] = field(default_factory=list)
    kind: str | None = None                          # default: 'input' for an editable row, else 'calc'
    fmt: str = "usd0"
    input: bool = False                              # True = editable cell (PUT /api/projection/series)
    group: str | None = None                         # header key that owns the row (default: the preceding header)
    note: str | None = None                          # tooltip next to the label; put the source of an input here
    agg: str | None = None                           # FY column: sum | avg | last | none
    digits: int | None = None
    indent: int | None = None

    def __post_init__(self) -> None:
        self.kind = normalize_kind(self.kind) if self.kind is not None else ("input" if self.input else "calc")
        self.fmt, implied = normalize_fmt(self.fmt)
        if self.digits is None:
            self.digits = implied
        if self.agg is not None and self.agg not in AGGS:
            raise ValueError(f"row agg must be one of {AGGS}, got {self.agg!r}")
        if self.digits is not None and (isinstance(self.digits, bool) or not isinstance(self.digits, int) or not 0 <= self.digits <= MAX_DIGITS):
            raise ValueError(f"row digits must be an integer from 0 to {MAX_DIGITS}")
        if self.indent is not None and (isinstance(self.indent, bool) or not isinstance(self.indent, int) or not 0 <= self.indent <= 6):
            raise ValueError("row indent must be an integer from 0 to 6")
        for name in ("group", "note"):
            v = getattr(self, name)
            if v is not None and not isinstance(v, str):
                raise ValueError(f"row {name} must be a string")

    def to_dict(self) -> dict:
        d = {"key": self.key, "label": self.label, "kind": self.kind, "fmt": self.fmt, "values": [_clean_num(v) for v in self.values], "input": bool(self.input)}
        for name in ("group", "note", "agg", "digits", "indent"):
            if getattr(self, name) is not None:
                d[name] = getattr(self, name)
        return d


@dataclass
class Table:
    """The shared grid payload: {months:[iso], status:['actual'|'projected'], rows:[...], captions:[...], fy?:{years, rows}}."""
    months: tuple[Month, ...] | list[Month] = MONTHS
    status: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)
    fy: dict | None = None                           # {years:[2025,...], rows:{key:[value per year]}} overrides the grid's computed FY values

    @classmethod
    def for_axis(cls, actual_through: date | None, months=MONTHS) -> "Table":
        return cls(months=months, status=status_for(actual_through, months))

    def add(self, key: str, label: str, values: Series | list | None = None, *, kind: str | None = None, fmt: str = "usd0", input: bool = False,
            missing: float | None = 0.0, group: str | None = None, note: str | None = None, agg: str | None = None, digits: int | None = None,
            indent: int | None = None) -> Row:
        """Append a row. `values` = a Series (looked up by month; `missing` fills gaps, pass None for input rows) or a list of len(months)."""
        row = Row(key=key, label=label, kind=kind, fmt=fmt, input=input, group=group, note=note, agg=agg, digits=digits, indent=indent)
        if row.kind == "header":
            row.values = [None] * len(self.months)
        elif isinstance(values, dict) or values is None:
            row.values = series_to_list(values, self.months, missing)
        else:
            row.values = list(values)
            if len(row.values) != len(self.months):
                raise ValueError(f"row {key!r}: {len(row.values)} values for {len(self.months)} months")
        self.rows.append(row)
        return row

    def to_payload(self) -> dict:
        status = self.status or ["projected"] * len(self.months)
        out = {"months": [m.isoformat() for m in self.months], "status": list(status),
               "rows": [r.to_dict() for r in self.rows], "captions": list(self.captions)}
        if self.fy is not None:
            out["fy"] = {"years": list(self.fy.get("years", [])), "rows": {k: [_clean_num(v) for v in vals] for k, vals in (self.fy.get("rows") or {}).items()}}
        return out


# ---------------------------------------------------------------------------------------------------------------- module descriptor

@dataclass
class Module:
    """What `app/projection/<module>/__init__.py` exports as MODULE. Use keyword arguments.

    load_inputs(conn[, scenario]) -> Inputs   reads the module's typed inputs (read-only; conn rows are dicts)
    compute(scenario, inputs, **upstream)     returns the module's result dataclass; the engine passes only the upstream
                                              results the function names (drivers, cohorts, pnl, demand, inventory, financing)
    """
    key: str
    label: str = ""
    group: str = ""
    load_inputs: Callable[..., Any] | None = None
    compute: Callable[..., Any] | None = None
    blueprint: Any = None                            # flask Blueprint; the registry mounts it at /api/projection/<key-with-dashes>
    ui: str | None = None                            # file under app/static/js/projection/, e.g. 'cohorts.js'
    validate_series: Callable[..., Any] | None = None   # (conn, scenario, items) -> None | raises ApiError(400): rules for the module's own series keys
    validate_params: Callable[..., Any] | None = None   # (conn, scenario, namespace, params) -> None | raises ApiError(400): rules for params[<key>]


# key -> (label, group). The order is the nav order and the engine order.
MODULE_CATALOG: dict[str, tuple[str, str]] = {
    "modeler": ("Scenario modeler", "P1"),              # a what-if workbench over the spend plan and the retention model; it has no engine step
    "cohorts": ("Cohorts & curves", "P1"),
    "drivers": ("Spend plan", "P1"),
    "pnl": ("P&L", "P1"),
    "inventory_demand": ("Inventory demand", "P2"),
    "inventory_supply": ("Inventory supply", "P2"),
    "financing": ("Cash & debt", "P2"),
    "pacing": ("Pacing", "P3"),
    "variables": ("Variables", "Settings"),             # a settings surface over the seven calculating modules; it has no engine step
}
