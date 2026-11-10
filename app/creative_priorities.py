"""Creative priorities (/priorities, /api/priorities): the weekly one-sheeter written by etl.creative_priorities (table creative_priorities_snapshots, migration 196).
Reads one stored snapshot; nothing is computed on request. See docs/CREATIVE_PRIORITIES.md."""
from __future__ import annotations

import datetime as dt
import re
import secrets

from psycopg.types.json import Jsonb

from . import queries as q

NOT_BUILT = "No creative priorities snapshot exists yet. It is written on Sundays by the nightly etl; run python -m etl.creative_priorities to write the current week."


def _week(raw: str | None) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        raise q.ApiError(400, "week must be a date, YYYY-MM-DD") from None
    return d


def weeks(conn) -> list[dict]:
    cur = conn.execute("SELECT week_start, built_at, data_through, jsonb_array_length(rows) AS n FROM creative_priorities_snapshots ORDER BY week_start DESC LIMIT 104")
    return [{"week_start": r["week_start"].isoformat(), "built_at": r["built_at"].isoformat(), "data_through": r["data_through"].isoformat(), "rows": r["n"]} for r in cur.fetchall()]


def snapshot(conn, raw_week: str | None) -> dict:
    """The snapshot of the given week (any day: the Sunday on or before it is used), else the latest."""
    wk = _week(raw_week)
    if wk is not None:
        wk = wk - dt.timedelta(days=(wk.weekday() + 1) % 7)
        row = conn.execute("SELECT week_start, built_at, data_through, basis, thresholds, baselines, rows, stats FROM creative_priorities_snapshots WHERE week_start = %s", (wk,)).fetchone()
        if row is None:
            raise q.ApiError(404, f"No creative priorities snapshot for the week of {wk.isoformat()}.")
    else:
        row = conn.execute("SELECT week_start, built_at, data_through, basis, thresholds, baselines, rows, stats FROM creative_priorities_snapshots ORDER BY week_start DESC LIMIT 1").fetchone()
        if row is None:
            raise q.ApiError(404, NOT_BUILT)
    return {"week_start": row["week_start"].isoformat(), "built_at": row["built_at"].isoformat(), "data_through": row["data_through"].isoformat(), "basis": row["basis"],
            "thresholds": row["thresholds"], "baselines": row["baselines"], "rows": row["rows"], "stats": row["stats"]}


def payload(conn, raw_week: str | None) -> dict:
    from etl import creative_priorities as cp
    snap = snapshot(conn, raw_week)
    comp = (snap.get("stats") or {}).get("competitors")
    return {**snap, "competitors_as_of": comp["as_of"] if comp else None, "competitor_stale": (comp or {}).get("stale_brands", []), "weeks": weeks(conn), "legend": [{"tag": a, "meaning": b} for a, b in cp.LEGEND]}


def row_key(r: dict) -> str:
    """The stable row key (same join as the page): product|section|cluster|icp|pain_point, unique per row within a snapshot."""
    return "|".join(str(r.get(k)) for k in ("product", "section", "cluster", "icp", "pain_point"))


def filter_rows(snap: dict, ids: list[str] | None) -> dict:
    """Snapshot restricted to the rows whose key is in ids (None or empty = everything)."""
    if not ids:
        return snap
    keep = set(ids)
    return {**snap, "rows": [r for r in snap["rows"] if row_key(r) in keep]}


def csv_text(conn, raw_week: str | None, ids: list[str] | None = None) -> tuple[str, str]:
    from etl import creative_priorities as cp
    snap = filter_rows(snapshot(conn, raw_week), ids)
    return snap["week_start"], cp.to_csv(snap)


# ---------------------------------------------------------------------------------------------------------------- saved views
MAX_VIEW_ROWS = 500
MAX_NAME = 120
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,64}")
VIEW_BODY_BYTES = 256 * 1024


def _views_conn():
    from .projection import rwdb          # its own read-write pool: the app's pool is read-only
    return rwdb.get_rw()


def _view_payload(snap: dict, ids: list[str], comp_snapshot: dict) -> dict:
    keep = set(ids)
    rows = [r for r in snap["rows"] if row_key(r) in keep]
    return {**comp_snapshot, "rows": rows}


def create_view(conn, body: dict) -> dict:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and 0 < len(i) <= 600 for i in ids):
        raise q.ApiError(400, "ids must be a non-empty list of row keys")
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_VIEW_ROWS:
        raise q.ApiError(400, f"A saved view holds at most {MAX_VIEW_ROWS} rows.")
    name = body.get("name") or ""
    if not isinstance(name, str):
        raise q.ApiError(400, "name must be text")
    name = " ".join(name.split())[:MAX_NAME]
    full = payload(conn, body.get("week") if isinstance(body.get("week"), str) else None)
    view = _view_payload(full, ids, {k: v for k, v in full.items() if k not in ("rows", "weeks", "stats")})
    if not view["rows"]:
        raise q.ApiError(400, "None of the selected rows exist in that snapshot.")
    kept = [row_key(r) for r in view["rows"]]
    token = secrets.token_urlsafe(24)
    with _views_conn() as w:
        w.execute("INSERT INTO creative_priorities_views (token, name, week_start, keys, payload) VALUES (%s,%s,%s,%s,%s)",
                  (token, name, full["week_start"], kept, Jsonb(view)))
    return {"token": token, "url": f"/priorities/v/{token}", "name": name, "count": len(kept), "missing": len(ids) - len(kept), "week_start": full["week_start"]}


def list_views(limit: int = 50) -> list[dict]:
    with _views_conn() as w:
        cur = w.execute("SELECT token, name, week_start, cardinality(keys) AS n, created_at FROM creative_priorities_views ORDER BY created_at DESC LIMIT %s", (limit,))
        return [{"token": r["token"], "name": r["name"], "week_start": r["week_start"].isoformat(), "count": r["n"], "created_at": r["created_at"].isoformat(), "url": f"/priorities/v/{r['token']}"} for r in cur.fetchall()]


def get_view(token: str) -> dict:
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    with _views_conn() as w:
        r = w.execute("SELECT token, name, week_start, payload, created_at FROM creative_priorities_views WHERE token = %s", (token,)).fetchone()
    if r is None:
        raise q.ApiError(404, "Saved view not found.")
    return {**r["payload"], "weeks": [], "view": {"token": r["token"], "name": r["name"], "week_start": r["week_start"].isoformat(), "created_at": r["created_at"].isoformat(), "count": len(r["payload"]["rows"])}}


def view_csv(token: str) -> tuple[str, str]:
    from etl import creative_priorities as cp
    v = get_view(token)
    return v["week_start"], cp.to_csv({**v, "stats": {}})


def delete_view(token: str) -> None:
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    with _views_conn() as w:
        n = w.execute("DELETE FROM creative_priorities_views WHERE token = %s", (token,)).rowcount
    if not n:
        raise q.ApiError(404, "Saved view not found.")
