"""Fixtures for the cohorts module tests: own throwaway database (tagexp_proj_cohorts_test), the app's base + core + cohorts migrations only,
a logged-in client, synthetic cohort data. The dev database tagexp_proj_cohorts is never touched.

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/cohorts -q
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import psycopg
import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.projection import registry, rwdb                    # noqa: E402
from app.projection.cohorts import engine as eng, store      # noqa: E402
from app.projection.interfaces import add_months              # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_COHORTS_DB", "tagexp_proj_cohorts_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
USERS = {name: generate_password_hash(PASSWORD, method="scrypt:1024:8:1") for name in ("JoshHoughton", "Lazer")}


def _migration_names() -> list[str]:
    """Tag Explorer base (00x), projection core (100-109) and this module (110-119): never another module's migration."""
    names = []
    for p in sorted((ROOT / "migrations").glob("*.sql")):
        n = int(re.match(r"(\d+)_", p.name).group(1))
        if n < 100 or 100 <= n < 110 or 110 <= n < 120:
            names.append(p.name)
    return names


def make_app(**overrides):
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True,
           "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def cohorts_db(tmp_path_factory):
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for name in _migration_names():
        shutil.copy(ROOT / "migrations" / name, d / name)
    from etl import migrate
    with psycopg.connect(DB_URL) as conn:
        migrate.migrate(conn, d)
    yield DB_URL
    rwdb.close_all()


@pytest.fixture(scope="session")
def app(cohorts_db):
    a = make_app()
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def app_factory(cohorts_db):
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
def pg(cohorts_db):
    from psycopg.rows import dict_row
    with psycopg.connect(cohorts_db, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean(cohorts_db):
    """Every test starts empty: no cohorts, no goal curve, only the 'current' scenario, no audit rows."""
    with psycopg.connect(cohorts_db) as c:
        for t in ("proj_audit", "proj_actuals", "proj_scenario_history", "proj_series", "proj_cohort_cum", "proj_cohorts", "proj_curve_goal", "proj_cohort_uploads"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM proj_scenarios WHERE slug <> 'current'")
        c.execute("UPDATE proj_scenarios SET name = 'Current plan', base_slug = NULL, params = '{}', version = 1, is_default = true")
    yield


# ------------------------------------------------------------------------------------------------------------ synthetic data

def make_cohort(month: date, n: float = 1000, first: float = 50.0, ratios: list[float] | None = None, **kw) -> eng.Cohort:
    """A cohort whose month-by-month increments are `ratios` x first order (cum = first + running sum). ratios=None -> no repeat observed."""
    cum: list[float | None] = []
    run = first
    for r in ratios or []:
        run += r * first
        cum.append(run)
    cum += [None] * (eng.COHORT_AGES - len(cum))
    return eng.Cohort(month=month, new_customers=n, first_order=first, cum=tuple(cum), **kw)


def seed_cohorts(conn, cohorts: list[eng.Cohort], goal: list[float] | None = None, goal_date: date = date(2026, 9, 29)) -> None:
    rows = [store.CohortRow(month=c.month, new_customers=c.new_customers, first_order=c.first_order, first_order_realized=c.first_order_realized,
                            repeat_90d=c.repeat_90d, cac=c.cac, payback_months=c.payback_months, nc_rev_reference=c.nc_rev_reference,
                            cum={a: v for a, v in enumerate(c.cum, start=1) if v is not None}) for c in cohorts]
    store.upsert_cohorts(conn, rows, "test")
    if goal:
        store.replace_goal(conn, goal_date, goal, "test")
    conn.commit()


def build_history(last: date = date(2026, 10, 1), first: date = date(2025, 1, 1), ratios=(0.2, 0.3, 0.2, 0.1, 0.05)) -> list[eng.Cohort]:
    """Monthly cohorts first..last; each has as many observed months as have passed by `last`'s month end + 1 (bounded by len(ratios))."""
    out = []
    m = first
    while m <= last:
        age_now = eng.months_between(m, last)                      # months observed up to and including `last`
        out.append(make_cohort(m, n=1000 + (m.month * 10), first=60.0 + m.month, ratios=list(ratios)[:age_now]))
        m = add_months(m, 1)
    return out
