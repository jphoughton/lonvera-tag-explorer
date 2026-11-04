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


def snapshot_inventory() -> InventoryResult:
    """Inventory buys exactly as the sheet shows them (P&L row 125 = Inventory Cash Outlay row 9)."""
    return InventoryResult(outlay=stubs.snapshot_series("P&L", 125))


def snapshot_cohorts() -> CohortResult:
    return CohortResult(curve_live=stubs._curve("C"), curve_goal=stubs._curve("B"))


def snapshot_drivers() -> DriverResult:
    return DriverResult(nc_aov=stubs.snapshot_series("P&L", 15), nc_cpa=stubs.snapshot_series("P&L", 14), actual_through=date(2026, 6, 1))


def compute_with_snapshot(conn, slug: str = "current"):
    """Financing result for a scenario of the test database with the sheet's own NOI, inventory buys and curves as upstream."""
    from app.projection import scenarios
    from app.projection.financing import compute, load_inputs
    scn = scenarios.get_scenario(conn, slug)
    return compute(scn, load_inputs(conn, scn), pnl=snapshot_pnl(), inventory=snapshot_inventory(), cohorts=snapshot_cohorts(),
                   drivers=snapshot_drivers())


def engine_result(fin, scn: Scenario | None = None) -> EngineResult:
    return EngineResult(scenario=scn or Scenario(id=1, slug="current", name="Current plan"), actual_through=date(2026, 6, 1),
                        results={"financing": fin}, built={"financing": True}, stubbed={})


def reconcile(fin, tol: float = 0.005, abs_tol: float = 1.0):
    """Reports for every line of the financing reconcile map against the snapshot."""
    import reconcile as rc
    from app.projection.financing.reconcile_map import MAP
    return rc.reconcile({"financing": MAP}, rc.SnapshotSource(), engine_result(fin), tol, abs_tol)
