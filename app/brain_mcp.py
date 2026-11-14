"""The brain as a hosted MCP endpoint: POST /mcp (stateless streamable HTTP, JSON-RPC 2.0, JSON answers, no sessions, no SSE) plus the "Connect an agent" key routes.

Who may call /mcp (the app's own auth, nothing new):
  * a signed-in app session (cookie), or
  * `Authorization: Bearer <key>` with a brain key (table brain_keys, made on /connect-agent) or an API key (table api_keys).
Everything else is 401. When AUTH_DISABLED is on (the app is open to anyone with the link) there are no sessions, so /mcp takes a Bearer key only and a keyless call is still 401: every call stays
attributable and rate limited. Anyone who can open the app can make a key then (created_by 'open-app', no user, a salted hash of the creator's IP, 50 keys a day app-wide).
Vendor link tokens grant nothing: /mcp is not in auth.PUBLIC_API_RE and nothing here reads a vendor token. A bad Bearer token never falls back to the cookie.

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
from brain.instructions import INSTRUCTIONS  # shared with the local stdio server
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
OPEN_KEYS_PER_DAY = 50
MAX_BATCH = 20


# ------------------------------------------------------------------------------------------------------------------------------ keys
def _generate() -> str:
    import secrets
    return KEY_PREFIX + secrets.token_urlsafe(48)[:40]


def _lookup(conn, raw: str) -> dict | None:
    """The brain key row {id, username} for a raw key, or None (unknown or malformed). Found by equality on the sha256 hash (an index lookup)."""
    if not KEY_RE.fullmatch(raw or ""):
        return None
    row = conn.execute("SELECT id, username, prefix, (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s)) AS stale FROM brain_keys WHERE key_hash = %s",
                       (TOUCH_EVERY_SEC, api_keys.hash_key(raw))).fetchone()
    if row is None:
        return None
    row = row if isinstance(row, dict) else dict(zip(("id", "username", "prefix", "stale"), row))
    if row["stale"]:
        try:
            from .projection import rwdb
            with rwdb.get_rw() as w:
                w.execute("UPDATE brain_keys SET last_used_at = now() WHERE id = %s", (row["id"],))
        except Exception:                                        # noqa: BLE001 - bookkeeping must never block a request
            pass
    return {"id": row["id"], "username": row["username"], "prefix": row["prefix"]}


def _identify():
    """(actor, None) for an allowed caller, or (None, flask response) for a refusal."""
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
            return (f"brain:{bk['username']}" if bk["username"] else f"brain:open-app:{bk['prefix']}"), None
        if ak and ak["scope"] == api_keys.PARTNER_SCOPE:
            return None, (jsonify(error=api_keys.PARTNER_MESSAGE), 403)       # a partner_push key never reaches the data brain (partner_read_all is a deliberate per-key choice and does)
        if ak:
            return f"key:{ak['name']}", None
        throttle.fail(ip)
        return None, (jsonify(error="unauthorized"), 401)
    if auth.auth_disabled():                                     # no sessions exist while the app is open: a key is the only way in
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


def _key_routes_refused():
    """Keys are made by a person: not by an API key. With sign-in on that person is signed in; with the app open (AUTH_DISABLED) it is anyone who can open the app."""
    if auth.is_key_request():
        return jsonify(error="An API key cannot make agent keys. Sign in."), 403
    return None


@bp.get("/api/brain/key")
def brain_key_status():
    """Whether the signed-in user has an agent key (prefix and last use only, never the key) and the endpoint address; with the app open, just the endpoint."""
    refused = _key_routes_refused()
    if refused:
        return refused
    if auth.auth_disabled():
        return jsonify(open_app=True, user=None, endpoint=_endpoint(), has_key=False, prefix=None, created_at=None, last_used_at=None, sql_configured=rundb.configured(False))
    user = auth.current_user()
    row = db.get_conn().execute("SELECT prefix, created_at, last_used_at FROM brain_keys WHERE username = %s", (user,)).fetchone()
    row = (row if isinstance(row, dict) else dict(zip(("prefix", "created_at", "last_used_at"), row))) if row else None
    return jsonify(open_app=False, user=user, endpoint=_endpoint(), has_key=row is not None, prefix=(row or {}).get("prefix"), created_at=(row or {}).get("created_at"), last_used_at=(row or {}).get("last_used_at"),
                   sql_configured=rundb.configured(False))


def _ip_hash() -> str:
    """Salted (HMAC under SECRET_KEY) hash of the client IP, 32 hex characters: enough to tell creators apart, useless to recover an address."""
    import hashlib
    import hmac
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, b"brain-open-app-ip:" + auth._client_ip().encode(), hashlib.sha256).hexdigest()[:32]


@bp.post("/api/brain/key")
def brain_key_reset():
    """Make this user's agent key, or replace it (the old key stops working at once). With the app open anyone can make a new key (capped at 50 a day app-wide). The key is in this answer once and is stored only as a hash."""
    refused = _key_routes_refused()
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
    raw = _generate()
    if auth.auth_disabled():
        cap = int(current_app.config.get("BRAIN_OPEN_KEYS_PER_DAY", OPEN_KEYS_PER_DAY))
        with rwdb.get_rw() as w:
            w.execute("LOCK TABLE brain_keys IN SHARE ROW EXCLUSIVE MODE")           # the count and the insert are one step: two makers cannot both take the last slot
            made = w.execute("SELECT count(*) AS n FROM brain_keys WHERE created_by = 'open-app' AND created_at > now() - interval '1 day'").fetchone()["n"]
            if made >= cap:
                resp = jsonify(error=f"Today's limit of {cap} new keys for the whole app has been reached. Try again tomorrow, or ask Josh.", retry_after=3600)
                resp.status_code = 429
                resp.headers["Retry-After"] = "3600"
                return resp
            w.execute("INSERT INTO brain_keys (username, key_hash, prefix, created_by, creator_hash) VALUES (NULL, %s, %s, 'open-app', %s)", (api_keys.hash_key(raw), raw[:8], _ip_hash()))
        resp = jsonify(user=None, open_app=True, key=raw, prefix=raw[:8], endpoint=_endpoint())
    else:
        user = auth.current_user()
        with rwdb.get_rw() as w:
            w.execute("INSERT INTO brain_keys (username, key_hash, prefix, created_by) VALUES (%s, %s, %s, 'signed-in') "
                      "ON CONFLICT (username) DO UPDATE SET key_hash = EXCLUDED.key_hash, prefix = EXCLUDED.prefix, created_at = now(), last_used_at = NULL",
                      (user, api_keys.hash_key(raw), raw[:8]))
        resp = jsonify(user=user, open_app=False, key=raw, prefix=raw[:8], endpoint=_endpoint())
    resp.headers["Cache-Control"] = "no-store"
    return resp
