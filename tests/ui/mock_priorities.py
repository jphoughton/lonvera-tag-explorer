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

    def recs():
        return sorted(manual.values(), key=lambda r: (r["created_at"], r["id"]), reverse=True)

    def merged(include_done=True):
        from app import priorities_manual as pm
        return pm.merge(json.loads(FIXTURE.read_text()), recs(), include_done=include_done)

    @app.get("/api/priorities")
    def priorities_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        if request.args.get("empty") == "1":
            return jsonify(error="No creative priorities snapshot exists yet."), 404
        return jsonify(merged())

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
        snap = cpr.filter_rows(merged(include_done=bool(ids)), ids)
        return Response(cp.to_csv(snap), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="creative-priorities-{snap["week_start"]}.csv"'})

    # saved views, in memory (same shape as app/creative_priorities.py)
    views: dict[str, dict] = {}

    def snap_of(ids):
        from app import creative_priorities as cpr
        return cpr.filter_rows(merged(), ids)

    @app.post("/api/priorities/views")
    def priorities_view_create_mock():
        from app import creative_priorities as cpr
        body = request.get_json(silent=True) or {}
        ids = body.get("ids") or []
        snap = snap_of(ids)
        if not snap["rows"]:
            return jsonify(error="None of the selected rows exist in that snapshot."), 400
        tok = secrets.token_urlsafe(24)
        views[tok] = {"snap": snap, "name": cpr.clean_name(body.get("name")), "created": dt.datetime.now(dt.timezone.utc).isoformat()}
        return jsonify(token=tok, url=f"/priorities/v/{tok}", name=views[tok]["name"], count=len(snap["rows"]), missing=len(set(ids)) - len(snap["rows"]), week_start=snap["week_start"]), 201

    @app.get("/api/priorities/views")
    def priorities_views_mock():
        return jsonify(views=[{"token": t, "name": v["name"], "week_start": v["snap"]["week_start"], "count": len(v["snap"]["rows"]), "created_at": v["created"], "url": f"/priorities/v/{t}"}
                              for t, v in reversed(list(views.items()))])

    @app.get("/api/priorities/views/<token>")
    def priorities_view_mock(token):
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        s = v["snap"]
        return jsonify({**s, "weeks": [], "view": {"token": token, "name": v["name"], "week_start": s["week_start"], "created_at": v["created"], "count": len(s["rows"])}})

    @app.get("/api/priorities/views/<token>/csv")
    def priorities_view_csv_mock(token):
        from etl import creative_priorities as cp
        v = views.get(token)
        if v is None:
            return jsonify(error="Saved view not found."), 404
        from app import creative_priorities as cpr
        return Response(cp.to_csv(v["snap"]), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{cpr.slug(v["name"])}.csv"'})

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
