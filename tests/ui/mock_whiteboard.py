"""Mock of the Creative priorities whiteboard API for tests/ui/mock_server.py (in memory, same contract as app/priorities_whiteboard.py):
GET /api/priorities/whiteboard?week=YYYY-MM-DD -> {week, version, picks, dropped} with ETag W/"wb-<week>-<version>" and 304 on If-None-Match;
PUT /api/priorities/whiteboard {week, picks, dropped, removed?} replaces the concepts it mentions (array order = rank), untouched concepts stay; `removed` takes concepts off the week entirely.
POST /__test/whiteboard/reset clears every week; POST /__test/whiteboard/fail?n=2 makes the next n PUTs answer 500."""
from __future__ import annotations

import datetime as dt

from flask import Response, jsonify, request

LANES = ("must", "if_time", "parked")
TAGS = ("product", "cluster", "icp", "pain_point", "line_code", "title")


def register(app, deps):
    weeks: dict[str, dict] = {}
    seq = [0]
    fail = [0]

    def monday(s: str | None) -> str:
        try:
            d = dt.date.fromisoformat((s or "")[:10])
        except ValueError:
            d = dt.date.today()
        return (d - dt.timedelta(days=d.weekday())).isoformat()

    def wk(week):
        return weeks.setdefault(week, {"version": 0, "picks": {}, "dropped": {}, "order": 0})

    def payload(week):
        w = wk(week)
        picks = sorted(w["picks"].values(), key=lambda p: (LANES.index(p["lane"]), p["rank"], p["id"]))
        dropped = sorted(w["dropped"].values(), key=lambda p: p["at"], reverse=True)
        return {"week": week, "version": w["version"],
                "picks": [{k: v for k, v in p.items() if k != "at"} for p in picks],
                "dropped": [{k: v for k, v in p.items() if k not in ("at", "lane", "rank", "note")} for p in dropped]}

    def respond(week, status=200):
        body = payload(week)
        r = jsonify(body)
        r.status_code = status
        r.headers["ETag"] = f'W/"wb-{week}-{body["version"]}"'
        return r

    @app.get("/api/priorities/whiteboard")
    def whiteboard_get():
        week = monday(request.args.get("week"))
        etag = f'W/"wb-{week}-{wk(week)["version"]}"'
        if request.headers.get("If-None-Match") == etag:
            r = Response(status=304)
            r.headers["ETag"] = etag
            return r
        return respond(week)

    @app.put("/api/priorities/whiteboard")
    def whiteboard_put():
        if fail[0] > 0:
            fail[0] -= 1
            return jsonify(error="mock failure"), 500
        body = request.get_json(silent=True) or {}
        picks, dropped = body.get("picks") or [], body.get("dropped") or []
        if len(picks) + len(dropped) > 300:
            return jsonify(error="too many items"), 400
        week = monday(body.get("week"))
        w = wk(week)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for k in body.get("removed") or []:
            w["picks"].pop(k if isinstance(k, str) else k.get("concept_key"), None)
            w["dropped"].pop(k if isinstance(k, str) else k.get("concept_key"), None)
        for rank, p in enumerate(picks):
            key = p.get("concept_key")
            if not key or p.get("lane") not in LANES:
                return jsonify(error="bad pick"), 400
            if len(p.get("note") or "") > 500:
                return jsonify(error="note too long"), 400
            w["dropped"].pop(key, None)
            seq[0] += 1
            w["picks"][key] = {"id": seq[0], "concept_key": key, "lane": p["lane"], "rank": rank, "note": p.get("note") or "", "rev": w["version"] + 1, "updated_by": "mock", "updated_at": now,
                               **{t: p.get(t) for t in TAGS}}
        for i, p in enumerate(dropped):
            key = p.get("concept_key")
            if not key:
                return jsonify(error="bad dropped"), 400
            w["picks"].pop(key, None)
            seq[0] += 1
            w["dropped"][key] = {"id": seq[0], "concept_key": key, "at": f"{now}{len(dropped) - i:04d}", "updated_by": "mock", "updated_at": now, **{t: p.get(t) for t in TAGS}}
        for lane in LANES:                                              # renumber each lane so ranks are 0..n-1 again
            for r, p in enumerate(sorted((p for p in w["picks"].values() if p["lane"] == lane), key=lambda p: (p["rank"], p["id"]))):
                p["rank"] = r
        w["version"] += 1
        return respond(week)

    @app.post("/__test/whiteboard/reset")
    def whiteboard_reset():
        weeks.clear()
        fail[0] = 0
        return Response(status=204)

    @app.post("/__test/whiteboard/fail")
    def whiteboard_fail():
        fail[0] = int(request.args.get("n", "1"))
        return Response(status=204)
