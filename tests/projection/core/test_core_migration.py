from __future__ import annotations

import psycopg
import pytest

from conftest import BASE_MIGRATIONS, ROOT


def test_runner_is_idempotent(core_db, tmp_path):
    import shutil
    from etl import migrate
    d = tmp_path / "m"
    d.mkdir()
    for n in BASE_MIGRATIONS:
        shutil.copy(ROOT / "migrations" / n, d / n)
    with psycopg.connect(core_db) as conn:
        assert migrate.migrate(conn, d) == []                 # already applied by the fixture: nothing to do
        assert conn.execute("SELECT count(*) FROM schema_migrations WHERE version = '100_proj_core.sql'").fetchone()[0] == 1


def test_sql_applies_twice_without_error_or_duplicate_seed(core_db):
    sql = (ROOT / "migrations" / "100_proj_core.sql").read_text()
    with psycopg.connect(core_db) as conn:
        conn.execute(sql)
        conn.execute(sql)
        conn.commit()
        rows = conn.execute("SELECT slug, name, is_default, params, version FROM proj_scenarios WHERE slug = 'current'").fetchall()
    assert rows == [("current", "Current plan", True, {}, 1)]


def test_tables_and_constraints(pg):
    names = {r["table_name"] for r in pg.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'proj\\_%'").fetchall()}
    assert names == {"proj_scenarios", "proj_series", "proj_actuals", "proj_audit", "proj_scenario_history"}
    sid = pg.execute("SELECT id FROM proj_scenarios WHERE slug = 'current'").fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):          # kind
        pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) VALUES (%s,'a','2026-01-01',1,'guess')", (sid,))
    with pytest.raises(psycopg.errors.CheckViolation):          # first of month
        pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) VALUES (%s,'a','2026-01-15',1,'input')", (sid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO proj_actuals (line_key, month, value) VALUES ('a','2026-01-02',1)")
    with pytest.raises(psycopg.errors.UniqueViolation):         # one default scenario only
        pg.execute("INSERT INTO proj_scenarios (slug, name, is_default) VALUES ('other','Other',true)")
    with pytest.raises(psycopg.errors.UniqueViolation):         # slug unique
        pg.execute("INSERT INTO proj_scenarios (slug, name) VALUES ('current','Dup')")


def test_series_and_history_cascade_with_scenario(pg):
    sid = pg.execute("INSERT INTO proj_scenarios (slug, name) VALUES ('tmp','Tmp') RETURNING id").fetchone()["id"]
    pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) VALUES (%s,'a','2026-01-01',1,'input')", (sid,))
    pg.execute("INSERT INTO proj_scenario_history (scenario_id, version, snapshot, action) VALUES (%s,1,'{}','create')", (sid,))
    pg.execute("DELETE FROM proj_scenarios WHERE id = %s", (sid,))
    assert pg.execute("SELECT count(*) AS n FROM proj_series WHERE scenario_id = %s", (sid,)).fetchone()["n"] == 0
    assert pg.execute("SELECT count(*) AS n FROM proj_scenario_history WHERE scenario_id = %s", (sid,)).fetchone()["n"] == 0
