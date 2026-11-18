"""The Proposed Concept lane of the Creative Pipeline Board (/api/priorities/backlog, formerly the Needs brief view): concepts the team decided to work on in a week that are not yet given to a partner. Table creative_backlog (migration 268).
Add cuts (from the By cut view or by hand), change their week or note, remove them, and assign a partner and a brief to several at once: that creates the real board tickets
(priorities_assign.create_ticket) and links them back. Nothing here writes to the Google Sheet or Meta."""
from __future__ import annotations

import datetime as dt

import psycopg

from . import priorities_assign as pa, queries as q

MAX_ITEMS = 100
COLS = "id, product, cluster, icp, pain_point, media_type, story, title, for_week, note, status, assignment_id, created_by, created_at, updated_at"
NOT_READY = "The Proposed Concept lane is not set up yet (migration 268 is not applied)."


NAMES = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vita Belle"}


def _shape(r: dict) -> dict:
    return {**r, "for_week": r["for_week"].isoformat(), "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
            "product_name": NAMES.get(r["product"], r["product"])}


def _title(product: str, cluster: str, icp: str, pain: str) -> str:
    return " - ".join(x for x in (NAMES.get(product, product), cluster, icp, pain) if x)[:200] or "Concept"


REQUIRED = (("product", "product"), ("cluster", "market cluster"), ("icp", "ICP"), ("pain_point", "pain point"), ("media_type", "image or video"))


def _need_tags(f: dict, what: str = "") -> None:
    """Every item that goes to Proposed Concept carries a product, a market cluster, an ICP, a pain point and image or video: the brief is written for that cut."""
    missing = [name for key, name in REQUIRED if not f.get(key)]
    if missing:
        raise q.ApiError(400, (what + ": " if what else "") + "add " + ", ".join(missing[:-1]) + (" and " if len(missing) > 1 else "") + missing[-1] + " before this goes to Proposed Concept")


def _clean_item(it, week: dt.date) -> dict:
    if not isinstance(it, dict):
        raise q.ApiError(400, "each item must be an object with product, cluster, icp and pain_point")
    product = it.get("product") or ""
    if product and product not in pa.PRODUCT_SLUGS:
        raise q.ApiError(400, "product must be one of: " + ", ".join(pa.PRODUCT_SLUGS))
    f = {k: pa._text(it.get(k), k, 200) for k in ("cluster", "icp", "pain_point")}
    media = pa._media_type(it.get("media_type"))
    _need_tags({**f, "product": product, "media_type": media})
    title = pa._text(it.get("title"), "title", 200) or _title(product, f["cluster"], f["icp"], f["pain_point"])
    wk = pa.monday_of(it["for_week"]) if it.get("for_week") else week
    return {"product": product, **f, "media_type": media, "story": pa._text(it.get("story"), "story", 300), "title": title, "for_week": wk, "note": pa._text(it.get("note"), "note", 500)}


def list_items(conn, week=None, include_assigned: bool = False) -> dict:
    """{rows, weeks, vendors}. Default: items still waiting for a brief; include_assigned adds the ones that became tickets. week = one or more Mondays, comma separated."""
    where, params = [], []
    if not include_assigned:
        where.append("status = 'needs_brief'")
    weeks_ = pa._multi(week, pa.monday_of)
    if weeks_:
        where.append("for_week = ANY(%s)")
        params.append(weeks_)
    try:
        rows = conn.execute(f"SELECT {COLS} FROM creative_backlog {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY for_week, created_at, id", params).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {"rows": [], "vendors": list(pa.VENDORS), "ready": False, "message": NOT_READY}
    return {"rows": [_shape(r) for r in rows], "vendors": list(pa.VENDORS), "ready": True}


def add_items(conn, body: dict, user: str | None = None) -> dict:
    """{items: [{cluster, icp, pain_point, product?, media_type?, title?, note?, for_week?}, ...] (1 to 100; product, cluster, icp, pain_point and media_type are required), for_week? (default this week's Monday)} -> {created: [...], skipped: [{index, reason}]}. An item that is already waiting for the same week is skipped."""
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise q.ApiError(400, f"items must be a list of 1 to {MAX_ITEMS} items")
    week = pa.monday_of(body.get("for_week") or pa.today_pt())
    clean = [_clean_item(it, week) for it in items]
    created, skipped = [], []
    try:
        with pa._rw() as w:
            for i, c in enumerate(clean):
                r = w.execute(
                    """INSERT INTO creative_backlog (product, cluster, icp, pain_point, media_type, story, title, for_week, note, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING RETURNING """ + COLS, (c["product"], c["cluster"], c["icp"], c["pain_point"], c["media_type"], c["story"], c["title"], c["for_week"], c["note"], user)).fetchone()
                if r is None:
                    skipped.append({"index": i, "reason": "already in Proposed Concept for that week"})
                else:
                    created.append(_shape(r))
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    return {"created": created, "skipped": skipped}


def update_item(conn, item_id: int, body: dict) -> dict:
    """PATCH one waiting item: product, cluster, icp, pain_point, media_type (never empty), for_week (any day, snaps to its Monday), note, title."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    sets, params = [], []
    if "story" in body:
        sets.append("story = %s")
        params.append(pa._text(body["story"], "story", 300))
    if "media_type" in body:
        m = pa._media_type(body["media_type"])
        if not m:
            raise q.ApiError(400, "image or video cannot be empty")
        sets.append("media_type = %s")
        params.append(m)
    if "product" in body:
        if body["product"] not in pa.PRODUCT_SLUGS:
            raise q.ApiError(400, "product must be one of: " + ", ".join(pa.PRODUCT_SLUGS))
        sets.append("product = %s")
        params.append(body["product"])
    for k, name in REQUIRED[1:4]:
        if k in body:
            v = pa._text(body[k], name, 200)
            if not v:
                raise q.ApiError(400, f"{name} cannot be empty")
            sets.append(f"{k} = %s")
            params.append(v)
    if "for_week" in body:
        sets.append("for_week = %s")
        params.append(pa.monday_of(body["for_week"]))
    if "note" in body:
        sets.append("note = %s")
        params.append(pa._text(body["note"], "note", 500))
    if "title" in body:
        t = pa._text(body["title"], "title", 200)
        if not t:
            raise q.ApiError(400, "title cannot be empty")
        sets.append("title = %s")
        params.append(t)
    if not sets:
        raise q.ApiError(400, "nothing to change: send product, cluster, icp, pain_point, media_type, for_week, note or title")
    try:
        with pa._rw() as w:
            try:
                r = w.execute(f"UPDATE creative_backlog SET {', '.join(sets)}, updated_at = now() WHERE id = %s AND status = 'needs_brief' RETURNING " + COLS, (*params, item_id)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise q.ApiError(409, "that cut is already waiting for a brief in that week") from None
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    if r is None:
        raise q.ApiError(404, "No waiting item with that id.")
    return _shape(r)


def remove_items(conn, ids: list[int]) -> dict:
    """Delete waiting items (assigned ones are history and stay). -> {removed: n}"""
    try:
        with pa._rw() as w:
            n = w.execute("DELETE FROM creative_backlog WHERE id = ANY(%s) AND status = 'needs_brief'", (ids,)).rowcount
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, NOT_READY) from None
    return {"removed": n}


def assign(conn, body: dict, user: str | None = None) -> dict:
    """{ids: [...], vendor, brief_url? and / or brief?, status? (assigned | brief_sent, default brief_sent when a link is given), note?} -> {created: [ticket cards], assigned: [ids]}.
    One ticket per waiting item, all for the item's own week, all to the same vendor with the same brief. Every ticket needs a brief (a link and / or text); nothing is created when one is wrong."""
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids or len(ids) > MAX_ITEMS or not all(isinstance(i, int) for i in ids):
        raise q.ApiError(400, f"ids must be a list of 1 to {MAX_ITEMS} item ids")
    try:
        rows = conn.execute(f"SELECT {COLS} FROM creative_backlog WHERE id = ANY(%s) AND status = 'needs_brief' ORDER BY for_week, id", (ids,)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        raise q.ApiError(503, NOT_READY) from None
    if len(rows) != len(set(ids)):
        raise q.ApiError(404, "Some of those items are no longer waiting for a brief. Refresh the list.")
    for r in rows:
        _need_tags({**r, "media_type": pa._media_type(body.get("media_type")) or r["media_type"]}, r["title"])
    tickets = []
    for r in rows:
        t = {"concept_name": r["title"], "vendor": body.get("vendor"), "product": r["product"], "cluster": r["cluster"], "icp": r["icp"], "pain_point": r["pain_point"], "story": r["story"], "media_type": pa._media_type(body.get("media_type")) or r["media_type"], "for_week": r["for_week"].isoformat(),
             "brief_url": body.get("brief_url"), "brief": body.get("brief"), "note": "; ".join(x for x in (r["note"], pa._text(body.get("note"), "note", 500)) if x)[:500]}
        if body.get("status"):
            t["status"] = body["status"]
        elif body.get("brief_url"):
            t["status"] = "brief_sent"
        tickets.append(t)
    res = pa.create_ticket(conn, {"tickets": tickets}, user)
    cards = res["created"]
    with pa._rw() as w:
        for r, c in zip(rows, cards):
            w.execute("UPDATE creative_backlog SET status = 'assigned', assignment_id = %s, updated_at = now() WHERE id = %s", (c["id"], r["id"]))
    return {"created": cards, "assigned": [r["id"] for r in rows]}
