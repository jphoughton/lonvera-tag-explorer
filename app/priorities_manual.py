"""Manual concepts on /priorities: concepts Josh adds by hand (product, market cluster, ICP, pain point, story, notes). Table creative_priorities_manual (migration 199).

They are NOT part of a weekly snapshot: they persist across Sundays until marked done or deleted, and are merged into every /api/priorities payload as extra rows (manual = true, key
'manual:<id>', status 'MANUAL'). Options for the form come from the committed Communication Overview catalogs (etl/autotag/catalogs/<slug>.json); a value that is not in the catalog is
stored as typed and flagged new_* so it shows as a proposal. Validation / row building / options are pure functions (the UI mock server reuses them over an in-memory store).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import psycopg

from . import queries as q

CATALOGS = Path(__file__).resolve().parent.parent / "etl" / "autotag" / "catalogs"
PRODUCTS = (("vita-bull", "Vita Bull"), ("beef-liver", "Beef Liver"), ("boneflex", "Boneflex"))
PRODUCT_NAME = dict(PRODUCTS)
SECTIONS = ("iterate", "faded", "new", "other")        # the page's own section keys; 'other' is the manual-only catch-all
SECTION_LABEL = {"iterate": "Iterate on what is working", "faded": "Was working, now isn’t", "new": "New and untested", "other": "Other"}
LIMITS = {"cluster": 200, "icp": 200, "pain_point": 200, "story": 400, "notes": 2000, "why_now": 300}
BODY_BYTES = 32 * 1024
FIELDS = ("product", "section", "cluster", "icp", "pain_point", "story", "notes", "why_now")
COLS = "id, product, section, cluster, icp, pain_point, story, notes, why_now, status, new_cluster, new_icp, new_pain_point, new_story, created_at, updated_at, done_at"
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ---------------------------------------------------------------------------------------------------------------- options (from the catalogs)
@lru_cache(maxsize=1)
def catalog_tree() -> dict:
    """{slug: {cluster: {icp: {pain_point: [story, ...]}}}} in catalog order."""
    tree: dict = {}
    for slug, _ in PRODUCTS:
        path = CATALOGS / f"{slug}.json"
        t: dict = {}
        if path.exists():
            for ln in json.loads(path.read_text()):
                stories = t.setdefault(ln["cluster"], {}).setdefault(ln["icp"], {}).setdefault(ln["pain"], [])
                if ln.get("story") and ln["story"] not in stories:
                    stories.append(ln["story"])
        tree[slug] = t
    return tree


def options() -> dict:
    return {"products": [{"slug": s, "name": n} for s, n in PRODUCTS], "sections": [{"key": k, "label": SECTION_LABEL[k]} for k in SECTIONS], "tree": catalog_tree(), "limits": LIMITS}


# ---------------------------------------------------------------------------------------------------------------- validation
def _text(raw, field: str, multiline: bool = False, required: bool = False) -> str:
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise q.ApiError(400, f"{field} must be text")
    s = _CTRL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    s = "\n".join(" ".join(ln.split()) for ln in s.split("\n")).strip() if multiline else " ".join(s.split())
    if required and not s:
        raise q.ApiError(400, f"{field.replace('_', ' ')} is required")
    if len(s) > LIMITS[field]:
        raise q.ApiError(400, f"{field.replace('_', ' ')} is at most {LIMITS[field]} characters")
    return s


def new_flags(product: str, cluster: str, icp: str, pain: str, story: str) -> dict:
    """Which of the four tags are not in the catalog for that product (compared case-insensitively along the path)."""
    low = lambda s: s.strip().lower()
    t = catalog_tree().get(product, {})
    c_key = next((k for k in t if low(k) == low(cluster)), None)
    i_key = next((k for k in (t.get(c_key) or {}) if low(k) == low(icp)), None) if c_key else None
    p_key = next((k for k in ((t.get(c_key) or {}).get(i_key) or {}) if low(k) == low(pain)), None) if i_key else None
    stories = (((t.get(c_key) or {}).get(i_key) or {}).get(p_key) or []) if p_key else []
    return {"new_cluster": c_key is None, "new_icp": i_key is None, "new_pain_point": p_key is None,
            "new_story": bool(story) and not any(low(x) == low(story) for x in stories)}


def clean(body: dict, partial: bool = False) -> dict:
    """The stored fields from a request body. partial (PATCH): only keys present are returned; status may be open|done."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    out: dict = {}
    if not partial or "product" in body:
        p = body.get("product")
        if p not in PRODUCT_NAME:
            raise q.ApiError(400, "product must be one of: " + ", ".join(PRODUCT_NAME))
        out["product"] = p
    if not partial or "section" in body:
        s = body.get("section")
        if s not in SECTIONS:
            raise q.ApiError(400, "section must be one of: " + ", ".join(SECTIONS))
        out["section"] = s
    for f, req, ml in (("cluster", True, False), ("icp", True, False), ("pain_point", True, False), ("story", False, False), ("notes", False, True), ("why_now", False, False)):
        if not partial or f in body:
            out[f] = _text(body.get(f), f, multiline=ml, required=req)
    if partial and "status" in body:
        if body["status"] not in ("open", "done"):
            raise q.ApiError(400, "status must be open or done")
        out["status"] = body["status"]
    unknown = set(body) - set(FIELDS) - {"status"}
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    return out


# ---------------------------------------------------------------------------------------------------------------- the page row
def to_row(rec: dict) -> dict:
    """A stored concept as a /priorities row: every key the CSV / print / table code reads is present."""
    new = [k for k, f in (("cluster", "new_cluster"), ("ICP", "new_icp"), ("pain point", "new_pain_point"), ("story", "new_story")) if rec.get(f)]
    iso = lambda v: v if isinstance(v, str) or v is None else v.isoformat()
    return {"manual": True, "id": rec["id"], "key": f"manual:{rec['id']}", "done": rec.get("status") == "done", "section": rec["section"], "product": rec["product"],
            "product_name": PRODUCT_NAME.get(rec["product"], rec["product"]), "rank": -1, "line_id": None, "line_code": "", "cluster": rec["cluster"], "icp": rec["icp"], "pain_point": rec["pain_point"],
            "story": rec.get("story") or "", "emotional_trigger": "", "ingredients": "", "concepts": 0, "score": 0, "confidence": "", "flags": [], "why": rec.get("why_now") or "", "why_now": rec.get("why_now") or "",
            "notes": rec.get("notes") or "", "status": "MANUAL", "cluster_description": "", "icp_description": "", "pain_description": "", "components": {}, "metrics": {}, "hooks": [], "evidence": None,
            "new_values": new, "created_at": iso(rec.get("created_at")), "updated_at": iso(rec.get("updated_at")), "done_at": iso(rec.get("done_at"))}


def merge(snap: dict, recs: list[dict], include_done: bool = True) -> dict:
    """Snapshot (or payload) with the manual rows appended."""
    rows = [to_row(r) for r in recs if include_done or r.get("status") != "done"]
    return {**snap, "rows": list(snap["rows"]) + rows}


# ---------------------------------------------------------------------------------------------------------------- storage
def _rw():
    from .projection import rwdb          # the app's own pool is read-only
    return rwdb.get_rw()


def list_records(conn, include_done: bool = True) -> list[dict]:
    try:
        cur = conn.execute(f"SELECT {COLS} FROM creative_priorities_manual" + ("" if include_done else " WHERE status = 'open'") + " ORDER BY created_at DESC, id DESC")
        return cur.fetchall()
    except psycopg.errors.UndefinedTable:          # migration 199 not applied yet: the page still works without manual concepts
        conn.rollback()
        return []


def get_record(rid: int) -> dict:
    with _rw() as w:
        r = w.execute(f"SELECT {COLS} FROM creative_priorities_manual WHERE id = %s", (rid,)).fetchone()
    if r is None:
        raise q.ApiError(404, "Manual concept not found.")
    return r


def create(body: dict) -> dict:
    f = clean(body)
    f.update(new_flags(f["product"], f["cluster"], f["icp"], f["pain_point"], f["story"]))
    cols = list(f)
    with _rw() as w:
        r = w.execute(f"INSERT INTO creative_priorities_manual ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING {COLS}", [f[c] for c in cols]).fetchone()
    return r


def update(rid: int, body: dict) -> dict:
    f = clean(body, partial=True)
    if not f:
        raise q.ApiError(400, "nothing to update")
    cur = get_record(rid)
    merged = {**cur, **f}
    if any(k in f for k in ("product", "cluster", "icp", "pain_point", "story")):
        f.update(new_flags(merged["product"], merged["cluster"], merged["icp"], merged["pain_point"], merged["story"] or ""))
    sets = [f"{c} = %s" for c in f] + ["updated_at = now()"]
    if "status" in f:
        sets.append("done_at = now()" if f["status"] == "done" else "done_at = NULL")
    with _rw() as w:
        r = w.execute(f"UPDATE creative_priorities_manual SET {', '.join(sets)} WHERE id = %s RETURNING {COLS}", [*f.values(), rid]).fetchone()
    return r


def delete(rid: int) -> None:
    with _rw() as w:
        n = w.execute("DELETE FROM creative_priorities_manual WHERE id = %s", (rid,)).rowcount
    if not n:
        raise q.ApiError(404, "Manual concept not found.")
