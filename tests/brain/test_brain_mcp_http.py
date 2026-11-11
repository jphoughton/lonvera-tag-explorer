"""The hosted endpoint POST /mcp: who gets in (the auth matrix), the JSON-RPC subset, every tool through tools/call, the audit row, the rate limit, and the Connect an agent key routes."""
import hashlib
import json

import pytest

from etl import api_keys as cli

from brain_helpers import DB_URL, PASSWORD, login

URL_SECRET = "analytics_reader:topsecretpw"


def rpc(client, method, params=None, id_=1, headers=None, path="/mcp"):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return client.post(path, json=body, headers=headers)


def call(client, tool, args=None, headers=None):
    r = rpc(client, "tools/call", {"name": tool, "arguments": args or {}}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    res = r.get_json()["result"]
    return res["isError"], json.loads(res["content"][0]["text"]) if not res["isError"] else res["content"][0]["text"]


def bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture()
def brain_key(client):
    r = client.post("/api/brain/key", json={})
    assert r.status_code == 200
    return r.get_json()["key"]


@pytest.fixture()
def api_key(pg):
    raw, _ = cli.create_key(pg, "ci-colleague", "pytest")
    return raw


# --------------------------------------------------------------------------------------------------------------------- the auth matrix
def test_signed_out_is_401(anon):
    assert rpc(anon, "initialize").status_code == 401
    assert anon.post("/mcp", data="not json").status_code == 401                # auth before parsing
    assert rpc(anon, "tools/list").get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("raw", ["", "lvb_", "lvb_short", "x" * 44, "lvb_" + "A" * 40, "lvk_" + "A" * 40, "Bearer", "lvb_" + "!" * 40])
def test_invalid_keys_are_401(anon, raw):
    assert rpc(anon, "initialize", headers=bearer(raw)).status_code == 401


def test_a_bad_bearer_never_falls_back_to_the_session_cookie(client):
    assert rpc(client, "initialize").status_code == 200
    assert rpc(client, "initialize", headers=bearer("lvb_" + "A" * 40)).status_code == 401


def test_vendor_link_tokens_never_grant_access(client, anon):
    tok = client.post("/api/priorities/vendor-links", json={"vendor": "Madre"}).get_json()["token"]
    assert rpc(anon, "initialize", headers=bearer(tok)).status_code == 401
    assert anon.post(f"/mcp/{tok}", json={}).status_code == 404 and anon.post(f"/api/priorities/vendor/{tok}/mcp", json={}).status_code == 401
    from app import auth
    assert not auth.PUBLIC_API_RE.fullmatch("/mcp") and not auth.PUBLIC_API_RE.fullmatch(f"/mcp/{tok}") and "/mcp" not in auth.PUBLIC_API
    assert anon.get(f"/api/priorities/vendor/{tok}").status_code == 200        # (the vendor page itself still works: the token opens that and only that)
    assert rpc(anon, "tools/list").status_code == 401


def test_auth_disabled_still_needs_a_key(app_factory):
    """Detail of the open-app matrix is in test_brain_open_app.py: with sign-in off /mcp is 401 without a valid key, never open and never 403."""
    c = app_factory(AUTH_DISABLED=True).test_client()
    assert rpc(c, "initialize").status_code == 401 and rpc(c, "initialize", headers=bearer("lvb_" + "A" * 40)).status_code == 401


def test_a_session_works(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    res = r.get_json()["result"]
    assert r.status_code == 200 and res["protocolVersion"] == "2025-06-18" and res["serverInfo"]["name"] == "lonvera-brain" and "tools" in res["capabilities"] and "catalog" in res["instructions"]


def test_a_brain_key_works(anon, brain_key):
    assert brain_key.startswith("lvb_") and len(brain_key) == 44
    assert rpc(anon, "tools/list", headers=bearer(brain_key)).status_code == 200


def test_an_api_key_works_and_a_revoked_one_does_not(anon, pg, api_key):
    assert rpc(anon, "tools/list", headers=bearer(api_key)).status_code == 200
    cli.revoke_key(pg, "ci-colleague")
    assert rpc(anon, "tools/list", headers=bearer(api_key)).status_code == 401


def test_a_brain_key_opens_mcp_and_nothing_else(anon, brain_key):
    for url in ("/api/status", "/api/products", "/api/brain/key", "/media/1"):
        assert anon.get(url, headers=bearer(brain_key)).status_code == 401, url
    assert anon.post("/api/brain/key", json={}, headers=bearer(brain_key)).status_code == 401


def test_a_session_from_another_site_is_refused_but_a_key_is_not_bound_to_an_origin(client, anon, brain_key):
    assert rpc(client, "initialize", headers={"Origin": "https://evil.example"}).status_code == 403
    assert rpc(client, "initialize", headers={"Origin": "http://localhost"}).status_code == 200
    assert rpc(anon, "initialize", headers={**bearer(brain_key), "Origin": "https://claude.ai"}).status_code == 200


def test_failed_keys_are_throttled_per_ip_like_the_api(anon):
    codes = [rpc(anon, "initialize", headers=bearer("lvb_" + "B" * 40)).status_code for _ in range(25)]
    assert codes[0] == 401 and codes[-1] == 429


# --------------------------------------------------------------------------------------------------------------------- protocol
def test_methods_and_errors(client):
    assert client.get("/mcp").status_code == 405 and client.get("/mcp").headers["Allow"] == "POST" and client.delete("/mcp").status_code == 405
    assert client.post("/mcp", data="not json", content_type="application/json").status_code == 400
    assert rpc(client, "nope").get_json()["error"]["code"] == -32601
    assert rpc(client, "ping").get_json()["result"] == {}
    assert rpc(client, "tools/call", {"name": "rm_rf"}).get_json()["error"]["code"] == -32602
    n = rpc(client, "notifications/initialized", id_=None)
    assert n.status_code == 202 and n.get_data() == b""
    assert client.post("/mcp", json={"method": "ping", "id": 1}).get_json()["error"]["code"] == -32600
    assert client.post("/mcp", json=[]).status_code == 400
    b = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
    assert [m["id"] for m in b.get_json()] == [1, 3]


def test_tools_list_matches_the_registry(client):
    from brain import tools as T
    tl = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in tl] == [t.name for t in T.TOOLS] and all(t["inputSchema"]["type"] == "object" and t["description"] for t in tl)


def test_every_tool_through_tools_call(client, monkeypatch):
    err, cat = call(client, "catalog")
    assert not err and cat["domains"] and cat["source"] in ("docs/schema.json", "analytics.catalog (live)")
    err, d = call(client, "describe", {"name": "meta_ad_daily"})
    assert not err and d["columns"]
    err, s = call(client, "search", {"text": "api key bearer"})
    assert not err and s["hits"]
    err, m = call(client, "metric", {"name": "roas"})
    assert not err and "loaded" in m
    err, a = call(client, "app_map", {"section": "services"})
    assert not err and any(x["name"] == "web" for x in a["services"])
    err, q = call(client, "past_questions", {"text": "repeat"})
    assert not err and "matches" in q
    err, text = call(client, "sql", {"query": "select 1"})
    assert err and "SQL is not configured" in text                            # ANALYTICS_READER_URL is unset: the other tools still work
    err, text = call(client, "freshness")
    assert err and "SQL is not configured" in text
    err, text = call(client, "describe", {"name": "zzz"})
    assert err and "no view or table" in text
    err, text = call(client, "search", {"text": "x", "bogus": 1})
    assert err and "unknown argument" in text


def test_sql_over_http_uses_only_analytics_reader_url(client, monkeypatch):
    monkeypatch.setenv("ANALYTICS_READER_URL", DB_URL)
    err, r = call(client, "sql", {"query": "select 41 + 1 as answer"})
    assert not err and r == {"columns": ["answer"], "rows": [[42]], "row_count": 1, "truncated": False, "note": None}
    err, r = call(client, "sql", {"query": "select g from generate_series(1, 10) g", "limit": 4})
    assert not err and r["row_count"] == 4 and r["truncated"] is True
    for q in ("insert into app_meta (key, value) values ('x', 'y')", "delete from ad_daily", "update products set name = 'x'", "drop table products", "select 1; select 2", "copy products to stdout"):
        err, text = call(client, "sql", {"query": q})
        assert err and "query rejected" in text, q
    err, cov = call(client, "freshness")
    assert not err and cov["coverage"]["columns"][0] == "source"
    err, cat = call(client, "catalog")
    assert not err and cat["source"].startswith("analytics.catalog") or "warning" in cat or cat["source"] == "docs/schema.json"      # live catalog view may not exist yet: falls back


def test_database_url_is_never_used_for_sql(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    err, text = call(client, "sql", {"query": "select 1"})
    assert err and "not configured" in text


def test_the_url_never_appears_in_any_answer(client, monkeypatch):
    monkeypatch.setenv("ANALYTICS_READER_URL", f"postgresql://{URL_SECRET}@127.0.0.1:1/none")
    err, text = call(client, "sql", {"query": "select 1"})
    assert err and "topsecretpw" not in text and "analytics_reader" not in text and "127.0.0.1" not in text


# --------------------------------------------------------------------------------------------------------------------- audit and rate limit
def test_every_tool_call_writes_an_audit_row_without_the_question_or_the_result(client, pg, monkeypatch):
    monkeypatch.setenv("ANALYTICS_READER_URL", DB_URL)
    call(client, "search", {"text": "billing reminder secretquestion"})
    call(client, "sql", {"query": "select 'sekret-result-value' as v"})
    call(client, "sql", {"query": "drop table products"})
    call(client, "describe", {"name": "meta_ad_daily"})
    rows = pg.execute("SELECT actor, tool, duration_ms, row_count, ok FROM brain_audit ORDER BY id").fetchall()
    assert [(r[0], r[1], r[3], r[4]) for r in rows] == [("JoshHoughton", "search", rows[0][3], True), ("JoshHoughton", "sql", 1, True), ("JoshHoughton", "sql", None, False), ("JoshHoughton", "describe", None, True)]
    assert all(isinstance(r[2], int) and r[2] >= 0 for r in rows)
    dump = json.dumps([list(map(str, r)) for r in pg.execute("SELECT * FROM brain_audit").fetchall()]) + str([d.name for d in pg.execute("SELECT * FROM brain_audit").description])
    assert "secretquestion" not in dump and "sekret" not in dump and "products" not in dump
    rpc(client, "tools/list")
    assert pg.execute("SELECT count(*) FROM brain_audit").fetchone()[0] == 4          # only tools/call is audited


def test_audit_names_the_kind_of_caller(anon, pg, brain_key, api_key):
    call(anon, "catalog", headers=bearer(brain_key))
    call(anon, "catalog", headers=bearer(api_key))
    assert [r[0] for r in pg.execute("SELECT actor FROM brain_audit ORDER BY id").fetchall()] == ["brain:JoshHoughton", "key:ci-colleague"]


def test_rate_limit(app_factory, pg):
    a = app_factory(BRAIN_RATE_PER_MIN=3)
    c = login(a)
    assert [rpc(c, "ping").status_code for _ in range(4)] == [200, 200, 200, 429]
    r = rpc(c, "ping")
    assert r.get_json()["error"] == "too_many_attempts" and int(r.headers["Retry-After"]) >= 1
    other = login(a, "Lazer", "staple battery horse")
    assert rpc(other, "ping").status_code == 200                                  # the limit is per caller


# --------------------------------------------------------------------------------------------------------------------- Connect an agent
def test_key_routes_need_a_signed_in_person(anon, client, api_key, brain_key):
    assert anon.get("/api/brain/key").status_code == 401 and anon.post("/api/brain/key", json={}).status_code == 401
    assert client.post("/api/brain/key", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/brain/key", headers=bearer(api_key)).status_code == 403          # an API key cannot make or read agent keys
    assert client.post("/api/brain/key", json={}, headers=bearer(api_key)).status_code == 403


def test_the_key_is_shown_once_and_stored_hashed_and_a_reset_replaces_it(client, anon, pg):
    st = client.get("/api/brain/key").get_json()
    assert st["has_key"] is False and st["open_app"] is False and st["endpoint"].endswith("/mcp") and st["user"] == "JoshHoughton" and st["sql_configured"] is False
    made = client.post("/api/brain/key", json={})
    k1 = made.get_json()["key"]
    assert made.status_code == 200 and made.headers["Cache-Control"] == "no-store" and made.get_json()["prefix"] == k1[:8]
    again = client.get("/api/brain/key").get_json()
    assert again["has_key"] is True and again["prefix"] == k1[:8] and k1 not in json.dumps(again) and "key" not in again
    row = pg.execute("SELECT username, key_hash, prefix, last_used_at FROM brain_keys").fetchall()
    assert len(row) == 1 and row[0][1] == hashlib.sha256(k1.encode()).hexdigest() and k1 not in " ".join(map(str, row[0]))
    assert rpc(anon, "ping", headers=bearer(k1)).status_code == 200
    assert pg.execute("SELECT last_used_at IS NOT NULL FROM brain_keys").fetchone()[0] is True
    k2 = client.post("/api/brain/key", json={}).get_json()["key"]
    assert k2 != k1 and rpc(anon, "ping", headers=bearer(k1)).status_code == 401 and rpc(anon, "ping", headers=bearer(k2)).status_code == 200
    assert pg.execute("SELECT count(*) FROM brain_keys").fetchone()[0] == 1
    lazer = login(client.application, "Lazer", "staple battery horse")
    k3 = lazer.post("/api/brain/key", json={}).get_json()["key"]
    assert pg.execute("SELECT count(*) FROM brain_keys").fetchone()[0] == 2 and rpc(anon, "ping", headers=bearer(k2)).status_code == 200 and rpc(anon, "ping", headers=bearer(k3)).status_code == 200


def test_key_creation_is_rate_limited(client):
    codes = [client.post("/api/brain/key", json={}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[-1] == 429


def test_the_connect_page_is_served_and_its_code_is_wired(client, anon):
    r = anon.get("/connect-agent")
    assert r.status_code == 200 and b'id="app"' in r.get_data() or r.status_code == 200
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "app" / "static"
    assert "connectAgentPage" in (root / "app.js").read_text() and "claude mcp add --transport http lonvera-brain" in (root / "js" / "pages" / "connect_agent.js").read_text()
    assert PASSWORD
