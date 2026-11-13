"""Loads the day panel the daily story is computed from: account totals per day, one row per ad family x campaign x day (with the family's tags, format, vendor,
hook and age), ad-set rows, and the small side sources. Read-only SELECTs on the analytics views; pure Python after that, so a backfill reads the data once.

Family identity: coalesce(meta_ads.tracker_family_key, meta_ads.family_key, ad_id), the key the Creative Tracker uses (count(distinct family), never ad_id: copies of
one creative are one family). Tags come from analytics.creative_tracker's first row of each family (the current line-map tag, else the tracker's own columns).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

START = dt.date(2026, 5, 6)                       # Meta ad-level history starts here
SHOPIFY_START = dt.date(2026, 7, 16)
BREAKDOWN_START = dt.date(2026, 10, 12)
LP_HISTORY_START = dt.date(2026, 11, 11)
PRODUCT_LABEL = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vita Belle", "vital-flow": "Vital Flow"}
FUNNEL_SUMS = ("spend", "impressions", "clicks", "lpv", "atc", "ic", "nc", "nc_rev", "mp", "mrev", "n_ads")


def structure_of(campaign_name: str, stage: str) -> str:
    """'Scaling / tROAS / 7-day click' from the campaign name (tROAS vs CostCap, 7DC vs 1DC); the stage column names the first part."""
    n = (campaign_name or "").lower()
    bid = "tROAS" if "troas" in n else "Cost cap" if "costcap" in n else "Highest volume" if "highest" in n else "Other bid"
    win = "7-day click" if "7dc" in n else "1-day click" if "1dc" in n else "window not in name"
    return f"{(stage or 'unclassified').capitalize()} / {bid} / {win}"


def age_bucket(age_days: int | None) -> str:
    if age_days is None:
        return "age unknown"
    for hi, lab in ((3, "0-3 days"), (7, "4-7 days"), (14, "8-14 days"), (30, "15-30 days"), (90, "31-90 days")):
        if age_days <= hi:
            return lab
    return "91+ days"


def _num(x):
    return float(x) if x is not None else 0.0


class Panel:
    def __init__(self):
        self.acct: dict[dt.date, dict] = {}              # day -> account sums
        self.rows: dict[dt.date, list[dict]] = defaultdict(list)   # day -> family x campaign rows
        self.fam: dict[str, dict] = {}                   # fk -> tags / format / vendor / hook / name
        self.fam_first: dict[str, dt.date] = {}          # fk -> first day of Meta spend (None-safe)
        self.fam_created: dict[str, dt.date] = {}
        self.adsets: dict[dt.date, list[dict]] = defaultdict(list)
        self.adset_first: dict[str, dt.date] = {}
        self.adset_name: dict[str, str] = {}
        self.place: dict[dt.date, dict] = {}             # day -> {facebook: {...}, instagram: {...}}
        self.klaviyo: dict[dt.date, dict] = {}           # day -> {(source, channel): sums}
        self.klaviyo_top: dict[dt.date, list[dict]] = {}  # day -> biggest sends of the day
        self.google_type: dict[dt.date, dict] = {}
        self.amazon: dict[dt.date, dict] = {}
        self.shop: dict[dt.date, dict] = {}
        self.traffic: dict[dt.date, float] = {}
        self.lp_changes: dict[dt.date, list[dict]] = defaultdict(list)
        self.stage_moves: dict[dt.date, int] = defaultdict(int)
        self.coverage: dict[str, dict] = {}              # source -> {first_day, last_day}
        self.has_ic = False
        self.feed_events: dict[dt.date, list[dict]] = defaultdict(list)
        self.feed_name: str | None = None

    # ---- convenience ----------------------------------------------------------------------------------------------------
    def days(self):
        return sorted(self.acct)

    def age_days(self, fk: str, day: dt.date) -> int | None:
        f = self.fam_first.get(fk)
        if f is None:
            return None
        if f <= START:                                    # first loaded day: the real launch is earlier; use the ad's created date when known
            c = self.fam_created.get(fk)
            return (day - c).days if c else None
        return (day - f).days


def _dicts(cur) -> list[dict]:
    return list(cur.fetchall())


def _fetch(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else r for r in cur.fetchall()]


def _ic_column(conn) -> str | None:
    rows = _fetch(conn, "SELECT column_name FROM information_schema.columns WHERE table_schema='analytics' AND table_name='meta_ad_daily' AND column_name ILIKE '%%checkout%%' ORDER BY column_name")
    names = [r["column_name"] for r in rows]
    for pref in ("initiated_checkouts", "initiate_checkouts", "initiated_checkout", "checkouts_initiated"):
        if pref in names:
            return pref
    return names[0] if names else None


def _exists(conn, name: str) -> bool:
    return bool(_fetch(conn, "SELECT to_regclass(%s) AS r", (name,))[0]["r"])


def load(conn, since: dt.date, until: dt.date, history_since: dt.date | None = None) -> Panel:
    """Everything for the days since..until (Pacific). `since` should be at least 35 days before the first day to be computed (baselines, noise, change windows)."""
    p = Panel()
    since = max(since, START - dt.timedelta(days=0))
    ic = _ic_column(conn)
    p.has_ic = bool(ic)
    ic_sel = f", sum(d.{ic}) AS ic" if ic else ", NULL::numeric AS ic"

    # --- coverage of each source (data_coverage view)
    for r in _fetch(conn, "SELECT source, first_day, last_day, loaded_at FROM analytics.data_coverage"):
        p.coverage[r["source"]] = r

    # --- ad family x campaign x day
    sql = f"""SELECT d.day, coalesce(a.tracker_family_key, a.family_key, d.ad_id) AS fk, d.product_slug AS product, d.campaign_name, d.stage,
                     sum(d.spend) AS spend, sum(d.impressions) AS impressions, sum(d.link_clicks) AS clicks, sum(d.landing_page_views) AS lpv, sum(d.add_to_carts) AS atc
                     {ic_sel}, sum(d.upstack_new_customers) AS nc, sum(d.upstack_new_customer_revenue) AS nc_rev,
                     sum(d.meta_purchases) AS mp, sum(d.meta_revenue) AS mrev, count(DISTINCT d.ad_id) FILTER (WHERE d.spend > 0) AS n_ads
              FROM analytics.meta_ad_daily d LEFT JOIN analytics.meta_ads a USING (ad_id)
              WHERE d.day BETWEEN %s AND %s
              GROUP BY 1, 2, 3, 4, 5"""
    for r in _fetch(conn, sql, (since, until)):
        row = {k: (_num(v) if k not in ("day", "fk", "product", "campaign_name", "stage") else v) for k, v in r.items()}
        row["ic"] = float(r["ic"]) if (ic and r["ic"] is not None) else None
        row["structure"] = structure_of(r["campaign_name"], r["stage"])
        p.rows[r["day"]].append(row)
    # --- family first spend day (all history) and created date
    for r in _fetch(conn, """SELECT coalesce(a.tracker_family_key, a.family_key, d.ad_id) AS fk, min(d.day) AS first_day, min(a.created_date) AS created
                             FROM analytics.meta_ad_daily d LEFT JOIN analytics.meta_ads a USING (ad_id) WHERE d.spend > 0 GROUP BY 1"""):
        p.fam_first[r["fk"]] = r["first_day"]
        if r["created"]:
            p.fam_created[r["fk"]] = r["created"]
    # --- family tags / format / vendor / hook / name (Creative Tracker first row per family)
    for r in _fetch(conn, """SELECT DISTINCT ON (family_key) family_key AS fk, concept_id, coalesce(nullif(concept_name, ''), nullif(ad_family, ''), family_key) AS name, product_slug,
                                    cluster, icp, pain_point, emotional_trigger, line_code, ad_type, style, vendor, hook
                             FROM analytics.creative_tracker WHERE is_first_row ORDER BY family_key, tracker_row_id"""):
        fmt = (r["ad_type"] or "").strip()
        sty = (r["style"] or "").strip()
        r["format"] = f"{'Video' if fmt == 'VID' else 'Image' if fmt == 'IMG' else fmt or 'Unknown'}: {sty}" if sty else ("Video" if fmt == "VID" else "Image" if fmt == "IMG" else "Unknown")
        r["media"] = "Video" if fmt == "VID" else "Image" if fmt == "IMG" else "Unknown"
        p.fam[r["fk"]] = r
    # --- ad sets
    for r in _fetch(conn, "SELECT day, adset_id, adset_name, campaign_name, stage, spend FROM analytics.meta_adset_daily WHERE day BETWEEN %s AND %s AND spend > 0", (since, until)):
        p.adsets[r["day"]].append({"adset_id": r["adset_id"], "name": r["adset_name"], "campaign": r["campaign_name"], "stage": r["stage"], "spend": _num(r["spend"])})
        p.adset_name[r["adset_id"]] = r["adset_name"]
    for r in _fetch(conn, "SELECT adset_id, min(day) AS first_day FROM analytics.meta_adset_daily WHERE spend > 0 GROUP BY 1"):
        p.adset_first[r["adset_id"]] = r["first_day"]

    # --- account level: orders, google, bids, traffic
    orders = {r["day"]: r for r in _fetch(conn, "SELECT day, new_customer_revenue, new_customers, returning_customer_revenue, total_revenue, orders FROM analytics.orders_daily WHERE day BETWEEN %s AND %s", (since, until))}
    google = {r["day"]: r for r in _fetch(conn, """SELECT day, sum(spend) AS spend, sum(clicks) AS clicks, sum(upstack_new_customers) AS nc, sum(upstack_new_customer_revenue) AS nc_rev,
                                                         sum(impressions) AS impressions, sum(google_purchases) AS gp, sum(google_revenue) AS grev
                                                  FROM analytics.google_campaign_daily WHERE day BETWEEN %s AND %s GROUP BY 1""", (since, until))}
    for r in _fetch(conn, """SELECT day, coalesce(nullif(campaign_type, ''), 'unknown') AS ctype, sum(spend) AS spend, sum(clicks) AS clicks, sum(impressions) AS impressions,
                                    sum(google_purchases) AS gp, sum(google_revenue) AS grev, sum(upstack_new_customers) AS nc, sum(upstack_new_customer_revenue) AS nc_rev
                             FROM analytics.google_campaign_daily WHERE day BETWEEN %s AND %s GROUP BY 1, 2""", (since, until)):
        p.google_type.setdefault(r["day"], {})[r["ctype"]] = {k: _num(r[k]) for k in ("spend", "clicks", "impressions", "gp", "grev", "nc", "nc_rev")}
    bids = {r["day"]: r for r in _fetch(conn, "SELECT day, spend, capped_spend, floor_spend FROM analytics.meta_bids_daily WHERE day BETWEEN %s AND %s", (since, until))}
    p.traffic = {r["day"]: _num(r["new_visitors"]) for r in _fetch(conn, "SELECT day, new_visitors FROM analytics.site_traffic_daily WHERE day BETWEEN %s AND %s", (since, until))}
    d = since
    while d <= until:
        rows = p.rows.get(d, [])
        a = {k: sum(r[k] for r in rows) for k in ("spend", "impressions", "clicks", "lpv", "atc", "nc", "nc_rev", "mp", "mrev", "n_ads")}
        icv = [r["ic"] for r in rows if r["ic"] is not None]
        a["ic"] = sum(icv) if icv else None
        a["has_meta"] = bool(rows)
        o = orders.get(d)
        a["has_orders"] = o is not None
        a["acct_nc_rev"] = _num(o["new_customer_revenue"]) if o else None
        a["acct_nc"] = _num(o["new_customers"]) if o else None
        a["returning_rev"] = _num(o["returning_customer_revenue"]) if o else None
        a["orders"] = _num(o["orders"]) if o else None
        g = google.get(d)
        a["has_google"] = g is not None
        a["g_spend"] = _num(g["spend"]) if g else None
        a["g_clicks"] = _num(g["clicks"]) if g else None
        a["g_nc"] = _num(g["nc"]) if g else None
        a["g_nc_rev"] = _num(g["nc_rev"]) if g else None
        a["g_impr"] = _num(g["impressions"]) if g else None
        a["g_gp"] = _num(g["gp"]) if g else None
        a["g_grev"] = _num(g["grev"]) if g else None
        b = bids.get(d)
        a["capped_spend"] = _num(b["capped_spend"]) if b else None
        a["floor_spend"] = _num(b["floor_spend"]) if b else None
        a["bid_spend"] = _num(b["spend"]) if b else None
        p.acct[d] = a
        d += dt.timedelta(days=1)

    # --- placement (publisher_platform): from 2026-10-12, ties to the account spend
    for r in _fetch(conn, """SELECT day, value, sum(spend) AS spend, sum(impressions) AS impressions, sum(link_clicks) AS clicks, sum(purchases) AS mp, sum(purchase_value) AS mrev
                             FROM analytics.meta_breakdown_daily WHERE breakdown = 'publisher_platform' AND day BETWEEN %s AND %s GROUP BY 1, 2""", (max(since, BREAKDOWN_START), until)):
        p.place.setdefault(r["day"], {})[r["value"]] = {k: _num(r[k]) for k in ("spend", "impressions", "clicks", "mp", "mrev")}

    # --- Klaviyo (channel x source), the biggest sends of each day, Amazon
    for r in _fetch(conn, """SELECT day, source, channel, sum(sends) AS sends, sum(delivered) AS delivered, sum(opens) AS opens, sum(clicks) AS clicks, sum(unsubscribes) AS unsubs,
                                    sum(revenue) AS revenue, sum(orders) AS orders
                             FROM analytics.klaviyo_daily WHERE day BETWEEN %s AND %s GROUP BY 1, 2, 3""", (since, until)):
        p.klaviyo.setdefault(r["day"], {})[(r["source"], (r["channel"] or "email").lower())] = {k: _num(r[k]) for k in ("sends", "delivered", "opens", "clicks", "unsubs", "revenue", "orders")}
    for r in _fetch(conn, """SELECT day, source, channel, parent_name, sum(sends) AS sends, sum(delivered) AS delivered, sum(revenue) AS revenue, sum(orders) AS orders
                             FROM analytics.klaviyo_daily WHERE day BETWEEN %s AND %s GROUP BY 1, 2, 3, 4 HAVING sum(sends) > 0""", (since, until)):
        p.klaviyo_top.setdefault(r["day"], []).append({"source": r["source"], "channel": (r["channel"] or "email").lower(), "name": r["parent_name"],
                                                       "sends": _num(r["sends"]), "delivered": _num(r["delivered"]), "revenue": _num(r["revenue"]), "orders": _num(r["orders"])})
    for v in p.klaviyo_top.values():
        v.sort(key=lambda x: -x["sends"])
        del v[8:]
    for r in _fetch(conn, "SELECT day, orders, units, revenue, new_customer_orders, new_customer_revenue, ad_spend, attributed_sales, clicks, impressions FROM analytics.amazon_daily WHERE day BETWEEN %s AND %s", (since, until)):
        p.amazon[r["day"]] = {"rev": _num(r["revenue"]), "nc_rev": _num(r["new_customer_revenue"]), "ad_spend": _num(r["ad_spend"]), "orders": _num(r["orders"]), "units": _num(r["units"]),
                              "nc_orders": _num(r["new_customer_orders"]), "attr_sales": _num(r["attributed_sales"]), "clicks": _num(r["clicks"]), "impressions": _num(r["impressions"])}

    # --- Shopify orders: pack mix and discount share of qualifying new-customer orders (rebills, Trybe gifts, cancelled and test excluded)
    if _exists(conn, "analytics.shopify_orders"):
        for r in _fetch(conn, """WITH q AS (SELECT o.order_id, o.order_date, (o.discount_codes IS NOT NULL AND o.discount_codes <> '') AS coded, o.total_price, o.refunded_amount,
                                                   coalesce((SELECT sum(l.quantity) FROM analytics.shopify_order_lines l WHERE l.order_id = o.order_id), 0) AS bottles
                                            FROM analytics.shopify_orders o
                                            WHERE o.order_date BETWEEN %s AND %s AND o.is_new_customer AND NOT o.is_rebill AND NOT o.is_trybe_gift AND NOT o.is_cancelled AND NOT o.is_test)
                                 SELECT order_date AS day, count(*) AS n, count(*) FILTER (WHERE bottles <= 1) AS p1, count(*) FILTER (WHERE bottles = 2) AS p2, count(*) FILTER (WHERE bottles = 3) AS p3,
                                        count(*) FILTER (WHERE bottles >= 4) AS p4, count(*) FILTER (WHERE coded) AS coded, sum(total_price) AS rev, sum(refunded_amount) AS refunded
                                 FROM q GROUP BY 1""", (max(since, SHOPIFY_START), until)):
            p.shop[r["day"]] = {k: _num(v) for k, v in r.items() if k != "day"}
        for r in _fetch(conn, "SELECT order_date AS day, count(*) AS n FROM analytics.shopify_orders WHERE order_date BETWEEN %s AND %s AND is_rebill GROUP BY 1", (max(since, SHOPIFY_START), until)):
            p.shop.setdefault(r["day"], {})["rebills"] = _num(r["n"])

    # --- landing page content changes (a version whose hash differs from the page's previous version), Pacific day of the fetch
    if _exists(conn, "analytics.landing_page_versions"):
        rows = _fetch(conn, "SELECT landing_page_id, content_hash, (fetched_at AT TIME ZONE 'America/Los_Angeles')::date AS day, title FROM analytics.landing_page_versions ORDER BY landing_page_id, fetched_at")
        prev = {}
        for r in rows:
            lp = r["landing_page_id"]
            if lp in prev and prev[lp] != r["content_hash"] and since <= r["day"] <= until:
                p.lp_changes[r["day"]].append({"landing_page_id": lp, "title": r["title"]})
            prev[lp] = r["content_hash"]

    # --- creative stage moves (docs/TEST_TO_SCALE.md): families whose first scaling day is the day
    if _exists(conn, "analytics.creative_stage_moves"):
        for r in _fetch(conn, """SELECT scaling_first_day AS day, count(DISTINCT family_key) AS n FROM analytics.creative_stage_moves
                                 WHERE basis = 'meta' AND moved AND scaling_first_day BETWEEN %s AND %s GROUP BY 1""", (since, until)):
            p.stage_moves[r["day"]] = int(r["n"])

    _load_change_feed(conn, p, since, until)
    return p


# --------------------------------------------------------------------------------------------------------------------------------------------
# The Meta change feed (migration 247 of another job): analytics.meta_change_ledger (ad set snapshot differences: day, adset_id, adset_name, change_type, old_value, new_value)
# and analytics.meta_change_log (the activity log: event_day, event_type, object_name, is_system_event, old_amount, new_amount). Read when they exist, never required.
def _load_change_feed(conn, p: Panel, since: dt.date, until: dt.date) -> None:
    if _exists(conn, "analytics.meta_change_ledger"):
        cols = {r["column_name"] for r in _fetch(conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = 'meta_change_ledger'")}
        if {"day", "change_type", "adset_id"} <= cols:
            old = "old_value::text" if "old_value" in cols else "NULL::text"
            new = "new_value::text" if "new_value" in cols else "NULL::text"
            name = "adset_name" if "adset_name" in cols else "adset_id"
            for r in _fetch(conn, f"""SELECT day, change_type, count(DISTINCT adset_id) AS n, (array_agg(DISTINCT adset_id))[1:200] AS ids, (array_agg({name}))[1:3] AS names,
                                             (array_agg({old} || ' to ' || {new}))[1:3] AS moves
                                      FROM analytics.meta_change_ledger WHERE day BETWEEN %s AND %s GROUP BY 1, 2""", (since, until)):
                p.feed_events[r["day"]].append({"type": r["change_type"], "count": int(r["n"]), "adset_ids": list(r["ids"] or []), "subject": "; ".join(x for x in (r["names"] or []) if x)[:160],
                                                "detail": "; ".join(x for x in (r["moves"] or []) if x)[:200], "source": "ledger"})
            p.feed_name = "analytics.meta_change_ledger"
    if _exists(conn, "analytics.meta_change_log"):
        cols = {r["column_name"] for r in _fetch(conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = 'meta_change_log'")}
        if {"event_day", "event_type"} <= cols:
            sysf = "AND NOT coalesce(is_system_event, false)" if "is_system_event" in cols else ""
            for r in _fetch(conn, f"""SELECT event_day AS day, event_type, count(*) AS n, (array_agg(object_name))[1:3] AS names FROM analytics.meta_change_log
                                      WHERE event_day BETWEEN %s AND %s {sysf} GROUP BY 1, 2""", (since, until)):
                p.feed_events[r["day"]].append({"type": "log_" + r["event_type"], "count": int(r["n"]), "adset_ids": [], "subject": "; ".join(x for x in (r["names"] or []) if x)[:160], "detail": f"{r['n']} logged events", "source": "log"})
            p.feed_name = p.feed_name or "analytics.meta_change_log"
