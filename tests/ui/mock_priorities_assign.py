"""Mock of the Production API for tests/ui/mock_server.py: /api/priorities/assignments, /vendor-links and /vendor/<token>, held in memory and built on the pure functions of app/priorities_assign.py
(validation, week snapping, stage). Same request / response shapes as the real routes (package A2). Seed: a few assignments plus a small tracker, rebuilt by POST /__test/assign/reset."""
from __future__ import annotations

import datetime as dt
import secrets

from flask import jsonify, request

VENDORS = ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS", "Internal", "PuredMedia", "CreateMastery", "OpenProgram"]
SEED_TOKENS = {"Madre": "tok-madre-0000000000000000", "Victoria": "tok-victoria-00000000000000"}


def register(app, deps):
    from app import priorities_assign as pa, queries as q

    S: dict = {}

    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def this_monday():
        return pa.monday_of(dt.date.today())

    def reset():
        mon = this_monday()
        nxt = mon + dt.timedelta(days=7)
        base = {"story": "", "note": "", "vendor_note": "", "brief_url": None, "creative_url": None, "tracker_concept_ids": [], "ready_at": None, "launched_at": None, "dropped_at": None, "created_at": now(), "_suggested": []}
        rows = [
            {**base, "id": 1, "vendor": "Madre", "for_week": mon, "product": "vita-bull", "cluster": "ED / Performance", "icp": "Bedroom Upgrade Seeker", "pain_point": "General Vitality / Aging Well", "story": "He wants the old spark back."},
            {**base, "id": 2, "vendor": "Madre", "for_week": mon, "product": "beef-liver", "cluster": "Hair Thinning", "icp": "Thinning and Drained", "pain_point": "Hair Loss", "brief_url": "https://docs.google.com/document/d/brief2", "creative_url": "https://drive.google.com/drive/folders/creative2"},
            {**base, "id": 3, "vendor": "Victoria", "for_week": nxt, "product": "boneflex", "cluster": "Glucosamine & Chondroitin", "icp": "Tried Everything", "pain_point": "Joint Pain & Mobility Loss", "_suggested": ["Lonvera-Boneflex-Static-1.1"]},
            {**base, "id": 4, "vendor": "Madre", "for_week": mon - dt.timedelta(days=14), "product": "vita-bull", "cluster": "Prostate", "icp": "Prostate Worrier", "pain_point": "Prostate / Urinary Health", "brief_url": "https://docs.google.com/document/d/brief4", "creative_url": "https://drive.google.com/drive/folders/creative4", "ready_at": now(), "tracker_concept_ids": ["Lonvera-VitalBull-Static-5.4"]},
        ]
        S["rows"] = {r["id"]: r for r in rows}
        S["seq"] = 4
        S["tokens"] = dict(SEED_TOKENS)
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
        if vendor_side:                                     # a vendor sees own rows, their ads' spend and return, but not the internal note and no ad page links (those need a login)
            out.pop("note", None)
            out.pop("suggested_concept_ids", None)
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

    @app.post("/api/priorities/vendor-links")
    def vlink_create():
        v = (request.get_json(silent=True) or {}).get("vendor")
        if v not in VENDORS:
            return jsonify(error="vendor must be one of: " + ", ".join(VENDORS)), 400
        tok = S["tokens"].setdefault(v, secrets.token_urlsafe(24))
        return jsonify(vendor=v, token=tok, url=f"/priorities/vendor/{tok}")

    @app.post("/api/priorities/vendor-links/<vendor>/reset")
    def vlink_reset(vendor):
        if vendor not in VENDORS:
            return jsonify(error="Unknown vendor."), 404
        tok = secrets.token_urlsafe(24)
        S["tokens"][vendor] = tok
        return jsonify(vendor=vendor, token=tok, url=f"/priorities/vendor/{tok}")

    def vendor_of(token):
        return next((v for v, t in S["tokens"].items() if t == token), None)

    @app.get("/api/priorities/vendor/<token>")
    def vendor_rows(token):
        v = vendor_of(token)
        if v is None:
            return jsonify(error="not_found"), 404
        rows = [view(r, True) for r in sorted(S["rows"].values(), key=lambda r: (r["for_week"], r["id"]), reverse=True) if r["vendor"] == v]
        rows = [r for r in rows if r["stage"] not in ("dropped",)]
        return jsonify(vendor=v, rows=rows)

    @app.patch("/api/priorities/vendor/<token>/<int:aid>")
    def vendor_patch(token, aid):
        v = vendor_of(token)
        r = S["rows"].get(aid)
        if v is None or r is None or r["vendor"] != v:
            return jsonify(error="not_found"), 404
        body = dict(request.get_json(silent=True) or {})
        ready = body.pop("ready", None)
        try:
            f = pa.clean_vendor_patch(body)
            if ready is not None and not isinstance(ready, bool):
                raise q.ApiError(400, "ready must be true or false")
            if ready and not (f.get("creative_url") or r["creative_url"]):
                raise q.ApiError(400, "add the creative link first")
        except q.ApiError as e:
            return err(e)
        if ready is not None:
            f["ready"] = ready
        apply(r, f)
        return jsonify(view(r, True))

    @app.post("/__test/assign/reset")
    def assign_reset():
        reset()
        return jsonify(ok=True, tokens=S["tokens"])
