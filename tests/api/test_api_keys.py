"""Scoped API keys (app/api_keys.py, app/auth.py, migration 205): a key reads everything the app serves and writes only the two production assignment routes.
Everything about a key is checked here: scope, revoked / malformed keys, no cookie fallback, the launch gate, login, vendor link tokens, the hash at rest, last_used_at, the failed-lookup
throttle, the Origin check and the response cache."""
import hashlib
import re

import pytest

from app import api_keys as ak
from etl import api_keys as cli

from .conftest import PASSWORD
from .test_api_priorities_assign import LINE_ROW, MONDAY, NEXT_MONDAY, clean, link, one, snaps          # noqa: F401  (fixtures: snaps, and the autouse clean)
from .test_api_priorities import _key

NAME = "ci-colleague"
ASSIGN_KEY = _key({**LINE_ROW, "section": "faded"})


@pytest.fixture(autouse=True)
def fresh_throttle(app):
    app.extensions["apikey_throttle_ip"]._fails.clear()          # the shared test app sees every request from one IP
    yield
    app.extensions["apikey_throttle_ip"]._fails.clear()


def bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture()
def mint(pg):
    """mint(name) -> raw key, a real active row in api_keys; all rows removed afterwards."""
    pg.execute("DELETE FROM api_keys")
    pg.commit()

    def make(name=NAME):
        raw, _prefix = cli.create_key(pg, name, "pytest")
        return raw

    yield make
    pg.rollback()
    pg.execute("DELETE FROM api_keys")
    pg.commit()


@pytest.fixture()
def key(mint):
    return mint()


def post_assign(c, raw, **kw):
    return c.post("/api/priorities/assignments", json={"keys": [ASSIGN_KEY], "vendor": "Madre", "for_week": MONDAY}, headers=bearer(raw), **kw)


# ------------------------------------------------------------------------------------------------------------------ format and storage
def test_generated_keys_are_long_random_and_well_formed():
    ks = {ak.generate_key() for _ in range(200)}
    assert len(ks) == 200
    for k in ks:
        assert k.startswith("lvk_") and len(k) == 44 and ak.well_formed(k)
    assert not ak.well_formed("lvk_short") and not ak.well_formed("x" * 44) and not ak.well_formed("") and not ak.well_formed(None)
    assert not ak.well_formed("lvk_" + "a" * 40 + "x") and not ak.well_formed("lvk_" + "a" * 39 + "!")


def test_only_the_hash_is_stored(key, pg):
    rows = pg.execute("SELECT * FROM api_keys").fetchall()
    assert len(rows) == 1
    cols = [d.name for d in pg.execute("SELECT * FROM api_keys").description]
    row = dict(zip(cols, rows[0]))
    assert row["key_hash"] == hashlib.sha256(key.encode()).hexdigest() and re.fullmatch(r"[0-9a-f]{64}", row["key_hash"])
    assert row["prefix"] == key[:8] and row["scope"] == "read_assign" and row["name"] == NAME and row["revoked_at"] is None and row["last_used_at"] is None
    body = key[4:]
    assert not any(isinstance(v, str) and body in v for v in row.values())              # the secret part of the key is in no column
    assert body not in " ".join(str(v) for v in pg.execute("SELECT * FROM api_keys").fetchone())


def test_the_scope_is_checked_by_the_database(pg):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO api_keys (name, key_hash, prefix, scope) VALUES ('x', 'h', 'lvk_aaaa', 'admin')")
    pg.rollback()


# ------------------------------------------------------------------------------------------------------------------ reads
@pytest.mark.parametrize("url", ["/api/status", "/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/ads", "/api/ads/1000",
                                 "/api/priorities", "/api/priorities/assignments", "/api/priorities/manual", "/api/priorities/manual/options", "/api/priorities/views", "/api/trends"])
def test_a_key_can_read(anon, key, url, snaps):
    r = anon.get(url, headers=bearer(key))
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:200])
    assert r.get_json() is not None


def test_a_key_reaches_media_and_head_requests(anon, key):
    r = anon.get("/media/1", headers=bearer(key))
    assert r.status_code not in (401, 403)                                                # 200 with the file, or 404 when the fixture has none: never an auth refusal
    assert anon.get("/media/1").status_code == 401                                        # the same request without the key is refused
    assert anon.head("/api/products", headers=bearer(key)).status_code == 200


def test_status_names_the_key(anon, key):
    assert anon.get("/api/status", headers=bearer(key)).get_json()["user"] == f"key:{NAME}"


def test_a_get_with_a_key_keeps_its_per_route_behaviour(anon, key, client):
    for url in ("/api/products/nope/families", "/api/products/vita-bull/tree?depth=9", "/api/nope", "/api/ads/abc"):
        assert anon.get(url, headers=bearer(key)).status_code == client.get(url).status_code, url


# ------------------------------------------------------------------------------------------------------------------ the two allowed writes
def test_a_key_can_assign_and_update_and_the_audit_names_the_key(anon, key, pg, snaps):
    r = post_assign(anon, key)
    assert r.status_code == 201, r.get_data(as_text=True)
    a = r.get_json()["created"][0]
    row = pg.execute("SELECT created_by, updated_by FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()
    assert tuple(row) == (f"key:{NAME}", f"key:{NAME}")
    r = anon.patch(f"/api/priorities/assignments/{a['id']}", json={"note": "from the key", "brief_url": "https://docs.example/brief"}, headers=bearer(key))
    assert r.status_code == 200 and r.get_json()["note"] == "from the key"
    pg.rollback()
    row = pg.execute("SELECT note, updated_by, brief_at IS NOT NULL FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()
    assert tuple(row) == ("from the key", f"key:{NAME}", True)
    listed = anon.get("/api/priorities/assignments", headers=bearer(key)).get_json()["rows"]
    assert [x["id"] for x in listed] == [a["id"]]


def test_a_key_still_gets_the_route_validation_and_duplicates(anon, key, snaps):
    assert post_assign(anon, key).status_code == 201
    dup = post_assign(anon, key)
    assert dup.status_code == 201 and dup.get_json()["created"] == [] and dup.get_json()["skipped"][0]["reason"].startswith("already assigned")
    assert anon.post("/api/priorities/assignments", json={"keys": [], "vendor": "Nobody", "for_week": MONDAY}, headers=bearer(key)).status_code == 400
    assert anon.patch("/api/priorities/assignments/999999", json={"note": "x"}, headers=bearer(key)).status_code == 404
    assert anon.patch("/api/priorities/assignments/abc", json={"note": "x"}, headers=bearer(key)).status_code == 403          # not an integer: no such route, so not an allowed write


def test_the_origin_check_still_applies_to_key_writes(anon, key, snaps):
    assert post_assign(anon, key).status_code == 201                                      # no Origin header at all (curl, scripts): allowed
    bad = {**bearer(key), "Origin": "http://evil.example"}
    r = anon.post("/api/priorities/assignments", json={"keys": [ASSIGN_KEY], "vendor": "Victoria", "for_week": MONDAY}, headers=bad)
    assert r.status_code == 403 and r.get_json() == {"error": "origin mismatch"}
    same = {**bearer(key), "Origin": "http://localhost"}                                  # the test client's own host
    assert anon.post("/api/priorities/assignments", json={"keys": [ASSIGN_KEY], "vendor": "Victoria", "for_week": MONDAY}, headers=same).status_code == 201


# ------------------------------------------------------------------------------------------------------------------ everything else is read only
def _fill(rule: str) -> str:
    return re.sub(r"<(?:int:|aint:)?[a-z_]+>", lambda m: "1" if m.group(0).startswith(("<int", "<aint")) else "x", rule)


def _write_rules(app):
    from app import auth
    out = []
    for r in app.url_map.iter_rules():
        extra = set(r.methods) - {"GET", "HEAD", "OPTIONS"}
        if not extra or r.rule in auth.PUBLIC_API or not r.rule.startswith(auth.PROTECTED_PREFIXES) or auth.PUBLIC_API_RE.fullmatch(_fill(r.rule)):
            continue
        for m in sorted(extra):
            if (m, r.rule) not in ak.ALLOWED_WRITES:
                out.append((m, r.rule))
    return out


def test_allowed_writes_are_exactly_two_and_exist():
    assert ak.ALLOWED_WRITES == {("POST", "/api/priorities/assignments"), ("PATCH", "/api/priorities/assignments/<int:aid>")}


def test_every_other_write_route_is_refused_with_403(app, anon, key, pg, snaps):
    rules = _write_rules(app)
    names = {r for _, r in rules}
    for must in ("/api/priorities/views", "/api/priorities/manual", "/api/priorities/vendor-links", "/api/priorities/vendor-links/<vendor>/reset", "/api/priorities/launch/settings/<product>",
                 "/api/priorities/assignments/<int:aid>/launch/prepare"):
        assert must in names, must                                                        # the sweep really covers views, manual concepts, vendor links, launch and settings
    assert any(n.startswith("/api/projection/") for n in names) and len(rules) >= 15
    before = pg.execute("SELECT (SELECT count(*) FROM creative_priorities_assignments), (SELECT count(*) FROM creative_priorities_views), (SELECT count(*) FROM creative_priorities_manual)").fetchone()
    for method, rule in rules:
        r = anon.open(_fill(rule), method=method, json={}, headers=bearer(key))
        assert r.status_code == 403 and r.get_json() == {"error": ak.READ_ONLY_MESSAGE}, (method, rule, r.status_code)
    pg.rollback()
    assert pg.execute("SELECT (SELECT count(*) FROM creative_priorities_assignments), (SELECT count(*) FROM creative_priorities_views), (SELECT count(*) FROM creative_priorities_manual)").fetchone() == before


@pytest.mark.parametrize("method,url", [("put", "/api/priorities/views/abc"), ("post", "/api/priorities/views"), ("patch", "/api/priorities/views/abc"), ("delete", "/api/priorities/views/abc"),
                                        ("post", "/api/priorities/manual"), ("patch", "/api/priorities/manual/1"), ("delete", "/api/priorities/manual/1"),
                                        ("post", "/api/priorities/vendor-links"), ("post", "/api/priorities/vendor-links/Madre/reset"),
                                        ("put", "/api/priorities/assignments/1/launch/draft"), ("post", "/api/priorities/assignments/1/launch/prepare"),
                                        ("post", "/api/priorities/assignments/1/launch/confirm"), ("post", "/api/priorities/assignments/1/launch/cancel"),
                                        ("put", "/api/priorities/launch/settings/vita-bull"), ("put", "/api/priorities/assignments/1"), ("delete", "/api/priorities/assignments/1"),
                                        ("post", "/api/priorities/assignments/1"), ("post", "/api/products"), ("delete", "/api/ads/1000")])
def test_named_writes_are_refused(anon, key, method, url):
    r = getattr(anon, method)(url, json={}, headers=bearer(key))
    assert r.status_code == 403, (method, url, r.status_code)
    assert r.get_json() == {"error": "This API key is read only except for assigning items on the production board."}


def test_the_vendor_token_route_is_not_a_key_route(anon, key, client, snaps):
    a = one(client)
    tok = link(client)["token"]
    # the vendor link page is login-exempt on purpose (the token is the credential); a key neither helps nor hurts there
    assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"vendor_note": "hi"}).status_code == 200
    assert anon.patch(f"/api/priorities/vendor/{'B' * 30}/{a['id']}", json={"vendor_note": "hi"}, headers=bearer(key)).status_code == 404         # wrong token, key present: still 404


def test_a_key_cannot_read_vendor_link_tokens(anon, key, client, snaps):
    tok = link(client)["token"]
    assert client.get("/api/priorities/vendor-links").get_json()["links"][0]["token"] == tok       # a login can
    r = anon.get("/api/priorities/vendor-links", headers=bearer(key))
    assert r.status_code == 403 and r.get_json() == {"error": ak.BLOCKED_MESSAGE} and tok not in r.get_data(as_text=True)
    assert tok not in anon.get("/api/priorities/assignments", headers=bearer(key)).get_data(as_text=True)      # and no other read hands a token out
    assert tok not in anon.get("/api/priorities", headers=bearer(key)).get_data(as_text=True)
    assert tok not in anon.get("/api/priorities/views", headers=bearer(key)).get_data(as_text=True)


# ------------------------------------------------------------------------------------------------------------------ bad keys
def test_revoked_key_is_401(anon, key, pg):
    assert anon.get("/api/status", headers=bearer(key)).status_code == 200
    cli.revoke_key(pg, NAME)
    for r in (anon.get("/api/status", headers=bearer(key)), post_assign(anon, key)):
        assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("raw", ["", "lvk_", "lvk_short", "abc", "x" * 44, "Bearer", "lvk_" + "A" * 40, "lvk_" + "A" * 41, "lvk_" + "A" * 39, "lvk_" + "!" * 40, "LVK_" + "A" * 40, "a b", "lvk_" + "A" * 20 + " " + "A" * 19])
def test_malformed_or_unknown_keys_are_401(anon, key, raw):
    for r in (anon.get("/api/status", headers=bearer(raw)), anon.post("/api/priorities/assignments", json={}, headers=bearer(raw)), anon.get("/media/1", headers=bearer(raw))):
        assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_the_key_body_without_its_prefix_is_401(anon, key):
    assert anon.get("/api/status", headers=bearer(key[4:])).status_code == 401
    assert anon.get("/api/status", headers=bearer("lvx_" + key[4:])).status_code == 401
    assert anon.get("/api/status", headers={"Authorization": key}).status_code == 401                 # no Bearer word: not a key, and no cookie either


def test_a_bad_bearer_never_falls_back_to_a_valid_cookie(client, key):
    assert client.get("/api/status").status_code == 200
    for raw in ("lvk_" + "Z" * 40, "garbage", ""):
        r = client.get("/api/status", headers=bearer(raw))
        assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}, raw
    assert client.get("/api/status", headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 200      # a non-Bearer header is not a key: the cookie login stands


def test_a_key_in_scope_beats_a_cookie_for_identity_and_out_of_scope_stays_refused(client, key):
    assert client.get("/api/status", headers=bearer(key)).get_json()["user"] == f"key:{NAME}"
    assert client.post("/api/priorities/views", json={}, headers=bearer(key)).status_code == 403          # a valid cookie does not widen a key


def test_a_key_never_authenticates_login(anon, key):
    r = anon.post("/api/login", json={}, headers=bearer(key))
    assert r.status_code == 401 and r.get_json() == {"error": "invalid username or password"} and r.headers.get("Set-Cookie") is None
    r = anon.post("/api/login", json={"username": NAME, "password": key}, headers=bearer(key))
    assert r.status_code == 401 and r.headers.get("Set-Cookie") is None
    assert anon.get("/api/status").status_code == 401                                                    # no session was created
    assert anon.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204     # real logins are untouched


def test_auth_disabled_apps_ignore_keys(app_factory):
    c = app_factory(AUTH_DISABLED=True).test_client()
    assert c.get("/api/status", headers=bearer("garbage")).status_code == 200                           # open mode: nothing to authenticate, the header is ignored


# ------------------------------------------------------------------------------------------------------------------ launch gate
def test_a_key_never_passes_the_launch_gate(anon, key, monkeypatch, snaps):
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    monkeypatch.setenv("META_LAUNCH_USERS", f"joshhoughton,key:{NAME},guest")            # even a deliberately listed key name does not count
    for method, url in (("get", "/api/priorities/launch/settings"), ("get", "/api/priorities/assignments/1/launch"), ("put", "/api/priorities/launch/settings/vita-bull"),
                        ("post", "/api/priorities/assignments/1/launch/prepare"), ("post", "/api/priorities/assignments/1/launch/confirm")):
        r = getattr(anon, method)(url, json={}, headers=bearer(key))
        assert r.status_code == 403, (method, url, r.status_code)
    assert anon.get("/api/priorities/assignments", headers=bearer(key)).get_json()["launch"] == {"enabled": True, "can_launch": False, "dry_run": True}
    monkeypatch.delenv("META_LAUNCH_ENABLED")
    assert anon.get("/api/priorities/launch/settings", headers=bearer(key)).status_code == 404       # dark: the same 404 a login gets
    assert anon.post("/api/priorities/assignments/1/launch/prepare", json={}, headers=bearer(key)).status_code == 403


# ------------------------------------------------------------------------------------------------------------------ last used, throttle, cache
def test_last_used_at_moves_at_most_once_a_minute(anon, key, pg):
    def last():
        pg.rollback()
        return pg.execute("SELECT last_used_at FROM api_keys WHERE name = %s", (NAME,)).fetchone()[0]
    assert last() is None
    assert anon.get("/api/status", headers=bearer(key)).status_code == 200
    first = last()
    assert first is not None
    for _ in range(3):
        anon.get("/api/status", headers=bearer(key))
    assert last() == first                                                                # inside the minute: untouched
    pg.execute("UPDATE api_keys SET last_used_at = now() - interval '2 minutes' WHERE name = %s", (NAME,))
    pg.commit()
    old = last()
    anon.get("/api/status", headers=bearer(key))
    assert last() > old
    assert (pg.execute("SELECT now() - last_used_at < interval '30 seconds' FROM api_keys WHERE name = %s", (NAME,)).fetchone()[0])


def test_failed_lookups_are_throttled_per_ip_and_a_valid_key_waits_too(app_factory, key):
    c = app_factory(API_KEY_MAX_FAILS_IP=3).test_client()
    for _ in range(3):
        assert c.get("/api/status", headers=bearer("lvk_" + "N" * 40)).status_code == 401
    r = c.get("/api/status", headers=bearer("lvk_" + "N" * 40))
    assert r.status_code == 429 and r.get_json()["error"] == "too_many_attempts" and int(r.headers["Retry-After"]) >= 1
    assert c.get("/api/status", headers=bearer(key)).status_code == 429                    # the refusal comes before any lookup
    other = app_factory(API_KEY_MAX_FAILS_IP=3).test_client()
    assert other.get("/api/status", headers=bearer(key)).status_code == 200               # another app instance (another throttle) is unaffected
    assert other.get("/api/status").status_code == 401                                    # cookie-less requests do not feed the key throttle
    cookie = app_factory(API_KEY_MAX_FAILS_IP=1).test_client()
    for _ in range(3):
        assert cookie.get("/api/status").status_code == 401                              # (never 429: only bad Bearer tokens count)


def test_the_response_cache_never_leaks_across_auth(app_factory, key):
    app = app_factory(RESPONSE_CACHE_MB=8)
    c = app.test_client()
    url = "/api/products/vita-bull/families?window=30d"
    first = c.get(url, headers=bearer(key))
    assert first.status_code == 200 and first.headers["X-Cache"] == "miss"
    assert c.get(url, headers=bearer(key)).headers["X-Cache"] == "hit"
    anon = app.test_client()
    for headers in ({}, bearer("lvk_" + "Y" * 40), bearer(""), {"Authorization": "Basic eDp5"}):
        r = anon.get(url, headers=headers)
        assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"} and r.headers.get("X-Cache") is None, headers
    assert app.extensions["response_cache"].hits == 1
    assert c.get(url, headers=bearer(key)).data == first.data                              # and the key still gets its own cached answer
    assert all(key not in str(k) for k in app.extensions["response_cache"]._d)
