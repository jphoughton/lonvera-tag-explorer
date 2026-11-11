"""Landing pages: which page each Meta ad sends people to, and the copy on every such page.

    python -m etl.landing_pages [--days 30] [--dry-run] [--no-ads] [--no-pages] [--refresh-ads] [--max-previews 150] [--max-pages N] [--stale-hours 20] [--no-render]

Step 1, ads to pages (read-only Meta Graph GET): for every ad with spend in the last --days days (public.ad_daily) that has not been looked at yet (or whose last look found no link
more than 14 days ago), read the creative's destination links in this order: object_story_spec (link_data / video_data / photo_data link or call to action link, carousel cards),
asset_feed_spec.link_urls, then, for ads that reuse an existing Page post (effective_object_story_id), the post's call to action link. Ads whose post Meta will not show us (our own Page
needs a permission the token lacks) fall back to the ad preview, which contains the link; at most --max-previews previews per run, biggest spenders first, one request at a time.
Links are normalised (https, lower-case host, tracking parameters and fragment removed, Facebook link shims unwrapped, trailing slash removed) and stored in landing_pages / landing_page_ads.
Step 2, page copy: every active page not fetched in the last --stale-hours hours is read with a plain public GET (one request per second, browser-like User-Agent, 20 second timeout) and parsed
with the standard library: title, meta description, ordered text blocks with heading level, button and link text, prices, guarantee sentences, review snippets and image alt text. A new row
in landing_page_versions is written only when the copy hash changed (the hash covers text only, not image addresses or scripts). Pages whose server HTML has almost no text (the page is
built in the browser) are read again with headless Chromium (Playwright, already a dependency for etl.competitor_ads) unless --no-render; rendered_in_browser says which.
One page or ad failing is never fatal: it is recorded (landing_pages.last_error, landing_page_ad_checks) and the run goes on. Errors are scrubbed with db.scrub. --dry-run reads and parses
but writes nothing. Not wired into scripts/deploy/run_etl.sh yet (see docs/LANDING_PAGES.md)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import sys
import time
import urllib.parse
from html.parser import HTMLParser

from . import db
from .meta_api import G

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
PAGE_PAUSE = 1.0
PREVIEW_UA = "Mozilla/5.0"
PAGE_TIMEOUT = 20
THIN_WORDS = 120                       # fewer words than this in the server HTML: the page is probably built in the browser
RECHECK_NO_LINK_DAYS = 14
MAX_BYTES = 3_000_000
SKIP_HOSTS = ("facebook.com", "instagram.com", "fb.com", "fb.me", "l.facebook.com")
TRACKING_PARAMS = {"fbclid", "gclid", "gbraid", "wbraid", "ttclid", "msclkid", "dclid", "igshid", "mc_cid", "mc_eid", "ref", "ad_id", "adset_id", "campaign_id", "placement",
                   "site_source_name", "campaign_name", "adset_name", "ad_name", "_ga", "_gl", "sscid", "fb_ref", "ndcid", "sid", "trybe"}


# ------------------------------------------------------------------------------------------------------------------ urls
def normalise_url(raw: str | None) -> tuple[str, str, str] | None:
    """(url, host, path) with https, lower-case host, no tracking parameters, no fragment, no trailing slash; None when raw is not a landing page address."""
    if not raw or not isinstance(raw, str):
        return None
    s = htmllib.unescape(raw.strip())
    if re.match(r"(?i)(mailto|tel|sms|javascript|data|blob|file):", s):
        return None
    if "{{" in s and "}}" not in s.split("{{", 1)[1]:
        return None
    for _ in range(2):                                     # unwrap Facebook link shims (l.facebook.com/l.php?u=...)
        p = urllib.parse.urlsplit(s if "//" in s else "//" + s, scheme="https")
        if p.hostname and p.hostname.lower() in ("l.facebook.com", "lm.facebook.com", "l.instagram.com") and p.path.startswith("/l.php"):
            u = urllib.parse.parse_qs(p.query).get("u")
            if u:
                s = urllib.parse.unquote(u[0])
                continue
        break
    p = urllib.parse.urlsplit(s if "//" in s else "//" + s, scheme="https")
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or "." not in host:
        return None
    if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
        return None
    path = urllib.parse.unquote(p.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False)
         if not k.lower().startswith(("utm_", "fb_", "hsa_")) and k.lower() not in TRACKING_PARAMS and "{{" not in v and "{{" not in k]
    q.sort()
    qs = urllib.parse.urlencode(q)
    full = f"https://{host}{urllib.parse.quote(path, safe='/%:@!$&()*+,;=-._~')}" + (f"?{qs}" if qs else "")
    return full, host, path + (f"?{qs}" if qs else "")


# ------------------------------------------------------------------------------------------------------------------ ads to pages
CREATIVE_FIELDS = "creative{id,object_story_spec,asset_feed_spec{link_urls},effective_object_story_id}"


def _link_from_cta(cta) -> list[str]:
    if isinstance(cta, dict):
        v = cta.get("value")
        if isinstance(v, dict) and v.get("link"):
            return [v["link"]]
    return []


def extract_creative_links(creative: dict | None) -> list[str]:
    """Every destination link a Graph creative object carries (raw, not yet normalised), in a fixed order, without duplicates."""
    out: list[str] = []
    if not isinstance(creative, dict):
        return out
    oss = creative.get("object_story_spec") or {}
    for key in ("link_data", "video_data", "photo_data", "text_data"):
        d = oss.get(key)
        if not isinstance(d, dict):
            continue
        if isinstance(d.get("link"), str):
            out.append(d["link"])
        out += _link_from_cta(d.get("call_to_action"))
        for card in d.get("child_attachments") or []:
            if isinstance(card, dict):
                if isinstance(card.get("link"), str):
                    out.append(card["link"])
                out += _link_from_cta(card.get("call_to_action"))
    for lu in (creative.get("asset_feed_spec") or {}).get("link_urls") or []:
        if isinstance(lu, dict):
            if isinstance(lu.get("website_url"), str):
                out.append(lu["website_url"])
    seen, res = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


def links_from_preview_html(text: str) -> list[str]:
    """Landing links inside an ad preview page. Only Facebook link shims (l.facebook.com/l.php?u=...) count: the rest of the page holds unrelated Facebook links (careers, video chat)."""
    found: list[str] = []
    for m in re.finditer(r"https?:(?:\\/\\/|//)(?:[^\"'\s<>\\]|\\/|\\u[0-9a-fA-F]{4})+", text):
        u = m.group(0).replace("\\/", "/")
        if "l.facebook.com/l.php" not in u and "l.instagram.com" not in u:
            continue
        u = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), u)
        n = normalise_url(htmllib.unescape(u))
        if n and n[0] not in found:
            found.append(n[0])
    return found


def ads_to_check(conn, days: int, refresh: bool = False) -> list[tuple[str, float]]:
    """(ad_id, spend over the window) for ads with spend in the last `days` days that were never looked at (or looked at with no link more than RECHECK_NO_LINK_DAYS ago), biggest first."""
    rows = conn.execute(
        """SELECT d.ad_id, sum(d.spend) AS spend FROM ad_daily d
           LEFT JOIN landing_page_ad_checks c ON c.ad_id = d.ad_id
           WHERE d.day > (now() AT TIME ZONE 'America/Los_Angeles')::date - %s AND d.spend > 0 AND d.ad_id NOT LIKE 'fam:%%'
             AND (%s OR c.ad_id IS NULL OR (c.result <> 'linked' AND c.checked_at < now() - make_interval(days => %s))
                  OR (c.result = 'linked' AND NOT EXISTS (SELECT 1 FROM landing_page_ads a WHERE a.ad_id = d.ad_id)))
           GROUP BY d.ad_id ORDER BY spend DESC""", (days, refresh, RECHECK_NO_LINK_DAYS)).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def fetch_creatives(tok, ad_ids: list[str], graph_get, batch: int = 50, pace: float = 0.4, sleep=time.sleep) -> dict[str, dict]:
    """{ad_id: {'links': [raw urls], 'story_id': str|None, 'error': str|None}} from the Graph; one failing batch is bisected so a bad id costs only itself."""
    out: dict[str, dict] = {}

    def one(chunk):
        try:
            j = graph_get(f"{G}/", {"ids": ",".join(chunk), "fields": CREATIVE_FIELDS, "access_token": tok}, "ad creative", max_retries=4)
        except Exception as e:                              # noqa: BLE001
            if "TokenRejected" in type(e).__name__:
                raise
            if len(chunk) > 1:
                mid = len(chunk) // 2
                one(chunk[:mid])
                one(chunk[mid:])
            else:
                out[chunk[0]] = {"links": [], "story_id": None, "error": db.scrub(e)[:160]}
            return
        for k, v in (j or {}).items():
            if isinstance(v, dict):
                c = v.get("creative") or {}
                out[str(k)] = {"links": extract_creative_links(c), "story_id": c.get("effective_object_story_id"), "error": None}

    for i in range(0, len(ad_ids), batch):
        one(ad_ids[i:i + batch])
        sleep(pace)
    return out


def fetch_post_links(tok, story_ids: list[str], graph_get, pace: float = 0.3, sleep=time.sleep) -> dict[str, list[str]]:
    """{story_id: [raw urls]} from each Page post's call to action. Posts of our own Page need a permission the token lacks (error 10): those are simply absent from the result."""
    out: dict[str, list[str]] = {}
    for sid in story_ids:
        try:
            j = graph_get(f"{G}/{sid}", {"fields": "call_to_action", "access_token": tok}, "post cta", max_retries=3)
        except Exception as e:                              # noqa: BLE001
            if "TokenRejected" in type(e).__name__ or "RateLimited" in type(e).__name__:
                raise
            sleep(pace)
            continue
        links = _link_from_cta((j or {}).get("call_to_action"))
        if links:
            out[sid] = links
        sleep(pace)
    return out


def fetch_preview_links(tok, ad_id: str, graph_get, http_get) -> list[str]:
    j = graph_get(f"{G}/{ad_id}/previews", {"ad_format": "MOBILE_FEED_STANDARD", "access_token": tok}, "ad preview", max_retries=3)
    body = ((j or {}).get("data") or [{}])[0].get("body") or ""
    m = re.search(r'src="([^"]+)"', body)
    if not m:
        return []
    r = http_get(htmllib.unescape(m.group(1)), headers={"User-Agent": PREVIEW_UA}, timeout=PAGE_TIMEOUT)      # a full Chrome User-Agent string gets HTTP 400 from the preview host
    return links_from_preview_html(r.text) if r.status_code == 200 else []


def resolve_ads(conn, tok, ads: list[tuple[str, float]], graph_get, http_get, max_previews: int, write: bool = True, sleep=time.sleep) -> dict:
    """Find each ad's landing pages and store them. Returns counts by source."""
    stats = {"ads": len(ads), "creative": 0, "post": 0, "preview": 0, "no_link": 0, "error": 0, "pages": 0, "left_for_next_run": 0}
    if not ads:
        return stats
    creatives = fetch_creatives(tok, [a for a, _ in ads], graph_get, sleep=sleep)
    result: dict[str, tuple[str, list[str], str | None]] = {}        # ad_id -> (source, raw links, detail)
    need_post: dict[str, str] = {}
    for ad_id, _ in ads:
        c = creatives.get(ad_id)
        if c is None:
            result[ad_id] = ("error", [], "not returned by Meta")
        elif c["links"] and any(normalise_url(u) for u in c["links"]):
            result[ad_id] = ("creative", c["links"], None)
        elif c["error"]:
            result[ad_id] = ("error", [], c["error"])
        elif c["story_id"]:
            need_post[ad_id] = c["story_id"]
        else:
            result[ad_id] = ("no_link", [], "creative has no link and no Page post")
    if need_post:
        post_links = fetch_post_links(tok, sorted(set(need_post.values())), graph_get, sleep=sleep)
        for ad_id, sid in need_post.items():
            if sid in post_links and any(normalise_url(u) for u in post_links[sid]):
                result[ad_id] = ("post", post_links[sid], None)
    store = _Store(conn, stats, write)
    for ad_id, (src, links, detail) in result.items():
        store.put(ad_id, src, links, detail)
    store.commit()                                                              # the cheap sources are saved before the slow previews start
    previews_used = 0
    for ad_id, _ in ads:                                                        # biggest spenders first
        if ad_id in result:
            continue
        if previews_used >= max_previews:
            stats["left_for_next_run"] += 1                                     # not recorded as checked: the next run tries it
            continue
        previews_used += 1
        try:
            links = fetch_preview_links(tok, ad_id, graph_get, http_get)
            store.put(ad_id, "preview" if links else "no_link", links, None if links else "preview shows no link")
        except Exception as e:                                                  # noqa: BLE001
            if "TokenRejected" in type(e).__name__ or "RateLimited" in type(e).__name__:
                stats["stopped"] = db.scrub(e)[:160]                            # stop quietly, keep what was found; unchecked ads are tried again next run
                break
            store.put(ad_id, "error", [], db.scrub(e)[:160])
        if previews_used % 25 == 0:
            store.commit()
        sleep(1.0)
    store.commit()
    return stats


class _Store:
    """Writes one ad's result: pages, ad to page rows and the check row. Counts by source in stats; writes nothing when write is False."""

    def __init__(self, conn, stats: dict, write: bool):
        self.conn, self.stats, self.write = conn, stats, write

    def commit(self):
        if self.write:
            self.conn.commit()

    def put(self, ad_id: str, src: str, links: list[str], detail: str | None):
        pages = {}
        for raw in links:
            n = normalise_url(raw)
            if n:
                pages.setdefault(n[0], (n, raw))
        if src in ("creative", "post", "preview") and pages:
            self.stats[src] += 1
        elif src == "error":
            self.stats["error"] += 1
        else:
            self.stats["no_link"] += 1
        if not self.write:
            return
        for url, ((u, host, path), raw) in pages.items():
            row = self.conn.execute(
                """INSERT INTO landing_pages (url, host, path) VALUES (%s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET last_seen_at = now() RETURNING id, (xmax = 0)""", (u, host, path)).fetchone()
            self.stats["pages"] += 1 if row[1] else 0
            self.conn.execute(
                """INSERT INTO landing_page_ads (ad_id, landing_page_id, raw_url, source) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (ad_id, landing_page_id) DO UPDATE SET last_seen_date = current_date, raw_url = EXCLUDED.raw_url, source = EXCLUDED.source""", (ad_id, row[0], raw[:1000], src))
        self.conn.execute(
            """INSERT INTO landing_page_ad_checks (ad_id, result, detail) VALUES (%s, %s, %s)
               ON CONFLICT (ad_id) DO UPDATE SET checked_at = now(), result = EXCLUDED.result, detail = EXCLUDED.detail""",
            (ad_id, "linked" if pages else ("error" if src == "error" else "no_link"), detail))


# ------------------------------------------------------------------------------------------------------------------ page parsing
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head", "iframe"}
BLOCK_TAGS = {"p", "li", "div", "section", "article", "header", "footer", "main", "aside", "blockquote", "ul", "ol", "tr", "td", "th", "table", "form", "figure", "figcaption",
              "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "nav", "label", "dt", "dd", "details", "summary", "button", "a"}
LABEL_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption", "button", "a", "dt", "dd", "summary", "td", "th"}
CTA_WORDS = re.compile(r"\b(add to cart|buy|order|get|claim|shop|try|checkout|check out|yes|start|continue|subscribe|learn more|see|join|save|complete|place|select|choose|upgrade|no thanks|take|redeem)\b", re.I)
CTA_CLASS = re.compile(r"btn|button|cta", re.I)
GUARANTEE = re.compile(r"guarantee|money[- ]?back|refund|risk[- ]free|satisfaction|return policy|cancel anytime|no questions asked", re.I)
PRICE = re.compile(r"(?<![\w.])(?:US)?\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")
REVIEWISH = re.compile(r"verified (?:buyer|purchase|customer)|[★⭐]{2,}|★|⭐|\d(?:\.\d)? ?(?:out of|/) ?5 stars?", re.I)


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.buf: list[str] = []
        self.label_stack: list[str] = []
        self.blocks: list[dict] = []
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.ctas: list[dict] = []
        self.images: list[dict] = []
        self.jsonld: list[str] = []
        self._ld = False
        self._cta_stack: list[dict] = []
        self.quote_depth = 0
        self.scripts = 0

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if t:
            tag = self.label_stack[-1] if self.label_stack else "text"
            level = int(tag[1]) if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit() else None
            if not (self.blocks and self.blocks[-1]["text"] == t and self.blocks[-1]["tag"] == tag):
                self.blocks.append({"i": len(self.blocks), "tag": tag, "level": level, "text": t[:2000], **({"quote": True} if self.quote_depth else {})})

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            self.scripts += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = True
        if tag in SKIP_TAGS and tag != "head":
            self.skip += 1
            return
        if tag == "head":
            return
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
        if tag == "img":
            alt = re.sub(r"\s+", " ", a.get("alt", "")).strip()
            if alt:
                self.images.append({"alt": alt[:300], "src": (a.get("src") or a.get("data-src") or "")[:300]})
        if tag == "input" and a.get("type", "").lower() in ("submit", "button") and a.get("value"):
            self.ctas.append({"text": a["value"].strip()[:100], "href": None, "tag": "input"})
        if self.skip:
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in LABEL_TAGS:
            self.label_stack.append(tag)
        if tag == "blockquote":
            self.quote_depth += 1
        if tag in ("a", "button") or a.get("role") == "button":
            self._cta_stack.append({"tag": tag, "href": a.get("href"), "cls": a.get("class", ""), "role": a.get("role", ""), "text": []})
        if tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "script":
            self._ld = False
        if tag in SKIP_TAGS and tag != "head":
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        if self.skip:
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in LABEL_TAGS and self.label_stack:
            for i in range(len(self.label_stack) - 1, -1, -1):
                if self.label_stack[i] == tag:
                    del self.label_stack[i:]
                    break
        if tag == "blockquote":
            self.quote_depth = max(0, self.quote_depth - 1)
        if tag in ("a", "button") and self._cta_stack:
            c = self._cta_stack.pop()
            text = re.sub(r"\s+", " ", "".join(c["text"])).strip()
            if text and len(text) <= 70 and (c["tag"] == "button" or c["role"] == "button" or CTA_CLASS.search(c["cls"]) or CTA_WORDS.search(text)):
                self.ctas.append({"text": text, "href": (c["href"] or None) and c["href"][:300], "tag": c["tag"]})

    def handle_data(self, data):
        if self._ld:
            self.jsonld.append(data)
            return
        if self.in_title:
            self.title += data
            return
        if self.skip:
            return
        self.buf.append(data)
        if self._cta_stack:
            self._cta_stack[-1]["text"].append(data)


def parse_page(markup: str) -> dict:
    """Copy of one HTML page: title, meta description, ordered text blocks (tag, heading level, text), button and link text, prices, guarantee sentences, review snippets, image alt text, content hash."""
    p = _Page()
    try:
        p.feed(markup)
        p.close()
    except Exception:                                                           # noqa: BLE001  (malformed markup: keep what was read)
        pass
    p._flush()
    blocks = p.blocks
    full = "\n".join(b["text"] for b in blocks)
    title = re.sub(r"\s+", " ", p.title).strip() or p.meta.get("og:title", "")
    desc = p.meta.get("description") or p.meta.get("og:description") or ""
    prices, seen = [], set()
    for b in blocks:
        for m in PRICE.finditer(b["text"]):
            key = (m.group(0), b["text"][:60])
            if key not in seen and len(prices) < 60:
                seen.add(key)
                a = max(0, m.start() - 50)
                prices.append({"price": re.sub(r"\s+", "", m.group(0)), "context": b["text"][a:m.end() + 40]})
    guarantees, gseen = [], set()
    for b in blocks:
        for s in re.split(r"(?<=[.!?])\s+", b["text"]):
            if GUARANTEE.search(s) and 12 <= len(s) <= 400 and s not in gseen and len(guarantees) < 20:
                gseen.add(s)
                guarantees.append(s)
    reviews, rseen = [], set()
    for n, b in enumerate(blocks):
        t = b["text"]
        if len(t) < 30 and not REVIEWISH.search(t):
            continue
        if b.get("quote") or REVIEWISH.search(t) or (t[:1] in "\"\u201c\u2018" and len(t) > 60):
            if len(t) < 80 and REVIEWISH.search(t) and not b.get("quote"):          # a label such as "Verified Purchase": the review text is the nearest long block around it
                near = [x["text"] for x in blocks[max(0, n - 2):n + 3] if x is not b and len(x["text"]) >= 40]
                if not near:
                    continue
                t = near[0]
            if t in rseen:
                continue
            rseen.add(t)
            if len(reviews) < 40:
                reviews.append(t[:600])
    for raw in p.jsonld:
        try:
            for node in _walk_jsonld(json.loads(raw)):
                if node.get("@type") in ("Review",) and node.get("reviewBody") and node["reviewBody"] not in rseen and len(reviews) < 40:
                    rseen.add(node["reviewBody"])
                    reviews.append(str(node["reviewBody"])[:600])
        except ValueError:
            continue
    ctas, cseen = [], set()
    for c in p.ctas:
        k = (c["text"].lower(), c["href"] or "")
        if k not in cseen and len(ctas) < 60:
            cseen.add(k)
            ctas.append(c)
    images, iseen = [], set()
    for im in p.images:
        if im["alt"] not in iseen and len(images) < 80:
            iseen.add(im["alt"])
            images.append(im)
    words = len(re.findall(r"\w+", full))
    digest_src = json.dumps([title, desc, [b["text"] for b in blocks], [c["text"] for c in ctas], [i["alt"] for i in images]], ensure_ascii=False, sort_keys=True)
    return {"title": title[:500], "meta_description": desc[:1000], "blocks": blocks, "ctas": ctas, "prices": prices, "guarantees": guarantees, "reviews": reviews, "images": images,
            "full_text": full, "word_count": words, "scripts": p.scripts, "content_hash": hashlib.sha256(digest_src.encode()).hexdigest()}


def _walk_jsonld(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_jsonld(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_jsonld(v)


def is_thin(parsed: dict) -> bool:
    return parsed["word_count"] < THIN_WORDS


# ------------------------------------------------------------------------------------------------------------------ page fetching
def fetch_html(http_get, url: str) -> tuple[int, str, str]:
    """(status, final url, text) for a plain public GET."""
    r = http_get(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}, timeout=PAGE_TIMEOUT, allow_redirects=True)
    raw = getattr(r, "content", None)
    text = ""
    if isinstance(raw, (bytes, bytearray)) and raw:                                # decode the bytes ourselves: requests guesses Latin-1 when the header names no charset
        try:
            text = bytes(raw[:MAX_BYTES * 2]).decode("utf-8")
        except UnicodeDecodeError:
            text = r.text or ""
    else:
        text = r.text or ""
    text = text[:MAX_BYTES]
    return r.status_code, str(getattr(r, "url", url)), text


def render_in_browser(url: str) -> str:
    """HTML of the page after scripts ran (headless Chromium through Playwright, as etl.competitor_ads). Raises when Playwright or its browser is missing."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            ctx = b.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            pg.goto(url, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(1500)
            return pg.content()
        finally:
            b.close()


def pages_to_fetch(conn, stale_hours: float, limit: int | None = None) -> list[tuple[int, str]]:
    sql = """SELECT id, url FROM landing_pages WHERE active AND (last_fetched_at IS NULL OR last_fetched_at < now() - make_interval(secs => %s))
             ORDER BY last_fetched_at NULLS FIRST, id"""
    rows = conn.execute(sql + (" LIMIT %s" if limit else ""), (stale_hours * 3600,) + ((limit,) if limit else ())).fetchall()
    return [(r[0], r[1]) for r in rows]


def save_version(conn, page_id: int, parsed: dict, status: int, final_url: str, rendered: bool) -> bool:
    """Insert a version when the copy hash is new for this page (returns True), and move the page's current version pointer to the row with this hash either way."""
    row = conn.execute(
        """INSERT INTO landing_page_versions (landing_page_id, content_hash, http_status, final_url, title, meta_description, rendered_in_browser, word_count, blocks, ctas, prices, guarantees, reviews, images, full_text)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (landing_page_id, content_hash) DO NOTHING RETURNING id""",
        (page_id, parsed["content_hash"], status, final_url, parsed["title"], parsed["meta_description"], rendered, parsed["word_count"], db.jsonb(parsed["blocks"]), db.jsonb(parsed["ctas"]),
         db.jsonb(parsed["prices"]), db.jsonb(parsed["guarantees"]), db.jsonb(parsed["reviews"]), db.jsonb(parsed["images"]), parsed["full_text"])).fetchone()
    vid = row[0] if row else conn.execute("SELECT id FROM landing_page_versions WHERE landing_page_id=%s AND content_hash=%s", (page_id, parsed["content_hash"])).fetchone()[0]
    conn.execute("UPDATE landing_pages SET current_version_id=%s, last_fetched_at=now(), last_status=%s, last_error=NULL WHERE id=%s", (vid, status, page_id))
    return row is not None


def fetch_pages(conn, pages: list[tuple[int, str]], http_get, render=render_in_browser, allow_render: bool = True, write: bool = True, sleep=time.sleep) -> dict:
    stats = {"pages": len(pages), "new_versions": 0, "unchanged": 0, "failed": 0, "rendered": 0, "thin": 0}
    for n, (pid, url) in enumerate(pages):
        if n:
            sleep(PAGE_PAUSE)
        try:
            status, final, text = fetch_html(http_get, url)
            if status >= 400 or not text.strip():
                raise RuntimeError(f"HTTP {status}" if status >= 400 else "empty response")
            parsed, rendered = parse_page(text), False
            if is_thin(parsed):
                stats["thin"] += 1
                if allow_render:
                    try:
                        p2 = parse_page(render(url))
                        if p2["word_count"] > parsed["word_count"]:
                            parsed, rendered = p2, True
                            stats["rendered"] += 1
                    except Exception as e:                                      # noqa: BLE001
                        db.log(f"  render failed for {url}: {db.scrub(e)[:160]} (keeping the server HTML)")
            if write:
                if save_version(conn, pid, parsed, status, final, rendered):
                    stats["new_versions"] += 1
                else:
                    stats["unchanged"] += 1
                conn.commit()
        except Exception as e:                                                  # noqa: BLE001  (one page never stops the run)
            stats["failed"] += 1
            conn.rollback()
            msg = db.scrub(f"{type(e).__name__}: {e}")[:300]
            db.log(f"  {url}: {msg}")
            if write:
                conn.execute("UPDATE landing_pages SET last_fetched_at=now(), last_error=%s WHERE id=%s", (msg, pid))
                conn.commit()
    return stats


# ------------------------------------------------------------------------------------------------------------------ run
def run(conn, days: int = 30, do_ads: bool = True, do_pages: bool = True, refresh_ads: bool = False, max_previews: int = 150, max_pages: int | None = None,
        stale_hours: float = 20, write: bool = True, allow_render: bool = True, tok=None, graph_get=None, http_get=None, render=render_in_browser, sleep=time.sleep) -> dict:
    import requests
    http_get = http_get or requests.get
    stats: dict = {}
    if do_ads:
        if graph_get is None:
            from . import meta_api
            graph_get = meta_api.graph_get
        if tok is None:
            from . import meta_api
            tok = meta_api.get_token()
        ads = ads_to_check(conn, days, refresh_ads)
        stats["ads"] = resolve_ads(conn, tok, ads, graph_get, http_get, max_previews, write=write, sleep=sleep)
    if do_pages:
        pages = pages_to_fetch(conn, stale_hours, max_pages)
        stats["pages"] = fetch_pages(conn, pages, http_get, render=render, allow_render=allow_render, write=write, sleep=sleep)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=30, help="ads with spend in this many days (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="read and parse, write nothing")
    ap.add_argument("--no-ads", action="store_true")
    ap.add_argument("--no-pages", action="store_true")
    ap.add_argument("--refresh-ads", action="store_true", help="look at every ad again, not only new ones")
    ap.add_argument("--max-previews", type=int, default=150)
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--stale-hours", type=float, default=20)
    ap.add_argument("--no-render", action="store_true", help="never fall back to headless Chromium for pages built in the browser")
    a = ap.parse_args(argv)
    try:
        with db.JobRun("landing_pages") as job, db.connect() as conn:
            stats = run(conn, a.days, not a.no_ads, not a.no_pages, a.refresh_ads, a.max_previews, a.max_pages, a.stale_hours, write=not a.dry_run, allow_render=not a.no_render)
            job.stats = {**stats, "dry_run": a.dry_run}
            job.rows_written = 0 if a.dry_run else (stats.get("pages", {}).get("new_versions", 0) + stats.get("ads", {}).get("pages", 0))
        db.log(json.dumps(stats))
        return 0
    except Exception as e:                                                      # noqa: BLE001
        print("landing_pages failed:", db.scrub(f"{type(e).__name__}: {e}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
