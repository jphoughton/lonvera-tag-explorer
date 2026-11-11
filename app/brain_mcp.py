"""The brain as a hosted MCP endpoint: POST /mcp (stateless streamable HTTP, JSON-RPC 2.0, JSON answers, no sessions, no SSE) plus the "Connect an agent" key routes.

Who may call /mcp (the app's own auth, nothing new):
  * a signed-in app session (cookie), or
  * `Authorization: Bearer <key>` with a brain key (table brain_keys, made on /connect-agent) or an API key (table api_keys).
Everything else is 401. When AUTH_DISABLED is on /mcp is refused with 403 (there is no identity to audit). Vendor link tokens grant nothing: /mcp is not in auth.PUBLIC_API_RE and
nothing here reads a vendor token. A bad Bearer token never falls back to the cookie.

Tool logic lives in the brain package. SQL runs through brain.rundb with ANALYTICS_READER_URL (the analytics_reader role) only; this module never runs user SQL on the app's own connections.
Every tools/call writes one row to brain_audit (actor, tool, duration, row count; never the question, the SQL or the result).
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from brain import rundb
from brain import tools as T

from . import api_keys, auth, db

log = logging.getLogger("tagexp")
bp = Blueprint("brain_mcp", __name__)

SERVER_NAME = "lonvera-brain"
SERVER_VERSION = "1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
KEY_PREFIX = "lvb_"
KEY_RE = re.compile(r"lvb_[A-Za-z0-9_-]{40}")
TOUCH_EVERY_SEC = 60
RATE_PER_MIN = 120
MAX_BATCH = 20
INSTRUCTIONS = ("Lonvera data brain. For any data question: call catalog first, then search or metric, then describe, then sql. Name the basis of every metric (Meta-reported, Upstack "
                "new customers, Upstack all orders). Spend adds across platforms; purchases and revenue never do. A cause needs a like-for-like control, and counts are not rates. "
                "Call past_questions before re-answering a 'why did X move' question.")


# ------------------------------------------------------------------------------------------------------------------------------ keys
def _generate() -> str:
    import secrets
    return KEY_PREFIX + secrets.token_urlsafe(48)[:40]


def _lookup(conn, raw: str) -> dict | None:
    """The brain key row {id, username} for a raw key, or None (unknown or malformed). Found by equality on the sha256 hash (an index lookup)."""
    if not KEY_RE.fullmatch(raw or ""):
        return None
    row = conn.execute("SELECT id, username, (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s)) AS stale FROM brain_keys WHERE key_hash = %s",
                       (TOUCH_EVERY_SEC, api_keys.hash_key(raw))).fetchone()
    if row is None:
        return None
    row = row if isinstance(row, dict) else dict(zip(("id", "username", "stale"), row))
    if row["stale"]:
        try:
            from .projection import rwdb
            with rwdb.get_rw() as w:
                w.execute("UPDATE brain_keys SET last_used_at = now() WHERE id = %s", (row["id"],))
        except Exception:                                        # noqa: BLE001 - bookkeeping must never block a request
            pass
    return {"id": row["id"], "username": row["username"]}


def _identify():
    """(actor, None) for an allowed caller, or (None, flask response) for a refusal."""
    if auth.auth_disabled():
        return None, (jsonify(error="The brain endpoint is off while the app is open (AUTH_DISABLED). Sign-in is required."), 403)
    token = auth._bearer_token()
    if token is not None:
        throttle = current_app.extensions["apikey_throttle_ip"]
        ip = auth._client_ip()
        wait = throttle.retry_after(ip)
        if wait:
            return None, auth._too_many(wait)
        import psycopg
        conn = db.get_conn()
        try:
            bk = _lookup(conn, token)
            ak = None if bk else api_keys.lookup(conn, token)
        except psycopg.errors.UndefinedTable:                    # migrations not applied: no key can be valid
            conn.rollback()
            bk = ak = None
        if bk:
            return f"brain:{bk['username']}", None
        if ak:
            return f"key:{ak['name']}", None
        throttle.fail(ip)
        return None, (jsonify(error="unauthorized"), 401)
    user = auth.current_user()
    if not user:
        return None, (jsonify(error="unauthorized"), 401)
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
        return None, (jsonify(error="origin mismatch"), 403)       # a browser page on another site cannot use a signed-in session here
    return user, None


def _limiter():
    lim = current_app.extensions.get("brain_throttle")
    if lim is None:
        lim = current_app.extensions["brain_throttle"] = auth.LoginThrottle(int(current_app.config.get("BRAIN_RATE_PER_MIN", RATE_PER_MIN)), 60)
    return lim


# ------------------------------------------------------------------------------------------------------------------------------ audit
def _audit(actor: str, tool: str, ms: int, rows: int | None, ok: bool) -> None:
    try:
        from .projection import rwdb
        with rwdb.get_rw() as w:
            w.execute("INSERT INTO brain_audit (actor, tool, duration_ms, row_count, ok) VALUES (%s, %s, %s, %s, %s)", (actor[:80], tool[:40], int(ms), rows, ok))
    except Exception:                                            # noqa: BLE001
        log.warning("brain_audit write failed for tool %s", tool)


# ------------------------------------------------------------------------------------------------------------------------------ JSON-RPC
def _err(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _brain() -> T.Brain:
    return T.Brain(allow_file_url=False)                         # hosted: ANALYTICS_READER_URL only, never a file


def _handle(msg, actor: str):
    """One JSON-RPC message -> its response dict, or None for a notification / a response."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, -32600, "invalid request")
    method, id_ = msg.get("method"), msg.get("id")
    if method is None:                                           # a response from the client: nothing to do
        return None
    if not isinstance(method, str):
        return _err(id_, -32600, "invalid request")
    if "id" not in msg:                                          # a notification (notifications/initialized, ...)
        return None
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        want = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": id_, "result": {"protocolVersion": want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                                                       "capabilities": {"tools": {"listChanged": False}},
                                                       "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema} for t in T.TOOLS]}}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in T.BY_NAME:
            return _err(id_, -32602, f"unknown tool {name!r}")
        t0 = time.monotonic()
        rows, ok = None, True
        try:
            result = T.call(_brain(), name, params.get("arguments"))
            rows = T.row_count(result)
            text = T.dumps(result)
        except T.ToolError as e:
            ok, text = False, str(e)
        except Exception:                                        # noqa: BLE001
            log.exception("brain tool %s failed", name)
            ok, text = False, "internal error"
        _audit(actor, name, int((time.monotonic() - t0) * 1000), rows, ok)
        return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}], "isError": not ok}}
    return _err(id_, -32601, f"method not found: {method}")


@bp.route("/mcp", methods=["POST", "GET", "DELETE"])
def mcp():
    """The brain's MCP endpoint (stateless streamable HTTP, JSON-RPC over POST: initialize, ping, tools/list, tools/call)."""
    if request.method != "POST":                                 # stateless: no SSE stream to open, no session to close
        return jsonify(error="This endpoint accepts POST only (stateless streamable HTTP)."), 405, {"Allow": "POST"}
    actor, refusal = _identify()
    if refusal is not None:
        return refusal
    lim = _limiter()
    wait = lim.retry_after(actor)
    if wait:
        return auth._too_many(wait)
    lim.fail(actor)                                              # LoginThrottle counts events: here, requests per minute
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(_err(None, -32700, "parse error: send a JSON-RPC 2.0 object as application/json")), 400
    if isinstance(body, list):
        if not body or len(body) > MAX_BATCH:
            return jsonify(_err(None, -32600, f"a batch holds 1 to {MAX_BATCH} messages")), 400
        out = [r for r in (_handle(m, actor) for m in body) if r is not None]
        return (jsonify(out), 200) if out else ("", 202)
    out = _handle(body, actor)
    return (jsonify(out), 200) if out is not None else ("", 202)


# ------------------------------------------------------------------------------------------------------------------------------ Connect an agent
def _endpoint() -> str:
    return request.url_root.rstrip("/") + "/mcp"


def _own_session_only():
    """Keys are made by a person with a login: not by an API key, not while the app is open."""
    if auth.auth_disabled():
        return jsonify(error="Sign-in is off (AUTH_DISABLED), so there is no account to attach a key to."), 403
    if auth.is_key_request():
        return jsonify(error="An API key cannot make agent keys. Sign in."), 403
    return None


@bp.get("/api/brain/key")
def brain_key_status():
    """Whether the signed-in user has an agent key (prefix and last use only, never the key) and the endpoint address."""
    refused = _own_session_only()
    if refused:
        return refused
    user = auth.current_user()
    row = db.get_conn().execute("SELECT prefix, created_at, last_used_at FROM brain_keys WHERE username = %s", (user,)).fetchone()
    row = (row if isinstance(row, dict) else dict(zip(("prefix", "created_at", "last_used_at"), row))) if row else None
    return jsonify(user=user, endpoint=_endpoint(), has_key=row is not None, prefix=(row or {}).get("prefix"), created_at=(row or {}).get("created_at"), last_used_at=(row or {}).get("last_used_at"),
                   sql_configured=rundb.configured(False))


@bp.post("/api/brain/key")
def brain_key_reset():
    """Make this user's agent key, or replace it (the old key stops working at once). The key is in this answer once and is stored only as a hash."""
    refused = _own_session_only()
    if refused:
        return refused
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
        return jsonify(error="origin mismatch"), 403
    lim = current_app.extensions.get("brain_key_throttle")
    if lim is None:
        lim = current_app.extensions["brain_key_throttle"] = auth.LoginThrottle(10, 60)
    ip = auth._client_ip()
    wait = lim.retry_after(ip)
    if wait:
        return auth._too_many(wait)
    lim.fail(ip)
    from .projection import rwdb
    user = auth.current_user()
    raw = _generate()
    with rwdb.get_rw() as w:
        w.execute("INSERT INTO brain_keys (username, key_hash, prefix) VALUES (%s, %s, %s) "
                  "ON CONFLICT (username) DO UPDATE SET key_hash = EXCLUDED.key_hash, prefix = EXCLUDED.prefix, created_at = now(), last_used_at = NULL",
                  (user, api_keys.hash_key(raw), raw[:8]))
    resp = jsonify(user=user, key=raw, prefix=raw[:8], endpoint=_endpoint())
    resp.headers["Cache-Control"] = "no-store"
    return resp
