"""Projection module `pnl`: costs, gross profit, net operating income, fiscal-year / trailing / growth metrics, implied enterprise value, and the
QuickBooks history upload. See docs/projection/MODULE_pnl.md."""
from __future__ import annotations

from ..interfaces import Module, Scenario
from . import charts, lines, store                                  # noqa: F401  (lines is part of the package surface: pnl.lines.LINE_KEYS)
from .api import bp
from .model import PnlOutput, compute_pnl
from .validate import validate_params, validate_series              # noqa: F401  (hooks the core finds by name)


def load_inputs(conn, scn: Scenario | None = None):
    return store.load_inputs(conn, scn)


def compute(scn: Scenario, inputs, drivers=None, cohorts=None) -> PnlOutput:
    params = (scn.params or {}).get("pnl") or {}
    out = compute_pnl(inputs, drivers=drivers, cohorts=cohorts, params=params)          # the sheet chart: revenue basis for both charts, the output of the default chart
    if charts.chart_of(params) == charts.QB2026:
        from .charts import qb2026
        out = qb2026.compute(inputs, drivers, cohorts, params, out)
    return out


MODULE = Module(key="pnl", label="P&L", group="P1", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="pnl.js")
MODULE.validate_series = validate_series                         # the core finds write rules on MODULE or on the package
MODULE.validate_params = validate_params
