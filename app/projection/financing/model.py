"""Financing module: loads the scenario's inputs and computes cash, term debt, seller note, tax, payback, spend x ROAS grid and payout schedule.

Upstream (read by name): `pnl` (net operating income, China COGS and the variable-cost lines by month) and `inventory` (cash outlay by month) are
REQUIRED: without either the engine marks this module degraded instead of showing cash built on zeros. `cohorts` (retention curves) is needed for the
payback, grid and payout (they read "unavailable" without it) and `drivers` (last closed month's new-customer AOV and CAC) only fills blank payback inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import series as series_mod
import logging

from ...queries import ApiError
from ..drivers import loaders as drivers_loaders, model as drivers_model, reference as drivers_reference
from ..interfaces import MONTHS, FinancingResult, Month, Series, add_months, month_floor
from . import calc
from . import params as P

log = logging.getLogger("tagexp.projection")
CHANNEL_NAMES = {"meta": "Meta", "google": "Google", "tiktok": "TikTok", "amazon": "Amazon ads"}
NOI_KEYS = ("noi", "net_operating_income")
CHINA_COGS_KEYS = ("china_cogs", "cogs_china", "cogs")          # P&L row 32; the stub pnl only has row 31 (the same amount to the cent)
DEFAULT_CLOSE = date(2026, 9, 1)


@dataclass
class FinancingInputs:
    params: dict = field(default_factory=dict)                  # effective `financing` params (defaults overlaid)
    series: dict[str, Series] = field(default_factory=dict)     # financing.* monthly cells
    cells: dict[str, dict[Month, dict]] = field(default_factory=dict)   # key -> month -> {kind, note, origin}
    realized: dict | None = None                                # what new customers really cost lately, from the feeds (None when they hold nothing)
    today: date | None = None


@dataclass
class FinancingDetail(FinancingResult):
    """FinancingResult plus everything the page and the reconcile map read."""
    params: dict = field(default_factory=dict)
    close_month: Month = DEFAULT_CLOSE
    debt: calc.DebtSchedule = field(default_factory=calc.DebtSchedule)
    seller: calc.SellerNote = field(default_factory=calc.SellerNote)
    tax_detail: list[dict] = field(default_factory=list)
    cash: calc.Cash = field(default_factory=calc.Cash)
    payback: calc.Payback | None = None                          # on product margin (the sheet's Payback tab)
    payback_loaded: calc.Payback | None = None                   # after all variable costs (the P&L's own rates)
    payback_plan: calc.Payback | None = None                     # the same two readings at the spend plan's own inputs (AOV, CPA of the plan month)
    payback_plan_loaded: calc.Payback | None = None
    loaded_info: dict = field(default_factory=dict)              # the P&L rate behind payback_loaded and its parts
    unavailable: dict[str, str] = field(default_factory=dict)    # section -> why it cannot be computed (payback / matrix / payout)
    top_up: dict = field(default_factory=dict)                   # monthly cash that keeps ending cash at zero, and what drives the low point
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
    out.today = drivers_loaders.current_date()
    out.realized = realized_inputs(conn, scn, out.today)
    return out


def realized_inputs(conn, scn, today: date) -> dict | None:
    """What a new customer really costs and spends lately: the Spend plan page's "last 28 days" reference (drivers.reference with the scenario's bounds:
    the chosen channels' ad spend over Shopify new customers, and the average order value of the same days). Each number is offered only when its feeds cover the
    window and it is believable; otherwise it is absent and `unavailable` says why in plain words. None when the reference cannot be read at all."""
    try:
        lv = drivers_model.parse_levers(getattr(scn, "params", None) or {})
        ref = drivers_reference.load_reference(conn, lv.ad_channels, lv.reference_days, today, levers=lv)
        w, a = ref.cpa_window, ref.aov_window
        win = w or a
        out: dict = {"cac": w["cpa"] if w else None, "aov": a["aov"] if a else None, "days": lv.reference_days, "channels": list(lv.ad_channels),
                     "unavailable": dict(ref.unavailable)}
        if win:
            out.update({"from": win["from"], "to": win["to"]})
            d0, d1 = date.fromisoformat(win["from"]), date.fromisoformat(win["to"])
            names = [CHANNEL_NAMES.get(c, str(c)) for c in lv.ad_channels]
            out["label"] = f"last {lv.reference_days} days ({d0:%b} {d0.day} to {d1:%b} {d1.day}), {' + '.join(names)} + Shopify feeds"
        if w:
            out.update({"spend": w["spend"], "new_customers": w["new_customers"]})
        return out if (w or a or ref.unavailable) else None
    except Exception:                                           # the feeds are a reference: a failure here must not take cash and debt down
        log.exception("financing: realized payback inputs unavailable")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


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


def plan_inputs(drivers: Any, today: date | None) -> dict | None:
    """The spend plan's average order value and cost per new customer for the first plan-driven month from the open month on (the Spend plan page's chip)."""
    pa = getattr(drivers, "plan_aov", None) or getattr(drivers, "nc_aov", None) or {}
    pc = getattr(drivers, "plan_cpa", None) or getattr(drivers, "nc_cpa", None) or {}
    basis = getattr(drivers, "nc_basis", None) or {}
    start = month_floor(today or date.today())
    cands = [m for m in MONTHS if m >= start and pc.get(m) and pa.get(m)]
    m = next((x for x in cands if basis.get(x) == "plan"), cands[0] if cands else None)
    return {"month": m.isoformat(), "aov": pa[m], "cac": pc[m]} if m else None


def payback_inputs(prm: dict, drivers: Any, realized: dict | None = None, today: date | None = None) -> dict:
    """AOV and CAC for the payback calculator, in this order: the scenario's typed value, what new customers really cost and spent over the last
    28 days (the feeds), the drivers' last closed month. The plan's own inputs are carried alongside (`plan`) so the page shows both."""
    pb = prm["payback"]
    through = getattr(drivers, "actual_through", None)
    ref_aov = _last_actual(getattr(drivers, "nc_aov", {}) or {}, through)
    ref_cac = _last_actual(getattr(drivers, "nc_cpa", {}) or {}, through)
    typed_aov, typed_cac = P.opt_num(pb.get("aov")), P.opt_num(pb.get("cac"))
    rz = realized or {}
    window = f"last {rz['days']} days" if rz.get("days") else None

    def pick(typed, real, closed):
        if typed is not None:
            return typed, "scenario"
        if real:
            return real, window
        if closed is not None:
            return closed, "last closed month"
        return None, None

    aov, aov_src = pick(typed_aov, rz.get("aov"), ref_aov)
    cac, cac_src = pick(typed_cac, rz.get("cac"), ref_cac)
    labels = {"scenario": "typed in this scenario", "last closed month": f"last closed month ({through:%b %Y})" if through else "last closed month"}
    if window and rz.get("label"):
        labels[window] = rz["label"]
    unavailable = rz.get("unavailable") or {}
    why = {"aov": unavailable.get("aov") if aov_src == "last closed month" else None, "cac": unavailable.get("cpa") if cac_src == "last closed month" else None}
    return {"aov": aov, "cac": cac, "aov_source": aov_src, "cac_source": cac_src,
            "aov_source_label": labels.get(aov_src), "cac_source_label": labels.get(cac_src),
            "aov_fallback_reason": why["aov"], "cac_fallback_reason": why["cac"],
            "realized": rz or None, "plan": plan_inputs(drivers, today),
            "reference": {"aov": ref_aov, "cac": ref_cac, "month": through.isoformat() if through else None},
            "product_cost_pct": P.num(pb.get("product_cost_pct")), "fulfillment": P.num(pb.get("fulfillment")),
            "max_age": int(P.num(pb.get("max_age"), 36)), "curve": pb.get("curve") or "live"}


def run_payback(inp: dict, curve: list[float], loaded_rate: float | None = None, *, fully_loaded: bool = False) -> calc.Payback | None:
    if not inp.get("aov") or inp.get("cac") is None:
        return None
    if fully_loaded:
        if loaded_rate is None:
            return None
        return calc.payback(inp["aov"], inp["cac"], inp["product_cost_pct"], inp["fulfillment"], curve, inp["max_age"], fully_loaded=True,
                            loaded_rate=loaded_rate)
    return calc.payback(inp["aov"], inp["cac"], inp["product_cost_pct"], inp["fulfillment"], curve, inp["max_age"])


def loaded_rate_info(prm: dict, pnl: Any, rate_override: float | None = None) -> dict:
    """Variable cost per new-customer order as a share of AOV, from the P&L's own rates: each configured line (product cost, fulfillment,
    inbound shipping, payment processing, chargeback management) divided by DTC revenue, summed over the P&L's forward months (the first
    `loaded_months` projected months; the latest months when none are projected). A typed `loaded_rate` replaces it. rate is None when a
    configured line is missing from the P&L, so a partial list never reads as a lower cost."""
    pb = prm["payback"]
    keys = [k for k in (pb.get("loaded_lines") or P.LOADED_LINES) if isinstance(k, str)]
    n = max(1, int(P.num(pb.get("loaded_months"), 12)))
    typed = rate_override if rate_override is not None else P.opt_num(pb.get("loaded_rate"))
    info: dict = {"rate": None, "source": None, "parts": [], "months": [], "missing": [], "lines": keys}
    if typed is not None:
        info.update(rate=typed, source="typed")
        return info
    lines = getattr(pnl, "lines", None) or {}
    dtc = lines.get("dtc_rev") or {}
    through = getattr(pnl, "actual_through", None)
    live = sorted(m for m, v in dtc.items() if v and v > 0)
    months = [m for m in live if through is None or m > through][:n] or live[-n:]
    if not months:
        info["missing"] = ["dtc_rev"]
        return info
    base = sum(dtc[m] for m in months)
    parts = []
    for k in keys:
        if k not in lines:
            info["missing"].append(k)
            continue
        parts.append({"key": k, "label": P.LOADED_LABELS.get(k, k.replace("_", " ").capitalize()), "rate": sum(lines[k].get(m, 0.0) for m in months) / base})
    info["parts"] = parts
    info["months"] = [months[0].isoformat(), months[-1].isoformat()]
    if not info["missing"] and parts:
        info.update(rate=sum(x["rate"] for x in parts), source="P&L rates")
    return info


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


def run_matrix(prm: dict, pb: calc.Payback | None, curve: list[float], loaded: calc.Payback | None = None, **over: Any) -> calc.Matrix | None:
    if pb is None:
        return None
    mx = {**prm["matrix"], **{k: v for k, v in over.items() if v is not None}}
    base = loaded if (mx.get("margin") == "loaded" and loaded is not None) else pb
    m = calc.spend_roas_matrix(curve=curve, cm_rate=base.cm_pct, target_month=int(mx["target_month"]), basis=mx["basis"],
                               fixed_cost=P.num(mx["fixed_cost"]), spends=[P.num(x) for x in mx["spends"]], roas=[P.num(x) for x in mx["roas"]])
    m.margin = "loaded" if base is loaded and loaded is not None else "product"
    return m


# ------------------------------------------------------------------------------------------------------------------ compute

def compute(scn, inputs: FinancingInputs | None, pnl, inventory, cohorts=None, drivers=None) -> FinancingDetail:
    inputs = inputs or FinancingInputs(params=P.effective(getattr(scn, "params", None)))
    prm = inputs.params
    try:
        P.validate_params(prm)                                  # a stored value the validators would refuse (e.g. via the generic scenario PATCH) never computes
    except ApiError as e:
        raise ValueError(f"invalid financing input: {e.message}") from None
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
    wf = dict(close=close, noi_before_inventory=noi_bi, loan_interest=interest, loan_principal=principal, seller_principal=seller.principal,
              seller_interest=seller.interest, tax=tax, equity=equity, inventory=inv, opening_first=P.num(cash.get("opening")),
              opening_override=ser.get(P.S_OPENING))
    c = calc.cash_waterfall(MONTHS, floor=P.num(cash.get("floor")), **wf)
    L = c.lines
    out.lines = {"interest": interest, "principal": principal, "balance": balance, "tax": L["tax"], "equity": L["equity_payback"],
                 "cash_after_inventory": L["cash_after_inventory"], "ending_cash": L["ending_cash"], "noi_before_inventory": L["noi_before_inventory"],
                 "opening_cash": L["opening_cash"], "loan_payment": L["loan_payment"], "inventory_buys": L["inventory_buys"],
                 "net_cash_flow": L["net_cash_flow"], "headroom": L["headroom"], "seller_principal": L["seller_principal"],
                 "seller_interest": L["seller_interest"], "seller_balance": seller.balance}
    out.debt, out.seller, out.cash = debt, seller, c
    out.override_cells = {k: sorted(m for m in cells if cells[m]["kind"] == "override") for k, cells in inputs.cells.items()
                          if any(v["kind"] == "override" for v in cells.values())}

    out.top_up = cash_drivers(out, c, wf, tax, interest, principal, inv, seller)

    # payback, grid, payout (need the retention curve from the cohorts module)
    pbi = payback_inputs(prm, drivers, inputs.realized, inputs.today)
    curve = _curve(cohorts, pbi["curve"])
    pbi["shared_sources"] = P.payback_sources(getattr(scn, "params", None))
    out.payback_inputs = pbi
    out.loaded_info = loaded_rate_info(prm, pnl)
    if not any(curve):
        out.unavailable = {k: "The retention curve is not available: the cohorts module has no result." for k in ("payback", "matrix", "payout")}
    else:
        out.payback = run_payback(pbi, curve)
        out.payback_loaded = run_payback(pbi, curve, out.loaded_info["rate"], fully_loaded=True)
        if pbi["plan"]:
            at_plan = {**pbi, "aov": pbi["plan"]["aov"], "cac": pbi["plan"]["cac"]}
            out.payback_plan = run_payback(at_plan, curve)
            out.payback_plan_loaded = run_payback(at_plan, curve, out.loaded_info["rate"], fully_loaded=True)
        out.matrix = run_matrix(prm, out.payback, curve, out.payback_loaded)
        repeat, src = payout_repeat(prm, cohorts)
        out.payout, out.payout_repeat_source = run_payout(prm, repeat), src
        if out.payback is None:
            out.unavailable["payback"] = "Enter the average order value and customer acquisition cost."

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


def cash_drivers(out: FinancingDetail, c: calc.Cash, wf: dict, tax: Series, interest: Series, principal: Series, inv: Series, seller: calc.SellerNote) -> dict:
    """What drives the low point and what it takes to stay at zero: the cash earned and paid out from the close through the lowest month,
    and the smallest monthly top-up that keeps every month at zero or above. Computed from the waterfall, nothing typed."""
    funding, top = calc.top_up_to_zero(MONTHS, **wf)
    res: dict[str, Any] = {"close_funding": funding, "monthly_top_up": top, "through": _iso(c.lowest_month), "months": 0, "operating": 0.0, "outflows": [], "starting_cash_adjustments": 0.0}
    if c.lowest_month is None or (c.lowest_value or 0) >= 0:
        return res
    span = [m for m in MONTHS if out.close_month <= m <= c.lowest_month]
    tot = lambda s: sum(s.get(m, 0.0) for m in span)                       # noqa: E731
    items = [("term_loan_principal", "term-loan principal", tot(principal)), ("inventory", "inventory buys", tot(inv)),
             ("term_loan_interest", "term-loan interest", tot(interest)), ("tax", "tax", tot(tax)),
             ("seller_note", "seller note", tot(seller.principal) + tot(seller.interest)), ("equity", "equity payback", tot(wf["equity"]))]
    L = c.lines
    adj = 0.0
    prev = None
    for m in span:
        if prev is not None:
            adj += L["opening_cash"][m] - prev
        prev = L["ending_cash"][m]
    res.update(months=len(span), operating=tot(wf["noi_before_inventory"]), starting_cash_adjustments=adj,
               outflows=sorted(({"key": k, "label": lab, "amount": a} for k, lab, a in items if a > 0.5), key=lambda x: -x["amount"]))
    return res


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
        "payback_months_loaded": r.payback_loaded.months if r.payback_loaded else None,
        "payback_loaded_available": r.payback_loaded is not None,
        "payback_basis": (r.payback_inputs.get("aov_source_label") or None) if r.payback else None,
        "payback_months_plan": r.payback_plan.months if r.payback_plan else None,
        "payback_months_plan_loaded": r.payback_plan_loaded.months if r.payback_plan_loaded else None,
        "payback_horizon": r.payback.ages[-1] if r.payback else None,
        "monthly_top_up": r.top_up.get("monthly_top_up", 0.0),
        "close_funding": r.top_up.get("close_funding", 0.0),
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
