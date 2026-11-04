import pytest

from .conftest import PASSWORD

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


def test_login_bad_and_good(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=50)
    c = a.test_client()
    for body in ({"password": "nope"}, {"password": ""}, {}, {"password": 123}, {"password": None}, {"pw": PASSWORD}):
        r = c.post("/api/login", json=body)
        assert r.status_code == 401, body
    assert c.post("/api/login", data="password=" + PASSWORD).status_code == 401      # not JSON
    assert c.get("/api/status").status_code == 401
    r = c.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 204 and r.data == b""
    assert c.get("/api/status").status_code == 200
    assert c.post("/api/logout").status_code == 204
    assert c.get("/api/status").status_code == 401


def test_cookie_flags_prod_and_dev(app_factory):
    prod = app_factory(DEV_INSECURE_COOKIES=False, SECRET_KEY="another-secret")
    r = prod.test_client().post("/api/login", json={"password": PASSWORD})
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    assert "Max-Age" in cookie or "Expires" in cookie                      # permanent session
    dev = app_factory(DEV_INSECURE_COOKIES=True)
    cookie = dev.test_client().post("/api/login", json={"password": PASSWORD}).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie


def test_failed_login_throttle(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=3)
    c = a.test_client()
    for _ in range(3):
        assert c.post("/api/login", json={"password": "bad"}).status_code == 401
    r = c.post("/api/login", json={"password": PASSWORD})          # even the right password is refused while throttled
    assert r.status_code == 429
    assert r.get_json()["error"] == "too_many_attempts"
    assert int(r.headers["Retry-After"]) > 0
    assert c.get("/api/status").status_code == 401
    # a different client address is not throttled
    other = a.test_client()
    assert other.post("/api/login", json={"password": PASSWORD}, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).status_code == 204


def test_success_resets_throttle(app_factory):
    a = app_factory(LOGIN_MAX_FAILS=3)
    c = a.test_client()
    for _ in range(2):
        c.post("/api/login", json={"password": "bad"})
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 204
    for _ in range(2):
        assert c.post("/api/login", json={"password": "bad"}).status_code == 401      # counter restarted from zero


def test_tampered_or_stale_session_rejected(app_factory, client):
    # cookie signed with another secret / for another password is not accepted
    cookie = client.get_cookie("tagexp_session").value
    other_secret = app_factory(SECRET_KEY="different-secret").test_client()
    other_secret.set_cookie("tagexp_session", cookie)
    assert other_secret.get("/api/status").status_code == 401
    other_pw = app_factory(APP_PASSWORD="a different password", SECRET_KEY="test-secret-key").test_client()
    other_pw.set_cookie("tagexp_session", cookie)
    assert other_pw.get("/api/status").status_code == 401         # rotating APP_PASSWORD signs everyone out
    junk = app_factory().test_client()
    junk.set_cookie("tagexp_session", cookie[:-3] + "xyz")
    assert junk.get("/api/status").status_code == 401


def test_no_password_configured_fails_closed(app_factory):
    a = app_factory(APP_PASSWORD="")
    c = a.test_client()
    assert c.post("/api/login", json={"password": ""}).status_code == 503
    assert c.get("/api/status").status_code == 401


def test_secret_key_required():
    from app import create_app
    with pytest.raises(RuntimeError):
        create_app({"DATABASE_URL": "postgresql://localhost/x", "SECRET_KEY": "", "DEV_INSECURE_COOKIES": False})


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
