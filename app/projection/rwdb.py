"""Read-write Postgres access for projection: its own pool, separate from the app's read-only pool (which stays untouched).

    with rwdb.get_rw() as conn:        # dict rows, autocommit off, statement_timeout 20 s
        conn.execute(...)              # commits when the block exits, rolls back if it raises

The pool is created lazily per process (so gunicorn workers each get their own after the fork), keyed by database URL, and closed at exit.
"""
from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager

import psycopg
from flask import current_app, has_app_context
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIN_SIZE = 1
MAX_SIZE = 4
STATEMENT_TIMEOUT_MS = 20_000

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()
_atexit_registered = False


def _configure(conn: psycopg.Connection) -> None:
    conn.autocommit = True
    conn.execute(f"SET statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
    conn.autocommit = False


def database_url() -> str:
    url = current_app.config.get("DATABASE_URL") if has_app_context() else None
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url


def get_pool(url: str | None = None) -> ConnectionPool:
    global _atexit_registered
    url = url or database_url()
    with _lock:
        pool = _pools.get(url)
        if pool is None:
            pool = ConnectionPool(conninfo=url, min_size=MIN_SIZE, max_size=MAX_SIZE, kwargs={"row_factory": dict_row},
                                  configure=_configure, timeout=10, open=False)
            pool.open(wait=False)
            _pools[url] = pool
            if not _atexit_registered:
                atexit.register(close_all)
                _atexit_registered = True
        return pool


@contextmanager
def get_rw(url: str | None = None):
    """A pooled read-write connection; the block is one transaction (commit on success, rollback on any exception)."""
    with get_pool(url).connection() as conn:
        yield conn


def close_all() -> None:
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for p in pools:
        try:
            p.close()
        except Exception:                        # shutting down: nothing useful to do with a close error
            pass
