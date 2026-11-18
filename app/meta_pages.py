"""By page (Marketing metrics, platform Meta): the Lonvera Facebook Page vs every other page (handle) the ads run from, for any date window, product and stage.

Numbers come from analytics.meta_ad_daily joined to public.meta_ad_pages / public.meta_pages (feed etl.meta_pages, docs/META_PAGES.md). Meta-reported columns (purchases, purchase value,
link clicks, impressions, spend) and, beside them, the Upstack new-customer columns the rest of the app uses for NC ROAS. Ratios are ratios of sums, never an average of daily ratios; a figure
with nothing behind it reads blank. Creator / partnership ads that run from an Instagram account (no Page) are the '(Instagram-only handle)' row and ads with no identity the '(unknown page)' row, so the Total always equals total Meta ad-level spend.

Shape (same as app/countries.py): `build()` is a pure function of a *source*; `DbSource` reads Postgres and tests feed the same builder from made-up rows.
Page mix is confounded by product, stage and spend: the payload carries that note and the page prints it. No causal claims.
"""
from __future__ import annotations

import datetime as dt

from . import queries as q

LIKE_FOR_LIKE = ("Page mix is confounded by product, stage and spend: compare pages like for like (same product, same stage, same window) and read differences as "
                 "consistent with a page effect, not as caused by it.")
BASIS = "Spend, impressions, link clicks, purchases and purchase value are Meta-reported (purchases count returning customers). NC columns are Upstack-attributed first orders."
UNKNOWN = "(unknown page)"
INSTAGRAM = "(Instagram-only handle)"


def _f(x):
    return None if x is None else float(x)


def _div(a, b, nd):
    if a is None or b is None or float(b) == 0:
        return None
    return round(float(a) / float(b), nd)


def _iso(d):
    return d.isoformat() if d else None


class DbSource:
    """pages(lo, hi, products, stages) -> one dict per page; product_list(lo, hi) -> [{slug, name?, spend}]."""

    def __init__(self, conn):
        self.conn = conn

    def pages(self, lo: dt.date, hi: dt.date, product: str | None, stages: list[str]) -> list[dict]:
        where, args = ["d.day BETWEEN %s AND %s"], [lo, hi]
        if product:
            where.append("d.product_slug = %s")
            args.append(product)
        named = [s for s in stages if s != "unclassified"]
        cond = []
        if named:
            cond.append("d.stage = ANY(%s)")
            args.append(named)
        if "unclassified" in stages:
            cond.append("d.stage IS NULL")
        where.append("(" + " OR ".join(cond) + ")" if cond else "false")
        rows = self.conn.execute(f"""
            SELECT p.page_id,
                   CASE WHEN p.page_id IS NOT NULL THEN coalesce(g.name, 'Page ' || p.page_id) WHEN p.source = 'instagram_only' THEN '{INSTAGRAM}' ELSE '{UNKNOWN}' END AS page_name,
                   coalesce(g.is_lonvera, false) AS is_lonvera,
                   count(DISTINCT d.ad_id) FILTER (WHERE d.spend > 0) AS ads,
                   sum(d.spend) AS spend, sum(d.impressions) AS impressions, sum(d.link_clicks) AS link_clicks,
                   sum(d.meta_purchases) AS purchases, sum(d.meta_revenue) AS purchase_value,
                   sum(d.spend) FILTER (WHERE d.upstack_new_customers IS NOT NULL) AS up_spend,
                   sum(d.upstack_new_customers) AS nc, sum(d.upstack_new_customer_revenue) AS nc_revenue
            FROM analytics.meta_ad_daily d
            LEFT JOIN public.meta_ad_pages p ON p.ad_id = d.ad_id
            LEFT JOIN public.meta_pages g ON g.page_id = p.page_id
            WHERE {' AND '.join(where)}
            GROUP BY p.page_id, g.name, g.is_lonvera, (p.source = 'instagram_only')""", args).fetchall()
        return [dict(r) for r in rows]

    def products(self, lo: dt.date, hi: dt.date) -> list[dict]:
        rows = self.conn.execute("""SELECT d.product_slug AS slug, max(pr.name) AS name, sum(d.spend) AS spend FROM analytics.meta_ad_daily d
                                    LEFT JOIN public.products pr ON pr.slug = d.product_slug WHERE d.day BETWEEN %s AND %s AND d.product_slug IS NOT NULL
                                    GROUP BY 1 HAVING sum(d.spend) > 0 ORDER BY 3 DESC""", (lo, hi)).fetchall()
        return [{"slug": r["slug"], "name": r["name"] or r["slug"], "spend": _f(r["spend"])} for r in rows]

    def coverage(self) -> dict:
        r = self.conn.execute("SELECT count(*) AS ads, count(page_id) AS with_page, max(last_seen) AS last_seen, min(first_seen) AS first_seen FROM public.meta_ad_pages").fetchone()
        return dict(r)


def metrics(r: dict, total_spend) -> dict:
    spend, imps, clicks = _f(r.get("spend")), _f(r.get("impressions")), _f(r.get("link_clicks"))
    purch, pval = _f(r.get("purchases")), _f(r.get("purchase_value"))
    up_spend, nc, nc_rev = _f(r.get("up_spend")), _f(r.get("nc")), _f(r.get("nc_revenue"))
    return {"ads": int(r.get("ads") or 0), "spend": None if spend is None else round(spend, 2), "share": _div(spend, total_spend, 4),
            "nc_roas": _div(nc_rev, up_spend, 2), "nc_cpa": _div(up_spend, nc, 2), "new_customers": None if nc is None else int(nc),
            "meta_roas": _div(pval, spend, 2), "cost_per_purchase": _div(spend, purch, 2), "purchases": None if purch is None else int(purch),
            "aov": _div(pval, purch, 2), "cpm": _div(None if spend is None else spend * 1000, imps, 2), "ctr": _div(clicks, imps, 4),
            "cpc": _div(spend, clicks, 2), "conv_rate": _div(purch, clicks, 4),
            "impressions": None if imps is None else int(imps), "link_clicks": None if clicks is None else int(clicks),
            "purchase_value": None if pval is None else round(pval, 2)}


def _sum(rows: list[dict]) -> dict:
    keys = ("spend", "impressions", "link_clicks", "purchases", "purchase_value", "up_spend", "nc", "nc_revenue", "ads")
    out = {}
    for k in keys:
        vals = [_f(r.get(k)) for r in rows if r.get(k) is not None]
        out[k] = sum(vals) if vals else None
    return out


def build(src, w: q.WindowSpec, meta: q.DataMeta, product: str | None = None) -> dict:
    lo, hi = w.span(meta)
    stages = q.stage_list(w.stage)
    raw = src.pages(lo, hi, product, stages)
    cov = src.coverage()
    info = w.info(meta)
    base = {"channel": "meta", "view": "pages", "window": info, "window_info": info, "compare_info": None, "product": product, "products": src.products(lo, hi),
            "range": {"from": _iso(lo), "to": _iso(hi), "days": (hi - lo).days + 1}, "like_for_like": LIKE_FOR_LIKE, "basis": BASIS,
            "coverage": {"ads": cov["ads"], "with_page": cov["with_page"], "first_seen": _iso(cov["first_seen"]), "last_seen": _iso(cov["last_seen"])}}
    if not cov["ads"]:
        return {**base, "loaded": False, "message": "Page identity has not loaded yet", "rows": [], "groups": [], "total": None}
    total_spend = sum(float(r["spend"] or 0) for r in raw) if raw else None
    rows = [{"page_id": r["page_id"], "page": r["page_name"], "is_lonvera": bool(r["is_lonvera"]), "unknown": r["page_id"] is None, "instagram_only": r["page_name"] == INSTAGRAM, **metrics(r, total_spend)} for r in raw]
    rows.sort(key=lambda r: (-(r["spend"] or 0.0), r["page"]))
    lon = [r for r in raw if r["is_lonvera"]]
    oth = [r for r in raw if not r["is_lonvera"] and r["page_id"] is not None]
    ig = [r for r in raw if r["page_id"] is None and r["page_name"] == INSTAGRAM]
    unk = [r for r in raw if r["page_id"] is None and r["page_name"] != INSTAGRAM]
    groups = []
    for key, label, part in (("lonvera", "Lonvera page", lon), ("other", "All other pages", oth), ("instagram", INSTAGRAM, ig), ("unknown", UNKNOWN, unk)):
        if part:
            groups.append({"key": key, "page": label, "pages": len(part), **metrics(_sum(part), total_spend)})
    total = {"page": "Total", **metrics(_sum(raw), total_spend)} if raw else None
    return {**base, "loaded": True, "rows": rows, "groups": groups, "total": total, "count": len(rows)}
