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
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql",
              "106_proj_scenario_lifecycle.sql", "160_proj_financing.sql", "161_proj_financing_payback_store.sql")


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
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true, frozen = false, archived_at = NULL")
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


SHEET_AOV, SHEET_CAC = 69.15600858369098, 105.4916738197425          # Payback!B7 / B8 on the 2026-11-03 snapshot (month-to-date pacing figures)


@pytest.fixture()
def sheet_payback(fin_db):
    """Type the sheet's average order value and cost per new customer into the Current plan (it holds none: they follow the last closed month)."""
    with psycopg.connect(fin_db) as c:
        c.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,payback}', coalesce(params #> '{financing,payback}', '{}'::jsonb) "
                  "|| jsonb_build_object('aov', %s::float8, 'cac', %s::float8)) WHERE slug = 'current'", (SHEET_AOV, SHEET_CAC))
    yield


@pytest.fixture()
def feeds(pg, monkeypatch):
    """With today = 2026-11-03: Meta ad spend by day and Shopify new customers by day: Oct 1 .. Nov 2 at $1,000 a day, 10 new customers of $70 each, except Sep/early Oct (cheaper)."""
    from datetime import date as _date
    from app.projection.drivers import loaders as _drv
    monkeypatch.setattr(_drv, "current_date", lambda: _date(2026, 11, 3))
    pg.execute("CREATE TABLE IF NOT EXISTS proj_feed_orders (day date PRIMARY KEY, nc_rev numeric(18,2) NOT NULL DEFAULT 0, rc_rev numeric(18,2) NOT NULL DEFAULT 0, "
               "nc_orders numeric(18,2) NOT NULL DEFAULT 0, total_rev numeric(18,2) NOT NULL DEFAULT 0, total_orders numeric(18,2) NOT NULL DEFAULT 0, "
               "tax numeric(18,2) NOT NULL DEFAULT 0, refunds numeric(18,2) NOT NULL DEFAULT 0, loaded_at timestamptz NOT NULL DEFAULT now())")
    pg.execute("DELETE FROM proj_feed_orders")
    pg.execute("DELETE FROM ad_daily")
    pg.execute("CREATE TABLE IF NOT EXISTS proj_feed_platform (day date NOT NULL, platform text NOT NULL, spend numeric(18,2) NOT NULL DEFAULT 0, "
               "purchases numeric(18,2) NOT NULL DEFAULT 0, purchase_value numeric(18,2) NOT NULL DEFAULT 0, nc_purchases numeric(18,2) NOT NULL DEFAULT 0, "
               "nc_purchase_value numeric(18,2) NOT NULL DEFAULT 0, link_clicks numeric(18,2) NOT NULL DEFAULT 0, clicks numeric(18,2) NOT NULL DEFAULT 0, "
               "loaded_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (day, platform))")
    pg.execute("DELETE FROM proj_feed_platform")
    from datetime import timedelta as _td
    d = _date(2026, 9, 1)
    while d <= _date(2026, 11, 2):
        old = d < _date(2026, 10, 5)                                   # before the 28-day window (Oct 5 .. Nov 1): $50 per customer, $90 order value
        spend, nc, rev = (500.0, 10, 900.0) if old else (1000.0, 10, 700.0)
        pg.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES ('a', %s, 'f', 'F', %s, 'meta_api')", (d, spend))
        pg.execute("INSERT INTO proj_feed_orders (day, nc_rev, nc_orders) VALUES (%s, %s, %s)", (d, rev, nc))
        pg.execute("INSERT INTO proj_feed_platform (day, platform, spend) VALUES (%s, 'Google Ads', 0)", (d,))
        d += _td(days=1)
    yield
    pg.execute("DELETE FROM proj_feed_orders")
    pg.execute("DELETE FROM proj_feed_platform")
    pg.execute("DELETE FROM ad_daily")


