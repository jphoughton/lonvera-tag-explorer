"""etl.competitor_landing_pages with the browser and `claude -p` faked: no network, no token. url_key / lp_id stability and tracking-param stripping, dedupe of one page across ads, the 200 cap,
per-LP failure isolation, wall / blank detection, content_hash change detection (clocks ignored) + change_note, JSON validation and the one retry, screenshot size cap, resumability."""
import datetime as dt
import io
import json
import subprocess

import pytest
from PIL import Image

from etl import auto_tag as at
from etl import competitor_ads as ca
from etl import competitor_landing_pages as lp

WEEK = dt.date(2026, 11, 9)
PRIOR = dt.date(2026, 11, 2)


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE competitor_ads, competitor_concept_class, competitor_brands, competitor_landing_pages, competitor_lp_media, competitor_ad_lp CASCADE")
    conn.commit()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))
    monkeypatch.setattr(lp, "BACKOFF", (0,))


def add_ad(conn, lid, brand, landing, week=WEEK):
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES (%s,'x',ARRAY[%s],%s) ON CONFLICT DO NOTHING", (brand, brand, brand.lower()))
    conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, landing_url, concept_key, seen_on) VALUES (%s,%s,%s,%s,%s,%s)", (week, lid, brand, landing, f"{brand}-{lid}", week))
    conn.commit()


def jpg(w=1280, h=900, noisy=False):
    im = Image.effect_noise((w, h), 90).convert("RGB") if noisy else Image.new("RGB", (w, h), (30, 90, 200))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def raw_page(title="Acme Organ Complex", h1=("Feel like yourself again",), body=None, extra=None):
    body = body or ("Feel like yourself again. " * 12 + "$49.99 per bottle. 60 day money back guarantee. 4.8 out of 5 stars from 12,431 reviews. Only 17 left in stock. Ends in 02:14:55")
    r = {"title": title, "h1": list(h1), "h2": ["Why it works", "What is inside"], "bullets": ["Grass-fed beef liver", "3000 mg per serving"], "buttons": ["Add to cart"], "faq": ["Is it safe?"], "body": body}
    r.update(extra or {})
    return r


class FakeBrowser:
    def __init__(self, pages):
        self.pages, self.opened = pages, []

    def capture(self, url):
        self.opened.append(url)
        for k, v in self.pages.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v
        raise lp.LPFailed("load failed: net::ERR_NAME_NOT_RESOLVED")

    def close(self):
        pass


def page(raw=None, status=200, final=None, shot=True):
    return {"status": status, "final_url": final or "https://x.example/p", "raw": raw or raw_page(), "shot": jpg() if shot else None}


class FakeClaude:
    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, cmd, **kw):
        self.calls.append(kw.get("input"))
        out = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(out):
            out = out(kw.get("input"))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": out, "usage": {}}), "")


def good(i, **kw):
    d = {"i": i, "page_type": "product page", "key_theme": "Organ-based supplement that restores energy in men over forty", "hero_headline": "Feel like yourself again",
         "selling_points": ["Grass-fed beef liver", "3000 mg per serving", "No fillers"], "offer_summary": "Buy 2 get 1 free, free shipping", "price_text": "$49.99 per bottle",
         "guarantee": "60 day money back", "proof_elements": ["reviews count or stars", "UGC or testimonials"], "urgency_devices": ["low stock", "countdown"], "objections": ["is it safe"],
         "cta": "Add to cart", "line": "L07", "cluster": None, "icp": None, "pain_point": None, "why_it_works": "Leads with the felt problem — then the dose", "confidence": "high"}
    d.update(kw)
    return d


def answers(prompt):
    n = prompt.count('"screenshot_file"')
    return json.dumps([good(i) for i in range(n)])


# ---- urls ----
def test_url_key_normalises_and_drops_tracking():
    a = lp.url_key("https://www.Shop.Example.com/Products/Organ-Complex/?utm_source=fb&fbclid=abc&gclid=1")
    assert a == lp.url_key("shop.example.com/products/organ-complex") == lp.url_key("http://shop.example.com/products/organ-complex/#top") == "shop.example.com/products/organ-complex"
    assert lp.url_key("shop.example.com/p?variant=123&utm_medium=x") == "shop.example.com/p?variant=123"
    assert lp.url_key("shop.example.com/p?variant=1") != lp.url_key("shop.example.com/p?variant=2") != lp.url_key("shop.example.com/p")
    assert lp.url_key("shop.example.com") == "shop.example.com"
    assert lp.lp_id_for(a) == lp.lp_id_for("shop.example.com/products/organ-complex") and len(lp.lp_id_for(a)) == 16 and int(lp.lp_id_for(a), 16) >= 0
    assert lp.lp_id_for(a) == __import__("hashlib").sha1(a.encode()).hexdigest()[:16]               # stable, spec'd id


@pytest.mark.parametrize("bad", ["", None, "facebook.com/ads", "https://www.instagram.com/x/", "youtu.be/abc", "mailto:a@b.com", "ftp://x.com/a", "localhost/x", "l.facebook.com/l.php?u=1"])
def test_url_key_skips_non_lp_and_non_http(bad):
    assert lp.url_key(bad) is None


def test_open_url_prefixes_https_and_keeps_only_page_selecting_params():
    assert lp.open_url("shop.example.com/products/x") == "https://shop.example.com/products/x"
    assert lp.open_url("http://shop.example.com/p?utm_source=fb&variant=9&fbclid=z#frag") == "https://shop.example.com/p?variant=9"
    assert lp.strip_tracking("https://shop.example.com/p/?utm_source=fb&fbclid=1#x") == "https://shop.example.com/p/"
    with pytest.raises(ValueError):
        lp.open_url("mailto:x@y.com")


def test_plan_dedupes_across_ads_attributes_brand_by_most_ads_and_caps():
    ads = [{"library_id": "1", "brand": "B", "landing_url": "shop.example.com/p?utm_source=a"}, {"library_id": "2", "brand": "A", "landing_url": "https://www.shop.example.com/p/"},
           {"library_id": "3", "brand": "A", "landing_url": "shop.example.com/p"}, {"library_id": "4", "brand": "C", "landing_url": "other.example.com/x"},
           {"library_id": "5", "brand": "C", "landing_url": None}, {"library_id": "6", "brand": "C", "landing_url": "facebook.com/x"}]
    lps, links, st = lp.plan_lps(ads)
    assert [(x["url_key"], x["brand"], x["ad_count"]) for x in lps] == [("shop.example.com/p", "A", 3), ("other.example.com/x", "C", 1)]
    assert links == {"1": lps[0]["lp_id"], "2": lps[0]["lp_id"], "3": lps[0]["lp_id"], "4": lps[1]["lp_id"]}
    assert st["skipped"] == {"no_landing_url": 1, "skipped_host_or_invalid": 1}
    many = [{"library_id": str(i), "brand": "B", "landing_url": f"s{i}.example.com/p"} for i in range(250)] + [{"library_id": "x", "brand": "B", "landing_url": "s0.example.com/p"}]
    lps, links, st = lp.plan_lps(many)
    assert len(lps) == 200 and st["dropped_over_cap"] == 50 and lps[0]["url_key"] == "s0.example.com/p" and len(links) == 201 and "x" in links


# ---- page judgement / key text / hash ----
def test_judge_page_walls_and_blank():
    assert lp.judge_page(403, raw_page()) == "blocked: HTTP 403"
    assert lp.judge_page(404, raw_page()).startswith("HTTP 404")
    assert lp.judge_page(200, raw_page(title="Just a moment...", body="Checking your browser before accessing")).startswith("blocked: bot challenge")
    assert lp.judge_page(200, raw_page(body="hi")).startswith("blank page")
    assert lp.judge_page(200, raw_page()) is None


def test_key_text_has_key_lines_and_is_capped():
    t = lp.key_text(raw_page(body="x " * 30000 + "$49.99 per bottle 60 day money back guarantee"))
    assert len(t) <= lp.TEXT_MAX and "## H1" in t and "Feel like yourself again" in t and "## BUTTONS" in t and "Add to cart" in t
    short = lp.key_text(raw_page())
    assert "$49.99" in short and "60 day money back guarantee" in short and "12,431 reviews" in short and "Is it safe?" in short


def test_content_hash_ignores_clocks_counters_and_body_drift_but_catches_real_change():
    base = lp.content_hash(raw_page())
    assert base == lp.content_hash(raw_page(body=raw_page()["body"].replace("02:14:55", "01:59:03").replace("17 left", "11 left")))
    assert base == lp.content_hash(raw_page(extra={"body": "a rotating testimonial " * 40 + "$49.99 per bottle. 60 day money back guarantee."}))
    assert base != lp.content_hash(raw_page(h1=("Lose weight in 7 days",)))
    assert base != lp.content_hash(raw_page(body=raw_page()["body"].replace("$49.99", "$39.99")))
    assert base != lp.content_hash(raw_page(body=raw_page()["body"].replace("60 day", "90 day")))


def test_encode_shot_caps_size():
    data, w, h = lp.encode_shot(jpg(noisy=True))
    assert w <= 640 and len(data) <= lp.SHOT_MAX_BYTES and Image.open(io.BytesIO(data)).format == "JPEG"
    data, w, h = lp.encode_shot(jpg())
    assert (w, h) == (640, 450) and len(data) < 20_000


# ---- tagging ----
CAT = [{"line": "L07", "product": "vita-bull", "cluster": "Low T", "icp": "Tired Dad", "pain": "Low energy"}]


def test_parse_batch_validates_maps_line_and_strips_dashes():
    a = lp.parse_batch(json.dumps([good(0), good(1, page_type="blog", key_theme="x"), good(2, line="NEW", cluster="Menopause", icp="Hot Flash Mom", pain_point="Night sweats"),
                                   good(3, line="OUT"), good(4, confidence="sure")]), CAT, 5)
    assert set(a) == {0, 2, 3}                                                         # bad page_type and bad confidence dropped
    assert (a[0]["cluster"], a[0]["icp"], a[0]["pain_point"]) == ("Low T", "Tired Dad", "Low energy")
    assert (a[2]["cluster"], a[2]["icp"]) == ("Menopause", "Hot Flash Mom") and a[3]["cluster"] is None
    assert "—" not in a[0]["why_it_works"] and a[0]["urgency_devices"] == ["low stock", "countdown"] and a[0]["proof_elements"] == ["reviews count or stars", "UGC or testimonials"]
    with pytest.raises(ValueError):
        lp.parse_batch("not json", CAT, 1)
    with pytest.raises(ValueError):
        lp.parse_batch("[]", CAT, 1)


def test_tag_batch_retries_only_the_missing_ones_once():
    items = [{"brand": "A", "ad_count": 2, "open": "a.example/p", "final_url": "https://a.example/p", "status": 200, "text": "t0", "shot": jpg()},
             {"brand": "B", "ad_count": 1, "open": "b.example/p", "final_url": "https://b.example/p", "status": 200, "text": "t1", "shot": None}]
    fake = FakeClaude([json.dumps([good(0)]), json.dumps([good(0, key_theme="second page theme")])])        # first call answers only page 0; the retry covers page 1 only
    got = lp.tag_batch(CAT, items, fake, lambda s: None)
    assert set(got) == {0, 1} and got[1]["key_theme"] == "second page theme" and len(fake.calls) == 2
    assert '"text": "t0"' not in fake.calls[1] and '"text": "t1"' in fake.calls[1]
    assert 'lp0.jpg' in fake.calls[0] and '"screenshot_file": null' in fake.calls[1]
    bad = FakeClaude(["garbage"])
    with pytest.raises(at.CallFailed):
        lp.tag_batch(CAT, items, bad, lambda s: None)
    assert len(bad.calls) == 2                                                          # one retry, no more


# ---- run ----
def seed(conn):
    add_ad(conn, "1", "Acme", "good.example.com/p?utm_source=fb")
    add_ad(conn, "2", "Acme", "https://www.good.example.com/p/")
    add_ad(conn, "3", "Beta", "walled.example.com/x")
    add_ad(conn, "4", "Beta", "dead.example.com/x")
    add_ad(conn, "5", "Beta", "blank.example.com/x")
    add_ad(conn, "6", "Beta", "facebook.com/somepage")
    add_ad(conn, "7", "Beta", None)


def pages():
    return {"good.example.com": page(final="https://good.example.com/p?utm_source=fb&fbclid=1"), "walled.example.com": page(raw_page(title="Just a moment...", body="Checking your browser"), status=403),
            "dead.example.com": lp.LPFailed("load failed: timeout"), "blank.example.com": page(raw_page(body="hi"), status=200)}


def run(conn, browser, claude, **kw):
    kw.setdefault("sleep", lambda s: None)
    return lp.run(conn, WEEK, browser=browser, runner=claude, log=lambda *a: None, **kw)


def test_run_end_to_end_isolates_failures_links_every_mapped_ad(conn, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_PAUSE", 0)
    seed(conn)
    br, cl = FakeBrowser(pages()), FakeClaude([answers])
    st = run(conn, br, cl, retag=False)
    assert (st["captured"], st["blocked"], st["failed"], st["tagged"]) == (1, 2, 1, 1) and st["lps"]["kept"] == 4 and st["linked_ads"] == 5
    assert br.opened[0].startswith("https://good.example.com/p") and "utm" not in br.opened[0]
    rows = {r[0]: r for r in conn.execute("SELECT url_key, http_status, fetch_error, page_type, ad_count, brand, final_url, method, tagged_at IS NOT NULL, changed_from_prior FROM competitor_landing_pages ORDER BY 1").fetchall()}
    g = rows["good.example.com/p"]
    assert g[1] == 200 and g[2] is None and g[3] == "product page" and g[4] == 2 and g[5] == "Acme" and g[6] == "https://good.example.com/p" and g[7] == "vision_text" and g[8] and not g[9]
    w = rows["walled.example.com/x"]
    assert w[1] == 403 and "blocked" in w[2] and w[3] is None and not w[8]
    assert rows["dead.example.com/x"][1] is None and "load failed" in rows["dead.example.com/x"][2]
    assert "blank" in rows["blank.example.com/x"][2]
    links = dict(conn.execute("SELECT library_id, lp_id FROM competitor_ad_lp WHERE week_start=%s", (WEEK,)).fetchall())
    assert set(links) == {"1", "2", "3", "4", "5"} and links["1"] == links["2"] == lp.lp_id_for("good.example.com/p")
    m = conn.execute("SELECT count(*), max(width), max(octet_length(bytes)) FROM competitor_lp_media").fetchone()
    assert m[0] == 1 and m[1] == 640 and m[2] <= 90_000                                # only the page that loaded has a screenshot
    # tagged fields round trip (arrays, taxonomy mapped from the catalog line L07 or left null when the catalog lacks it)
    t = conn.execute("SELECT selling_points, proof_elements, urgency_devices, guarantee, confidence, model FROM competitor_landing_pages WHERE url_key='good.example.com/p'").fetchone()
    assert t[0] == ["Grass-fed beef liver", "3000 mg per serving", "No fillers"] and t[3] == "60 day money back" and t[4] == "high" and t[5] == at.MODEL


def test_run_is_resumable_and_retag_redoes(conn, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_PAUSE", 0)
    seed(conn)
    run(conn, FakeBrowser(pages()), FakeClaude([answers]))
    br2, cl2 = FakeBrowser(pages()), FakeClaude([answers])
    st = run(conn, br2, cl2)
    assert st["skipped_done"] == 3 and len(br2.opened) == 1 and "dead.example.com" in br2.opened[0]       # tagged + blocked + blank skipped, the timeout is retried
    assert cl2.calls == []
    br3, cl3 = FakeBrowser(pages()), FakeClaude([answers])
    st = run(conn, br3, cl3, retag=True)
    assert st["skipped_done"] == 0 and len(br3.opened) == 4 and st["tagged"] == 1


def test_no_tag_captures_only_and_a_later_tagging_run_reopens(conn, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_PAUSE", 0)
    add_ad(conn, "1", "Acme", "good.example.com/p")
    cl = FakeClaude([answers])
    st = run(conn, FakeBrowser(pages()), cl, no_tag=True)
    assert st["captured"] == 1 and st["tagged"] == 0 and cl.calls == []
    assert conn.execute("SELECT tagged_at IS NULL, content_hash IS NOT NULL FROM competitor_landing_pages").fetchone() == (True, True)
    assert run(conn, FakeBrowser(pages()), cl, no_tag=True)["skipped_done"] == 1
    br = FakeBrowser(pages())
    st = run(conn, br, cl)
    assert len(br.opened) == 1 and st["tagged"] == 1


def test_week_over_week_change_flag_and_note(conn, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_PAUSE", 0)
    add_ad(conn, "1", "Acme", "good.example.com/p", week=PRIOR)
    add_ad(conn, "1", "Acme", "good.example.com/p", week=WEEK)
    # last week: capture + tag
    lp.run(conn, PRIOR, browser=FakeBrowser(pages()), runner=FakeClaude([answers]), sleep=lambda s: None, log=lambda *a: None)
    # this week, same page, only a clock moved: not changed
    same = pages()
    same["good.example.com"] = page(raw_page(body=raw_page()["body"].replace("02:14:55", "00:01:10")))
    st = run(conn, FakeBrowser(same), FakeClaude([answers]))
    assert st["changed"] == 0 and conn.execute("SELECT changed_from_prior, change_note FROM competitor_landing_pages WHERE week_start=%s", (WEEK,)).fetchone() == (False, None)
    # a real change: new headline and price; the model writes a note from old vs new tags
    conn.execute("DELETE FROM competitor_landing_pages WHERE week_start=%s", (WEEK,))
    conn.commit()
    new = pages()
    new["good.example.com"] = page(raw_page(h1=("Lose weight in 7 days",), body=raw_page()["body"].replace("$49.99", "$39.99")))
    cl = FakeClaude([lambda p: json.dumps([good(0, key_theme="New weight loss promise", hero_headline="Lose weight in 7 days", price_text="$39.99")]), json.dumps([{"i": 0, "note": "Headline now promises weight loss; price down to $39.99 — new offer"}])])
    st = run(conn, FakeBrowser(new), cl)
    assert st["changed"] == 1
    r = conn.execute("SELECT changed_from_prior, change_note, content_hash FROM competitor_landing_pages WHERE week_start=%s", (WEEK,)).fetchone()
    p = conn.execute("SELECT content_hash FROM competitor_landing_pages WHERE week_start=%s", (PRIOR,)).fetchone()
    assert r[0] is True and r[2] != p[0] and "$39.99" in r[1] and "—" not in r[1]
    assert "Feel like yourself again" in cl.calls[1] and "Lose weight in 7 days" in cl.calls[1]       # old vs new key lines went to the model
    # first ever week for a page is never 'changed'
    assert conn.execute("SELECT changed_from_prior FROM competitor_landing_pages WHERE week_start=%s", (PRIOR,)).fetchone() == (False,)


def test_claude_failure_keeps_captures_and_stop_run_halts(conn, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_PAUSE", 0)
    add_ad(conn, "1", "Acme", "good.example.com/p")
    st = run(conn, FakeBrowser(pages()), FakeClaude(["garbage"]))
    assert st["captured"] == 1 and st["tagged"] == 0 and st["tag_failed"] == 1
    assert conn.execute("SELECT fetched_at IS NOT NULL, tagged_at IS NULL FROM competitor_landing_pages").fetchone() == (True, True)

    def limit(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Claude usage limit reached")
    conn.execute("DELETE FROM competitor_landing_pages")
    conn.commit()
    st = run(conn, FakeBrowser(pages()), limit)
    assert st["stopped_early"] and "limit" in st["stopped_early"]


def test_dry_run_and_cap_and_brand_filter(conn):
    seed(conn)
    st = lp.run(conn, WEEK, dry_run=True, log=lambda *a: None)
    assert st["lps"]["kept"] == 4 and conn.execute("SELECT count(*) FROM competitor_landing_pages").fetchone()[0] == 0
    assert lp.run(conn, WEEK, brand="Acme", dry_run=True, log=lambda *a: None)["lps"]["kept"] == 1
    assert lp.run(conn, WEEK, limit=2, dry_run=True, log=lambda *a: None)["lps"]["kept"] == 2
    with pytest.raises(lp.db.JobFailed):
        lp.run(conn, dt.date(2020, 1, 6), dry_run=True, log=lambda *a: None)


def test_prune_media_keeps_latest_week(conn):
    for wk in (dt.date(2026, 6, 1), dt.date(2026, 11, 9)):
        conn.execute("INSERT INTO competitor_lp_media (week_start, lp_id, content_type, bytes) VALUES (%s,'aaaaaaaaaaaaaaaa','image/jpeg',%s)", (wk, b"x"))
    conn.commit()
    assert lp.prune_media(conn, WEEK) == 1
    assert [r[0] for r in conn.execute("SELECT week_start FROM competitor_lp_media").fetchall()] == [dt.date(2026, 11, 9)]
