"""Mock of GET /api/priorities for tests/ui/mock_server.py: a trimmed copy of a real weekly snapshot (tests/ui/priorities_fixture.json, 2 rows per product and section)."""
from __future__ import annotations

import json
from pathlib import Path

import datetime as dt
import secrets

from flask import Response, jsonify, request

FIXTURE = Path(__file__).resolve().parent / "priorities_fixture.json"


def register(app, deps):
    @app.get("/api/priorities")
    def priorities_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        if request.args.get("empty") == "1":
            return jsonify(error="No creative priorities snapshot exists yet."), 404
        return jsonify(json.loads(FIXTURE.read_text()))


    @app.get("/api/priorities.csv")
    def priorities_csv_mock():
        """Same filtering as the real route (app.creative_priorities.filter_rows) over the fixture."""
        from app import creative_priorities as cpr
        from etl import creative_priorities as cp
        snap = cpr.filter_rows(json.loads(FIXTURE.read_text()), request.args.getlist("id"))
        return Response(cp.to_csv(snap), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="creative-priorities-{snap["week_start"]}.csv"'})

    # saved views, in memory (same shape as app/creative_priorities.py)
    views: dict[str, dict] = {}

    def snap_of(ids):
        from app import creative_priorities as cpr
        return cpr.filter_rows(json.loads(FIXTURE.read_text()), ids)

    @app.post("/api/priorities/views")
    def priorities_view_create_mock():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids") or []
        snap = snap_of(ids)
        if not snap["rows"]:
            return jsonify(error="None of the selected rows exist in that snapshot."), 400
        tok = secrets.token_urlsafe(24)
        views[tok] = {"snap": snap, "name": " ".join(str(body.get("name") or "").split())[:120], "created": dt.datetime.now(dt.timezone.utc).isoformat()}
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
        return Response(cp.to_csv(v["snap"]), mimetype="text/csv")

    @app.delete("/api/priorities/views/<token>")
    def priorities_view_delete_mock(token):
        if views.pop(token, None) is None:
            return jsonify(error="Saved view not found."), 404
        return "", 204
