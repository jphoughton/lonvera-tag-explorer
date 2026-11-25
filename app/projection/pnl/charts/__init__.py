"""Charts of accounts the P&L can show. A chart is a definition (lines, sections, driver table, upload accounts) plus, for the new one, its own compute.

    sheet    the sheet's 89 lines (app/projection/pnl/lines.py, untouched), loaded under 'pnl.<line>' in proj_actuals. The default.
    qb2026   the numbered QuickBooks chart, loaded under 'qb26.<line>'; see charts/qb2026.py.

A scenario selects a chart with `params.pnl.chart` ('sheet' or absent = sheet). The setting is inherited through the base chain like every other
param. The sheet chart is always computed (it is the revenue basis and the source of old-format months); the qb2026 chart is built on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SHEET = "sheet"
QB2026 = "qb2026"
CHARTS = (SHEET, QB2026)


@dataclass(frozen=True)
class ChartDef:
    name: str
    label: str
    line_keys: dict[str, Any]                  # key -> Line (label, kind, fmt, parent, indent)
    qb_keys: tuple[str, ...]                   # lines that take loaded books (upload targets)
    actuals_prefix: str                        # proj_actuals prefix of loaded values
    series_prefix: str                         # proj_series prefix of typed scenario months
    driver_keys: tuple[str, ...]               # lines a driver projects (and a scenario may type over)
    driven_parents: tuple[str, ...]
    channel_keys: tuple[str, ...]              # lines the spend plan supplies (typed over, never driven)
    bases: tuple[str, ...]
    modes: tuple[str, ...]
    rate_kinds: tuple[str, ...]
    sections: list
    driver_groups: list
    income_keys: tuple[str, ...]               # loaded lines that add up to income (upload sanity check)
    families: dict[str, tuple[str, ...]]
    family_label: dict[str, str]
    params_ns: tuple[str, ...]                 # path inside params['pnl'] holding this chart's driver overrides
    readonly_keys: frozenset = field(default_factory=frozenset)
    total_keys: tuple[str, ...] = ()
    subtotal_calc: tuple[str, ...] = ()
    trend_keys: tuple[str, ...] = ()
    accounts: dict[str, str] = field(default_factory=dict)

    def actuals_key(self, key: str) -> str:
        return self.actuals_prefix + key

    def series_key(self, key: str) -> str:
        return self.series_prefix + key

    @property
    def overridable(self) -> frozenset:
        return frozenset(self.driver_keys + self.driven_parents + self.channel_keys)


def chart_of(pnl_params: dict | None) -> str:
    """The chart a scenario's effective params['pnl'] selects."""
    v = (pnl_params or {}).get("chart")
    return v if v in CHARTS else SHEET


def get(name: str | None) -> ChartDef:
    if name == QB2026:
        from . import qb2026
        return qb2026.CHART
    from . import sheet
    return sheet.CHART


def driver_params(pnl_params: dict | None, chart: str) -> dict:
    """The driver overrides a scenario holds for `chart`: {line key: {segments}} (sheet: params.pnl.drivers, qb2026: params.pnl.qb26.drivers)."""
    p = pnl_params or {}
    if chart == QB2026:
        return ((p.get("qb26") or {}).get("drivers")) or {}
    return p.get("drivers") or {}
