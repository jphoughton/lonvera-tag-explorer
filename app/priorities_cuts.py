"""Creative Pipeline "By cut" view (/api/priorities/cuts): the production assignments counted by status AND the Meta spend and return behind the same cuts, for any date window.

A cut is any ordered subset of product, market cluster, ICP (ideal customer profile), pain point and vendor (the assignment vendor on the pipeline side, the tracker Vendor column on the ad side). Pipeline side: the rows of priorities_assign.list_assignments, counted by board status.
Ad side (unique live ads = tracker families that spent in the last two loaded days; ads with spend = families that spent in the window): ad_daily (Meta spend, clicks, impressions, Meta-reported purchases) and ad_daily_upstack_nc (Upstack new customers and new-customer revenue, the NC basis) summed over the window and joined
Meta family -> tracker family -> first tracker row -> current tag (Communication Overview line tag, else the tracker's own columns). Nothing is written. Ratios come from sums, never an average.
"""
from __future__ import annotations

import datetime as dt

import psycopg

from . import priorities_assign as pa, queries as q

DIMS = ("product", "cluster", "icp", "pain_point", "vendor")
DIM_LABELS = {"product": "Product", "cluster": "Market cluster", "icp": "ICP", "pain_point": "Pain point", "vendor": "Vendor"}
PRODUCT_NAMES = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vita Belle"}
STATUSES = ("assigned", "brief_sent", "in_production", "in_review", "ready", "launched")
NONE = "(none)"
MAX_DAYS = 800

# tag per tracker family: the linemap tag when there is one, else the tracker's own columns. first row of the family only, so a family counts once.
CUT_CTE = """
WITH fam AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, tr.id, p.slug AS product, tr.vendor, tr.tracker_cluster, tr.tracker_icp, tr.tracker_pain
  FROM tracker_rows tr JOIN products p ON p.id = tr.product_id
  ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
), tagged AS (
  SELECT f.family_key, f.product, NULLIF(btrim(f.vendor), '') AS vendor,
         COALESCE(NULLIF(btrim(g.cluster), ''), NULLIF(btrim(f.tracker_cluster), '')) AS cluster,
         COALESCE(NULLIF(btrim(g.icp), ''), NULLIF(btrim(f.tracker_icp), '')) AS icp,
         COALESCE(NULLIF(btrim(g.pain_point), ''), NULLIF(btrim(f.tracker_pain), '')) AS pain_point
  FROM fam f LEFT JOIN ad_tags g ON g.tracker_row_id = f.id AND g.tag_set = 'linemap' AND g.is_current
)
"""

SQL_META = CUT_CTE + """
SELECT t.product, t.vendor, t.cluster, t.icp, t.pain_point, sum(d.spend) AS spend, sum(d.link_clicks) AS clicks, sum(d.impressions) AS impressions, sum(d.purchases) AS meta_purchases, sum(d.conv_value) AS meta_revenue,
       count(DISTINCT t.family_key) FILTER (WHERE d.spend > 0) AS ads_spending
FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.day BETWEEN %s AND %s GROUP BY 1, 2, 3, 4, 5
"""
# unique live ads: tracker families (one per ad however many copies it has) that spent in the last two loaded days. Not tied to the date window: live is a now-state.
SQL_LIVE = CUT_CTE + """
SELECT t.product, t.vendor, t.cluster, t.icp, t.pain_point, count(DISTINCT t.family_key) AS live_ads
FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.spend > 0 AND d.day >= (SELECT max(day) FROM ad_daily) - 1 GROUP BY 1, 2, 3, 4, 5
"""
SQL_NC = CUT_CTE + """
SELECT t.product, t.vendor, t.cluster, t.icp, t.pain_point, sum(d.purchases) AS nc_purchases, sum(d.conv_value) AS nc_revenue
FROM ad_daily_upstack_nc d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key JOIN tagged t ON t.family_key = m.tracker_family_key
WHERE d.day BETWEEN %s AND %s GROUP BY 1, 2, 3, 4, 5
"""


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


def cuts(conn, group: list[str], raw_from: str | None, raw_to: str | None, week=None, vendor=None, products: list[str] | None = None) -> dict:
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
    rows = pa.list_assignments(conn, week=week, vendor=vendor, include_closed=True)["rows"]
    for r in rows:
        st = status_of(r)
        if r.get("stage") == "dropped" or r.get("dropped_at") or st not in STATUSES or (want and r.get("product") not in want):
            continue
        s = slot({d: r.get(d) for d in DIMS})
        s["stages"][st] += 1
        s["concepts"] += 1

    warning = None
    try:
        for r in conn.execute(SQL_META, (frm, to)).fetchall():
            if want and r["product"] not in want:
                continue
            a = slot({d: r[d] for d in DIMS})["ads"]
            a["spend"] += _f(r["spend"]); a["clicks"] += _f(r["clicks"]); a["impressions"] += _f(r["impressions"])
            a["meta_purchases"] += _f(r["meta_purchases"]); a["meta_revenue"] += _f(r["meta_revenue"]); a["ads_spending"] += int(r["ads_spending"] or 0)
        for r in conn.execute(SQL_LIVE).fetchall():
            if want and r["product"] not in want:
                continue
            slot({d: r[d] for d in DIMS})["ads"]["live_ads"] += int(r["live_ads"] or 0)
        for r in conn.execute(SQL_NC, (frm, to)).fetchall():
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
    out.sort(key=lambda x: (-x["stats"]["spend"], -x["concepts"], tuple(x["labels"].values())))
    return {"group": group, "from": frm.isoformat(), "to": to.isoformat(), "rows": out, "totals": {"stages": tot["stages"], "concepts": tot["concepts"], "stats": _stats(tot["ads"])},
            "stages": [{"status": k, "label": pa.STATUS_LABELS[k]} for k in STATUSES], "dims": [{"key": d, "label": DIM_LABELS[d]} for d in DIMS],
            "basis": "Spend, clicks, impressions and Meta ROAS are Meta-reported; new-customer (NC) revenue, ROAS, cost per new customer, conversion rate and average order value are Upstack first orders (any-click, 30 day).",
            "warning": warning}
