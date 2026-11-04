"""Restore calls each module's optional on_restore for the plan of record, so rows kept outside params and series (supply orders) go back too."""
from __future__ import annotations

import importlib
import os
import shutil

import psycopg
import pytest

from app.projection import registry, rwdb
from conftest import DB_NAME, PG_BASE, ROOT, login, make_app
from core_helpers import P, audit_rows, create, item, put

FAKE = {
    "inventory_supply/__init__.py": """
        from app.projection.interfaces import Module
        CALLS = []

        def on_restore(conn, slug, to_version, new_version, actor=None):
            CALLS.append({"slug": slug, "to": to_version, "new": new_version, "actor": actor, "one": conn.execute("SELECT 1 AS one").fetchone()["one"]})
            return {"reverted": 2}

        MODULE = Module(key="inventory_supply")
    """,
    "pnl/__init__.py": """
        from app.projection.interfaces import Module
        MODULE = Module(key="pnl")          # no hook
    """,
    "cohorts/__init__.py": """
        from app.projection.interfaces import Module
        CALLS = []

        def on_restore(*, slug, new_version):                    # a module may declare only what it needs, by name
            CALLS.append((slug, new_version))
            return {"touched": True}

        MODULE = Module(key="cohorts")
    """,
}


def test_restore_calls_on_restore_hooks_for_the_plan_of_record_and_reports_and_audits_the_results(app_factory, pkg_factory, pg):
    pkg = pkg_factory(FAKE)
    c = login(app_factory().test_client())
    put(c, "current", item(value=1))                                                         # v2
    put(c, "current", item(value=2))                                                         # v3
    r = c.post(f"{P}/scenarios/current/restore", json={"version": 2})
    assert r.status_code == 200
    j = r.get_json()
    assert j["scenario"]["version"] == 4
    assert j["restore"]["modules"] == {"cohorts": {"touched": True}, "inventory_supply": {"reverted": 2}}
    call = importlib.import_module(f"{pkg}.inventory_supply").CALLS
    assert call == [{"slug": "current", "to": 2, "new": 4, "actor": "JoshHoughton", "one": 1}]
    assert importlib.import_module(f"{pkg}.cohorts").CALLS == [("current", 4)]
    row = [a for a in audit_rows(pg, "scenario.restore")][-1]
    assert row["new"]["modules"] == {"cohorts": {"touched": True}, "inventory_supply": {"reverted": 2}} and row["version"] == 4


def test_hooks_are_called_for_the_plan_of_record_only(app_factory, pkg_factory):
    pkg = pkg_factory(FAKE)
    c = login(app_factory().test_client())
    create(c, "Copy")
    put(c, "copy", item(value=1))
    r = c.post(f"{P}/scenarios/copy/restore", json={"version": 1})
    assert r.status_code == 200 and "modules" not in r.get_json()["restore"]
    assert importlib.import_module(f"{pkg}.inventory_supply").CALLS == []


def test_a_failing_hook_rolls_the_whole_restore_back(app_factory, pkg_factory, client):
    pkg_factory({"pnl/__init__.py": """
        from app.projection.interfaces import Module
        def on_restore(conn, slug, to_version, new_version):
            raise RuntimeError("supply history is damaged")
        MODULE = Module(key="pnl")
    """})
    c = login(app_factory().test_client())
    put(c, "current", item(value=1))
    put(c, "current", item(value=2))
    assert c.post(f"{P}/scenarios/current/restore", json={"version": 2}).status_code == 500
    from core_helpers import get_series, scenario
    assert scenario(c, "current")["version"] == 3 and get_series(c, "current", "pnl.margin_adj")["series"]["pnl.margin_adj"] == {"2026-11-01": 2.0}


# ------------------------------------------------------------------------------------------------------------ the real inventory_supply module

SUPPLY_DB = os.environ.get("TEST_PROJ_CORE_SUPPLY_DB", DB_NAME + "_supply")
SUPPLY_MIGRATIONS = ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "106_proj_scenario_lifecycle.sql",
                     "140_proj_inventory_demand.sql", "150_proj_inventory_supply.sql", "151_proj_inventory_supply_history.sql")


@pytest.fixture(scope="module")
def supply_url(tmp_path_factory):
    if not (ROOT / "migrations" / "151_proj_inventory_supply_history.sql").exists():
        pytest.skip("inventory_supply history migration not present")
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{SUPPLY_DB}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{SUPPLY_DB}"')
            conn.execute(f'ALTER DATABASE "{SUPPLY_DB}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("supply_migrations")
    for name in SUPPLY_MIGRATIONS:
        shutil.copy(ROOT / "migrations" / name, d / name)
    url = f"{PG_BASE}/{SUPPLY_DB}"
    from etl import migrate
    with psycopg.connect(url) as conn:
        migrate.migrate(conn, d)
    yield url
    rwdb.close_all()


def test_restoring_the_plan_of_record_removes_a_purchase_order_added_after_that_version(supply_url, monkeypatch):
    monkeypatch.setattr(registry, "PACKAGE", "app.projection")
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    app = make_app(DATABASE_URL=supply_url, AUTH_DISABLED=True, APP_USERS={})
    try:
        c = app.test_client()
        body = {"product": "bull", "vendor": "KeyStone", "warehouse": "revamp", "units": 30000, "land_date": "2026-12-15", "status": "planned"}
        a = c.post(f"{P}/inventory-supply/po", json=body)
        assert a.status_code == 201, a.get_json()
        v_after_a = c.get(f"{P}/scenarios").get_json()["scenarios"][0]["version"]
        b = c.post(f"{P}/inventory-supply/po", json={**body, "units": 12000, "land_date": "2027-01-20"})
        assert b.status_code == 201
        ids = lambda: sorted(p["id"] for p in c.get(f"{P}/inventory-supply/po").get_json()["pos"])          # noqa: E731
        assert ids() == sorted([a.get_json()["po"]["id"], b.get_json()["po"]["id"]])
        r = c.post(f"{P}/scenarios/current/restore", json={"version": v_after_a})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["restore"]["modules"]["inventory_supply"]["reverted"] >= 1
        assert ids() == [a.get_json()["po"]["id"]]                                                      # the order added after that version is gone
        assert r.get_json()["scenario"]["version"] == c.get(f"{P}/scenarios").get_json()["scenarios"][0]["version"]
        hist = c.get(f"{P}/inventory-supply/history").get_json()
        assert "restore" in str(hist)
    finally:
        app.extensions["pool"].close()
