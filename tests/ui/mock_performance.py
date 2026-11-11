"""Routes for Overview, Google, Campaigns and the platform trend / totals strip (split out of mock_server.py)."""
import io
import json
import math
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory

from mock_app import app  # noqa: F401
from mock_data import *  # noqa: F401,F403
from mock_data import _cpa, _roas  # noqa: F401


@app.get("/api/google/rollup")
def google_rollup():
    win = parse_win(request.args)
    group = request.args.get("group", "type")
    if group not in ("type", "campaign"):
        raise Bad("bad group")
    rows, total, info = g_rows(group, request.args, win)
    return jsonify(channel="google", group=group, type=request.args.get("type"), window=info, window_info=info, compare_info=None, rows=rows, total=dict(m=total))


@app.get("/api/google/trend")
def google_trend():
    win = parse_win(request.args)
    src = "upstack" if request.args.get("source") == "upstack" else "meta"
    i0, i1 = (0, N - 1) if win.get("launch") else (win["i0"], win["i1"])
    pts = []
    for i in range(i0, i1 + 1):
        sp = sum(g_day(ci, i)["spend"] for ci in range(len(G_CAMPS)))
        pu = sum(g_day(ci, i)["up" if src == "upstack" else "rp"] for ci in range(len(G_CAMPS)))
        rv = sum(g_day(ci, i)["uv" if src == "upstack" else "rv"] for ci in range(len(G_CAMPS)))
        pts.append(dict(t=DAYS[i].isoformat(), partial=False, spend=round(sp, 2), purchases=round(pu, 2), revenue=round(rv, 2), roas=round(rv / sp, 4), cpa=round(sp / pu, 2),
                        impressions=int(sp * 60), link_clicks=int(sp * 1.8), ctr=0.03, cpc=1.2, cpm=40.0, conv_rate=0.02))
    return jsonify(channel="google", window_info=g_info(win, src, request.args.get("basis", "nc")), compare_info=None, grain=request.args.get("grain", "day"), scope={}, points=pts)


@app.get("/api/campaigns")
def platform_campaigns():
    """Platform level (Meta): every campaign across the products. The mock has one product per campaign, so the biggest campaign is shown as Mixed (70 / 30 with
    a second product) to exercise that cell; the API tests cover the real split."""
    win = parse_win(request.args)
    slug = request.args.get("product")
    rows, tot = meta_rows(win, slug)
    names = {p["slug"]: p["name"] for p in PRODUCTS}
    brief = dict(slug=slug, name=names.get(slug, "No product")) if slug else None
    return jsonify(platform="meta", product=brief, scope="tracker+unmatched", window_info=win["info"], compare_info=cinfo(), products=product_chips(win), rows=rows, total=tot)


@app.get("/api/campaigns/trend")
def platform_campaigns_trend():
    win = parse_win(request.args)
    grain = request.args.get("grain", "day")
    split = request.args.get("split", "none")
    if grain not in ("day", "week", "month") or split not in ("none", "stage", "product"):
        raise Bad("bad grain or split")
    slug, cid = request.args.get("product"), request.args.get("campaign_id")
    ads_in = [a for a in cur_ads() if (not slug or a["product"] == slug) and (not cid or a["campaign_id"] == cid)]
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    out = dict(platform="meta", window_info=win["info"], compare_info=cinfo(), grain=grain, scope=dict(product=slug, campaign_id=cid), points=series_points(sum_series(ads_in, win["stage"]), lo, hi, grain))
    if getattr(g, "cmp", None):
        out["cmp_points"] = align_points(series_points(sum_series(ads_in, win["stage"]), g.cmp["i0"], g.cmp["i1"], grain), len(out["points"]))
    if split == "stage":
        out["groups"] = [dict(key=st, label=st.capitalize(), color=None, points=series_points(sum_series(ads_in, frozenset({st})), lo, hi, grain)) for st in ALL_STAGES if st in win["stage"]]
    elif split == "product":
        out["groups"] = [dict(key=p["slug"], label=p["name"], color=None, points=series_points(sum_series([a for a in ads_in if a["product"] == p["slug"]], win["stage"]), lo, hi, grain)) for p in PRODUCTS]
    return jsonify(out)


@app.get("/api/platforms/trend")
def platforms_trend():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    grain = request.args.get("grain", "day")
    if grain not in ("day", "week", "month"):
        raise Bad("bad grain")
    numbers = numbers_arg(request.args)
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    groups = []
    for key, name, color in (("meta", "Meta", "#1a5fb4"), ("google", "Google", "#d9822b")):
        if only and only != key or (key == "google" and slug) or (key == "meta" and typ):
            continue
        ads_f = [a for a in cur_ads() if a["product"] == slug] if (key == "meta" and slug) else None
        arr, lo, hi = platform_series(win, key, numbers, ads_f, typ)
        groups.append(dict(key=key, label=name, color=color, points=series_points(arr, lo, hi, grain)))
    labels = sorted({pt["t"] for gr in groups for pt in gr["points"]})
    comb = []
    for t in labels:
        col = [pt for gr in groups for pt in gr["points"] if pt["t"] == t]
        comb.append(dict(t=t, spend=round(sum(c["spend"] for c in col), 2), impressions=sum(c["impressions"] or 0 for c in col), link_clicks=sum(c["link_clicks"] or 0 for c in col)))
    notes = ["Upstack attribution is drawn only where Upstack has it: Meta from May 6, 2026, Google from May 13, 2025."] if numbers != "own" else []
    total = total_block(win, grain, labels, only, slug, typ)
    return jsonify(window_info=win["info"], compare_info=None, grain=grain, numbers=numbers, labels=labels, points=comb, groups=groups, total=total, notes=notes)


@app.get("/api/platforms/kpis")
def platforms_kpis():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    if only not in (None, "meta", "google"):
        raise Bad("only must be meta or google")
    if slug and typ:
        raise Bad("product and type cannot both be set")
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    view = only or ("meta" if slug else "google" if typ else None)
    if view:                                           # one platform's own figures on the Numbers basis: the subtotal of the All campaigns table for the same arguments
        numbers = numbers_arg(request.args)
        res = campaigns_all_platforms().get_json()
        m = res["subtotals"][view]["m"]
        spend = round(m["spend"], 2)
        scope = next(pr["name"] for pr in PRODUCTS if pr["slug"] == slug) if slug else typ
        other = "google" if view == "meta" else "meta"
        total = dict(view=view, name=view.capitalize() + (f" · {scope}" if scope else ""), platform=view.capitalize(), numbers=numbers, basis=res["basis"][view],
                     labels=dict(zip(("revenue", "roas"), dict(own=("Revenue", "ROAS"), nc=("NC revenue", "NC ROAS"), all=("Revenue", "ROAS"))[numbers])),
                     spend=spend, **{f"{view}_spend": spend, f"{other}_spend": 0.0, f"{view}_share": 1.0, f"{other}_share": 0.0},
                     window=dict(**{"from": DAYS[lo].isoformat(), "to": DAYS[hi].isoformat()}, days=hi - lo + 1),
                     m={k: m.get(k) for k in ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks")})
        return jsonify(view=view, filters=dict(only=only, product=slug, type=typ), total=total, compare=None, delta=None, numbers=numbers, notes=[], platform_windows={}, window_info=win["info"])
    out = dict(view="all", filters=dict(only=only, product=slug, type=typ), total=kpi_block(lo, hi, only, slug, typ), compare=None, delta=None,
               orders=dict(first=DAYS[ORDERS_FROM].isoformat(), last=DAYS[-1].isoformat()), platform_windows={}, notes=[], window_info=win["info"])
    if getattr(g, "cmp", None):
        b = kpi_block(g.cmp["i0"], g.cmp["i1"], only, slug, typ)
        out["compare"] = b
        out["compare_info"] = g.cmp["info"]
        ta, tb = out["total"], b
        pct = lambda x, y: round((x - y) / y, 4) if x is not None and y else None
        out["delta"] = dict(spend_pct=pct(ta["spend"], tb["spend"]), nc_rev_pct=pct(ta["nc"].get("rev"), tb["nc"].get("rev")), nc_roas_pct=pct(ta["nc"].get("roas"), tb["nc"].get("roas")))
    return jsonify(out)


@app.get("/api/campaigns/all")
def campaigns_all_platforms():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    numbers = numbers_arg(request.args)
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    if slug and typ:
        raise Bad("product and type cannot both be set")
    basis_label = {("meta", "own"): "Meta's own reporting", ("google", "own"): "Google's own reporting", ("meta", "nc"): "Upstack · new customers", ("google", "nc"): "Upstack · new customers",
                   ("meta", "all"): "Upstack · all orders", ("google", "all"): "Upstack · all orders"}
    rows, subtotals = [], {}
    if (only in (None, "meta")) and not typ:
        mrows, mtot = meta_rows(win, slug)
        k = dict(own=(1.0, 1.0), nc=(0.7, 0.72), all=(1.2, 1.1))[numbers]
        for r in mrows:
            m = {kk: vv for kk, vv in r.items() if kk not in ("products", "spark")}
            m["purchases"] = round(m["purchases"] * k[0]); m["revenue"] = round(m["revenue"] * k[1], 2)
            m["roas"] = _roas(m["spend"], m["revenue"]); m["cpa"] = _cpa(m["spend"], m["purchases"])
            rows.append(dict(key="meta:" + r["campaign_id"], platform="meta", platform_name="Meta", name=r["name"], campaign_id=r["campaign_id"], group=r["product"], group_kind="product",
                             product_slug=r["product_slug"], products=r["products"], account=r["account"], stage=r["stage"], ads=r["ads"], families=r["families"], live=r["live"],
                             last_day=r["last_day"], status=None, basis=basis_label[("meta", numbers)], spark=r.get("spark"), m=m))
        mt = {kk: vv for kk, vv in mtot.items() if kk != "spark"}
        mt["purchases"] = round(mt["purchases"] * k[0]); mt["revenue"] = round(mt["revenue"] * k[1], 2); mt["roas"] = _roas(mt["spend"], mt["revenue"]); mt["cpa"] = _cpa(mt["spend"], mt["purchases"])
        subtotals["meta"] = dict(campaigns=len(mrows), ads=mt.get("ads"), m=mt)
    if (only in (None, "google")) and not slug:
        grows, gtot, _info = g_rows("campaign", dict(source="upstack" if numbers != "own" else "meta", basis="all" if numbers == "all" else "nc", **({"type": typ} if typ else {})), win)
        for r in grows:
            rows.append(dict(key="google:" + r["key"], platform="google", platform_name="Google", name=r["label"], campaign_id=r["key"], group=r["campaign_type"], group_kind="type", product_slug=None,
                             products=None, account=None, stage=None, ads=None, families=None, live=None, last_day=None, status=r["status"], basis=basis_label[("google", numbers)], spark=None, m=r["m"]))
        subtotals["google"] = dict(campaigns=len(grows), ads=None, m=gtot)
    rows.sort(key=lambda r: -r["m"]["spend"])
    tot_sp = sum(s_["m"]["spend"] for s_ in subtotals.values())
    for r in rows:
        r["m"]["spend_share"] = round(r["m"]["spend"] / tot_sp, 4) if tot_sp else None
    for s_ in subtotals.values():
        s_["m"]["spend_share"] = round(s_["m"]["spend"] / tot_sp, 4) if tot_sp else None
    imp = sum(s_["m"].get("impressions") or 0 for s_ in subtotals.values()); clk = sum(s_["m"].get("link_clicks") or 0 for s_ in subtotals.values())
    total = dict(spend=round(tot_sp, 2), impressions=imp, link_clicks=clk, campaigns=sum(s_["campaigns"] for s_ in subtotals.values()), spend_share=1.0 if tot_sp else None,
                 ctr=round(clk / imp, 5) if imp else None, cpc=round(tot_sp / clk, 4) if clk else None, additive=len(subtotals) == 1)
    gtypes = g_rows("type", dict(source="meta"), win)[0]
    plat_win = {k: {"from": win["info"].get("from"), "to": win["info"].get("to"), "days": win["info"].get("days"), "label": win["info"].get("label"), "note": None, "clamped": False} for k in subtotals}
    return jsonify(numbers=numbers, platform=only, window_info=win["info"], platform_windows=plat_win, rows=rows, subtotals=subtotals, total=total, notes=[], basis={k: basis_label[(k, numbers)] for k in subtotals},
                   platforms=[dict(key="meta", name="Meta", spend=round(sum(c["spend"] for c in product_chips(win)), 2)), dict(key="google", name="Google", spend=round(sum(r["m"]["spend"] for r in gtypes), 2))],
                   products=product_chips(win), types=[dict(key=r["key"], name=r["label"], spend=r["m"]["spend"]) for r in gtypes])


@app.get("/api/platforms/overview")
def platforms_overview():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    _rows, _bs, tot = campaign_payload(win, None)
    imp, clk = tot.get("impressions"), tot.get("link_clicks")

    def meta_m(pu, rv):
        sp = tot["spend"]
        return dict(spend=sp, purchases=pu, revenue=round(rv, 2), roas=round(rv / sp, 4) if sp else None, cpa=round(sp / pu, 2) if pu else None, impressions=imp, link_clicks=clk,
                    ctr=round(clk / imp, 5) if imp and clk else None, cpc=round(sp / clk, 4) if clk else None)
    meta_rows = [dict(basis="reported", label="Meta's own reporting", labels=dict(purchases="Purchases"), m=meta_m(tot["purchases"], tot["revenue"])),
                 dict(basis="nc", label="Upstack · new customers", labels=dict(purchases="New customers"), m=meta_m(round(tot["purchases"] * 0.7), tot["revenue"] * 0.72)),
                 dict(basis="all", label="Upstack · all orders", labels=dict(purchases="Orders"), m=meta_m(round(tot["purchases"] * 1.2), tot["revenue"] * 1.1))]
    for r in meta_rows:
        r["window"] = dict(win["info"], clamped=False)
    g_rows_out = []
    for basis, src, label in (("reported", "meta", "Google's own reporting"), ("nc", "upstack", "Upstack · new customers"), ("all", "upstack", "Upstack · all orders")):
        _r, total, info = g_rows("type", dict(source=src, basis="all" if basis == "all" else "nc"), win)
        g_rows_out.append(dict(basis=basis, label=label, labels=info["source"]["labels"], m=total, window=dict(win["info"], clamped=False)))
    return jsonify(window_info=win["info"], compare_info=None, platforms=[dict(key="meta", name="Meta", rows=meta_rows, note=None), dict(key="google", name="Google", rows=g_rows_out, note=None)])
