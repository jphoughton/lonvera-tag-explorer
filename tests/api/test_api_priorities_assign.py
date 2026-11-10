"""Production assignments on /priorities: /api/priorities/assignments, /vendor-links and the no-login vendor link routes (app/priorities_assign.py). Keys, duplicates, Monday weeks, links,
the stage (own facts and the tracker override), vendor isolation, the write guards and an untouched snapshot."""
import datetime as dt
import json
import re

import pytest

from app import api as api_mod
from app import priorities_assign as pa

from .test_api_priorities import ROW, _key

LINE_ROW = ROW                                                                                    # line_id 1, vita-bull, Energy / Given Up Guy / Low T
DRAFT = {**ROW, "line_id": None, "line_code": "", "cluster": "Hair Thinning", "icp": "Denise", "pain_point": "Shedding", "section": "new", "story": ""}
EMPTY = {**ROW, "line_id": None, "line_code": "", "cluster": "", "icp": "X", "pain_point": "", "section": "other"}
MONDAY = "2026-11-09"
NEXT_MONDAY = "2026-11-16"


def seed_snapshot(pg, rows_by_week):
    pg.execute("DELETE FROM creative_priorities_snapshots")
    for wk, rows in rows_by_week.items():
        pg.execute("INSERT INTO creative_priorities_snapshots (week_start, data_through, basis, thresholds, baselines, rows, stats) VALUES (%s,%s,'meta',%s,%s,%s,%s)",
                   (wk, wk, json.dumps({}), json.dumps({}), json.dumps(rows), json.dumps({})))
    pg.commit()


@pytest.fixture()
def snaps(pg):
    seed_snapshot(pg, {"2026-11-01": [{**LINE_ROW, "section": "iterate"}, DRAFT, EMPTY], "2026-11-08": [{**LINE_ROW, "section": "faded"}, DRAFT, EMPTY]})
    yield
    pg.execute("DELETE FROM creative_priorities_snapshots")
    pg.commit()


@pytest.fixture(autouse=True)
def clean(pg, app):
    for t in ("creative_priorities_assignments", "creative_priorities_vendor_links", "creative_priorities_manual"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()
    api_mod._assign_limiter = None
    api_mod._vendor_limiter = None
    yield
    for t in ("creative_priorities_assignments", "creative_priorities_vendor_links", "creative_priorities_manual"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


@pytest.fixture()
def tracker(pg):
    """Two extra tracker rows (concept ZZ-A1 'Brief link' set, ZZ-A2 asset url set) with ad_metrics, removed afterwards."""
    pid = pg.execute("SELECT id FROM products WHERE slug = 'vita-bull'").fetchone()[0]
    rows = [(9101, "ZZ-A1", "ZZ-Fam-1", "5 - Briefed", "https://docs.example/brief-1", None, dt.date(2026, 11, 2), 120.00, 180.00, 4),
            (9102, "ZZ-A2", "ZZ-Fam-2", "7 - Approved - Ready To Launch", None, "https://drive.example/asset-2", None, 0, 0, 0)]
    for rid, cid, fam, st, brief, asset, launch, sp, rev, pur in rows:
        pg.execute("INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, launch_date, asset_url, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (rid, pid, cid + "#1", cid, fam, fam.lower(), 99000 + rid, st, launch, asset, json.dumps({"Brief Link": brief} if brief else {})))
        pg.execute("INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, launch_date, spend_launch, purchases_launch, revenue_launch) VALUES (%s,%s,%s,true,%s,%s,%s,%s)",
                   (rid, pid, fam.lower(), launch, sp, pur, rev))
    pg.commit()
    yield
    pg.execute("DELETE FROM tracker_rows WHERE id IN (9101, 9102)")
    pg.commit()


def assign(client, keys, vendor="Madre", week=MONDAY, note="", **kw):
    return client.post("/api/priorities/assignments", json={"keys": keys, "snapshot_week": "2026-11-08", "vendor": vendor, "for_week": week, "note": note, **kw})


def one(client, vendor="Madre", week=MONDAY, key=None):
    r = assign(client, [key or _key({**LINE_ROW, "section": "faded"})], vendor, week)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["created"][0]


def link(client, vendor="Madre"):
    r = client.post("/api/priorities/vendor-links", json={"vendor": vendor})
    assert r.status_code in (200, 201)
    return r.get_json()


# ---------------------------------------------------------------------------------------------------------------- keys
def test_ckey_on_every_row_and_stable_across_sections(client, snaps):
    a = client.get("/api/priorities?week=2026-11-01").get_json()["rows"]
    b = client.get("/api/priorities?week=2026-11-08").get_json()["rows"]
    assert [r["ckey"] for r in a] == ["line:1", "text:vita-bull|hair thinning|denise|shedding", "text:vita-bull||x|"]
    assert [r["ckey"] for r in b] == [r["ckey"] for r in a]
    assert a[0]["section"] == "make_more" and b[0]["section"] == "revive"          # the section moved (stored as iterate / faded, read as the new Action), the concept key did not


def test_ckey_is_added_to_rows_and_nothing_else_changes(client, snaps, pg):
    d = client.get("/api/priorities?week=2026-11-08").get_json()
    stored = pg.execute("SELECT rows FROM creative_priorities_snapshots WHERE week_start = '2026-11-08'").fetchone()[0]
    from etl import creative_priorities as cpm
    assert [{k: v for k, v in r.items() if k != "ckey"} for r in d["rows"]] == cpm.norm_snapshot({"rows": stored})["rows"]       # only the old->new vocabulary read map; storage itself is untouched
    assert stored[0]["section"] == "faded" and d["rows"][0]["section"] == "revive"


def test_saved_view_rows_carry_ckey(client, snaps, pg):
    pg.execute("DELETE FROM creative_priorities_views")
    pg.commit()
    try:
        v = client.post("/api/priorities/views", json={"week": "2026-11-08", "ids": [_key({**LINE_ROW, "section": "faded"})]}).get_json()
        assert client.get("/api/priorities/views/" + v["token"]).get_json()["rows"][0]["ckey"] == "line:1"
    finally:
        pg.execute("DELETE FROM creative_priorities_views")
        pg.commit()


def test_a_draft_proposal_that_becomes_a_line_still_matches_by_text_key(client, snaps, pg):
    resp = assign(client, [_key(DRAFT)]).get_json()
    assert resp["created"], resp
    r = resp["created"][0]
    assert r["concept_key"] == "text:vita-bull|hair thinning|denise|shedding" and r["line_id"] is None
    become_line = {**DRAFT, "line_id": 77, "line_code": "VB77", "section": "iterate"}
    seed_snapshot(pg, {"2026-11-15": [become_line]})
    new = client.get("/api/priorities?week=2026-11-15").get_json()["rows"][0]
    assert new["ckey"] == "line:77" and pa.text_key(new) == r["text_key"]


# ---------------------------------------------------------------------------------------------------------------- creating
def test_assign_creates_rows_from_the_server_snapshot_and_skips_duplicates(client, snaps):
    keys = [_key({**LINE_ROW, "section": "faded"}), _key(DRAFT)]
    r = assign(client, keys, "Madre", "2026-11-11", note="  brief   sent ")                     # a Wednesday: snaps to Monday the 9th
    assert r.status_code == 201
    d = r.get_json()
    assert [c["concept_key"] for c in d["created"]] == ["line:1", "text:vita-bull|hair thinning|denise|shedding"] and d["skipped"] == []
    c = d["created"][0]
    assert c["for_week"] == MONDAY and c["vendor"] == "Madre" and c["note"] == "brief sent" and c["stage"] == "assigned" and c["stage_label"] == "Assigned"
    assert (c["product"], c["cluster"], c["icp"], c["pain_point"], c["line_id"], c["line_code"], c["section_at_assign"]) == ("vita-bull", "Energy", "Given Up Guy", "Low T", 1, "L01", "revive")
    assert c["story"] == ROW["story"] and c["created_by"] == "JoshHoughton" and c["updated_by"] == "JoshHoughton"
    assert c["ads"] == [] and c["suggested_concept_ids"] == [] and c["tracker_status"] is None and c["tracker_concept_ids"] == []
    again = assign(client, keys, "Madre", MONDAY).get_json()
    assert again["created"] == [] and [s["key"] for s in again["skipped"]] == keys and "already assigned" in again["skipped"][0]["reason"]
    other = assign(client, [keys[0]], "Victoria", NEXT_MONDAY).get_json()                         # another vendor and week is a second entry
    assert len(other["created"]) == 1
    assert len(client.get("/api/priorities/assignments").get_json()["rows"]) == 3


def test_assign_skips_unknown_and_empty_concepts_and_the_same_key_twice(client, snaps):
    k = _key({**LINE_ROW, "section": "faded"})
    d = assign(client, [k, k, "vita-bull|faded|nope|nope|nope", _key(EMPTY)]).get_json()
    assert len(d["created"]) == 1
    reasons = {s["key"]: s["reason"] for s in d["skipped"]}
    assert "not in this week" in reasons["vita-bull|faded|nope|nope|nope"] and "no cluster or pain point" in reasons[_key(EMPTY)] and k not in reasons


def test_assign_ignores_client_text_for_the_concept(client, snaps):
    r = client.post("/api/priorities/assignments", json={"keys": [_key({**LINE_ROW, "section": "faded"})], "snapshot_week": "2026-11-08", "vendor": "Madre", "for_week": MONDAY,
                                                         "cluster": "Hacked", "pain_point": "Hacked", "product": "boneflex", "story": "Hacked"})
    c = r.get_json()["created"][0]
    assert c["cluster"] == "Energy" and c["product"] == "vita-bull" and c["story"] == ROW["story"]


def test_assign_a_manual_concept(client, snaps):
    m = client.post("/api/priorities/manual", json={"product": "boneflex", "section": "new", "cluster": "Menopause", "icp": "BF3", "pain_point": "Joint Pain"}).get_json()
    c = assign(client, [m["key"]]).get_json()["created"][0]
    assert c["concept_key"] == m["key"] and c["manual_id"] == m["id"] and c["product"] == "boneflex"


def test_assign_validation(client, snaps):
    k = _key({**LINE_ROW, "section": "faded"})
    for body, msg in (({"keys": [k], "vendor": "Other", "for_week": MONDAY}, "vendor must be one of"), ({"keys": [k], "vendor": "Madre"}, "week is required"),
                      ({"keys": [k], "vendor": "Madre", "for_week": "soon"}, "week must be a date"), ({"vendor": "Madre", "for_week": MONDAY}, "keys must be"),
                      ({"keys": [], "vendor": "Madre", "for_week": MONDAY}, "keys must be"), ({"keys": [1], "vendor": "Madre", "for_week": MONDAY}, "keys must be"),
                      ({"keys": [f"k{i}" for i in range(101)], "vendor": "Madre", "for_week": MONDAY}, "at most 100"), ({"keys": [k], "vendor": "Madre", "for_week": MONDAY, "note": "x" * 501}, "note is at most")):
        r = client.post("/api/priorities/assignments", json=body)
        assert r.status_code == 400 and msg in r.get_json()["error"], body
    assert client.post("/api/priorities/assignments", json=[k]).status_code == 400
    assert client.post("/api/priorities/assignments", json={"keys": [k], "snapshot_week": "2025-01-05", "vendor": "Madre", "for_week": MONDAY}).status_code == 404       # no such snapshot
    for v in ("PuredMedia", "CreateMastery", "OpenProgram", "Internal", "10xROAS"):
        assert assign(client, [k], v).status_code == 201


def test_assigning_leaves_the_snapshot_byte_identical(client, snaps, pg):
    q = "SELECT week_start::text, rows::text, stats::text, baselines::text, thresholds::text FROM creative_priorities_snapshots ORDER BY week_start"
    before = pg.execute(q).fetchall()
    d1 = client.get("/api/priorities?week=2026-11-08").get_json()["rows"]
    one(client)
    one(client, "Victoria", NEXT_MONDAY)
    assert pg.execute(q).fetchall() == before
    assert client.get("/api/priorities?week=2026-11-08").get_json()["rows"] == d1                  # the ideation rows do not change either


# ---------------------------------------------------------------------------------------------------------------- listing
def test_list_filters_weeks_and_closed(client, snaps):
    a = one(client, "Madre", MONDAY)
    b = one(client, "Victoria", NEXT_MONDAY)
    d = client.get("/api/priorities/assignments").get_json()
    assert [r["id"] for r in d["rows"]] == [b["id"], a["id"]]                                       # newest week first
    assert d["vendors"] == pa.VENDORS and d["weeks"] == [NEXT_MONDAY, MONDAY]
    assert [r["id"] for r in client.get(f"/api/priorities/assignments?week={MONDAY}").get_json()["rows"]] == [a["id"]]
    assert [r["id"] for r in client.get("/api/priorities/assignments?week=2026-11-12").get_json()["rows"]] == [a["id"]]       # any day of the week
    assert [r["id"] for r in client.get("/api/priorities/assignments?vendor=Victoria").get_json()["rows"]] == [b["id"]]
    assert client.get("/api/priorities/assignments?vendor=Nobody").status_code == 400
    assert client.get("/api/priorities/assignments?week=zzz").status_code == 400
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"dropped": True})
    client.patch(f"/api/priorities/assignments/{b['id']}", json={"launched": True})
    assert client.get("/api/priorities/assignments").get_json()["rows"] == []
    closed = client.get("/api/priorities/assignments?include_closed=1").get_json()
    assert {r["id"]: r["stage"] for r in closed["rows"]} == {a["id"]: "dropped", b["id"]: "launched"} and closed["weeks"] == [NEXT_MONDAY, MONDAY]


def test_listing_before_the_migration_is_empty_not_an_error(client, snaps, pg):
    pg.execute("ALTER TABLE creative_priorities_assignments RENAME TO cpa_tmp")
    pg.commit()
    try:
        listing = client.get("/api/priorities/assignments").get_json()
        assert listing.pop("launch") == {"enabled": False, "can_launch": False, "dry_run": True}          # the discovery flag is there even when nothing is assigned (launch is dark by default)
        assert listing == {"rows": [], "vendors": pa.VENDORS, "weeks": []}
        assert client.get("/api/priorities/vendor-links").get_json()["links"] == []
    finally:
        pg.rollback()
        pg.execute("ALTER TABLE cpa_tmp RENAME TO creative_priorities_assignments")
        pg.commit()


# ---------------------------------------------------------------------------------------------------------------- patch + stages
def patch(client, aid, **body):
    return client.patch(f"/api/priorities/assignments/{aid}", json=body)


def test_patch_links_stamp_times_and_move_the_stage(client, snaps):
    a = one(client)
    r = patch(client, a["id"], brief_url="https://docs.example/brief")
    d = r.get_json()
    assert r.status_code == 200 and d["stage"] == "brief_sent" and d["brief_at"] and d["creative_at"] is None and d["updated_by"] == "JoshHoughton"
    first_at = d["brief_at"]
    d = patch(client, a["id"], brief_url="https://docs.example/brief-2").get_json()
    assert d["brief_at"] == first_at                                                               # first-added time is kept
    d = patch(client, a["id"], creative_url="https://drive.example/c").get_json()
    assert d["stage"] == "creative_in" and d["creative_at"]
    d = patch(client, a["id"], ready=True).get_json()
    assert d["stage"] == "ready" and d["ready_at"] and d["stage_label"] == "Ready to launch"
    assert patch(client, a["id"], ready=False).get_json()["stage"] == "creative_in"
    d = patch(client, a["id"], launched=True).get_json()
    assert d["stage"] == "launched" and d["launched_at"]
    d = patch(client, a["id"], dropped=True).get_json()
    assert d["stage"] == "dropped" and d["dropped_at"]
    assert patch(client, a["id"], dropped=False, launched=False).get_json()["stage"] == "creative_in"
    d = patch(client, a["id"], brief_url="", creative_url=None).get_json()
    assert d["stage"] == "assigned" and d["brief_url"] is None and d["creative_at"] is None and d["brief_at"] is None


def test_patch_other_fields_week_snap_and_validation(client, snaps):
    a = one(client)
    d = patch(client, a["id"], vendor="Victoria", for_week="2026-11-18", note=" new  note ", tracker_concept_ids=["A-1", " A-1 ", "B-2", ""]).get_json()
    assert (d["vendor"], d["for_week"], d["note"], d["tracker_concept_ids"]) == ("Victoria", NEXT_MONDAY, "new note", ["A-1", "B-2"])
    for body in ({"brief_url": "ftp://x"}, {"brief_url": "javascript:alert(1)"}, {"creative_url": "https://x.com/" + "a" * 2000}, {"ready": "yes"}, {"dropped": 1}, {"vendor": "Other"},
                 {"for_week": ""}, {"tracker_concept_ids": "A-1"}, {"id": 5}, {"vendor_note": "x"}, {"created_by": "me"}, {"concept_key": "line:2"}):
        assert patch(client, a["id"], **body).status_code == 400, body
    assert patch(client, a["id"]).status_code == 400
    assert patch(client, 999999, note="x").status_code == 404
    assert client.patch(f"/api/priorities/assignments/{a['id']}", data="[]", content_type="application/json").status_code == 400
    assert client.delete(f"/api/priorities/assignments/{a['id']}").status_code == 405              # no hard delete


def test_patch_into_an_existing_open_row_is_a_409_and_a_dropped_row_can_be_assigned_again(client, snaps):
    a = one(client, "Madre", MONDAY)
    b = one(client, "Victoria", MONDAY)
    assert patch(client, b["id"], vendor="Madre").status_code == 409
    assert patch(client, a["id"], dropped=True).status_code == 200
    again = assign(client, [_key({**LINE_ROW, "section": "faded"})], "Madre", MONDAY).get_json()
    assert len(again["created"]) == 1 and again["skipped"] == []
    assert patch(client, a["id"], dropped=False).status_code == 409                                # un-dropping would clash with the new open row


def test_stage_comes_from_the_tracker_when_it_is_further_along(client, snaps, tracker):
    a = one(client)
    d = patch(client, a["id"], tracker_concept_ids=["ZZ-A1"]).get_json()
    assert d["stage"] == "brief_sent" and d["tracker_status"] == "5 - Briefed"                     # tracker status 5 alone
    ad = d["ads"][0]
    assert (ad["concept_id"], ad["ad_family"], ad["status"], ad["launch_date"], ad["spend"], ad["roas"], ad["ad_url"]) == ("ZZ-A1", "ZZ-Fam-1", "5 - Briefed", "2026-11-02", 120.0, 1.5, "/ad/9101")
    d = patch(client, a["id"], tracker_concept_ids=["ZZ-A1", "ZZ-A2"]).get_json()
    assert d["stage"] == "ready" and d["tracker_status"].startswith("7") and {x["concept_id"] for x in d["ads"]} == {"ZZ-A1", "ZZ-A2"}


@pytest.mark.parametrize("status,want", [("3 - Brief Drafted", "brief_sent"), ("4 - Brief Sent", "brief_sent"), ("6 - Creative In", "creative_in"), ("7 - Approved - Ready To Launch", "ready"),
                                         ("8 - Launched", "launched"), ("9 - Relaunched", "launched"), ("2 - Idea", "assigned")])
def test_every_tracker_status_override(client, snaps, tracker, pg, status, want):
    pg.execute("UPDATE tracker_rows SET status = %s WHERE id = 9101", (status,))
    pg.commit()
    a = one(client)
    assert patch(client, a["id"], tracker_concept_ids=["ZZ-A1"]).get_json()["stage"] == want
    assert patch(client, a["id"], dropped=True).get_json()["stage"] == "dropped"                    # dropped beats everything


def test_own_facts_stage_derivation_and_the_tracker_never_pulls_it_back(client, snaps, tracker, pg):
    a = one(client)
    patch(client, a["id"], creative_url="https://drive.example/c", ready=True)
    pg.execute("UPDATE tracker_rows SET status = '3 - Brief Drafted' WHERE id = 9101")
    pg.commit()
    assert patch(client, a["id"], tracker_concept_ids=["ZZ-A1"]).get_json()["stage"] == "ready"      # tracker behind: own facts win


def test_ads_match_by_brief_link_and_creative_link_and_are_suggested(client, snaps, tracker):
    a = one(client)
    d = patch(client, a["id"], brief_url="https://docs.example/brief-1").get_json()                 # tracker raw 'Brief Link' equals it
    assert [x["concept_id"] for x in d["ads"]] == ["ZZ-A1"] and d["suggested_concept_ids"] == ["ZZ-A1"] and d["tracker_concept_ids"] == []
    d = patch(client, a["id"], creative_url="https://drive.example/asset-2").get_json()             # tracker asset_url equals it
    assert [x["concept_id"] for x in d["ads"]] == ["ZZ-A1", "ZZ-A2"] and d["suggested_concept_ids"] == ["ZZ-A1", "ZZ-A2"] and d["stage"] == "ready"
    d = patch(client, a["id"], tracker_concept_ids=["ZZ-A1"]).get_json()                           # typed in: no longer only a suggestion
    assert d["suggested_concept_ids"] == ["ZZ-A2"] and len(d["ads"]) == 2
    d = patch(client, a["id"], brief_url="https://docs.example/other", creative_url="https://drive.example/other", tracker_concept_ids=[]).get_json()
    assert d["ads"] == [] and d["suggested_concept_ids"] == []
    assert patch(client, a["id"], tracker_concept_ids=["NOPE-1"]).get_json()["ads"] == []           # an unknown Concept ID links nothing


# ---------------------------------------------------------------------------------------------------------------- vendor links
def test_vendor_links_get_create_reset(client, snaps):
    assert client.get("/api/priorities/vendor-links").get_json() == {"links": [], "vendors": pa.VENDORS}
    r = client.post("/api/priorities/vendor-links", json={"vendor": "Madre"})
    assert r.status_code == 201
    d = r.get_json()
    assert re.fullmatch(r"[A-Za-z0-9_-]{22,64}", d["token"]) and d["url"] == "/priorities/vendor/" + d["token"] and d["vendor"] == "Madre" and d["rotated_at"] is None
    r2 = client.post("/api/priorities/vendor-links", json={"vendor": "Madre"})
    assert r2.status_code == 200 and r2.get_json()["token"] == d["token"]                          # permanent: the same link every time
    assert client.post("/api/priorities/vendor-links", json={"vendor": "Nobody"}).status_code == 400
    assert client.post("/api/priorities/vendor-links", json={}).status_code == 400
    link(client, "Victoria")
    assert [x["vendor"] for x in client.get("/api/priorities/vendor-links").get_json()["links"]] == ["Madre", "Victoria"]
    rr = client.post("/api/priorities/vendor-links/Madre/reset")
    assert rr.status_code == 200 and rr.get_json()["token"] != d["token"] and rr.get_json()["rotated_at"]
    assert client.get("/api/priorities/vendor-links").get_json()["links"][0]["token"] == rr.get_json()["token"]
    assert client.post("/api/priorities/vendor-links/Nobody/reset").status_code == 400
    anon = client.application.test_client()
    assert anon.get(f"/api/priorities/vendor/{d['token']}").status_code == 404                     # the old link stopped working
    assert anon.get(f"/api/priorities/vendor/{rr.get_json()['token']}").status_code == 200


# ---------------------------------------------------------------------------------------------------------------- the vendor link page
@pytest.fixture()
def anon(app):
    return app.test_client()


def test_vendor_sees_only_their_rows_with_spend_and_never_the_internal_note(client, anon, snaps, tracker):
    mine = assign(client, [_key({**LINE_ROW, "section": "faded"})], "Madre", MONDAY, note="INTERNAL ONLY secret").get_json()["created"][0]
    theirs = assign(client, [_key(DRAFT)], "Victoria", MONDAY, note="other vendor note").get_json()["created"][0]
    patch(client, mine["id"], tracker_concept_ids=["ZZ-A1"], brief_url="https://docs.example/brief")
    patch(client, theirs["id"], tracker_concept_ids=["ZZ-A2"])
    tm, tv = link(client, "Madre")["token"], link(client, "Victoria")["token"]
    r = anon.get(f"/api/priorities/vendor/{tm}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["vendor"] == "Madre" and [x["id"] for x in d["rows"]] == [mine["id"]] and d["weeks"] == [MONDAY]
    row = d["rows"][0]
    assert row["ads"][0]["spend"] == 120.0 and row["ads"][0]["roas"] == 1.5 and row["ads"][0]["concept_id"] == "ZZ-A1" and row["ads"][0]["launch_date"] == "2026-11-02"
    assert "ad_url" not in row["ads"][0] and row["brief_url"] == "https://docs.example/brief" and row["stage"] == "brief_sent" and row["story"] == ROW["story"]
    for k in ("note", "created_by", "updated_by", "suggested_concept_ids"):
        assert k not in row, k
    text = r.get_data(as_text=True)
    for leak in ("INTERNAL ONLY", "other vendor note", "Victoria", "JoshHoughton", "Denise", "Shedding"):
        assert leak not in text, leak
    assert [x["id"] for x in anon.get(f"/api/priorities/vendor/{tv}").get_json()["rows"]] == [theirs["id"]]


def test_vendor_list_hides_dropped_and_old_launched_but_keeps_recent_launched(client, anon, snaps, pg):
    keys = [_key({**LINE_ROW, "section": "faded"}), _key(DRAFT)]
    a, b = assign(client, keys, "Madre", MONDAY).get_json()["created"]
    tok = link(client)["token"]
    patch(client, a["id"], dropped=True)
    assert [x["id"] for x in anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"]] == [b["id"]]
    patch(client, b["id"], launched=True)
    assert [x["id"] for x in anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"]] == [b["id"]]            # launched just now
    pg.execute("UPDATE creative_priorities_assignments SET launched_at = now() - interval '40 days' WHERE id = %s", (b["id"],))
    pg.commit()
    assert anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"] == []


def test_vendor_can_add_a_creative_link_a_note_and_mark_ready(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    r = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"creative_url": "https://drive.example/done", "vendor_note": "  two  cuts \n"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["creative_url"] == "https://drive.example/done" and d["vendor_note"] == "two cuts" and d["stage"] == "creative_in" and "note" not in d and "updated_by" not in d
    d = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True}).get_json()
    assert d["stage"] == "ready" and d["ready_at"]
    d = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": False}).get_json()
    assert d["stage"] == "creative_in" and d["ready_at"] is None
    internal = client.get("/api/priorities/assignments").get_json()["rows"][0]
    assert internal["updated_by"] == "vendor:Madre" and internal["vendor_note"] == "two cuts"          # the author of a vendor write


def test_vendor_ready_needs_a_creative_link(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    r = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True})
    assert r.status_code == 400 and "creative link" in r.get_json()["error"]
    assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True, "creative_url": "https://drive.example/x"}).get_json()["stage"] == "ready"
    r = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"creative_url": ""})                 # clearing the link keeps the earlier ready stamp until it is undone
    assert r.status_code == 200


def test_vendor_patch_is_limited_to_its_own_fields_and_rows(client, anon, snaps):
    mine = one(client, "Madre")
    theirs = one(client, "Victoria", key=_key(DRAFT))
    tm = link(client, "Madre")["token"]
    link(client, "Victoria")
    for body in ({"brief_url": "https://x.com/a"}, {"note": "x"}, {"vendor": "Victoria"}, {"for_week": MONDAY}, {"launched": True}, {"dropped": True}, {"tracker_concept_ids": ["A"]}, {"stage": "launched"}):
        assert anon.patch(f"/api/priorities/vendor/{tm}/{mine['id']}", json=body).status_code == 400, body
    assert anon.patch(f"/api/priorities/vendor/{tm}/{mine['id']}", json={}).status_code == 400
    assert anon.patch(f"/api/priorities/vendor/{tm}/{mine['id']}", json={"creative_url": "javascript:x"}).status_code == 400
    assert anon.patch(f"/api/priorities/vendor/{tm}/{mine['id']}", json={"ready": "true"}).status_code == 400
    r = anon.patch(f"/api/priorities/vendor/{tm}/{theirs['id']}", json={"vendor_note": "hijack", "creative_url": "https://x.com/h"})       # another vendor's row
    assert r.status_code == 404
    assert anon.patch(f"/api/priorities/vendor/{tm}/999999", json={"vendor_note": "x"}).status_code == 404
    still = [x for x in client.get("/api/priorities/assignments").get_json()["rows"] if x["id"] == theirs["id"]][0]
    assert still["vendor_note"] == "" and still["creative_url"] is None
    client.patch(f"/api/priorities/assignments/{mine['id']}", json={"dropped": True})
    assert anon.patch(f"/api/priorities/vendor/{tm}/{mine['id']}", json={"vendor_note": "late"}).status_code == 409


def test_bad_tokens_are_404_and_nothing_else_is_public(client, anon, snaps):
    assert anon.get("/api/priorities/vendor/short").status_code == 401                           # not a token shape: not exempt, so the login guard answers
    assert anon.get("/api/priorities/vendor/" + "A" * 30 + "/extra").status_code == 401
    assert anon.get("/api/priorities").status_code == 401
    assert anon.get("/api/priorities/vendor/" + "A" * 30).status_code == 404
    assert anon.patch(f"/api/priorities/vendor/{'A' * 30}/1", json={"vendor_note": "x"}).status_code == 404
    for method, url in (("get", "/api/priorities/assignments"), ("post", "/api/priorities/assignments"), ("patch", "/api/priorities/assignments/1"), ("get", "/api/priorities/vendor-links"),
                        ("post", "/api/priorities/vendor-links"), ("post", "/api/priorities/vendor-links/Madre/reset")):
        r = getattr(anon, method)(url, json={}) if method != "get" else anon.get(url)
        assert r.status_code == 401, (method, url)


# ---------------------------------------------------------------------------------------------------------------- guards
def test_every_write_route_checks_origin(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    bad = {"Origin": "http://evil.example"}
    k = _key({**LINE_ROW, "section": "faded"})
    assert client.post("/api/priorities/assignments", json={"keys": [k], "vendor": "Madre", "for_week": NEXT_MONDAY}, headers=bad).status_code == 403
    assert client.patch(f"/api/priorities/assignments/{a['id']}", json={"note": "x"}, headers=bad).status_code == 403
    assert client.post("/api/priorities/vendor-links", json={"vendor": "Madre"}, headers=bad).status_code == 403
    assert client.post("/api/priorities/vendor-links/Madre/reset", headers=bad).status_code == 403
    assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"vendor_note": "x"}, headers=bad).status_code == 403
    assert client.get("/api/priorities/vendor-links").get_json()["links"][0]["token"] == tok       # nothing rotated by the refused reset


def test_write_rate_limits(client, anon, app, snaps):
    a = one(client)
    tok = link(client)["token"]
    old = (app.config.get("PRIORITIES_ASSIGN_PER_MIN"), app.config.get("PRIORITIES_VENDOR_PER_MIN"))
    try:
        app.config["PRIORITIES_ASSIGN_PER_MIN"] = 3
        app.config["PRIORITIES_VENDOR_PER_MIN"] = 2
        api_mod._assign_limiter = None
        api_mod._vendor_limiter = None
        codes = [client.patch(f"/api/priorities/assignments/{a['id']}", json={"note": f"n{i}"}).status_code for i in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
        vc = [anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"vendor_note": f"v{i}"}) for i in range(3)]
        assert [r.status_code for r in vc] == [200, 200, 429] and vc[2].headers["Retry-After"]
    finally:
        app.config["PRIORITIES_ASSIGN_PER_MIN"], app.config["PRIORITIES_VENDOR_PER_MIN"] = old
        api_mod._assign_limiter = None
        api_mod._vendor_limiter = None


def test_assign_accepts_an_old_section_key_and_stores_the_new_action(client, snaps, pg):
    r = assign(client, [_key({**LINE_ROW, "section": "faded"})], "Madre", MONDAY)          # product|faded|...: a key from before the redesign
    assert r.status_code == 201 and r.get_json()["skipped"] == []
    assert pg.execute("SELECT section_at_assign FROM creative_priorities_assignments").fetchone()[0] == "revive"
    pg.execute("UPDATE creative_priorities_assignments SET section_at_assign = 'proposal'")
    pg.commit()
    got = client.get("/api/priorities/assignments").get_json()
    assert [a["section_at_assign"] for a in got["rows"]] == ["test"]                 # an old stored value shows as the new Action (add folded into test)


def test_a_new_idea_row_still_assigns_under_its_old_add_key_and_stores_test(client, pg):
    idea = {**ROW, "section": "add", "source": "Our draft", "line_id": None, "line_code": "DRAFT", "cluster": "Chest Fat", "icp": "Chest Guy", "pain_point": "Belly", "story": ""}
    seed_snapshot(pg, {"2026-11-08": [LINE_ROW, idea]})
    r = assign(client, [_key(idea)], "Madre", MONDAY)                                       # product|add|...: the key a page or bookmark built before the change
    assert r.status_code == 201 and r.get_json()["skipped"] == []
    row = pg.execute("SELECT section_at_assign, vendor, icp FROM creative_priorities_assignments").fetchone()
    assert tuple(row) == ("test", "Madre", "Chest Guy")
    again = assign(client, [_key({**idea, "section": "test"})], "Madre", MONDAY).get_json()      # the same concept under its new key is the same concept (no duplicate)
    assert not again["created"]
