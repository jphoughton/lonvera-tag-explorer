"""Fixtures for the financing tests: own throwaway database (tagexp_proj_financing) holding the base migrations, the projection core and
migration 160 (so every test starts from the seeded Current plan), a logged-in client, and the snapshot as upstream.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/financing -q
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import psycopg
import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app                                   # noqa: E402
from app.projection import rwdb                              # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_FINANCING_DB", "tagexp_proj_financing")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "160_proj_financing.sql")


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def fin_db(tmp_path_factory):
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for name in MIGRATIONS:                                     # only the migrations this module needs: other agents' files are still changing
        shutil.copy(ROOT / "migrations" / name, d / name)
    from etl import migrate
    with psycopg.connect(DB_URL) as conn:
        migrate.migrate(conn, d)
    yield DB_URL
    rwdb.close_all()


@pytest.fixture(scope="session")
def app(fin_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


def login(client, username: str = "JoshHoughton"):
    r = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 204
    return client


@pytest.fixture()
def app_factory(fin_db):
    made = []

    def factory(**overrides):
        a = make_app(**overrides)
        made.append(a)
        return a

    yield factory
    for a in made:
        a.extensions["pool"].close()


@pytest.fixture()
def anon(app):
    return app.test_client()


@pytest.fixture()
def client(app):
    return login(app.test_client())


@pytest.fixture()
def pg(fin_db):
    from psycopg.rows import dict_row
    with psycopg.connect(fin_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def reset(db_url: str, seed: bool = True) -> None:
    from etl.projection_financing import apply_seed
    with psycopg.connect(db_url) as c:
        c.execute("DELETE FROM proj_audit")
        c.execute("DELETE FROM proj_actuals")
        c.execute("DELETE FROM proj_scenario_history")
        c.execute("DELETE FROM proj_series")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
        if seed:
            apply_seed(c)


@pytest.fixture(autouse=True)
def clean(fin_db):
    """Every test starts from the seeded Current plan (the state migration 160 leaves)."""
    reset(fin_db)
    yield


@pytest.fixture()
def bare(fin_db):
    """The Current plan with no seed at all: empty params, no series."""
    reset(fin_db, seed=False)
    yield


@pytest.fixture()
def dconn(fin_db):
    from psycopg.rows import dict_row
    with psycopg.connect(fin_db, row_factory=dict_row) as conn:
        yield conn
