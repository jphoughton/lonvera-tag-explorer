import os

import pytest
from werkzeug.security import generate_password_hash

from .conftest import PASSWORD, USERS, USERS_PLAIN

API_URLS = [
    "/api/status", "/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/ads",
    "/api/products/vita-bull/campaigns", "/api/products/vita-bull/stage-daily", "/api/products/vita-bull/unmatched", "/api/ads/1000",
    "/media/1", "/media/999999", "/api/nope", "/media/abc",
]


@pytest.mark.parametrize("url", API_URLS)
def test_unauthenticated_gets_401_json(anon, url):
    r = anon.get(url)
    assert r.status_code == 401
    assert r.get_json() == {"error": "unauthorized"}


def test_public_routes(anon):
    assert anon.get("/healthz").get_json() == {"ok": True}
    for path in ("/", "/login", "/p/vita-bull", "/ad/1000"):
        r = anon.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.content_type
        assert b"<" in r.data
    assert anon.get("/static/does-not-exist.js").status_code == 404


def login(c, user, pw, **kw):
    return c.post("/api/login", json={"username": user, "password": pw}, **kw)


def test_both_users_can_log_in_and_the_session_names_them(app_factory):
    a = app_factory()
    for user, pw in USERS_PLAIN.items():
        c = a.test_client()
        assert c.get("/api/status").status_code == 401
        r = login(c, user, pw)
        assert r.status_code == 204 and r.data == b""
        st = c.get("/api/status")
        assert st.status_code == 200 and st.get_json()["user"] == user
        assert c.post("/api/logout").status_code == 204
        assert c.get("/api/status").status_code == 401


def test_username_is_case_insensitive_but_the_session_has_the_canonical_name(app_factory):
    a = app_factory()
    for typed in ("joshhoughton", "JOSHHOUGHTON", "  JoshHoughton  ", "jOsHhOuGhToN"):
        c = a.test_client()
        assert login(c, typed, USERS_PLAIN["JoshHoughton"]).status_code == 204, typed
        assert c.get("/api/status").get_json()["user"] == "JoshHoughton"
    c = a.test_client()
    assert login(c, "lAzEr", USERS_PLAIN["Lazer"]).status_code == 204 and c.get("/api/status").get_json()["user"] == "Lazer"
    assert login(a.test_client(), "joshhoughton", USERS_PLAIN["Lazer"]).status_code == 401           # a user's password only opens that user


def test_wrong_password_and_unknown_user_give_the_identical_response(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=50, LOGIN_MAX_FAILS_IP=500)
    c = a.test_client()
    wrong_pw = login(c, "JoshHoughton", "nope")
    unknown = login(c, "NoSuchUser", USERS_PLAIN["JoshHoughton"])
    unknown_pw = login(c, "NoSuchUser", "nope")
    for r in (wrong_pw, unknown, unknown_pw):
        assert r.status_code == 401 and r.get_json() == {"error": "invalid username or password"}
    assert wrong_pw.data == unknown.data == unknown_pw.data and wrong_pw.headers.get("Set-Cookie") == unknown.headers.get("Set-Cookie") is None
    for body in ({}, {"username": "JoshHoughton"}, {"password": USERS_PLAIN["JoshHoughton"]}, {"username": None, "password": None}, {"username": 5, "password": 7},
                 {"username": "JoshHoughton", "password": ""}, {"username": "", "password": ""}, {"username": ["JoshHoughton"], "password": USERS_PLAIN["JoshHoughton"]},
                 {"user": "JoshHoughton", "password": USERS_PLAIN["JoshHoughton"]}):
        r = c.post("/api/login", json=body)
        assert r.status_code == 401 and r.get_json() == {"error": "invalid username or password"}, body
    assert c.post("/api/login", data="username=JoshHoughton&password=" + USERS_PLAIN["JoshHoughton"]).status_code == 401      # not JSON
    assert c.get("/api/status").status_code == 401


def test_a_hash_check_runs_even_for_unknown_users(app_factory, monkeypatch):
    from app import auth
    seen = []
    real = auth.check_password_hash
    monkeypatch.setattr(auth, "check_password_hash", lambda h, p: (seen.append(h), real(h, p))[1])
    a = app_factory(LOGIN_MAX_FAILS=50, LOGIN_MAX_FAILS_IP=500)
    c = a.test_client()
    login(c, "JoshHoughton", "x")
    login(c, "Ghost", "x")
    c.post("/api/login", json={})
    assert len(seen) == 3                                         # known user, unknown user, malformed body: always one verification
    assert seen[0] == USERS["JoshHoughton"] and seen[1] == seen[2] == a.extensions["auth_dummy_hash"] and seen[1] not in USERS.values()


def test_cookie_flags_prod_and_dev(app_factory):
    prod = app_factory(DEV_INSECURE_COOKIES=False, SECRET_KEY="another-secret")
    cookie = login(prod.test_client(), "JoshHoughton", PASSWORD).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    assert "Max-Age" in cookie or "Expires" in cookie                      # permanent session
    dev = app_factory(DEV_INSECURE_COOKIES=True)
    cookie = login(dev.test_client(), "Lazer", USERS_PLAIN["Lazer"]).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie


def test_throttle_per_user_and_ip_pair(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=3, LOGIN_MAX_FAILS_IP=100)
    c = a.test_client()
    for _ in range(3):
        assert login(c, "JoshHoughton", "bad").status_code == 401
    r = login(c, "JoshHoughton", PASSWORD)                         # even the right password is refused while the pair is throttled
    assert r.status_code == 429 and r.get_json()["error"] == "too_many_attempts" and int(r.headers["Retry-After"]) > 0
    assert login(c, "joshhoughton", PASSWORD).status_code == 429   # username case does not dodge it
    assert c.get("/api/status").status_code == 401
    assert login(c, "Lazer", USERS_PLAIN["Lazer"]).status_code == 204                                         # same IP, other user: fine
    assert login(a.test_client(), "JoshHoughton", PASSWORD, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).status_code == 204      # other IP: fine


def test_throttle_per_ip_across_usernames(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=100, LOGIN_MAX_FAILS_IP=5)
    c = a.test_client()
    for i in range(5):
        assert login(c, f"guess{i}", "bad").status_code == 401     # a different made-up username every time: the per-IP limit still counts
    r = login(c, "JoshHoughton", PASSWORD)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert login(a.test_client(), "JoshHoughton", PASSWORD, environ_overrides={"REMOTE_ADDR": "10.8.8.8"}).status_code == 204


def test_success_resets_the_pair_counter(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=3, LOGIN_MAX_FAILS_IP=100)
    c = a.test_client()
    for _ in range(2):
        login(c, "JoshHoughton", "bad")
    assert login(c, "JoshHoughton", PASSWORD).status_code == 204
    for _ in range(2):
        assert login(c, "JoshHoughton", "bad").status_code == 401      # counter restarted from zero


def test_changing_a_users_hash_signs_only_that_user_out(app_factory, client):
    josh_cookie = client.get_cookie("tagexp_session").value
    lazer = app_factory().test_client()
    login(lazer, "Lazer", USERS_PLAIN["Lazer"])
    lazer_cookie = lazer.get_cookie("tagexp_session").value
    rotated = dict(USERS, JoshHoughton=generate_password_hash("a brand new password", method="scrypt:1024:8:1"))
    a2 = app_factory(APP_USERS=rotated)                                 # same SECRET_KEY, Josh's hash changed
    for name, cookie, expect in (("josh", josh_cookie, 401), ("lazer", lazer_cookie, 200)):
        c = a2.test_client()
        c.set_cookie("tagexp_session", cookie)
        assert c.get("/api/status").status_code == expect, name
    assert login(a2.test_client(), "JoshHoughton", "a brand new password").status_code == 204
    assert login(a2.test_client(), "JoshHoughton", PASSWORD).status_code == 401


def test_removing_a_user_signs_them_out(app_factory, client):
    josh_cookie = client.get_cookie("tagexp_session").value
    only_lazer = app_factory(APP_USERS={"Lazer": USERS["Lazer"]})
    c = only_lazer.test_client()
    c.set_cookie("tagexp_session", josh_cookie)
    assert c.get("/api/status").status_code == 401
    assert login(only_lazer.test_client(), "JoshHoughton", PASSWORD).status_code == 401


def test_tampered_or_foreign_session_rejected(app_factory, client):
    cookie = client.get_cookie("tagexp_session").value
    other_secret = app_factory(SECRET_KEY="different-secret").test_client()
    other_secret.set_cookie("tagexp_session", cookie)
    assert other_secret.get("/api/status").status_code == 401           # signed with another SECRET_KEY
    junk = app_factory().test_client()
    junk.set_cookie("tagexp_session", cookie[:-3] + "xyz")
    assert junk.get("/api/status").status_code == 401


@pytest.mark.parametrize("users", [None, "", "{}", "not json", "[]", '["JoshHoughton"]', '{"JoshHoughton": ""}', '{"JoshHoughton": 5}', {},
                                   '{"A": "x:1", "a": "x:2"}'])
def test_fail_closed_without_usable_app_users(app_factory, users, caplog):
    import logging
    with caplog.at_level(logging.ERROR, logger="tagexp"):
        a = app_factory(APP_USERS=users)
    assert any("APP_USERS" in r.getMessage() for r in caplog.records)           # a clear startup error
    c = a.test_client()
    for user, pw in list(USERS_PLAIN.items()) + [("admin", "admin"), ("", "")]:
        r = login(c, user, pw)
        assert r.status_code == 503 and r.get_json() == {"error": "auth_not_configured"}
    assert c.get("/api/status").status_code == 401 and c.get("/api/products").status_code == 401
    assert c.get("/healthz").get_json() == {"ok": True}                          # the app itself still starts


def test_app_password_is_gone():
    import app.auth as auth
    import app.config as config
    from app import create_app
    assert "APP_PASSWORD" not in config.load_config({})
    for src in (open(auth.__file__).read(), open(config.__file__).read()):
        assert "APP_PASSWORD" not in src.replace("APP_PASSWORD is", "")
    a = create_app({"DATABASE_URL": "postgresql://localhost/x", "SECRET_KEY": "k", "DEV_INSECURE_COOKIES": True, "APP_PASSWORD": "legacy", "APP_USERS": ""})
    assert login(a.test_client(), "JoshHoughton", "legacy").status_code == 503     # no fallback to a shared password
    a.extensions["pool"].close()


def test_hashes_and_passwords_never_leak(app_factory, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        a = app_factory(LOGIN_MAX_FAILS=2, LOGIN_MAX_FAILS_IP=100)
        c = a.test_client()
        bodies = [login(c, "JoshHoughton", "bad"), login(c, "Ghost", "bad"), login(c, "JoshHoughton", "bad"), login(c, "JoshHoughton", PASSWORD)]
        bodies.append(c.post("/api/login", json={"username": "Lazer", "password": USERS_PLAIN["Lazer"]}))
        st = app_factory().test_client()
        login(st, "Lazer", USERS_PLAIN["Lazer"])
        bodies += [st.get("/api/status"), st.get("/api/products"), st.get("/api/nope")]
    blob = "".join(r.get_data(as_text=True) + str(dict(r.headers)) for r in bodies) + caplog.text
    for secret in list(USERS.values()) + list(USERS_PLAIN.values()) + ["scrypt:"]:
        assert secret not in blob, secret[:12]


def test_hash_script_prints_a_verifiable_hash_without_echoing_the_password():
    import subprocess
    import sys
    from werkzeug.security import check_password_hash
    script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "auth", "hash_password.py")
    out = subprocess.run([sys.executable, script], input="s3cret pass\n", capture_output=True, text=True, check=True)
    h = out.stdout.strip()
    assert h.startswith("scrypt:") and check_password_hash(h, "s3cret pass") and "s3cret pass" not in out.stdout + out.stderr
    assert subprocess.run([sys.executable, script], input="", capture_output=True, text=True).returncode == 1


def test_security_headers_and_json_errors(client):
    r = client.get("/api/status")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    assert client.post("/api/status").status_code == 405
    assert client.get("/api/ads/not-a-number").status_code == 404


def test_healthz_reports_db_failure(app_factory):
    a = app_factory(DATABASE_URL="postgresql://localhost:1/nothing", DB_POOL_MIN=1)
    r = a.test_client().get("/healthz")
    assert r.status_code == 503 and r.get_json() == {"ok": False}


def test_large_json_is_gzipped_when_accepted(client):
    import gzip
    import json
    url = "/api/products/vita-bull/families"
    plain = client.get(url)
    assert "Content-Encoding" not in plain.headers
    z = client.get(url, headers={"Accept-Encoding": "gzip, deflate"})
    assert z.headers["Content-Encoding"] == "gzip" and "Accept-Encoding" in z.headers["Vary"]
    assert int(z.headers["Content-Length"]) == len(z.data) < len(plain.data)
    assert json.loads(gzip.decompress(z.data)) == plain.get_json()
    small = client.get("/api/status", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in small.headers and small.get_json()["data_through"] == "2026-11-02"


def test_static_is_revalidated_and_media_is_cacheable(anon, client):
    import os
    from app import STATIC_DIR
    names = [n for n in ("app.js", "app.css", "index.html") if os.path.isfile(os.path.join(STATIC_DIR, n))]
    assert names, "no static files to check"
    for n in names:
        r = anon.get(f"/static/{n}")
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-cache", n          # ES modules are not hashed -> always revalidate
        etag = r.headers.get("ETag")
        if etag:
            again = anon.get(f"/static/{n}", headers={"If-None-Match": etag})
            assert again.status_code == 304 and again.headers["Cache-Control"] == "no-cache"
    assert anon.get("/static/definitely-missing.js").status_code == 404
    thumb = client.get("/api/ads/1000").get_json()["assets"]["thumb"]
    assert client.get(f"/media/{thumb}").headers["Cache-Control"] == "private, max-age=86400"


# ---------------------------------------------------------------------------------------------------------------- AUTH_DISABLED switch
def test_auth_disabled_opens_everything_without_a_cookie(app_factory):
    a = app_factory(AUTH_DISABLED=True, APP_USERS="")                 # no users configured at all: open mode does not care
    c = a.test_client()
    st = c.get("/api/status")
    assert st.status_code == 200 and st.get_json()["auth"] == "disabled" and st.get_json()["user"] == "guest"
    for url in ("/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/tree?root=cluster&depth=1", "/api/products/vita-bull/trend",
                "/api/ads/1000", "/api/products/vita-bull/ads?family_key=vb-fam-a"):
        assert c.get(url).status_code == 200, url
    thumb = c.get("/api/ads/1000").get_json()["assets"]["thumb"]
    r = c.get(f"/media/{thumb}")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.headers["Cache-Control"] == "private, max-age=86400"
    assert c.get("/api/products/nope/rollup").status_code == 404                  # real errors still behave


def test_auth_disabled_login_endpoints_are_harmless_and_unthrottled(app_factory):
    a = app_factory(AUTH_DISABLED=True, LOGIN_MAX_FAILS=1, LOGIN_MAX_FAILS_IP=1)
    c = a.test_client()
    for body in ({"username": "JoshHoughton", "password": "wrong"}, {}, {"username": "x"}, None):
        for _ in range(4):                                                         # far past any throttle limit
            r = c.post("/api/login", json=body) if body is not None else c.post("/api/login", data="junk")
            assert r.status_code == 204 and r.data == b"" and "Set-Cookie" not in r.headers
    assert c.post("/api/logout").status_code == 204
    assert c.get("/api/status").get_json()["user"] == "guest"
    r = c.get("/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert c.get("/").status_code == 200


def test_auth_enabled_is_exactly_the_old_behaviour(app_factory, anon):
    for flag in (False, None):
        a = app_factory(AUTH_DISABLED=flag) if flag is not None else app_factory()
        c = a.test_client()
        assert c.get("/api/status").status_code == 401 and c.get("/api/products").status_code == 401 and c.get("/media/1").status_code == 401
        assert c.get("/login").status_code == 200                                    # the login page is served, no redirect
        assert login(c, "JoshHoughton", "bad").status_code == 401
        assert login(c, "JoshHoughton", PASSWORD).status_code == 204
        st = c.get("/api/status").get_json()
        assert st["auth"] == "enabled" and st["user"] == "JoshHoughton"
    # switching it on again with no users configured must not open the door (fail closed stays)
    closed = app_factory(AUTH_DISABLED=False, APP_USERS="").test_client()
    assert closed.get("/api/products").status_code == 401 and login(closed, "JoshHoughton", PASSWORD).status_code == 503


def test_auth_disabled_reads_the_env_flag(monkeypatch):
    from app.config import load_config
    for val, want in (("1", True), ("true", True), ("0", False), ("", False), ("no", False)):
        monkeypatch.setenv("AUTH_DISABLED", val)
        assert load_config({})["AUTH_DISABLED"] is want, val
    monkeypatch.delenv("AUTH_DISABLED")
    assert load_config({})["AUTH_DISABLED"] is False


# ---------------------------------------------------------------------------------------------------------------- crawlers + read-only
def test_robots_txt_and_noindex_header_everywhere(anon, client, app_factory):
    r = anon.get("/robots.txt")
    assert r.status_code == 200 and r.mimetype == "text/plain" and r.get_data(as_text=True) == "User-agent: *\nDisallow: /"
    open_app = app_factory(AUTH_DISABLED=True).test_client()
    for c, urls in ((anon, ["/", "/login", "/p/vita-bull", "/healthz", "/robots.txt", "/static/app.js", "/api/status", "/media/1", "/nope", "/api/nope"]),
                    (client, ["/api/products", "/api/ads/1000"]), (open_app, ["/api/products", "/login", "/"])):
        for u in urls:
            assert c.get(u).headers.get("X-Robots-Tag") == "noindex, nofollow", u
    assert anon.post("/api/login", json={}).headers.get("X-Robots-Tag") == "noindex, nofollow"


def test_the_api_is_read_only(app, client, app_factory):
    # 1. routing: only the two session endpoints accept anything but GET (and they touch the cookie, never the database)
    writers = {}
    for rule in app.url_map.iter_rules():
        extra = set(rule.methods) - {"GET", "HEAD", "OPTIONS"}
        if extra and not rule.rule.startswith(("/api/projection/", "/api/priorities/")):         # the projection editor and the /priorities saved views + manual concepts write through their own read-write pool; the metrics API never does
            writers[rule.rule] = extra
    assert writers == {"/api/login": {"POST"}, "/api/logout": {"POST"},
                       "/api/priorities/views": {"POST"}, "/api/priorities/views/<token>": {"DELETE"}}   # saved priorities views write only their own table (own pool)
    for url in ("/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/tree", "/api/products/vita-bull/trend", "/api/ads/1000",
                "/api/status", "/media/1"):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(url, json={}).status_code == 405, (method, url)
    # 2. the database role the app uses is in a READ ONLY transaction: even a hand-written write is refused
    import psycopg
    from app import db
    with app.test_request_context():
        conn = db.get_conn()
        for sql in ("INSERT INTO app_meta (key, value) VALUES ('x', 'y')", "DELETE FROM ad_daily", "UPDATE products SET name = 'x'", "TRUNCATE ad_daily",
                    "CREATE TABLE evil (a int)"):
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute(sql)
            conn.rollback()
    # 3. open mode does not change that
    open_app = app_factory(AUTH_DISABLED=True)
    c = open_app.test_client()
    assert c.post("/api/products", json={}).status_code == 405 and c.delete("/api/ads/1000").status_code == 405
