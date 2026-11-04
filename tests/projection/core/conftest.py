"""Fixtures for the projection core tests: own throwaway database (tagexp_proj_core), a logged-in client, fake module packages.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/core -q
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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_CORE_DB", "tagexp_proj_core")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
from werkzeug.security import generate_password_hash                # noqa: E402
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}   # cheap scrypt params keep test logins fast
BASE_MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql")


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def core_db(tmp_path_factory):
    """Fresh database with the Tag Explorer base migrations + 100_proj_core (other modules' migrations are not needed here)."""
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')       # throwaway data: skip the per-commit fsync
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for name in BASE_MIGRATIONS:
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
    """Plain autocommit connection with dict rows, for assertions about the database itself."""
    from psycopg.rows import dict_row
    with psycopg.connect(core_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean(core_db):
    """Every test starts from the seeded database: only 'current', no series, no audit, no actuals."""
    with psycopg.connect(core_db) as c:                       # one transaction: a single commit (fsync) per test
        c.execute("DELETE FROM proj_audit")
        c.execute("DELETE FROM proj_refresh_runs")
        c.execute("DELETE FROM proj_actuals")
        c.execute("DELETE FROM proj_scenario_history")
        c.execute("DELETE FROM proj_series")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield


# ------------------------------------------------------------------------------------------------------------ fake module packages

def write_pkg(root: Path, files: dict[str, str]) -> str:
    """Create a uniquely named package under `root` from {relative path: source}; returns the package name."""
    name = "fakeproj_" + uuid.uuid4().hex[:8]
    base = root / name
    base.mkdir(parents=True)
    (base / "__init__.py").write_text("")
    for rel, src in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return name


@pytest.fixture()
def pkg_factory(tmp_path, monkeypatch):
    """pkg_factory({'good/__init__.py': src, ...}) -> package name, and points registry.PACKAGE at it for the test."""
    def make(files: dict[str, str], activate: bool = True) -> str:
        name = write_pkg(tmp_path, files)
        if activate:
            monkeypatch.setattr(registry, "PACKAGE", name)
        return name
    yield make
    registry._cache.clear()


EMPTY_PKG: dict[str, str] = {}

DRIVERS_MODULE = """
from app.projection.interfaces import DriverResult, Module
from datetime import date

def load_inputs(conn, scn):
    return {"slug": scn.slug}

def compute(scn, inputs):
    nc = {date(2026, 11, 1): 100.0, date(2026, 12, 1): 250.0}
    return DriverResult(nc_rev=nc, spend_total={date(2026, 11, 1): 50.0}, actual_through=date(2026, 10, 1))

MODULE = Module(key="drivers", load_inputs=load_inputs, compute=compute, ui="drivers.js")
"""

COHORTS_MODULE = """
from app.projection.interfaces import CohortResult, Module

SEEN = {}

def compute(scn, inputs, drivers=None, nc_rev=None):
    SEEN["drivers"] = drivers
    SEEN["nc_rev"] = nc_rev
    return CohortResult(curve_live=[0.5, 0.25], lifetime_multiple=0.75, repeat_rev={})

MODULE = Module(key="cohorts", compute=compute)
"""

PNL_MODULE = """
from app.projection.interfaces import PnlResult, Module

SEEN = {}

def load_inputs(conn):
    return "inputs-without-scenario"

def compute(scn, inputs, **upstream):
    SEEN.update(upstream)
    SEEN["inputs"] = inputs
    return PnlResult(lines={"noi": {}})

MODULE = Module(key="pnl", load_inputs=load_inputs, compute=compute)
"""
