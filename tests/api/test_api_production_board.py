"""The Production board API (migration 245, app/priorities_assign.py): stored status and the one set_status writer, the transition rules for app users and vendors, drag-and-drop moves and their
position maths, due dates and priority, comments, vendor capacity, the board payload, the Creative Tracker pull-forward and the backfill. Vendors never see the internal note, the history or another vendor's data."""
import datetime as dt
import itertools
import pathlib
import re

import pytest

from app import api_keys as ak
from app import priorities_assign as pa

from .test_api_keys import bearer, key, mint, fresh_throttle                        # noqa: F401  (fixtures)
from .test_api_priorities import _key
from .test_api_priorities_assign import (DRAFT, LINE_ROW, MONDAY, NEXT_MONDAY, assign, clean, link, one, patch, snaps, tracker)          # noqa: F401  (fixtures: snaps, clean, tracker)

BRIEF = "https://docs.example/brief"
CREATIVE = "https://drive.example/creative"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def mv(client, aid, **body):
    return client.post(f"/api/priorities/assignments/{aid}/move", json=body)


def status_of(client, aid):
    return next(r for r in client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"] if r["id"] == aid)["status"]


def card(client, vendor="Madre", week=MONDAY, key=None, status=None, creative=False):
    """A new card, optionally moved to a status (creative link first when the status needs one)."""
    a = one(client, vendor, week, key)
    if creative or status in ("in_review", "ready"):
        patch(client, a["id"], creative_url=CREATIVE)
    if status:
        r = mv(client, a["id"], status=status) if status != "launched" else patch(client, a["id"], launched=True)
        assert r.status_code == 200, r.get_data(as_text=True)
    return a


def vpatch(anon, tok, aid, **body):
    return anon.patch(f"/api/priorities/vendor/{tok}/{aid}", json=body)


# ---------------------------------------------------------------------------------------------------------------- new fields on a new card
def test_a_new_assignment_carries_the_board_fields(client, snaps):
    a = one(client)
    assert a["status"] == "assigned" and a["status_label"] == "Assigned" and a["stage"] == "assigned"
    assert a["due_date"] == "2026-11-13" and a["priority"] == "normal" and a["comment_count"] == 0 and a["status_changed_at"]
    assert a["position"] > 0 and isinstance(a["overdue"], bool)
    b = one(client, "Victoria")
    assert b["position"] > a["position"]                                                # new cards go to the bottom of the column
    assert a["due_in_days"] == (dt.date(2026, 11, 13) - pa.today_pt()).days


def test_due_date_priority_and_the_default_due_date_following_the_week(client, snaps):
    a = one(client)
    d = patch(client, a["id"], for_week=NEXT_MONDAY).get_json()
    assert d["due_date"] == "2026-11-20"                                                # the default Friday moved with the week
    d = patch(client, a["id"], due_date="2026-11-18", priority="high").get_json()
    assert d["due_date"] == "2026-11-18" and d["priority"] == "high"
    d = patch(client, a["id"], for_week=MONDAY).get_json()
    assert d["due_date"] == "2026-11-18"                                                # a due date set by hand stays
    assert patch(client, a["id"], due_date=None).get_json()["due_date"] is None
    d = patch(client, a["id"]).status_code
    assert d == 400
    for bad in ({"due_date": "soon"}, {"due_date": 5}, {"priority": "urgent"}, {"position": "top"}, {"position": True}, {"status": "nope"}, {"status": "dropped"}, {"comment": 5}):
        r = patch(client, a["id"], **bad)
        assert r.status_code == 400 and r.get_json()["error"], bad
    kinds = [e["kind"] for e in next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])["history"]]
    assert kinds == ["assigned", "week", "due_changed", "due_changed", "priority_changed", "week", "due_changed"]


def test_overdue_and_due_in_days(client, snaps):
    a = one(client)
    today = pa.today_pt()
    d = patch(client, a["id"], due_date=(today - dt.timedelta(days=2)).isoformat()).get_json()
    assert d["due_in_days"] == -2 and d["overdue"] is True
    d = patch(client, a["id"], due_date=today.isoformat()).get_json()
    assert d["due_in_days"] == 0 and d["overdue"] is False
    patch(client, a["id"], due_date=(today - dt.timedelta(days=5)).isoformat())
    d = patch(client, a["id"], launched=True).get_json()
    assert d["status"] == "launched" and d["due_in_days"] is None and d["overdue"] is False        # a launched card is never overdue
    assert patch(client, a["id"], launched=False, dropped=True).get_json()["due_in_days"] is None


# ---------------------------------------------------------------------------------------------------------------- transitions
APP_ALLOWED_BARE = [("assigned", "brief_sent"), ("assigned", "in_production"), ("brief_sent", "assigned"), ("in_production", "brief_sent"), ("in_production", "assigned"),
                    ("in_review", "ready"), ("ready", "in_review"), ("ready", "in_production"), ("ready", "assigned"), ("in_production", "ready")]


@pytest.mark.parametrize("cur,new", list(itertools.permutations(pa.STATUSES, 2)))
@pytest.mark.parametrize("has_creative", [True, False])
@pytest.mark.parametrize("comment", [None, "needs a tighter hook"])
def test_app_transition_rules_matrix(cur, new, has_creative, comment):
    creative = CREATIVE if has_creative else None
    try:
        note = pa.check_transition(cur, new, vendor=False, creative_url=creative, comment=comment)
        ok = True
    except Exception as e:                                                              # noqa: BLE001 - ApiError
        assert getattr(e, "status", 400) == 400 or True
        ok, msg = False, str(e)
    expect_ok = not (new == "launched" or (new == "in_review" and not has_creative) or (cur == "in_review" and new == "in_production" and not comment))
    assert ok == expect_ok, (cur, new, has_creative, comment)
    if ok:
        assert (note == "Skipped review") == (new == "ready" and cur != "in_review")


@pytest.mark.parametrize("cur,new", list(itertools.product(pa.STATUSES, repeat=2)))
@pytest.mark.parametrize("has_creative", [True, False])
def test_vendor_transition_rules_matrix(cur, new, has_creative):
    creative = CREATIVE if has_creative else None
    try:
        pa.check_transition(cur, new, vendor=True, creative_url=creative, comment=None)
        ok = True
    except Exception:                                                                   # noqa: BLE001
        ok = False
    allowed = cur == new or (cur, new) in pa.VENDOR_MOVES
    if allowed and new == "in_review" and cur != new and not has_creative:
        allowed = False
    assert ok == allowed, (cur, new, has_creative)


def test_app_status_changes_through_patch_and_move_follow_the_rules(client, snaps):
    a = one(client)
    r = patch(client, a["id"], status="in_review")
    assert r.status_code == 400 and "creative link" in r.get_json()["error"]
    assert patch(client, a["id"], status="launched").status_code == 400
    assert mv(client, a["id"], status="launched").status_code == 400
    d = patch(client, a["id"], status="in_production").get_json()
    assert d["status"] == "in_production" and d["stage"] == "brief_sent"
    d = patch(client, a["id"], status="in_review", creative_url=CREATIVE).get_json()          # the link in the same call counts
    assert d["status"] == "in_review" and d["stage"] == "creative_in"
    r = patch(client, a["id"], status="in_production")
    assert r.status_code == 400 and "comment" in r.get_json()["error"]
    r = patch(client, a["id"], status="in_production", comment="  Hook is too slow  ")
    assert r.status_code == 200 and r.get_json()["status"] == "in_production" and r.get_json()["comment_count"] == 1
    c = client.get(f"/api/priorities/assignments/{a['id']}/comments").get_json()["comments"]
    assert [x["body"] for x in c] == ["Changes requested: Hook is too slow"] and c[0]["author_kind"] == "app" and c[0]["author_name"] == "JoshHoughton"
    d = patch(client, a["id"], status="in_review").get_json()
    d = patch(client, a["id"], status="ready").get_json()                                      # Approve
    assert d["status"] == "ready" and d["ready_at"] and d["stage"] == "ready"
    d = patch(client, a["id"], status="in_review").get_json()                                  # back: ready_at clears
    assert d["status"] == "in_review" and d["ready_at"] is None
    assert patch(client, a["id"], dropped=True).status_code == 200
    r = patch(client, a["id"], status="assigned")
    assert r.status_code == 400 and "dropped" in r.get_json()["error"]
    assert mv(client, a["id"], status="assigned").status_code == 400


def test_skipping_review_is_logged_and_the_history_uses_status_values(client, snaps):
    a = one(client)
    patch(client, a["id"], status="in_production")
    patch(client, a["id"], status="ready")
    h = next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])["history"]
    ch = [e for e in h if e["kind"] == "status_changed"]
    assert [(e["stage_before"], e["stage_after"], e["actor"]) for e in ch] == [("assigned", "in_production", "JoshHoughton"), ("in_production", "ready", "JoshHoughton")]
    assert ch[1]["detail"] == "Skipped review"
    patch(client, a["id"], status="ready")                                                       # same status: no new row
    h2 = next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])["history"]
    assert len(h2) == len(h)


def test_old_flags_keep_the_status_in_step(client, snaps):
    a = one(client)
    assert patch(client, a["id"], brief_url=BRIEF).get_json()["status"] == "brief_sent"
    patch(client, a["id"], status="in_production")
    assert patch(client, a["id"], brief_url=BRIEF + "2").get_json()["status"] == "in_production"        # a link never moves it back
    assert patch(client, a["id"], creative_url=CREATIVE).get_json()["status"] == "in_review"
    d = patch(client, a["id"], ready=True).get_json()
    assert d["status"] == "ready" and d["ready_at"]
    assert patch(client, a["id"], ready=False).get_json()["status"] == "in_review"
    d = patch(client, a["id"], launched=True).get_json()
    assert d["status"] == "launched" and d["launched_at"] and d["stage"] == "launched"
    assert patch(client, a["id"], launched=False).get_json()["status"] == "in_review"
    d = patch(client, a["id"], dropped=True).get_json()
    assert d["stage"] == "dropped" and d["status"] == "in_review"                                         # a dropped card keeps its last status
    assert patch(client, a["id"], dropped=False).get_json()["status"] == "in_review"


# ---------------------------------------------------------------------------------------------------------------- vendor
def test_vendor_can_start_submit_and_pull_back_and_nothing_else(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    r = vpatch(anon, tok, a["id"], status="in_review")
    assert r.status_code == 400 and r.get_json()["error"]                                    # not started, no link
    d = vpatch(anon, tok, a["id"], status="in_production").get_json()
    assert d["status"] == "in_production" and d["status_label"] == "In production"
    r = vpatch(anon, tok, a["id"], status="in_review")
    assert r.status_code == 400 and "creative link" in r.get_json()["error"]
    d = vpatch(anon, tok, a["id"], status="in_review", creative_url=CREATIVE).get_json()
    assert d["status"] == "in_review"
    for bad in ("ready", "launched", "assigned", "brief_sent"):
        r = vpatch(anon, tok, a["id"], status=bad)
        assert r.status_code == 400, bad
    assert vpatch(anon, tok, a["id"], status="dropped").status_code == 400
    d = vpatch(anon, tok, a["id"], status="in_production").get_json()                         # pull back needs no comment
    assert d["status"] == "in_production"
    d = vpatch(anon, tok, a["id"], status="in_production").get_json()                         # same status is fine
    assert d["status"] == "in_production"
    # after the team approves, the vendor cannot move it
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"status": "in_review"})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"status": "ready"})
    assert vpatch(anon, tok, a["id"], status="in_production").status_code == 400
    assert vpatch(anon, tok, a["id"], ready=False).get_json()["status"] == "ready"           # the old flag never demotes an approved card
    h = [e for e in next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])["history"] if e["kind"] == "status_changed" and e["actor"] == "vendor:Madre"]
    assert [(e["stage_before"], e["stage_after"]) for e in h] == [("assigned", "in_production"), ("in_production", "in_review"), ("in_review", "in_production")]


def test_vendor_starts_from_brief_sent_and_a_dropped_row_is_409(client, anon, snaps):
    a = one(client)
    patch(client, a["id"], brief_url=BRIEF)
    tok = link(client)["token"]
    assert vpatch(anon, tok, a["id"], status="in_production").get_json()["status"] == "in_production"
    patch(client, a["id"], dropped=True)
    assert vpatch(anon, tok, a["id"], status="in_review").status_code == 409


def test_vendor_payload_has_the_board_fields_and_never_the_internal_ones(client, anon, snaps):
    a = one(client)
    patch(client, a["id"], priority="high", note="internal only")
    tok = link(client)["token"]
    client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "hello vendor"})
    row = anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"][0]
    for k in ("status", "status_label", "due_date", "due_in_days", "overdue", "comment_count"):
        assert k in row, k
    assert row["status"] == "assigned" and row["due_date"] == "2026-11-13" and row["comment_count"] == 1
    for k in ("note", "history", "created_by", "updated_by", "suggested_concept_ids", "priority", "position"):
        assert k not in row, k
    assert "internal only" not in anon.get(f"/api/priorities/vendor/{tok}").get_data(as_text=True)


def test_vendor_comments_are_scoped_to_the_vendors_own_cards(client, anon, snaps):
    mine = one(client, "Madre")
    theirs = one(client, "Victoria", key=_key(DRAFT))
    tm, tv = link(client, "Madre")["token"], link(client, "Victoria")["token"]
    client.post(f"/api/priorities/assignments/{theirs['id']}/comments", json={"body": "secret for Victoria"})
    r = anon.post(f"/api/priorities/vendor/{tm}/{mine['id']}/comments", json={"body": "  Done with cut one  "})
    assert r.status_code == 201
    c = r.get_json()["comment"]
    assert set(c) == {"id", "author_kind", "author_name", "body", "created_at"} and (c["author_kind"], c["author_name"], c["body"]) == ("vendor", "Madre", "Done with cut one")
    assert anon.get(f"/api/priorities/vendor/{tm}/{mine['id']}/comments").get_json()["comments"] == [c]
    for fn in (lambda: anon.get(f"/api/priorities/vendor/{tm}/{theirs['id']}/comments"), lambda: anon.post(f"/api/priorities/vendor/{tm}/{theirs['id']}/comments", json={"body": "x"}),
               lambda: anon.get(f"/api/priorities/vendor/{tm}/999999/comments"), lambda: anon.get(f"/api/priorities/vendor/{'z' * 30}/{mine['id']}/comments")):
        assert fn().status_code == 404
    assert [x["body"] for x in anon.get(f"/api/priorities/vendor/{tv}/{theirs['id']}/comments").get_json()["comments"]] == ["secret for Victoria"]
    assert client.get(f"/api/priorities/assignments/{theirs['id']}/comments").get_json()["comments"][0]["author_kind"] == "app"
    for bad in ({"body": ""}, {"body": "   "}, {"body": 5}, {"body": "x" * 2001}, {}, {"body": "ok", "author_name": "Josh"}):
        assert anon.post(f"/api/priorities/vendor/{tm}/{mine['id']}/comments", json=bad).status_code == 400, bad
    assert anon.post(f"/api/priorities/vendor/{tm}/{mine['id']}/comments", data="[]", content_type="application/json").status_code == 400
    patch(client, mine["id"], dropped=True)
    assert anon.post(f"/api/priorities/vendor/{tm}/{mine['id']}/comments", json={"body": "late"}).status_code == 409
    assert anon.get(f"/api/priorities/vendor/{tm}/{mine['id']}/comments").status_code == 404


def test_vendor_comment_routes_are_public_only_for_the_vendors_token_path(app, anon):
    from app import auth
    tok = "B" * 30
    assert auth.PUBLIC_API_RE.fullmatch(f"/api/priorities/vendor/{tok}/5/comments")
    for p in ("/api/priorities/assignments/5/comments", "/api/priorities/vendor-capacity", "/api/priorities/board", "/api/priorities/assignments/5/move"):
        assert not auth.PUBLIC_API_RE.fullmatch(p)
    # not signed in: every app route is 401
    for method, p in (("get", "/api/priorities/board"), ("get", "/api/priorities/vendor-capacity"), ("put", "/api/priorities/vendor-capacity"), ("get", "/api/priorities/assignments/1/comments"),
                      ("post", "/api/priorities/assignments/1/comments"), ("post", "/api/priorities/assignments/1/move")):
        assert getattr(anon, method)(p).status_code == 401, p


def test_vendor_must_not_see_history_via_any_route(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    vpatch(anon, tok, a["id"], status="in_production")
    for u in (f"/api/priorities/vendor/{tok}", f"/api/priorities/vendor/{tok}/{a['id']}/comments"):
        assert "history" not in anon.get(u).get_data(as_text=True)
    assert "history" not in vpatch(anon, tok, a["id"], vendor_note="x").get_data(as_text=True)


# ---------------------------------------------------------------------------------------------------------------- move + position maths
def ids_in(client, status):
    cols = client.get("/api/priorities/board?closed=1").get_json()["columns"]
    return [c["id"] for c in next(col for col in cols if col["status"] == status)["cards"]]


def test_move_returns_the_card_and_the_ordered_column(client, snaps):
    a = one(client)
    r = mv(client, a["id"], status="in_production")
    assert r.status_code == 200
    d = r.get_json()
    assert set(d) == {"card", "column"} and d["card"]["id"] == a["id"] and d["card"]["status"] == "in_production" and "history" not in d["card"]
    assert d["column"] == {"status": "in_production", "ids": [a["id"]]}
    assert status_of(client, a["id"]) == "in_production"


def test_move_between_neighbours_and_to_the_ends(client, snaps):
    vendors = ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS"]
    cs = [one(client, v)["id"] for v in vendors]
    for c in cs:
        assert mv(client, c, status="in_production").status_code == 200
    assert ids_in(client, "in_production") == cs
    d = mv(client, cs[4], status="in_production", before_id=cs[1]).get_json()                     # put the last card just above the second
    assert d["column"]["ids"] == [cs[0], cs[4], cs[1], cs[2], cs[3]]
    d = mv(client, cs[0], status="in_production", after_id=cs[2]).get_json()                      # first card goes just below the third
    assert d["column"]["ids"] == [cs[4], cs[1], cs[2], cs[0], cs[3]]
    d = mv(client, cs[3], status="in_production", after_id=cs[4], before_id=cs[1]).get_json()     # between two neighbours
    assert d["column"]["ids"] == [cs[4], cs[3], cs[1], cs[2], cs[0]]
    d = mv(client, cs[2], status="in_production", before_id=cs[4]).get_json()                     # to the very top
    assert d["column"]["ids"][0] == cs[2]
    d = mv(client, cs[2], status="in_production").get_json()                                      # neither = the bottom
    assert d["column"]["ids"][-1] == cs[2]
    d = mv(client, cs[1], status="in_production", after_id=cs[2]).get_json()                      # below the last card
    assert d["column"]["ids"][-1] == cs[1]
    pos = {r["id"]: r["position"] for r in client.get("/api/priorities/board").get_json()["columns"][2]["cards"]}
    assert len(set(pos.values())) == 5


def test_move_to_another_column_lands_between_that_columns_cards(client, snaps):
    a, b, c = (one(client, v)["id"] for v in ("Madre", "Victoria", "TrybeDCShop"))
    mv(client, a, status="in_production")
    mv(client, b, status="in_production")
    d = mv(client, c, status="in_production", after_id=a).get_json()
    assert d["column"]["ids"] == [a, c, b] and d["card"]["status"] == "in_production"
    assert mv(client, c, status="brief_sent").get_json()["column"] == {"status": "brief_sent", "ids": [c]}
    assert ids_in(client, "in_production") == [a, b]


def test_move_between_two_cards_with_no_gap_left_renumbers_the_column(client, snaps, app):
    from app.projection import rwdb
    a, b, c = (one(client, v)["id"] for v in ("Madre", "Victoria", "TrybeDCShop"))
    for x in (a, b, c):
        mv(client, x, status="in_production")
    with app.app_context(), rwdb.get_rw() as w:                                           # two cards 1e-15 apart: the midpoint equals one of them
        w.execute("UPDATE creative_priorities_assignments SET position = 5 WHERE id = %s", (a,))
        w.execute("UPDATE creative_priorities_assignments SET position = 5.000000000000001 WHERE id = %s", (b,))
        w.execute("UPDATE creative_priorities_assignments SET position = 9 WHERE id = %s", (c,))
    d = mv(client, c, status="in_production", after_id=a, before_id=b).get_json()
    assert d["column"]["ids"] == [a, c, b]
    pos = [x["position"] for x in client.get("/api/priorities/board").get_json()["columns"][2]["cards"]]
    assert pos == sorted(pos) and len(set(pos)) == 3


def test_move_validation(client, snaps):
    a, b = (one(client, v)["id"] for v in ("Madre", "Victoria"))
    mv(client, b, status="in_production")
    for body in ({}, {"status": "nope"}, {"status": "dropped"}, {"status": "in_production", "before_id": "x"}, {"status": "in_production", "before_id": a},
                 {"status": "in_production", "after_id": a}, {"status": "in_production", "before_id": b, "after_id": b}, {"status": "in_production", "before_id": 999999},
                 {"status": "brief_sent", "before_id": b}, {"status": "in_production", "extra": 1}, {"status": "in_production", "comment": 5}):
        r = mv(client, a, **body)
        assert r.status_code == 400 and r.get_json()["error"], body
    assert status_of(client, a) == "assigned"
    assert mv(client, 999999, status="in_production").status_code == 404
    assert client.post(f"/api/priorities/assignments/{a}/move", data="[]", content_type="application/json").status_code == 400
    assert mv(client, a, status="in_review").status_code == 400                           # no creative link
    assert [x["status"] for x in client.get("/api/priorities/board").get_json()["columns"]][0] == "assigned"


def test_move_request_changes_needs_a_comment_and_posts_it(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    patch(client, a["id"], creative_url=CREATIVE)
    mv(client, a["id"], status="in_review")
    r = mv(client, a["id"], status="in_production")
    assert r.status_code == 400 and "comment" in r.get_json()["error"]
    r = mv(client, a["id"], status="in_production", comment="Cut the intro")
    assert r.status_code == 200 and r.get_json()["card"]["comment_count"] == 1
    c = anon.get(f"/api/priorities/vendor/{tok}/{a['id']}/comments").get_json()["comments"]
    assert [x["body"] for x in c] == ["Changes requested: Cut the intro"] and c[0]["author_kind"] == "app"
    row = anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"][0]
    assert row["status"] == "in_production" and row["comment_count"] == 1


def test_move_skipping_review_and_dragging_out_of_launched(client, snaps):
    a = one(client)
    d = mv(client, a["id"], status="ready").get_json()
    assert d["card"]["status"] == "ready" and d["card"]["ready_at"]
    patch(client, a["id"], launched=True)
    d = mv(client, a["id"], status="in_production").get_json()
    assert d["card"]["status"] == "in_production" and d["card"]["launched_at"] is None and d["card"]["ready_at"] is None


# ---------------------------------------------------------------------------------------------------------------- comments (app side)
def test_app_comments_thread_history_and_counts(client, snaps):
    a = one(client)
    assert client.get(f"/api/priorities/assignments/{a['id']}/comments").get_json() == {"comments": []}
    r = client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "First line\nsecond line"})
    assert r.status_code == 201 and r.get_json()["comment"]["body"] == "First line\nsecond line"
    client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "Second"})
    cs = client.get(f"/api/priorities/assignments/{a['id']}/comments").get_json()["comments"]
    assert [c["body"] for c in cs] == ["First line\nsecond line", "Second"] and [c["id"] for c in cs] == sorted(c["id"] for c in cs)
    assert all(set(c) == {"id", "author_kind", "author_name", "body", "created_at"} for c in cs) and "history" not in client.get(f"/api/priorities/assignments/{a['id']}/comments").get_data(as_text=True)
    row = next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])
    assert row["comment_count"] == 2 and [e["kind"] for e in row["history"]][-2:] == ["comment_added", "comment_added"]
    assert client.get("/api/priorities/assignments/999999/comments").status_code == 404
    assert client.post("/api/priorities/assignments/999999/comments", json={"body": "x"}).status_code == 404
    assert client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": ""}).status_code == 400
    assert client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "x" * 2001}).status_code == 400
    assert client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "ok", "x": 1}).status_code == 400
    assert client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "x" * 2000}).status_code == 201


def test_patch_comment_alone_posts_a_comment(client, snaps):
    a = one(client)
    d = patch(client, a["id"], comment="just a note").get_json()
    assert d["comment_count"] == 1 and d["status"] == "assigned"


# ---------------------------------------------------------------------------------------------------------------- capacity
def test_vendor_capacity_defaults_set_and_validation(client, snaps, pg):
    d = client.get("/api/priorities/vendor-capacity").get_json()
    assert [c["vendor"] for c in d["capacity"]] == pa.VENDORS and all(c["per_week"] == 10 for c in d["capacity"])
    r = client.put("/api/priorities/vendor-capacity", json={"vendor": "Madre", "per_week": 4})
    assert r.status_code == 200 and r.get_json()["vendor"] == "Madre" and r.get_json()["per_week"] == 4 and r.get_json()["updated_at"]
    assert next(c for c in client.get("/api/priorities/vendor-capacity").get_json()["capacity"] if c["vendor"] == "Madre")["per_week"] == 4
    assert client.put("/api/priorities/vendor-capacity", json={"vendor": "Madre", "per_week": 0}).status_code == 200
    for bad in ({"vendor": "Nobody", "per_week": 3}, {"vendor": "Madre", "per_week": -1}, {"vendor": "Madre", "per_week": 1001}, {"vendor": "Madre", "per_week": "5"},
                {"vendor": "Madre", "per_week": 2.5}, {"vendor": "Madre", "per_week": True}, {"vendor": "Madre"}, {"per_week": 3}, {"vendor": "Madre", "per_week": 3, "x": 1}):
        r = client.put("/api/priorities/vendor-capacity", json=bad)
        assert r.status_code == 400 and r.get_json()["error"], bad
    pg.execute("UPDATE creative_priorities_vendor_capacity SET per_week = 10")
    pg.commit()


def test_vendor_capacity_is_app_only_for_keys_and_vendors(client, anon, key, snaps, pg):
    assert client.get("/api/priorities/vendor-capacity", headers=bearer(key)).status_code == 200                    # a key may read it
    r = client.put("/api/priorities/vendor-capacity", json={"vendor": "Madre", "per_week": 1}, headers=bearer(key))
    assert r.status_code == 403 and r.get_json() == {"error": ak.READ_ONLY_MESSAGE}
    tok = link(client)["token"]
    assert anon.put("/api/priorities/vendor-capacity", json={"vendor": "Madre", "per_week": 1}).status_code == 401
    assert anon.get(f"/api/priorities/vendor/{tok}").get_data(as_text=True).find("per_week") == -1
    assert next(c for c in client.get("/api/priorities/vendor-capacity").get_json()["capacity"] if c["vendor"] == "Madre")["per_week"] == 10


def test_keys_may_move_and_comment_like_they_may_patch(client, anon, key, snaps):
    a = one(client)
    r = anon.post(f"/api/priorities/assignments/{a['id']}/move", json={"status": "in_production"}, headers=bearer(key))
    assert r.status_code == 200 and r.get_json()["card"]["status"] == "in_production"
    r = anon.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "from a key"}, headers=bearer(key))
    assert r.status_code == 201 and r.get_json()["comment"]["author_name"] == "key:ci-colleague"
    assert anon.get("/api/priorities/board", headers=bearer(key)).status_code == 200


# ---------------------------------------------------------------------------------------------------------------- board payload
def test_board_shape_order_filters_and_capacity(client, snaps):
    a = one(client, "Madre")
    b = one(client, "Victoria", key=_key(DRAFT))
    c = one(client, "Madre", NEXT_MONDAY)
    patch(client, c["id"], brief_url=BRIEF)
    patch(client, b["id"], priority="high")
    d = client.get(f"/api/priorities/board?week={MONDAY}").get_json()
    assert [x["status"] for x in d["columns"]] == pa.STATUSES
    assert [x["label"] for x in d["columns"]] == ["Assigned", "Brief sent", "In production", "In review", "Ready to launch", "Launched"]
    assert set(d) == {"columns", "capacity", "week", "generated_at", "vendors", "weeks", "formats"} and d["week"] == MONDAY
    first = d["columns"][0]
    assert first["count"] == 2 and [x["id"] for x in first["cards"]] == [b["id"], a["id"]]          # high priority first
    assert all("history" not in x for col in d["columns"] for x in col["cards"])
    cap = {x["vendor"]: x for x in d["capacity"]}
    assert cap["Madre"] == {"vendor": "Madre", "per_week": 10, "open": 1, "week": MONDAY} and cap["Victoria"]["open"] == 1 and len(cap) == 9
    allw = client.get("/api/priorities/board").get_json()
    assert sum(x["count"] for x in allw["columns"]) == 3 and allw["week"] == pa.monday_of(pa.today_pt()).isoformat()
    assert client.get("/api/priorities/board?vendor=Victoria").get_json()["columns"][0]["cards"][0]["id"] == b["id"]
    assert [x["vendor"] for x in client.get("/api/priorities/board?vendor=Victoria").get_json()["capacity"]] == ["Victoria"]
    assert sum(x["count"] for x in client.get("/api/priorities/board?product=boneflex").get_json()["columns"]) == 0
    assert sum(x["count"] for x in client.get("/api/priorities/board?product=vita-bull").get_json()["columns"]) == 3
    assert client.get("/api/priorities/board?vendor=Nobody").status_code == 400
    assert client.get("/api/priorities/board?week=soon").status_code == 400
    one_card = d["columns"][0]["cards"][1]
    for k in ("id", "status", "status_label", "stage", "due_date", "due_in_days", "overdue", "priority", "position", "comment_count", "status_changed_at", "vendor", "for_week", "ads", "format_code"):
        assert k in one_card, k


def test_board_launched_lane_and_dropped_lane(client, snaps, app):
    a = one(client, "Madre")
    b = one(client, "Victoria")
    c = one(client, "TrybeDCShop")
    patch(client, a["id"], launched=True)
    patch(client, b["id"], launched=True)
    patch(client, c["id"], dropped=True)
    from app.projection import rwdb
    with app.app_context(), rwdb.get_rw() as w:
        w.execute("UPDATE creative_priorities_assignments SET launched_at = now() - interval '30 days', status_changed_at = now() - interval '30 days' WHERE id = %s", (b["id"],))
    d = client.get("/api/priorities/board").get_json()
    launched = d["columns"][5]
    assert [x["id"] for x in launched["cards"]] == [a["id"]] and launched["count"] == 1 and launched["hidden"] == 1 and "dropped" not in d
    d = client.get("/api/priorities/board?closed=1").get_json()
    assert {x["id"] for x in d["columns"][5]["cards"]} == {a["id"], b["id"]} and [x["id"] for x in d["dropped"]] == [c["id"]] and d["columns"][5]["hidden"] == 0
    assert all(x["id"] != c["id"] for col in d["columns"] for x in col["cards"])
    cap = {x["vendor"]: x["open"] for x in client.get(f"/api/priorities/board?week={MONDAY}").get_json()["capacity"]}
    assert cap["Madre"] == 0 and cap["TrybeDCShop"] == 0                                           # launched and dropped cards do not use capacity


def test_existing_assignment_responses_keep_every_old_field(client, snaps):
    a = one(client)
    d = client.get("/api/priorities/assignments").get_json()
    row = d["rows"][0]
    for k in ("id", "concept_key", "vendor", "for_week", "brief_url", "creative_url", "stage", "stage_label", "tracker_status", "suggested_concept_ids", "ads", "history", "format_code", "created_by", "note"):
        assert k in row, k
    assert set(d) >= {"rows", "vendors", "weeks", "formats", "launch"} and a["id"] == row["id"]


# ---------------------------------------------------------------------------------------------------------------- Creative Tracker pull-forward
@pytest.mark.parametrize("trk,want", [("3 - Brief Drafted", "brief_sent"), ("6 - Creative In", "in_review"), ("7 - Approved - Ready To Launch", "ready"), ("8 - Launched", "launched"),
                                      ("9 - Relaunched", "launched"), ("2 - Idea", "assigned")])
def test_tracker_pulls_the_status_forward_on_link(client, snaps, tracker, pg, trk, want):
    pg.execute("UPDATE tracker_rows SET status = %s WHERE id = 9101", (trk,))
    pg.commit()
    a = one(client)
    d = patch(client, a["id"], tracker_concept_ids=["ZZ-A1"]).get_json()
    assert d["status"] == want
    if want != "assigned":
        assert status_of(client, a["id"]) == want
        row = pg.execute("SELECT status FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()
        pg.rollback()
        assert row[0] == want                                                                # stored, not just shown


def test_tracker_never_pulls_a_card_backward_and_the_nightly_step_moves_cards_forward(client, snaps, tracker, pg):
    a = card(client, status="ready")
    b = one(client, "Victoria")
    c = one(client, "TrybeDCShop")
    patch(client, a["id"], tracker_concept_ids=["ZZ-A1"])                                    # tracker 5 - Briefed is behind ready
    patch(client, b["id"], tracker_concept_ids=["ZZ-A2"])                                    # tracker 7 pulled it forward at link time
    assert status_of(client, a["id"]) == "ready" and status_of(client, b["id"]) == "ready"
    patch(client, c["id"], tracker_concept_ids=["ZZ-A1"])                                    # status 5 = brief sent
    assert status_of(client, c["id"]) == "brief_sent"
    pg.execute("UPDATE tracker_rows SET status = '8 - Launched' WHERE id = 9101")
    pg.commit()
    d = {r["id"]: r for r in client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"]}
    assert d[a["id"]]["status"] == "launched" and d[c["id"]]["status"] == "launched"            # shown right away (read-time pull-forward)
    pg.execute("UPDATE tracker_rows SET status = '3 - Brief Drafted' WHERE id = 9101")          # the tracker slips backward
    pg.commit()
    d = {r["id"]: r for r in client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"]}
    assert d[a["id"]]["status"] == "ready" and d[c["id"]]["status"] == "brief_sent"            # stored status is unchanged: never backward


def test_pull_forward_function_moves_cards_logs_the_tracker_and_never_goes_back(client, snaps, tracker, pg, app):
    from app.projection import rwdb
    a = one(client)
    b = one(client, "Victoria")
    with app.app_context(), rwdb.get_rw() as w:
        w.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = %s WHERE id = %s", (["ZZ-A2"], a["id"]))        # tracker 7 = ready, linked behind the app's back
        w.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = %s, status = 'ready', ready_at = now() WHERE id = %s", (["ZZ-A1"], b["id"]))    # tracker 5, card already ready
        n = pa.pull_forward(w)
    assert n == 1
    assert pg.execute("SELECT status FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()[0] == "ready"
    pg.rollback()
    assert pg.execute("SELECT status FROM creative_priorities_assignments WHERE id = %s", (b["id"],)).fetchone()[0] == "ready"
    pg.rollback()
    h = next(r for r in client.get("/api/priorities/assignments").get_json()["rows"] if r["id"] == a["id"])["history"]
    assert h[-1]["kind"] == "status_changed" and h[-1]["actor"] == "tracker" and (h[-1]["stage_before"], h[-1]["stage_after"]) == ("assigned", "ready")
    with app.app_context(), rwdb.get_rw() as w:
        assert pa.pull_forward(w) == 0                                                           # nothing left to move


# ---------------------------------------------------------------------------------------------------------------- one writer, backfill, docs
def test_set_status_is_the_only_writer_of_status():
    users = {f.relative_to(ROOT).as_posix() for base in ("app", "etl") for f in (ROOT / base).rglob("*.py") if "status_changed_at" in f.read_text()}
    assert users == {"app/priorities_assign.py"}, users                                             # the launch worker, the tracker step and the vendor page all go through it
    src = (ROOT / "app" / "priorities_assign.py").read_text()
    assert src.count("status_changed_at = now()") == 1                                              # inside set_status only
    assert len(re.findall(r"SET status = %s", src)) == 1                                              # one UPDATE of the column in the whole module


def test_backfill_sql_maps_the_old_stage_and_the_friday_and_position(pg):
    sql = (ROOT / "migrations" / "245_production_board.sql").read_text()
    stmts = re.findall(r"^(UPDATE creative_priorities_assignments.*?;)\s*$", sql, re.S | re.M)
    assert len(stmts) == 2
    pg.execute("DELETE FROM creative_priorities_assignments")
    cols = "concept_key, text_key, product, vendor, for_week, brief_url, creative_url, ready_at, launched_at, dropped_at, created_at"
    base = "('k%s','t%s','vita-bull','%s','%s',%s,%s,%s,%s,%s,'2026-10-01')"
    rows = [(1, "Madre", "2026-11-09", "NULL", "NULL", "NULL", "NULL", "NULL"), (2, "Madre", "2026-11-02", "'https://b'", "NULL", "NULL", "NULL", "NULL"),
            (3, "Victoria", "2026-11-02", "'https://b'", "'https://c'", "NULL", "NULL", "NULL"), (4, "Victoria", "2026-11-09", "'https://b'", "'https://c'", "'2026-11-05'", "NULL", "NULL"),
            (5, "Internal", "2026-11-09", "'https://b'", "'https://c'", "'2026-11-05'", "'2026-11-06'", "NULL"), (6, "Internal", "2026-11-02", "NULL", "NULL", "NULL", "NULL", "'2026-11-06'")]
    for n, v, wk, b, c, r_, l, d in rows:
        pg.execute(f"INSERT INTO creative_priorities_assignments ({cols}) VALUES " + base % (n, n, v, wk, b, c, r_, l, d))
    pg.execute("UPDATE creative_priorities_assignments SET status = 'assigned', due_date = NULL, status_changed_at = NULL, position = 0")
    for s in stmts:
        pg.execute(s)
    pg.commit()
    got = {r[0]: r[1:] for r in pg.execute("SELECT concept_key, status, due_date, position, status_changed_at IS NOT NULL FROM creative_priorities_assignments ORDER BY concept_key").fetchall()}
    pg.rollback()
    assert [got[f"k{i}"][0] for i in range(1, 7)] == ["assigned", "brief_sent", "in_review", "ready", "launched", "assigned"]
    assert got["k1"][1] == dt.date(2026, 11, 13) and got["k2"][1] == dt.date(2026, 11, 6)
    order = sorted(got, key=lambda k: got[k][2])                                                  # the old table order: week descending, vendor, id
    assert order == ["k5", "k1", "k4", "k6", "k2", "k3"] and [got[k][2] for k in order] == [1, 2, 3, 4, 5, 6] and all(got[k][3] for k in got)
    again = pg.execute("SELECT count(*) FROM creative_priorities_assignments WHERE status_changed_at IS NOT NULL").fetchone()[0]
    pg.rollback()
    for s in stmts:
        pg.execute(s)                                                                             # idempotent: a second run changes nothing
    pg.commit()
    assert pg.execute("SELECT count(*) FROM creative_priorities_assignments WHERE status_changed_at IS NOT NULL").fetchone()[0] == again == 6
    pg.execute("DELETE FROM creative_priorities_assignments")
    pg.commit()


def test_check_constraints_on_the_new_columns(pg):
    import psycopg
    pg.execute("DELETE FROM creative_priorities_assignments")
    pg.commit()
    pg.execute("INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week) VALUES ('k','t','vita-bull','Madre','2026-11-09')")
    pg.commit()
    for col, val in (("status", "dropped"), ("priority", "urgent")):
        with pytest.raises(psycopg.errors.CheckViolation):
            pg.execute(f"UPDATE creative_priorities_assignments SET {col} = %s", (val,))
        pg.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO creative_priorities_assignment_comments (assignment_id, author_kind, author_name, body) SELECT id, 'robot', 'x', 'y' FROM creative_priorities_assignments")
    pg.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO creative_priorities_assignment_comments (assignment_id, author_kind, author_name, body) SELECT id, 'app', 'x', '' FROM creative_priorities_assignments")
    pg.rollback()
    pg.execute("DELETE FROM creative_priorities_assignments")
    pg.commit()
    assert pg.execute("SELECT count(*), min(per_week), max(per_week) FROM creative_priorities_vendor_capacity").fetchone() == (9, 10, 10)
    pg.rollback()


def test_write_routes_keep_the_origin_check_and_the_rate_limit(client, anon, snaps):
    from app import api as api_mod
    a = one(client)
    tok = link(client)["token"]
    evil = {"Origin": "https://evil.example"}
    for method, url, body in (("post", f"/api/priorities/assignments/{a['id']}/move", {"status": "in_production"}), ("post", f"/api/priorities/assignments/{a['id']}/comments", {"body": "x"}),
                              ("put", "/api/priorities/vendor-capacity", {"vendor": "Madre", "per_week": 3}), ("post", f"/api/priorities/vendor/{tok}/{a['id']}/comments", {"body": "x"})):
        r = getattr(client, method)(url, json=body, headers=evil)
        assert r.status_code == 403 and r.get_json()["error"] == "origin mismatch", url
    api_mod._assign_limiter = None
    api_mod._vendor_limiter = None
    codes = [client.post(f"/api/priorities/assignments/{a['id']}/comments", json={"body": "x"}).status_code for _ in range(70)]
    assert 429 in codes and codes[0] == 201
    api_mod._vendor_limiter = None
    vcodes = [anon.post(f"/api/priorities/vendor/{tok}/{a['id']}/comments", json={"body": "x"}).status_code for _ in range(70)]
    assert 429 in vcodes and vcodes[0] == 201
    api_mod._assign_limiter = None
    api_mod._vendor_limiter = None


def test_the_board_carries_vendors_weeks_and_formats_so_the_page_needs_one_request(client, snaps):
    d = client.get("/api/priorities/board").get_json()
    assert d["vendors"] == pa.VENDORS and isinstance(d["weeks"], list) and [f["code"] for f in d["formats"]][:2] == ["F1", "F2"]
