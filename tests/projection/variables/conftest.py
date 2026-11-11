"""Fixtures for the variables tests: own database (tagexp_proj_variables_test) with every module's migrations and seeders run from the frozen sheet snapshot,
a pinned date, a restore-after-test fixture, and Flask apps (login on / off).

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/variables -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app                      # noqa: E402
from app.projection import rwdb                 # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_VARIABLES_DB", "tagexp_proj_variables_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
SNAP = Path(os.environ.get("PROJECTION_SNAPSHOT_DIR", "~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03")).expanduser()
PINNED = "2026-11-03"
PASSWORD = "throwaway-test-password"
USERS = {"JoshHoughton": generate_password_hash(PASSWORD, method="scrypt:1024:8:1")}
SEEDERS = (("etl.projection_cohorts", []), ("etl.projection_drivers", ["seed", "--with-nc"]), ("etl.projection_pnl", ["seed"]),
           ("etl.projection_inventory_demand", ["seed", "--snapshot", "--amazon-source", "daily-units"]), ("etl.projection_inventory_supply", ["seed"]),
           ("etl.projection_financing", ["--apply"]))


@pytest.fixture(scope="session", autouse=True)
def pinned_date():
    os.environ["PROJECTION_TODAY"] = PINNED
    yield


@pytest.fixture(autouse=True)
def _sensitivity_cap_does_not_depend_on_the_machine(monkeypatch):
    """the sensitivity run stops after CAP_SECONDS of wall clock (15 s) and says `truncated`: on a busy machine the whole run takes longer than that, so the tests
    that need the complete ranking give it an hour. The cap itself is tested in test_sensitivity_cap_truncates_and_says_so."""
    from app.projection.variables import sensitivity
    monkeypatch.setattr(sensitivity.run, "__kwdefaults__", {**sensitivity.run.__kwdefaults__, "cap": 3600.0})


@pytest.fixture(scope="session")
def vdb(tmp_path_factory):
    if not SNAP.exists():
        pytest.skip("sheet snapshot not available")
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("migrations")
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        shutil.copy(f, d / f.name)
    from etl import migrate
    with psycopg.connect(DB_URL) as conn:
        migrate.migrate(conn, d)
    env = {**os.environ, "DATABASE_URL": DB_URL, "PYTHONPATH": str(ROOT), "PROJECTION_TODAY": PINNED}
    for mod, args in SEEDERS:
        p = subprocess.run([sys.executable, "-m", mod, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, f"{mod} seed failed: {p.stderr[-600:]}"
    yield DB_URL
    rwdb.close_all()


@pytest.fixture()
def conn(vdb):
    c = psycopg.connect(vdb, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()


@pytest.fixture()
def rw(vdb):
    """A read-write connection whose changes are rolled back at the end of the test."""
    c = psycopg.connect(vdb, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()


_TABLES = ("proj_scenarios", "proj_series", "proj_scenario_history", "proj_audit")


@pytest.fixture()
def restore(vdb):
    """Puts scenarios, series, history and audit back to the seeded state after a test that commits (API writes)."""
    with psycopg.connect(vdb, row_factory=dict_row) as c:
        saved = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in _TABLES}
        seq = c.execute("SELECT last_value FROM proj_audit_id_seq").fetchone()["last_value"] if _has_seq(c) else None
    yield
    with psycopg.connect(vdb, row_factory=dict_row) as c:
        c.execute("TRUNCATE proj_audit, proj_scenario_history, proj_series, proj_scenarios CASCADE")
        for t in ("proj_scenarios", "proj_series", "proj_scenario_history", "proj_audit"):
            rows = saved[t]
            if not rows:
                continue
            cols = list(rows[0])
            ph = ",".join(["%s"] * len(cols))
            from psycopg.types.json import Jsonb
            with c.cursor() as cur:
                cur.executemany(f"INSERT INTO {t} ({','.join(cols)}) OVERRIDING SYSTEM VALUE VALUES ({ph})" if t == "proj_audit" else f"INSERT INTO {t} ({','.join(cols)}) VALUES ({ph})",
                                [tuple(Jsonb(v) if isinstance(v, (dict, list)) else v for v in r.values()) for r in rows])
        try:
            c.execute("SELECT setval(pg_get_serial_sequence('proj_scenarios', 'id'), (SELECT max(id) FROM proj_scenarios))")
        except Exception:
            pass
    from app.projection.variables import sensitivity
    sensitivity.clear()


def _has_seq(c) -> bool:
    return bool(c.execute("SELECT to_regclass('proj_audit_id_seq') AS s").fetchone()["s"])


def make_app(url, **overrides):
    cfg = {"DATABASE_URL": url, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4,
           "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def app(vdb):
    a = make_app(vdb, AUTH_DISABLED=True)
    yield a
    a.extensions["pool"].close()


@pytest.fixture(scope="session")
def app_login(vdb):
    a = make_app(vdb)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def client(app):
    return app.test_client()
