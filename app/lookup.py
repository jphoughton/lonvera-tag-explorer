"""Tag lookup: paste an ad's name as the ad platform shows it (or a Meta ad id, a tracker Concept ID, an Ad Family) and get the new tags of that ad.

The matching logic here is pure Python over a `store` (the database in production: DbStore; made-up lists in the UI tests' mock server), so the
very same code answers both. Nothing here writes, and nothing guesses a tag: a tag is only ever shown for a tracker row that the pasted text
reaches through a named rule (match_kind below), and a platform ad with no tracker row gets 'no match' plus the closest names, never a tag.

match_kind:
  id          the pasted digits are a Meta ad id; its name leads to the tracker row(s) through the ETL's Meta family map
  exact       the pasted text is a platform ad name character for character
  normalized  same name after case / spacing / ' - Copy N' / merge-tag differences (the family key the ETL uses)
  family      the pasted text is a tracker Ad Family, or a platform name that is a tracker name plus an 'S-' prefix and/or a '_VID_...' / '_IMG_...' encoding
  concept     the pasted text is a tracker Concept ID (or a tracker 'Name' column value)
  none        nothing reached a tracker row; `platform` holds the platform ad when it exists
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from . import queries as q
from .config import ads_manager_url

QUERY_TIMEOUT_MS = 8000                 # statement_timeout for this endpoint (the app default is 20 s, the gateway's limit)
MAX_QUERY_CHARS = 300
MAX_MATCHES = 10
MAX_PLATFORM_ADS = 5

MERGE_SUFFIX = re.compile(r"\s*\|\s*[A-Za-z0-9]+-merge-\d{4}-\d{2}-\d{2}\s*$", re.I)
COPY_SUFFIX = re.compile(r"(\s*-\s*Copy(\s*\d+)?)+\s*$", re.I)
ENCODING = re.compile(r"_(vid|img)_.*$", re.I)
AD_ID = re.compile(r"\d{8,25}")
AD_ID_IN_URL = re.compile(r"selected_ad_ids=(\d{8,25})")
QUOTES = "\"'`“”‘’«»"


class Store(Protocol):
    def ads_by_id(self, ad_id: str) -> list[dict]: ...
    def ads_by_name(self, name: str) -> list[dict]: ...
    def ads_by_family_keys(self, keys: list[str]) -> list[dict]: ...
    def tracker_family_of(self, meta_family_keys: list[str]) -> dict[str, str]: ...
    def trackers_by_family(self, family_keys: list[str]) -> list[dict]: ...
    def trackers_by_concept(self, keys: list[str]) -> list[dict]: ...
    def details(self, rows: list[dict]) -> dict: ...


# --------------------------------------------------------------------------------------------------------------------
# the pasted text
# --------------------------------------------------------------------------------------------------------------------


def family_of(name: str) -> str:
    """Platform ad name -> ad family key, the way the ETL derives meta_family_key: lower case, ' - Copy N' and a merge tag stripped (any order)."""
    s = str(name).strip()
    while True:
        t = COPY_SUFFIX.sub("", MERGE_SUFFIX.sub("", s)).strip()
        if t == s:
            return t.lower()
        s = t


def parse_query(raw: Any) -> dict:
    """Trim, drop zero-width characters, strip surrounding quotes, collapse whitespace; detect a pasted Ads Manager ad id (or an Ads Manager link
    that carries one). Raises q.ApiError(400) for an empty or over-long text."""
    s = str(raw if raw is not None else "")
    s = re.sub(r"[​‌‍⁠﻿]", "", s).replace(" ", " ")
    s = s.strip()
    while len(s) >= 2 and s[0] in QUOTES and s[-1] in QUOTES:
        s = s[1:-1].strip()
    s = s.strip(QUOTES + " ") if s[:1] in QUOTES or s[-1:] in QUOTES else s
    s = re.sub(r"\s+", " ", s)
    if not s:
        raise q.ApiError(400, "Type or paste an ad name, a Meta ad id or a Concept ID.")
    if len(s) > MAX_QUERY_CHARS:
        raise q.ApiError(400, f"That is longer than {MAX_QUERY_CHARS} characters. Paste just the ad name.")
    m = AD_ID_IN_URL.search(s)
    ad_id = m.group(1) if m else (s if AD_ID.fullmatch(s) else None)
    key = family_of(s)
    spaced = re.sub(r"\s+", " ", key)
    base = ENCODING.sub("", key).strip()
    stripped = re.sub(r"^s-", "", base)
    return {"text": s, "ad_id": ad_id, "key": key, "keys": [x for x in dict.fromkeys([key, spaced]) if x],
            "derived": [x for x in dict.fromkeys([base, stripped]) if x and x not in (key, spaced)]}


def _platform_block(ads: list[dict]) -> dict | None:
    if not ads:
        return None
    ads = sorted(ads, key=lambda a: (str(a.get("last_day") or ""), str(a["ad_id"])), reverse=True)
    names = list(dict.fromkeys(a["ad_name"] for a in ads if a.get("ad_name")))
    return {
        "ad_count": len(ads), "names": names[:MAX_PLATFORM_ADS],
        "ads": [{"ad_id": a["ad_id"], "name": a.get("ad_name"), "status": a.get("status"), "adset_name": a.get("adset_name"), "account": a.get("account"),
                 "last_day": str(a["last_day"]) if a.get("last_day") else None, "ads_manager_url": ads_manager_url(a.get("account"), a["ad_id"])}
                for a in ads[:MAX_PLATFORM_ADS]],
    }


def _new_tags(det: dict, row: dict) -> tuple[dict | None, str | None]:
    t = det["tags"].get(row["id"])
    if not t:
        return None, "not tagged yet"
    method = (t.get("method") or "")
    ing = det["ingredients"].get(t.get("line_id"), [])
    block = {
        "version": t["version"], "line_code": t.get("line_code"), "line_name": " · ".join(x for x in (t.get("icp"), t.get("pain_point")) if x) or None,
        "cluster": t.get("cluster"), "icp": t.get("icp"), "pain_point": t.get("pain_point"), "emotional_trigger": t.get("emotional_trigger"),
        "ingredients": [{"ingredient": i["ingredient"], "attacks": i["attacks"], "supports": i["supports"]} for i in ing],
        "confidence": t.get("confidence"), "method": method or None, "seen": t.get("seen"), "why": t.get("why"), "runner_up": t.get("runner_up"),
        "text_only": method.lower().startswith(("text only", "no picture")),
        "caveat": ("Read from the ad's copy only (no picture was available), so treat the line as a first pass." if method.lower().startswith(("text only", "no picture")) else None),
        "story": t.get("story"),
    }
    return block, None


def _match(row: dict, det: dict) -> dict:
    new, reason = _new_tags(det, row)
    return {
        "id": row["id"], "concept_id": row["concept_id"], "ad_family": row["ad_family"] or None, "family_key": row["family_key"],
        "product": {"slug": row["slug"], "name": row["product_name"]}, "status": row.get("status"),
        "new_tags": new, "new_tags_reason": reason,
        "links": {"ad_page": f"/ad/{row['id']}"},
    }


def lookup(store: Store, raw_q: Any) -> dict:
    """The /api/tag-lookup payload."""
    qp = parse_query(raw_q)
    kind: str | None = None
    platform_ads: list[dict] = []
    rows: list[dict] = []

    def via_ads(ads: list[dict]) -> list[dict]:
        fam = store.tracker_family_of(sorted({a["meta_family_key"] for a in ads if a.get("meta_family_key")}))
        fks = sorted({v for v in fam.values() if v})
        return store.trackers_by_family(fks) if fks else []

    # 1. a Meta ad id
    if qp["ad_id"]:
        ads = store.ads_by_id(qp["ad_id"])
        if ads:
            platform_ads = ads
            rows = via_ads(ads)
            kind = "id" if rows else None
    # 2. a platform ad name: as typed, then as the family key the ETL uses
    if not rows:
        ads = store.ads_by_name(qp["text"])
        k2 = "exact"
        if not ads:
            ads = store.ads_by_family_keys(qp["keys"])
            k2 = "normalized"
        if ads:
            platform_ads = platform_ads or ads
            rows = via_ads(ads)
            kind = k2 if rows else kind
    # 3. a tracker Concept ID / Name, then the tracker's own Ad Family
    if not rows:
        rows = store.trackers_by_concept(qp["keys"])
        kind = "concept" if rows else kind
    if not rows:
        fams = store.trackers_by_family(qp["keys"])
        if fams:
            rows, kind = fams, "family"
    # 4. the platform name is the tracker name with a prefix / encoding added (S-Trybe-...-13_VID_...)
    derived_used = False
    if not rows and qp["derived"]:
        rows = store.trackers_by_concept(qp["derived"]) or store.trackers_by_family(qp["derived"])
        kind = "family" if rows else kind
        derived_used = bool(rows)

    out: dict[str, Any] = {"query": qp["text"], "match_kind": kind or "none", "platform": _platform_block(platform_ads), "matches": [], "total_matches": len(rows), "truncated": False,
                           "notes": [], "reason": None}
    if not rows:
        out["match_kind"] = "none"
        out["reason"] = ("This ad exists on the platform but no tracker row is tied to it, so it has no new tags. Nothing is guessed."
                         if platform_ads else "No platform ad, tracker Concept ID or Ad Family has that text. Nothing is guessed.")
        return out

    rows = sorted(rows, key=lambda r: (r["product_name"] or "", r.get("sheet_row") or 10**9, r["id"]))
    out["truncated"] = len(rows) > MAX_MATCHES
    shown = rows[:MAX_MATCHES]
    det = store.details(shown)
    out["matches"] = [_match(r, det) for r in shown]
    if len(rows) > 1:
        codes = {m["new_tags"]["line_code"] for m in out["matches"] if m["new_tags"] and m["new_tags"]["line_code"]}
        prods = {m["product"]["slug"] for m in out["matches"]}
        out["notes"].append(f"{len(rows)} tracker rows answer to this name (copies, iterations or the same ad family)." + (" Their new tags differ, so all are shown." if len(codes) > 1 else ""))
        if len(prods) > 1:
            out["notes"].append("The name is shared across more than one product's tracker rows.")
    if derived_used:
        out["notes"].append("The platform name carries a prefix or an encoded suffix; it was matched to the tracker by the name underneath.")
    return out


# --------------------------------------------------------------------------------------------------------------------
# the database store
# --------------------------------------------------------------------------------------------------------------------

_TRACKER_COLS = """t.id, t.product_id, t.concept_id, t.ad_family, t.family_key, t.sheet_row, t.status, p.slug, p.name AS product_name"""
_AD_COLS = "ma.ad_id, ma.account, ma.ad_name, ma.meta_family_key, ma.adset_name, ma.status, ma.last_day"


class DbStore:
    def __init__(self, conn):
        self.conn = conn

    def _all(self, sql: str, params: Any = None) -> list[dict]:
        return q.rows_of(self.conn, sql, params)

    def ads_by_id(self, ad_id):
        return self._all(f"SELECT {_AD_COLS} FROM meta_ads ma WHERE ma.ad_id = %s", (ad_id,))

    def ads_by_name(self, name):
        return self._all(f"SELECT {_AD_COLS} FROM meta_ads ma WHERE ma.ad_name = %s ORDER BY ma.last_day DESC NULLS LAST LIMIT 200", (name,))

    def ads_by_family_keys(self, keys):
        return self._all(f"SELECT {_AD_COLS} FROM meta_ads ma WHERE ma.meta_family_key = ANY(%s) ORDER BY ma.last_day DESC NULLS LAST LIMIT 200", (list(keys),))

    def tracker_family_of(self, meta_family_keys):
        if not meta_family_keys:
            return {}
        return {r["meta_family_key"]: r["tracker_family_key"] for r in self._all(
            "SELECT meta_family_key, tracker_family_key FROM meta_family_map WHERE meta_family_key = ANY(%s) AND tracker_family_key IS NOT NULL", (list(meta_family_keys),))}

    def _trackers(self, where: str, params: Any) -> list[dict]:
        return self._all(f"SELECT {_TRACKER_COLS} FROM tracker_rows t JOIN products p ON p.id = t.product_id WHERE {where}" + q.alive("t") + " ORDER BY t.sheet_row NULLS LAST, t.id LIMIT 60", params)

    def trackers_by_family(self, family_keys):
        return self._trackers("t.family_key = ANY(%s)", (list(family_keys),)) if family_keys else []

    def trackers_by_concept(self, keys):
        return self._trackers("(lower(t.concept_id) = ANY(%s) OR lower(btrim(t.meta_ad_name)) = ANY(%s))", (list(keys), list(keys))) if keys else []

    def details(self, rows):
        ids = [r["id"] for r in rows]
        tags: dict[int, dict] = {}
        ing: dict[int, list] = {}
        for t in self._all("""
                SELECT g.tracker_row_id, g.version, g.line_id, g.confidence, g.method, g.seen, g.why, g.runner_up, tl.line_code, tl.story,
                       COALESCE(g.cluster, tl.cluster) AS cluster, COALESCE(g.icp, tl.icp) AS icp, COALESCE(g.pain_point, tl.pain_point) AS pain_point,
                       COALESCE(g.emotional_trigger, tl.emotional_trigger) AS emotional_trigger
                FROM ad_tags g LEFT JOIN taxonomy_lines tl ON tl.id = g.line_id
                WHERE g.tracker_row_id = ANY(%s) AND g.is_current AND g.tag_set = 'linemap'""", (ids,)):
            tags[t["tracker_row_id"]] = t
        line_ids = sorted({t["line_id"] for t in tags.values() if t["line_id"]})
        if line_ids:
            for i in self._all("SELECT line_id, ingredient, attacks, supports FROM line_ingredients WHERE line_id = ANY(%s) ORDER BY line_id, sort_order, id", (line_ids,)):
                ing.setdefault(i["line_id"], []).append(i)
        return {"tags": tags, "ingredients": ing}


def lookup_db(conn, args) -> dict:
    """The /api/tag-lookup payload from the database (read-only, 8 s statement timeout)."""
    conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")          # a bad query answers a clear JSON error (503 'query timed out'), not a gateway hang
    try:
        q.load_meta(conn)                                                       # also learns which optional columns exist (tracker_rows.deleted_at)
    except q.ApiError:
        pass
    return lookup(DbStore(conn), args.get("q"))
