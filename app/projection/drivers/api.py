"""HTTP layer for the drivers module, mounted at /api/projection/drivers.

    GET  /table?scenario=            grid payload (+ cellmeta, summary, levers) for the Spend plan page
    GET  /levers?scenario=           effective levers
    PUT  /levers                     {scenario, levers:{channel_share?, sales_tax_rate?, roas_slide?, payback?}, expected_version?}
    POST /patterns                   {scenario, target, pattern, from, to, preview?, expected_version?}  fill a month range of one plan line
    GET  /compare?scenarios=a,b      spend, new-customer revenue, ROAS, repeat and total revenue per scenario

Plan cells are edited with the core PUT /api/projection/series (audited, versioned, undoable); this blueprint adds only what the core cannot do:
patterns over a range (previewed before they are written), validated levers, the page payload, and the scenario compare. All writes inherit the
core guard rails (JSON only, Origin check, rate limit, size cap) and the app's own auth.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, engine, rwdb, scenarios, series
from ..interfaces import MONTHS, Month, parse_month
from . import loaders, model, patterns, payback, reference, table, undo, validation
from .model import SHARE_KEYS, DriversResult, Levers
from .revenue import revenue_lines

bp = Blueprint("proj_drivers", __name__)

MAX_COMPARE = 6
SHARE_TOLERANCE = 0.0005


# ------------------------------------------------------------------------------------------------------------------ helpers

def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _arg(name: str) -> str | None:
    v = request.args.get(name)
    v = v.strip() if v else None
    return v or None


def _int(data: dict, name: str) -> int | None:
    v = data.get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, f"{name} must be an integer")
    return v


def _month(v: Any, name: str) -> Month:
    try:
        return parse_month(v)
    except ValueError:
        raise ApiError(400, f"{name} must be a first-of-month date (YYYY-MM-01) inside 2025-01 .. 2029-12") from None


def _drivers(res) -> DriversResult:
    d = res.results.get("drivers")
    if not isinstance(d, DriversResult):
        raise ApiError(500, "drivers module did not run: " + res.errors.get("drivers", "unknown error"))
    return d


def _run(conn, slug: str | None):
    return engine.run(slug, conn, today=loaders.current_date())


# ------------------------------------------------------------------------------------------------------------------ table

@bp.get("/table")
def get_table():
    conn = db.get_conn()
    res = _run(conn, _arg("scenario"))
    _drivers(res)
    today = loaders.current_date()
    d = _drivers(res)
    ref = reference.load_reference(conn, d.levers.ad_channels, d.levers.reference_days, today, d.levers)
    return jsonify(table.build(res, res.scenario.name, res.scenario.slug, res.scenario.version, today, ref))


# ------------------------------------------------------------------------------------------------------------------ levers

@bp.get("/levers")
def get_levers():
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, _arg("scenario"))
    return jsonify({"scenario": scn.slug, "version": scn.version, "levers": model.parse_levers(scn.params).to_dict(),
                    "defaults": Levers().to_dict()})


def _check_levers(raw: Any) -> dict:
    """Validate a levers patch; returns the params['drivers'] merge patch."""
    if not isinstance(raw, dict) or not raw:
        raise ApiError(400, "levers must be a non-empty object")
    extra = set(raw) - {"channel_share", "sales_tax_rate", "roas_slide", "payback", "ad_spend_channels", "posture_band", "summary_months", "reference_days", "reference_tolerance", "reference_gap_days",
                         "reference_cpa_min", "reference_cpa_max", "reference_aov_min", "reference_aov_max"}
    if extra:
        raise ApiError(400, f"unknown lever {sorted(extra)[0]!r}")
    patch: dict[str, Any] = {}
    if "channel_share" in raw:
        cs = raw["channel_share"]
        if not isinstance(cs, dict) or set(cs) != set(SHARE_KEYS):
            raise ApiError(400, f"channel_share needs exactly {', '.join(SHARE_KEYS)}")
        vals = {}
        for k in SHARE_KEYS:
            v = model._num(cs[k])
            if v is None or not 0 <= v <= 1:
                raise ApiError(400, f"channel_share.{k} must be a fraction between 0 and 1")
            vals[k] = v
        total = sum(vals.values())
        if abs(total - 1.0) > SHARE_TOLERANCE:
            raise ApiError(400, f"channel shares must add up to 100% (now {total * 100:.2f}%)")
        patch["channel_share"] = vals
    if "sales_tax_rate" in raw:
        v = model._num(raw["sales_tax_rate"])
        if v is None or not 0 <= v <= 0.5:
            raise ApiError(400, "sales_tax_rate must be a fraction between 0 and 0.5")
        patch["sales_tax_rate"] = v
    if "payback" in raw:
        pb = raw["payback"]
        if not isinstance(pb, dict) or not pb or set(pb) - {"product_cost_pct", "fulfillment_per_order", "horizon_months"}:
            raise ApiError(400, "payback takes product_cost_pct, fulfillment_per_order and horizon_months")
        out = {}
        if "product_cost_pct" in pb:
            v = model._num(pb["product_cost_pct"])
            if v is None or not 0 <= v < 1:
                raise ApiError(400, "payback.product_cost_pct must be a fraction below 1")
            out["product_cost_pct"] = v
        if "fulfillment_per_order" in pb:
            v = model._num(pb["fulfillment_per_order"])
            if v is None or not 0 <= v <= 1000:
                raise ApiError(400, "payback.fulfillment_per_order must be between 0 and 1,000")
            out["fulfillment_per_order"] = v
        if "horizon_months" in pb:
            v = model._num(pb["horizon_months"])
            if v is None or v != int(v) or not 1 <= v <= 60:
                raise ApiError(400, "payback.horizon_months must be a whole number of months from 1 to 60")
            out["horizon_months"] = int(v)
        patch["payback"] = out
    if "ad_spend_channels" in raw:
        ch = raw["ad_spend_channels"]
        if not isinstance(ch, list) or not ch or any(c not in SHARE_KEYS for c in ch) or len(set(ch)) != len(ch):
            raise ApiError(400, f"ad_spend_channels must list one or more of {', '.join(SHARE_KEYS)}, each once")
        patch["ad_spend_channels"] = list(ch)
    if "posture_band" in raw:
        v = model._num(raw["posture_band"])
        if v is None or not 0 < v <= 1:
            raise ApiError(400, "posture_band must be a fraction above 0 and up to 1")
        patch["posture_band"] = v
    if "summary_months" in raw:
        v = model._num(raw["summary_months"])
        if v is None or v != int(v) or not 1 <= v <= 36:
            raise ApiError(400, "summary_months must be a whole number of months from 1 to 36")
        patch["summary_months"] = int(v)
    if "reference_days" in raw:
        v = model._num(raw["reference_days"])
        if v is None or v != int(v) or not 7 <= v <= 90:
            raise ApiError(400, "reference_days must be a whole number of days from 7 to 90")
        patch["reference_days"] = int(v)
    if "reference_tolerance" in raw:
        v = model._num(raw["reference_tolerance"])
        if v is None or not 0 < v <= 1:
            raise ApiError(400, "reference_tolerance must be a fraction above 0 and up to 1")
        patch["reference_tolerance"] = v
    if "reference_gap_days" in raw:
        v = model._num(raw["reference_gap_days"])
        if v is None or v != int(v) or not 0 <= v <= 14:
            raise ApiError(400, "reference_gap_days must be a whole number of days from 0 to 14")
        patch["reference_gap_days"] = int(v)
    for pair in (("reference_cpa_min", "reference_cpa_max"), ("reference_aov_min", "reference_aov_max")):
        if pair[0] in raw or pair[1] in raw:
            if pair[0] not in raw or pair[1] not in raw:
                raise ApiError(400, f"send {pair[0]} and {pair[1]} together")
            lo, hi = model._num(raw[pair[0]]), model._num(raw[pair[1]])
            if lo is None or hi is None or not 0 < lo < hi <= 100_000:
                raise ApiError(400, f"{pair[0]} must be above 0 and below {pair[1]}, which is at most 100,000")
            patch[pair[0]], patch[pair[1]] = lo, hi
    if "roas_slide" in raw:
        patch["roas_slide"] = _check_slide(raw["roas_slide"])
    return patch


def _check_slide(rs: Any) -> dict:
    if not isinstance(rs, dict):
        raise ApiError(400, "roas_slide must be an object")
    if not rs.get("enabled"):
        return {"enabled": False}
    start, end = model._num(rs.get("start")), model._num(rs.get("end"))
    if start is None or end is None or not (0.05 <= start <= 20) or not (0.05 <= end <= 20):
        raise ApiError(400, "roas_slide start and end must be NC-ROAS values between 0.05x and 20x")
    first, last = _month(rs.get("from"), "roas_slide.from"), _month(rs.get("to"), "roas_slide.to")
    if last < first:
        raise ApiError(400, "roas_slide.to must not be before roas_slide.from")
    return {"enabled": True, "from": first.isoformat(), "to": last.isoformat(), "start": start, "end": end}


@bp.put("/levers")
def put_levers():
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    patch = _check_levers(data.get("levers"))
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params={"drivers": patch}, expected_version=_int(data, "expected_version"), actor=audit.current_actor())
        scn_params = scenarios.get_scenario(conn, slug).params
    return jsonify({"scenario": row, "levers": model.parse_levers(scn_params).to_dict()})


# ------------------------------------------------------------------------------------------------------------------ series (validated)

@bp.put("/series")
def put_series():
    """Same body as the core PUT /series, for Spend plan lines only, with the line bounds and the closed-month refusal applied."""
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    items = series.parse_items(data.get("items"))
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, slug)
    inp = loaders.load_inputs(conn, scn, loaders.current_date())
    validation.check_items(items, model.compute_drivers(scn.params, inp), inp, only_drivers=True)
    with rwdb.get_rw() as wconn:
        res = series.apply_items(wconn, slug, items, expected_version=_int(data, "expected_version"), actor=audit.current_actor())
    return jsonify(res)


# ------------------------------------------------------------------------------------------------------------------ patterns

def _range_stats(d: DriversResult, months: list[Month], curve: list[float]) -> dict:
    spend = sum(d.spend_total.get(m, 0.0) for m in months)
    ad = sum(d.ad_spend.get(m, 0.0) for m in months)
    nc = sum(d.nc_rev.get(m, 0.0) for m in months)
    last = months[-1]
    pb = d.levers.payback
    return {"spend_total": spend, "ad_spend": ad, "nc_rev": nc, "nc_roas": (nc / ad) if ad > 0 else None,
            "plan_cpa": d.plan_cpa.get(last), "plan_aov": d.plan_aov.get(last),
            "payback_months": payback.payback_month(d.plan_aov.get(last, 0.0), d.plan_cpa.get(last, 0.0), curve, pb["product_cost_pct"],
                                                    pb["fulfillment_per_order"], int(pb["horizon_months"]))}


def _params_with_slide(params: dict, slide: dict) -> dict:
    p = copy.deepcopy(params or {})
    p.setdefault("drivers", {})["roas_slide"] = {"enabled": True, **{k: v for k, v in slide.items() if k != "enabled"}}
    return p


def _plan_view(d: DriversResult, target: str) -> dict[Month, float]:
    return {"spend_total": d.spend_total, "nc_cpa": d.plan_cpa, "nc_aov": d.plan_aov, "amazon_rev": d.amazon_rev, "tiktok_rev": d.tiktok_rev}[target]


@bp.post("/patterns")
def post_patterns():
    data = _body()
    slug = data.get("scenario")
    target = data.get("target")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    first, last = _month(data.get("from"), "from"), _month(data.get("to"), "to")
    if last < first:
        raise ApiError(400, "to must not be before from")
    months = model.month_range(first, last)
    spec = data.get("pattern")
    preview = data.get("preview") is True
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, slug)
    today = loaders.current_date()
    inp = loaders.load_inputs(conn, scn, today)
    base = model.compute_drivers(scn.params, inp)
    curve = list(_run(conn, slug).cohorts.curve_live or [])

    slide = None
    cells: dict[Month, float] = {}
    if target == "nc_roas":
        if not isinstance(spec, dict) or spec.get("type") != "roas_slide":
            raise ApiError(400, "nc_roas takes pattern {type:'roas_slide', start, end}")
        slide = _check_slide({"enabled": True, "from": first.isoformat(), "to": last.isoformat(), "start": spec.get("start"), "end": spec.get("end")})
        locked = [m for m in months if base.nc_basis.get(m) != "plan" or base.source.get(m) != "plan"]
    else:
        if target not in patterns.TARGETS:
            raise ApiError(400, f"target must be one of {', '.join([*patterns.TARGETS, 'nc_roas'])}")
        try:
            cells = patterns.generate(spec, months)
            patterns.check_bounds(target, cells)
        except patterns.PatternError as e:
            raise ApiError(400, str(e)) from None
        locked = validation.locked_months(target, months, base, inp)
    if locked:
        raise ApiError(400, f"{validation.fmt_months(locked)} hold loaded actuals; the plan applies to planned months only")

    # what the plan would look like with the pattern in place
    inp2 = copy.deepcopy(inp)
    params2 = scn.params
    if slide is not None:
        params2 = _params_with_slide(scn.params, slide)
    else:
        inp2.plan[target].update(cells)
    after = model.compute_drivers(params2, inp2)
    view = "nc_cpa" if slide is not None else target
    changes = [{"month": m.isoformat(), "before": _plan_view(base, view).get(m), "after": _plan_view(after, view).get(m)} for m in months]
    out: dict[str, Any] = {"target": "nc_roas" if slide is not None else target, "from": first.isoformat(), "to": last.isoformat(),
                           "months": len(months), "cells": changes, "before": _range_stats(base, months, curve), "after": _range_stats(after, months, curve)}
    if preview:
        out["preview"] = True
        return jsonify(out)

    if slide is not None:
        note = f"NC-ROAS slide {slide['start']:.2f}x to {slide['end']:.2f}x, {first:%b %Y} - {last:%b %Y}"
    else:
        note = patterns.describe(target, spec, first, last)
    with rwdb.get_rw() as wconn:
        actor = audit.current_actor()
        ev = _int(data, "expected_version")
        if slide is not None:
            v0 = scenarios.get_row(wconn, slug)["version"]
            scenarios.update(wconn, slug, params={"drivers": {"roas_slide": slide}}, expected_version=ev, actor=actor)
            v1 = scenarios.get_row(wconn, slug)["version"]
            res = {"version": v1, "changed": int(v1 != v0)}
        else:
            cutoff = today.replace(day=1)                      # a closed month is corrected with kind 'override' (the core refuses a typed input there)
            items = series.parse_items([{"line_key": f"drivers.{target}", "month": m.isoformat(), "value": v, "kind": "override" if m < cutoff else "input",
                                         "note": note} for m, v in cells.items()])
            res = series.apply_items(wconn, slug, items, expected_version=ev, actor=actor)
    out.update({"preview": False, "version": res["version"], "changed": res["changed"], "note": note})
    return jsonify(out)


@bp.post("/undo")
def post_undo():
    """Put back exactly what one Spend plan change wrote (a pattern fill returns the `version` to pass), as one audited change."""
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    with rwdb.get_rw() as conn:
        out = undo.undo_change(conn, slug, data.get("version"), expected_version=_int(data, "expected_version"), actor=audit.current_actor())
    return jsonify(out)


# ------------------------------------------------------------------------------------------------------------------ compare

@bp.get("/compare")
def get_compare():
    conn = db.get_conn()
    raw = _arg("scenarios")
    names = [s.strip() for s in raw.split(",") if s.strip()] if raw else [s["slug"] for s in scenarios.list_scenarios(conn)]
    if not names:
        raise ApiError(400, "no scenarios to compare")
    if len(names) > MAX_COMPARE:
        raise ApiError(400, f"compare at most {MAX_COMPARE} scenarios")
    out = []
    actual_through: date | None = None
    for slug in names:
        res = _run(conn, slug)
        d = _drivers(res)
        rl = revenue_lines(d, res.cohorts.repeat_rev, attach=False)
        actual_through = actual_through or d.actual_through
        pb = d.levers.payback
        curve = list(res.cohorts.curve_live or [])
        fy = {}
        for y in sorted({m.year for m in MONTHS}):
            ad = sum(v for m, v in d.ad_spend.items() if m.year == y)
            nc = sum(v for m, v in d.nc_rev.items() if m.year == y)
            fy[str(y)] = {"spend_total": sum(v for m, v in d.spend_total.items() if m.year == y), "nc_rev": nc, "nc_roas": (nc / ad) if ad > 0 else None,
                          "repeat_rev": sum(v for m, v in rl.repeat_rev.items() if m.year == y),
                          "total_revenue": sum(v for m, v in rl.total_revenue.items() if m.year == y)}
        out.append({"slug": res.scenario.slug, "name": res.scenario.name, "version": res.scenario.version,
                    "series": {"spend_total": [d.spend_total.get(m, 0.0) for m in MONTHS], "nc_rev": [d.nc_rev.get(m, 0.0) for m in MONTHS],
                               "nc_roas": [d.nc_roas.get(m, 0.0) for m in MONTHS], "repeat_rev": [rl.repeat_rev.get(m, 0.0) for m in MONTHS],
                               "total_revenue": [rl.total_revenue.get(m, 0.0) for m in MONTHS],
                               "payback": [payback.payback_month(d.plan_aov.get(m, 0.0), d.plan_cpa.get(m, 0.0), curve, pb["product_cost_pct"],
                                                                 pb["fulfillment_per_order"], int(pb["horizon_months"])) for m in MONTHS]},
                    "fy": fy})
    status = ["actual" if actual_through and m <= actual_through else "projected" for m in MONTHS]
    view = out[0]
    for o in out[1:]:                                    # differences from the viewing (first) scenario's side: view minus other, so "more spend here" is positive
        o["vs_view"] = {"view": view["slug"], "fy": {y: {k: _diff(view["fy"][y].get(k), v) for k, v in vals.items()}
                                                     for y, vals in o["fy"].items()}}
    return jsonify({"months": [m.isoformat() for m in MONTHS], "status": status, "view": view["slug"], "scenarios": out})


def _diff(mine: float | None, other: float | None) -> dict | None:
    if mine is None or other is None:
        return None
    d = mine - other
    return {"diff": d, "pct": (d / abs(other)) if other else None}
