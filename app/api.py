"""HTTP layer: parameter parsing + JSON shaping. All SQL lives in app/queries.py."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, Response, jsonify, request

from . import db, queries as q

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
        info["note"] = base + (f" {w.note}" if w.note else "")
    return info


@bp.get("/api/status")
def status():
    conn, meta, _ = _ctx(with_window=False)
    n = len(q.rows_of(conn, """SELECT p.id FROM products p WHERE p.active AND (
            EXISTS (SELECT 1 FROM tracker_rows r WHERE r.product_id = p.id)
            OR EXISTS (SELECT 1 FROM unmatched_meta u WHERE u.product_id = p.id AND u.spend_all > 0))"""))
    return jsonify(q.status(conn, meta, n))


@bp.get("/api/products")
def products():
    # The contract specifies a bare array, so there is no room for window_info here; /rollup and the other endpoints carry it.
    # `?envelope=1` returns {window_info, compare_info, rows} instead, for clients that need the window labels on the landing page.
    conn, meta, w = _ctx()
    rows = q.products_list(conn, meta, w)
    if request.args.get("envelope") == "1":
        return jsonify({"window_info": w.info(meta), "compare_info": w.compare_info(meta), "rows": rows})
    return jsonify(rows)


@bp.get("/api/products/<slug>/rollup")
def rollup(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    level = (_arg("level") or "cluster").lower()
    cluster, icp = _arg("cluster"), _arg("icp")
    res = q.rollup(conn, meta, w, product, level, cluster, icp)
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "level": level, "filters": {"cluster": cluster, "icp": icp},
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/families")
def families(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    cluster, icp, pain = _arg("cluster"), _arg("icp"), _arg("pain")
    res = q.families(conn, meta, w, product, cluster, icp, pain)
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
    res = q.tree(conn, meta, w, product, root, flt["cluster"], flt["icp"], flt["pain"], int(raw_depth))
    info = w.info(meta)
    return jsonify({"product": q.product_brief(product), "root": root, "depth": int(raw_depth), "filters": flt,
                    "window": info, "window_info": info, "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/ads")
def ads(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    flt = {"cluster": _arg("cluster"), "icp": _arg("icp"), "pain": _arg("pain"),
           "family_key": _arg("family_key"), "campaign_id": _arg("campaign_id")}
    res = q.meta_ads(conn, meta, w, product, **flt)
    return jsonify({"product": q.product_brief(product), "filters": flt, "window_info": w.info(meta),
                    "compare_info": w.compare_info(meta), **res})


@bp.get("/api/products/<slug>/campaigns")
def campaigns(slug: str):
    conn, meta, w = _ctx()
    product = q.get_product(conn, slug)
    res = q.campaigns(conn, meta, w, product)
    return jsonify({"product": q.product_brief(product), "window_info": _all_days_info(w, meta),
                    "compare_info": w.compare_info(meta), **res})


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
