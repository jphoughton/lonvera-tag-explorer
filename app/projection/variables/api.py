"""/api/projection/variables: every variable of the model in one list, edited through the owning module's mechanism (audited, versioned, restorable).

    GET  /                    ?scenario=&module=&q=&changed=1     variables grouped for the page, with current value, sheet value and changed flag
    PUT  /                    {scenario, items:[{id, value}], expected_version?}      value null = back to the sheet value
    PUT  /<id>                {scenario, value, expected_version?}
    POST /reset               {scenario, ids? | module? | group? | all?, expected_version?}
    GET  /sensitivity         ?scenario=       one-at-a-time +/-10% on every scalar number: effect on FY27 net operating income, revenue, lowest cash
Auth, Origin, rate limits and the JSON-only rule are the core's app-level guards.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, rwdb, scenarios
from . import access as A, registry, sensitivity
from .spec import MODULE_LABELS, MODULES, VariableSpec

bp = Blueprint("proj_variables", __name__)
MAX_ITEMS = 100


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _arg(name: str) -> str | None:
    v = request.args.get(name)
    v = v.strip() if v else None
    return v or None


def _scenario_slug(conn, name: Any) -> str:
    if name is None:
        return scenarios.default_slug(conn)
    if not isinstance(name, str) or not name:
        raise ApiError(400, "scenario must be a slug")
    scenarios.get_row(conn, name)
    return name


def _int(data: dict, name: str) -> int | None:
    v = data.get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, f"{name} must be an integer")
    return v


def entry(spec: VariableSpec, r: A.Resolved) -> dict:
    out = spec.to_meta()
    out.update({"value": r.value, "changed": r.changed, "origin": r.origin, "computed": r.computed, "summary": r.summary,
                "edit_link": f"/projection/{spec.page}" if spec.page else None})
    return out


def listing(conn, slug: str, *, module: str | None = None, q: str | None = None, changed_only: bool = False) -> dict:
    ctx = A.Context(conn, slug)
    needle = (q or "").lower()
    rows = []
    for spec in registry.all_specs():
        if module and spec.module != module:
            continue
        if needle and needle not in " ".join((spec.id, spec.label, spec.group, spec.sheet_ref, spec.where_used, spec.notes)).lower():
            continue
        try:
            r = A.resolve(spec, ctx)
        except Exception:                                    # one unreadable variable must not hide the others
            try:
                conn.rollback()
            except Exception:
                pass
            r = A.Resolved(value=None, default=spec.default, changed=None, origin="default", summary={"text": "Could not be read"})
        if changed_only and not r.changed:
            continue
        rows.append(entry(spec, r))
    scn = ctx.scn
    all_rows = rows if not (module or needle or changed_only) else None
    return {"scenario": {"slug": scn.slug, "name": scn.name, "version": scn.version, "is_default": scn.is_default, "base_slug": scn.base_slug},
            "modules": [{"key": m, "label": MODULE_LABELS[m]} for m in MODULES], "counts": registry.counts(), "variables": rows,
            "changed_count": sum(1 for x in (all_rows if all_rows is not None else _all_rows(conn, ctx)) if x["changed"] and x["kind"] == "scalar" and x["status"] != "MISSING"),
            "shown": len(rows)}


def _all_rows(conn, ctx: A.Context) -> list[dict]:
    out = []
    for spec in registry.all_specs():
        try:
            out.append({"changed": A.resolve(spec, ctx).changed, "kind": spec.kind, "status": spec.status})
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    return out


@bp.get("")
@bp.get("/")
def list_variables():
    conn = db.get_conn()
    slug = _scenario_slug(conn, _arg("scenario"))
    module = _arg("module")
    if module and module not in MODULES:
        raise ApiError(400, f"module must be one of {', '.join(MODULES)}")
    return jsonify(listing(conn, slug, module=module, q=_arg("q"), changed_only=_arg("changed") == "1"))


def _spec(var_id: Any) -> VariableSpec:
    if not isinstance(var_id, str):
        raise ApiError(400, "id must be a variable id")
    s = registry.get(var_id)
    if s is None:
        raise ApiError(404, f"unknown variable {var_id!r}")
    return s


def _write(slug: str, items: list[tuple[VariableSpec, Any]], expected_version: int | None) -> dict:
    with rwdb.get_rw() as conn:
        before_ctx = A.Context(conn, slug)
        before = {s.id: A.resolve(s, before_ctx).value for s, _ in items}
        row = A.set_values(conn, slug, items, expected_version=expected_version, actor=audit.current_actor())
        ctx = A.Context(conn, slug)
        ids = {s.id for s, _ in items}
        out = []
        for spec in registry.all_specs():
            if spec.kind == "scalar" and spec.binding != "none" and (spec.id in ids or spec.module == "drivers" and spec.id.startswith("drivers.channel_share.") and any(i.startswith("drivers.channel_share.") for i in ids)):
                out.append(entry(spec, A.resolve(spec, ctx)))
        version = row["version"] if isinstance(row, dict) else ctx.scn.version
        return {"scenario": {"slug": slug, "version": scenarios.get_row(conn, slug)["version"]}, "variables": out, "previous": before}


@bp.put("")
@bp.put("/")
def put_variables():
    data = _body()
    extra = set(data) - {"scenario", "items", "expected_version"}
    if extra:
        raise ApiError(400, f"unknown field {sorted(extra)[0]!r}")
    raw = data.get("items")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "items must be a non-empty list")
    if len(raw) > MAX_ITEMS:
        raise ApiError(400, f"at most {MAX_ITEMS} items per request")
    items = []
    seen = set()
    for i, it in enumerate(raw):
        if not isinstance(it, dict) or set(it) - {"id", "value"} or "value" not in it:
            raise ApiError(400, f"items[{i}] needs id and value")
        if it["id"] in seen:
            raise ApiError(400, f"items[{i}]: {it['id']} appears twice")
        seen.add(it["id"])
        items.append((_spec(it["id"]), it["value"]))
    conn = db.get_conn()
    slug = _scenario_slug(conn, data.get("scenario"))
    sensitivity.clear()
    return jsonify(_write(slug, items, _int(data, "expected_version")))


@bp.put("/<var_id>")
def put_variable(var_id: str):
    data = _body()
    if "value" not in data:
        raise ApiError(400, "value is required (null goes back to the sheet value)")
    conn = db.get_conn()
    slug = _scenario_slug(conn, data.get("scenario"))
    sensitivity.clear()
    return jsonify(_write(slug, [(_spec(var_id), data["value"])], _int(data, "expected_version")))


@bp.post("/reset")
def reset_variables():
    data = _body()
    conn = db.get_conn()
    slug = _scenario_slug(conn, data.get("scenario"))
    ctx = A.Context(conn, slug)
    ids, module, group = data.get("ids"), data.get("module"), data.get("group")
    pool = [s for s in registry.all_specs() if s.editable_inline]
    if ids is not None:
        if not isinstance(ids, list):
            raise ApiError(400, "ids must be a list")
        pool = [_spec(i) for i in ids]
    elif module:
        if module not in MODULES:
            raise ApiError(400, f"module must be one of {', '.join(MODULES)}")
        pool = [s for s in pool if s.module == module and (not group or s.group == group)]
    elif not data.get("all"):
        raise ApiError(400, "send ids, module (with an optional group) or all:true")
    targets = [s for s in pool if A.resolve(s, ctx).changed]
    if not targets:
        return jsonify({"scenario": {"slug": slug, "version": ctx.scn.version}, "reset": [], "variables": []})
    sensitivity.clear()
    with rwdb.get_rw() as rw:
        A.reset(rw, slug, targets, expected_version=_int(data, "expected_version"), actor=audit.current_actor())
        rctx = A.Context(rw, slug)
        out = [entry(s, A.resolve(s, rctx)) for s in targets]
        version = scenarios.get_row(rw, slug)["version"]
    return jsonify({"scenario": {"slug": slug, "version": version}, "reset": [s.id for s in targets], "variables": out})


@bp.get("/sensitivity")
def sensitivity_get():
    conn = db.get_conn()
    slug = _scenario_slug(conn, _arg("scenario"))
    return jsonify(sensitivity.run(conn, slug))
