"""Saved views of the Creative Pipeline By cut page (/api/priorities/cut-views): a name plus the page state, shared by the team. Table creative_cut_views (migration 270).
The state is whatever the page needs to rebuild itself; the server keeps it small and only keeps the keys the page knows (nothing else is stored). Nothing here touches ads, Meta or the sheet."""
from __future__ import annotations

import json

import psycopg

from . import priorities_assign as pa, priorities_cuts as pc, queries as q

MAX_BYTES = 8000
MAX_VIEWS = 200
NOT_READY = "Saved views are not set up yet (migration 270 is not applied)."
STAGES = pc.STATUSES
SORT_KEYS_OK = lambda k: isinstance(k, str) and len(k) <= 60                      # noqa: E731
TAG_DIMS = ("cluster", "icp", "pain_point")


def _strs(v, name: str, limit: int = 60, item: int = 200) -> list[str]:
    if not isinstance(v, list) or len(v) > limit or not all(isinstance(x, str) and len(x) <= item for x in v):
        raise q.ApiError(400, f"{name} must be a list of up to {limit} short texts")
    return v


def clean_state(raw) -> dict:
    """Validate and trim a page state. Unknown keys are dropped; wrong types are a 400."""
    if not isinstance(raw, dict):
        raise q.ApiError(400, "state must be an object")
    out: dict = {}
    if "by" in raw:
        by = _strs(raw["by"], "by", 6, 20)
        if not by or any(d not in pc.DIMS for d in by) or len(set(by)) != len(by):
            raise q.ApiError(400, "by must be a list of cuts: " + ", ".join(pc.DIMS))
        out["by"] = by
    if "preset" in raw:
        if raw["preset"] not in ("", "7", "14", "30", "90", "mtd", "lastmonth"):
            raise q.ApiError(400, "preset must be one of 7, 14, 30, 90, mtd, lastmonth or empty (custom dates)")
        out["preset"] = raw["preset"]
    for k in ("from", "to"):
        if raw.get(k):
            out[k] = pc._day(str(raw[k]), k).isoformat()
    if "f" in raw:
        if not isinstance(raw["f"], dict):
            raise q.ApiError(400, "f must be an object")
        out["f"] = {d: _strs(raw["f"].get(d, []), "f." + d, 200) for d in TAG_DIMS}
    if "metrics" in raw:
        out["metrics"] = _strs(raw["metrics"], "metrics", 40, 40)
    if "order" in raw:
        out["order"] = _strs(raw["order"], "order", 40, 40)
    if "stages" in raw:
        st = _strs(raw["stages"], "stages", 10, 30)
        if any(s not in STAGES for s in st):
            raise q.ApiError(400, "stages must be board statuses: " + ", ".join(STAGES))
        out["stages"] = st
    if "quick" in raw:
        if not isinstance(raw["quick"], str) or len(raw["quick"]) > 30:
            raise q.ApiError(400, "quick must be a short text")
        out["quick"] = raw["quick"]
    if "sort" in raw:
        s = raw["sort"]
        if not isinstance(s, dict) or not SORT_KEYS_OK(s.get("key", "")) or s.get("dir", "desc") not in ("asc", "desc"):
            raise q.ApiError(400, "sort must be {key, dir: asc or desc}")
        out["sort"] = {"key": s.get("key", ""), "dir": s.get("dir", "desc")}
    if "pipelineOnly" in raw:
        out["pipelineOnly"] = bool(raw["pipelineOnly"])
    for k in ("product", "vendor", "week"):                                       # the page's own filters (comma lists)
        if k in raw:
            if not isinstance(raw[k], str) or len(raw[k]) > 300:
                raise q.ApiError(400, f"{k} must be a short text")
            out[k] = raw[k]
    if len(json.dumps(out)) > MAX_BYTES:
        raise q.ApiError(400, "that view is too large to save")
    return out


def _shape(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], "state": r["state"], "created_by": r["created_by"], "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}


def list_views(conn) -> dict:
    try:
        rows = conn.execute("SELECT id, name, state, created_by, created_at, updated_at FROM creative_cut_views ORDER BY lower(name), id").fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {"views": [], "ready": False, "message": NOT_READY}
    return {"views": [_shape(r) for r in rows], "ready": True}


def save_view(conn, body: dict, user: str | None = None) -> dict:
    """{name, state} -> the saved view. A name that exists (any case) is replaced: saving over a view updates it."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    name = pa._text(body.get("name"), "name", 80)
    if not name:
        raise q.ApiError(400, "give the view a name")
    state = clean_state(body.get("state"))
    try:
        with pa._rw() as w:
            if w.execute("SELECT count(*) AS n FROM creative_cut_views WHERE lower(name) <> lower(%s)", (name,)).fetchone()["n"] >= MAX_VIEWS:
                raise q.ApiError(400, f"at most {MAX_VIEWS} saved views")
            r = w.execute("""INSERT INTO creative_cut_views (name, state, created_by) VALUES (%s, %s::jsonb, %s)
                             ON CONFLICT (lower(name)) DO UPDATE SET name = EXCLUDED.name, state = EXCLUDED.state, updated_at = now()
                             RETURNING id, name, state, created_by, created_at, updated_at""", (name, json.dumps(state), user)).fetchone()
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    return _shape(r)


def delete_view(conn, view_id: int) -> dict:
    try:
        with pa._rw() as w:
            n = w.execute("DELETE FROM creative_cut_views WHERE id = %s", (view_id,)).rowcount
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    if not n:
        raise q.ApiError(404, "No saved view with that id.")
    return {"removed": n}
