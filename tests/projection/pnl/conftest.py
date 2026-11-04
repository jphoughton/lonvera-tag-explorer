"""Fixtures for the pnl module tests: own throwaway database (tagexp_proj_pnl_test), a logged-in client, fake upstream modules.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/pnl -q
Upstream modules (drivers, cohorts) are replaced by two small fake packages so the tests do not depend on other agents' modules or on the snapshot.
"""
from __future__ import annotations

import os
import shutil
import sys
import textwrap
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402
from app.projection.pnl import store                         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnl_support import DB_NAME as _DB_NAME, DB_URL as _DB_URL, PASSWORD as _PASSWORD, PG_BASE as _PG_BASE, TODAY as _TODAY      # noqa: E402
from pnl_support import login as _login                                                                                      # noqa: E402
from werkzeug.security import generate_password_hash                # noqa: E402
USERS = {name: generate_password_hash(_PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "130_proj_pnl.sql")


def _pnl_make_app(**overrides):
    """The app with the REAL projection package mounted (other test directories may leave registry.PACKAGE pointing at their own fake package)."""
    old = registry.PACKAGE
    registry.PACKAGE = "app.projection"
    registry._cache.pop("app.projection", None)
    try:
        return _pnl_build(**overrides)
    finally:
        registry.PACKAGE = old


def _pnl_build(**overrides):
    cfg = {"DATABASE_URL": _DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def pnl_db(tmp_path_factory):
    try:
        with psycopg.connect(f"{_PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{_DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{_DB_NAME}"')
            c.execute(f'ALTER DATABASE "{_DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for name in MIGRATIONS:
        shutil.copy(ROOT / "migrations" / name, d / name)
    from etl import migrate
    with psycopg.connect(_DB_URL) as conn:
        migrate.migrate(conn, d)
    yield _DB_URL
    rwdb.close_all()


FAKE_DRIVERS = '''
from datetime import date
from app.projection.interfaces import DriverResult, Module, MONTHS

class DriversResult(DriverResult):
    pass

def compute(scn, inputs):
    d = DriversResult(actual_through=date(2026, 7, 1))
    for m in MONTHS:
        d.nc_rev[m] = 300_000.0 if m >= date(2026, 8, 1) else 0.0
        d.amazon_rev[m] = 100_000.0
        d.tiktok_rev[m] = 0.0
        d.sales_tax[m] = 0.0
        d.spend_total[m] = 250_000.0
        d.spend_meta[m] = 225_000.0
        d.spend_google[m] = 12_500.0
        d.spend_amazon_ads[m] = 10_000.0
        d.spend_tiktok[m] = 2_500.0
    d.marketing_spend = dict(d.spend_total)
    d.spend_bing = {}
    return d

MODULE = Module(key="drivers", compute=compute, ui="drivers.js")
'''
FAKE_COHORTS = '''
from app.projection.interfaces import CohortResult, Module, MONTHS

def compute(scn, inputs, drivers=None):
    return CohortResult(repeat_rev={m: 500_000.0 for m in MONTHS}, lifetime_multiple=1.9)

MODULE = Module(key="cohorts", compute=compute, ui="cohorts.js")
'''


@pytest.fixture(scope="session")
def upstream_pkg(tmp_path_factory):
    """A package holding pnl (the real one) plus fake drivers and cohorts; registry.PACKAGE points at it for the tests that use `app`."""
    root = tmp_path_factory.mktemp("pnlpkg")
    name = "pnltest_" + uuid.uuid4().hex[:8]
    base = root / name
    (base / "pnl").mkdir(parents=True)
    (base / "drivers").mkdir()
    (base / "cohorts").mkdir()
    (base / "__init__.py").write_text("")
    (base / "pnl" / "__init__.py").write_text("from app.projection.pnl import MODULE\n")
    (base / "drivers" / "__init__.py").write_text(textwrap.dedent(FAKE_DRIVERS))
    (base / "cohorts" / "__init__.py").write_text(textwrap.dedent(FAKE_COHORTS))
    sys.path.insert(0, str(root))
    return name


@pytest.fixture(scope="session")
def app(pnl_db):
    a = _pnl_make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(pnl_db):
    made = []

    def factory(**overrides):
        a = _pnl_make_app(**overrides)
        made.append(a)
        return a

    yield factory
    for a in made:
        a.extensions["pool"].close()


@pytest.fixture()
def client(app, upstream_pkg, monkeypatch):
    monkeypatch.setattr(registry, "PACKAGE", upstream_pkg)
    monkeypatch.setattr(store, "today", lambda: _TODAY)
    return _login(app.test_client())


@pytest.fixture()
def anon(app, upstream_pkg, monkeypatch):
    monkeypatch.setattr(registry, "PACKAGE", upstream_pkg)
    return app.test_client()


@pytest.fixture()
def pg(pnl_db):
    from psycopg.rows import dict_row
    with psycopg.connect(pnl_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean(pnl_db):
    with psycopg.connect(pnl_db) as c:
        for t in ("proj_audit", "proj_actuals", "proj_scenario_history", "proj_series", "proj_pnl_drivers", "proj_pnl_settings", "proj_pnl_cogs_sku",
                  "proj_pnl_qb_map", "proj_pnl_vendors"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield
    registry._cache.clear()
