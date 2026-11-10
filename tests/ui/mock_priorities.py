"""Mock of GET /api/priorities for tests/ui/mock_server.py: a trimmed copy of a real weekly snapshot (tests/ui/priorities_fixture.json, 2 rows per product and section)."""
from __future__ import annotations

import json
from pathlib import Path

import datetime as dt
import secrets

from flask import Response, jsonify, request

FIXTURE = Path(__file__).resolve().parent / "priorities_fixture.json"


def register(app, deps):
    # manual concepts, in memory (same validation / rows / options as app/priorities_manual.py)
    manual: dict[int, dict] = {}
    seq = [0]
    state = {"drop": set(), "edit": {}}

    def recs():
        return sorted(manual.values(), key=lambda r: (r["created_at"], r["id"]), reverse=True)

    def live(include_done=True):
        """The mock's current snapshot (plus the manual concepts): the fixture, minus rows dropped and plus row edits made through /__test/priorities/drop."""
        from app import creative_priorities as cpr, priorities_manual as pm
        from etl import creative_priorities as cp
        d = cp.norm_snapshot(json.loads(FIXTURE.read_text()))                   # the fixture is a snapshot stored before the Action / Status redesign: read through the same map as the real API
        drop = {cp.norm_key(k) for k in state["drop"]}
        edit = {cp.norm_key(k): v for k, v in state["edit"].items()}
        d["rows"] = [r for r in d["rows"] if cpr.row_key(r) not in drop]
        for r in d["rows"]:
            r.update(edit.get(cpr.row_key(r), {}))
        d = pm.merge(d, recs(), include_done=include_done)
        return {**d, "rows": cpr.with_ckey(d["rows"]), "definitions": cp.definitions(d.get("thresholds"))}       # ckey and definitions as in app.creative_priorities.payload

    merged = live
    app.config["PRI_LIVE"] = live                                             # the assignments mock resolves the keys of POST /api/priorities/assignments from this snapshot

    @app.get("/api/priorities")
    def priorities_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        if request.args.get("empty") == "1":
            return jsonify(error="No creative priorities snapshot exists yet."), 404
        return jsonify(live())

    @app.get("/api/priorities/manual/options")
    def priorities_manual_options_mock():
        from app import priorities_manual as pm
        return jsonify(pm.options())

    @app.get("/api/priorities/manual")
    def priorities_manual_list_mock():
        from app import priorities_manual as pm
        return jsonify(rows=[pm.to_row(r) for r in recs() if request.args.get("include_done") in ("1", "true") or r["status"] == "open"])

    @app.post("/api/priorities/manual")
    def priorities_manual_create_mock():
        from app import priorities_manual as pm, queries as q
        try:
            f = pm.clean(request.get_json(silent=True))
        except q.ApiError as e:
            return jsonify(error=str(e)), e.status
        seq[0] += 1
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rec = {"id": seq[0], **f, "status": "open", "created_at": now, "updated_at": now, "done_at": None, **pm.new_flags(f["product"], f["cluster"], f["icp"], f["pain_point"], f["story"])}
        manual[rec["id"]] = rec
        return jsonify(pm.to_row(rec)), 201

    @app.patch("/api/priorities/manual/<int:rid>")
    def priorities_manual_update_mock(rid):
        from app import priorities_manual as pm, queries as q
        if rid not in manual:
            return jsonify(error="Manual concept not found."), 404
        try:
            f = pm.clean(request.get_json(silent=True), partial=True)
        except q.ApiError as e:
            return jsonify(error=str(e)), e.status
        rec = manual[rid]
        rec.update(f)
        rec.update(pm.new_flags(rec["product"], rec["cluster"], rec["icp"], rec["pain_point"], rec["story"]))
        rec["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if "status" in f:
            rec["done_at"] = rec["updated_at"] if f["status"] == "done" else None
        return jsonify(pm.to_row(rec))

    @app.delete("/api/priorities/manual/<int:rid>")
    def priorities_manual_delete_mock(rid):
        if manual.pop(rid, None) is None:
            return jsonify(error="Manual concept not found."), 404
        return "", 204


    @app.get("/api/priorities.csv")
    def priorities_csv_mock():
        """Same filtering as the real route (app.creative_priorities.filter_rows) over the fixture."""
        from app import creative_priorities as cpr
        from etl import creative_priorities as cp
        ids = request.args.getlist("id")
        snap = cpr.filter_rows(live(include_done=bool(ids)), ids)
        fname = cpr.slug(request.args["name"]) if request.args.get("name") else f"creative-priorities-{snap['week_start']}"
        bt = request.args.get("briefed_from")
        return Response(cp.to_csv(snap, briefed=views[bt]["briefed"] if bt in views else None), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})

    # saved views, in memory (the same builders as app/creative_priorities.py)
    views: dict[str, dict] = {}

    full = live

    def err(e):
        return jsonify(error=e.message if hasattr(e, "message") else str(e)), getattr(e, "status", 400)

    @app.post("/api/priorities/views")
    def priorities_view_create_mock():
        from app import creative_priorities as cpr, queries as q
        body = request.get_json(silent=True) or {}
        try:
            v = cpr.build_view(full(), body)
        except q.ApiError as e:
            return err(e)
        tok = secrets.token_urlsafe(24)
        views[tok] = {**v, "briefed": {}, "token": tok, "name": cpr.clean_name(body.get("name")), "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        return jsonify(token=tok, url=f"/priorities/v/{tok}", name=views[tok]["name"], count=v["count"], missing=v["missing"], week_start=v["week_start"], kind=v["kind"]), 201

    @app.get("/api/priorities/views")
    def priorities_views_mock():
        return jsonify(views=sorted(({"token": t, "name": v["name"], "week_start": v["week_start"], "count": v["count"], "kind": v["kind"], "filters": v["filters"], "created_at": v["created_at"],
                                      "url": f"/priorities/v/{t}"} for t, v in views.items()), key=lambda x: x["name"].lower()))

    @app.get("/api/priorities/views/<token>")
    def priorities_view_mock(token):
        from app import creative_priorities as cpr
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        return jsonify(cpr.view_response({**v, "payload": v["payload"]}))

    @app.get("/api/priorities/views/<token>/csv")
    def priorities_view_csv_mock(token):
        from app import creative_priorities as cpr
        from etl import creative_priorities as cp
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        snap = cpr.filter_rows({**v["payload"], "stats": {}}, request.args.getlist("id"))
        return Response(cp.to_csv(snap, briefed=v["briefed"]), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{cpr.slug(v["name"])}.csv"'})

    @app.put("/api/priorities/views/<token>")
    def priorities_view_update_mock(token):
        from app import creative_priorities as cpr, queries as q
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        try:
            n = cpr.build_view(full(), {**(request.get_json(silent=True) or {}), "kind": v["kind"]})
        except q.ApiError as e:
            return err(e)
        v.update(n)
        return jsonify(token=token, count=n["count"], missing=n["missing"], week_start=n["week_start"], kind=n["kind"])

    @app.patch("/api/priorities/views/<token>/briefed")
    def priorities_view_briefed_mock(token):
        from app import creative_priorities as cpr, queries as q
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        try:
            key, flag, note = cpr.clean_briefed(request.get_json(silent=True) or {})
        except q.ApiError as e:
            return err(e)
        from etl import creative_priorities as cp
        key = cp.norm_key(key)
        valid = set(v["keys"]) | {cpr.row_key(x) for x in v["payload"]["rows"]}
        if v["kind"] == "filters":
            valid |= {cpr.row_key(x) for x in live()["rows"]}
        if key not in valid:
            return jsonify(error="That concept is not in this view."), 404
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        if flag:
            v["briefed"][key] = {"at": now, "note": note}
        else:
            v["briefed"].pop(key, None)
        return jsonify(key=key, briefed=flag, at=now if flag else None, note=note if flag else "")

    @app.patch("/api/priorities/views/<token>")
    def priorities_view_rename_mock(token):
        from app import creative_priorities as cpr
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        v["name"] = cpr.clean_name((request.get_json(silent=True) or {}).get("name"))
        return jsonify(token=token, name=v["name"])

    @app.delete("/api/priorities/views/<token>")
    def priorities_view_delete_mock(token):
        if views.pop(token, None) is None:
            return jsonify(error="Saved view not found."), 404
        return "", 204

    # test hooks on the mock's "current snapshot": drop rows (missing-row fallback) and edit a row's concept count (live vs frozen numbers)

    @app.post("/__test/priorities/drop")
    def priorities_drop_mock():
        from app import creative_priorities as cpr
        b = request.get_json(silent=True) or {}
        state["drop"] = set(b.get("keys") or [])
        state["edit"] = b.get("edit") or {}
        return jsonify(ok=True)
