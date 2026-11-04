"""Small constructors the per-module spec files share. Nothing here has behavior; it only fills VariableSpec fields from short calls."""
from __future__ import annotations

from typing import Any, Callable

from .spec import VariableSpec


_WORDS = (("Amazon FBA", "Amazon"), ("FBA", "Amazon"), ("DTC", "direct-to-consumer"), ("ROAS", "return on ad spend"), ("COGS", "cost of goods sold"),
          ("per-SKU", "per-item"), ("SKUs", "items"), ("SKU", "item"), ("international 3PL", "international warehouse"), ("3PL", "warehouse"),
          ("EBITDA", "operating earnings"))


def plain(text: str) -> str:
    """Labels read as a finished document: acronyms are written out (the sheet's names stay in sheet_ref)."""
    for a, b in _WORDS:
        text = text.replace(a, b)
    text = text.replace(" a item", " an item")
    return text[:1].upper() + text[1:]


class Builder:
    """Collects the specs of one module. `page` is the module page that edits series and tables (the dashed key the shell uses)."""

    def __init__(self, module: str, page: str):
        self.module = module
        self.page = page
        self.specs: list[VariableSpec] = []
        self._order = 0

    def _add(self, **kw) -> VariableSpec:
        kw.setdefault("module", self.module)
        kw.setdefault("page", self.page)
        kw.setdefault("group_order", self._order)
        self._order += 1
        kw["label"] = plain(kw["label"])
        kw["group"] = plain(kw["group"])
        s = VariableSpec(**kw)
        self.specs.append(s)
        return s

    # ---- scalar levers in scenario params
    def lever(self, name: str, label: str, *, group: str, unit: str, vt: str, default: Any, path: tuple[str, ...], sheet: str, used: str,
              lo: float | None = None, hi: float | None = None, step: float | None = None, choices: tuple[str, ...] | None = None,
              status: str = "IMPLEMENTED", **kw) -> VariableSpec:
        return self._add(id=f"{self.module}.{name}", group=group, label=label, unit=unit, kind="scalar", value_type=vt, default=default, status=status,
                         sheet_ref=sheet, where_used=used, binding=kw.pop("binding", "param"), path=path, lo=lo, hi=hi, step=step, choices=choices, **kw)

    # ---- monthly series (proj_series)
    def series(self, name: str, label: str, *, group: str, unit: str, key: str, sheet: str, used: str, cells: int | None = None, vt: str = "money",
               status: str = "IMPLEMENTED", **kw) -> VariableSpec:
        return self._add(id=f"{self.module}.{name}", group=group, label=label, unit=unit, kind="series", value_type=vt, default=None, status=status,
                         sheet_ref=sheet, where_used=used, binding="series", series_key=key, series_cells=cells, **kw)

    # ---- tables (rows edited on the module's page)
    def table(self, name: str, label: str, *, group: str, sheet: str, used: str, unit: str = "rows", status: str = "IMPLEMENTED", scope: str = "scenario",
              **kw) -> VariableSpec:
        return self._add(id=f"{self.module}.{name}", group=group, label=label, unit=unit, kind="table", value_type="list", default=None, status=status,
                         sheet_ref=sheet, where_used=used, binding="table", scope=scope, **kw)

    # ---- sheet drivers the app does not have (or only partly)
    def missing(self, name: str, label: str, *, group: str, unit: str, vt: str = "number", default: Any = None, sheet: str, used: str, ticket: str | None = None,
                notes: str = "", scope: str = "scenario", **kw) -> VariableSpec:
        return self._add(id=f"{self.module}.{name}", group=group, label=label, unit=unit, kind="scalar", value_type=vt, default=default, status="MISSING",
                         sheet_ref=sheet, where_used=used, binding="none", ticket=ticket, notes=notes, scope=scope, **kw)
