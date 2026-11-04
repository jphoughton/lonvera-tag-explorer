"""Postgres access: one psycopg3 connection pool per process, one read-only snapshot transaction per request."""
from __future__ import annotations

import psycopg
from flask import Flask, current_app, g
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _make_configure(statement_timeout_ms: int):
    def configure(conn: psycopg.Connection) -> None:
        conn.autocommit = True
        conn.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
        conn.autocommit = False
        conn.read_only = True                                   # the app never writes
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ   # one consistent snapshot per request

    return configure


def init_app(app: Flask) -> None:
    url = app.config.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    pool = ConnectionPool(
        conninfo=url,
        min_size=app.config["DB_POOL_MIN"],
        max_size=app.config["DB_POOL_MAX"],
        kwargs={"row_factory": dict_row},
        configure=_make_configure(app.config["STATEMENT_TIMEOUT_MS"]),
        timeout=10,
        open=False,
    )
    pool.open(wait=False)
    app.extensions["pool"] = pool
    app.teardown_appcontext(_release)


def get_conn() -> psycopg.Connection:
    """The request's connection (checked out lazily, returned in teardown)."""
    conn = g.get("_conn")
    if conn is None:
        conn = current_app.extensions["pool"].getconn()
        g._conn = conn
    return conn


def _release(_exc) -> None:
    conn = g.pop("_conn", None)
    if conn is not None:
        try:
            conn.rollback()                                  # read-only snapshot transaction: nothing to keep
        finally:
            current_app.extensions["pool"].putconn(conn)


def ping(timeout: float = 3.0) -> bool:
    try:
        pool: ConnectionPool = current_app.extensions["pool"]
        with pool.connection(timeout=timeout) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


def close_pool(app: Flask) -> None:
    pool = app.extensions.get("pool")
    if pool is not None:
        pool.close()
