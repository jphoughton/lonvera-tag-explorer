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
from .. import audit, engine, rwdb, scenarios, series
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
    fin = res.results.get("financing")
    if res.stubbed.get("financing") or not isinstance(fin, model.FinancingDetail):
        raise ApiError(500, "financing failed to compute: " + res.errors.get("financing", "unknown error"))
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


@bp.get("/payback")
def payback():
    """Payback for today's economics. Query overrides (not saved): aov, cac, product_cost_pct, fulfillment, curve=live|goal, max_age."""
    conn = db.get_conn()
    _, res, fin = _run(conn)
    inp = dict(fin.payback_inputs)
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
    pb = model.run_payback(inp, _curve(res, fin, curve_kind))
    return jsonify(tables.payback_payload(fin, pb, inp, curve_kind))


@bp.get("/matrix")
def matrix():
    """Profit at the target cohort age by monthly spend x NC-ROAS. Query overrides: target_month, basis=cm|loaded, fixed_cost, spends, roas, plus the payback overrides."""
    conn = db.get_conn()
    _, res, fin = _run(conn)
    inp = dict(fin.payback_inputs)
    for k, lo, hi in (("aov", 0.01, 1e6), ("cac", 0.0, 1e6), ("product_cost_pct", 0.0, 1.0), ("fulfillment", 0.0, 1e6)):
        v = _float_arg(k, lo, hi)
        if v is not None:
            inp[k] = v
    curve = _curve(res, fin, inp["curve"])
    basis = request.args.get("basis")
    if basis is not None and basis not in ("cm", "loaded"):
        raise ApiError(400, "basis must be cm or loaded")
    pb = model.run_payback(inp, curve)
    m = model.run_matrix(fin.params, pb, curve, target_month=_int_arg("target_month", 0, 60), basis=basis,
                         fixed_cost=_float_arg("fixed_cost", 0, 1e10), spends=_list_arg("spends", 1, 1e10), roas=_list_arg("roas", 0.01, 100))
    return jsonify(tables.matrix_payload(m))


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
    repeat, used = model.payout_repeat(prm, res.results.get("cohorts"))
    return jsonify(tables.payout_payload(model.run_payout(prm, repeat), used))


@bp.get("/inputs")
def inputs():
    conn = db.get_conn()
    slug = _slug(conn)
    scn = scenarios.get_scenario(conn, slug)
    own = (scenarios.get_row(conn, slug)["params"] or {}).get(P.NS) or {}
    cells = series.fetch_cells(conn, scn, prefix="financing.")
    return jsonify({
        "scenario": slug, "version": scn.version, "params": P.effective(scn.params), "scenario_params": own, "defaults": P.DEFAULTS,
        "sources": P.SOURCES,
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
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params={P.NS: patch}, expected_version=ev, actor=audit.current_actor())
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
