"""HTTP layer: parameter parsing + JSON shaping. All SQL lives in app/queries.py."""
from __future__ import annotations

import datetime as dt
import re

from flask import Blueprint, Response, current_app, jsonify, request

from . import auth, db, preview as pv, queries as q

bp = Blueprint("api", __name__)


def _arg(name: str) -> str | None:
    v = request.args.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _ctx(with_window: bool = True):
    conn = db.get_conn()
    meta = q.load_meta(conn)
    w = q.parse_window(request.args, meta) if with_window else None
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
    n = len(q.rows_of(conn, """SELECT p.id FROM products p WHERE p.active AND (
            EXISTS (SELECT 1 FROM tracker_rows r WHERE r.product_id = p.id)
            OR EXISTS (SELECT 1 FROM unmatched_meta u WHERE u.product_id = p.id AND u.spend_all > 0))"""))
    return jsonify({**q.status(conn, meta, n), "auth": "disabled" if auth.auth_disabled() else "enabled", "user": auth.current_user()})


@bp.get("/api/products")
def products():
    # The contract specifies a bare array, so there is no room for window_info here; /rollup and the other endpoints carry it.
    # `?envelope=1` returns {window_info, compare_info, rows, total:{m, rows, spark?}} instead: the form the landing page uses (labels + the Total row).
    conn, meta, w = _ctx()
    spark = q.parse_spark(request.args)
    if request.args.get("envelope") == "1":
        rows, total = q.products_list(conn, meta, w, spark=spark, with_total=True)
        return jsonify({"window_info": w.info(meta), "compare_info": w.compare_info(meta), "rows": rows, "total": total})
    return jsonify(q.products_list(conn, meta, w, spark=spark))


@bp.get("/api/products/<slug>/rollup")
def rollup(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    level = (_arg("level") or "cluster").lower()
    cluster, icp = _arg("cluster"), _arg("icp")
    res = q.rollup(conn, meta, w, product, level, cluster, icp, spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "level": level, "filters": {"cluster": cluster, "icp": icp},
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/families")
def families(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    cluster, icp, pain = _arg("cluster"), _arg("icp"), _arg("pain")
    res = q.families(conn, meta, w, product, cluster, icp, pain, spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "filters": {"cluster": cluster, "icp": icp, "pain": pain},
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/tree")
def tree(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    root = (_arg("root") or "cluster").lower()
    raw_depth = _arg("depth") or "0"
    if not raw_depth.isdigit() or int(raw_depth) > 3:
        raise q.ApiError(400, "depth must be 0, 1, 2 or 3")
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain")}
    res = q.tree(conn, meta, w, product, root, flt["cluster"], flt["icp"], flt["pain"], int(raw_depth), spark=q.parse_spark(request.args))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "root": root, "depth": int(raw_depth), "filters": flt,
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/ads")
def ads(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain"),
           "family_key": _arg("family_key"), "campaign_id": _arg("campaign_id")}
    res = q.meta_ads(conn, meta, w, product, spark=q.parse_spark(request.args), **flt)
    return jsonify({"product": q.product_brief(product), "filters": flt, "window_info": w.info(meta),
                    "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/campaigns")
def campaigns(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.campaigns(conn, meta, w, product, spark=q.parse_spark(request.args))
    return jsonify({"product": q.product_brief(product), "window_info": _all_days_info(w, meta),
                    "compare_info": w.compare_info(meta), **res})


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
    return jsonify({"product": q.product_brief(product), "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/stage-daily")
def stage_daily(slug: str):
    conn, meta, _ = _ctx(with_window=False)
    product = q.get_product(conn, slug)
    if any(request.args.get(k) for k in ("window", "from", "to")):
        w = q.parse_window({k: v for k, v in request.args.items() if k not in ("compare", "cfrom", "cto")}, meta)   # no comparison here
        df, dtt = w.span(meta)
    else:
        raw = _arg("days") or "90"
        if not raw.isdigit() or not (1 <= int(raw) <= 366):
            raise q.ApiError(400, "days must be an integer from 1 to 366")
        dtt = meta.data_through
        df = max(meta.daily_start, dtt - dt.timedelta(days=int(raw) - 1))
    return jsonify(q.stage_daily(conn, meta, product, df, dtt, q.stage_list(q.parse_stage(request.args))))


@bp.get("/api/products/<slug>/unmatched")
def unmatched(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.unmatched(conn, meta, w, product)
    return jsonify({"product": q.product_brief(product), "window_info": _all_days_info(w, meta),
                    "compare_info": w.compare_info(meta), **res})


@bp.get("/api/ads/<int:row_id>")
def ad_detail(row_id: int):
    conn, meta, w = _ctx()
    res = q.ad_detail(conn, meta, w, row_id)
    res["window_info"] = w.info(meta)
    res["compare_info"] = w.compare_info(meta)
    return jsonify(res)


@bp.get("/media/<int:asset_id>")
def media(asset_id: int):
    conn = db.get_conn()
    got = q.media(conn, asset_id)
    if got is None:
        raise q.ApiError(404, "asset not found")
    body, mime = got
    resp = Response(body, mimetype=mime)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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


@bp.get("/api/ads/<int:row_id>/preview")
def tracker_ad_preview(row_id: int):
    def lookup():
        conn = db.get_conn()
        row = q.row_of(conn, "SELECT family_key FROM tracker_rows WHERE id = %s", (row_id,))
        if not row:
            raise q.ApiError(404, "ad not found")
        ad = q.family_links(conn, [row["family_key"]])[row["family_key"]]["ad_id"]
        if not ad:
            raise q.ApiError(404, "no Meta ad for this creative yet")
        return ad
    return _preview_response(None, lookup)
