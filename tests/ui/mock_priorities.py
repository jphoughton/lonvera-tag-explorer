"""Mock of GET /api/priorities for tests/ui/mock_server.py: a trimmed copy of a real weekly snapshot (tests/ui/priorities_fixture.json, 2 rows per product and section)."""
from __future__ import annotations

import json
from pathlib import Path

from flask import jsonify, request

FIXTURE = Path(__file__).resolve().parent / "priorities_fixture.json"


def register(app, deps):
    @app.get("/api/priorities")
    def priorities_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        if request.args.get("empty") == "1":
            return jsonify(error="No creative priorities snapshot exists yet."), 404
        return jsonify(json.loads(FIXTURE.read_text()))
