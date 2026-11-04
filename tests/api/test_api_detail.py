"""/api/ads/<id> and /media/<asset_id>."""
import datetime as dt

import pytest

from . import fixture_data as F
from .helpers import get_ok

THROUGH = F.THROUGH


def test_detail_linemap_row(client, fx):
    d = get_ok(client, "/api/ads/1000")
    assert d["id"] == 1000 and d["product"] == {"slug": "vita-bull", "name": "Vita Bull"} and d["concept_id"] == "VB-A1"
    assert d["ad_family"] == "VB-Fam-A" and d["family_key"] == "vb-fam-a" and d["sheet_row"] == 2 and d["status"] == "8 - Launched" and d["ad_type"] == "VID"
    assert d["launch_date"] == "2026-08-01" and d["concept_name"] == "Concept VB-A1" and d["meta_ad_name"] == "VB-A1 Name"
    assert d["story_url"] == "https://facebook.example/post/1" and d["asset_url"] == "https://drive.example/VB-A1"
    tag = d["tag"]
    assert (tag["tag_set"], tag["version"], tag["line_code"]) == ("linemap", "linemap-v2-2026-11-03", "L01")
    assert (tag["cluster"], tag["icp"], tag["pain_point"], tag["emotional_trigger"]) == ("Alpha Cluster", "ICP One", "Pain A", "Fear")
    assert tag["confidence"] == "high" and tag["method"] == "image" and tag["seen"] == "Bottle on counter" and tag["why"] == "spoken point is zinc"
    assert tag["runner_up"] == "ICP Two - Pain C (Relief)"
    assert tag["copy_only_line"] == {"line_code": "L03", "label": "ICP Two - Pain C (Relief)"}
    assert tag["reliability_note"] == "High confidence — blind re-tag agreed ~93%. Blind re-tag agreement overall: line 79%, ICP 84%, cluster 88%."
    assert d["tracker_tag"] == {"cluster": None, "icp": None, "pain_point": None, "emotion": "Fear", "ingredient": "Zinc"}
    assert d["line"] == {"line_code": "L01", "story": "Story A", "emotional_trigger": "Fear",
                         "ingredients": [{"ingredient": "Zinc", "attacks": "Zinc depletion", "supports": "Supports testosterone"},
                                         {"ingredient": "Cholesterol", "attacks": "Missing substrate", "supports": "Supports hormones"}]}
    c = d["content"]
    assert c["primary_text"] == ["Primary text for VB-A1", "Second primary text"] and c["headlines"] == ["Headline 1", "Headline 2"]
    assert (c["meta_body"], c["meta_title"], c["meta_desc"], c["transcript"], c["is_video"]) == ("Meta body text", "Meta title", "Meta description", "Spoken transcript", True)
    assert isinstance(d["assets"]["thumb"], int) and len(d["assets"]["frames"]) == 2
    assert d["window_info"]["kind"] == "launch" and d["compare_info"] is None
    assert [s["id"] for s in d["siblings"]] == [1001]
    sib = d["siblings"][0]
    assert (sib["concept_id"], sib["line_code"], sib["cluster"], sib["icp"], sib["pain_point"], sib["status"]) == ("VB-A2", "L02", "Alpha Cluster", "ICP One", "Pain B", "8 - Launched")
    assert d["metrics"]["counted_on_this_row"] is True


def test_detail_second_row_of_family_and_notes(client, fx):
    d = get_ok(client, "/api/ads/1001")
    assert d["metrics"]["counted_on_this_row"] is False and [s["id"] for s in d["siblings"]] == [1000]
    assert d["tag"]["reliability_note"].startswith("Tagged from copy only (no image). Low confidence — blind re-tag agreed ~52%.")
    assert d["tag"]["copy_only_line"] is None
    # family-level truth is shown on every row of the family
    assert d["metrics"]["family"] == get_ok(client, "/api/ads/1000")["metrics"]["family"]
    thumb = get_ok(client, "/api/ads/1002")["tag"]["reliability_note"]
    assert thumb.startswith("Tagged from a thumbnail only. Medium confidence.")
    assert "thumbnail" not in get_ok(client, "/api/ads/1008")["tag"]["reliability_note"].lower() or "4 frames" in get_ok(client, "/api/ads/1008")["tag"]["method"]


def test_detail_family_metrics(client, fx):
    d = get_ok(client, "/api/ads/1000?window=7d")
    f = d["metrics"]["family"]
    first = fx.ad_metrics[1000]
    assert f["launch"]["spend"] == pytest.approx(float(first["spend_launch"]), abs=0.006) and f["launch"]["purchases"] == first["purchases_launch"]
    assert f["d7"]["spend"] == pytest.approx(float(first["spend_7d"]), abs=0.006) and f["d30"]["spend"] == pytest.approx(float(first["spend_30d"]), abs=0.006)
    assert f["live"] is True
    s = F.window_sums(fx, "vb-fam-a", THROUGH - dt.timedelta(days=6), THROUGH, F.STAGES)
    assert f["window"]["spend"] == pytest.approx(float(s[0]), abs=0.006) and f["window"]["revenue"] == pytest.approx(float(s[2]), abs=0.006)
    assert f["window"]["roas"] == pytest.approx(float(s[2] / s[0]), abs=1e-3)
    assert f["window"]["spend_per_day"] == pytest.approx(float(s[0]) / 7, abs=0.006)
    # default window = launch -> equals the launch block
    assert get_ok(client, "/api/ads/1000")["metrics"]["family"]["window"]["spend"] == f["launch"]["spend"]
    # stage filter applies to the window block only
    t = get_ok(client, "/api/ads/1000?window=launch&stage=testing")["metrics"]["family"]
    assert t["launch"] == f["launch"] and t["window"]["spend"] < f["launch"]["spend"]


def test_detail_daily_series(client, fx):
    d = get_ok(client, "/api/ads/1000")["daily"]
    assert len(d) == 181 and d[0]["day"] == "2026-05-06" and d[-1]["day"] == "2026-11-02"
    days = [x["day"] for x in d]
    assert days == sorted(days)
    got = {x["day"]: x for x in d}
    series = {}
    for (f, day, st), v in fx.family_daily.items():
        if f == "vb-fam-a":
            cur = series.setdefault(day.isoformat(), [F.Z, F.Z, F.Z, 0])
            for i in range(4):
                cur[i] += v[i]
    for day, row in got.items():
        want = series.get(day, [0, 0, 0, 0])
        assert row["spend"] == pytest.approx(float(want[0]), abs=0.006) and row["purchases"] == pytest.approx(float(want[1]), abs=0.006)
        assert row["revenue"] == pytest.approx(float(want[2]), abs=0.006) and row["link_clicks"] == want[3]
    assert got["2026-05-06"]["spend"] == 0                     # zero filled before the family existed


def test_detail_meta_ads_and_campaigns(client, fx):
    d = get_ok(client, "/api/ads/1000?window=30d")
    ads = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-a&window=30d")
    assert d["meta_ads"] == ads["rows"]
    by_camp = {c["campaign_id"]: c for c in d["campaigns"]}
    assert set(by_camp) == {"C100", "C200"} | ({None} if any(r["ad_id"] is None for r in ads["rows"]) else set())
    assert by_camp["C200"]["name"] == "VB_Testing_Cambium" and by_camp["C200"]["stage"] == "testing"
    assert sum(c["spend"] for c in d["campaigns"]) == pytest.approx(sum(r["spend"] for r in ads["rows"]), abs=0.011)
    # since launch = API lifetime per ad, so the campaign table adds up to the family's lifetime with no campaign-less remainder
    full = get_ok(client, "/api/ads/1000")
    assert {c["campaign_id"] for c in full["campaigns"]} == {"C100", "C200"}
    assert sum(r["spend"] for r in full["meta_ads"]) == pytest.approx(full["metrics"]["family"]["launch"]["spend"], abs=0.011)
    assert sum(c["spend"] for c in full["campaigns"]) == pytest.approx(full["metrics"]["family"]["launch"]["spend"], abs=0.011)


def test_detail_blank_family_and_untagged_and_tracker_tags(client, fx):
    d = get_ok(client, "/api/ads/1003")                       # concept 0510, no ad family
    assert d["concept_id"] == "0510" and d["family_key"] == "id:0510" and d["metrics"]["counted_on_this_row"] is False
    assert d["metrics"]["family"]["launch"]["spend"] == 0 and d["metrics"]["family"]["window"]["spend"] == 0 and d["meta_ads"] == []
    assert len(d["daily"]) == 181 and all(x["spend"] == 0 for x in d["daily"]) and d["story_url"] is None
    assert d["content"]["is_video"] is False and d["assets"] == {"thumb": None, "frames": []} and d["line"] is not None
    assert get_ok(client, "/api/ads/1004")["concept_id"] == "510"
    e = get_ok(client, "/api/ads/1007")                       # untagged
    assert e["tag"] is None and e["line"] is None and e["metrics"]["counted_on_this_row"] is True
    bl = get_ok(client, "/api/ads/1009")                      # beef liver: tracker tag set, no taxonomy
    assert bl["tag"]["tag_set"] == "tracker" and bl["tag"]["cluster"] == "Hair" and bl["tag"]["pain_point"] == "Hair Loss"
    assert bl["tag"]["reliability_note"] is None and bl["line"] is None and bl["tag"]["line_code"] is None
    assert bl["product"]["slug"] == "beef-liver"


def test_detail_404(client):
    assert client.get("/api/ads/99999").status_code == 404
    assert client.get("/api/ads/0").status_code == 404
    assert client.get("/api/ads/-1").status_code == 404


def test_media(client, fx, pg):
    thumb = get_ok(client, "/api/ads/1000")["assets"]
    r = client.get(f"/media/{thumb['thumb']}")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data[:4] == b"\x89PNG"
    assert r.headers["Cache-Control"] == "private, max-age=86400" and r.headers["X-Content-Type-Options"] == "nosniff"
    for fid in thumb["frames"]:
        assert client.get(f"/media/{fid}").status_code == 200
    jpg_id = get_ok(client, "/api/ads/1002")["assets"]["thumb"]
    r = client.get(f"/media/{jpg_id}")
    assert r.mimetype == "image/jpeg" and r.data == b"\xff\xd8\xff\xd9"
    for bad in ("999999", "0", "-3", "abc", "1;DROP TABLE x", "1%27%20OR%201=1", "99999999999999999999"):
        assert client.get(f"/media/{bad}").status_code == 404, bad
    assert client.get("/media/").status_code == 404
