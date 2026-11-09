"""Fixtures for the pacing tests: own database (tagexp_proj_pacing_t) with the base + core + pacing migrations, the sheet snapshot loaded into the feeds,
a snapshot-backed P&L / cohort double, and the pinned date of the snapshot (the sheet's TODAY() was 2026-11-03).

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/pacing -q
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection import rwdb                                             # noqa: E402
from app.projection.pacing import targets                                   # noqa: E402
from etl import migrate, projection_pacing as ETL                            # noqa: E402
from snapshot_pnl import ROW_MAP, cohorts_from_snapshot, pnl_from_snapshot   # noqa: E402
from pacing_support import PASSWORD, SNAP, SNAPSHOT_DAY, make_app          # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_PACING_DB", "tagexp_proj_pacing_t")
DB_URL = f"{PG_BASE}/{DB_NAME}"
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql", "106_proj_scenario_lifecycle.sql", "160_proj_financing.sql", "161_proj_financing_payback_store.sql", "170_proj_pacing.sql", "020_proj_feed_nc_hourly.sql", "192_proj_live_today.sql")


@pytest.fixture(scope="session")
def pacing_db(tmp_path_factory):
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    if not SNAP.exists():
        pytest.skip("sheet snapshot not available")
    d = tmp_path_factory.mktemp("migrations")
    for name in MIGRATIONS:
        shutil.copy(ROOT / "migrations" / name, d / name)
    with psycopg.connect(DB_URL) as conn:
        migrate.migrate(conn, d)
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        ETL.refresh(conn, snapshot_dir=SNAP, backfill=True)
        ETL.seed_hourly(conn, ETL.read_grid("Automated Pacing ", SNAP))
    yield DB_URL
    rwdb.close_all()


@pytest.fixture()
def conn(pacing_db):
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def clean_conn(pacing_db):
    """A connection whose changes are rolled back at the end of the test."""
    c = psycopg.connect(pacing_db, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("PROJECTION_AS_OF", SNAPSHOT_DAY.isoformat())


@pytest.fixture(scope="session")
def pnl():
    return pnl_from_snapshot()


@pytest.fixture(scope="session")
def cohorts():
    return cohorts_from_snapshot()


@pytest.fixture()
def book(pnl, cohorts):
    return targets.TargetBook(pnl, None, cohorts, row_map=ROW_MAP)


class FakeRun:
    """What engine.run returns, with the snapshot P&L double as the plan."""

    def __init__(self, pnl, cohorts, scenario):
        self.results = {"pnl": pnl, "cohorts": cohorts, "drivers": None}
        self.scenario = scenario
        self.errors = {}
        self.actual_through = dt.date(2026, 7, 1)                              # the last month the books hold

    def summary(self):                                                       # what GET /run (the shell's headline strip) asks for
        return {"scenario": self.scenario.slug, "version": self.scenario.version, "actual_through": "2026-10-01", "modules": {}, "headline": {}}


@pytest.fixture()
def api_app(pacing_db, pnl, cohorts, monkeypatch):
    """Flask app (the app's own login on) whose engine returns the snapshot P&L as the scenario's plan."""
    from app.projection import engine
    from app.projection.interfaces import Scenario
    from app.projection.pacing import api as pacing_api

    from app.projection import scenarios

    def fake_run(slug, conn, **kw):
        scn = scenarios.get_scenario(conn, slug)                              # the real scenario row: its stored levers drive the pacing
        return FakeRun(pnl, cohorts, scn)

    monkeypatch.setattr(pacing_api.engine, "run", fake_run)
    monkeypatch.setattr(targets, "default_row_map", lambda: dict(ROW_MAP))
    a = make_app(pacing_db)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def api(api_app):
    c = api_app.test_client()
    r = c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD})
    assert r.status_code == 204
    return c


@pytest.fixture()
def meta_sandbox(pacing_db):
    """A connection for tests that fill ad_daily and refresh (refresh commits): afterwards ad_daily is emptied and the feeds are reloaded from the snapshot."""
    c = psycopg.connect(pacing_db, row_factory=dict_row)
    yield c
    c.rollback()
    for t in ("ad_daily", "meta_ads", "meta_campaigns"):
        c.execute(f"DELETE FROM {t}")
    ETL.refresh(c, snapshot_dir=SNAP, backfill=True)
    c.close()


@pytest.fixture(autouse=True)
def _trend_panel_gets_an_empty_series(monkeypatch):
    """The Pacing page mounts the Marketing Trend panel, which reads /api/platforms/trend. This test database has none of the ad tables (503, a console error),
    so every browser context made by these tests answers that one URL with an empty 200; test_pacing_trend_panel.py routes its own series on top."""
    try:
        from playwright.sync_api import Browser
    except Exception:                                                        # noqa: BLE001
        return
    orig = Browser.new_context
    body = '{"window_info":null,"compare_info":null,"grain":"day","labels":[],"points":[],"groups":[],"total":{"available":false,"points":[]},"notes":[]}'

    def new_context(self, *a, **kw):
        ctx = orig(self, *a, **kw)
        ctx.route("**/api/platforms/trend*", lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        return ctx
    monkeypatch.setattr(Browser, "new_context", new_context)
