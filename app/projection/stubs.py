"""Fixture stubs: stand-ins for modules that are not built yet, fed from the frozen sheet snapshot.

`engine.run` calls `stub_for(key, ...)` for any module whose package is missing, so every module can be developed against a complete
upstream. Values come from `scripts/projection/snapshot_read.py` (env PROJECTION_SNAPSHOT_DIR overrides the snapshot folder). If the
snapshot is unreadable every series is zero, so the engine never crashes. Stub line keys are NOT the real pnl LINE_KEYS.
"""
from __future__ import annotations

import importlib
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import series as series_mod
from .interfaces import (MONTHS, CohortResult, DemandResult, DriverResult, FinancingResult, InventoryResult, Month, PnlResult, Scenario, Series,
                         zero_series)

log = logging.getLogger("tagexp.projection")
_REPO = Path(__file__).resolve().parents[2]
PNL = "P&L"

# P&L rows (snapshot 2026-11-03)
DRIVER_ROWS = {"spend_total": 11, "nc_rev": 12, "nc_orders": 13, "nc_cpa": 14, "nc_aov": 15, "nc_roas": 16, "amazon_rev": 19, "tiktok_rev": 20,
               "sales_tax": 21, "spend_amazon_ads": 52, "spend_meta": 53, "spend_google": 54, "spend_tiktok": 56}
PNL_ROWS = {"ad_spend": 11, "nc_rev": 12, "nc_orders": 13, "repeat_rev": 17, "dtc_rev": 18, "amazon_rev": 19, "tiktok_rev": 20, "sales_tax": 21,
            "total_revenue": 22, "amazon_fees": 24, "cogs": 31, "cogs_total": 46, "gross_profit": 47, "marketing_spend": 51,
            "total_expenses": 95, "noi": 100}
FINANCING_KEYS = ("interest", "principal", "balance", "tax", "equity", "cash_after_inventory", "ending_cash")


def snapshot_module() -> Any | None:
    """scripts.projection.snapshot_read, or None when it cannot be imported."""
    for name in ("scripts.projection.snapshot_read", "snapshot_read"):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    try:
        sys.path.insert(0, str(_REPO / "scripts" / "projection"))
        return importlib.import_module("snapshot_read")
    except Exception:
        return None


def _sr_call(fn: str, *a, **k):
    sr = snapshot_module()
    if sr is None:
        return None
    try:
        return getattr(sr, fn)(*a, **k)
    except Exception as e:                                 # missing snapshot / tab / row: fall back to zeros
        log.debug("snapshot read failed (%s %s): %s", fn, a, e)
        return None


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pnl_months() -> list[Month | None]:
    sr = snapshot_module()
    hdr = _sr_call("row", PNL, 8, "C", "BJ")
    if sr is None or hdr is None:
        return []
    return [sr.serial_to_date(v) if _num(v) else None for v in hdr]


def snapshot_series(tab: str, row: int, months_row: int | None = 8, c0: str = "C", c1: str = "BJ") -> Series:
    """One horizontal range as a Series on the 60-month axis (non-numeric cells are skipped)."""
    vals = _sr_call("row", tab, row, c0, c1)
    out: Series = {}
    if vals is None:
        return out
    months = _pnl_months() if tab == PNL else [MONTHS[i] if i < len(MONTHS) else None for i in range(len(vals))]
    for m, v in zip(months, vals):
        if m is not None and _num(v) and m in MONTHS:
            out[m] = float(v)
    return out


def _snapshot_actual_through() -> Month | None:
    flags = _sr_call("row", PNL, 9, "C", "BJ")
    months = _pnl_months()
    last = None
    for m, f in zip(months, flags or []):
        if m is not None and f == "Actual":
            last = m
    return last


def _fill(s: Series) -> Series:
    return {m: s.get(m, 0.0) for m in MONTHS}


def _curve(col: str) -> list[float]:
    vals = _sr_call("col", "Curves", col, 4, 63) or []
    out = [float(v) if _num(v) else 0.0 for v in vals]
    return out + [0.0] * (60 - len(out))


def stub_drivers(conn, scn: Scenario, today: date) -> DriverResult:
    at = series_mod.actual_through(conn, today) or _snapshot_actual_through()
    kw = {k: _fill(snapshot_series(PNL, r)) for k, r in DRIVER_ROWS.items()}
    return DriverResult(actual_through=at, **kw)


def stub_cohorts(conn, scn: Scenario, today: date, drivers: DriverResult | None = None) -> CohortResult:
    life = _sr_call("cell", "Curves", "C65")
    return CohortResult(curve_live=_curve("C"), curve_goal=_curve("B"), lifetime_multiple=float(life) if _num(life) else 0.0,
                        repeat_rev=_fill(snapshot_series(PNL, 17)), repeat_rev_actual={}, cohort_rows=[])


def _rolling(s: Series, back: int, forward: int) -> Series:
    vals = [s.get(m, 0.0) for m in MONTHS]
    return {m: sum(vals[max(0, i - back): i + forward + 1]) for i, m in enumerate(MONTHS)}


def stub_pnl(conn, scn: Scenario, today: date) -> PnlResult:
    lines = {k: _fill(snapshot_series(PNL, r)) for k, r in PNL_ROWS.items()}
    fy: dict[int, dict[str, float]] = {}
    for k, s in lines.items():
        for m, v in s.items():
            fy.setdefault(m.year, {})[k] = fy.get(m.year, {}).get(k, 0.0) + v
    noi = lines["noi"]
    return PnlResult(lines=lines, fy=fy, ttm=_rolling(noi, 11, 0), ftm=_rolling(noi, 0, 11), ev={})


def stub_demand(conn, scn: Scenario, today: date) -> DemandResult:
    return DemandResult(units={})


def stub_inventory(conn, scn: Scenario, today: date) -> InventoryResult:
    return InventoryResult(outlay=zero_series(), value=zero_series(), burn={}, reorder=[])


def stub_financing(conn, scn: Scenario, today: date) -> FinancingResult:
    return FinancingResult(lines={k: zero_series() for k in FINANCING_KEYS})


STUBS: dict[str, Callable[..., Any]] = {
    "drivers": stub_drivers, "cohorts": stub_cohorts, "pnl": stub_pnl, "inventory_demand": stub_demand,
    "inventory_supply": stub_inventory, "financing": stub_financing,
}


def stub_for(key: str, conn, scn: Scenario, today: date, **upstream) -> Any:
    fn = STUBS[key]
    return fn(conn, scn, today, **({"drivers": upstream.get("drivers")} if key == "cohorts" else {}))
