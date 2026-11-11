"""etl.landing_pages: URL normalisation, creative link extraction, ad to page mapping (creative / post / preview), page parsing, versioning, the top-ads view. No network: Graph and HTTP are faked."""
import datetime as dt
import json

import pytest

from etl import landing_pages as lp

PAGE_V1 = """<html><head><title>Beef Liver Capsules | Lonvera</title><meta name="description" content="Grass fed beef liver.">
<script>var x = "ignored text";</script></head><body>
<h1>Why my hair stopped falling out</h1><p>I tried everything for my low ferritin.</p>
<img src="https://cdn.example.com/a.jpg?v=1" alt="Bottle of beef liver capsules">
<ul><li>Grass fed and finished</li><li>3rd party tested</li></ul>
<div>Only <span>$29.99</span> per bottle today.</div>
<p>You are covered by our 90 day money back guarantee. Ship in 24 hours.</p>
<blockquote>Within a few days my hair began growing back. -- PK</blockquote>
<a class="btn btn-primary" href="/cart/add">Add to cart</a><button type="submit">Yes, send my order</button>
<a href="/policies/privacy">Privacy policy</a>
</body></html>"""
PAGE_V1_SAME_TEXT_NEW_IMAGE_URL = PAGE_V1.replace("a.jpg?v=1", "a.jpg?v=2").replace('var x = "ignored text"', 'var x = "different script"')
PAGE_V2 = PAGE_V1.replace("$29.99", "$34.99")
THIN = "<html><head><title>x</title></head><body><div id='app'></div><script src='a.js'></script></body></html>"


class Resp:
    def __init__(self, text="", status=200, url="https://shop.lonvera.com/bl/sp"):
        self.text, self.status_code, self.url = text, status, url


class Http:
    def __init__(self, pages=None):
        self.pages, self.calls = pages or {}, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        v = self.pages.get(url)
        if isinstance(v, Exception):
            raise v
        if v is None:
            return Resp("", 404, url)
        return Resp(v, 200, url)


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE landing_pages, landing_page_versions, landing_page_ads, landing_page_ad_checks RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def seed_spend(conn, rows):
    """rows: (ad_id, days_ago, spend, purchases, conv_value)"""
    today = dt.datetime.now(dt.timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles")).date()
    for ad, ago, sp, pu, cv in rows:
        conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, purchases, conv_value, source) VALUES (%s,%s,'f','F',%s,%s,%s,'meta_api')",
                     (ad, today - dt.timedelta(days=ago), sp, pu, cv))
    conn.commit()


# ---------------------------------------------------------------- urls
@pytest.mark.parametrize("raw,want", [
    ("https://Shop.Lonvera.com/bl/sp/?utm_source=fb&fbclid=abc#top", "https://shop.lonvera.com/bl/sp"),
    ("http://lonvera.com/pages/lonvera-grassfed-beef-liver-landing-a", "https://lonvera.com/pages/lonvera-grassfed-beef-liver-landing-a"),
    ("lonvera.com/pages/x?b=2&a=1&utm_campaign={{campaign.name}}", "https://lonvera.com/pages/x?a=1&b=2"),
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Flonvera.com%2Fpages%2Fa%3Futm_source%3Dfb&h=zzz", "https://lonvera.com/pages/a"),
    ("https://lonvera.com", "https://lonvera.com/"),
])
def test_normalise_url(raw, want):
    assert lp.normalise_url(raw)[0] == want


@pytest.mark.parametrize("raw", [None, "", "https://www.facebook.com/123/videos/9", "mailto:a@b.com", "https://instagram.com/p/x", "notaurl"])
def test_normalise_url_rejects(raw):
    assert lp.normalise_url(raw) is None


def test_extract_creative_links_all_shapes():
    c = {"object_story_spec": {"video_data": {"call_to_action": {"type": "LEARN_MORE", "value": {"link": "https://a.com/1"}}},
                               "link_data": {"link": "https://a.com/2", "child_attachments": [{"link": "https://a.com/3"}, {"link": "https://a.com/2"}]}},
         "asset_feed_spec": {"link_urls": [{"website_url": "https://a.com/4"}, {"display_url": "x.com"}]}}
    assert lp.extract_creative_links(c) == ["https://a.com/2", "https://a.com/3", "https://a.com/1", "https://a.com/4"]
    assert lp.extract_creative_links(None) == [] and lp.extract_creative_links({"id": "1"}) == []


def test_links_from_preview_html():
    html = 'x https:\\/\\/l.facebook.com\\/l.php?u=https\\u00253A\\u00252F\\u00252Flonvera.com\\u00252Fpages\\u00252Fa&h=AUB1 y https://static.xx.fbcdn.net/a.js https://facebook.csod.com/ https://fb.zoom.us/'
    assert lp.links_from_preview_html(html) == ["https://lonvera.com/pages/a"]


# ---------------------------------------------------------------- ads to pages
class Graph:
    """creative batch -> by ad id; post lookups -> by story id (missing = permission error); previews -> body with an iframe src."""

    def __init__(self, creatives, posts=None, previews=None):
        self.creatives, self.posts, self.previews, self.calls = creatives, posts or {}, previews or {}, []

    def __call__(self, url, params, what, max_retries=7, sleep=None):
        self.calls.append(what)
        if what == "ad creative":
            ids = params["ids"].split(",")
            if any(i == "BAD" for i in ids):
                raise RuntimeError("HTTP 400 code 100: bad id access_token=SECRET123456789")
            return {i: {"id": i, "creative": self.creatives[i]} for i in ids if i in self.creatives}
        if what == "post cta":
            sid = url.rsplit("/", 1)[1]
            if sid not in self.posts:
                raise RuntimeError("HTTP 400 code 10: needs pages_read_engagement")
            return {"call_to_action": {"value": {"link": self.posts[sid]}}}
        if what == "ad preview":
            ad = url.split("/")[-2]
            if ad not in self.previews:
                return {"data": []}
            return {"data": [{"body": f'<iframe src="https://preview.example/{ad}"></iframe>'}]}
        raise AssertionError(what)


def test_resolve_ads_uses_creative_post_and_preview_and_is_idempotent(conn):
    seed_spend(conn, [("A1", 1, 100, 2, 200), ("A2", 2, 50, 1, 60), ("A3", 3, 40, 1, 30), ("A4", 3, 30, 0, 0), ("A5", 1, 10, 0, 0), ("fam:x", 1, 99, 0, 0)])
    cre = {"A1": {"object_story_spec": {"video_data": {"call_to_action": {"value": {"link": "https://shop.lonvera.com/bl/sp?utm_source=fb"}}}}},
           "A2": {"effective_object_story_id": "P_1"},
           "A3": {"effective_object_story_id": "P_2"},
           "A4": {"effective_object_story_id": "P_3"},
           "A5": {}}
    g = Graph(cre, posts={"P_1": "https://lonvera.com/pages/lp-a"}, previews={"A3": 1})
    http = Http({"https://preview.example/A3": 'see https://l.facebook.com/l.php?u=https%3A%2F%2Flonvera.com%2Fpages%2Fbf-a%3Futm_x%3D1&h=1'})
    ads = lp.ads_to_check(conn, 30)
    assert [a for a, _ in ads] == ["A1", "A2", "A3", "A4", "A5"]                         # biggest first, fam: rows skipped
    st = lp.resolve_ads(conn, "tok", ads, g, http, max_previews=1, sleep=lambda s: None)
    assert (st["creative"], st["post"], st["preview"], st["no_link"], st["left_for_next_run"]) == (1, 1, 1, 1, 1)
    got = dict(conn.execute("SELECT a.ad_id || '|' || l.url, a.source FROM landing_page_ads a JOIN landing_pages l ON l.id = a.landing_page_id").fetchall())
    assert got == {"A1|https://shop.lonvera.com/bl/sp": "creative", "A2|https://lonvera.com/pages/lp-a": "post", "A3|https://lonvera.com/pages/bf-a": "preview"}
    assert dict(conn.execute("SELECT ad_id, result FROM landing_page_ad_checks").fetchall()) == {"A1": "linked", "A2": "linked", "A3": "linked", "A5": "no_link"}      # A4 was past the preview budget: not recorded
    assert [a for a, _ in lp.ads_to_check(conn, 30)] == ["A4"]                           # only the one the budget left over is asked again
    lp.resolve_ads(conn, "tok", lp.ads_to_check(conn, 30), g, Http({}), 5, sleep=lambda s: None)    # next run: its preview shows no link
    assert dict(conn.execute("SELECT ad_id, result FROM landing_page_ad_checks").fetchall())["A4"] == "no_link" and lp.ads_to_check(conn, 30) == []
    lp.resolve_ads(conn, "tok", [("A1", 100.0)], g, http, 0, sleep=lambda s: None)       # a second look changes nothing
    assert conn.execute("SELECT count(*) FROM landing_page_ads").fetchone()[0] == 3 and conn.execute("SELECT count(*) FROM landing_pages").fetchone()[0] == 3


def test_resolve_ads_dry_run_writes_nothing(conn):
    seed_spend(conn, [("A1", 1, 100, 2, 200)])
    g = Graph({"A1": {"asset_feed_spec": {"link_urls": [{"website_url": "https://lonvera.com/pages/z"}]}}})
    st = lp.resolve_ads(conn, "tok", lp.ads_to_check(conn, 30), g, Http(), 0, write=False, sleep=lambda s: None)
    assert st["creative"] == 1 and conn.execute("SELECT count(*) FROM landing_pages").fetchone()[0] == 0 and conn.execute("SELECT count(*) FROM landing_page_ad_checks").fetchone()[0] == 0


def test_one_bad_ad_in_a_batch_is_not_fatal_and_the_error_is_scrubbed(conn):
    seed_spend(conn, [("A1", 1, 100, 2, 200), ("BAD", 1, 90, 0, 0)])
    g = Graph({"A1": {"object_story_spec": {"link_data": {"link": "https://lonvera.com/pages/ok"}}}})
    st = lp.resolve_ads(conn, "tok", lp.ads_to_check(conn, 30), g, Http(), 0, sleep=lambda s: None)
    assert st["creative"] == 1 and st["error"] == 1
    detail = conn.execute("SELECT detail FROM landing_page_ad_checks WHERE ad_id='BAD'").fetchone()[0]
    assert "SECRET123456789" not in detail and "access_token=***" in detail
    assert conn.execute("SELECT result FROM landing_page_ad_checks WHERE ad_id='A1'").fetchone()[0] == "linked"


# ---------------------------------------------------------------- parsing
def test_parse_page_extracts_the_copy():
    p = lp.parse_page(PAGE_V1)
    assert p["title"] == "Beef Liver Capsules | Lonvera" and p["meta_description"] == "Grass fed beef liver."
    h1 = [b for b in p["blocks"] if b["level"] == 1]
    assert h1 and h1[0]["text"] == "Why my hair stopped falling out" and h1[0]["tag"] == "h1"
    texts = [b["text"] for b in p["blocks"]]
    assert "Grass fed and finished" in texts and "ignored text" not in p["full_text"]
    assert [x["price"] for x in p["prices"]] == ["$29.99"]
    assert any("90 day money back guarantee" in g for g in p["guarantees"])
    assert any("hair began growing back" in r for r in p["reviews"])
    assert {c["text"] for c in p["ctas"]} >= {"Add to cart", "Yes, send my order"} and "Privacy policy" not in {c["text"] for c in p["ctas"]}
    assert p["images"][0]["alt"] == "Bottle of beef liver capsules"
    assert [b["i"] for b in p["blocks"]] == list(range(len(p["blocks"])))
    assert not lp.is_thin(p) or p["word_count"] < lp.THIN_WORDS


def test_hash_ignores_scripts_and_image_addresses_but_not_text():
    a, b, c = lp.parse_page(PAGE_V1), lp.parse_page(PAGE_V1_SAME_TEXT_NEW_IMAGE_URL), lp.parse_page(PAGE_V2)
    assert a["content_hash"] == b["content_hash"] and a["content_hash"] != c["content_hash"]


# ---------------------------------------------------------------- fetching and versions
def add_page(conn, url="https://shop.lonvera.com/bl/sp"):
    pid = conn.execute("INSERT INTO landing_pages (url, host, path) VALUES (%s, 'shop.lonvera.com', '/bl/sp') RETURNING id", (url,)).fetchone()[0]
    conn.commit()
    return pid


def test_new_version_only_when_the_copy_changes(conn):
    pid = add_page(conn)
    pages = [(pid, "https://shop.lonvera.com/bl/sp")]
    nap = lambda s: None
    s1 = lp.fetch_pages(conn, pages, Http({pages[0][1]: PAGE_V1}), allow_render=False, sleep=nap)
    s2 = lp.fetch_pages(conn, pages, Http({pages[0][1]: PAGE_V1_SAME_TEXT_NEW_IMAGE_URL}), allow_render=False, sleep=nap)
    s3 = lp.fetch_pages(conn, pages, Http({pages[0][1]: PAGE_V2}), allow_render=False, sleep=nap)
    assert (s1["new_versions"], s2["new_versions"], s2["unchanged"], s3["new_versions"]) == (1, 0, 1, 1)
    assert conn.execute("SELECT count(*) FROM landing_page_versions WHERE landing_page_id=%s", (pid,)).fetchone()[0] == 2
    cur = conn.execute("SELECT v.prices->0->>'price', p.last_status, p.last_error FROM landing_pages p JOIN landing_page_versions v ON v.id = p.current_version_id WHERE p.id=%s", (pid,)).fetchone()
    assert cur == ("$34.99", 200, None)
    s4 = lp.fetch_pages(conn, pages, Http({pages[0][1]: PAGE_V1}), allow_render=False, sleep=nap)          # changed back: the old version row is reused
    assert s4["unchanged"] == 1 and conn.execute("SELECT count(*) FROM landing_page_versions").fetchone()[0] == 2
    assert conn.execute("SELECT v.prices->0->>'price' FROM landing_pages p JOIN landing_page_versions v ON v.id = p.current_version_id").fetchone()[0] == "$29.99"


def test_a_failing_page_is_recorded_and_the_rest_still_run(conn):
    p1, p2, p3 = add_page(conn, "https://a.example.com/x"), add_page(conn, "https://b.example.com/y"), add_page(conn, "https://c.example.com/z")
    http = Http({"https://a.example.com/x": RuntimeError("boom access_token=SECRET123456789"), "https://c.example.com/z": PAGE_V1})   # b is a 404
    st = lp.fetch_pages(conn, [(p1, "https://a.example.com/x"), (p2, "https://b.example.com/y"), (p3, "https://c.example.com/z")], http, allow_render=False, sleep=lambda s: None)
    assert (st["failed"], st["new_versions"]) == (2, 1)
    errs = dict(conn.execute("SELECT id, last_error FROM landing_pages").fetchall())
    assert "SECRET123456789" not in errs[p1] and "boom" in errs[p1] and errs[p2] == "RuntimeError: HTTP 404" and errs[p3] is None


def test_thin_pages_fall_back_to_the_browser_only_when_allowed(conn):
    pid = add_page(conn)
    pages = [(pid, "https://shop.lonvera.com/bl/sp")]
    rendered = PAGE_V1
    st = lp.fetch_pages(conn, pages, Http({pages[0][1]: THIN}), render=lambda u: rendered, allow_render=True, sleep=lambda s: None)
    assert st["thin"] == 1 and st["rendered"] == 1
    assert conn.execute("SELECT rendered_in_browser, title FROM landing_page_versions").fetchone() == (True, "Beef Liver Capsules | Lonvera")
    pid2 = add_page(conn, "https://other.example.com/p")
    calls = []
    st = lp.fetch_pages(conn, [(pid2, "https://other.example.com/p")], Http({"https://other.example.com/p": THIN}), render=lambda u: calls.append(u) or rendered, allow_render=False, sleep=lambda s: None)
    assert st["thin"] == 1 and st["rendered"] == 0 and calls == []


def test_a_broken_browser_keeps_the_server_copy(conn):
    pid = add_page(conn)

    def boom(u):
        raise RuntimeError("no chromium")
    st = lp.fetch_pages(conn, [(pid, "https://shop.lonvera.com/bl/sp")], Http({"https://shop.lonvera.com/bl/sp": THIN}), render=boom, sleep=lambda s: None)
    assert st["failed"] == 0 and st["new_versions"] == 1 and conn.execute("SELECT rendered_in_browser FROM landing_page_versions").fetchone()[0] is False


def test_dry_run_and_staleness(conn):
    pid = add_page(conn)
    http = Http({"https://shop.lonvera.com/bl/sp": PAGE_V1})
    lp.fetch_pages(conn, [(pid, "https://shop.lonvera.com/bl/sp")], http, allow_render=False, write=False, sleep=lambda s: None)
    assert conn.execute("SELECT count(*) FROM landing_page_versions").fetchone()[0] == 0
    assert lp.pages_to_fetch(conn, 20) == [(pid, "https://shop.lonvera.com/bl/sp")]
    lp.fetch_pages(conn, [(pid, "https://shop.lonvera.com/bl/sp")], http, allow_render=False, sleep=lambda s: None)
    assert lp.pages_to_fetch(conn, 20) == [] and lp.pages_to_fetch(conn, 0) == [(pid, "https://shop.lonvera.com/bl/sp")]


def test_run_end_to_end_with_fakes(conn):
    seed_spend(conn, [("A1", 1, 100, 2, 200)])
    g = Graph({"A1": {"object_story_spec": {"link_data": {"link": "https://shop.lonvera.com/bl/sp?utm_source=fb"}}}})
    http = Http({"https://shop.lonvera.com/bl/sp": PAGE_V1})
    st = lp.run(conn, tok="t", graph_get=g, http_get=http, render=lambda u: PAGE_V1, sleep=lambda s: None)
    assert st["ads"]["creative"] == 1 and st["pages"]["new_versions"] == 1
    st2 = lp.run(conn, tok="t", graph_get=g, http_get=http, render=lambda u: PAGE_V1, sleep=lambda s: None)
    assert st2["ads"]["ads"] == 0 and st2["pages"]["pages"] == 0                          # fresh page, known ad: nothing to do


# ---------------------------------------------------------------- the view
def test_top_ads_view_ranks_by_spend_in_7_and_30_day_windows(conn):
    pid = add_page(conn)
    other = add_page(conn, "https://lonvera.com/pages/other")
    for ad in ("A1", "A2", "A3"):
        conn.execute("INSERT INTO landing_page_ads (ad_id, landing_page_id, raw_url, source) VALUES (%s,%s,'u','creative')", (ad, pid))
    conn.execute("INSERT INTO landing_page_ads (ad_id, landing_page_id, raw_url, source) VALUES ('A9',%s,'u','creative')", (other,))
    conn.execute("INSERT INTO meta_ads (ad_id, ad_name, campaign_id) VALUES ('A1','Ad one','c1')")
    conn.commit()
    seed_spend(conn, [("A1", 1, 50, 1, 100), ("A1", 20, 100, 2, 100), ("A2", 2, 80, 2, 240), ("A3", 25, 500, 0, 0), ("A9", 1, 5, 0, 0)])
    rows = conn.execute("SELECT window_days, rank_by_spend, ad_id, spend, purchases, revenue, roas, ad_name FROM landing_page_top_ads WHERE landing_page_id=%s ORDER BY window_days, rank_by_spend", (pid,)).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(7, 1, "A2"), (7, 2, "A1"), (30, 1, "A3"), (30, 2, "A1"), (30, 3, "A2")]
    a1_30 = rows[3]
    assert (float(a1_30[3]), float(a1_30[4]), float(a1_30[5]), float(a1_30[6]), a1_30[7]) == (150.0, 3.0, 200.0, 1.333, "Ad one")
    assert float(rows[0][6]) == 3.0
