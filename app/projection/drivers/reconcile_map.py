"""Reconcile map for the drivers module: P&L rows 11-16, 19-20 and 51-56 (C..BJ = Jan-2025 .. Dec-2029) against the engine.

Rows 18 (DTC revenue), 21 (sales tax) and 22 (total company revenue) need the cohort repeat revenue, which is computed after this module; they
are produced by drivers.revenue.revenue_lines (called by the pnl module) and compared through the pnl lines of the same names.
"""
_P = {"tab": "P&L", "c0": "C", "c1": "BJ", "kind": "values"}

MAP = [
    {**_P, "key": "ad_spend", "row": 11, "result": "drivers.ad_spend"},
    {**_P, "key": "nc_rev", "row": 12, "result": "drivers.nc_rev"},
    {**_P, "key": "nc_orders", "row": 13, "result": "drivers.nc_orders", "abs": 0.5},
    {**_P, "key": "nc_cpa", "row": 14, "result": "drivers.nc_cpa", "abs": 0.005},
    {**_P, "key": "nc_aov", "row": 15, "result": "drivers.nc_aov", "abs": 0.005},
    {**_P, "key": "nc_roas", "row": 16, "result": "drivers.nc_roas", "abs": 0.0001},
    {**_P, "key": "amazon_rev", "row": 19, "result": "drivers.amazon_rev"},
    {**_P, "key": "tiktok_rev", "row": 20, "result": "drivers.tiktok_rev"},
    {**_P, "key": "marketing_spend", "row": 51, "result": "drivers.spend_total"},
    {**_P, "key": "spend_amazon_ads", "row": 52, "result": "drivers.spend_amazon_ads"},
    {**_P, "key": "spend_meta", "row": 53, "result": "drivers.spend_meta"},
    {**_P, "key": "spend_google", "row": 54, "result": "drivers.spend_google"},
    {**_P, "key": "spend_bing", "row": 55, "result": "drivers.spend_bing"},
    {**_P, "key": "spend_tiktok", "row": 56, "result": "drivers.spend_tiktok"},
    {**_P, "key": "dtc_rev", "row": 18, "result": "pnl.lines.dtc_rev"},
    {**_P, "key": "sales_tax", "row": 21, "result": "pnl.lines.sales_tax"},
    {**_P, "key": "total_revenue", "row": 22, "result": "pnl.lines.total_revenue"},
]
