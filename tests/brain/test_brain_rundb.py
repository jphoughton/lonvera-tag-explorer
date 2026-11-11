"""sql(): the connection URL comes only from ANALYTICS_READER_URL (hosted) or the named file (local server only); the transaction is read only; nothing leaks the URL."""
import pytest

import psycopg

from brain import rundb, tools as T
from brain.sqlguard import SqlRejected

from brain_helpers import DB_URL


class _Spy:
    calls = []

    def __init__(self, real):
        self.real = real

    def __call__(self, conninfo, **kw):
        _Spy.calls.append((conninfo, kw))
        return self.real(conninfo, **kw)


@pytest.fixture()
def spy(monkeypatch):
    _Spy.calls = []
    monkeypatch.setattr(rundb.psycopg, "connect", _Spy(psycopg.connect))
    return _Spy


def test_not_configured_is_a_plain_message_and_never_falls_back_to_database_url(monkeypatch, spy):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    with pytest.raises(rundb.SqlUnavailable, match="SQL is not configured"):
        rundb.run_select("select 1")
    assert spy.calls == []


def test_the_url_file_is_ignored_by_the_hosted_path_and_used_by_the_local_one(monkeypatch, tmp_path, spy, migrated_db):
    f = tmp_path / "url.txt"
    f.write_text(migrated_db + "\n")
    monkeypatch.setenv("LONVERA_ANALYTICS_URL_FILE", str(f))
    with pytest.raises(rundb.SqlUnavailable):
        rundb.run_select("select 1", allow_file=False)
    assert spy.calls == []
    assert rundb.run_select("select 1 as x", allow_file=True)["rows"] == [[1]]
    assert spy.calls[0][0] == migrated_db
    monkeypatch.setenv("ANALYTICS_READER_URL", migrated_db)                    # the env var wins and works without the file
    f.unlink()
    assert rundb.run_select("select 2 as x")["rows"] == [[2]]


def test_runs_with_the_env_url_only_in_a_read_only_transaction(monkeypatch, spy, migrated_db):
    monkeypatch.setenv("ANALYTICS_READER_URL", migrated_db)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/never")
    r = rundb.run_select("select g as n, g::numeric / 3 as third, now() as t, 'a' as s from generate_series(1, 5) g order by 1", limit=3)
    assert r["columns"] == ["n", "third", "t", "s"] and r["row_count"] == 3 and r["truncated"] is True and isinstance(r["rows"][0][2], str)
    assert [c[0] for c in spy.calls] == [migrated_db]
    assert "statement_timeout=30000" in spy.calls[0][1]["options"] and "read_only=on" in spy.calls[0][1]["options"]
    assert rundb.run_select("select 1", limit=50_000)["row_count"] == 1          # the cap itself is clamped (MAX_ROWS)


def test_a_write_fails_even_if_the_check_were_bypassed(monkeypatch, migrated_db):
    """Layers two and three: a server-side cursor cannot hold DML, and the session is a READ ONLY transaction (the test role itself could write)."""
    monkeypatch.setenv("ANALYTICS_READER_URL", migrated_db)
    monkeypatch.setattr(rundb, "check_select", lambda q: q)
    for q in ("insert into app_meta (key, value) values ('brain', 'x')", "create table brain_evil (a int)", "delete from app_meta"):
        with pytest.raises(rundb.SqlUnavailable, match="syntax error"):
            rundb.run_select(q)
    with pytest.raises(rundb.SqlUnavailable, match="read-only transaction"):
        rundb.run_select("select nextval('brain_keys_id_seq')")                   # a SELECT that writes (a sequence)
    with psycopg.connect(migrated_db) as c:
        assert c.execute("select count(*) from app_meta where key = 'brain'").fetchone()[0] == 0
        assert c.execute("select to_regclass('brain_evil')").fetchone()[0] is None


def test_the_tool_rejects_writes_multiple_statements_and_copy(monkeypatch, migrated_db):
    monkeypatch.setenv("ANALYTICS_READER_URL", migrated_db)
    b = T.Brain()
    for q in ("insert into app_meta (key, value) values ('a', 'b')", "update products set name = 'x'", "delete from ad_daily", "drop table ad_daily", "select 1; select 2", "copy ad_daily to stdout", "create table x (a int)"):
        with pytest.raises(T.ToolError, match="query rejected"):
            b.sql(q)
    assert b.sql("select 1 as one")["rows"] == [[1]]


def test_errors_never_contain_the_url(monkeypatch):
    url = "postgresql://analytics_reader:s3cretpass@db.internal.example:5432/prod"
    monkeypatch.setenv("ANALYTICS_READER_URL", url)
    monkeypatch.setattr(rundb.psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError(f"connection to {url} failed for user analytics_reader at db.internal.example")))
    with pytest.raises(rundb.SqlUnavailable) as e:
        rundb.run_select("select 1")
    text = str(e.value)
    assert "s3cretpass" not in text and "db.internal.example" not in text and "analytics_reader" not in text


def test_a_database_error_is_reported_without_the_query_text(monkeypatch, migrated_db):
    monkeypatch.setenv("ANALYTICS_READER_URL", migrated_db)
    with pytest.raises(rundb.SqlUnavailable, match="does not exist"):
        rundb.run_select("select * from analytics.no_such_view")
    with pytest.raises(SqlRejected):
        rundb.run_select("select 1; select 2")
