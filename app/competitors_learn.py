"""Learning layer of the Competitors page: model-read tags and transcripts of competitor ads, the 'What competitors are doing' charts (competitor share against Lonvera's share of spend), and tagged landing pages.

Read only. Every table here is optional (checked with to_regclass): absent or empty means no new keys in the payload and the page behaves exactly as before. Tables are written by other jobs:
competitor_ad_transcript, competitor_ad_tags, competitor_landing_pages, competitor_lp_media, competitor_ad_lp. See docs/COMPETITOR_ADS.md.
"""
from __future__ import annotations

import collections
import re
from urllib.parse import quote

from . import queries as q

# page filter param -> tag column. Proof and emotion are shown as chips and charts but are not page filters.
FILTERS = (("style", "style"), ("hook", "hook_type"), ("device", "device"), ("offer", "offer_type"))
FILTER_PARAM = {col: p for p, col in FILTERS}
FILTER_LABEL = {"style": "Style", "hook_type": "Hook type", "device": "Device", "offer_type": "Offer type"}
# (tag column, chart title, Lonvera key or None)
LEARN = (("style", "Style", "style"), ("hook_type", "Hook type", None), ("device", "Device", None),
         ("proof_mechanism", "Proof mechanism", "proof_mechanism"), ("emotion", "Emotion", "emotion"), ("offer_type", "Offer type", None))
CHIP_ORDER = (("style", "Style"), ("hook_type", "Hook type"), ("device", "Device"), ("proof_mechanism", "Proof"), ("emotion", "Emotion"), ("offer_type", "Offer"))
TAG_COLS = ("style", "fidelity", "proof_mechanism", "emotion", "hook_type", "offer_type", "device", "cta", "hook_text", "why_it_works", "seen_on", "said_on", "confidence", "cluster", "pain_point", "is_new_angle", "new_angle_label")
GAP_POINTS = 15            # a value is highlighted when competitors lean on it this many percentage points harder than Lonvera ...
GAP_MIN_ADS = 5            # ... and at least this many tagged ads carry it
CHART_ROWS = 8
LONVERA_DAYS = 30
NOTE = "Tags are read by a model from each ad's picture and words, so they are approximate."
TRANSCRIPT_MAX = 6000
SEEN_MAX = 1500


def exists(conn, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", ("public." + table,)).fetchone()["ok"]


def norm(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ""


# ---------------------------------------------------------------- reading rows

def sql_parts(conn) -> tuple[str, str]:
    """(extra SELECT columns, extra JOINs) for the ads query: tags, transcript and landing-page link, each only when its table exists."""
    sel, join = "", ""
    if exists(conn, "competitor_ad_tags"):
        sel += ", (t.library_id IS NOT NULL) AS tg_present" + "".join(f", t.{c} AS tg_{c}" for c in TAG_COLS)
        join += "LEFT JOIN competitor_ad_tags t ON t.library_id = a.library_id\n"
    if exists(conn, "competitor_ad_transcript"):
        sel += ", tx.transcript AS tr_transcript"
        join += "LEFT JOIN competitor_ad_transcript tx ON tx.library_id = a.library_id\n"
    if exists(conn, "competitor_ad_lp") and exists(conn, "competitor_landing_pages"):
        sel += ", al.lp_id AS lp_id, lp.page_type AS lp_page_type, lp.key_theme AS lp_key_theme"
        join += ("LEFT JOIN competitor_ad_lp al ON al.week_start = a.week_start AND al.library_id = a.library_id\n"
                 "LEFT JOIN competitor_landing_pages lp ON lp.week_start = al.week_start AND lp.lp_id = al.lp_id\n")
    return sel, join


def merge_tags(a: dict) -> dict:
    """Where the model-read tag row names an angle, it wins over the text-only class of the same ad (copy returned, input untouched)."""
    if not a.get("tg_present"):
        return a
    cl, pp, new = norm(a.get("tg_cluster")) or None, norm(a.get("tg_pain_point")) or None, bool(a.get("tg_is_new_angle"))
    if not (cl or pp or new):
        return a
    b = dict(a)
    b["cluster"], b["pain_point"] = cl, pp
    if b.get("mapped_product") == "none":
        b["mapped_product"] = None
    b["is_new_angle"] = new
    b["new_angle_label"] = (norm(a.get("tg_new_angle_label")) or None) if new else None
    return b


def filters_from(args) -> dict:
    """{tag column: value} from ?style= ?hook= ?device= ?offer= ."""
    out = {}
    for p, col in FILTERS:
        v = (args.get(p) or "").strip()
        if len(v) > 80:
            raise q.ApiError(400, f"{p} is too long")
        if v:
            out[col] = v
    return out


def tag_ok(a: dict, f: dict) -> bool:
    if not f:
        return True
    return bool(a.get("tg_present")) and all(norm(a.get("tg_" + col)).casefold() == v.casefold() for col, v in f.items())


def options(rows: list[dict]) -> dict:
    """Filter values that exist among the tagged ads of the week: {param: [{value, count}]} most common first."""
    out = {}
    for p, col in FILTERS:
        c: collections.Counter = collections.Counter()
        names: dict[str, str] = {}
        for a in rows:
            v = norm(a.get("tg_" + col)) if a.get("tg_present") else ""
            if v:
                c[v.casefold()] += 1
                names.setdefault(v.casefold(), v)
        if c:
            out[p] = [{"value": names[k], "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out


def card_extras(a: dict) -> dict:
    """Keys the lightbox needs, present only when there is something to show."""
    out: dict = {}
    if a.get("tg_present"):
        chips = [{"key": k, "label": lab, "value": norm(a.get("tg_" + k)), "filter": FILTER_PARAM.get(k)} for k, lab in CHIP_ORDER if norm(a.get("tg_" + k))]
        if chips:
            out["tag_chips"] = chips
        for src, dst, cap in (("tg_why_it_works", "why_it_works", 400), ("tg_hook_text", "hook_quote", 400), ("tg_seen_on", "seen_on", SEEN_MAX), ("tg_said_on", "said_on", SEEN_MAX)):
            if norm(a.get(src)):
                out[dst] = norm(a.get(src))[:cap]
    if norm(a.get("tr_transcript")):
        out["transcript"] = norm(a.get("tr_transcript"))[:TRANSCRIPT_MAX]
    if a.get("lp_id") and (a.get("lp_page_type") or a.get("lp_key_theme")):
        out["landing_page"] = {"lp_id": a["lp_id"], "page_type": a.get("lp_page_type"), "key_theme": a.get("lp_key_theme")}
    return out


# ---------------------------------------------------------------- Lonvera's own shares

def lonvera_shares(conn) -> dict | None:
    """Share of Lonvera's Meta spend over the last 30 days by tracker Style / Proof Mechanism / Emotion. One row per ad family (its first tracker row), blank values left out of the base.
    Returns {"through": iso day, "style": {casefold: (label, share)}, ...} or None when there is no spend or no tracker data."""
    if not (exists(conn, "family_daily") and exists(conn, "tracker_rows")):
        return None
    hi = conn.execute("SELECT max(day) AS d FROM family_daily").fetchone()["d"]
    if hi is None:
        return None
    rows = conn.execute(
        """WITH fr AS (SELECT DISTINCT ON (family_key) family_key, style, proof, tracker_emotion FROM tracker_rows WHERE deleted_at IS NULL ORDER BY family_key, sheet_row NULLS LAST, id)
           SELECT fr.style, fr.proof, fr.tracker_emotion AS emotion, sum(f.spend) AS spend FROM family_daily f JOIN fr ON fr.family_key = f.family_key
           WHERE f.day > %s::date - %s AND f.day <= %s GROUP BY 1, 2, 3""", (hi, LONVERA_DAYS, hi)).fetchall()
    out: dict = {"through": hi.isoformat()}
    for key, col in (("style", "style"), ("proof_mechanism", "proof"), ("emotion", "emotion")):
        tot: collections.Counter = collections.Counter()
        names: dict[str, str] = {}
        for r in rows:
            v = norm(r[col])
            if v:
                tot[v.casefold()] += float(r["spend"] or 0)
                names.setdefault(v.casefold(), v)
        base = sum(tot.values())
        if base > 0:
            out[key] = {k: (names[k], s / base) for k, s in tot.items()}
    return out if len(out) > 1 else None


# ---------------------------------------------------------------- learning section

def _pct(x: float) -> int:
    return int(round(x * 100))


def learning(cur: list[dict], lon: dict | None) -> dict | None:
    tagged = [a for a in cur if a.get("tg_present")]
    if not tagged:
        return None
    charts = []
    for col, title, lkey in LEARN:
        cnt: collections.Counter = collections.Counter()
        names: dict[str, str] = {}
        for a in tagged:
            v = norm(a.get("tg_" + col))
            if v:
                cnt[v.casefold()] += 1
                names.setdefault(v.casefold(), v)
        n = sum(cnt.values())
        if n == 0:
            continue
        ld = (lon or {}).get(lkey) if lkey else None
        keys = set(cnt) | {k for k, (lab, s) in (ld or {}).items() if s >= 0.05}
        rows = []
        for k in keys:
            cs = cnt.get(k, 0) / n
            ls = ld[k][1] if ld and k in ld else (0.0 if ld else None)
            label = names.get(k) or ld[k][0]
            hl = bool(ld) and cnt.get(k, 0) >= GAP_MIN_ADS and round((cs - (ls or 0)) * 100, 6) >= GAP_POINTS
            rows.append({"value": label, "ads": cnt.get(k, 0), "share": round(cs, 4), "lonvera_share": None if ls is None else round(ls, 4), "hl": hl})
        rows.sort(key=lambda r: (-max(r["share"], r["lonvera_share"] or 0), r["value"]))
        rows = rows[:CHART_ROWS]
        pick = max((r for r in rows if r["hl"]), key=lambda r: r["share"] - (r["lonvera_share"] or 0), default=None) or max(rows, key=lambda r: (r["share"], r["value"]))
        sent = f"Competitors lean on {pick['value'].lower()} {_pct(pick['share'])}% of the time"
        sent += f", Lonvera {_pct(pick['lonvera_share'])}% of spend." if pick["lonvera_share"] is not None else "."
        charts.append({"key": col, "title": title, "n": n, "has_lonvera": ld is not None, "sentence": sent, "rows": rows})
    combos: dict[tuple, dict] = {}
    for a in tagged:
        h, d = norm(a.get("tg_hook_type")), norm(a.get("tg_device"))
        if not (h and d):
            continue
        g = combos.setdefault((h.casefold(), d.casefold()), {"hook_type": h, "device": d, "ads": 0, "brands": set(), "ex": None})
        g["ads"] += 1
        g["brands"].add(a["brand"])
        if g["ex"] is None or (a.get("days_running") or 0, a["library_id"]) > (g["ex"].get("days_running") or 0, g["ex"]["library_id"]):
            g["ex"] = a
    top = sorted(combos.values(), key=lambda g: (-g["ads"], -len(g["brands"]), g["hook_type"], g["device"]))[:5]
    from .competitors import ad_url        # late import: competitors imports this module
    return {"tagged_ads": len(tagged), "total_ads": len(cur), "note": NOTE, "lonvera_through": (lon or {}).get("through"), "charts": charts,
            "combos": [{"hook_type": g["hook_type"], "device": g["device"], "ads": g["ads"], "brands": len(g["brands"]),
                        "example": {"library_id": g["ex"]["library_id"], "brand": g["ex"]["brand"], "hook": (g["ex"].get("tg_hook_text") or g["ex"].get("hook") or g["ex"].get("headline") or "")[:120],
                                    "ad_library_url": ad_url(g["ex"]["library_id"])}} for g in top]}


def tag_mix(cur: list[dict]) -> dict | None:
    """A brand's most used style, hook type and device with counts (top three each)."""
    out = {}
    for col in ("style", "hook_type", "device"):
        c: collections.Counter = collections.Counter()
        names: dict[str, str] = {}
        for a in cur:
            v = norm(a.get("tg_" + col)) if a.get("tg_present") else ""
            if v:
                c[v.casefold()] += 1
                names.setdefault(v.casefold(), v)
        if c:
            out[col] = [{"value": names[k], "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return out or None


# ---------------------------------------------------------------- landing pages

LP_SQL = ("SELECT p.lp_id, p.brand, p.final_url, p.page_type, p.key_theme, p.hero_headline, p.selling_points, p.offer_summary, p.price_text, p.guarantee, p.proof_elements,\n"
          "       p.urgency_devices, p.objections, p.cta, p.cluster, p.pain_point, p.why_it_works, p.ad_count, p.changed_from_prior, p.change_note, p.fetch_error, {media}\n"
          "FROM competitor_landing_pages p {join}WHERE p.week_start = %s{brand}\nORDER BY p.ad_count DESC NULLS LAST, p.lp_id")


def lp_rows(conn, week: str | None, brand: str | None = None) -> list[dict]:
    if not week or not exists(conn, "competitor_landing_pages"):
        return []
    has_m = exists(conn, "competitor_lp_media")
    sql = LP_SQL.format(media="(m.lp_id IS NOT NULL) AS has_media" if has_m else "false AS has_media",
                        join="LEFT JOIN competitor_lp_media m ON m.week_start = p.week_start AND m.lp_id = p.lp_id\n" if has_m else "", brand=" AND p.brand = %s" if brand else "")
    return [r for r in conn.execute(sql, (week, brand) if brand else (week,)).fetchall() if r["page_type"] or r["key_theme"] or r["hero_headline"]]


def lp_linked(conn, week: str | None) -> bool:
    return bool(week) and exists(conn, "competitor_ad_lp") and bool(conn.execute("SELECT 1 FROM competitor_ad_lp WHERE week_start = %s LIMIT 1", (week,)).fetchone())


def _list(v, cap=8) -> list[str]:
    return [norm(x) for x in (v or []) if norm(x)][:cap]


def lp_card(r: dict, week: str) -> dict:
    out = {"lp_id": r["lp_id"], "brand": r["brand"], "final_url": r.get("final_url"), "page_type": r.get("page_type"), "key_theme": r.get("key_theme"), "hero_headline": r.get("hero_headline"),
           "selling_points": _list(r.get("selling_points")), "offer_summary": r.get("offer_summary"), "price_text": r.get("price_text"), "guarantee": r.get("guarantee"),
           "proof_elements": _list(r.get("proof_elements")), "urgency_devices": _list(r.get("urgency_devices")), "objections": _list(r.get("objections")), "cta": r.get("cta"),
           "why_it_works": r.get("why_it_works"), "ad_count": r.get("ad_count") or 0, "changed": bool(r.get("changed_from_prior")), "change_note": r.get("change_note") if r.get("changed_from_prior") else None,
           "angle": " / ".join(x for x in (r.get("cluster"), r.get("pain_point")) if x) or None}
    out["lp_thumb_url"] = f"/api/competitors/lp-media/{quote(str(r['lp_id']), safe='')}?week={week}" if r.get("has_media") else None
    return out


def restrict(rows: list[dict], cur: list[dict], active: bool, linked: bool) -> list[dict]:
    """With a format or tag filter on, keep only landing pages some visible ad points to (when ads are linked to pages at all)."""
    if not (active and linked):
        return rows
    keep = {a.get("lp_id") for a in cur if a.get("lp_id")}
    return [r for r in rows if r["lp_id"] in keep]


GUAR = re.compile(r"(\d+)\s*[- ]?\s*(day|week|month|year)s?", re.I)
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def guarantee_days(text: str | None) -> int | None:
    m = GUAR.search(text or "")
    return int(m.group(1)) * UNIT_DAYS[m.group(2).lower()] if m else None


def _count(values_per_row: list[list[str]], cap=8) -> list[dict]:
    c: collections.Counter = collections.Counter()
    names: dict[str, collections.Counter] = {}
    for vs in values_per_row:
        seen: dict[str, str] = {}
        for v in vs:
            seen.setdefault(v.casefold(), v)             # one vote per page per value
        for k, v in seen.items():
            c[k] += 1
            names.setdefault(k, collections.Counter())[v] += 1
    return [{"label": names[k].most_common(1)[0][0], "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]]


def lp_patterns(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    charts = []
    types = [[norm(r["page_type"])] for r in rows if norm(r.get("page_type"))]
    for key, title, per in (("page_type", "Page types", types), ("proof", "Proof elements", [_list(r.get("proof_elements")) for r in rows]),
                            ("urgency", "Urgency devices", [_list(r.get("urgency_devices")) for r in rows]), ("selling_points", "Selling points", [_list(r.get("selling_points"), 12) for r in rows])):
        cs = _count(per)
        if cs:
            charts.append({"key": key, "title": title, "rows": cs})
    days = [[(f"{d} days" if d % 365 else f"{d // 365} year")] for d in (guarantee_days(r.get("guarantee")) for r in rows) if d]
    gc: collections.Counter = collections.Counter(d[0] for d in days)
    if gc:
        charts.append({"key": "guarantee", "title": "Guarantee length", "rows": [{"label": k, "count": n} for k, n in sorted(gc.items(), key=lambda kv: (-kv[1], int(kv[0].split()[0])))[:CHART_ROWS]]})
    changed = [{"lp_id": r["lp_id"], "brand": r["brand"], "page_type": r.get("page_type"), "change_note": r.get("change_note"), "final_url": r.get("final_url")}
               for r in sorted((r for r in rows if r.get("changed_from_prior")), key=lambda r: (r["brand"], r["lp_id"]))][:8]
    return {"pages": len(rows), "charts": charts, "changed": changed}
