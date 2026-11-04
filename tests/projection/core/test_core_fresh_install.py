"""A brand-new database migrated with every migration in the tree, and the real modules mounted: the routes the pages call exist and answer."""
from __future__ import annotations

import os

import psycopg
import pytest

from app.projection import registry, rwdb
from conftest import DB_NAME, PG_BASE, make_app
from core_helpers import P

FRESH_DB = os.environ.get("TEST_PROJ_CORE_FRESH_DB", DB_NAME + "_fresh")

PAGE_ROUTES = [
    "/modules", "/scenarios", "/scenarios/archived", "/freshness", "/run", "/audit",
    "/variables", "/variables/sensitivity", "/pnl/checks", "/pnl/uploads", "/inventory-supply/history", "/inventory-supply/summary",
    "/inventory-demand/table", "/cohorts/table", "/drivers/table", "/financing/inputs", "/pacing/month",
]


@pytest.fixture(scope="module")
def fresh_url():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{FRESH_DB}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{FRESH_DB}"')
            c.execute(f'ALTER DATABASE "{FRESH_DB}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    url = f"{PG_BASE}/{FRESH_DB}"
    from etl import migrate
    with psycopg.connect(url) as conn:
        done = migrate.migrate(conn)                          # the real migrations directory, in filename order
        assert done and migrate.migrate(conn) == []           # applied once, then a no-op
    yield url
    rwdb.close_all()


def test_every_page_route_is_registered_and_answers_on_a_fresh_database(fresh_url, monkeypatch):
    monkeypatch.setattr(registry, "PACKAGE", "app.projection")
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    app = make_app(DATABASE_URL=fresh_url, AUTH_DISABLED=True, APP_USERS={})
    try:
        c = app.test_client()
        mods = {m["key"]: m for m in c.get(f"{P}/modules").get_json()["modules"]}
        assert all(m["built"] for k, m in mods.items()), {k: m["built"] for k, m in mods.items() if not m["built"]}
        problems = {}
        for route in PAGE_ROUTES:
            r = c.get(P + route)
            if r.status_code in (404, 500):                    # not registered, or broken on a fresh schema; 200/400/503 mean the route exists and answered
                problems[route] = (r.status_code, str(r.get_json())[:160])
        assert not problems, problems
    finally:
        app.extensions["pool"].close()
