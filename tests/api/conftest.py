"""Fixtures for the API tests: a throwaway Postgres database seeded with tests/api/fixture_data.py (see seed_fixture.py)."""
from __future__ import annotations

import os

import psycopg
import pytest

from app import create_app

from . import seed_fixture as S

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_API_DB", "tagexp_apitest")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "correct horse battery"


@pytest.fixture(scope="session")
def fx():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", DB_NAME)
        with psycopg.connect(DB_URL) as conn:
            S.apply_migrations(conn)
            return S.seed(conn)
    except psycopg.OperationalError as e:       # no local Postgres -> skip the whole suite instead of erroring
        pytest.skip(f"Postgres not available for API tests: {e}")


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_PASSWORD": PASSWORD, "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "LOGIN_MAX_FAILS": 5, "DB_POOL_MAX": 4}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def app(fx):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(fx):
    """Build extra apps (own login throttle / cookie policy) and close their pools afterwards."""
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
    c = app.test_client()
    r = c.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 204
    return c


@pytest.fixture(scope="session")
def pg(fx):
    """Direct (plain) connection for assertions about the database itself."""
    with psycopg.connect(DB_URL) as conn:
        yield conn
