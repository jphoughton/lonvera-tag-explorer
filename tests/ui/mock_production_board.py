"""Mock of the Production board API (spec: production_board_spec.md) for tests/ui/mock_server.py, in memory, on top of the assignments held by mock_priorities_assign.py:
status / due date / priority / position / comment count on every assignment, POST .../move, GET and POST .../comments, GET /board, GET and PUT /vendor-capacity, and the extra PATCH fields.
Same request and response shapes as the contract: /move returns {card, column:{status, ids}}; rejections are 400 {"error": plain English}. POST /__test/board/seed adds a richer set of cards (ids 5-11)."""
from __future__ import annotations

import datetime as dt

from flask import jsonify, request

ORDER = ["assigned", "brief_sent", "in_production", "in_review", "ready", "launched"]
LABEL = {"assigned": "Assigned", "brief_sent": "Brief sent", "in_production": "In production", "in_review": "In review", "ready": "Ready to launch", "launched": "Launched"}
STAGE_OF_STATUS = {"assigned": "assigned", "brief_sent": "brief_sent", "in_production": "brief_sent", "in_review": "creative_in", "ready": "ready", "launched": "launched"}
STATUS_OF_STAGE = {"assigned": "assigned", "brief_sent": "brief_sent", "creative_in": "in_review", "ready": "ready", "launched": "launched"}
NEW_FIELDS = ("status", "due_date", "priority", "position", "comment")


class Refuse(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _friday(week_iso):
    return (dt.date.fromisoformat(week_iso) + dt.timedelta(days=4)).isoformat()


def ensure(r):
    """Fields a stored row gets the first time the board mock sees it."""
    if "_ensured" in r:
        return
    r["_ensured"] = True
    r.setdefault("_status", None)
    r.setdefault("due_date", _friday(r["for_week"].isoformat() if hasattr(r["for_week"], "isoformat") else r["for_week"]))
    r.setdefault("priority", "normal")
    r.setdefault("position", float(r["id"]))
    r.setdefault("comments", [])
    r.setdefault("status_changed_at", r.get("updated_at") or _now())


def status_of(H, r):
    ensure(r)
    if r.get("dropped_at"):
        return "dropped"
    if r.get("_status"):
        return r["_status"]
    v = H["view_base"](r)
    return STATUS_OF_STAGE.get(v["stage"], "assigned")


def decorate(H, r, out):
    ensure(r)
    s = status_of(H, r)
    due = r.get("due_date")
    n = (dt.date.fromisoformat(due) - dt.date.today()).days if due and s not in ("launched", "dropped") else None
    out.update(status=s, status_label=LABEL.get(s, s), due_date=due, due_in_days=n, overdue=bool(n is not None and n < 0), priority=r["priority"], position=r["position"],
               comment_count=len(r["comments"]), status_changed_at=r["status_changed_at"])
    if r.get("_status") and s in STAGE_OF_STATUS:
        out["stage"] = STAGE_OF_STATUS[s]
        out["stage_label"] = {"assigned": "Assigned", "brief_sent": "Brief sent", "creative_in": "Creative delivered", "ready": "Ready to launch", "launched": "Launched"}[out["stage"]]
    return out


def _event(H, r, kind, detail="", before=None, after=None, actor="josh"):
    H["S"]["events"].setdefault(r["id"], []).append({"at": _now(), "actor": actor, "kind": kind, "stage_before": before, "stage_after": after, "status_before": before, "status_after": after, "detail": detail})


def set_status(H, r, to, comment=None, actor="josh"):
    ensure(r)
    before = status_of(H, r)
    if before == to:
        return
    r["_status"] = to
    r["status_changed_at"] = _now()
    if to == "ready" and not r.get("ready_at"):
        r["ready_at"] = _now()
    if to != "ready" and to != "launched" and r.get("ready_at"):
        r["ready_at"] = None
    detail = "skipped review" if to == "ready" and before != "in_review" else ""
    _event(H, r, "status_changed", detail, before, to, actor)
    if comment:
        add_comment(H, r, comment, "app", "Josh", prefix="Changes requested: " if before == "in_review" and to == "in_production" else "")


def add_comment(H, r, body, kind, name, prefix=""):
    ensure(r)
    c = {"id": sum(len(x.get("comments", [])) for x in H["S"]["rows"].values()) + 1, "author_kind": kind, "author_name": name, "body": prefix + body, "created_at": _now()}
    r["comments"].append(c)
    _event(H, r, "comment_added", "", actor="josh" if kind == "app" else "vendor:" + name)
    return c


def check_move(H, r, to, comment):
    if to not in ORDER:
        raise Refuse("Unknown status.")
    if r.get("dropped_at"):
        raise Refuse("This concept was dropped. Restore it first.", 409)
    if to == "launched":
        raise Refuse("A card becomes Launched with Mark launched or when its Creative Tracker row is launched. It cannot be moved there.")
    before = status_of(H, r)
    if to == "in_review" and not r.get("creative_url"):
        raise Refuse("Add a creative link before sending to review.")
    if before == "in_review" and to == "in_production" and not (comment or "").strip():
        raise Refuse("Say what to change: add a comment when requesting changes.")
    if before == "launched":
        raise Refuse("A launched card cannot move back.")


def patch_extra(H, r, f):
    """The new PATCH fields: status (same rules as /move), due_date, priority, position, comment (recorded with the move)."""
    if "status" in f:
        check_move(H, r, f["status"], f.get("comment"))
        set_status(H, r, f["status"], f.get("comment"))
    elif f.get("comment"):
        add_comment(H, r, f["comment"], "app", "Josh")
    if "due_date" in f:
        v = f["due_date"]
        if v is not None:
            try:
                dt.date.fromisoformat(v)
            except (TypeError, ValueError):
                raise Refuse("due_date must look like 2026-11-14.")
        if v != r.get("due_date"):
            _event(H, r, "due_changed", f"{r.get('due_date')} to {v}")
        r["due_date"] = v
    if "priority" in f:
        if f["priority"] not in ("normal", "high"):
            raise Refuse("priority must be normal or high.")
        if f["priority"] != r["priority"]:
            _event(H, r, "priority_changed", f"{r['priority']} to {f['priority']}")
        r["priority"] = f["priority"]
    if "position" in f:
        r["position"] = float(f["position"])


def after_patch(H, r, f):
    """Old fields keep the status consistent: ready / launched / dropped flags and links."""
    ensure(r)
    if "launched" in f and f["launched"]:
        r["_status"] = "launched"; r["status_changed_at"] = _now()
    elif "launched" in f and not f["launched"] and r.get("_status") == "launched":
        r["_status"] = "ready"
    if "ready" in f and f["ready"] and r.get("_status") in ("assigned", "brief_sent", "in_production"):
        r["_status"] = "in_review"; r["status_changed_at"] = _now()
    elif "ready" in f and not f["ready"] and r.get("_status") == "ready":
        r["_status"] = "in_review"
    if "dropped" in f and not f["dropped"] and r.get("_status") == "dropped":
        r["_status"] = None


def register(app, deps):
    import mock_priorities_assign as ma
    from app import priorities_assign as pa

    H = ma.HANDLE
    S = H["S"]
    VENDORS = H["VENDORS"]
    S.setdefault("capacity", {v: 10 for v in VENDORS})
    S["capacity"]["Madre"] = 2

    def cards(vendor="", week="", product=""):
        out = []
        for r in S["rows"].values():
            if vendor and r["vendor"] not in vendor.split(",") or week and r["for_week"].isoformat() not in week.split(",") or product and r["product"] not in product.split(","):
                continue
            out.append(H["view"](r))
        return out

    def ordered(rows):
        return sorted(rows, key=lambda c: (0 if c["priority"] == "high" else 1, c["position"], c["id"]))

    @app.get("/api/priorities/board")
    def board():
        a = request.args
        week, vendor, product = a.get("week") or "", a.get("vendor") or "", a.get("product") or ""
        closed = a.get("closed") in ("1", "true")
        allc = cards(vendor, week, product)
        cols = [{"status": s, "label": LABEL[s], "count": 0, "cards": ordered([c for c in allc if c["status"] == s and c["stage"] != "dropped"])} for s in ORDER]
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat()
        for c in cols:
            if c["status"] == "launched":
                keep = [x for x in c["cards"] if closed or x["status_changed_at"] >= old]
                c["hidden"] = len(c["cards"]) - len(keep)
                c["cards"] = keep
            c["count"] = len(c["cards"])
        wk = week or H["pa"].monday_of(dt.date.today()).isoformat()
        cap = [{"vendor": v, "per_week": S["capacity"].get(v, 10), "open": sum(1 for c in cards(v, wk) if c["status"] not in ("launched", "dropped")), "week": wk} for v in VENDORS]
        out = {"columns": cols, "capacity": cap, "week": week or None, "generated_at": _now()}
        if closed:
            out["dropped"] = ordered([c for c in allc if c["status"] == "dropped" or c["stage"] == "dropped"])
        return jsonify(out)

    @app.post("/api/priorities/assignments/<int:aid>/move")
    def move(aid):
        r = S["rows"].get(aid)
        if r is None:
            return jsonify(error="Assignment not found."), 404
        b = request.get_json(silent=True) or {}
        try:
            to = b.get("status")
            check_move(H, r, to, b.get("comment"))
            col = [x for x in S["rows"].values() if x["id"] != aid and H["view"](x)["status"] == to]
            pos = {x["id"]: x["position"] for x in col}
            before, after = b.get("before_id"), b.get("after_id")
            ensure(r)
            if after in pos and before in pos:
                r["position"] = (pos[after] + pos[before]) / 2
            elif after in pos:
                r["position"] = pos[after] + 1
            elif before in pos:
                r["position"] = pos[before] - 1
            else:
                r["position"] = (max(pos.values()) + 1) if pos else 1.0
            set_status(H, r, to, b.get("comment"))
        except Refuse as e:
            return jsonify(error=e.message), e.status
        card = H["view"](r)
        ids = [c["id"] for c in ordered([H["view"](x) for x in S["rows"].values() if H["view"](x)["status"] == to])]
        return jsonify(card=card, column={"status": to, "ids": ids})

    @app.get("/api/priorities/assignments/<int:aid>/comments")
    def comments_get(aid):
        r = S["rows"].get(aid)
        if r is None:
            return jsonify(error="Assignment not found."), 404
        ensure(r)
        return jsonify(comments=list(r["comments"]))

    @app.post("/api/priorities/assignments/<int:aid>/comments")
    def comments_post(aid):
        r = S["rows"].get(aid)
        if r is None:
            return jsonify(error="Assignment not found."), 404
        body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
        if not 1 <= len(body) <= 2000:
            return jsonify(error="A comment needs 1 to 2000 characters."), 400
        return jsonify(comment=add_comment(H, r, body, "app", "Josh")), 201

    @app.get("/api/priorities/vendor-capacity")
    def cap_get():
        return jsonify(capacity=[{"vendor": v, "per_week": S["capacity"].get(v, 10)} for v in VENDORS])

    @app.put("/api/priorities/vendor-capacity")
    def cap_put():
        b = request.get_json(silent=True) or {}
        v, n = b.get("vendor"), b.get("per_week")
        if v not in VENDORS:
            return jsonify(error="vendor must be one of: " + ", ".join(VENDORS)), 400
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 200:
            return jsonify(error="per_week must be a whole number from 0 to 200."), 400
        S["capacity"][v] = n
        return jsonify(vendor=v, per_week=n, updated_at=_now())

    @app.post("/__test/board/seed")
    def seed():
        mon = H["pa"].monday_of(dt.date.today())
        today = dt.date.today()
        base = {k: v for k, v in S["rows"][1].items()}
        spec = [  # id, vendor, week offset, product, pain point, status, due offset, priority, format, creative, brief
            (5, "Madre", 0, "vita-bull", "Energy Crash", "in_production", -2, "high", "F1", None, None),
            (6, "Victoria", 0, "beef-liver", "Brain Fog", "in_production", 1, "normal", None, "https://drive.google.com/file/d/6/view", None),
            (7, "Victoria", 0, "boneflex", "Knee Pain", "in_review", 5, "normal", "F2", "https://drive.google.com/file/d/7/view", "https://docs.google.com/document/d/7"),
            (8, "Madre", 0, "boneflex", "Morning Stiffness", "brief_sent", 9, "normal", None, None, "https://docs.google.com/document/d/8"),
            (9, "TrybeDCShop", 7, "vita-bull", "Low Drive", "assigned", 12, "normal", None, None, None),
            (10, "Victoria", 0, "vita-bull", "Belly Fat", "ready", 3, "normal", "F3", "https://drive.google.com/file/d/10/view", None),
            (11, "Madre", -14, "vita-bull", "Old Winner", "launched", None, "normal", None, "https://drive.google.com/file/d/11/view", None),
        ]
        for i, v, wo, prod, pain, status, due, prio, fc, cu, bu in spec:
            r = {**base, "id": i, "vendor": v, "for_week": mon + dt.timedelta(days=wo), "product": prod, "pain_point": pain, "cluster": "Seed cluster", "icp": "Seed persona", "format_code": fc, "creative_url": cu, "brief_url": bu,
                 "tracker_concept_ids": [], "_suggested": [], "ready_at": None, "launched_at": None, "dropped_at": None, "note": "", "vendor_note": "", "concept_key": f"seed-{i}", "text_key": f"seed-{i}", "story": ""}
            r.pop("_ensured", None)
            S["rows"][i] = r
            ensure(r)
            r["_status"] = status
            r["due_date"] = (today + dt.timedelta(days=due)).isoformat() if due is not None else None
            r["priority"] = prio
            r["position"] = float(i)
            r["status_changed_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30 if i == 11 else 1)).isoformat()
            S["events"][i] = [{"at": _now(), "actor": "josh", "kind": "assigned", "stage_before": None, "stage_after": "assigned", "status_before": None, "status_after": "assigned", "detail": f"{v}, week of {r['for_week'].isoformat()}"}]
        S["seq"] = max(S["seq"], 11)
        r = S["rows"][7]
        r["comments"] = [{"id": 1, "author_kind": "vendor", "author_name": "Victoria", "body": "First cut is in the folder.", "created_at": _now()}, {"id": 2, "author_kind": "app", "author_name": "Josh", "body": "Looks good, checking the hook.", "created_at": _now()}]
        return jsonify(ok=True, ids=[s[0] for s in spec])
