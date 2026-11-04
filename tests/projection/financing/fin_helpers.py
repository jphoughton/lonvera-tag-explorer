"""Helpers shared by the financing tests: the sheet snapshot as upstream results, and a runner that reconciles an engine result against the sheet."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "scripts" / "projection"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app.projection import stubs                                           # noqa: E402
from app.projection.engine import EngineResult                              # noqa: E402
from app.projection.interfaces import CohortResult, DriverResult, InventoryResult, PnlResult, Scenario   # noqa: E402


def snapshot_pnl() -> PnlResult:
    """Net operating income (P&L row 100) and China COGS (row 32) from the snapshot, under the pnl module's line keys."""
    lines = {"noi": stubs.snapshot_series("P&L", 100), "china_cogs": stubs.snapshot_series("P&L", 32)}
    return PnlResult(lines=lines)


LOADED_ROWS = {"dtc_rev": 18, "china_cogs": 32, "fulfillment_services": 37, "inbound_shipping": 38, "payment_processing": 40, "chargeback_management": 80}


def snapshot_pnl_with_costs(actual_through: date = date(2026, 7, 1)) -> PnlResult:
    """snapshot_pnl plus the P&L's variable-cost lines and DTC revenue (the sheet's own forward rates), with the P&L's actual boundary."""
    r = snapshot_pnl()
    for k, row in LOADED_ROWS.items():
        r.lines[k] = stubs.snapshot_series("P&L", row)
    r.actual_through = actual_through
    return r


def snapshot_inventory() -> InventoryResult:
    """Inventory buys exactly as the sheet shows them (P&L row 125 = Inventory Cash Outlay row 9)."""
    return InventoryResult(outlay=stubs.snapshot_series("P&L", 125))


def snapshot_cohorts() -> CohortResult:
    return CohortResult(curve_live=stubs._curve("C"), curve_goal=stubs._curve("B"))


def snapshot_drivers() -> DriverResult:
    return DriverResult(nc_aov=stubs.snapshot_series("P&L", 15), nc_cpa=stubs.snapshot_series("P&L", 14), actual_through=date(2026, 6, 1))


SHEET_PAYBACK = {"financing": {"payback": {"aov": 69.15600858369098, "cac": 105.4916738197425}}}


def compute_with_snapshot(conn, slug: str = "current", costs: bool = False, sheet_payback: bool = True):
    """Financing result for a scenario of the test database with the sheet's own NOI, inventory buys and curves as upstream
    (costs=True adds the P&L's variable-cost lines for the after-all-variable-costs payback; sheet_payback=True types the sheet's month-to-date
    average order value and cost per customer, which the Current plan itself leaves blank so they follow the last closed month)."""
    from app.projection import scenarios
    from app.projection.financing import compute, load_inputs
    from app.projection.financing import params as P
    scn = scenarios.get_scenario(conn, slug)
    if sheet_payback:
        scn.params = P.deep_merge(scn.params, SHEET_PAYBACK)
    return compute(scn, load_inputs(conn, scn), pnl=snapshot_pnl_with_costs() if costs else snapshot_pnl(), inventory=snapshot_inventory(),
                   cohorts=snapshot_cohorts(), drivers=snapshot_drivers())


def engine_result(fin, scn: Scenario | None = None) -> EngineResult:
    return EngineResult(scenario=scn or Scenario(id=1, slug="current", name="Current plan"), actual_through=date(2026, 6, 1),
                        results={"financing": fin}, built={"financing": True}, stubbed={})


def reconcile(fin, tol: float = 0.005, abs_tol: float = 1.0):
    """Reports for every line of the financing reconcile map against the snapshot."""
    import reconcile as rc
    from app.projection.financing.reconcile_map import MAP
    return rc.reconcile({"financing": MAP}, rc.SnapshotSource(), engine_result(fin), tol, abs_tol)
