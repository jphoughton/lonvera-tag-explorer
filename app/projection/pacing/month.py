"""Month pacing (Automated Pacing), Month Snapshot, payback box and the intraday Meta spend projection.

Month pacing compares a month's actuals so far with the scenario's P&L goal for that month. For every metric:
  MTD actual        sum over the days with data (through yesterday)
  Last 7-day avg    average per day over the last 7 full days
  MTD should be     goal x share of the month elapsed
  EOM goal          the P&L amount for the month
  EOM projection    MTD / days with data x days in the month (ratios carry their MTD value)
  Pacing            projection / goal; status colors read the direction (revenue and profit: more is better; costs and CPA: less is better)
  Daily needed      what each remaining day must average to land on the goal (ratios: the incremental ratio the rest of the month needs)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .feeds import Frame
from .periods import data_through as dthru, days_elapsed as dels, days_in_month, month_end
from .rollup import Period, aggregate, project
from . import levers as lv
from .levers import Levers
from .targets import TargetBook

GOOD, WARN, BAD, PLAN, NONE = "good", "warn", "bad", "plan", "none"


# ---------------------------------------------------------------------------------------------------------------- building blocks

@dataclass
class Amt:
    """A flow metric for one month: month-to-date total, per-day average of the last 7 days, and the month's goal."""
    mtd: float
    l7: float
    goal: float | None

    def __add__(self, o: "Amt") -> "Amt":
        g = None if self.goal is None or o.goal is None else self.goal + o.goal
        return Amt(self.mtd + o.mtd, self.l7 + o.l7, g)

    def __sub__(self, o: "Amt") -> "Amt":
        g = None if self.goal is None or o.goal is None else self.goal - o.goal
        return Amt(self.mtd - o.mtd, self.l7 - o.l7, g)


@dataclass
class Ctx:
    month: date
    book: TargetBook
    mtd: dict                  # summed bases, month start .. data_through
    l7: dict                   # summed bases, last 7 full days
    elapsed: int
    dim: int
    frac: float                # share of the month elapsed
    levers: Levers = field(default_factory=Levers)

    @property
    def remaining(self) -> int:
        return self.dim - self.elapsed


def base_amt(c: Ctx, key: str, goal: float | None) -> Amt:
    return Amt(c.mtd[key], c.l7[key] / c.levers.last_days, goal)


def plan_amt(c: Ctx, goal: float | None) -> Amt:
    """A plan-only line (no measured feed): spread evenly over the month."""
    g = goal or 0.0
    return Amt(g * c.frac, g / c.dim, goal)


def line_amt(c: Ctx, key: str, drivers: dict[str, Amt]) -> Amt:
    """A P&L cost line: rate x driver (variable) or goal x share elapsed (fixed)."""
    line = c.book.line(key)
    goal = c.book.line_goal(line, c.month)
    if line.driver == "fixed":
        return Amt(goal * c.frac, goal / c.dim, goal)
    rate = c.book.rate(line, c.month)
    d = drivers[line.driver]
    return Amt(d.mtd * rate, d.l7 * rate, goal)


# ---------------------------------------------------------------------------------------------------------------- the table

@dataclass
class Spec:
    key: str
    label: str
    group: str
    fmt: str
    direction: str | None            # 'up' | 'down' | None (no status)
    indent: int = 0
    kind: str = "line"               # line | subtotal | ratio | memo
    note: str = ""


def _status(p: float | None, direction: str | None, L: Levers | None = None) -> str:
    """Revenue and profit (up): on pace from pace_good, watch from pace_warn. Costs (down): within plan up to 1/pace_good-style 100%, watch up to cost_warn."""
    L = L or Levers()
    if p is None or not math.isfinite(p) or direction is None:
        return NONE
    if direction == "up":
        return GOOD if p >= L.pace_good else WARN if p >= L.pace_warn else BAD
    return GOOD if p <= 1 else WARN if p <= L.cost_warn else BAD


def _row(spec: Spec, mtd, l7, goal, projection, should_be, needed, L: Levers | None = None) -> dict:
    pacing = None
    if goal not in (None, 0) and projection is not None and (goal > 0 or spec.direction is None):       # a negative goal only reads as a pace on rows with no status
        pacing = projection / goal
    return {"key": spec.key, "label": spec.label, "group": spec.group, "fmt": spec.fmt, "indent": spec.indent, "kind": spec.kind,
            "direction": spec.direction, "note": spec.note, "mtd": mtd, "last7": l7, "goal": goal, "projection": projection, "should_be": should_be,
            "daily_needed": needed, "pacing": pacing, "status": _status(pacing, spec.direction, L)}


def amount_row(c: Ctx, spec: Spec, a: Amt, plan_only: bool = False) -> dict:
    proj = a.goal if plan_only else (a.mtd / c.elapsed * c.dim if c.elapsed else None)
    if plan_only and a.goal is None:
        proj = None
    should = a.goal * c.frac if a.goal is not None else None
    needed = (a.goal - a.mtd) / c.remaining if a.goal is not None and c.remaining > 0 else None
    return _row(spec, a.mtd, a.l7, a.goal, proj, should, needed, c.levers)


def ratio_row(c: Ctx, spec: Spec, num: Amt, den: Amt, scale: float = 1.0) -> dict:
    """num / den. The projection is the MTD ratio; the needed ratio is the incremental num / incremental den the rest of the month requires."""
    f = lambda n, d: (n / d * scale) if d else None                                  # noqa: E731
    mtd, l7 = f(num.mtd, den.mtd), f(num.l7, den.l7)
    goal = f(num.goal, den.goal) if num.goal is not None and den.goal is not None else None
    needed = None
    if num.goal is not None and den.goal is not None and den.goal - den.mtd > 0:
        needed = (num.goal - num.mtd) / (den.goal - den.mtd) * scale
    return _row(spec, mtd, l7, goal, mtd, goal, needed, c.levers)


def build_rows(c: Ctx) -> list[dict]:
    """Every metric row of the month pacing table, in sheet order."""
    bk = c.book
    m = c.month
    g = lambda name: bk.goal(name, m)                                                # noqa: E731
    dtc = base_amt(c, "o_total_rev", g("dtc_rev"))
    nc = base_amt(c, "o_nc_rev", g("nc_rev"))
    rep = base_amt(c, "o_rc_rev", g("repeat_rev"))
    amz = base_amt(c, "a_revenue", g("amazon_rev"))
    tax_rate = bk.tax_rate(m)
    tax = Amt(dtc.mtd * tax_rate, dtc.l7 * tax_rate, g("tax"))
    total = dtc + amz + tax
    total.goal = g("total_rev")
    dr = {"total_rev": total, "dtc_rev": dtc, "amazon_rev": amz}

    ncc = base_amt(c, "o_nc_orders", g("nc_orders"))
    meta = base_amt(c, "m_spend", g("spend_meta"))
    goog = base_amt(c, "g_spend", g("spend_google"))
    aads = base_amt(c, "a_ad_spend", g("spend_amazon"))
    tik = plan_amt(c, g("spend_tiktok"))
    adv = meta + goog + aads + tik
    adv.goal = g("spend_total")
    dtc_ad = meta + goog
    dtc_ad.goal = g("dtc_ad_spend")

    cl = {ln.key: line_amt(c, ln.key, dr) for ln in bk.lines_()}
    fees = cl["amazon_fees_var"] + cl["amazon_fees_fixed"]
    cogs_total = fees + cl["cogs_direct"] + cl["fulfillment"] + cl["inbound"] + cl["payment"]
    mkt = cl["mkt_fixed"] + cl["mkt_software"]
    ovh = cl["ovh_fixed"] + cl["ovh_dtc"] + cl["ovh_total"]
    opex = mkt + cl["other_biz"] + cl["headcount"] + ovh
    below_goal = bk.below_ebitda(m)
    ebitda = total - cogs_total - adv - opex
    ebitda.goal = bk.ebitda_goal(m)
    below = plan_amt(c, below_goal)
    noi = ebitda - below
    noi.goal = g("noi")

    rows: list[dict] = []
    add = rows.append
    S_ = Spec
    add(amount_row(c, S_("total_rev", "Revenue", "REVENUE", "money", "up", 0, "subtotal", "DTC + Amazon + estimated sales tax (net of tax)"), total))
    add(amount_row(c, S_("dtc_rev", "DTC revenue", "REVENUE", "money", "up", 1, note="Shopify total revenue"), dtc))
    add(amount_row(c, S_("nc_rev", "DTC new-customer revenue", "REVENUE", "money", "up", 1), nc))
    add(amount_row(c, S_("repeat_rev", "DTC repeat revenue", "REVENUE", "money", "up", 1), rep))
    add(ratio_row(c, S_("nc_roas", "DTC NC ROAS", "REVENUE", "x", "up", 1, "ratio", "New-customer revenue / DTC ad spend"), nc, dtc_ad))
    add(ratio_row(c, S_("nc_cpa", "DTC NC CPA", "REVENUE", "money2", "down", 1, "ratio", "DTC ad spend / new customers"), dtc_ad, ncc))
    add(amount_row(c, S_("amazon_rev", "Amazon revenue", "REVENUE", "money", "up", 1), amz))
    add(amount_row(c, S_("sales_tax", "Sales tax (est.)", "REVENUE", "money", None, 1, "memo", "DTC revenue x the plan's sales-tax rate"), tax))
    add(_proj_only(c, S_("refunds", "Refunds (actual)", "REVENUE", "money", None, 1, "memo", "Shopify refunds so far. The plan's revenue does not deduct refunds."),
                   base_amt(c, "o_refunds", None)))

    add(amount_row(c, S_("cogs", "Cost of goods sold", "COST OF GOODS SOLD", "money", "down", 0, "subtotal"), cogs_total))
    add(amount_row(c, S_("amazon_fees", "Amazon fees", "COST OF GOODS SOLD", "money", "down", 1), fees))
    add(amount_row(c, S_("cogs_direct", "COGS (direct)", "COST OF GOODS SOLD", "money", "down", 1), cl["cogs_direct"]))
    add(amount_row(c, S_("fulfillment", "Fulfillment", "COST OF GOODS SOLD", "money", "down", 1), cl["fulfillment"]))
    add(amount_row(c, S_("inbound", "Inbound shipping", "COST OF GOODS SOLD", "money", "down", 1), cl["inbound"]))
    add(amount_row(c, S_("payment", "Payment processing", "COST OF GOODS SOLD", "money", "down", 1), cl["payment"]))

    add(amount_row(c, S_("advertising", "Advertising", "ADVERTISING", "money", "down", 0, "subtotal"), adv))
    add(amount_row(c, S_("ad_meta", "Meta", "ADVERTISING", "money", "down", 1), meta))
    add(amount_row(c, S_("ad_google", "Google", "ADVERTISING", "money", "down", 1), goog))
    add(amount_row(c, S_("ad_amazon", "Amazon", "ADVERTISING", "money", "down", 1), aads))
    add(amount_row(c, S_("ad_tiktok", "TikTok (plan)", "ADVERTISING", "money", None, 1, "memo", "No daily feed: the plan spread over the month"), tik, plan_only=True))

    add(amount_row(c, S_("opex", "Other operating expenses", "OTHER OPERATING EXPENSES", "money", "down", 0, "subtotal"), opex))
    add(amount_row(c, S_("marketing_opex", "Marketing opex", "OTHER OPERATING EXPENSES", "money", "down", 1), mkt))
    add(amount_row(c, S_("other_business", "Other business", "OTHER OPERATING EXPENSES", "money", "down", 1), cl["other_biz"]))
    add(amount_row(c, S_("headcount", "Headcount", "OTHER OPERATING EXPENSES", "money", None, 1, "memo", "Fixed monthly plan"), cl["headcount"], plan_only=True))
    add(amount_row(c, S_("overhead", "Overhead", "OTHER OPERATING EXPENSES", "money", "down", 1), ovh))

    add(amount_row(c, S_("ebitda", "EBITDA", "PROFIT", "money", "up", 0, "total"), ebitda))
    add(amount_row(c, S_("below_ebitda", "Items below EBITDA", "PROFIT", "money", None, 1, "memo", "Marketing influencers + uncategorized, plan"), below, plan_only=True))
    add(amount_row(c, S_("noi", "NOI", "PROFIT", "money", "up", 0, "total", "Net operating income"), noi))
    cm_mtd = (total.mtd - cogs_total.mtd - adv.mtd) / total.mtd if total.mtd else None
    cm_l7 = (total.l7 - cogs_total.l7 - adv.l7) / total.l7 if total.l7 else None
    add(_row(S_("contribution_margin", "Contribution margin", "PROFIT", "pct1", None, 1, "ratio", "(Revenue - COGS - advertising) / revenue"), cm_mtd, cm_l7, None, None, None, None))
    need_ebitda = (ebitda.goal - ebitda.mtd) / c.remaining if ebitda.goal is not None and c.remaining > 0 else None
    add(_row(S_("required_daily_ebitda", "Required daily EBITDA, rest of month", "PROFIT", "money", None, 1, "memo"), None, None, None, None, None, need_ebitda))
    run = ebitda.mtd / c.elapsed if c.elapsed else None
    extra = None
    if need_ebitda is not None and run is not None and cm_mtd and cm_mtd > 0:
        extra = (need_ebitda - run) / cm_mtd
    add(_row(S_("required_extra_revenue", "Required additional daily revenue", "PROFIT", "money", None, 1, "memo", "Extra revenue per remaining day at the current contribution margin"),
             None, None, None, None, None, extra))

    add(amount_row(c, S_("nc_count", "New customers", "NEW CUSTOMERS (DTC)", "int", "up", 0), ncc))
    add(amount_row(c, S_("dtc_ad_spend", "DTC ad spend", "NEW CUSTOMERS (DTC)", "money", "down", 0, note="Meta + Google"), dtc_ad))
    add(amount_row(c, S_("nc_rev2", "DTC new-customer revenue", "NEW CUSTOMERS (DTC)", "money", "up", 0), nc))
    add(ratio_row(c, S_("nc_cpa2", "NC CPA", "NEW CUSTOMERS (DTC)", "money2", "down", 0, "ratio"), dtc_ad, ncc))
    add(ratio_row(c, S_("nc_aov", "NC AOV", "NEW CUSTOMERS (DTC)", "money2", "up", 0, "ratio", "New-customer revenue / new customers"), nc, ncc))
    add(ratio_row(c, S_("nc_roas2", "NC ROAS", "NEW CUSTOMERS (DTC)", "x", "up", 0, "ratio"), nc, dtc_ad))

    co_rev = Amt(nc.mtd + c.mtd["a_nc_rev"], nc.l7 + c.l7["a_nc_rev"] / c.levers.last_days, None)
    co_cus = Amt(ncc.mtd + c.mtd["a_nc_orders"], ncc.l7 + c.l7["a_nc_orders"] / c.levers.last_days, None)
    co_adv = Amt(adv.mtd, adv.l7, None)
    add(_proj_only(c, S_("co_nc_rev", "Company new-customer revenue", "COMPANY-WIDE (DTC + AMAZON)", "money", None, 0), co_rev))
    add(_proj_only(c, S_("co_nc_count", "Company new customers", "COMPANY-WIDE (DTC + AMAZON)", "int", None, 0), co_cus))
    add(ratio_row(c, S_("co_nc_cpa", "Company NC CPA", "COMPANY-WIDE (DTC + AMAZON)", "money2", None, 0, "ratio", "All advertising / company new customers"), co_adv, co_cus))
    add(ratio_row(c, S_("co_nc_aov", "Company NC AOV", "COMPANY-WIDE (DTC + AMAZON)", "money2", None, 0, "ratio"), co_rev, co_cus))
    add(ratio_row(c, S_("co_nc_roas", "Company NC ROAS", "COMPANY-WIDE (DTC + AMAZON)", "x", None, 0, "ratio"), co_rev, co_adv))
    return rows


def _proj_only(c: Ctx, spec: Spec, a: Amt) -> dict:
    return _row(spec, a.mtd, a.l7, None, a.mtd / c.elapsed * c.dim if c.elapsed else None, None, None, c.levers)


# ---------------------------------------------------------------------------------------------------------------- payback

def payback_months(aov: float | None, cac: float | None, product_cost_pct: float, fulfillment: float, curve: list[float], horizon: int = 36) -> dict:
    """Month in which cumulative contribution per customer reaches the acquisition cost.

    First-order margin cm = aov x (1 - product cost %) - fulfillment. After k months the cumulative margin is cm x (1 + the curve summed over ages 1..k).
    months = 0 when the first order already pays for the customer; None ('36+') when the horizon is not enough.
    """
    if not aov or not cac:
        return {"first_order_margin": None, "months": None, "label": "n/a"}
    cm = aov * (1 - product_cost_pct) - fulfillment
    if cm >= cac:
        return {"first_order_margin": cm, "months": 0, "label": "0"}
    cum, s = [], 0.0
    for x in curve[:horizon]:
        s += x
        cum.append(cm * (1 + s))
    for k, v in enumerate(cum, start=1):
        if v >= cac:
            return {"first_order_margin": cm, "months": k, "label": str(k)}
    return {"first_order_margin": cm, "months": None, "label": f"{horizon}+"}


def payback_params(scn_params: dict | None) -> tuple[float, float, int]:
    """Payback box inputs of the scenario: product cost share, fulfillment per order, horizon in months (levers; defaults are the sheet's Payback cells)."""
    L = lv.parse(scn_params)
    return L.product_cost_pct, L.fulfillment_per_order, L.payback_horizon


def payback_status(diff: int | None, L: Levers) -> str:
    """Payback months against the goal: on pace at or under it, behind up to `payback_bad_months` over, well behind beyond."""
    if diff is None:
        return NONE
    return GOOD if diff <= 0 else WARN if diff < L.payback_bad_months else BAD


def payback_box(rows: list[dict], cohorts: Any, scn_params: dict | None) -> dict | None:
    by = {r["key"]: r for r in rows}
    aov, cpa = by.get("nc_aov"), by.get("nc_cpa2")
    if aov is None or cpa is None:
        return None
    pc, ful, hz = payback_params(scn_params)
    live = list(getattr(cohorts, "curve_live", None) or [])
    goal = list(getattr(cohorts, "curve_goal", None) or [])
    if not live and not goal:
        return {"available": False, "reason": "The cohort curve is not available."}
    now = payback_months(aov["mtd"], cpa["mtd"], pc, ful, live or goal, hz)
    plan = payback_months(aov["goal"], cpa["goal"], pc, ful, goal or live, hz)
    diff = None if now["months"] is None or plan["months"] is None else now["months"] - plan["months"]
    return {"available": True, "product_cost_pct": pc, "fulfillment_per_order": ful, "horizon_months": hz,
            "aov": {"mtd": aov["mtd"], "goal": aov["goal"]}, "cpa": {"mtd": cpa["mtd"], "goal": cpa["goal"]},
            "first_order_margin": {"mtd": now["first_order_margin"], "goal": plan["first_order_margin"]},
            "months": {"mtd": now["months"], "mtd_label": now["label"], "goal": plan["months"], "goal_label": plan["label"], "diff": diff,
                       "status": payback_status(diff, lv.parse(scn_params))},
            "note": "Month-to-date AOV and CPA on the live curve versus the plan's AOV and CPA on the goal curve."}


# ---------------------------------------------------------------------------------------------------------------- intraday

def intraday_projection(hourly: list[float], spent: float, hours: float) -> dict:
    """Projected end-of-day Meta spend: spend so far / share of a typical day's spend already gone by `hours` (0-24, Pacific)."""
    total = float(sum(hourly))
    if len(hourly) != 24 or total <= 0:
        return {"share": None, "projected_eod": None, "to_go": None, "reason": "The hourly spend curve is not loaded."}
    hours = max(0.0, min(24.0, hours))
    f = min(int(hours), 23)
    cum = sum(hourly[:f]) / total
    share = cum + hourly[f] / total * (hours - f)
    if share <= 0:
        return {"share": 0.0, "projected_eod": None, "to_go": None, "reason": "Too early in the day to project."}
    eod = spent / share
    return {"share": share, "projected_eod": eod, "to_go": eod - spent}


# ---------------------------------------------------------------------------------------------------------------- orchestration

def month_context(fr: Frame, month: date, today: date, book: TargetBook) -> Ctx:
    L = book.levers
    thr = dthru(today, L.data_lag_days)
    elapsed = dels(month, today, L.data_lag_days)
    dim = days_in_month(month)
    p = Period("month", month.strftime("%Y-%m"), month, month_end(month), min(month_end(month), thr) if elapsed else month - timedelta(days=1), elapsed, dim, elapsed < dim)
    mtd = aggregate(fr, p) if elapsed else aggregate(fr, Period("month", "", month, month_end(month), month - timedelta(days=1), 0, dim, True))
    # the recent window: the last `last_days` full days ending at the last complete day (may start in the previous month)
    l7: dict = defaultdict(float)
    for k in range(L.last_days):
        d = thr - timedelta(days=k)
        row = fr.days.get(d)
        if row:
            for key, v in row.items():
                l7[key] += v
    return Ctx(month, book, mtd, l7, elapsed, dim, elapsed / dim if dim else 0.0, L)


def pacing_month(fr: Frame, month: date, today: date, book: TargetBook, cohorts: Any = None, scn_params: dict | None = None) -> dict:
    c = month_context(fr, month, today, book)
    L = book.levers
    rows = build_rows(c) if c.elapsed else []
    by = {r["key"]: r for r in rows}

    tl = tldr(by) if rows else []
    return {"month": month.isoformat(), "as_of": today.isoformat(), "data_through": dthru(today, L.data_lag_days).isoformat(), "days_in_month": c.dim,
            "days_elapsed": c.elapsed, "days_remaining": c.remaining, "share_elapsed": c.frac, "early": 0 < c.elapsed < L.early_days,
            "rows": rows, "tldr": tl, "payback": payback_box(rows, cohorts, scn_params) if rows else None,
            "missing_goal_rows": book.missing_rows()}


def _pct_text(p: float | None) -> str:
    return "n/a" if p is None else f"{p * 100:.0f}%"


def tldr(by: dict[str, dict]) -> list[dict]:
    """Three to five headline numbers for the page header."""
    out = []
    for key, label in (("total_rev", "Revenue"), ("noi", "NOI"), ("nc_roas", "NC ROAS"), ("advertising", "Ad spend"), ("nc_cpa", "NC CPA")):
        r = by.get(key)
        if r is None:
            continue
        out.append({"key": key, "label": label, "pacing": r["pacing"], "status": r["status"], "projection": r["projection"], "goal": r["goal"], "fmt": r["fmt"]})
    return out


def tldr_sentence(tl: list[dict]) -> str:
    parts = []
    names = {"total_rev": "Revenue tracking", "noi": "NOI", "nc_roas": "NC ROAS", "advertising": "Ad spend", "nc_cpa": "NC CPA"}
    for t in tl:
        if t["pacing"] is not None:
            parts.append(f"{names.get(t['key'], t['label'])} {_pct_text(t['pacing'])}")
    return "; ".join(parts) + (" of goal" if parts else "")


# ---------------------------------------------------------------------------------------------------------------- month snapshot

# Month Snapshot rows: (key, label, group, snapshot key in the month pacing table, direction). The snapshot is the month pacing table for the chosen month,
# so the page states one number per metric: the sheet's snapshot took EBITDA and NOI from a different estimate than its pacing tab.
SNAPSHOT_ROWS = (
    ("total_rev", "Total revenue", "REVENUE", "total_rev", "money", "up"),
    ("dtc_rev", "DTC revenue", "REVENUE", "dtc_rev", "money", "up"),
    ("nc_rev", "NC revenue", "REVENUE", "nc_rev", "money", "up"),
    ("repeat_rev", "Repeat revenue", "REVENUE", "repeat_rev", "money", "up"),
    ("amazon_rev", "Amazon revenue", "REVENUE", "amazon_rev", "money", "up"),
    ("nc_orders", "NC orders", "NEW-CUSTOMER ACQUISITION (DTC)", "nc_count", "int", "up"),
    ("dtc_ad_spend", "DTC ad spend", "NEW-CUSTOMER ACQUISITION (DTC)", "dtc_ad_spend", "money", "down"),
    ("nc_cpa", "NC CPA", "NEW-CUSTOMER ACQUISITION (DTC)", "nc_cpa2", "money2", "down"),
    ("nc_aov", "NC AOV", "NEW-CUSTOMER ACQUISITION (DTC)", "nc_aov", "money2", "up"),
    ("nc_roas", "NC ROAS", "NEW-CUSTOMER ACQUISITION (DTC)", "nc_roas2", "x", "up"),
    ("total_spend", "Total ad spend", "ADVERTISING", "advertising", "money", "down"),
    ("meta", "Meta", "ADVERTISING", "ad_meta", "money", "down"),
    ("google", "Google", "ADVERTISING", "ad_google", "money", "down"),
    ("amazon_ads", "Amazon ads", "ADVERTISING", "ad_amazon", "money", "down"),
    ("ebitda", "EBITDA", "PROFIT", "ebitda", "money", "up"),
    ("noi", "NOI", "PROFIT", "noi", "money", "up"),
)


def snapshot_month(fr: Frame, month: date, today: date, book: TargetBook, cohorts: Any = None, scn_params: dict | None = None) -> dict:
    """Month Snapshot: Actual / Projected* / Target / Diff / Diff % for any month. A running month projects at its run-rate; a closed month is its own projection."""
    L = book.levers
    elapsed = dels(month, today, L.data_lag_days)
    dim = days_in_month(month)
    partial = 0 < elapsed < dim
    by: dict[str, dict] = {}
    if elapsed:
        by = {r["key"]: r for r in build_rows(month_context(fr, month, today, book))}
    out = []
    for key, label, group, src, fmt, direction in SNAPSHOT_ROWS:
        r = by.get(src)
        actual = None if r is None else r["mtd"]
        projected = None if r is None else r["projection"]
        target = None if r is None else r["goal"]
        diff = None if projected is None or target is None else projected - target
        diff_pct = None if diff is None or not target else projected / target - 1
        out.append({"key": key, "label": label, "group": group, "fmt": fmt, "direction": direction, "actual": actual, "projected": projected, "target": target,
                    "diff": diff, "diff_pct": diff_pct, "status": _status(None if diff_pct is None else 1 + diff_pct, direction, L)})
    vals = {r["key"]: r for r in out}
    pc, ful, hz = payback_params(scn_params)
    pb = None
    if elapsed:
        live = list(getattr(cohorts, "curve_live", None) or [])
        goal_curve = list(getattr(cohorts, "curve_goal", None) or [])
        if live or goal_curve:
            now = payback_months(vals["nc_aov"]["actual"], vals["nc_cpa"]["actual"], pc, ful, live or goal_curve, hz)
            plan = payback_months(vals["nc_aov"]["target"], vals["nc_cpa"]["target"], pc, ful, goal_curve or live, hz)
            pb = {"first_order_margin": {"actual": now["first_order_margin"], "target": plan["first_order_margin"]},
                  "months": {"actual": now["months"], "actual_label": now["label"], "target": plan["months"], "target_label": plan["label"]}}
    return {"month": month.isoformat(), "partial": partial, "days_elapsed": elapsed, "days_in_month": dim, "scale": (dim / elapsed) if partial else 1.0,
            "rows": out, "payback": pb,
            "status_text": ("Month to date: %d of %d days" % (elapsed, dim)) if partial else ("Closed month" if elapsed else "No data for this month yet")}
