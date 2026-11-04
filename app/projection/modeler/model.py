"""Scenario modeler: run the model on a draft (nothing is written) and shape the answer for the page.

    run_state(conn, slug, edits=None, op=None, retention=None, today=None) -> payload   the grid, the yearly results, the retention model
    outputs(res, addbacks)                                                    -> yearly table, lowest cash, headline year, averages
    compare(conn, slugs, today)                                               -> the same yearly table for several scenarios

The numbers are the engine's own: yearly revenue is `pnl.fy[year]['total_revenue']`, net operating income `pnl.fy[year]['noi']`, the figures the P&L page
shows. EBITDA is net operating income plus the lines named in the scenario setting `modeler.ebitda_addbacks` (default: interest expense).
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date
from typing import Any

from ...queries import ApiError
from .. import engine, scenarios, series
from ..drivers import loaders as drv_loaders
from ..drivers import model as drv_model
from ..drivers import table as drv_table
from ..interfaces import MONTHS, Month, Scenario
from . import ops, overlay

NAMESPACE = "modeler"
ADDBACK_CHOICES = ("interest_expense", "marketing_influencers", "uncategorized")        # P&L lines that can sit below EBITDA
DEFAULT_ADDBACKS = ("interest_expense",)
ADDBACK_LABELS = {"interest_expense": "interest expense", "marketing_influencers": "marketing influencers", "uncategorized": "uncategorized expense"}
SOURCES = ("live", "goal", "asof")
WORKSPACE_NAME = "Modeler"
CRITICAL = ("drivers", "cohorts", "pnl")                                                   # without these there is nothing to show


# ---------------------------------------------------------------------------------------------------------------- settings

def parse_addbacks(params: dict | None) -> tuple[str, ...]:
    """The P&L lines added back to net operating income for EBITDA, from the scenario's effective params (unknown names are ignored)."""
    raw = ((params or {}).get(NAMESPACE) or {}).get("ebitda_addbacks")
    if not isinstance(raw, list):
        return DEFAULT_ADDBACKS
    keep = tuple(dict.fromkeys(k for k in raw if k in ADDBACK_CHOICES))
    return keep if keep or raw == [] else DEFAULT_ADDBACKS


def check_addbacks(raw: Any) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(k, str) for k in raw):
        raise ApiError(400, "ebitda_addbacks must be a list of line names")
    bad = [k for k in raw if k not in ADDBACK_CHOICES]
    if bad:
        raise ApiError(400, f"ebitda_addbacks can list {', '.join(ADDBACK_CHOICES)}")
    return list(dict.fromkeys(raw))


def ebitda_text(addbacks: tuple[str, ...]) -> str:
    if not addbacks:
        return "EBITDA is net operating income."
    names = [ADDBACK_LABELS[k] for k in addbacks]
    return "EBITDA is net operating income before " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) + "."


# ---------------------------------------------------------------------------------------------------------------- retention draft

def retention_patch(conn, scn: Scenario, raw: Any, today: date) -> dict | None:
    """The cohorts levers a draft retention choice sets: {curve_source, asof_month?, n_cohorts?}. Same checks as PUT /cohorts/levers."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ApiError(400, "retention must be an object")
    source = raw.get("curve_source")
    if source not in SOURCES:
        raise ApiError(400, "curve_source must be live, goal or asof")
    patch: dict[str, Any] = {"curve_source": source}
    if source == "asof":
        try:
            month = date.fromisoformat(str(raw.get("asof_month")))
        except ValueError:
            raise ApiError(400, "Choose the month the curve is taken at the end of.") from None
        if month.day != 1:
            raise ApiError(400, "asof_month must be the first of a month")
        patch["asof_month"] = month.isoformat()
    n = raw.get("n_cohorts")
    if n is not None:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 36:
            raise ApiError(400, "n_cohorts must be a whole number from 1 to 36")
        patch["n_cohorts"] = n
    try:
        from ..cohorts import compare as ccmp
        from ..cohorts import engine as ceng
        from ..cohorts import store as cstore
        current = {k: v for k, v in dict((scn.params or {}).get("cohorts") or {}).items() if k != "curve"}
        parsed = ceng.parse_levers({**current, **patch}, strict=True)
        problem = ccmp.asof_problem(cstore.load_cohorts(conn), parsed, today)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    except (ImportError, AttributeError):                  # the cohorts module is older than the curve choices: trust the request
        problem = None
    if problem:
        raise ApiError(400, problem)
    return patch


def with_retention(scn: Scenario, patch: dict | None) -> Scenario:
    if not patch:
        return scn
    params = copy.deepcopy(scn.params or {})
    coh = {k: v for k, v in dict(params.get("cohorts") or {}).items() if k != "curve"}
    coh.update(patch)
    params["cohorts"] = coh
    return replace(scn, params=params)


# ---------------------------------------------------------------------------------------------------------------- running the model

def edit_kind(key: str, month: Month, scn: Scenario) -> str:
    """A cost-per-customer cell inside the return-on-ad-spend slide has to be an override to count (the Spend plan page does the same)."""
    return "override" if key == "drivers.nc_cpa" and drv_model.parse_levers(scn.params).roas_slide.covers(month) else "input"


def draft_for(conn, scn: Scenario, edits: dict[ops.Edit, float | None]) -> overlay.Draft:
    """The edits as a draft of the scenario. A frozen copy reads nothing from its base, so a cell taken away has no fallback."""
    reads_base = len(scenarios.chain_ids(conn, scn)) > 1
    return overlay.Draft(scn.slug, scn.base_slug if reads_base else None, {(k, m): (v, edit_kind(k, m, scn)) for (k, m), v in edits.items()})


def drivers_view(conn, scn: Scenario, edits: dict[ops.Edit, float | None], today: date):
    """Just the drivers module on the draft: the cell codes (which months hold loaded figures) and the values each plan row has now."""
    with overlay.active(draft_for(conn, scn, edits)):
        inp = drv_loaders.load_inputs(conn, scn, today)
    return drv_model.compute_drivers(scn.params, inp)


def run_engine(conn, scn: Scenario, edits: dict[ops.Edit, float | None], today: date):
    with overlay.active(draft_for(conn, scn, edits)):
        res = engine.run(scn, conn, today=today)
    bad = [k for k in CRITICAL if res.results.get(k) is None]
    if bad:
        raise ApiError(500, "The model could not run: " + "; ".join(f"{k}: {res.errors.get(k, 'no result')}" for k in bad))
    return res


# ---------------------------------------------------------------------------------------------------------------- yearly results

def _sum(s: dict, year: int) -> float:
    return float(sum(v for m, v in s.items() if m.year == year))


def _line(pnl, key: str) -> dict:
    return pnl.lines.get(key) or {}


def monthly_ebitda(pnl, addbacks: tuple[str, ...]) -> dict[Month, float]:
    noi = _line(pnl, "noi")
    adds = [_line(pnl, k) for k in addbacks]
    return {m: noi.get(m, 0.0) + sum(a.get(m, 0.0) for a in adds) for m in MONTHS}


def first_year(pnl) -> int:
    t = getattr(pnl, "actual_through", None)
    return t.year if t else MONTHS[0].year


def years_table(pnl, addbacks: tuple[str, ...]) -> list[dict]:
    """One entry per year from the actual-through year to the end of the plan, straight from `pnl.fy` (the P&L page's fiscal-year column)."""
    through = getattr(pnl, "actual_through", None)
    out = []
    for y in range(first_year(pnl), MONTHS[-1].year + 1):
        fy = pnl.fy.get(y)
        if not fy:
            continue
        months = [m for m in MONTHS if m.year == y]
        closed = [m for m in months if through and m <= through]
        total, nc, rep = float(fy.get("total_revenue", 0.0)), float(fy.get("nc_rev", 0.0)), float(fy.get("repeat_rev", 0.0))
        noi = float(fy.get("noi", 0.0))
        adds = {k: float(fy.get(k, 0.0)) for k in addbacks}
        ebitda = noi + sum(adds.values())
        status = "actual" if len(closed) == len(months) else "projected" if not closed else "partial"
        sub = ""
        if status == "partial":
            sub = f"Actual through {closed[-1]:%b}"
        out.append({"year": y, "label": f"FY{y}", "status": status, "sub": sub, "actual_months": len(closed),
                    "total_revenue": total, "nc_rev": nc, "repeat_rev": rep, "other_rev": total - nc - rep,
                    "ad_spend": float(fy.get("marketing_spend", 0.0)), "noi": noi, "addbacks": adds, "ebitda": ebitda,
                    "ebitda_margin": (ebitda / total) if total else None})
    return out


def lowest_cash(res) -> dict | None:
    fin = res.results.get("financing")
    cash = (getattr(fin, "lines", None) or {}).get("ending_cash") if fin is not None else None
    if not cash:
        return None
    month, value = min(cash.items(), key=lambda kv: kv[1])
    return {"month": month.isoformat(), "value": float(value)}


def headline_year(pnl, years: list[dict]) -> int | None:
    """The year the headline talks about: the P&L's own value year (setting ev.year, 2027) when it is on the page, else the first fully projected year."""
    wanted = (getattr(pnl, "ev", None) or {}).get("basis_year")
    have = [y["year"] for y in years]
    if isinstance(wanted, int) and wanted in have:
        return wanted
    return next((y["year"] for y in years if y["status"] == "projected"), have[-1] if have else None)


def averages(d, year: int | None) -> dict | None:
    """What the plan looks like on average in the headline year: spend a month, cost per new customer, order value, and whether any of them moves."""
    if year is None:
        return None
    months = [m for m in MONTHS if m.year == year]
    spend = [d.spend_total.get(m, 0.0) for m in months]
    cpa = [d.plan_cpa.get(m, 0.0) for m in months]
    aov = [d.plan_aov.get(m, 0.0) for m in months]
    orders = _sum(d.nc_orders, year)
    avg_spend = sum(spend) / len(months)

    def moves(v: list[float]) -> bool:
        lo, hi = min(v), max(v)
        return hi > 0 and (hi - lo) / hi > 0.005

    return {"year": year, "spend_per_month": avg_spend, "cpa": (_sum(d.ad_spend, year) / orders) if orders else None,
            "aov": (_sum(d.nc_rev, year) / orders) if orders else None, "varies": moves(spend) or moves(cpa) or moves(aov)}


def outputs(res, addbacks: tuple[str, ...]) -> dict:
    pnl = res.results["pnl"]
    years = years_table(pnl, addbacks)
    hy = headline_year(pnl, years)
    return {"years": years, "headline_year": hy, "lowest_cash": lowest_cash(res), "averages": averages(res.results["drivers"], hy),
            "ebitda": {"addbacks": list(addbacks), "text": ebitda_text(addbacks)}}


def retention_info(res) -> dict:
    c = res.results["cohorts"]
    lv = getattr(c, "levers", None) or {}
    return {"curve_source": lv.get("curve_source", "live"), "asof_month": lv.get("asof_month"), "n_cohorts": lv.get("n_cohorts"),
            "label": getattr(c, "curve_label", "") or "", "lifetime": float(c.lifetime_multiple), "twelve": float(getattr(c, "twelve_month_multiple", 0.0)),
            "warnings": list(getattr(c, "warnings", []) or [])}


# ---------------------------------------------------------------------------------------------------------------- the grid

def _slice(values: dict, idx: list[int], missing: float | None = 0.0) -> list:
    return [values.get(MONTHS[i], missing) for i in idx]


def _ratio(a: float, b: float) -> float | None:
    return (a / b) if b else None


def grid(res, scn: Scenario, addbacks: tuple[str, ...], codes: dict[str, dict[str, str]]) -> dict:
    """The page's grid in the shared grid's vocabulary: three yellow plan rows, then what they produce month by month."""
    d, pnl = res.results["drivers"], res.results["pnl"]
    start = first_year(pnl)
    idx = [i for i, m in enumerate(MONTHS) if m.year >= start]
    months = [MONTHS[i] for i in idx]
    through = getattr(pnl, "actual_through", None)
    status = ["actual" if through and m <= through else "projected" for m in months]
    ebitda = monthly_ebitda(pnl, addbacks)
    rep, total, nc = _line(pnl, "repeat_rev"), _line(pnl, "total_revenue"), _line(pnl, "nc_rev")
    other = {m: total.get(m, 0.0) - nc.get(m, 0.0) - rep.get(m, 0.0) for m in MONTHS}
    margin = {m: _ratio(ebitda[m], total.get(m, 0.0)) for m in MONTHS}
    plan = {"spend_total": d.spend_total, "nc_aov": d.plan_aov, "nc_cpa": d.plan_cpa}
    notes = {
        "spend_total": "All channels: Meta, Google, Amazon ads, TikTok and Bing. The same row as Marketing spend on the Spend plan page.",
        "nc_aov": "Average order value: what a new customer spends on a first order. The same row as New-customer AOV on the Spend plan page.",
        "nc_cpa": "Cost per new customer: Meta and Google spend divided by new customers. The same row as New-customer CPA on the Spend plan page.",
    }
    fmts = {"spend_total": "usd0", "nc_aov": "usd", "nc_cpa": "usd"}

    def row(key, label, values, kind="calc", fmt="usd0", **extra):
        return {"key": key, "label": label, "kind": "input" if extra.get("input") else kind, "fmt": fmt, "values": values, "input": bool(extra.pop("input", False)), **extra}

    rows = [row("h_inputs", "Plan inputs", [None] * len(idx), "header")]
    for r, key in ops.ROWS.items():
        rows.append(row(key, ops.LABELS[r] + (" (all channels)" if r == "spend_total" else " (AOV)" if r == "nc_aov" else " (CPA)"),
                        _slice(plan[r], idx), fmt=fmts[r], input=True, note=notes[r]))
    rows.append(row("h_out", "What the plan produces", [None] * len(idx), "header"))
    rows += [
        row("mo.ad_spend", "Spend on Meta and Google", _slice(d.ad_spend, idx), note="The part of ad spend that finds new customers."),
        row("mo.nc_orders", "New customers", _slice(d.nc_orders, idx), fmt="num", digits=0),
        row("mo.nc_rev", "New-customer revenue", _slice(d.nc_rev, idx)),
        row("mo.nc_roas", "Return on ad spend", _slice(d.nc_roas, idx, None), "ratio", "x", note="New-customer revenue per dollar of Meta and Google spend."),
        row("mo.repeat_rev", "Repeat revenue", _slice(rep, idx), note="From the retention model below."),
        row("mo.other_rev", "Amazon and TikTok, less sales tax estimate", _slice(other, idx)),
        row("mo.total_rev", "Total revenue", _slice(total, idx), "total"),
        row("mo.ebitda", "EBITDA", _slice(ebitda, idx), "subtotal", note=ebitda_text(addbacks)),
        row("mo.ebitda_margin", "EBITDA margin", _slice(margin, idx, None), "ratio", "pct", digits=1),
    ]
    years = sorted({m.year for m in months})
    fy_rows: dict[str, list] = {}
    for key in ("mo.ad_spend", "mo.nc_orders", "mo.nc_rev", "mo.repeat_rev", "mo.other_rev", "mo.total_rev", "mo.ebitda"):
        src = {"mo.ad_spend": d.ad_spend, "mo.nc_orders": d.nc_orders, "mo.nc_rev": d.nc_rev, "mo.repeat_rev": rep, "mo.other_rev": other, "mo.total_rev": total,
               "mo.ebitda": ebitda}[key]
        fy_rows[key] = [_sum(src, y) for y in years]
    fy_rows[ops.ROWS["spend_total"]] = [_sum(d.spend_total, y) for y in years]
    fy_rows[ops.ROWS["nc_cpa"]] = [_ratio(_sum(d.ad_spend, y), _sum(d.nc_orders, y)) for y in years]
    fy_rows[ops.ROWS["nc_aov"]] = [_ratio(_sum(d.nc_rev, y), _sum(d.nc_orders, y)) for y in years]
    fy_rows["mo.nc_roas"] = [_ratio(_sum(d.nc_rev, y), _sum(d.ad_spend, y)) for y in years]
    fy_rows["mo.ebitda_margin"] = [_ratio(_sum(ebitda, y), _sum(total, y)) for y in years]
    in_view = {iso for iso in (m.isoformat() for m in months)}
    meta = {k: {iso: c for iso, c in v.items() if iso in in_view} for k, v in codes.items() if k in ops.KEY_TO_ROW}
    cap = ("QuickBooks through " + through.strftime("%b %Y") + "; later months follow the plan inputs. Closed months are locked. Projected values are marked *."
           if through else "No QuickBooks months loaded yet. Projected values are marked *.")
    return {"months": [m.isoformat() for m in months], "status": status, "rows": rows, "captions": [cap], "cellmeta": meta,
            "fy": {"years": years, "rows": fy_rows}, "actual_through": through.isoformat() if through else None}


# ---------------------------------------------------------------------------------------------------------------- one call for the page

def scenario_card(scn: Scenario) -> dict:
    return {"slug": scn.slug, "name": scn.name, "version": scn.version, "is_default": scn.is_default, "base_slug": scn.base_slug}


def plan_of_record(conn) -> Scenario:
    return scenarios.get_scenario(conn, scenarios.default_slug(conn))


def por_rows(conn, por: Scenario) -> dict[str, dict[Month, float]]:
    got = series.get_series(conn, por, list(ops.ROWS.values()))
    return {r: got.get(k, {}) for r, k in ops.ROWS.items()}


def run_state(conn, slug: str | None, *, edits: Any = None, op: Any = None, retention: Any = None, today: date | None = None, with_por: bool = False) -> dict:
    """The page's whole state for a scenario with draft edits on top. Reads only: `conn` is the app's read-only connection and nothing is written."""
    today = today or drv_loaders.current_date()
    scn = scenarios.get_scenario(conn, slug)
    patch = retention_patch(conn, scn, retention, today)
    scn_run = with_retention(scn, patch)
    pending = ops.parse_edits(edits)
    ops.check_values(pending)
    applied = None
    base_view = drivers_view(conn, scn_run, pending, today)
    codes = drv_table.cell_codes(base_view)
    ops.check_unlocked(pending, codes)
    merged = dict(pending)
    por = plan_of_record(conn)
    if op is not None:
        ctx = ops.OpContext(current={"spend_total": base_view.spend_total, "nc_aov": base_view.plan_aov, "nc_cpa": base_view.plan_cpa}, codes=codes,
                            por=por_rows(conn, por) if scn.slug != por.slug else None)
        exp = ops.expand(op, ctx)
        merged.update(exp.edits)
        applied = {"text": exp.text, "row": exp.row, "count": exp.count, "skipped": [m.isoformat() for m in exp.skipped],
                   "edits": [{"line_key": k, "month": m.isoformat(), "value": v, "kind": edit_kind(k, m, scn)} for (k, m), v in exp.edits.items()]}
    res = run_engine(conn, scn_run, merged, today)
    addbacks = parse_addbacks(scn_run.params)
    out = {"scenario": scenario_card(scn), "plan_of_record": scenario_card(por), "draft": bool(merged) or patch is not None, "applied": applied,
           **grid(res, scn_run, addbacks, codes), "outputs": outputs(res, addbacks), "retention": retention_info(res),
           "levers": drv_model.parse_levers(scn_run.params).to_dict(), "settings": {"ebitda_addbacks": list(addbacks), "ebitda_choices": list(ADDBACK_CHOICES)}}
    if with_por and scn.slug != por.slug:
        pres = run_engine(conn, por, {}, today)
        out["por"] = {"outputs": outputs(pres, parse_addbacks(por.params))}
    return out


def compare(conn, slugs: list[str], *, today: date | None = None) -> list[dict]:
    """The yearly table of each scenario as it is saved."""
    today = today or drv_loaders.current_date()
    por = plan_of_record(conn)
    out = []
    for slug in slugs:
        scn = scenarios.get_scenario(conn, slug)
        res = run_engine(conn, scn, {}, today)
        out.append({"scenario": scenario_card(scn), "is_plan_of_record": scn.slug == por.slug, "outputs": outputs(res, parse_addbacks(scn.params)),
                    "retention": retention_info(res)})
    return out
