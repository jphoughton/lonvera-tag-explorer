"""Fixtures for the brain tests: a small fake repo (schema.json, docs, questions) for the offline tools, and a throwaway migrated Postgres for the /mcp endpoint."""
from __future__ import annotations

import glob
import json

import psycopg
import pytest
from brain_helpers import DB_NAME, DB_URL, PG_BASE, ROOT, SCHEMA, login, make_app  # noqa: F401


@pytest.fixture()
def fake_root(tmp_path):
    (tmp_path / "docs" / "questions").mkdir(parents=True)
    (tmp_path / "docs" / "schema.json").write_text(json.dumps(SCHEMA))
    (tmp_path / "docs" / "GUIDE.md").write_text("# Guide\n\nIntro text.\n\n## The five rules\n\nName the basis of every ratio. Hit rate threshold is five percent of spend.\n\n## Other\n\nUnrelated gardening notes.\n")
    (tmp_path / "docs" / "DATA_SCHEMA.md").write_text("# duplicate dictionary\n\nzebra zebra zebra\n")
    (tmp_path / "docs" / "app_map.json").write_text(json.dumps({"services": [{"name": "web"}], "routes": [{"rule": "/mcp"}], "pages": [], "etl_jobs": []}))
    items = [{"id": 7, "date": "2026-10-23", "verbatim": "Why did repeat revenue fall last week?", "answer": "Billing reminder flow, not timing. Consistent with the Sep 14 scale-up.", "status": "answered"},
             {"id": 8, "asked_at": "2026-10-01", "question": "What is the Boneflex hit rate?", "response": "Five percent.", "state": "open"}]
    (tmp_path / "docs" / "questions" / "lonvera_items.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")
    return tmp_path


@pytest.fixture()
def bare_root(tmp_path_factory):
    """Only today's schema.json: no metrics, no questions, no app map."""
    tmp_path = tmp_path_factory.mktemp("bare")
    (tmp_path / "docs").mkdir()
    s = {k: v for k, v in SCHEMA.items() if k != "metrics"}
    s["relations"] = [{k: v for k, v in r.items() if k != "domain"} for r in SCHEMA["relations"]]
    (tmp_path / "docs" / "schema.json").write_text(json.dumps(s))
    return tmp_path


@pytest.fixture(autouse=True)
def no_reader_env(monkeypatch):
    for k in ("ANALYTICS_READER_URL", "LONVERA_ANALYTICS_URL_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LONVERA_ANALYTICS_URL_FILE", "/nonexistent/analytics_reader_url.txt")


@pytest.fixture(scope="session")
def migrated_db():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
        with psycopg.connect(DB_URL) as conn:
            for path in sorted(glob.glob(str(ROOT / "migrations" / "*.sql")) + [str(ROOT / "tests" / "api" / "schema_shim.sql")]):
                conn.execute(open(path).read())
            conn.commit()
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for the brain tests: {e}")
    return DB_URL


@pytest.fixture(scope="session")
def app(migrated_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(migrated_db):
    made = []

    def f(**o):
        a = make_app(**o)
        made.append(a)
        return a

    yield f
    for a in made:
        a.extensions["pool"].close()


@pytest.fixture(scope="session")
def pg(migrated_db):
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean_tables(request):
    """Fresh keys / audit rows for tests that use the database; resets the in-process throttles of the shared app."""
    if "app" not in request.fixturenames and "app_factory" not in request.fixturenames:
        yield
        return
    pg = request.getfixturevalue("pg")
    pg.execute("DELETE FROM brain_audit")
    pg.execute("DELETE FROM brain_keys")
    pg.execute("DELETE FROM api_keys")
    app = request.getfixturevalue("app")
    for k in ("brain_throttle", "brain_key_throttle"):
        app.extensions.pop(k, None)
    app.extensions["apikey_throttle_ip"]._fails.clear()
    yield


@pytest.fixture()
def anon(app):
    return app.test_client()


@pytest.fixture()
def client(app):
    return login(app)
