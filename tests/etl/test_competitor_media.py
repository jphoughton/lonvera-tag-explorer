"""Competitor ad thumbnails in etl.competitor_ads, all mocked (no network): card -> media url, selection (top 12 + new 8, one per concept, cap, skip stored), JPEG size cap, per-image failure
isolation, the run() hook leaving ad rows alone, prune, and --media-backfill (selection from stored rows, resumable, competitor_ads untouched)."""
import datetime as dt
import io

import pytest
from PIL import Image

from etl import competitor_ads as ca

TODAY = dt.date(2026, 11, 8)
WEEK = dt.date(2026, 11, 2)


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE competitor_ads, competitor_concept_class, competitor_brands, competitor_ad_media CASCADE")
    conn.commit()
    monkeypatch.setattr(ca, "BRAND_PAUSE", 0)
    monkeypatch.setattr(ca, "MEDIA_PAUSE", 0)


def png(w=900, h=600, noisy=False):
    im = Image.effect_noise((w, h), 80).convert("RGB") if noisy else Image.new("RGB", (w, h), (200, 30, 30))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def ad(lid, brand="Acme", concept=None, days=10, start=None, url="https://scontent.fbcdn.net/x.jpg", kind="image"):
    return {"brand": brand, "library_id": str(lid), "concept_key": concept or f"c{lid}", "days_running": days,
            "start_date": start if start is not None else TODAY - dt.timedelta(days=days), "media_url": url, "media_kind": kind}


# ---- card -> url ----
def test_pick_media_prefers_video_poster_and_skips_avatar():
    assert ca.pick_media({"imgs": [{"src": "https://a/av.jpg", "w": 60}, {"src": "https://a/cr.jpg", "w": 600}], "vids": 0}) == ("https://a/cr.jpg", "image")
    assert ca.pick_media({"imgs": [{"src": "https://a/cr.jpg", "w": 600}], "posters": ["https://a/p.jpg"], "vids": 1}) == ("https://a/p.jpg", "video_poster")
    assert ca.pick_media({"imgs": [{"src": "https://a/av.jpg", "w": 60}], "vids": 0}) == (None, None)
    assert ca.pick_media({}) == (None, None)


# ---- selection ----
def test_select_top_12_by_days_one_per_concept_plus_new():
    old = [ad(i, days=100 + i, concept=f"c{i // 2}") for i in range(40)]            # 20 concepts, two ads each
    sel = ca.select_media(old, set(), TODAY)
    assert len(sel) == 12 and len({a["concept_key"] for a in sel}) == 12
    assert sel[0]["days_running"] == 139 and [a["days_running"] for a in sel][:2] == [139, 137]    # longest first, the longer ad of each concept
    fresh = [ad(1000 + i, days=i % 5, concept=f"n{i}") for i in range(12)]            # 12 new concepts started within 7 days
    sel = ca.select_media(old + fresh, set(), TODAY)
    assert len(sel) == 12 + 8 and sum(1 for a in sel if a["library_id"].startswith("10")) == 8
    dup = ca.select_media([ad(1, days=300, concept="same"), ad(2, days=3, concept="same")], set(), TODAY)
    assert [a["library_id"] for a in dup] == ["1"]                                     # a new ad of an already-picked concept adds nothing


def test_select_skips_stored_after_choosing_and_caps_across_brands():
    rows = [ad(i, days=500 - i, concept=f"c{i}") for i in range(14)]
    sel = ca.select_media(rows, {"0", "1"}, TODAY)
    assert [a["library_id"] for a in sel] == [str(i) for i in range(2, 12)]            # 12 chosen, 2 already stored dropped: no deeper ad pulled in
    many = [ad(b * 100 + i, brand=f"B{b}", days=300 - i, concept=f"b{b}c{i}") for b in range(5) for i in range(12)]
    sel = ca.select_media(many, set(), TODAY, cap=10)
    assert len(sel) == 10 and {a["brand"] for a in sel} == {f"B{b}" for b in range(5)}  # cap trims every brand's tail alike: two ranks of five brands
    assert ca.select_media(many, set(), TODAY, cap=0) == []


# ---- encode ----
def test_encode_downscales_and_enforces_size_cap():
    data, w, h = ca.encode_jpeg(png(1200, 800))
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG" and w == 480 == im.width and h == 320 and len(data) <= 150_000
    small, w2, _ = ca.encode_jpeg(png(200, 200))
    assert w2 == 200                                                                    # never enlarged
    big, w3, _ = ca.encode_jpeg(png(1600, 1600, noisy=True))                           # noise does not compress: quality/size must come down to fit
    assert len(big) <= 150_000 and w3 <= 480
    with pytest.raises(ValueError):
        ca.encode_jpeg(png(1600, 1600, noisy=True), max_bytes=500)
    with pytest.raises(ValueError):
        ca.encode_jpeg(b"<html>not an image</html>")


# ---- capture: isolation ----
def test_capture_counts_failures_and_stores_the_rest(conn):
    def fetch(url):
        if "bad" in url:
            raise OSError("boom")
        if "junk" in url:
            return b"junk"
        return png()
    picks = [ad(1), ad(2, url="https://x.fbcdn.net/bad"), ad(3, url="https://x.fbcdn.net/junk"), ad(4, url=None, kind=None), ad(5, kind="video_poster")]
    st = ca.new_media_stats()
    logs = []
    ca.capture_media(conn, picks, st, fetch, lambda s: None, logs.append)
    assert (st["selected"], st["stored"], st["failed"], st["no_image"]) == (5, 2, 2, 1)
    rows = dict(conn.execute("SELECT library_id, kind FROM competitor_ad_media").fetchall())
    assert rows == {"1": "image", "5": "video_poster"}
    assert any("boom" in l for l in logs)


# ---- run() hook ----
def card(lib, adv, start, text, img):
    lines = ["​", "Active", f"Library ID: {lib}", f"Started running on {start} · Total active time 30 days", "Platforms", "​", "Open Dropdown", "See ad details", adv, "Sponsored",
             *text.split("\n"), "SHOP.EXAMPLE.COM", "Shop the blend", "Shop now"]
    return {"text": "\n".join(lines), "hrefs": [], "vids": 0, "imgs": [{"src": img, "w": 600, "h": 600}] if img else [], "posters": []}


class FakeBrowser:
    def __init__(self, cards, one=None):
        self.cards, self.one, self.loaded = cards, one or {}, []

    def load(self, url, max_cards=150):
        return self.cards, ""

    def load_one(self, lib):
        self.loaded.append(lib)
        r = self.one.get(lib)
        if isinstance(r, Exception):
            raise r
        return r or []


def test_run_stores_media_and_a_media_crash_never_fails_the_ad_rows(conn):
    cards = [card("111", "Acme Co", "Oct 1, 2026", "Old organ copy", "https://scontent.fbcdn.net/a.jpg"), card("222", "Acme Co", "Nov 6, 2026", "Fresh copy here", "https://scontent.fbcdn.net/b.jpg")]
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme')")
    conn.commit()
    calls = []
    def fetch(u):
        calls.append(u)
        return png()
    # the seeded BRANDS list is added by ensure_brands: restrict to Acme
    st = ca.run(conn, "Acme", dry_run=False, week=WEEK, do_classify=False, browser=FakeBrowser(cards), sleep=lambda s: None, today=TODAY, fetch=fetch)
    assert st["ads"] == 2 and st["media"]["stored"] == 2 and st["media"]["failed"] == 0 and len(calls) == 2
    assert conn.execute("SELECT count(*) FROM competitor_ad_media").fetchone()[0] == 2
    # second run: nothing re-downloaded
    calls.clear()
    st = ca.run(conn, "Acme", week=WEEK, do_classify=False, browser=FakeBrowser(cards), sleep=lambda s: None, today=TODAY, fetch=fetch)
    assert calls == [] and st["media"]["stored"] == 0
    # a crash inside the media step: ad rows still written and committed
    conn.execute("TRUNCATE competitor_ad_media")
    conn.commit()
    def explode(u):
        raise RuntimeError("fetch exploded")
    def boom(*a, **k):
        raise RuntimeError("selection exploded")
    mp = pytest.MonkeyPatch()
    mp.setattr(ca, "select_media", boom)
    try:
        st = ca.run(conn, "Acme", week=WEEK, do_classify=False, browser=FakeBrowser(cards), sleep=lambda s: None, today=TODAY, fetch=explode)
    finally:
        mp.undo()
    assert st["ads"] == 2 and not st["failed_brands"]
    assert conn.execute("SELECT count(*) FROM competitor_ads WHERE week_start=%s AND brand='Acme'", (WEEK,)).fetchone()[0] == 2
    # --no-media equivalent
    st = ca.run(conn, "Acme", week=WEEK, do_classify=False, browser=FakeBrowser(cards), sleep=lambda s: None, today=TODAY, fetch=explode, do_media=False)
    assert "media" not in st


def test_prune_keeps_latest_week_and_recent_rows(conn):
    for lid in ("1", "2", "3"):
        ca.store_media(conn, lid, "image", b"x", 1, 1)
    conn.execute("UPDATE competitor_ad_media SET fetched_at = now() - interval '90 days' WHERE library_id IN ('1','2')")
    brand(conn)
    conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, seen_on, concept_key, format) VALUES (%s,'1','Acme',%s,'k','image')", (WEEK, TODAY))
    conn.commit()
    assert ca.prune_media(conn) == 1                                                    # '2' is old and not in the latest week; '1' is in it; '3' is fresh
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_media").fetchall()} == {"1", "3"}


# ---- backfill ----
def brand(conn):
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme') ON CONFLICT DO NOTHING")
    conn.commit()


def seed_rows(conn, n=15):
    brand(conn)
    for i in range(n):
        conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, concept_key, format, seen_on) VALUES (%s,%s,'Acme',%s,%s,%s,'image',%s)",
                     (WEEK, str(900 + i), TODAY - dt.timedelta(days=200 - i), 200 - i, f"k{i}", TODAY))
    conn.commit()


def test_backfill_selects_from_stored_rows_is_resumable_and_leaves_ads_untouched(conn):
    seed_rows(conn)
    before = conn.execute("SELECT * FROM competitor_ads ORDER BY library_id").fetchall()
    one = {str(900 + i): [card(str(900 + i), "Acme Co", "Oct 1, 2026", "copy", f"https://scontent.fbcdn.net/{i}.jpg")] for i in range(15)}
    one["903"] = []                                                                      # gone from the library
    one["904"] = RuntimeError("wall")                                                    # page error
    one["905"] = [card("905", "Acme Co", "Oct 1, 2026", "copy", None)]                   # card without an image
    br = ca.BrandFailed
    one["904"] = br("browser error: wall")
    b = FakeBrowser([], one)
    logs = []
    st = ca.backfill_media(conn, b, today=TODAY, fetch=lambda u: png(), sleep=lambda s: None, log=logs.append, progress_every=5)
    assert len(b.loaded) == 12                                                           # curated: 12 longest-running concepts of 15, not all 15
    assert (st["stored"], st["gone"], st["failed"], st["no_image"]) == (9, 1, 1, 1)
    assert st["selected"] == 12
    assert sum("backfill:" in l and "/12" in l for l in logs) >= 2                       # progress lines
    assert conn.execute("SELECT * FROM competitor_ads ORDER BY library_id").fetchall() == before     # competitor_ads untouched
    # resumable: stored ones skipped; only the three unstored of the same selection are retried
    b2 = FakeBrowser([], {k: v for k, v in one.items()})
    st2 = ca.backfill_media(conn, b2, today=TODAY, fetch=lambda u: png(), sleep=lambda s: None, log=lambda *_: None)
    assert sorted(b2.loaded) == ["903", "904", "905"] and st2["stored"] == 0
    assert ca.backfill_media(conn, FakeBrowser([], {}), brand="Nobody", today=TODAY, fetch=lambda u: png(), sleep=lambda s: None, log=lambda *_: None)["selected"] == 0


def test_backfill_with_no_stored_week_is_a_noop(conn):
    st = ca.backfill_media(conn, FakeBrowser([], {}), today=TODAY, fetch=lambda u: png(), sleep=lambda s: None, log=lambda *_: None)
    assert st["week_start"] is None and st["stored"] == 0


def test_select_derives_days_from_start_date_when_rows_have_none():
    rows = [{"brand": "A", "library_id": str(i), "concept_key": f"c{i}", "start_date": TODAY - dt.timedelta(days=i * 10)} for i in range(1, 16)]
    sel = ca.select_media(rows, set(), TODAY)
    assert [a["library_id"] for a in sel][:3] == ["15", "14", "13"] and len(sel) == 12
