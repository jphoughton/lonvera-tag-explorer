"""Ad copy on a ticket (migration 282): PUT /api/priorities/assignments/<id>/copy and the partner route. Validation, replace and clear, history, who may write, and that a vendor link page never sees it."""
import json

import pytest

from .test_api_keys import bearer, fresh_throttle, mint, key, post_assign      # noqa: F401  (fixtures)
from .test_api_priorities_assign import MIN, MONDAY, clean, link, one, snaps    # noqa: F401
from .test_partner_keys import create, pkey, pk                                  # noqa: F401  (fixtures)

VARS = [{"label": "Execution A"}, {"label": "Execution B"}]


def mk(client, **kw):
    r = client.post("/api/priorities/tickets", json={**MIN, "variations": VARS, **kw})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def put(client, tid, body):
    return client.put(f"/api/priorities/assignments/{tid}/copy", json=body)


def stored(pg, tid):
    pg.rollback()
    return pg.execute("SELECT ad_copy FROM creative_priorities_assignments WHERE id = %s", (tid,)).fetchone()[0]


def test_a_new_ticket_has_empty_copy(client, snaps):
    assert mk(client)["ad_copy"] == {}


def test_copy_saves_and_comes_back_on_every_read(client, snaps, pg):
    t = mk(client)
    r = put(client, t["id"], {"variation": "execution a", "primary_texts": ["First text\nsecond line", "Text two"], "headlines": ["Head 1", "Head 2"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    want = {"Execution A": {"primary_texts": ["First text\nsecond line", "Text two"], "headlines": ["Head 1", "Head 2"]}}
    assert j["variation"] == "Execution A" and j["ad_copy"] == want and j["card"]["ad_copy"] == want
    assert stored(pg, t["id"]) == want
    rows = client.get("/api/priorities/assignments", query_string={"include_closed": 1}).get_json()["rows"]
    assert next(x for x in rows if x["id"] == t["id"])["ad_copy"] == want
    cards = [c for col in client.get("/api/priorities/board", query_string={"include_closed": 1}).get_json()["columns"] for c in col["cards"]]
    assert next(c for c in cards if c["id"] == t["id"])["ad_copy"] == want


def test_a_second_variation_is_kept_and_a_rewrite_replaces_only_its_own(client, snaps, pg):
    t = mk(client)
    put(client, t["id"], {"variation": "Execution A", "primary_texts": ["a"], "headlines": ["ha"]})
    put(client, t["id"], {"variation": "Execution B", "primary_texts": ["b"]})
    put(client, t["id"], {"variation": "Execution A", "headlines": ["only head"]})
    assert stored(pg, t["id"]) == {"Execution A": {"primary_texts": [], "headlines": ["only head"]}, "Execution B": {"primary_texts": ["b"], "headlines": []}}


def test_empty_lists_clear_that_variation(client, snaps, pg):
    t = mk(client)
    put(client, t["id"], {"variation": "Execution A", "primary_texts": ["a"]})
    r = put(client, t["id"], {"variation": "Execution A", "primary_texts": [], "headlines": []})
    assert r.status_code == 200 and r.get_json()["ad_copy"] == {} and stored(pg, t["id"]) == {}


def test_copy_is_logged_in_the_history(client, snaps):
    t = mk(client)
    put(client, t["id"], {"variation": "Execution B", "primary_texts": ["a", "b"], "headlines": ["h"]})
    put(client, t["id"], {"variation": "Execution B", "primary_texts": [], "headlines": []})
    ev = [e for e in client.get("/api/priorities/assignments", query_string={"include_closed": 1}).get_json()["rows"] if e["id"] == t["id"]][0]["history"]
    got = [e["detail"] for e in ev if e["kind"] == "copy_changed"]
    assert got == ["Execution B: 2 primary texts, 1 headlines", "Execution B: copy cleared"]


@pytest.mark.parametrize("body,msg", [
    ({"primary_texts": ["a"]}, "variation is required"),
    ({"variation": "Hook 9", "primary_texts": ["a"]}, "variation must be one of: Execution A, Execution B"),
    ({"variation": "Execution A", "primary_texts": ["x"] * 6}, "at most 5"),
    ({"variation": "Execution A", "headlines": ["x"] * 6}, "at most 5"),
    ({"variation": "Execution A", "primary_texts": ["x" * 2001]}, "at most 2000"),
    ({"variation": "Execution A", "headlines": ["x" * 256]}, "at most 255"),
    ({"variation": "Execution A", "primary_texts": [""]}, "is empty"),
    ({"variation": "Execution A", "primary_texts": "text"}, "must be a list"),
    ({"variation": "Execution A", "primary_texts": [5]}, "must be text"),
    ({"variation": "Execution A", "bogus": 1}, "unknown field"),
])
def test_bad_copy_is_a_plain_400(client, snaps, body, msg):
    t = mk(client)
    r = put(client, t["id"], body)
    assert r.status_code == 400 and msg in r.get_json()["error"], r.get_data(as_text=True)


def test_limits_are_inclusive(client, snaps):
    t = mk(client)
    r = put(client, t["id"], {"variation": "Execution A", "primary_texts": ["x" * 2000] * 5, "headlines": ["y" * 255] * 5})
    assert r.status_code == 200


def test_a_ticket_with_no_variations_takes_original(client, snaps):
    t = mk(client, variations=[])
    assert put(client, t["id"], {"variation": "Original", "primary_texts": ["a"]}).status_code == 200
    assert put(client, t["id"], {"variation": "Hook 2", "primary_texts": ["a"]}).status_code == 400


def test_unknown_ticket_is_404_and_dropped_is_refused(client, snaps):
    assert put(client, 987654, {"variation": "Original", "primary_texts": ["a"]}).status_code == 404
    t = mk(client)
    client.patch(f"/api/priorities/assignments/{t['id']}", json={"dropped": True})
    assert put(client, t["id"], {"variation": "Execution A", "primary_texts": ["a"]}).status_code == 400


def test_a_read_assign_key_can_write_copy(anon, key, client, snaps, pg):
    t = mk(client)
    r = anon.put(f"/api/priorities/assignments/{t['id']}/copy", json={"variation": "Execution A", "primary_texts": ["via key"]}, headers=bearer(key))
    assert r.status_code == 200 and stored(pg, t["id"])["Execution A"]["primary_texts"] == ["via key"]


def test_a_partner_writes_own_copy_only(anon, pk, pkey, client, snaps, pg):
    mine = create(anon, pk, variations=[{"label": "Execution A"}]).get_json()
    url = f"/api/priorities/partner/tickets/{mine['id']}/copy"
    r = anon.put(url, json={"variation": "Execution A", "primary_texts": ["p"], "headlines": ["h"]}, headers=bearer(pk))
    assert r.status_code == 200 and r.get_json()["ad_copy"] == {"Execution A": {"primary_texts": ["p"], "headlines": ["h"]}}
    assert anon.put(url, json={"variation": "Nope", "primary_texts": ["p"]}, headers=bearer(pk)).status_code == 400
    other = pkey("partner-victoria", "Victoria")
    assert anon.put(url, json={"variation": "Execution A", "primary_texts": ["x"]}, headers=bearer(other)).status_code == 404
    assert stored(pg, mine["id"])["Execution A"]["primary_texts"] == ["p"]
    allt = anon.get(f"/api/priorities/partner/all-tickets/{mine['id']}", headers=bearer(pk)).get_json()
    assert allt["ticket"]["ad_copy"]["Execution A"]["headlines"] == ["h"]


def test_a_caller_with_no_key_is_refused(anon, snaps, client):
    t = mk(client)
    assert anon.put(f"/api/priorities/partner/tickets/{t['id']}/copy", json={"variation": "Execution A", "primary_texts": ["x"]}).status_code in (401, 403)


def test_the_vendor_link_page_never_sees_copy(client, anon, snaps):
    d = link(client, "Studio Flow")
    t = mk(client)
    put(client, t["id"], {"variation": "Execution A", "primary_texts": ["secret copy"]})
    rows = anon.get("/api/priorities/vendor/" + d["token"]).get_json()["rows"]
    assert "secret copy" not in json.dumps(rows) and all("ad_copy" not in r for r in rows)
    assert anon.put(f"/api/priorities/vendor/{d['token']}/{t['id']}/copy", json={"variation": "Execution A", "primary_texts": ["x"]}).status_code in (401, 404, 405)
