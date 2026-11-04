"""/api/projection/financing: cash waterfall, term debt (read and write), payback, spend x ROAS grid, payout schedule, inputs.

Reads run the whole engine for the scenario (milliseconds) and use this module's result. Writes go through the core stores
(`series.apply_items`, `scenarios.update`), so every change is audited, versioned and undoable. The core write guard covers these routes.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, degraded, engine, rwdb, scenarios, series
from ..interfaces import parse_month
from . import model, tables
from . import params as P

bp = Blueprint("proj_financing", __name__)

MAX_PAYMENTS = 120


# ------------------------------------------------------------------------------------------------------------------ helpers

def _slug(conn) -> str:
    name = request.args.get("scenario")
    if not name:
        return scenarios.default_slug(conn)
    scenarios.get_row(conn, name)                                  # 404 for an unknown scenario
    return name


def _run(conn):
    slug = _slug(conn)
    res = engine.run(slug, conn)
    degraded.ensure_ok(res, "financing")                    # 503 upstream_degraded {modules:[{module,error}]}: never cash built on zeros
    fin = res.results.get("financing")
    if not isinstance(fin, model.FinancingDetail):
        raise ApiError(503, "financing is not available")
    return slug, res, fin


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _float_arg(name: str, lo: float | None = None, hi: float | None = None) -> float | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except ValueError:
        raise ApiError(400, f"{name} must be a number") from None
    if not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(400, f"{name} is out of range")
    return v


def _int_arg(name: str, lo: int, hi: int) -> int | None:
    v = _float_arg(name, lo, hi)
    if v is None:
        return None
    if v != int(v):
        raise ApiError(400, f"{name} must be a whole number")
    return int(v)


def _list_arg(name: str, lo: float, hi: float, max_len: int = 40) -> list[float] | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        vals = [float(x) for x in raw.split(",") if x.strip() != ""]
    except ValueError:
        raise ApiError(400, f"{name} must be a comma-separated list of numbers") from None
    if not vals or len(vals) > max_len or any(not math.isfinite(v) or v < lo or v > hi for v in vals):
        raise ApiError(400, f"{name} must hold 1 to {max_len} numbers from {lo:g} to {hi:g}")
    return vals


def _curve(res, fin, kind: str) -> list[float]:
    return model._curve(res.results.get("cohorts"), kind)


# ------------------------------------------------------------------------------------------------------------------ reads

@bp.get("/table")
@bp.get("/cash")
def cash():
    conn = db.get_conn()
    _, res, fin = _run(conn)
    return jsonify(tables.cash_table(fin, res.actual_through))


@bp.get("/debt")
def debt():
    conn = db.get_conn()
    _, res, fin = _run(conn)
    return jsonify(tables.debt_table(fin, res.actual_through))


def _payback_overrides(inp: dict) -> dict:
    """Apply the what-if query overrides (aov, cac, product_cost_pct, fulfillment, max_age, curve) to a copy of the payback inputs."""
    inp = dict(inp)
    for k, lo, hi in (("aov", 0.01, 1e6), ("cac", 0.0, 1e6), ("product_cost_pct", 0.0, 1.0), ("fulfillment", 0.0, 1e6)):
        v = _float_arg(k, lo, hi)
        if v is not None:
            inp[k] = v
            if k in ("aov", "cac"):
                inp[k + "_source"] = "query"
    ma = _int_arg("max_age", 1, 60)
    if ma is not None:
        inp["max_age"] = ma
    curve_kind = request.args.get("curve") or inp["curve"]
    if curve_kind not in ("live", "goal"):
        raise ApiError(400, "curve must be live or goal")
    inp["curve"] = curve_kind
    return inp


def _no_curve(res) -> str | None:
    c = res.results.get("cohorts")
    return None if c is not None and (getattr(c, "curve_live", None) or getattr(c, "curve_goal", None)) else "The retention curve is not available: the cohorts module has no result."


@bp.get("/payback")
def payback():
    """Payback for today's economics, on product margin and after all variable costs. Query overrides (not saved): aov, cac, product_cost_pct,
    fulfillment, curve=live|goal, max_age, loaded_rate (variable cost as a share of AOV)."""
    conn = db.get_conn()
    _, res, fin = _run(conn)
    inp = _payback_overrides(fin.payback_inputs)
    curve_kind = inp["curve"]
    reason = _no_curve(res)
    if reason:
        return jsonify(tables.payback_payload(fin, None, inp, curve_kind, None, fin.loaded_info, reason))
    curve = _curve(res, fin, curve_kind)
    info = model.loaded_rate_info(fin.params, res.results.get("pnl"), _float_arg("loaded_rate", 0.0, 0.99))
    pb = model.run_payback(inp, curve)
    loaded = model.run_payback(inp, curve, info["rate"], fully_loaded=True)
    return jsonify(tables.payback_payload(fin, pb, inp, curve_kind, loaded, info, None if pb else "Enter the average order value and customer acquisition cost."))


@bp.get("/matrix")
def matrix():
    """Profit at the target cohort age by monthly spend x NC-ROAS. Query overrides: target_month, basis=cm|loaded, margin=product|loaded, fixed_cost,
    spends, roas, plus the payback overrides."""
    conn = db.get_conn()
    _, res, fin = _run(conn)
    inp = _payback_overrides(fin.payback_inputs)
    reason = _no_curve(res)
    if reason:
        return jsonify({"result": None, "unavailable": reason})
    curve = _curve(res, fin, inp["curve"])
    basis = request.args.get("basis")
    if basis is not None and basis not in ("cm", "loaded"):
        raise ApiError(400, "basis must be cm or loaded")
    margin = request.args.get("margin")
    if margin is not None and margin not in ("product", "loaded"):
        raise ApiError(400, "margin must be product or loaded")
    pb = model.run_payback(inp, curve)
    info = model.loaded_rate_info(fin.params, res.results.get("pnl"), _float_arg("loaded_rate", 0.0, 0.99))
    loaded = model.run_payback(inp, curve, info["rate"], fully_loaded=True)
    m = model.run_matrix(fin.params, pb, curve, loaded, target_month=_int_arg("target_month", 0, 60), basis=basis, margin=margin,
                         fixed_cost=_float_arg("fixed_cost", 0, 1e10), spends=_list_arg("spends", 1, 1e10), roas=_list_arg("roas", 0.01, 100))
    out = tables.matrix_payload(m)
    out["unavailable"] = None if m else "Enter the average order value and customer acquisition cost."
    return jsonify(out)


@bp.get("/payout")
def payout():
    """Scale-payback payout schedule. Query overrides (not saved): media_spend, cpa, aov, repeat_source=goal|live|typed."""
    conn = db.get_conn()
    _, res, fin = _run(conn)
    prm = P.deep_merge({}, fin.params)
    for k, lo, hi in (("media_spend", 0, 1e10), ("cpa", 0.01, 1e6), ("aov", 0.01, 1e6)):
        v = _float_arg(k, lo, hi)
        if v is not None:
            prm["payout"][k] = v
    src = request.args.get("repeat_source")
    if src is not None:
        if src not in ("goal", "live", "typed"):
            raise ApiError(400, "repeat_source must be goal, live or typed")
        prm["payout"]["repeat_source"] = src
    reason = _no_curve(res) if prm["payout"]["repeat_source"] != "typed" else None
    if reason:
        return jsonify({"repeat_source": prm["payout"]["repeat_source"], "result": None, "unavailable": reason})
    repeat, used = model.payout_repeat(prm, res.results.get("cohorts"))
    out = tables.payout_payload(model.run_payout(prm, repeat), used)
    out["unavailable"] = None
    return jsonify(out)


@bp.get("/inputs")
def inputs():
    conn = db.get_conn()
    slug = _slug(conn)
    scn = scenarios.get_scenario(conn, slug)
    own = (scenarios.get_row(conn, slug)["params"] or {}).get(P.NS) or {}
    cells = series.fetch_cells(conn, scn, prefix="financing.")
    return jsonify({
        "scenario": slug, "version": scn.version, "params": P.effective(scn.params), "scenario_params": own, "defaults": P.DEFAULTS,
        "sources": P.SOURCES, "payback_sources": P.payback_sources(scn.params),
        "series": [{"line_key": c["line_key"], "month": c["month"].isoformat(), "value": c["value"], "kind": c["kind"], "note": c["note"],
                    "origin": c["origin"]} for c in sorted(cells, key=lambda c: (c["line_key"], c["month"]))],
    })


# ------------------------------------------------------------------------------------------------------------------ writes

@bp.put("/inputs")
def inputs_put():
    """Merge-patch the scenario's financing levers: {scenario, params:{...}, expected_version?}. A null value removes the key."""
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    patch = P.validate_patch(data.get("params"))
    if not patch:
        raise ApiError(400, "params must hold at least one input")
    ev = data.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    fin_patch, drivers_patch = P.split_payback_patch(patch)        # product cost, fulfillment and horizon live in params['drivers']['payback'] (one store)
    params: dict = {P.NS: fin_patch}
    if drivers_patch:
        params["drivers"] = drivers_patch
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params=params, expected_version=ev, actor=audit.current_actor())
    return jsonify({"scenario": row})


@bp.put("/debt")
def debt_put():
    """Write term-loan principal payments and/or terms: {scenario, payments:[{month, principal_paid|null}], terms:{principal,rate,mode,fixed_payment}, expected_version?}.
    A null principal_paid deletes the typed cell (back to interest only, or the base scenario's value)."""
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    unknown = set(data) - {"scenario", "payments", "terms", "expected_version"}
    if unknown:
        raise ApiError(400, f"unknown field {sorted(unknown)[0]!r}")
    payments, terms = data.get("payments"), data.get("terms")
    if payments is None and terms is None:
        raise ApiError(400, "send payments and/or terms")
    ev = data.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    items: list[dict] = []
    if payments is not None:
        if not isinstance(payments, list) or not payments or len(payments) > MAX_PAYMENTS:
            raise ApiError(400, f"payments must be a list of 1 to {MAX_PAYMENTS} items")
        for i, p in enumerate(payments):
            if not isinstance(p, dict) or set(p) - {"month", "principal_paid"} or "month" not in p or "principal_paid" not in p:
                raise ApiError(400, f"payments[{i}] must be {{month, principal_paid}}")
            v = p["principal_paid"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
                raise ApiError(400, f"payments[{i}].principal_paid must be zero or more, or null to clear")
            try:
                m = parse_month(p["month"])
            except ValueError as e:
                raise ApiError(400, f"payments[{i}].month: {e}") from None
            items.append({"line_key": P.S_PRINCIPAL, "month": m.isoformat(), "value": v, "kind": "input"})
    patch = P.validate_patch({"debt": terms}) if terms is not None else None
    with rwdb.get_rw() as conn:
        out: dict[str, Any] = {}
        actor = audit.current_actor()
        if items:
            held = {c["month"].isoformat(): c for c in series.fetch_cells(conn, slug, [P.S_PRINCIPAL])}
            for it in items:                                       # a cell re-sent with the same value keeps its source note; a changed value drops it
                c = held.get(it["month"])
                if c and it["value"] is not None and c["note"] and abs(c["value"] - it["value"]) < 5e-5:
                    it["note"] = c["note"]
            r = series.apply_items(conn, slug, series.parse_items(items), expected_version=ev, actor=actor)
            out.update(r)
            ev = r["version"]
        if patch:
            row = scenarios.update(conn, slug, params={P.NS: patch}, expected_version=ev, actor=actor)
            out["version"] = row["version"]
        return jsonify(out)
