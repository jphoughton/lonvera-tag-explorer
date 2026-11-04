"""Grid payloads for the financing page (contract shape: months, status, rows, captions) plus the page's extra blocks."""
from __future__ import annotations

from typing import Any

from ..interfaces import MONTHS, Month, Series, Table
from . import calc
from . import params as P
from .model import FinancingDetail


# FY-column aggregation hints for the shared grid: balances take the last month, starting cash has no yearly figure
AGG = {"loan_balance": "last", "seller_balance": "last", "ending_cash": "last", "headroom": "last", P.S_OPENING: "none",
       "beginning": "none", "ending": "last", "cum_principal": "last", "cum_interest": "last"}


def _agg(payload: dict) -> dict:
    for r in payload["rows"]:
        if r["key"] in AGG:
            r["agg"] = AGG[r["key"]]
    return payload


def cash_table(res: FinancingDetail, actual_through: Month | None) -> dict:
    """Monthly cash waterfall from the close month. Months before the close are blank (the seller's)."""
    t = Table.for_axis(actual_through)
    L = res.lines
    close = res.close_month

    t.add("hdr_operating", "Operating cash", kind="header")
    t.add(P.S_NOI_BI, "Net operating income before inventory", L["noi_before_inventory"], input=True, missing=None)
    t.add("hdr_debt", "Debt service", kind="header")
    t.add("loan_interest", "Term loan interest", L["interest"], missing=None)
    t.add(P.S_PRINCIPAL, "Term loan principal", L["principal"], input=True, missing=None)
    t.add("loan_payment", "Total loan payment", L["loan_payment"], kind="subtotal", missing=None)
    t.add("loan_balance", "Term loan balance, end of month", L["balance"], kind="memo", missing=None)
    if res.seller.principal or res.seller.interest:
        t.add("seller_principal", "Seller note principal", L["seller_principal"], missing=None)
        t.add("seller_interest", "Seller note interest", L["seller_interest"], missing=None)
        t.add("seller_balance", "Seller note balance, end of month", L["seller_balance"], kind="memo", missing=None)
    t.add("hdr_other", "Other cash items", kind="header")
    t.add(P.S_TAX, "Federal tax", L["tax"], input=True, missing=None)
    t.add(P.S_EQUITY, "Equity payback", L["equity"], input=True, missing=None)
    t.add("inventory_buys", "Inventory buys", L["inventory_buys"], missing=None)
    t.add("hdr_cash", "Cash", kind="header")
    t.add(P.S_OPENING, "Starting cash", L["opening_cash"], input=True, missing=None)
    t.add("net_cash_flow", "Net cash flow", L["net_cash_flow"], kind="subtotal", missing=None)
    t.add("ending_cash", "Ending cash", L["ending_cash"], kind="total", missing=None)
    floor = P.num(res.params["cash"].get("floor"))
    if floor:
        t.add("headroom", "Headroom above cash floor", L["headroom"], kind="memo", missing=None)
    t.captions.append("Source: net operating income from the P&L, inventory buys from the supply plan, loan terms and tax from this page's inputs.")
    payload = _agg(t.to_payload())
    notes = {
        P.S_NOI_BI: "Net operating income plus China cost of goods sold, which the inventory buys replace. Sep-26 is typed because the buyer owns only the part of the month after close; clear a typed month to use the P&L.",
        P.S_PRINCIPAL: "Typed principal paid per month. A month without a value pays interest only.",
        P.S_TAX: "Tax = rate x net operating income of the period, paid in the month shown. Type a value to replace a month.",
        P.S_OPENING: "Previous month's ending cash. A typed value replaces it for that month.",
        "inventory_buys": "Deposits by order month plus balances by landing month, from the inventory supply plan.",
    }
    for r in payload["rows"]:
        if r["key"] in notes:
            r["note"] = notes[r["key"]]
    payload["close_month"] = close.isoformat()
    payload["overrides"] = {k: [m.isoformat() for m in v] for k, v in res.override_cells.items()}
    payload["summary"] = res.summary
    payload["tax_detail"] = [{**d, "month": d["month"].isoformat(), "from": d["from"].isoformat(), "to": d["to"].isoformat()} for d in res.tax_detail]
    return payload


def debt_table(res: FinancingDetail, actual_through: Month | None) -> dict:
    d = res.debt
    t = Table.for_axis(actual_through)

    def ser(vals: list[float]) -> Series:
        return calc.as_series(d.months, vals)

    t.add("beginning", "Beginning balance", ser(d.beginning), missing=None)
    t.add("interest", "Interest", ser(d.interest), missing=None)
    t.add(P.S_PRINCIPAL, "Principal paid", ser(d.principal), input=True, missing=None)
    t.add("total_payment", "Total payment", ser(d.total_payment), kind="subtotal", missing=None)
    t.add("ending", "Ending balance", ser(d.ending), kind="total", missing=None)
    t.add("cum_principal", "Cumulative principal paid", ser(d.cum_principal), kind="memo", missing=None)
    t.add("cum_interest", "Cumulative interest paid", ser(d.cum_interest), kind="memo", missing=None)
    t.captions.append("Source: term loan terms and typed principal payments; interest = beginning balance x annual rate / 12.")
    payload = _agg(t.to_payload())
    for r in payload["rows"]:
        if r["key"] == P.S_PRINCIPAL:
            r["note"] = "Typed principal paid per month. A month without a value pays interest only (or follows the fixed payment in that mode)."
    dd = res.params["debt"]
    payload["terms"] = {"principal": P.num(dd["principal"]), "rate": P.num(dd["rate"]), "mode": dd.get("mode"), "fixed_payment": P.num(dd["fixed_payment"]),
                        "start": res.close_month.isoformat()}
    payload["summary"] = {
        "total_interest": d.total_interest, "total_principal_paid": res.summary["total_principal_paid"], "payoff_month": res.summary["payoff_month"],
        "residual_balance": res.summary["residual_balance"], "year_end": res.summary["debt_balance_year_end"],
    }
    payload["warnings"] = [w for w in res.warnings if w.startswith("Term loan")]
    seller = res.seller
    payload["seller_note"] = {
        "enabled": bool(res.params["seller_note"].get("enabled")), "terms": res.params["seller_note"],
        "payments": [{**p, "month": p["month"].isoformat()} for p in res.seller_flags],
    }
    return payload


def payback_payload(res: FinancingDetail, pb: calc.Payback | None, inputs: dict, curve_kind: str) -> dict:
    out: dict[str, Any] = {"inputs": inputs, "curve": curve_kind}
    if pb is None:
        out["result"] = None
        return out
    out["result"] = {
        "aov": pb.aov, "cac": pb.cac, "product_cost": pb.product_cost, "fulfillment": pb.fulfillment, "total_variable": pb.total_variable,
        "cm_first": pb.cm_first, "cm_pct": pb.cm_pct, "roas": pb.roas, "gap": pb.gap, "months": pb.months,
        "months_interpolated": pb.months_interpolated, "final_cumulative": pb.final_cumulative, "surplus": pb.surplus,
        "ages": pb.ages, "retention": pb.retention, "cm": pb.cm, "cumulative": pb.cumulative, "paid": pb.paid,
    }
    return out


def matrix_payload(m: calc.Matrix | None) -> dict:
    if m is None:
        return {"result": None}
    return {"result": {"target_month": m.target_month, "basis": m.basis, "cm_rate": m.cm_rate, "cum_retention": m.cum_retention,
                       "fixed_cost": m.fixed_cost, "breakeven_roas": m.breakeven_roas, "spends": m.spends, "roas": m.roas,
                       "profit": m.profit, "breakeven_spend": m.breakeven_spend}}


def payout_payload(p: calc.Payout, source: str) -> dict:
    return {"repeat_source": source, "result": {
        "media_spend": p.media_spend, "cpa": p.cpa, "aov": p.aov, "roas": p.roas, "nc_rev": p.nc_rev, "marketing_expenses": p.marketing_expenses,
        "lazar": p.lazar, "designer": p.designer, "samples": p.samples, "creative_production": p.creative_production, "cogs": p.cogs,
        "fulfillment": p.fulfillment, "cm": p.cm, "repeat_ratio": p.repeat_ratio, "repeat_rev": p.repeat_rev, "repeat_cm": p.repeat_cm,
        "running_cm": p.running_cm, "payback_month": p.payback_month, "scales": p.scales, "columns": p.columns,
        "bonus": {str(k): v for k, v in p.bonus.items()}, "total_comp": {str(k): v for k, v in p.total_comp.items()}, "selected": p.selected,
    }}
