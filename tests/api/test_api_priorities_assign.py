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
    assert [{k: v for k, v in r.items() if k not in ("ckey", "sheet_pos")} for r in d["rows"]] == cpm.norm_snapshot({"rows": stored})["rows"]       # only the old->new vocabulary read map; storage itself is untouched
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
    for v in ("PuredMedia", "CreateMastery", "OpenProgram", "Studio Flow", "10xROAS"):
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
        assert listing == {"rows": [], "vendors": pa.VENDORS, "weeks": [], "formats": pa.FORMATS}
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
    assert d["creative_url"] == "https://drive.example/done" and d["vendor_note"] == "two cuts" and d["status"] == "assigned" and "note" not in d and "updated_by" not in d      # a link alone does not submit it
    d = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True}).get_json()             # the old Ready flag now means Submit for review
    assert d["status"] == "in_review" and d["stage"] == "creative_in" and d["ready_at"] is None
    d = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": False}).get_json()            # and Not ready means Pull back
    assert d["status"] == "in_production" and d["ready_at"] is None
    internal = client.get("/api/priorities/assignments").get_json()["rows"][0]
    assert internal["updated_by"] == "vendor:Madre" and internal["vendor_note"] == "two cuts"          # the author of a vendor write


def test_vendor_ready_needs_a_creative_link(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    r = anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True})
    assert r.status_code == 400 and "creative link" in r.get_json()["error"]
    assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True, "creative_url": "https://drive.example/x"}).get_json()["status"] == "in_review"
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


# ---------------------------------------------------------------------------------------------------------------- detail panel data (Production page and vendor page)
@pytest.fixture()
def tagged(pg):
    """A taxonomy line (id returned) with 12 tagged ads of spend 1200 down to 100 (ids 9201-9212): ad 5 has the best return among ads over $100, ad 12 a higher one on exactly $100 (counts), ad 11 an even higher one under $100 (does not)."""
    pid = pg.execute("SELECT id FROM products WHERE slug = 'vita-bull'").fetchone()[0]
    lid = pg.execute("INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, sort_order) VALUES (%s,'ZZ99','Energy','Given Up Guy','Low T',999) RETURNING id", (pid,)).fetchone()[0]
    for i in range(1, 13):
        rid = 9200 + i
        spend = 1300 - i * 100 if i < 11 else (99.0 if i == 11 else 100.0)
        roas = {5: 3.0, 11: 9.0, 12: 2.9}.get(i, 1.0 + i / 100)
        pg.execute("INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,'8 - Launched','{}')",
                   (rid, pid, f"ZZ-D{i}#1", f"ZZ-D{i}", f"ZZ-DFam-{i}", f"zz-dfam-{i}", 99500 + i))
        pg.execute("INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, spend_launch, purchases_launch, revenue_launch) VALUES (%s,%s,%s,true,%s,3,%s)", (rid, pid, f"zz-dfam-{i}", spend, spend * roas))
        pg.execute("INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id) VALUES (%s,'linemap','v-test',true,%s)", (rid, lid))
    pg.commit()
    yield lid
    pg.execute("DELETE FROM tracker_rows WHERE id BETWEEN 9201 AND 9212")
    pg.execute("DELETE FROM taxonomy_lines WHERE id = %s", (lid,))
    pg.commit()


def line_snapshot(pg, lid, extra=None):
    seed_snapshot(pg, {"2026-11-08": [{**LINE_ROW, "line_id": lid, "section": "faded", **(extra or {})}, DRAFT]})
    return _key({**LINE_ROW, "line_id": lid, "section": "faded"})


def detail(client, aid):
    return client.get(f"/api/priorities/assignments/{aid}/detail")


def test_detail_of_a_line_concept_has_the_priorities_row_and_ten_examples_by_spend(client, pg, tagged):
    key = line_snapshot(pg, tagged)
    a = assign(client, [key]).get_json()["created"][0]
    r = detail(client, a["id"])
    assert r.status_code == 200
    d = r.get_json()
    assert d["current"] is True and d["snapshot_week"] == "2026-11-08" and d["message"] == "" and d["assignment_id"] == a["id"]
    assert d["row"]["cluster"] == "Energy" and d["row"]["story"] == LINE_ROW["story"] and d["row"]["hooks"][0]["ad_id"] == 1000 and d["row"]["line_id"] == tagged     # the same row /priorities shows, links intact
    assert d["baselines"] == {"vita-bull": {}} and "actions" in d["definitions"]
    ex = d["examples"]
    assert len(ex) == 10 and d["examples_max"] == 10                                  # 12 tagged ads, the top 10
    assert [e["spend"] for e in ex] == sorted([e["spend"] for e in ex], reverse=True) and ex[0]["spend"] == 1200
    assert ex[0]["ad"] == "zz-dfam-1" and ex[0]["ad_id"] == 9201 and ex[0]["roas"] == 1.01
    best = [e for e in ex if e["best"]]
    assert len(best) == 1 and best[0]["ad_id"] == 9205 and best[0]["roas"] == 3.0
    assert detail(client, 99999).status_code == 404


def test_the_best_performer_needs_at_least_100_spent(client, pg, tagged):
    pg.execute("UPDATE ad_metrics SET spend_launch = spend_launch / 100, revenue_launch = revenue_launch / 100 WHERE tracker_row_id BETWEEN 9201 AND 9212")     # every ad under $100
    pg.commit()
    a = assign(client, [line_snapshot(pg, tagged)]).get_json()["created"][0]
    ex = detail(client, a["id"]).get_json()["examples"]
    assert ex and not any(e["best"] for e in ex)


def test_detail_of_a_manual_concept_uses_the_manual_row_and_the_line_with_the_same_text(client, pg, tagged):
    seed_snapshot(pg, {"2026-11-08": [DRAFT]})
    m = client.post("/api/priorities/manual", json={"product": "vita-bull", "section": "test", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T", "story": "He gave up.", "notes": "internal thoughts", "why_now": "because"})
    assert m.status_code == 201, m.get_data(as_text=True)
    mkey = m.get_json()["key"]
    a = assign(client, [mkey]).get_json()["created"][0]
    assert a["concept_key"].startswith("manual:")
    d = detail(client, a["id"]).get_json()
    assert d["current"] is True and d["row"]["manual"] is True and d["row"]["notes"] == "internal thoughts"            # the team sees its own notes
    assert len(d["examples"]) == 10                                                   # no line of its own: the line with the same product / cluster / ICP / pain point supplies the examples


def test_detail_falls_back_to_the_stored_tags_when_the_concept_left_the_snapshot(client, pg, tagged):
    a = assign(client, [line_snapshot(pg, tagged)]).get_json()["created"][0]
    seed_snapshot(pg, {"2026-11-15": [DRAFT]})                                       # the next week's list no longer has the line
    d = detail(client, a["id"]).get_json()
    assert d["current"] is False and d["message"] == "No current numbers for this concept."
    assert d["row"]["cluster"] == "Energy" and d["row"]["icp"] == "Given Up Guy" and d["row"]["pain_point"] == "Low T" and "metrics" not in d["row"] and d["baselines"] == {}
    assert len(d["examples"]) == 10                                                   # the examples do not depend on the snapshot


def test_detail_with_no_snapshot_at_all_still_answers(client, pg, tagged):
    a = assign(client, [line_snapshot(pg, tagged)]).get_json()["created"][0]
    pg.execute("DELETE FROM creative_priorities_snapshots")
    pg.commit()
    d = detail(client, a["id"]).get_json()
    assert d["current"] is False and d["snapshot_week"] is None and d["message"] == "No current numbers for this concept."


def test_detail_needs_a_login(anon, client, snaps):
    a = one(client)
    assert anon.get(f"/api/priorities/assignments/{a['id']}/detail").status_code == 401


def test_vendor_detail_is_the_vendors_own_row_without_links_or_manual_notes(client, anon, pg, tagged):
    key = line_snapshot(pg, tagged)
    a = assign(client, [key], vendor="Madre", note="internal only").get_json()["created"][0]
    tok = link(client, "Madre")["token"]
    r = anon.get(f"/api/priorities/vendor/{tok}/{a['id']}/detail")
    assert r.status_code == 200
    d = r.get_json()
    assert d["current"] is True and d["row"]["cluster"] == "Energy" and len(d["examples"]) == 10 and sum(e["best"] for e in d["examples"]) == 1
    assert all(e["ad_id"] is None for e in d["examples"])                             # the ad page needs a login
    assert all("ad_id" not in h for h in d["row"]["hooks"]) and all("ad_id" not in w for w in d["row"].get("former_winners", []))
    text = json.dumps(d)
    assert "internal only" not in text and "created_by" not in text and "JoshHoughton" not in text
    assert "evidence" not in d["row"] and "components" not in d["row"]


def test_vendor_example_ads_link_only_to_public_facebook_or_instagram_never_drive_ads_manager_or_the_ad_page(client, anon, pg, tagged):
    """The stored Facebook post, else the tracker asset link, but only when it is a https Facebook / Instagram URL; everything else (Drive, Ads Manager, other hosts) is None. The internal detail has no such field."""
    pg.execute("INSERT INTO family_content (family_key, story_url) VALUES ('zz-dfam-1','https://www.facebook.com/lonvera/posts/1'), ('zz-dfam-2','javascript:alert(1)'), ('zz-dfam-3','https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=2')")
    pg.execute("UPDATE tracker_rows SET asset_url = 'https://drive.google.com/file/d/2/view' WHERE id = 9202")            # bad post link, Drive asset link -> nothing: Drive is never exposed
    pg.execute("UPDATE tracker_rows SET asset_url = 'https://www.instagram.com/p/Cabc/' WHERE id = 9203")                # Ads Manager post link refused, asset link is Instagram -> that
    pg.execute("UPDATE tracker_rows SET asset_url = 'https://www.facebook.com/reel/4' WHERE id = 9204")                  # no post row at all -> the Facebook asset link
    pg.execute("UPDATE tracker_rows SET asset_url = 'ftp://x/y' WHERE id = 9205")                                          # nothing usable
    pg.commit()
    try:
        a = assign(client, [line_snapshot(pg, tagged)], vendor="Madre").get_json()["created"][0]
        tok = link(client, "Madre")["token"]
        ex = anon.get(f"/api/priorities/vendor/{tok}/{a['id']}/detail").get_json()["examples"]
        by = {e["ad"]: e for e in ex}
        assert by["zz-dfam-1"]["public_url"] == "https://www.facebook.com/lonvera/posts/1"
        assert by["zz-dfam-2"]["public_url"] is None
        assert by["zz-dfam-3"]["public_url"] == "https://www.instagram.com/p/Cabc/" and by["zz-dfam-4"]["public_url"] == "https://www.facebook.com/reel/4" and by["zz-dfam-5"]["public_url"] is None
        assert all(e["ad_id"] is None for e in ex) and "/ad/" not in json.dumps(ex) and "adsmanager" not in json.dumps(ex) and "drive.google" not in json.dumps(ex)
        inner = detail(client, a["id"]).get_json()["examples"]
        assert inner and all("public_url" not in e for e in inner)
    finally:
        pg.execute("DELETE FROM family_content WHERE family_key LIKE 'zz-dfam-%'")
        pg.commit()


@pytest.mark.parametrize("url", ["https://www.facebook.com/lonvera/posts/101", "https://facebook.com/lonvera/posts/101", "https://fb.watch/abc123/", "https://instagram.com/p/Cabc/", "https://www.instagram.com/reel/Cabc/?utm_source=x",
                                 "  https://www.facebook.com/lonvera/posts/101  "])
def test_public_url_allows_https_facebook_and_instagram_hosts(url):
    from app import priorities_detail as pdt
    assert pdt.public_url(url) == url.strip()


@pytest.mark.parametrize("url", ["https://drive.google.com/file/d/1/view", "https://docs.google.com/document/d/1", "https://drive.example/file/2", "http://www.facebook.com/lonvera/posts/1", "javascript:alert(1)", "data:text/html,x", "ftp://facebook.com/x",
                                 "https://adsmanager.facebook.com/adsmanager/manage/ads", "https://business.facebook.com/x", "https://m.facebook.com/x", "https://facebook.com.evil.example/x", "https://evil.example/facebook.com", "https://www.facebook.com@evil.example/x",
                                 "https://evil.example@www.facebook.com/x", "https://user:pw@www.facebook.com/x", "https://www.facebook.com:8443/x", "https://www.facebook.com/a b", "//www.facebook.com/x", "www.facebook.com/x", "", None])
def test_public_url_refuses_every_other_host_scheme_or_shape(url):
    from app import priorities_detail as pdt
    assert pdt.public_url(url) is None


def test_public_url_takes_the_first_qualifying_candidate():
    from app import priorities_detail as pdt
    assert pdt.public_url("https://drive.google.com/x", None, "https://www.instagram.com/p/1/", "https://www.facebook.com/2") == "https://www.instagram.com/p/1/"


def test_payload_rows_on_a_sheet_line_carry_the_sheet_position(client, pg, tagged):
    key = line_snapshot(pg, tagged)
    rows = client.get("/api/priorities").get_json()["rows"]
    mine = [r for r in rows if r.get("line_id") == tagged]
    assert mine and mine[0]["sheet_pos"] == 999
    assert all("sheet_pos" not in r for r in rows if r.get("line_id") is None)                                                 # a new idea has none


def test_vendor_detail_of_a_manual_concept_hides_the_internal_notes(client, anon, pg, tagged):
    seed_snapshot(pg, {"2026-11-08": [DRAFT]})
    mkey = client.post("/api/priorities/manual", json={"product": "vita-bull", "section": "test", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T", "story": "He gave up.", "notes": "internal thoughts", "why_now": "secret reason"}).get_json()["key"]
    a = assign(client, [mkey], vendor="Madre").get_json()["created"][0]
    tok = link(client, "Madre")["token"]
    d = anon.get(f"/api/priorities/vendor/{tok}/{a['id']}/detail").get_json()
    assert d["row"]["story"] == "He gave up." and d["row"]["manual"] is True
    assert "internal thoughts" not in json.dumps(d) and "secret reason" not in json.dumps(d)


def test_vendor_detail_of_another_vendors_row_bad_token_dropped_row_and_unknown_id_are_404(client, anon, pg, tagged):
    key = line_snapshot(pg, tagged)
    mine = assign(client, [key], vendor="Madre").get_json()["created"][0]
    theirs = assign(client, [key], vendor="Victoria", week=NEXT_MONDAY).get_json()["created"][0]
    tok = link(client, "Madre")["token"]
    assert anon.get(f"/api/priorities/vendor/{tok}/{mine['id']}/detail").status_code == 200
    assert anon.get(f"/api/priorities/vendor/{tok}/{theirs['id']}/detail").status_code == 404          # another vendor's assignment
    assert anon.get(f"/api/priorities/vendor/{tok}/999999/detail").status_code == 404
    assert anon.get(f"/api/priorities/vendor/{'x' * 30}/{mine['id']}/detail").status_code == 404        # a token that is not a vendor link
    assert anon.get(f"/api/priorities/vendor/short/{mine['id']}/detail").status_code == 401             # not a public path at all
    client.patch(f"/api/priorities/assignments/{mine['id']}", json={"dropped": True})
    assert anon.get(f"/api/priorities/vendor/{tok}/{mine['id']}/detail").status_code == 404
    assert anon.post(f"/api/priorities/vendor/{tok}/{mine['id']}/detail", json={}).status_code in (401, 403, 405)    # GET only
    assert anon.get(f"/api/priorities/assignments/{theirs['id']}/detail").status_code == 401             # the internal route stays behind sign-in


# ---------------------------------------------------------------------------------------------------------------- creative format (migration 204)
def test_listing_returns_the_six_formats_and_every_row_carries_the_format_fields(client, snaps):
    one(client)
    d = client.get("/api/priorities/assignments").get_json()
    assert [f["code"] for f in d["formats"]] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert d["formats"][0] == {"code": "F1", "name": "Long animated story, openly AI, told by the character", "length": "90s to 3 min"} and d["formats"][3]["name"] == "Whiteboard explainer"
    r = d["rows"][0]
    assert (r["format_code"], r["format_name"], r["format_length"]) == (None, None, None)


def test_assign_with_a_format_stores_and_returns_it(client, snaps):
    r = assign(client, [_key({**LINE_ROW, "section": "faded"}), _key(DRAFT)], "Madre", MONDAY, format_code="F4")
    assert r.status_code == 201
    for c in r.get_json()["created"]:
        assert (c["format_code"], c["format_name"], c["format_length"]) == ("F4", "Whiteboard explainer", "60 to 90s")
    none = assign(client, [_key(EMPTY)], "Madre", MONDAY, format_code="")                                       # blank = no format
    assert none.status_code == 201 and none.get_json()["skipped"]
    assert {a["format_code"] for a in client.get("/api/priorities/assignments").get_json()["rows"]} == {"F4"}


def test_assign_with_an_unknown_format_is_a_400_and_creates_nothing(client, snaps):
    r = assign(client, [_key({**LINE_ROW, "section": "faded"})], "Madre", MONDAY, format_code="F9")
    assert r.status_code == 400 and "not one of the available formats" in r.get_json()["error"] and "F1" in r.get_json()["error"]
    assert client.get("/api/priorities/assignments").get_json()["rows"] == []
    assert assign(client, [_key({**LINE_ROW, "section": "faded"})], "Madre", MONDAY, format_code=5).status_code == 400


def test_patch_sets_changes_and_clears_the_format(client, snaps):
    a = one(client)
    got = patch(client, a["id"], format_code="F2").get_json()
    assert (got["format_code"], got["format_name"], got["format_length"]) == ("F2", "Talking head (realistic AI)", "45 to 90s")
    assert patch(client, a["id"], format_code="F6").get_json()["format_name"] == "Animated song"
    assert patch(client, a["id"], note="x").get_json()["format_code"] == "F6"                                   # other edits leave it alone
    cleared = patch(client, a["id"], format_code=None).get_json()
    assert (cleared["format_code"], cleared["format_name"], cleared["format_length"]) == (None, None, None)
    patch(client, a["id"], format_code="F3")
    assert patch(client, a["id"], format_code="  ").get_json()["format_code"] is None                           # blank clears too
    r = patch(client, a["id"], format_code="nope")
    assert r.status_code == 400 and "not one of the available formats" in r.get_json()["error"]
    assert patch(client, a["id"], format_code=7).status_code == 400


def test_a_retired_format_cannot_be_chosen_but_stays_on_rows_that_have_it(client, snaps, pg):
    a = one(client)
    patch(client, a["id"], format_code="F5")
    pg.execute("UPDATE creative_formats SET active = false WHERE code = 'F5'")
    pg.commit()
    try:
        d = client.get("/api/priorities/assignments").get_json()
        assert "F5" not in [f["code"] for f in d["formats"]] and d["rows"][0]["format_name"] == "Opens on a sound"
        assert patch(client, a["id"], format_code="F5").status_code == 400
    finally:
        pg.execute("UPDATE creative_formats SET active = true WHERE code = 'F5'")
        pg.commit()


def test_the_vendor_sees_the_format_but_cannot_change_it(client, anon, snaps):
    a = one(client)
    patch(client, a["id"], format_code="F3")
    tok = link(client)["token"]
    row = anon.get(f"/api/priorities/vendor/{tok}").get_json()["rows"][0]
    assert (row["format_code"], row["format_name"], row["format_length"]) == ("F3", "Short acted scene with two people", "30 to 60s")
    for body in ({"format_code": "F1"}, {"format_code": None}, {"format_code": "F1", "vendor_note": "x"}):
        assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json=body).status_code == 400, body
    assert client.get("/api/priorities/assignments").get_json()["rows"][0]["format_code"] == "F3"
    assert anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"vendor_note": "ok"}).get_json()["format_code"] == "F3"       # their own edits keep it


def test_everything_still_works_before_migration_204(client, snaps, pg):
    a = one(client, "Madre", MONDAY)
    patch(client, a["id"], format_code="F2")
    pg.execute("ALTER TABLE creative_priorities_assignments DROP CONSTRAINT creative_priorities_assignments_format_code_fkey")
    pg.execute("ALTER TABLE creative_priorities_assignments RENAME COLUMN format_code TO fc_tmp")
    pg.execute("ALTER TABLE creative_formats RENAME TO cf_tmp")
    pg.commit()
    try:
        d = client.get("/api/priorities/assignments").get_json()
        assert d["formats"] == pa.FORMATS and d["rows"][0]["format_code"] is None and d["rows"][0]["format_name"] is None
        assert assign(client, [_key(DRAFT)], "Madre", MONDAY).status_code == 201                                  # assigning without a format needs nothing from 204
        r = patch(client, a["id"], format_code="F1")
        assert r.status_code == 503 and "migration 204" in r.get_json()["error"]
        assert patch(client, a["id"], note="still fine").status_code == 200
    finally:
        pg.rollback()
        pg.execute("ALTER TABLE cf_tmp RENAME TO creative_formats")
        pg.execute("ALTER TABLE creative_priorities_assignments RENAME COLUMN fc_tmp TO format_code")
        pg.execute("ALTER TABLE creative_priorities_assignments ADD CONSTRAINT creative_priorities_assignments_format_code_fkey FOREIGN KEY (format_code) REFERENCES creative_formats (code)")
        pg.commit()


# ---------------------------------------------------------------------------------------------------------------- history (who did what, when)
def hist(client, aid):
    rows = client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"]
    return next(r for r in rows if r["id"] == aid)["history"]


def test_history_records_who_assigned_and_each_stage_change_in_order(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    assert [(e["kind"], e["actor"], e["stage_before"], e["stage_after"]) for e in a["history"]] == [("assigned", "JoshHoughton", None, "assigned")]
    assert "Madre" in a["history"][0]["detail"] and MONDAY in a["history"][0]["detail"]
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"brief_url": "https://docs.example/brief"})
    anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"creative_url": "https://drive.example/done"})
    anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"ready": True})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"vendor": "Victoria"})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"launched": True})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"dropped": True})
    h = hist(client, a["id"])
    assert [(e["kind"], e["actor"], e["stage_before"], e["stage_after"]) for e in h] == [
        ("assigned", "JoshHoughton", None, "assigned"), ("brief_added", "JoshHoughton", "assigned", "brief_sent"), ("creative_added", "vendor:Madre", "brief_sent", "creative_in"),
        ("status_changed", "vendor:Madre", "brief_sent", "in_review"), ("vendor", "JoshHoughton", "creative_in", "creative_in"), ("launched", "JoshHoughton", "creative_in", "launched"), ("dropped", "JoshHoughton", "launched", "dropped")]
    assert h[4]["detail"] == "Madre to Victoria"
    assert [e["at"] for e in h] == sorted(e["at"] for e in h) and all(e["at"] for e in h)


def test_history_records_a_cleared_ready_and_a_restore_and_ignores_a_no_change_save(client, snaps):
    a = one(client)
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"creative_url": "https://drive.example/x"})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"ready": True})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"ready": True})                          # already ready: no new event
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"ready": False})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"dropped": True})
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"dropped": False})
    assert [e["kind"] for e in hist(client, a["id"])] == ["assigned", "creative_added", "ready", "ready_cleared", "dropped", "restored"]


def test_vendor_page_never_gets_the_history(client, anon, snaps):
    a = one(client)
    tok = link(client)["token"]
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"brief_url": "https://docs.example/brief"})
    d = anon.get(f"/api/priorities/vendor/{tok}").get_json()
    assert d["rows"] and all("history" not in r for r in d["rows"])
    assert "history" not in anon.patch(f"/api/priorities/vendor/{tok}/{a['id']}", json={"vendor_note": "hi"}).get_json()


def test_history_rows_go_with_the_assignment_and_old_rows_are_rebuilt_by_the_migration(client, snaps, pg):
    a = one(client)
    pg.execute("DELETE FROM creative_priorities_assignment_events WHERE assignment_id = %s", (a["id"],))
    pg.execute("UPDATE creative_priorities_assignments SET created_at = now() - interval '5 days', brief_at = now() - interval '2 days', ready_at = now() WHERE id = %s", (a["id"],))
    pg.commit()
    import pathlib
    sql = pathlib.Path(__file__).resolve().parents[2].joinpath("migrations", "243_assignment_events.sql").read_text()
    pg.execute(sql)
    pg.commit()
    pg.execute(sql)                                                                                       # idempotent
    pg.commit()
    h = hist(client, a["id"])
    assert [e["kind"] for e in h] == ["assigned", "brief_added", "ready"] and h[0]["actor"] == "JoshHoughton" and h[1]["actor"] is None


def test_week_vendor_and_product_filters_take_comma_lists_on_the_list_and_the_board(client, snaps):
    a = one(client, "Madre", MONDAY)
    b = one(client, "Victoria", NEXT_MONDAY, key=_key(DRAFT))
    c = one(client, "TrybeOrion", MONDAY)
    ids = lambda d: sorted(r["id"] for r in d["rows"])
    base = "/api/priorities/assignments?include_closed=1"
    assert ids(client.get(base).get_json()) == sorted([a["id"], b["id"], c["id"]])
    assert ids(client.get(base + "&vendor=Madre,Victoria").get_json()) == sorted([a["id"], b["id"]])
    assert ids(client.get(base + f"&week={MONDAY},{NEXT_MONDAY}").get_json()) == sorted([a["id"], b["id"], c["id"]])
    assert ids(client.get(base + f"&week={NEXT_MONDAY}").get_json()) == [b["id"]]
    assert client.get(base + "&vendor=Madre,Nobody").status_code == 400
    cards = lambda d: sorted(x["id"] for col in d["columns"] for x in col["cards"])
    got = client.get("/api/priorities/board?vendor=Madre,Victoria&closed=1").get_json()
    assert cards(got) == sorted([a["id"], b["id"]]) and sorted(x["vendor"] for x in got["capacity"]) == ["Madre", "Victoria"]
    assert cards(client.get(f"/api/priorities/board?week={MONDAY},{NEXT_MONDAY}&closed=1").get_json()) == sorted([a["id"], b["id"], c["id"]])
    one_week = client.get(f"/api/priorities/board?week={NEXT_MONDAY}").get_json()
    assert one_week["week"] == NEXT_MONDAY and client.get(f"/api/priorities/board?week={MONDAY},{NEXT_MONDAY}").get_json()["week"] != NEXT_MONDAY


def test_transcripts_of_a_cards_ads_come_back_original_first_and_a_missing_card_is_a_404(client, snaps, pg):
    a = one(client)
    pg.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = %s WHERE id = %s", (["ZZ-T-Video-1", "ZZ-T-Video-1-hook2", "ZZ-T-Video-1-hook10"], a["id"]))
    for cid, label, text in (("ZZ-T-Video-1-hook10", "Hook 10", "ten"), ("ZZ-T-Video-1-hook2", "Hook 2", "two"), ("ZZ-T-Video-1", "Original", "one")):
        pg.execute("INSERT INTO creative_ad_transcripts (concept_id, assignment_id, label, transcript, language, duration_s, engine, model) VALUES (%s,%s,%s,%s,'en',30.5,'faster-whisper','base.en')", (cid, a["id"], label, text))
    pg.commit()
    try:
        d = client.get(f"/api/priorities/assignments/{a['id']}/transcripts").get_json()
        assert [t["label"] for t in d["transcripts"]] == ["Original", "Hook 2", "Hook 10"] and d["transcripts"][0]["transcript"] == "one" and d["transcripts"][0]["duration_s"] == 30.5
        assert client.get("/api/priorities/assignments/999999/transcripts").status_code == 404
        assert client.get(f"/api/priorities/assignments/{one(client, 'Victoria', NEXT_MONDAY)['id']}/transcripts").get_json() == {"transcripts": []}
    finally:
        pg.execute("DELETE FROM creative_ad_transcripts WHERE concept_id LIKE 'ZZ-T-%'")
        pg.commit()


SEVEN = {"concept_name": "Grandfather used to eat well", "product": "beef-liver", "variations": [{"label": "Original", "url": "https://drive.google.com/file/d/AAA/view", "concept_id": "Lonvera-BeefLiver-Video-1.1"}, {"label": "Hook 2"}],
         "cluster": "Primal Living", "concept_kind": "iteration", "vendor": "Studio Flow", "for_week": MONDAY}


def test_a_ticket_needs_all_seven_fields_and_keeps_its_variations(client, snaps):
    r = client.post("/api/priorities/tickets", json={**SEVEN, "concept_name": "  Grandfather used to eat well ", "status": "in_production"})
    assert r.status_code == 201, r.get_data(as_text=True)
    c = r.get_json()
    assert c["concept_name"] == "Grandfather used to eat well" and c["concept_kind"] == "iteration" and c["status"] == "in_production" and c["vendor"] == "Studio Flow" and c["cluster"] == "Primal Living"
    assert [v["label"] for v in c["variations"]] == ["Original", "Hook 2"] and c["variations"][0]["url"].startswith("https://drive") and c["variations"][1]["url"] == "" and c["tracker_concept_ids"] == ["Lonvera-BeefLiver-Video-1.1"]
    assert c["concept_key"].startswith("ticket:") and c["due_date"] == "2026-11-13" and [e["kind"] for e in hist(client, c["id"])] == ["assigned", "status_changed"]
    assert client.post("/api/priorities/tickets", json=SEVEN).status_code == 201                          # the same concept may be added twice on purpose
    for k in SEVEN:                                                                                        # each one of the seven is required
        bad = {x: v for x, v in SEVEN.items() if x != k}
        r = client.post("/api/priorities/tickets", json=bad)
        assert r.status_code == 400 and k in r.get_json()["error"], (k, r.get_json())
    for bad in ({**SEVEN, "product": "nope"}, {**SEVEN, "vendor": "Nobody"}, {**SEVEN, "concept_kind": "maybe"}, {**SEVEN, "status": "launched"}, {**SEVEN, "variations": 3}, {**SEVEN, "variations": [{"url": "https://x.co"}]},
                {**SEVEN, "variations": [{"label": "Original", "url": "javascript:alert(1)"}]}):
        assert client.post("/api/priorities/tickets", json=bad).status_code == 400, bad
    p = client.patch(f"/api/priorities/assignments/{c['id']}", json={"concept_name": "Grandfather ate well", "concept_kind": None, "variations": [{"label": "Original"}]})
    assert p.status_code == 200 and p.get_json()["concept_name"] == "Grandfather ate well" and p.get_json()["concept_kind"] is None and len(p.get_json()["variations"]) == 1
    assert client.patch(f"/api/priorities/assignments/{c['id']}", json={"concept_kind": "maybe"}).status_code == 400


def test_tickets_can_be_pushed_in_a_batch_all_or_nothing(client, snaps, pg):
    before = pg.execute("SELECT count(*) FROM creative_priorities_assignments").fetchone()[0]
    ok = client.post("/api/priorities/tickets", json={"tickets": [SEVEN, {**SEVEN, "concept_name": "Second", "vendor": "Victoria"}]})
    assert ok.status_code == 201 and [c["concept_name"] for c in ok.get_json()["created"]] == ["Grandfather used to eat well", "Second"]
    bad = client.post("/api/priorities/tickets", json={"tickets": [SEVEN, {**SEVEN, "cluster": ""}]})
    assert bad.status_code == 400 and bad.get_json()["error"].startswith("ticket 2:")
    assert pg.execute("SELECT count(*) FROM creative_priorities_assignments").fetchone()[0] == before + 2          # the bad batch saved nothing
    assert client.post("/api/priorities/tickets", json={"tickets": []}).status_code == 400
