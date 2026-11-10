"""Production assignments on /priorities: a concept given to a vendor to make for a week. Tables creative_priorities_assignments and creative_priorities_vendor_links (migration 202).

A concept stays in the ideation list exactly as it is; each "Assign to vendor" creates one assignment (concept x vendor x week). The stage of an assignment is never stored: it is derived from the
links, the timestamps and the status of the linked Creative Tracker rows (stage_of). Nothing here writes to the Google Sheet or Meta. This part of the module is pure (no database): key building,
validation, stage and the "In production" text; the UI mock server reuses it over an in-memory store.
"""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlsplit

from . import queries as q

VENDORS = ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS", "Internal"]     # spelled as in tracker_rows.vendor (Creative Tracker "Vendor" column)
STAGES = {"assigned": "Assigned", "brief_sent": "Brief sent", "creative_in": "Creative delivered", "ready": "Ready to launch", "launched": "Launched", "dropped": "Dropped"}
_ORDER = ["assigned", "brief_sent", "creative_in", "ready", "launched"]                  # progress order; dropped sits outside it
LIMITS = {"note": 500, "vendor_note": 1000, "url": 2000, "concept_id": 200, "concept_ids": 20}
CREATE_FIELDS = ("vendor", "for_week", "note")
PATCH_FIELDS = ("vendor", "for_week", "note", "brief_url", "creative_url", "tracker_concept_ids", "ready", "launched", "dropped")
PATCH_BOOLS = ("ready", "launched", "dropped")
VENDOR_PATCH_FIELDS = ("creative_url", "vendor_note")
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
    """Vendor, week (snapped to its Monday) and note of an Assign request. Other keys of the body (keys, snapshot_week) belong to the route and are ignored here."""
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    if body.get("for_week") in (None, ""):
        raise q.ApiError(400, "week is required")
    return {"vendor": _vendor(body.get("vendor")), "for_week": monday_of(body["for_week"]), "note": _text(body.get("note"), "note", LIMITS["note"])}


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
    return out


def clean_vendor_patch(body: dict) -> dict:
    """What a vendor may change on their own link page: the creative link and a note. Nothing else."""
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
