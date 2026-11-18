"""Theme previews API (app/theme_api.py): the three gates, the hard safety rules, the audit trail, the token never leaking, and no publish / delete in the source.
The Shopify client is replaced at ONE seam (app.shopify_theme.client) by FakeShopify; nothing here can reach Shopify."""
import hashlib
import logging
import re
from pathlib import Path

import pytest

from app import api as api_mod
from app import shopify_theme as st

TOKEN = "shpat_SECRETTOKEN123456"
ROOT = Path(__file__).resolve().parents[2]


class FakeShopify:
    def __init__(self):
        self.themes = {"1": {"id": "1", "name": "Live", "role": "MAIN"}}
        self.files = {}
        self.upserts = []
        self.fail = None

    def get_theme(self, tid):
        return self.themes.get(str(tid))

    def get_live_theme(self):
        return self.themes["1"]

    def duplicate_theme(self, source, name):
        if self.fail:
            raise st.ShopifyError(self.fail)
        n = str(100 + len(self.themes))
        self.themes[n] = {"id": n, "name": name, "role": "UNPUBLISHED"}
        self.files[n] = {"sections/header.liquid": "<header></header>", "config/settings_data.json": "{}"}
        return self.themes[n]

    def list_files(self, tid):
        return [{"key": k, "size": len(v)} for k, v in self.files.get(str(tid), {}).items()]

    def read_file(self, tid, key):
        return self.files.get(str(tid), {}).get(key)

    def upsert_file(self, tid, key, content):
        self.upserts.append((str(tid), key, content))
        self.files.setdefault(str(tid), {})[key] = content


@pytest.fixture(autouse=True)
def clean(pg, app):
    for t in ("theme_edit_audit", "theme_previews"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()
    api_mod._theme_limiter = None
    yield
    for t in ("theme_edit_audit", "theme_previews"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv("THEME_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("THEME_EDITORS", "joshhoughton")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "example-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_THEME_TOKEN", TOKEN)


@pytest.fixture()
def fake(monkeypatch, on):
    f = FakeShopify()
    monkeypatch.setattr(st, "client", lambda: f)
    return f


ROUTES = [("get", "/api/theme/themes"), ("post", "/api/theme/previews"), ("get", "/api/theme/previews/100/files"), ("put", "/api/theme/previews/100/files"), ("get", "/api/theme/status")]


def mk(client, name="Bigger button"):
    r = client.post("/api/theme/previews", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_flag_off_is_404_and_status_key_says_disabled(client, monkeypatch):
    monkeypatch.delenv("THEME_PREVIEW_ENABLED", raising=False)
    monkeypatch.setenv("THEME_EDITORS", "joshhoughton")
    for m, u in ROUTES:
        r = getattr(client, m)(u, json={})
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, (m, u)
    assert client.get("/api/status").get_json()["theme"] == {"enabled": False, "can_edit": False}


def test_auth_disabled_is_403(app_factory, monkeypatch):
    monkeypatch.setenv("THEME_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("THEME_EDITORS", "guest")
    c = app_factory(AUTH_DISABLED=True).test_client()
    for m, u in ROUTES[:4]:
        r = getattr(c, m)(u, json={})
        assert r.status_code == 403 and r.get_json() == {"error": "Theme editing needs sign-in to be on."}, (m, u)


def test_non_editor_is_403_and_status_shows_cannot_edit(client, monkeypatch):
    monkeypatch.setenv("THEME_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("THEME_EDITORS", "someoneelse")
    for m, u in ROUTES[:4]:
        r = getattr(client, m)(u, json={})
        assert r.status_code == 403 and r.get_json() == {"error": "Your login is not allowed to edit themes."}, (m, u)
    assert client.get("/api/theme/status").get_json() == {"enabled": True, "can_edit": False}


def test_requires_login(app, on):
    r = app.test_client().get("/api/theme/themes")
    assert r.status_code == 401
    assert not re.search("theme", __import__("app.auth", fromlist=["x"]).PUBLIC_API_RE.pattern)


def test_create_preview_names_records_and_audits(client, fake, pg):
    p = mk(client, "Bigger Buy Button!")
    assert re.fullmatch(r"preview-joshhoughton-\d{8}-bigger-buy-button", p["name"])
    assert p["preview_url"] == f"https://example-store.myshopify.com/?preview_theme_id={p['id']}"
    assert pg.execute("SELECT count(*) FROM theme_previews WHERE shopify_theme_id = %s", (p["id"],)).fetchone()[0] == 1
    assert pg.execute("SELECT action, user_login FROM theme_edit_audit").fetchall() == [("create_preview", "JoshHoughton")]
    lst = client.get("/api/theme/themes").get_json()
    assert [x["id"] for x in lst["previews"]] == [p["id"]] and lst["live"] == {"id": "1", "name": "Live", "role": "MAIN"}


def test_save_writes_and_audit_row_has_hash(client, fake, pg):
    p = mk(client)
    r = client.put(f"/api/theme/previews/{p['id']}/files", json={"key": "sections/header.liquid", "content": "<b>hi</b>"})
    assert r.status_code == 200
    assert fake.upserts == [(p["id"], "sections/header.liquid", "<b>hi</b>")]
    row = pg.execute("SELECT action, user_login, file_key, content_sha256, size_bytes FROM theme_edit_audit WHERE action = 'save_file'").fetchone()
    assert row == ("save_file", "JoshHoughton", "sections/header.liquid", hashlib.sha256(b"<b>hi</b>").hexdigest(), 9)
    assert client.get(f"/api/theme/previews/{p['id']}/files", query_string={"key": "sections/header.liquid"}).get_json()["content"] == "<b>hi</b>"
    keys = [f["key"] for f in client.get(f"/api/theme/previews/{p['id']}/files").get_json()["files"]]
    assert "config/settings_data.json" not in keys and "sections/header.liquid" in keys


def test_live_theme_write_refused_even_if_recorded_ours(client, fake, pg):
    p = mk(client)
    fake.themes[p["id"]]["role"] = "MAIN"                         # Shopify now says it is live (someone published it in admin)
    r = client.put(f"/api/theme/previews/{p['id']}/files", json={"key": "sections/a.liquid", "content": "x"})
    assert r.status_code == 409 and r.get_json() == {"error": "That is the live theme. Live changes are made in Shopify admin."}
    assert fake.upserts == []
    assert pg.execute("SELECT count(*) FROM theme_edit_audit WHERE action = 'save_file'").fetchone()[0] == 0


def test_non_app_theme_write_refused(client, fake):
    fake.themes["555"] = {"id": "555", "name": "Someone else's", "role": "UNPUBLISHED"}
    r = client.put("/api/theme/previews/555/files", json={"key": "sections/a.liquid", "content": "x"})
    assert r.status_code == 409 and fake.upserts == []
    r = client.put("/api/theme/previews/1/files", json={"key": "sections/a.liquid", "content": "x"})
    assert r.status_code == 409 and fake.upserts == []


@pytest.mark.parametrize("key", ["config/settings_data.json", "sections/a.png", "../sections/a.liquid", "/sections/a.liquid", "sections/../config/x.json", "assets/a.liquid.exe", "locales/en.json", "", None, 5])
def test_bad_path_refused(client, fake, key):
    p = mk(client)
    r = client.put(f"/api/theme/previews/{p['id']}/files", json={"key": key, "content": "x"})
    assert r.status_code == 400 and fake.upserts == []


def test_oversize_refused_and_exact_limit_ok(client, fake):
    p = mk(client)
    big = client.put(f"/api/theme/previews/{p['id']}/files", json={"key": "assets/a.js", "content": "a" * 1_000_001})
    assert big.status_code == 400 and fake.upserts == []
    ok = client.put(f"/api/theme/previews/{p['id']}/files", json={"key": "assets/a.js", "content": "a" * 1_000_000})
    assert ok.status_code == 200


def test_token_never_in_responses_or_logs(client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    p = mk(client)
    fake.fail = f"boom {TOKEN}"
    bodies = [client.get("/api/theme/themes").get_data(as_text=True), client.get("/api/theme/status").get_data(as_text=True), client.get("/api/status").get_data(as_text=True),
              client.put(f"/api/theme/previews/{p['id']}/files", json={"key": "assets/a.css", "content": "x"}).get_data(as_text=True), str(p)]
    r = client.post("/api/theme/previews", json={"name": "again"})
    assert r.status_code == 502
    assert TOKEN not in r.get_data(as_text=True)
    assert all(TOKEN not in b for b in bodies) and TOKEN not in caplog.text


def test_scrub_removes_token(monkeypatch):
    monkeypatch.setenv("SHOPIFY_THEME_TOKEN", TOKEN)
    assert TOKEN not in st.scrub(f"bad {TOKEN} and shpat_other123")
    assert "shpat_other123" not in st.scrub("shpat_other123")


def test_no_publish_or_delete_in_source():
    for f in ("app/shopify_theme.py", "app/theme_api.py", "app/static/js/pages/theme_previews.js"):
        src = (ROOT / f).read_text()
        body = src.split('"""', 2)[2] if f.endswith(".py") else src               # the module docstring may say what it lacks
        assert not re.search(r"themePublish|themeDelete|themeFilesDelete|\.delete\(", body), f


def test_theme_routes_are_exactly_these(app):
    got = {(r.rule, m) for r in app.url_map.iter_rules() if r.rule.startswith("/api/theme") for m in set(r.methods) - {"HEAD", "OPTIONS"}}
    assert got == {("/api/theme/status", "GET"), ("/api/theme/themes", "GET"), ("/api/theme/previews", "POST"), ("/api/theme/previews/<pid>/files", "GET"), ("/api/theme/previews/<pid>/files", "PUT")}
