"""Read the pacing feeds (proj_feed_* tables, Meta aggregates built from ad_daily) into a per-day frame of base measures.

A Frame holds, for each day, a defaultdict(float) of additive base measures (names in catalog.py: o_* Shopify orders, a_* Amazon, g_*/tt_*/pl_* ad
platforms, at_* attribution, t_* traffic, m_* / mg_* Meta, ps_*/pn_* by product, ac_* any-click, b_* bids) and, for the non-additive reach
measures, the feed rows pulled at each grain. Reads only; call with a read-only connection.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .catalog import PRODUCT_FEED
from .periods import iso_week_key, iter_days, month_key

SLUG_OF_PRODUCT = {v: k for k, v in PRODUCT_FEED.items()}                     # 'VitaBull' -> 'vitabull'
GROUP_OF_PRODUCT = {"VitaBull": "bull", "Beef Liver": "liver", "Boneflex": "bone"}


def rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [d.name for d in cur.description]
    out = []
    for r in cur.fetchall():
        out.append(dict(r) if isinstance(r, dict) else dict(zip(names, r)))
    return out


@dataclass
class Frame:
    start: date
    end: date
    days: dict[date, dict] = field(default_factory=dict)
    reach: dict[tuple[str, str], dict] = field(default_factory=dict)             # (grain, key) -> impressions / reach / uoc
    preach: dict[tuple[str, str, str], float] = field(default_factory=dict)      # (grain, key, product slug) -> unique outbound clicks
    meta_source: dict[date, str] = field(default_factory=dict)                   # day -> 'app' | 'sheet'
    last_day: dict[str, date | None] = field(default_factory=dict)               # feed -> last day with data in the loaded range

    def day(self, d: date) -> dict:
        return self.days[d]


def _f(x) -> float:
    return float(x or 0)


def load_frame(conn, start: date, end: date) -> Frame:
    fr = Frame(start, end)
    for d in iter_days(start, end):
        fr.days[d] = defaultdict(float)
    D = fr.days

    def seen(feed: str, d: date) -> None:
        if fr.last_day.get(feed) is None or d > fr.last_day[feed]:
            fr.last_day[feed] = d

    for r in rows(conn, "SELECT * FROM proj_feed_orders WHERE day BETWEEN %s AND %s", (start, end)):
        b = D[r["day"]]
        b["o_nc_rev"], b["o_rc_rev"], b["o_nc_orders"] = _f(r["nc_rev"]), _f(r["rc_rev"]), _f(r["nc_orders"])
        b["o_total_rev"], b["o_total_orders"], b["o_tax"], b["o_refunds"] = _f(r["total_rev"]), _f(r["total_orders"]), _f(r["tax"]), _f(r["refunds"])
        seen("orders", r["day"])
    for r in rows(conn, "SELECT * FROM proj_feed_amazon WHERE day BETWEEN %s AND %s", (start, end)):
        b = D[r["day"]]
        for k, c in (("a_orders", "orders"), ("a_units", "units"), ("a_revenue", "revenue"), ("a_nc_orders", "nc_orders"), ("a_nc_rev", "nc_rev"),
                     ("a_ad_spend", "ad_spend"), ("a_attr", "attributed_sales"), ("a_clicks", "clicks"), ("a_impr", "impressions")):
            b[k] = _f(r[c])
        seen("amazon", r["day"])
    for r in rows(conn, "SELECT * FROM proj_feed_platform WHERE day BETWEEN %s AND %s", (start, end)):
        b, p = D[r["day"]], r["platform"]
        seen("platform", r["day"])
        if p != "Meta (Facebook/Instagram)":
            b["pl_nonmeta_spend"] += _f(r["spend"])
        if p == "Google Ads":
            b["g_spend"] += _f(r["spend"])
            b["g_purchases"] += _f(r["purchases"])
            b["g_value"] += _f(r["purchase_value"])
            b["g_clicks"] += _f(r["clicks"])
        elif p == "TikTok Ads":
            b["tt_spend"] += _f(r["spend"])
    for r in rows(conn, "SELECT * FROM proj_feed_attribution WHERE day BETWEEN %s AND %s", (start, end)):
        b, s = D[r["day"]], r["ad_source"]
        seen("attribution", r["day"])
        if s == "Meta Ads":
            b["at_meta_nc_orders"], b["at_meta_nc_rev"] = _f(r["nc_orders"]), _f(r["nc_rev"])
        elif s == "Google Ads":
            b["at_google_nc_orders"], b["at_google_nc_rev"] = _f(r["nc_orders"]), _f(r["nc_rev"])
    for r in rows(conn, "SELECT * FROM proj_feed_traffic WHERE day BETWEEN %s AND %s", (start, end)):
        D[r["day"]]["t_new_visitors"] = _f(r["new_visitors"])
        seen("traffic", r["day"])
    for r in rows(conn, "SELECT * FROM proj_feed_nc_product WHERE day BETWEEN %s AND %s", (start, end)):
        slug = SLUG_OF_PRODUCT.get(r["product"])
        seen("nc_product", r["day"])
        if slug:
            D[r["day"]][f"pn_rev_{slug}"] += _f(r["nc_rev"])
            D[r["day"]][f"pn_ord_{slug}"] += _f(r["nc_orders"])
    for r in rows(conn, "SELECT * FROM proj_feed_anyclick WHERE day BETWEEN %s AND %s", (start, end)):
        b = D[r["day"]]
        seen("anyclick", r["day"])
        b["ac_scaling" if r["stage"] == "Scaling" else "ac_testing"] += _f(r["nc"])
        b["ac_total"] += _f(r["nc"])
    for r in rows(conn, "SELECT * FROM proj_feed_bids WHERE day BETWEEN %s AND %s", (start, end)):
        b = D[r["day"]]
        b["b_spend"], b["b_capped"], b["b_spend_x_cap"] = _f(r["spend"]), _f(r["capped_spend"]), _f(r["spend_x_cap"])
        b["b_floor"], b["b_spend_x_floor"] = _f(r["floor_spend"]), _f(r["spend_x_floor"])
        seen("bids", r["day"])

    # Meta: a day is read from the app's ad_daily aggregate when it has rows, else from the sheet history
    sql_meta = ("SELECT d.* FROM proj_feed_meta_day d WHERE d.day BETWEEN %s AND %s AND (d.source = 'app' OR NOT EXISTS "
                "(SELECT 1 FROM proj_feed_meta_day a WHERE a.day = d.day AND a.source = 'app'))")
    for r in rows(conn, sql_meta, (start, end)):
        b, d = D[r["day"]], r["day"]
        fr.meta_source[d] = r["source"]
        seen("meta", d)
        sp, cl, pu, va = _f(r["spend"]), _f(r["link_clicks"]), _f(r["purchases"]), _f(r["purchase_value"])
        b["m_spend"] += sp
        b["m_link_clicks"] += cl
        b["m_purchases"] += pu
        b["m_value"] += va
        stage_slug = "test" if r["stage"] == "Testing" else "scale"
        b["m_testing" if r["stage"] == "Testing" else "m_scaling"] += sp
        groups = ["all", stage_slug]
        if r["product"] in GROUP_OF_PRODUCT:
            groups.append(GROUP_OF_PRODUCT[r["product"]])
        for g in groups:
            b[f"mg_{g}_spend"] += sp
            b[f"mg_{g}_clicks"] += cl
            b[f"mg_{g}_purchases"] += pu
            b[f"mg_{g}_value"] += va
    sql_prod = ("SELECT d.* FROM proj_feed_meta_product d WHERE d.day BETWEEN %s AND %s AND (d.source = 'app' OR NOT EXISTS "
                "(SELECT 1 FROM proj_feed_meta_product a WHERE a.day = d.day AND a.source = 'app'))")
    for r in rows(conn, sql_prod, (start, end)):
        slug = SLUG_OF_PRODUCT.get(r["product"])
        if slug:
            D[r["day"]][f"ps_{slug}"] += _f(r["spend"])

    for r in rows(conn, "SELECT * FROM proj_feed_meta_reach WHERE period_end >= %s AND period_start <= %s", (start, end)):
        fr.reach[(r["grain"], r["period_key"])] = {"r_impressions": _f(r["impressions"]), "r_reach": _f(r["reach"]), "r_uoc": _f(r["unique_outbound_clicks"])}
        if r["grain"] == "day":
            seen("meta_reach", r["period_start"])
    for r in rows(conn, "SELECT * FROM proj_feed_product_reach WHERE period_end >= %s AND period_start <= %s", (start, end)):
        slug = SLUG_OF_PRODUCT.get(r["product"])
        if slug:
            fr.preach[(r["grain"], r["period_key"], slug)] = _f(r["unique_outbound_clicks"])
    return fr


def period_key_for(grain: str, d: date) -> str:
    return d.isoformat() if grain == "day" else iso_week_key(d) if grain == "week" else month_key(d)


def apply_reach(bases: dict, fr: Frame, grain: str, key: str) -> None:
    """Fill the non-additive reach bases of a period from the feed rows pulled at that grain (None when the feed has no row)."""
    row = fr.reach.get((grain, key))
    if row is None:
        bases["r_impressions"] = bases["r_reach"] = bases["r_uoc"] = None
    else:
        bases.update(row)
    for slug in PRODUCT_FEED:
        v = fr.preach.get((grain, key, slug))
        bases[f"pu_{slug}"] = v if v else None
