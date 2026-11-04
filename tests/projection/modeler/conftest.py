"""Fixtures for the Scenario modeler tests: own database (tagexp_proj_modeler_test) with every module's migrations and seeders run from the frozen sheet snapshot,
a pinned date, a restore-after-test fixture, and Flask apps (login off, login on).

Run:  PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/modeler -q
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
from psycopg.types.json import Jsonb
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import create_app                      # noqa: E402
from app.projection import rwdb                 # noqa: E402

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_MODELER_DB", "tagexp_proj_modeler_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
SNAP = Path(os.environ.get("PROJECTION_SNAPSHOT_DIR", "~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03")).expanduser()
PINNED = "2026-11-03"
PASSWORD = "throwaway-test-password"
USERS = {"JoshHoughton": generate_password_hash(PASSWORD, method="scrypt:1024:8:1")}
SEEDERS = (("etl.projection_cohorts", []), ("etl.projection_drivers", ["seed", "--with-nc"]), ("etl.projection_pnl", ["seed"]),
           ("etl.projection_inventory_demand", ["seed", "--snapshot", "--amazon-source", "daily-units"]), ("etl.projection_inventory_supply", ["seed"]),
           ("etl.projection_financing", ["--apply"]))
P = "/api/projection"
SPEND, AOV, CPA = "drivers.spend_total", "drivers.nc_aov", "drivers.nc_cpa"


@pytest.fixture(scope="session", autouse=True)
def pinned_date():
    os.environ["PROJECTION_TODAY"] = PINNED
    yield


@pytest.fixture(scope="session")
def mdb(tmp_path_factory):
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
def conn(mdb):
    c = psycopg.connect(mdb, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()


@pytest.fixture()
def ro(mdb):
    """A read-only connection like the app's: any write through it raises."""
    c = psycopg.connect(mdb, row_factory=dict_row)
    c.read_only = True
    yield c
    c.rollback()
    c.close()


_TABLES = ("proj_scenarios", "proj_series", "proj_scenario_history", "proj_audit")


@pytest.fixture()
def restore(mdb):
    """Puts scenarios, series, history, audit and the cohort levers back to the seeded state after a test that commits (API writes)."""
    with psycopg.connect(mdb, row_factory=dict_row) as c:
        saved = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in _TABLES}
    yield
    with psycopg.connect(mdb, row_factory=dict_row) as c:
        c.execute("TRUNCATE proj_audit, proj_scenario_history, proj_series, proj_scenarios CASCADE")
        for t in ("proj_scenarios", "proj_series", "proj_scenario_history", "proj_audit"):
            rows = saved[t]
            if not rows:
                continue
            cols = list(rows[0])
            ph = ",".join(["%s"] * len(cols))
            with c.cursor() as cur:
                cur.executemany(f"INSERT INTO {t} ({','.join(cols)}) OVERRIDING SYSTEM VALUE VALUES ({ph})" if t == "proj_audit"
                                else f"INSERT INTO {t} ({','.join(cols)}) VALUES ({ph})",
                                [tuple(Jsonb(v) if isinstance(v, (dict, list)) else v for v in r.values()) for r in rows])
        for seq, tbl in (("proj_scenarios", "proj_scenarios"), ("proj_audit", "proj_audit")):
            try:
                c.execute(f"SELECT setval(pg_get_serial_sequence('{seq}', 'id'), (SELECT max(id) FROM {tbl}))")
            except Exception:
                pass


def make_app(url, **overrides):
    cfg = {"DATABASE_URL": url, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4,
           "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)


@pytest.fixture(scope="session")
def app(mdb):
    a = make_app(mdb, AUTH_DISABLED=True)
    yield a
    a.extensions["pool"].close()


@pytest.fixture(scope="session")
def app_login(mdb):
    a = make_app(mdb)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def client(app):
    return app.test_client()


def copy_of(client, name: str, source: str = "current") -> str:
    """A scenario copy through the real API; returns its slug."""
    r = client.post(f"{P}/scenarios", json={"name": name, "clone_from": source})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["scenario"]["slug"]


def put_cells(client, slug: str, cells: list[tuple[str, str, float | None]]) -> dict:
    """Save plan cells through the core PUT /series (the same call the page's grid makes)."""
    r = client.put(f"{P}/series", json={"scenario": slug, "items": [{"line_key": k, "month": m, "value": v, "kind": "input"} for k, m, v in cells]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def state(client, slug: str) -> dict:
    r = client.get(f"{P}/modeler/state", query_string={"scenario": slug})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def preview(client, slug: str, **body) -> dict:
    r = client.post(f"{P}/modeler/preview", json={"scenario": slug, "preview": True, **body})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def years(payload: dict) -> dict[int, dict]:
    return {y["year"]: y for y in payload["outputs"]["years"]}


def fy_months(start: str, n: int) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for i in range(n):
        k = y * 12 + m - 1 + i
        out.append(f"{k // 12}-{k % 12 + 1:02d}-01")
    return out
