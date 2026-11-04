"""/api/projection/pnl: table, metrics, summary, ev, compare, drivers (GET/PUT), settings (PUT), qb-map, freshness, upload (preview then commit).

Auth, Origin check, rate limits, JSON-only bodies and size caps come from the projection write guard (api.install_guards): nothing is repeated here.
Every write is audited in proj_audit. Driver and setting edits are scenario edits (versioned, restorable) through scenarios.update; QuickBooks loads
write proj_actuals and one 'pnl.upload' audit row carrying the file's sha256 and row count.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from ... import db
from ..interfaces import MONTHS
from ...queries import ApiError
from .. import audit, engine, rwdb, scenarios, series
from .. import freshness as fresh
from . import lines as L
from . import qb, store, views
from .model import SCENARIO_GROUPS, PnlOutput, validate_segments

bp = Blueprint("proj_pnl", __name__)
MAX_COMPARE = 8


def _today() -> date:                                         # a seam for tests (patch store.today)
    return store.today()


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _slug(conn, name: str | None = None) -> str:
    name = name if name is not None else (request.args.get("scenario") or "").strip() or None
    if name is None:
        return scenarios.default_slug(conn)
    if not isinstance(name, str):
        raise ApiError(400, "scenario must be a slug")
    scenarios.get_row(conn, name)                             # 404 for an unknown scenario
    return name


def _run(conn, slug: str):
    res = engine.run(slug, conn, today=_today())              # not strict: another module's failure falls back to its stub
    out = res.results.get("pnl")
    if not isinstance(out, PnlOutput):
        raise ApiError(500, res.errors.get("pnl") or "the P&L module did not run")
    return res, out


def _freshness(conn) -> dict:
    today = _today()
    entries = store.freshness_entries(conn, today)
    return fresh.evaluate("pnl", entries[0], today)


# ------------------------------------------------------------------------------------------------------------------ reads

@bp.get("/table")
def table():
    conn = db.get_conn()
    _, out = _run(conn, _slug(conn))
    return jsonify(views.table_payload(out, sources=store.actual_sources(conn)))


@bp.get("/metrics")
def metrics():
    conn = db.get_conn()
    _, out = _run(conn, _slug(conn))
    return jsonify(views.metrics_payload(out))


@bp.get("/summary")
def summary():
    conn = db.get_conn()
    _, out = _run(conn, _slug(conn))
    return jsonify(views.summary(out, _freshness(conn), _today(), store.stale_after_days(conn)))


@bp.get("/freshness")
def freshness_get():
    conn = db.get_conn()
    return jsonify({**_freshness(conn), "late_after_days": store.stale_after_days(conn)})


@bp.put("/freshness")
def freshness_put():
    """The one global (not per scenario) setting: days after a month closes before QuickBooks not having it counts as late."""
    data = _body()
    days = _num(data.get("stale_after_days"), "stale_after_days", 1, 365, integer=True)
    with rwdb.get_rw() as conn:
        old = store.stale_after_days(conn)
        if old != days:
            conn.execute("INSERT INTO proj_pnl_settings (key, value, note) VALUES ('freshness', %s, 'Freshness rule') "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (Jsonb({"stale_after_days": days}),))
            audit.record(conn, actor=audit.current_actor(), scenario=None, action="pnl.setting", line_key="pnl.freshness.stale_after_days",
                         old={"value": old}, new={"value": days})
    return jsonify({"stale_after_days": days})


def _scenario_list(conn, raw: str | None) -> list[str]:
    if raw:
        slugs = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        slugs = [s["slug"] for s in scenarios.list_scenarios(conn)]
    if not slugs or len(slugs) > MAX_COMPARE:
        raise ApiError(400, f"scenarios must list 1-{MAX_COMPARE} scenario slugs")
    for s in slugs:
        scenarios.get_row(conn, s)
    return slugs


@bp.get("/ev")
def ev():
    """Implied enterprise value: one row per scenario, one column per multiple, against the LOI."""
    conn = db.get_conn()
    slugs = _scenario_list(conn, request.args.get("scenarios"))
    rows = []
    base_ev: dict = {}
    for s in slugs:
        res, out = _run(conn, s)
        e = out.ev or {}
        base_ev = base_ev or e
        loi = e.get("loi") or 0.0
        mults = [m for m in e.get("multiples", [])]
        rows.append({"slug": s, "name": res.scenario.name, "basis_noi": e.get("basis_noi"), "values": {f"{m:g}x": e.get(f"{m:g}x") for m in mults},
                     "vs_loi": {f"{m:g}x": (None if e.get(f"{m:g}x") is None else e[f"{m:g}x"] - loi) for m in mults}})
    return jsonify({"year": base_ev.get("basis_year"), "multiples": base_ev.get("multiples", []), "loi": base_ev.get("loi"), "rows": rows,
                    "caption": "Full-year net operating income x multiple. Not an add-back-adjusted earnings figure."})


@bp.get("/compare")
def compare():
    """Revenue, gross profit and net operating income by fiscal year, one entry per scenario."""
    conn = db.get_conn()
    slugs = _scenario_list(conn, request.args.get("scenarios"))
    out_rows = []
    years: list[int] = []
    for s in slugs:
        res, out = _run(conn, s)
        years = sorted(out.fy)
        out_rows.append({"slug": s, "name": res.scenario.name,
                         **{k: [out.fy[y].get(k) for y in years] for k in ("total_revenue", "gross_profit", "noi", "noi_margin", "marketing_spend")}})
    return jsonify({"years": years, "scenarios": out_rows})


def _drivers_payload(conn, slug: str) -> dict:
    res, out = _run(conn, slug)
    row = scenarios.get_row(conn, slug)
    own = (row["params"] or {}).get("pnl", {})
    payload = views.drivers_payload(out, store.load_driver_defaults(conn), own.get("drivers") or {}, store.load_driver_notes(conn))
    first_projected = next((m for m in MONTHS if out.actual_through is None or m > out.actual_through), None)
    overrides = {k: [{"month": m.isoformat(), "value": v} for m, v in sorted(s.items())]
                 for k, s in store.load_overrides(conn, slug).items() if k in L.DRIVER_KEYS + L.DRIVEN_PARENTS}
    for g in payload["groups"]:
        for it in g["items"]:
            it["overrides"] = overrides.get(it["key"], [])
    payload.update(scenario=slug, version=row["version"], vendors=store.load_vendors(conn), late_after_days=store.stale_after_days(conn), first_projected=first_projected.isoformat() if first_projected else None,
                   own_settings={k: v for k, v in own.items() if k != "drivers"})
    return payload


@bp.get("/drivers")
def drivers_get():
    conn = db.get_conn()
    return jsonify(_drivers_payload(conn, _slug(conn)))


# ------------------------------------------------------------------------------------------------------------------ driver and setting edits

def _version(data: dict) -> int | None:
    v = data.get("expected_version")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, "expected_version must be an integer")
    return v


@bp.put("/drivers")
def drivers_put():
    data = _body()
    key, segs = data.get("key"), data.get("segments")
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    if not isinstance(key, str) or key not in L.DRIVER_KEYS + L.DRIVEN_PARENTS:
        raise ApiError(400, "key must be a driven line")
    if segs is None:
        patch: Any = {"pnl": {"drivers": {key: None}}}                 # null: back to the default
    else:
        try:
            patch = {"pnl": {"drivers": {key: {"segments": validate_segments(key, segs)}}}}
        except ValueError as e:
            raise ApiError(400, str(e)) from None
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params=patch, expected_version=_version(data), actor=audit.current_actor())
    return jsonify({"scenario": row})


def _num(v: Any, name: str, lo: float, hi: float, integer: bool = False) -> float | int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ApiError(400, f"{name} must be a number from {lo:g} to {hi:g}")
    if integer and (isinstance(v, float) and v != int(v)):
        raise ApiError(400, f"{name} must be a whole number")
    return int(v) if integer else float(v)


def check_settings(patch: Any) -> dict:
    """Validate a settings patch {engine?, headcount?, cogs?, ev?, trend?}; null removes a scenario value (back to the default)."""
    if not isinstance(patch, dict) or not patch:
        raise ApiError(400, "settings must be a non-empty object")
    out: dict = {}
    for name, v in patch.items():
        if name not in SCENARIO_GROUPS:
            raise ApiError(400, f"unknown setting group {name!r}")
        if v is None:
            out[name] = None
            continue
        if not isinstance(v, dict):
            raise ApiError(400, f"{name} must be an object")
        g: dict = {}
        for k, x in v.items():
            if x is None:
                g[k] = None
            elif name == "engine" and k == "rate_window_months":
                g[k] = _num(x, k, 1, 36, integer=True)
            elif name == "trend" and k == "avg_window":
                g[k] = _num(x, k, 1, 36, integer=True)
            elif name == "engine" and k in ("opex_inflation", "cogs_inflation"):
                g[k] = _num(x, k, -0.5, 1.0)
            elif name == "headcount" and k in ("base", "increment"):
                g[k] = _num(x, k, 0, 1e7)
            elif name == "headcount" and k in ("threshold", "step"):
                g[k] = _num(x, k, 1, 1e10)
            elif name == "headcount" and k == "start":
                try:
                    d = date.fromisoformat(x)
                except (TypeError, ValueError):
                    raise ApiError(400, "start must be a first-of-month date") from None
                if d.day != 1:
                    raise ApiError(400, "start must be a first-of-month date")
                g[k] = x
            elif name == "cogs" and k == "us_share":
                g[k] = _num(x, k, 0, 1)
            elif name == "cogs" and k in ("price_per_bottle", "current_price_per_bottle"):
                g[k] = _num(x, k, 0.01, 10000)
            elif name == "cogs" and k in ("mix", "china_cost", "us_cost"):
                if not isinstance(x, dict) or len(x) > 50:
                    raise ApiError(400, f"{k} must be an object of SKU -> number")
                hi = 1 if k == "mix" else 1000
                g[k] = {str(sk)[:80]: (None if sv is None else _num(sv, f"{k} {sk}", 0, hi)) for sk, sv in x.items()}
            elif name == "ev" and k == "multiples":
                if not isinstance(x, list) or not 1 <= len(x) <= 12:
                    raise ApiError(400, "multiples must list 1-12 numbers")
                g[k] = [_num(m, "multiple", 0.1, 50) for m in x]
            elif name == "ev" and k == "year":
                g[k] = _num(x, k, 2025, 2029, integer=True)
            elif name == "ev" and k == "loi":
                g[k] = _num(x, k, 0, 1e10)
            else:
                raise ApiError(400, f"unknown setting {name}.{k}")
        out[name] = g
    return out


@bp.put("/settings")
def settings_put():
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    patch = {"pnl": check_settings(data.get("settings"))}
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params=patch, expected_version=_version(data), actor=audit.current_actor())
    return jsonify({"scenario": row})


# ------------------------------------------------------------------------------------------------------------------ QuickBooks account map

@bp.get("/qb-map")
def qb_map_get():
    conn = db.get_conn()
    m = store.load_qb_map(conn)
    return jsonify({"map": [{"account": a, "line_key": k, "line": L.LINE_KEYS[k].label if k in L.LINE_KEYS else k} for a, k in sorted(m.items())],
                    "lines": [{"key": k, "label": L.LINE_KEYS[k].label} for k in L.QB_KEYS]})


@bp.put("/qb-map")
def qb_map_put():
    data = _body()
    account, key = data.get("account"), data.get("line_key")
    if not isinstance(account, str) or not store.norm_account(account) or len(account) > 200:
        raise ApiError(400, "account is required")
    if key not in L.QB_KEYS:
        raise ApiError(400, "line_key must be a line that takes QuickBooks data")
    with rwdb.get_rw() as conn:
        store.upsert_qb_map(conn, [(account, key, "Mapped on upload")], actor=audit.current_actor(), audit_it=True)
    return jsonify({"account": store.norm_account(account), "line_key": key})


@bp.delete("/qb-map")
def qb_map_delete():
    data = _body()
    account = data.get("account")
    if not isinstance(account, str) or not store.norm_account(account):
        raise ApiError(400, "account is required")
    with rwdb.get_rw() as conn:
        removed = store.delete_qb_map(conn, account, actor=audit.current_actor())
    if not removed:
        raise ApiError(404, "no such account in the map")
    return jsonify({"deleted": store.norm_account(account)})


# ------------------------------------------------------------------------------------------------------------------ upload

def _existing(conn) -> dict[str, dict[date, float]]:
    return store.load_actuals(conn)


def _upload_file() -> tuple[bytes, str]:
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError(400, "attach the QuickBooks export as the 'file' field (csv or xlsx)")
    data = f.read(qb.MAX_BYTES + 1)
    if len(data) > qb.MAX_BYTES:
        raise ApiError(400, "the file is larger than 8 MB")
    return data, f.filename


@bp.post("/upload")
def upload():
    """Preview (default) or commit (?commit=1 with the preview's diff_sha256 in the form)."""
    data, name = _upload_file()
    commit = request.args.get("commit") in ("1", "true")
    today = _today()
    try:
        parsed = qb.parse(data, name)
    except qb.UploadError as e:
        raise ApiError(400, str(e)) from None
    if not commit:
        conn = db.get_conn()
        pv, _ = qb.preview(parsed, store.load_qb_map(conn), _existing(conn), today)
        return jsonify({**pv, "committed": False})
    token = (request.form.get("diff_sha256") or "").strip()
    if not token:
        raise ApiError(400, "diff_sha256 is required: preview the file first and send the hash it returned")
    with rwdb.get_rw() as conn:
        pv, cells = qb.preview(parsed, store.load_qb_map(conn), _existing(conn), today)
        if token != pv["diff_sha256"]:
            raise ApiError(409, "the loaded data changed since the preview: preview the file again")
        if not cells:
            raise ApiError(400, "nothing to load: no mapped account has a closed month")
        rows = [(L.actuals_key(k), m, v) for (k, m), v in cells.items()]
        src = f"QuickBooks upload {name[:80]} (sha256 {parsed.sha256[:12]})"
        n = series.upsert_actuals(conn, rows, src, actor=audit.current_actor())
        audit.record(conn, actor=audit.current_actor(), scenario=None, action="pnl.upload", note=name[:200],
                     old={"changed": pv["changes"], "changes_truncated": pv["changes_truncated"]},
                     new={"sha256": parsed.sha256, "bytes": parsed.bytes, "file_rows": parsed.rows, "cells": n, "new": pv["cells"]["new"],
                          "changed": pv["cells"]["changed"], "months": [m["month"] for m in pv["months"]]})
    return jsonify({**pv, "committed": True, "cells_written": n})
