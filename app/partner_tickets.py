"""What an outside creative partner can do with a partner_push API key (app/api_keys.py, migration 261), and nothing else.

A partner key is bound to one vendor. Everything here reads and writes only that vendor's tickets (rows of creative_priorities_assignments): push tickets (the ticket contract, vendor forced), list
them in a minimal shape (no spend, notes or history), fix them, and add the creative as LINKS to an outside platform (Drive, Frame.io, Dropbox, Vimeo, YouTube, Loom ...). Writes reuse the
helpers of the Production board (create_ticket, _update, set_status, _log), so validation, history events and the face fields are the same as when a person does it; the actor is 'key:<name>'.
Another vendor's ticket is always a 404 (never a 403), so the existence of a ticket id leaks nothing.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from urllib.parse import urlsplit

import psycopg

from . import priorities_assign as pas
from . import queries as q

PARTNER_STATUSES = ("assigned", "brief_sent", "in_production")          # what a partner may start a ticket in (not in_review, ready, launched)
PATCH_FIELDS = ("concept_name", "concept_kind", "cluster", "variations", "brief_url", "creative_url", "story")
MAX_VARIATIONS = 40
MAX_BATCH = 100
BODY_BYTES = 1024 * 1024
WRITES_PER_MIN = 60
LIST_FIELDS = ("id", "concept_name", "product", "cluster", "icp", "pain_point", "concept_kind", "variations", "vendor", "for_week", "due_date", "status", "created_at", "updated_at")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
PLAYS_IN_TICKET = {"Google Drive": "files and folders", "YouTube": "watch, youtu.be and shorts links", "Vimeo": "vimeo.com/<number> links", "Loom": "loom.com/share links"}


def forbidden(msg: str) -> q.ApiError:
    return q.ApiError(403, msg)


# ----------------------------------------------------------------------------------------------------------------------------- links
def partner_url(raw, field: str) -> str:
    """A link a partner sends: https only, at most 2,000 characters, no spaces, a real public host (a dotted name; no localhost, no IP address, no user:password@, no odd port). The 400 names the field."""
    try:
        u = pas.clean_url(raw)
    except q.ApiError as e:
        raise q.ApiError(400, f"{field}: {e.message}") from None
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    if p.scheme.lower() != "https":
        raise q.ApiError(400, f"{field}: link must start with https://")
    if p.username is not None or p.password is not None or "@" in p.netloc:
        raise q.ApiError(400, f"{field}: link must not contain a user name or password")
    try:
        port = p.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise q.ApiError(400, f"{field}: link must not use a custom port")
    if not _HOST.fullmatch(host) or re.fullmatch(r"[0-9.]+", host) or host.rsplit(".", 1)[-1] in ("local", "internal", "localhost", "lan", "test", "invalid", "example") or not re.search(r"[a-z]", host.rsplit(".", 1)[-1]):
        raise q.ApiError(400, f"{field}: link must point to a public website (like drive.google.com)")
    return u


def _check_variation_urls(variations, prefix: str = "") -> None:
    if isinstance(variations, list):
        for i, v in enumerate(variations):
            if isinstance(v, dict) and v.get("url") not in (None, ""):
                partner_url(v["url"], f"{prefix}variations[{i + 1}].url")


def _opt_partner_url(raw, field: str):
    return None if raw is None or (isinstance(raw, str) and not raw.strip()) else partner_url(raw, field)


# ----------------------------------------------------------------------------------------------------------------------------- shape
def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def shape(r: dict) -> dict:
    """The minimal ticket a partner sees: the contract fields plus needs_creative (no variation has a link yet). No spend, notes, history, comments or other vendors' data."""
    vs = [{"label": v.get("label", ""), "url": v.get("url", ""), "concept_id": v.get("concept_id", "")} for v in (r.get("variations") or []) if isinstance(v, dict)]
    out = {k: _iso(r.get(k)) for k in LIST_FIELDS}
    out["variations"] = vs
    out["needs_creative"] = not any(v["url"] for v in vs)
    out["tags"] = "pending" if r.get("tag_state") == "pending" else "complete"          # pending = the system is still filling in product, market cluster, ICP, pain point and new vs iteration
    return out


_SELECT = ("SELECT id, COALESCE(NULLIF(concept_name, ''), pain_point, '') AS concept_name, product, cluster, icp, pain_point, concept_kind, variations, vendor, for_week, due_date, status, created_at, updated_at, "
           "creative_url, tracker_concept_ids{tag} FROM creative_priorities_assignments")


def _select(conn, tail: str, params, retry: bool = True):
    """The partner SELECT with tag_state (the auto-tagger's column); a database without that column answers with every ticket 'complete'."""
    try:
        with conn.transaction():
            return conn.execute(_SELECT.format(tag=", tag_state") + tail, params).fetchall()
    except psycopg.errors.UndefinedColumn:
        return conn.execute(_SELECT.format(tag="") + tail, params).fetchall()


def _own(conn, vendor: str, tid: int, for_update: bool = False) -> dict:
    rows = _select(conn, " WHERE id = %s AND vendor = %s AND dropped_at IS NULL" + (" FOR UPDATE" if for_update else ""), (tid, vendor))
    r = rows[0] if rows else None
    if r is None:
        gone = conn.execute("SELECT 1 FROM creative_priorities_assignments WHERE id = %s AND vendor = %s AND dropped_at IS NOT NULL", (tid, vendor)).fetchone()
        if gone:
            raise q.ApiError(409, "This ticket was dropped. It can no longer be changed.")
        raise q.ApiError(404, "Ticket not found.")
    return r


# ----------------------------------------------------------------------------------------------------------------------------- create / list
def create(conn, body, vendor: str, user: str) -> tuple[dict, bool]:
    """POST /api/priorities/tickets for a partner: the ticket contract with the vendor forced to the key's vendor (a different one is a 403, an absent one is filled in), status limited to
    PARTNER_STATUSES, every link https. One ticket or {tickets: [...]} (up to 100, all or nothing). -> (result, batch)."""
    batch = isinstance(body, dict) and "tickets" in body
    items = body["tickets"] if batch else [body]
    if batch and (not isinstance(items, list) or not items or len(items) > MAX_BATCH):
        raise q.ApiError(400, "tickets must be a list of 1 to 100 tickets")
    fixed = []
    for i, it in enumerate(items):
        pre = f"ticket {i + 1}: " if batch else ""
        if not isinstance(it, dict):
            raise q.ApiError(400, pre + "JSON object body required")
        miss = [k for k, ok in (("concept_name", str(it.get("concept_name") or "").strip()), ("brief", str(it.get("brief") or "").strip() or str(it.get("brief_url") or "").strip())) if not ok]
        if miss:
            raise q.ApiError(400, f"{pre}missing required field: {'concept_name' if 'concept_name' in miss else 'brief (text) or brief_url (a link)'} (a ticket needs concept_name and a brief)")
        if it.get("vendor") not in (None, "", vendor):
            raise forbidden(f"{pre}This key can only create tickets for {vendor}. Leave vendor out or set it to {vendor}.")
        if it.get("status") not in (None, "") and it["status"] not in PARTNER_STATUSES:
            raise q.ApiError(400, f"{pre}status must be one of: " + ", ".join(PARTNER_STATUSES))
        _check_variation_urls(it.get("variations"), pre)
        _opt_partner_url(it.get("brief_url"), pre + "brief_url")
        fixed.append({**it, "vendor": vendor})
    res = pas.create_ticket(conn, {"tickets": fixed} if batch else fixed[0], user)
    return ({"created": [shape(c) for c in res["created"]]} if batch else shape(res)), batch


def list_tickets(conn, vendor: str, status=None, week=None, limit=None, offset=None) -> dict:
    """The vendor's own non-dropped tickets, newest week first: ?status= &week= (any day, snaps to its Monday) &limit (default 100, at most 500) &offset."""
    where, params = ["vendor = %s", "dropped_at IS NULL"], [vendor]
    if status not in (None, ""):
        if status not in pas.STATUSES:
            raise q.ApiError(400, "status must be one of: " + ", ".join(pas.STATUSES))
        where.append("status = %s")
        params.append(status)
    if week not in (None, ""):
        where.append("for_week = %s")
        params.append(pas.monday_of(week))
    lim, off = _int(limit, 100, "limit", 1, 500), _int(offset, 0, "offset", 0, 10 ** 9)
    w = " WHERE " + " AND ".join(where)
    total = conn.execute("SELECT count(*) AS n FROM creative_priorities_assignments" + w, params).fetchone()["n"]
    rows = _select(conn, w + " ORDER BY for_week DESC, id DESC LIMIT %s OFFSET %s", [*params, lim, off])
    return {"tickets": [shape(r) for r in rows], "count": total, "limit": lim, "offset": off}


def _int(raw, default: int, name: str, lo: int, hi: int) -> int:
    if raw in (None, ""):
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise q.ApiError(400, f"{name} must be a whole number") from None
    if not lo <= n <= hi:
        raise q.ApiError(400, f"{name} must be between {lo} and {hi}")
    return n


# ----------------------------------------------------------------------------------------------------------------------------- every vendor's tickets, read only
HIDE = ("created_by", "updated_by")                           # who made or changed a row (a login name or a key name): not shown to partners


def _scrub(card: dict) -> dict:
    out = {k: v for k, v in card.items() if k not in HIDE}
    out["history"] = [{**e, "actor": "api" if str(e.get("actor") or "").startswith("key:") else e.get("actor")} for e in card.get("history", [])]
    return out


def all_tickets(conn, args) -> dict:
    """GET /api/priorities/partner/all-tickets: every vendor's non-dropped tickets as the app shows them (brief and story text, notes, spend and return, variations with their links, history, comment counts),
    READ ONLY. Filters ?vendor= ?status= ?week= ?product= ?limit (100, at most 500) ?offset. Writes stay limited to the key's own vendor."""
    where, params = ["a.dropped_at IS NULL"], []
    v = args.get("vendor")
    if v not in (None, ""):
        if v not in pas.VENDORS:
            raise q.ApiError(400, "vendor must be one of: " + ", ".join(pas.VENDORS))
        where.append("a.vendor = %s")
        params.append(v)
    st = args.get("status")
    if st not in (None, ""):
        if st not in pas.STATUSES:
            raise q.ApiError(400, "status must be one of: " + ", ".join(pas.STATUSES))
        where.append("a.status = %s")
        params.append(st)
    pr = args.get("product")
    if pr not in (None, ""):
        if pr not in pas.PRODUCT_SLUGS:
            raise q.ApiError(400, "product must be one of: " + ", ".join(pas.PRODUCT_SLUGS))
        where.append("a.product = %s")
        params.append(pr)
    wk = args.get("week")
    if wk not in (None, ""):
        where.append("a.for_week = %s")
        params.append(pas.monday_of(wk))
    lim, off = _int(args.get("limit"), 100, "limit", 1, 500), _int(args.get("offset"), 0, "offset", 0, 10 ** 9)
    w = " WHERE " + " AND ".join(where)
    total = conn.execute("SELECT count(*) AS n FROM creative_priorities_assignments a" + w, params).fetchone()["n"]
    ids = [r["id"] for r in conn.execute("SELECT a.id FROM creative_priorities_assignments a" + w + " ORDER BY a.for_week DESC, a.id DESC LIMIT %s OFFSET %s", [*params, lim, off]).fetchall()]
    cards = {c["id"]: c for c in pas._shape_all(conn, pas._fetch(conn, "WHERE a.id = ANY(%s)", (ids,)), vendor_view=False, history=True)} if ids else {}
    return {"tickets": [_scrub(cards[i]) for i in ids if i in cards], "count": total, "limit": lim, "offset": off}


def one_ticket(conn, tid: int) -> dict:
    """GET /api/priorities/partner/all-tickets/<id>: one ticket of any vendor in full: the card, its comments and the transcripts of its ads. 404 for a dropped or missing ticket."""
    recs = pas._fetch(conn, "WHERE a.id = %s AND a.dropped_at IS NULL", (tid,))
    if not recs:
        raise q.ApiError(404, "Ticket not found.")
    card = _scrub(pas._shape_all(conn, recs, vendor_view=False, history=True)[0])
    return {"ticket": card, "comments": pas.list_comments(conn, tid).get("comments", []), "transcripts": pas.list_transcripts(conn, tid).get("transcripts", [])}


# ----------------------------------------------------------------------------------------------------------------------------- patch
def patch(conn, vendor: str, tid: int, body, user: str) -> dict:
    """Fix a ticket: only concept_name, concept_kind, cluster, variations (replaces the whole list; use the creative routes to ADD links), brief_url, creative_url, story. Another field is a 400 naming it;
    another vendor's ticket is a 404; a dropped ticket is a 409."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    bad = sorted(set(body) - set(PATCH_FIELDS))
    if bad:
        raise q.ApiError(400, f"field not allowed: {', '.join(bad)} (you can change: {', '.join(PATCH_FIELDS)})")
    if not body:
        raise q.ApiError(400, "nothing to update")
    _own(conn, vendor, tid)
    if "concept_name" in body and not str(body["concept_name"] or "").strip():
        raise q.ApiError(400, "concept_name cannot be empty")
    if "cluster" in body and not str(body["cluster"] or "").strip():
        raise q.ApiError(400, "cluster cannot be empty")
    if "concept_kind" in body and body["concept_kind"] not in ("new", "iteration"):
        raise q.ApiError(400, "concept_kind must be new or iteration")
    if "variations" in body:
        if not isinstance(body["variations"], list) or not body["variations"]:
            raise q.ApiError(400, "variations must be a list with at least one {label, url?, concept_id?}")
        _check_variation_urls(body["variations"])
    for f in ("brief_url", "creative_url"):
        if f in body:
            _opt_partner_url(body[f], f)
    f = pas.clean_patch(body)
    try:
        pas._update(tid, f, user, vendor=vendor)
    except q.ApiError as e:
        if e.status == 404:
            raise q.ApiError(404, "Ticket not found.") from None
        raise
    with pas._rw() as w:
        return shape(_own(w, vendor, tid))


# ----------------------------------------------------------------------------------------------------------------------------- creative
def _incoming(body, pre: str = "") -> list[dict]:
    """The variations of a creative push as [{label, url, concept_id}]: {variations: [...]} or the shorthand {creative_url} (= the Original). Each needs a label and an https link."""
    if not isinstance(body, dict):
        raise q.ApiError(400, pre + "JSON object body required")
    has_v, has_u = "variations" in body, "creative_url" in body
    if has_v and has_u:
        raise q.ApiError(400, pre + "send either variations or creative_url, not both")
    if has_u:
        return [{"label": "Original", "url": partner_url(body["creative_url"], pre + "creative_url"), "concept_id": ""}]
    if not has_v:
        raise q.ApiError(400, pre + "send variations: [{label, url, concept_id?}] or creative_url")
    raw = body["variations"]
    if not isinstance(raw, list) or not raw or len(raw) > MAX_VARIATIONS:
        raise q.ApiError(400, pre + "variations must be a list of 1 to 40 {label, url, concept_id?}")
    out = []
    for i, v in enumerate(raw):
        where = f"{pre}variations[{i + 1}]"
        if not isinstance(v, dict):
            raise q.ApiError(400, f"{where}: expected an object with label and url")
        try:
            label = pas._text(v.get("label"), "label", 60)
            cid = pas._text(v.get("concept_id"), "concept_id", pas.LIMITS["concept_id"])
        except q.ApiError as e:
            raise q.ApiError(400, f"{where}: {e.message}") from None
        if not label:
            raise q.ApiError(400, f"{where}.label: label is required (Original, Hook 2 ...)")
        if v.get("url") in (None, ""):
            raise q.ApiError(400, f"{where}.url: url is required")
        out.append({"label": label, "url": partner_url(v["url"], f"{where}.url"), "concept_id": cid})
    if "submit" in body and not isinstance(body["submit"], bool):
        raise q.ApiError(400, pre + "submit must be true or false")
    return out


def merge(existing: list, incoming: list[dict]) -> list[dict]:
    """Merge by label (case does not matter): a known label gets the new url (and concept_id when one is sent), a new label is appended, nothing is ever removed."""
    out = [{"label": v.get("label", ""), "url": v.get("url", ""), "concept_id": v.get("concept_id", "")} for v in (existing or []) if isinstance(v, dict)]
    index = {v["label"].lower(): v for v in out}
    for v in incoming:
        cur = index.get(v["label"].lower())
        if cur is None:
            cur = {"label": v["label"], "url": v["url"], "concept_id": v["concept_id"]}
            out.append(cur)
            index[v["label"].lower()] = cur
        else:
            cur["url"] = v["url"]
            if v["concept_id"]:
                cur["concept_id"] = v["concept_id"]
    if len(out) > MAX_VARIATIONS:
        raise q.ApiError(400, f"a ticket holds at most {MAX_VARIATIONS} variations")
    return out


def _apply_creative(w, vendor: str, tid: int, incoming: list[dict], submit: bool, user: str) -> dict:
    cur = _own(w, vendor, tid, for_update=True)
    merged = merge(cur["variations"], incoming)
    ids = list(cur["tracker_concept_ids"] or [])
    for v in incoming:
        if v["concept_id"] and v["concept_id"] not in ids:
            ids.append(v["concept_id"])
    if len(ids) > pas.LIMITS["concept_ids"]:
        ids = ids[:pas.LIMITS["concept_ids"]]
    first = next((v["url"] for v in merged if v["url"]), None)
    had = cur["creative_url"]
    w.execute("UPDATE creative_priorities_assignments SET variations = %s::jsonb, tracker_concept_ids = %s, creative_url = COALESCE(NULLIF(creative_url, ''), %s), "
              "creative_at = CASE WHEN COALESCE(creative_url, '') = '' THEN now() ELSE creative_at END, updated_at = now(), updated_by = %s WHERE id = %s",
              (json.dumps(merged), ids, first, user, tid))
    labels = ", ".join(v["label"] for v in incoming)
    pas._log(w, tid, user, "creative_added", cur["status"], cur["status"], f"{len(incoming)} link{'s' if len(incoming) != 1 else ''} from the partner: {labels}"[:300])
    status = cur["status"]
    submitted, message = False, ""
    if submit:
        tracker = pas._tracker_statuses(w, cur)
        was = pas.effective_status(cur, tracker)
        if was == "in_production" and (had or first):
            pas.set_status(w, tid, "in_review", user, was=was, note="Submitted for review by the partner")
            status, submitted = "in_review", True
        else:
            status = was
            message = (f"The ticket stays {pas.STATUS_LABELS.get(was, was)}: only a ticket that is In production can be submitted for review." if was != "in_production"
                       else "The ticket stays In production: it has no creative link yet.")
    row = _own(w, vendor, tid)
    return {"ticket": shape(row), "submitted": submitted, "status": status, **({"message": message} if message else {})}


def add_creative(vendor: str, tid: int, body, user: str) -> dict:
    """POST .../tickets/<id>/creative: merge {variations: [{label, url, concept_id?}]} (or the shorthand {creative_url} = Original) into the ticket; {submit: true} also moves it to In review when it is In production."""
    incoming = _incoming(body)
    submit = body.get("submit", False)
    if not isinstance(submit, bool):
        raise q.ApiError(400, "submit must be true or false")
    try:
        with pas._rw() as w:
            return _apply_creative(w, vendor, tid, incoming, submit, user)
    except psycopg.errors.UndefinedColumn as e:
        raise pas._missing_column(e) from None


def add_creative_batch(vendor: str, body, user: str) -> dict:
    """POST /api/priorities/partner/creative: {creatives: [{ticket_id, variations | creative_url, submit?}, ...]} (up to 100). Every item is checked first (the error says 'creative 3: ...'); every ticket must be this
    vendor's; nothing is saved when one is wrong."""
    items = body.get("creatives") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items or len(items) > MAX_BATCH:
        raise q.ApiError(400, "creatives must be a list of 1 to 100 {ticket_id, variations}")
    prepared = []
    for i, it in enumerate(items):
        pre = f"creative {i + 1}: "
        if not isinstance(it, dict):
            raise q.ApiError(400, pre + "expected an object with ticket_id and variations")
        tid = it.get("ticket_id")
        if isinstance(tid, bool) or not isinstance(tid, int):
            raise q.ApiError(400, pre + "ticket_id must be a whole number")
        sub = it.get("submit", False)
        if not isinstance(sub, bool):
            raise q.ApiError(400, pre + "submit must be true or false")
        prepared.append((tid, _incoming(it, pre), sub))
    try:
        with pas._rw() as w:
            for i, (tid, _inc, _s) in enumerate(prepared):
                try:
                    _own(w, vendor, tid)
                except q.ApiError as e:
                    raise q.ApiError(e.status, f"creative {i + 1}: {e.message}") from None
            results = [{"ticket_id": tid, **_apply_creative(w, vendor, tid, inc, sub, user)} for tid, inc, sub in prepared]
    except psycopg.errors.UndefinedColumn as e:
        raise pas._missing_column(e) from None
    return {"results": results}


# ----------------------------------------------------------------------------------------------------------------------------- contract
def contract(vendor: str, limit_per_min: int = WRITES_PER_MIN, scope: str = "partner_push") -> dict:
    """A machine-readable description of what this key can do, so a partner's tooling can check itself."""
    return {
        "vendor": vendor,
        "read_scope": "everything except secret-bearing routes (and the data brain at /mcp)" if scope == "partner_read_all" else "creative and performance data only",
        "key_scope": scope,
        "required_fields": ["concept_name", "brief or brief_url (at least one)"],
        "implied": {"vendor": "filled in from your key; sending a different vendor is refused"},
        "optional_fields": ["brief", "brief_url", "product", "cluster", "concept_kind", "variations", "icp", "pain_point", "story", "for_week", "format_code", "note", "due_date", "status"],
        "auto_tagging": "send the name and a short brief and the system fills in product, market cluster, ICP, pain point and new vs iteration; anything you send yourself is kept as you sent it. GET tickets shows tags: pending | complete.",
        "products": list(pas.PRODUCT_SLUGS),
        "concept_kind_values": ["new", "iteration"],
        "statuses_you_may_set": list(PARTNER_STATUSES),
        "variation": {"fields": {"label": "required, up to 60 characters", "url": "https link, up to 2000 characters", "concept_id": "optional"}, "max_per_ticket": MAX_VARIATIONS, "min_per_ticket": 1},
        "for_week": "any date in the week; it snaps to that week's Monday (YYYY-MM-DD)",
        "patchable_fields": list(PATCH_FIELDS),
        "links": {"scheme": "https only", "max_length": pas.LIMITS["url"], "plays_inside_ticket": PLAYS_IN_TICKET, "other_platforms": "any public https link works; it stays a link"},
        "limits": {"max_tickets_per_request": MAX_BATCH, "max_creatives_per_request": MAX_BATCH, "writes_per_minute": limit_per_min, "max_body_bytes": BODY_BYTES, "list_page_default": 100, "list_page_max": 500,
                   "concept_name_max": pas.LIMITS["concept_name"], "note_max": pas.LIMITS["note"]},
        "endpoints": [{"method": m, "path": p} for m, p in (("POST", "/api/priorities/tickets"), ("GET", "/api/priorities/partner/tickets"), ("GET", "/api/priorities/partner/all-tickets"), ("GET", "/api/priorities/partner/all-tickets/<id>"), ("PATCH", "/api/priorities/partner/tickets/<id>"),
                                                            ("POST", "/api/priorities/partner/tickets/<id>/creative"), ("POST", "/api/priorities/partner/creative"), ("GET", "/api/priorities/partner/contract"))],
        "generated": dt.date.today().isoformat(),
    }
