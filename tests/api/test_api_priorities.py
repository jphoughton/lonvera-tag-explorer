"""/api/priorities, /api/priorities.csv and the /priorities page route: the stored weekly snapshot is read as written, a week selector, 404s, CSV leads with the four tags."""
import csv
import datetime as dt
import io
import json

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
