"""Launch API for /priorities production (package P4): settings, the per-assignment launch form, and the queue (prepare, confirm, cancel). Tables from migration 203.

DARK BUILD: nothing here calls Meta or Google, and no token is read or stored. The web process only reads and writes Postgres rows; prepare and confirm INSERT a queued job and a separate worker
(not part of the web app) picks it up. Every route sits behind three gates, in this order: META_LAUNCH_ENABLED is on (else 404), sign-in is on (else 403), and the signed-in user is listed in
META_LAUNCH_USERS (else 403). The routes live on their own blueprint, are never under /api/priorities/vendor/ and are not in auth.PUBLIC_API_RE. The pure helpers are in app/priorities_launch.py.
"""
from __future__ import annotations

import hmac
import re

import psycopg
from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from . import auth, db
from . import priorities_assign as pas
from . import priorities_launch as pl
from . import queries as q

bp = Blueprint("launch", __name__)

_LAUNCH_LIMITER = "_launch_limiter"
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CTA = re.compile(r"[A-Z_]{1,40}")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
BUDGET_CENTS = (100, 1_000_000)                      # $1 to $10,000
TYPES = ["New-Concept", "Iteration"]
OPEN_STATES = ("queued", "running")
BUSY = "A launch is already running for this row."
STALE = "The plan changed. Prepare again."
EXTRA_DRAFT_KEYS = ("primary_texts", "headlines", "link")           # copy overrides saved with the draft (the confirm dialog edits them)
SETTINGS_OUT = ("product", "account_id", "campaign_id", "template_adset_id", "page_id", "instagram_actor_id", "pixel_id", "link_url", "url_tags", "daily_budget_cents", "cost_cap_cents",
                "cta_image", "cta_video", "description", "primary_texts", "headlines", "updated_at", "updated_by")
SETTINGS_READONLY = ("product", "instagram_actor_id", "updated_at", "updated_by")
# JSON key -> column, where the two differ (migration 203 spells them default_page_id / landing_url / bid_amount_cents)
COL = {"page_id": "default_page_id", "link_url": "landing_url", "cost_cap_cents": "bid_amount_cents"}
SETTINGS_IN = ("account_id", "campaign_id", "template_adset_id", "page_id", "pixel_id", "link_url", "url_tags", "daily_budget_cents", "cost_cap_cents", "cta_image", "cta_video",
               "description", "primary_texts", "headlines")


def _rw():
    from .projection import rwdb          # the app's own pool is read-only
    return rwdb.get_rw()


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _err(status: int, message: str):
    resp = jsonify(error=message)
    resp.status_code = status
    return resp


# ------------------------------------------------------------------------------------------------------------------ gate
@bp.before_request
def _gate():
    if not pl.launch_enabled():
        return _err(404, "not found")
    if auth.auth_disabled():
        return _err(403, "Launching needs sign-in to be on.")
    user = auth.current_user()
    if not user or user.lower() not in pl.launch_users():
        return _err(403, "Your login is not allowed to launch ads.")
    return None


def can_launch() -> bool:
    """True when the signed-in user passes all three gates (used for the discovery flag on the assignments list)."""
    return pl.launch_enabled() and not auth.auth_disabled() and bool(auth.current_user()) and auth.current_user().lower() in pl.launch_users()


def discovery() -> dict:
    return {"enabled": pl.launch_enabled(), "can_launch": can_launch(), "dry_run": pl.dry_run()}


def _write_guard():
    """Same-origin and a per-IP rate limit, like the other /priorities writes."""
    from . import api
    return api._views_write_guard(_LAUNCH_LIMITER, "PRIORITIES_LAUNCH_PER_MIN", 30)


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return body


def _audit(w, action: str, aid=None, detail=None) -> None:
    w.execute("INSERT INTO creative_launch_audit (actor, action, assignment_id, detail) VALUES (%s, %s, %s, %s)", (auth.current_user(), action, aid, Jsonb(detail or {})))


# ----------------------------------------------------------------------------------------------------------------- shapes
def _job_json(r) -> dict | None:
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "state": r["state"], "dry_run": r["dry_run"], "plan_hash": r["plan_hash"], "plan": r["plan"], "error": r["error"],
            "created_at": _iso(r["created_at"]), "finished_at": _iso(r["finished_at"])}


JOB_COLS = "id, assignment_id, kind, state, plan, plan_hash, dry_run, error, created_at, finished_at"


def _settings_row(r: dict) -> dict:
    out = {k: r.get(k) for k in SETTINGS_OUT if k not in COL and k != "instagram_actor_id"}
    for k, c in COL.items():
        out[k] = r.get(c)
    out["instagram_actor_id"] = r.get("instagram_user_id")
    out["primary_texts"] = list(r.get("primary_texts") or [])
    out["headlines"] = list(r.get("headlines") or [])
    out["updated_at"] = _iso(r.get("updated_at"))
    return {k: out[k] for k in SETTINGS_OUT}


SETTINGS_SQL = ("SELECT s.*, p.instagram_user_id FROM creative_launch_settings s LEFT JOIN creative_launch_pages p ON p.page_id = s.default_page_id")


def _settings_for(conn, product) -> dict | None:
    r = conn.execute(SETTINGS_SQL + " WHERE s.product = %s", (product,)).fetchone()
    return _settings_row(r) if r else None


def _assignment(conn, aid: int) -> dict:
    recs = pas._fetch(conn, "WHERE id = %s", (aid,))
    if not recs:
        raise q.ApiError(404, "Assignment not found.")
    return pas._shape_all(conn, recs)[0]


def _readiness(a: dict) -> str | None:
    """None when the row can be launched, else the plain-English reason it cannot."""
    if a["stage"] != "ready":
        return "The row is not marked ready to launch." if a["stage"] in ("assigned", "brief_sent", "creative_in") else f"The row is {pas.stage_label(a['stage']).lower()}."
    if not a.get("creative_url"):
        return "Add the creative link first."
    return None


def _concept_label(a: dict) -> str:
    return " / ".join(x for x in (a.get("icp"), a.get("pain_point")) if x) or a.get("line_code") or a.get("story") or ""


def _allowed(conn) -> dict:
    """Allowed values for the draft pick lists: the tracker's own spellings for style / fidelity / proof, the two types, and the active Pages."""
    def distinct(col):
        return [r["v"] for r in conn.execute(f"SELECT DISTINCT {col} AS v FROM tracker_rows WHERE deleted_at IS NULL AND {col} IS NOT NULL AND btrim({col}) <> '' ORDER BY 1").fetchall()]
    return {"style": distinct("style"), "fidelity": distinct("fidelity"), "proof": distinct("proof"), "type": list(TYPES),
            "page": [r["page_id"] for r in conn.execute("SELECT page_id FROM creative_launch_pages WHERE active ORDER BY page_id").fetchall()]}


def _saved_draft(conn, aid: int) -> dict | None:
    r = conn.execute("SELECT fields FROM creative_launch_drafts WHERE assignment_id = %s", (aid,)).fetchone()
    return dict(r["fields"] or {}) if r else None


def _draft_view(a: dict, saved: dict | None, settings: dict | None) -> dict | None:
    """The saved edits laid over the defaults from the settings. None when there is neither."""
    if saved is None and settings is None:
        return None
    s = settings or {}
    out = {"style": "", "fidelity": "", "type": pl.default_type(a.get("section_at_assign")), "proof": "", "page": s.get("page_id") or "",
           "primary_texts": list(s.get("primary_texts") or []), "headlines": list(s.get("headlines") or []), "link": s.get("link_url") or ""}
    out.update({k: v for k, v in (saved or {}).items() if k in out})
    return out


def _clean_copy(raw, field: str) -> list[str]:
    if not isinstance(raw, list):
        raise q.ApiError(400, f"{field.replace('_', ' ')} must be a list")
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            raise q.ApiError(400, f"each of {field.replace('_', ' ')} must be text")
        t = _CTRL.sub("", x.replace("\r\n", "\n").replace("\r", "\n")).strip()
        if len(t) > 2000:
            raise q.ApiError(400, f"each of {field.replace('_', ' ')} is at most 2,000 characters")
        if t and t not in out:
            out.append(t)
    if len(out) > pl.MAX_COPY:
        raise q.ApiError(400, f"at most {pl.MAX_COPY} {field.replace('_', ' ')}")
    return out


def _ads_manager_url(account_id, ad_id):
    if not account_id or not ad_id:
        return None
    return f"https://adsmanager.facebook.com/adsmanager/manage/ads?act={re.sub(r'^act_', '', str(account_id))}&selected_ad_ids={ad_id}"


# ---------------------------------------------------------------------------------------------------------------- settings
@bp.get("/api/priorities/launch/settings")
def launch_settings():
    """{settings: [one row per product], pages: [{page_id, name, instagram_actor_id}], vendor_prefixes: [{vendor, prefix}]}. Keys follow the contract; page_id, link_url, cost_cap_cents and
    instagram_actor_id are the columns default_page_id, landing_url, bid_amount_cents and the Page's instagram_user_id."""
    conn = db.get_conn()
    rows = conn.execute(SETTINGS_SQL + " ORDER BY s.product").fetchall()
    pages = conn.execute("SELECT page_id, name, instagram_user_id FROM creative_launch_pages WHERE active ORDER BY name").fetchall()
    prefixes = conn.execute("SELECT vendor, prefix FROM creative_launch_vendor_prefixes ORDER BY vendor").fetchall()
    return jsonify({"settings": [_settings_row(r) for r in rows],
                    "pages": [{"page_id": p["page_id"], "name": p["name"], "instagram_actor_id": p["instagram_user_id"]} for p in pages],
                    "vendor_prefixes": [{"vendor": p["vendor"], "prefix": p["prefix"]} for p in prefixes]})


def _clean_settings(conn, body: dict) -> dict:
    """Column -> value for the keys given. Blank text clears a field to NULL; bad input is a 400."""
    unknown = [k for k in body if k not in SETTINGS_IN and k not in SETTINGS_READONLY]
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    out: dict = {}
    for k in SETTINGS_IN:
        if k not in body:
            continue
        v = body[k]
        col = COL.get(k, k)
        if k in ("primary_texts", "headlines"):
            out[col] = _clean_copy(v, k)
        elif k in ("daily_budget_cents", "cost_cap_cents"):
            if isinstance(v, bool) or not isinstance(v, int) or not BUDGET_CENTS[0] <= v <= BUDGET_CENTS[1]:
                raise q.ApiError(400, f"{k.replace('_', ' ')} must be a whole number of cents between {BUDGET_CENTS[0]} and {BUDGET_CENTS[1]}")
            out[col] = v
        elif k == "link_url":
            out[col] = pas.clean_url(v) if isinstance(v, str) and v.strip() else None
        elif k in ("cta_image", "cta_video"):
            if not isinstance(v, str) or not _CTA.fullmatch(v.strip()):
                raise q.ApiError(400, f"{k.replace('_', ' ')} must be like LEARN_MORE")
            out[col] = v.strip()
        elif k in ("url_tags", "description"):
            if v is not None and not isinstance(v, str):
                raise q.ApiError(400, f"{k.replace('_', ' ')} must be text")
            t = _CTRL.sub("", v or "").strip()
            if len(t) > 500:
                raise q.ApiError(400, f"{k.replace('_', ' ')} is at most 500 characters")
            out[col] = t or None
        else:                                                                       # account_id, campaign_id, template_adset_id, page_id, pixel_id
            if v is not None and not isinstance(v, str):
                raise q.ApiError(400, f"{k.replace('_', ' ')} must be text")
            t = (v or "").strip()
            if t and not _ID.fullmatch(t):
                raise q.ApiError(400, f"{k.replace('_', ' ')} may only contain letters, numbers, underscore, dot and hyphen (at most 64)")
            if k == "page_id" and t and not conn.execute("SELECT 1 FROM creative_launch_pages WHERE page_id = %s AND active", (t,)).fetchone():
                raise q.ApiError(400, "page_id is not one of the Pages in the list")
            out[col] = t or None
    return out


@bp.put("/api/priorities/launch/settings/<product>")
def launch_settings_put(product: str):
    """Save any subset of the settings keys for one product; 200 returns the saved row."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    if product not in pl.PRODUCT_WORDS:
        raise q.ApiError(400, "product must be one of: " + ", ".join(pl.PRODUCT_WORDS))
    body = _body()
    conn = db.get_conn()
    cols = _clean_settings(conn, body)
    if not cols:
        raise q.ApiError(400, "nothing to update")
    names = list(cols)
    vals = list(cols.values())
    user = auth.current_user()
    with _rw() as w:
        w.execute("INSERT INTO creative_launch_settings (product, " + ", ".join(names) + ", updated_by) VALUES (%s, " + ", ".join(["%s"] * len(names)) + ", %s) "
                  "ON CONFLICT (product) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in names) + ", updated_at = now(), updated_by = EXCLUDED.updated_by",
                  [product, *vals, user])
        _audit(w, "settings_saved", None, {"product": product, "fields": [k for k in body if k in SETTINGS_IN]})
        saved = _settings_for(w, product)                      # read back on the writing connection: the request's read-only snapshot predates the write
    return jsonify(saved)


# ------------------------------------------------------------------------------------------------------------- assignment
@bp.get("/api/priorities/assignments/<int:aid>/launch")
def launch_get(aid: int):
    """Everything the launch panel for one row needs: readiness, the draft (saved edits over the settings defaults), the latest job with its plan, the ads made so far and the tracker rows."""
    conn = db.get_conn()
    a = _assignment(conn, aid)
    reason = _readiness(a)
    settings = _settings_for(conn, a["product"])
    saved = _saved_draft(conn, aid)
    draft = _draft_view(a, saved, settings)
    job = conn.execute(f"SELECT {JOB_COLS} FROM creative_launch_jobs WHERE assignment_id = %s ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    rows = conn.execute("SELECT concept_id, ad_name, file_name, kind, state, meta_ad_id, error FROM creative_launch_ads WHERE assignment_id = %s ORDER BY id", (aid,)).fetchall()
    account = (settings or {}).get("account_id")
    ads = [{**{k: r[k] for k in ("concept_id", "ad_name", "file_name", "kind", "state", "meta_ad_id", "error")}, "ads_manager_url": _ads_manager_url(account, r["meta_ad_id"])} for r in rows]
    tsv = None
    if rows:
        page = conn.execute("SELECT name_token FROM creative_launch_pages WHERE page_id = %s", ((draft or {}).get("page"),)).fetchone() if (draft or {}).get("page") else None
        d = draft or {}
        type_ = d.get("type") or pl.default_type(a.get("section_at_assign"))
        tsv = "\n".join(pl.tracker_row_tsv(pl.tracker_row_values(r["concept_id"], r["kind"], a.get("pain_point"), a.get("icp"), pl.TRACKER_PRODUCTS.get(a["product"], a["product"]), d.get("style"),
                                                                  d.get("fidelity"), a.get("vendor"), type_, d.get("proof"), (page or {}).get("name_token") or "",
                                                                  asset_url=a.get("creative_url") or "", brief_url=a.get("brief_url") or "")) for r in rows)
    return jsonify({"assignment": {"id": a["id"], "product": a["product"], "vendor": a["vendor"], "for_week": a["for_week"], "stage": a["stage"], "creative_url": a.get("creative_url"),
                                   "concept_label": _concept_label(a)},
                    "ready": reason is None, "not_ready_reason": reason, "draft": draft, "job": _job_json(job), "ads": ads, "tracker_tsv": tsv, "dry_run": pl.dry_run()})


@bp.put("/api/priorities/assignments/<int:aid>/launch/draft")
def launch_draft_put(aid: int):
    """Save a subset of style / fidelity / type / proof / page (each must be a listed value; blank clears it) and the copy overrides primary_texts / headlines / link."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    body = _body()
    conn = db.get_conn()
    a = _assignment(conn, aid)
    unknown = [k for k in body if k not in pl.FIELD_KEYS and k not in EXTRA_DRAFT_KEYS]
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    given = {k: body[k] for k in pl.FIELD_KEYS if k in body}
    for k, v in given.items():
        if v is not None and not isinstance(v, str):
            raise q.ApiError(400, f"{k} must be text")
    clean, errors = pl.validate_fields(given, _allowed(conn))
    if errors:
        raise q.ApiError(400, "; ".join(errors))
    new = dict(clean)
    cleared = [k for k, v in given.items() if not str(v or "").strip()]
    for k in ("primary_texts", "headlines"):
        if k in body:
            new[k] = _clean_copy(body[k], k)
    if "link" in body:
        link = body["link"]
        new["link"] = pas.clean_url(link) if isinstance(link, str) and link.strip() else ""
    user = auth.current_user()
    try:
        with _rw() as w:
            row = w.execute("SELECT fields FROM creative_launch_drafts WHERE assignment_id = %s FOR UPDATE", (aid,)).fetchone()
            if w.execute("SELECT 1 FROM creative_launch_jobs WHERE assignment_id = %s AND state IN ('queued', 'running')", (aid,)).fetchone():
                raise q.ApiError(409, BUSY)
            fields = {k: v for k, v in dict((row or {}).get("fields") or {}).items() if k not in cleared}
            fields.update(new)
            w.execute("INSERT INTO creative_launch_drafts (assignment_id, fields, updated_by) VALUES (%s, %s, %s) "
                      "ON CONFLICT (assignment_id) DO UPDATE SET fields = EXCLUDED.fields, updated_at = now(), updated_by = EXCLUDED.updated_by", (aid, Jsonb(fields), user))
            _audit(w, "draft_saved", aid, {"fields": sorted([*new, *cleared])})
            view = _draft_view(a, _saved_draft(w, aid), _settings_for(w, a["product"]))
    except psycopg.errors.ForeignKeyViolation:
        raise q.ApiError(404, "Assignment not found.") from None
    return jsonify({"draft": view})


def _queue(w, aid: int, kind: str, plan, plan_hash, detail: dict):
    """INSERT one queued job; the partial unique index allows only one open job per assignment, so a clash is the 409."""
    try:
        r = w.execute(f"INSERT INTO creative_launch_jobs (assignment_id, kind, state, plan, plan_hash, dry_run, requested_by) VALUES (%s, %s, 'queued', %s, %s, %s, %s) RETURNING {JOB_COLS}",
                      (aid, kind, Jsonb(plan) if plan is not None else None, plan_hash, pl.dry_run(), auth.current_user())).fetchone()
    except psycopg.errors.UniqueViolation:
        w.rollback()
        raise q.ApiError(409, BUSY) from None
    _audit(w, f"{kind}_queued", aid, {"job_id": r["id"], "dry_run": r["dry_run"], **detail})
    return r


@bp.post("/api/priorities/assignments/<int:aid>/launch/prepare")
def launch_prepare(aid: int):
    """Queue a prepare job (the worker lists the files and builds the plan). 202 {job}; 409 if the row is not ready or a job is already open."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    a = _assignment(db.get_conn(), aid)
    reason = _readiness(a)
    if reason:
        raise q.ApiError(409, reason)
    with _rw() as w:
        job = _queue(w, aid, "prepare", None, None, {})
    return jsonify({"job": _job_json(job)}), 202


@bp.post("/api/priorities/assignments/<int:aid>/launch/confirm")
def launch_confirm(aid: int):
    """{job_id, plan_hash}: queue a launch job carrying the plan of a finished prepare job, only when the hash matches. 202 {job}; 409 'The plan changed. Prepare again.' otherwise."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    body = _body()
    job_id, h = body.get("job_id"), body.get("plan_hash")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or not isinstance(h, str) or not h:
        raise q.ApiError(400, "job_id (a number) and plan_hash (text) are required")
    a = _assignment(db.get_conn(), aid)
    reason = _readiness(a)
    if reason:
        raise q.ApiError(409, reason)
    with _rw() as w:
        j = w.execute(f"SELECT {JOB_COLS} FROM creative_launch_jobs WHERE id = %s AND assignment_id = %s FOR UPDATE", (job_id, aid)).fetchone()
        if (not j or j["kind"] != "prepare" or j["state"] != "done" or not j["plan"] or not j["plan_hash"] or not hmac.compare_digest(j["plan_hash"], h)
                or pl.plan_hash(j["plan"]) != j["plan_hash"]):
            raise q.ApiError(409, STALE)
        job = _queue(w, aid, "launch", j["plan"], j["plan_hash"], {"prepare_job_id": job_id, "plan_hash": h})
    return jsonify({"job": _job_json(job)}), 202


@bp.post("/api/priorities/assignments/<int:aid>/launch/cancel")
def launch_cancel(aid: int):
    """Cancel the open job if it is still waiting to start. 200 {job}; 409 if it is already running; 404 if nothing is open."""
    blocked = _write_guard()
    if blocked is not None:
        return blocked
    _assignment(db.get_conn(), aid)
    with _rw() as w:
        j = w.execute(f"SELECT {JOB_COLS} FROM creative_launch_jobs WHERE assignment_id = %s AND state IN ('queued', 'running') FOR UPDATE", (aid,)).fetchone()
        if not j:
            raise q.ApiError(404, "There is nothing to cancel.")
        if j["state"] != "queued":
            raise q.ApiError(409, "It is already running and cannot be cancelled.")
        job = w.execute(f"UPDATE creative_launch_jobs SET state = 'cancelled', finished_at = now() WHERE id = %s RETURNING {JOB_COLS}", (j["id"],)).fetchone()
        _audit(w, "job_cancelled", aid, {"job_id": j["id"], "kind": j["kind"]})
    return jsonify({"job": _job_json(job)})
