"""Creative Pipeline "By cut" view (/api/priorities/cuts): the production assignments counted by status AND the Meta spend and return behind the same cuts, for any date window.

A cut is any ordered subset of product, market cluster, ICP (ideal customer profile), pain point and vendor (the assignment vendor on the pipeline side, the tracker Vendor column on the ad side). Pipeline side: the rows of priorities_assign.list_assignments, counted by board status.
Ad side (unique live ads = tracker families that spent in the last two loaded days; ads with spend = families that spent in the window): ad_daily (Meta spend, clicks, impressions, Meta-reported purchases) and ad_daily_upstack_nc (Upstack new customers and new-customer revenue, the NC basis) summed over the window and joined
Meta family -> tracker family -> first tracker row -> current tag (Communication Overview line tag, else the tracker's own columns). Nothing is written. Ratios come from sums, never an average.
"""
from __future__ import annotations

import datetime as dt

import psycopg

from . import priorities_assign as pa, queries as q

DIMS = ("product", "cluster", "icp", "pain_point", "vendor", "media")
DIM_LABELS = {"product": "Product", "cluster": "Market cluster", "icp": "ICP", "pain_point": "Pain point", "vendor": "Vendor", "media": "Image / video"}
PRODUCT_NAMES = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vita Belle"}
STATUSES = ("assigned", "brief_sent", "in_production", "in_review", "ready", "launched")
NONE = "(none)"
MAX_DAYS = 800

# tag per tracker family: the linemap tag when there is one, else the tracker's own columns. first row of the family only, so a family counts once.
CUT_CTE = """
WITH fam AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, tr.id, p.slug AS product, tr.vendor, tr.ad_type, tr.tracker_cluster, tr.tracker_icp, tr.tracker_pain
  FROM tracker_rows tr JOIN products p ON p.id = tr.product_id
  ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
), tagged AS (
  SELECT f.family_key, f.product, NULLIF(btrim(f.vendor), '') AS vendor, CASE upper(btrim(f.ad_type)) WHEN 'VID' THEN 'Video' WHEN 'IMG' THEN 'Image' END AS media,
         COALESCE(NULLIF(btrim(g.cluster), ''), NULLIF(btrim(f.tracker_cluster), '')) AS cluster,
         COALESCE(NULLIF(btrim(g.icp), ''), NULLIF(btrim(f.tracker_icp), '')) AS icp,
         COALESCE(NULLIF(btrim(g.pain_point), ''), NULLIF(btrim(f.tracker_pain), '')) AS pain_point
  FROM fam f LEFT JOIN ad_tags g ON g.tracker_row_id = f.id AND g.tag_set = 'linemap' AND g.is_current
)
"""

SQL_META = CUT_CTE + """
SELECT t.product, t.vendor, t.media, t.cluster, t.icp, t.pain_point, sum(d.spend) AS spend, sum(d.link_clicks) AS clicks, sum(d.impressions) AS impressions, sum(d.purchases) AS meta_purchases, sum(d.conv_value) AS meta_revenue,
       count(DISTINCT t.family_key) FILTER (WHERE d.spend > 0) AS ads_spending
FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.day BETWEEN %s AND %s{flt} GROUP BY 1, 2, 3, 4, 5, 6
"""
# unique live ads: tracker families (one per ad however many copies it has) that spent in the last two loaded days. Not tied to the date window: live is a now-state.
SQL_LIVE = CUT_CTE + """
SELECT t.product, t.vendor, t.media, t.cluster, t.icp, t.pain_point, count(DISTINCT t.family_key) AS live_ads
FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.spend > 0 AND d.day >= (SELECT max(day) FROM ad_daily) - 1{flt} GROUP BY 1, 2, 3, 4, 5, 6
"""
SQL_NC = CUT_CTE + """
SELECT t.product, t.vendor, t.media, t.cluster, t.icp, t.pain_point, sum(d.purchases) AS nc_purchases, sum(d.conv_value) AS nc_revenue
FROM ad_daily_upstack_nc d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.day BETWEEN %s AND %s{flt} GROUP BY 1, 2, 3, 4, 5, 6
"""
SQL_COMBOS = CUT_CTE + "SELECT DISTINCT product, cluster, icp, pain_point FROM tagged"


def _norm(v) -> str:
    return " ".join(str(v if v is not None else "").split()).lower()


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def parse_group(raw: str | None) -> list[str]:
    """group=icp,product -> ['icp', 'product'] (order kept, repeats dropped). At least one, only the four known cuts."""
    out: list[str] = []
    for part in str(raw or "product").split(","):
        p = part.strip().lower().replace("-", "_")
        if p == "pain":
            p = "pain_point"
        if not p:
            continue
        if p not in DIMS:
            raise q.ApiError(400, "group must be a comma list of: " + ", ".join(DIMS))
        if p not in out:
            out.append(p)
    if not out:
        raise q.ApiError(400, "group needs at least one of: " + ", ".join(DIMS))
    return out


def _day(raw: str | None, name: str) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise q.ApiError(400, f"{name} must be a date, YYYY-MM-DD") from None


def window(conn, raw_from: str | None, raw_to: str | None) -> tuple[dt.date, dt.date]:
    """The date window: both given -> as is; otherwise the last 30 loaded days (ending on the last day ad_daily holds)."""
    frm, to = _day(raw_from, "from"), _day(raw_to, "to")
    if to is None:
        last = conn.execute("SELECT max(day) AS d FROM ad_daily").fetchone()["d"]
        to = last or dt.date.today() - dt.timedelta(days=1)
    if frm is None:
        frm = to - dt.timedelta(days=29)
    if frm > to:
        raise q.ApiError(400, "from must be on or before to")
    if (to - frm).days > MAX_DAYS:
        raise q.ApiError(400, f"the window can be at most {MAX_DAYS} days")
    return frm, to


def status_of(r: dict) -> str:
    return r.get("status") or {"creative_in": "in_review"}.get(r.get("stage")) or r.get("stage") or "assigned"


def _label(dim: str, raw) -> str:
    if raw in (None, ""):
        return NONE
    return PRODUCT_NAMES.get(str(raw), str(raw)) if dim == "product" else str(raw).strip()


def _cut_key(dim_values: dict, group: list[str]) -> tuple:
    return tuple(_norm(dim_values.get(d)) for d in group)


def _ratio(a: float, b: float):
    return a / b if b else None


def _stats(s: dict) -> dict:
    spend, clicks, imps = s["spend"], s["clicks"], s["impressions"]
    return {"live_ads": s["live_ads"], "ads_spending": s["ads_spending"], "spend": spend, "nc_revenue": s["nc_revenue"], "nc_purchases": s["nc_purchases"], "nc_roas": _ratio(s["nc_revenue"], spend), "nc_cpa": _ratio(spend, s["nc_purchases"]),
            "meta_roas": _ratio(s["meta_revenue"], spend), "cpm": _ratio(spend * 1000, imps), "ctr": _ratio(clicks, imps), "cplc": _ratio(spend, clicks),
            "conv_rate": _ratio(s["nc_purchases"], clicks), "aov": _ratio(s["nc_revenue"], s["nc_purchases"])}


def _blank() -> dict:
    return {"stages": {k: 0 for k in STATUSES}, "concepts": 0, "ads": {"spend": 0.0, "clicks": 0.0, "impressions": 0.0, "meta_purchases": 0.0, "meta_revenue": 0.0, "nc_purchases": 0.0, "nc_revenue": 0.0, "live_ads": 0, "ads_spending": 0}}


def tag_filters(args: dict) -> dict:
    """cluster / icp / pain_point from the query string: each a '|' separated list of chosen values ('(none)' = untagged). Returns {dim: [normalised values]} for the dims that have a choice."""
    out = {}
    for d in ("cluster", "icp", "pain_point"):
        vals = [v for v in str(args.get(d) or "").split("|") if v.strip()]
        if vals:
            out[d] = ["" if v.strip() == NONE else _norm(v) for v in vals][:200]
    return out


def _tag_sql(tf: dict) -> tuple[str, list]:
    sql, params = [], []
    for d, vals in tf.items():
        sql.append(f" AND lower(btrim(COALESCE(t.{d}, ''))) = ANY(%s)")
        params.append(vals)
    return "".join(sql), params


def attach_priority(conn, out: dict, group: list[str], frm: dt.date, to: dt.date, tf: dict, products: list[str] | None, week, vendor) -> None:
    """Add row['priority'] = {score, tier, why, confidence, drivers, flags} to every row that has one (app/priority_score.py; spec docs/PRIORITY_SCORE.md). Product is never a scoring level: a row is scored on the
    other cuts of the grouping; when Product is one of the grouped cuts each product is scored on its own (its own prior), so an ICP row under Vita Bull is not diluted by Beef Liver. Rows with no scorable cut get none."""
    from . import priority_score as ps, priority_signals as sig
    sg = [d for d in group if d != "product"]
    if not sg:
        out["priority_note"] = "Add Market cluster, ICP or Pain point to Group by to score cuts (Product is a filter, not a level that is scored)."
        return
    names = {v: k for k, v in PRODUCT_NAMES.items()}
    per_product = "product" in group
    runs = [(slug, [slug]) for slug in sorted({names.get(r["labels"].get("product")) for r in out["rows"]} - {None})] if per_product else [(None, products or None)]
    for slug, prods in runs:
        filters = {**tf, **({"products": prods} if prods else {})}
        scored = ps.score_cuts(sig.signals(conn, sg, frm, to, filters, week=week, vendor=vendor)["rows"])
        by = {tuple(_norm(s["labels"].get(d)) for d in sg): s for s in scored}
        for r in out["rows"]:
            if per_product and names.get(r["labels"].get("product")) != slug:
                continue
            s = by.get(tuple(_norm(r["labels"].get(d)) for d in sg))
            if s:
                r["priority"] = {k: s[k] for k in ("score", "tier", "why", "confidence", "flags", "drivers")}


def cuts(conn, group: list[str], raw_from: str | None, raw_to: str | None, week=None, vendor=None, products: list[str] | None = None, tf: dict | None = None, score: bool = False) -> dict:
    """{group, from, to, rows: [{labels: {dim: text}, stages: {status: n}, concepts, stats: {...}}], totals, stages, dims, warning?}.
    products filters BOTH sides to those product slugs; week and vendor filter the pipeline side only (spend is not tied to a week or a vendor)."""
    frm, to = window(conn, raw_from, raw_to)
    acc: dict[tuple, dict] = {}
    labels: dict[tuple, dict] = {}

    def slot(dv: dict) -> dict:
        key = _cut_key(dv, group)
        if key not in acc:
            acc[key] = _blank()
            labels[key] = {d: _label(d, dv.get(d)) for d in group}
        return acc[key]

    want = set(products or [])
    tf = tf or {}
    tsql, tparams = _tag_sql(tf)
    keep = lambda r: all(_norm(r.get(d)) in vals for d, vals in tf.items())
    rows = pa.list_assignments(conn, week=week, vendor=vendor, include_closed=True)["rows"]
    for r in rows:
        st = status_of(r)
        if r.get("stage") == "dropped" or r.get("dropped_at") or st not in STATUSES or (want and r.get("product") not in want) or not keep(r):
            continue
        s = slot({**{d: r.get(d) for d in DIMS}, "media": {"video": "Video", "image": "Image"}.get(r.get("media_type")) or ("Video" if r.get("format_code") else None)})      # the ticket's own Image / video choice; when none, a creative format (F1 to F6) is a video style
        s["stages"][st] += 1
        s["concepts"] += 1

    warning = None
    try:
        for r in conn.execute(SQL_META.format(flt=tsql), (frm, to, *tparams)).fetchall():
            if want and r["product"] not in want:
                continue
            a = slot({d: r[d] for d in DIMS})["ads"]
            a["spend"] += _f(r["spend"]); a["clicks"] += _f(r["clicks"]); a["impressions"] += _f(r["impressions"])
            a["meta_purchases"] += _f(r["meta_purchases"]); a["meta_revenue"] += _f(r["meta_revenue"]); a["ads_spending"] += int(r["ads_spending"] or 0)
        for r in conn.execute(SQL_LIVE.format(flt=tsql), tparams).fetchall():
            if want and r["product"] not in want:
                continue
            slot({d: r[d] for d in DIMS})["ads"]["live_ads"] += int(r["live_ads"] or 0)
        for r in conn.execute(SQL_NC.format(flt=tsql), (frm, to, *tparams)).fetchall():
            if want and r["product"] not in want:
                continue
            a = slot({d: r[d] for d in DIMS})["ads"]
            a["nc_purchases"] += _f(r["nc_purchases"]); a["nc_revenue"] += _f(r["nc_revenue"])
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        warning = "Ad spend could not be read here (a table is missing), so only the pipeline counts are shown."

    out, tot = [], _blank()
    for key, s in acc.items():
        out.append({"labels": labels[key], "stages": s["stages"], "concepts": s["concepts"], "stats": _stats(s["ads"])})
        for k in STATUSES:
            tot["stages"][k] += s["stages"][k]
        tot["concepts"] += s["concepts"]
        for k, v in s["ads"].items():
            tot["ads"][k] += v
    combos: set[tuple] = set()
    try:
        for r in conn.execute(SQL_COMBOS).fetchall():
            combos.add((r["product"], r["cluster"] or "", r["icp"] or "", r["pain_point"] or ""))
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
    for r in rows:                                                    # concepts with no ad yet still offer their tags
        if r.get("product") and r.get("stage") != "dropped":
            combos.add((r["product"], r.get("cluster") or "", r.get("icp") or "", r.get("pain_point") or ""))
    out.sort(key=lambda x: (-x["stats"]["spend"], -x["concepts"], tuple(x["labels"].values())))
    result = {"group": group, "from": frm.isoformat(), "to": to.isoformat(), "rows": out, "totals": {"stages": tot["stages"], "concepts": tot["concepts"], "stats": _stats(tot["ads"])},
            "stages": [{"status": k, "label": pa.STATUS_LABELS[k]} for k in STATUSES], "dims": [{"key": d, "label": DIM_LABELS[d]} for d in DIMS],
            "basis": "Spend, clicks, impressions and Meta ROAS are Meta-reported; new-customer (NC) revenue, ROAS, cost per new customer, conversion rate and average order value are Upstack first orders (any-click, 30 day).",
            "combos": sorted([list(c) for c in combos]), "warning": warning}
    if score:
        try:
            attach_priority(conn, result, group, frm, to, tf, products, week, vendor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
            conn.rollback()
            result["priority_note"] = "The priority score could not be read here (a table is missing)."
    return result


# ---------------------------------------------------------------------------------------------------------------- the ads behind one cut (the popup on a row)
ADS_TOP = 5
ADS_WINDOW_MAX = 60


def _cut_filter(args: dict) -> tuple[str, list]:
    """WHERE fragment over `tagged t` for the cuts given in args (a dim missing = no filter; '(none)' = untagged). Values are compared lower-cased and trimmed, like the table's grouping."""
    sql, params = [], []
    for d in DIMS:
        v = args.get(d)
        if v in (None, ""):
            continue
        col = f"lower(btrim(COALESCE(t.{d}, '')))"
        if d == "product":
            sql.append("t.product = %s")
            params.append(str(v))
        elif v == NONE:
            sql.append(f"{col} = ''")
        else:
            sql.append(f"{col} = %s")
            params.append(_norm(v))
    return (" AND " + " AND ".join(sql)) if sql else "", params


WINDOW_ADS_SQL = CUT_CTE + """
SELECT t.family_key, COALESCE(cs.display, t.family_key) AS name, t.product, sum(d.spend) AS spend, sum(d.purchases) AS meta_purchases, sum(d.conv_value) AS meta_revenue,
       max(d.day) FILTER (WHERE d.spend > 0) AS last_spend_day
FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
LEFT JOIN creative_stage_moves cs ON cs.family_key = t.family_key
WHERE d.day BETWEEN %s AND %s {flt}
GROUP BY 1, 2, 3 HAVING sum(d.spend) > 0 ORDER BY sum(d.spend) DESC LIMIT %s
"""
NC_ADS_SQL = CUT_CTE + """
SELECT t.family_key, sum(d.purchases) AS nc_purchases, sum(d.conv_value) AS nc_revenue
FROM ad_daily_upstack_nc d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.day BETWEEN %s AND %s AND t.family_key = ANY(%s) GROUP BY 1
"""
TOP_ADS_SQL = CUT_CTE + """
SELECT t.family_key, COALESCE(cs.display, t.family_key) AS name, t.product, am.spend_launch AS spend, am.revenue_launch AS revenue, am.purchases_launch AS purchases
FROM tagged t JOIN ad_metrics am ON am.family_key = t.family_key AND am.is_first_row
LEFT JOIN creative_stage_moves cs ON cs.family_key = t.family_key
WHERE am.spend_launch > 0 {flt}
ORDER BY am.spend_launch DESC, t.family_key LIMIT %s
"""
LINKS_SQL = """
SELECT fc.family_key, fc.story_url, (SELECT tr.id FROM tracker_rows tr WHERE tr.family_key = fc.family_key ORDER BY tr.sheet_row NULLS LAST, tr.id LIMIT 1) AS tracker_row_id
FROM family_content fc WHERE fc.family_key = ANY(%s)
"""
HOOKS_SQL = """
SELECT DISTINCT ON (tr.family_key) tr.family_key, h.hook
FROM tracker_rows tr JOIN ad_hooks h ON h.tracker_row_id = tr.id
WHERE tr.family_key = ANY(%s) AND tr.deleted_at IS NULL AND btrim(COALESCE(h.hook, '')) <> ''
ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
"""
HOOK_MIN_SPEND = 250.0                       # an ad must have spent this much in the dates before its hook can be called a top performer
HOOKS_TOP = 5
LIVE_KEYS_SQL = "SELECT DISTINCT m.tracker_family_key AS k FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key WHERE d.spend > 0 AND d.day >= (SELECT max(day) FROM ad_daily) - 1 AND m.tracker_family_key = ANY(%s)"


def cut_ads(conn, cut: dict, raw_from: str | None, raw_to: str | None) -> dict:
    """{cut, from, to, window: [ad], top: [ad], window_total, window_shown}. window = the unique ads tagged to the cut that spent in the dates (top by spend, at most ADS_WINDOW_MAX), with Meta and Upstack
    new-customer numbers and a live flag; top = the ADS_TOP highest spenders of the cut since launch (Meta basis, whatever the dates). An ad = one tracker family, copies counted once."""
    frm, to = window(conn, raw_from, raw_to)
    flt, fp = _cut_filter(cut)
    out = {"cut": {d: cut.get(d) for d in DIMS if cut.get(d) not in (None, "")}, "from": frm.isoformat(), "to": to.isoformat(), "window": [], "top": [], "top_hooks": [], "window_shown": 0, "warning": None}
    try:
        wrows = conn.execute(WINDOW_ADS_SQL.format(flt=flt), (frm, to, *fp, ADS_WINDOW_MAX)).fetchall()
        trows = conn.execute(TOP_ADS_SQL.format(flt=flt), (*fp, ADS_TOP)).fetchall()
        keys = sorted({r["family_key"] for r in wrows} | {r["family_key"] for r in trows})
        nc = {r["family_key"]: r for r in conn.execute(NC_ADS_SQL, (frm, to, keys)).fetchall()} if keys else {}
        live = {r["k"] for r in conn.execute(LIVE_KEYS_SQL, (keys,)).fetchall()} if keys else set()
        links = {r["family_key"]: r for r in conn.execute(LINKS_SQL, (keys,)).fetchall()} if keys else {}
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        out["warning"] = "The ads behind this cut could not be read here (a table is missing)."
        return out
    try:
        hooks = {r["family_key"]: r["hook"].strip() for r in conn.execute(HOOKS_SQL, (keys,)).fetchall()} if keys else {}
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):          # hooks are optional (migration 010)
        conn.rollback()
        hooks = {}

    def link(k):
        l = links.get(k) or {}
        return {"story_url": l.get("story_url"), "ad_id": l.get("tracker_row_id")}

    for r in wrows:
        n = nc.get(r["family_key"]) or {}
        spend = _f(r["spend"])
        out["window"].append({"key": r["family_key"], "name": r["name"], "product": PRODUCT_NAMES.get(r["product"], r["product"]), "live": r["family_key"] in live, "spend": spend,
                              "nc_purchases": _f(n.get("nc_purchases")), "nc_revenue": _f(n.get("nc_revenue")), "nc_roas": _ratio(_f(n.get("nc_revenue")), spend), "nc_cpa": _ratio(spend, _f(n.get("nc_purchases"))),
                              "meta_roas": _ratio(_f(r["meta_revenue"]), spend), **link(r["family_key"]), "hook": hooks.get(r["family_key"])})
    for r in trows:
        spend = _f(r["spend"])
        out["top"].append({"key": r["family_key"], "name": r["name"], "product": PRODUCT_NAMES.get(r["product"], r["product"]), "live": r["family_key"] in live, "spend": spend, "revenue": _f(r["revenue"]),
                           "purchases": _f(r["purchases"]), "meta_roas": _ratio(_f(r["revenue"]), spend), **link(r["family_key"]), "hook": hooks.get(r["family_key"])})
    # top hooks: best new-customer return among the window's ads that spent at least HOOK_MIN_SPEND, one entry per distinct hook text
    seen: set[str] = set()
    cand = sorted((a for a in out["window"] if a["hook"] and a["spend"] >= HOOK_MIN_SPEND and a["nc_roas"] is not None), key=lambda a: (-a["nc_roas"], -a["spend"]))
    for a in cand:
        k = _norm(a["hook"])
        if k in seen:
            continue
        seen.add(k)
        out["top_hooks"].append({key: a[key] for key in ("hook", "name", "spend", "nc_roas", "nc_cpa", "live", "ad_id", "story_url")})
        if len(out["top_hooks"]) >= HOOKS_TOP:
            break
    out["window_shown"] = len(out["window"])
    return out
