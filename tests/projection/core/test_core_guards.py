"""Auth follows the app (no projection auth), plus the password-free guard rails: Origin, JSON only, size cap, rate limits, noindex."""
from __future__ import annotations

import io

import pytest

from app.projection.interfaces import MODULE_CATALOG
from conftest import login, make_app
from core_helpers import P, create, item, put

GOOD_MODULE = {
    "good/__init__.py": """
        from flask import Blueprint, jsonify, request
        from app.projection.interfaces import Module
        bp = Blueprint("proj_good", __name__)

        @bp.get("/ping")
        def ping():
            return jsonify(ok=True)

        @bp.post("/echo")
        def echo():
            return jsonify(got=request.get_json(silent=True))

        @bp.post("/upload")
        def upload():
            return jsonify(files=sorted(request.files))

        MODULE = Module(key="good", blueprint=bp, ui="good.js")
    """,
}

ROUTES = [("get", "/modules"), ("get", "/scenarios"), ("post", "/scenarios"), ("patch", "/scenarios/current"), ("delete", "/scenarios/current"),
          ("post", "/scenarios/current/restore"), ("post", "/scenarios/current/clone"), ("get", "/scenarios/current/history"),
          ("get", "/series?keys=a"), ("put", "/series"), ("get", "/audit"), ("get", "/run")]


# ------------------------------------------------------------------------------------------------------------ auth follows the app

@pytest.mark.parametrize("method,path", ROUTES)
def test_app_login_on_means_401_without_a_session(anon, method, path):
    r = getattr(anon, method)(P + path, json={})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_logged_in_reaches_every_route(client):
    for method, path in ROUTES:
        assert getattr(client, method)(P + path, json={}).status_code != 401


def test_open_when_the_apps_auth_is_off(app_factory, monkeypatch):
    """Projection has no auth of its own: with the app's guard off the same requests work without a cookie.
    The AUTH_DISABLED flag lives in newer app/auth.py; on this branch the guard is stubbed, which is the same code path (is_authenticated)."""
    from app import auth
    if hasattr(auth, "auth_disabled"):
        a = app_factory(AUTH_DISABLED=True)
    else:
        a = app_factory()
        monkeypatch.setattr(auth, "is_authenticated", lambda: True)
    c = a.test_client()
    assert c.get(f"{P}/modules").status_code == 200
    assert c.post(f"{P}/scenarios", json={"name": "Open"}).status_code == 201
    assert put(c, "open", item()).status_code == 200


def test_no_login_logout_or_session_endpoints_of_its_own(client):
    for path in ("/login", "/logout", "/session"):
        assert client.get(P + path).status_code in (404, 405)
        assert client.post(P + path, json={}).status_code in (404, 405)


def test_module_endpoint_is_guarded_with_no_code_in_the_module(app_factory, pkg_factory):
    pkg_factory(GOOD_MODULE)
    a = app_factory()
    anon, c = a.test_client(), login(a.test_client())
    assert anon.get(f"{P}/good/ping").status_code == 401                    # app guard
    assert anon.post(f"{P}/good/echo", json={"x": 1}).status_code == 401
    assert c.get(f"{P}/good/ping").get_json() == {"ok": True}
    assert c.post(f"{P}/good/echo", json={"x": 1}).get_json() == {"got": {"x": 1}}
    # the projection write guard covers the module's endpoint too
    assert c.post(f"{P}/good/echo", json={"x": 1}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(f"{P}/good/echo", data="x=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert c.get(f"{P}/good/ping").headers["X-Robots-Tag"] == "noindex, nofollow"
    assert [m["key"] for m in c.get(f"{P}/modules").get_json()["modules"]] == list(MODULE_CATALOG)       # the listing is the seven catalog modules only


# ------------------------------------------------------------------------------------------------------------ Origin / content type / size

MUTATIONS = [("post", "/scenarios", {"name": "N"}), ("patch", "/scenarios/current", {"name": "N"}), ("put", "/series", {"scenario": "current", "items": [item()]}),
             ("delete", "/scenarios/current", {"confirm": "x"}), ("post", "/scenarios/current/restore", {"version": 1})]


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_cross_site_origin_is_rejected_on_every_write(client, method, path, body):
    r = getattr(client, method)(P + path, json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.get_json() == {"error": "origin_mismatch"}
    r = getattr(client, method)(P + path, json=body, headers={"Origin": "null"})
    assert r.status_code == 403


def test_matching_or_absent_origin_is_allowed_and_reads_are_not_checked(client):
    assert client.post(f"{P}/scenarios", json={"name": "A"}, headers={"Origin": "http://localhost"}).status_code == 201
    assert client.post(f"{P}/scenarios", json={"name": "B"}).status_code == 201
    assert client.get(f"{P}/scenarios", headers={"Origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_json_only(client, method):
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None):
        kw = {"data": "{}"} | ({"content_type": ctype} if ctype else {})
        r = getattr(client, method)(f"{P}/series", **kw)
        assert r.status_code == 415 and r.get_json() == {"error": "json_required"}, (method, ctype)
    r = getattr(client, method)(f"{P}/series", data="{}", content_type="application/json; charset=utf-8")
    assert r.status_code != 415


def test_delete_with_a_non_json_body_is_rejected(client):
    assert client.delete(f"{P}/scenarios/current", data="confirm=current", content_type="text/plain").status_code == 415


def test_body_cap_is_1mb_not_the_apps_64kb(client):
    ok = {"scenario": "current", "items": [item(note="x" * 400)], "pad": "y" * 200_000}         # 200 KB: over the app cap, under ours
    assert client.put(f"{P}/series", json=ok).status_code == 200
    big = {"scenario": "current", "items": [item()], "pad": "y" * 1_100_000}
    r = client.put(f"{P}/series", json=big)
    assert r.status_code == 413 and "error" in r.get_json()


# ------------------------------------------------------------------------------------------------------------ rate limits

def test_mutation_rate_limit_per_ip(app_factory):
    a = app_factory(PROJ_RATE_MUTATIONS_PER_MIN=4)
    c = login(a.test_client())
    for i in range(4):
        assert c.post(f"{P}/scenarios", json={"name": f"S{i}"}).status_code == 201
    r = c.post(f"{P}/scenarios", json={"name": "S9"})
    assert r.status_code == 429 and r.get_json() == {"error": "rate_limited"} and int(r.headers["Retry-After"]) >= 1
    assert c.get(f"{P}/scenarios").status_code == 200                       # reads are not limited
    assert c.put(f"{P}/series", json={"scenario": "current", "items": [item()]}).status_code == 429      # same budget for every write


def test_rate_limit_counts_per_client_ip(app_factory):
    a = app_factory(PROJ_RATE_MUTATIONS_PER_MIN=2)
    c = login(a.test_client())
    assert c.post(f"{P}/scenarios", json={"name": "A"}).status_code == 201
    assert c.post(f"{P}/scenarios", json={"name": "B"}).status_code == 201
    assert c.post(f"{P}/scenarios", json={"name": "C"}).status_code == 429
    other = a.test_client()
    other.post("/api/login", json={"password": "throwaway-test-password"})
    assert other.post(f"{P}/scenarios", json={"name": "C"}, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).status_code == 201


def test_upload_rate_limit_and_multipart_only_on_upload_paths(app_factory, pkg_factory):
    pkg_factory(GOOD_MODULE)
    a = app_factory(PROJ_RATE_UPLOADS_PER_HOUR=2)
    c = login(a.test_client())

    def up():
        return c.post(f"{P}/good/upload", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}, content_type="multipart/form-data")

    assert up().get_json() == {"files": ["file"]}
    assert up().status_code == 200
    r = up()
    assert r.status_code == 429 and r.get_json() == {"error": "rate_limited"}
    # multipart anywhere else is refused
    r = c.post(f"{P}/scenarios", data={"name": "x"}, content_type="multipart/form-data")
    assert r.status_code == 415


def test_upload_body_cap_is_8mb(app_factory, pkg_factory):
    pkg_factory(GOOD_MODULE)
    c = login(app_factory().test_client())
    ok = c.post(f"{P}/good/upload", data={"file": (io.BytesIO(b"x" * 3_000_000), "x.csv")}, content_type="multipart/form-data")
    assert ok.status_code == 200
    big = c.post(f"{P}/good/upload", data={"file": (io.BytesIO(b"x" * 9_000_000), "x.csv")}, content_type="multipart/form-data")
    assert big.status_code == 413


# ------------------------------------------------------------------------------------------------------------ noindex

def test_noindex_headers_on_pages_and_api(client, anon):
    for c, path in ((client, f"{P}/modules"), (anon, f"{P}/modules"), (client, "/projection"), (client, "/projection/cohorts"), (anon, "/projection")):
        r = c.get(path)
        assert r.headers.get("X-Robots-Tag") == "noindex, nofollow", path
    assert "X-Robots-Tag" not in client.get("/healthz").headers
    assert "X-Robots-Tag" not in client.get("/api/status").headers


def test_pages_serve_the_spa_shell_without_login(anon):
    for path in ("/projection", "/projection/cohorts", "/projection/inventory-demand"):
        r = anon.get(path)
        assert r.status_code == 200 and r.mimetype == "text/html" and b"<title>" in r.data


def test_robots_txt_fallback_only_when_the_app_has_no_route(anon, app_factory):
    r = anon.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /projection" in r.get_data(as_text=True) and "Disallow: /api/projection" in r.get_data(as_text=True)
    a = app_factory()
    a.add_url_rule("/robots.txt", endpoint="robots_own", view_func=lambda: ("User-agent: *\nDisallow: /", 200, {"Content-Type": "text/plain"}))
    assert a.test_client().get("/robots.txt").get_data(as_text=True) == "User-agent: *\nDisallow: /"       # an existing route is left alone
