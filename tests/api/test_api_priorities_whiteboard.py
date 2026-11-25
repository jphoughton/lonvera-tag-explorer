"""The This week whiteboard (app/priorities_whiteboard.py, /api/priorities/whiteboard): full-state save, lanes and rank, drop and restore, idempotent writes, ETag, validation, rate limit."""
import pytest

from .test_api_keys import fresh_throttle, key, mint                                                # noqa: F401  (fixtures)
from .test_api_priorities_assign import MONDAY, NEXT_MONDAY, clean                                   # noqa: F401  (fixture: clean)

URL = "/api/priorities/whiteboard"
A = {"concept_key": "line:1", "lane": "must", "product": "vita-bull", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T", "line_code": "VB01", "title": "Vita Bull - Low T"}
B = {"concept_key": "line:2", "lane": "if_time", "product": "boneflex", "title": "Boneflex - Knee"}
C = {"concept_key": "manual:3", "lane": "must", "title": "Manual idea"}


@pytest.fixture(autouse=True)
def empty(pg, clean):                                                                                  # noqa: F811
    for t in ("creative_whiteboard_events", "creative_whiteboard_picks"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()
    yield
    for t in ("creative_whiteboard_events", "creative_whiteboard_picks"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


def put(client, picks=(), dropped=(), week=MONDAY, **kw):
    r = client.put(URL, json={"week": week, "picks": list(picks), "dropped": list(dropped)}, **kw)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def events(pg):
    return [dict(zip(("kind", "pick_id", "batch"), r)) for r in pg.execute("SELECT kind, pick_id, batch FROM creative_whiteboard_events ORDER BY id").fetchall()]


def test_empty_week_and_pick_dedupe_order(client):
    d = client.get(URL + "?week=" + MONDAY).get_json()
    assert d == {"week": MONDAY, "version": 0, "picks": [], "dropped": []}
    out = put(client, [B, A, C])                                                                        # lane order: must (A, C by rank), then if_time (B)
    assert [p["concept_key"] for p in out["picks"]] == ["line:1", "manual:3", "line:2"]
    assert [p["rank"] for p in out["picks"]] == [2.0, 3.0, 1.0] and out["picks"][0]["line_code"] == "VB01" and out["picks"][0]["in_production"] is False
    assert out["version"] > 0 and client.get(URL + "?week=" + MONDAY).get_json() == out
    again = put(client, [B, A, C])
    assert len(again["picks"]) == 3
    assert client.put(URL, json={"week": MONDAY, "picks": [A, A]}).status_code == 400                   # twice in one save


def test_monday_snap_and_other_weeks_are_separate(client):
    put(client, [A], week="2026-11-11")
    assert client.get(URL + "?week=2026-11-13").get_json()["picks"][0]["concept_key"] == "line:1"
    assert client.get(URL + "?week=" + NEXT_MONDAY).get_json()["picks"] == []
    assert client.get(URL + "?week=nope").status_code == 400


def test_reorder_lane_move_and_note_write_events(client, pg):
    put(client, [A, B])
    v1 = client.get(URL + "?week=" + MONDAY).get_json()["version"]
    out = put(client, [{**B, "lane": "must"}, {**A, "note": "after the call"}])
    assert [p["concept_key"] for p in out["picks"]] == ["line:2", "line:1"] and out["picks"][0]["lane"] == "must" and out["picks"][1]["note"] == "after the call"
    assert out["picks"][0]["rev"] == 2 and out["version"] > v1
    kinds = [e["kind"] for e in events(pg)]
    assert kinds == ["pick", "pick", "move", "move"]
    assert len({e["batch"] for e in events(pg)[2:]}) == 1 and events(pg)[2]["batch"] != events(pg)[0]["batch"]
    ev = pg.execute("SELECT before, after FROM creative_whiteboard_events WHERE kind = 'move' ORDER BY id LIMIT 1").fetchone()
    assert ev[0]["lane"] == "if_time" and ev[1]["lane"] == "must"
    out = put(client, [{**B, "lane": "must"}, {**A, "note": "changed again"}])                          # same lane and order, only the note
    assert [e["kind"] for e in events(pg)][-1] == "note" and out["picks"][1]["note"] == "changed again"
    keep = put(client, [{**B, "lane": "must"}, {k: v for k, v in A.items()}])                           # note omitted keeps the stored note
    assert keep["picks"][1]["note"] == "changed again"


def test_unmentioned_concepts_are_untouched_and_tags_only_fill_blanks(client):
    put(client, [A, B])
    out = put(client, [{**A, "cluster": "Other cluster", "title": "New title"}])
    assert {p["concept_key"] for p in out["picks"]} == {"line:1", "line:2"}
    a = next(p for p in out["picks"] if p["concept_key"] == "line:1")
    assert a["cluster"] == "Energy" and a["title"] == "Vita Bull - Low T"
    b = next(p for p in out["picks"] if p["concept_key"] == "line:2")
    assert b["lane"] == "if_time"
    out = put(client, [{**A, "lane": "parked"}, {**B, "cluster": "Joint"}])                              # blank cluster on B gets filled on a real change only
    assert next(p for p in out["picks"] if p["concept_key"] == "line:1")["lane"] == "parked"


def test_drop_then_repick_is_a_status_flip(client, pg):
    put(client, [A, B])
    out = put(client, [B], [A])
    assert [p["concept_key"] for p in out["picks"]] == ["line:2"] and [p["concept_key"] for p in out["dropped"]] == ["line:1"]
    assert pg.execute("SELECT count(*) AS n FROM creative_whiteboard_picks").fetchone()[0] == 2          # never deleted
    c = put(client, [B], [A, C])["dropped"]
    assert {p["concept_key"] for p in c} == {"line:1", "manual:3"} and c[0]["concept_key"] == "manual:3"   # newest first
    out = put(client, [A, B])
    assert [p["concept_key"] for p in out["picks"]] == ["line:1", "line:2"]
    assert [p["concept_key"] for p in out["dropped"]] == ["manual:3"]
    kinds = [e["kind"] for e in events(pg)]
    assert kinds == ["pick", "pick", "move", "drop", "drop", "restore", "move"]                         # B moved up when A left, A dropped, C dropped, A restored, B moved back
    assert [e["kind"] for e in events(pg)].count("drop") == 2


def test_identical_save_writes_nothing_and_keeps_the_version(client, pg):
    first = put(client, [A, B], [C])
    n = len(events(pg))
    second = put(client, [A, B], [C])
    assert len(events(pg)) == n and second["version"] == first["version"] and second["picks"] == first["picks"]
    assert pg.execute("SELECT max(rev) AS r FROM creative_whiteboard_picks").fetchone()[0] == 1


def test_etag_304_and_changes_after_a_write(client):
    out = put(client, [A])
    r = client.get(URL + "?week=" + MONDAY)
    tag = r.headers["ETag"]
    assert tag == f'W/"wb-{MONDAY}-{out["version"]}"'
    same = client.get(URL + "?week=" + MONDAY, headers={"If-None-Match": tag})
    assert same.status_code == 304 and same.get_data() == b"" and same.headers["ETag"] == tag
    put(client, [A, B])
    new = client.get(URL + "?week=" + MONDAY, headers={"If-None-Match": tag})
    assert new.status_code == 200 and new.headers["ETag"] != tag
    assert client.put(URL, json={"week": MONDAY, "picks": [A, B]}).headers["ETag"] == new.headers["ETag"]


def test_in_production_flag_reads_open_assignments(client, pg):
    put(client, [{**A, "concept_key": "line:77"}])
    pg.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week) VALUES ('line:77','text:x','vita-bull','Madre',%s)""", (MONDAY,))
    pg.commit()
    assert client.get(URL + "?week=" + MONDAY).get_json()["picks"][0]["in_production"] is True


@pytest.mark.parametrize("body", [
    {"picks": [{"concept_key": "k", "lane": "nope"}]}, {"picks": [{"concept_key": "k"}]}, {"picks": [{"concept_key": "  ", "lane": "must"}]},
    {"picks": [{"concept_key": "k" * 401, "lane": "must"}]}, {"picks": [{"concept_key": "k", "lane": "must", "note": "x" * 501}]}, {"picks": ["x"]},
    {"picks": [{"concept_key": f"k{i}", "lane": "must"} for i in range(301)]}, {"picks": "x"}, {"picks": [{"concept_key": "k", "lane": "must", "product": "nope"}]},
    {"picks": [{"concept_key": "k", "lane": "must"}], "dropped": [{"concept_key": "k"}]}, {"week": "tomorrow", "picks": []},
])
def test_bad_input_is_a_400(client, body):
    assert client.put(URL, json=body).status_code == 400


def test_non_json_body_is_a_400(client):
    assert client.put(URL, data="x", content_type="text/plain").status_code == 400
    assert client.put(URL, json={"picks": [A]}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_signed_out_and_partner_keys_are_refused(anon, key, mint, pg):
    assert anon.get(URL).status_code in (401, 403)
    assert anon.put(URL, json={"picks": [A]}).status_code in (401, 403)
    from etl import api_keys as cli
    from .test_api_keys import bearer
    partner, _ = cli.create_key(pg, "pk-wb", "pytest", "partner_push", "Madre")
    assert anon.put(URL, json={"picks": [A]}, headers=bearer(partner)).status_code == 403
    assert anon.get(URL, headers=bearer(partner)).status_code == 403
    assert anon.get(URL, headers=bearer(key)).status_code == 200                                         # a team key reads, but writes are for signed-in people only
    assert anon.put(URL, json={"week": MONDAY, "picks": [A]}, headers=bearer(key)).status_code == 403


def test_rate_limit(client, app):
    import app.api as api_mod
    api_mod._wb_limiter = None
    app.config["PRIORITIES_WB_PER_MIN"] = 3
    try:
        codes = [client.put(URL, json={"week": MONDAY, "picks": [A]}).status_code for _ in range(5)]
    finally:
        app.config["PRIORITIES_WB_PER_MIN"] = 0
        api_mod._wb_limiter = None
    assert codes[:2] == [200, 200] and 429 in codes


def test_removed_deletes_a_card_back_to_the_backlog(client, pg):
    put(client, [A], [B])
    r = client.put(URL, json={"week": MONDAY, "picks": [], "dropped": [], "removed": ["line:1", "line:2"]})
    assert r.status_code == 200 and r.get_json()["picks"] == [] and r.get_json()["dropped"] == []
    assert [e["kind"] for e in events(pg)].count("remove") == 2
    assert client.put(URL, json={"week": MONDAY, "removed": "x"}).status_code == 400
