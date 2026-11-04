"""Reconcile map for the financing module (frozen sheet snapshot 2026-11-03). Cash rows run from the close month, so month0 is 2026-09-01 at column W.

Not mapped: the Spend-ROAS Matrix tab (its target-month input reads a deleted tab and shows #REF!, so it has no values to compare) and P&L
rows 108-131 before Sep-26 (row 128 is #REF! there and the close month starts the buyer's cash).
"""
PL = "P&L"
TD = "Term Debt Monthly Paydown"
CASH = {"tab": PL, "c0": "W", "c1": "BJ", "kind": "values", "month0": "2026-09-01"}


def _pl(key: str, row: int, result: str, **kw) -> dict:
    return {"key": key, "row": row, "result": result, **CASH, **kw}


def _td(key: str, col: str, result: str, r0: int = 10, r1: int = 49) -> dict:
    return {"key": key, "tab": TD, "kind": "column", "c0": col, "r0": r0, "r1": r1, "result": result}


def _sc(key: str, tab: str, cell: str, result: str) -> dict:
    col = "".join(ch for ch in cell if ch.isalpha())
    return {"key": key, "tab": tab, "kind": "scalar", "row": int("".join(ch for ch in cell if ch.isdigit())), "c0": col, "result": result}


def _col(key: str, tab: str, col: str, r0: int, r1: int, result: str) -> dict:
    return {"key": key, "tab": tab, "kind": "column", "c0": col, "r0": r0, "r1": r1, "result": result}


MAP = [
    # Term Debt Monthly Paydown, rows 10-49 = Sep-26 .. Dec-29
    _td("debt_beginning", "C", "financing.debt.beginning"),
    _td("debt_interest", "D", "financing.debt.interest"),
    _td("debt_principal", "E", "financing.debt.principal"),
    _td("debt_ending", "F", "financing.debt.ending"),
    _td("debt_cum_principal", "G", "financing.debt.cum_principal", 10, 40),        # the sheet's column G stops at row 40
    _td("debt_cum_interest", "H", "financing.debt.cum_interest"),
    _td("debt_total_payment", "J", "financing.recon.total_payment_from_month2", 11, 49),   # J10 is empty in the sheet
    # P&L cash rows
    _pl("noi_before_inventory", 108, "financing.lines.noi_before_inventory"),
    _pl("starting_cash", 110, "financing.lines.opening_cash"),
    _pl("loan_interest", 112, "financing.lines.interest"),
    _pl("loan_principal", 113, "financing.lines.principal"),
    _pl("loan_payment", 114, "financing.lines.loan_payment"),
    _pl("loan_balance", 115, "financing.lines.balance"),
    _pl("seller_principal", 117, "financing.lines.seller_principal"),
    _pl("seller_interest", 118, "financing.lines.seller_interest"),
    _pl("tax", 120, "financing.lines.tax"),
    _pl("equity_payback", 122, "financing.lines.equity"),
    _pl("inventory_buys", 125, "financing.lines.inventory_buys"),
    _pl("cash_after_inventory", 128, "financing.lines.cash_after_inventory"),
    _pl("ending_cash", 131, "financing.lines.ending_cash"),
    # Payback (section 1)
    _sc("payback_months", "Payback", "B58", "financing.payback.months"),
    _sc("payback_cm_first", "Payback", "B14", "financing.payback.cm_first"),
    _sc("payback_cm_pct", "Payback", "B15", "financing.payback.cm_pct"),
    _sc("payback_gap", "Payback", "B16", "financing.payback.gap"),
    _sc("payback_roas", "Payback", "D8", "financing.payback.roas"),
    _sc("payback_final_cumulative", "Payback", "B59", "financing.payback.final_cumulative"),
    _sc("payback_surplus", "Payback", "F59", "financing.payback.surplus"),
    _col("payback_retention", "Payback", "B", 21, 56, "financing.recon.retention_from_age1"),
    _col("payback_cm", "Payback", "C", 20, 56, "financing.payback.cm"),
    _col("payback_cumulative", "Payback", "D", 20, 56, "financing.payback.cumulative"),
    # Scale-payback and payout block, P&L E177:P206
    _sc("payout_nc_rev", PL, "F181", "financing.payout.nc_rev"),
    _sc("payout_marketing_expenses", PL, "F184", "financing.payout.marketing_expenses"),
    _sc("payout_cogs", PL, "F186", "financing.payout.cogs"),
    _sc("payout_fulfillment", PL, "F187", "financing.payout.fulfillment"),
    _sc("payout_cm", PL, "F189", "financing.payout.cm"),
    _sc("payout_roas", PL, "G183", "financing.payout.roas"),
    _sc("payout_variable_rate", PL, "G189", "financing.payout.variable_rate"),
    _col("payout_repeat_ratio", PL, "J", 180, 186, "financing.payout.repeat_ratio"),
    _col("payout_repeat_rev", PL, "K", 180, 186, "financing.payout.repeat_rev"),
    _col("payout_repeat_cm", PL, "L", 180, 186, "financing.payout.repeat_cm"),
    _col("payout_running_cm", PL, "M", 180, 186, "financing.payout.running_cm"),
    _col("bonus_payback_4", PL, "G", 193, 202, "financing.recon.bonus_4"),
    _col("bonus_payback_3", PL, "H", 193, 202, "financing.recon.bonus_3"),
    _col("bonus_payback_2", PL, "I", 193, 202, "financing.recon.bonus_2"),
    _col("comp_payback_4", PL, "N", 193, 202, "financing.recon.comp_4"),
    _col("comp_payback_3", PL, "O", 193, 202, "financing.recon.comp_3"),
    _col("comp_payback_2", PL, "P", 193, 202, "financing.recon.comp_2"),
]
