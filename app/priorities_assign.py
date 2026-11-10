"""Production assignments on /priorities: a concept given to a vendor to make for a week. Tables creative_priorities_assignments and creative_priorities_vendor_links (migration 202).

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

VENDORS = ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS", "Internal", "PuredMedia", "CreateMastery", "OpenProgram"]     # spelled as in tracker_rows.vendor (Creative Tracker "Vendor" column)
STAGES = {"assigned": "Assigned", "brief_sent": "Brief sent", "creative_in": "Creative delivered", "ready": "Ready to launch", "launched": "Launched", "dropped": "Dropped"}
_ORDER = ["assigned", "brief_sent", "creative_in", "ready", "launched"]                  # progress order; dropped sits outside it
LIMITS = {"note": 500, "vendor_note": 1000, "url": 2000, "concept_id": 200, "concept_ids": 20}
CREATE_FIELDS = ("vendor", "for_week", "note")
PATCH_FIELDS = ("vendor", "for_week", "note", "brief_url", "creative_url", "tracker_concept_ids", "ready", "launched", "dropped")
PATCH_BOOLS = ("ready", "launched", "dropped")
VENDOR_PATCH_FIELDS = ("creative_url", "vendor_note", "ready")
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
    """What a vendor may change on their own link page: the creative link, a note, and Ready to launch (a real boolean: true stamps ready_at, false clears it). Nothing else."""
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


# ================================================================================================================ storage
MAX_KEYS = 100
VENDOR_RECENT_DAYS = 28                      # the vendor page also shows what was launched in the last four weeks
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,64}")
COLS = ("id, concept_key, text_key, product, vendor, line_id, manual_id, line_code, cluster, icp, pain_point, story, section_at_assign, note, vendor_note, for_week, brief_url, creative_url, "
        "brief_at, creative_at, ready_at, launched_at, dropped_at, tracker_concept_ids, created_at, updated_at, created_by, updated_by")
INTERNAL_ONLY = ("note", "created_by", "updated_by", "suggested_concept_ids")          # never sent to a vendor link page
_TS = ("brief_at", "creative_at", "ready_at", "launched_at", "dropped_at", "created_at", "updated_at")


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


def _shape(rec: dict, ads: list[dict], vendor_view: bool = False) -> dict:
    statuses = [a["status"] for a in ads]
    stage = stage_of(rec, statuses)
    best = max(statuses, key=_tracker_rank, default=None) if statuses else None
    d = {k: (_iso(rec[k]) if k in _TS or k == "for_week" else rec[k]) for k in rec}
    if d.get("section_at_assign"):
        from etl import creative_priorities as cp
        d["section_at_assign"] = cp.norm_section(d["section_at_assign"])        # assignments made before 2026-11-10 stored the old section name; shown as the new Action
    d["tracker_concept_ids"] = list(rec.get("tracker_concept_ids") or [])
    d.update(stage=stage, stage_label=stage_label(stage), tracker_status=best)
    d["suggested_concept_ids"] = [a["concept_id"] for a in ads if not a["explicit"]]
    d["ads"] = [{k: v for k, v in a.items() if k != "explicit"} for a in ads]
    if vendor_view:
        for k in INTERNAL_ONLY:
            d.pop(k, None)
        for a in d["ads"]:
            a.pop("ad_url", None)                      # the ad page needs a login
    return d


def _fetch(conn, where: str = "", params: tuple = ()) -> list[dict]:
    try:
        return conn.execute(f"SELECT {COLS} FROM creative_priorities_assignments {where} ORDER BY for_week DESC, vendor, id", params).fetchall()
    except psycopg.errors.UndefinedTable:         # migration 202 not applied yet: the pages still work, with nothing assigned
        conn.rollback()
        return []


def _weeks(conn) -> list[str]:
    try:
        return [_iso(r["for_week"]) for r in conn.execute("SELECT DISTINCT for_week FROM creative_priorities_assignments ORDER BY for_week DESC").fetchall()]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return []


def _shape_all(conn, recs: list[dict], vendor_view: bool = False) -> list[dict]:
    ads = _tracker_ads(conn, recs)
    return [_shape(r, ads[r["id"]], vendor_view) for r in recs]


def list_assignments(conn, week=None, vendor=None, include_closed: bool = False) -> dict:
    """{rows, vendors, weeks}. Default: open rows (not launched, not dropped); include_closed adds launched and dropped. week snaps to its Monday; vendor must be a known vendor."""
    where, params = [], []
    if week not in (None, ""):
        where.append("for_week = %s")
        params.append(monday_of(week))
    if vendor not in (None, ""):
        where.append("vendor = %s")
        params.append(_vendor(vendor))
    rows = _shape_all(conn, _fetch(conn, ("WHERE " + " AND ".join(where)) if where else "", tuple(params)))
    if not include_closed:
        rows = [r for r in rows if r["stage"] not in ("launched", "dropped")]
    return {"rows": rows, "vendors": list(VENDORS), "weeks": _weeks(conn)}


def _after_write(ids: list[int], vendor_view: bool = False) -> list[dict]:
    """The just-written rows, read on a fresh read-write connection (the request's read connection may hold an older snapshot that does not see the write yet)."""
    if not ids:
        return []
    with _rw() as w:
        recs = _fetch(w, "WHERE id = ANY(%s)", (ids,))
        shaped = _shape_all(w, recs, vendor_view)
    return sorted(shaped, key=lambda a: ids.index(a["id"]))


def create_assignments(conn, body: dict, user: str | None = None) -> dict:
    """Assign concepts (row keys of a snapshot) to a vendor for a week. The server resolves the rows from the stored snapshot, never from client text. Duplicates (same concept, vendor and week,
    not dropped) are skipped. -> {created: [assignment, ...], skipped: [{key, reason}]}"""
    from . import creative_priorities as cpr
    from etl.creative_priorities import norm_key
    f = clean_create(body)
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
                row = w.execute(
                    """INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, line_id, manual_id, line_code, cluster, icp, pain_point, story, section_at_assign, note, for_week, created_by, updated_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (concept_key, vendor, for_week) WHERE dropped_at IS NULL DO NOTHING RETURNING id""",
                    (concept_key(r), text_key(r), r["product"], f["vendor"], r.get("line_id"), r["id"] if r.get("manual") else None, r.get("line_code") or "", r.get("cluster") or "", r.get("icp") or "",
                     r.get("pain_point") or "", r.get("story") or "", r.get("section") or "", f["note"], f["for_week"], user, user)).fetchone()
                if row is None:
                    skipped.append({"key": k, "reason": "already assigned to this vendor for that week"})
                else:
                    created_ids.append(row["id"])
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    return {"created": _after_write(created_ids), "skipped": skipped}


def _update(aid: int, f: dict, user: str | None, vendor: str | None = None) -> None:
    """Apply a cleaned patch. Links stamp their first-added time and clear it with the link; ready / launched / dropped set or clear their timestamp. vendor= restricts to that vendor's row (404 otherwise)."""
    sets: list[str] = []
    params: list = []
    for c in ("vendor", "for_week", "note", "vendor_note"):
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
    if not sets:
        raise q.ApiError(400, "nothing to update")
    sets += ["updated_at = now()", "updated_by = %s"]
    params.append(user)
    where, wp = "id = %s", [aid]
    if vendor is not None:
        where += " AND vendor = %s"
        wp.append(vendor)
    try:
        with _rw() as w:
            n = w.execute(f"UPDATE creative_priorities_assignments SET {', '.join(sets)} WHERE {where}", [*params, *wp]).rowcount
    except psycopg.errors.UniqueViolation:
        raise q.ApiError(409, "That concept is already assigned to this vendor for that week.") from None
    except psycopg.errors.UndefinedTable:
        raise q.ApiError(503, "Production is not set up yet (migration 202 is not applied).") from None
    if not n:
        raise q.ApiError(404, "Assignment not found.")


def update_assignment(conn, aid: int, body: dict, user: str | None = None) -> dict:
    f = clean_patch(body)
    if not f:
        raise q.ApiError(400, "nothing to update")
    _update(aid, f, user)
    return _after_write([aid])[0]


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
    """A vendor edits their own row: creative link, note, Ready to launch (needs a creative link). Another vendor's row, or a row that does not exist, is a 404."""
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
    if f.get("ready") and not (f["creative_url"] if "creative_url" in f else cur["creative_url"]):
        raise q.ApiError(400, "Add the creative link before marking it ready to launch.")
    _update(aid, f, f"vendor:{vendor}", vendor=vendor)
    return _after_write([aid], vendor_view=True)[0]
