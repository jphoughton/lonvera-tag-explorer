"""The brain while the app is open (AUTH_DISABLED on): anyone who can open the app can make a key, but /mcp still needs a valid Bearer key, so every call is attributable and rate limited."""
import hashlib
import json

import pytest

from etl import api_keys as cli


def bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def rpc(c, method, params=None, headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return c.post("/mcp", json=body, headers=headers)


@pytest.fixture()
def open_client(app_factory):
    return app_factory(AUTH_DISABLED=True).test_client()


def make_key(c, **kw):
    r = c.post("/api/brain/key", json={}, **kw)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["key"]


def test_the_matrix_without_a_valid_key_is_401(open_client, app_factory):
    c = open_client
    assert rpc(c, "initialize").status_code == 401 and rpc(c, "tools/list").get_json() == {"error": "unauthorized"}          # keyless is NOT open
    assert c.post("/mcp", data="not json").status_code == 401
    for raw in ("", "lvb_", "lvb_" + "A" * 40, "lvk_" + "A" * 40, "x" * 44):
        assert rpc(c, "initialize", headers=bearer(raw)).status_code == 401, raw
    tok = c.post("/api/priorities/vendor-links", json={"vendor": "Madre"}).get_json()["token"]
    assert rpc(c, "initialize", headers=bearer(tok)).status_code == 401                           # a vendor link token grants nothing
    assert c.post(f"/mcp/{tok}", json={}).status_code == 404 and c.get("/mcp").status_code == 405


def test_a_fresh_open_app_key_works_on_mcp_and_only_there(open_client, pg):
    c = open_client
    k = make_key(c)
    assert k.startswith("lvb_") and len(k) == 44
    r = rpc(c, "tools/list", headers=bearer(k))
    assert r.status_code == 200 and len(r.get_json()["result"]["tools"]) == 10
    # the key is a brain key: with sign-in off the rest of the API is open to everyone anyway (with or without it), so it gains nothing there; it is never a login
    assert c.get("/api/status", headers=bearer(k)).status_code == c.get("/api/status").status_code != 401
    res = rpc(c, "tools/call", {"name": "describe", "arguments": {"name": "meta_ad_daily"}}, headers=bearer(k)).get_json()["result"]
    assert res["isError"] is False
    row = pg.execute("SELECT actor, tool FROM brain_audit").fetchone()
    assert row == (f"brain:open-app:{k[:8]}", "describe")


def test_an_existing_api_key_works_exactly_as_with_sign_in_on(open_client, pg):
    raw, _ = cli.create_key(pg, "ci-colleague", "pytest")
    c = open_client
    assert rpc(c, "tools/list", headers=bearer(raw)).status_code == 200
    cli.revoke_key(pg, "ci-colleague")
    assert rpc(c, "tools/list", headers=bearer(raw)).status_code == 401


def test_the_key_row_has_no_user_and_only_a_salted_ip_hash(open_client, pg):
    k = make_key(open_client)
    row = pg.execute("SELECT username, created_by, creator_hash, key_hash, prefix FROM brain_keys").fetchall()
    assert len(row) == 1
    username, created_by, creator_hash, key_hash, prefix = row[0]
    assert username is None and created_by == "open-app" and prefix == k[:8] and key_hash == hashlib.sha256(k.encode()).hexdigest()
    assert len(creator_hash) == 32 and set(creator_hash) <= set("0123456789abcdef")
    for ip in ("127.0.0.1", "::1"):
        assert creator_hash not in (hashlib.sha256(ip.encode()).hexdigest()[:32], ip)               # not a plain hash of the address (it is salted with SECRET_KEY)
    dump = json.dumps([list(map(str, r)) for r in pg.execute("SELECT * FROM brain_keys").fetchall()])
    assert "127.0.0.1" not in dump and k not in dump


def test_each_post_makes_a_new_key_and_old_ones_keep_working(open_client, pg):
    k1, k2 = make_key(open_client), make_key(open_client)
    assert k1 != k2 and pg.execute("SELECT count(*) FROM brain_keys").fetchone()[0] == 2
    assert rpc(open_client, "ping", headers=bearer(k1)).status_code == 200 and rpc(open_client, "ping", headers=bearer(k2)).status_code == 200


def test_status_and_page_work_without_a_login(open_client):
    st = open_client.get("/api/brain/key").get_json()
    assert st["open_app"] is True and st["user"] is None and st["has_key"] is False and st["endpoint"].endswith("/mcp")
    assert open_client.get("/connect-agent").status_code == 200


def test_the_daily_cap_across_the_whole_app_is_429(app_factory, pg):
    c = app_factory(AUTH_DISABLED=True, BRAIN_OPEN_KEYS_PER_DAY=3).test_client()
    assert [c.post("/api/brain/key", json={}).status_code for _ in range(3)] == [200, 200, 200]
    r = c.post("/api/brain/key", json={})
    assert r.status_code == 429 and "limit" in r.get_json()["error"] and r.headers["Retry-After"] == "3600"
    assert pg.execute("SELECT count(*) FROM brain_keys").fetchone()[0] == 3
    pg.execute("UPDATE brain_keys SET created_at = now() - interval '25 hours' WHERE id = (SELECT min(id) FROM brain_keys)")           # a key older than a day frees a slot
    assert c.post("/api/brain/key", json={}).status_code == 200
    other_ip = c.post("/api/brain/key", json={}, environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    assert other_ip.status_code == 429                                                             # the cap is app-wide, not per address


def test_signed_in_keys_do_not_use_up_the_open_app_cap(app, client, app_factory, pg):
    assert client.post("/api/brain/key", json={}).status_code == 200
    row = pg.execute("SELECT username, created_by, creator_hash FROM brain_keys").fetchone()
    assert row == ("JoshHoughton", "signed-in", None)
    c = app_factory(AUTH_DISABLED=True, BRAIN_OPEN_KEYS_PER_DAY=1).test_client()
    assert c.post("/api/brain/key", json={}).status_code == 200 and c.post("/api/brain/key", json={}).status_code == 429


def test_a_foreign_origin_is_403_and_per_ip_limit_is_ten_a_minute(open_client, pg):
    assert open_client.post("/api/brain/key", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert pg.execute("SELECT count(*) FROM brain_keys").fetchone()[0] == 0
    assert open_client.post("/api/brain/key", json={}, headers={"Origin": "http://localhost"}).status_code == 200
    codes = [open_client.post("/api/brain/key", json={}).status_code for _ in range(11)]
    assert codes[:9] == [200] * 9 and codes[-1] == 429


def test_the_mcp_rate_limit_still_applies(app_factory):
    a = app_factory(AUTH_DISABLED=True, BRAIN_RATE_PER_MIN=2)
    c = a.test_client()
    k = make_key(c)
    assert [rpc(c, "ping", headers=bearer(k)).status_code for _ in range(3)] == [200, 200, 429]
