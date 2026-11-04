"""Fixtures for the feeds tests: own throwaway database (tagexp_proj_feeds), migrations 001-004, 100, 105, 180 only (plus the sheet-fed tables of 140 and 170
loaded directly for the verify tests). No network: HTTP goes through FakeSession, Meta through a patched graph_get.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/feeds -q
"""
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

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_FEEDS_DB", "tagexp_proj_feeds")
DB_URL = f"{PG_BASE}/{DB_NAME}"
MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql", "180_proj_feeds.sql")
SHEET_FED = ("140_proj_inventory_demand.sql", "170_proj_pacing.sql")


@pytest.fixture(scope="session")
def feeds_db(tmp_path_factory):
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
        for name in SHEET_FED:                                   # idempotent; gives the main tables the verify tests compare against
            conn.execute((ROOT / "migrations" / name).read_text())
        conn.commit()
    return DB_URL


@pytest.fixture()
def conn(feeds_db):
    c = psycopg.connect(feeds_db)
    yield c
    c.rollback()
    names = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND (tablename LIKE 'proj_feed%%' OR tablename LIKE 'proj_units%%' "
                                     "OR tablename = 'proj_demand_snapshot' OR tablename = 'feed_oauth')").fetchall()]
    keep = {"proj_feed_levers"}
    c.execute("TRUNCATE " + ", ".join(n for n in names if n not in keep))
    c.execute("DELETE FROM proj_feed_levers WHERE updated_by <> 'seed'")
    c.execute("UPDATE proj_feed_levers SET value = 0.905 WHERE key = 'intl_ratio.bull'")
    c.execute("UPDATE proj_feed_levers SET value = 0.895 WHERE key = 'intl_ratio.liver'")
    c.execute("UPDATE proj_feed_levers SET value = 0.868 WHERE key = 'intl_ratio.boneflex'")
    c.commit()
    c.close()
