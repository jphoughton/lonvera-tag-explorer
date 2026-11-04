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
from . import payback, reference as ref_mod
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


def summary(d: DriversResult, rl: RevenueLines, curve: list[float], today: date) -> dict:
    """Headline numbers. The next window starts at the open month; the window before it ends with the last closed month."""
    n = d.levers.summary_months
    band = d.levers.posture_band
    anchor = add_months(month_floor(today), -1)
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


def status_line(d: DriversResult) -> dict:
    """The three-state month status in words, for the header chips and the source caption."""
    feeds = [m for m in MONTHS if d.month_status.get(m) == "feeds"]
    proj = next((m for m in MONTHS if d.month_status.get(m) == "projected" and (d.feeds_through is None or m > d.feeds_through) and
                 (d.books_through is None or m > d.books_through)), None)
    return {"books_through": d.books_through.isoformat() if d.books_through else None,
            "feeds_from": feeds[0].isoformat() if feeds else None, "feeds_to": feeds[-1].isoformat() if feeds else None,
            "projected_from": proj.isoformat() if proj else None}


def status_caption(sl: dict) -> str:
    def mon(iso):
        return date.fromisoformat(iso).strftime("%b %Y")
    parts = []
    if sl["books_through"]:
        parts.append(f"Books through {mon(sl['books_through'])}.")
    if sl["feeds_from"]:
        span = mon(sl["feeds_from"]) if sl["feeds_from"] == sl["feeds_to"] else f"{mon(sl['feeds_from'])} to {mon(sl['feeds_to'])}"
        parts.append(f"Feeds only, no books yet: {span}.")
    if sl["projected_from"]:
        parts.append(f"Projected from {mon(sl['projected_from'])}*.")
    return " ".join(parts)


def reference_payload(d: DriversResult, ref: "ref_mod.Reference | None", plan_month: Month, today: date) -> dict:
    """Read-only references beside the plan: the realized CPA chip and the Amazon feed note (None when the feeds hold nothing)."""
    out: dict = {"window_days": d.levers.reference_days, "tolerance": d.levers.reference_tolerance, "cpa": None, "amazon": None}
    if ref is None:
        return out
    n, tol = ref.window_days, d.levers.reference_tolerance
    plan_cpa = d.plan_cpa.get(plan_month)
    w = ref.cpa_window
    if w and plan_cpa:
        diff = plan_cpa / w["cpa"] - 1.0
        out["cpa"] = {"plan": plan_cpa, "plan_month": plan_month.isoformat(), "realized": w["cpa"], "diff": diff, "window": {"from": w["from"], "to": w["to"]},
                      "chip": f"Plan ${plan_cpa:,.0f} vs last {n} days ${w['cpa']:,.0f}", "tone": "warn" if abs(diff) > tol else "info"}
    series_ = ref_mod.amazon_series(ref, today)
    if series_:
        codes = cell_codes(d).get(SERIES_PREFIX + "amazon_rev", {})
        over = []
        for m in MONTHS:
            feed = series_.get(m)
            typed = codes.get(m.isoformat()) in ("typed", "carried")
            if not feed or not typed:
                continue
            diff = d.amazon_rev.get(m, 0.0) / feed - 1.0
            if abs(diff) > tol:
                over.append((m, diff))
        note = None
        if over:
            closed = [(m, x) for m, x in over if m < month_floor(today)]
            planned = [(m, x) for m, x in over if m >= month_floor(today)]
            bits = [f"{m:%b %Y} {x:+.0%}" for m, x in closed]
            if planned:
                bits.append(f"{planned[0][0]:%b %Y}{' on' if len(planned) > 1 else ''} {planned[0][1]:+.0%} (last {n} days)")
            note = "Typed Amazon revenue against the feed: " + ", ".join(bits) + "."
        w2 = ref.amazon_window
        out["amazon"] = {"run_rate_month": (w2["daily"] * ref_mod.AVG_DAYS_PER_MONTH) if w2 else None, "note": note,
                         "window": {"from": w2["from"], "to": w2["to"]} if w2 else None}
    return out


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


def build(res, scenario_name: str, scenario_slug: str, version: int, today: date, reference: "ref_mod.Reference | None" = None) -> dict:
    d: DriversResult = res.drivers
    rl = revenue_lines(d, res.cohorts.repeat_rev)
    curve = list(res.cohorts.curve_live or [])
    at = d.actual_through                                   # = books_through
    pb = d.levers.payback
    nref = d.levers.reference_days
    plan_vals = _plan_values(d)
    rows: list[dict] = []

    rows.append(_row("h_plan", "Spend plan", None, "header"))
    fmts = {"spend_total": "usd0", "nc_cpa": "usd", "nc_aov": "usd", "amazon_rev": "usd0", "tiktok_rev": "usd0"}
    for key in PLAN_KEYS:
        rows.append(_row(SERIES_PREFIX + key, PLAN_LABELS[key], plan_vals[key], fmt=fmts[key], input=True, note=PLAN_NOTES[key]))
        if key == "nc_cpa" and reference is not None and reference.cpa_by_month:
            rows.append(_row("ref_cpa", "Realized CPA", reference.cpa_by_month, "ratio", "usd", missing=None, agg="none",
                             note="Read only. Ad spend in the Plan settings channels per new customer, by closed month, from the Meta feed, the ad-platform feed and Shopify new customers."))
        if key == "amazon_rev" and reference is not None:
            amz = ref_mod.amazon_series(reference, today)
            if amz:
                rows.append(_row("ref_amazon", f"Amazon feed run-rate (last {nref} days)", amz, "ratio", "usd0", missing=None, agg="none",
                                 note="Read only. Amazon feed revenue: the monthly total for closed months the feed covers, the daily average of the last days times the average month length from the open month on."))
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
    words = {"books": "Books", "feeds": "Feeds", "projected": "Projected"}
    rows.append(_row("month_status", "Month status", [words[d.month_status.get(m, "projected")] for m in MONTHS], "ratio", "text", agg="none",
                     note="Books: QuickBooks loaded. Feeds: closed month with cohort or ad-feed data and no books yet. Projected: the plan."))
    sl = status_line(d)

    summ = summary(d, rl, curve, today)
    plan_month = date.fromisoformat(summ["payback_from"])
    return {"months": [m.isoformat() for m in MONTHS], "status": ["actual" if d.month_status.get(m) == "books" else "projected" for m in MONTHS], "rows": rows,
            "captions": [("Source: " + status_caption(sl) + " Cells marked * are projected or feeds only.").strip()],
            "cellmeta": cell_codes(d), "summary": summ, "levers": d.levers.to_dict(), "month_status": [d.month_status.get(m, "projected") for m in MONTHS],
            "status_line": sl, "reference": reference_payload(d, reference, plan_month, today),
            "scenario": {"slug": scenario_slug, "name": scenario_name, "version": version}, "actual_through": at.isoformat() if at else None,
            "feeds_through": d.feeds_through.isoformat() if d.feeds_through else None, "nc_source": d.nc_source}
