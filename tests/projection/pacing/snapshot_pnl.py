"""Test double: a PnlResult / DriverResult / CohortResult built from the frozen sheet snapshot (every P&L row 8..101), keyed `row_<n>`."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "projection"))
import snapshot_read as sr                                           # noqa: E402

from app.projection.interfaces import CohortResult, DriverResult, MONTHS, PnlResult      # noqa: E402

ROW_MAP = {n: f"row_{n}" for n in range(9, 102)}


def pnl_from_snapshot() -> PnlResult:
    months = [sr.serial_to_date(v) if isinstance(v, (int, float)) else None for v in sr.row("P&L", 8, "C", "BJ")]
    lines = {}
    for n in range(9, 102):
        vals = sr.row("P&L", n, "C", "BJ")
        s = {}
        for m, v in zip(months, vals):
            if m is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
                s[m] = float(v)
        lines[f"row_{n}"] = s
    try:                                                              # the same series under the pnl module's real line keys (financing reads china_cogs, dtc_rev ...)
        from app.projection.pnl.lines import ROW_TO_KEY
        for n, key in ROW_TO_KEY.items():
            if f"row_{n}" in lines:
                lines.setdefault(key, lines[f"row_{n}"])
    except Exception:                                                 # noqa: BLE001
        pass
    res = PnlResult(lines=lines)
    res.actual_through = date(2026, 7, 1)                             # the books' last month: the loaded rate reads the P&L's forward months
    return res


def cohorts_from_snapshot() -> CohortResult:
    def col(c):
        vals = sr.col("Curves", c, 4, 63)
        return [float(v) if isinstance(v, (int, float)) else 0.0 for v in vals]
    return CohortResult(curve_live=col("C"), curve_goal=col("B"))


def drivers_from_snapshot() -> DriverResult:
    return DriverResult()
