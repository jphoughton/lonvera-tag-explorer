"""Lonvera's own ad learning tags (hook type, device, offer type, CTA, hook quote, why it works, seen / said, transcript) in the Tag Explorer.

Read only. The table `ad_learning_tags` (one row per ad family, written by another job) is optional: absent or empty means no key in any payload and the pages behave exactly as before.
  GET /api/products/<slug>/learning-tags   performance by hook type / device / offer type for the page's product, window, stage, filters (see cuts())
  ad_learning(conn, family_key)            the `learning` block of GET /api/ads/<id>
Metrics come from the existing helpers (queries._rm builds one row per tracker row with the window's spend, purchases, revenue and traffic; queries.shape_m turns the sums into ratios), so every number
here is the same number the tag tree shows for the same selection. Both bases are computed: Meta's own reporting and Upstack new customers (NC), the headline.
"""
from __future__ import annotations

from werkzeug.datastructures import MultiDict

from . import queries as q
from .competitors_learn import exists, norm

TABLE = "ad_learning_tags"
DIMS = (("hook_type", "Hook type"), ("device", "Device"), ("offer_type", "Offer type"))
CHIPS = (("hook_type", "Hook type"), ("device", "Device"), ("offer_type", "Offer type"), ("cta", "CTA"))
UPSTACK_TABLE = "family_daily_upstack_nc"
TRANSCRIPT_MAX = 6000
SEEN_MAX = 1500
QUOTE_MAX = 400


def available(conn) -> bool:
    """True when the learning table exists and holds at least one row."""
    return exists(conn, TABLE) and conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1").fetchone() is not None


# ---------------------------------------------------------------- performance by tag cut

def _sql(conn_unused, meta, w, product, flt: dict) -> tuple[str, dict]:
    sql, params = q._rm(meta, w, product_id=product["id"])
    where = "WHERE TRUE" + q._tag_where(flt.get("cluster"), flt.get("icp"), flt.get("pain"), params, prefix="rm.")
    sql += f""",
lt AS (
  SELECT DISTINCT ON (family_key) family_key, NULLIF(btrim(hook_type), '') AS hook_type, NULLIF(btrim(device), '') AS device, NULLIF(btrim(offer_type), '') AS offer_type
  FROM {TABLE} ORDER BY family_key, tagged_at DESC NULLS LAST
),
rmt AS (
  SELECT rm.*, lt.hook_type, lt.device, lt.offer_type
  FROM rm LEFT JOIN lt ON lt.family_key = rm.family_key {where}
)
SELECT GROUPING(hook_type) AS g_h, GROUPING(device) AS g_d, GROUPING(offer_type) AS g_o, hook_type, device, offer_type,
       count(DISTINCT family_key) FILTER (WHERE hook_type IS NOT NULL) AS n_hook,
       count(DISTINCT family_key) FILTER (WHERE device IS NOT NULL) AS n_device,
       count(DISTINCT family_key) FILTER (WHERE offer_type IS NOT NULL) AS n_offer,
       {q.AGG_COLS}
FROM rmt GROUP BY GROUPING SETS ((hook_type), (device), (offer_type), ())"""
    return sql, params


def _block(agg: dict) -> dict:
    """The metrics of one aggregate row: spend, purchases, revenue, ROAS, CPA, traffic ratios (CTR, CPM, cost per link click, conversion rate) and average order value."""
    m = q.shape_m(agg, None)
    rev, pur = q._f(m["revenue"]), q._f(m["purchases"])
    return {"spend": m["spend"], "purchases": m["purchases"], "revenue": m["revenue"], "roas": m["roas"], "cpa": m["cpa"], "ctr": m["ctr"], "cpm": m["cpm"], "cplc": m["cpc"],
            "conv_rate": m["conv_rate"], "aov": round(rev / pur, 2) if pur else None, "link_clicks": m["link_clicks"], "impressions": m["impressions"]}


def _read(conn, meta, w, product, flt: dict) -> dict:
    sql, params = _sql(conn, meta, w, product, flt)
    rows = q.rows_of(conn, sql, params)
    total = next((r for r in rows if r["g_h"] and r["g_d"] and r["g_o"]), None)
    out: dict = {"families": int(total["unique_ads"]) if total else 0, "total": _block(total) if total else None, "dims": {}}
    sets = {"hook_type": lambda r: (not r["g_h"]) and r["g_d"] and r["g_o"], "device": lambda r: r["g_h"] and (not r["g_d"]) and r["g_o"],
            "offer_type": lambda r: r["g_h"] and r["g_d"] and (not r["g_o"])}
    n_col = {"hook_type": "n_hook", "device": "n_device", "offer_type": "n_offer"}
    for col, _ in DIMS:
        mine = [r for r in rows if sets[col](r)]
        out["dims"][col] = {"tagged": int(total[n_col[col]]) if total else 0,
                            "rows": {r[col]: {"unique_ads": int(r["unique_ads"]), **_block(r)} for r in mine if r[col] is not None}}
    return out


def cuts(conn, args, product: dict, flt: dict | None = None) -> dict:
    """Performance by Hook type / Device / Offer type for one product over the page's window and stage. Per value: ad families, spend and share of spend, and the Meta and the NC (Upstack new customer) blocks."""
    flt = flt or {}
    if not available(conn):
        return {"available": False}
    base = q.load_meta(conn)
    a2 = MultiDict(args)
    for k in ("compare", "cfrom", "cto"):
        a2.pop(k, None)
    sides: dict = {}
    prev_sfx = q._SRC_SFX.get()
    try:
        for src in ("meta", "upstack"):
            if src == "upstack" and not exists(conn, UPSTACK_TABLE):
                continue
            try:
                meta_s = q.meta_for_source(base, src)
            except q.ApiError:
                continue
            w = q.parse_window(a2, meta_s)
            if w.active and not q._CAPS["active"]:
                raise q.ApiError(503, "active-ads data is not built yet")
            q.set_source(src, "nc")
            sides[src] = (meta_s, w, _read(conn, meta_s, w, product, flt))
    finally:
        q._SRC_SFX.set(prev_sfx)
    if "meta" not in sides:
        return {"available": False}
    meta_s, w, mres = sides["meta"]
    nres = sides.get("upstack", (None, None, None))[2]
    out_cuts = []
    for col, title in DIMS:
        mrows = mres["dims"][col]["rows"]
        nrows = (nres or {"dims": {col: {"rows": {}}}})["dims"][col]["rows"]
        values = set(mrows) | set(nrows)
        if not values:
            continue
        tot_sp = q._f((mres["total"] or {}).get("spend"))
        rows = []
        for v in values:
            mb, nb = mrows.get(v), nrows.get(v)
            sp = q._f((mb or nb)["spend"])
            rows.append({"value": v, "unique_ads": (mb or nb)["unique_ads"], "spend": (mb or nb)["spend"], "spend_share": q.share(sp, tot_sp),
                         "meta": {k: x for k, x in mb.items() if k != "unique_ads"} if mb else None, "nc": {k: x for k, x in nb.items() if k != "unique_ads"} if nb else None})
        rows.sort(key=lambda r: (-q._f(r["spend"]), r["value"]))
        tagged = mres["dims"][col]["tagged"]
        out_cuts.append({"key": col, "title": title, "families": mres["families"], "tagged": tagged,
                         "note": f"{tagged} of {mres['families']} ad families tagged", "rows": rows})
    if not out_cuts:
        return {"available": False}
    info = w.info(meta_s)
    return {"available": True, "window_info": info, "has_nc": nres is not None, "cuts": out_cuts,
            "total": {"meta": mres["total"], "nc": nres["total"] if nres else None},
            "note": "NC ROAS is Upstack new customer revenue divided by spend (return on ad spend). Meta ROAS is Meta's own reporting. Each ad family counts once, on its first tracker row, under its tag."}


# ---------------------------------------------------------------- the ad page

def ad_learning(conn, family_key: str | None) -> dict | None:
    """The learning block of one ad family: chips (hook type, device, offer type, CTA), hook quote, why it works, seen / said and the transcript, each only when present. None when there is nothing."""
    if not family_key or not exists(conn, TABLE):
        return None
    r = conn.execute(f"SELECT hook_type, device, offer_type, cta, hook_text, why_it_works, seen_on, said_on, method, confidence, tag_version FROM {TABLE} WHERE family_key = %s ORDER BY tagged_at DESC NULLS LAST LIMIT 1",
                     (family_key,)).fetchone()
    if r is None:
        return None
    out: dict = {}
    chips = [{"key": k, "label": lab, "value": norm(r[k])} for k, lab in CHIPS if norm(r[k])]
    if chips:
        out["chips"] = chips
    for src, dst, cap in (("hook_text", "hook_quote", QUOTE_MAX), ("why_it_works", "why_it_works", QUOTE_MAX), ("seen_on", "seen_on", SEEN_MAX), ("said_on", "said_on", SEEN_MAX)):
        if norm(r[src]):
            out[dst] = norm(r[src])[:cap]
    tr = None
    if exists(conn, "ad_transcript"):
        t = conn.execute("SELECT transcript FROM ad_transcript WHERE family_key = %s ORDER BY created_at DESC NULLS LAST LIMIT 1", (family_key,)).fetchone()
        tr = norm(t["transcript"]) if t else None
    if not tr and exists(conn, "family_content"):
        t = conn.execute("SELECT transcript FROM family_content WHERE family_key = %s", (family_key,)).fetchone()
        tr = norm(t["transcript"]) if t else None
    if tr:
        out["transcript"] = tr[:TRANSCRIPT_MAX]
    if not out:
        return None
    out["method"], out["confidence"], out["version"] = norm(r["method"]) or None, norm(r["confidence"]) or None, norm(r["tag_version"]) or None
    return out
