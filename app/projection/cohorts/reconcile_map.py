"""Reconcile map for the cohorts module (scripts/projection/reconcile.py --module cohorts).

curve_live / curve_goal / lifetime / twelve_month   Curves tab (ages 1-60 in rows 4-63; row 65 lifetime, row 66 first 12 months)
repeat_rev                Hist Waterfall row 228 = P&L row 17, on the cohort table's new-customer revenue (new customers x first order)
repeat_rev_sheet_basis    the same waterfall on the new-customer revenue typed on the Hist Waterfall tab (the sheet's own inputs): ties to the cent
repeat_rev_actual         Hist Waterfall row 377 (layer 5, actual repeat from the cohort increments)
pnl_row17                 P&L row 17 (Repeat Revenue) = row 228 by date, except Sep-2026 (W17), which the sheet blends 50/50 with month-to-date pacing
Months: Hist Waterfall row 5 starts at Feb-2024 (column C), so the axis month Jan-2025 sits 11 columns later (column N) and Dec-2029 is column BU.
"""
HW = "Hist Waterfall - Last 4 Months Curve"

MAP = [
    {"key": "curve_live", "tab": "Curves", "kind": "column", "c0": "C", "r0": 4, "r1": 63, "result": "cohorts.curve_live"},
    {"key": "curve_goal", "tab": "Curves", "kind": "column", "c0": "B", "r0": 4, "r1": 63, "result": "cohorts.curve_goal"},
    {"key": "lifetime", "tab": "Curves", "kind": "scalar", "row": 65, "c0": "C", "result": "cohorts.lifetime_multiple"},
    {"key": "twelve_month", "tab": "Curves", "kind": "scalar", "row": 66, "c0": "C", "result": "cohorts.twelve_month_multiple"},
    {"key": "repeat_rev_sheet_basis", "tab": HW, "row": 228, "c0": "N", "c1": "BU", "kind": "values", "month0": "2025-01-01",
     "result": "cohorts.repeat_rev_sheet_basis"},
    {"key": "repeat_rev", "tab": HW, "row": 228, "c0": "N", "c1": "BU", "kind": "values", "month0": "2025-01-01", "result": "cohorts.repeat_rev"},
    {"key": "repeat_rev_actual", "tab": HW, "row": 377, "c0": "N", "c1": "BU", "kind": "values", "month0": "2025-01-01",
     "result": "cohorts.repeat_rev_actual"},
    {"key": "pnl_row17", "tab": "P&L", "row": 17, "c0": "C", "c1": "BJ", "kind": "values", "month0": "2025-01-01", "result": "cohorts.repeat_rev"},
]
