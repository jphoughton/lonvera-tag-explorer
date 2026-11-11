"""The Goal curve: every save and restore is its own dated snapshot, the Goal replaced stays restorable, and each step is audited and versioned."""
from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

import psycopg
import pytest
from conftest import PG_BASE, ROOT, build_history, seed_cohorts

from app.projection import rwdb
from app.projection.cohorts import model as cohorts_model
from app.projection.cohorts import store

P = "/api/projection"
C = f"{P}/cohorts"
D = date
TODAY = D(2026, 11, 3)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(cohorts_model, "today", lambda: TODAY)
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path))


@pytest.fixture()
def loaded(cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, build_history(first=D(2025, 1, 1), last=D(2026, 10, 1)), goal=[0.3, 0.2] + [0.01] * 58, goal_date=D(2026, 9, 29))


def audit_rows(pg, action):
    return pg.execute("SELECT * FROM proj_audit WHERE action = %s ORDER BY id", (action,)).fetchall()


def test_saving_the_current_curve_adds_a_snapshot_and_reports_what_it_replaced(client, loaded, pg):
    live = client.get(f"{C}/curves").get_json()["live"]
    r = client.post(f"{C}/goal", json={"from": "live"})
    assert r.status_code == 200
    out = r.get_json()
    assert out["snapshot_date"] == "2026-11-03" and out["lifetime"] == pytest.approx(sum(live))
    assert out["replaced"]["snapshot_date"] == "2026-09-29" and out["replaced"]["lifetime"] == pytest.approx(0.3 + 0.2 + 0.58)
    assert client.get(f"{C}/curves").get_json()["goal"] == pytest.approx(live)
    g = client.get(f"{C}/goal").get_json()
    assert g["active"]["id"] == out["snapshot_id"] and g["previous"]["snapshot_date"] == "2026-09-29" and len(g["history"]) == 2
    assert g["previous"]["lifetime"] == pytest.approx(1.08)
    (a,) = audit_rows(pg, "cohorts.goal.snapshot")
    assert a["version"] == out["snapshot_id"] and a["old"]["lifetime"] == pytest.approx(1.08) and a["new"]["snapshot_id"] == out["snapshot_id"] and a["actor"] == "JoshHoughton"


def test_two_saves_on_the_same_day_keep_both_and_an_identical_curve_is_refused(client, loaded, pg):
    client.post(f"{C}/goal", json={"from": "live"})
    assert client.post(f"{C}/goal", json={"from": "live"}).status_code == 409                       # the Goal already equals the current curve
    client.put(f"{C}/levers", json={"levers": {"tail_decay": 0.9}})                                  # a different current curve
    out = client.post(f"{C}/goal", json={"from": "live"})
    assert out.status_code == 200
    h = client.get(f"{C}/goal").get_json()["history"]
    assert [x["snapshot_date"] for x in h] == ["2026-11-03", "2026-11-03", "2026-09-29"] and len({x["id"] for x in h}) == 3
    assert pg.execute("SELECT count(DISTINCT snapshot_id) AS n FROM proj_curve_goal").fetchone()["n"] == 3


def test_restore_previous_puts_the_old_goal_back_as_a_new_snapshot_and_can_be_flipped_back(client, loaded, pg):
    old = client.get(f"{C}/curves").get_json()["goal"]
    new = client.post(f"{C}/goal", json={"from": "live"}).get_json()
    r = client.post(f"{C}/goal/restore", json={})
    assert r.status_code == 200
    res = r.get_json()
    assert res["snapshot_date"] == "2026-09-29" and res["restored_from"] < new["snapshot_id"] < res["snapshot_id"]
    assert res["replaced"]["snapshot_id"] == new["snapshot_id"] and res["lifetime"] == pytest.approx(1.08)
    assert client.get(f"{C}/curves").get_json()["goal"] == pytest.approx(old)                      # exactly the old curve
    assert client.get(f"{C}/curves").get_json()["goal_snapshot"] == "2026-09-29"
    g = client.get(f"{C}/goal").get_json()
    assert g["active"]["restored_from"] == res["restored_from"] and g["previous"]["id"] == new["snapshot_id"] and len(g["history"]) == 3
    (a,) = audit_rows(pg, "cohorts.goal.restore")
    assert a["version"] == res["snapshot_id"] and a["new"]["restored_from"] == res["restored_from"] and a["old"]["snapshot_id"] == new["snapshot_id"]
    again = client.post(f"{C}/goal/restore", json={}).get_json()                                      # restoring "previous" now brings the saved curve back
    assert again["replaced"]["snapshot_id"] == res["snapshot_id"] and again["lifetime"] == pytest.approx(new["lifetime"])
    assert client.get(f"{C}/curves").get_json()["goal"] == pytest.approx(client.get(f"{C}/curves").get_json()["live"])


def test_restore_by_snapshot_id_and_the_refusals(client, loaded):
    first = client.get(f"{C}/goal").get_json()["active"]["id"]
    assert client.post(f"{C}/goal/restore", json={}).status_code == 400                               # nothing earlier
    client.post(f"{C}/goal", json={"from": "live"})
    assert client.post(f"{C}/goal/restore", json={"snapshot_id": first}).status_code == 200
    active = client.get(f"{C}/goal").get_json()["active"]["id"]
    assert client.post(f"{C}/goal/restore", json={"snapshot_id": active}).status_code == 409           # already the one in use
    assert client.post(f"{C}/goal/restore", json={"snapshot_id": 987654}).status_code == 404
    assert client.post(f"{C}/goal/restore", json={"snapshot_id": "1"}).status_code == 400


def test_restore_with_no_goal_at_all_is_a_plain_400(client):
    r = client.post(f"{C}/goal/restore", json={})
    assert r.status_code == 400 and "no Goal curve" in r.get_json()["error"]


def test_saving_when_there_is_no_goal_yet_reports_nothing_replaced(client, cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, build_history(first=D(2025, 1, 1), last=D(2026, 10, 1)))
    out = client.post(f"{C}/goal", json={"from": "live"}).get_json()
    assert out["replaced"] is None and client.get(f"{C}/goal").get_json()["previous"] is None


def test_summary_carries_the_goal_overview_for_the_page(client, loaded):
    s = client.get(f"{C}/summary").get_json()
    assert s["goal"]["active"]["snapshot_date"] == "2026-09-29" and s["goal"]["previous"] is None
    client.post(f"{C}/goal", json={"from": "live"})
    s = client.get(f"{C}/summary").get_json()
    assert s["goal"]["previous"]["snapshot_date"] == "2026-09-29"


def test_goal_endpoints_need_the_apps_login(anon, loaded):
    assert anon.get(f"{C}/goal").status_code == 401 and anon.post(f"{C}/goal/restore", json={}).status_code == 401


def test_the_seeder_style_replace_is_idempotent_and_keeps_other_snapshots(cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        store.replace_goal(conn, D(2026, 9, 29), [0.2] * 60, "seed note")
        store.replace_goal(conn, D(2026, 9, 29), [0.3] * 60, "seed note")
        sid = store.save_goal(conn, D(2026, 11, 3), [0.4] * 60, "mine")
        store.replace_goal(conn, D(2026, 9, 29), [0.5] * 60, "seed note")
        assert [x["lifetime"] for x in store.goal_snapshots(conn)] == [pytest.approx(24.0), pytest.approx(30.0)] and store.goal_snapshots(conn)[0]["id"] == sid


def test_the_migration_gives_old_snapshots_ids_in_date_order_and_can_run_twice(tmp_path):
    name = "tagexp_proj_cohorts_mig" + os.environ.get("TEST_WORKER_SUFFIX", "")
    with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
    url = f"{PG_BASE}/{name}"
    mig = tmp_path / "m"
    mig.mkdir()
    for f in ("001_init.sql", "002_family_daily.sql", "003_traffic.sql", "004_api_lifetime.sql", "100_proj_core.sql", "105_proj_refresh.sql", "110_proj_cohorts.sql"):
        shutil.copy(ROOT / "migrations" / f, mig / f)
    from etl import migrate
    with psycopg.connect(url) as conn:
        migrate.migrate(conn, mig)
        for d in ("2026-09-29", "2026-05-01"):                                                        # legacy rows: a full 60-age curve each, no snapshot_id yet
            for age in range(1, 61):
                conn.execute("INSERT INTO proj_curve_goal (snapshot_date, age, value) VALUES (%s,%s,0.1)", (d, age))
        conn.commit()
        for f in ("111_proj_cohorts_history.sql", "112_proj_cohorts_goal_history.sql"):
            shutil.copy(ROOT / "migrations" / f, mig / f)
        migrate.migrate(conn, mig)
        rows = conn.execute("SELECT snapshot_date, snapshot_id, count(*) FROM proj_curve_goal GROUP BY 1, 2 ORDER BY 2").fetchall()
        assert [str(r[0]) for r in rows] == ["2026-05-01", "2026-09-29"] and rows[0][1] < rows[1][1] and [r[2] for r in rows] == [60, 60]     # one id per curve, in date order
        conn.execute((ROOT / "migrations" / "112_proj_cohorts_goal_history.sql").read_text())               # a second run changes nothing
        conn.commit()
        assert conn.execute("SELECT count(*) FROM proj_curve_goal").fetchone()[0] == 120
        assert conn.execute("SELECT nextval('proj_curve_goal_snapshot_seq')").fetchone()[0] == 3
        conn.execute("INSERT INTO proj_curve_goal (snapshot_id, snapshot_date, age, value) VALUES (99, '2026-11-03', 1, 0.2)")     # no longer a date-and-age key
        conn.execute("INSERT INTO proj_curve_goal (snapshot_id, snapshot_date, age, value) VALUES (100, '2026-11-03', 1, 0.2)")
        conn.commit()
    with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
