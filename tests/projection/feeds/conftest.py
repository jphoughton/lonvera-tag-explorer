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
MIGRATIONS = ("001_init.sql", "020_proj_feed_nc_hourly.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql", "180_proj_feeds.sql", "249_amazon_data.sql")
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


# ---------------------------------------------------------------------------------------------------------------- module hook-up tests (step 5)
# A second database with EVERY migration of the tree: the hook-up tests run the real refresh hooks of the pacing, inventory demand and inventory supply
# modules (and, in the end-to-end test, the whole etl.projection_daily run) against it, with dict-row connections as the projection runner hands them out.

WIRED_DB_NAME = os.environ.get("TEST_PROJ_FEEDS_WIRED_DB", "tagexp_proj_feeds_wired")
WIRED_DB_URL = f"{PG_BASE}/{WIRED_DB_NAME}"
WIRED_TRUNCATE = ("proj_feed_orders", "proj_feed_amazon", "proj_feed_platform", "proj_feed_attribution", "proj_feed_traffic", "proj_feed_meta_reach",
                  "proj_feed_product_reach", "proj_feed_nc_product", "proj_feed_nc_hourly", "proj_feed_anyclick", "proj_feed_bids", "proj_feed_meta_day", "proj_feed_meta_product",
                  "proj_feed_runs", "proj_feed_config", "proj_feed_attempts", "proj_feed_verify", "proj_feed_verify_runs",
                  "proj_feedx_orders", "proj_feedx_amazon", "proj_feedx_platform", "proj_feedx_attribution", "proj_feedx_traffic", "proj_feedx_meta_reach",
                  "proj_feedx_product_reach", "proj_feedx_nc_product", "proj_feedx_anyclick", "proj_feedx_bids", "proj_feedx_units_daily",
                  "proj_feedx_demand_snapshot", "proj_feedx_intl_nonus_daily",
                  "proj_units_daily", "proj_units_monthly", "proj_units_country", "proj_units_loads", "proj_demand_snapshot",
                  "proj_stock_snapshot", "proj_po", "proj_fba_transfer", "proj_vendor_lead", "proj_pacing_hourly", "proj_audit", "proj_refresh_runs",
                  "proj_actuals", "proj_cohorts", "ad_daily", "meta_ads", "meta_campaigns")


@pytest.fixture(scope="session")
def wired_db():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{WIRED_DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{WIRED_DB_NAME}"')
            c.execute(f'ALTER DATABASE "{WIRED_DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    from etl import migrate
    with psycopg.connect(WIRED_DB_URL) as conn:
        migrate.migrate(conn)                                    # the real migrations directory, in filename order
    return WIRED_DB_URL


@pytest.fixture()
def dconn(wired_db):
    """A dict-row connection (what etl.projection_daily hands to a refresh hook) on the all-migrations database, emptied of feed and module data afterwards."""
    from psycopg.rows import dict_row
    c = psycopg.connect(wired_db, row_factory=dict_row)
    yield c
    c.rollback()
    present = {r["tablename"] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()}
    c.execute("TRUNCATE " + ", ".join(t for t in WIRED_TRUNCATE if t in present) + " CASCADE")           # CASCADE: the cohort child tables and the vendor price lists
    c.commit()
    c.close()
