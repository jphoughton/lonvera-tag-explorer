"""etl.competitor_landing_pages --own with the browser faked: own-LP URL normalisation and ranking by spend, is_own flag, ad_family_lp links, competitors untouched (is_own false)."""
import datetime as dt
import io

import pytest
from PIL import Image

from etl import competitor_landing_pages as lp


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE competitor_ads, competitor_brands, competitor_landing_pages, competitor_lp_media, competitor_ad_lp, ad_family_lp, landing_pages, landing_page_ads CASCADE")
    conn.commit()


def test_plan_own_ranks_by_spend_normalises_and_links_families():
    rows = [("fam-a", "https://www.lonvera.com/pages/try-bl?utm_source=fb&fbclid=1", 300.0), ("fam-b", "https://lonvera.com/pages/try-bl/", 100.0), ("fam-a", "https://lonvera.com/pages/try-vb", 50.0),
            ("fam-c", "https://lonvera.com/pages/try-vb", 500.0), ("fam-d", "https://facebook.com/post/1", 999.0), ("fam-e", "not a url", 9.0), ("fam-f", "https://lonvera.com/pages/tiny", 1.0)]
    lps, links, st = lp.plan_own_lps(rows, limit=2)
    assert [l["url_key"] for l in lps] == ["lonvera.com/pages/try-vb", "lonvera.com/pages/try-bl"]     # 550 > 400; tracking params and www dropped; trailing slash merged
    assert all(l["brand"] == "Lonvera" for l in lps) and lps[1]["ad_count"] == 2 and lps[1]["spend"] == 400.0
    by_key = {l["url_key"]: l["lp_id"] for l in lps}
    assert links == {"fam-a": by_key["lonvera.com/pages/try-bl"], "fam-b": by_key["lonvera.com/pages/try-bl"], "fam-c": by_key["lonvera.com/pages/try-vb"]}   # fam-a: most spend to try-bl
    assert st["distinct"] == 3 and st["dropped_over_cap"] == 1 and st["skipped"]["skipped_host_or_invalid"] == 2
    assert lp.lp_id_for("lonvera.com/pages/try-bl") == by_key["lonvera.com/pages/try-bl"]


def seed_own(conn):
    for ad, fam, url, spend, day in (("1", "fam-a", "https://lonvera.com/pages/try-bl", 80, 5), ("2", "fam-a", "https://lonvera.com/pages/try-vb", 20, 5), ("3", "fam-b", "https://lonvera.com/pages/try-vb", 70, 5),
                                      ("4", "fam-old", "https://lonvera.com/pages/old", 999, 200)):
        d = dt.date.today() - dt.timedelta(days=day)
        conn.execute("INSERT INTO meta_ads (ad_id, ad_name, meta_family_key) VALUES (%s,%s,%s)", (ad, fam, fam))
        conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES (%s,%s,%s,%s,%s,'meta_api')", (ad, d, fam, fam, spend))
        conn.execute("INSERT INTO landing_pages (url, host, path) VALUES (%s,'lonvera.com','/x') ON CONFLICT DO NOTHING", (url,))
        conn.execute("INSERT INTO landing_page_ads (ad_id, landing_page_id, raw_url, source) SELECT %s, id, %s, 'test' FROM landing_pages WHERE url=%s", (ad, url, url))
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key) VALUES ('fam-b','fam-b','tracker-b')")
    conn.commit()


class Browser:
    def __init__(self):
        self.opened = []

    def capture(self, url):
        self.opened.append(url)
        b = io.BytesIO()
        Image.new("RGB", (1280, 900), (20, 90, 200)).save(b, "PNG")
        return {"status": 200, "final_url": url, "shot": b.getvalue(), "raw": {"title": "Try Beef Liver", "h1": ["Feel like yourself"], "h2": [], "bullets": [], "buttons": ["Add to cart"],
                                                                                   "faq": [], "body": "Feel like yourself again. $49.99 per bottle. " * 8}}

    def close(self):
        pass


def test_own_run_flags_pages_links_families_and_leaves_competitors_alone(conn):
    seed_own(conn)
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme')")
    conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, landing_url, concept_key, seen_on) VALUES ('2026-11-09','c1','Acme','https://acme.com/p','k1','2026-11-09')")
    conn.commit()
    b = Browser()
    week = dt.date(2026, 11, 9)
    st = lp.run(conn, week=week, no_tag=True, browser=b, sleep=lambda s: None, log=lambda *a: None, own=True)
    assert st["own"] and st["captured"] == 2 and st["ads"] == 2 and st["lps"]["distinct"] == 2        # the 200-day-old ad is outside the 120-day window
    rows = {r[0]: r[1:] for r in conn.execute("SELECT url_key, brand, is_own, ad_count FROM competitor_landing_pages WHERE week_start=%s", (week,))}
    assert rows == {"lonvera.com/pages/try-vb": ("Lonvera", True, 2), "lonvera.com/pages/try-bl": ("Lonvera", True, 1)}
    links = dict(conn.execute("SELECT family_key, lp_id FROM ad_family_lp WHERE week_start=%s", (week,)).fetchall())
    assert set(links) == {"fam-a", "tracker-b"}
    assert links["fam-a"] == lp.lp_id_for("lonvera.com/pages/try-bl") and links["tracker-b"] == lp.lp_id_for("lonvera.com/pages/try-vb")
    assert conn.execute("SELECT count(*) FROM competitor_ad_lp").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM competitor_lp_media WHERE week_start=%s", (week,)).fetchone()[0] == 2
    # competitor run: is_own stays false, own pages are not touched
    st2 = lp.run(conn, week=week, no_tag=True, browser=Browser(), sleep=lambda s: None, log=lambda *a: None)
    assert conn.execute("SELECT is_own FROM competitor_landing_pages WHERE url_key='acme.com/p'").fetchone() == (False,)
    assert conn.execute("SELECT count(*) FROM competitor_landing_pages WHERE is_own").fetchone()[0] == 2 and not st2["own"]
    # rerun rewrites links for the week without duplicating
    lp.run(conn, week=week, no_tag=True, browser=Browser(), sleep=lambda s: None, log=lambda *a: None, own=True)
    assert conn.execute("SELECT count(*) FROM ad_family_lp").fetchone()[0] == 2


def test_own_dry_run_and_limit(conn):
    seed_own(conn)
    st = lp.run(conn, week=dt.date(2026, 11, 9), dry_run=True, own=True, limit=1, log=lambda *a: None)
    assert st["lps"]["kept"] == 1 and st["sample"][0]["brand"] == "Lonvera"
    assert conn.execute("SELECT count(*) FROM competitor_landing_pages").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM ad_family_lp").fetchone()[0] == 0
