"""Fixtures for the inventory_supply tests: own throwaway database (tagexp_proj_inventory_supply_test) holding the base migrations, 100_proj_core and
150_proj_inventory_supply only (other modules' migrations are not needed), a logged-in client, the reference day pinned to the sheet snapshot date."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_SUPPLY_DB", "tagexp_proj_inventory_supply_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
from werkzeug.security import generate_password_hash                # noqa: E402
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "106_proj_scenario_lifecycle.sql", "140_proj_inventory_demand.sql", "150_proj_inventory_supply.sql", "151_proj_inventory_supply_history.sql")
MODULE_TABLES = ("proj_supply_history", "proj_po", "proj_fba_transfer", "proj_vendor_cogs", "proj_vendor_lead", "proj_product_cost", "proj_reorder_param", "proj_stock_snapshot")


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4,
           "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def supply_db(tmp_path_factory):
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
        migrate.migrate(conn, d)                                # idempotent: a second run applies nothing
    yield DB_URL
    rwdb.close_all()


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")


@pytest.fixture(scope="session")
def app(supply_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(supply_db):
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
def client(app):
    return login(app.test_client())


@pytest.fixture()
def anon(app):
    return app.test_client()


@pytest.fixture()
def pg(supply_db):
    from psycopg.rows import dict_row
    with psycopg.connect(supply_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean(supply_db):
    """Every test starts empty: module tables, audit, actuals; only the 'current' scenario."""
    with psycopg.connect(supply_db) as c:
        for t in MODULE_TABLES:
            c.execute(f"DELETE FROM {t}")                              # noqa: S608
        c.execute("ALTER SEQUENCE proj_po_id_seq RESTART WITH 1000")
        c.execute("DELETE FROM proj_audit")
        c.execute("DELETE FROM proj_actuals")
        c.execute("DELETE FROM proj_scenario_history")
        c.execute("DELETE FROM proj_series")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield


@pytest.fixture()
def seeded(supply_db):
    """The module tables loaded from the frozen sheet snapshot (skips when the snapshot is not on this machine)."""
    import inv_supply_support as S
    if not S.snapshot_available():
        pytest.skip("sheet snapshot not available")
    from psycopg.rows import dict_row
    from etl import projection_inventory_supply as etl
    with psycopg.connect(supply_db, row_factory=dict_row) as c:
        counts = etl.seed(c)
    return counts


@pytest.fixture()
def with_demand(monkeypatch):
    """Swap the demand stub for the sheet's own demand (what inventory_demand reproduces) and recompute supply against it."""
    import inv_supply_support as S
    if not S.snapshot_available():
        pytest.skip("sheet snapshot not available")
    from app.projection import engine as proj_engine
    from app.projection.inventory_supply import calc, loaders
    from app.projection.inventory_supply.model import Settings
    real = proj_engine.run

    def run(scn, conn, **kw):
        eng = real(scn, conn, **kw)
        dem = S.demand_from_snapshot()
        inputs = loaders.load_inputs(conn, eng.scenario)
        params = eng.scenario.params
        eng.results["inventory_supply"] = calc.compute_supply(inputs, dem, loaders.resolve_today(params), Settings.from_params(params))
        eng.results["inventory_demand"] = dem
        eng.stubbed["inventory_demand"] = False
        return eng

    monkeypatch.setattr(proj_engine, "run", run)
