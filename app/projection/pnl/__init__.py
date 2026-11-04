"""Projection module `pnl`: costs, gross profit, net operating income, fiscal-year / trailing / growth metrics, implied enterprise value, and the
QuickBooks history upload. See docs/projection/MODULE_pnl.md."""
from __future__ import annotations

from ..interfaces import Module, Scenario
from . import lines, store                                  # noqa: F401  (lines is part of the package surface: pnl.lines.LINE_KEYS)
from .api import bp
from .model import PnlOutput, compute_pnl
from .validate import validate_params, validate_series              # noqa: F401  (hooks the core finds by name)


def load_inputs(conn, scn: Scenario | None = None):
    return store.load_inputs(conn, scn)


def compute(scn: Scenario, inputs, drivers=None, cohorts=None) -> PnlOutput:
    return compute_pnl(inputs, drivers=drivers, cohorts=cohorts, params=(scn.params or {}).get("pnl") or {})


MODULE = Module(key="pnl", label="P&L", group="P1", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="pnl.js")
MODULE.validate_series = validate_series                         # the core finds write rules on MODULE or on the package
MODULE.validate_params = validate_params
