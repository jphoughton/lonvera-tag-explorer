"""Competitor / category chatter: what people and the press say about watched brands, from public sources that need no login and no paid scraper.

Sources (see docs/COMPETITOR_ADS.md, "Chatter sources", for what was tested and what is blocked):
  * google_news : Google News RSS, one query per active competitor_brands row (exact brand phrase) plus a small constant list of category terms.
  * reddit      : public RSS of a short list of subreddits (/r/<sub>/new/.rss). Reddit's own search (search.json / search.rss) is blocked (403) and the .json
                  endpoints return 403 to unauthenticated clients, so we read each subreddit's newest posts and keep only those that mention a brand or a
                  category term. Reddit allows about one request per minute per IP without OAuth, so requests are spaced REDDIT_PAUSE apart and a 429 stops
                  that source for the run (non-fatal). Reddit has historically blocked datacenter IPs: on Railway it may return 403 for every subreddit, in
                  which case the source is recorded as failed and the rest of the run still completes.
No model calls. Idempotent upsert into competitor_posts on (source, url). A failing source or brand is logged (db.scrub) and skipped, never fatal.
Usage: python -m etl.competitor_chatter [--days 14] [--dry-run] [--source google_news|reddit] [--max-items N]"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from . import db

PT = ZoneInfo("America/Los_Angeles")
UA = "lonvera-research/1.0 (competitor research; contact josh@lonvera.com)"
DAYS = 14
MAX_ITEMS = 400                  # hard cap of rows written per run
PAUSE = 1.5                      # seconds between requests to Google News
REDDIT_PAUSE = 65.0              # Reddit's unauthenticated budget is ~1 request per minute (x-ratelimit headers)
TIMEOUT = 25
MAX_NEWS_PER_QUERY = 25
SUMMARY_CHARS = 400

# Category terms searched in the news and matched in Reddit posts (small on purpose).
CATEGORY_TERMS = ["beef organ supplement", "beef liver supplement", "bone marrow supplement", "organ meat supplement", "desiccated beef liver", "ancestral supplement"]
# Subreddits whose newest posts are read (public RSS, one request each). Order = priority: the run stops early when the item cap or a rate limit is hit.
SUBREDDITS = ["carnivore", "Supplements", "Biohackers", "StackAdvice", "Nootropics", "ketoscience", "mensfitness", "Menopause"]

SPAM = re.compile(r"\b(casino|crypto|bitcoin|viagra|porn|onlyfans|escort|payday loan|forex|telegram|whatsapp|promo code|coupon code|discount code|buy now|click here)\b", re.I)


class SourceBlocked(Exception):
    """A whole source answered with a block / rate limit: stop that source for this run."""


# ---- pure helpers ---------------------------------------------------------------------------------------------------------------------

def week_start_for(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_of(ts: dt.datetime) -> dt.date:
    return week_start_for(ts.astimezone(PT).date())


def strip_html(s: str | None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("&", " and ")).strip()


def brand_terms(br: dict) -> list[str]:
    """Phrases that identify the brand in text: the brand name plus its search phrases that are not domains."""
    out = {norm(br["brand"])}
    for p in br.get("search_phrases") or []:
        if "." not in p:
            out.add(norm(p))
    return sorted(t for t in out if len(t) >= 4)


def match_brand(text: str, brands: list[dict]) -> str | None:
    n = " " + norm(text) + " "
    for br in brands:
        for t in brand_terms(br):
            if f" {t} " in n:
                return br["brand"]
    return None


def match_category(text: str) -> bool:
    n = " " + norm(text) + " "
    return any(f" {norm(t)} " in n for t in CATEGORY_TERMS)


def keep(title: str, body: str, brands: list[dict]) -> tuple[bool, str | None]:
    """(keep?, brand): spam is dropped; the item must mention a brand or a category term."""
    text = f"{title} {body}"
    if not title.strip() or SPAM.search(text):
        return False, None
    b = match_brand(text, brands)
    return (b is not None or match_category(text)), b


def parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)                       # RSS pubDate
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))    # Atom updated
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse_news(xml_text: str) -> list[dict]:
    """Google News RSS -> items {title, url, posted_at, summary, source_name}. Title 'Headline - Publisher' is split."""
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = strip_html(it.findtext("title"))
        pub = strip_html(it.findtext("source")) or None
        if pub and title.endswith(" - " + pub):
            title = title[: -len(pub) - 3]
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        out.append({"title": title, "url": link, "posted_at": parse_dt(it.findtext("pubDate")), "summary": pub, "subreddit": None, "score": 0, "num_comments": 0})
    return out


def parse_reddit(xml_text: str) -> list[dict]:
    """Subreddit Atom feed -> items. RSS carries no score or comment count (left 0)."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(xml_text).findall("a:entry", ns):
        title = strip_html(e.findtext("a:title", namespaces=ns))
        link_el = e.find("a:link", ns)
        link = (link_el.get("href") if link_el is not None else "") or ""
        cat = e.find("a:category", ns)
        body = strip_html(e.findtext("a:content", namespaces=ns))
        body = re.sub(r"\s*submitted by /u/\S+\s*\[link\]\s*\[comments\]\s*$", "", body).strip()
        if not title or not link:
            continue
        out.append({"title": title, "url": link.split("?")[0], "posted_at": parse_dt(e.findtext("a:updated", namespaces=ns) or e.findtext("a:published", namespaces=ns)),
                    "summary": body[:SUMMARY_CHARS] or None, "subreddit": cat.get("term") if cat is not None else None, "score": 0, "num_comments": 0, "_body": body})
    return out


# ---- HTTP -----------------------------------------------------------------------------------------------------------------------------

def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise SourceBlocked(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from e
        raise


def news_url(q: str, days: int) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f'"{q}" when:{days}d', "hl": "en-US", "gl": "US", "ceid": "US:en"})


def reddit_url(sub: str) -> str:
    return f"https://www.reddit.com/r/{sub}/new/.rss?limit=100"


# ---- collection -----------------------------------------------------------------------------------------------------------------------

def collect_news(brands: list[dict], days: int, fetch=http_get, sleep=time.sleep, log=db.log, st: dict | None = None) -> list[dict]:
    items: list[dict] = []
    queries = [(br["brand"], [p for p in (br.get("search_phrases") or []) if "." not in p][:1] or [br["brand"]]) for br in brands]
    queries = [(b, p[0]) for b, p in queries] + [(None, t) for t in CATEGORY_TERMS]
    for n, (brand, phrase) in enumerate(queries):
        if n:
            sleep(PAUSE)
        try:
            got = parse_news(fetch(news_url(phrase, days)))[:MAX_NEWS_PER_QUERY]
        except SourceBlocked as e:
            log(f"  competitor_chatter google_news: blocked ({db.scrub(e)}), source stopped")
            if st is not None:
                st.setdefault("failures", []).append({"source": "google_news", "scope": phrase, "error": db.scrub(e)[:200]})
            break
        except Exception as e:                                          # noqa: BLE001
            log(f"  competitor_chatter google_news {phrase!r}: FAILED ({db.scrub(e)[:200]})")
            if st is not None:
                st.setdefault("failures", []).append({"source": "google_news", "scope": phrase, "error": db.scrub(e)[:200]})
            continue
        for g in got:
            g["source"] = "google_news"
            g["brand"] = brand or match_brand(g["title"], brands)
            items.append(g)
        if st is not None:
            st.setdefault("queries", {})[phrase] = len(got)
    return items


def collect_reddit(brands: list[dict], fetch=http_get, sleep=time.sleep, log=db.log, st: dict | None = None, pause: float | None = None) -> list[dict]:
    items: list[dict] = []
    gap = REDDIT_PAUSE if pause is None else pause
    for n, sub in enumerate(SUBREDDITS):
        if n:
            sleep(gap)
        try:
            got = parse_reddit(fetch(reddit_url(sub)))
        except SourceBlocked as e:
            log(f"  competitor_chatter reddit r/{sub}: blocked ({db.scrub(e)}), source stopped")
            if st is not None:
                st.setdefault("failures", []).append({"source": "reddit", "scope": sub, "error": db.scrub(e)[:200]})
            break
        except Exception as e:                                          # noqa: BLE001
            log(f"  competitor_chatter reddit r/{sub}: FAILED ({db.scrub(e)[:200]})")
            if st is not None:
                st.setdefault("failures", []).append({"source": "reddit", "scope": sub, "error": db.scrub(e)[:200]})
            continue
        for g in got:
            ok, b = keep(g["title"], g.pop("_body", ""), brands)
            if ok:
                g["source"], g["brand"] = "reddit", b
                items.append(g)
        if st is not None:
            st.setdefault("subreddits", {})[sub] = len(got)
    return items


SOURCES = {"google_news": lambda brands, days, **k: collect_news(brands, days, **k), "reddit": lambda brands, days, **k: collect_reddit(brands, **k)}


def finalize(items: list[dict], brands: list[dict], now: dt.datetime, days: int, cap: int) -> list[dict]:
    """Last `days` days only, spam out, must mention a brand or category term (news already matched by query, so only spam + age are re-checked there),
    de-duplicated on (source, url), newest first, capped."""
    cutoff = now - dt.timedelta(days=days)
    seen: set[tuple[str, str]] = set()
    out = []
    for it in sorted(items, key=lambda x: x["posted_at"] or cutoff, reverse=True):
        if not it["posted_at"] or it["posted_at"] < cutoff or it["posted_at"] > now + dt.timedelta(hours=6):
            continue
        k = (it["source"], it["url"])
        if k in seen or SPAM.search(it["title"]):
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= cap:
            break
    return out


def store(conn, items: list[dict], today: dt.date) -> int:
    n = 0
    for it in items:
        conn.execute("""INSERT INTO competitor_posts (week_start, source, brand, title, url, subreddit, score, num_comments, posted_at, summary, seen_on)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (source, url) DO UPDATE SET title=EXCLUDED.title, brand=coalesce(EXCLUDED.brand, competitor_posts.brand), score=EXCLUDED.score,
                          num_comments=EXCLUDED.num_comments, summary=coalesce(EXCLUDED.summary, competitor_posts.summary), seen_on=EXCLUDED.seen_on""",
                     (week_of(it["posted_at"]), it["source"], it["brand"], it["title"][:500], it["url"], it["subreddit"], it["score"], it["num_comments"],
                      it["posted_at"], it["summary"], today))
        n += 1
    return n


def run(conn, days: int = DAYS, dry_run: bool = False, sources: list[str] | None = None, max_items: int = MAX_ITEMS, fetch=http_get, sleep=time.sleep,
        now: dt.datetime | None = None, log=db.log, reddit_pause: float | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(PT).date()
    rows = conn.execute("SELECT brand, search_phrases FROM competitor_brands WHERE active ORDER BY brand").fetchall()
    brands = [{"brand": r[0], "search_phrases": list(r[1] or [])} for r in rows]
    st: dict = {"dry_run": dry_run, "brands": len(brands), "sources": {}}
    items: list[dict] = []
    for name in sources or list(SOURCES):
        sub: dict = {}
        try:
            if name == "reddit":
                got = collect_reddit(brands, fetch=fetch, sleep=sleep, log=log, st=sub, pause=reddit_pause)
            else:
                got = collect_news(brands, days, fetch=fetch, sleep=sleep, log=log, st=sub)
        except Exception as e:                                          # noqa: BLE001  (a source never takes the run down)
            log(f"  competitor_chatter {name}: FAILED ({db.scrub(e)[:200]})")
            sub["failures"] = sub.get("failures", []) + [{"source": name, "scope": "*", "error": db.scrub(e)[:200]}]
            got = []
        sub["raw"] = len(got)
        st["sources"][name] = sub
        items += got
    final = finalize(items, brands, now, days, max_items)
    st["kept"] = len(final)
    st["by_source"] = {s: sum(1 for i in final if i["source"] == s) for s in {i["source"] for i in final}}
    st["by_brand"] = {b: sum(1 for i in final if i["brand"] == b) for b in sorted({i["brand"] for i in final if i["brand"]})}
    st["failures"] = [f for s in st["sources"].values() for f in s.get("failures", [])]
    if not dry_run and final:
        st["written"] = store(conn, final, today)
        conn.commit()
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DAYS, help="keep items posted in the last N days (default 14)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and count; no writes")
    ap.add_argument("--source", choices=sorted(SOURCES), action="append", help="limit to a source (repeatable)")
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS)
    a = ap.parse_args()
    with db.JobRun("competitor_chatter") as job, db.connect() as conn:
        st = run(conn, a.days, a.dry_run, a.source, a.max_items)
        job.stats = st
        job.rows_written = st.get("written", 0)
        db.log("  " + json.dumps(st, default=str))
        if st["failures"] and not st["kept"]:
            raise db.JobFailed("competitor_chatter: nothing collected: " + "; ".join(f"{f['source']}/{f['scope']}: {f['error']}" for f in st["failures"][:3]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                              # noqa: BLE001
        db.log("competitor_chatter FAILED:", db.scrub(e))
        sys.exit(1)
