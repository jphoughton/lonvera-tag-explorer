"""Launching approved creative into Meta from /priorities production: the PURE part (no database, no network, no Flask).

Everything here is a plain function of its arguments: Concept ID minting, the ad name, the Creative Tracker row, file ordering, copy precedence, the launch plan and its hash, field
validation and the environment switches. The storage and worker parts live elsewhere and call these, so the plan a person approves is byte for byte the plan that runs.

AD NAME FORMAT (verified read-only against the Creative Tracker on 2026-11-10): the `Name` column (Z) is
    =SUBSTITUTE(E&"_"&L&"_"&M&"_"&N&"_"&O&"_"&P&"_"&Q&"_"&R&"_"&T&"_"&S&"_"&U, " ", "")
i.e. Concept ID _ Type (IMG|VID) _ Pain Point _ ICP _ Product _ Style _ Creative Fidelity _ Vendor _ New Concept / Iteration _ Proof Mechanism _ Handle, with ONLY spaces removed (every other
character, such as & ' / ( ) @ and hyphens, is kept as typed). `Ad Family` (column AE) is NOT that string: it is the Concept ID with a trailing " - Copy N" removed.
The last token is the tracker's `Handle` column (for example `lonvera`, `@ettercreations`); here it is the Page's name_token. """
from __future__ import annotations

import hashlib
import json
import os
import re

# ---------------------------------------------------------------------------------------------------------------- names
# Concept ID product word, spelled as in real Creative Tracker Concept IDs (Lonvera-VitalBull-Video-7.3, Lonvera-BeefLiver-Static-3.2, Victoria-BoneFlex-Static-12.2; tracker Concept IDs
# also contain Lonvera-Boneflex-Static-1.1, Lonvera-BoneFlex-Video-1.5; BoneFlex is the majority spelling).
PRODUCT_WORDS = {"vita-bull": "VitalBull", "beef-liver": "BeefLiver", "boneflex": "BoneFlex"}
# the tracker's `Product` column spelling (a controlled list: VitaBull / BeefLiver / Boneflex)
TRACKER_PRODUCTS = {"vita-bull": "VitaBull", "beef-liver": "BeefLiver", "boneflex": "Boneflex"}
ICP_HEADER = "ICP - Target Avatar (Who are we trying to talk to?) "          # trailing space is real
TRACKER_HEADERS = ["Brief Link", "Asset URL", "Concept ID", "Status", "Type", "Pain Point", ICP_HEADER, "Product", "Style", "Creative Fidelity", "Vendor", "Proof Mechanism",
                   "New Concept / Iteration", "Handle"]
PAUSED = "PAUSED"
MAX_COPY = 3
_NUM = re.compile(r"(\d+)")


def vendor_prefix(vendor, prefixes: dict | None = None) -> str:
    """Concept ID prefix for a vendor: the configured one, else the vendor name with non-alphanumerics stripped."""
    v = str(vendor or "").strip()
    p = (prefixes or {}).get(v)
    if p and str(p).strip():
        return str(p).strip()
    return re.sub(r"[^A-Za-z0-9]", "", v)


def media_kind_word(kind) -> str:
    return "Video" if kind == "video" else "Static"


def media_token(kind) -> str:
    return "VID" if kind == "video" else "IMG"


def mint_concept_ids(prefix, product, kind_by_file, assignment_id, taken=()) -> list[str]:
    """One file: <prefix>-<ProductWord>-<Static|Video>-A<id>. Several files: the same base with -v1..-vN in the given order (each file uses its own Static/Video word).
    On a clash with `taken` (case-insensitive) a letter is appended to the A<id> part (A12b, A12c, ...) until the whole set is free. Never returns duplicates."""
    word = PRODUCT_WORDS.get(product) or re.sub(r"[^A-Za-z0-9]", "", str(product).title())
    kinds = list(kind_by_file)
    used = {str(t).lower() for t in taken}
    letters = [""] + [chr(c) for c in range(ord("b"), ord("z") + 1)]
    for n in range(1, 10_000):
        letter = letters[n - 1] if n <= len(letters) else letters[-1] + str(n)
        names = []
        for i, k in enumerate(kinds, 1):
            base = f"{prefix}-{word}-{media_kind_word(k)}-A{assignment_id}{letter}"
            names.append(base if len(kinds) == 1 else f"{base}-v{i}")
        low = [x.lower() for x in names]
        if len(set(low)) == len(low) and not (set(low) & used):
            return names
    raise ValueError("could not mint free concept ids")


def default_type(section_at_assign) -> str:
    """The tracker's `New Concept / Iteration` value: Iteration for iterate or make_more, else New-Concept."""
    return "Iteration" if section_at_assign in ("iterate", "make_more") else "New-Concept"


def build_ad_name(concept_id, kind, pain, icp, product, style, fidelity, vendor, type_, proof, page_token) -> str:
    """The tracker `Name` formula, exactly: fields joined by '_' in the sheet's order, then every space removed (nothing else is changed)."""
    parts = [concept_id, media_token(kind), pain, icp, product, style, fidelity, vendor, type_, proof, page_token]
    return "_".join("" if p is None else str(p) for p in parts).replace(" ", "")


def tracker_row_values(concept_id, kind, pain, icp, product, style, fidelity, vendor, type_, proof, page_token, asset_url="", brief_url="", status="5 - Brief Sent") -> dict:
    """Header name -> value for a new Creative Tracker row, from the same inputs as build_ad_name (so the copied row and the ad name cannot disagree).
    `Type` is the tracker's IMG/VID column; `New Concept / Iteration` is the type_ value; `Handle` is the page token."""
    return {"Brief Link": brief_url or "", "Asset URL": asset_url or "", "Concept ID": concept_id, "Status": status, "Type": media_token(kind), "Pain Point": pain or "",
            ICP_HEADER: icp or "", "Product": product or "", "Style": style or "", "Creative Fidelity": fidelity or "", "Vendor": vendor or "", "Proof Mechanism": proof or "",
            "New Concept / Iteration": type_ or "", "Handle": page_token or ""}


def tracker_row_tsv(values: dict, header_order=None) -> str:
    """One tab separated line in the given header order (default TRACKER_HEADERS); a missing header is an empty cell; tabs and newlines inside a value become spaces."""
    order = list(header_order) if header_order is not None else TRACKER_HEADERS
    return "\t".join(re.sub(r"[\t\r\n]+", " ", str(values.get(h, "") if values.get(h) is not None else "")) for h in order)


def _natural(name):
    return [int(t) if t.isdigit() else t for t in _NUM.split(str(name).lower())]


def order_files(files) -> list:
    """Sort by file name, natural and case-insensitive (2.png before 10.png); stable for equal names. The caller applies any cap."""
    def key(f):
        k = _natural(f.get("name", ""))
        return [(0, x, "") if isinstance(x, int) else (1, 0, x) for x in k]
    return sorted(files, key=key)


# ------------------------------------------------------------------------------------------------------------------ copy
def _clean_list(v, limit=MAX_COPY) -> list[str]:
    out = []
    for x in (v or []):
        t = str(x or "").strip()
        if t and t not in out:
            out.append(t)
    return out[:limit]


def resolve_copy(draft, best_ad, defaults) -> dict:
    """Primary texts and headlines: the draft wins, then the best existing ad, then the product default (each list on its own, blanks trimmed, at most 3).
    `source` names where the primary texts came from (the headlines' source when there are no primary texts)."""
    levels = [("draft", draft or {}), ("best_ad", best_ad or {}), ("default", defaults or {})]

    def pick(field):
        for name, src in levels:
            v = _clean_list(src.get(field))
            if v:
                return v, name
        return [], "default"
    texts, st = pick("primary_texts")
    heads, sh = pick("headlines")
    return {"primary_texts": texts, "headlines": heads, "source": st if texts else sh}


# ------------------------------------------------------------------------------------------------------------------ plan
def adset_name(product, vendor, for_week) -> str:
    return f"{TRACKER_PRODUCTS.get(product) or PRODUCT_WORDS.get(product) or product}_{vendor}_{for_week.isoformat() if hasattr(for_week, 'isoformat') else for_week}_Production"


def build_plan(assignment, settings, page, files, concept_ids, fields, copy, adset) -> dict:
    """Deterministic plain dict of everything a launch would do. `files` are in launch order (order_files), `concept_ids` line up with them, `fields` carries style / fidelity / type / proof
    (the draft after validate_fields), `adset` is {'meta_adset_id': ...} for an existing ad set or None for a new one. Status is always PAUSED."""
    a, s, pg = assignment, settings, page or {}
    product = a.get("product")
    token = pg.get("name_token") or ""
    type_ = fields.get("type") or default_type(a.get("section_at_assign"))
    ads = []
    for f, cid in zip(files, concept_ids):
        kind = f.get("kind")
        ads.append({"file_id": f.get("id"), "file_name": f.get("name"), "kind": kind, "concept_id": cid,
                    "ad_name": build_ad_name(cid, kind, a.get("pain_point"), a.get("icp"), TRACKER_PRODUCTS.get(product, product), fields.get("style"), fields.get("fidelity"),
                                             a.get("vendor"), type_, fields.get("proof"), token),
                    "link": s.get("landing_url"), "url_tags": s.get("url_tags"), "cta": s.get("cta_video") if kind == "video" else s.get("cta_image"),
                    "page_id": pg.get("page_id"), "page_name": pg.get("name"), "instagram_user_id": pg.get("instagram_user_id"),
                    "primary_texts": list(copy.get("primary_texts") or []), "headlines": list(copy.get("headlines") or []), "description": s.get("description")})
    name = adset_name(product, a.get("vendor"), a.get("for_week"))
    dest = {"account_id": s.get("account_id"), "campaign_id": s.get("campaign_id"), "pixel_id": s.get("pixel_id"),
            "adset_id": (adset or {}).get("meta_adset_id") or None, "adset_name": name, "new_adset": not (adset or {}).get("meta_adset_id"),
            "template_adset_id": s.get("template_adset_id"), "daily_budget_cents": s.get("daily_budget_cents"), "bid_amount_cents": s.get("bid_amount_cents")}
    return {"assignment_id": a.get("id"), "product": product, "vendor": a.get("vendor"), "for_week": str(a.get("for_week")), "status": PAUSED, "copy_source": copy.get("source"),
            "destination": dest, "ads": ads}


def plan_hash(plan) -> str:
    """sha256 of the canonical JSON (sorted keys, no whitespace, str() for dates); the same plan always gives the same hash whatever its dict order."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------------------ validation
FIELD_KEYS = ("style", "fidelity", "type", "proof", "page")


def validate_fields(fields, allowed: dict, require_all: bool = False):
    """(clean, errors). Each of style / fidelity / type / proof / page, when given, must be one of allowed[key] (exact match after trimming). Unknown keys are dropped.
    A blank value is left out; with require_all a blank is an error too."""
    clean, errors = {}, []
    src = fields or {}
    for k in FIELD_KEYS:
        v = str(src.get(k) if src.get(k) is not None else "").strip()
        if not v:
            if require_all:
                errors.append(f"{k} is required")
            continue
        if v not in (allowed or {}).get(k, []):
            errors.append(f"{k} must be one of: " + ", ".join((allowed or {}).get(k, [])))
            continue
        clean[k] = v
    return clean, errors


# ------------------------------------------------------------------------------------------------------------------- env
def _env(env):
    return os.environ if env is None else env


def launch_enabled(env=None) -> bool:
    """Launching is on only when META_LAUNCH_ENABLED is exactly '1'."""
    return _env(env).get("META_LAUNCH_ENABLED") == "1"


def launch_users(env=None) -> set[str]:
    """META_LAUNCH_USERS: comma list, trimmed and lowercased; unset or empty means nobody."""
    return {u.strip().lower() for u in str(_env(env).get("META_LAUNCH_USERS") or "").split(",") if u.strip()}


def dry_run(env=None) -> bool:
    """Dry run unless META_LAUNCH_DRY_RUN is exactly '0' (anything else, or unset, stays a dry run)."""
    return _env(env).get("META_LAUNCH_DRY_RUN") != "0"
