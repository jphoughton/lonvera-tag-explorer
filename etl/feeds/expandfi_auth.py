"""ExpandFi server-side access: OAuth login (one interactive step), unattended refresh, encrypted token storage, a minimal MCP-over-HTTP client and a spike.

    python -m etl.feeds.expandfi_auth login      JOSH ONLY, once: opens the browser, he logs in to ExpandFi and approves; the refresh token is stored encrypted
    python -m etl.feeds.expandfi_auth refresh    refresh the access token from the stored refresh token (what the unattended job does); prints expiry only
    python -m etl.feeds.expandfi_auth spike      refresh -> one MCP call -> the database host/port the MCP reports -> TCP connect -> SELECT 1 (read-only)
    python -m etl.feeds.expandfi_auth tcp HOST PORT   plain TCP connect test, no login needed (run it where the job will run, e.g. as a Railway one-off)

ExpandFi's OAuth metadata (https://app.expandfi.com/.well-known/oauth-authorization-server): authorization_code + refresh_token, PKCE S256, public client
(token_endpoint_auth_method none), dynamic client registration, resource indicators, scopes mcp_full mcp_read mcp_write mcp_db. The Postgres credentials the
warehouse accepts are minted per call by the MCP tool `readonly_db_connection` (30-minute lifetime); the job mints one per run, never stores it.

Storage: table feed_oauth, refresh token encrypted with Fernet (cryptography) using env FEEDS_ENC_KEY. `login` creates the key if it is missing and sets it on
the Railway `etl` service through the Railway CLI over stdin. Nothing secret is printed, logged or written to a file; errors go through scrub().
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .common import AuthError, Ctx, FeedError, log, make_ctx, scrub

BASE = "https://app.expandfi.com"
MCP_URL = BASE + "/mcp"
METADATA_URL = BASE + "/.well-known/oauth-authorization-server"
SCOPES = "mcp_read mcp_db"
PROVIDER = "expandfi"
MERCHANT_ID = 1452
CLIENT_NAME = "Lonvera feeds (server-side, read-only)"
PROTOCOL_VERSION = "2025-06-18"
EXPIRY_MARGIN_S = 120

DDL = """CREATE TABLE IF NOT EXISTS feed_oauth (
  provider text PRIMARY KEY, client_id text NOT NULL, enc_refresh_token bytea NOT NULL, scopes text,
  access_expires_at timestamptz, rotated_at timestamptz NOT NULL DEFAULT now(),
  last_error text CHECK (last_error IS NULL OR length(last_error) <= 300))"""


# ---------------------------------------------------------------------------------------------------------------- encryption

def fernet(key: str):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise FeedError("the cryptography package is not installed (see docs/projection/REQUIREMENTS_REQUESTS.md)") from None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise FeedError("FEEDS_ENC_KEY is not a valid Fernet key (32 url-safe base64 bytes)") from None


def new_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def encrypt(key: str, token: str) -> bytes:
    return fernet(key).encrypt(token.encode())


def decrypt(key: str, blob: bytes) -> str:
    from cryptography.fernet import InvalidToken
    try:
        return fernet(key).decrypt(bytes(blob)).decode()
    except InvalidToken:
        raise FeedError("the stored ExpandFi token cannot be decrypted with FEEDS_ENC_KEY (key changed? run login again)") from None


# ---------------------------------------------------------------------------------------------------------------- OAuth

@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str | None


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _sess(ctx: Ctx):
    if ctx.session is None:
        import requests
        ctx.session = requests.Session()
    return ctx.session


def _json(resp, what: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise FeedError(f"expandfi {what}: response was not JSON (HTTP {resp.status_code})") from None
    return body


def discover(ctx: Ctx) -> dict:
    r = _sess(ctx).request("GET", METADATA_URL, timeout=30)
    if r.status_code != 200:
        raise FeedError(f"expandfi: OAuth metadata unavailable (HTTP {r.status_code})")
    meta = _json(r, "metadata")
    for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if k not in meta:
            raise FeedError(f"expandfi: OAuth metadata has no {k}")
    return meta


def register_client(ctx: Ctx, meta: dict, redirect_uri: str) -> str:
    r = _sess(ctx).request("POST", meta["registration_endpoint"], json={
        "client_name": CLIENT_NAME, "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPES}, timeout=30)
    if r.status_code not in (200, 201):
        raise FeedError(scrub(f"expandfi: client registration failed (HTTP {r.status_code}) {str(getattr(r, 'text', ''))[:120]}"))
    cid = _json(r, "registration").get("client_id")
    if not cid:
        raise FeedError("expandfi: registration returned no client_id")
    return cid


def _tokens(resp, what: str, now: datetime) -> Tokens:
    if resp.status_code != 200:
        body = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        code = body.get("error") or f"HTTP {resp.status_code}"
        if resp.status_code in (400, 401) or code in ("invalid_grant", "invalid_client"):
            raise AuthError(scrub(f"expandfi {what}: rejected ({code}); run `python -m etl.feeds.expandfi_auth login` again"))
        raise FeedError(scrub(f"expandfi {what}: {code}"))
    b = _json(resp, what)
    if not b.get("access_token"):
        raise FeedError(f"expandfi {what}: no access_token in the response")
    return Tokens(b["access_token"], b.get("refresh_token"), now + timedelta(seconds=int(b.get("expires_in") or 3600)), b.get("scope"))


def exchange_code(ctx: Ctx, meta: dict, client_id: str, code: str, verifier: str, redirect_uri: str) -> Tokens:
    r = _sess(ctx).request("POST", meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": client_id, "code_verifier": verifier, "resource": MCP_URL}, timeout=30)
    return _tokens(r, "code exchange", ctx.now_utc)


def refresh_grant(ctx: Ctx, meta: dict, client_id: str, refresh_token: str) -> Tokens:
    r = _sess(ctx).request("POST", meta["token_endpoint"], data={
        "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id, "resource": MCP_URL}, timeout=30)
    return _tokens(r, "token refresh", ctx.now_utc)


# ---------------------------------------------------------------------------------------------------------------- storage

def store_tokens(conn, key: str, client_id: str, t: Tokens, scopes: str | None) -> None:
    conn.execute(DDL)
    conn.execute(
        "INSERT INTO feed_oauth (provider, client_id, enc_refresh_token, scopes, access_expires_at, rotated_at, last_error) VALUES (%s, %s, %s, %s, %s, now(), NULL) "
        "ON CONFLICT (provider) DO UPDATE SET client_id = excluded.client_id, enc_refresh_token = excluded.enc_refresh_token, scopes = excluded.scopes, "
        "access_expires_at = excluded.access_expires_at, rotated_at = now(), last_error = NULL",
        (PROVIDER, client_id, encrypt(key, t.refresh_token or ""), scopes or t.scope, t.expires_at))
    conn.commit()


def load_row(conn) -> tuple[str, bytes] | None:
    r = conn.execute("SELECT client_id, enc_refresh_token FROM feed_oauth WHERE provider = %s", (PROVIDER,)).fetchone()
    return (r[0], bytes(r[1])) if r else None


def access_token(conn, ctx: Ctx, key: str | None = None, meta: dict | None = None) -> Tokens:
    """The unattended path: stored refresh token -> a fresh access token. A rotated refresh token is stored (and committed) before anything else
    happens, so a crash after rotation cannot lose it. A failure is recorded in feed_oauth.last_error (scrubbed) and raised as AuthError/FeedError."""
    key = key or ctx.require("FEEDS_ENC_KEY")
    row = load_row(conn)
    if not row:
        raise AuthError("expandfi: no stored login (run `python -m etl.feeds.expandfi_auth login`)")
    client_id, blob = row
    refresh_token = decrypt(key, blob)
    try:
        t = refresh_grant(ctx, meta or discover(ctx), client_id, refresh_token)
    except FeedError as e:
        conn.execute("UPDATE feed_oauth SET last_error = %s WHERE provider = %s", (scrub(e)[:300], PROVIDER))
        conn.commit()
        raise
    if t.refresh_token and t.refresh_token != refresh_token:
        store_tokens(conn, key, client_id, t, t.scope)
        log("expandfi: refresh token rotated and stored")
    else:
        conn.execute("UPDATE feed_oauth SET access_expires_at = %s, last_error = NULL WHERE provider = %s", (t.expires_at, PROVIDER))
        conn.commit()
    return t


# ---------------------------------------------------------------------------------------------------------------- MCP over HTTP

class McpClient:
    """Minimal streamable-HTTP MCP client: initialize, tools/call. Responses may be JSON or a server-sent-event stream. The bearer token never leaves
    the Authorization header and no header or body is logged."""

    def __init__(self, ctx: Ctx, token: str, url: str = MCP_URL):
        self.ctx, self._token, self.url, self.session_id, self.version, self._id = ctx, token, url, None, PROTOCOL_VERSION, 0

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": self.version}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    @staticmethod
    def parse_body(text: str, content_type: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None
        if "text/event-stream" in (content_type or ""):
            msgs = []
            for block in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(l[5:].lstrip() for l in block.splitlines() if l.startswith("data:"))
                if data:
                    try:
                        msgs.append(json.loads(data))
                    except ValueError:
                        continue
            return next((m for m in reversed(msgs) if "result" in m or "error" in m), msgs[-1] if msgs else None)
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _post(self, payload: dict, expect: bool = True, tries: int = 3) -> dict | None:
        last = ""
        for attempt in range(1, tries + 1):
            r = _sess(self.ctx).request("POST", self.url, headers=self._headers(), json=payload, timeout=120)
            if r.status_code in (401, 403):
                raise AuthError(f"expandfi mcp: access token rejected (HTTP {r.status_code})")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries:
                last = f"HTTP {r.status_code}"
                self.ctx.sleep(2 * attempt)
                continue
            if r.status_code not in (200, 202):
                raise FeedError(scrub(f"expandfi mcp: HTTP {r.status_code} {str(getattr(r, 'text', ''))[:120]}"))
            sid = (getattr(r, "headers", None) or {}).get("Mcp-Session-Id") or (getattr(r, "headers", None) or {}).get("mcp-session-id")
            if sid:
                self.session_id = sid
            if not expect or r.status_code == 202:
                return None
            body = self.parse_body(r.text, (r.headers or {}).get("Content-Type", ""))
            if body is None:
                raise FeedError("expandfi mcp: empty or unreadable response")
            return body
        raise FeedError(f"expandfi mcp: gave up ({last})")

    def initialize(self) -> dict:
        self._id += 1
        body = self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "lonvera-feeds", "version": "1"}}})
        if body and "error" in body:
            raise FeedError(scrub(f"expandfi mcp initialize: {body['error'].get('message', 'error')}"))
        self.version = ((body or {}).get("result") or {}).get("protocolVersion") or self.version
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
        return (body or {}).get("result") or {}

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """-> the tool result: parsed JSON when the text content is JSON, else the text."""
        self._id += 1
        body = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
        if body and "error" in body:
            raise FeedError(scrub(f"expandfi tool {name}: {body['error'].get('message', 'error')}"))
        res = (body or {}).get("result") or {}
        text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        if res.get("isError"):
            raise FeedError(scrub(f"expandfi tool {name}: {text[:160]}"))
        try:
            return json.loads(text)
        except ValueError:
            return text

    def list_tools(self) -> list[str]:
        self._id += 1
        body = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": {}})
        return [t.get("name", "") for t in ((body or {}).get("result") or {}).get("tools") or []]


DSN_RX = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.I)


def find_dsn(result: Any) -> str:
    """The Postgres connection string inside a tool result (any key), or raise."""
    if isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, str) and DSN_RX.fullmatch(v.strip()):
                return v.strip()
        for v in result.values():
            try:
                return find_dsn(v)
            except FeedError:
                continue
    elif isinstance(result, (list, tuple)):
        for v in result:
            try:
                return find_dsn(v)
            except FeedError:
                continue
    elif isinstance(result, str):
        m = DSN_RX.search(result)
        if m:
            return m.group(0)
    raise FeedError("expandfi: the tool result holds no Postgres connection string")


def dsn_host_port(dsn: str) -> tuple[str, int]:
    u = urllib.parse.urlparse(dsn)
    if not u.hostname:
        raise FeedError("expandfi: the connection string has no host")
    return u.hostname, u.port or 5432


def mint_dsn(mcp: McpClient, merchant_id: int = MERCHANT_ID) -> str:
    """The same sequence the desktop runbooks use: freshness gate calls, then readonly_db_connection. The credential is returned, never logged."""
    for gate in ("get_docs_version", "get_skills_version"):
        try:
            mcp.call_tool(gate, {})
        except FeedError:
            pass                                     # advisory preconditions; the tool call below is what matters
    try:
        res = mcp.call_tool("readonly_db_connection", {"merchant_id": merchant_id})
    except FeedError:
        res = mcp.call_tool("readonly_db_connection", {"merchant_id": str(merchant_id)})
    return find_dsn(res)


def tcp_check(host: str, port: int, timeout: float = 10.0) -> tuple[bool, str, float]:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "connected", (time.monotonic() - t0) * 1000
    except OSError as e:
        return False, f"{type(e).__name__}", (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------------------------------------------- CLI

class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):                                                    # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        type(self).result = {k: v[0] for k, v in q.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ExpandFi login received. You can close this tab and return to the terminal.")

    def log_message(self, *a):                                           # noqa: D401
        return


def railway_dir() -> str:
    """The directory linked to the Railway project (the CLI needs it): env FEEDS_RAILWAY_DIR, else ~/lonvera-tag-explorer."""
    return os.environ.get("FEEDS_RAILWAY_DIR") or os.path.expanduser("~/lonvera-tag-explorer")


def _railway_names_stdin(name: str, value: str) -> None:
    subprocess.run(["railway", "variable", "set", "-s", "etl", "--stdin", name, "--skip-deploys"], input=value.encode(), check=True, cwd=railway_dir(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _railway_value(name: str) -> str | None:
    try:
        out = subprocess.run(["railway", "variable", "list", "-s", "etl", "--json"], capture_output=True, text=True, check=True, cwd=railway_dir()).stdout
        return json.loads(out).get(name)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _db_url() -> str:
    return os.environ.get("DATABASE_URL") or _railway_value("DATABASE_PUBLIC_URL") or sys.exit("no database URL (set DATABASE_URL or link the Railway project)")


def _enc_key(create: bool) -> str:
    key = os.environ.get("FEEDS_ENC_KEY") or _railway_value("FEEDS_ENC_KEY")
    if key:
        return key
    if not create:
        sys.exit("FEEDS_ENC_KEY is not set (run `login` first)")
    key = new_key()
    _railway_names_stdin("FEEDS_ENC_KEY", key)
    print("FEEDS_ENC_KEY created and set on the Railway etl service (value not shown)")
    return key


def login() -> int:
    import psycopg
    ctx = make_ctx()
    meta = discover(ctx)
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback"
    client_id = register_client(ctx, meta, redirect)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP_URL})
    _Callback.result = {}
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    print("Opening ExpandFi in your browser. Log in as the ExpandFi account owner and approve access (read-only scopes).")
    if not webbrowser.open(url):
        print("Could not open a browser; open this address yourself:\n" + url)
    th.join(timeout=300)
    res = _Callback.result
    srv.server_close()
    if not res:
        sys.exit("no answer from the browser within 5 minutes")
    if res.get("state") != state:
        sys.exit("the login answer did not match this request (state mismatch); nothing stored")
    if "error" in res:
        sys.exit(f"ExpandFi refused the login: {res.get('error')}")
    t = exchange_code(ctx, meta, client_id, res["code"], verifier, redirect)
    if not t.refresh_token:
        sys.exit("ExpandFi returned no refresh token (scope offline access missing?); nothing stored")
    key = _enc_key(create=True)
    with psycopg.connect(_db_url()) as conn:
        store_tokens(conn, key, client_id, t, t.scope)
    print(f"stored: encrypted refresh token saved; access token valid for about {int((t.expires_at - ctx.now_utc).total_seconds() // 60)} minutes; scopes: {t.scope}")
    return 0


def refresh_cmd() -> int:
    import psycopg
    ctx = make_ctx(env={**os.environ, "FEEDS_ENC_KEY": _enc_key(create=False)})
    with psycopg.connect(_db_url()) as conn:
        t = access_token(conn, ctx)
    print(f"refresh ok: access token valid for about {int((t.expires_at - datetime.now(timezone.utc)).total_seconds() // 60)} minutes; "
          f"refresh token {'rotated' if t.refresh_token else 'unchanged'}")
    return 0


def spike() -> int:
    import psycopg
    ctx = make_ctx(env={**os.environ, "FEEDS_ENC_KEY": _enc_key(create=False)})
    with psycopg.connect(_db_url()) as conn:
        t = access_token(conn, ctx)
    print("1. token refresh: ok")
    mcp = McpClient(ctx, t.access_token)
    info = mcp.initialize()
    names = mcp.list_tools()
    print(f"2. MCP initialize ok (server {((info.get('serverInfo') or {}).get('name') or '?')}); {len(names)} tools; readonly_db_connection present: {'readonly_db_connection' in names}")
    dsn = mint_dsn(mcp)
    host, port = dsn_host_port(dsn)
    print(f"3. database credential minted (not shown); host {host} port {port}")
    ok, why, ms = tcp_check(host, port)
    print(f"4. TCP connect from this machine: {'OK' if ok else 'FAILED'} ({why}, {ms:.0f} ms). Repeat from Railway with `tcp {host} {port}` in a one-off run.")
    if not ok:
        return 1
    try:
        with psycopg.connect(dsn, connect_timeout=15) as pg:
            pg.execute("SET default_transaction_read_only = on")
            pg.execute("SELECT 1").fetchone()
        print("5. SELECT 1 over the minted credential: ok (read-only session)")
    except Exception as e:                                              # noqa: BLE001
        print("5. database login FAILED:", scrub(f"{type(e).__name__}: {e}", 200))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etl.feeds.expandfi_auth", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["login", "refresh", "spike", "tcp"])
    ap.add_argument("host", nargs="?")
    ap.add_argument("port", nargs="?", type=int)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "login":
            return login()
        if a.cmd == "refresh":
            return refresh_cmd()
        if a.cmd == "spike":
            return spike()
        if not (a.host and a.port):
            sys.exit("usage: tcp HOST PORT")
        ok, why, ms = tcp_check(a.host, a.port)
        print(f"{a.host}:{a.port} {'OK' if ok else 'FAILED'} ({why}, {ms:.0f} ms)")
        return 0 if ok else 1
    except FeedError as e:
        print(f"[expandfi] {scrub(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
