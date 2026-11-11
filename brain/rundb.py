"""Run one read-only query as analytics_reader.

The connection URL comes ONLY from the environment variable ANALYTICS_READER_URL (hosted web app and local use) or, for the local stdio server only, from the file named by
LONVERA_ANALYTICS_URL_FILE (default ~/lonvera-tag-explorer-data/analytics_reader_url.txt). The web app's own DATABASE_URL is never used here. The URL is never printed, logged or
returned: error text has it removed.
"""
from __future__ import annotations

import datetime as dt
import decimal
import os
import uuid
from pathlib import Path

import psycopg
import psycopg.conninfo

from .sqlguard import SqlRejected, check_select

ENV_URL = "ANALYTICS_READER_URL"
ENV_FILE = "LONVERA_ANALYTICS_URL_FILE"
DEFAULT_FILE = "~/lonvera-tag-explorer-data/analytics_reader_url.txt"
STATEMENT_TIMEOUT_MS = 30_000
MAX_ROWS = 1000
NOT_CONFIGURED = "SQL is not configured: ANALYTICS_READER_URL is not set on this server."


class SqlUnavailable(RuntimeError):
    pass


def reader_url(allow_file: bool = False) -> str | None:
    url = (os.environ.get(ENV_URL) or "").strip()
    if url:
        return url
    if allow_file:
        path = Path(os.environ.get(ENV_FILE) or DEFAULT_FILE).expanduser()
        try:
            return path.read_text().strip() or None
        except OSError:
            return None
    return None


def configured(allow_file: bool = False) -> bool:
    return reader_url(allow_file) is not None


def _clean(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time, dt.timedelta, uuid.UUID)):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    return v


def run_select(query: str, limit: int = 200, allow_file: bool = False) -> dict:
    """{columns, rows, row_count, truncated}. Raises SqlRejected (bad query), SqlUnavailable (not configured / database error)."""
    limit = max(1, min(int(limit or 200), MAX_ROWS))
    sql = check_select(query)
    url = reader_url(allow_file)
    if not url:
        raise SqlUnavailable(NOT_CONFIGURED)
    try:
        with psycopg.connect(url, connect_timeout=10, options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c default_transaction_read_only=on") as conn:
            conn.read_only = True                                # BEGIN READ ONLY: a write fails even if the role could write
            with conn.cursor(name="brain_q") as cur:             # server-side cursor: never pulls more than limit + 1 rows
                cur.execute(sql)
                got = cur.fetchmany(limit + 1)
                cols = [d.name for d in (cur.description or [])]
            conn.rollback()
    except SqlRejected:
        raise
    except psycopg.Error as e:
        msg = (getattr(e, "diag", None) and e.diag.message_primary) or e.__class__.__name__
        raise SqlUnavailable(_scrub(f"database error: {msg}", url)) from None
    truncated = len(got) > limit
    rows = [[_clean(v) for v in r] for r in got[:limit]]
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "note": f"cut at {limit} rows; add a LIMIT or an aggregate" if truncated else None}


def _scrub(text: str, url: str) -> str:
    out = text.replace(url, "<url>")
    try:
        parts = psycopg.conninfo.conninfo_to_dict(url)
        for k in ("password", "user", "host"):
            if parts.get(k):
                out = out.replace(str(parts[k]), "<" + k + ">")
    except Exception:                                            # noqa: BLE001
        pass
    return out
