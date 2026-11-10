"""Fixtures for the alerts tests: a throwaway migrated Postgres database of their own (never the shared tagexp_test)."""
from __future__ import annotations

import datetime as dt
import glob
import os
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_API_DB", "tagexp_apitest") + "_alerts"
DB_URL = f"{PG_BASE}/{DB_NAME}"
NOW = dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.timezone.utc)          # Mon 12:00 PT


@pytest.fixture(scope="session")
def dburl():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
        with psycopg.connect(DB_URL) as c:
            for p in sorted(glob.glob(str(ROOT / "migrations" / "*.sql"))) + [str(ROOT / "tests" / "api" / "schema_shim.sql")]:
                c.execute(open(p).read())
            c.commit()
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    yield DB_URL
    with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


@pytest.fixture
def conn(dburl):
    c = psycopg.connect(dburl, row_factory=dict_row)
    c.execute("TRUNCATE etl_runs, app_meta, alert_state, upstack_orders_daily, google_daily, proj_live_today_snapshots, proj_amazon_mtd_snapshots, proj_refresh_runs, "
              "proj_feed_attempts RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.rollback()
    c.close()


def hours_ago(h: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=h)


def add_run(conn, job, finished_h_ago=None, status="ok", error=None, started_h_ago=None):
    """An etl_runs row: finished `finished_h_ago` hours before NOW (None = still running), started a minute earlier unless given."""
    fin = hours_ago(finished_h_ago) if finished_h_ago is not None else None
    start = hours_ago(started_h_ago) if started_h_ago is not None else (fin - dt.timedelta(minutes=1) if fin else NOW)
    conn.execute("INSERT INTO etl_runs (job, started_at, finished_at, status, error) VALUES (%s,%s,%s,%s,%s)", (job, start, fin, status, error))
    conn.commit()
