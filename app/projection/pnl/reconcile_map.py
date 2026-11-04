"""Reconcile map for the pnl module: engine lines versus the sheet's `P&L` tab, monthly C..BJ (Jan-25 .. Dec-29).

Entries that differ from the plain pattern, and why (details in docs/projection/MODULE_pnl.md):
  ftm_revenue, ftm_noi  the sheet fills these from Jun-26 (column T); the engine fills every month, so the map starts at T.
  marketing_software    the sheet's rate divides Apr-Jul-26 software cost by Feb-May-26 revenue (P&L!B63 = sum(R63:U63)/sum(P22:S22)); the engine
                        divides by the same four months, so projected months run 2.07% higher than the sheet ($83-123 a month). Tolerance 2.5%.
"""
from .lines import LINE_KEYS

MAP: list[dict] = []
for _key, _ln in LINE_KEYS.items():
    if _ln.row is None or _ln.row < 22:
        continue
    _e = {"key": _key, "tab": "P&L", "row": _ln.row, "c0": "C", "c1": "BJ", "kind": "values", "result": f"pnl.lines.{_key}"}
    if _key in ("ftm_revenue", "ftm_noi"):
        _e.update(c0="T", month0="2026-06-01")
    if _key == "marketing_software":
        _e.update(tol=0.025)
    MAP.append(_e)


# Fiscal-year columns BL:BP (2025-2029) of the main roll-up rows. FY2026 net operating income is skipped: the sheet's BM100 sums Sep-Dec only
# (=sum(W100:Z100)) while every other FY2026 cell is the full year; the engine reports the full year.
_FY_COL = {2025: "BL", 2026: "BM", 2027: "BN", 2028: "BO", 2029: "BP"}
for _key in ("total_revenue", "amazon_fees", "cogs", "total_cogs", "gross_profit", "marketing_spend", "marketing", "other_business_expenses",
             "headcount", "overhead", "total_expenses", "noi"):
    for _year, _col in _FY_COL.items():
        if _key == "noi" and _year == 2026:
            continue
        MAP.append({"key": f"fy{_year}_{_key}", "tab": "P&L", "row": LINE_KEYS[_key].row, "c0": _col, "kind": "scalar", "result": f"pnl.fy_flat.{_year}.{_key}"})

# Implied enterprise value: P&L!C162:H162 = FY2027 net operating income x 3..8
for _i, _col in enumerate("CDEFGH"):
    MAP.append({"key": f"ev_{3 + _i}x", "tab": "P&L", "row": 162, "c0": _col, "kind": "scalar", "result": f"pnl.ev.{3 + _i}x"})
