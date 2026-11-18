"""Theme previews API: let a named person edit Shopify theme files in PREVIEW (unpublished) themes created through the app. DARK BUILD: off unless THEME_PREVIEW_ENABLED is '1'.

Gates on every route, in this order (same as launch): THEME_PREVIEW_ENABLED on (else 404), sign-in on (else 403), login listed in THEME_EDITORS (else 403). Never in auth.PUBLIC_API_RE.
Writes re-fetch the theme's role from Shopify each time and refuse the live theme, any theme not in our own theme_previews table, and any file outside the allowed folders and types.
Nothing here can publish a theme or delete anything. Migration 276 holds the tables.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re

from flask import Blueprint, jsonify, request

from . import auth, db
from . import queries as q
from . import shopify_theme as st

bp = Blueprint("theme", __name__)

_LIMITER = "_theme_limiter"
MAX_BYTES = 1_000_000
ALLOWED_DIRS = ("templates/", "sections/", "snippets/", "blocks/", "layout/", "assets/")
ALLOWED_EXT = (".css", ".js", ".liquid", ".json")
LIVE_MSG = "That is the live theme. Live changes are made in Shopify admin."
NOT_OURS_MSG = "That theme was not created here, so it cannot be changed here."


def enabled() -> bool:
    return os.environ.get("THEME_PREVIEW_ENABLED") == "1"


def editors() -> set[str]:
    return {u.strip().lower() for u in str(os.environ.get("THEME_EDITORS") or "").split(",") if u.strip()}


def can_edit() -> bool:
    return enabled() and not auth.auth_disabled() and not auth.is_key_request() and bool(auth.current_user()) and auth.current_user().lower() in editors()


def discovery() -> dict:
    return {"enabled": enabled(), "can_edit": can_edit()}


def _err(status: int, message: str):
    resp = jsonify(error=message)
    resp.status_code = status
    return resp


@bp.before_request
def _gate():
    if request.path == "/api/theme/status":                   # discovery: login only (the global auth check), no editor check
        return None if enabled() else _err(404, "not found")
    if not enabled():
        return _err(404, "not found")
    if auth.auth_disabled():
        return _err(403, "Theme editing needs sign-in to be on.")
    user = auth.current_user()
    if auth.is_key_request() or not user or user.lower() not in editors():
        return _err(403, "Your login is not allowed to edit themes.")
    return None


def _rw():
    from .projection import rwdb
    return rwdb.get_rw()


def _write_guard():
    from . import api
    return api._views_write_guard(_LIMITER, "THEME_WRITES_PER_MIN", 30)


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return body


def _shopify_call(fn, *a):
    try:
        return fn(*a)
    except st.ShopifyError as e:
        raise q.ApiError(502, st.scrub(str(e))) from None


def check_key(key) -> str:
    if not isinstance(key, str) or not key or len(key) > 200 or ".." in key or "\\" in key or key.startswith("/") or "\x00" in key or not re.fullmatch(r"[A-Za-z0-9_./ -]+", key):
        raise q.ApiError(400, "That file path is not allowed.")
    if not key.startswith(ALLOWED_DIRS) or not key.lower().endswith(ALLOWED_EXT):
        raise q.ApiError(400, "Only css, js, liquid and json files under templates, sections, snippets, blocks, layout and assets can be edited.")
    return key


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40].strip("-")
    return s or "preview"


def _preview_row(conn, pid: str):
    return conn.execute("SELECT id, shopify_theme_id, name, created_by, created_at FROM theme_previews WHERE shopify_theme_id = %s", (str(pid),)).fetchone()


def _row_json(r, live_id=None) -> dict:
    return {"id": r["shopify_theme_id"], "name": r["name"], "created_by": r["created_by"], "created_at": r["created_at"].isoformat(), "preview_url": st.preview_url(r["shopify_theme_id"])}


def _audit(w, preview_pk, action, key=None, sha=None, size=None):
    w.execute("INSERT INTO theme_edit_audit (theme_preview_id, user_login, action, file_key, content_sha256, size_bytes) VALUES (%s, %s, %s, %s, %s, %s)",
              (preview_pk, auth.current_user(), action, key, sha, size))


def _require_writable(pid: str):
    """The safety rules, run on EVERY write: our own table first, then Shopify's current role for that theme (never trusted from a cache)."""
    with _rw() as w:
        row = _preview_row(w, pid)
    if not row:
        raise q.ApiError(409, NOT_OURS_MSG)
    theme = _shopify_call(st.client().get_theme, pid)
    if not theme:
        raise q.ApiError(404, "Shopify does not have that theme any more.")
    if theme["role"] == st.MAIN:
        raise q.ApiError(409, LIVE_MSG)
    return row


@bp.get("/api/theme/status")
def theme_status():
    """Theme previews discovery: {enabled, can_edit} for the signed-in person. 404 while the feature is off."""
    return jsonify(discovery())


@bp.get("/api/theme/themes")
def themes_list():
    """Our previews (newest first) and the live theme's name, id and role (read only)."""
    with _rw() as w:
        rows = w.execute("SELECT id, shopify_theme_id, name, created_by, created_at FROM theme_previews ORDER BY id DESC LIMIT 200").fetchall()
    live = _shopify_call(st.client().get_live_theme)
    return jsonify({"previews": [_row_json(r) for r in rows], "live": {"id": live["id"], "name": live["name"], "role": live["role"]}})


@bp.post("/api/theme/previews")
def preview_create():
    """Make a preview: {name} copies the live theme into a new UNPUBLISHED theme named preview-<login>-<yyyymmdd>-<slug>, records it, returns {id, name, preview_url}."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    name = _body().get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise q.ApiError(400, "Give the preview a short name.")
    login = re.sub(r"[^a-z0-9]+", "", (auth.current_user() or "").lower()) or "user"
    theme_name = f"preview-{login}-{dt.date.today():%Y%m%d}-{slugify(name)}"
    c = st.client()
    live = _shopify_call(c.get_live_theme)
    new = _shopify_call(c.duplicate_theme, live["id"], theme_name)
    if new["role"] == st.MAIN:                                         # cannot happen; refuse to record a live theme as ours
        raise q.ApiError(409, LIVE_MSG)
    with _rw() as w:
        pk = w.execute("INSERT INTO theme_previews (shopify_theme_id, name, created_by, source_theme_id) VALUES (%s, %s, %s, %s) RETURNING id",
                       (new["id"], theme_name, auth.current_user(), live["id"])).fetchone()["id"]
        _audit(w, pk, "create_preview")
        row = _preview_row(w, new["id"])
    return jsonify(_row_json(row)), 201


@bp.get("/api/theme/previews/<pid>/files")
def files_get(pid: str):
    """Files of one of our preview themes: no key lists the editable file paths; ?key=<path> returns that file's text."""
    with _rw() as w:
        if not _preview_row(w, pid):
            raise q.ApiError(404, "Preview not found.")
    c = st.client()
    key = request.args.get("key")
    if key is None:
        files = [f for f in _shopify_call(c.list_files, pid) if f["key"].startswith(ALLOWED_DIRS) and f["key"].lower().endswith(ALLOWED_EXT)]
        return jsonify({"files": sorted(files, key=lambda f: f["key"])})
    check_key(key)
    text = _shopify_call(c.read_file, pid, key)
    if text is None:
        raise q.ApiError(404, "File not found.")
    return jsonify({"key": key, "content": text})


@bp.put("/api/theme/previews/<pid>/files")
def files_put(pid: str):
    """Save one text file ({key, content}, at most 1 MB, allowed folders and types only) to one of our preview themes. Refuses the live theme and any theme not made here; writes an audit row."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    request.max_content_length = MAX_BYTES + 64 * 1024
    body = _body()
    key = check_key(body.get("key"))
    content = body.get("content")
    if not isinstance(content, str):
        raise q.ApiError(400, "content must be text")
    data = content.encode()
    if len(data) > MAX_BYTES:
        raise q.ApiError(400, "That file is over 1 MB.")
    row = _require_writable(pid)
    _shopify_call(st.client().upsert_file, pid, key, content)
    with _rw() as w:
        _audit(w, row["id"], "save_file", key, hashlib.sha256(data).hexdigest(), len(data))
    return jsonify({"ok": True, "key": key, "size": len(data)})
