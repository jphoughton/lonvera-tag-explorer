"""Amazon (Marketing metrics, channel Amazon): the Amazon pages' data, read from the ten tables that migration 249 creates (docs/AMAZON_DATA.md).

Amazon is a SEPARATE channel. Nothing here is added to the All platforms totals, Total new-customer ROAS or any Meta / Google number: Amazon sales overlap what Meta
and Google credit, and Upstack has no Amazon data, so there is no Numbers from switch, only Amazon's own reporting.

Shape (same as app/email_view.py): the payload builders are pure functions of a *source* object; `DbSource` reads Postgres, tests/ui/mock_amazon.py feeds the SAME builders from
made-up rows, so the rules below are production code in both places. Rules that matter (docs/AMAZON_DATA.md):
  * every ratio is a ratio of sums, never an average of daily ratios; NULL is not zero (a day or table with no data is None, not 0);
  * search queries: market totals (search volume, total impressions / clicks / cart adds / purchases) take the MAX across the ASIN rows of one query-week, our own
    counts take the SUM; shares are percent 0 to 100 and are recomputed from our summed count / the market total whenever several ASINs are rolled into a product or
    a range; a week the term is missing is "not in Amazon's top ~100 terms for that product that week": a gap, never a zero;
  * ads: Sponsored Products sales are 14-day, Brands and Display carry the one figure Amazon reports (`sales_reported`); orders likewise;
  * orders: `orders` is a count per ASIN, so it is summed per product / total as "orders counted per product" (an order of two of our products counts for each);
  * the ad share of revenue and the ad return only use days the ads data covers (it starts 2025-12-30, orders 2024-09-24).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from . import queries as q

US = "ATVPDKIKX0DER"
PRODUCTS = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vital Belle"}
AD_TYPES = {"sp": "Sponsored Products", "sb": "Sponsored Brands", "sbv": "Sponsored Brands video", "sd": "Sponsored Display"}
ATTRIBUTION = {"sp": "14-day sales and orders", "sb": "as Amazon reports", "sbv": "as Amazon reports", "sd": "as Amazon reports"}
BRANDED = ("all", "branded", "nonbranded")
GRAINS = ("day", "week", "month")
EMPTY_MESSAGE = "Amazon data has not loaded yet"
NO_SWITCH = "Amazon pages show Amazon's own reporting only; there is no Numbers from switch here."
MAX_TERMS = 2000

# which tables each page needs: its window is the span they share (the earliest start, the earliest end); traffic is not one of them (it lags by days, so it is aligned inside the Overview instead)
KINDS = {"overview": ("orders", "ads"), "ads": ("ads",), "products": ("orders", "ads_asin"), "sqp": ("sqp",), "fees": ("fees",)}
TABLES = {"orders": ("amazon_orders_asin_daily", "day", "day"), "ads": ("amazon_ads_campaign_daily", "day", "day"), "ads_asin": ("amazon_ads_asin_daily", "day", "day"),
          "traffic": ("amazon_traffic_daily", "day", "day"), "fees": ("amazon_settlement_fees_daily", "day", "day"), "sqp": ("amazon_sqp_weekly", "period_start", "period_end")}


# ---------------------------------------------------------------------------------------------------------------- small helpers
def _sum(vals) -> float | None:
    vs = [float(v) for v in vals if v is not None]
    return sum(vs) if vs else None


def _div(a, b, nd: int) -> float | None:
    if a is None or b is None or float(b) == 0:
        return None
    return round(float(a) / float(b), nd)


def _money(x) -> float | None:
    return None if x is None else round(float(x), 2)


def _int(x) -> int | None:
    return None if x is None else int(round(float(x)))


def _max(a, b):
    return b if a is None else a if b is None else max(a, b)


def _add(a, b):
    return b if a is None else a if b is None else a + b


def _day(x) -> str | None:
    return x.isoformat() if x is not None else None


def _bucket(d: dt.date, grain: str) -> dt.date:
    return d if grain == "day" else d - dt.timedelta(days=d.weekday()) if grain == "week" else d.replace(day=1)


def _next_bucket(t: dt.date, grain: str) -> dt.date:
    return t + dt.timedelta(days=1) if grain == "day" else t + dt.timedelta(days=7) if grain == "week" else (t.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def _buckets(lo: dt.date, hi: dt.date, grain: str) -> list[tuple[dt.date, bool]]:
    """(bucket start, partial) for every bucket that touches lo..hi; partial = the range covers only part of it."""
    out, t = [], _bucket(lo, grain)
    while t <= hi:
        nxt = _next_bucket(t, grain)
        out.append((t, grain != "day" and (max(t, lo) > t or min(nxt - dt.timedelta(days=1), hi) < nxt - dt.timedelta(days=1))))
        t = nxt
    return out


def norm_query(s: str | None) -> str:
    """The normalisation the feed applies to every search query: lower case, trimmed, runs of whitespace collapsed."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


# ---------------------------------------------------------------------------------------------------------------- spans, window
def spans(src) -> dict[str, tuple[dt.date, dt.date] | None]:
    return src.spans()


def span_meta(src, kind: str) -> q.DataMeta | None:
    """The data span of a page: the days its tables share (earliest start, earliest end), so a ratio never mixes a table that has a day with one that has not.
    None = none of its tables holds data yet (the page shows 'Amazon data has not loaded yet')."""
    sp = src.spans()
    held = [sp.get(t) for t in KINDS[kind] if sp.get(t)]
    if not held:
        return None
    lo, hi = min(s[0] for s in held), min(s[1] for s in held)
    if hi < lo:
        lo = hi
    return q.DataMeta(daily_start=lo, data_through=hi, impressions_from=lo)


def parse(args, ameta: q.DataMeta) -> q.WindowSpec:
    """window / from / to only: Amazon has no stage, no Active only, no Numbers from and no comparison window (search terms compare to the prior equal period itself)."""
    return q.parse_window({k: args[k] for k in ("window", "from", "to") if k in args and args[k] is not None}, ameta)


def window_info(w: q.WindowSpec, ameta: q.DataMeta, sp: dict | None = None) -> dict:
    info = w.info(ameta)
    info["channel"] = "amazon"
    info["source"] = {"key": "amazon", "basis": None, "label": "Amazon", "labels": {"purchases": "Orders", "revenue": "Revenue", "roas": "Ad return", "cpa": "Cost per order"},
                      "model": None, "channel": "amazon", "summary": NO_SWITCH, "note": None, "history_from": ameta.daily_start.isoformat(), "through": ameta.data_through.isoformat(), "built_at": None}
    info["stage"], info["stages"], info["stage_note"] = "all", list(q.STAGES), ""
    if w.is_launch:
        info["label"] = f"Since launch · Amazon history from {q._fmt_day(ameta.daily_start, True)}"
    return info


def empty_payload(view: str) -> dict:
    return {"channel": "amazon", "view": view, "loaded": False, "message": EMPTY_MESSAGE, "window_info": None}


def status_entry(src) -> dict:
    """sources.amazon of /api/status: the page and the Platform picker's Amazon button are available once orders or ads hold a day."""
    sp = src.spans()
    held = [sp[t] for t in ("orders", "ads") if sp.get(t)]
    if not held:
        return {"available": False, "through": None, "first_day": None}
    return {"available": True, "through": min(s[1] for s in held).isoformat(), "first_day": min(s[0] for s in held).isoformat(),
            "tables": {k: {"first_day": v[0].isoformat(), "through": v[1].isoformat()} for k, v in sp.items() if v}}


def _known(sp_t: tuple[dt.date, dt.date] | None, f: dt.date, t: dt.date) -> tuple[dt.date, dt.date] | None:
    """The part of f..t a table covers (None = none of it)."""
    if sp_t is None:
        return None
    a, b = max(f, sp_t[0]), min(t, sp_t[1])
    return (a, b) if a <= b else None


# ---------------------------------------------------------------------------------------------------------------- ads metrics
def _ad_m(r: dict) -> dict:
    """Ad numbers of one group from its sums (ratios of sums). sales = ad-reported sales: Sponsored Products 14-day, Brands and Display as Amazon reports."""
    sp, imp, clk, sales, orders = r.get("spend"), r.get("impressions"), r.get("clicks"), r.get("sales"), r.get("orders")
    return {"spend": _money(sp), "impressions": _int(imp), "clicks": _int(clk), "ctr": _div(clk, imp, 5), "cpc": _div(sp, clk, 4), "ad_orders": _int(orders),
            "ad_sales": _money(sales), "ad_return": _div(sales, sp, 4), "cost_per_order": _div(sp, orders, 2)}


def _ads_sums(rows: list[dict]) -> dict:
    return {k: _sum(r.get(k) for r in rows) for k in ("spend", "impressions", "clicks", "sales", "orders")}


# ---------------------------------------------------------------------------------------------------------------- overview
def overview(src, ameta: q.DataMeta, w: q.WindowSpec) -> dict:
    f, t = w.span(ameta)
    sp = src.spans()
    ord_k, ads_k, trf_k = _known(sp.get("orders"), f, t), _known(sp.get("ads"), f, t), _known(sp.get("traffic"), f, t)
    ord_days = {r["day"]: r for r in src.orders_by_day(f, t)} if ord_k else {}
    ads_days = {r["day"]: r for r in src.ads_by_day(ads_k[0], ads_k[1])} if ads_k else {}
    trf_days = {r["day"]: r for r in src.traffic_by_day(trf_k[0], trf_k[1])} if trf_k else {}
    rev = _sum(r["revenue"] for r in ord_days.values()) if ord_k else None
    od = {k: (_sum(r[k] for r in ord_days.values()) if ord_k else None) for k in ("orders", "units", "nc_orders", "nc_revenue")}
    ads_sum = _ads_sums(list(ads_days.values())) if ads_k else dict.fromkeys(("spend", "impressions", "clicks", "sales", "orders"))
    ads_m = _ad_m(ads_sum)
    rev_ads_days = _sum(ord_days[d]["revenue"] for d in ord_days if ads_k and ads_k[0] <= d <= ads_k[1]) if ord_k and ads_k else None
    sessions = _sum(r["sessions"] for r in trf_days.values()) if trf_k else None
    orders_trf = _sum(ord_days[d]["orders"] for d in trf_days if d in ord_days) if trf_k and ord_k else None
    tiles = {"revenue": _money(rev), "orders": _int(od["orders"]), "units": _int(od["units"]), "nc_revenue": _money(od["nc_revenue"]), "nc_orders": _int(od["nc_orders"]),
             "ad_spend": ads_m["spend"], "ad_sales": ads_m["ad_sales"], "ad_return": ads_m["ad_return"], "ad_share_of_revenue": _div(ads_sum["spend"], rev_ads_days, 4),
             "sessions": _int(sessions), "conversion": _div(orders_trf, sessions, 5), "page_views": _int(_sum(r["page_views"] for r in trf_days.values()) if trf_k else None)}
    ranges = {"orders": _rng(ord_k), "ads": _rng(ads_k), "traffic": _rng(trf_k), "ad_ratio_days": _rng(ads_k if ord_k else None)}
    by_product = _by_product(src, f, t, ads_k, ord_k, sp, rev_ads_days, tiles)
    by_type = _by_type(src, ads_k)
    notes = []
    if ads_k is None:
        notes.append("Amazon ad data is not loaded for this range (it starts " + (q._fmt_day(sp["ads"][0], True) if sp.get("ads") else "when the first report loads") + ").")
    elif ads_k[0] > f and ord_k:
        notes.append(f"Ad figures cover {q._fmt_day(ads_k[0], True)} to {q._fmt_day(ads_k[1], True)} only; the ad share of revenue uses revenue from those same days.")
    if trf_k is None:
        notes.append("Sessions and conversion: no traffic days in this range.")
    elif trf_k != ord_k and ord_k:
        notes.append(f"Sessions and conversion cover {q._fmt_day(trf_k[0], True)} to {q._fmt_day(trf_k[1], True)}; conversion uses the orders of those same days.")
    return {"channel": "amazon", "view": "overview", "loaded": True, "tiles": tiles, "ranges": ranges, "by_product": by_product["rows"], "by_product_total": by_product["total"],
            "by_ad_type": by_type["rows"], "by_ad_type_total": by_type["total"], "notes": notes}


def _rng(k) -> dict | None:
    return {"from": k[0].isoformat(), "to": k[1].isoformat()} if k else None


def _prod_label(slug: str | None) -> str:
    return PRODUCTS.get(slug, "No single product") if slug else "No single product"


def _by_product(src, f, t, ads_k, ord_k, sp, rev_ads_days, tiles) -> dict:
    ords = {r["key"]: r for r in src.orders_grouped(f, t, "product")} if ord_k else {}
    ads = {r["key"]: r for r in src.ads_grouped(ads_k[0], ads_k[1], "product")} if ads_k else {}
    ords_al = ({r["key"]: r for r in src.orders_grouped(ads_k[0], ads_k[1], "product")} if (ord_k and ads_k and ads_k[0] != f) else ords) if ads_k else {}
    keys = [s for s in PRODUCTS] + [k for k in set(ords) | set(ads) if k not in PRODUCTS and k is not None] + ([None] if (None in ords or None in ads) else [])
    rows = []
    for k in keys:
        o, a, oa = ords.get(k), ads.get(k), ords_al.get(k)
        ad_m = _ad_m(a) if a else (_ad_m({"spend": 0.0, "impressions": 0, "clicks": 0, "sales": 0.0, "orders": 0}) if ads_k else _ad_m({}))
        rows.append({"key": k or "none", "slug": k, "label": _prod_label(k),
                     "revenue": _money(o["revenue"]) if o else (0.0 if ord_k else None), "orders": _int(o["orders"]) if o else (0 if ord_k else None),
                     "units": _int(o["units"]) if o else (0 if ord_k else None), "nc_revenue": _money(o["nc_revenue"]) if o else (0.0 if ord_k else None),
                     "nc_orders": _int(o["nc_orders"]) if o else (0 if ord_k else None), **ad_m,
                     "ad_share_of_revenue": _div(ad_m["spend"], (oa["revenue"] if oa else 0.0) if (ord_k and ads_k) else None, 4)})
    total = {"key": "total", "label": "Total", "revenue": tiles["revenue"], "orders": tiles["orders"], "units": tiles["units"], "nc_revenue": tiles["nc_revenue"], "nc_orders": tiles["nc_orders"],
             "spend": tiles["ad_spend"], "ad_sales": tiles["ad_sales"], "ad_return": tiles["ad_return"], "ad_share_of_revenue": tiles["ad_share_of_revenue"]}
    allads = _ad_m(_ads_sums(list(ads.values()))) if ads_k else _ad_m({})
    total.update({k: allads[k] for k in ("impressions", "clicks", "ctr", "cpc", "ad_orders", "cost_per_order")})
    return {"rows": rows, "total": total}


def _by_type(src, ads_k) -> dict:
    by = {r["key"]: r for r in src.ads_grouped(ads_k[0], ads_k[1], "ad_type")} if ads_k else {}
    tot_spend = _sum(r["spend"] for r in by.values()) if ads_k else None
    rows = []
    for k, label in AD_TYPES.items():
        r = by.get(k) or ({"spend": 0.0, "impressions": 0, "clicks": 0, "sales": 0.0, "orders": 0} if ads_k else {})
        m = _ad_m(r)
        rows.append({"key": k, "label": label, "attribution": ATTRIBUTION[k], **m, "spend_share": _div(m["spend"], tot_spend, 4)})
    extra = [k for k in by if k not in AD_TYPES]
    for k in extra:                                              # an ad type the feed did not know: still shown, never dropped
        m = _ad_m(by[k])
        rows.append({"key": k, "label": str(k), "attribution": "as Amazon reports", **m, "spend_share": _div(m["spend"], tot_spend, 4)})
    allm = _ad_m(_ads_sums(list(by.values()))) if ads_k else _ad_m({})
    return {"rows": rows, "total": {"key": "total", "label": "Total", "attribution": "", **allm, "spend_share": 1.0 if tot_spend else None}}


# ---------------------------------------------------------------------------------------------------------------- trend (overview chart)
def trend(src, ameta: q.DataMeta, w: q.WindowSpec, grain: str) -> dict:
    if grain not in GRAINS:
        raise q.ApiError(400, "grain must be day, week or month")
    f, t = w.span(ameta)
    sp = src.spans()
    ord_k, ads_k, trf_k = _known(sp.get("orders"), f, t), _known(sp.get("ads"), f, t), _known(sp.get("traffic"), f, t)
    ords = {r["day"]: r for r in src.orders_by_day(f, t)} if ord_k else {}
    ads = {r["day"]: r for r in src.ads_by_day(ads_k[0], ads_k[1])} if ads_k else {}
    trf = {r["day"]: r for r in src.traffic_by_day(trf_k[0], trf_k[1])} if trf_k else {}
    points = []
    for b, partial in _buckets(f, t, grain):
        nxt = _next_bucket(b, grain)
        lo, hi = max(b, f), min(nxt - dt.timedelta(days=1), t)
        days = [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]
        o_rows = [ords[d] for d in days if d in ords]
        a_days = [d for d in days if ads_k and ads_k[0] <= d <= ads_k[1]]
        a_rows = [ads[d] for d in a_days if d in ads]
        spend = _sum(r["spend"] for r in a_rows) if a_days else None
        sales = _sum(r["sales"] for r in a_rows) if a_days else None
        rev_a = _sum(ords[d]["revenue"] for d in a_days if d in ords) if a_days and ord_k else None
        t_rows = [trf[d] for d in days if d in trf]
        points.append({"t": b.isoformat(), "partial": partial, "revenue": _money(_sum(r["revenue"] for r in o_rows)) if ord_k else None,
                       "orders": _int(_sum(r["orders"] for r in o_rows)) if ord_k else None, "nc_revenue": _money(_sum(r["nc_revenue"] for r in o_rows)) if ord_k else None,
                       "ad_spend": _money(spend if spend is not None else (0.0 if a_days else None)), "ad_sales": _money(sales if sales is not None else (0.0 if a_days else None)),
                       "ad_return": _div(sales, spend, 4), "ad_share_of_revenue": _div(spend, rev_a, 4),
                       "sessions": _int(_sum(r["sessions"] for r in t_rows)) if t_rows else None})
    return {"channel": "amazon", "view": "trend", "loaded": True, "grain": grain, "points": points}


# ---------------------------------------------------------------------------------------------------------------- campaigns
def _check_ad_type(v: str | None) -> str | None:
    if v is None or v == "all":
        return None
    if v not in AD_TYPES:
        raise q.ApiError(400, "ad_type must be one of " + ", ".join(AD_TYPES))
    return v


def _check_product(v: str | None) -> str | None:
    if v is None or v == "all":
        return None
    if v not in PRODUCTS and v != "none":
        raise q.ApiError(400, "product must be one of " + ", ".join(PRODUCTS) + " or none")
    return v


def campaigns(src, ameta: q.DataMeta, w: q.WindowSpec, ad_type: str | None, product: str | None, text: str | None) -> dict:
    ad_type, product = _check_ad_type(ad_type), _check_product(product)
    f, t = w.span(ameta)
    sp = src.spans()
    k = _known(sp.get("ads"), f, t)
    raw = src.ads_grouped(k[0], k[1], "campaign") if k else []
    raw = [r for r in raw if (r.get("spend") or 0) > 0 or (r.get("impressions") or 0) > 0 or (r.get("clicks") or 0) > 0]
    facets_t: dict[str, float] = {}
    facets_p: dict[str, float] = {}
    for r in raw:
        facets_t[r["ad_type"]] = facets_t.get(r["ad_type"], 0.0) + float(r.get("spend") or 0)
        facets_p[r["product_slug"] or "none"] = facets_p.get(r["product_slug"] or "none", 0.0) + float(r.get("spend") or 0)
    needle = norm_query(text) if text else ""
    kept = [r for r in raw if (not ad_type or r["ad_type"] == ad_type) and (not product or (r["product_slug"] or "none") == product)
            and (not needle or needle in norm_query((r.get("name") or "") + " " + str(r["key"])))]
    rows = [{"key": str(r["key"]), "campaign_id": str(r["key"]), "name": r.get("name") or f"Campaign {r['key']}", "ad_type": r["ad_type"], "ad_type_label": AD_TYPES.get(r["ad_type"], r["ad_type"]),
             "attribution": ATTRIBUTION.get(r["ad_type"], "as Amazon reports"), "product": r["product_slug"], "product_label": _prod_label(r["product_slug"]),
             "status": (r.get("status") or "").lower() or None, "last_day": _day(r.get("last_day")), "m": _ad_m(r)} for r in kept]
    rows.sort(key=lambda x: (-(x["m"]["spend"] or 0), x["name"]))
    total = _ad_m(_ads_sums(kept)) if k else _ad_m({})
    return {"channel": "amazon", "view": "campaigns", "loaded": True, "rows": rows, "total": {"m": total},
            "facets": {"ad_types": [{"key": a, "label": AD_TYPES[a], "spend": _money(facets_t.get(a, 0.0))} for a in AD_TYPES if a in facets_t],
                       "products": [{"key": p, "label": _prod_label(None if p == "none" else p), "spend": _money(facets_p[p])} for p in list(PRODUCTS) + ["none"] if p in facets_p]},
            "scope": {"ad_type": ad_type, "product": product, "q": text or None}, "ads_range": _rng(k),
            "note": "Sponsored Products sales and orders are 14-day; Sponsored Brands, Brands video and Display are as Amazon reports them. The last two weeks read low while 14-day sales mature."}


def campaign_trend(src, ameta: q.DataMeta, w: q.WindowSpec, grain: str, campaign_id: str | None) -> dict:
    if grain not in GRAINS:
        raise q.ApiError(400, "grain must be day, week or month")
    if not campaign_id:
        raise q.ApiError(400, "campaign_id is required")
    f, t = w.span(ameta)
    k = _known(src.spans().get("ads"), f, t)
    days = {r["day"]: r for r in src.ads_by_day(k[0], k[1], campaign_id=campaign_id)} if k else {}
    points = []
    for b, partial in _buckets(f, t, grain):
        nxt = _next_bucket(b, grain)
        lo, hi = max(b, f), min(nxt - dt.timedelta(days=1), t)
        known = k and any(k[0] <= lo + dt.timedelta(days=i) <= k[1] for i in range((hi - lo).days + 1))
        rows = [days[d] for d in (lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)) if d in days]
        m = _ad_m(_ads_sums(rows)) if known else _ad_m({})
        if known and not rows:
            m = _ad_m({"spend": 0.0, "impressions": 0, "clicks": 0, "sales": 0.0, "orders": 0})
        points.append({"t": b.isoformat(), "partial": partial, **m})
    return {"channel": "amazon", "view": "campaign-trend", "loaded": True, "grain": grain, "campaign_id": campaign_id, "points": points}


# ---------------------------------------------------------------------------------------------------------------- products (ASIN)
def products(src, ameta: q.DataMeta, w: q.WindowSpec) -> dict:
    f, t = w.span(ameta)
    sp = src.spans()
    ord_k, ads_k = _known(sp.get("orders"), f, t), _known(sp.get("ads_asin"), f, t)
    ords = {r["key"]: r for r in src.orders_grouped(f, t, "asin")} if ord_k else {}
    ords_al = ({r["key"]: r for r in src.orders_grouped(ads_k[0], ads_k[1], "asin")} if (ord_k and ads_k and ads_k[0] != f) else ords) if ads_k else {}
    ads = {r["key"]: r for r in src.ads_asin_grouped(ads_k[0], ads_k[1])} if ads_k else {}
    names = src.asin_names()
    rows = []
    for a in sorted(set(ords) | set(ads)):
        o, ad, oa = ords.get(a), ads.get(a), ords_al.get(a)
        nm = names.get(a) or {}
        slug = (o or {}).get("product_slug") or nm.get("product_slug") or (ad or {}).get("product_slug")
        m = _ad_m(ad) if ad else _ad_m({"spend": 0.0, "impressions": 0, "clicks": 0, "sales": 0.0, "orders": 0} if ads_k else {})
        rows.append({"key": a, "asin": a, "name": nm.get("name") or a, "product": slug, "product_label": _prod_label(slug) if slug else "No product",
                     "revenue": _money(o["revenue"]) if o else (0.0 if ord_k else None), "units": _int(o["units"]) if o else (0 if ord_k else None),
                     "nc_revenue": _money(o["nc_revenue"]) if o else (0.0 if ord_k else None), "ad_spend": m["spend"], "ad_sales": m["ad_sales"], "ad_return": m["ad_return"],
                     "ad_share_of_revenue": _div(m["spend"], (oa["revenue"] if oa else 0.0) if (ord_k and ads_k) else None, 4)})
    rows.sort(key=lambda r: (-(r["revenue"] or 0), -(r["ad_spend"] or 0), r["asin"]))
    tot_o = (_sum(r["revenue"] for r in ords.values()) if ord_k else None)
    tot_ads = _ads_sums(list(ads.values())) if ads_k else dict.fromkeys(("spend", "sales"))
    rev_al = _sum(r["revenue"] for r in ords_al.values()) if (ord_k and ads_k) else None
    total = {"key": "total", "label": "Total", "revenue": _money(tot_o), "units": _int(_sum(r["units"] for r in ords.values()) if ord_k else None),
             "nc_revenue": _money(_sum(r["nc_revenue"] for r in ords.values()) if ord_k else None), "ad_spend": _money(tot_ads["spend"]), "ad_sales": _money(tot_ads["sales"]),
             "ad_return": _div(tot_ads["sales"], tot_ads["spend"], 4), "ad_share_of_revenue": _div(tot_ads["spend"], rev_al, 4)}
    gap = None
    camp_k = _known(sp.get("ads"), ads_k[0], ads_k[1]) if ads_k else None
    if camp_k and ads_k:
        camp_spend = _sum(r["spend"] for r in src.ads_by_day(camp_k[0], camp_k[1]))
        if camp_spend is not None and tot_ads["spend"] is not None:
            gap = _money(max(0.0, camp_spend - tot_ads["spend"]))
    return {"channel": "amazon", "view": "products", "loaded": True, "rows": rows, "total": total, "ads_range": _rng(ads_k), "spend_without_product": gap,
            "note": "Ad figures here are Sponsored Products and Sponsored Display by advertised ASIN (14-day for Sponsored Products). Sponsored Brands spend has no product grain, so it is in no row."}


# ---------------------------------------------------------------------------------------------------------------- search terms (organic)
MARKET = (("vol", "search_query_volume"), ("ti", "total_impressions"), ("tc", "total_clicks"), ("tca", "total_cart_adds"), ("tp", "total_purchases"))
OURS = (("oi", "asin_impressions"), ("oc", "asin_clicks"), ("oca", "asin_cart_adds"), ("op", "asin_purchases"))
SHARES = (("impression_share", "oi", "ti"), ("click_share", "oc", "tc"), ("cart_add_share", "oca", "tca"), ("purchase_share", "op", "tp"))


def term_weeks(rows: list[dict]) -> dict[tuple[dt.date, str], dict]:
    """(week, query) -> one entry from the ASIN rows of that query-week: the market numbers are MAX over the rows (every ASIN row repeats them; summing would count
    a term shown for five ASINs five times), ours are SUM. A column that is NULL on every row stays None."""
    out: dict[tuple[dt.date, str], dict] = {}
    for r in rows:
        e = out.get((r["period_start"], r["query_key"]))
        if e is None:
            e = out[(r["period_start"], r["query_key"])] = {"term": r.get("search_query") or r["query_key"], "branded": bool(r.get("is_branded")), "asins": 0,
                                                            **{k: None for k, _ in MARKET + OURS}}
        e["asins"] += 1
        for k, col in MARKET:
            e[k] = _max(e[k], r.get(col))
        for k, col in OURS:
            e[k] = _add(e[k], r.get(col))
    return out


def _period(entries: list[dict]) -> dict:
    """One term over some weeks it appeared in: shares are our summed count / the summed market total over those weeks (a ratio of sums), percent 0 to 100."""
    out: dict[str, Any] = {"weeks_present": len(entries)}
    for name, ok, mk in SHARES:
        pairs = [(e[ok], e[mk]) for e in entries if e[ok] is not None and e[mk] is not None]
        mt = sum(float(b) for _, b in pairs)
        out[name] = round(100.0 * sum(float(a) for a, _ in pairs) / mt, 2) if pairs and mt > 0 else None
    for k, _ in OURS:
        out[k] = _int(_sum(e[k] for e in entries))
    vols = [float(e["vol"]) for e in entries if e["vol"] is not None]
    out["volume_avg"] = round(sum(vols) / len(vols), 1) if vols else None
    return out


def _weeks_in(sqp_weeks: list[tuple[dt.date, dt.date]], f: dt.date, t: dt.date) -> list[tuple[dt.date, dt.date]]:
    return [wk for wk in sqp_weeks if wk[0] >= f and wk[1] <= t]


def _check_branded(v: str | None) -> str:
    v = (v or "all").lower()
    if v not in BRANDED:
        raise q.ApiError(400, "branded must be all, branded or nonbranded")
    return v


def search_terms(src, ameta: q.DataMeta, w: q.WindowSpec, product: str | None, branded: str | None, text: str | None) -> dict:
    product = _check_product(product)
    if product == "none":
        raise q.ApiError(400, "product must be one of " + ", ".join(PRODUCTS))
    branded = _check_branded(branded)
    f, t = w.span(ameta)
    weeks = src.weeks()
    sel = _weeks_in(weeks, f, t)
    base = {"channel": "amazon", "view": "search-terms", "loaded": True, "scope": {"product": product, "branded": branded, "q": text or None}, "weeks_in_range": len(sel),
            "weeks_held": [_day(weeks[0][0]), _day(weeks[-1][1])] if weeks else None}
    if not sel:
        return {**base, "rows": [], "total_terms": 0, "truncated": False, "weeks": [], "prior_weeks": [], "summary": None,
                "note": "No full week (Sunday to Saturday) fits in this range. Pick a range of at least 7 days that holds one."}
    n = len(sel)
    first = sel[0][0]
    prior_starts = [first - dt.timedelta(days=7 * k) for k in range(n, 0, -1)]
    held_starts = {a for a, _ in weeks}
    prior = [s for s in prior_starts if s in held_starts]
    prior_ok = len(prior) == n
    lo = prior[0] if prior_ok else first
    rows = src.sqp_rows(lo, sel[-1][0], product, None if branded == "all" else branded == "branded", norm_query(text) if text else None, None)
    tw = term_weeks(rows)
    sel_starts = [a for a, _ in sel]
    keys = {k for (wk, k) in tw if wk in set(sel_starts)}
    out = []
    for key in keys:
        cur = [tw[(wk, key)] for wk in sel_starts if (wk, key) in tw]
        latest_wk = max(wk for wk in sel_starts if (wk, key) in tw)
        pe = _period(cur)
        pr = _period([tw[(wk, key)] for wk in prior if (wk, key) in tw]) if prior_ok else None
        pr_present = bool(pr and pr["weeks_present"])
        latest = tw[(latest_wk, key)]
        out.append({"key": key, "term": latest["term"], "branded": latest["branded"], "weeks_present": pe["weeks_present"], "weeks_in_range": n,
                    "volume_latest": _int(latest["vol"]), "volume_latest_week": latest_wk.isoformat(), "volume_avg": pe["volume_avg"],
                    "impression_share": pe["impression_share"], "click_share": pe["click_share"], "cart_add_share": pe["cart_add_share"], "purchase_share": pe["purchase_share"],
                    "our_impressions": pe["oi"], "our_clicks": pe["oc"], "our_cart_adds": pe["oca"], "our_purchases": pe["op"],
                    "prior": ({"weeks_present": pr["weeks_present"], "volume_avg": pr["volume_avg"], "click_share": pr["click_share"], "purchase_share": pr["purchase_share"]} if pr_present else None),
                    "volume_change": _div((pe["volume_avg"] or 0) - (pr["volume_avg"] or 0), pr["volume_avg"], 4) if pr_present and pr["volume_avg"] and pe["volume_avg"] is not None else None,
                    "click_share_change": round(pe["click_share"] - pr["click_share"], 2) if pr_present and pe["click_share"] is not None and pr["click_share"] is not None else None,
                    "purchase_share_change": round(pe["purchase_share"] - pr["purchase_share"], 2) if pr_present and pe["purchase_share"] is not None and pr["purchase_share"] is not None else None})
    out.sort(key=lambda r: (-(r["volume_avg"] or 0), r["key"]))
    total = len(out)
    out = out[:MAX_TERMS]
    return {**base, "rows": out, "total_terms": total, "truncated": total > MAX_TERMS,
            "weeks": [{"from": a.isoformat(), "to": b.isoformat()} for a, b in sel], "prior_weeks": [s.isoformat() for s in prior] if prior_ok else [],
            "prior_available": prior_ok,
            "summary": {"terms": total, "branded_terms": sum(1 for r in out if r["branded"]), "our_clicks": _int(_sum(r["our_clicks"] for r in out)),
                        "our_purchases": _int(_sum(r["our_purchases"] for r in out))},
            "note": None if prior_ok else "The prior equal period is not held (Amazon's weekly history starts " + (q._fmt_day(weeks[0][0], True) if weeks else "later") + "), so there is no change column."}


def term_history(src, ameta: q.DataMeta, term: str | None, product: str | None) -> dict:
    """One search term over every week held: search volume (the market's, MAX over every row) and, per product and for all products together, our shares and counts.
    A calendar week with no row for the term is present=False with None numbers (a gap), never a zero."""
    key = norm_query(term)
    if not key:
        raise q.ApiError(400, "term is required")
    product = _check_product(product)
    if product == "none":
        raise q.ApiError(400, "product must be one of " + ", ".join(PRODUCTS))
    weeks = src.weeks()
    if not weeks:
        return {"channel": "amazon", "view": "search-term-history", "loaded": True, "term": term, "key": key, "weeks": [], "volume": [], "series": []}
    rows = src.sqp_rows(weeks[0][0], weeks[-1][0], None, None, None, key)
    by_week: dict[dt.date, list[dict]] = {}
    for r in rows:
        by_week.setdefault(r["period_start"], []).append(r)
    starts, s = [], weeks[0][0]
    while s <= weeks[-1][0]:
        starts.append(s)
        s += dt.timedelta(days=7)
    held = {a for a, _ in weeks}
    volume = []
    for s in starts:
        tw = term_weeks(by_week.get(s, []))
        e = tw.get((s, key))
        volume.append({"week": s.isoformat(), "held": s in held, "present": e is not None, "volume": _int(e["vol"]) if e else None})

    def series_for(slug: str | None) -> dict | None:
        pts = []
        for s in starts:
            rs = [r for r in by_week.get(s, []) if slug is None or r.get("product_slug") == slug]
            e = term_weeks(rs).get((s, key))
            if e is None:
                pts.append({"week": s.isoformat(), "present": False, "click_share": None, "purchase_share": None, "impression_share": None, "cart_add_share": None,
                            "our_clicks": None, "our_purchases": None, "volume": None})
                continue
            p = _period([e])
            pts.append({"week": s.isoformat(), "present": True, "click_share": p["click_share"], "purchase_share": p["purchase_share"], "impression_share": p["impression_share"],
                        "cart_add_share": p["cart_add_share"], "our_clicks": p["oc"], "our_purchases": p["op"], "volume": _int(e["vol"])})
        if not any(p["present"] for p in pts):
            return None
        return {"slug": slug or "all", "label": PRODUCTS.get(slug, "All products together") if slug else "All products together", "points": pts}

    series = []
    for slug in ([product] if product else list(PRODUCTS)):
        sr = series_for(slug)
        if sr:
            series.append(sr)
    if not product:
        allp = series_for(None)
        if allp:
            series.insert(0, allp)
    label = next((r["search_query"] for r in rows if r.get("search_query")), term)
    return {"channel": "amazon", "view": "search-term-history", "loaded": True, "term": label, "key": key, "branded": any(r.get("is_branded") for r in rows), "weeks": [s.isoformat() for s in starts],
            "volume": volume, "series": series}


# ---------------------------------------------------------------------------------------------------------------- settlement fees
def fees(src, ameta: q.DataMeta, w: q.WindowSpec) -> dict:
    f, t = w.span(ameta)
    raw = src.fees_grouped(f, t)
    all_months = [b for b, _ in _buckets(f, t, "month")]
    cols = [{"month": m.isoformat(), "partial": any(b == m and p for b, p in _buckets(f, t, "month"))} for m in all_months]
    by: dict[tuple, dict] = {}
    for r in raw:
        k = (r["transaction_type"], r["amount_type"], r["amount_description"])
        e = by.setdefault(k, {"key": " | ".join(k), "transaction_type": k[0], "amount_type": k[1], "amount_description": k[2], "months": {}, "total": 0.0, "lines": 0})
        e["months"][r["month"].isoformat()] = _money(r["amount"])
        e["total"] += float(r["amount"] or 0)
        e["lines"] += int(r["lines"] or 0)
    rows = []
    for e in by.values():
        label = e["amount_description"] or e["amount_type"] or e["transaction_type"] or "(no description)"
        rows.append({**e, "label": label, "sublabel": " · ".join(x for x in (e["transaction_type"], e["amount_type"]) if x and x != label), "total": _money(e["total"])})
    rows.sort(key=lambda r: (r["transaction_type"], r["total"]))
    tot_m = {c["month"]: _money(_sum(r["months"].get(c["month"]) for r in rows)) for c in cols}
    return {"channel": "amazon", "view": "fees", "loaded": True, "months": cols, "rows": rows, "total": {"months": tot_m, "total": _money(_sum(r["total"] for r in rows))},
            "note": "Signed as Amazon settles them: sales positive; fees, promotions and ad debits negative. Posted-day basis, and settlements arrive in batches, so the newest days can be two weeks old. "
                    "Cost of Advertising here is the settlement debit, not the Ads report spend."}


# ---------------------------------------------------------------------------------------------------------------- Postgres source
class DbSource:
    """The database side of every builder. All SQL of the Amazon pages is here."""

    def __init__(self, conn):
        self.c = conn

    def _all(self, sql: str, params=None) -> list[dict]:
        return self.c.execute(sql, params).fetchall()

    def spans(self) -> dict:
        if getattr(self, "_spans", None) is not None:
            return self._spans
        out: dict = {}
        for name, (table, lo, hi) in TABLES.items():
            if self.c.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", ("public." + table,)).fetchone()["ok"]:
                r = self.c.execute(f"SELECT min({lo}) AS lo, max({hi}) AS hi FROM {table}").fetchone()
                out[name] = (r["lo"], r["hi"]) if r and r["lo"] is not None else None
            else:
                out[name] = None
        self._spans = out
        return out

    def orders_by_day(self, f, t):
        return self._all("""SELECT day, sum(orders) AS orders, sum(units) AS units, sum(revenue) AS revenue, sum(nc_orders) AS nc_orders, sum(nc_revenue) AS nc_revenue
                            FROM amazon_orders_asin_daily WHERE day BETWEEN %s AND %s GROUP BY day""", (f, t))

    def orders_grouped(self, f, t, by):
        key = {"product": "product_slug", "asin": "asin"}[by]
        return self._all(f"""SELECT {key} AS key, min(product_slug) AS product_slug, sum(orders) AS orders, sum(units) AS units, sum(revenue) AS revenue, sum(nc_orders) AS nc_orders,
                                    sum(nc_revenue) AS nc_revenue FROM amazon_orders_asin_daily WHERE day BETWEEN %s AND %s GROUP BY {key}""", (f, t))

    _ADS = "sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks, sum(sales_reported) AS sales, sum(COALESCE(purchases_14d, purchases)) AS orders"

    def ads_by_day(self, f, t, campaign_id=None):
        return self._all(f"SELECT day, {self._ADS} FROM amazon_ads_campaign_daily WHERE day BETWEEN %s AND %s" + (" AND campaign_id = %s" if campaign_id else "") + " GROUP BY day",
                         (f, t, campaign_id) if campaign_id else (f, t))

    def ads_grouped(self, f, t, by):
        if by == "campaign":
            return self._all(f"""SELECT campaign_id AS key,
                                        (array_agg(campaign_name ORDER BY day DESC) FILTER (WHERE campaign_name IS NOT NULL))[1] AS name,
                                        (array_agg(campaign_status ORDER BY day DESC) FILTER (WHERE campaign_status IS NOT NULL))[1] AS status,
                                        (array_agg(ad_type ORDER BY spend DESC NULLS LAST))[1] AS ad_type, max(product_slug) AS product_slug, max(day) AS last_day, {self._ADS}
                                 FROM amazon_ads_campaign_daily WHERE day BETWEEN %s AND %s GROUP BY campaign_id""", (f, t))
        key = {"product": "product_slug", "ad_type": "ad_type"}[by]
        return self._all(f"SELECT {key} AS key, {self._ADS} FROM amazon_ads_campaign_daily WHERE day BETWEEN %s AND %s GROUP BY {key}", (f, t))

    def ads_asin_grouped(self, f, t):
        return self._all(f"SELECT asin AS key, max(product_slug) AS product_slug, {self._ADS} FROM amazon_ads_asin_daily WHERE day BETWEEN %s AND %s GROUP BY asin", (f, t))

    def traffic_by_day(self, f, t):
        return self._all("SELECT day, sessions, page_views FROM amazon_traffic_daily WHERE marketplace_id = %s AND day BETWEEN %s AND %s", (US, f, t))

    def asin_names(self):
        return {r["asin"]: {"name": r["name"], "product_slug": r["product_slug"]} for r in self._all(
            "SELECT asin, min(COALESCE(friendly_name, product_name)) AS name, min(product_slug) AS product_slug FROM amazon_asin_products GROUP BY asin")}

    def weeks(self):
        return [(r["period_start"], r["period_end"]) for r in self._all(
            "SELECT period_start, max(period_end) AS period_end FROM amazon_sqp_weekly WHERE marketplace_id = %s GROUP BY period_start ORDER BY period_start", (US,))]

    def sqp_rows(self, first_start, last_start, product, branded, text, term):
        sql = ("SELECT period_start, query_key, search_query, asin, product_slug, is_branded, search_query_volume, total_impressions, asin_impressions, total_clicks, asin_clicks, "
               "total_cart_adds, asin_cart_adds, total_purchases, asin_purchases FROM amazon_sqp_weekly WHERE marketplace_id = %s AND period_start BETWEEN %s AND %s")
        params: list = [US, first_start, last_start]
        if product:
            sql += " AND product_slug = %s"
            params.append(product)
        if branded is not None:
            sql += " AND is_branded = %s"
            params.append(branded)
        if text:
            sql += " AND strpos(query_key, %s) > 0"
            params.append(text)
        if term:
            sql += " AND query_key = %s"
            params.append(term)
        return self._all(sql, params)

    def fees_grouped(self, f, t):
        return self._all("""SELECT date_trunc('month', day)::date AS month, transaction_type, amount_type, amount_description, sum(lines) AS lines, sum(COALESCE(amount_usd, amount)) AS amount
                            FROM amazon_settlement_fees_daily WHERE day BETWEEN %s AND %s GROUP BY 1, 2, 3, 4""", (f, t))
