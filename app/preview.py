"""Ad previews: a Graph API call made on the server, so the browser only ever receives a validated facebook.com / fbcdn.net iframe URL.

Feature flag: META_PREVIEW_TOKEN. Unset -> every preview endpoint answers 200 {"enabled": false} and nothing else happens.
Safety (the app can be public): only ad ids that exist in meta_ads are queried, the client never supplies a URL, the iframe src is parsed with a
strict HTML parser and must be https on facebook.com / fbcdn.net (or a subdomain), results are cached 6 h per (ad id, format), requests are rate
limited per IP and globally, Graph is called with an 8 s timeout, the token is only ever sent in an Authorization header and is scrubbed from every
log line and error, and Meta errors never reach the client as text.
"""
from __future__ import annotations

import collections
import datetime as dt
import logging
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

log = logging.getLogger("tagexp.preview")

GRAPH_URL = "https://graph.facebook.com/v23.0/{ad_id}/previews"
FORMATS = {"mobile_feed": "MOBILE_FEED_STANDARD", "desktop_feed": "DESKTOP_FEED_STANDARD", "reels": "INSTAGRAM_REELS", "stories": "INSTAGRAM_STORY"}
DEFAULT_FORMAT = "mobile_feed"
ALLOWED_HOST_SUFFIXES = ("facebook.com", "fbcdn.net")
CACHE_TTL_SEC = 6 * 3600
ERROR_TTL_SEC = 60
TIMEOUT_SEC = 8
PER_IP_LIMIT = 20
GLOBAL_LIMIT = 120
WINDOW_SEC = 60
AD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
UNAVAILABLE = {"enabled": True, "error": "preview unavailable"}


class _IframeSrc(HTMLParser):
    """Collects the src of the FIRST <iframe>; anything unexpected simply yields no src."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.src: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "iframe" and self.src is None:
            self.src = dict(attrs).get("src")


def extract_iframe_src(body: str) -> str | None:
    p = _IframeSrc()
    try:
        p.feed(body)
        p.close()
    except Exception:
        return None
    return p.src


def validate_src(src: str | None) -> str | None:
    """https only, no credentials, standard port, host == facebook.com / fbcdn.net or a subdomain of them (never a look-alike suffix)."""
    if not isinstance(src, str) or not src or len(src) > 4096 or any(ord(c) < 32 for c in src):
        return None
    try:
        u = urlsplit(src.strip())
        host = (u.hostname or "").lower().rstrip(".")
        port = u.port
    except ValueError:
        return None
    if u.scheme != "https" or u.username or u.password or port not in (None, 443) or not host:
        return None
    if not any(host == d or host.endswith("." + d) for d in ALLOWED_HOST_SUFFIXES):
        return None
    return src.strip()


class _Window:
    """Sliding-window counter."""

    def __init__(self, limit: int, window: int = WINDOW_SEC):
        self.limit, self.window = limit, window
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int:
        """Records a request; returns 0 if allowed, else the seconds to wait."""
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 5000:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    self._hits.pop(k, None)
            q = self._hits.setdefault(key, collections.deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            return 0


class PreviewService:
    def __init__(self, token: str | None, http_get=requests.get, clock=time.time):
        self.token = (token or "").strip()
        self.http_get = http_get
        self.clock = clock
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._per_ip = _Window(PER_IP_LIMIT)
        self._global = _Window(GLOBAL_LIMIT)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def scrub(self, text) -> str:
        s = str(text)
        return s.replace(self.token, "***") if self.token else s

    def rate_check(self, ip: str) -> int:
        return max(self._per_ip.hit(ip), self._global.hit("*"))

    def get(self, ad_id: str, fmt: str) -> dict:
        """Preview for an ad that is known to exist. Never raises; never includes the token or Meta's error text."""
        key = (ad_id, fmt)
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        result, ttl = self._fetch(ad_id, fmt), CACHE_TTL_SEC
        if result.get("error"):
            ttl = ERROR_TTL_SEC
        with self._lock:
            if len(self._cache) > 5000:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[key] = (now + ttl, result)
        return result

    def _fetch(self, ad_id: str, fmt: str) -> dict:
        try:
            r = self.http_get(GRAPH_URL.format(ad_id=ad_id), params={"ad_format": FORMATS[fmt]},
                              headers={"Authorization": f"Bearer {self.token}"}, timeout=TIMEOUT_SEC, allow_redirects=False)
            if r.status_code != 200:
                log.warning("preview: Graph answered %s for an ad", r.status_code)
                return dict(UNAVAILABLE)
            data = r.json().get("data") or []
            body = data[0].get("body") if data and isinstance(data[0], dict) else None
            src = validate_src(extract_iframe_src(body)) if isinstance(body, str) else None
            if not src:
                log.warning("preview: Graph returned no usable iframe for an ad")
                return dict(UNAVAILABLE)
            return {"enabled": True, "ad_id": ad_id, "format": fmt, "src": src, "fetched_at": dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat(timespec="seconds")}
        except Exception as e:                      # timeouts, DNS, bad JSON ...: log the class only, scrubbed
            log.warning("preview: request failed (%s)", self.scrub(type(e).__name__))
            return dict(UNAVAILABLE)
