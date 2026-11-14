"""Learning layer of the Competitors page: model-read tags and transcripts of competitor ads, the 'What competitors are doing' charts (competitor share against Lonvera's share of spend), and tagged landing pages.

Read only. Every table here is optional (checked with to_regclass): absent or empty means no new keys in the payload and the page behaves exactly as before. Tables are written by other jobs:
competitor_ad_transcript, competitor_ad_tags, competitor_landing_pages (is_own rows = our own pages), competitor_lp_media, competitor_ad_lp, and for Lonvera's own side ad_learning_tags and ad_family_lp.
See docs/COMPETITOR_ADS.md.
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
LEARN = (("style", "Style", "style"), ("hook_type", "Hook type", "hook_type"), ("device", "Device", "device"),
         ("proof_mechanism", "Proof mechanism", "proof_mechanism"), ("emotion", "Emotion", "emotion"), ("offer_type", "Offer type", "offer_type"))
OWN_TAGS = "ad_learning_tags"
OWN_DIMS = ("hook_type", "device", "offer_type")          # Lonvera's own values come from ad_learning_tags; style / proof / emotion from the Creative Tracker
BEST_FLOOR = 1000.0                                      # a value needs this much spend in the window before it can be called our best
THIN_SPEND = 5000.0                                      # ... and below this spend or under THIN_ADS ad families the line says so
THIN_ADS = 3
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


def has_col(conn, table: str, col: str) -> bool:
    return conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s AND column_name = %s", (table, col)).fetchone() is not None


def not_own(conn, alias: str) -> str:
    """' AND NOT COALESCE(<alias>.is_own, false)' when competitor_landing_pages has the column (our own pages live in the same table), else ''."""
    return f" AND NOT COALESCE({alias}.is_own, false)" if has_col(conn, "competitor_landing_pages", "is_own") else ""


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
                 "LEFT JOIN competitor_landing_pages lp ON lp.week_start = al.week_start AND lp.lp_id = al.lp_id" + not_own(conn, "lp") + "\n")
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

def _own_tags_cte(conn) -> tuple[str, str, str]:
    """(CTE text, select columns, join) that bring Lonvera's own hook type / device / offer type next to the first tracker row of each family; blanks when the table is absent or empty."""
    if not (exists(conn, OWN_TAGS) and conn.execute(f"SELECT 1 FROM {OWN_TAGS} LIMIT 1").fetchone()):
        return "", ", NULL::text AS hook_type, NULL::text AS device, NULL::text AS offer_type", ""
    cte = (f", lt AS (SELECT DISTINCT ON (family_key) family_key, NULLIF(btrim(hook_type), '') AS hook_type, NULLIF(btrim(device), '') AS device, NULLIF(btrim(offer_type), '') AS offer_type "
           f"FROM {OWN_TAGS} ORDER BY family_key, tagged_at DESC NULLS LAST)")
    return cte, ", lt.hook_type, lt.device, lt.offer_type", "LEFT JOIN lt ON lt.family_key = fr.family_key"


KEYS = (("style", "style"), ("proof_mechanism", "proof"), ("emotion", "emotion"), ("hook_type", "hook_type"), ("device", "device"), ("offer_type", "offer_type"))


def lonvera_shares(conn) -> dict | None:
    """Share of Lonvera's Meta spend over the last 30 days by tracker Style / Proof Mechanism / Emotion and, once ad_learning_tags has rows, by hook type / device / offer type.
    One row per ad family (its first tracker row), blank values left out of the base. Also "best": per dimension the two values with the highest NC ROAS (Upstack new customer revenue / spend)
    among those with at least BEST_FLOOR spend, over the same 30 days ending at the last day both Meta and Upstack hold.
    Returns {"through": iso day, "style": {casefold: (label, share)}, ..., "best": {key: [{value, nc_roas, spend, ads, thin}]}} or None when there is no spend or no tracker data."""
    if not (exists(conn, "family_daily") and exists(conn, "tracker_rows")):
        return None
    hi = conn.execute("SELECT max(day) AS d FROM family_daily").fetchone()["d"]
    if hi is None:
        return None
    cte, cols, join = _own_tags_cte(conn)
    fr = ("fr AS (SELECT DISTINCT ON (family_key) family_key, style, proof, tracker_emotion FROM tracker_rows WHERE deleted_at IS NULL ORDER BY family_key, sheet_row NULLS LAST, id)" + cte)
    rows = conn.execute(
        f"""WITH {fr}
           SELECT fr.style, fr.proof, fr.tracker_emotion AS emotion{cols}, sum(f.spend) AS spend FROM family_daily f JOIN fr ON fr.family_key = f.family_key {join}
           WHERE f.day > %s::date - %s AND f.day <= %s GROUP BY 1, 2, 3, 4, 5, 6""", (hi, LONVERA_DAYS, hi)).fetchall()
    out: dict = {"through": hi.isoformat()}
    for key, col in KEYS:
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
    if len(out) < 2:
        return None
    best = lonvera_best(conn, fr, cols, join)
    if best:
        out["best"] = best
    return out


def lonvera_best(conn, fr: str, cols: str, join: str) -> dict:
    """{dimension: [two best values by NC ROAS with at least BEST_FLOOR spend]} over the last 30 days of the Upstack new customer table (never past the last Meta day)."""
    if not exists(conn, "family_daily_upstack_nc"):
        return {}
    hi = conn.execute("SELECT LEAST((SELECT max(day) FROM family_daily), (SELECT max(day) FROM family_daily_upstack_nc)) AS d").fetchone()["d"]
    if hi is None:
        return {}
    rows = conn.execute(
        f"""WITH {fr}
           SELECT fr.style, fr.proof, fr.tracker_emotion AS emotion{cols}, count(DISTINCT f.family_key) FILTER (WHERE f.spend > 0) AS fams, sum(f.spend) AS spend, sum(f.revenue) AS rev
           FROM family_daily_upstack_nc f JOIN fr ON fr.family_key = f.family_key {join}
           WHERE f.day > %s::date - %s AND f.day <= %s GROUP BY 1, 2, 3, 4, 5, 6""", (hi, LONVERA_DAYS, hi)).fetchall()
    out: dict = {}
    for key, col in KEYS:
        agg: dict[str, list] = {}
        names: dict[str, str] = {}
        for r in rows:
            v = norm(r[col])
            if v:
                a = agg.setdefault(v.casefold(), [0.0, 0.0, 0])
                a[0] += float(r["spend"] or 0)
                a[1] += float(r["rev"] or 0)
                a[2] += int(r["fams"] or 0)
                names.setdefault(v.casefold(), v)
        ok = [(k, a) for k, a in agg.items() if a[0] >= BEST_FLOOR]
        ok.sort(key=lambda kv: (-(kv[1][1] / kv[1][0]), -kv[1][0], kv[0]))
        if ok:
            out[key] = [{"value": names[k], "nc_roas": round(a[1] / a[0], 4), "spend": round(a[0], 2), "ads": a[2], "thin": a[0] < THIN_SPEND or a[2] < THIN_ADS} for k, a in ok[:2]]
    if out:
        out["_through"] = hi.isoformat()
    return out


# ---------------------------------------------------------------- learning section

def _pct(x: float) -> int:
    return int(round(x * 100))


def money_short(x: float) -> str:
    return f"${x / 1000:.0f}K" if x >= 10000 else f"${x / 1000:.1f}K" if x >= 1000 else f"${x:.0f}"


def plain(v: str) -> str:
    """Lower case for running text, except a value that carries capitals inside it (an acronym such as 'UGC demo')."""
    return v if any(c.isupper() for c in v[1:]) else v.lower()


def working(title: str, best: list[dict] | None, cnt: collections.Counter, n: int) -> dict | None:
    """'What is working for us' under a chart: our top values of the dimension by NC ROAS (computed, not generated) next to how often competitors use them."""
    if not best:
        return None
    items = []
    for b in best:
        items.append({**b, "competitor_share": round(cnt.get(b["value"].casefold(), 0) / n, 4) if n else None})
    parts = []
    for i, b in enumerate(items):
        comp = f"competitors use it {_pct(b['competitor_share'])}% of the time" if b["competitor_share"] is not None else ""
        on = f"{b['nc_roas']:.2f} on {money_short(b['spend'])}" + (" (thin sample)" if b["thin"] else "")
        if i == 0:
            parts.append(f"Our best {title.lower()} on NC ROAS is {plain(b['value'])} at {on}" + (f", {comp}." if comp else "."))
        else:
            parts.append(f"Next: {plain(b['value'])} at {on}" + (f", competitors {_pct(b['competitor_share'])}%." if comp else "."))
    return {"items": items, "sentence": " ".join(parts)}


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
        chart = {"key": col, "title": title, "n": n, "has_lonvera": ld is not None, "sentence": sent, "rows": rows}
        wk = working(title, ((lon or {}).get("best") or {}).get(lkey) if lkey else None, cnt, n)
        if wk:
            chart["working"] = wk
        charts.append(chart)
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
    return {"tagged_ads": len(tagged), "total_ads": len(cur), "note": NOTE, "lonvera_through": (lon or {}).get("through"), "best_through": ((lon or {}).get("best") or {}).get("_through"), "charts": charts,
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
          "FROM competitor_landing_pages p {join}WHERE p.week_start = %s{brand}{own}\nORDER BY p.ad_count DESC NULLS LAST, p.lp_id")


def lp_rows(conn, week: str | None, brand: str | None = None) -> list[dict]:
    if not week or not exists(conn, "competitor_landing_pages"):
        return []
    has_m = exists(conn, "competitor_lp_media")
    sql = LP_SQL.format(media="(m.lp_id IS NOT NULL) AS has_media" if has_m else "false AS has_media",
                        join="LEFT JOIN competitor_lp_media m ON m.week_start = p.week_start AND m.lp_id = p.lp_id\n" if has_m else "", brand=" AND p.brand = %s" if brand else "", own=not_own(conn, "p"))
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


# ---------------------------------------------------------------- our own landing pages
# Rows of competitor_landing_pages with is_own = true are OUR pages (brand Lonvera); ad_family_lp(family_key, lp_id, week_start) links our ad families to them.
# They never enter a competitor query (not_own above); this panel is the only reader.

OWN_SQL = ("SELECT p.lp_id, p.brand, p.final_url, p.page_type, p.key_theme, p.hero_headline, p.selling_points, p.offer_summary, p.price_text, p.guarantee, p.proof_elements,\n"
           "       p.urgency_devices, p.objections, p.cta, p.cluster, p.pain_point, p.why_it_works, p.ad_count, p.changed_from_prior, p.change_note, p.fetch_error, {media}\n"
           "FROM competitor_landing_pages p {join}WHERE p.week_start = %s AND COALESCE(p.is_own, false)\nORDER BY p.ad_count DESC NULLS LAST, p.lp_id")
TOP_FAMILIES = 5


def own_lp_ctx(conn, sel: str | None) -> dict | None:
    """Our pages of the latest week at or before the selected competitor week, with the ad families pointing to each and their last 30 days (Meta spend and return, Upstack new customer return). None when there are none."""
    if not sel or not exists(conn, "competitor_landing_pages") or not has_col(conn, "competitor_landing_pages", "is_own"):
        return None
    wk = conn.execute("SELECT max(week_start) AS w FROM competitor_landing_pages WHERE COALESCE(is_own, false) AND week_start <= %s", (sel,)).fetchone()["w"]
    if wk is None:
        return None
    has_m = exists(conn, "competitor_lp_media")
    sql = OWN_SQL.format(media="(m.lp_id IS NOT NULL) AS has_media" if has_m else "false AS has_media",
                         join="LEFT JOIN competitor_lp_media m ON m.week_start = p.week_start AND m.lp_id = p.lp_id\n" if has_m else "")
    rows = [r for r in conn.execute(sql, (wk,)).fetchall() if r["page_type"] or r["key_theme"] or r["hero_headline"]]
    if not rows:
        return None
    fams: dict[str, list[dict]] = {}
    through = None
    if exists(conn, "ad_family_lp") and exists(conn, "family_daily"):
        nc = exists(conn, "family_daily_upstack_nc")
        hi = conn.execute("SELECT " + ("LEAST((SELECT max(day) FROM family_daily), (SELECT max(day) FROM family_daily_upstack_nc))" if nc else "(SELECT max(day) FROM family_daily)") + " AS d").fetchone()["d"]
        through = hi.isoformat() if hi else None
        nc_cte = (", n AS (SELECT family_key, sum(spend) AS nsp, sum(revenue) AS nrev FROM family_daily_upstack_nc WHERE day > %(hi)s::date - %(d)s AND day <= %(hi)s GROUP BY 1)" if nc else "")
        nc_cols, nc_join = (", n.nrev AS nc_revenue", "LEFT JOIN n ON n.family_key = l.family_key") if nc else (", NULL::numeric AS nc_revenue", "")
        got = conn.execute(
            f"""WITH m AS (SELECT family_key, sum(spend) AS spend, sum(revenue) AS rev FROM family_daily WHERE day > %(hi)s::date - %(d)s AND day <= %(hi)s GROUP BY 1){nc_cte},
                     fr AS (SELECT DISTINCT ON (family_key) family_key, COALESCE(NULLIF(btrim(ad_family), ''), concept_id) AS name FROM tracker_rows WHERE deleted_at IS NULL ORDER BY family_key, sheet_row NULLS LAST, id)
               SELECT l.lp_id, l.family_key, COALESCE(fr.name, l.family_key) AS name, COALESCE(m.spend, 0) AS spend, COALESCE(m.rev, 0) AS meta_revenue{nc_cols}
               FROM ad_family_lp l LEFT JOIN m ON m.family_key = l.family_key {nc_join} LEFT JOIN fr ON fr.family_key = l.family_key
               WHERE l.week_start = %(wk)s ORDER BY l.lp_id, spend DESC, l.family_key""", {"hi": hi, "d": LONVERA_DAYS, "wk": wk}).fetchall() if hi else []
        for r in got:
            fams.setdefault(r["lp_id"], []).append(r)
    return {"week": wk.isoformat(), "rows": rows, "families": fams, "through": through, "has_nc": exists(conn, "family_daily_upstack_nc")}


def _ratio(a, b, nd=4):
    return round(float(a) / float(b), nd) if a is not None and b and float(b) > 0 else None


def own_lp_cards(own: dict) -> list[dict]:
    out = []
    for r in own["rows"]:
        card = lp_card(r, own["week"])
        fl = own["families"].get(r["lp_id"], [])
        spend = sum(float(x["spend"] or 0) for x in fl)
        card.update({"own": True, "ad_count": len(fl), "spend": round(spend, 2), "meta_roas": _ratio(sum(float(x["meta_revenue"] or 0) for x in fl), spend),
                     "nc_roas": _ratio(sum(float(x["nc_revenue"] or 0) for x in fl), spend) if own.get("has_nc") else None,
                     "families": [{"name": x["name"], "spend": round(float(x["spend"] or 0), 2)} for x in fl[:TOP_FAMILIES]], "more_families": max(0, len(fl) - TOP_FAMILIES)})
        out.append(card)
    return out


def own_lp_compare(own_rows: list[dict], comp_rows: list[dict]) -> list[str]:
    """One-line comparisons of our pages against the competitor pages of the week: page type, proof elements, guarantee. Empty without competitor pages."""
    n = len(comp_rows)
    if not n:
        return []
    lines = []
    ours_t = sorted({norm(r.get("page_type")) for r in own_rows if norm(r.get("page_type"))}, key=str.casefold)
    typ = _count([[norm(r["page_type"])] for r in comp_rows if norm(r.get("page_type"))], 2)
    if ours_t and typ:
        tn = sum(1 for r in comp_rows if norm(r.get("page_type")))
        lines.append("Page type: ours " + ("is " if len(ours_t) == 1 else "are ") + " and ".join(t.lower() for t in ours_t) + ". Competitors: "
                     + ", ".join(f"{round(100 * c['count'] / tn)}% {c['label'].lower()}" for c in typ) + ".")
    mine = {x.casefold() for r in own_rows for x in _list(r.get("proof_elements"))}
    pr = _count([_list(r.get("proof_elements")) for r in comp_rows], 3)
    if pr:
        lines.append("Proof: Competitors: " + "; ".join(f"{round(100 * c['count'] / n)}% use {c['label'].lower()} (we {'do' if c['label'].casefold() in mine else 'do not'})" for c in pr) + ".")
    ours_g = sorted({d for d in (guarantee_days(r.get("guarantee")) for r in own_rows) if d})
    theirs = collections.Counter(d for d in (guarantee_days(r.get("guarantee")) for r in comp_rows) if d)
    if theirs:
        d, c = sorted(theirs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        ours = ("ours is " + " and ".join(f"{x} days" for x in ours_g)) if ours_g else "ours states none"
        lines.append(f"Guarantee: {ours}. Competitors: the most common is {d} days ({round(100 * c / sum(theirs.values()))}% of pages that state one).")
    return lines


def own_lp_panel(own: dict | None, comp_rows: list[dict]) -> dict | None:
    if not own or not own.get("rows"):
        return None
    return {"week": own["week"], "through": own.get("through"), "days": LONVERA_DAYS, "pages": own_lp_cards(own), "compare": own_lp_compare(own["rows"], comp_rows),
            "competitor_pages": len(comp_rows)}
