"""Grid payload and summary for the Spend plan page.

build(res, scn_name) -> the /table payload: {months, status, rows, captions} (shared grid contract) plus
  cellmeta   {row_key: {iso_month: 'typed' | 'carried' | 'slid' | 'actual' | 'override'}}   how each cell of the plan rows came to be
  summary    headline numbers for the TLDR strip and the growth / harvest read
  levers     the effective levers
"""
from __future__ import annotations

import math
from datetime import date

from ..interfaces import MONTHS, Month, add_months, month_floor, series_to_list
from . import payback
from .model import OVERRIDE_KEYS, PLAN_KEYS, SERIES_PREFIX, DriversResult
from .revenue import RevenueLines, revenue_lines

PLAN_LABELS = {
    "spend_total": "Marketing spend",
    "nc_cpa": "New-customer CPA",
    "nc_aov": "New-customer AOV",
    "amazon_rev": "Amazon revenue",
    "tiktok_rev": "TikTok revenue",
}
PLAN_NOTES = {
    "spend_total": "All channels (Meta, Google, Amazon ads, Bing, TikTok). Actual months show what was spent; planned months are typed or carried from the month before.",
    "nc_cpa": "Cost per acquisition: ad spend (Meta and Google) per new customer. Planned months are typed, set by the NC-ROAS slide, or carried.",
    "nc_aov": "Average order value: first-order revenue per new customer. Planned months are typed or carried.",
    "amazon_rev": "Loaded actuals for closed months; typed or carried for planned months.",
    "tiktok_rev": "Loaded actuals for closed months; typed or carried for planned months.",
}
OVERRIDE_LABELS = {
    "ad_spend": "Ad spend, typed",
    "nc_orders": "New-customer orders, typed",
    "nc_rev": "New-customer revenue, typed",
    "repeat_rev": "Repeat revenue, typed",
}
OVERRIDE_NOTE = "Replaces the computed value for that month. Clear the cell to go back to the model."


def _num(v):
    return None if v is None else float(v)


def _plan_values(d: DriversResult) -> dict[str, dict[Month, float]]:
    return {"spend_total": d.spend_total, "nc_cpa": d.plan_cpa, "nc_aov": d.plan_aov, "amazon_rev": d.amazon_rev, "tiktok_rev": d.tiktok_rev}


def cell_codes(d: DriversResult) -> dict[str, dict[str, str]]:
    """How each plan cell came to be. Typed = a cell in the scenario; carried = the month before; slid = NC-ROAS slide; actual = loaded data."""
    out: dict[str, dict[str, str]] = {}
    for key in PLAN_KEYS:
        info = d.cell_info.get(key, {})
        codes: dict[str, str] = {}
        for m in MONTHS:
            if key == "spend_total" and d.source.get(m) != "plan":
                code = "actual"
            elif key in ("nc_cpa", "nc_aov") and d.nc_basis.get(m) != "plan":
                code = "actual"
            elif key == "nc_cpa" and m in d.slid:
                code = "slid"
            elif m in info and info[m]["kind"] in ("input", "override"):
                code = "typed"
            elif m in d.carried.get(key, ()):
                code = "carried"
            elif key in ("amazon_rev", "tiktok_rev"):
                code = "actual"
            else:
                code = "carried"
            codes[m.isoformat()] = code
        out[SERIES_PREFIX + key] = codes
    for key in OVERRIDE_KEYS:
        out[SERIES_PREFIX + key] = {m.isoformat(): "override" for m in d.cell_info.get(key, {})}
    return out


def _years(ss: dict[Month, float], year: int) -> float:
    return sum(v for m, v in ss.items() if m.year == year)


def _window(ss: dict[Month, float], start: Month, n: int) -> float:
    return sum(ss.get(add_months(start, i), 0.0) for i in range(n))


def summary(d: DriversResult, rl: RevenueLines, curve: list[float], actual_through: date | None, today: date) -> dict:
    """Headline numbers. 'Next 12' starts the month after the last actual month; 'trailing 12' is the 12 months up to and including it."""
    n = d.levers.summary_months
    band = d.levers.posture_band
    anchor = actual_through or add_months(month_floor(today), -1)
    nxt = add_months(anchor, 1)
    prev_start = add_months(anchor, 1 - n)
    spend_prev, spend_next = _window(d.spend_total, prev_start, n), _window(d.spend_total, nxt, n)
    ad_next, nc_next = _window(d.ad_spend, nxt, n), _window(d.nc_rev, nxt, n)
    change = (spend_next / spend_prev - 1.0) if spend_prev > 0 else None
    posture = "unknown" if change is None else "harvest" if change <= -band else "growth" if change >= band else "hold"
    pb = d.levers.payback
    pm_month = next((m for m in MONTHS if m >= nxt and d.nc_basis.get(m) == "plan"), nxt)          # first month driven by the plan, not by measured customers
    cpa, aov = d.plan_cpa.get(pm_month, 0.0), d.plan_aov.get(pm_month, 0.0)
    pm = payback.payback_month(aov, cpa, curve, pb["product_cost_pct"], pb["fulfillment_per_order"], int(pb["horizon_months"]))
    fy = {}
    for y in sorted({m.year for m in MONTHS}):
        ad_y = _years(d.ad_spend, y)
        fy[str(y)] = {"spend_total": _years(d.spend_total, y), "ad_spend": ad_y, "nc_rev": _years(d.nc_rev, y), "nc_orders": _years(d.nc_orders, y),
                      "nc_roas": (_years(d.nc_rev, y) / ad_y) if ad_y > 0 else None, "repeat_rev": _years(rl.repeat_rev, y),
                      "total_revenue": _years(rl.total_revenue, y)}
    return {"anchor": anchor.isoformat(), "next_from": nxt.isoformat(), "spend_prev_12": spend_prev, "spend_next_12": spend_next,
            "spend_change": change, "posture": posture, "nc_rev_next_12": nc_next, "ad_spend_next_12": ad_next,
            "nc_roas_next_12": (nc_next / ad_next) if ad_next > 0 else None, "plan_cpa": cpa, "plan_aov": aov, "payback_months": pm, "payback_from": pm_month.isoformat(), "open_month": month_floor(today).isoformat(),
            "total_revenue_next_12": _window(rl.total_revenue, nxt, n), "window_months": n, "posture_band": band, "fy": fy}


def _row(key: str, label: str, values, kind: str = "calc", fmt: str = "usd0", *, input: bool = False, missing: float | None = 0.0, **extra) -> dict:
    """One grid row in the shell's vocabulary (kind: input | calc | subtotal | total | header | ratio, fmt: usd | usd0 | num | x | pct | text)."""
    if kind == "header":
        vals = [None] * len(MONTHS)
    elif isinstance(values, dict):
        vals = series_to_list(values, MONTHS, missing)
    else:
        vals = list(values)
    vals = [None if isinstance(v, float) and not math.isfinite(v) else v for v in vals]
    return {"key": key, "label": label, "kind": "input" if input else kind, "fmt": fmt, "values": vals, "input": bool(input), **extra}


def build(res, scenario_name: str, scenario_slug: str, version: int, today: date) -> dict:
    d: DriversResult = res.drivers
    rl = revenue_lines(d, res.cohorts.repeat_rev)
    curve = list(res.cohorts.curve_live or [])
    at = d.actual_through
    pb = d.levers.payback
    plan_vals = _plan_values(d)
    rows: list[dict] = []

    rows.append(_row("h_plan", "Spend plan", None, "header"))
    fmts = {"spend_total": "usd0", "nc_cpa": "usd", "nc_aov": "usd", "amazon_rev": "usd0", "tiktok_rev": "usd0"}
    for key in PLAN_KEYS:
        rows.append(_row(SERIES_PREFIX + key, PLAN_LABELS[key], plan_vals[key], fmt=fmts[key], input=True, note=PLAN_NOTES[key]))
    rows.append(_row("h_typed", "Typed over the model", None, "header"))
    for key in OVERRIDE_KEYS:
        rows.append(_row(SERIES_PREFIX + key, OVERRIDE_LABELS[key], d.overrides.get(key, {}), input=True, missing=None, note=OVERRIDE_NOTE))

    rows.append(_row("h_nc", "New customers", None, "header"))
    rows.append(_row("ad_spend", "Ad spend", d.ad_spend, note="The new-customer denominator: the channels chosen in Plan settings (Meta and Google by default), or the typed value."))
    rows.append(_row("nc_orders", "New-customer orders", d.nc_orders, fmt="num"))
    rows.append(_row("nc_rev", "New-customer revenue", d.nc_rev, "subtotal"))
    rows.append(_row("nc_roas", "NC-ROAS", d.nc_roas, "ratio", "x", agg="none", note="New-customer revenue per dollar of ad spend."))
    paybacks = [payback.payback_month(d.plan_aov.get(m, 0.0), d.plan_cpa.get(m, 0.0), curve, pb["product_cost_pct"], pb["fulfillment_per_order"],
                                      int(pb["horizon_months"])) for m in MONTHS]
    rows.append(_row("payback", "Payback, months", [None if p is None else float(p) for p in paybacks], "ratio", "num", digits=0, agg="none",
                     note="Months after the first order until a new customer's contribution covers the CPA, on the live retention curve."))
    rows.append(_row("nc_rev_cohort", "Cohort table revenue", d.nc_rev_cohort, "ratio", missing=None,
                     note="New-customer revenue per the cohort table, shown next to any typed value."))

    rows.append(_row("h_channels", "Marketing spend by channel", None, "header"))
    for key, label, ser in (("spend_meta", "Meta", d.spend_meta), ("spend_google", "Google", d.spend_google),
                            ("spend_amazon_ads", "Amazon ads", d.spend_amazon_ads), ("spend_tiktok", "TikTok ads", d.spend_tiktok),
                            ("spend_bing", "Bing ads", d.spend_bing)):
        rows.append(_row(key, label, ser))
    gap = {m: d.meta_feed[m] - d.meta_books[m] for m in d.meta_feed if m in d.meta_books}
    if gap:
        rows.append(_row("meta_gap", "Meta feed less books", gap, "ratio", missing=None))

    rows.append(_row("h_rev", "Revenue", None, "header"))
    rows.append(_row("repeat_rev", "Repeat revenue", rl.repeat_rev))
    rows.append(_row("rev_nc", "New-customer revenue", d.nc_rev))
    rows.append(_row("dtc_rev", "Direct-to-consumer revenue", rl.dtc_rev, "subtotal", note="Repeat plus new-customer revenue; the books value for closed months."))
    rows.append(_row("rev_amazon", "Amazon revenue", rl.amazon_rev))
    rows.append(_row("rev_tiktok", "TikTok revenue", rl.tiktok_rev))
    rows.append(_row("sales_tax", "Sales tax collected (estimate)", rl.sales_tax, note="Negative: an estimate of the tax in direct-to-consumer revenue, set in Plan settings. Closed months from the books carry none."))
    rows.append(_row("total_revenue", "Total company revenue", rl.total_revenue, "total"))
    mer = {m: (rl.total_revenue[m] / d.spend_total[m]) if d.spend_total.get(m, 0.0) > 0 else None for m in MONTHS}
    rows.append(_row("mer", "Revenue per marketing dollar", mer, "ratio", "x", missing=None, agg="none", note="Total company revenue divided by marketing spend."))

    rows.append(_row("h_src", "Data source", None, "header"))
    labels = {"books": "Books", "meta_feed": "Meta feed", "plan": "Plan"}
    rows.append(_row("source", "Spend source", [labels[d.source.get(m, "plan")] for m in MONTHS], "ratio", "text", agg="none"))

    return {"months": [m.isoformat() for m in MONTHS], "status": ["actual" if at and m <= at else "projected" for m in MONTHS], "rows": rows,
            "captions": ["Source: books and Meta feed for closed months; the scenario's spend plan, typed cells and levers for the rest. Projected months are marked *."],
            "cellmeta": cell_codes(d), "summary": summary(d, rl, curve, at, today), "levers": d.levers.to_dict(),
            "scenario": {"slug": scenario_slug, "name": scenario_name, "version": version}, "actual_through": at.isoformat() if at else None,
            "nc_source": d.nc_source}
