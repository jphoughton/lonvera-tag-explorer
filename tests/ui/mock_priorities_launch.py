"""Mock of the launch API (package P4) for tests/ui/mock_server.py: the seven routes under /api/priorities/launch and /api/priorities/assignments/<id>/launch, plus the `launch` key on
GET /api/priorities/assignments. In memory, no Meta, no database. Switch it with POST /__test/launch/config (JSON: enabled, can_launch, dry_run, stale, fail_ad) and read what the page sent
with GET /__test/launch/calls; POST /__test/launch/reset puts it back to everything off (the real default: launch not enabled).
A job advances one step on every GET .../launch: queued, running, done. A prepare job ends with a plan; a launch job ends with one created ad and one failed ad."""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from flask import jsonify, request

TSV = "Lonvera-BeefLiver-Static-9.1\tBeefLiver-9.1-a\t7 - Approved - Ready To Launch"


def register(app, deps):
    S: dict = {}

    def reset():
        S.clear()
        S.update(cfg={"enabled": False, "can_launch": True, "dry_run": False, "stale": False, "fail_ad": True}, jobs={}, drafts={}, calls=[], seq=0)

    reset()

    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def gate():
        if not S["cfg"]["enabled"]:
            return jsonify(error="not found"), 404
        if not S["cfg"]["can_launch"]:
            return jsonify(error="Your login is not allowed to launch ads."), 403
        return None

    def draft_of(aid):
        return S["drafts"].get(aid) or {"primary_texts": ["Primary one", "Primary two", "Primary three"], "headlines": ["Headline one", "Headline two", "Headline three"]}

    def plan_for(aid):
        d = draft_of(aid)
        plan = {"adset_name": "BeefLiver_BL1_Scaling_11-10-26", "adset_exists": False,
                "ads": [{"concept_id": "Lonvera-BeefLiver-Static-9.1", "ad_name": "BeefLiver-9.1-a", "file_name": "9.1-a.png", "kind": "image"},
                        {"concept_id": "Lonvera-BeefLiver-Static-9.2", "ad_name": "BeefLiver-9.2-b", "file_name": "9.2-b.png", "kind": "image"}],
                "skipped": [{"file_name": "notes.txt", "reason": "Not an image or video"}],
                "primary_texts": d["primary_texts"], "headlines": d["headlines"], "link_url": "https://lonvera.com/pages/beef-liver", "page_id": "123",
                "daily_budget_cents": 15000, "cost_cap_cents": 9500, "status": "PAUSED"}
        return plan, hashlib.sha1(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]

    def open_job(aid):
        j = S["jobs"].get(aid)
        return j if j and j["state"] in ("queued", "running") else None

    def job_view(j):
        return None if j is None else {k: v for k, v in j.items() if not k.startswith("_")}

    def advance(aid):
        j = S["jobs"].get(aid)
        if not j or j["state"] in ("done", "failed", "cancelled"):
            return
        j["state"] = "running" if j["state"] == "queued" else "done"
        if j["state"] == "done":
            j["finished_at"] = now()
            if j["kind"] == "prepare":
                j["plan"], j["plan_hash"] = plan_for(aid)
            else:
                S.setdefault("ads", {})[aid] = [
                    {"concept_id": "Lonvera-BeefLiver-Static-9.1", "ad_name": "BeefLiver-9.1-a", "file_name": "9.1-a.png", "kind": "image", "state": "created", "meta_ad_id": "555", "ads_manager_url": "https://adsmanager.facebook.com/ads/manage/ads?selected_ad_ids=555", "error": None},
                    {"concept_id": "Lonvera-BeefLiver-Static-9.2", "ad_name": "BeefLiver-9.2-b", "file_name": "9.2-b.png", "kind": "image", "state": "failed" if S["cfg"]["fail_ad"] else "created", "meta_ad_id": None, "ads_manager_url": None, "error": "Meta refused the image: too small." if S["cfg"]["fail_ad"] else None}]

    def new_job(aid, kind, plan=None, plan_hash=None):
        S["seq"] += 1
        j = {"id": S["seq"], "kind": kind, "state": "queued", "dry_run": S["cfg"]["dry_run"], "plan_hash": plan_hash, "plan": plan, "error": None, "created_at": now(), "finished_at": None}
        S["jobs"][aid] = j
        return j

    @app.get("/api/priorities/launch/settings")
    def l_settings():
        g = gate()
        return g or jsonify(settings=[], pages=[], vendor_prefixes=[])

    @app.get("/api/priorities/assignments/<int:aid>/launch")
    def l_get(aid):
        g = gate()
        if g:
            return g
        S["calls"].append(["GET", "launch", aid])
        advance(aid)
        j = S["jobs"].get(aid)
        ads = S.get("ads", {}).get(aid, []) if j and j["kind"] == "launch" and j["state"] == "done" else []
        return jsonify(assignment={"id": aid, "product": "beef-liver", "vendor": "Madre", "for_week": "2026-11-09", "stage": "ready", "creative_url": "https://drive.google.com/drive/folders/creative2", "concept_label": "Hair Loss"},
                       ready=True, not_ready_reason=None, draft=draft_of(aid), job=job_view(j), ads=ads, tracker_tsv=TSV if ads else None, dry_run=S["cfg"]["dry_run"])

    @app.put("/api/priorities/assignments/<int:aid>/launch/draft")
    def l_draft(aid):
        g = gate()
        if g:
            return g
        body = request.get_json(silent=True) or {}
        S["calls"].append(["PUT", "draft", aid, body])
        if open_job(aid):
            return jsonify(error="A launch is already running for this row."), 409
        S["drafts"][aid] = {**draft_of(aid), **{k: body[k] for k in ("primary_texts", "headlines") if k in body}}
        return jsonify(draft=S["drafts"][aid])

    @app.post("/api/priorities/assignments/<int:aid>/launch/prepare")
    def l_prepare(aid):
        g = gate()
        if g:
            return g
        S["calls"].append(["POST", "prepare", aid])
        if open_job(aid):
            return jsonify(error="A launch is already running for this row."), 409
        return jsonify(job=job_view(new_job(aid, "prepare"))), 202

    @app.post("/api/priorities/assignments/<int:aid>/launch/confirm")
    def l_confirm(aid):
        g = gate()
        if g:
            return g
        body = request.get_json(silent=True) or {}
        S["calls"].append(["POST", "confirm", aid, body])
        j = S["jobs"].get(aid)
        if S["cfg"]["stale"] or not j or j["kind"] != "prepare" or j["state"] != "done" or body.get("job_id") != j["id"] or body.get("plan_hash") != j["plan_hash"]:
            return jsonify(error="The plan changed. Prepare again."), 409
        return jsonify(job=job_view(new_job(aid, "launch", j["plan"], j["plan_hash"]))), 202

    @app.post("/api/priorities/assignments/<int:aid>/launch/cancel")
    def l_cancel(aid):
        g = gate()
        if g:
            return g
        S["calls"].append(["POST", "cancel", aid])
        j = open_job(aid)
        if j is None:
            return jsonify(error="Nothing to cancel."), 404
        if j["state"] == "running":
            return jsonify(error="It is already running and cannot be cancelled."), 409
        j["state"] = "cancelled"
        return jsonify(job=job_view(j))

    @app.put("/api/priorities/launch/settings/<product>")
    def l_settings_put(product):
        g = gate()
        return g or jsonify(settings={"product": product})

    @app.after_request
    def add_launch_flag(resp):
        """GET /api/priorities/assignments gains a top-level `launch` key (never gated)."""
        if request.method == "GET" and request.path == "/api/priorities/assignments" and resp.status_code == 200 and resp.is_json:
            d = resp.get_json()
            c = S["cfg"]
            d["launch"] = {"enabled": c["enabled"], "can_launch": bool(c["enabled"] and c["can_launch"]), "dry_run": c["dry_run"]}
            resp.set_data(json.dumps(d))
        return resp

    @app.post("/__test/launch/config")
    def t_config():
        S["cfg"].update({k: bool(v) for k, v in (request.get_json(silent=True) or {}).items() if k in S["cfg"]})
        return jsonify(S["cfg"])

    @app.get("/__test/launch/calls")
    def t_calls():
        return jsonify(S["calls"])

    @app.post("/__test/launch/reset")
    def t_reset():
        reset()
        return jsonify(ok=True)
