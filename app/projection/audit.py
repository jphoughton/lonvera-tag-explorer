"""proj_audit: one row per mutation (who, what, old and new value). Rows are never updated or deleted by the app."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from flask import has_request_context, session
from psycopg.types.json import Jsonb

ANONYMOUS = "anonymous"
SYSTEM = "system"
MAX_LIMIT = 1000


def current_actor() -> str:
    """The signed-in user's name when the request carries one, 'anonymous' otherwise ('system' outside a request: scripts, loaders)."""
    if not has_request_context():
        return SYSTEM
    user = None
    try:
        from .. import auth                                # the parent app's auth module; newer versions expose current_user()
        fn = getattr(auth, "current_user", None)
        user = fn() if fn else None
    except Exception:                                      # an auth hiccup must never block (or fail) a write
        user = None
    if not user:
        user = session.get("u") or session.get("user")
    if isinstance(user, str) and user.strip() and user.strip().lower() != "guest":
        return user.strip()[:64]
    return ANONYMOUS


def _j(v: Any):
    return None if v is None else Jsonb(v)


AuditRow = tuple      # (actor, scenario_slug, action, line_key, month, old, new, note, version)

_SQL = "INSERT INTO proj_audit (actor, scenario_slug, action, line_key, month, old, new, note, version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"


def record(conn, *, actor: str | None, scenario: str | None, action: str, line_key: str | None = None, month: date | None = None,
           old: Any = None, new: Any = None, note: str | None = None, version: int | None = None) -> None:
    conn.execute(_SQL, (actor or current_actor(), scenario, action, line_key, month, _j(old), _j(new), note, version))


def record_many(conn, rows: Iterable[AuditRow]) -> int:
    """rows: (actor, scenario_slug, action, line_key, month, old, new, note, version) tuples; old/new are JSON-able or None."""
    prepared = [(a, s, act, k, m, _j(o), _j(n), note, v) for a, s, act, k, m, o, n, note, v in rows]
    if not prepared:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_SQL, prepared)
    return len(prepared)


def list_rows(conn, *, scenario: str | None = None, line_key: str | None = None, limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    where, args = [], []
    if scenario:
        where.append("scenario_slug = %s")
        args.append(scenario)
    if line_key:
        where.append("line_key = %s")
        args.append(line_key)
    sql = ("SELECT id, at, actor, scenario_slug, action, line_key, month, old, new, note, version FROM proj_audit"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT %s")
    return conn.execute(sql, (*args, limit)).fetchall()
