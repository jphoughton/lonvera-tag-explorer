"""The Needs brief queue (app/priorities_backlog.py, /api/priorities/backlog): add cuts for a week, edit, remove, and assign a partner and a brief to several at once, which creates board tickets."""
import pytest

from .test_api_keys import fresh_throttle, key, mint                                                # noqa: F401  (fixtures)
from .test_api_priorities_assign import MONDAY, NEXT_MONDAY, clean                                   # noqa: F401  (fixture: clean)

ITEM = {"product": "vita-bull", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T", "media_type": "video"}


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
    out = add(client, [ITEM, {"product": "boneflex", "cluster": "Joint", "icp": "Eileen", "pain_point": "Knee Pain", "media_type": "image"}])
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
    for body in ({"items": []}, {"items": [{}]}, {"items": [{**ITEM, "product": "nope"}]}, {"items": [{**ITEM, "media_type": "gif"}]}, {"items": ["x"]}, {"items": [ITEM] * 101}):
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
    ids = [a["id"] for a in add(client, [ITEM, {"product": "beef-liver", "cluster": "Hair Thinning", "icp": "Denise", "pain_point": "Shedding", "media_type": "video"}])["created"]]
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
    ids = [a["id"] for a in add(client, [{**ITEM, "media_type": "image"}, ITEM])["created"]]      # same cut, image and video are separate items
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


@pytest.mark.parametrize("missing", ["product", "cluster", "icp", "pain_point", "media_type"])
def test_each_of_the_five_tags_is_required_to_enter_needs_brief(client, missing):
    r = client.post("/api/priorities/backlog", json={"items": [{k: v for k, v in ITEM.items() if k != missing}], "for_week": MONDAY})
    assert r.status_code == 400
    assert {"product": "product", "cluster": "market cluster", "icp": "ICP", "pain_point": "pain point", "media_type": "image or video"}[missing] in r.get_json()["error"]


def test_story_is_stored_edited_and_carried_to_the_ticket(client):
    item = add(client, [{**ITEM, "story": "He decided feeling worn out is just turning 50, and stopped trying."}])["created"][0]
    assert item["story"].startswith("He decided")
    assert client.patch(f"/api/priorities/backlog/{item['id']}", json={"story": "He quit trying at 50."}).get_json()["story"] == "He quit trying at 50."
    t = client.post("/api/priorities/backlog/assign", json={"ids": [item["id"]], "vendor": "Madre", "brief": "Write it."}).get_json()["created"][0]
    assert "He quit trying at 50." in t["story"]
    assert client.post("/api/priorities/backlog", json={"items": [{**ITEM, "pain_point": "Other", "story": "x" * 301}], "for_week": MONDAY}).status_code == 400


def test_a_waiting_item_missing_a_tag_cannot_be_assigned_until_it_is_filled_in(client, pg):
    pg.execute("INSERT INTO creative_backlog (product, cluster, icp, pain_point, title, for_week) VALUES ('boneflex', '', 'Eileen', 'Knee Pain', 'Old item', %s)", (MONDAY,))
    pg.commit()
    item = client.get("/api/priorities/backlog").get_json()["rows"][0]
    r = client.post("/api/priorities/backlog/assign", json={"ids": [item["id"]], "vendor": "Madre", "brief": "x"})
    assert r.status_code == 400 and "market cluster" in r.get_json()["error"] and "image or video" in r.get_json()["error"]
    assert client.patch(f"/api/priorities/backlog/{item['id']}", json={"cluster": "  "}).status_code == 400
    assert client.patch(f"/api/priorities/backlog/{item['id']}", json={"cluster": "Joint", "media_type": "image"}).status_code == 200
    assert client.post("/api/priorities/backlog/assign", json={"ids": [item["id"]], "vendor": "Madre", "brief": "x"}).status_code == 201


def test_a_team_key_can_run_the_whole_needs_brief_flow_and_a_partner_key_cannot(anon, key, mint, pg):
    from etl import api_keys as cli
    from .test_api_keys import bearer
    h = bearer(key)
    r = anon.post("/api/priorities/backlog", json={"items": [ITEM], "for_week": MONDAY}, headers=h)
    assert r.status_code == 201, r.get_data(as_text=True)
    item = r.get_json()["created"][0]
    assert anon.patch(f"/api/priorities/backlog/{item['id']}", json={"note": "via the API"}, headers=h).status_code == 200
    assert anon.get("/api/priorities/backlog", headers=h).get_json()["rows"][0]["note"] == "via the API"
    assert anon.get("/api/priorities/cuts", query_string={"group": "product"}, headers=h).status_code == 200
    a = anon.post("/api/priorities/backlog/assign", json={"ids": [item["id"]], "vendor": "Madre", "brief": "x"}, headers=h)
    assert a.status_code == 201 and a.get_json()["created"][0]["media_type"] == "video"
    t = a.get_json()["created"][0]
    assert anon.patch(f"/api/priorities/assignments/{t['id']}", json={"media_type": "image"}, headers=h).get_json()["media_type"] == "image"
    assert anon.delete(f"/api/priorities/backlog/{item['id']}", headers=h).get_json() == {"removed": 0}                 # an assigned item is history and stays
    partner, _ = cli.create_key(pg, "pk-backlog", "pytest", "partner_push", "Madre")
    for method, path, body in (("post", "/api/priorities/backlog", {"items": [ITEM]}), ("post", "/api/priorities/backlog/assign", {"ids": [1], "vendor": "Madre", "brief": "x"})):
        assert getattr(anon, method)(path, json=body, headers=bearer(partner)).status_code == 403
    assert anon.get("/api/priorities/backlog", headers=bearer(partner)).status_code == 403
    assert anon.get("/api/priorities/cuts", headers=bearer(partner)).status_code == 403
