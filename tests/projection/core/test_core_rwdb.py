from __future__ import annotations

import pytest

from app.projection import rwdb


def _count(pg, action):
    return pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = %s", (action,)).fetchone()["n"]


def test_commit_on_exit(core_db, pg):
    with rwdb.get_rw(core_db) as conn:
        conn.execute("INSERT INTO proj_audit (action) VALUES ('rwdb.commit')")
    assert _count(pg, "rwdb.commit") == 1


def test_rollback_on_error(core_db, pg):
    with pytest.raises(RuntimeError):
        with rwdb.get_rw(core_db) as conn:
            conn.execute("INSERT INTO proj_audit (action) VALUES ('rwdb.rollback')")
            raise RuntimeError("boom")
    assert _count(pg, "rwdb.rollback") == 0


def test_connection_settings(core_db):
    with rwdb.get_rw(core_db) as conn:
        assert conn.autocommit is False
        assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "20s"
        assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}            # dict rows
        assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "off"
    pool = rwdb.get_pool(core_db)
    assert (pool.min_size, pool.max_size) == (1, 4)


def test_pool_is_lazy_shared_and_closed(core_db):
    rwdb.close_all()
    assert rwdb._pools == {}
    p1 = rwdb.get_pool(core_db)
    assert rwdb.get_pool(core_db) is p1
    rwdb.close_all()
    assert rwdb._pools == {} and p1.closed
    assert rwdb.get_pool(core_db) is not p1


def test_url_comes_from_app_config_or_env(app, core_db, monkeypatch):
    with app.app_context():
        assert rwdb.database_url() == core_db
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/from_env")
    assert rwdb.database_url() == "postgresql://localhost/from_env"
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError):
        rwdb.database_url()
