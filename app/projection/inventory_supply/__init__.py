"""Module inventory_supply: purchase orders, FBA transfers, stock burn per location, reorder status, inventory value, cash outlay.

Replaces the sheet tabs Purchase Orders, Vendor COGS, FBA Transfers, Warehance/Intl/Amazon/Total Inventory Burn, Warehance/Intl Reorder,
Inventory Value and Inventory Cash Outlay. See docs/projection/MODULE_inventory_supply.md.
"""
from __future__ import annotations

from ..interfaces import DemandResult, Module
from . import calc, history, loaders, overlay
from .api import bp
from .model import Inputs, Settings, SupplyResult


def load_inputs(conn, scn=None) -> Inputs:
    return loaders.load_inputs(conn, scn)


def compute(scn, inputs: Inputs, demand: DemandResult | None = None) -> SupplyResult:
    """Burn, reorder, value and outlay for the scenario. `demand` is inventory_demand's DemandResult (full-month units per (location, product)
    plus (f'{location}_mtd', product) month-to-date units for the current month)."""
    params = getattr(scn, "params", None)
    return calc.compute_supply(inputs, demand, loaders.resolve_today(params), Settings.from_params(params))


MODULE = Module(key="inventory_supply", label="Inventory supply", group="P2", load_inputs=load_inputs, compute=compute, blueprint=bp,
                ui="inventory_supply.js")


def on_restore(conn, slug: str, to_version: int, new_version: int, actor: str | None = None) -> dict:
    """Restore hook the core calls after a restore of the plan of record: the supply data (orders, transfers, counts, prices) goes back with it."""
    return history.on_restore(conn, slug, to_version, new_version, actor)


def validate_params(params: dict, conn=None) -> None:
    """Validation hook the core calls for every change to params["inventory_supply"] (`params` = the module's whole group after the patch); raises ApiError(400)."""
    overlay.validate_params({overlay.NS: params or {}}, conn)
