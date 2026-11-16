"""The Needs brief queue (app/priorities_backlog.py, /api/priorities/backlog): add cuts for a week, edit, remove, and assign a partner and a brief to several at once, which creates board tickets."""
import pytest

from .test_api_priorities_assign import MONDAY, NEXT_MONDAY, clean                                   # noqa: F401  (fixture: clean)

ITEM = {"product": "vita-bull", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T"}


@pytest.fixture(autouse=True)
def empty(pg, clean):                                                                                  # noqa: F811
    pg.execute("DELETE FROM creative_backlog")
    pg.commit()
    yield
    pg.execute("DELETE FROM creative_backlog")
    pg.commit()


def add(client, items=None, week=MONDAY, **extra):
    r = client.post("/api/priorities/backlog", json={"items": items or [ITEM], "for_week": week, **extra})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def test_add_list_and_dedupe(client):
    out = add(client, [ITEM, {"product": "boneflex", "pain_point": "Knee Pain"}])
    assert len(out["created"]) == 2 and out["created"][0]["title"] == "Vita Bull - Energy - Given Up Guy - Low T" and out["created"][0]["status"] == "needs_brief"
    again = add(client)
    assert again["created"] == [] and again["skipped"][0]["reason"].startswith("already")
    nxt = add(client, week=NEXT_MONDAY)["created"]
    assert len(nxt) == 1                                                                                # same cut, another week is a new item
    rows = client.get("/api/priorities/backlog").get_json()["rows"]
    assert len(rows) == 3 and rows[0]["for_week"] == MONDAY
    assert [r["id"] for r in client.get("/api/priorities/backlog?week=" + NEXT_MONDAY).get_json()["rows"]] == [nxt[0]["id"]]


def test_week_snaps_to_monday_and_bad_input_is_a_400(client):
    assert add(client, week="2026-11-11")["created"][0]["for_week"] == MONDAY
    for body in ({"items": []}, {"items": [{}]}, {"items": [{"product": "nope"}]}, {"items": ["x"]}, {"items": [ITEM] * 101}):
        assert client.post("/api/priorities/backlog", json=body).status_code == 400


def test_edit_and_remove(client):
    a = add(client)["created"][0]
    r = client.patch(f"/api/priorities/backlog/{a['id']}", json={"for_week": "2026-11-18", "note": "after the Madre call"})
    assert r.status_code == 200 and r.get_json()["for_week"] == NEXT_MONDAY and r.get_json()["note"] == "after the Madre call"
    assert client.patch(f"/api/priorities/backlog/{a['id']}", json={}).status_code == 400
    assert client.patch("/api/priorities/backlog/999999", json={"note": "x"}).status_code == 404
    assert client.delete(f"/api/priorities/backlog/{a['id']}").get_json() == {"removed": 1}
    assert client.get("/api/priorities/backlog").get_json()["rows"] == []


def test_assign_creates_board_tickets_and_marks_items(client):
    ids = [a["id"] for a in add(client, [ITEM, {"product": "beef-liver", "cluster": "Hair Thinning", "icp": "Denise", "pain_point": "Shedding"}])["created"]]
    r = client.post("/api/priorities/backlog/assign", json={"ids": ids, "vendor": "Madre", "brief_url": "https://docs.example/brief"})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()
    assert d["assigned"] == ids and len(d["created"]) == 2
    t = d["created"][0]
    assert t["vendor"] == "Madre" and t["for_week"] == MONDAY and t["status"] == "brief_sent" and t["concept_name"] == "Vita Bull - Energy - Given Up Guy - Low T" and t["product"] == "vita-bull"
    assert client.get("/api/priorities/backlog").get_json()["rows"] == []
    done = client.get("/api/priorities/backlog?include_assigned=1").get_json()["rows"]
    assert [x["assignment_id"] for x in done] == [c["id"] for c in d["created"]] and all(x["status"] == "assigned" for x in done)
    assert {c["id"] for c in d["created"]} <= {x["id"] for x in client.get("/api/priorities/assignments").get_json()["rows"]}
    assert client.post("/api/priorities/backlog/assign", json={"ids": ids, "vendor": "Madre", "brief": "again"}).status_code == 404          # no longer waiting


def test_assign_needs_a_vendor_and_a_brief_and_creates_nothing_otherwise(client):
    ids = [a["id"] for a in add(client)["created"]]
    for body in ({"ids": ids, "vendor": "Madre"}, {"ids": ids, "brief": "x"}, {"ids": ids, "vendor": "Nobody", "brief": "x"}, {"ids": [], "vendor": "Madre", "brief": "x"}):
        assert client.post("/api/priorities/backlog/assign", json=body).status_code == 400
    assert len(client.get("/api/priorities/backlog").get_json()["rows"]) == 1
    ok = client.post("/api/priorities/backlog/assign", json={"ids": ids, "vendor": "Madre", "brief": "Write a 45 second talking head about low T."})
    assert ok.status_code == 201 and ok.get_json()["created"][0]["status"] == "assigned"


def test_media_type_flows_from_backlog_to_ticket_and_into_the_cut(client):
    ids = [a["id"] for a in add(client, [{**ITEM, "media_type": "image"}, {**ITEM, "media_type": "video"}])["created"]]      # same cut, image and video are separate items
    assert len(ids) == 2
    d = client.post("/api/priorities/backlog/assign", json={"ids": ids, "vendor": "Madre", "brief": "x"}).get_json()
    assert sorted(c["media_type"] for c in d["created"]) == ["image", "video"]
    out = client.get("/api/priorities/cuts", query_string={"group": "media"}).get_json()
    got = {r["labels"]["media"]: r["concepts"] for r in out["rows"]}
    assert got.get("Image") == 1 and got.get("Video") == 1
    t = d["created"][0]
    assert client.patch(f"/api/priorities/assignments/{t['id']}", json={"media_type": "video"}).get_json()["media_type"] == "video"
    assert client.patch(f"/api/priorities/assignments/{t['id']}", json={"media_type": "gif"}).status_code == 400
    assert client.patch(f"/api/priorities/assignments/{t['id']}", json={"media_type": ""}).get_json()["media_type"] is None
