"""/api/priorities, /api/priorities.csv and the /priorities page route: the stored weekly snapshot is read as written, a week selector, 404s, CSV leads with the four tags."""
import csv
import datetime as dt
import io
import json
import re

import pytest

from etl import creative_priorities as cp

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
    assert rows[1][4:6] == ["Vita Bull", "iterate"] and rows[1][6:9] == ["The Energy market.", "The specific person.", "The problem."]


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
    assert c.status_code == 200 and rows == full                                                      # same columns, same row
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
    assert [s["key"] for s in d["sections"]] == ["iterate", "faded", "new", "other"]
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
    assert p["notes"] == "edited" and p["section"] == "other" and p["done"] is False
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
    assert head[:4] == ["market_cluster", "icp", "pain_point", "story"] and head[-2:] == ["source", "notes"] and len(full) == 3
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
