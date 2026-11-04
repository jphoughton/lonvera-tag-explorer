"""Payload builders for the pnl API: the P&L grid, the trend grid, the summary strip, the driver table. Pure functions over a PnlOutput."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from ..interfaces import MONTHS, add_months, series_to_list
from . import lines as L
from .model import PnlOutput

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TOTAL_KEYS = ("total_revenue", "gross_profit", "noi")
SUBTOTAL_CALC = ("total_cogs", "total_expenses", "non_media_expenses")
BASE_LABEL = {"amazon_rev": "Amazon revenue", "dtc_rev": "DTC revenue", "total_revenue": "total company revenue"}
TREND_KEYS = ("ttm_revenue", "ttm_noi", "ttm_noi_margin", "ftm_revenue", "ftm_noi", "dtc_rev_mom", "dtc_rev_mom_6m", "dtc_rev_yoy", "dtc_rev_yoy_6m",
              "total_rev_mom", "total_rev_mom_6m", "total_rev_yoy", "total_rev_yoy_6m", "noi_mom", "noi_mom_6m", "noi_yoy", "noi_yoy_6m")
DRIVER_GROUPS: list[tuple[str, list[str]]] = [
    ("Amazon fees", ["amazon_fba_fees", "amazon_seller_fees", "amazon_subscriptions", "amazon_inbound_transport"]),
    ("Cost of goods sold", ["china_cogs", "us_cogs", "inventory_shrinkage", "fulfillment_services", "inbound_shipping", "shipping_supplies",
                            "shopify_subscriptions", "seller_fees"]),
    ("Marketing", ["marketing_agency", "marketing_services", "marketing_influencers", "marketing_software"]),
    ("Other business expenses", ["other_business_expenses"]),
    ("Headcount", ["headcount"]),
    ("Overhead", ["accounting", "chargeback_management", "consulting", "customer_service", "insurance", "interest_expense", "legal", "memberships",
                  "office_admin", "outside_services", "sales_tax_filing", "samples", "software_apps"]),
    ("Other", ["uncategorized"]),
]


def month_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def money(v: float, digits: int = 0) -> str:
    s = f"{abs(v):,.{digits}f}"
    return f"-${s}" if v < 0 else f"${s}"


def pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%"


def window_text(months: list[str]) -> str:
    if not months:
        return ""
    a, b = date.fromisoformat(months[0]), date.fromisoformat(months[-1])
    if a == b:
        return f"{MONTH_NAMES[a.month - 1]} {a.year}"
    ya = "" if a.year == b.year else f" {a.year}"
    return f"{MONTH_NAMES[a.month - 1]}{ya} to {MONTH_NAMES[b.month - 1]} {b.year}"


def segment_text(seg: dict | None, info: dict | None = None, cogs: dict | None = None) -> str:
    """One plain sentence for a driver step."""
    if not seg:
        return "Not set"
    mode = seg["mode"]
    if mode == "fixed":
        return f"{money(seg.get('value', 0.0), 2 if abs(seg.get('value', 0.0)) < 100 and seg.get('value') else 0)} a month"
    if mode == "carry":
        return "Last month's amount carried forward"
    if mode == "avg":
        return f"Mean of the previous {seg['window']} months"
    if mode == "headcount":
        return "Base amount plus a step as annualised revenue grows"
    base = BASE_LABEL.get(seg.get("base"), seg.get("base", ""))
    kind = seg.get("rate_kind", "window")
    if kind == "typed":
        return f"{pct(seg.get('rate', 0.0), 2)} of {base}"
    if kind == "cogs_model":
        r = cogs["rate"] if cogs else None
        return f"Cost of goods model{(' ' + pct(r, 2)) if r is not None else ''} of {base}"
    if kind == "ratio_avg":
        return f"Mean monthly ratio to {base} over the last {seg['window']} actual months"
    return f"Share of {base} over the last {seg.get('window') or 'N'} actual months" if not info else f"{pct(info.get('rate', 0.0), 2)} of {base}"


# ---------------------------------------------------------------------------------------------------------------- the grid

def _row_kind(key: str) -> str:
    ln = L.LINE_KEYS[key]
    if ln.kind == "ratio":
        return "ratio"
    if key in TOTAL_KEYS:
        return "total"
    if ln.kind == "sum" or key in SUBTOTAL_CALC:
        return "subtotal"
    return "calc"


def _driver_note(key: str, out: PnlOutput) -> str | None:
    info = out.driver_info.get(key)
    if not info or not info.get("mode"):
        return None
    segs = info.get("segments") or []
    probe = segs[-1] if segs else None
    txt = segment_text(probe, info if probe and probe.get("mode") == "rate" and probe.get("rate_kind", "window") == "window" else None, out.cogs_model)
    w = info.get("window")
    if w and probe and probe.get("mode") == "rate" and probe.get("rate_kind", "window") in ("window", "ratio_avg"):
        txt += f" ({window_text(w)})"
    return "Projected months: " + txt


def span_text(months: list[date]) -> str:
    """'Aug-Oct 2026', 'Dec 2026-Feb 2027', 'Aug 2026'."""
    if not months:
        return ""
    a, b = months[0], months[-1]
    if a == b:
        return f"{MONTH_NAMES[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{MONTH_NAMES[a.month - 1]}-{MONTH_NAMES[b.month - 1]} {b.year}"
    return f"{MONTH_NAMES[a.month - 1]} {a.year}-{MONTH_NAMES[b.month - 1]} {b.year}"


def status_summary(out: PnlOutput) -> dict:
    """The three-state boundary in words, the same on every page: books (QuickBooks loaded), feeds (closed month with cohort / ad-feed actuals, no books),
    projected."""
    feeds = [m for m in MONTHS if out.month_status.get(m) == "feeds"]
    last_real = max([d for d in (out.books_through, out.feeds_through) if d], default=None)
    first_proj = next((m for m in MONTHS if out.month_status.get(m) == "projected"), None)
    parts = [f"Books through {month_label(out.books_through.isoformat())}" if out.books_through else "No books loaded"]
    if feeds:
        parts.append(f"Feeds only, no books yet: {span_text(feeds)}")
    if first_proj:
        parts.append(f"Projected from {month_label(first_proj.isoformat())}")
    return {"text": ". ".join(parts) + ".", "books_through": out.books_through.isoformat() if out.books_through else None,
            "feeds_through": out.feeds_through.isoformat() if out.feeds_through else None, "feeds_span": span_text(feeds),
            "feeds_months": [m.isoformat() for m in feeds], "projected_from": first_proj.isoformat() if first_proj else None,
            "last_real": last_real.isoformat() if last_real else None}


def table_payload(out: PnlOutput, *, sources: dict[date, str] | None = None) -> dict:
    """The P&L grid in the shared grid's vocabulary (kind input/calc/subtotal/total/header/ratio, fmt usd0/pct). The grid knows two month states
    (actual / projected): books months are actual, feeds and projected months are projected (their cost lines are). `month_status` carries the three
    words for the legend."""
    through = out.books_through
    status = ["actual" if through and m <= through else "projected" for m in MONTHS]
    rows: list[dict] = []
    fy_rows: dict[str, list] = {}
    years = sorted(out.fy)
    for title, keys in L.SECTIONS:
        rows.append({"key": "hdr_" + title.lower().replace(" ", "_"), "label": title, "kind": "header", "fmt": "usd0", "values": [None] * len(MONTHS),
                     "input": False})
        for k in keys:
            ln = L.LINE_KEYS[k]
            driven = k in L.DRIVER_KEYS or k in L.DRIVEN_PARENTS
            vals = series_to_list(out.lines.get(k, {}), MONTHS, None)
            # Not an input row: the shared grid would make every month editable, QuickBooks months included. Projected months are changed through the
            # driver table (a new step, or a typed month), so the cells here stay calculated.
            row = {"key": k, "label": ln.label, "kind": _row_kind(k), "fmt": ln.fmt, "values": vals, "input": False,
                   "indent": ln.indent if ln.indent else None}
            if row["indent"] is None:
                row.pop("indent")
            note = _driver_note(k, out) if driven else None
            if note:
                row["note"] = note
            if ln.fmt == "pct":
                row["agg"] = "none"
            rows.append(row)
            fy_rows[k] = [out.fy[y].get(k) for y in years]
    cap = "Source: QuickBooks through " + (month_label(through.isoformat()) if through else "(none loaded)") + "; later months projected from the drivers"
    return {"months": [m.isoformat() for m in MONTHS], "status": status, "month_status": [out.month_status.get(m, "projected") for m in MONTHS],
            "status_summary": status_summary(out), "rows": rows, "captions": [cap],
            "fy": {"years": years, "rows": fy_rows}, "actual_through": through.isoformat() if through else None,
            "sources": {m.isoformat(): s for m, s in (sources or {}).items()}}


def metrics_payload(out: PnlOutput) -> dict:
    status = ["actual" if out.actual_through and m <= out.actual_through else "projected" for m in MONTHS]
    rows = []
    for k in TREND_KEYS:
        ln = L.LINE_KEYS[k]
        rows.append({"key": k, "label": ln.label, "kind": "ratio" if ln.fmt == "pct" else "calc", "fmt": ln.fmt,
                     "values": series_to_list(out.lines.get(k, {}), MONTHS, None), "input": False})
    return {"months": [m.isoformat() for m in MONTHS], "status": status, "rows": rows,
            "captions": ["Calculated from the P&L above; growth is blank where the earlier month is zero (net operating income: not positive)"]}


# ---------------------------------------------------------------------------------------------------------------- summary

def summary(out: PnlOutput, freshness: dict | None = None, today: date | None = None, stale_days: int | None = None) -> dict:
    ss = status_summary(out)
    ev = out.ev or {}
    year = ev["basis_year"]
    fy = out.fy.get(year) or {}
    actual_year = out.actual_through.year if out.actual_through else None
    mults = {k: ev.get(k) for k in ev if k.endswith("x")}
    valid = [v for v in mults.values() if v is not None]
    through = out.actual_through
    next_month = None
    qb_text = ss["text"]
    due = None
    if through and stale_days is not None:
        nxt = add_months(through, 1)
        end = date(nxt.year, nxt.month, calendar.monthrange(nxt.year, nxt.month)[1])
        due = {"month": nxt.isoformat(), "due_by": (end + timedelta(days=stale_days)).isoformat()}
    mults = ev.get("multiples") or []
    multiple_range = f"{min(mults):g}x to {max(mults):g}x" if mults else ""
    return {"next_due": due, "multiple_range": multiple_range, "basis_year": year, "revenue": fy.get("total_revenue"), "noi": fy.get("noi"), "noi_margin": fy.get("noi_margin"),
            "gross_profit_pct": fy.get("gross_profit_pct"), "ev": mults, "ev_low": min(valid) if valid else None, "ev_high": max(valid) if valid else None,
            "loi": ev.get("loi"), "ttm_noi": (out.ttm.get(out.actual_through) if out.actual_through else None),
            "actual_through": through.isoformat() if through else None, "actual_year": actual_year, "quickbooks_text": qb_text, "status": ss,
            "freshness": freshness, "cogs_rate": out.cogs_model.get("rate"), "cogs_rate_at_current_price": out.cogs_model.get("rate_at_current_price"),
            "warnings": out.warnings}


# ---------------------------------------------------------------------------------------------------------------- driver table

def drivers_payload(out: PnlOutput, defaults: dict[str, list[dict]], scenario_overrides: dict[str, Any], notes: dict[str, dict]) -> dict:
    groups = []
    for title, keys in DRIVER_GROUPS:
        items = []
        for k in keys:
            info = out.driver_info.get(k, {})
            segs = info.get("segments") or []
            first = segs[0] if segs else None
            current = segs[-1] if segs else None
            e = {"key": k, "label": L.LINE_KEYS[k].label, "mode": info.get("mode"), "segments": segs, "default_segments": defaults.get(k) or [],
                 "overridden": k in scenario_overrides, "text": segment_text(current, info, out.cogs_model),
                 "source": (notes.get(k) or {}).get("source"), "note": (notes.get(k) or {}).get("note")}
            if info.get("rate") is not None:
                e["rate"] = info["rate"]
                e["base"] = info.get("base")
                e["window"] = info.get("window") or []
                e["window_text"] = window_text(e["window"])
            if info.get("value") is not None:
                e["value"] = info["value"]
            items.append(e)
        groups.append({"title": title, "items": items})
    return {"groups": groups, "settings": out.settings, "cogs_model": out.cogs_model,
            "window": [m.isoformat() for m in out.window], "window_text": window_text([m.isoformat() for m in out.window]),
            "actual_through": out.actual_through.isoformat() if out.actual_through else None}


# ---------------------------------------------------------------------------------------------------------------- data checks

def data_checks_payload(out: PnlOutput) -> dict:
    """Typed figures next to the cohort figures, month by month. The P&L uses the typed ones; the rest are for comparison only."""
    d = out.data_checks
    note = "The typed figures drive the P&L today. The cohort figures are shown for comparison; nothing here changes a number. Which source should drive is your call."
    if not d:
        return {"months": [], "rows": [], "note": note}
    months = d["months"]

    def vals(key):
        return [d[key].get(m) for m in months]

    def gap(a, b):
        return [((x - y) / y) if (x is not None and y) else None for x, y in zip(vals(a), vals(b))]

    rows = [
        {"key": "nc_used", "label": "New-customer revenue used in the P&L", "fmt": "usd0", "values": vals("nc_used")},
        {"key": "nc_cohort", "label": "New-customer revenue in the cohort table", "fmt": "usd0", "values": vals("nc_cohort")},
        {"key": "nc_gap", "label": "Used compared with the cohort table", "fmt": "pct", "values": gap("nc_used", "nc_cohort")},
        {"key": "repeat_used", "label": "Repeat revenue used in the P&L", "fmt": "usd0", "values": vals("repeat_used")},
        {"key": "repeat_typed", "label": "Repeat revenue typed in the sheet", "fmt": "usd0", "values": vals("repeat_typed")},
        {"key": "repeat_model", "label": "Repeat revenue, Cohorts model", "fmt": "usd0", "values": vals("repeat_model")},
        {"key": "repeat_export", "label": "Repeat revenue, cohort export", "fmt": "usd0", "values": vals("repeat_export")},
        {"key": "repeat_gap", "label": "Used compared with the cohort export", "fmt": "pct", "values": gap("repeat_used", "repeat_export")},
    ]
    return {"months": [m.isoformat() for m in months], "rows": rows, "note": note}
