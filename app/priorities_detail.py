"""The detail panel of one production assignment (Production page and vendor link page): the same data the concept has on /priorities, plus up to 10 example ads.

Read one assignment, then look its concept up in the LATEST priorities snapshot (by concept key, else by the stored text key). Nothing is written. If the concept is no longer in the latest snapshot the
panel falls back to what the assignment stored (tags and story) and says so. A vendor gets the same panel minus anything internal: no ad page links (those need a login), no manual-concept notes,
no analysis blobs the panel does not draw.
"""
from __future__ import annotations

from urllib.parse import urlparse

import psycopg

from . import creative_priorities as cpr, priorities_assign as pa, queries as q

EXAMPLES_MAX = 10
BEST_MIN_SPEND = 100.0                    # same bar as etl.creative_priorities.JUDGED_SPEND: an ad counts as tested once it spent this much
NO_NUMBERS = "No current numbers for this concept."
VENDOR_DROP_ROW = ("evidence", "components", "unmapped_ads", "notes", "why_now")          # keys a vendor never gets (manual notes are the team's own; the other two are not drawn by the panel)

EXAMPLES_SQL = """
SELECT m.family_key, m.tracker_row_id, m.spend_launch, m.revenue_launch, COALESCE(cs.display, m.family_key) AS display, fc.story_url, tr.asset_url
FROM ad_metrics m
JOIN ad_tags g ON g.tracker_row_id = m.tracker_row_id AND g.tag_set = 'linemap' AND g.is_current AND g.line_id = ANY(%s)
LEFT JOIN creative_stage_moves cs ON cs.family_key = m.family_key
LEFT JOIN family_content fc ON fc.family_key = m.family_key
LEFT JOIN tracker_rows tr ON tr.id = m.tracker_row_id
WHERE m.is_first_row AND m.spend_launch > 0
ORDER BY m.spend_launch DESC, m.family_key
LIMIT %s
"""

LINES_SQL = """
SELECT l.id FROM taxonomy_lines l JOIN products p ON p.id = l.product_id
WHERE p.slug = %s AND lower(btrim(l.cluster)) = %s AND lower(btrim(l.icp)) = %s AND lower(btrim(l.pain_point)) = %s
"""


def _norm(v) -> str:
    return " ".join(str(v if v is not None else "").split()).lower()


def line_ids(conn, rec: dict, row: dict | None) -> list[int]:
    """The taxonomy lines whose ads count as this concept's examples: the line itself; a concept with no line (a manual concept, a draft) uses any line with the same product / cluster / ICP / pain point."""
    lid = (row or {}).get("line_id") if (row or {}).get("line_id") is not None else rec.get("line_id")
    if lid is not None:
        return [int(lid)]
    try:
        return [r["id"] for r in conn.execute(LINES_SQL, (rec["product"], _norm(rec["cluster"]), _norm(rec["icp"]), _norm(rec["pain_point"]))).fetchall()]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return []


PUBLIC_HOSTS = frozenset({"facebook.com", "www.facebook.com", "fb.watch", "instagram.com", "www.instagram.com"})


def public_url(*candidates) -> str | None:
    """The first candidate that is a public Facebook or Instagram link a vendor may open: https only, host exactly one of PUBLIC_HOSTS (so no Google Drive, no Ads Manager, no look-alike host,
    no user:password@ trick, no custom port), no whitespace or quote characters. Anything else is skipped; None when no candidate qualifies."""
    for c in candidates:
        t = str(c or "").strip()
        if not t or len(t) > 2000 or any(ch in t for ch in " \t\r\n<>\"'\\"):
            continue
        try:
            u = urlparse(t)
            host, port = (u.hostname or "").lower(), u.port
        except ValueError:
            continue
        if u.scheme == "https" and host in PUBLIC_HOSTS and u.username is None and u.password is None and port is None:
            return t
    return None


def examples(conn, ids: list[int], vendor_view: bool = False, limit: int = EXAMPLES_MAX) -> list[dict]:
    """Up to `limit` ads of the concept, top by spend since launch: {ad, spend, roas, ad_id, best}. best marks the highest return among ads that spent at least $100 (one ad). A vendor gets no ad_id but a public_url (the stored Facebook post, else the tracker asset link, but only a https Facebook / Instagram link, see PUBLIC_HOSTS; None otherwise, never Google Drive)."""
    if not ids:
        return []
    try:
        rows = conn.execute(EXAMPLES_SQL, (ids, limit)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return []
    out = []
    for r in rows:
        spend, rev = float(r["spend_launch"]), (float(r["revenue_launch"]) if r["revenue_launch"] is not None else None)
        out.append({"ad": r["display"], "spend": round(spend, 2), "roas": round(rev / spend, 2) if rev is not None and spend else None, "ad_id": None if vendor_view else r["tracker_row_id"], "best": False}
                   | ({"public_url": public_url(r["story_url"], r["asset_url"])} if vendor_view else {}))
    cand = [e for e in out if e["roas"] is not None and e["spend"] >= BEST_MIN_SPEND]
    if cand:
        max(cand, key=lambda e: (e["roas"], e["spend"]))["best"] = True
    return out


def _find_row(snap_rows: list[dict], rec: dict) -> dict | None:
    for r in snap_rows:
        if r.get("ckey") == rec["concept_key"]:
            return r
    if rec.get("text_key"):
        for r in snap_rows:
            if not r.get("manual") and pa.text_key(r) == rec["text_key"]:
                return r
    return None


def _strip_links(items):
    return [{k: v for k, v in it.items() if k != "ad_id"} for it in (items or [])]


def _vendor_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in VENDOR_DROP_ROW}
    out["hooks"] = _strip_links(row.get("hooks"))
    out["former_winners"] = _strip_links(row.get("former_winners"))
    if row.get("manual"):
        out["why"] = ""                                          # a manual concept's `why` is its internal "why now"
    return out


def _fallback_row(rec: dict) -> dict:
    return {"product": rec["product"], "product_name": cpr_product_name(rec["product"]), "cluster": rec["cluster"], "icp": rec["icp"], "pain_point": rec["pain_point"], "story": rec.get("story") or "",
            "line_id": rec.get("line_id"), "line_code": rec.get("line_code") or ""}


def cpr_product_name(slug: str) -> str:
    from .priorities_manual import PRODUCT_NAME
    return PRODUCT_NAME.get(slug, slug)


def build(conn, rec: dict, vendor_view: bool = False) -> dict:
    """{assignment_id, current, snapshot_week, row, baselines, definitions, examples, examples_max, message}. current is false when the concept is not in the latest snapshot: row is then just the
    stored tags and story and message says "No current numbers for this concept." (the examples are still looked up, they do not depend on the snapshot)."""
    snap = None
    try:
        snap = cpr.payload(conn, None)
    except q.ApiError as e:
        if e.status != 404:
            raise
    row = _find_row(snap["rows"], rec) if snap else None
    ids = line_ids(conn, rec, row)
    ex = examples(conn, ids, vendor_view)
    out = {"assignment_id": rec["id"], "current": row is not None, "snapshot_week": snap["week_start"] if snap else None, "examples": ex, "examples_max": EXAMPLES_MAX}
    if row is None:
        return {**out, "row": _fallback_row(rec), "baselines": {}, "definitions": (snap or {}).get("definitions") or {}, "message": NO_NUMBERS}
    base = ((snap.get("baselines") or {}).get(row["product"])) or {}
    return {**out, "row": _vendor_row(row) if vendor_view else row, "baselines": {row["product"]: base}, "definitions": snap.get("definitions") or {}, "message": ""}


def assignment_detail(conn, aid: int) -> dict:
    recs = pa._fetch(conn, "WHERE id = %s", (aid,))
    if not recs:
        raise q.ApiError(404, "Assignment not found.")
    return build(conn, recs[0])


def vendor_detail(conn, token: str, aid: int) -> dict:
    vendor = pa.vendor_of_token(conn, token)
    recs = pa._fetch(conn, "WHERE id = %s AND vendor = %s", (aid, vendor))
    if not recs:
        raise q.ApiError(404, "Assignment not found.")
    if recs[0]["dropped_at"]:
        raise q.ApiError(404, "Assignment not found.")                 # a dropped row is not on the vendor page, so it has no detail either
    return build(conn, recs[0], vendor_view=True)
