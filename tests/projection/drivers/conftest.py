"""Fixtures for the drivers tests: own throwaway database (tagexp_proj_drivers_test) with the base migrations, core, cohorts (for proj_cohorts) and drivers.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/drivers -q
Other agents' migrations are never applied here: a temp folder with only the files below is handed to the migration runner.
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402
from app.projection.drivers import loaders                   # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_DRIVERS_DB", "tagexp_proj_drivers_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
from werkzeug.security import generate_password_hash                # noqa: E402
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "110_proj_cohorts.sql",
              "120_proj_drivers.sql")
TODAY = date(2026, 11, 3)
SNAPSHOT_OK = (Path("~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03/_manifest.json").expanduser()).exists()


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def drv_db(tmp_path_factory):
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
def app(drv_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(drv_db):
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
def pg(drv_db):
    """Autocommit connection with dict rows for assertions and direct inserts."""
    with psycopg.connect(drv_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture()
def rw(drv_db):
    """Transactional dict-row connection for seeders and hooks; commits on exit."""
    with psycopg.connect(drv_db, row_factory=dict_row) as conn:
        yield conn
        conn.commit()


@pytest.fixture(autouse=True)
def clean(drv_db):
    with psycopg.connect(drv_db) as c:
        for t in ("proj_audit", "proj_actuals", "proj_scenario_history", "proj_series", "proj_cohort_cum", "proj_cohorts", "ad_daily"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """The business calendar says 2026-11-03 in every test."""
    monkeypatch.setattr(loaders, "current_date", lambda: TODAY)
    yield TODAY


@pytest.fixture()
def drivers_only(tmp_path, monkeypatch):
    """A package that exposes only the real drivers module, so the engine uses fixture stubs for every other module."""
    name = "fakedrv_" + uuid.uuid4().hex[:8]
    base = tmp_path / name
    (base / "drivers").mkdir(parents=True)
    (base / "__init__.py").write_text("")
    (base / "drivers" / "__init__.py").write_text("from app.projection.drivers import MODULE\n")
    sys.path.insert(0, str(tmp_path))
    yield name
    registry._cache.pop(name, None)
    sys.path.remove(str(tmp_path))


needs_snapshot = pytest.mark.skipif(not SNAPSHOT_OK, reason="sheet snapshot not available")


# ------------------------------------------------------------------------------------------------------------ data helpers

def put_cells(conn, key: str, cells: dict, slug: str = "current", kind: str = "input", note: str | None = None):
    """Write scenario cells straight through the core series API (audited, versioned)."""
    from app.projection import series
    items = series.parse_items([{"line_key": key, "month": m.isoformat(), "value": v, "kind": kind, **({"note": note} if note else {})}
                                for m, v in cells.items()])
    out = series.apply_items(conn, slug, items, actor="test")
    conn.commit()
    return out


def put_actuals(conn, key: str, cells: dict, source: str = "test"):
    from app.projection import series
    series.upsert_actuals(conn, [(key, m, v) for m, v in cells.items()], source, actor="test")
    conn.commit()


def months(y0, m0, n):
    from app.projection.interfaces import add_months
    return [add_months(date(y0, m0, 1), i) for i in range(n)]


def small_plan(rw, slug: str = "current"):
    """A compact plan: books through Jun-26, a typed plan Jul-26 .. Dec-27 (spend 300K, CPA 95, AOV 70, Amazon 150K)."""
    hist = months(2026, 1, 6)
    for key, v in (("spend.meta", 400000.0), ("spend.google", 20000.0), ("spend.amazon_ads", 20000.0), ("spend.tiktok", 2000.0)):
        put_actuals(rw, key, {m: v for m in hist})
    put_actuals(rw, "amazon_rev", {m: 150000.0 for m in hist})
    put_actuals(rw, "tiktok_rev", {m: 3000.0 for m in hist})
    put_actuals(rw, "dtc_rev", {m: 1200000.0 for m in hist})
    plan = months(2026, 7, 18)
    put_cells(rw, "drivers.spend_total", {m: 300000.0 for m in plan}, slug)
    put_cells(rw, "drivers.nc_cpa", {m: 95.0 for m in plan}, slug)
    put_cells(rw, "drivers.nc_aov", {m: 70.0 for m in plan}, slug)
    put_cells(rw, "drivers.amazon_rev", {m: 150000.0 for m in plan}, slug)
    put_cells(rw, "drivers.tiktok_rev", {m: 0.0 for m in plan}, slug)
    return hist, plan


@pytest.fixture()
def stub_world(monkeypatch, drivers_only):
    """The engine sees the real drivers module and fixture stubs for everything else (also in API requests)."""
    monkeypatch.setattr(registry, "PACKAGE", drivers_only)
    yield drivers_only
