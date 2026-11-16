"""Madre review (/api/madre-review): the weekly deck payload for one creative vendor, Meta-reported, and the cached ad video route."""
import datetime as dt

import pytest

from app import madre_media as mmed, madre_review as mr

from .helpers import get_ok

URL = "/api/madre-review"


@pytest.fixture()
def vendor_rows(pg):
    """VB-Fam-A and VB-Fam-B tracker rows tagged Madre for the test; vendor back to NULL afterwards"""
    before = pg.execute("SELECT id, vendor FROM tracker_rows").fetchall()
    fams = pg.execute("SELECT DISTINCT family_key FROM tracker_rows WHERE family_key IN ('vb-fam-a', 'vb-fam-b')").fetchall()
    assert fams, "fixture changed: no vb-fam-a / vb-fam-b tracker rows"
    pg.execute("UPDATE tracker_rows SET vendor = 'Madre' WHERE family_key IN ('vb-fam-a', 'vb-fam-b')")
    pg.commit()
    yield
    for r in before:
        pg.execute("UPDATE tracker_rows SET vendor = %s WHERE id = %s", (r[1] if not isinstance(r, dict) else r["vendor"], r[0] if not isinstance(r, dict) else r["id"]))
    pg.commit()


def test_slide_set_and_order(client):
    d = get_ok(client, URL)
    ids = [(s["section"], s["id"]) for s in d["slides"]]
    assert ids[:3] == [("overview", "cover"), ("overview", "week"), ("overview", "scorecard")]
    for prod in ("vita-bull", "beef-liver", "boneflex"):
        assert [i for sec, i in ids if sec == prod] == ["cover", "scorecard", "stage", "winners", "losers", "pockets", "pipeline", "next", "asks"]
    assert ids[-1] == ("notes", "notes") and len(ids) == 3 + 27 + 1
    assert [s["id"] for s in d["sections"]] == ["overview", "vita-bull", "beef-liver", "boneflex", "notes"]


def test_default_week_is_latest_complete_monday_sunday(client):
    d = get_ok(client, URL)
    start, end = dt.date.fromisoformat(d["week"]["start"]), dt.date.fromisoformat(d["week"]["end"])
    assert start.weekday() == 0 and end == start + dt.timedelta(days=6)
    assert end <= dt.date.fromisoformat(d["last_loaded_day"]) and end + dt.timedelta(days=7) > dt.date.fromisoformat(d["last_loaded_day"])
    assert d["prev"]["end"] == (start - dt.timedelta(days=1)).isoformat()
    assert d["weeks"][0]["start"] == d["week"]["start"]


def test_week_param_snaps_to_monday_and_rejects_junk(client):
    d = get_ok(client, URL)
    older = dt.date.fromisoformat(d["week"]["start"]) - dt.timedelta(days=7)
    mid = older + dt.timedelta(days=3)
    assert get_ok(client, f"{URL}?week={mid}")["week"]["start"] == older.isoformat()
    assert client.get(URL + "?week=banana").status_code == 400
    assert get_ok(client, URL + "?week=2999-01-01")["week"]["start"] == d["week"]["start"]


def test_account_scorecard_equals_the_view_and_ratios_come_from_sums(client, pg):
    d = get_ok(client, URL)
    a, b = d["week"]["start"], d["week"]["end"]
    s = pg.execute("""SELECT sum(spend) sp, sum(meta_purchases) pu, sum(meta_revenue) rv, sum(impressions) im, sum(link_clicks) lc
                      FROM analytics.meta_ad_daily WHERE day BETWEEN %s AND %s""", (a, b)).fetchone()
    cur = d["scorecard"]["account"]["cur"]
    assert cur["spend"] == pytest.approx(float(s[0]), abs=0.01) and cur["purchases"] == pytest.approx(float(s[1]))
    assert cur["cpa"] == pytest.approx(float(s[0]) / float(s[1]), rel=1e-9)
    assert cur["roas"] == pytest.approx(float(s[2]) / float(s[0]), rel=1e-9)
    assert cur["ctr"] == pytest.approx(float(s[4]) / float(s[3]), rel=1e-9)
    assert cur["cpc"] == pytest.approx(float(s[0]) / float(s[4]), rel=1e-9)


def test_vendor_is_separate_from_untagged_and_others(client, vendor_rows):
    d = get_ok(client, URL)
    v, acc = d["scorecard"]["vendor"]["cur"], d["scorecard"]["account"]["cur"]
    assert 0 < v["spend"] < acc["spend"]
    sc = next(s for s in d["slides"] if s["section"] == "overview" and s["id"] == "scorecard")
    groups = {c["group"] for c in sc["table"]["cols"]}
    assert groups == {"Madre", "Account", "Other vendors", "Untagged"}
    cols = {(c["group"], c["window"]): c["m"] for c in sc["table"]["cols"]}
    cur = d["week"]["label"]
    assert cols[("Madre", cur)]["spend"] + cols[("Other vendors", cur)]["spend"] + cols[("Untagged", cur)]["spend"] == pytest.approx(cols[("Account", cur)]["spend"], abs=0.01)
    other = get_ok(client, URL + "?vendor=Nobody")
    assert other["scorecard"]["vendor"]["cur"]["spend"] == 0


def test_product_sections_only_hold_that_products_ads_and_tickets(client, vendor_rows):
    d = get_ok(client, URL)
    for s in d["slides"]:
        if s["section"] not in ("vita-bull", "beef-liver", "boneflex"):
            continue
        for k in s.get("ads", []):
            assert d["ads"][k]["product"] == s["section"]
        for c in s.get("columns", []):
            assert all(d["tickets"][t]["product"] == s["section"] for t in c["tickets"])
        for r in s.get("rows", []) if s["kind"] == "pockets" else []:
            assert r["product"] == s["section"]
        for c in s.get("cards", []) if s["kind"] == "suggest" else []:
            assert c["product"] == s["section"]


def test_winner_and_loser_rules():
    ok = {"m": {"spend": 300.0, "cpa": 90.0}, "open": 2, "ready": 0}
    assert mr.pocket_flag(ok, 100.0) == "scale"
    assert mr.pocket_flag({"m": {"spend": 299.0, "cpa": 50.0}, "open": 0, "ready": 0}, 100.0) == ""
    assert mr.pocket_flag({"m": {"spend": 500.0, "cpa": 90.0}, "open": 3, "ready": 0}, 100.0) == ""
    assert mr.pocket_flag({"m": {"spend": 599.0, "cpa": 200.0}, "open": 0, "ready": 8}, 100.0) == "pile"
    assert mr.pocket_flag({"m": {"spend": 700.0, "cpa": 130.0}, "open": 5, "ready": 0}, 100.0) == "pull"
    assert mr.pocket_flag({"m": {"spend": 700.0, "cpa": None}, "open": 5, "ready": 0}, 100.0) == "pull"
    assert mr.pocket_flag({"m": {"spend": 700.0, "cpa": 129.0}, "open": 5, "ready": 0}, 100.0) == ""


def test_helpers():
    assert mr.concept_key("Lonvera-VitalBull-Video-8.6-hook2 - Copy 3") == "lonvera-vitalbull-video-8.6"
    assert mr.status_num("7 - Approved - Ready To Launch") == 7 and mr.status_num("n/a") is None
    m = mr.metrics({"spend": 200, "impressions": 10000, "link_clicks": 100, "add_to_carts": 20, "purchases": 4, "purchase_value": 300})
    assert (m["cpa"], m["roas"], m["ctr"], m["cpc"], m["cpm"], m["cvr"], m["aov"], m["atc_rate"], m["atc_to_purchase"]) == (50.0, 1.5, 0.01, 2.0, 20.0, 0.04, 75.0, 0.2, 0.2)
    assert mr.metrics({})["cpa"] is None
    assert mr.latest_week(dt.date(2026, 11, 15)) == dt.date(2026, 11, 9) and mr.latest_week(dt.date(2026, 11, 14)) == dt.date(2026, 11, 2)
    assert mr.span_label(dt.date(2026, 11, 9), dt.date(2026, 11, 15)) == "Nov 9–15"


def test_voice_terms_on_the_page(client):
    d = get_ok(client, URL)
    text = " ".join(str(s.get("title", "")) + str(s.get("foot", "")) for s in d["slides"])
    assert "cost per purchase" not in text.lower()
    t = {s["id"]: s["title"] for s in d["slides"] if s["section"] == "vita-bull"}
    assert t["winners"].endswith("winners: CPA 10%+ under Vita Bull avg") and t["pipeline"] == "Open concepts" and t["next"] == "Brief more here"


def test_video_route_404_without_a_file_and_streams_with_range(client, monkeypatch, tmp_path):
    monkeypatch.setattr(mmed, "get_video", lambda fam, url: None)
    assert client.get("/api/madre-review/video?family=vb-fam-a").status_code == 404
    f = tmp_path / "x.mp4"
    f.write_bytes(bytes(range(256)) * 40)
    monkeypatch.setattr(mmed, "get_video", lambda fam, url: f)
    r = client.get("/api/madre-review/video?family=vb-fam-a")
    assert r.status_code == 200 and r.mimetype == "video/mp4" and len(r.data) == 10240
    r = client.get("/api/madre-review/video?family=vb-fam-a", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100


def test_media_family_names_are_validated():
    assert mmed.get_video("../../etc/passwd", None) is None
    assert mmed.get_video("", None) is None


def test_media_pick_prefers_exact_name_then_shortest_copy():
    files = [{"name": "bull-526-hook2 - 2.mp4", "mimeType": "video/mp4", "size": "5"}, {"name": "bull-526-hook2.mp4", "mimeType": "video/mp4", "size": "5"},
             {"name": "bull-526-hook2.jpg", "mimeType": "image/jpeg", "size": "5"}]
    assert mmed._pick(files, "bull-526-hook2")["name"] == "bull-526-hook2.mp4"
    assert mmed._pick(files[:1], "bull-526-hook2")["name"] == "bull-526-hook2 - 2.mp4"
    assert mmed._pick(files[2:], "bull-526-hook2") is None
