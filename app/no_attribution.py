"""Products without attribution (/api/no-attribution/products): per product, Meta spend and the business's actual new-customer revenue.

Neither side is attributed. Spend is Meta spend by product (proj_feed_meta_product: classified by campaign name, then ad set name, then ad name, else Other; it adds up to the
whole Meta account, unlike the tracker-based Products table, which leaves out ads that are not on the tracker). Revenue is the first order of every new customer, from all channels
(proj_feed_nc_product: ExpandFi first Shopify orders split across product lines; tax in, refunds not out). The window is cut to the days both feeds hold, so a ratio never divides
a day of spend by a day with no orders yet."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any

from . import queries as q

# feed product name -> (key, display name, product page slug); anything else the feeds carry rolls into Other
PRODUCTS = [("VitaBull", "vita-bull", "Vita Bull", "vita-bull"), ("Beef Liver", "beef-liver", "Beef Liver", "beef-liver"), ("Boneflex", "boneflex", "Boneflex", "boneflex")]
OTHER = ("Other", "other", "Other products", None)
_KEY = {name: (key, label, slug) for name, key, label, slug in [*PRODUCTS, OTHER]}

# Meta spend per day and product: the app's own days win, the sheet's days only where the app has none (same rule as app/trends.py)
SPEND_SQL = """SELECT h.product, sum(h.spend) AS spend, sum(h.link_clicks) AS link_clicks FROM proj_feed_meta_product h
               WHERE h.day BETWEEN %(a)s AND %(b)s AND (h.source = 'app' OR NOT EXISTS (SELECT 1 FROM proj_feed_meta_product x WHERE x.day = h.day AND x.source = 'app'))
               GROUP BY 1"""
NC_SQL = "SELECT product, sum(nc_rev) AS nc_rev, sum(nc_orders) AS nc_orders FROM proj_feed_nc_product WHERE day BETWEEN %(a)s AND %(b)s GROUP BY 1"


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def span_meta(conn) -> q.DataMeta | None:
    """First and last day both feeds hold (the last one never past Meta's last loaded day, which is yesterday Pacific). None while a feed is empty."""
    r = conn.execute("""SELECT (SELECT min(day) FROM proj_feed_nc_product) AS nc_lo, (SELECT max(day) FROM proj_feed_nc_product) AS nc_hi,
                               (SELECT min(day) FROM proj_feed_meta_product) AS sp_lo, (SELECT max(day) FROM proj_feed_meta_product) AS sp_hi""").fetchone()
    if not (r["nc_lo"] and r["sp_lo"]):
        return None
    meta = q.load_meta(conn)
    lo, hi = max(r["nc_lo"], r["sp_lo"]), min(r["nc_hi"], r["sp_hi"], meta.data_through)
    if hi < lo:
        lo = hi
    return q.DataMeta(daily_start=lo, data_through=hi, impressions_from=lo)


def parse(args, ameta: q.DataMeta) -> q.WindowSpec:
    """window / from / to only (no stage, no Active only, no Numbers from, no comparison); since launch means every day both feeds hold."""
    return q.parse_window({k: args[k] for k in ("window", "from", "to") if k in args and args[k] is not None}, ameta)


def window_info(w: q.WindowSpec, ameta: q.DataMeta) -> dict:
    info = w.info(ameta)
    info["channel"] = "meta"
    info["source"] = {"key": "actual", "basis": None, "label": "No attribution", "labels": {"purchases": "New customers", "revenue": "New-customer revenue", "roas": "New-customer ROAS", "cpa": "New-customer CPA"},
                      "model": None, "channel": "meta", "summary": "", "note": None, "history_from": ameta.daily_start.isoformat(), "through": ameta.data_through.isoformat(), "built_at": None}
    info["stage"], info["stages"], info["stage_note"] = "all", list(q.STAGES), ""
    if w.is_launch:
        info["label"] = f"All days held · {q._fmt_day(ameta.daily_start, True)} – {q._fmt_day(ameta.data_through, True)}"
    return info


def empty_payload() -> dict:
    return {"loaded": False, "rows": [], "total": None, "window_info": None, "compare_info": None, "data_through": None,
            "message": "The product spend and new-customer feeds have not loaded yet. They refill every night."}


def _metrics(spend: float, clicks: float, rev: float, orders: float, tot_spend: float, tot_rev: float) -> dict:
    return {"spend": spend, "spend_share": _div(spend, tot_spend), "link_clicks": clicks, "cpc": _div(spend, clicks),
            "nc_revenue": rev, "nc_revenue_share": _div(rev, tot_rev), "nc_orders": orders,
            "nc_roas": _div(rev, spend), "nc_cpa": _div(spend, orders), "aov": _div(rev, orders), "conv_rate": _div(orders, clicks)}


def products(conn, ameta: q.DataMeta, w: q.WindowSpec) -> dict:
    """One row per product (the three tracked products, then Other) and the Total; ratios come from sums."""
    acc = {name: dict(spend=0.0, clicks=0.0, rev=0.0, orders=0.0) for name, *_ in [*PRODUCTS, OTHER]}
    if not w.is_empty:
        a, b = w.span(ameta)
        for r in conn.execute(SPEND_SQL, {"a": a, "b": b}).fetchall():
            t = acc[r["product"] if r["product"] in acc else "Other"]
            t["spend"] += _f(r["spend"]); t["clicks"] += _f(r["link_clicks"])
        for r in conn.execute(NC_SQL, {"a": a, "b": b}).fetchall():
            t = acc[r["product"] if r["product"] in acc else "Other"]
            t["rev"] += _f(r["nc_rev"]); t["orders"] += _f(r["nc_orders"])
    tot = {k: sum(v[k] for v in acc.values()) for k in ("spend", "clicks", "rev", "orders")}
    rows = []
    for name, key, label, slug in [*PRODUCTS, OTHER]:
        v = acc[name]
        rows.append({"key": key, "name": label, "slug": slug, **_metrics(v["spend"], v["clicks"], v["rev"], v["orders"], tot["spend"], tot["rev"])})
    total = _metrics(tot["spend"], tot["clicks"], tot["rev"], tot["orders"], tot["spend"], tot["rev"])
    # tie-out: the products' revenue against the business's total new-customer revenue (Upstack order-level) over the same days
    check = None
    if not w.is_empty:
        a, b = w.span(ameta)
        od = conn.execute("SELECT sum(nc_rev) AS s, count(*) AS n FROM upstack_orders_daily WHERE day BETWEEN %s AND %s", (a, b)).fetchone()
        if od and od["n"]:
            check = {"orders_daily_nc_revenue": _f(od["s"]), "products_nc_revenue": tot["rev"], "diff_pct": _div(tot["rev"] - _f(od["s"]), _f(od["s"]))}
    return {"loaded": True, "rows": rows, "total": total, "check": check, "data_through": ameta.data_through.isoformat(), "data_from": ameta.daily_start.isoformat()}
