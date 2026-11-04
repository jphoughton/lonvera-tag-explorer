"""/api/projection/modeler: the Scenario modeler page.

    GET  /state?scenario=            the page's whole state for the saved scenario (grid, yearly results, retention model, plan of record for the difference)
    POST /preview                    {scenario, preview:true, edits?, op?, retention?}  the same state on a DRAFT: nothing is written (the app's read-only connection)
    GET  /compare?scenarios=a,b,c    the yearly table of each scenario as saved (up to 5)
    POST /workspace                  open (or create, as a copy of the plan of record) the working scenario named "Modeler"
    POST /save-as                    {source, name}  a named, frozen copy of the working scenario
    POST /reset                      {scenario}  plan inputs and retention model back to the plan of record's (audited, restorable from History)
    PUT  /settings                   {scenario, ebitda_addbacks}  which P&L lines sit below EBITDA

Edits are saved through the core PUT /series and PUT /cohorts/levers (audited, versioned, restorable); this blueprint adds the read-only draft, the quick-edit
expansion, and the three safety actions. Origin check, JSON-only, rate limits and the app's auth come from the core guards.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, rwdb, scenarios, series
from . import model, ops

bp = Blueprint("proj_modeler", __name__)
MAX_COMPARE = 5


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _slug(conn, name: Any) -> str:
    if name is None or name == "":
        return scenarios.default_slug(conn)
    if not isinstance(name, str):
        raise ApiError(400, "scenario must be a slug")
    scenarios.get_row(conn, name)                                   # 404 for an unknown scenario
    return name


def _required_slug(data: dict) -> str:
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    return slug


# ------------------------------------------------------------------------------------------------------------------ reads

@bp.get("/state")
def state():
    conn = db.get_conn()
    return jsonify(model.run_state(conn, _slug(conn, request.args.get("scenario")), with_por=True))


@bp.post("/preview")
def preview():
    """Runs the model on the draft and returns the state. Writes nothing: the connection is the app's read-only one and the draft lives in memory."""
    data = _body()
    conn = db.get_conn()
    slug = _slug(conn, data.get("scenario"))
    return jsonify(model.run_state(conn, slug, edits=data.get("edits"), op=data.get("op"), retention=data.get("retention"), with_por=bool(data.get("with_por"))))


@bp.get("/compare")
def compare():
    conn = db.get_conn()
    raw = (request.args.get("scenarios") or "").strip()
    slugs = list(dict.fromkeys(s.strip() for s in raw.split(",") if s.strip()))
    if not slugs:
        raise ApiError(400, "scenarios is required (comma-separated slugs)")
    if len(slugs) > MAX_COMPARE:
        raise ApiError(400, f"compare at most {MAX_COMPARE} scenarios")
    for s in slugs:
        scenarios.get_row(conn, s)
    return jsonify({"scenarios": model.compare(conn, slugs)})


# ------------------------------------------------------------------------------------------------------------------ the working scenario

@bp.post("/workspace")
def workspace():
    """The scenario this page works on. Created once, as a copy of the plan of record, so the plan of record is never the thing being tried out."""
    with rwdb.get_rw() as conn:
        def find():
            return next((s for s in scenarios.list_scenarios(conn) if not s["is_default"] and (s["slug"] == "modeler" or s["name"].lower() == model.WORKSPACE_NAME.lower())), None)

        found = find()
        if found:
            return jsonify({"scenario": found, "created": False})
        try:
            made = scenarios.create(conn, model.WORKSPACE_NAME, clone_from=scenarios.default_slug(conn), actor=audit.current_actor())
        except ApiError as e:
            if e.status != 409:
                raise
            found = find()                                         # another tab created it a moment ago
            if not found:
                raise
            return jsonify({"scenario": found, "created": False})
        return jsonify({"scenario": made, "created": True})


@bp.post("/save-as")
def save_as():
    """A named, frozen copy of the working scenario (body: {source, name}). Later edits to the working scenario never reach the copy.
    The key is `source`, not `scenario`: this is a clone, not an edit of a scenario, so the page's plan-of-record guard leaves it alone."""
    data = _body()
    slug = data.get("source")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "source is required")
    with rwdb.get_rw() as conn:
        src = scenarios.get_row(conn, slug)
        made = scenarios.create(conn, data.get("name"), clone_from=slug, actor=audit.current_actor())
        if not src["is_default"] and not made.get("frozen"):                  # a core without frozen copies: keep the copy on the working scenario's own fallback
            conn.execute("UPDATE proj_scenarios SET base_slug = %s WHERE id = %s", (src["base_slug"], made["id"]))
            audit.record(conn, actor=None, scenario=made["slug"], action="scenario.rebase", old={"base_slug": slug}, new={"base_slug": src["base_slug"]},
                         note=f"saved from {slug}", version=made["version"])
            made = scenarios.get_row(conn, made["slug"])
            made = {k: made[k] for k in ("id", "slug", "name", "base_slug", "is_default", "params", "version", "created_at", "updated_at") if k in made}
        return jsonify({"scenario": made}), 201


@bp.post("/reset")
def reset():
    """Plan inputs (spend, order value, cost per customer) and the retention model back to the plan of record's. Every other setting stays."""
    data = _body()
    slug = _required_slug(data)
    with rwdb.get_rw() as conn:
        row = scenarios.get_row(conn, slug)
        if row["is_default"]:
            raise ApiError(400, "This is the plan of record. There is nothing to reset to.")
        por = scenarios.default_slug(conn)
        keys = list(ops.ROWS.values())
        mine = {(c["line_key"], c["month"]): c for c in series.fetch_cells(conn, slug, keys)}
        theirs = {(c["line_key"], c["month"]): c for c in series.fetch_cells(conn, por, keys)}
        items = []
        for (k, m) in sorted(mine.keys() | theirs.keys()):
            a, b = mine.get((k, m)), theirs.get((k, m))
            if a is not None and b is not None and a["value"] == b["value"]:
                continue
            if a is None and b is None:
                continue
            items.append({"line_key": k, "month": m.isoformat(), "value": None if b is None else b["value"], "kind": "input" if b is None else b["kind"],
                          "note": "reset to the plan of record"})
        changed, version = 0, row["version"]
        for i in range(0, len(items), series.MAX_ITEMS):
            res = series.apply_items(conn, slug, series.parse_items(items[i:i + series.MAX_ITEMS]), actor=audit.current_actor())
            changed, version = changed + res["changed"], res["version"]
        mine_p = (scenarios.get_scenario(conn, slug).params or {}).get("cohorts") or {}
        theirs_p = (scenarios.get_scenario(conn, por).params or {}).get("cohorts") or {}
        keys_p = ("curve_source", "asof_month", "n_cohorts", "curve")
        patch = {k: theirs_p.get(k) for k in keys_p if mine_p.get(k) != theirs_p.get(k)}
        if patch:
            scenarios.update(conn, slug, params={"cohorts": patch}, actor=audit.current_actor())
            version = scenarios.get_row(conn, slug)["version"]
        return jsonify({"scenario": slug, "cells_changed": changed, "retention_reset": bool(patch), "version": version})


@bp.put("/settings")
def settings():
    data = _body()
    slug = _required_slug(data)
    addbacks = model.check_addbacks(data.get("ebitda_addbacks"))
    ev = data.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, params={model.NAMESPACE: {"ebitda_addbacks": addbacks}}, expected_version=ev, actor=audit.current_actor())
    return jsonify({"scenario": row, "ebitda_addbacks": addbacks})
