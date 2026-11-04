"""Financing module: loads the scenario's inputs and computes cash, term debt, seller note, tax, payback, spend x ROAS grid and payout schedule.

Upstream (read by name): `pnl` (net operating income and China COGS by month), `inventory` (cash outlay by month), `cohorts` (retention curves),
`drivers` (last closed month's new-customer AOV and CAC when the payback inputs are blank). Each is optional: a missing one reads as zeros.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import series as series_mod
from ..interfaces import MONTHS, FinancingResult, Month, Series, add_months
from . import calc
from . import params as P

NOI_KEYS = ("noi", "net_operating_income")
CHINA_COGS_KEYS = ("china_cogs", "cogs_china", "cogs")          # P&L row 32; the stub pnl only has row 31 (the same amount to the cent)
DEFAULT_CLOSE = date(2026, 9, 1)


@dataclass
class FinancingInputs:
    params: dict = field(default_factory=dict)                  # effective `financing` params (defaults overlaid)
    series: dict[str, Series] = field(default_factory=dict)     # financing.* monthly cells
    cells: dict[str, dict[Month, dict]] = field(default_factory=dict)   # key -> month -> {kind, note, origin}


@dataclass
class FinancingDetail(FinancingResult):
    """FinancingResult plus everything the page and the reconcile map read."""
    params: dict = field(default_factory=dict)
    close_month: Month = DEFAULT_CLOSE
    debt: calc.DebtSchedule = field(default_factory=calc.DebtSchedule)
    seller: calc.SellerNote = field(default_factory=calc.SellerNote)
    tax_detail: list[dict] = field(default_factory=list)
    cash: calc.Cash = field(default_factory=calc.Cash)
    payback: calc.Payback | None = None
    payback_inputs: dict = field(default_factory=dict)
    matrix: calc.Matrix | None = None
    payout: calc.Payout | None = None
    payout_repeat_source: str = ""
    seller_flags: list[dict] = field(default_factory=list)       # seller note installments with trailing-12-month NOI and the EBITDA floor check (a flag only)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    override_cells: dict[str, list[Month]] = field(default_factory=dict)
    recon: dict[str, Any] = field(default_factory=dict)          # lists aligned to sheet rows, for reconcile_map.py only


# ------------------------------------------------------------------------------------------------------------------ inputs

def load_inputs(conn, scn) -> FinancingInputs:
    out = FinancingInputs(params=P.effective(getattr(scn, "params", None)))
    for c in series_mod.fetch_cells(conn, scn, prefix="financing."):
        out.series.setdefault(c["line_key"], {})[c["month"]] = c["value"]
        out.cells.setdefault(c["line_key"], {})[c["month"]] = {"kind": c["kind"], "note": c["note"], "origin": c["origin"]}
    return out


# ------------------------------------------------------------------------------------------------------------------ helpers

def _pick(lines: dict[str, Series] | None, keys: tuple[str, ...]) -> Series:
    for k in keys:
        if lines and k in lines:
            return lines[k]
    return {}


def _curve(cohorts: Any, which: str) -> list[float]:
    c = getattr(cohorts, "curve_goal" if which == "goal" else "curve_live", None) or []
    return [P.num(x) for x in c]


def _last_actual(s: Series, through: Month | None) -> float | None:
    if through is None:
        return None
    for m in sorted((m for m in s if m <= through), reverse=True):
        if s[m]:
            return s[m]
    return None


def payback_inputs(prm: dict, drivers: Any) -> dict:
    """AOV and CAC for the payback calculator: the scenario's typed value, else the drivers' last closed month."""
    pb = prm["payback"]
    through = getattr(drivers, "actual_through", None)
    ref_aov = _last_actual(getattr(drivers, "nc_aov", {}) or {}, through)
    ref_cac = _last_actual(getattr(drivers, "nc_cpa", {}) or {}, through)
    aov = P.opt_num(pb.get("aov"))
    cac = P.opt_num(pb.get("cac"))
    return {"aov": aov if aov is not None else ref_aov, "cac": cac if cac is not None else ref_cac,
            "aov_source": "scenario" if aov is not None else ("last closed month" if ref_aov is not None else None),
            "cac_source": "scenario" if cac is not None else ("last closed month" if ref_cac is not None else None),
            "reference": {"aov": ref_aov, "cac": ref_cac, "month": through.isoformat() if through else None},
            "product_cost_pct": P.num(pb.get("product_cost_pct")), "fulfillment": P.num(pb.get("fulfillment")),
            "max_age": int(P.num(pb.get("max_age"), 36)), "curve": pb.get("curve") or "live"}


def run_payback(inp: dict, curve: list[float]) -> calc.Payback | None:
    if not inp.get("aov") or inp.get("cac") is None:
        return None
    return calc.payback(inp["aov"], inp["cac"], inp["product_cost_pct"], inp["fulfillment"], curve, inp["max_age"])


def payout_repeat(prm: dict, cohorts: Any) -> tuple[list[float], str]:
    po = prm["payout"]
    n = int(P.num(po.get("repeat_months"), 7))
    src = po.get("repeat_source") or "goal"
    if src == "typed":
        vals = [P.num(x) for x in (po.get("repeat_typed") or [])]
    else:
        vals = _curve(cohorts, src)
        if not any(vals):                                       # no curve of that kind: use the other one
            alt = "live" if src == "goal" else "goal"
            vals, src = _curve(cohorts, alt), alt
    return (vals + [0.0] * n)[:n], src


def run_payout(prm: dict, repeat: list[float]) -> calc.Payout:
    po = prm["payout"]
    return calc.payout_block(
        media_spend=P.num(po["media_spend"]), cpa=P.num(po["cpa"], 1.0), aov=P.num(po["aov"], 1.0), lazar=P.num(po["lazar"]),
        designer=P.num(po["designer"]), samples_count=P.num(po["samples_count"]), sample_cost=P.num(po["sample_cost"]),
        creative_production_rate=P.num(po["creative_production_rate"]), cogs_rate=P.num(po["cogs_rate"]),
        fulfillment_rate=P.num(po["fulfillment_rate"]), repeat=repeat, scales=[P.num(x) for x in po["scales"]],
        breakpoint=P.num(po["breakpoint"]), columns=po["columns"])


def run_matrix(prm: dict, pb: calc.Payback | None, curve: list[float], **over: Any) -> calc.Matrix | None:
    if pb is None:
        return None
    mx = {**prm["matrix"], **{k: v for k, v in over.items() if v is not None}}
    return calc.spend_roas_matrix(curve=curve, cm_rate=pb.cm_pct, target_month=int(mx["target_month"]), basis=mx["basis"],
                                  fixed_cost=P.num(mx["fixed_cost"]), spends=[P.num(x) for x in mx["spends"]], roas=[P.num(x) for x in mx["roas"]])


# ------------------------------------------------------------------------------------------------------------------ compute

def compute(scn, inputs: FinancingInputs | None, pnl: Any = None, inventory: Any = None, cohorts: Any = None, drivers: Any = None) -> FinancingDetail:
    inputs = inputs or FinancingInputs(params=P.effective(getattr(scn, "params", None)))
    prm = inputs.params
    ser = inputs.series
    close = P.to_month(prm.get("close_month"), DEFAULT_CLOSE)
    out = FinancingDetail(params=prm, close_month=close)

    # term loan
    d = prm["debt"]
    debt = calc.debt_schedule(MONTHS, principal=P.num(d["principal"]), rate=P.num(d["rate"]), start=close, principal_paid=ser.get(P.S_PRINCIPAL),
                              mode=d.get("mode") or "typed", fixed_payment=P.num(d["fixed_payment"]))
    interest = calc.as_series(debt.months, debt.interest)
    principal = calc.as_series(debt.months, debt.principal)
    balance = calc.as_series(debt.months, debt.ending)

    # seller note
    sn = prm["seller_note"]
    seller = calc.seller_note(MONTHS, enabled=bool(sn.get("enabled")), principal=P.num(sn["principal"]), rate=P.num(sn["rate"]), close=close,
                              first_payment=P.to_month(sn.get("first_payment"), add_months(close, 12)), installments=int(P.num(sn["installments"], 1)))

    # operating cash before inventory: NOI + the China COGS that the inventory buys replace
    lines = getattr(pnl, "lines", None) or {}
    noi, china = _pick(lines, NOI_KEYS), _pick(lines, CHINA_COGS_KEYS)
    floor_ebitda = P.opt_num(sn.get("ebitda_floor"))
    for pay in seller.payments:                                # the LOI gates the note on an EBITDA floor: shown as a flag, never applied to the cash
        i = MONTHS.index(pay["month"])
        ttm = sum(noi.get(x, 0.0) for x in MONTHS[max(0, i - 11): i + 1])
        out.seller_flags.append({**pay, "ttm_noi": ttm, "ebitda_floor": floor_ebitda,
                                 "below_floor": None if floor_ebitda is None else ttm < floor_ebitda})
    noi_bi: Series = {}
    for m in MONTHS:
        if m < close:
            continue
        noi_bi[m] = ser.get(P.S_NOI_BI, {}).get(m, noi.get(m, 0.0) + china.get(m, 0.0))

    # tax
    tx = prm["tax"]
    payments = [{"month": P.to_month(p.get("month")), "from": P.to_month(p.get("from")), "to": P.to_month(p.get("to")), "label": p.get("label", "")}
                for p in tx.get("payments", []) if P.to_month(p.get("month")) and P.to_month(p.get("from")) and P.to_month(p.get("to"))]
    tax, tax_detail = calc.tax_payments(MONTHS, payments, noi, P.num(tx["rate"]), interest=interest, deduct_interest=bool(tx.get("deduct_interest")))
    for m, v in ser.get(P.S_TAX, {}).items():
        tax[m] = v
        for row in tax_detail:
            if row["month"] == m:
                row["overridden"] = v
    out.tax_detail = tax_detail

    equity = {m: v for m, v in ser.get(P.S_EQUITY, {}).items() if m >= close}
    outlay = getattr(inventory, "outlay", None) or {}
    inv = {m: v for m, v in outlay.items() if m >= close}
    cash = prm["cash"]
    c = calc.cash_waterfall(MONTHS, close=close, noi_before_inventory=noi_bi, loan_interest=interest, loan_principal=principal,
                            seller_principal=seller.principal, seller_interest=seller.interest, tax=tax, equity=equity, inventory=inv,
                            opening_first=P.num(cash.get("opening")), opening_override=ser.get(P.S_OPENING), floor=P.num(cash.get("floor")))
    L = c.lines
    out.lines = {"interest": interest, "principal": principal, "balance": balance, "tax": L["tax"], "equity": L["equity_payback"],
                 "cash_after_inventory": L["cash_after_inventory"], "ending_cash": L["ending_cash"], "noi_before_inventory": L["noi_before_inventory"],
                 "opening_cash": L["opening_cash"], "loan_payment": L["loan_payment"], "inventory_buys": L["inventory_buys"],
                 "net_cash_flow": L["net_cash_flow"], "headroom": L["headroom"], "seller_principal": L["seller_principal"],
                 "seller_interest": L["seller_interest"], "seller_balance": seller.balance}
    out.debt, out.seller, out.cash = debt, seller, c
    out.override_cells = {k: sorted(m for m in cells if cells[m]["kind"] == "override") for k, cells in inputs.cells.items()
                          if any(v["kind"] == "override" for v in cells.values())}

    # payback, grid, payout
    pbi = payback_inputs(prm, drivers)
    curve = _curve(cohorts, pbi["curve"])
    out.payback_inputs = pbi
    out.payback = run_payback(pbi, curve)
    out.matrix = run_matrix(prm, out.payback, curve)
    repeat, src = payout_repeat(prm, cohorts)
    out.payout, out.payout_repeat_source = run_payout(prm, repeat), src

    out.summary = summarize(out, inventory_present=bool(outlay))
    out.warnings = out.summary["warnings"]
    out.recon = build_recon(out)
    return out


def build_recon(r: FinancingDetail) -> dict[str, Any]:
    """Lists in the order the sheet lays them out (payback ages, payout scale rows), so reconcile_map.py can address them by sheet row."""
    rec: dict[str, Any] = {}
    d = r.debt
    rec["total_payment_from_month2"] = d.total_payment[1:]
    if r.payback:
        rec["retention_from_age1"] = r.payback.retention[1:]
    if r.payout:
        for col in r.payout.columns:
            rec[f"bonus_{col}"] = [0.0 if b is None else b for b in r.payout.bonus[col]]
            rec[f"comp_{col}"] = [0.0 if b is None else b for b in r.payout.total_comp[col]]
    return rec


# ------------------------------------------------------------------------------------------------------------------ summary

def _usd(v: float) -> str:
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def _iso(m: Month | None) -> str | None:
    return m.isoformat() if m else None


def summarize(r: FinancingDetail, *, inventory_present: bool = True) -> dict:
    c, debt = r.cash, r.debt
    years = sorted({m.year for m in debt.months})
    year_end = []
    for y in years:
        dec = [i for i, m in enumerate(debt.months) if m.year == y]
        last = dec[-1]
        year_end.append({"year": y, "month": _iso(debt.months[last]), "balance": debt.ending[last], "partial": debt.months[last].month != 12})
    s: dict[str, Any] = {
        "close_month": _iso(r.close_month),
        "lowest_ending_cash": {"month": _iso(c.lowest_month), "value": c.lowest_value},
        "first_negative_month": _iso(c.first_negative), "negative_months": c.negative_months,
        "peak_shortfall": c.peak_shortfall, "floor": P.num(r.params["cash"].get("floor")), "below_floor_months": c.below_floor_months,
        "ending_cash_last": (list(r.lines["ending_cash"].values()) or [None])[-1],
        "payback_months": r.payback.months if r.payback else None,
        "payback_interpolated": r.payback.months_interpolated if r.payback else None,
        "debt_balance_year_end": year_end,
        "term_debt_principal": P.num(r.params["debt"]["principal"]),
        "total_interest": debt.total_interest, "payoff_month": _iso(debt.payoff_month),
        "residual_balance": debt.ending[-1] if debt.ending else 0.0,
        "total_principal_paid": debt.cum_principal[-1] if debt.cum_principal else 0.0,
    }
    w: list[str] = []
    if debt.ending and debt.ending[-1] > calc.EPS:
        last = debt.months[-1]
        w.append(f"Term loan: ${debt.ending[-1]:,.0f} is still outstanding in {last:%b %Y}; principal paid so far totals "
                 f"${debt.cum_principal[-1]:,.0f} of ${s['term_debt_principal']:,.0f}.")
    if debt.capped:
        w.append("Term loan: typed principal in " + ", ".join(f"{m:%b %Y}" for m in debt.capped[:4]) + " exceeds the balance owed and is cut back.")
    if c.negative_months:
        w.append(f"Ending cash is negative in {c.negative_months} month{'s' if c.negative_months != 1 else ''}, first in {c.first_negative:%b %Y}; "
                 f"lowest is {_usd(c.lowest_value)} in {c.lowest_month:%b %Y}.")
    if not inventory_present:
        w.append("Inventory buys read zero: the inventory supply plan has no cash outlay yet.")
    return {**s, "warnings": w}
