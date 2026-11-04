"""HTTP layer for projection core: /api/projection/{modules,scenarios,series,audit,run} plus the guard rails for every projection write.

Auth is not handled here: the app's own /api/* guard covers /api/projection/* (login on = 401 without a session, AUTH_DISABLED = open).
`install_guards(app)` adds ONE app-level before_request for the /api/projection/ prefix, so a module blueprint added later is covered with no
code of its own: Origin check, per-IP rate limits, JSON-only bodies and the request size cap for every non-GET method.
Noindex is the app's: it sends `X-Robots-Tag: noindex, nofollow` on every response and serves `/robots.txt` with `Disallow: /`.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from .. import db
from ..queries import ApiError
from . import audit, degraded, engine, freshness, registry, rwdb, scenarios, series
from .interfaces import AXIS_END, AXIS_START, parse_month
from .limits import Limits

PREFIX = "/api/projection"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
MAX_JSON_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 8_000_000

bp = Blueprint("projection_api", __name__, url_prefix=PREFIX)


# ------------------------------------------------------------------------------------------------------------------ guard rails

def _same_origin(origin: str) -> bool:
    try:
        return urlsplit(origin).netloc.lower() == request.host.lower() != ""
    except ValueError:
        return False


def _is_upload_path(path: str) -> bool:
    return any(seg.startswith("upload") for seg in path.split("/"))


def _too_many(wait: int):
    resp = jsonify(error="rate_limited")
    resp.status_code = 429
    resp.headers["Retry-After"] = str(wait)
    return resp


def _write_guard():
    """Runs before every request; acts only on non-GET requests under /api/projection/."""
    path = request.path
    if not (path.startswith(PREFIX + "/") or path == PREFIX) or request.method in SAFE_METHODS:
        return None
    origin = request.headers.get("Origin")
    if origin is not None and not _same_origin(origin):
        return jsonify(error="origin_mismatch"), 403
    limits: Limits = current_app.extensions["proj_limits"]
    ip = request.remote_addr or "unknown"
    preview = path.rstrip("/").rsplit("/", 1)[-1] == "preview"       # computes and writes nothing: its own, higher budget
    wait = limits.previews.hit(ip) if preview else limits.mutations.hit(ip)
    if not wait and _is_upload_path(path):
        wait = limits.uploads.hit(ip)
    if wait:
        return _too_many(wait)
    mime = request.mimetype
    has_body = request.method in ("POST", "PUT", "PATCH") or bool(request.content_length)
    if has_body and mime != "application/json" and not (mime == "multipart/form-data" and _is_upload_path(path)):
        return jsonify(error="json_required"), 415
    request.max_content_length = MAX_UPLOAD_BYTES if mime == "multipart/form-data" else MAX_JSON_BYTES      # the app-wide cap is 64 KB
    return None


def _degraded_gate(resp):
    """A GET on a module's endpoints whose module (or anything it reads) the engine degraded during the request is answered 503 upstream_degraded,
    whatever the module's own code returned (a 200 built on nothing, or its own 500)."""
    path = request.path
    code = resp.status_code
    if request.method not in ("GET", "HEAD") or not path.startswith(PREFIX + "/") or not (200 <= code < 300 or code >= 500) or code == 503:
        return resp
    seg = path[len(PREFIX) + 1:].split("/", 1)[0].replace("-", "_")
    errors = g.get("proj_degraded")
    if not errors or seg not in engine.ORDER:
        return resp
    bad = degraded.degraded_in(errors, seg)
    return degraded.response(bad) if bad else resp


def install_guards(app) -> None:
    app.extensions["proj_limits"] = Limits.from_config(app.config)
    degraded.register(app)
    app.after_request(_degraded_gate)
    app.before_request(_write_guard)


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


def _int_field(data: dict, name: str, required: bool = False) -> int | None:
    v = data.get(name)
    if v is None:
        if required:
            raise ApiError(400, f"{name} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, f"{name} must be an integer")
    return v


def _month_arg(name: str) -> date | None:
    v = _arg(name)
    if v is None:
        return None
    try:
        return parse_month(v)
    except ValueError as e:
        raise ApiError(400, f"{name}: {e}") from None


def _scenario_slug(conn, name: str | None) -> str:
    if name is None:
        return scenarios.default_slug(conn)
    if not isinstance(name, str):
        raise ApiError(400, "scenario must be a slug")
    scenarios.get_row(conn, name)                                  # 404 for an unknown scenario
    return name


# ------------------------------------------------------------------------------------------------------------------ routes

@bp.get("/modules")
def modules():
    return jsonify({"modules": registry.listing()})


@bp.get("/scenarios")
def scenarios_list():
    return jsonify({"scenarios": scenarios.list_scenarios(db.get_conn())})


@bp.get("/scenarios/archived")
def scenarios_archived():
    return jsonify({"archived": scenarios.list_archived(db.get_conn())})


@bp.post("/scenarios/<slug>/detach")
def scenarios_detach(slug: str):
    data = _body()
    with rwdb.get_rw() as conn:
        row = scenarios.detach(conn, slug, expected_version=_int_field(data, "expected_version"), actor=audit.current_actor())
    return jsonify({"scenario": row})


@bp.post("/scenarios/<slug>/undelete")
def scenarios_undelete(slug: str):
    _body()
    with rwdb.get_rw() as conn:
        row = scenarios.undelete(conn, slug, actor=audit.current_actor())
    return jsonify({"scenario": row})


@bp.post("/scenarios")
def scenarios_create():
    data = _body()
    clone_from = data.get("clone_from")
    if clone_from is not None and not isinstance(clone_from, str):
        raise ApiError(400, "clone_from must be a scenario slug")
    with rwdb.get_rw() as conn:
        row = scenarios.create(conn, data.get("name"), clone_from or None, actor=audit.current_actor())
    return jsonify({"scenario": row}), 201


@bp.post("/scenarios/<slug>/clone")
def scenarios_clone(slug: str):
    data = _body()
    with rwdb.get_rw() as conn:
        row = scenarios.create(conn, data.get("name"), slug, actor=audit.current_actor())
    return jsonify({"scenario": row}), 201


@bp.patch("/scenarios/<slug>")
def scenarios_patch(slug: str):
    data = _body()
    with rwdb.get_rw() as conn:
        row = scenarios.update(conn, slug, name=data.get("name"), params=data.get("params"),
                               expected_version=_int_field(data, "expected_version"), actor=audit.current_actor())
    return jsonify({"scenario": row})


@bp.delete("/scenarios/<slug>")
def scenarios_delete(slug: str):
    data = request.get_json(silent=True) if request.content_length else None
    confirm = data.get("confirm") if isinstance(data, dict) else None
    with rwdb.get_rw() as conn:
        scenarios.delete(conn, slug, confirm, actor=audit.current_actor())
    return jsonify({"deleted": slug})


@bp.post("/scenarios/<slug>/restore")
def scenarios_restore(slug: str):
    data = _body()
    with rwdb.get_rw() as conn:
        row, stats = scenarios.restore(conn, slug, data.get("version"), expected_version=_int_field(data, "expected_version"),
                                       actor=audit.current_actor())
    return jsonify({"scenario": row, "restore": stats})


@bp.get("/scenarios/<slug>/history")
def scenarios_history(slug: str):
    limit = _arg("limit") or "50"
    if not limit.isdigit():
        raise ApiError(400, "limit must be an integer")
    return jsonify({"history": scenarios.list_history(db.get_conn(), slug, int(limit))})


@bp.get("/series")
def series_get():
    conn = db.get_conn()
    slug = _scenario_slug(conn, _arg("scenario"))
    raw_keys, prefix = _arg("keys"), _arg("prefix")
    if (raw_keys is None) == (prefix is None):
        raise ApiError(400, "send exactly one of keys=a,b or prefix=")
    keys = None
    if raw_keys is not None:
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not keys or len(keys) > series.MAX_KEYS:
            raise ApiError(400, f"keys must list 1-{series.MAX_KEYS} line keys")
        for k in keys:
            series.check_key(k, "keys")
    else:
        series.check_key(prefix, "prefix")
    lo, hi = _month_arg("from") or AXIS_START, _month_arg("to") or AXIS_END
    cells = series.fetch_cells(conn, slug, keys, prefix=prefix, from_=lo, to=hi)
    out: dict[str, dict[str, float]] = {k: {} for k in keys or []}
    for c in sorted(cells, key=lambda c: (c["line_key"], c["month"])):
        out.setdefault(c["line_key"], {})[c["month"].isoformat()] = c["value"]
    resp: dict[str, Any] = {"scenario": slug, "version": scenarios.get_row(conn, slug)["version"], "series": out}
    if _arg("detail") == "1":
        detail: dict[str, dict] = {}
        for c in cells:
            detail.setdefault(c["line_key"], {})[c["month"].isoformat()] = {"kind": c["kind"], "note": c["note"], "origin": c["origin"]}
        resp["detail"] = detail
    return jsonify(resp)


@bp.put("/series")
def series_put():
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    items = series.parse_items(data.get("items"))
    with rwdb.get_rw() as conn:
        res = series.apply_items(conn, slug, items, expected_version=_int_field(data, "expected_version"), actor=audit.current_actor())
    return jsonify(res)


@bp.get("/audit")
def audit_list():
    limit = _arg("limit") or "100"
    if not limit.isdigit() or int(limit) < 1:
        raise ApiError(400, "limit must be a positive integer")
    return jsonify({"audit": audit.list_rows(db.get_conn(), scenario=_arg("scenario"), line_key=_arg("line_key"), limit=int(limit))})


@bp.get("/freshness")
def freshness_get():
    return jsonify(freshness.report(db.get_conn()))


@bp.get("/run")
def run():
    conn = db.get_conn()
    return jsonify(engine.run(_arg("scenario"), conn).summary())
