"""Module `inventory_demand`: units shipped or sold per month by location (US direct, Amazon, international) and product.

Replaces the sheet tabs Warehance / Amazon / Intl Daily, Weekly, Fcst Inputs, Demand Fcst and Total Demand Fcst. Output: `DemandDetail`
(a `DemandResult`, see engine.py); `inventory_supply` reads `units[(location, product)]` and `units[(location + '_mtd', product)]`.
"""
from __future__ import annotations

from ..interfaces import Module, Series
from . import engine, store
from .engine import DemandDetail, DemandInputs
from .routes import bp


def load_inputs(conn, scn=None) -> DemandInputs:
    return store.read_inputs(conn)


def compute(scn, inputs: DemandInputs, nc_rev: Series | None = None, cohorts=None) -> DemandDetail:
    """Forecast demand for the scenario. `nc_rev` and `cohorts` (upstream) only matter when a location is tied to revenue."""
    repeat = getattr(cohorts, "repeat_rev", None) or {}
    months = set(nc_rev or {}) | set(repeat)
    dtc = {m: (nc_rev or {}).get(m, 0.0) + repeat.get(m, 0.0) for m in months} if months else None
    return engine.compute_demand(inputs, scn.params, dtc_revenue=dtc)


MODULE = Module(key="inventory_demand", label="Inventory demand", group="P2", load_inputs=load_inputs, compute=compute, blueprint=bp,
                ui="inventory_demand.js")
