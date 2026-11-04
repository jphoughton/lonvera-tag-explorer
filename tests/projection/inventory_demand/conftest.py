"""Fixtures for the inventory_demand tests: own throwaway database (tagexp_proj_inventory_demand_test), only this module registered, helpers.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/inventory_demand -q
"""
from __future__ import annotations

import os
import shutil
import sys
import textwrap
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_INVENTORY_DEMAND_DB", "tagexp_proj_inventory_demand_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
from werkzeug.security import generate_password_hash                # noqa: E402
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "140_proj_inventory_demand.sql")


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session", autouse=True)
def only_this_module(tmp_path_factory):
    """Register only inventory_demand: the other modules' tables are not in this database."""
    root = tmp_path_factory.mktemp("pkg")
    name = "idtest_" + uuid.uuid4().hex[:8]
    base = root / name / "inventory_demand"
    base.mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (base / "__init__.py").write_text(textwrap.dedent("from app.projection.inventory_demand import MODULE\n"))
    sys.path.insert(0, str(root))
    mp = pytest.MonkeyPatch()
    mp.setattr(registry, "PACKAGE", name)
    registry._cache.clear()
    yield name
    mp.undo()
    registry._cache.clear()


@pytest.fixture(scope="session")
def core_db(tmp_path_factory, only_this_module):
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for name in MIGRATIONS:
        shutil.copy(ROOT / "migrations" / name, d / name)
    from etl import migrate
    with psycopg.connect(DB_URL) as conn:
        migrate.migrate(conn, d)
    yield DB_URL
    rwdb.close_all()


@pytest.fixture(scope="session")
def app(core_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(core_db):
    made = []

    def factory(**overrides):
        a = make_app(**overrides)
        made.append(a)
        return a

    yield factory
    for a in made:
        a.extensions["pool"].close()


def login(client, username: str = "JoshHoughton"):
    r = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 204
    return client


@pytest.fixture()
def anon(app):
    return app.test_client()


@pytest.fixture()
def client(app):
    return login(app.test_client())


@pytest.fixture()
def pg(core_db):
    with psycopg.connect(core_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture()
def conn(core_db):
    """A dict-row connection like the daily runner's; tests commit through the code under test."""
    with psycopg.connect(core_db, row_factory=dict_row) as c:
        yield c


@pytest.fixture(autouse=True)
def clean(core_db, monkeypatch):
    monkeypatch.delenv("PROJECTION_TODAY", raising=False)
    with psycopg.connect(core_db) as c:
        for t in ("proj_audit", "proj_actuals", "proj_scenario_history", "proj_series", "proj_units_daily", "proj_units_monthly",
                  "proj_units_country", "proj_units_loads", "proj_demand_snapshot"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield
