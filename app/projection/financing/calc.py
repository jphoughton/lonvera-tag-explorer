"""Pure financing calculations (no database, no Flask): term-loan amortization, seller note, tax, cash waterfall, payback, spend x ROAS grid,
scale-payback payout schedule. Every function takes plain numbers / dicts and returns plain data, so each is testable alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..interfaces import MONTHS, Month, Series, add_months

EPS = 0.005                                         # a balance under half a cent is paid off


# ------------------------------------------------------------------------------------------------------------------ term loan

@dataclass
class DebtSchedule:
    """One row per month from the start month. All lists have the same length as `months`."""
    months: list[Month] = field(default_factory=list)
    beginning: list[float] = field(default_factory=list)
    interest: list[float] = field(default_factory=list)
    principal: list[float] = field(default_factory=list)
    ending: list[float] = field(default_factory=list)
    total_payment: list[float] = field(default_factory=list)
    cum_principal: list[float] = field(default_factory=list)
    cum_interest: list[float] = field(default_factory=list)
    payoff_month: Month | None = None               # first month whose ending balance is zero
    total_interest: float = 0.0
    capped: list[Month] = field(default_factory=list)   # months where the typed principal exceeded the balance and was cut back


def debt_schedule(months, *, principal: float, rate: float, start: Month, principal_paid: Series | None = None, mode: str = "typed",
                  fixed_payment: float = 0.0) -> DebtSchedule:
    """Interest = beginning balance x rate / 12. Principal paid is the typed series (mode 'typed': a month without a cell pays interest only) or
    fixed payment minus interest (mode 'fixed_payment'). Principal never exceeds the beginning balance (the payment floors to what is owed)."""
    out = DebtSchedule()
    paid = principal_paid or {}
    bal = max(0.0, principal)
    cum_p = cum_i = 0.0
    for m in months:
        if m < start:
            continue
        interest = bal * rate / 12.0
        if mode == "fixed_payment":
            want = max(0.0, fixed_payment - interest)
        else:
            want = max(0.0, float(paid.get(m, 0.0) or 0.0))
        pay = min(want, bal)
        if want > bal + EPS and mode == "typed":
            out.capped.append(m)
        end = bal - pay
        if end < EPS:
            end = 0.0
        cum_p += pay
        cum_i += interest
        out.months.append(m)
        out.beginning.append(bal)
        out.interest.append(interest)
        out.principal.append(pay)
        out.ending.append(end)
        out.total_payment.append(pay + interest)
        out.cum_principal.append(cum_p)
        out.cum_interest.append(cum_i)
        if out.payoff_month is None and principal > 0 and end <= 0.0:
            out.payoff_month = m
        bal = end
    out.total_interest = cum_i
    return out


def as_series(months: list[Month], values: list[float]) -> Series:
    return {m: v for m, v in zip(months, values)}


# ------------------------------------------------------------------------------------------------------------------ seller note

@dataclass
class SellerNote:
    principal: Series = field(default_factory=dict)
    interest: Series = field(default_factory=dict)
    balance: Series = field(default_factory=dict)   # balance after the month's payment, from the close month
    payments: list[dict] = field(default_factory=list)   # [{month, principal, interest, total, balance}]


def seller_note(months, *, enabled: bool, principal: float, rate: float, close: Month, first_payment: Month, installments: int,
                interval: int = 12) -> SellerNote:
    """Equal principal installments every `interval` months from `first_payment`; interest on the balance before each installment at the annual
    rate (one year of interest per annual installment). Off (all zero) unless enabled."""
    out = SellerNote()
    if not enabled or principal <= 0 or installments < 1:
        return out
    due = {add_months(first_payment, interval * k): k for k in range(installments)}
    part = principal / installments
    bal = principal
    for m in months:
        if m < close:
            continue
        if m in due:
            interest = bal * rate * interval / 12.0
            out.principal[m] = part
            out.interest[m] = interest
            bal -= part
            out.payments.append({"month": m, "principal": part, "interest": interest, "total": part + interest, "balance": max(bal, 0.0)})
        out.balance[m] = max(bal, 0.0)
    return out


# ------------------------------------------------------------------------------------------------------------------ tax

def tax_payments(months, payments: list[dict], noi: Series, rate: float, *, interest: Series | None = None,
                 deduct_interest: bool = False) -> tuple[Series, list[dict]]:
    """Tax = rate x NOI of the period (never negative). Each payment covers the months from..to and is paid in its `month`.
    With deduct_interest the term-loan interest paid in the period reduces the base. Returns (series by payment month, detail rows)."""
    out: Series = {}
    detail: list[dict] = []
    for p in payments:
        f, t, paid_in = p["from"], p["to"], p["month"]
        span = [m for m in months if f <= m <= t]
        base = sum(noi.get(m, 0.0) for m in span)
        if deduct_interest and interest:
            base -= sum(interest.get(m, 0.0) for m in span)
        amount = max(0.0, base) * rate
        out[paid_in] = out.get(paid_in, 0.0) + amount
        detail.append({"month": paid_in, "from": f, "to": t, "label": p.get("label") or "", "base": base, "rate": rate, "amount": amount})
    return out, detail


# ------------------------------------------------------------------------------------------------------------------ cash waterfall

@dataclass
class Cash:
    lines: dict[str, Series] = field(default_factory=dict)
    first_negative: Month | None = None
    negative_months: int = 0
    lowest_month: Month | None = None
    lowest_value: float | None = None
    peak_shortfall: float = 0.0                     # how far below the floor ending cash goes at its worst (0 when never)
    below_floor_months: int = 0


def cash_waterfall(months, *, close: Month, noi_before_inventory: Series, loan_interest: Series, loan_principal: Series,
                   seller_principal: Series, seller_interest: Series, tax: Series, equity: Series, inventory: Series,
                   opening_first: float = 0.0, opening_override: Series | None = None, floor: float = 0.0) -> Cash:
    """Ending cash = starting cash + NOI before inventory - loan payment - seller note - tax - equity payback - inventory buys.
    Starting cash is the previous month's ending cash (the first month starts from `opening_first`); an override replaces it for one month."""
    keys = ("noi_before_inventory", "opening_cash", "loan_interest", "loan_principal", "loan_payment", "seller_principal", "seller_interest",
            "tax", "equity_payback", "inventory_buys", "net_cash_flow", "cash_after_inventory", "ending_cash", "headroom")
    lines: dict[str, Series] = {k: {} for k in keys}
    over = opening_override or {}
    prev: float | None = None
    low_m: Month | None = None
    low_v: float | None = None
    first_neg: Month | None = None
    neg = below = 0
    peak = 0.0
    for m in months:
        if m < close:
            continue
        opening = over[m] if m in over else (opening_first if prev is None else prev)
        noi_bi = noi_before_inventory.get(m, 0.0)
        li, lp = loan_interest.get(m, 0.0), loan_principal.get(m, 0.0)
        sp, si = seller_principal.get(m, 0.0), seller_interest.get(m, 0.0)
        tx, eq, inv = tax.get(m, 0.0), equity.get(m, 0.0), inventory.get(m, 0.0)
        net = noi_bi - li - lp - sp - si - tx - eq - inv
        end = opening + net
        for k, v in (("noi_before_inventory", noi_bi), ("opening_cash", opening), ("loan_interest", li), ("loan_principal", lp),
                     ("loan_payment", li + lp), ("seller_principal", sp), ("seller_interest", si), ("tax", tx), ("equity_payback", eq),
                     ("inventory_buys", inv), ("net_cash_flow", net), ("cash_after_inventory", end), ("ending_cash", end),
                     ("headroom", end - floor)):
            lines[k][m] = v
        if low_v is None or end < low_v:
            low_m, low_v = m, end
        if end < 0:
            neg += 1
            if first_neg is None:
                first_neg = m
        if end < floor:
            below += 1
            peak = max(peak, floor - end)
        prev = end
    return Cash(lines=lines, first_negative=first_neg, negative_months=neg, lowest_month=low_m, lowest_value=low_v, peak_shortfall=peak,
                below_floor_months=below)


# ------------------------------------------------------------------------------------------------------------------ payback

@dataclass
class Payback:
    aov: float = 0.0
    cac: float = 0.0
    product_cost: float = 0.0                       # dollars per order
    fulfillment: float = 0.0
    total_variable: float = 0.0
    cm_first: float = 0.0                           # contribution margin (CM) of the first order
    cm_pct: float = 0.0
    roas: float = 0.0                               # first-order revenue per ad dollar = AOV / CAC
    gap: float = 0.0                                # CAC minus first-order CM
    ages: list[int] = field(default_factory=list)
    retention: list[float | None] = field(default_factory=list)       # None at age 0 (the first order)
    cm: list[float] = field(default_factory=list)                     # CM earned in the month
    cumulative: list[float] = field(default_factory=list)             # cumulative CM per customer by age
    paid: list[bool] = field(default_factory=list)
    months: int | None = None                       # first age whose cumulative CM covers CAC; None = not within max_age
    months_interpolated: float | None = None
    final_cumulative: float = 0.0
    surplus: float = 0.0                            # final cumulative CM minus CAC


def payback(aov: float, cac: float, product_cost_pct: float, fulfillment: float, curve: list[float], max_age: int = 36) -> Payback:
    """CM per first order = AOV - product cost % x AOV - fulfillment. Month by month, repeat CM = first-order CM x the retention ratio of that age
    (the curve is repeat revenue per $1 of first-order revenue); cumulative CM per customer vs CAC gives the payback month."""
    if not (isinstance(aov, (int, float)) and math.isfinite(aov) and aov > 0):
        raise ValueError("AOV must be a positive number")
    if not (isinstance(cac, (int, float)) and math.isfinite(cac) and cac >= 0):
        raise ValueError("CAC must be zero or more")
    if not 0 <= product_cost_pct <= 1:
        raise ValueError("product cost percent must be between 0 and 1")
    if fulfillment < 0:
        raise ValueError("fulfillment cost must be zero or more")
    max_age = max(1, int(max_age))
    p = Payback(aov=aov, cac=cac, fulfillment=fulfillment)
    p.product_cost = product_cost_pct * aov
    p.total_variable = p.product_cost + fulfillment
    p.cm_first = aov - p.total_variable
    p.cm_pct = p.cm_first / aov
    p.roas = aov / cac if cac > 0 else math.inf
    p.gap = cac - p.cm_first
    cum = 0.0
    for age in range(max_age + 1):
        r = None if age == 0 else (curve[age - 1] if age - 1 < len(curve) else 0.0)
        cm = p.cm_first if age == 0 else p.cm_first * r
        cum += cm
        p.ages.append(age)
        p.retention.append(r)
        p.cm.append(cm)
        p.cumulative.append(cum)
        p.paid.append(cum >= cac)
    p.months = next((a for a, ok in zip(p.ages, p.paid) if ok), None)
    if p.months is not None:
        p.months_interpolated = float(p.months)
        if p.months > 0:
            prev, now = p.cumulative[p.months - 1], p.cumulative[p.months]
            if now > prev:
                p.months_interpolated = p.months - 1 + (cac - prev) / (now - prev)
    p.final_cumulative = p.cumulative[-1]
    p.surplus = p.final_cumulative - cac
    return p


# ------------------------------------------------------------------------------------------------------------------ spend x ROAS grid

@dataclass
class Matrix:
    target_month: int = 0
    basis: str = "cm"
    cm_rate: float = 0.0
    cum_retention: float = 0.0                      # revenue multiple of a first order by the target month = 1 + sum of the curve to that age
    fixed_cost: float = 0.0
    breakeven_roas: float | None = None             # marginal: spend pays for itself by the target month, before fixed cost
    spends: list[float] = field(default_factory=list)
    roas: list[float] = field(default_factory=list)
    profit: list[list[float]] = field(default_factory=list)           # [spend][roas]
    breakeven_spend: list[float | None] = field(default_factory=list)  # per ROAS column: loaded basis = fixed cost / (multiple - 1); cm basis = 0 when profitable


def cumulative_retention(curve: list[float], target_month: int) -> float:
    return 1.0 + sum(curve[:max(0, target_month)])


def spend_roas_matrix(*, curve: list[float], cm_rate: float, target_month: int, basis: str, fixed_cost: float, spends: list[float],
                      roas: list[float]) -> Matrix:
    """Profit at the target cohort age = spend x ROAS x CM rate x cumulative retention - spend (minus fixed cost on the loaded basis)."""
    cum = cumulative_retention(curve, target_month)
    mult = cum * cm_rate
    fixed = fixed_cost if basis == "loaded" else 0.0
    m = Matrix(target_month=target_month, basis=basis, cm_rate=cm_rate, cum_retention=cum, fixed_cost=fixed_cost, spends=list(spends),
               roas=list(roas))
    m.breakeven_roas = (1.0 / mult) if mult > 0 else None
    m.profit = [[s * r * mult - s - fixed for r in roas] for s in spends]
    for r in roas:
        k = r * mult
        if basis == "loaded":
            m.breakeven_spend.append(fixed_cost / (k - 1.0) if k > 1.0 else None)
        else:
            m.breakeven_spend.append(0.0 if k > 1.0 else None)
    return m


# ------------------------------------------------------------------------------------------------------------------ payout (scale-payback schedule)

@dataclass
class Payout:
    media_spend: float = 0.0
    cpa: float = 0.0
    aov: float = 0.0
    roas: float = 0.0
    nc_rev: float = 0.0
    marketing_expenses: float = 0.0
    lazar: float = 0.0
    designer: float = 0.0
    samples: float = 0.0
    creative_production: float = 0.0
    cogs: float = 0.0
    fulfillment: float = 0.0
    variable_rate: float = 0.0
    cm: float = 0.0
    repeat_ratio: list[float] = field(default_factory=list)           # M1..Mn
    repeat_rev: list[float] = field(default_factory=list)
    repeat_cm: list[float] = field(default_factory=list)
    running_cm: list[float] = field(default_factory=list)             # M1..Mn; the first order CM (cm) is the starting point
    payback_month: int | None = None                # first month whose running CM is zero or more
    scales: list[float] = field(default_factory=list)
    columns: list[int] = field(default_factory=list)                  # payback months
    bonus: dict[int, list[float | None]] = field(default_factory=dict)       # payback month -> bonus per scale (None = no bonus at that scale)
    total_comp: dict[int, list[float | None]] = field(default_factory=dict)  # bonus + retainer
    selected: dict = field(default_factory=dict)


def payout_block(*, media_spend: float, cpa: float, aov: float, lazar: float, designer: float, samples_count: float, sample_cost: float,
                 creative_production_rate: float, cogs_rate: float, fulfillment_rate: float, repeat: list[float], scales: list[float],
                 breakpoint: float, columns: list[dict]) -> Payout:
    """Scale-payback model for the media-buyer arrangement. NC revenue = spend / CPA x AOV. Marketing expenses = retainer + designer + samples +
    creative production (a % of media spend; vendor production cost, not compensation). Running CM adds repeat CM month by month to the
    first-order CM; payback is the first month it reaches zero. Bonus per scale tier steps up from each column's first paying tier;
    total comp = bonus + the retainer."""
    if cpa <= 0 or aov <= 0:
        raise ValueError("CPA and AOV must be positive")
    p = Payout(media_spend=media_spend, cpa=cpa, aov=aov, lazar=lazar, designer=designer)
    p.roas = aov / cpa
    p.nc_rev = media_spend / cpa * aov
    p.samples = samples_count * sample_cost
    p.creative_production = media_spend * creative_production_rate
    p.marketing_expenses = lazar + designer + p.samples + p.creative_production
    p.cogs = p.nc_rev * cogs_rate
    p.fulfillment = p.nc_rev * fulfillment_rate
    p.variable_rate = cogs_rate + fulfillment_rate
    p.cm = p.nc_rev - media_spend - p.marketing_expenses - p.cogs - p.fulfillment
    running = p.cm
    for r in repeat:
        rev = p.nc_rev * r
        cm = rev * (1.0 - p.variable_rate)
        running += cm
        p.repeat_ratio.append(r)
        p.repeat_rev.append(rev)
        p.repeat_cm.append(cm)
        p.running_cm.append(running)
    if p.cm >= 0:
        p.payback_month = 0
    else:
        p.payback_month = next((i + 1 for i, v in enumerate(p.running_cm) if v >= 0), None)
    p.scales = list(scales)
    for c in columns:
        month = int(c["payback"])
        bonus: list[float | None] = []
        prev: float | None = None
        for s in scales:
            if s < c["first_scale"]:
                bonus.append(None)
                continue
            if prev is None:
                prev = float(c["first_bonus"])
            else:
                prev = prev * (1.0 + (c["step_up"] if s <= breakpoint else c["step_above"]))
            bonus.append(prev)
        p.columns.append(month)
        p.bonus[month] = bonus
        p.total_comp[month] = [None if b is None else b + lazar for b in bonus]
    p.selected = _select(p)
    return p


def _select(p: Payout) -> dict:
    """The column and scale tier this model lands in, and the bonus there. Payback faster than the fastest column pays that column;
    slower than the slowest column pays nothing."""
    out = {"payback_month": p.payback_month, "column": None, "scale": None, "bonus": None, "total_comp": None}
    cols = sorted(p.columns)
    if p.payback_month is not None and cols:
        out["column"] = next((c for c in cols if p.payback_month <= c), None)
    tiers = [s for s in p.scales if s <= p.media_spend]
    if tiers:
        out["scale"] = tiers[-1]
    if out["column"] is not None and out["scale"] is not None:
        i = p.scales.index(out["scale"])
        out["bonus"] = p.bonus[out["column"]][i]
        out["total_comp"] = p.total_comp[out["column"]][i]
    return out
