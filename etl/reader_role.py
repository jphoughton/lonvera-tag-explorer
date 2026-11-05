"""Read-only database access for people and agents: one LOGIN role that can SELECT from the `analytics` views and from nothing else.

    python -m etl.reader_role create   [--out FILE] [--role analytics_reader] [--host H --port P --dbname DB --sslmode MODE]
    python -m etl.reader_role rotate   [--out FILE] ...        new password, written to the file; sessions on the old password are ended
    python -m etl.reader_role status                           grants / settings / drift report (exit 1 on drift); prints no secret
    python -m etl.reader_role drop --yes                       end its sessions, drop its privileges, drop the role

Connects as the admin user of DATABASE_URL (the same variable every ETL job uses; from a laptop run it through `railway run -s etl sh -c
'DATABASE_URL="$DATABASE_PUBLIC_URL" python -m etl.reader_role create'`). The reader's connection URL is built from the host / port / database of
DATABASE_PUBLIC_URL (or --host / --port / --dbname) and written to --out (default ~/lonvera-tag-explorer-data/analytics_reader_url.txt), mode 600, a path that
must be OUTSIDE the repository. The password and the URL are NEVER printed or logged: stdout carries only the file path and a success line, and the password
reaches Postgres only as a SCRAM-SHA-256 verifier (the server, its logs and pg_stat_statements never see the clear text).

What the role gets (and everything it does not)
  * LOGIN, NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT, CONNECTION LIMIT 5
  * USAGE on schema analytics and SELECT on every view in it (ALTER DEFAULT PRIVILEGES makes views added later readable too). Nothing on schema public.
  * role defaults: default_transaction_read_only = on, statement_timeout = 30s, idle_in_transaction_session_timeout = 60s, temp_file_limit = 512MB,
    search_path = analytics, pg_catalog (so `select * from meta_ad_daily` works)
  The read-only default is a convenience, not the guard: a session can switch it off. What actually stops a write or a read of a public table is that the role
  has no privilege for it (views run with their owner's rights, so SELECT on a view needs no privilege on the tables behind it).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import psycopg
from psycopg import sql

from . import db

DEFAULT_ROLE = "analytics_reader"
SCHEMA = "analytics"
CONN_LIMIT = 5
STATEMENT_TIMEOUT = "30s"
IDLE_IN_TX_TIMEOUT = "60s"
TEMP_FILE_LIMIT = "512MB"
DEFAULT_OUT = "~/lonvera-tag-explorer-data/analytics_reader_url.txt"
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRAM_ITERATIONS = 4096
_ROLE_RX = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


class ReaderRoleError(Exception):
    """A failure with a message that is safe to print (never contains the password or the URL)."""


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """PostgreSQL SCRAM-SHA-256 verifier for `password` (RFC 5802 / 7677; the same string `ALTER ROLE ... PASSWORD` would store). The password is ASCII
    (token_urlsafe), so SASLprep is the identity."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def new_password() -> str:
    return secrets.token_urlsafe(32)


def check_role_name(role: str) -> str:
    if not _ROLE_RX.match(role or ""):
        raise ReaderRoleError("role name must be lower-case letters, digits and underscores, starting with a letter (3-41 characters)")
    return role


def role_exists(conn, role: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None


def _ident(role: str) -> sql.Identifier:
    return sql.Identifier(role)


# ---------------------------------------------------------------------------------------------------------------- grants and settings
def apply_policy(conn, role: str) -> list[str]:
    """(Re-)apply attributes, role defaults and grants: idempotent, safe to run any time. Returns warnings (a setting the admin may not set)."""
    r = _ident(role)
    warnings: list[str] = []
    conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT {}").format(r, sql.Literal(CONN_LIMIT)))
    for name, value in (("default_transaction_read_only", "on"), ("statement_timeout", STATEMENT_TIMEOUT), ("idle_in_transaction_session_timeout", IDLE_IN_TX_TIMEOUT),
                        ("search_path", f"{SCHEMA}, pg_catalog"), ("temp_file_limit", TEMP_FILE_LIMIT)):
        try:
            conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(r, sql.Identifier(name), sql.Literal(value) if name != "search_path" else sql.SQL(value)))
        except psycopg.errors.InsufficientPrivilege:
            warnings.append(f"could not set {name} (the admin user may not set it); the role works without it")
    dbname = conn.execute("SELECT current_database()").fetchone()[0]
    for stmt in (
        # start clean: nothing outside analytics, nothing beyond SELECT inside it
        sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(sql.Identifier(SCHEMA), r),
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(sql.Identifier(SCHEMA), r),
        sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(dbname), r),
        # the grants
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), r),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(SCHEMA), r),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(SCHEMA), r),
        # views created later by this admin user are readable without another grant
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(sql.Identifier(SCHEMA), r),
    ):
        conn.execute(stmt)
    return warnings


def set_password(conn, role: str, password: str) -> None:
    """ALTER ROLE ... PASSWORD with a SCRAM verifier, never the clear text. Any failure is re-raised without Postgres' echo of the statement."""
    verifier = scram_verifier(password)
    try:
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(_ident(role), sql.Literal(verifier)))
    except Exception as e:                                                   # noqa: BLE001
        raise ReaderRoleError(f"could not set the role's password ({type(e).__name__})") from None


def end_sessions(conn, role: str) -> int:
    """Terminate every session of the role (best effort: needs superuser or pg_signal_backend). Returns how many were signalled."""
    try:
        rows = conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s AND pid <> pg_backend_pid()", (role,)).fetchall()
        return sum(1 for (ok,) in rows if ok)
    except psycopg.errors.InsufficientPrivilege:
        return 0


# ---------------------------------------------------------------------------------------------------------------- the URL file
def _host_port_db(conn, host: str | None, port: int | None, dbname: str | None) -> tuple[str, int, str]:
    pub = urlparse(os.environ.get("DATABASE_PUBLIC_URL") or "")
    host = host or pub.hostname
    port = port or pub.port
    dbname = dbname or (pub.path or "").lstrip("/") or conn.execute("SELECT current_database()").fetchone()[0]
    if not host or not port:
        raise ReaderRoleError("cannot tell the host / port clients should use: set DATABASE_PUBLIC_URL (railway run -s etl ...) or pass --host and --port")
    return host, int(port), dbname


def reader_url(role: str, password: str, host: str, port: int, dbname: str, sslmode: str) -> str:
    return f"postgresql://{quote(role, safe='')}:{quote(password, safe='')}@{host}:{port}/{quote(dbname, safe='')}?sslmode={quote(sslmode, safe='')}"


def check_out_path(out: str) -> Path:
    p = Path(out).expanduser().resolve()
    try:
        p.relative_to(REPO_ROOT)
    except ValueError:
        return p
    raise ReaderRoleError(f"--out must be outside the repository ({REPO_ROOT}); the file holds a password")


def write_secret_file(path: Path, text: str) -> None:
    """Atomic write, mode 600, parent directory created mode 700 when missing."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text + "\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------------------------------------------- commands
def create(conn, role: str = DEFAULT_ROLE, out: str = DEFAULT_OUT, host: str | None = None, port: int | None = None, dbname: str | None = None,
           sslmode: str = "require") -> dict:
    role = check_role_name(role)
    path = check_out_path(out)
    exists = role_exists(conn, role)
    if exists:
        warnings = apply_policy(conn, role)
        return {"role": role, "created": False, "path": None, "warnings": warnings}
    host, port, dbname = _host_port_db(conn, host, port, dbname)
    if not conn.execute("SELECT to_regnamespace(%s) IS NOT NULL", (SCHEMA,)).fetchone()[0]:
        raise ReaderRoleError(f"schema {SCHEMA} does not exist: apply the migrations first (python -m etl.migrate)")
    password = new_password()
    conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT {}").format(_ident(role), sql.Literal(CONN_LIMIT)))
    set_password(conn, role, password)
    warnings = apply_policy(conn, role)
    write_secret_file(path, reader_url(role, password, host, port, dbname, sslmode))
    return {"role": role, "created": True, "path": path, "warnings": warnings}


def rotate(conn, role: str = DEFAULT_ROLE, out: str = DEFAULT_OUT, host: str | None = None, port: int | None = None, dbname: str | None = None,
           sslmode: str = "require") -> dict:
    role = check_role_name(role)
    path = check_out_path(out)
    if not role_exists(conn, role):
        raise ReaderRoleError(f"role {role} does not exist: run `python -m etl.reader_role create`")
    host, port, dbname = _host_port_db(conn, host, port, dbname)
    password = new_password()
    set_password(conn, role, password)
    warnings = apply_policy(conn, role)
    write_secret_file(path, reader_url(role, password, host, port, dbname, sslmode))
    return {"role": role, "path": path, "ended_sessions": end_sessions(conn, role), "warnings": warnings}


def drop(conn, role: str = DEFAULT_ROLE) -> dict:
    role = check_role_name(role)
    if not role_exists(conn, role):
        return {"role": role, "dropped": False}
    end_sessions(conn, role)
    conn.execute(sql.SQL("DROP OWNED BY {}").format(_ident(role)))
    conn.execute(sql.SQL("DROP ROLE {}").format(_ident(role)))
    return {"role": role, "dropped": True}


def status(conn, role: str = DEFAULT_ROLE) -> dict:
    """What the role can do right now, and every deviation from the policy. No secret is read or printed."""
    role = check_role_name(role)
    out: dict = {"role": role, "exists": False, "problems": []}
    row = conn.execute("""SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolconnlimit, rolconfig
                          FROM pg_roles WHERE rolname = %s""", (role,)).fetchone()
    if row is None:
        out["problems"].append("role does not exist")
        return out
    login, sup, cdb, crole, repl, bypass, limit, config = row
    out.update(exists=True, login=login, connection_limit=limit, config=sorted(config or []))
    for bad, label in ((sup, "superuser"), (cdb, "createdb"), (crole, "createrole"), (repl, "replication"), (bypass, "bypassrls")):
        if bad:
            out["problems"].append(f"role has {label}")
    if not login:
        out["problems"].append("role cannot log in")
    if limit != CONN_LIMIT:
        out["problems"].append(f"connection limit is {limit}, policy is {CONN_LIMIT}")
    cfg = {c.split("=", 1)[0]: c.split("=", 1)[1] for c in (config or [])}
    if cfg.get("default_transaction_read_only") != "on":
        out["problems"].append("default_transaction_read_only is not on")
    if cfg.get("statement_timeout") != STATEMENT_TIMEOUT:
        out["problems"].append(f"statement_timeout is {cfg.get('statement_timeout')}, policy is {STATEMENT_TIMEOUT}")
    # analytics: every view readable, nothing but SELECT
    objs = conn.execute("""SELECT c.oid, c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                           WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f')""", (SCHEMA,)).fetchall()
    out["analytics_relations"] = len(objs)
    if not conn.execute("SELECT has_schema_privilege(%s, %s, 'USAGE')", (role, SCHEMA)).fetchone()[0]:
        out["problems"].append(f"no USAGE on schema {SCHEMA}")
    if conn.execute("SELECT has_schema_privilege(%s, %s, 'CREATE')", (role, SCHEMA)).fetchone()[0]:
        out["problems"].append(f"CREATE on schema {SCHEMA}")
    for oid, name, kind in objs:
        if kind != "v":
            out["problems"].append(f"{SCHEMA}.{name} is not a view (relkind {kind})")
        if not conn.execute("SELECT has_table_privilege(%s, %s::oid, 'SELECT')", (role, oid)).fetchone()[0]:
            out["problems"].append(f"no SELECT on {SCHEMA}.{name}")
        if conn.execute("SELECT has_table_privilege(%s, %s::oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')", (role, oid)).fetchone()[0]:
            out["problems"].append(f"write privilege on {SCHEMA}.{name}")
    # everything outside analytics: nothing
    leaked = [n for (n,) in conn.execute("""SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                      WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') AND n.nspname <> %s AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_toast'
                      AND has_table_privilege(%s, c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')""", (SCHEMA, role)).fetchall()]
    out["readable_outside_analytics"] = leaked
    out["problems"] += [f"privilege on {n}" for n in leaked]
    for sch in [s for (s,) in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('information_schema', %s)", (SCHEMA,)).fetchall()]:
        if conn.execute("SELECT has_schema_privilege(%s, %s, 'CREATE')", (role, sch)).fetchone()[0]:
            out["problems"].append(f"CREATE on schema {sch}")
    out["sessions"] = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (role,)).fetchone()[0]
    return out


# ---------------------------------------------------------------------------------------------------------------- cli
def _fail(msg: str) -> int:
    print(f"reader_role: {msg}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etl.reader_role", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("create", "rotate", "status", "drop"))
    ap.add_argument("--role", default=DEFAULT_ROLE)
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"file for the connection URL (mode 600, outside the repo; default {DEFAULT_OUT})")
    ap.add_argument("--host", help="host clients connect to (default: DATABASE_PUBLIC_URL)")
    ap.add_argument("--port", type=int, help="port clients connect to (default: DATABASE_PUBLIC_URL)")
    ap.add_argument("--dbname", help="database name (default: DATABASE_PUBLIC_URL, else the connected database)")
    ap.add_argument("--sslmode", default="require", help="sslmode written into the URL (default require)")
    ap.add_argument("--yes", action="store_true", help="drop: confirm")
    a = ap.parse_args(argv)
    try:
        with db.connect(autocommit=True) as conn:
            if a.command == "create":
                r = create(conn, a.role, a.out, a.host, a.port, a.dbname, a.sslmode)
                for w in r["warnings"]:
                    print(f"warning: {w}")
                if r["created"]:
                    print(f"created role {r['role']}: connection URL written to {r['path']} (mode 600)")
                else:
                    print(f"role {r['role']} already exists: attributes and grants re-applied, password unchanged (use `rotate` for a new one)")
            elif a.command == "rotate":
                r = rotate(conn, a.role, a.out, a.host, a.port, a.dbname, a.sslmode)
                for w in r["warnings"]:
                    print(f"warning: {w}")
                print(f"rotated the password of {r['role']}: connection URL written to {r['path']} (mode 600); {r['ended_sessions']} session(s) on the old password ended")
            elif a.command == "drop":
                if not a.yes:
                    return _fail(f"this drops role {a.role}; pass --yes to confirm")
                r = drop(conn, a.role)
                print(f"role {r['role']} {'dropped' if r['dropped'] else 'did not exist'}")
            else:
                s = status(conn, a.role)
                if not s["exists"]:
                    print(f"role {s['role']} does not exist")
                    return 1
                print(f"role {s['role']}: login={s['login']} connection_limit={s['connection_limit']} sessions={s['sessions']} analytics_relations={s['analytics_relations']}")
                print("settings: " + ", ".join(s["config"]))
                if s["problems"]:
                    for p in s["problems"]:
                        print(f"DRIFT: {p}")
                    return 1
                print("ok: SELECT on every analytics view, nothing else")
    except ReaderRoleError as e:
        return _fail(str(e))
    except psycopg.Error as e:
        return _fail(f"database error ({type(e).__name__}): {str(e).splitlines()[0][:200] if str(e) else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
