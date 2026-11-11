"""etl.reader_role: a login role that can read the `analytics` views and nothing else, and whose password never leaks.

These tests start a THROWAWAY Postgres cluster (initdb in a temp directory, password authentication over TCP, unix-socket trust for the admin user) because the
local development server trusts every connection and could not prove that a password works, that a rotated password stops working, or that a session on the old
password is ended. They are skipped when initdb / pg_ctl cannot be found.
"""
from __future__ import annotations

import glob
import os
import shutil
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, unquote

import psycopg
import pytest

from etl import reader_role as rr

from . import seed_fixture as S
from .test_schema_docs import D

ROOT = Path(__file__).resolve().parents[2]
ROLE = f"analytics_reader_t{os.getpid()}"


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for pat in ("/opt/homebrew/opt/postgresql@*/bin", "/usr/local/opt/postgresql@*/bin", "/usr/lib/postgresql/*/bin"):
        for d in sorted(glob.glob(pat), reverse=True):
            if os.path.exists(os.path.join(d, name)):
                return os.path.join(d, name)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cluster():
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("initdb / pg_ctl not found: cannot start a scratch Postgres cluster")
    tmp = tempfile.mkdtemp(prefix="rrpg_", dir="/tmp")
    data, sock, log = os.path.join(tmp, "data"), os.path.join(tmp, "sock"), os.path.join(tmp, "server.log")
    os.mkdir(sock)
    port = _free_port()
    env = {**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}                # macOS: the postmaster refuses to start without a valid locale
    try:
        subprocess.run([initdb, "-D", data, "-U", "postgres", "-E", "UTF8", "-N", "--auth-local=trust", "--auth-host=scram-sha-256"], check=True, capture_output=True, env=env)
        subprocess.run([pg_ctl, "-D", data, "-l", log, "-w", "-o", f"-p {port} -k {sock} -c listen_addresses=127.0.0.1 -c fsync=off -c log_statement=all "
                        f"-c password_encryption=scram-sha-256", "start"], check=True, capture_output=True, env=env)
    except (subprocess.CalledProcessError, OSError) as e:
        tail = Path(log).read_text()[-600:] if os.path.exists(log) else ""
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.skip(f"could not start a scratch Postgres cluster: {getattr(e, 'stderr', e)} {tail}")
    admin = f"postgresql://postgres@/lonvera?host={sock}&port={port}"
    try:
        with psycopg.connect(f"postgresql://postgres@/postgres?host={sock}&port={port}", autocommit=True) as c:
            c.execute("CREATE DATABASE lonvera")
        with psycopg.connect(admin, autocommit=True) as c:
            S.apply_migrations(c)
        with psycopg.connect(admin) as c:
            S.seed(c)
            c.commit()
        yield {"admin": admin, "port": port, "tmp": tmp, "log": log}
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def outfile(cluster):
    return os.path.join(cluster["tmp"], "private", "analytics_reader_url.txt")


@pytest.fixture()
def admin(cluster):
    c = psycopg.connect(cluster["admin"], autocommit=True)
    yield c
    c.close()


def cli(cluster, monkeypatch, outfile, *args):
    monkeypatch.setenv("DATABASE_URL", cluster["admin"])
    return rr.main([*args, "--role", ROLE, "--out", outfile, "--host", "127.0.0.1", "--port", str(cluster["port"]), "--dbname", "lonvera", "--sslmode", "disable"])


def read_url(outfile) -> str:
    return Path(outfile).read_text().strip()


def password_of(url: str) -> str:
    return unquote(urlparse(url).password)


def connect_reader(url: str, **kw):
    return psycopg.connect(url, autocommit=True, connect_timeout=10, **kw)


# ---------------------------------------------------------------------------------------------------------------------------- create
def test_scram_verifier_matches_what_the_server_stores(admin):
    """The verifier is computed here so the clear password never reaches the server; it must be byte-identical to the server's own for the same salt."""
    admin.execute("CREATE ROLE zz_scram_probe LOGIN PASSWORD 'correct-horse-battery-staple'")
    try:
        stored = admin.execute("SELECT rolpassword FROM pg_authid WHERE rolname = 'zz_scram_probe'").fetchone()[0]
        iterations, salt_b64 = stored.split("$")[1].split(":")
        import base64
        assert rr.scram_verifier("correct-horse-battery-staple", base64.b64decode(salt_b64), int(iterations)) == stored
        assert rr.scram_verifier("x").startswith("SCRAM-SHA-256$4096:")
    finally:
        admin.execute("DROP ROLE zz_scram_probe")


def test_create_writes_the_url_file_and_prints_no_secret(cluster, monkeypatch, outfile, capsys):
    rc = cli(cluster, monkeypatch, outfile, "create")
    out = capsys.readouterr()
    assert rc == 0
    url = read_url(outfile)
    pw = password_of(url)
    assert len(pw) >= 40
    for stream in (out.out, out.err):
        assert pw not in stream and url not in stream and "postgresql://" not in stream and "SCRAM-SHA-256" not in stream
    assert outfile in out.out and "created role" in out.out                       # only the path and a success line
    mode = stat.S_IMODE(os.stat(outfile).st_mode)
    assert mode == 0o600, oct(mode)
    assert stat.S_IMODE(os.stat(os.path.dirname(outfile)).st_mode) == 0o700
    u = urlparse(url)
    assert (u.scheme, u.username, u.hostname, u.port, u.path, u.query) == ("postgresql", ROLE, "127.0.0.1", cluster["port"], "/lonvera", "sslmode=disable")


def test_the_password_is_in_no_server_log(cluster):
    """log_statement = all was on: the server saw a SCRAM verifier, never the clear password."""
    pw = password_of(read_url(os.path.join(cluster["tmp"], "private", "analytics_reader_url.txt")))
    log = Path(cluster["log"]).read_text()
    assert pw not in log
    assert "SCRAM-SHA-256$" in log                                                # the verifier statement is there


def test_create_refuses_a_path_inside_the_repository(cluster, monkeypatch, admin):
    with pytest.raises(rr.ReaderRoleError):
        rr.create(admin, role="analytics_reader_zz", out=str(ROOT / "leak.txt"), host="127.0.0.1", port=1, dbname="x")
    assert not (ROOT / "leak.txt").exists()
    assert not rr.role_exists(admin, "analytics_reader_zz")
    with pytest.raises(rr.ReaderRoleError):
        rr.check_role_name("Robert'); DROP TABLE x;--")


def test_host_and_port_come_from_database_public_url(admin, monkeypatch):
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://postgres:ignored@proxy.example:6543/railway")
    assert rr._host_port_db(admin, None, None, None) == ("proxy.example", 6543, "railway")
    assert rr._host_port_db(admin, "h", 1, "d") == ("h", 1, "d")
    monkeypatch.delenv("DATABASE_PUBLIC_URL")
    with pytest.raises(rr.ReaderRoleError):
        rr._host_port_db(admin, None, None, None)


# ---------------------------------------------------------------------------------------------------------------------------- what it can read
def test_reader_can_select_every_analytics_view_and_every_documented_example(cluster, outfile):
    with connect_reader(read_url(outfile)) as c:
        views = [r[0] for r in c.execute("SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics' ORDER BY 1").fetchall()]
        assert len(views) >= 12
        assert {v for v in D.REL if v.startswith("analytics.") and v not in D.DEFERRED} == {f"analytics.{v}" for v in views}      # every documented analytics view exists and is readable, none is missing
        for v in views:
            c.execute(f"SELECT count(*) FROM analytics.{v}").fetchone()
        assert c.execute("SELECT count(*) FROM meta_ad_daily").fetchone()[0] > 0                    # search_path = analytics
        n = 0
        for key, spec in D.REL.items():
            if key.startswith("analytics.") and key not in D.DEFERRED:
                for title, sql in spec["examples"]:
                    c.execute(sql).fetchall()
                    n += 1
        assert n >= 20
        assert c.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
        assert c.execute("SHOW statement_timeout").fetchone()[0] == "30s"
        assert c.execute("SHOW search_path").fetchone()[0] == "analytics, pg_catalog"
        assert c.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0] == "1min"
        assert c.execute("SHOW temp_file_limit").fetchone()[0] == "512MB"


FORBIDDEN_READS = ["SELECT * FROM public.feed_oauth", "SELECT * FROM public.ad_daily", "SELECT * FROM public.tracker_rows", "SELECT * FROM public.proj_scenarios",
                   "SELECT * FROM public.proj_po", "SELECT * FROM public.proj_actuals", "SELECT * FROM public.upstack_orders_daily", "SELECT * FROM public.etl_runs",
                   "SELECT * FROM public.creative_assets", "SELECT rolpassword FROM pg_authid", "SELECT * FROM pg_shadow",
                   # migration 230: the tables behind the new views and the ones it deliberately leaves out
                   "SELECT * FROM public.api_keys", "SELECT * FROM public.creative_priorities_views", "SELECT * FROM public.creative_priorities_vendor_links",
                   "SELECT * FROM public.creative_priorities_snapshots", "SELECT * FROM public.creative_priorities_assignments", "SELECT * FROM public.proj_audit",
                   "SELECT * FROM public.proj_scenario_history", "SELECT * FROM public.proj_cohorts", "SELECT * FROM public.proj_feed_orders", "SELECT * FROM public.competitor_ads",
                   "SELECT * FROM public.creative_launch_jobs", "SELECT * FROM public.creative_launch_settings", "SELECT * FROM public.creative_launch_audit",
                   "SELECT * FROM public.proj_feedx_orders", "SELECT * FROM public.alert_state", "SELECT * FROM public.app_meta"]
FORBIDDEN_WRITES = [
    "INSERT INTO analytics.orders_daily (day) VALUES ('2026-01-01')", "UPDATE analytics.etl_runs SET status = 'ok'", "DELETE FROM analytics.etl_runs",
    "TRUNCATE analytics.etl_runs", "CREATE TABLE analytics.zz (a int)", "CREATE TABLE public.zz (a int)", "CREATE VIEW analytics.zz AS SELECT 1",
    "CREATE SCHEMA zz", "DROP VIEW analytics.etl_runs", "COMMENT ON VIEW analytics.etl_runs IS 'x'", "ALTER VIEW analytics.etl_runs RENAME TO zz",
    "INSERT INTO public.ad_daily (ad_id, day) VALUES ('x', '2026-01-01')", "UPDATE public.tracker_rows SET status = 'x'", "DELETE FROM public.ad_daily",
    "DROP TABLE public.ad_daily", "CREATE ROLE zz", "CREATE EXTENSION IF NOT EXISTS dblink",
    "COPY public.ad_daily TO '/tmp/zz'", "CREATE FUNCTION public.zz() RETURNS int LANGUAGE sql AS 'select 1'",
]


@pytest.mark.parametrize("stmt", FORBIDDEN_READS)
def test_reader_cannot_read_anything_outside_analytics(cluster, outfile, stmt):
    with connect_reader(read_url(outfile)) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt)


@pytest.mark.parametrize("stmt", FORBIDDEN_WRITES)
def test_reader_cannot_write_or_create_even_with_the_read_only_default_switched_off(cluster, outfile, stmt):
    """default_transaction_read_only is a convenience a session can undo; the privileges are the guard. Both layers are checked."""
    with connect_reader(read_url(outfile)) as c:
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
            c.execute(stmt)
        c.execute("SET default_transaction_read_only = off")
        c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.WrongObjectType)):          # TRUNCATE of a view is refused before the privilege check
            c.execute(stmt)


def test_reader_cannot_hand_out_access_to_anyone(cluster, outfile, admin):
    """GRANT by a role without grant option is not an error in Postgres (a warning, nothing granted): check that nothing was."""
    with connect_reader(read_url(outfile)) as c:
        c.execute("SET default_transaction_read_only = off")
        c.execute("GRANT SELECT ON analytics.etl_runs TO PUBLIC")
        c.execute("GRANT ALL ON analytics.etl_runs TO postgres")
    acl = admin.execute("SELECT relacl::text FROM pg_class WHERE oid = 'analytics.etl_runs'::regclass").fetchone()[0]
    import re
    assert not re.search(r"[{,]=r", acl) and ROLE + "=r/" in acl, acl                # no PUBLIC entry ('=r/...'); the reader still holds exactly SELECT


def test_reader_has_no_dangerous_attributes(admin):
    r = admin.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone()
    assert r == (False, False, False, False, False, False, True, 5)
    assert admin.execute("SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = %s)", (ROLE,)).fetchone()[0] == 0
    st = rr.status(admin, ROLE)
    assert st["problems"] == [] and st["readable_outside_analytics"] == [] and st["analytics_relations"] >= 12


def test_connection_limit_is_enforced(cluster, outfile):
    url = read_url(outfile)
    conns = [connect_reader(url) for _ in range(5)]
    try:
        with pytest.raises(psycopg.OperationalError, match="too many connections"):
            connect_reader(url)
    finally:
        for c in conns:
            c.close()


# ---------------------------------------------------------------------------------------------------------------------------- policy drift, new views, status
def test_status_reports_drift_and_create_repairs_it(cluster, monkeypatch, outfile, admin, capsys):
    assert cli(cluster, monkeypatch, outfile, "status") == 0
    assert "ok: SELECT on every analytics view" in capsys.readouterr().out
    admin.execute(f"GRANT SELECT ON public.ad_daily TO {ROLE}")
    assert cli(cluster, monkeypatch, outfile, "status") == 1
    assert "DRIFT: privilege on public.ad_daily" in capsys.readouterr().out
    before = read_url(outfile)
    assert cli(cluster, monkeypatch, outfile, "create") == 0                      # exists: re-applies the policy, never touches the password or the file
    out = capsys.readouterr().out
    assert "already exists" in out and "password unchanged" in out
    assert read_url(outfile) == before
    assert cli(cluster, monkeypatch, outfile, "status") == 0
    with connect_reader(before) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM public.ad_daily")


def test_a_view_added_later_is_readable_without_another_grant(cluster, outfile, admin):
    admin.execute("CREATE VIEW analytics.zz_later AS SELECT 1 AS x")
    try:
        with connect_reader(read_url(outfile)) as c:
            assert c.execute("SELECT x FROM analytics.zz_later").fetchone()[0] == 1
    finally:
        admin.execute("DROP VIEW analytics.zz_later")


# ---------------------------------------------------------------------------------------------------------------------------- rotate and drop
def test_rotate_changes_the_password_ends_old_sessions_and_prints_no_secret(cluster, monkeypatch, outfile, admin, capsys):
    old_url = read_url(outfile)
    old_pw = password_of(old_url)
    old_conn = connect_reader(old_url)
    old_conn.execute("SELECT 1")
    stored_before = admin.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (ROLE,)).fetchone()[0]
    rc = cli(cluster, monkeypatch, outfile, "rotate")
    out = capsys.readouterr()
    assert rc == 0
    new_url = read_url(outfile)
    new_pw = password_of(new_url)
    assert new_pw != old_pw and new_url != old_url
    for stream in (out.out, out.err):
        assert old_pw not in stream and new_pw not in stream and new_url not in stream and "postgresql://" not in stream
    assert outfile in out.out and "1 session(s)" in out.out
    assert stat.S_IMODE(os.stat(outfile).st_mode) == 0o600
    assert admin.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (ROLE,)).fetchone()[0] != stored_before
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        connect_reader(old_url)
    with connect_reader(new_url) as c:
        assert c.execute("SELECT count(*) FROM analytics.orders_daily").fetchone()[0] >= 0
    with pytest.raises(psycopg.Error):                                            # the session opened with the old password was terminated
        old_conn.execute("SELECT 1")
    old_conn.close()
    log = Path(cluster["log"]).read_text()
    assert old_pw not in log and new_pw not in log


def test_rotate_needs_an_existing_role(cluster, monkeypatch, outfile, capsys):
    monkeypatch.setenv("DATABASE_URL", cluster["admin"])
    rc = rr.main(["rotate", "--role", "analytics_reader_missing", "--out", outfile, "--host", "127.0.0.1", "--port", str(cluster["port"])])
    assert rc == 1 and "does not exist" in capsys.readouterr().err


def test_drop_needs_yes_and_removes_the_role_and_its_privileges(cluster, monkeypatch, outfile, admin, capsys):
    assert cli(cluster, monkeypatch, outfile, "drop") == 1
    assert "--yes" in capsys.readouterr().err and rr.role_exists(admin, ROLE)
    assert cli(cluster, monkeypatch, outfile, "drop", "--yes") == 0
    assert not rr.role_exists(admin, ROLE)
    assert cli(cluster, monkeypatch, outfile, "drop", "--yes") == 0                  # idempotent
    # a fresh create after a drop works and starts clean
    assert cli(cluster, monkeypatch, outfile, "create") == 0
    with connect_reader(read_url(outfile)) as c:
        assert c.execute("SELECT count(*) FROM analytics.meta_ads").fetchone()[0] > 0
    assert rr.drop(admin, ROLE)["dropped"]
