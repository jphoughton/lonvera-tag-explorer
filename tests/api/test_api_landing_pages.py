"""/api/landing-pages and /api/landing-pages/<id>: read-only copy store plus top ads per page (migration 207)."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture()
def seeded(pg):
    today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()
    pg.execute("DELETE FROM landing_pages WHERE url LIKE 'https://lpt.example.com/%'")
    pg.execute("DELETE FROM ad_daily WHERE ad_id LIKE 'LPT_%'")
    ids = {}
    for name, url in (("a", "https://lpt.example.com/a"), ("b", "https://lpt.example.com/b")):
        ids[name] = pg.execute("INSERT INTO landing_pages (url, host, path) VALUES (%s,'lpt.example.com',%s) RETURNING id", (url, "/" + name)).fetchone()[0]
    for ad, page in (("LPT_1", "a"), ("LPT_2", "a"), ("LPT_3", "b")):
        pg.execute("INSERT INTO landing_page_ads (ad_id, landing_page_id, raw_url, source) VALUES (%s,%s,'u','creative')", (ad, ids[page]))
    for ad, ago, sp, pu, cv in (("LPT_1", 1, 50, 1, 100), ("LPT_2", 10, 200, 2, 150), ("LPT_3", 1, 5, 0, 0)):
        pg.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, purchases, conv_value, source) VALUES (%s,%s,'lpt','LPT',%s,%s,%s,'meta_api')", (ad, today - dt.timedelta(days=ago), sp, pu, cv))
    vid = pg.execute("""INSERT INTO landing_page_versions (landing_page_id, content_hash, http_status, final_url, title, word_count, blocks, prices)
                        VALUES (%s,'h1',200,'https://lpt.example.com/a','Page A',12,%s,%s) RETURNING id""",
                     (ids["a"], json.dumps([{"i": 0, "tag": "h1", "level": 1, "text": "Hello"}]), json.dumps([{"price": "$29", "context": "only $29"}]))).fetchone()[0]
    pg.execute("UPDATE landing_pages SET current_version_id=%s, last_status=200, last_fetched_at=now() WHERE id=%s", (vid, ids["a"]))
    pg.commit()
    yield ids
    pg.execute("DELETE FROM landing_pages WHERE url LIKE 'https://lpt.example.com/%'")
    pg.execute("DELETE FROM ad_daily WHERE ad_id LIKE 'LPT_%'")
    pg.commit()


def test_list_orders_pages_by_spend_and_names_the_top_ads(client, seeded):
    d = client.get("/api/landing-pages?window=30").get_json()
    mine = [p for p in d["pages"] if p["url"].startswith("https://lpt.example.com/")]
    assert [p["url"] for p in mine] == ["https://lpt.example.com/a", "https://lpt.example.com/b"]
    a = mine[0]
    assert a["spend"] == 250.0 and a["title"] == "Page A" and a["price_count"] == 1 and a["versions"] == 1 and a["ads_mapped"] == 2
    assert [(t["rank"], t["ad_id"], t["spend"], t["roas"]) for t in a["top_ads"]] == [(1, "LPT_2", 200.0, 0.75), (2, "LPT_1", 50.0, 2.0)]
    assert mine[1]["title"] is None and mine[1]["last_fetched_at"] is None


def test_seven_day_window_and_top_limit(client, seeded):
    d = client.get("/api/landing-pages?window=7&top=1").get_json()
    a = next(p for p in d["pages"] if p["url"].endswith("/a"))
    assert d["window_days"] == 7 and a["spend"] == 50.0 and [t["ad_id"] for t in a["top_ads"]] == ["LPT_1"]


def test_detail_has_the_copy_and_top_ads(client, seeded):
    d = client.get(f"/api/landing-pages/{seeded['a']}").get_json()
    assert d["url"].endswith("/a") and len(d["versions"]) == 1 and d["versions"][0]["is_current"]
    assert d["versions"][0]["blocks"][0]["text"] == "Hello" and d["versions"][0]["prices"][0]["price"] == "$29"
    assert [t["ad_id"] for t in d["top_ads"]] == ["LPT_2", "LPT_1"]
    assert client.get("/api/landing-pages/999999999").status_code == 404


@pytest.mark.parametrize("q", ["window=14", "top=0", "top=99", "top=x"])
def test_bad_parameters_are_400(client, q):
    assert client.get("/api/landing-pages?" + q).status_code == 400


def test_login_required(anon):
    assert anon.get("/api/landing-pages").status_code == 401
