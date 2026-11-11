"""Routes for Meta products, the tag tree, the product page, ads, the ad page, trend, preview and media (split out of mock_server.py)."""
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


@app.get("/api/products")
def products():
    win = parse_win(request.args)
    out = []
    for p in PRODUCTS:
        ents = prod_items(p["slug"])
        d = product_json(p)
        d["rows"] = len(ents)
        d["m"] = make_m(items_for_m(ents), win)
        out.append(d)
    tot = dict(spend=sum(d["m"]["spend"] for d in out))
    if g.cmp:
        tot["cmp"] = dict(spend=sum(d["m"]["cmp"]["spend"] for d in out))
        tot["delta"] = {}
    shares([d["m"] for d in out], tot)
    if request.args.get("envelope"):
        all_ents = [e for pr in PRODUCTS for e in prod_items(pr["slug"])]
        tm = make_m(items_for_m(all_ents), win)
        um = unmatched_total_m(win)
        ta = merge_m(tm, um, win)
        # % of spend on the landing is relative to the Meta account total (tracker products + not on the tracker)
        for obj in [d["m"] for d in out] + [tm, um]:
            obj["spend_share"] = round(obj["spend"] / ta["spend"], 6) if ta["spend"] else None
            if obj.get("cmp"):
                obj["cmp"]["spend_share"] = round(obj["cmp"]["spend"] / ta["cmp"]["spend"], 6) if ta["cmp"]["spend"] else None
                obj["delta"]["spend_share_pp"] = None if obj["spend_share"] is None or obj["cmp"]["spend_share"] is None else round((obj["spend_share"] - obj["cmp"]["spend_share"]) * 100, 4)
        ta["spend_share"] = 1.0
        if ta.get("cmp"):
            ta["cmp"]["spend_share"] = 1.0
            ta["delta"]["spend_share_pp"] = 0.0
        return jsonify(window_info=win["info"], compare_info=cinfo(), rows=out, total=dict(m=tm, rows=sum(d["rows"] for d in out)),
                       unmatched=dict(m=um), total_all=dict(m=ta))
    return jsonify(out)


@app.get("/api/products/<slug>/rollup")
def rollup(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    level = request.args.get("level", "cluster")
    if level not in LEVEL_NAMES:
        raise Bad("bad level")
    flt = {k: request.args.get(k) for k in ("cluster", "icp") if request.args.get(k)}
    rows, ents = rollup_rows(slug, level, flt, win)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([r["m"] for r in rows], total["m"])
    attach_sparks(rows, win, lambda r: fams_ads(r["_fams"]))
    for r in rows:
        r.pop("_fams", None)
    saved, g.cmp = g.cmp, None
    um = unmatched_rows(slug, parse_win({}))
    g.cmp = saved
    um7 = sum(u["spend_7d"] for u in um)
    return jsonify(product=product_json(p), level=level, filters=dict(cluster=request.args.get("cluster"), icp=request.args.get("icp")),
                   window=win["info"], window_info=win["info"], compare_info=cinfo(), rows=rows, total=total,
                   unmatched=dict(spend_7d=round(um7, 2), spend_30d=round(um7 * 4.1, 2), live_families=sum(1 for u in um if u["live"]), families=len(um)))


@app.get("/api/products/<slug>/families")
def families(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    flt = {k: request.args.get(k) for k in ("cluster", "icp", "pain") if request.args.get(k)}
    rows, ents = family_rows(slug, flt, win)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([r["m"] for r in rows], total["m"])
    attach_sparks(rows, win, lambda r: fams_ads(r["_fams"]))
    for r in rows:
        r.pop("group", None)
        r.pop("_fams", None)
    return jsonify(product=product_json(p), filters={k: request.args.get(k) for k in ("cluster", "icp", "pain")}, window=win["info"], window_info=win["info"], compare_info=cinfo(),
                   rows=rows, total=total)


@app.get("/api/products/<slug>/tree")
def tree(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    root = request.args.get("root", "cluster")
    levels = {"cluster": 0, "icp": 1, "pain": 2, "family": 3}
    if root not in levels:
        raise Bad("bad root")
    r0 = levels[root]
    try:
        depth = int(request.args.get("depth", 0))
    except ValueError:
        raise Bad("bad depth")
    if not 0 <= depth <= 3:
        raise Bad("bad depth")
    base = {k: request.args.get(k) for k in ("cluster", "icp", "pain") if request.args.get(k)}
    need = ("cluster", "icp", "pain")[:r0]
    if any(k not in base for k in need):
        raise Bad(f"root={root} needs " + "+".join(need))
    out = []
    spark_spec = spark_request()

    def key_of(level, flt, label=None, fam=None):
        parts = [flt.get("cluster"), flt.get("icp"), flt.get("pain")][:level]
        if level < 3 and label is not None:
            parts.append(label)
        if level == 3:
            parts.append(fam)
        return "|".join(parts)

    def emit(level, flt, parent_key):
        if level < 3:
            rows, _ = rollup_rows(slug, LEVEL_NAMES[level], flt, win)
        else:
            rows, _ = family_rows(slug, flt, win)
        for row in rows:
            if level < 3:
                label = row["label"]
                node = dict(key=key_of(level, flt, label), parent_key=parent_key, level=level, label=label, cluster=row["cluster"] if level >= 0 else None,
                            icp=row["icp"], pain=row["pain"], family_key=None, primary_id=None, color=row["color"], line_code=row["line_code"], trigger=row["trigger"],
                            confidence=None, ad_type=None, status=None, thumb_asset_id=None, has_children=True, child_count=row["child_count"], counted_here=True, m=row["m"])
                child_flt = {**flt, LEVEL_NAMES[level]: label}
            else:
                grp = row["group"]
                node = dict(key=key_of(3, dict(cluster=grp[0], icp=grp[1], pain=grp[2]), fam=row["family_key"]), parent_key=parent_key, level=3, label=row["display"],
                            cluster=grp[0], icp=grp[1], pain=grp[2], family_key=row["family_key"], primary_id=row["primary_id"], color=None, line_code=None, trigger=None,
                            confidence=row["confidence"], ad_type=row["ad_type"], status=row["status"], thumb_asset_id=row["thumb_asset_id"], has_children=True,
                            launch_date=row["launch_date"], ads_count=row["ads_count"], ads_live=row["ads_live"],
                            child_count=sum(1 for a in cur_ads() if a["fam"] == row["family_key"]), counted_here=row["counted_here"], m=row["m"])
            if spark_spec:
                node["spark"] = spark_for(fams_ads(row["_fams"]), win, spark_spec)
            out.append(node)
            if level - r0 < depth and level < 3:
                emit(level + 1, child_flt, node["key"])

    parent0 = key_of(r0 - 1, base) if r0 > 0 else None
    emit(r0, base, parent0)
    ents = filt(prod_items(slug), base)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([n["m"] for n in out], total["m"])
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=out, total=total)


@app.get("/api/products/<slug>/coverage")
def coverage(slug):
    """Synthetic coverage for the UI tests: the real per-line numbers of the mock, with L02 forced to 'no ads', L03 to '$0 spend' and L04 to 'added after tagging'
    so every section of the Not tested panel has a row."""
    win = parse_win(request.args)
    p = prod_meta(slug)
    if p["tag_set"] != "linemap":
        return jsonify(available=False, reason="Tracker tags, no line list.", summary=None, rows=[], keys={}, window_info=win["info"], compare_info=None)
    force = {"L02": "no_ads", "L03": "no_spend", "L04": "added"}
    keys, rows, cnt = {}, [], {"total": 0, "no_ads": 0, "no_spend": 0, "added_after_tagging": 0, "tested": 0}
    for code, cluster, icp, pain, trig in VB_LINES:
        st = {"no_ads": "no_ads", "no_spend": "no_spend", "added": "no_ads"}.get(force.get(code), "tested")
        added = force.get(code) == "added"
        keys["|".join([cluster, icp, pain])] = {"state": st, "added_after_tagging": added}
        cnt["total"] += 1
        cnt["added_after_tagging" if added else st] += 1
        if st != "tested":
            rows.append(dict(level=2, key="|".join([cluster, icp, pain]), cluster=cluster, icp=icp, pain=pain, label=pain, line_code=code, trigger=trig, color=None,
                             state=st, added_after_tagging=added, unique_ads=0 if st == "no_ads" else 2, spend=0.0))
    summ = dict(cnt, not_tested=cnt["no_ads"] + cnt["no_spend"] - 0)
    icps = dict(total=1, tested=1, no_ads=0, no_spend=0, added_after_tagging=0, not_tested=0)
    return jsonify(available=True, summary=dict(pain_points=summ, icps=icps), rows=rows, keys=keys, window_info=win["info"], compare_info=None)


@app.get("/api/products/<slug>/unmatched")
def unmatched(slug):
    win = parse_win(request.args)
    rows = unmatched_rows(slug, win)
    tot = dict(spend=round(sum(r["spend"] for r in rows), 2), purchases=sum(r["purchases"] for r in rows), revenue=round(sum(r["revenue"] for r in rows), 2),
               spend_7d=round(sum(r["spend_7d"] for r in rows), 2), families=len(rows))
    tot["roas"] = _roas(tot["spend"], tot["revenue"]); tot["cpa"] = _cpa(tot["spend"], tot["purchases"])
    tot.update(flat_traffic(rows, tot["spend"], tot["purchases"]))
    sum_cmp(tot, rows, win)
    shares(rows, tot)
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=rows, total=tot)


@app.get("/api/products/<slug>/ads")
def ads(slug):
    win = parse_win(request.args)
    prod_meta(slug)
    rows = ads_for(slug, request.args, win)
    spec = spark_request()
    if spec:
        by_id = {a["ad_id"]: a for a in cur_ads()}
        for r in rows:
            r["spark"] = spark_for([by_id[r["ad_id"]]], win, spec) if r["ad_id"] in by_id else None
    tot = total_of(rows, win)
    shares(rows, tot)
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=rows, total=tot)


@app.get("/api/products/<slug>/campaigns")
def campaigns(slug):
    win = parse_win(request.args)
    prod_meta(slug)
    rows, by_stage, tot = campaign_payload(win, {slug})
    return jsonify(window_info=win["info"], compare_info=cinfo(), by_stage=by_stage, rows=rows, total=tot)


@app.get("/api/products/<slug>/stage-daily")
def stage_daily(slug):
    prod_meta(slug)
    if any(k in request.args for k in ("window", "from", "to")):
        win = parse_win({k: v for k, v in request.args.items() if k != "stage"})
        lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    else:
        days = int(request.args.get("days", 90))
        lo, hi = max(0, N - days), N - 1
    out = []
    for i in range(lo, hi + 1):
        d = dict(day=DAYS[i].isoformat(), testing=0.0, scaling=0.0, unclassified=0.0)
        for ad in cur_ads():
            if ad["product"] == slug:
                d["unclassified" if i < UNC_DAYS else ad_stage(ad)] += ad["daily"][i][0]
        d = {k: (round(v, 2) if k != "day" else v) for k, v in d.items()}
        out.append(d)
    return jsonify(out)


@app.get("/api/ads/<int:rid>")
def ad_detail(rid):
    row = ROW_BY_ID.get(rid)
    if not row:
        return jsonify(error="not found"), 404
    win = parse_win(request.args)
    fam = FAM_BY_KEY[row["fam"]]
    p = prod_meta(row["product"])
    linemap = p["tag_set"] == "linemap"
    ln = row["line"]
    tag = None
    line = None
    if linemap and ln:
        tag = dict(tag_set="linemap", version="linemap-v2-2026-11-03", line_code=ln[0], cluster=ln[1], icp=ln[2], pain_point=ln[3], emotional_trigger=ln[4],
                   confidence=row["conf"], method=row["method"], seen="A man at a kitchen table holding the bottle; text overlay about testosterone.",
                   why=f"Hook speaks to {ln[3].lower()} for {ln[2]}.", runner_up="L07 · Busy Provider Dad / Afternoon Crash",
                   copy_only_line=dict(line_code="L02", label="Low Drive") if row["conf"] != "high" else None, reliability_note=reliability(row))
        line = dict(line_code=ln[0], story=f"{ln[2]} feels {ln[3].lower()} and wants it fixed without a prescription.", emotional_trigger=ln[4],
                    ingredients=[dict(ingredient=i, attacks=a, supports=s) for i, a, s in INGREDIENTS[:2]])
    is_video = fam["ad_type"] == "VID"
    xss = fam["display"].startswith("XSS")
    content = dict(primary_text=[XSS if xss else f"Tired of {ln[3].lower() if ln else 'feeling run down'}?\nHere is what 3 studies say about {row['ingredient']}.", "Second primary text variant, shorter."],
                   headlines=["Built for men over 40", "The 90-day guarantee"], description="Free shipping on 3+ bottles.", meta_body="Meta body text as running.",
                   meta_title="Vita Bull", meta_desc="Beef organ complex", transcript=("So here's the thing about testosterone... " * 30) if is_video else None, is_video=is_video)
    nxt = [r for r in fam["rows"] if r["id"] != rid]
    L = parse_win({}); D7 = parse_win({"window": "7d"}); D30 = parse_win({"window": "30d"})

    def fm(w):
        sp, pu, rv = metrics_of(fam_split(fam["key"], w)[0], w)
        return dict(spend=round(sp, 2), purchases=int(round(pu)), revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu))
    live = fam_split(fam["key"], L)[2]
    cmp_fam = {}
    if getattr(g, "cmp", None):
        fa, fb = fm(win), fm(g.cmp)
        cmp_fam = dict(compare=fb, delta=delta_block(fa, fb, win, g.cmp))
    series = [0, 0, 0]
    daily = []
    fam_ads = [a for a in cur_ads() if a["fam"] == fam["key"]]
    for i in range(N):
        sp = sum(a["daily"][i][0] for a in fam_ads); pu = sum(a["daily"][i][1] for a in fam_ads); rv = sum(a["daily"][i][2] for a in fam_ads)
        known = [a["tr"][i][0] for a in fam_ads if a["tr"][i][0] is not None]
        daily.append(dict(day=DAYS[i].isoformat(), spend=round(sp, 2), purchases=pu, revenue=round(rv, 2), link_clicks=sum(a["tr"][i][1] for a in fam_ads),
                          impressions=sum(known) if known else None))
    meta_ads = [ad_row(a, win, fam["launch"]) for a in fam_ads]
    spec = spark_request()
    if spec:
        for r, a in zip(meta_ads, fam_ads):
            r["spark"] = spark_for([a], win, spec)
    shares(meta_ads, total_of(meta_ads, win))
    camps = {}
    for r in meta_ads:
        e = camps.setdefault(r["campaign_id"], dict(campaign_id=r["campaign_id"], name=r["campaign_name"], stage=r["stage"], spend=0.0, purchases=0, revenue=0.0))
        e["spend"] += r["spend"]; e["purchases"] += r["purchases"]; e["revenue"] += r["revenue"]
    asset_ids = [fam["thumb"]] if fam["thumb"] else []
    frames = [f"f{rid}{k}" for k in range(4)] if is_video else []
    links = ad_links(fam_ads[0]) if fam_ads else dict(story_url=None, ads_manager_url=None, ad_id=None, account=None)
    return jsonify(id=rid, links=links, product=dict(slug=p["slug"], name=p["name"]), concept_id=row["concept_id"], ad_family=fam["display"], family_key=fam["key"], sheet_row=row["sheet_row"],
                   status=fam["status"], ad_type=fam["ad_type"], style="UGC" if is_video else "Static", vendor="Madre", launch_date=fam["launch"].isoformat() if fam["launch"] else None,
                   concept_name="Doctor says no", description="Founder explains the study.", hypothesis="Skeptics respond to cited studies.", meta_ad_name=fam["display"],
                   story_url="https://example.com/story", asset_url="javascript:alert(1)" if xss else "https://drive.example.com/asset",
                   tag=tag, hook=hook_of(row), tracker_tag=dict(cluster=row["cluster"], icp=row["icp"], pain_point=row["pain"], emotion=row["emotion"], ingredient=row["ingredient"]),
                   line=line, content=content, assets=dict(thumb=fam["thumb"], frames=frames),
                   siblings=[dict(id=r["id"], concept_id=r["concept_id"], line_code=r["line"][0] if r["line"] else None, cluster=r["cluster"], icp=r["icp"], pain_point=r["pain"], status=fam["status"]) for r in nxt],
                   metrics=dict(counted_on_this_row=row["ordinal"] == 0, family=dict(launch=fm(L), d7=fm(D7), d30=fm(D30), window=fm(win), live=live, **cmp_fam)),
                   window_info=win["info"], compare_info=cinfo(), daily=daily, meta_ads=meta_ads,
                   campaigns=[dict(e, spend=round(e["spend"], 2), revenue=round(e["revenue"], 2)) for e in camps.values()])


@app.get("/api/products/<slug>/trend")
def trend(slug):
    TREND_CALLS["n"] += 1
    win = parse_win(request.args)
    prod_meta(slug)
    grain = request.args.get("grain", "day")
    split = request.args.get("split", "none")
    if grain not in ("day", "week", "month"):
        raise Bad("bad grain")
    if split not in ("none", "stage", "children"):
        raise Bad("bad split")
    scope = {k: request.args.get(k) for k in ("cluster", "icp", "pain", "family_key", "campaign_id")}
    ads_in = scope_ads(slug, scope)
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    info = dict(win["info"])
    if win["launch"]:
        info["note"] = " ".join(x for x in (info.get("note"), f"Trend covers {lab(DAYS[0])} to {lab(DAYS[-1])} (daily data).") if x)
    arr = sum_series(ads_in, win["stage"])
    points = series_points(arr, lo, hi, grain)
    out = dict(window_info=info, compare_info=cinfo(), grain=grain, scope={k: v for k, v in scope.items()}, points=points)
    if g.cmp:
        b = series_points(sum_series(ads_in, win["stage"]), g.cmp["i0"], g.cmp["i1"], grain)
        out["cmp_points"] = align_points(b, len(points))
    if split == "stage":
        out["by_stage"] = {st: series_points(sum_series(ads_in, frozenset({st})), lo, hi, grain) for st in ALL_STAGES if st in win["stage"]}
    if split == "children":
        flt = {k: scope[k] for k in ("cluster", "icp", "pain") if scope.get(k)}
        groups = []
        if scope.get("family_key") or scope.get("campaign_id") or scope.get("pain"):
            if scope.get("pain") and not (scope.get("family_key") or scope.get("campaign_id")):
                fam_rows, _ = family_rows(slug, flt, parse_win({}))
                for fr in fam_rows:
                    groups.append((fr["family_key"], fr["display"], None, fams_ads([fr["family_key"]]) if fr["counted_here"] else []))
            else:
                for a in ads_in:
                    groups.append((a["ad_id"], a["name"], None, [a]))
        else:
            level = "cluster" if not scope.get("cluster") else ("icp" if not scope.get("icp") else "pain")
            rows, _ = rollup_rows(slug, level, flt, parse_win({}))
            for r in rows:
                fams = [f["key"] for f, rr, ch in filt(prod_items(slug), {**flt, level: r["label"]}) if ch]
                groups.append((r["label"], r["label"], r["color"], fams_ads(fams)))
        scored = []
        for key, label, color, ads_g in groups:
            a_ = sum_series(ads_g, win["stage"])
            scored.append((sum(a_[i][0] for i in range(lo, hi + 1)), key, label, color, ads_g))
        scored.sort(key=lambda x: -x[0])
        top, rest = scored[:8], scored[8:]
        kids = [dict(key=k, label=lb, color=c, points=series_points(sum_series(ag, win["stage"]), lo, hi, grain)) for _, k, lb, c, ag in top]
        if rest:
            other_ads = [a for _, _, _, _, ag in rest for a in ag]
            kids.append(dict(key="__other__", label="Other", color=None, points=series_points(sum_series(other_ads, win["stage"]), lo, hi, grain)))
        out["children"] = kids
    return jsonify(out)


@app.get("/api/_test/trend_calls")
def trend_calls():
    return jsonify(TREND_CALLS)


@app.get("/api/ads/<int:rid>/preview")
def ad_preview(rid):
    row = ROW_BY_ID.get(rid)
    if not row:
        return jsonify(error="not found"), 404
    return preview_json(FAM_BY_KEY[row["fam"]]["display"])


@app.get("/api/meta-ads/<ad_id>/preview")
def meta_ad_preview(ad_id):
    ad = next((a for a in cur_ads() if a["ad_id"] == ad_id), None)
    if not ad:
        return jsonify(error="not found"), 404
    return preview_json(ad["name"])


@app.get("/mock/preview")
def mock_preview_page():
    from html import escape
    name, fmt = request.args.get("name", ""), request.args.get("format", "")
    return Response(f"<!doctype html><meta charset=utf-8><body style='font:14px sans-serif;margin:0;padding:16px;background:#fafafa'><div id='mock-ad' style='border:1px solid #ccc;border-radius:8px;padding:12px;background:#fff'><b>{escape(name)}</b><div>Mock Meta preview ({escape(fmt)})</div></div></body>", mimetype="text/html")


@app.get("/media/<aid>")
def media(aid):
    from PIL import Image, ImageDraw
    h = sum(ord(c) * (i + 3) for i, c in enumerate(aid))
    img = Image.new("RGB", (160, 160), ((h * 37) % 200 + 30, (h * 91) % 200 + 30, (h * 53) % 200 + 30))
    ImageDraw.Draw(img).text((10, 10), aid, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = Response(buf.getvalue(), mimetype="image/png")
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r
