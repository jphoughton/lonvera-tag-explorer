"""Mock of the PUBLIC vendor routes of the Production board contract (feat/production-board): GET /api/priorities/vendor/<token> with status, due_date, overdue, comment_count; PATCH .../<id> with the three
vendor transitions; GET / POST .../<id>/comments. In memory. Registered BEFORE mock_priorities_assign so its vendor routes win for its own tokens; any other token falls through to that mock's view functions.
Reset: POST /__test/vendorb3/reset. Test helper: POST /__test/vendorb3/team-comment {id, body}."""
from __future__ import annotations

import datetime as dt

from flask import jsonify, request

MADRE = "tok-b3-madre-00000000000000000"
VICTORIA = "tok-b3-victoria-000000000000000"
LABEL = {"assigned": "Assigned", "brief_sent": "Brief sent", "in_production": "In production", "in_review": "In review", "ready": "Ready to launch", "launched": "Launched"}
STAGE = {"assigned": "assigned", "brief_sent": "brief_sent", "in_production": "creative_in", "in_review": "creative_in", "ready": "ready", "launched": "launched"}
FORMATS = {"F1": ("Long animated story, openly AI, told by the character", "90s to 3 min"), "F3": ("Short acted scene with two people", "30 to 60s")}
VENDOR_TOKENS = {MADRE: "Madre", VICTORIA: "Victoria"}


def register(app, deps):
    S: dict = {}

    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def day(n):
        return (dt.date.today() + dt.timedelta(days=n)).isoformat()

    def monday(off=0):
        t = dt.date.today()
        return (t - dt.timedelta(days=t.weekday()) + dt.timedelta(days=7 * off)).isoformat()

    def reset():
        def row(i, vendor, status, **kw):
            base = {"id": i, "vendor": vendor, "product": "vita-bull", "cluster": "ED / Performance", "icp": "Bedroom Upgrade Seeker", "pain_point": f"Pain point {i}", "story": "", "for_week": monday(),
                    "brief_url": "https://docs.google.com/document/d/brief%d" % i, "creative_url": None, "vendor_note": "", "format_code": None, "status": status, "due_date": day(4), "ads": [],
                    "note": "INTERNAL NOTE %d" % i, "history": [{"kind": "secret"}]}
            return {**base, **kw}
        rows = [
            row(1, "Madre", "assigned", pain_point="Hair Loss", product="beef-liver", cluster="Hair Thinning", icp="Thinning and Drained", due_date=day(1), format_code="F1", story="He wants the old spark back."),
            row(2, "Madre", "brief_sent", pain_point="Joint Pain", product="boneflex", cluster="Glucosamine", icp="Tried Everything", due_date=day(9), format_code="F3"),
            row(3, "Madre", "in_production", pain_point="Low Energy", due_date=day(-2)),
            row(4, "Madre", "in_production", pain_point="Prostate Health", creative_url="https://drive.google.com/drive/folders/c4", due_date=day(2)),
            row(5, "Madre", "in_review", pain_point="Belly Fat", product="beef-liver", creative_url="https://drive.google.com/drive/folders/c5", for_week=monday(-1)),
            row(6, "Madre", "ready", pain_point="Sleep", creative_url="https://drive.google.com/drive/folders/c6", for_week=monday(-1), due_date=day(-3)),
            row(7, "Madre", "launched", pain_point="Testosterone", creative_url="https://drive.google.com/drive/folders/c7", for_week=monday(-2), due_date=day(-9),
                ads=[{"concept_id": "Lonvera-VitalBull-Static-5.4", "status": "8 - Launched", "launch_date": day(-9), "spend": 1240.4, "roas": 1.8}]),
            row(8, "Victoria", "assigned", pain_point="Victoria only concept", product="boneflex", note="VICTORIA SECRET NOTE"),
        ]
        S["rows"] = {r["id"]: r for r in rows}
        S["comments"] = {i: [] for i in S["rows"]}
        S["comments"][4].append({"id": 1, "author_kind": "app", "author_name": "Josh", "body": "Changes requested: the first 3 seconds need the product on screen.", "created_at": now()})
        S["comments"][5].append({"id": 2, "author_kind": "vendor", "author_name": "Madre", "body": "Sent the second cut.", "created_at": now()})
        S["comments"][5].append({"id": 3, "author_kind": "app", "author_name": "Josh", "body": "Looks good, checking today.", "created_at": now()})
        S["cseq"] = 3

    reset()

    def view(r):
        d = {k: v for k, v in r.items() if k not in ("note", "history", "vendor")}
        d["for_week"] = r["for_week"]
        d["status_label"] = LABEL[r["status"]]
        d["stage"] = STAGE[r["status"]]
        d["stage_label"] = LABEL[r["status"]] if r["status"] not in ("in_production", "in_review") else "Creative delivered"
        d["overdue"] = bool(r["due_date"] and r["due_date"] < day(0) and r["status"] not in ("ready", "launched"))
        d["comment_count"] = len(S["comments"][r["id"]])
        f = FORMATS.get(r.get("format_code"))
        d["format_name"], d["format_length"] = (f if f else (None, None))
        d["tracker_concept_ids"] = [a["concept_id"] for a in r["ads"]]
        d["ready_at"] = now() if r["status"] == "ready" else None
        return d

    def vendor_of(token):
        return VENDOR_TOKENS.get(token)

    def fall(name, *a, **k):
        return app.view_functions[name](*a, **k)

    def mine(token, aid):
        v = vendor_of(token)
        r = S["rows"].get(aid)
        return (r if (v and r and r["vendor"] == v) else None)

    @app.get("/api/priorities/vendor/<token>", endpoint="b3_vendor_rows")
    def b3_vendor_rows(token):
        v = vendor_of(token)
        if v is None:
            return fall("vendor_rows", token)
        rows = [view(r) for r in sorted(S["rows"].values(), key=lambda r: (r["for_week"], r["id"]), reverse=True) if r["vendor"] == v]
        return jsonify(vendor=v, rows=rows, weeks=sorted({r["for_week"] for r in rows}, reverse=True))

    @app.patch("/api/priorities/vendor/<token>/<int:aid>", endpoint="b3_vendor_patch")
    def b3_vendor_patch(token, aid):
        if vendor_of(token) is None:
            return fall("vendor_patch", token, aid)
        r = mine(token, aid)
        if r is None:
            return jsonify(error="not_found"), 404
        b = request.get_json(silent=True) or {}
        allowed = {"status", "creative_url", "vendor_note", "ready"}
        if not b or set(b) - allowed:
            return jsonify(error="Nothing to update." if not b else "You can only change your creative link, note and status."), 400
        url = b["creative_url"] if "creative_url" in b else r["creative_url"]
        want = b.get("status")
        if b.get("ready") is True:
            want = "in_review"
        if want is not None:
            ok = {("assigned", "in_production"), ("brief_sent", "in_production"), ("in_production", "in_review"), ("in_review", "in_production")}
            if (r["status"], want) not in ok:
                return jsonify(error="You cannot move this card from %s to %s." % (LABEL[r["status"]], LABEL.get(want, want))), 400
            if want == "in_review" and not url:
                return jsonify(error="Add the creative link before sending it for review."), 400
        if "creative_url" in b:
            r["creative_url"] = b["creative_url"] or None
        if "vendor_note" in b:
            r["vendor_note"] = b["vendor_note"]
        if want:
            r["status"] = want
        return jsonify(view(r))

    @app.get("/api/priorities/vendor/<token>/<int:aid>/detail", endpoint="b3_vendor_detail")
    def b3_vendor_detail(token, aid):
        if vendor_of(token) is None:
            return fall("vendor_detail", token, aid)
        r = mine(token, aid)
        if r is None:
            return jsonify(error="not_found"), 404
        from app import priorities_detail as pdt
        rec = {"product": r["product"], "cluster": r["cluster"], "icp": r["icp"], "pain_point": r["pain_point"], "story": r["story"]}
        return jsonify({"assignment_id": aid, "current": False, "snapshot_week": None, "examples": [], "examples_max": 10, "definitions": {}, "row": pdt._fallback_row(rec), "baselines": {}, "message": pdt.NO_NUMBERS})

    @app.get("/api/priorities/vendor/<token>/<int:aid>/comments", endpoint="b3_vendor_comments")
    def b3_vendor_comments(token, aid):
        if mine(token, aid) is None:
            return jsonify(error="not_found"), 404
        return jsonify(comments=S["comments"][aid])

    @app.post("/api/priorities/vendor/<token>/<int:aid>/comments", endpoint="b3_vendor_comment_post")
    def b3_vendor_comment_post(token, aid):
        r = mine(token, aid)
        if r is None:
            return jsonify(error="not_found"), 404
        body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
        if not body or len(body) > 2000:
            return jsonify(error="A comment needs 1 to 2000 characters."), 400
        S["cseq"] += 1
        c = {"id": S["cseq"], "author_kind": "vendor", "author_name": r["vendor"], "body": body, "created_at": now()}
        S["comments"][aid].append(c)
        return jsonify(comment=c), 201

    @app.post("/__test/vendorb3/team-comment", endpoint="b3_team_comment")
    def b3_team_comment():
        b = request.get_json(silent=True) or {}
        S["cseq"] += 1
        S["comments"][b["id"]].append({"id": S["cseq"], "author_kind": "app", "author_name": "Josh", "body": b["body"], "created_at": now()})
        return jsonify(ok=True)

    @app.post("/__test/vendorb3/reset", endpoint="b3_reset")
    def b3_reset():
        reset()
        return jsonify(ok=True)
