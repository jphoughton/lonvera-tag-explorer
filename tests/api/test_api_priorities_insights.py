"""/api/priorities/lines/<id>/insights: the drill-down behind each block of a /priorities row panel. Read-only, from the fixture's ad_metrics / family_daily / linemap tags."""
import pytest


@pytest.fixture()
def line(pg):
    row = pg.execute("""SELECT g.line_id FROM ad_tags g JOIN ad_metrics m ON m.tracker_row_id = g.tracker_row_id AND m.is_first_row
                        JOIN family_daily f ON f.family_key = m.family_key
                        WHERE g.tag_set = 'linemap' AND g.is_current AND g.line_id IS NOT NULL GROUP BY 1 ORDER BY sum(f.spend) DESC LIMIT 1""").fetchone()
    assert row, "the fixture has no tagged line with spend"
    return row[0] if not isinstance(row, dict) else row["line_id"]


def url(line, **kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    return f"/api/priorities/lines/{line}/insights" + ("?" + q if q else "")


def test_every_block_answers_with_ads_trend_and_hooks(client, line):
    for block in ("then_now", "timeline", "hooks", "winners", "performance", "testing", "live"):
        d = client.get(url(line, block=block, window="all")).get_json()
        assert d["block"] == block and d["line_id"] == line and d["line"]
        assert d["ads"] and len(d["ads"]) <= 10 and d["weekly"] and d["total"]["spend"] > 0
        assert set(d["ads"][0]) >= {"ad_id", "name", "spend", "roas", "purchases", "launch_date", "hook"}


def test_ads_are_by_spend_and_window_narrows(client, line):
    a = client.get(url(line, block="performance", window="all")).get_json()
    assert [x["spend"] for x in a["ads"]] == sorted((x["spend"] for x in a["ads"]), reverse=True)
    w = client.get(url(line, block="live", window="7")).get_json()
    assert w["total"]["spend"] <= a["total"]["spend"] and len(w["weekly"]) <= 2


def test_one_ad_drill_and_foreign_ad(client, line):
    ad = client.get(url(line, block="winners", window="all")).get_json()["ads"][0]
    d = client.get(url(line, block="ad", window="all", ad_id=ad["ad_id"])).get_json()
    assert [x["ad_id"] for x in d["ads"]] == [ad["ad_id"]] and d["weekly"]
    assert client.get(url(line, block="ad", window="all", ad_id=999999999)).status_code == 404


def test_bad_arguments(client, line):
    assert client.get(url(line, block="nope")).status_code == 400
    assert client.get(url(line, block="hooks", window="12")).status_code == 400
    assert client.get(url(line, block="ad", ad_id="x")).status_code == 400
    assert client.get(url(987654, block="hooks")).status_code == 404
