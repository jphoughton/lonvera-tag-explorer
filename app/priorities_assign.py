"""Production assignments on /priorities: a concept given to a vendor to make for a week. Tables creative_priorities_assignments and creative_priorities_vendor_links (migration 202); the optional creative
format of an assignment is creative_priorities_assignments.format_code -> creative_formats (migration 204; everything here still works when 204 is not applied: no formats, nothing chosen).

A concept stays in the ideation list exactly as it is; each "Assign to vendor" creates one assignment (concept x vendor x week). The stage of an assignment is never stored: it is derived from the
links, the timestamps and the status of the linked Creative Tracker rows (stage_of). Nothing here writes to the Google Sheet or Meta. The first part of the module is pure (no database): key building,
validation, stage and the "In production" text; the UI mock server reuses it over an in-memory store. The second part (storage) reads and writes the two tables.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
from urllib.parse import urlsplit

import psycopg

from . import queries as q

VENDORS = ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS", "PuredMedia", "CreateMastery", "OpenProgram", "Studio Flow"]     # spelled as in tracker_rows.vendor (Creative Tracker "Vendor" column)
STAGES = {"assigned": "Assigned", "brief_sent": "Brief sent", "creative_in": "Creative delivered", "ready": "Ready to launch", "launched": "Launched", "dropped": "Dropped"}
_ORDER = ["assigned", "brief_sent", "creative_in", "ready", "launched"]                  # progress order; dropped sits outside it
LIMITS = {"concept_name": 200, "note": 500, "vendor_note": 1000, "url": 2000, "concept_id": 200, "concept_ids": 20}
# the six creative formats as migration 204 seeds them; the database table is the truth, this copy serves the UI mock server and the not-yet-migrated fallback
FORMATS = [{"code": "F1", "name": "Long animated story, openly AI, told by the character", "length": "90s to 3 min"}, {"code": "F2", "name": "Talking head (realistic AI)", "length": "45 to 90s"},
           {"code": "F3", "name": "Short acted scene with two people", "length": "30 to 60s"}, {"code": "F4", "name": "Whiteboard explainer", "length": "60 to 90s"},
           {"code": "F5", "name": "Opens on a sound", "length": "30 to 60s"}, {"code": "F6", "name": "Animated song", "length": "45 to 90s"}]
CREATE_FIELDS = ("vendor", "for_week", "note", "format_code")
PATCH_FIELDS = ("vendor", "for_week", "note", "format_code", "brief_url", "creative_url", "tracker_concept_ids", "ready", "launched", "dropped", "status", "due_date", "priority", "position", "comment", "concept_name", "concept_kind")
PATCH_BOOLS = ("ready", "launched", "dropped")
VENDOR_PATCH_FIELDS = ("creative_url", "vendor_note", "ready", "status", "comment")
# the board (migration 245): the stored status of an assignment, in column order. `dropped` is not a status (dropped_at hides the card in an archived lane).
STATUSES = ["assigned", "brief_sent", "in_production", "in_review", "ready", "launched"]
STATUS_LABELS = {"assigned": "Assigned", "brief_sent": "Brief sent", "in_production": "In production", "in_review": "In review", "ready": "Ready to launch", "launched": "Launched"}
PRIORITIES = ("normal", "high")
LIMITS["comment"] = 2000
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CTRL_ALL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------------------------------------------- keys
def _norm(v) -> str:
    return _WS.sub(" ", str(v if v is not None else "")).strip().lower()


def text_key(row: dict) -> str:
    """'text:<product>|<cluster>|<icp>|<pain point>', lowercased, whitespace collapsed. Always stored, so a draft proposal that later becomes a real line still matches."""
    return "text:" + "|".join(_norm(row.get(k)) for k in ("product", "cluster", "icp", "pain_point"))


def concept_key(row: dict) -> str:
    """The stable concept handle. The page's row key includes the section (changes weekly) and line_code gets re-coded, so: line:<taxonomy_lines.id> when the row has a line, manual:<id> for a manual
    concept, otherwise the text key."""
    if row.get("manual") and row.get("id") is not None:
        return f"manual:{row['id']}"
    if row.get("line_id") is not None:
        return f"line:{row['line_id']}"
    return text_key(row)


def monday_of(d) -> dt.date:
    """The Monday of the ISO week containing d (a date, datetime or 'YYYY-MM-DD')."""
    if isinstance(d, dt.datetime):
        d = d.date()
    elif isinstance(d, str):
        try:
            d = dt.date.fromisoformat(d.strip()[:10])
        except ValueError:
            raise q.ApiError(400, "week must be a date like 2026-11-09") from None
    elif not isinstance(d, dt.date):
        raise q.ApiError(400, "week must be a date like 2026-11-09")
    return d - dt.timedelta(days=d.weekday())


# ---------------------------------------------------------------------------------------------------------------- validation
def _format_code(raw):
    """A format code as typed: blank or null means none (None); otherwise trimmed text. Whether it is a real, active format is check_format's job (needs the list)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise q.ApiError(400, "format must be text")
    return _CTRL_ALL.sub("", raw).strip()[:20]


def check_format(code, formats):
    """code (None = no format) must be one of the active formats [{code, ...}]; otherwise a 400 in plain English. Returns code."""
    if code is None:
        return None
    ok = [f["code"] for f in formats]
    if code not in ok:
        raise q.ApiError(400, "That creative format is not one of the available formats" + (": " + ", ".join(ok) if ok else "") + ".")
    return code


def clean_url(s) -> str:
    """A link: http or https only, at most 2,000 characters, control characters stripped. Anything else is a 400."""
    if not isinstance(s, str):
        raise q.ApiError(400, "link must be text")
    u = _CTRL_ALL.sub("", s).strip()
    if not u:
        raise q.ApiError(400, "link is empty")
    if len(u) > LIMITS["url"]:
        raise q.ApiError(400, f"link is at most {LIMITS['url']:,} characters")
    if re.search(r"\s", u):
        raise q.ApiError(400, "link must not contain spaces")
    try:
        p = urlsplit(u)
    except ValueError:
        raise q.ApiError(400, "link is not a valid web address") from None
    if p.scheme.lower() not in ("http", "https") or not p.netloc:
        raise q.ApiError(400, "link must start with http:// or https://")
    return u


def _opt_url(raw):
    """A link field of a PATCH: empty or null clears it (None), otherwise a clean URL."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return clean_url(raw)


def _text(raw, field: str, limit: int) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise q.ApiError(400, f"{field.replace('_', ' ')} must be text")
    s = _CTRL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    s = "\n".join(" ".join(ln.split()) for ln in s.split("\n")).strip()
    if len(s) > limit:
        raise q.ApiError(400, f"{field.replace('_', ' ')} is at most {limit} characters")
    return s


def _vendor(raw) -> str:
    if raw not in VENDORS:
        raise q.ApiError(400, "vendor must be one of: " + ", ".join(VENDORS))
    return raw


def _concept_ids(raw) -> list[str]:
    if not isinstance(raw, list):
        raise q.ApiError(400, "tracker_concept_ids must be a list")
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            raise q.ApiError(400, "each Concept ID must be text")
        s = " ".join(_CTRL_ALL.sub("", x).split())
        if not s:
            continue
        if len(s) > LIMITS["concept_id"]:
            raise q.ApiError(400, f"a Concept ID is at most {LIMITS['concept_id']} characters")
        if s not in out:
            out.append(s)
    if len(out) > LIMITS["concept_ids"]:
        raise q.ApiError(400, f"at most {LIMITS['concept_ids']} Concept IDs")
    return out


def clean_create(body: dict) -> dict:
    """Vendor, week (snapped to its Monday), note and optional format_code (None when none) of an Assign request. Other keys of the body (keys, snapshot_week) belong to the route and are ignored here."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    if body.get("for_week") in (None, ""):
        raise q.ApiError(400, "week is required")
    return {"vendor": _vendor(body.get("vendor")), "for_week": monday_of(body["for_week"]), "note": _text(body.get("note"), "note", LIMITS["note"]), "format_code": _format_code(body.get("format_code"))}


def clean_patch(body: dict) -> dict:
    """Only the keys present, validated. ready / launched / dropped must be booleans (the route sets or clears the matching timestamp); a link set to '' or null clears it."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    unknown = set(body) - set(PATCH_FIELDS)
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    out: dict = {}
    if "vendor" in body:
        out["vendor"] = _vendor(body["vendor"])
    if "for_week" in body:
        if body["for_week"] in (None, ""):
            raise q.ApiError(400, "week is required")
        out["for_week"] = monday_of(body["for_week"])
    if "note" in body:
        out["note"] = _text(body["note"], "note", LIMITS["note"])
    if "format_code" in body:
        out["format_code"] = _format_code(body["format_code"])
    for f in ("brief_url", "creative_url"):
        if f in body:
            out[f] = _opt_url(body[f])
    if "tracker_concept_ids" in body:
        out["tracker_concept_ids"] = _concept_ids(body["tracker_concept_ids"])
    for f in PATCH_BOOLS:
        if f in body:
            if not isinstance(body[f], bool):
                raise q.ApiError(400, f"{f} must be true or false")
            out[f] = body[f]
    if "status" in body:
        out["status"] = clean_status(body["status"])
    if "due_date" in body:
        out["due_date"] = None if body["due_date"] in (None, "") else _date(body["due_date"], "due date")
    if "priority" in body:
        if body["priority"] not in PRIORITIES:
            raise q.ApiError(400, "priority must be normal or high")
        out["priority"] = body["priority"]
    if "position" in body:
        out["position"] = clean_position(body["position"])
    if "concept_name" in body:
        out["concept_name"] = _text(body["concept_name"], "concept name", LIMITS["concept_name"])
    if "concept_kind" in body:
        if body["concept_kind"] in (None, ""):
            out["concept_kind"] = None
        elif body["concept_kind"] in ("new", "iteration"):
            out["concept_kind"] = body["concept_kind"]
        else:
            raise q.ApiError(400, "new vs iteration must be new or iteration")
    if "comment" in body:
        out["comment"] = _comment_opt(body["comment"])
    return out


def clean_status(raw) -> str:
    if raw == "dropped":
        raise q.ApiError(400, "Dropped is not a status: use Drop on the card.")
    if raw not in STATUSES:
        raise q.ApiError(400, "status must be one of: " + ", ".join(STATUSES))
    return raw


def clean_position(raw) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw or abs(raw) > 1e12:
        raise q.ApiError(400, "position must be a number")
    return float(raw)


def _date(raw, what: str) -> dt.date:
    if not isinstance(raw, str):
        raise q.ApiError(400, f"{what} must be a date like 2026-11-13")
    try:
        return dt.date.fromisoformat(raw.strip()[:10])
    except ValueError:
        raise q.ApiError(400, f"{what} must be a date like 2026-11-13") from None


def _comment_opt(raw) -> str | None:
    """An optional short comment sent with a status move: blank or null means none."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return clean_comment(raw)


def clean_comment(raw) -> str:
    """A comment body: text, 1 to 2,000 characters, line breaks kept."""
    if not isinstance(raw, str) or not raw.strip():
        raise q.ApiError(400, "Write something before posting.")
    return _text(raw, "comment", LIMITS["comment"])


def clean_vendor_patch(body: dict) -> dict:
    """What a vendor may change on their own link page: the creative link, a note, and Ready to launch (a real boolean: true stamps ready_at, false clears it). Nothing else; the creative format is
    shown to the vendor but is not in VENDOR_PATCH_FIELDS, so sending it is a 400 like any other field that is not theirs."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    unknown = set(body) - set(VENDOR_PATCH_FIELDS)
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    out: dict = {}
    if "creative_url" in body:
        out["creative_url"] = _opt_url(body["creative_url"])
    if "vendor_note" in body:
        out["vendor_note"] = _text(body["vendor_note"], "vendor_note", LIMITS["vendor_note"])
    if "ready" in body:
        if not isinstance(body["ready"], bool):
            raise q.ApiError(400, "ready must be true or false")
        out["ready"] = body["ready"]
    if "status" in body:
        out["status"] = clean_status(body["status"])
    if "comment" in body:
        out["comment"] = _comment_opt(body["comment"])
    return out


# ---------------------------------------------------------------------------------------------------------------- stage
def _tracker_rank(status) -> int:
    """Index in _ORDER that a Creative Tracker status implies: 3, 4, 5 = brief sent; 6 = creative delivered; 7 = ready; 8, 9 = launched; anything else = assigned."""
    m = re.match(r"\s*(\d+)", str(status or ""))
    n = int(m.group(1)) if m else 0
    if n in (8, 9):
        return 4
    return {7: 3, 6: 2, 3: 1, 4: 1, 5: 1}.get(n, 0)


def stage_of(rec: dict, tracker_statuses=()) -> str:
    """Stage key of an assignment: dropped if dropped_at is set; otherwise the furthest of what the assignment itself shows (launched_at, ready_at, creative link, brief link) and what its linked
    Creative Tracker rows show (the tracker wins when it is further along)."""
    if rec.get("dropped_at"):
        return "dropped"
    own = 0
    if rec.get("launched_at"):
        own = 4
    elif rec.get("ready_at"):
        own = 3
    elif rec.get("creative_url"):
        own = 2
    elif rec.get("brief_url"):
        own = 1
    best = max([own] + [_tracker_rank(s) for s in (tracker_statuses or ())])
    return _ORDER[best]


def stage_label(key: str) -> str:
    return STAGES.get(key, key)


# ---------------------------------------------------------------------------------------------------------------- status (the board column, stored since migration 245)
def status_rank(status) -> int:
    return STATUSES.index(status) if status in STATUSES else 0


def status_of_facts(rec: dict, tracker_statuses=()) -> str:
    """The status the links and timestamps of an assignment imply: launched_at, ready_at, a creative link (in review), a brief link (brief sent), else assigned; the Creative Tracker rows can only push it
    further (3 to 5 brief sent, 6 in review, 7 ready, 8 and 9 launched). Used to backfill, to follow link edits and to pull a card forward."""
    own = 0
    if rec.get("launched_at"):
        own = 5
    elif rec.get("ready_at"):
        own = 4
    elif rec.get("creative_url"):
        own = 3
    elif rec.get("brief_url"):
        own = 1
    best = max([own] + [_TRACKER_STATUS_RANK.get(_tracker_rank(s), 0) for s in (tracker_statuses or ())])
    return STATUSES[best]


_TRACKER_STATUS_RANK = {0: 0, 1: 1, 2: 3, 3: 4, 4: 5}      # _tracker_rank (assigned, brief, creative in, ready, launched) -> index in STATUSES


def tracker_status_of(tracker_statuses) -> str | None:
    """The board status the linked tracker rows imply, or None when they imply nothing (idea or no rows)."""
    best = max([_tracker_rank(s) for s in (tracker_statuses or ())], default=0)
    return STATUSES[_TRACKER_STATUS_RANK[best]] if best else None


def stage_from_status(rec: dict, status: str) -> str:
    """The older stage word the existing pages still read, derived from the status: in production reads as brief sent (creative delivered once a creative link exists), in review as creative delivered,
    and a dropped assignment is dropped whatever its status."""
    if rec.get("dropped_at"):
        return "dropped"
    if status == "in_production":
        return "creative_in" if rec.get("creative_url") else "brief_sent"
    return {"in_review": "creative_in"}.get(status, status)


# transitions. App users (a login or an API key) may move a card anywhere except to Launched; vendors only start, submit and pull back.
VENDOR_MOVES = {("assigned", "in_production"), ("brief_sent", "in_production"), ("in_production", "in_review"), ("in_review", "in_production")}


def check_transition(cur: str, new: str, *, vendor: bool, creative_url, comment) -> str:
    """Validate a status change; returns '' or a note for the history ('Skipped review'). Raises a 400 in plain English when it is not allowed. A move to the status the card is already in is always fine."""
    if new == cur:
        return ""
    if vendor:
        if (cur, new) not in VENDOR_MOVES:
            raise q.ApiError(400, "You can start a task, send it for review, or pull it back from review. The rest is done by the team.")
        if new == "in_review" and not creative_url:
            raise q.ApiError(400, "Add the creative link before sending it for review.")
        return ""
    if new == "launched":
        raise q.ApiError(400, "A card becomes Launched with Mark launched or when its Creative Tracker row is launched. It cannot be moved there.")
    if new == "in_review" and not creative_url:
        raise q.ApiError(400, "Add a creative link before sending to review.")
    if cur == "in_review" and new == "in_production" and not comment:
        raise q.ApiError(400, "Say what to change: add a comment when requesting changes.")
    if new == "ready" and cur != "in_review":
        return "Skipped review"
    return ""


# ---------------------------------------------------------------------------------------------------------------- the "In production" text
def _week_date(v) -> dt.date:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


def summary_for_row(assignments) -> str:
    """The "In production" cell of an ideation row: the latest open assignment as 'Madre, week of 11/9: brief sent' plus ', +N earlier' for the other undropped ones. An open assignment is one that
    is not launched or dropped; when every undropped assignment is launched, the latest launched one shows (what was made last time). Dropped ones are ignored; no assignment gives ''.
    Each assignment is a dict with vendor, for_week and stage (or the fields stage_of reads)."""
    live = []
    for a in assignments or []:
        stage = a.get("stage") or stage_of(a, a.get("tracker_statuses") or ())
        if stage != "dropped":
            live.append((a, stage))
    if not live:
        return ""
    live.sort(key=lambda t: (_week_date(t[0]["for_week"]), t[0].get("id") or 0), reverse=True)
    pick = next((t for t in live if t[1] != "launched"), live[0])
    a, stage = pick
    d = _week_date(a["for_week"])
    text = f"{a['vendor']}, week of {d.month}/{d.day}: {stage_label(stage).lower()}"
    n = len(live) - 1
    return text + (f", +{n} earlier" if n else "")


# ================================================================================================================ storage
MAX_KEYS = 100
VENDOR_RECENT_DAYS = 28                      # the vendor page also shows what was launched in the last four weeks
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,64}")
COLS = ("id, concept_key, text_key, product, vendor, line_id, manual_id, line_code, cluster, icp, pain_point, story, section_at_assign, note, vendor_note, for_week, brief_url, creative_url, "
        "brief_at, creative_at, ready_at, launched_at, dropped_at, tracker_concept_ids, created_at, updated_at, created_by, updated_by")
STRUCT_COLS = ("concept_name", "concept_kind")                                         # migration 258: the ticket fields; a database without it still serves every page
BOARD_COLS = ("status", "due_date", "priority", "position", "status_changed_at")        # migration 245; a database without it still serves every page (status is then derived from the links)
FMT_COLS = ("format_code", "format_name", "format_length")                               # on every row; None when no format was chosen (or migration 204 is not applied)
INTERNAL_ONLY = ("note", "created_by", "updated_by", "suggested_concept_ids")          # never sent to a vendor link page
_TS = ("brief_at", "creative_at", "ready_at", "launched_at", "dropped_at", "created_at", "updated_at", "status_changed_at")
VENDOR_HIDDEN = INTERNAL_ONLY + ("priority", "position")


def _rw():
    from .projection import rwdb          # the app's own pool is read-only
    return rwdb.get_rw()


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _num(v):
    return None if v is None else float(v)


def _tracker_ads(conn, recs: list[dict]) -> dict:
    """{assignment id: [ad, ...]} for the tracker rows linked to each assignment: concept_id in tracker_concept_ids, or Brief Link = brief_url, or asset_url = creative_url (exact, non-empty, computed now).
    One entry per ad family (spend and return are the family's own launch totals from ad_metrics). `explicit` marks a Concept ID typed on the assignment."""
    ids = sorted({c for r in recs for c in (r.get("tracker_concept_ids") or [])})
    briefs = sorted({r["brief_url"] for r in recs if r.get("brief_url")})
    assets = sorted({r["creative_url"] for r in recs if r.get("creative_url")})
    if not (ids or briefs or assets):
        return {r["id"]: [] for r in recs}
    try:
        rows = conn.execute(
            """SELECT t.id, t.concept_id, t.ad_family, t.family_key, t.status, t.launch_date, t.asset_url, t.raw->>'Brief Link' AS brief_link, t.sheet_row,
                      f.spend_launch, f.revenue_launch, f.purchases_launch
               FROM tracker_rows t
               LEFT JOIN LATERAL (SELECT m.spend_launch, m.revenue_launch, m.purchases_launch FROM ad_metrics m WHERE m.family_key = t.family_key AND m.product_id = t.product_id AND m.is_first_row LIMIT 1) f ON true
               WHERE t.deleted_at IS NULL AND (t.concept_id = ANY(%s) OR t.asset_url = ANY(%s) OR t.raw->>'Brief Link' = ANY(%s))
               ORDER BY t.sheet_row NULLS LAST, t.id""", (ids, assets, briefs)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {r["id"]: [] for r in recs}
    out: dict = {}
    for r in recs:
        mine = set(r.get("tracker_concept_ids") or [])
        seen: dict = {}
        for t in rows:
            explicit = t["concept_id"] in mine
            by_link = (bool(r.get("brief_url")) and t["brief_link"] == r["brief_url"]) or (bool(r.get("creative_url")) and t["asset_url"] == r["creative_url"])
            if not (explicit or by_link):
                continue
            prev = seen.get(t["family_key"])
            if prev is not None and (prev["explicit"] or not explicit):
                continue
            spend, rev = _num(t["spend_launch"]), _num(t["revenue_launch"])
            seen[t["family_key"]] = {"concept_id": t["concept_id"], "ad_family": t["ad_family"] or None, "status": t["status"], "launch_date": _iso(t["launch_date"]), "spend": spend,
                                     "roas": round(rev / spend, 2) if spend and rev is not None else None, "ad_url": f"/ad/{t['id']}", "explicit": explicit}
        out[r["id"]] = list(seen.values())
    return out


# ---------------------------------------------------------------------------------------------------------------- history (who did what, when)
EVENT_KINDS = ("assigned", "brief_added", "brief_removed", "creative_added", "creative_removed", "ready", "ready_cleared", "launched", "launched_cleared", "dropped", "restored", "vendor", "week", "format",
               "status_changed", "due_changed", "priority_changed", "comment_added")


def _log(w, aid: int, actor: str | None, kind: str, before: str | None, after: str | None, detail: str = "") -> None:
    """One history row, in the caller's transaction. A savepoint keeps the change itself safe when migration 243 is not applied yet."""
    try:
        with w.transaction():
            w.execute("INSERT INTO creative_priorities_assignment_events (assignment_id, actor, kind, stage_before, stage_after, detail) VALUES (%s,%s,%s,%s,%s,%s)", (aid, actor, kind, before, after, detail))
    except psycopg.errors.UndefinedTable:
        pass


def _snapshot(w, aid: int, where: str, params: list) -> dict | None:
    """The assignment as it is now (explicit columns, so a changed table never trips a cached plan), with format_code when migration 204 is applied."""
    rec = w.execute(f"SELECT {COLS}, {', '.join(BOARD_COLS)} FROM creative_priorities_assignments WHERE {where}", params).fetchone()
    if rec is None:
        return None
    try:
        with w.transaction():
            rec = {**rec, "format_code": w.execute("SELECT format_code FROM creative_priorities_assignments WHERE id = %s", (aid,)).fetchone()["format_code"]}
    except psycopg.errors.UndefinedColumn:
        pass
    return rec


def events_for(before: dict, after: dict) -> list[tuple[str, str | None, str, str]]:
    """What changed between two assignment rows, as [(kind, stage_before, stage_after, detail)]. The stage is the one the row itself shows (stage_of with no tracker rows)."""
    sb, sa = stage_of(before), stage_of(after)
    out: list[tuple[str, str | None, str, str]] = []
    for col, kind in (("vendor", "vendor"), ("for_week", "week"), ("format_code", "format"), ("due_date", "due_changed"), ("priority", "priority_changed")):
        a, b = before.get(col), after.get(col)
        if a != b:
            out.append((kind, sa, sa, f"{_iso(a) or 'none'} to {_iso(b) or 'none'}"))
    for col, on, off in (("brief_url", "brief_added", "brief_removed"), ("creative_url", "creative_added", "creative_removed")):
        if not before.get(col) and after.get(col):
            out.append((on, sb, sa, ""))
        elif before.get(col) and not after.get(col):
            out.append((off, sb, sa, ""))
    for col, on, off in (("ready_at", "ready", "ready_cleared"), ("launched_at", "launched", "launched_cleared"), ("dropped_at", "dropped", "restored")):
        if not before.get(col) and after.get(col):
            out.append((on, sb, sa, ""))
        elif before.get(col) and not after.get(col):
            out.append((off, sb, sa, ""))
    return out


def _history(conn, ids: list[int]) -> dict[int, list[dict]]:
    """{assignment id: [event, ...]} oldest first; empty when migration 243 is not applied."""
    if not ids:
        return {}
    try:
        rows = conn.execute("SELECT assignment_id, at, actor, kind, stage_before, stage_after, detail FROM creative_priorities_assignment_events WHERE assignment_id = ANY(%s) ORDER BY at, id", (ids,)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {}
    out: dict[int, list[dict]] = {i: [] for i in ids}
    for r in rows:
        out[r["assignment_id"]].append({"at": _iso(r["at"]), "actor": r["actor"], "kind": r["kind"], "stage_before": r["stage_before"], "stage_after": r["stage_after"], "detail": r["detail"]})
    return out


def today_pt() -> dt.date:
    """Today's date in Pacific time (the team's day), for due dates."""
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()


def effective_status(rec: dict, tracker_statuses=()) -> str:
    """The stored status, pulled forward (never back) by the linked Creative Tracker rows; derived from the links when the row has no stored status yet (migration 245 not applied)."""
    cur = rec.get("status") or status_of_facts(rec)
    t = tracker_status_of(tracker_statuses)
    return t if t and status_rank(t) > status_rank(cur) else cur


def _shape(rec: dict, ads: list[dict], vendor_view: bool = False, today: dt.date | None = None, comment_count: int = 0) -> dict:
    statuses = [a["status"] for a in ads]
    status = effective_status(rec, statuses)
    stage = stage_from_status(rec, status)
    best = max(statuses, key=_tracker_rank, default=None) if statuses else None
    d = {k: (_iso(rec[k]) if k in _TS or k in ("for_week", "due_date") else rec[k]) for k in rec}
    due = rec.get("due_date")
    days = None if (due is None or status == "launched" or rec.get("dropped_at")) else (due - (today or today_pt())).days
    d.update(status=status, status_label=STATUS_LABELS[status], due_date=_iso(due), due_in_days=days, overdue=days is not None and days < 0, priority=rec.get("priority") or "normal",
             position=rec.get("position") or 0.0, comment_count=comment_count, status_changed_at=_iso(rec.get("status_changed_at")))
    if d.get("section_at_assign"):
        from etl import creative_priorities as cp
        d["section_at_assign"] = cp.norm_section(d["section_at_assign"])        # assignments made before 2026-11-10 stored the old section name; shown as the new Action
    d["tracker_concept_ids"] = list(rec.get("tracker_concept_ids") or [])
    d.update(stage=stage, stage_label=stage_label(stage), tracker_status=best)
    d["suggested_concept_ids"] = [a["concept_id"] for a in ads if not a["explicit"]]
    d["ads"] = [{k: v for k, v in a.items() if k != "explicit"} for a in ads]
    if vendor_view:
        for k in VENDOR_HIDDEN:
            d.pop(k, None)
        for a in d["ads"]:
            a.pop("ad_url", None)                      # the ad page needs a login
    return d


def _fetch(conn, where: str = "", params: tuple = ()) -> list[dict]:
    acols = ", ".join("a." + c.strip() for c in COLS.split(","))
    bcols = ", ".join("a." + c for c in BOARD_COLS)
    join = "FROM creative_priorities_assignments a LEFT JOIN creative_formats f ON f.code = a.format_code"
    scols = ", ".join("a." + c for c in STRUCT_COLS)
    try:
        return conn.execute(f"SELECT {acols}, {bcols}, {scols}, a.format_code, f.name AS format_name, f.length AS format_length {join} {where} ORDER BY a.for_week DESC, a.vendor, a.id", params).fetchall()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):          # migration 258 (ticket fields) not applied yet
        conn.rollback()
    try:
        return conn.execute(f"SELECT {acols}, {bcols}, a.format_code, f.name AS format_name, f.length AS format_length {join} {where} ORDER BY a.for_week DESC, a.vendor, a.id", params).fetchall()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):          # migration 245 (board), 204 (formats) or 202 (assignments) not applied yet
        conn.rollback()
    try:
        return conn.execute(f"SELECT {acols}, a.format_code, f.name AS format_name, f.length AS format_length {join} {where} ORDER BY a.for_week DESC, a.vendor, a.id", params).fetchall()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
    try:
        return [{**r, "format_code": None, "format_name": None, "format_length": None} for r in conn.execute(f"SELECT {COLS} FROM creative_priorities_assignments {where} ORDER BY for_week DESC, vendor, id", params).fetchall()]
    except psycopg.errors.UndefinedTable:         # migration 202 not applied yet: the pages still work, with nothing assigned
        conn.rollback()
        return []


def formats(conn, active_only: bool = True) -> list[dict]:
    """The creative formats [{code, name, length}] in pick-list order; the built-in six when migration 204 is not applied yet (choosing one then fails with a 503 on write)."""
    try:
        rows = conn.execute("SELECT code, name, length FROM creative_formats" + (" WHERE active" if active_only else "") + " ORDER BY sort, code").fetchall()
        return [{"code": r["code"], "name": r["name"], "length": r["length"]} for r in rows]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return [dict(f) for f in FORMATS]


def _weeks(conn) -> list[str]:
    try:
        return [_iso(r["for_week"]) for r in conn.execute("SELECT DISTINCT for_week FROM creative_priorities_assignments ORDER BY for_week DESC").fetchall()]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return []


def _comment_counts(conn, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    try:
        return {r["assignment_id"]: r["n"] for r in conn.execute("SELECT assignment_id, count(*) AS n FROM creative_priorities_assignment_comments WHERE assignment_id = ANY(%s) GROUP BY 1", (ids,)).fetchall()}
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {}


def _shape_all(conn, recs: list[dict], vendor_view: bool = False, history: bool = True) -> list[dict]:
    ads = _tracker_ads(conn, recs)
    counts = _comment_counts(conn, [r["id"] for r in recs])
    today = today_pt()
    shaped = [_shape(r, ads[r["id"]], vendor_view, today, counts.get(r["id"], 0)) for r in recs]
    if not vendor_view and history:                                      # who did what is internal: a vendor link page never gets it
        hist = _history(conn, [r["id"] for r in recs])
        for d in shaped:
            d["history"] = hist.get(d["id"], [])
    return shaped


def _multi(raw, clean) -> list:
    """A filter value that may hold several choices, comma separated ("Madre,Victoria"): each part cleaned and de-duplicated, at most 30; [] when nothing is chosen."""
    if raw in (None, ""):
        return []
    return list(dict.fromkeys(clean(p.strip()) for p in str(raw).split(",") if p.strip()))[:30]


def list_assignments(conn, week=None, vendor=None, include_closed: bool = False) -> dict:
    """{rows, vendors, weeks, formats}. Default: open rows (not launched, not dropped); include_closed adds launched and dropped. week snaps to its Monday and vendor must be a known vendor; each may list several, comma separated."""
    where, params = [], []
    weeks_, vendors_ = _multi(week, monday_of), _multi(vendor, _vendor)
    if weeks_:
        where.append("for_week = ANY(%s)")
        params.append(weeks_)
    if vendors_:
        where.append("vendor = ANY(%s)")
        params.append(vendors_)
    rows = _shape_all(conn, _fetch(conn, ("WHERE " + " AND ".join(where)) if where else "", tuple(params)))
    if not include_closed:
        rows = [r for r in rows if r["stage"] not in ("launched", "dropped")]
    return {"rows": rows, "vendors": list(VENDORS), "weeks": _weeks(conn), "formats": formats(conn)}


def _after_write(ids: list[int], vendor_view: bool = False, history: bool = True) -> list[dict]:
    """The just-written rows, read on a fresh read-write connection (the request's read connection may hold an older snapshot that does not see the write yet)."""
    if not ids:
        return []
    with _rw() as w:
        recs = _fetch(w, "WHERE id = ANY(%s)", (ids,))
        shaped = _shape_all(w, recs, vendor_view, history)
    return sorted(shaped, key=lambda a: ids.index(a["id"]))


PRODUCT_SLUGS = ("vita-bull", "beef-liver", "boneflex", "vita-belle")
TICKET_STATUSES = ("assigned", "brief_sent", "in_production", "in_review")             # a new ticket can start anywhere before Ready to launch


def create_ticket(conn, body: dict, user: str | None = None) -> dict:
    """A ticket added straight from the board (no row of the weekly list behind it): {concept_name, product, vendor, for_week?, cluster?, icp?, pain_point?, concept_kind?, format_code?, brief_url?, note?, due_date?,
    status?} -> the new card. The concept key is 'ticket:<random>', so the same concept can be added twice on purpose; the text key still lets it show next to a matching concept on /priorities."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    name = _text(body.get("concept_name"), "concept name", LIMITS["concept_name"])
    if not name:
        raise q.ApiError(400, "concept name is required")
    product = body.get("product")
    if product not in PRODUCT_SLUGS:
        raise q.ApiError(400, "product must be one of: " + ", ".join(PRODUCT_SLUGS))
    vendor = _vendor(body.get("vendor"))
    week = monday_of(body.get("for_week") or today_pt())
    cluster, icp, pain = (_text(body.get(k), k.replace("_", " "), 200) for k in ("cluster", "icp", "pain_point"))
    kind = body.get("concept_kind")
    if kind in (None, ""):
        kind = None
    elif kind not in ("new", "iteration"):
        raise q.ApiError(400, "new vs iteration must be new or iteration")
    status = body.get("status") or "assigned"
    if status not in TICKET_STATUSES:
        raise q.ApiError(400, "status must be one of: " + ", ".join(TICKET_STATUSES))
    due = week + dt.timedelta(days=4) if body.get("due_date") in (None, "") else _date(body["due_date"], "due date")
    brief = _opt_url(body.get("brief_url"))
    note = _text(body.get("note"), "note", LIMITS["note"])
    fc = check_format(_format_code(body.get("format_code")), formats(conn))
    try:
        with _rw() as w:
            pos = w.execute("SELECT COALESCE(max(position), 0) + 1 AS p FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL", (status,)).fetchone()["p"]
            row = w.execute(
                """INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, line_code, cluster, icp, pain_point, story, section_at_assign, note, for_week, created_by, updated_by,
                       concept_name, concept_kind, format_code, brief_url, brief_at, status, due_date, priority, position, status_changed_at)
                   VALUES (%s,%s,%s,%s,'',%s,%s,%s,'','test',%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s::text IS NOT NULL THEN now() END,%s,%s,'normal',%s,now()) RETURNING id""",
                ("ticket:" + secrets.token_hex(8), text_key({"product": product, "cluster": cluster, "icp": icp, "pain_point": pain}), product, vendor, cluster, icp, pain, note, week, user, user,
                 name, kind, fc, brief, brief, status, due, pos)).fetchone()
            _log(w, row["id"], user, "assigned", None, "assigned", f"{vendor}, week of {week}")
            if status != "assigned":
                _log(w, row["id"], user, "status_changed", "assigned", status, "Added on the board")
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    except psycopg.errors.UndefinedColumn as e:
        raise _missing_column(e) from None
    return _after_write([row["id"]])[0]


def create_assignments(conn, body: dict, user: str | None = None) -> dict:
    """Assign concepts (row keys of a snapshot) to a vendor for a week. The server resolves the rows from the stored snapshot, never from client text. Duplicates (same concept, vendor and week,
    not dropped) are skipped. -> {created: [assignment, ...], skipped: [{key, reason}]}"""
    from . import creative_priorities as cpr
    from etl.creative_priorities import norm_key
    f = clean_create(body)
    f["format_code"] = check_format(f["format_code"], formats(conn))
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and 0 < len(k) <= 400 for k in keys):
        raise q.ApiError(400, "keys must be a list of concept row keys")
    keys = list(dict.fromkeys(keys))
    if len(keys) > MAX_KEYS:
        raise q.ApiError(400, f"at most {MAX_KEYS} concepts at a time")
    sw = body.get("snapshot_week")
    full = cpr.payload(conn, sw if isinstance(sw, str) and sw else None)
    by_key = {cpr.row_key(r): r for r in full["rows"]}
    created_ids: list[int] = []
    skipped: list[dict] = []
    try:
        with _rw() as w:
            for k in keys:
                r = by_key.get(norm_key(k))
                if r is None:
                    skipped.append({"key": k, "reason": "not in this week's list"})
                    continue
                if not (r.get("cluster") or "").strip() and not (r.get("pain_point") or "").strip():
                    skipped.append({"key": k, "reason": "no cluster or pain point"})
                    continue
                fc = f["format_code"]
                row = w.execute(
                    """INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, line_id, manual_id, line_code, cluster, icp, pain_point, story, section_at_assign, note, for_week, created_by, updated_by, due_date, status_changed_at, position"""
                    + (", format_code" if fc else "") + """)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),(SELECT COALESCE(max(position), 0) + 1 FROM creative_priorities_assignments WHERE status = 'assigned' AND dropped_at IS NULL)""" + (",%s" if fc else "") + """)
                       ON CONFLICT (concept_key, vendor, for_week) WHERE dropped_at IS NULL DO NOTHING RETURNING id""",
                    (concept_key(r), text_key(r), r["product"], f["vendor"], r.get("line_id"), r["id"] if r.get("manual") else None, r.get("line_code") or "", r.get("cluster") or "", r.get("icp") or "",
                     r.get("pain_point") or "", r.get("story") or "", r.get("section") or "", f["note"], f["for_week"], user, user, f["for_week"] + dt.timedelta(days=4), *([fc] if fc else []))).fetchone()
                if row is None:
                    skipped.append({"key": k, "reason": "already assigned to this vendor for that week"})
                else:
                    created_ids.append(row["id"])
                    _log(w, row["id"], user, "assigned", None, "assigned", f"{f['vendor']}, week of {f['for_week']}")
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    except psycopg.errors.UndefinedColumn as e:
        raise _missing_column(e) from None
    return {"created": _after_write(created_ids), "skipped": skipped}


def _missing_column(e) -> q.ApiError:
    return q.ApiError(503, "Creative formats are not set up yet (migration 204 is not applied)." if "format_code" in str(e) else "The Production board is not set up yet (migration 245 is not applied).")


def _author(user: str | None) -> str:
    return user if user and user != "guest" else "Team"


# ---------------------------------------------------------------------------------------------------------------- status: one helper for every writer
def _column_ids(w, status: str) -> list[int]:
    """Ids of the open cards of one board column in display order (high priority first, then position)."""
    return [r["id"] for r in w.execute("SELECT id FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL ORDER BY (priority = 'high') DESC, position, id", (status,)).fetchall()]


def _excerpt(text: str, n: int = 300) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "..."


def set_status(w, aid: int, new: str, actor: str | None, *, was: str | None = None, position: float | None = None, note: str = "", comment: str | None = None, event: bool = True) -> bool:
    """The ONLY writer of creative_priorities_assignments.status (the Production page, the move route, a vendor, the tracker pull-forward, Mark ready and Mark launched all end here). In the caller's
    transaction it sets status, status_changed_at, position (the bottom of the new column unless position is given) and keeps ready_at and launched_at in step (ready sets ready_at, launched sets both
    launched_at and keeps ready_at, anything earlier clears them), then writes one 'status_changed' history row (stage_before / stage_after are the status values) unless event=False (a caller that already
    wrote a more specific history row, such as 'ready' for the old Ready flag). `was` is the status the card showed (the tracker can show it further along than the stored one). Returns True when it changed."""
    cur = w.execute("SELECT status FROM creative_priorities_assignments WHERE id = %s FOR UPDATE", (aid,)).fetchone()
    if cur is None:
        raise q.ApiError(404, "Assignment not found.")
    if new not in STATUSES:
        raise q.ApiError(400, "status must be one of: " + ", ".join(STATUSES))
    was = was or cur["status"]
    if new == cur["status"] and position is None:
        return False
    if position is None:
        position = w.execute("SELECT COALESCE(max(position), 0) + 1 AS p FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL AND id <> %s", (new, aid)).fetchone()["p"]
    w.execute("""UPDATE creative_priorities_assignments SET status = %s, status_changed_at = now(), position = %s, updated_at = now(), updated_by = %s,
                 ready_at = CASE WHEN %s = 'ready' THEN COALESCE(ready_at, now()) WHEN %s = 'launched' THEN ready_at ELSE NULL END,
                 launched_at = CASE WHEN %s = 'launched' THEN COALESCE(launched_at, now()) ELSE NULL END WHERE id = %s""", (new, position, actor, new, new, new, aid))
    if event and was != new:
        detail = "; ".join(x for x in (note, _excerpt(comment) if comment else "") if x)
        _log(w, aid, actor, "status_changed", was, new, detail)
    return was != new


def _add_comment(w, aid: int, kind: str, author: str, body: str) -> dict:
    r = w.execute("INSERT INTO creative_priorities_assignment_comments (assignment_id, author_kind, author_name, body) VALUES (%s,%s,%s,%s) RETURNING id, author_kind, author_name, body, created_at", (aid, kind, author, body)).fetchone()
    return _comment_json(r)


def _comment_json(r: dict) -> dict:
    return {"id": r["id"], "author_kind": r["author_kind"], "author_name": r["author_name"], "body": r["body"], "created_at": _iso(r["created_at"])}


def _tracker_statuses(w, rec: dict) -> list[str]:
    return [a["status"] for a in _tracker_ads(w, [rec])[rec["id"]]]


def _reconcile(w, aid: int, before: dict, after: dict, actor: str | None) -> None:
    """After a link or flag edit without an explicit status: follow the links the way the old derived stage did. A link, Ready or Launched that was added pulls the status forward (never back); one that
    was removed lets a card that depended on it fall back to what is left. The history already holds the specific row (brief_added, ready, ...), so no status_changed row is written."""
    tstat = _tracker_statuses(w, after)
    cur = effective_status(after, tstat)
    own_before, own_after = status_of_facts(before), status_of_facts(after)
    target = cur
    if status_rank(own_after) > status_rank(own_before):
        target = max(cur, own_after, tracker_status_of(tstat) or "assigned", key=status_rank)
    elif status_rank(own_after) < status_rank(own_before) and status_rank(cur) >= status_rank("in_review"):
        target = max(own_after, tracker_status_of(tstat) or "assigned", key=status_rank)
    if target != after.get("status"):
        set_status(w, aid, target, actor, was=after.get("status"), event=False)


def pull_forward(w, actor: str = "tracker") -> int:
    """Move every open assignment forward to what its linked Creative Tracker rows show (7 = ready, 8 and 9 = launched, 6 = in review, 3 to 5 = brief sent), never backward. Writes a status_changed row
    each (actor 'tracker'). Used by the nightly etl.production_status. Returns how many cards moved."""
    recs = _fetch(w, "WHERE dropped_at IS NULL AND status <> 'launched'")
    ads = _tracker_ads(w, recs)
    n = 0
    for r in recs:
        stats = [a["status"] for a in ads[r["id"]]]
        t = tracker_status_of(stats)
        if t and status_rank(t) > status_rank(r["status"]):
            if set_status(w, r["id"], t, actor, note="Creative Tracker row moved on"):
                n += 1
    return n


def _update(aid: int, f: dict, user: str | None, vendor: str | None = None, legacy_ready: bool = False) -> None:
    """Apply a cleaned patch. Links stamp their first-added time and clear it with the link; ready / launched / dropped set or clear their timestamp. An explicit status goes through check_transition and
    set_status; without one the status follows the links (_reconcile). vendor= restricts to that vendor's row (404 otherwise) and applies the vendor rules."""
    f = dict(f)
    comment = f.pop("comment", None)
    new_status = f.pop("status", None)
    if not f and new_status is None and comment is None:
        raise q.ApiError(400, "nothing to update")
    where, wp = "id = %s", [aid]
    if vendor is not None:
        where += " AND vendor = %s"
        wp.append(vendor)
    try:
        with _rw() as w:
            before = _snapshot(w, aid, where, wp)
            if before is None:
                raise q.ApiError(404, "Assignment not found.")
            if "for_week" in f and "due_date" not in f and before.get("due_date") == before["for_week"] + dt.timedelta(days=4):
                f["due_date"] = f["for_week"] + dt.timedelta(days=4)             # the default due date (Friday) follows the week
            was = effective_status(before, _tracker_statuses(w, before))
            note = ""
            if new_status is not None:
                if before["dropped_at"]:
                    raise q.ApiError(400, "This concept was dropped. Restore it before moving it.")
                creative = f["creative_url"] if "creative_url" in f else before["creative_url"]
                note = "" if legacy_ready else check_transition(was, new_status, vendor=vendor is not None, creative_url=creative, comment=comment)
            sets: list[str] = []
            params: list = []
            for c in ("vendor", "for_week", "note", "vendor_note", "format_code", "due_date", "priority", "position", "concept_name", "concept_kind"):
                if c in f:
                    sets.append(f"{c} = %s")
                    params.append(f[c])
            for url, at in (("brief_url", "brief_at"), ("creative_url", "creative_at")):
                if url in f:
                    sets.append(f"{url} = %s")
                    params.append(f[url])
                    sets.append(f"{at} = " + ("COALESCE(" + at + ", now())" if f[url] else "NULL"))
            if "tracker_concept_ids" in f:
                sets.append("tracker_concept_ids = %s")
                params.append(f["tracker_concept_ids"])
            for flag, at in (("ready", "ready_at"), ("launched", "launched_at"), ("dropped", "dropped_at")):
                if flag in f:
                    sets.append(f"{at} = " + (f"COALESCE({at}, now())" if f[flag] else "NULL"))
            after = before
            if sets:
                w.execute(f"UPDATE creative_priorities_assignments SET {', '.join(sets)}, updated_at = now(), updated_by = %s WHERE {where}", [*params, user, *wp])
                after = _snapshot(w, aid, "id = %s", [aid])
                for kind, sb, sa, detail in events_for(before, after):
                    _log(w, aid, user, kind, sb, sa, detail)
            if new_status is not None:
                body = comment
                if comment and was == "in_review" and new_status == "in_production" and vendor is None:
                    body = "Changes requested: " + comment
                if new_status != was:
                    set_status(w, aid, new_status, user, was=was, note=note, comment=body)
                if body:
                    _add_comment(w, aid, "vendor" if vendor is not None else "app", vendor or _author(user), body)
            else:
                if comment:
                    _add_comment(w, aid, "vendor" if vendor is not None else "app", vendor or _author(user), comment)
                    _log(w, aid, user, "comment_added", None, None, _excerpt(comment))
                if sets and vendor is None:
                    _reconcile(w, aid, before, after, user)
    except psycopg.errors.UniqueViolation:
        raise q.ApiError(409, "That concept is already assigned to this vendor for that week.") from None
    except psycopg.errors.UndefinedColumn as e:
        raise _missing_column(e) from None
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None


def update_assignment(conn, aid: int, body: dict, user: str | None = None) -> dict:
    f = clean_patch(body)
    if not f:
        raise q.ApiError(400, "nothing to update")
    if "format_code" in f:
        f["format_code"] = check_format(f["format_code"], formats(conn))
    _update(aid, f, user)
    return _after_write([aid])[0]


def move_assignment(conn, aid: int, body: dict, user: str | None = None) -> dict:
    """Drag and drop: one atomic status change plus reposition. Body {status, before_id?, after_id?, comment?}: before_id = the card the moved card is put immediately above (it takes the place just before that
    card), after_id = the card it is put immediately below; both must be open cards of the target column; neither = the bottom of the column. The same rules as PATCH status (check_transition).
    -> {card, column: {status, ids}} where ids is the target column in display order."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    unknown = set(body) - {"status", "before_id", "after_id", "comment"}
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    new = clean_status(body.get("status"))
    comment = _comment_opt(body.get("comment"))
    ids = {}
    for k in ("before_id", "after_id"):
        v = body.get(k)
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int):
                raise q.ApiError(400, f"{k.replace('_', ' ')} must be a card number")
            ids[k] = v
    if len(ids) == 2 and ids["before_id"] == ids["after_id"]:
        raise q.ApiError(400, "before_id and after_id must be different cards")
    if aid in ids.values():
        raise q.ApiError(400, "A card cannot be placed next to itself.")
    try:
        with _rw() as w:
            before = _snapshot(w, aid, "id = %s", [aid])
            if before is None:
                raise q.ApiError(404, "Assignment not found.")
            if before["dropped_at"]:
                raise q.ApiError(400, "This concept was dropped. Restore it before moving it.")
            was = effective_status(before, _tracker_statuses(w, before))
            note = check_transition(was, new, vendor=False, creative_url=before["creative_url"], comment=comment)
            pos = _position_between(w, new, aid, ids.get("before_id"), ids.get("after_id"))
            body_text = ("Changes requested: " + comment) if comment and was == "in_review" and new == "in_production" else comment
            if new != was:
                set_status(w, aid, new, user, was=was, position=pos, note=note, comment=body_text)
            else:
                w.execute("UPDATE creative_priorities_assignments SET position = %s, updated_at = now(), updated_by = %s WHERE id = %s", (pos, user, aid))
                if new != before["status"]:                       # the tracker showed it further along than the stored status: store it now
                    set_status(w, aid, new, user, was=new, position=pos, event=False)
            if body_text:
                _add_comment(w, aid, "app", _author(user), body_text)
            column = _column_ids(w, new)
    except psycopg.errors.UndefinedColumn as e:
        raise _missing_column(e) from None
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    return {"card": _after_write([aid], history=False)[0], "column": {"status": new, "ids": column}}


def _position_between(w, status: str, aid: int, before_id: int | None, after_id: int | None) -> float:
    """The position that puts a card in `status` just above before_id and/or just below after_id (neighbours must be open cards of that column); the bottom when neither is given. Renumbers the
    column once when two positions get too close to split."""
    def pos_of(cid):
        r = w.execute("SELECT position FROM creative_priorities_assignments WHERE id = %s AND status = %s AND dropped_at IS NULL", (cid, status)).fetchone()
        if r is None:
            raise q.ApiError(400, "That card is not in this column any more. Refresh the board.")
        return r["position"]
    for attempt in (0, 1):
        if before_id is None and after_id is None:
            return w.execute("SELECT COALESCE(max(position), 0) + 1 AS p FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL AND id <> %s", (status, aid)).fetchone()["p"]
        if before_id is not None and after_id is not None:
            lo, hi = pos_of(after_id), pos_of(before_id)
        elif before_id is not None:
            hi = pos_of(before_id)
            r = w.execute("SELECT max(position) AS p FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL AND id <> %s AND position < %s", (status, aid, hi)).fetchone()
            lo = r["p"] if r["p"] is not None else hi - 2
        else:
            lo = pos_of(after_id)
            r = w.execute("SELECT min(position) AS p FROM creative_priorities_assignments WHERE status = %s AND dropped_at IS NULL AND id <> %s AND position > %s", (status, aid, lo)).fetchone()
            hi = r["p"] if r["p"] is not None else lo + 2
        if lo > hi:
            lo, hi = hi, lo
        mid = (lo + hi) / 2
        if lo < mid < hi:
            return mid
        if attempt == 0:                                               # the gap is exhausted: renumber this column 1..n in its current order, then try again
            for n, cid in enumerate(_column_ids(w, status), start=1):
                w.execute("UPDATE creative_priorities_assignments SET position = %s WHERE id = %s", (float(n), cid))
    raise q.ApiError(400, "Could not place the card there. Refresh the board.")


# ---------------------------------------------------------------------------------------------------------------- comments
def list_transcripts(conn, aid: int) -> dict:
    """{transcripts: [{concept_id, label, transcript, language, duration_s, engine, model, created_at}]} for the ads of one card (its tracker_concept_ids), Original first then Hook 1, 2, 3. Internal only; empty
    when migration 249 is not applied or nothing was transcribed yet."""
    rec = conn.execute("SELECT tracker_concept_ids FROM creative_priorities_assignments WHERE id = %s", (aid,)).fetchone()
    if rec is None:
        raise q.ApiError(404, "Assignment not found.")
    ids = list(rec["tracker_concept_ids"] or [])
    try:
        rows = conn.execute("SELECT concept_id, label, transcript, language, duration_s, engine, model, created_at FROM creative_ad_transcripts WHERE concept_id = ANY(%s) OR assignment_id = %s", (ids, aid)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {"transcripts": []}
    def order(r):
        m = re.search(r"(\d+)$", r["label"] or "")
        return (0 if (r["label"] or "") == "Original" else 1, int(m.group(1)) if m else 0, r["concept_id"])
    out = [{"concept_id": r["concept_id"], "label": r["label"], "transcript": r["transcript"], "language": r["language"], "duration_s": _num(r["duration_s"]), "engine": r["engine"], "model": r["model"], "created_at": _iso(r["created_at"])} for r in sorted(rows, key=order)]
    return {"transcripts": out}


def list_comments(conn, aid: int, vendor: str | None = None) -> dict:
    """{comments: [{id, author_kind, author_name, body, created_at}]} oldest first. vendor= limits to that vendor's own, not dropped assignment (404 otherwise)."""
    where, params = "WHERE id = %s", [aid]
    if vendor is not None:
        where += " AND vendor = %s AND dropped_at IS NULL"
        params.append(vendor)
    try:
        if conn.execute(f"SELECT 1 FROM creative_priorities_assignments {where}", params).fetchone() is None:
            raise q.ApiError(404, "Assignment not found.")
        rows = conn.execute("SELECT id, author_kind, author_name, body, created_at FROM creative_priorities_assignment_comments WHERE assignment_id = %s ORDER BY created_at, id", (aid,)).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {"comments": []}
    return {"comments": [_comment_json(r) for r in rows]}


def post_comment(conn, aid: int, body: dict, user: str | None = None, vendor: str | None = None) -> dict:
    """Post a comment ({body}). App users and API keys write author_kind 'app' (name = the signed-in user, or Team), a vendor link page writes 'vendor' (name = the vendor). A vendor can only comment on their
    own open assignment. Also lists in the history. -> {comment}"""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    unknown = set(body) - {"body"}
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    text = clean_comment(body.get("body"))
    try:
        with _rw() as w:
            r = w.execute("SELECT vendor, dropped_at FROM creative_priorities_assignments WHERE id = %s", (aid,)).fetchone()
            if r is None or (vendor is not None and r["vendor"] != vendor):
                raise q.ApiError(404, "Assignment not found.")
            if vendor is not None and r["dropped_at"]:
                raise q.ApiError(409, "This concept was dropped.")
            c = _add_comment(w, aid, "vendor" if vendor is not None else "app", vendor or _author(user), text)
            _log(w, aid, f"vendor:{vendor}" if vendor is not None else user, "comment_added", None, None, _excerpt(text))
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Comments are not set up yet (migration 245 is not applied).") from None
    return {"comment": c}


# ---------------------------------------------------------------------------------------------------------------- vendor capacity, board
DEFAULT_CAPACITY = 10


def get_capacity(conn) -> dict:
    """{capacity: [{vendor, per_week, updated_at}]} for every vendor (10 where nothing is stored)."""
    try:
        have = {r["vendor"]: r for r in conn.execute("SELECT vendor, per_week, updated_at FROM creative_priorities_vendor_capacity").fetchall()}
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        have = {}
    return {"capacity": [{"vendor": v, "per_week": have[v]["per_week"] if v in have else DEFAULT_CAPACITY, "updated_at": _iso(have[v]["updated_at"]) if v in have else None} for v in VENDORS]}


def set_capacity(body: dict, user: str | None = None) -> dict:
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    unknown = set(body) - {"vendor", "per_week"}
    if unknown:
        raise q.ApiError(400, "unknown field: " + ", ".join(sorted(unknown)))
    v = _vendor(body.get("vendor"))
    n = body.get("per_week")
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 1000:
        raise q.ApiError(400, "per_week must be a whole number from 0 to 1000")
    try:
        with _rw() as w:
            r = w.execute("""INSERT INTO creative_priorities_vendor_capacity (vendor, per_week) VALUES (%s, %s)
                             ON CONFLICT (vendor) DO UPDATE SET per_week = EXCLUDED.per_week, updated_at = now() RETURNING vendor, per_week, updated_at""", (v, n)).fetchone()
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Vendor capacity is not set up yet (migration 245 is not applied).") from None
    return {"vendor": r["vendor"], "per_week": r["per_week"], "updated_at": _iso(r["updated_at"])}


BOARD_LAUNCHED_DAYS = 14                     # the Launched column shows the last two weeks unless closed=1


def _launched_on(a: dict) -> dt.date | None:
    for k in ("launched_at", "status_changed_at"):
        if a.get(k):
            return dt.date.fromisoformat(a[k][:10])
    return None


def board(conn, week=None, vendor=None, product=None, closed: bool = False) -> dict:
    """The Production board: {columns: [{status, label, count, cards}], dropped (only with closed), capacity: [{vendor, per_week, open, week}], week, generated_at}. Filters: week (snaps to Monday), vendor, product; each may list several, comma separated.
    Cards are the assignment JSON without history, ordered high priority first, then position. The Launched column holds the last 14 days unless closed (then all, plus a dropped lane). Capacity counts a vendor's
    open (not launched, not dropped) cards for the filtered week, or the current week when none or several are given; it ignores the product filter."""
    where, params = [], []
    weeks_, vendors_, products_ = _multi(week, monday_of), _multi(vendor, _vendor), _multi(product, lambda p: p[:40])
    if weeks_:
        where.append("for_week = ANY(%s)")
        params.append(weeks_)
    if vendors_:
        where.append("vendor = ANY(%s)")
        params.append(vendors_)
    if products_:
        where.append("product = ANY(%s)")
        params.append(products_)
    recs = _fetch(conn, ("WHERE " + " AND ".join(where)) if where else "", tuple(params))
    cards = _shape_all(conn, recs, history=False)
    cap_week = weeks_[0] if len(weeks_) == 1 else monday_of(today_pt())
    cards.sort(key=lambda a: (a["priority"] != "high", a["position"], a["id"]))
    today = today_pt()
    cols = []
    for st in STATUSES:
        mine = [a for a in cards if a["stage"] != "dropped" and a["status"] == st]
        hidden = 0
        if st == "launched" and not closed:
            keep = [a for a in mine if (_launched_on(a) or today) >= today - dt.timedelta(days=BOARD_LAUNCHED_DAYS)]
            hidden, mine = len(mine) - len(keep), keep
        c = {"status": st, "label": STATUS_LABELS[st], "count": len(mine), "cards": mine}
        if st == "launched":
            c["hidden"] = hidden
        cols.append(c)
    out = {"columns": cols, "week": cap_week.isoformat(), "vendors": list(VENDORS), "weeks": _weeks(conn), "formats": formats(conn)}       # so the page does not need a second request for them
    if closed:
        out["dropped"] = [a for a in cards if a["stage"] == "dropped"]
    capv = {c["vendor"]: c["per_week"] for c in get_capacity(conn)["capacity"]}
    opened: dict[str, int] = {}
    try:
        for r in conn.execute("SELECT vendor, count(*) AS n FROM creative_priorities_assignments WHERE for_week = %s AND dropped_at IS NULL AND launched_at IS NULL AND status <> 'launched' GROUP BY 1", (cap_week,)).fetchall():
            opened[r["vendor"]] = r["n"]
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
    out["capacity"] = [{"vendor": v, "per_week": capv.get(v, DEFAULT_CAPACITY), "open": opened.get(v, 0), "week": cap_week.isoformat()} for v in VENDORS if not vendors_ or v in vendors_]
    out["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return out


# ---------------------------------------------------------------------------------------------------------------- vendor links
def _link(r: dict) -> dict:
    return {"vendor": r["vendor"], "token": r["token"], "url": f"/priorities/vendor/{r['token']}", "created_at": _iso(r["created_at"]), "rotated_at": _iso(r["rotated_at"])}


def list_links(conn) -> dict:
    try:
        rows = conn.execute("SELECT vendor, token, created_at, rotated_at FROM creative_priorities_vendor_links").fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        rows = []
    have = {r["vendor"]: _link(r) for r in rows}
    return {"links": [have[v] for v in VENDORS if v in have], "vendors": list(VENDORS)}


def get_or_create_link(vendor) -> tuple[dict, bool]:
    """(link, created): the vendor's permanent link, made on first use."""
    v = _vendor(vendor)
    try:
        with _rw() as w:
            r = w.execute("INSERT INTO creative_priorities_vendor_links (vendor, token) VALUES (%s, %s) ON CONFLICT (vendor) DO NOTHING RETURNING vendor, token, created_at, rotated_at",
                          (v, secrets.token_urlsafe(24))).fetchone()
            created = r is not None
            if r is None:
                r = w.execute("SELECT vendor, token, created_at, rotated_at FROM creative_priorities_vendor_links WHERE vendor = %s", (v,)).fetchone()
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    return _link(r), created


def reset_link(vendor) -> dict:
    """Replace the vendor's token (the old link stops working); made if it did not exist."""
    v = _vendor(vendor)
    try:
        with _rw() as w:
            r = w.execute("""INSERT INTO creative_priorities_vendor_links (vendor, token) VALUES (%s, %s)
                             ON CONFLICT (vendor) DO UPDATE SET token = EXCLUDED.token, rotated_at = now() RETURNING vendor, token, created_at, rotated_at""", (v, secrets.token_urlsafe(24))).fetchone()
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    return _link(r)


def vendor_of_token(conn, token: str) -> str:
    if not TOKEN_RE.fullmatch(token or ""):
        raise q.ApiError(404, "Link not found.")
    try:
        r = conn.execute("SELECT vendor FROM creative_priorities_vendor_links WHERE token = %s", (token,)).fetchone()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        r = None
    if r is None:
        raise q.ApiError(404, "Link not found.")
    return r["vendor"]


def _launch_day(a: dict) -> dt.date | None:
    days = []
    for ad in a["ads"]:
        if ad.get("launch_date"):
            days.append(dt.date.fromisoformat(ad["launch_date"][:10]))
    if days:
        return min(days)
    if a.get("launched_at"):
        return dt.date.fromisoformat(a["launched_at"][:10])
    return None


def vendor_rows(conn, token: str, today: dt.date | None = None) -> dict:
    """What the vendor link page shows: only that vendor's assignments that are open, plus those launched in the last four weeks. Spend and return of their own ads are included; the internal note,
    the author columns, suggested Concept IDs and ad page links are not. -> {vendor, rows, weeks}"""
    vendor = vendor_of_token(conn, token)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    out = []
    for a in _shape_all(conn, _fetch(conn, "WHERE vendor = %s", (vendor,)), vendor_view=True):
        if a["stage"] == "dropped":
            continue
        if a["stage"] == "launched":
            day = _launch_day(a) or dt.date.fromisoformat(a["for_week"])
            if (today - day).days > VENDOR_RECENT_DAYS:
                continue
        out.append(a)
    return {"vendor": vendor, "rows": out, "weeks": sorted({a["for_week"] for a in out}, reverse=True)}


def vendor_update(conn, token: str, aid: int, body: dict) -> dict:
    """A vendor edits their own row: creative link, note, comment, and the three status moves (Start, Submit for review, Pull back; Submit needs a creative link). The old Ready to launch flag still works: true =
    submit for review, false = pull back. Another vendor's row, or a row that does not exist, is a 404."""
    vendor = vendor_of_token(conn, token)
    f = clean_vendor_patch(body)
    if not f:
        raise q.ApiError(400, "nothing to update")
    recs = _fetch(conn, "WHERE id = %s AND vendor = %s", (aid, vendor))
    if not recs:
        raise q.ApiError(404, "Assignment not found.")
    cur = recs[0]
    if cur["dropped_at"]:
        raise q.ApiError(409, "This concept was dropped.")
    legacy = "ready" in f
    if legacy:
        ready = f.pop("ready")
        if "status" in f:
            raise q.ApiError(400, "Send either ready or status, not both.")
        have = effective_status(cur, [a["status"] for a in _tracker_ads(conn, [cur])[aid]])
        creative = f["creative_url"] if "creative_url" in f else cur["creative_url"]
        if ready and not creative:
            raise q.ApiError(400, "Add the creative link before marking it ready to launch.")
        if ready and have in ("assigned", "brief_sent", "in_production"):
            f["status"] = "in_review"
        elif not ready and have == "in_review":
            f["status"] = "in_production"
    if f.get("status") == "in_review" and not (f["creative_url"] if "creative_url" in f else cur["creative_url"]):
        raise q.ApiError(400, "Add the creative link before sending it for review.")
    if not f:
        return _after_write([aid], vendor_view=True)[0]
    _update(aid, f, f"vendor:{vendor}", vendor=vendor, legacy_ready=legacy)
    return _after_write([aid], vendor_view=True)[0]
