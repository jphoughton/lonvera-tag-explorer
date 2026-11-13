"""etl.competitor_chatter with HTTP faked: no network. Parsing (Google News RSS, subreddit Atom), brand / category / spam filter, dedupe and idempotent upsert,
failure isolation per query / source, week_start (Monday Pacific), the 14-day window and the per-run cap."""
import datetime as dt

import pytest

from etl import competitor_chatter as cc

NOW = dt.datetime(2026, 11, 12, 20, 0, tzinfo=dt.timezone.utc)      # Thursday; week_start Monday 2026-11-09


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE competitor_posts, competitor_ads, competitor_concept_class, competitor_brands CASCADE")
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES "
                 "('Heart & Soil','beef organ',ARRAY['Heart & Soil'],'heart'),('My Marrow','marrow',ARRAY['My Marrow','mymarrow.com'],'my marrow')")
    conn.commit()


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate><source url='x'>{s}</source></item>" for t, u, d, s in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>q</title>{body}</channel></rss>"


def atom(entries):
    body = "".join(f"<entry><title>{t}</title><link href='{u}?ref=x'/><updated>{d}</updated><category term='{s}'/><content type='html'>{c}</content></entry>"
                   for t, u, d, s, c in entries)
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{body}</feed>"


def test_week_start_is_monday_pacific():
    # Monday 2026-11-09 03:00 UTC is still Sunday 2026-11-08 in Pacific: belongs to the week of 11-02
    assert cc.week_of(dt.datetime(2026, 11, 9, 3, 0, tzinfo=dt.timezone.utc)) == dt.date(2026, 11, 2)
    assert cc.week_of(dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.timezone.utc)) == dt.date(2026, 11, 9)
    assert cc.week_start_for(dt.date(2026, 11, 15)) == dt.date(2026, 11, 9)


def test_parse_news_splits_publisher_and_date():
    items = cc.parse_news(rss([("Heart &amp; Soil sued - The Law Review", "https://n/1", "Wed, 11 Nov 2026 10:00:00 GMT", "The Law Review")]))
    assert items[0]["title"] == "Heart & Soil sued"
    assert items[0]["summary"] == "The Law Review"
    assert items[0]["posted_at"] == dt.datetime(2026, 11, 11, 10, 0, tzinfo=dt.timezone.utc)


def test_parse_reddit_strips_html_and_tracking():
    items = cc.parse_reddit(atom([("Heart &amp; Soil review", "https://www.reddit.com/r/carnivore/comments/a/x/", "2026-11-11T10:00:00+00:00", "carnivore",
                                   "&lt;p&gt;Tried the capsules&lt;/p&gt; submitted by /u/bob [link] [comments]")]))
    it = items[0]
    assert it["title"] == "Heart & Soil review" and it["subreddit"] == "carnivore"
    assert it["url"] == "https://www.reddit.com/r/carnivore/comments/a/x/"
    assert it["summary"] == "Tried the capsules" and it["posted_at"].tzinfo is not None


def test_keep_filters_spam_and_offtopic():
    br = [{"brand": "Heart & Soil", "search_phrases": ["Heart & Soil"]}, {"brand": "My Marrow", "search_phrases": ["My Marrow", "mymarrow.com"]}]
    assert cc.keep("Is Heart and Soil worth it?", "", br) == (True, "Heart & Soil")
    assert cc.keep("My recovery", "best beef liver supplement for iron?", br) == (True, None)
    assert cc.keep("Best beef liver supplement casino bonus", "", br)[0] is False
    assert cc.keep("My heart and soul", "unrelated", br)[0] is False
    assert cc.keep("Random post", "nothing", br)[0] is False


def make_fetch(news=None, reddit=None, fail=()):
    calls = []

    def fetch(url):
        calls.append(url)
        for f in fail:
            if f in url:
                if isinstance(fail, dict):
                    raise fail[f]
                raise RuntimeError(f"boom {f}")
        if "news.google.com" in url:
            for k, v in (news or {}).items():
                if k in url:
                    return v
            return rss([])
        for k, v in (reddit or {}).items():
            if f"/r/{k}/" in url:
                return v
        return atom([])
    fetch.calls = calls
    return fetch


def test_run_end_to_end_idempotent_and_window(conn):
    old = "Mon, 20 Oct 2026 10:00:00 GMT"
    news = {"Heart+%26+Soil": rss([("Heart &amp; Soil expands - Fortune", "https://n/1", "Wed, 11 Nov 2026 10:00:00 GMT", "Fortune"),
                                   ("Heart &amp; Soil old - Fortune", "https://n/old", old, "Fortune"),
                                   ("Heart &amp; Soil expands - Fortune", "https://n/1", "Wed, 11 Nov 2026 10:00:00 GMT", "Fortune")])}
    reddit = {"carnivore": atom([("Heart and Soil vs Ancestral", "https://www.reddit.com/r/carnivore/comments/1/", "2026-11-11T10:00:00+00:00", "carnivore", "x"),
                                 ("Steak tips", "https://www.reddit.com/r/carnivore/comments/2/", "2026-11-11T10:00:00+00:00", "carnivore", "no mention")])}
    f = make_fetch(news, reddit)
    st = cc.run(conn, 14, False, fetch=f, sleep=lambda s: None, now=NOW, reddit_pause=0)
    rows = conn.execute("SELECT source, brand, title, subreddit, week_start FROM competitor_posts ORDER BY source, url").fetchall()
    assert [r[:2] for r in rows] == [("google_news", "Heart & Soil"), ("reddit", "Heart & Soil")]
    assert rows[0][4] == dt.date(2026, 11, 9) and rows[1][3] == "carnivore"
    assert st["kept"] == 2 and st["written"] == 2 and not st["failures"]
    cc.run(conn, 14, False, fetch=f, sleep=lambda s: None, now=NOW, reddit_pause=0)          # idempotent
    assert conn.execute("SELECT count(*) FROM competitor_posts").fetchone()[0] == 2


def test_dry_run_writes_nothing(conn):
    news = {"My+Marrow": rss([("My Marrow review - Blog", "https://n/2", "Wed, 11 Nov 2026 10:00:00 GMT", "Blog")])}
    st = cc.run(conn, 14, True, ["google_news"], fetch=make_fetch(news), sleep=lambda s: None, now=NOW)
    assert st["kept"] == 1 and "written" not in st
    assert conn.execute("SELECT count(*) FROM competitor_posts").fetchone()[0] == 0


def test_failures_are_isolated(conn):
    news = {"My+Marrow": rss([("My Marrow review - Blog", "https://n/2", "Wed, 11 Nov 2026 10:00:00 GMT", "Blog")])}
    f = make_fetch(news, fail=("Heart+%26+Soil",))                    # one brand query raises: the others still load
    st = cc.run(conn, 14, False, ["google_news"], fetch=f, sleep=lambda s: None, now=NOW)
    assert st["kept"] == 1 and len(st["failures"]) == 1 and st["failures"][0]["scope"] == "Heart & Soil"


def test_blocked_source_does_not_stop_other_source(conn):
    blocked = cc.SourceBlocked("HTTP 403 from www.reddit.com")
    news = {"My+Marrow": rss([("My Marrow review - Blog", "https://n/2", "Wed, 11 Nov 2026 10:00:00 GMT", "Blog")])}

    def fetch(url):
        if "reddit.com" in url:
            raise blocked
        return make_fetch(news)(url)
    st = cc.run(conn, 14, False, fetch=fetch, sleep=lambda s: None, now=NOW, reddit_pause=0)
    assert st["by_source"] == {"google_news": 1}
    assert [f["source"] for f in st["failures"]] == ["reddit"]              # stopped after the first blocked subreddit, not 8 failures


def test_cap_and_secret_scrub(conn):
    items = [{"source": "google_news", "brand": None, "title": f"Heart & Soil {i}", "url": f"https://n/{i}", "subreddit": None, "score": 0, "num_comments": 0,
              "summary": None, "posted_at": NOW - dt.timedelta(hours=i)} for i in range(10)]
    out = cc.finalize(items, [], NOW, 14, 3)
    assert [i["url"] for i in out] == ["https://n/0", "https://n/1", "https://n/2"]
    assert "sk-ant-oat01-abcdefghijklmnop" not in cc.db.scrub("err sk-ant-oat01-abcdefghijklmnop")
