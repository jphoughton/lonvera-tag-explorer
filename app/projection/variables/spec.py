"""VariableSpec: one named, editable, scenario-aware variable of the model, with the Lonvera Projections sheet's value as its default.

A spec says WHAT the variable is (id, label, unit, bounds, sheet value and cell), WHERE it lives (`binding` + `path` / `series_key`) and HOW the engine
uses it (`where_used`). It carries no logic: reading, writing and resetting go through app/projection/variables/access.py, which routes to the module's own
mechanism (scenario params, a series key, the module's table API) and so to the core's audit, version and restore path.

status
    IMPLEMENTED  a lever or series the engine reads; a test moves it and checks that engine output changes.
    PARTIAL      exposed, but part of its effect is missing, it is shared by two places, or it only changes what a page shows.
    MISSING      a driver of the sheet (or a constant the sheet buries in a formula) that the app does not have as a variable; `ticket` names the fix.
kind
    scalar  one value, edited inline.       series  one value per month (proj_series), edited in the module's grid.      table  rows (orders, cohorts...), edited on the module's page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

STATUSES = ("IMPLEMENTED", "PARTIAL", "MISSING")
KINDS = ("scalar", "series", "table")
VALUE_TYPES = ("number", "integer", "fraction", "money", "bool", "choice", "month", "date", "text", "list")
BINDINGS = ("param", "supply", "pnl_driver", "series", "table", "none")
SCOPES = ("scenario", "all", "feed")

MODULES = ("cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing", "pacing", "modeler")
MODULE_LABELS = {"cohorts": "Cohorts and curves", "drivers": "Spend plan", "pnl": "P&L", "inventory_demand": "Inventory demand",
                 "inventory_supply": "Inventory supply", "financing": "Cash and debt", "pacing": "Pacing", "modeler": "Scenario modeler"}

NUMERIC_TYPES = ("number", "integer", "fraction", "money")


@dataclass(frozen=True)
class VariableSpec:
    id: str                                   # '<module>.<name>', lower snake_case parts joined by dots
    module: str
    group: str                                # section inside the module on the page
    label: str                                # plain English, finished-document style, acronyms expanded
    unit: str                                 # 'fraction', 'dollars', 'dollars per month', 'days', 'months', 'count', 'multiple', 'text', 'date'
    kind: str                                 # scalar | series | table
    value_type: str
    default: Any                              # the sheet's value (None when the sheet computes it: see notes)
    status: str
    sheet_ref: str                            # "Curves!C2" / "Payback!D11" / "formula literal in P&L!V21:BJ21"
    where_used: str                           # how the engine uses it, one sentence
    binding: str = "param"                    # param | supply | pnl_driver | series | table | none
    path: tuple[str, ...] | None = None       # location inside scenario params (param / supply bindings)
    series_key: str | None = None             # proj_series line_key (series binding)
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    choices: tuple[str, ...] | None = None
    page: str | None = None                   # module page that edits it when it is not edited inline
    scope: str = "scenario"                   # scenario = per scenario; all = shared by every scenario; feed = loader rule, not a scenario input
    notes: str = ""
    ticket: str | None = None                 # 'drivers#payback-duplicate' -> docs/projection/TICKETS_drivers.md, anchor
    mirrors: tuple[tuple[str, ...], ...] = ()  # other params paths that must carry the same value (until the owning module reads one place)
    sheet_cells: tuple[tuple[str, str], ...] = ()   # (tab, cell) pairs whose snapshot value equals `default` (checked by a test)
    sheet_scale: float = 1.0                  # snapshot value x sheet_scale == default (percent cells stored as fractions need none)
    sens: bool = True                         # offer in the what-moves-the-plan table
    probe: str = "engine"                     # engine: some engine output moves with it (tested); plan: it moves the inventory planner's proposal; view: it only changes what a page shows; none: nothing moves today (notes say why)
    validator: Callable[[Any], Any] | None = field(default=None, compare=False, repr=False)   # extra check on the whole patch value (module-owned)
    also_used_by: tuple[str, ...] = ()        # other modules that read the same value
    series_cells: int | None = None           # cells the sheet types for a series variable
    extra: Any = field(default=None, compare=False, repr=False)   # binding-specific data (pnl_driver: {'mode','base'})
    group_order: int = 0

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"{self.id}: status {self.status!r}")
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: kind {self.kind!r}")
        if self.value_type not in VALUE_TYPES:
            raise ValueError(f"{self.id}: value_type {self.value_type!r}")
        if self.binding not in BINDINGS:
            raise ValueError(f"{self.id}: binding {self.binding!r}")
        if self.module not in MODULES:
            raise ValueError(f"{self.id}: module {self.module!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"{self.id}: scope {self.scope!r}")
        if self.binding in ("param", "supply") and not self.path:
            raise ValueError(f"{self.id}: a {self.binding} binding needs a path")
        if self.binding == "series" and not self.series_key:
            raise ValueError(f"{self.id}: a series binding needs a series_key")
        if self.status != "MISSING" and self.binding == "none":
            raise ValueError(f"{self.id}: {self.status} needs a binding")
        if self.status == "MISSING" and self.binding != "none":
            raise ValueError(f"{self.id}: a MISSING variable has no binding")
        if not self.id.startswith(self.module + "."):
            raise ValueError(f"{self.id}: id must start with '{self.module}.'")

    @property
    def editable_inline(self) -> bool:
        return self.kind == "scalar" and self.binding in ("param", "supply", "pnl_driver") and self.status != "MISSING" and not (self.extra or {}).get("readonly")

    @property
    def numeric(self) -> bool:
        return self.value_type in NUMERIC_TYPES

    def to_meta(self) -> dict:
        return {"id": self.id, "module": self.module, "group": self.group, "label": self.label, "unit": self.unit, "kind": self.kind,
                "value_type": self.value_type, "default": self.default, "status": self.status, "sheet_ref": self.sheet_ref, "where_used": self.where_used,
                "min": self.lo, "max": self.hi, "step": self.step, "choices": list(self.choices) if self.choices else None, "page": self.page,
                "scope": self.scope, "notes": self.notes, "ticket": self.ticket, "binding": self.binding,
                "path": ".".join(self.path) if self.path else None, "series_key": self.series_key, "editable_inline": self.editable_inline,
                "also_used_by": list(self.also_used_by)}
