"""Creative priorities (/priorities, /api/priorities): the weekly one-sheeter written by etl.creative_priorities (table creative_priorities_snapshots, migration 196).
Reads one stored snapshot; nothing is computed on request. See docs/CREATIVE_PRIORITIES.md."""
from __future__ import annotations

import datetime as dt
import re
import secrets

from psycopg.types.json import Jsonb

from . import priorities_manual as pm, queries as q

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
    snap = pm.merge(snapshot(conn, raw_week), pm.list_records(conn))        # manual concepts are not tied to a week: they ride along on every snapshot (done ones flagged, hidden by the page)
    comp = (snap.get("stats") or {}).get("competitors")
    return {**snap, "competitors_as_of": comp["as_of"] if comp else None, "competitor_stale": (comp or {}).get("stale_brands", []), "weeks": weeks(conn), "legend": [{"tag": a, "meaning": b} for a, b in cp.LEGEND]}


def row_key(r: dict) -> str:
    """The stable row key (same join as the page): product|section|cluster|icp|pain_point, unique per row within a snapshot; a manual concept is manual:<id>."""
    if r.get("manual"):
        return str(r["key"])
    return "|".join(str(r.get(k)) for k in ("product", "section", "cluster", "icp", "pain_point"))


def filter_rows(snap: dict, ids: list[str] | None) -> dict:
    """Snapshot restricted to the rows whose key is in ids (None or empty = everything)."""
    if not ids:
        return snap
    keep = set(ids)
    return {**snap, "rows": [r for r in snap["rows"] if row_key(r) in keep]}


def csv_text(conn, raw_week: str | None, ids: list[str] | None = None, briefed_token: str | None = None) -> tuple[str, str]:
    """briefed_token: a view's token; adds the briefed columns from that view (an in-page view exported with the latest data)."""
    from etl import creative_priorities as cp
    snap = snapshot(conn, raw_week)
    snap = filter_rows(pm.merge(snap, pm.list_records(conn), include_done=bool(ids)), ids)       # with no selection the full CSV leaves done manual concepts out; a selected one is exported
    return snap["week_start"], cp.to_csv(snap, briefed=get_view(briefed_token)["view"]["briefed"] if briefed_token else None)


# ---------------------------------------------------------------------------------------------------------------- saved views
MAX_VIEW_ROWS = 500
MAX_NAME = 120
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,64}")
VIEW_BODY_BYTES = 256 * 1024


def default_name(day: dt.date | None = None) -> str:
    d = day or dt.date.today()
    return f"Priorities - {d.month}/{d.day}/{d.year % 100:02d}"


def clean_name(raw, day: dt.date | None = None) -> str:
    """Free text (slashes, dashes, spaces all fine), whitespace collapsed, trimmed, at most 120 characters; empty becomes 'Priorities - M/D/YY'."""
    if raw is not None and not isinstance(raw, str):
        raise q.ApiError(400, "name must be text")
    name = " ".join((raw or "").split())[:MAX_NAME].strip()
    return name or default_name(day)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "view"


def _views_conn():
    from .projection import rwdb          # its own read-write pool: the app's pool is read-only
    return rwdb.get_rw()


FILTER_KEYS = ("products", "sections", "q", "key", "dir", "status")


def clean_filters(raw) -> dict:
    """The saved filter state: products / sections (lists of short strings; empty = all), q (search text), key (sort column or None), dir (1 or -1). Anything else is dropped."""
    if not isinstance(raw, dict):
        raise q.ApiError(400, "filters must be an object")
    out: dict = {}
    for k in ("products", "sections"):
        v = raw.get(k) or []
        if not isinstance(v, list) or len(v) > 30 or not all(isinstance(x, str) and 0 < len(x) <= 60 for x in v):
            raise q.ApiError(400, f"filters.{k} must be a short list of strings")
        out[k] = list(dict.fromkeys(v))
    qq = raw.get("q") or ""
    if not isinstance(qq, str) or len(qq) > 200:
        raise q.ApiError(400, "filters.q must be text of at most 200 characters")
    out["q"] = qq.strip()
    key = raw.get("key")
    if key is not None and (not isinstance(key, str) or len(key) > 30):
        raise q.ApiError(400, "filters.key must be a column name")
    out["key"] = key or None
    out["dir"] = -1 if raw.get("dir") == -1 else 1
    st = raw.get("status")
    if st is not None and (not isinstance(st, str) or len(st) > 60):
        raise q.ApiError(400, "filters.status must be a status name")
    out["status"] = st or None
    return out


def apply_filters(rows: list[dict], f: dict) -> list[dict]:
    """Rows matching a saved filter state (same rules as the page: product and section membership, search over cluster / ICP / pain point / story). Order is kept."""
    prods, secs, needle = set(f.get("products") or []), set(f.get("sections") or []), (f.get("q") or "").strip().lower()
    status = f.get("status")
    return [r for r in rows if (not prods or r.get("product") in prods) and (not secs or r.get("section") in secs) and (not status or r.get("status") == status)
            and (not needle or any(needle in str(r.get(k) or "").lower() for k in ("cluster", "icp", "pain_point", "story")))]


def build_view(full: dict, body: dict) -> dict:
    """Validate a create / update body against the full snapshot payload and return the record to store: kind, filters, keys, payload (frozen rows + baselines etc.), name, missing.
    kind 'rows' needs ids (row keys); kind 'filters' needs filters (frozen rows = those matching right now)."""
    kind = body.get("kind") or "rows"
    if kind not in ("rows", "filters"):
        raise q.ApiError(400, "kind must be rows or filters")
    filters = None
    if kind == "rows":
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and 0 < len(i) <= 600 for i in ids):
            raise q.ApiError(400, "ids must be a non-empty list of row keys")
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_VIEW_ROWS:
            raise q.ApiError(400, f"A saved view holds at most {MAX_VIEW_ROWS} rows.")
        keep = set(ids)
        rows = [r for r in full["rows"] if row_key(r) in keep]
        if not rows:
            raise q.ApiError(400, "None of the selected rows exist in that snapshot.")
        missing = len(ids) - len(rows)
    else:
        filters = clean_filters(body.get("filters"))
        rows = apply_filters(full["rows"], filters)
        missing = 0
        if len(rows) > MAX_VIEW_ROWS:
            rows = rows[:MAX_VIEW_ROWS]
    meta = {k: v for k, v in full.items() if k not in ("rows", "weeks", "stats")}
    return {"kind": kind, "filters": filters, "keys": [row_key(r) for r in rows] if kind == "rows" else [], "payload": {**meta, "rows": rows}, "missing": missing, "count": len(rows),
            "week_start": full["week_start"]}


def create_view(conn, body: dict) -> dict:
    name = clean_name(body.get("name"))
    full = payload(conn, body.get("week") if isinstance(body.get("week"), str) else None)
    v = build_view(full, body)
    token = secrets.token_urlsafe(24)
    with _views_conn() as w:
        w.execute("INSERT INTO creative_priorities_views (token, name, week_start, keys, payload, kind, filters) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (token, name, full["week_start"], v["keys"], Jsonb(v["payload"]), v["kind"], Jsonb(v["filters"]) if v["filters"] is not None else None))
    return {"token": token, "url": f"/priorities/v/{token}", "name": name, "count": v["count"], "missing": v["missing"], "week_start": full["week_start"], "kind": v["kind"]}


def update_view(conn, token: str, body: dict) -> dict:
    """Replace a view's rows / filters (and frozen copy) with the given ones, from the latest snapshot; the token, name and kind stay. Body: ids (kind rows) or filters (kind filters)."""
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    with _views_conn() as w:
        cur = w.execute("SELECT kind FROM creative_priorities_views WHERE token = %s", (token,)).fetchone()
    if cur is None:
        raise q.ApiError(404, "Saved view not found.")
    full = payload(conn, None)
    v = build_view(full, {**body, "kind": cur["kind"]})
    with _views_conn() as w:
        n = w.execute("UPDATE creative_priorities_views SET week_start=%s, keys=%s, payload=%s, filters=%s, updated_at=now() WHERE token=%s",
                      (full["week_start"], v["keys"], Jsonb(v["payload"]), Jsonb(v["filters"]) if v["filters"] is not None else None, token)).rowcount
    if not n:
        raise q.ApiError(404, "Saved view not found.")
    return {"token": token, "count": v["count"], "missing": v["missing"], "week_start": full["week_start"], "kind": v["kind"]}


def list_views(limit: int = 50) -> list[dict]:
    with _views_conn() as w:
        cur = w.execute("SELECT token, name, week_start, kind, filters, jsonb_array_length(payload->'rows') AS n, created_at FROM creative_priorities_views ORDER BY lower(name), created_at DESC LIMIT %s", (limit,))
        return [{"token": r["token"], "name": r["name"], "week_start": r["week_start"].isoformat(), "count": r["n"], "kind": r["kind"], "filters": r["filters"], "created_at": r["created_at"].isoformat(),
                 "url": f"/priorities/v/{r['token']}"} for r in cur.fetchall()]


def view_response(r: dict) -> dict:
    """The GET body for one stored view (shared with the UI test mock): the frozen payload plus a `view` block (name, kind, filters, row keys)."""
    p = r["payload"]
    return {**p, "weeks": [], "view": {"token": r["token"], "name": r["name"], "week_start": r["week_start"].isoformat() if hasattr(r["week_start"], "isoformat") else r["week_start"],
                                       "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"], "count": len(p["rows"]),
                                       "kind": r.get("kind") or "rows", "filters": r.get("filters"), "keys": list(r.get("keys") or []), "briefed": r.get("briefed") or {}}}


def get_view(token: str) -> dict:
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    with _views_conn() as w:
        r = w.execute("SELECT token, name, week_start, payload, kind, filters, keys, briefed, created_at FROM creative_priorities_views WHERE token = %s", (token,)).fetchone()
    if r is None:
        raise q.ApiError(404, "Saved view not found.")
    return view_response(r)


def view_csv(token: str, ids: list[str] | None = None) -> tuple[str, str]:
    """(file slug, csv text): the slug is the slugified view name; ids (row keys) keep only those of the view's frozen rows."""
    from etl import creative_priorities as cp
    v = get_view(token)
    return slug(v["view"]["name"]), cp.to_csv(filter_rows({**v, "stats": {}}, ids), briefed=v["view"]["briefed"])


MAX_NOTE = 200


def clean_briefed(body: dict) -> tuple[str, bool, str]:
    """Validate a briefed toggle body {key, briefed, note?} -> (key, briefed, cleaned note: whitespace collapsed, printable only, at most 200 characters)."""
    key = body.get("key")
    if not isinstance(key, str) or not 0 < len(key) <= 600:
        raise q.ApiError(400, "key must be a row key")
    if not isinstance(body.get("briefed"), bool):
        raise q.ApiError(400, "briefed must be true or false")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise q.ApiError(400, "note must be text")
    note = "".join(ch for ch in " ".join((note or "").split()) if ch.isprintable())[:MAX_NOTE].strip()
    return key, body["briefed"], note


def set_briefed(conn, token: str, body: dict) -> dict:
    """Mark a concept briefed (or not) inside a view: {key, briefed: bool, note?}. The key must be a row of the view (its frozen rows) or, for a filters view, of the current snapshot.
    Stored per view + row key with a timestamp, so everyone with the link sees the same state."""
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    key, flag, note = clean_briefed(body)
    with _views_conn() as w:
        r = w.execute("SELECT kind, keys, payload FROM creative_priorities_views WHERE token = %s", (token,)).fetchone()
    if r is None:
        raise q.ApiError(404, "Saved view not found.")
    valid = set(r["keys"]) | {row_key(x) for x in r["payload"]["rows"]}
    if key not in valid and r["kind"] == "filters":
        valid |= {row_key(x) for x in payload(conn, None)["rows"]}
    if key not in valid:
        raise q.ApiError(404, "That concept is not in this view.")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    with _views_conn() as w:
        if flag:
            w.execute("UPDATE creative_priorities_views SET briefed = briefed || jsonb_build_object(%s::text, %s::jsonb) WHERE token = %s", (key, Jsonb({"at": now, "note": note}), token))
        else:
            w.execute("UPDATE creative_priorities_views SET briefed = briefed - %s::text WHERE token = %s", (key, token))
    return {"key": key, "briefed": flag, "at": now if flag else None, "note": note if flag else ""}


def rename_view(token: str, raw_name) -> dict:
    """Change the name only; the token (the link) stays. Empty -> the default name."""
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    name = clean_name(raw_name)
    with _views_conn() as w:
        n = w.execute("UPDATE creative_priorities_views SET name = %s WHERE token = %s", (name, token)).rowcount
    if not n:
        raise q.ApiError(404, "Saved view not found.")
    return {"token": token, "name": name}


def delete_view(token: str) -> None:
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Saved view not found.")
    with _views_conn() as w:
        n = w.execute("DELETE FROM creative_priorities_views WHERE token = %s", (token,)).rowcount
    if not n:
        raise q.ApiError(404, "Saved view not found.")
