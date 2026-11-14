"""HTTP layer: parameter parsing + JSON shaping. All SQL lives in app/queries.py."""
from __future__ import annotations

import datetime as dt
import re

from flask import Blueprint, Response, current_app, g, jsonify, request
from werkzeug.datastructures import MultiDict

from . import amazon as amz, auth, partner_tickets as ptk, competitors as comp, meta_breakdowns as mbd, creative_priorities as cpr, priorities_assign as pas, priorities_detail as pdt, priorities_launch_api as pla, priorities_manual as pman, priorities_insights as pins, daily_story as dstory, db, email_view as em, google as gg, inventory_history as invh, landing_pages as lpg, platforms as pf, preview as pv, lookup as tlk, promotions as promo, queries as q, scale_views as sv, trends as trn

bp = Blueprint("api", __name__)


def _arg(name: str) -> str | None:
    v = request.args.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


# Which Meta ads an endpoint covers. 'tracker' = the ads of tracker creatives only; 'tracker+unmatched' = those plus the product's Meta ads that are
# on no tracker family; 'unmatched' = only the latter.
SCOPE_TRACKER, SCOPE_BOTH, SCOPE_UNMATCHED = "tracker", "tracker+unmatched", "unmatched"


def _tag_filtered(*vals) -> bool:
    return any(v is not None for v in vals)


def _need_active(active: bool) -> None:
    if active and not q._CAPS["active"]:
        raise q.ApiError(503, "active-ads data is not built yet")


@bp.before_request
def _pick_source():
    """Every query of this request reads the chosen source's tables (source=meta|upstack, basis=nc|all; see queries.source_sql). Set on EVERY request, so a
    worker thread never keeps the previous request's choice. /api/status and the tie-out are source-independent."""
    if request.endpoint in ("api.status", "api.source_tieout"):
        q.set_source("meta")
    else:
        q.set_source(*q.parse_source(request.args))


def _ctx(with_window: bool = True):
    conn = db.get_conn()
    meta = q.load_meta(conn)
    source, _ = q.parse_source(request.args)
    meta = q.meta_for_source(meta, source) if request.endpoint not in ("api.status",) else meta
    w = q.parse_window(request.args, meta) if with_window else None
    if w is not None:
        _need_active(w.active)
    return conn, meta, w


def _all_days_info(w: q.WindowSpec, meta: q.DataMeta) -> dict:
    """window_info for views that have no 'since launch' (campaigns, unmatched): launch means every day held."""
    info = w.info(meta)
    if w.is_launch:
        info["label"] = f"All days held · {q._fmt_day(meta.daily_start, True)} – {q._fmt_day(meta.data_through, True)}"
        base = "Since launch does not apply here; showing every day held."
        info["note"] = base + (f" {info['note']}" if info["note"] else "")
    return info


@bp.get("/api/status")
def status():
    conn, meta, _ = _ctx(with_window=False)
    n = len(q.rows_of(conn, f"""SELECT p.id FROM products p WHERE p.active AND (
            EXISTS (SELECT 1 FROM tracker_rows r WHERE r.product_id = p.id{q.alive("r")})
            OR EXISTS (SELECT 1 FROM unmatched_meta u WHERE u.product_id = p.id AND {"GREATEST(u.spend_all, u.spend_launch)" if q._CAPS["unm_launch"] else "u.spend_all"} > 0))"""))
    res = q.status(conn, meta, n)
    res["sources"] = {**res["sources"], "amazon": amz.status_entry(amz.DbSource(conn))}          # not in sources.platforms: that list is the spend platforms of the All views
    return jsonify({**res, "auth": "disabled" if auth.auth_disabled() else "enabled", "user": auth.current_user()})


@bp.get("/api/products")
def products():
    # The contract specifies a bare array, so there is no room for window_info here; /rollup and the other endpoints carry it.
    # `?envelope=1` returns {window_info, compare_info, rows, total:{m, rows, spark?}} instead: the form the landing page uses (labels + the Total row).
    conn, meta, w = _ctx()
    spark = q.parse_spark(request.args)
    if request.args.get("envelope") == "1":
        extras: dict = {}
        rows, total = q.products_list(conn, meta, w, spark=spark, with_total=True, extras=extras)
        # total = the tracker products; unmatched = all Meta spend on no tracker family; total_all = both = the whole Meta account
        return jsonify({"window_info": w.info(meta), "compare_info": w.compare_info(meta), "scope": SCOPE_TRACKER, "rows": rows, "total": total, **extras})
    resp = jsonify(q.products_list(conn, meta, w, spark=spark))
    resp.headers["X-Data-Scope"] = SCOPE_TRACKER                 # a bare array has no room for the field
    return resp


@bp.get("/api/products/<slug>/rollup")
def rollup(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    level = (_arg("level") or "cluster").lower()
    cluster, icp = _arg("cluster"), _arg("icp")
    res = q.rollup(conn, meta, w, product, level, cluster, icp, spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "level": level, "filters": {"cluster": cluster, "icp": icp}, "scope": SCOPE_TRACKER,
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/families")
def families(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    cluster, icp, pain = _arg("cluster"), _arg("icp"), _arg("pain")
    res = q.families(conn, meta, w, product, cluster, icp, pain, spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "filters": {"cluster": cluster, "icp": icp, "pain": pain}, "scope": SCOPE_TRACKER,
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/tree")
def tree(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    root = (_arg("root") or "cluster").lower()
    depth = q.parse_uint(_arg("depth") or "0", 0, 3, "depth must be 0, 1, 2 or 3")
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain")}
    res = q.tree(conn, meta, w, product, root, flt["cluster"], flt["icp"], flt["pain"], depth, spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "root": root, "depth": depth, "filters": flt, "scope": SCOPE_TRACKER,
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/coverage")
def coverage(slug: str):
    """Which ICPs / pain points have no ads (tracker based) or ads but $0 spend (selected window, stage, source). See queries.coverage."""
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.coverage(conn, meta, w, product)
    return jsonify({"product": q.product_brief(product), "scope": SCOPE_TRACKER, "window_info": w.info(meta), "compare_info": None, **res})


@bp.get("/api/products/<slug>/ads")
def ads(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain"),
           "family_key": _arg("family_key"), "campaign_id": _arg("campaign_id")}
    res = q.meta_ads(conn, meta, w, product, spark=q.parse_spark(request.args), **flt)
    scope = SCOPE_TRACKER if _tag_filtered(flt["cluster"], flt["icp"], flt["pain"], flt["family_key"]) else SCOPE_BOTH
    return jsonify({"product": q.product_brief(product), "filters": flt, "scope": scope, "window_info": w.info(meta),
                    "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/campaigns")
def campaigns(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.campaigns(conn, meta, w, product, spark=q.parse_spark(request.args))
    return jsonify({"product": q.product_brief(product), "scope": SCOPE_BOTH, "window_info": _all_days_info(w, meta),
                    "compare_info": w.compare_info(meta), **res})


@bp.get("/api/campaigns")
def campaigns_platform():
    """Platform level (Meta): every campaign across all products, one row per campaign; product=<slug> (or none = families of no active product) keeps one product's ads only.
    Same windows, stage / active filters, compare and Meta | Upstack numbers as the product Campaigns view; the total is the sum of the rows."""
    conn, meta, w = _ctx()
    slug = _arg("product")
    if slug is None:
        product, brief = "all", None
    elif slug.lower() == q.NO_PRODUCT_SLUG:
        product, brief = None, {"slug": q.NO_PRODUCT_SLUG, "name": q.NO_PRODUCT}
    else:
        product = q.get_product(conn, slug)
        brief = {"slug": product["slug"], "name": product["name"]}
    res = q.campaigns_all(conn, meta, w, product, spark=q.parse_spark(request.args))
    return jsonify({"platform": "meta", "product": brief, "scope": SCOPE_BOTH, "window_info": _all_days_info(w, meta), "compare_info": w.compare_info(meta),
                    "products": q.campaign_products(conn, meta, w), **res})


def _meta_product_arg(conn):
    """product=<slug> | none | absent -> ('all' | product dict | None for families of no active product, brief for the response)."""
    slug = _arg("product")
    if slug is None:
        return "all", None
    if slug.lower() == q.NO_PRODUCT_SLUG:
        return None, {"slug": q.NO_PRODUCT_SLUG, "name": q.NO_PRODUCT}
    p = q.get_product(conn, slug)
    return p, {"slug": p["slug"], "name": p["name"]}


@bp.get("/api/campaigns/trend")
def campaigns_platform_trend():
    """Trend of /api/campaigns (Meta, all products): same window, stage, active, Meta | Upstack numbers and product filter, so its points add up to the table's
    total. split=stage|product returns one point list per stage / product; campaign_id narrows to one campaign."""
    conn, meta, w = _ctx()
    product, brief = _meta_product_arg(conn)
    res = q.campaigns_trend(conn, meta, w, product, campaign_id=_arg("campaign_id"), grain=(_arg("grain") or "day").lower(), split=(_arg("split") or "none").lower())
    info = _all_days_info(w, meta)
    return jsonify({"platform": "meta", "product": brief, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/campaigns/all")
def campaigns_all_platforms():
    """Platform = All: every Meta and Google campaign in one list (see app/platforms.py). only=meta|google, product=<slug> (Meta) or type=<Google type> narrow it;
    the numbers are the page's Numbers from / Count (own reporting, Upstack new customers, Upstack all orders), counted per platform and labelled per row."""
    conn = db.get_conn()
    meta = q.load_meta(conn)
    numbers = pf.numbers_of(request.args)
    try:
        res = pf.all_campaigns(conn, meta, request.args, numbers, platform=_arg("only"), product_slug=_arg("product"), ctype=_arg("type"), spark=q.parse_spark(request.args))
        res.update(pf.filter_chips(conn, meta, request.args, numbers))
    finally:
        q.set_source("meta")
    return jsonify(res)


@bp.get("/api/meta/breakdowns/coverage")
def meta_breakdowns_coverage():
    """What the nightly Meta breakdown pull holds, per set and level: status (ok / unsupported / error), first and last day, rows, last error (app/meta_breakdowns.py)."""
    return jsonify(mbd.coverage(db.get_conn()))


@bp.get("/api/meta/breakdowns")
def meta_breakdowns():
    """One breakdown set (set=age | age,gender | region ...) aggregated over start..end by its dimensions: spend, impressions, clicks, purchases, revenue, ROAS, cost per
    purchase, cost per 1,000 impressions, click rate. Filters: level, product=<slug>, campaign_id. Meta-reported; reach is never summed."""
    return jsonify(mbd.breakdown(db.get_conn(), request.args))


@bp.get("/api/platforms/kpis")
def platforms_kpis():
    """Totals strip of the All views: Total spend (Meta + Google), actual new-customer revenue counted once, Total NC ROAS (new-customer revenue / total spend over the
    days Meta, Google and the orders feed all cover), the platform-attributed sum and its gap; window B too when Compare is on. With only= (or product= / type=, which
    belong to one platform) it is that platform's own figures on the Numbers basis (source / basis, as the All campaigns table): its spend, revenue and ROAS, the same
    numbers as that table's subtotal row."""
    conn = db.get_conn()
    meta = q.load_meta(conn)
    only = _arg("only")
    if only not in (None, "meta", "google"):
        raise q.ApiError(400, "only must be meta or google")
    if _arg("product") is not None and _arg("type") is not None:
        raise q.ApiError(400, "product and type cannot both be set")
    try:
        res = pf.kpis(conn, meta, request.args, numbers=pf.numbers_of(request.args), platform=only, product_slug=_arg("product"), ctype=_arg("type"))
    finally:
        q.set_source("meta")
    return jsonify(res)


@bp.get("/api/platforms/trend")
def platforms_trend():
    """Trend of the All views: spend (and any other metric) per platform on the chosen numbers over one x axis; same filters as /api/campaigns/all."""
    conn = db.get_conn()
    meta = q.load_meta(conn)
    numbers = pf.numbers_of(request.args)
    grain = (_arg("grain") or "day").lower()
    if grain not in q.TREND_GRAINS:
        raise q.ApiError(400, f"grain must be one of {', '.join(q.TREND_GRAINS)}")
    try:
        res = pf.trend(conn, meta, request.args, numbers, grain=grain, platform=_arg("only"), product_slug=_arg("product"), ctype=_arg("type"))
    finally:
        q.set_source("meta")
    return jsonify(res)


@bp.get("/api/products/<slug>/trend")
def trend(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain"), "family_key": _arg("family_key"), "campaign_id": _arg("campaign_id")}
    kids = [k.strip() for k in (_arg("children") or "").split(",") if k.strip()] or None
    res = q.trend(conn, meta, w, product, grain=(_arg("grain") or "day").lower(), split=(_arg("split") or "none").lower(), children_keys=kids, **flt)
    info = w.info(meta)
    if w.is_launch:
        info["note"] = ("Trend covers every day held "
                        f"({q._fmt_day(meta.daily_start, True)} – {q._fmt_day(meta.data_through, True)}).") + (f" {info['note']}" if info["note"] else "")
    # `scope` of a trend is already the filter object, so its data scope is `data_scope`
    data_scope = SCOPE_BOTH if flt["campaign_id"] is not None and not _tag_filtered(flt["cluster"], flt["icp"], flt["pain"], flt["family_key"]) else SCOPE_TRACKER
    return jsonify({"product": q.product_brief(product), "data_scope": data_scope, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/stage-daily")
def stage_daily(slug: str):
    conn, meta, _ = _ctx(with_window=False)
    product = q.get_product(conn, slug)
    active = q.parse_active(request.args)
    _need_active(active)
    if any(request.args.get(k) for k in ("window", "from", "to")):
        w = q.parse_window({k: v for k, v in request.args.items() if k not in ("compare", "cfrom", "cto")}, meta)   # no comparison here
        df, dtt = w.span(meta)
    else:
        days = q.parse_uint(_arg("days") or "90", 1, 366, "days must be an integer from 1 to 366")
        dtt = meta.data_through
        df = max(meta.daily_start, dtt - dt.timedelta(days=days - 1))
    resp = jsonify(q.stage_daily(conn, meta, product, df, dtt, q.stage_list(q.parse_stage(request.args)), active=active))
    resp.headers["X-Data-Scope"] = SCOPE_BOTH                    # a bare array has no room for the field
    return resp


@bp.get("/api/products/<slug>/unmatched")
def unmatched(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.unmatched(conn, meta, w, product)
    info = w.info(meta)
    if w.is_launch:
        if w.active or q._CAPS["unm_launch"]:
            info["label"] = "Since launch (Meta lifetime)"
            info["note"] = ("Since launch is Meta's lifetime for these families, lifetime-only ones included"
                            + ("." if q._CAPS["unm_traffic"] or w.active else " (spend, purchases and revenue; impressions and clicks are not part of it).")
                            + (f" {info['note']}" if info["note"] else ""))
        else:
            info = _all_days_info(w, meta)
    return jsonify({"product": q.product_brief(product), "scope": SCOPE_UNMATCHED, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/ads/<aint:row_id>")
def ad_detail(row_id: int):
    conn, meta, w = _ctx()
    res = q.ad_detail(conn, meta, w, row_id)
    res["window_info"] = w.info(meta)
    res["compare_info"] = w.compare_info(meta)
    res["scope"] = SCOPE_TRACKER
    return jsonify(res)


@bp.get("/api/source/tieout")
def source_tieout():
    """Meta vs Upstack totals for a window (window=7d|30d|...|mtd|launch(=every day held) or from/to). Days outside Upstack's history are not compared."""
    conn = db.get_conn()
    meta = q.meta_for_source(q.load_meta(conn), "upstack")
    w = q.parse_window({k: v for k, v in request.args.items() if k in ("window", "from", "to")}, meta)
    df, dtt = w.span(meta)
    res = q.source_tieout(conn, meta, df, dtt)
    res["window_info"] = w.info(meta)
    return jsonify(res)


def _google_ctx():
    """(conn, google data span, window) for the Google endpoints: the window is parsed against Google's own data span, never Meta's."""
    conn = db.get_conn()
    meta = q.load_meta(conn)
    source, _ = q.parse_source(request.args)
    gmeta = gg.google_meta(meta, source)
    return conn, gmeta, q.parse_window(request.args, gmeta)


@bp.get("/api/google/rollup")
def google_rollup():
    """Google campaigns by campaign type (group=type) or by campaign (group=campaign, optional type=). Upstack-fed, campaign grain; see app/google.py."""
    conn, gmeta, w = _google_ctx()
    info = gg.window_info(w, gmeta)
    res = gg.rollup(conn, gmeta, w, (_arg("group") or "type").lower(), _arg("type"))
    return jsonify({"channel": "google", "window": info, "window_info": info, "compare_info": w.compare_info(gmeta), **res})


@bp.get("/api/google/trend")
def google_trend():
    conn, gmeta, w = _google_ctx()
    info = gg.window_info(w, gmeta)
    res = gg.trend(conn, gmeta, w, (_arg("grain") or "day").lower(), _arg("type"), _arg("campaign"))
    if w.is_launch:
        info["note"] = f"Trend covers every day held ({q._fmt_day(gmeta.daily_start, True)} – {q._fmt_day(gmeta.data_through, True)})." + (f" {info['note']}" if info["note"] else "")
    return jsonify({"channel": "google", "window_info": info, "compare_info": w.compare_info(gmeta), **res})


def _amazon_ctx(kind: str):
    """(source, data span, window) for an Amazon endpoint; the span is the days that page's tables share, never Meta's. meta is None while those tables are empty."""
    src = amz.DbSource(db.get_conn())
    ameta = amz.span_meta(src, kind)
    return src, ameta, (amz.parse(request.args, ameta) if ameta is not None else None)


def _amazon_reply(view: str, kind: str, build):
    """Shared shape of the read-only Amazon routes: `build(src, ameta, w)` returns the payload; empty tables answer 200 with loaded=false (the page shows an empty state)."""
    src, ameta, w = _amazon_ctx(kind)
    if ameta is None:
        return jsonify(amz.empty_payload(view))
    info = amz.window_info(w, ameta)
    return jsonify({**build(src, ameta, w), "window": info, "window_info": info, "compare_info": None})


@bp.get("/api/amazon/overview")
def amazon_overview():
    """Amazon page, Overview: revenue, orders, units, new-customer revenue, ad spend and return, sessions and conversion for the range, by product and by ad type. Amazon is its own channel and is never added into the All platforms totals; see app/amazon.py."""
    return _amazon_reply("overview", "overview", lambda s, m, w: amz.overview(s, m, w))


@bp.get("/api/amazon/trend")
def amazon_trend():
    """Amazon Overview chart: revenue, ad spend, ad return and sessions by day, week (Monday) or month (grain=day|week|month)."""
    return _amazon_reply("trend", "overview", lambda s, m, w: amz.trend(s, m, w, (_arg("grain") or "day").lower()))


@bp.get("/api/amazon/campaigns")
def amazon_campaigns():
    """Amazon ad campaigns for the range with ad type, product, status and ad-reported results; filters ad_type=sp|sb|sbv|sd, product=<slug>|none, q=<text>."""
    return _amazon_reply("campaigns", "ads", lambda s, m, w: amz.campaigns(s, m, w, _arg("ad_type"), _arg("product"), _arg("q")))


@bp.get("/api/amazon/campaign-trend")
def amazon_campaign_trend():
    """Daily, weekly or monthly ad numbers of one Amazon campaign (campaign_id=<id>, grain=day|week|month)."""
    return _amazon_reply("campaign-trend", "ads", lambda s, m, w: amz.campaign_trend(s, m, w, (_arg("grain") or "day").lower(), _arg("campaign_id")))


@bp.get("/api/amazon/products")
def amazon_products():
    """Amazon by ASIN: revenue, units, new-customer revenue and the advertised-product ad numbers (Sponsored Products and Display; Brands has no product grain)."""
    return _amazon_reply("products", "products", lambda s, m, w: amz.products(s, m, w))


@bp.get("/api/amazon/search-terms")
def amazon_search_terms():
    """Organic search terms from Amazon Brand Analytics for the full weeks inside the range: search volume, our impression / click / cart-add / purchase share, our counts and the change vs the prior equal period; product=<slug>, branded=all|branded|nonbranded, q=<text>."""
    return _amazon_reply("search-terms", "sqp", lambda s, m, w: amz.search_terms(s, m, w, _arg("product"), _arg("branded"), _arg("q")))


@bp.get("/api/amazon/search-terms/history")
def amazon_search_term_history():
    """One search term over every week held: search volume and our click and purchase share per product (term=<query>, optional product=<slug>); weeks the term is missing are gaps."""
    return _amazon_reply("search-term-history", "sqp", lambda s, m, w: amz.term_history(s, m, _arg("term"), _arg("product")))


@bp.get("/api/amazon/keywords")
def amazon_keywords():
    """Amazon keyword layer: one row per Sponsored Products keyword or target with the range's cost, clicks, purchases and sales (7-day attribution) and the current bid; filters product=<slug>|mixed|unmapped, state=enabled|paused, q=<text>, all=1 (also keywords with no impressions). Loaded by hand; see docs/AMAZON_DATA.md."""
    return _amazon_reply("keywords", "keywords", lambda s, m, w: amz.keywords(s, m, w, _arg("product"), _arg("state"), _arg("q"), _arg("all") not in ("1", "true", "yes")))


@bp.get("/api/amazon/ad-search-terms")
def amazon_ad_search_terms():
    """Amazon customer search terms your Sponsored Products ads served on, one row per term across campaigns, with the paid numbers and the organic Search Query Performance columns (search volume, our click and purchase share) where Amazon lists the term; product=<slug>|mixed|unmapped, q=<text>."""
    return _amazon_reply("ad-search-terms", "adsearch", lambda s, m, w: amz.ad_search_terms(s, m, w, _arg("product"), _arg("q")))


@bp.get("/api/amazon/ad-search-terms/matches")
def amazon_ad_search_term_matches():
    """The keywords and campaigns that matched one customer search term in the range (term=<text>, optional product)."""
    return _amazon_reply("ad-search-term-matches", "adsearch", lambda s, m, w: amz.ad_search_term_matches(s, m, w, _arg("term"), _arg("product")))


@bp.get("/api/amazon/negatives")
def amazon_negatives():
    """Amazon negative keywords (ad group level, latest snapshot): q=<text>, match=all|phrase|exact, state=all|enabled|archived, product=<slug>|mixed|unmapped, limit (default 2000, at most 50000); the count is of every match."""
    return _amazon_reply("negatives", "negatives", lambda s, m, w: amz.negatives(s, m, _arg("q"), _arg("match"), _arg("state"), _arg("product"), int(_arg("limit")) if (_arg("limit") or "").isdigit() else None))


@bp.get("/api/amazon/fees")
def amazon_fees():
    """Amazon settlement lines by fee type and month for the range (posted-day basis, signed as Amazon settles them)."""
    return _amazon_reply("fees", "fees", lambda s, m, w: amz.fees(s, m, w))


@bp.get("/api/landing-pages")
def landing_pages():
    """Every landing page Meta ads send people to, with its current copy summary and its top ads by spend. window=7|30 (default 30), top=<1..25> (default 5). See docs/LANDING_PAGES.md."""
    return jsonify(lpg.list_pages(db.get_conn(), request.args))


@bp.get("/api/landing-pages/<int:page_id>")
def landing_page(page_id: int):
    """One landing page: the full stored copy of its current version, the list of versions, and its top ads (window=7|30, top=<1..25>)."""
    return jsonify(lpg.page_detail(db.get_conn(), page_id, request.args))


@bp.get("/api/promotions")
def promotions():
    """Test to scale (Meta only): creatives moved from Testing to Scaling in the window, those launched and not scaled, the funnel by launch week and a chart series.
    window / from / to / days (default the last 30 days), product=<slug>|none, hide_bulk=1, bulk_min=<n>, min_test_spend=<$>, grain=auto|day|week, source / basis. See app/promotions.py."""
    conn = db.get_conn()
    meta = q.meta_for_source(q.load_meta(conn), q.parse_source(request.args)[0])
    return jsonify(promo.promotions(conn, meta, request.args, product_slug=_arg("product")))


@bp.get("/api/promotions/scale")
def promotions_scale():
    """Test to scale, the simple views: view=should_scale (testing creatives that look good enough to move; min_test_spend, min_purchases, bar_pct), view=should_kill (live testing
    creatives clearly below their product's scaling average; kill_min_spend, kill_roas_pct) or view=in_scaling (creatives that moved and how they do since; window / from / to / days, hide_bulk, bulk_min); both product=<slug>|none, source / basis. See app/scale_views.py."""
    conn = db.get_conn()
    meta = q.meta_for_source(q.load_meta(conn), q.parse_source(request.args)[0])
    return jsonify(sv.scale_views(conn, meta, request.args, product_slug=_arg("product")))


@bp.get("/api/priorities")
def priorities():
    """Creative priorities: the weekly one-sheeter (week=YYYY-MM-DD, default the latest snapshot) plus the list of stored weeks. See app/creative_priorities.py."""
    return jsonify(cpr.payload(db.get_conn(), _arg("week")))


@bp.get("/api/competitors")
def competitors():
    """Competitors: what watched brands run on Meta this week (week=YYYY-MM-DD, default the latest snapshot; format=video|image, default all): one row per brand, what changed since the prior week, chatter when stored. See app/competitors.py."""
    return jsonify(comp.payload(db.get_conn(), _arg("week"), _arg("format")))


@bp.get("/api/competitors/<path:brand>")
def competitor_detail(brand: str):
    """One competitor: longest-running ads, new this week, angles, landing pages, Ad Library links (week, format as on /api/competitors)."""
    return jsonify(comp.brand_detail(db.get_conn(), brand, _arg("week"), _arg("format")))


@bp.get("/api/priorities/lines/<int:line_id>/insights")
def priorities_line_insights(line_id: int):
    """Drill-down behind one block of a /priorities row panel: block=then_now|timeline|hooks|winners|performance|testing|live|ad, window=7|30|90|all (default 30), optional ad_id=<tracker row id>
    for one ad. Top 10 ads on the line (name, spend, ROAS, purchases, launch date, hook), weekly trend, best hooks. Read-only, Meta basis. See app/priorities_insights.py."""
    ad = _arg("ad_id")
    if ad is not None and not ad.isdigit():
        raise q.ApiError(400, "ad_id must be a number")
    return jsonify(pins.insights(db.get_conn(), line_id, _arg("block") or "performance", _arg("window") or "30", int(ad) if ad else None))


@bp.get("/api/priorities.csv")
def priorities_csv():
    """The same snapshot as a CSV download (week=YYYY-MM-DD, default the latest). Repeated id=<row key> keeps only those rows (same columns); no id = every row."""
    week, text = cpr.csv_text(db.get_conn(), _arg("week"), request.args.getlist("id"), briefed_token=_arg("briefed_from"))
    fname = cpr.slug(_arg("name")) if _arg("name") else f"creative-priorities-{week}"       # name= (the active view's name) becomes the file name
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})


_view_limiter = None


def _views_write_guard(limiter_name: str = "_view_limiter", per_min_key: str = "PRIORITIES_VIEWS_PER_MIN", default: int = 30):
    """Saved-view writes: same-origin only, JSON only, per-IP sliding window (30 per minute, like the projection writes), a larger body cap than the app-wide 64 KB (500 row keys)."""
    from urllib.parse import urlsplit
    from .projection.limits import SlidingWindow
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
        raise q.ApiError(403, "origin mismatch")
    if globals().get(limiter_name) is None:
        globals()[limiter_name] = SlidingWindow(int(current_app.config.get(per_min_key) or default), 60)
    wait = globals()[limiter_name].hit(request.remote_addr or "unknown")
    if wait:
        resp = jsonify(error="rate_limited", retry_after=wait)
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp
    request.max_content_length = cpr.VIEW_BODY_BYTES
    return None


@bp.post("/api/priorities/views")
def priorities_view_create():
    """Save a view: {name?, week?, kind?: rows|filters, ids: [row key,...] (rows) or filters: {products, sections, q, key, dir} (filters)} -> {token, url, count, missing, kind}. The link is the access (the app has no password)."""
    blocked = _views_write_guard()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return jsonify(cpr.create_view(db.get_conn(), body)), 201


@bp.get("/api/priorities/views")
def priorities_views():
    return jsonify(views=cpr.list_views())


@bp.get("/api/priorities/views/<token>")
def priorities_view(token: str):
    return jsonify(cpr.get_view(token))


@bp.get("/api/priorities/views/<token>/csv")
def priorities_view_csv(token: str):
    name_slug, text = cpr.view_csv(token, request.args.getlist("id"))
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name_slug}.csv"'})


@bp.put("/api/priorities/views/<token>")
def priorities_view_update(token: str):
    """Update this view: replace its rows ({ids}) or filters ({filters}) with the current ones, from the latest snapshot. Name, kind and link stay."""
    blocked = _views_write_guard()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return jsonify(cpr.update_view(db.get_conn(), token, body))


_briefed_limiter = None


@bp.patch("/api/priorities/views/<token>/briefed")
def priorities_view_briefed(token: str):
    """Mark a concept briefed / not briefed in a view: {key, briefed: true|false, note?} -> {key, briefed, at, note}. Own limit (120 per minute per IP, PRIORITIES_BRIEFED_PER_MIN)."""
    blocked = _views_write_guard("_briefed_limiter", "PRIORITIES_BRIEFED_PER_MIN", 120)
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return jsonify(cpr.set_briefed(db.get_conn(), token, body))


@bp.patch("/api/priorities/views/<token>")
def priorities_view_rename(token: str):
    blocked = _views_write_guard()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return jsonify(cpr.rename_view(token, body.get("name")))


@bp.delete("/api/priorities/views/<token>")
def priorities_view_delete(token: str):
    blocked = _views_write_guard()
    if blocked is not None:
        return blocked
    cpr.delete_view(token)
    return "", 204


_manual_limiter = None


def _manual_guard():
    """Manual-concept writes: same-origin only, its own per-IP sliding window (60 per minute, PRIORITIES_MANUAL_PER_MIN), a 32 KB body cap."""
    global _manual_limiter
    from urllib.parse import urlsplit
    from .projection.limits import SlidingWindow
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
        raise q.ApiError(403, "origin mismatch")
    if _manual_limiter is None:
        _manual_limiter = SlidingWindow(int(current_app.config.get("PRIORITIES_MANUAL_PER_MIN") or 60), 60)
    wait = _manual_limiter.hit(request.remote_addr or "unknown")
    if wait:
        resp = jsonify(error="rate_limited", retry_after=wait)
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp
    request.max_content_length = pman.BODY_BYTES
    return None


@bp.get("/api/priorities/manual/options")
def priorities_manual_options():
    """Dropdown options for the Add concept form: products, sections and the Communication Overview tree product -> cluster -> ICP -> pain point -> [stories]."""
    return jsonify(pman.options())


@bp.get("/api/priorities/manual")
def priorities_manual_list():
    """Manual concepts as page rows (include_done=1 also returns the done ones)."""
    recs = pman.list_records(db.get_conn(), include_done=request.args.get("include_done") in ("1", "true"))
    return jsonify(rows=[pman.to_row(r) for r in recs])


@bp.post("/api/priorities/manual")
def priorities_manual_create():
    """Add a manual concept: {product, section, cluster, icp, pain_point, story?, notes?, why_now?}; a tag that is not in the catalog is stored as typed and flagged new."""
    blocked = _manual_guard()
    if blocked is not None:
        return blocked
    return jsonify(pman.to_row(pman.create(request.get_json(silent=True)))), 201


@bp.get("/api/priorities/manual/<int:rid>")
def priorities_manual_get(rid: int):
    return jsonify(pman.to_row(pman.get_record(rid)))


@bp.patch("/api/priorities/manual/<int:rid>")
def priorities_manual_update(rid: int):
    """Change any of the fields, or status open|done."""
    blocked = _manual_guard()
    if blocked is not None:
        return blocked
    return jsonify(pman.to_row(pman.update(rid, request.get_json(silent=True))))


@bp.delete("/api/priorities/manual/<int:rid>")
def priorities_manual_delete(rid: int):
    blocked = _manual_guard()
    if blocked is not None:
        return blocked
    pman.delete(rid)
    return "", 204


_assign_limiter = None
_vendor_limiter = None


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise q.ApiError(400, "JSON object body required")
    return body


@bp.get("/api/priorities/assignments")
def priorities_assignments():
    """Production assignments: ?week=YYYY-MM-DD (snaps to its Monday) &vendor=<name> &include_closed=1 (adds launched and dropped) -> {rows, vendors, weeks, formats: [{code, name, length}]}. Each row: the table fields plus format_code / format_name / format_length (null when no format was chosen), stage,
    stage_label, tracker_status, suggested_concept_ids and ads [{concept_id, ad_family, status, launch_date, spend, roas, ad_url}]. See app/priorities_assign.py."""
    out = pas.list_assignments(db.get_conn(), week=_arg("week"), vendor=_arg("vendor"), include_closed=request.args.get("include_closed") in ("1", "true"))
    out["launch"] = pla.discovery()                  # {enabled, can_launch, dry_run}: the production page shows launch controls only when enabled (see app/priorities_launch_api.py)
    return jsonify(out)


@bp.get("/api/priorities/assignments/<int:aid>/detail")
def priorities_assignment_detail(aid: int):
    """The detail panel of one assignment (loaded when its row is expanded): the concept's row from the latest priorities snapshot (or what the assignment stored, with current=false), the product baseline,
    the definitions and up to 10 example ads by spend -> {assignment_id, current, snapshot_week, row, baselines, definitions, examples, examples_max, message}. See app/priorities_detail.py."""
    return jsonify(pdt.assignment_detail(db.get_conn(), aid))


@bp.post("/api/priorities/tickets")
def priorities_ticket_create():
    """Add a ticket right from the Production board: {concept_name, product, vendor, for_week?, cluster?, icp?, pain_point?, concept_kind?, format_code?, brief_url?, note?, due_date?, status?} -> the new card
    (201). Signed-in users only, same limits as Assign to vendor. See app/priorities_assign.py (create_ticket)."""
    if g.get("api_key_scope") == "partner_push":
        blocked = _partner_guard()
        if blocked is not None:
            return blocked
        res, _batch = ptk.create(db.get_conn(), _json_body(), g.api_key_vendor, auth.current_user())
        return jsonify(res), 201
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.create_ticket(db.get_conn(), _json_body(), auth.current_user())), 201


# ---- outside creative partners (partner_push API keys, migration 260). See app/partner_tickets.py and docs/PARTNER_API.md.
_partner_limiter = None


def _partner() -> str:
    """The vendor of the partner key making this request; 403 for anyone else (a login or a read_assign key has no business on the partner routes)."""
    if g.get("api_key_scope") != "partner_push" or not g.get("api_key_vendor"):
        raise q.ApiError(403, "These routes are for partner API keys only.")
    return g.api_key_vendor


def _partner_guard():
    """Writes of a partner key: Origin header (when sent) must match, 60 writes a minute PER KEY (429 with Retry-After), body at most 1 MB."""
    from urllib.parse import urlsplit
    from .projection.limits import SlidingWindow
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
        raise q.ApiError(403, "origin mismatch")
    global _partner_limiter
    if _partner_limiter is None:
        _partner_limiter = SlidingWindow(int(current_app.config.get("PARTNER_WRITES_PER_MIN") or ptk.WRITES_PER_MIN), 60)
    wait = _partner_limiter.hit(f"key:{g.get('api_key_id')}")
    if wait:
        resp = jsonify(error="rate_limited", retry_after=wait, message="Too many requests from this key. Wait and try again.")
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp
    request.max_content_length = ptk.BODY_BYTES
    return None


@bp.get("/api/priorities/partner/contract")
def priorities_partner_contract():
    """What this partner key can do, as data (required and optional fields, allowed values, limits, the key's own vendor)."""
    return jsonify(ptk.contract(_partner(), int(current_app.config.get("PARTNER_WRITES_PER_MIN") or ptk.WRITES_PER_MIN)))


@bp.get("/api/priorities/partner/tickets")
def priorities_partner_tickets():
    """The key's own vendor's non-dropped tickets, minimal fields: ?status= &week= &limit (100, at most 500) &offset -> {tickets, count, limit, offset}."""
    return jsonify(ptk.list_tickets(db.get_conn(), _partner(), _arg("status"), _arg("week"), _arg("limit"), _arg("offset")))


@bp.get("/api/priorities/partner/all-tickets")
def priorities_partner_all_tickets():
    """Every vendor's non-dropped tickets in full (brief, notes, spend and return, links, history), READ ONLY: ?vendor= ?status= ?week= ?product= &limit &offset -> {tickets, count, limit, offset}."""
    _partner()
    return jsonify(ptk.all_tickets(db.get_conn(), request.args))


@bp.get("/api/priorities/partner/all-tickets/<int:tid>")
def priorities_partner_one_ticket(tid: int):
    """One ticket of any vendor in full, with its comments and transcripts. Read only."""
    _partner()
    return jsonify(ptk.one_ticket(db.get_conn(), tid))


@bp.patch("/api/priorities/partner/tickets/<int:tid>")
def priorities_partner_ticket_patch(tid: int):
    """Fix one of the key's own tickets: concept_name, concept_kind, cluster, variations, brief_url, creative_url, story. Anything else is a 400; another vendor's ticket is a 404."""
    vendor = _partner()
    blocked = _partner_guard()
    if blocked is not None:
        return blocked
    return jsonify(ptk.patch(db.get_conn(), vendor, tid, _json_body(), auth.current_user()))


@bp.post("/api/priorities/partner/tickets/<int:tid>/creative")
def priorities_partner_creative(tid: int):
    """Add the creative (links) to one of the key's own tickets: {variations: [{label, url, concept_id?}] | creative_url, submit?} merged by label -> {ticket, submitted, status, message?}."""
    vendor = _partner()
    blocked = _partner_guard()
    if blocked is not None:
        return blocked
    return jsonify(ptk.add_creative(vendor, tid, _json_body(), auth.current_user()))


@bp.post("/api/priorities/partner/creative")
def priorities_partner_creative_batch():
    """Add creative links to several of the key's own tickets at once: {creatives: [{ticket_id, variations | creative_url, submit?}, ...]} (up to 100, all or nothing)."""
    vendor = _partner()
    blocked = _partner_guard()
    if blocked is not None:
        return blocked
    return jsonify(ptk.add_creative_batch(vendor, _json_body(), auth.current_user()))


@bp.post("/api/priorities/assignments")
def priorities_assignments_create():
    """Assign concepts: {keys: [row keys], snapshot_week?, vendor, for_week, note?, format_code?} -> {created: [assignment], skipped: [{key, reason}]}. Rows come from the stored snapshot; duplicates are skipped."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.create_assignments(db.get_conn(), _json_body(), auth.current_user())), 201


@bp.patch("/api/priorities/assignments/<int:aid>")
def priorities_assignment_update(aid: int):
    """Change any of vendor, for_week, note, format_code (blank or null clears it), brief_url, creative_url, tracker_concept_ids, ready, launched, dropped (the last three booleans set or clear their timestamp). There is no delete."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.update_assignment(db.get_conn(), aid, _json_body(), auth.current_user()))


@bp.post("/api/priorities/assignments/<int:aid>/move")
def priorities_assignment_move(aid: int):
    """Drag and drop on the board: {status, before_id?, after_id?, comment?} changes the status and the position in one step (before_id = put it just above that card, after_id = just below that card;
    neither = bottom of the column). -> {card, column: {status, ids}}. Same rules as PATCH status: Launched cannot be dragged to, review needs a creative link, Request changes needs a comment (400, plain English)."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.move_assignment(db.get_conn(), aid, _json_body(), auth.current_user()))


@bp.get("/api/priorities/assignments/<int:aid>/transcripts")
def priorities_transcripts(aid: int):
    """The transcripts of the ads on one card (made from the real audio of each video that is Ready to launch) -> {transcripts: [{concept_id, label, transcript, language, duration_s, engine, model, created_at}]}, Original first. Internal only."""
    return jsonify(pas.list_transcripts(db.get_conn(), aid))


@bp.get("/api/priorities/assignments/<int:aid>/comments")
def priorities_comments(aid: int):
    """The comment thread of one assignment, oldest first -> {comments: [{id, author_kind, author_name, body, created_at}]}."""
    return jsonify(pas.list_comments(db.get_conn(), aid))


@bp.post("/api/priorities/assignments/<int:aid>/comments")
def priorities_comment_create(aid: int):
    """Post a comment: {body} (1 to 2,000 characters) -> 201 {comment}. The author is the signed-in user (Team when none)."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.post_comment(db.get_conn(), aid, _json_body(), auth.current_user())), 201


@bp.get("/api/priorities/board")
def priorities_board():
    """The Production board: ?week= (snaps to Monday) &vendor= &product= &closed=1 -> {columns: [{status, label, count, cards}], dropped (with closed=1), capacity: [{vendor, per_week, open, week}], week, generated_at}.
    Cards are the assignment JSON without history, high priority first, then position. See app/priorities_assign.py (board)."""
    return jsonify(pas.board(db.get_conn(), week=_arg("week"), vendor=_arg("vendor"), product=_arg("product"), closed=request.args.get("closed") in ("1", "true")))


@bp.get("/api/priorities/vendor-capacity")
def priorities_vendor_capacity():
    """Concepts per week each vendor can take -> {capacity: [{vendor, per_week, updated_at}]} (10 where none is stored)."""
    return jsonify(pas.get_capacity(db.get_conn()))


@bp.put("/api/priorities/vendor-capacity")
def priorities_vendor_capacity_set():
    """{vendor, per_week} (a whole number 0 to 1000) -> {vendor, per_week, updated_at}. Signed-in users only: an API key cannot change capacity."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.set_capacity(_json_body(), auth.current_user()))


@bp.get("/api/priorities/vendor-links")
def priorities_vendor_links():
    """Every vendor link made so far -> {links: [{vendor, token, url, created_at, rotated_at}], vendors}."""
    return jsonify(pas.list_links(db.get_conn()))


@bp.post("/api/priorities/vendor-links")
def priorities_vendor_link_create():
    """{vendor} -> that vendor's permanent link (made on first use: 201, then 200)."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    link, created = pas.get_or_create_link(_json_body().get("vendor"))
    return jsonify(link), 201 if created else 200


@bp.post("/api/priorities/vendor-links/<vendor>/reset")
def priorities_vendor_link_reset(vendor: str):
    """Replace the vendor's token; the old link stops working."""
    blocked = _views_write_guard("_assign_limiter", "PRIORITIES_ASSIGN_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.reset_link(vendor))


@bp.get("/api/priorities/vendor/<token>")
def priorities_vendor(token: str):
    """The vendor link page's data (no login; the link is the access): that vendor's open assignments plus those launched in the last four weeks, with spend and return of their own ads,
    never the internal note or another vendor's rows -> {vendor, rows, weeks}."""
    return jsonify(pas.vendor_rows(db.get_conn(), token))


@bp.get("/api/priorities/vendor/<token>/<int:aid>/detail")
def priorities_vendor_detail(token: str, aid: int):
    """The same detail panel for a vendor, GET only and only for that vendor's own assignment (another vendor's row, a dropped row or an unknown id is a 404): no ad page links, no manual notes."""
    return jsonify(pdt.vendor_detail(db.get_conn(), token, aid))


@bp.get("/api/priorities/vendor/<token>/<int:aid>/comments")
def priorities_vendor_comments(token: str, aid: int):
    """The comment thread of the vendor's own assignment (another vendor's row, a dropped row or an unknown id is a 404) -> {comments: [{id, author_kind, author_name, body, created_at}]}."""
    return jsonify(pas.list_comments(db.get_conn(), aid, vendor=pas.vendor_of_token(db.get_conn(), token)))


@bp.post("/api/priorities/vendor/<token>/<int:aid>/comments")
def priorities_vendor_comment_create(token: str, aid: int):
    """A vendor posts a comment on their own assignment: {body} -> 201 {comment} with author_kind 'vendor' and the vendor's name."""
    blocked = _views_write_guard("_vendor_limiter", "PRIORITIES_VENDOR_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.post_comment(db.get_conn(), aid, _json_body(), vendor=pas.vendor_of_token(db.get_conn(), token))), 201


@bp.patch("/api/priorities/vendor/<token>/<int:aid>")
def priorities_vendor_update(token: str, aid: int):
    """A vendor edits their own row: {creative_url?, vendor_note?, ready?, status?, comment?} only (status: start, submit for review, pull back) (the creative format is shown to them but is not theirs to change: 400); another vendor's row is a 404."""
    blocked = _views_write_guard("_vendor_limiter", "PRIORITIES_VENDOR_PER_MIN", 60)
    if blocked is not None:
        return blocked
    return jsonify(pas.vendor_update(db.get_conn(), token, aid, _json_body()))


@bp.get("/api/trends")
def trends():
    """Trends: the key marketing metrics by day, week or month with the change vs the previous period (like for like for the open period).
    interval=day|week|month (default week), periods=<n> (default 14 / 12 / 12), numbers=own|nc|all (Meta / Google purchases and revenue basis). See app/trends.py."""
    return jsonify(trn.trends(db.get_conn(), request.args))


@bp.get("/api/trends/plan")
def trends_plan():
    """Trends, month grain: the Pacing page's projected / target / % diff per month (from=YYYY-MM-DD&to=YYYY-MM-DD). Answers 200 with available=false and the reason when the
    plan cannot be built, so the Trends table still renders. See app/trends.py (plan_payload)."""
    return jsonify(trn.plan_payload(request.args))


@bp.get("/api/daily-story")
def daily_story():
    """The daily story of one Pacific day (day=YYYY-MM-DD, default the newest): the short story, its facts (metrics against the same-weekday baseline, decomposition, cuts, change ledger,
    Meta / Google / email and text / Amazon sections), the days that have a story and the pattern scoreboard. Read-only; computed nightly by etl/daily_story.py. See app/daily_story.py."""
    return jsonify(dstory.build(db.get_conn(), request.args))


@bp.get("/api/inventory/history")
def inventory_history():
    """Inventory history: units on hand and allocated per product and location on one past day (date=YYYY-MM-DD, default the newest day with a snapshot), plus a daily
    trend per location over the last days=<n> days (default 30, at most 180) ending on that day. Estimate rows are marked, missing days are listed as gaps. See app/inventory_history.py."""
    return jsonify(invh.history(db.get_conn(), request.args))


@bp.get("/api/email/summary")
def email_summary():
    """Email page header: totals for a window (window=7d|30d|90d|all, kind=all|flows|campaigns, channel=all|email|sms) with the change against the prior period when it is fully loaded.
    Rates come from sums. Revenue is Klaviyo-attributed. See app/email_view.py and docs/EMAIL.md."""
    return jsonify(em.summary(em.DbSource(db.get_conn()), em.parse_params(request.args)))


@bp.get("/api/email/timeseries")
def email_timeseries():
    """Email over time: grain=day|week|month (default day), split=1 adds flows and campaigns separately when kind=all. A bucket that runs past the loaded days is marked partial."""
    return jsonify(em.timeseries(em.DbSource(db.get_conn()), em.parse_params(request.args), (_arg("grain") or "day").lower(), _arg("split") == "1"))


@bp.get("/api/email/flows")
def email_flows():
    """Email by flow: one row per flow with activity in the window, revenue first."""
    return jsonify(em.flows(em.DbSource(db.get_conn()), em.parse_params(request.args)))


@bp.get("/api/email/flows/<flow_id>/messages")
def email_flow_messages(flow_id: str):
    """The messages of one flow with their totals for the window."""
    return jsonify(em.flow_messages(em.DbSource(db.get_conn()), em.parse_params(request.args), flow_id))


@bp.get("/api/email/campaigns")
def email_campaigns():
    """Email by campaign: one row per campaign with activity in the window, newest send first; q= filters on name and subject."""
    return jsonify(em.campaigns(em.DbSource(db.get_conn()), em.parse_params(request.args), _arg("q")))


@bp.get("/api/email/message/<message_id>")
def email_message(message_id: str):
    """One message: its copy (latest version only), totals for the window and a daily series."""
    return jsonify(em.message(em.DbSource(db.get_conn()), em.parse_params(request.args), message_id))


@bp.get("/api/tag-lookup")
def tag_lookup():
    """Tag lookup: q=<an ad name as the platform shows it | a Meta ad id | a tracker Concept ID or Ad Family>. The new (line-mapped) tags of the matching tracker rows,
    the tracker's own tags beside them, the hook, a 30-day and since-launch performance line, and, with no match, the closest names (never a guessed tag).
    source / basis choose the numbers like everywhere else. See app/lookup.py and docs/TAG_LOOKUP.md."""
    return jsonify(tlk.lookup_db(db.get_conn(), request.args))


@bp.get("/api/platforms/overview")
def platforms_overview():
    """All platforms side by side for one window: per platform three rows (its own reporting, Upstack new customers, Upstack all orders). Spend, clicks and
    impressions are the platform's in every row. Stage / Active only / comparison do not apply (Google has none of them) and are ignored. 'Since launch' is the
    stretch both platforms have data for. Each row carries the window it actually covers: Upstack answers only for the days it has pulled."""
    conn = db.get_conn()
    meta = q.load_meta(conn)
    rows: dict[str, list] = {"meta": [], "google": []}
    notes: dict[str, str | None] = {"meta": None, "google": None}
    head = None
    try:
        for numbers, bkey in (("own", "reported"), ("nc", "nc"), ("all", "all")):
            source, basis = pf.NUMBERS[numbers]
            wins, skipped, extra = pf.windows(meta, request.args, numbers)
            for k, why in skipped.items():
                notes[k] = why
            if head is None and numbers == "own" and wins:
                head = pf.head_info(wins, extra["note"])
            q.set_source(source, basis)
            for key in pf.PLATFORMS:
                if key not in wins:
                    continue
                span, w = wins[key]
                if key == "meta":
                    extras: dict = {}
                    q.products_list(conn, span, w, with_total=True, extras=extras)
                    m = extras["total_all"]["m"]
                    info = w.info(span)
                    labels = q.META_LABELS if source == "meta" else q.UPSTACK_LABELS[basis]
                else:
                    m = gg.rollup(conn, span, w, "type")["total"]["m"]
                    info = gg.window_info(w, span)
                    labels = info["source"]["labels"]
                if extra["note"]:
                    info["note"] = extra["note"]
                rows[key].append({"basis": bkey, "label": pf.BASIS_LABEL[(key, numbers)], "labels": labels, "m": m, "window": pf.window_dict(info)})
    finally:
        q.set_source("meta")
    if head is None:
        head = q.parse_window(MultiDict({k: v for k, v in request.args.items() if k in ("window", "from", "to")}), meta).info(meta)
    return jsonify({"window_info": head, "compare_info": None,
                    "platforms": [{"key": k, "name": pf.NAMES[k], "rows": rows[k], "note": notes[k]} for k in pf.PLATFORMS]})


@bp.get("/media/<aint:asset_id>")
def media(asset_id: int):
    conn = db.get_conn()
    meta = q.media_meta(conn, asset_id)
    if meta is None:
        raise q.ApiError(404, "asset not found")
    etag = meta["etag"]
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if request.if_none_match.contains(etag) and not request.headers.get("Range"):      # unchanged: no bytes from the database, no body
        resp = Response(status=304, headers=headers)
        resp.set_etag(etag)
        return resp
    got = q.media(conn, asset_id)
    if got is None:
        raise q.ApiError(404, "asset not found")
    body, mime = got
    resp = Response(body, mimetype=mime, headers=headers)
    resp.set_etag(etag)
    return resp.make_conditional(request, accept_ranges=True, complete_length=len(body))


# ---- ad previews (feature-flagged by META_PREVIEW_TOKEN) -----------------------------------------------------------------


def _preview_response(ad_id_or_none, conn_lookup):
    svc: pv.PreviewService = current_app.extensions["preview"]
    if not svc.enabled:
        return jsonify(enabled=False)
    fmt = (request.args.get("format") or pv.DEFAULT_FORMAT).strip().lower()
    if fmt not in pv.FORMATS:
        raise q.ApiError(400, f"format must be one of {', '.join(pv.FORMATS)}")
    ad_id = conn_lookup()
    wait = svc.rate_check(request.remote_addr or "unknown")
    if wait:
        resp = jsonify(error="too many preview requests", retry_after=wait)
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp
    return jsonify(svc.get(ad_id, fmt))


@bp.get("/api/meta-ads/<ad_id>/preview")
def meta_ad_preview(ad_id: str):
    def lookup():
        if not pv.AD_ID_RE.fullmatch(ad_id) or not q.row_of(db.get_conn(), "SELECT 1 AS x FROM meta_ads WHERE ad_id = %s", (ad_id,)):
            raise q.ApiError(404, "ad not found")        # only ad ids we hold are ever sent to Meta
        return ad_id
    return _preview_response(ad_id, lookup)


@bp.get("/api/ads/<aint:row_id>/preview")
def tracker_ad_preview(row_id: int):
    def lookup():
        conn = db.get_conn()
        row = q.row_of(conn, "SELECT family_key FROM tracker_rows WHERE id = %s" + q.alive("tracker_rows"), (row_id,))
        if not row:
            raise q.ApiError(404, "ad not found")
        ad = q.family_links(conn, [row["family_key"]])[row["family_key"]]["ad_id"]
        if not ad:
            raise q.ApiError(404, "no Meta ad for this creative yet")
        return ad
    return _preview_response(None, lookup)
