"""Mock of the Production API for tests/ui/mock_server.py: /api/priorities/assignments, /vendor-links and /vendor/<token>, held in memory and built on the pure functions of app/priorities_assign.py
(validation, week snapping, stage). Same request / response shapes as the real routes (package A2). Seed: a few assignments plus a small tracker, rebuilt by POST /__test/assign/reset."""
from __future__ import annotations

import datetime as dt
import secrets

from flask import jsonify, request

SEED_TOKENS = {"Madre": "tok-madre-0000000000000000", "Victoria": "tok-victoria-00000000000000"}


def register(app, deps):
    from app import priorities_assign as pa, queries as q

    VENDORS = list(pa.VENDORS)
    S: dict = {}

    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def this_monday():
        return pa.monday_of(dt.date.today())

    def reset():
        mon = this_monday()
        nxt = mon + dt.timedelta(days=7)
        base = {"story": "", "note": "", "vendor_note": "", "brief_url": None, "creative_url": None, "tracker_concept_ids": [], "created_by": "josh", "updated_by": "josh", "ready_at": None, "launched_at": None, "dropped_at": None, "created_at": now(), "_suggested": []}
        rows = [
            {**base, "id": 1, "vendor": "Madre", "for_week": mon, "product": "vita-bull", "cluster": "ED / Performance", "icp": "Bedroom Upgrade Seeker", "pain_point": "General Vitality / Aging Well", "story": "He wants the old spark back."},
            {**base, "id": 2, "vendor": "Madre", "for_week": mon, "product": "beef-liver", "cluster": "Hair Thinning", "icp": "Thinning and Drained", "pain_point": "Hair Loss", "brief_url": "https://docs.google.com/document/d/brief2", "creative_url": "https://drive.google.com/drive/folders/creative2"},
            {**base, "id": 3, "vendor": "Victoria", "for_week": nxt, "product": "boneflex", "cluster": "Glucosamine & Chondroitin", "icp": "Tried Everything", "pain_point": "Joint Pain & Mobility Loss", "_suggested": ["Lonvera-Boneflex-Static-1.1"]},
            {**base, "id": 4, "vendor": "Madre", "for_week": mon - dt.timedelta(days=14), "product": "vita-bull", "cluster": "Prostate", "icp": "Prostate Worrier", "pain_point": "Prostate / Urinary Health", "brief_url": "https://docs.google.com/document/d/brief4", "creative_url": "https://drive.google.com/drive/folders/creative4", "ready_at": now(), "tracker_concept_ids": ["Lonvera-VitalBull-Static-5.4"]},
        ]
        S["rows"] = {r["id"]: r for r in rows}
        S["seq"] = 4
        S["tokens"] = dict(SEED_TOKENS)
        S["links"] = {v: {"token": t, "created_at": now(), "rotated_at": None} for v, t in SEED_TOKENS.items()}
        S["tracker"] = {"Lonvera-VitalBull-Static-5.4": {"concept_id": "Lonvera-VitalBull-Static-5.4", "ad_family": "Lonvera-VitalBull-Static-5.4", "status": "8 - Launched", "launch_date": (mon - dt.timedelta(days=9)).isoformat(), "spend": 1240.4, "roas": 1.8},
                        "Lonvera-Boneflex-Static-1.1": {"concept_id": "Lonvera-Boneflex-Static-1.1", "ad_family": "Lonvera-Boneflex-Static-1.1", "status": "5 - Briefed", "launch_date": None, "spend": None, "roas": None}}

    reset()

    def err(e):
        return jsonify(error=e.message if hasattr(e, "message") else str(e)), getattr(e, "status", 400)

    def ads_of(r):
        out = []
        for cid in r["tracker_concept_ids"]:
            t = S["tracker"].get(cid)
            if t:
                out.append({**t, "ad_url": f"/ad/{cid}"})
        return out

    def view(r, vendor_side=False):
        statuses = [a["status"] for a in ads_of(r)]
        stage = pa.stage_of(r, statuses)
        out = {k: v for k, v in r.items() if not k.startswith("_")}
        out["for_week"] = r["for_week"].isoformat()
        out.update(stage=stage, stage_label=pa.stage_label(stage), tracker_status=statuses[0] if statuses else None, tracker_concept_ids=list(r["tracker_concept_ids"]))
        out["suggested_concept_ids"] = [c for c in r["_suggested"] if c not in r["tracker_concept_ids"]]
        out["ads"] = ads_of(r)
        if vendor_side:                                     # a vendor sees own rows, their ads' spend and return, but nothing in INTERNAL_ONLY and no ad page links (those need a login)
            for k in pa.INTERNAL_ONLY:
                out.pop(k, None)
            out["ads"] = [{k: v for k, v in a.items() if k != "ad_url"} for a in out["ads"]]
        return out

    def apply(r, f):
        for k in ("vendor", "for_week", "note", "brief_url", "creative_url", "tracker_concept_ids", "vendor_note"):
            if k in f:
                r[k] = f[k]
        for b, col in (("ready", "ready_at"), ("launched", "launched_at"), ("dropped", "dropped_at")):
            if b in f:
                r[col] = now() if f[b] else None
        r["updated_at"] = now()

    @app.get("/api/priorities/assignments")
    def assign_list():
        week, vendor = request.args.get("week") or "", request.args.get("vendor") or ""
        closed = request.args.get("include_closed") in ("1", "true")
        rows = []
        for r in sorted(S["rows"].values(), key=lambda r: (r["for_week"], r["id"]), reverse=True):
            v = view(r)
            if week and v["for_week"] != week or vendor and r["vendor"] != vendor:
                continue
            if v["stage"] in ("launched", "dropped") and not closed:
                continue
            rows.append(v)
        weeks = sorted({r["for_week"].isoformat() for r in S["rows"].values()}, reverse=True)
        return jsonify(rows=rows, vendors=VENDORS, weeks=weeks)

    @app.post("/api/priorities/assignments")
    def assign_create():
        """Same rules as app.priorities_assign.create_assignments: the rows come from the (mock) snapshot by row key, a duplicate (same concept, vendor, week, not dropped) is skipped with a reason."""
        from app import creative_priorities as cpr
        from etl.creative_priorities import norm_key
        body = request.get_json(silent=True) or {}
        try:
            f = pa.clean_create(body)
            keys = body.get("keys")
            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and 0 < len(k) <= 400 for k in keys):
                raise q.ApiError(400, "keys must be a list of concept row keys")
            if len(set(keys)) > pa.MAX_KEYS:
                raise q.ApiError(400, f"at most {pa.MAX_KEYS} concepts at a time")
        except q.ApiError as e:
            return err(e)
        by_key = {cpr.row_key(r): r for r in app.config["PRI_LIVE"]()["rows"]}
        created, skipped = [], []
        for k in dict.fromkeys(keys):
            r = by_key.get(norm_key(k))
            if r is None:
                skipped.append({"key": k, "reason": "not in this week's list"})
                continue
            if not (r.get("cluster") or "").strip() and not (r.get("pain_point") or "").strip():
                skipped.append({"key": k, "reason": "no cluster or pain point"})
                continue
            ck = pa.concept_key(r)
            if any(a.get("concept_key") == ck and a["vendor"] == f["vendor"] and a["for_week"] == f["for_week"] and not a["dropped_at"] for a in S["rows"].values()):
                skipped.append({"key": k, "reason": "already assigned to this vendor for that week"})
                continue
            S["seq"] += 1
            rec = {"story": r.get("story") or "", "note": f["note"], "vendor_note": "", "brief_url": None, "creative_url": None, "tracker_concept_ids": [], "created_by": "josh", "updated_by": "josh", "ready_at": None,
                   "launched_at": None, "dropped_at": None, "created_at": now(), "updated_at": now(), "_suggested": [], "id": S["seq"], "concept_key": ck, "text_key": pa.text_key(r), "vendor": f["vendor"],
                   "for_week": f["for_week"], "product": r["product"], "line_id": r.get("line_id"), "manual_id": r["id"] if r.get("manual") else None, "line_code": r.get("line_code") or "", "cluster": r.get("cluster") or "",
                   "icp": r.get("icp") or "", "pain_point": r.get("pain_point") or "", "section_at_assign": r.get("section") or ""}
            S["rows"][rec["id"]] = rec
            created.append(view(rec))
        return jsonify(created=created, skipped=skipped), 201

    @app.patch("/api/priorities/assignments/<int:aid>")
    def assign_patch(aid):
        r = S["rows"].get(aid)
        if r is None:
            return jsonify(error="Assignment not found."), 404
        try:
            f = pa.clean_patch(request.get_json(silent=True))
        except q.ApiError as e:
            return err(e)
        apply(r, f)
        return jsonify(view(r))

    def link(v):
        l = S["links"][v]
        return {"vendor": v, "token": l["token"], "url": f"/priorities/vendor/{l['token']}", "created_at": l["created_at"], "rotated_at": l["rotated_at"]}

    @app.post("/api/priorities/vendor-links")
    def vlink_create():
        v = (request.get_json(silent=True) or {}).get("vendor")
        if v not in VENDORS:
            return jsonify(error="vendor must be one of: " + ", ".join(VENDORS)), 400
        created = v not in S["links"]
        if created:
            S["links"][v] = {"token": S["tokens"].setdefault(v, secrets.token_urlsafe(24)), "created_at": now(), "rotated_at": None}
        return jsonify(link(v)), 201 if created else 200

    @app.post("/api/priorities/vendor-links/<vendor>/reset")
    def vlink_reset(vendor):
        if vendor not in VENDORS:
            return jsonify(error="vendor must be one of: " + ", ".join(VENDORS)), 400
        tok = secrets.token_urlsafe(24)
        S["tokens"][vendor] = tok
        S["links"][vendor] = {"token": tok, "created_at": S["links"].get(vendor, {}).get("created_at") or now(), "rotated_at": now()}
        return jsonify(link(vendor))

    def vendor_of(token):
        return next((v for v, t in S["tokens"].items() if t == token), None)

    @app.get("/api/priorities/vendor/<token>")
    def vendor_rows(token):
        v = vendor_of(token)
        if v is None:
            return jsonify(error="not_found"), 404
        rows = [view(r, True) for r in sorted(S["rows"].values(), key=lambda r: (r["for_week"], r["id"]), reverse=True) if r["vendor"] == v]
        rows = [r for r in rows if r["stage"] not in ("dropped",)]
        return jsonify(vendor=v, rows=rows, weeks=sorted({r["for_week"] for r in rows}, reverse=True))

    @app.patch("/api/priorities/vendor/<token>/<int:aid>")
    def vendor_patch(token, aid):
        v = vendor_of(token)
        r = S["rows"].get(aid)
        if v is None or r is None or r["vendor"] != v:
            return jsonify(error="not_found"), 404
        try:
            f = pa.clean_vendor_patch(request.get_json(silent=True))
            if not f:
                raise q.ApiError(400, "nothing to update")
            if r["dropped_at"]:
                raise q.ApiError(409, "This concept was dropped.")
            if f.get("ready") and not (f["creative_url"] if "creative_url" in f else r["creative_url"]):
                raise q.ApiError(400, "Add the creative link before marking it ready to launch.")
        except q.ApiError as e:
            return err(e)
        apply(r, f)
        return jsonify(view(r, True))

    def detail_payload(r, vendor_side=False):
        """Same shape and rules as app.priorities_detail.build: the concept's row in the mock snapshot (by concept key, else text key), else what the assignment stored with current=false; up to 10 example
        ads by spend (ad_id None for a vendor). Examples exist only for a concept that has a row (the mock has no tag table)."""
        from app import priorities_detail as pdt
        snap = app.config["PRI_LIVE"]()
        rec = {**r, "concept_key": r.get("concept_key") or "none", "text_key": r.get("text_key") or pa.text_key(r)}
        row = pdt._find_row(snap["rows"], rec)
        ex = []
        if row is not None:
            for i in range(12):
                spend = round(9000 - i * 700 + (i % 3) * 11.5, 2)
                ex.append({"ad": f"Example ad {i + 1}", "spend": spend, "roas": round(0.6 + ((i * 7) % 10) / 10, 2), "ad_id": None if vendor_side else 1000 + i, "best": False})
            ex = ex[:pdt.EXAMPLES_MAX]
            ok = [e for e in ex if e["roas"] is not None and e["spend"] >= pdt.BEST_MIN_SPEND]
            max(ok, key=lambda e: (e["roas"], e["spend"]))["best"] = True
        out = {"assignment_id": r["id"], "current": row is not None, "snapshot_week": snap["week_start"], "examples": ex, "examples_max": pdt.EXAMPLES_MAX, "definitions": snap.get("definitions") or {}}
        if row is None:
            return {**out, "row": pdt._fallback_row(rec), "baselines": {}, "message": pdt.NO_NUMBERS}
        return {**out, "row": pdt._vendor_row(row) if vendor_side else row, "baselines": {row["product"]: (snap.get("baselines") or {}).get(row["product"]) or {}}, "message": ""}

    @app.get("/api/priorities/assignments/<int:aid>/detail")
    def assign_detail(aid):
        r = S["rows"].get(aid)
        if r is None:
            return jsonify(error="Assignment not found."), 404
        return jsonify(detail_payload(r))

    @app.get("/api/priorities/vendor/<token>/<int:aid>/detail")
    def vendor_detail(token, aid):
        v = vendor_of(token)
        r = S["rows"].get(aid)
        if v is None or r is None or r["vendor"] != v or r["dropped_at"]:
            return jsonify(error="not_found"), 404
        return jsonify(detail_payload(r, True))

    @app.post("/__test/assign/reset")
    def assign_reset():
        reset()
        return jsonify(ok=True, tokens=S["tokens"])
