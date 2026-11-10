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
