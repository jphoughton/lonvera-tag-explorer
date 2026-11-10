"""/api/priorities, /api/priorities.csv and the /priorities page route: the stored weekly snapshot is read as written, a week selector, 404s, CSV leads with the four tags."""
import csv
import datetime as dt
import io
import json
import re

import pytest

from etl import creative_priorities as cp

cpv_norm = cp.norm_key                      # a snapshot saved before 2026-11-10 stores iterate / faded / ...; keys, sections and statuses read in the new vocabulary

ROW = {"section": "iterate", "product": "vita-bull", "product_name": "Vita Bull", "rank": 1, "line_id": 1, "line_code": "L01", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T",
       "story": "He decided feeling worn out is just turning 50, and stopped trying.", "emotional_trigger": "Fear", "ingredients": "Zinc", "concepts": 3, "score": 70.0, "confidence": "high", "flags": [],
       "why": "because", "status": "WORKING NOW", "cluster_description": "The Energy market.", "icp_description": "The specific person.", "pain_description": "The problem.", "components": {},
       "metrics": {"spend": 1000, "purchases": 12, "roas": 1.2, "cpa": 83.3, "roas_index": 1.1, "ads": 5, "judged": 5, "hits": 3, "mature": 5, "hit_rate": 0.6, "hit_rate_index": 1.1, "spend_30d": 100,
                   "roas_30d": 1.1, "spend_prior30": 90, "roas_prior30": 1.0, "spend_7d": 20, "launches_30d": 1, "launch_share_30d": 0.1, "spend_share": 0.2, "live_ads": 1, "winners_live": 0,
                   "nc_roas": 1.0, "nc_cpa": 90, "nc_purchases": 10, "nc_roas_index": 1.0, "nc_roas_30d": 1.0},
       "hooks": [{"hook": "A hook", "ad": "ad1", "ad_id": 1000, "spend": 500, "roas": 1.4, "line_code": "L01", "borrowed": False}], "evidence": None}


@pytest.fixture()
def snaps(pg):
    pg.execute("DELETE FROM creative_priorities_snapshots")
    for wk in ("2026-11-01", "2026-11-08"):
        pg.execute("INSERT INTO creative_priorities_snapshots (week_start, data_through, basis, thresholds, baselines, rows, stats) VALUES (%s,%s,'meta',%s,%s,%s,%s)",
                   (wk, wk, json.dumps({"INDEX_BAR": 1.05}), json.dumps({"vita-bull": {"product_name": "Vita Bull"}}), json.dumps([{**ROW, "concepts": 3 if wk == "2026-11-08" else 1}]), json.dumps({})))
    pg.commit()
    yield
    pg.execute("DELETE FROM creative_priorities_snapshots")
    pg.commit()


def test_latest_snapshot_and_week_list(client, snaps):
    r = client.get("/api/priorities")
    assert r.status_code == 200
    d = r.get_json()
    assert d["week_start"] == "2026-11-08" and d["rows"][0]["concepts"] == 3
    assert [w["week_start"] for w in d["weeks"]] == ["2026-11-08", "2026-11-01"]
    assert [x["tag"] for x in d["legend"]] == ["Market Cluster", "ICP", "Pain Point", "Story"]


def test_week_selector_snaps_to_the_sunday(client, snaps):
    d = client.get("/api/priorities?week=2026-11-03").get_json()           # a Tuesday: the Sunday on or before is Nov 1
    assert d["week_start"] == "2026-11-01" and d["rows"][0]["concepts"] == 1


def test_missing_week_and_bad_week(client, snaps):
    assert client.get("/api/priorities?week=2026-01-04").status_code == 404
    assert client.get("/api/priorities?week=nope").status_code == 400


def test_no_snapshot_is_a_404_with_a_plain_message(client, pg):
    pg.execute("DELETE FROM creative_priorities_snapshots")
    pg.commit()
    r = client.get("/api/priorities")
    assert r.status_code == 404 and "No creative priorities snapshot" in r.get_json()["error"]


def test_csv_download(client, snaps):
    r = client.get("/api/priorities.csv?week=2026-11-08")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "creative-priorities-2026-11-08.csv" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert rows[0][:4] == ["market_cluster", "icp", "pain_point", "story"]
    assert rows[1][:4] == ["Energy", "Given Up Guy", "Low T", ROW["story"]]            # the story is complete, nothing truncated
    assert rows[1][4:6] == ["Vita Bull", "make_more"] and rows[1][6:9] == ["The Energy market.", "The specific person.", "The problem."]


def test_requires_login(anon, snaps):
    assert anon.get("/api/priorities").status_code == 401
    assert anon.get("/api/priorities.csv").status_code == 401


def test_page_route_serves_the_shell(client):
    r = client.get("/priorities")
    assert r.status_code == 200 and b"topbar" in r.data


# ---------------------------------------------------------------------------------------------------------------- saved views

def _key(r):
    return "|".join(r[k] for k in ("product", "section", "cluster", "icp", "pain_point"))


@pytest.fixture()
def views(pg):
    pg.execute("DELETE FROM creative_priorities_views")
    pg.commit()
    yield
    pg.execute("DELETE FROM creative_priorities_views")
    pg.commit()


def test_view_create_open_csv_delete_and_unknown_token(client, snaps, views):
    r = client.post("/api/priorities/views", json={"name": "  Round   1 ", "week": "2026-11-08", "ids": [_key(ROW), "vita-bull|iterate|gone|gone|gone"]})
    assert r.status_code == 201
    v = r.get_json()
    assert len(v["token"]) >= 22 and v["url"] == "/priorities/v/" + v["token"] and v["name"] == "Round 1" and v["count"] == 1 and v["missing"] == 1
    d = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert d["view"]["name"] == "Round 1" and d["week_start"] == "2026-11-08" and [x["icp"] for x in d["rows"]] == ["Given Up Guy"] and d["baselines"]["vita-bull"]["product_name"] == "Vita Bull"
    assert [x["token"] for x in client.get("/api/priorities/views").get_json()["views"]] == [v["token"]]
    c = client.get(f"/api/priorities/views/{v['token']}/csv")
    rows = list(csv.reader(io.StringIO(c.get_data(as_text=True))))
    full = list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))
    assert c.status_code == 200 and [r[:len(full[0])] for r in rows] == full and rows[0][len(full[0]):] == ['briefed', 'briefed_at', 'briefed_note']                                                      # same columns, same row
    assert client.delete("/api/priorities/views/" + v["token"]).status_code == 204
    assert client.get("/api/priorities/views/" + v["token"]).status_code == 404
    assert client.delete("/api/priorities/views/" + v["token"]).status_code == 404
    assert client.get("/api/priorities/views/short").status_code == 404 and client.get("/api/priorities/views/" + "A" * 30).status_code == 404


def test_view_is_pinned_to_the_snapshot_it_was_saved_from(client, snaps, views, pg):
    v = client.post("/api/priorities/views", json={"week": "2026-11-08", "ids": [_key(ROW)]}).get_json()
    pg.execute("UPDATE creative_priorities_snapshots SET rows = %s WHERE week_start = '2026-11-08'", (json.dumps([{**ROW, "icp": "Changed", "concepts": 9}]),))
    pg.commit()
    d = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert d["rows"][0]["icp"] == "Given Up Guy" and d["rows"][0]["concepts"] == 3                      # later snapshot edits do not reach the view
    assert client.get("/api/priorities?week=2026-11-08").get_json()["rows"][0]["icp"] == "Changed"


def test_view_create_validation_cap_and_rate_limit(client, snaps, views, app):
    assert client.post("/api/priorities/views", json={"ids": []}).status_code == 400
    assert client.post("/api/priorities/views", json={"ids": "x"}).status_code == 400
    assert client.post("/api/priorities/views", json={"ids": ["nope|a|b|c|d"]}).status_code == 400            # nothing in the snapshot
    assert client.post("/api/priorities/views", json={"ids": [f"k{i}" for i in range(501)]}).status_code == 400
    assert client.post("/api/priorities/views", data="x", content_type="text/plain").status_code == 400
    assert client.post("/api/priorities/views", json={"ids": [_key(ROW)]}, headers={"Origin": "http://evil.example"}).status_code == 403
    import app.api as api_mod
    api_mod._view_limiter = None
    app.config["PRIORITIES_VIEWS_PER_MIN"] = 3
    try:
        codes = [client.post("/api/priorities/views", json={"ids": [_key(ROW)]}).status_code for _ in range(5)]
    finally:
        app.config["PRIORITIES_VIEWS_PER_MIN"] = 0
        api_mod._view_limiter = None
    assert codes[:2] == [201, 201] and 429 in codes


def test_view_requires_login_and_page_route_serves_the_shell(anon, client):
    assert anon.get("/api/priorities/views").status_code == 401
    assert anon.post("/api/priorities/views", json={"ids": ["a"]}).status_code == 401
    assert client.get("/priorities/v/" + "A" * 30).status_code == 200


def test_csv_ids_filter_keeps_columns(client, snaps):
    full = list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))
    sel = list(csv.reader(io.StringIO(client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": _key(ROW)}).get_data(as_text=True))))
    none = list(csv.reader(io.StringIO(client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": "zzz"}).get_data(as_text=True))))
    assert sel == full and none == [full[0]]


def test_view_names_special_characters_default_rename_and_csv_filename(client, snaps, views):
    name = "Madre - 11/10/26 Priorities"
    v = client.post("/api/priorities/views", json={"week": "2026-11-08", "ids": [_key(ROW)], "name": f"  {name}  "}).get_json()
    assert v["name"] == name
    assert client.get("/api/priorities/views/" + v["token"]).get_json()["view"]["name"] == name
    r = client.get(f"/api/priorities/views/{v['token']}/csv")
    assert 'filename="madre-11-10-26-priorities.csv"' in r.headers["Content-Disposition"]
    assert client.post("/api/priorities/views", json={"ids": [_key(ROW)], "name": "x" * 300}).get_json()["name"] == "x" * 120
    for empty in ("", "   ", None):
        d = client.post("/api/priorities/views", json={"ids": [_key(ROW)], "name": empty}).get_json()["name"]
        assert re.fullmatch(r"Priorities - \d{1,2}/\d{1,2}/\d{2}", d), d
    # rename keeps the token and the frozen rows
    p = client.patch("/api/priorities/views/" + v["token"], json={"name": "  Boss <b>cut</b> & co / 2  "})
    assert p.status_code == 200 and p.get_json() == {"token": v["token"], "name": "Boss <b>cut</b> & co / 2"}
    g = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert g["view"]["name"] == "Boss <b>cut</b> & co / 2" and len(g["rows"]) == 1
    assert [x["name"] for x in client.get("/api/priorities/views").get_json()["views"] if x["token"] == v["token"]] == ["Boss <b>cut</b> & co / 2"]
    assert re.fullmatch(r"Priorities - \d{1,2}/\d{1,2}/\d{2}", client.patch("/api/priorities/views/" + v["token"], json={"name": ""}).get_json()["name"])
    assert client.patch("/api/priorities/views/" + "A" * 30, json={"name": "x"}).status_code == 404
    assert client.patch("/api/priorities/views/" + v["token"], data="x", content_type="text/plain").status_code == 400
    assert client.patch("/api/priorities/views/" + v["token"], json={"name": 5}).status_code == 400


# ---------------------------------------------------------------------------------------------------------------- manual concepts (migration 199)

@pytest.fixture()
def manual(pg):
    import app.api as api_mod
    api_mod._manual_limiter = None                         # each test starts with a fresh write window
    pg.execute("DELETE FROM creative_priorities_manual")
    pg.commit()
    yield
    pg.execute("DELETE FROM creative_priorities_manual")
    pg.commit()


GOOD = {"product": "beef-liver", "section": "new", "cluster": "Hair Thinning", "icp": "BL3 Thinning and Drained", "pain_point": "Hair Loss", "story": "", "notes": "Try the shower-drain angle.\nSecond line.", "why_now": "Competitor ran it"}


def test_manual_options_come_from_the_catalogs_and_cascade(client):
    d = client.get("/api/priorities/manual/options").get_json()
    assert [p["slug"] for p in d["products"]] == ["vita-bull", "beef-liver", "boneflex"]
    assert [s["key"] for s in d["sections"]] == ["revive", "make_more", "test", "hold"]
    bl = d["tree"]["beef-liver"]
    assert "Hair Thinning" in bl and "BL3 Thinning and Drained" in bl["Hair Thinning"] and "Hair Loss" in bl["Hair Thinning"]["BL3 Thinning and Drained"]
    assert all(isinstance(x, str) for st in bl["Hair Thinning"]["BL3 Thinning and Drained"].values() for x in st)
    assert d["limits"]["notes"] == 2000


def test_manual_crud_and_flags(client, snaps, manual):
    r = client.post("/api/priorities/manual", json=GOOD)
    assert r.status_code == 201
    row = r.get_json()
    assert row["manual"] and row["key"] == f"manual:{row['id']}" and row["status"] == "MANUAL" and row["done"] is False and row["new_values"] == []
    assert row["notes"] == "Try the shower-drain angle.\nSecond line." and row["product_name"] == "Beef Liver" and row["created_at"]
    new = client.post("/api/priorities/manual", json={**GOOD, "cluster": "Brand New Cluster", "icp": "Someone", "pain_point": "Itch", "story": "A story"}).get_json()
    assert new["new_values"] == ["cluster", "ICP", "pain point", "story"]
    g = client.get(f"/api/priorities/manual/{row['id']}").get_json()
    assert g["id"] == row["id"]
    p = client.patch(f"/api/priorities/manual/{row['id']}", json={"notes": "edited", "section": "other"}).get_json()
    assert p["notes"] == "edited" and p["section"] == "hold" and p["done"] is False
    d = client.patch(f"/api/priorities/manual/{row['id']}", json={"status": "done"}).get_json()
    assert d["done"] is True and d["done_at"]
    assert client.patch(f"/api/priorities/manual/{row['id']}", json={"status": "open"}).get_json()["done_at"] is None
    assert [x["id"] for x in client.get("/api/priorities/manual").get_json()["rows"]] == [new["id"], row["id"]]
    client.patch(f"/api/priorities/manual/{row['id']}", json={"status": "done"})
    assert [x["id"] for x in client.get("/api/priorities/manual").get_json()["rows"]] == [new["id"]]
    assert len(client.get("/api/priorities/manual?include_done=1").get_json()["rows"]) == 2
    assert client.delete(f"/api/priorities/manual/{row['id']}").status_code == 204
    assert client.delete(f"/api/priorities/manual/{row['id']}").status_code == 404
    assert client.get(f"/api/priorities/manual/{row['id']}").status_code == 404
    assert client.patch("/api/priorities/manual/999999", json={"notes": "x"}).status_code == 404


def test_manual_validation_and_limits(client, manual):
    bad = lambda **kw: client.post("/api/priorities/manual", json={**GOOD, **kw}).status_code
    assert bad(product="nope") == 400 and bad(section="stop") == 400 and bad(cluster="  ") == 400 and bad(icp="") == 400 and bad(pain_point=None) == 400
    assert bad(notes="x" * 2001) == 400 and bad(why_now="x" * 301) == 400 and bad(cluster="x" * 201) == 400 and bad(notes=5) == 400 and bad(extra="x") == 400
    assert bad(notes="x" * 2000) == 201
    assert client.post("/api/priorities/manual", data="x", content_type="text/plain").status_code == 400
    assert client.post("/api/priorities/manual", json=GOOD, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.patch("/api/priorities/manual/1", json={"status": "weird"}).status_code == 400
    assert client.patch("/api/priorities/manual/1", json={}).status_code == 400
    assert client.post("/api/priorities/manual", json={**GOOD, "cluster": "  A   B  \x00"}).get_json()["cluster"] == "A B"


def test_manual_rate_limit(client, manual, app):
    import app.api as api_mod
    api_mod._manual_limiter = None
    app.config["PRIORITIES_MANUAL_PER_MIN"] = 3
    try:
        codes = [client.post("/api/priorities/manual", json=GOOD).status_code for _ in range(5)]
    finally:
        app.config["PRIORITIES_MANUAL_PER_MIN"] = 0
        api_mod._manual_limiter = None
    assert codes[:3] == [201, 201, 201] and 429 in codes


def test_manual_requires_login(anon):
    assert anon.get("/api/priorities/manual").status_code == 401
    assert anon.post("/api/priorities/manual", json=GOOD).status_code == 401
    assert anon.get("/api/priorities/manual/options").status_code == 401


def test_manual_rows_ride_on_every_snapshot_and_csv_and_views(client, snaps, manual, views):
    m = client.post("/api/priorities/manual", json={**GOOD, "notes": "=HYPERLINK(1)"}).get_json()
    for wk in ("2026-11-08", "2026-11-01"):                       # not tied to a week
        d = client.get(f"/api/priorities?week={wk}").get_json()
        assert [r["key"] for r in d["rows"] if r.get("manual")] == [m["key"]] and len(d["rows"]) == 2
    full = list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))
    head = full[0]
    assert head[:4] == ["market_cluster", "icp", "pain_point", "story"] and head[head.index("source"):head.index("source") + 2] == ["source", "notes"] and head[-5:-3] == ["spend_14d", "roas_14d"] and head[-1] == "add_source" and len(full) == 3
    by = {r[head.index("source")]: r for r in full[1:]}
    assert set(by) == {"auto", "manual"} and by["manual"][head.index("notes")] == "'=HYPERLINK(1)" and by["manual"][head.index("status")] == "MANUAL" and by["auto"][head.index("notes")] == ""
    sel = list(csv.reader(io.StringIO(client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": m["key"]}).get_data(as_text=True))))
    assert len(sel) == 2 and sel[1][head.index("source")] == "manual"
    # done rows leave the full CSV but can still be exported when selected
    client.patch(f"/api/priorities/manual/{m['id']}", json={"status": "done"})
    assert len(list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))) == 2
    assert len(list(csv.reader(io.StringIO(client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": m["key"]}).get_data(as_text=True))))) == 2
    # a saved view freezes the manual row
    v = client.post("/api/priorities/views", json={"week": "2026-11-08", "ids": [m["key"], _key(ROW)]}).get_json()
    assert v["count"] == 2 and v["missing"] == 0
    client.patch(f"/api/priorities/manual/{m['id']}", json={"notes": "changed later"})
    fr = [r for r in client.get("/api/priorities/views/" + v["token"]).get_json()["rows"] if r.get("manual")][0]
    assert fr["notes"] == "=HYPERLINK(1)"
    vc = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{v['token']}/csv").get_data(as_text=True))))
    assert {r[head.index("source")] for r in vc[1:]} == {"auto", "manual"}


# ---------------------------------------------------------------------------------------------------------------- view kinds: rows / filters, update, csv name

ROW2 = {**ROW, "section": "stop", "icp": "Plateau Man", "pain_point": "Flat energy", "product": "beef-liver", "product_name": "Beef Liver", "story": "He stopped noticing the fog.", "concepts": 0}


@pytest.fixture()
def two_rows(pg, snaps):
    from app import api as api_mod
    api_mod._view_limiter = None                                                                                   # 30 writes a minute per IP: each test starts a fresh window
    pg.execute("UPDATE creative_priorities_snapshots SET rows = %s, baselines = %s WHERE week_start = '2026-11-08'",
               (json.dumps([ROW, ROW2]), json.dumps({"vita-bull": {"product_name": "Vita Bull"}, "beef-liver": {"product_name": "Beef Liver"}})))
    pg.commit()


def test_rows_view_reports_kind_keys_and_legacy_default(client, two_rows, views):
    v = client.post("/api/priorities/views", json={"ids": [_key(ROW), _key(ROW2)]}).get_json()
    assert v["kind"] == "rows" and v["count"] == 2
    g = client.get("/api/priorities/views/" + v["token"]).get_json()["view"]
    assert g["kind"] == "rows" and g["filters"] is None and sorted(g["keys"]) == sorted([cpv_norm(_key(ROW)), cpv_norm(_key(ROW2))]) and g["count"] == 2
    assert client.post("/api/priorities/views", json={"ids": [_key(ROW)], "kind": "nope"}).status_code == 400


def test_filter_view_stores_filters_freezes_the_matching_rows_and_lists_its_kind(client, two_rows, views):
    v = client.post("/api/priorities/views", json={"name": "VB live", "kind": "filters", "filters": {"products": ["vita-bull"], "sections": [], "q": " low ", "key": "pain", "dir": -1, "status": "WORKING NOW", "junk": 1}}).get_json()
    assert v["kind"] == "filters" and v["count"] == 1
    g = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert g["view"]["kind"] == "filters" and g["view"]["filters"] == {"products": ["vita-bull"], "sections": [], "q": "low", "key": "pain", "dir": -1, "status": "Working", "statuses": []} and g["view"]["keys"] == []
    assert [r["icp"] for r in g["rows"]] == ["Given Up Guy"]                              # frozen copy = the rows matching at save time
    lst = client.get("/api/priorities/views").get_json()["views"]
    assert lst[0]["kind"] == "filters" and lst[0]["filters"]["products"] == ["vita-bull"] and lst[0]["count"] == 1
    assert client.post("/api/priorities/views", json={"kind": "filters", "filters": {"status": "NOT WORKING"}}).get_json()["count"] == 0       # status filter applies to the frozen copy too
    nothing = client.post("/api/priorities/views", json={"kind": "filters", "filters": {"q": "zzzz"}}).get_json()
    assert nothing["count"] == 0
    for bad in (None, "x", {"products": "vita-bull"}, {"q": "x" * 201}, {"sections": [1]}, {"dir": 1, "key": "k" * 40}):
        assert client.post("/api/priorities/views", json={"kind": "filters", "filters": bad}).status_code == 400, bad


def test_update_view_replaces_rows_or_filters_keeps_name_token_and_kind(client, two_rows, views):
    v = client.post("/api/priorities/views", json={"name": "Keep me", "ids": [_key(ROW), _key(ROW2)]}).get_json()
    u = client.put("/api/priorities/views/" + v["token"], json={"ids": [_key(ROW2)]})
    assert u.status_code == 200 and u.get_json()["count"] == 1 and u.get_json()["kind"] == "rows"
    g = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert g["view"]["name"] == "Keep me" and g["view"]["keys"] == [_key(ROW2)] and [r["icp"] for r in g["rows"]] == ["Plateau Man"]
    f = client.post("/api/priorities/views", json={"name": "F", "kind": "filters", "filters": {"products": ["vita-bull"]}}).get_json()
    u = client.put("/api/priorities/views/" + f["token"], json={"filters": {"products": ["beef-liver"], "q": "fog"}, "kind": "rows", "ids": [_key(ROW)]})     # kind cannot be switched
    assert u.status_code == 200 and u.get_json()["kind"] == "filters"
    g = client.get("/api/priorities/views/" + f["token"]).get_json()
    assert g["view"]["filters"]["products"] == ["beef-liver"] and [r["icp"] for r in g["rows"]] == ["Plateau Man"]
    assert client.put("/api/priorities/views/" + v["token"], json={"ids": []}).status_code == 400
    assert client.put("/api/priorities/views/" + v["token"], json={"ids": ["nope|a|b|c|d"]}).status_code == 400
    assert client.put("/api/priorities/views/" + v["token"], data="x", content_type="text/plain").status_code == 400
    assert client.put("/api/priorities/views/" + "A" * 30, json={"ids": [_key(ROW)]}).status_code == 404
    assert client.put("/api/priorities/views/" + v["token"], json={"ids": [_key(ROW)]}, headers={"Origin": "http://evil.example"}).status_code == 403
    client.delete("/api/priorities/views/" + v["token"])
    assert client.put("/api/priorities/views/" + v["token"], json={"ids": [_key(ROW)]}).status_code == 404         # a deleted view cannot be updated; GET is 404 too (the page shows 'deleted')
    assert client.get("/api/priorities/views/" + v["token"]).status_code == 404


def test_csv_name_param_and_view_csv_id_filter(client, two_rows, views):
    r = client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "name": "Madre - 11/10/26 Priorities"})
    assert 'filename="madre-11-10-26-priorities.csv"' in r.headers["Content-Disposition"]
    v = client.post("/api/priorities/views", json={"name": "Two", "ids": [_key(ROW), _key(ROW2)]}).get_json()
    full = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{v['token']}/csv").get_data(as_text=True))))
    one = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{v['token']}/csv", query_string={"id": _key(ROW2)}).get_data(as_text=True))))
    assert len(full) == 3 and len(one) == 2 and one[1][1] == "Plateau Man"


def test_view_write_routes_need_login(anon, two_rows):
    assert anon.put("/api/priorities/views/" + "A" * 30, json={"ids": ["a"]}).status_code == 401


# ---------------------------------------------------------------------------------------------------------------- briefed checkmarks

def _briefed(client, token, **body):
    return client.patch(f"/api/priorities/views/{token}/briefed", json=body)


def test_briefed_toggle_persists_for_everyone_with_a_timestamp_and_note(client, two_rows, views):
    v = client.post("/api/priorities/views", json={"name": "Brief me", "ids": [_key(ROW), _key(ROW2)]}).get_json()
    assert client.get("/api/priorities/views/" + v["token"]).get_json()["view"]["briefed"] == {}
    r = _briefed(client, v["token"], key=_key(ROW), briefed=True, note="  sent to   Madre 11/12  ")
    assert r.status_code == 200 and r.get_json()["briefed"] is True and r.get_json()["note"] == "sent to Madre 11/12" and re.match(r"\d{4}-\d\d-\d\dT", r.get_json()["at"])
    b = client.get("/api/priorities/views/" + v["token"]).get_json()["view"]["briefed"]
    assert list(b) == [cpv_norm(_key(ROW))] and b[cpv_norm(_key(ROW))]["note"] == "sent to Madre 11/12"
    csvt = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{v['token']}/csv").get_data(as_text=True))))
    assert csvt[0][-3:] == ["briefed", "briefed_at", "briefed_note"]
    by_icp = {r_[1]: r_ for r_ in csvt[1:]}
    assert by_icp["Given Up Guy"][-3] == "yes" and by_icp["Given Up Guy"][-1] == "sent to Madre 11/12" and by_icp["Plateau Man"][-3:] == ["", "", ""]
    plain = list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))
    assert "briefed" not in plain[0]                                                                              # only view exports carry the columns
    live = list(csv.reader(io.StringIO(client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "briefed_from": v["token"]}).get_data(as_text=True))))
    assert live[0][-3] == "briefed" and {r_[1]: r_[-3] for r_ in live[1:]} == {"Given Up Guy": "yes", "Plateau Man": ""}
    r = _briefed(client, v["token"], key=_key(ROW), briefed=False)
    assert r.get_json()["briefed"] is False and client.get("/api/priorities/views/" + v["token"]).get_json()["view"]["briefed"] == {}


def test_briefed_validation_unknown_key_token_and_formula_note(client, two_rows, views):
    v = client.post("/api/priorities/views", json={"ids": [_key(ROW)]}).get_json()
    t = v["token"]
    assert _briefed(client, t, key="nope|a|b|c|d", briefed=True).status_code == 404                              # not a row of the view
    assert _briefed(client, t, key=_key(ROW2), briefed=True).status_code == 404                                  # a real row, but not in this rows view
    assert _briefed(client, t, key="", briefed=True).status_code == 400
    assert _briefed(client, t, key=_key(ROW)).status_code == 400 and _briefed(client, t, key=_key(ROW), briefed="yes").status_code == 400
    assert _briefed(client, t, key=_key(ROW), briefed=True, note=5).status_code == 400
    assert _briefed(client, "A" * 30, key=_key(ROW), briefed=True).status_code == 404 and _briefed(client, "short", key="x", briefed=True).status_code == 404
    assert client.patch(f"/api/priorities/views/{t}/briefed", data="x", content_type="text/plain").status_code == 400
    assert client.patch(f"/api/priorities/views/{t}/briefed", json={"key": _key(ROW), "briefed": True}, headers={"Origin": "http://evil.example"}).status_code == 403
    long = _briefed(client, t, key=_key(ROW), briefed=True, note="x" * 500).get_json()
    assert len(long["note"]) == 200
    _briefed(client, t, key=_key(ROW), briefed=True, note="=HYPERLINK(1)")
    row = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{t}/csv").get_data(as_text=True))))[1]
    assert row[-1] == "'=HYPERLINK(1)"                                                                            # a typed note cannot run as a formula in Sheets
    f = client.post("/api/priorities/views", json={"kind": "filters", "filters": {"products": ["vita-bull"]}}).get_json()["token"]
    assert _briefed(client, f, key=_key(ROW), briefed=True).status_code == 200                                    # a filters view accepts any row of the current snapshot
    assert _briefed(client, f, key="zzz|a|b|c|d", briefed=True).status_code == 404
    client.delete("/api/priorities/views/" + t)
    assert _briefed(client, t, key=_key(ROW), briefed=True).status_code == 404


def test_briefed_has_its_own_rate_limit_and_needs_login(anon, client, two_rows, views, app):
    from app import api as api_mod
    assert anon.patch("/api/priorities/views/" + "A" * 30 + "/briefed", json={"key": "a", "briefed": True}).status_code == 401
    v = client.post("/api/priorities/views", json={"ids": [_key(ROW)]}).get_json()
    api_mod._briefed_limiter = None
    old = app.config.get("PRIORITIES_BRIEFED_PER_MIN")
    app.config["PRIORITIES_BRIEFED_PER_MIN"] = 3
    try:
        codes = [_briefed(client, v["token"], key=_key(ROW), briefed=bool(i % 2)).status_code for i in range(5)]
        assert codes[:3] == [200, 200, 200] and 429 in codes[3:]
    finally:
        app.config["PRIORITIES_BRIEFED_PER_MIN"] = old
        api_mod._briefed_limiter = None


def test_filter_view_stores_a_list_of_statuses_and_old_single_status_still_filters(client, snaps, manual, views):
    from app import creative_priorities as cpv
    f = cpv.clean_filters({"products": [], "sections": [], "statuses": ["RISING", "WORKING NOW"], "q": ""})
    assert f["statuses"] == ["Rising", "Working"] and f["status"] is None
    rows = [{"product": "vita-bull", "section": "iterate", "status": s} for s in ("RISING", "WORKING NOW", "NOT WORKING")]
    assert [r["status"] for r in cpv.apply_filters(rows, f)] == ["RISING", "WORKING NOW"]        # stored (old) statuses still match the new labels
    old = cpv.clean_filters({"products": [], "sections": [], "status": "NOT WORKING"})        # a view saved before multi-select
    assert [r["status"] for r in cpv.apply_filters(rows, old)] == ["NOT WORKING"]
    assert cpv.apply_filters(rows, cpv.clean_filters({})) == rows
    with pytest.raises(Exception):
        cpv.clean_filters({"statuses": "RISING"})


# ----------------------------------------------------------------------------------------------- Action / Status redesign: old stored data keeps working (2026-11-10)
def test_payload_carries_the_definitions_with_real_thresholds(client, snaps):
    d = client.get("/api/priorities?week=2026-11-08").get_json()
    assert [a["key"] for a in d["definitions"]["actions"]] == ["revive", "make_more", "test", "stop", "hold"]
    assert [s["label"] for s in d["definitions"]["statuses"]] == ["Working", "Rising", "Fading", "Not working", "Average", "Too early", "Untested"]
    assert "$5,000" in " ".join(s["rule"] for s in d["definitions"]["statuses"])
    assert d["rows"][0]["section"] == "make_more" and d["rows"][0]["status"] == "Working"                  # stored as iterate / WORKING NOW


def test_a_view_saved_with_old_keys_still_opens_briefs_and_unbriefs(client, two_rows, views, pg):
    old_key = _key(ROW)                                                                                        # product|iterate|...: how every view saved before the redesign stores it
    pg.execute("INSERT INTO creative_priorities_views (token, name, week_start, keys, payload, kind, briefed) VALUES (%s,%s,'2026-11-08',%s,%s,'rows',%s)",
               ("O" * 30, "Old view", [old_key], json.dumps({"week_start": "2026-11-08", "baselines": {}, "rows": [ROW]}), json.dumps({old_key: {"at": "2026-11-09T10:00:00+00:00", "note": "sent"}})))
    pg.commit()
    g = client.get("/api/priorities/views/" + "O" * 30).get_json()
    new_key = cpv_norm(old_key)
    assert new_key != old_key and g["view"]["keys"] == [new_key] and list(g["view"]["briefed"]) == [new_key]
    assert g["rows"][0]["section"] == "make_more" and g["rows"][0]["status"] == "Working"
    assert _briefed(client, "O" * 30, key=old_key, briefed=False).status_code == 200                           # an old key from a bookmark still addresses the row
    assert client.get("/api/priorities/views/" + "O" * 30).get_json()["view"]["briefed"] == {}
    assert _briefed(client, "O" * 30, key=new_key, briefed=True, note="again").status_code == 200
    stored = pg.execute("SELECT briefed FROM creative_priorities_views WHERE token = %s", ("O" * 30,)).fetchone()[0]
    assert list(stored) == [new_key]                                                                           # old and new forms never both stored
    csvt = list(csv.reader(io.StringIO(client.get(f"/api/priorities/views/{'O' * 30}/csv", query_string={"id": old_key}).get_data(as_text=True))))
    assert len(csvt) == 2 and csvt[1][csvt[0].index("section")] == "make_more"


def test_selected_rows_csv_and_filters_accept_old_section_names(client, two_rows):
    old_key = _key(ROW)
    r = client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": old_key})
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert len(rows) == 2 and rows[1][rows[0].index("section")] == "make_more"
    from app import creative_priorities as cpv
    f = cpv.clean_filters({"products": [], "sections": ["iterate", "proposal", "make_more"], "statuses": ["RUNNING AS USUAL"], "q": ""})
    assert f["sections"] == ["make_more", "test"] and f["statuses"] == ["Average"]


# ----------------------------------------------------------------------------------------------- "Add to sheet" folded into Test (2026-11-10, later)
ONSHEET = {**ROW, "section": "new", "line_id": 5, "line_code": "L05", "cluster": "Sleep", "icp": "Night Owl", "pain_point": "Wakes at 3", "status": "UNTESTED / UNDER-TESTED", "rank": 1}
IDEA_DRAFT = {**ROW, "section": "add", "source": "Our draft", "line_id": None, "line_code": "DRAFT", "cluster": "Chest Fat", "icp": "Chest Guy", "pain_point": "Belly", "status": "Untested", "rank": 1}
IDEA_COMP = {**ROW, "section": "add", "source": "Competitor", "line_id": None, "line_code": "not on sheet", "cluster": "Other Angle", "icp": "z", "pain_point": "w", "status": "Untested", "rank": 1}


@pytest.fixture()
def add_rows(pg, snaps):
    """A snapshot stored before the change: Add rows still carry section 'add', and each kind is ranked from 1."""
    from app import api as api_mod
    api_mod._view_limiter = None
    pg.execute("UPDATE creative_priorities_snapshots SET rows = %s WHERE week_start = '2026-11-08'", (json.dumps([IDEA_COMP, IDEA_DRAFT, ROW, ONSHEET]),))
    pg.commit()


def test_an_old_snapshot_add_row_reads_as_test_with_its_source_and_test_orders_sheet_lines_first(client, add_rows):
    d = client.get("/api/priorities?week=2026-11-08").get_json()
    assert "add" not in {r["section"] for r in d["rows"]}
    assert "add" not in [a["key"] for a in d["definitions"]["actions"]]
    test = sorted((r for r in d["rows"] if r["section"] == "test"), key=lambda r: r["rank"])
    assert [(r["source"] if "source" in r else None) for r in test] == [None, "Our draft", "Competitor"] and [r["rank"] for r in test] == [1, 2, 3]
    assert [r["icp"] for r in test] == ["Night Owl", "Chest Guy", "z"]


def test_csv_has_no_add_action_and_keeps_the_add_source_column(client, add_rows):
    rows = list(csv.reader(io.StringIO(client.get("/api/priorities.csv?week=2026-11-08").get_data(as_text=True))))
    h = rows[0]
    assert "add_source" in h and h[-1] == "add_source"
    assert "add" not in {r[h.index("section")] for r in rows[1:]}
    got = {r[h.index("icp")]: (r[h.index("section")], r[h.index("add_source")]) for r in rows[1:]}
    assert got["Chest Guy"] == ("test", "Our draft") and got["z"] == ("test", "Competitor") and got["Night Owl"] == ("test", "")


def test_a_filter_or_selection_saved_on_add_means_test(client, add_rows, views):
    from app import creative_priorities as cpv
    assert cpv.clean_filters({"sections": ["add"]})["sections"] == ["test"]
    assert cpv.clean_filters({"sections": ["add", "test", "stop"]})["sections"] == ["test", "stop"]
    v = client.post("/api/priorities/views", json={"kind": "filters", "filters": {"sections": ["add"]}}).get_json()
    g = client.get("/api/priorities/views/" + v["token"]).get_json()
    assert g["view"]["filters"]["sections"] == ["test"] and v["count"] == 3                        # the on-sheet line and both new ideas
    assert {r["section"] for r in g["rows"]} == {"test"}
    old_key = "|".join([IDEA_DRAFT["product"], "add", IDEA_DRAFT["cluster"], IDEA_DRAFT["icp"], IDEA_DRAFT["pain_point"]])                     # a bookmarked selection from before the change
    r = client.get("/api/priorities.csv", query_string={"week": "2026-11-08", "id": old_key})
    out = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert len(out) == 2 and out[1][out[0].index("section")] == "test" and out[1][out[0].index("add_source")] == "Our draft"
