"""HTTP layer: parameter parsing + JSON shaping. All SQL lives in app/queries.py."""
from __future__ import annotations

import datetime as dt
import re

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.datastructures import MultiDict

from . import auth, db, google as gg, platforms as pf, preview as pv, lookup as tlk, promotions as promo, queries as q, trends as trn

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
    return jsonify({**q.status(conn, meta, n), "auth": "disabled" if auth.auth_disabled() else "enabled", "user": auth.current_user()})


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


@bp.get("/api/promotions")
def promotions():
    """Test to scale (Meta only): creatives moved from Testing to Scaling in the window, those launched and not scaled, the funnel by launch week and a chart series.
    window / from / to / days (default the last 30 days), product=<slug>|none, hide_bulk=1, bulk_min=<n>, min_test_spend=<$>, grain=auto|day|week, source / basis. See app/promotions.py."""
    conn = db.get_conn()
    meta = q.meta_for_source(q.load_meta(conn), q.parse_source(request.args)[0])
    return jsonify(promo.promotions(conn, meta, request.args, product_slug=_arg("product")))


@bp.get("/api/trends")
def trends():
    """Trends: the key marketing metrics by day, week or month with the change vs the previous period (like for like for the open period).
    interval=day|week|month (default week), periods=<n> (default 14 / 12 / 12), numbers=own|nc|all (Meta / Google purchases and revenue basis). See app/trends.py."""
    return jsonify(trn.trends(db.get_conn(), request.args))


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
