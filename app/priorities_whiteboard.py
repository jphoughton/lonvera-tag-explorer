"""The "This week" whiteboard of the Creative priorities page (/api/priorities/whiteboard). Tables creative_whiteboard_picks and creative_whiteboard_events (migration 284).
One read (GET) and one full-state write (PUT): the client sends the board it wants, array order is the rank, the server stores what changed and logs one event per changed card.
A drop is a status flip. Concepts the client does not mention are left alone. Nothing here writes to the Google Sheet or Meta."""
from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Jsonb

from . import priorities_assign as pa, queries as q

MAX_ITEMS = 300
LANES = ("must", "if_time", "parked")
TAGS = ("product", "cluster", "icp", "pain_point", "line_code")
NOT_READY = "The whiteboard is not set up yet (migration 284 is not applied)."
SHOWN = "p.id, p.concept_key, p.product, p.cluster, p.icp, p.pain_point, p.line_code, p.title, p.lane, p.rank, p.note, p.rev, p.updated_by, p.updated_at"
IN_PROD = ("EXISTS (SELECT 1 FROM creative_priorities_assignments a WHERE a.concept_key = p.concept_key AND a.for_week = p.week AND a.dropped_at IS NULL) AS in_production")


def etag(week, version: int) -> str:
    return f'W/"wb-{week.isoformat()}-{version}"'


def _shape(r: dict) -> dict:
    return {**r, "rank": float(r["rank"]), "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None}


def version_of(conn, week) -> int:
    try:
        return int(conn.execute("SELECT COALESCE(max(id), 0) AS v FROM creative_whiteboard_events WHERE week = %s", (week,)).fetchone()["v"])
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return 0


def read_week(conn, week) -> dict:
    """{week, version, picks: [...lane order then rank], dropped: [...newest first]} for one Monday."""
    try:
        rows = conn.execute(
            f"""SELECT {SHOWN}, p.status, {IN_PROD} FROM creative_whiteboard_picks p WHERE p.week = %s
                ORDER BY CASE p.lane WHEN 'must' THEN 0 WHEN 'if_time' THEN 1 ELSE 2 END, p.rank, p.id""", (week,)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {"week": week.isoformat(), "version": 0, "picks": [], "dropped": [], "ready": False, "message": NOT_READY}
    picks = [_shape({k: v for k, v in r.items() if k != "status"}) for r in rows if r["status"] == "picked"]
    dropped = sorted((r for r in rows if r["status"] == "dropped"), key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    return {"week": week.isoformat(), "version": version_of(conn, week), "picks": picks,
            "dropped": [_shape({k: v for k, v in r.items() if k != "status"}) for r in dropped]}


def _clean(it, field: str, lane_required: bool) -> dict:
    if not isinstance(it, dict):
        raise q.ApiError(400, f"each {field} item must be an object with concept_key" + (" and lane" if lane_required else ""))
    key = it.get("concept_key")
    if not isinstance(key, str) or not key.strip():
        raise q.ApiError(400, "concept_key is required")
    key = key.strip()
    if len(key) > 400:
        raise q.ApiError(400, "concept_key is at most 400 characters")
    out: dict = {"concept_key": key}
    if lane_required:
        lane = it.get("lane")
        if lane not in LANES:
            raise q.ApiError(400, "lane must be one of: " + ", ".join(LANES))
        out["lane"] = lane
    out["note"] = pa._text(it["note"], "note", 500) if it.get("note") is not None else None
    for k in TAGS:
        out[k] = pa._text(it.get(k), k.replace("_", " "), 200)
    if out["product"] and out["product"] not in pa.PRODUCT_SLUGS:
        raise q.ApiError(400, "product must be one of: " + ", ".join(pa.PRODUCT_SLUGS))
    out["title"] = pa._text(it.get("title"), "title", 200)
    return out


def _state(r: dict) -> dict:
    return {"lane": r["lane"], "rank": float(r["rank"]), "status": r["status"], "note": r["note"]}


def save(conn, body: dict, user: str | None = None) -> tuple:
    """PUT {week, picks: [{concept_key, lane, note?, tags?, title?}], dropped: [{concept_key, tags?, title?}]} -> (shape of read_week, version before, changed card count)."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    week = pa.monday_of(body.get("week") or pa.today_pt())
    picks, dropped = body.get("picks", []), body.get("dropped", [])
    if not isinstance(picks, list) or not isinstance(dropped, list):
        raise q.ApiError(400, "picks and dropped must be lists")
    if len(picks) + len(dropped) > MAX_ITEMS:
        raise q.ApiError(400, f"at most {MAX_ITEMS} items per save")
    removed = body.get("removed", [])
    if not isinstance(removed, list) or not all(isinstance(k, str) and 0 < len(k) <= 400 for k in removed):
        raise q.ApiError(400, "removed must be a list of concept keys")
    P = [_clean(it, "picks", True) for it in picks]
    D = [_clean(it, "dropped", False) for it in dropped]
    keys = [p["concept_key"] for p in P] + [d["concept_key"] for d in D]
    keys += [k for k in removed if k not in keys]
    if len(set(keys)) != len(keys) or len(keys) > MAX_ITEMS:
        raise q.ApiError(400, "a concept can appear only once in picks and dropped")
    batch = uuid.uuid4()
    changed = 0
    try:
        with pa._rw() as w:
            w.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"whiteboard:{week.isoformat()}",))
            have = {r["concept_key"]: r for r in w.execute("SELECT * FROM creative_whiteboard_picks WHERE week = %s AND concept_key = ANY(%s)", (week, keys)).fetchall()} if keys else {}

            def log(kind, row_id, before, after):
                w.execute("INSERT INTO creative_whiteboard_events (week, pick_id, actor, kind, batch, before, after) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                          (week, row_id, user, kind, batch, Jsonb(before) if before else None, Jsonb(after)))

            def blank_tags(c, r):
                return {k: c[k] for k in (*TAGS, "title") if c[k] and not r[k]}

            for i, c in enumerate(P, 1):
                r = have.get(c["concept_key"])
                if r is None:
                    new = w.execute(
                        """INSERT INTO creative_whiteboard_picks (week, concept_key, product, cluster, icp, pain_point, line_code, title, lane, status, rank, note, created_by, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'picked',%s,%s,%s,%s) RETURNING *""",
                        (week, c["concept_key"], c["product"], c["cluster"], c["icp"], c["pain_point"], c["line_code"], c["title"], c["lane"], i, c["note"] or "", user, user)).fetchone()
                    log("pick", new["id"], None, _state(new))
                    changed += 1
                    continue
                note = r["note"] if c["note"] is None else c["note"]
                after = {"lane": c["lane"], "rank": float(i), "status": "picked", "note": note}
                before = _state(r)
                if after == before:
                    continue
                kind = "restore" if r["status"] == "dropped" else "move" if (after["lane"], after["rank"]) != (before["lane"], before["rank"]) else "note"
                extra = blank_tags(c, r)
                sets = ", ".join(f"{k} = %s" for k in extra)
                w.execute(f"UPDATE creative_whiteboard_picks SET lane=%s, rank=%s, status='picked', note=%s, rev=rev+1, updated_by=%s, updated_at=now(){', ' + sets if sets else ''} WHERE id=%s",
                          (c["lane"], i, note, user, *extra.values(), r["id"]))
                log(kind, r["id"], before, after)
                changed += 1
            for c in D:
                r = have.get(c["concept_key"])
                if r is None:
                    new = w.execute(
                        """INSERT INTO creative_whiteboard_picks (week, concept_key, product, cluster, icp, pain_point, line_code, title, status, note, created_by, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'dropped',%s,%s,%s) RETURNING *""",
                        (week, c["concept_key"], c["product"], c["cluster"], c["icp"], c["pain_point"], c["line_code"], c["title"], c["note"] or "", user, user)).fetchone()
                    log("drop", new["id"], None, _state(new))
                    changed += 1
                elif r["status"] == "picked":
                    note = r["note"] if c["note"] is None else c["note"]
                    w.execute("UPDATE creative_whiteboard_picks SET status='dropped', note=%s, rev=rev+1, updated_by=%s, updated_at=now() WHERE id=%s", (note, user, r["id"]))
                    log("drop", r["id"], _state(r), {**_state(r), "status": "dropped", "note": note})
                    changed += 1
            for k in removed:
                r = have.get(k)
                if r is not None and k not in {c["concept_key"] for c in P} | {c["concept_key"] for c in D}:
                    w.execute("DELETE FROM creative_whiteboard_picks WHERE id = %s", (r["id"],))
                    log("remove", r["id"], _state(r), {})
                    changed += 1
            out = read_week(w, week)
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    return out, changed
