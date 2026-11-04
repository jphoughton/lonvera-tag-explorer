"""Postgres helpers shared by every ETL job: connection, bulk helpers, job logging (etl_runs), secret scrubbing."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

_SECRET_PATTERNS = [
    re.compile(r"(access_token=)[^&\s'\"]+", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.I),
    re.compile(r"\bEA[A-Za-z0-9]{40,}\b"),          # Meta user/system tokens start with EA...
]


def scrub(text) -> str:
    """Remove anything that looks like a token (and the literal env token values) from text destined for logs / etl_runs."""
    s = str(text)
    for env in ("META_ACCESS_TOKEN", "GOOGLE_SA_JSON", "APP_PASSWORD", "SECRET_KEY"):
        v = os.environ.get(env)
        if v and len(v) > 8:
            s = s.replace(v, "***")
    for p in _SECRET_PATTERNS:
        s = p.sub(lambda m: (m.group(1) + "***") if p.groups else "***", s)
    return s


def log(*a):
    print(scrub(" ".join(str(x) for x in a)), flush=True)


def database_url(url: str | None = None) -> str:
    u = url or os.environ.get("DATABASE_URL")
    if not u:
        sys.exit("DATABASE_URL is not set (e.g. postgresql://localhost/tagexp_dev)")
    return u


def connect(url: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(database_url(url), autocommit=autocommit)


def jsonb(obj):
    return Jsonb(obj)


def bulk_copy(conn, table: str, cols: list[str], rows) -> int:
    """COPY rows into table (plain insert; caller handles conflicts by deleting first)."""
    n = 0
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
                n += 1
    return n


def execmany(conn, sql: str, rows) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def get_meta(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM app_meta WHERE key=%s", (key,)).fetchone()
    return r[0] if r else default


def set_meta(conn, key: str, value: str, only_if_missing: bool = False):
    if only_if_missing:
        conn.execute("INSERT INTO app_meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (key, str(value)))
    else:
        conn.execute("INSERT INTO app_meta (key,value,updated_at) VALUES (%s,%s,now()) "
                     "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, str(value)))


class JobRun:
    """etl_runs bookkeeping on its OWN autocommit connection so a failed / rolled-back job still leaves a 'failed' row."""

    def __init__(self, job: str, url: str | None = None):
        self.job = job
        self.url = url
        self.stats: dict = {}
        self.rows_written: int | None = None
        self.data_through: date | None = None
        self.run_id = None
        self._c = None

    def __enter__(self):
        self._c = connect(self.url, autocommit=True)
        self.run_id = self._c.execute("INSERT INTO etl_runs (job) VALUES (%s) RETURNING id", (self.job,)).fetchone()[0]
        log(f"[{self.job}] run #{self.run_id} started")
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self._c.execute("UPDATE etl_runs SET finished_at=now(), status='ok', data_through=%s, rows_written=%s, stats=%s WHERE id=%s",
                                (self.data_through, self.rows_written, Jsonb(self.stats), self.run_id))
                log(f"[{self.job}] run #{self.run_id} ok rows={self.rows_written} stats={json.dumps(self.stats, default=str)[:400]}")
            else:
                err = scrub("".join(traceback.format_exception(et, ev, tb))[-3000:])
                self._c.execute("UPDATE etl_runs SET finished_at=now(), status='failed', data_through=%s, rows_written=%s, stats=%s, error=%s WHERE id=%s",
                                (self.data_through, self.rows_written, Jsonb(self.stats), err, self.run_id))
                log(f"[{self.job}] run #{self.run_id} FAILED: {scrub(ev)}")
        finally:
            self._c.close()
        return False


@contextmanager
def timed(label: str):
    t = time.time()
    yield
    log(f"  {label}: {time.time() - t:.1f}s")


def d2s(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else d
