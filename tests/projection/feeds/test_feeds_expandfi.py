"""ExpandFi access: encryption, PKCE, OAuth grants, token rotation storage, MCP over HTTP (JSON and event-stream), credential minting, TCP check.
No network and no credential: a FakeSession plays ExpandFi; the login browser step is not exercised (Josh runs it)."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import socket
import threading

import pytest

from etl.feeds import expandfi_auth as X
from etl.feeds.common import AuthError, FeedError, make_ctx
from feeds_helpers import FakeResponse, FakeSession

NOW = dt.datetime(2026, 11, 3, 20, tzinfo=dt.timezone.utc)
META = {"authorization_endpoint": "https://app.expandfi.com/mcp/oauth/authorize", "token_endpoint": "https://app.expandfi.com/mcp/oauth/token",
        "registration_endpoint": "https://app.expandfi.com/mcp/oauth/register"}
DSN = "postgresql://ro_user_2026_11_0320_00_00:S3cretPassw0rd@db.example.internal:6543/tenant_1452"


def ctx(session, **env):
    return make_ctx(session=session, sleep=lambda s: None, now_utc=NOW, today=dt.date(2026, 11, 3), env=env)


def key():
    return X.new_key()


def test_encrypt_round_trip_and_wrong_key_is_a_clear_error():
    k, other = key(), key()
    blob = X.encrypt(k, "refresh-token-value")
    assert b"refresh-token-value" not in blob and X.decrypt(k, blob) == "refresh-token-value"
    with pytest.raises(FeedError, match="cannot be decrypted"):
        X.decrypt(other, blob)
    with pytest.raises(FeedError, match="valid Fernet key"):
        X.encrypt("not-a-key", "x")


def test_pkce_challenge_is_s256_of_the_verifier():
    v, c = X.pkce_pair()
    assert 43 <= len(v) <= 128 and c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")


def test_discovery_registration_and_code_exchange():
    meta_doc = dict(META, issuer="x")
    s = FakeSession()
    s.on(lambda c: c["url"].endswith("oauth-authorization-server"), FakeResponse(200, meta_doc))
    s.on(lambda c: c["url"].endswith("/register"), FakeResponse(201, {"client_id": "cid-1"}))
    s.on(lambda c: c["url"].endswith("/token"), FakeResponse(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "mcp_read mcp_db"}))
    c = ctx(s)
    meta = X.discover(c)
    assert X.register_client(c, meta, "http://127.0.0.1:5555/callback") == "cid-1"
    reg = next(x for x in s.calls if x["url"].endswith("/register"))["json"]
    assert reg["token_endpoint_auth_method"] == "none" and reg["redirect_uris"] == ["http://127.0.0.1:5555/callback"] and "authorization_code" in reg["grant_types"]
    t = X.exchange_code(c, meta, "cid-1", "CODE", "VERIFIER", "http://127.0.0.1:5555/callback")
    assert (t.access_token, t.refresh_token, t.expires_at) == ("AT", "RT", NOW + dt.timedelta(hours=1))
    d = next(x for x in s.calls if x["url"].endswith("/token"))["data"]
    assert d["grant_type"] == "authorization_code" and d["code_verifier"] == "VERIFIER" and d["resource"] == X.MCP_URL


def test_rejected_refresh_is_an_auth_error_that_says_what_to_do():
    s = FakeSession().on(lambda c: True, FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(AuthError, match="login"):
        X.refresh_grant(ctx(s), META, "cid", "RT")


def test_access_token_stores_a_rotated_refresh_token_and_records_failures_without_secrets(conn):
    k = key()
    X.store_tokens(conn, k, "cid", X.Tokens("AT0", "RT-old", NOW, "mcp_read mcp_db"), "mcp_read mcp_db")
    s = FakeSession().on(lambda c: c["url"].endswith("/token"), FakeResponse(200, {"access_token": "AT1", "refresh_token": "RT-new", "expires_in": 1800}))
    t = X.access_token(conn, ctx(s), key=k, meta=META)
    assert t.access_token == "AT1"
    row = conn.execute("SELECT client_id, enc_refresh_token FROM feed_oauth").fetchone()
    assert X.decrypt(k, bytes(row[1])) == "RT-new" and b"RT-new" not in bytes(row[1])
    sent = [c for c in s.calls if c["url"].endswith("/token")][0]["data"]
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "RT-old" and sent["client_id"] == "cid"
    # not rotated: the stored token stays, expiry moves
    s2 = FakeSession().on(lambda c: True, FakeResponse(200, {"access_token": "AT2", "expires_in": 900}))
    X.access_token(conn, ctx(s2), key=k, meta=META)
    assert X.decrypt(k, bytes(conn.execute("SELECT enc_refresh_token FROM feed_oauth").fetchone()[0])) == "RT-new"
    # rejected: recorded, scrubbed, raised
    s3 = FakeSession().on(lambda c: True, FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(AuthError):
        X.access_token(conn, ctx(s3), key=k, meta=META)
    err = conn.execute("SELECT last_error FROM feed_oauth").fetchone()[0]
    assert "invalid_grant" in err and "RT-new" not in err


def test_access_token_without_a_stored_login_says_to_run_login(conn):
    with pytest.raises(AuthError, match="login"):
        X.access_token(conn, ctx(FakeSession()), key=key(), meta=META)
    with pytest.raises(FeedError, match="FEEDS_ENC_KEY"):
        X.access_token(conn, ctx(FakeSession()), meta=META)


def sse(msg):
    return FakeResponse(200, text="event: message\ndata: " + json.dumps(msg) + "\n\n", headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "sess-1"})


def rpc(result=None, error=None, rid=1):
    return {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}


def test_mcp_initialize_and_tool_call_over_json_and_sse():
    s = FakeSession()
    s.on(lambda c: c["json"].get("method") == "initialize", sse(rpc({"protocolVersion": "2025-06-18", "serverInfo": {"name": "ExpandFi MCP"}})))
    s.on(lambda c: c["json"].get("method") == "notifications/initialized", FakeResponse(202))
    s.on(lambda c: c["json"].get("method") == "tools/call" and c["json"]["params"]["name"] == "echo",
         FakeResponse(200, rpc({"content": [{"type": "text", "text": json.dumps({"ok": True})}]}), headers={"Content-Type": "application/json"}))
    s.on(lambda c: c["json"].get("method") == "tools/call", sse(rpc({"content": [{"type": "text", "text": "plain words"}]})))
    m = X.McpClient(ctx(s), "AT-secret")
    assert m.initialize()["serverInfo"]["name"] == "ExpandFi MCP" and m.session_id == "sess-1"
    assert m.call_tool("echo") == {"ok": True} and m.call_tool("other") == "plain words"
    last = s.calls[-1]["headers"]
    assert last["Authorization"] == "Bearer AT-secret" and last["Mcp-Session-Id"] == "sess-1" and "text/event-stream" in last["Accept"]


def test_mcp_errors_are_scrubbed_and_auth_is_not_retried():
    s = FakeSession().on(lambda c: c["json"].get("method") == "tools/call", FakeResponse(200, rpc(error={"message": "merchant not allowed"}), headers={"Content-Type": "application/json"}))
    with pytest.raises(FeedError, match="merchant not allowed"):
        X.McpClient(ctx(s), "AT").call_tool("readonly_db_connection", {"merchant_id": 1})
    s401 = FakeSession().on(lambda c: True, FakeResponse(401))
    with pytest.raises(AuthError):
        X.McpClient(ctx(s401), "AT").call_tool("x")
    assert len(s401.calls) == 1
    iserr = FakeSession().on(lambda c: True, FakeResponse(200, rpc({"isError": True, "content": [{"type": "text", "text": "boom"}]}), headers={"Content-Type": "application/json"}))
    with pytest.raises(FeedError, match="boom"):
        X.McpClient(ctx(iserr), "AT").call_tool("x")


def test_find_dsn_in_any_shape_and_host_port():
    assert X.find_dsn({"connection_string": DSN, "expires_at": "x"}) == DSN
    assert X.find_dsn({"data": {"nested": [{"url": DSN}]}}) == DSN
    assert X.find_dsn(f"Use this: {DSN} (30 minutes)") == DSN
    with pytest.raises(FeedError):
        X.find_dsn({"message": "none here"})
    assert X.dsn_host_port(DSN) == ("db.example.internal", 6543)
    assert X.dsn_host_port("postgresql://u:p@h/db") == ("h", 5432)


def test_mint_dsn_runs_the_gate_calls_then_the_tool_and_falls_back_to_a_string_merchant_id():
    seen = []

    def call(c):
        p = c["json"]["params"]
        seen.append((p["name"], p["arguments"]))
        if p["name"] == "readonly_db_connection" and isinstance(p["arguments"]["merchant_id"], int):
            return FakeResponse(200, rpc(error={"message": "merchant_id must be a string"}), headers={"Content-Type": "application/json"})
        return FakeResponse(200, rpc({"content": [{"type": "text", "text": json.dumps({"connection_string": DSN})}]}), headers={"Content-Type": "application/json"})
    s = FakeSession().on(lambda c: c["json"].get("method") == "tools/call", call)
    assert X.mint_dsn(X.McpClient(ctx(s), "AT"), 1452) == DSN
    assert [n for n, _ in seen] == ["get_docs_version", "get_skills_version", "readonly_db_connection", "readonly_db_connection"]
    assert seen[-1][1] == {"merchant_id": "1452"}


def test_tcp_check_reports_open_and_closed_ports():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    def accept():
        try:
            srv.accept()
        except OSError:
            pass
    t = threading.Thread(target=accept, daemon=True); t.start()
    ok, why, ms = X.tcp_check("127.0.0.1", port, 3)
    assert ok and why == "connected"
    srv.close()
    ok, why, _ = X.tcp_check("127.0.0.1", port, 1)
    assert not ok


def test_cli_tcp_command(capsys):
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    assert X.main(["tcp", "127.0.0.1", str(port)]) == 0
    assert "OK" in capsys.readouterr().out
    srv.close()


def test_railway_helpers_run_in_the_linked_directory_and_pass_the_value_on_stdin(monkeypatch):
    seen = {}

    class R:
        stdout = json.dumps({"FEEDS_ENC_KEY": "k" * 44})

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return R()
    monkeypatch.setattr(X.subprocess, "run", fake_run)
    monkeypatch.setenv("FEEDS_RAILWAY_DIR", "/some/linked/dir")
    X._railway_names_stdin("FEEDS_ENC_KEY", "value-that-must-not-be-in-argv")
    assert seen["cwd"] == "/some/linked/dir" and seen["input"] == b"value-that-must-not-be-in-argv" and "value-that-must-not-be-in-argv" not in seen["cmd"]
    assert "--skip-deploys" in seen["cmd"] and "--stdin" in seen["cmd"]
    assert X._railway_value("FEEDS_ENC_KEY") == "k" * 44 and seen["cwd"] == "/some/linked/dir"
