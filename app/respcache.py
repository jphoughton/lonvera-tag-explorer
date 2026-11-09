"""Small in-process cache of finished metrics responses.

The numbers only change when the ETL runs, and the same drills are requested over and over (landing page, back button, several people), so a
200 response is stored (already gzipped when the client takes gzip) and served again until the data changes. "Changed" is a version token read
from the database inside the request's own snapshot: a digest of app_meta (data_through, build / pull timestamps, status refresh time, ...) plus
the state of etl_runs. Nothing is cached while an ETL job is running, and entries also expire after a TTL as a backstop for manual SQL edits.

Per process (each gunicorn worker has its own), bounded by bytes (least recently used goes first), 0 MB = off.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict

from flask import Response, g, request

from . import db

CACHEABLE = re.compile(r"^/api/(products|campaigns|campaigns/(trend|all)|platforms/(overview|trend|kpis)|promotions(/scale)?|products/[^/]+/(rollup|families|tree|ads|campaigns|unmatched|trend|stage-daily|coverage)|ads/[0-9]+|google/(rollup|trend))$")
MAX_ENTRY_BYTES = 16 * 1024 * 1024
VERSION_SQL = """
SELECT (SELECT md5(COALESCE(string_agg(key || '=' || value, '|' ORDER BY key), '')) FROM app_meta) AS meta,
       (SELECT max(id) FROM etl_runs) AS last_run,
       (SELECT max(finished_at)::text FROM etl_runs) AS last_finished,
       (SELECT count(*) FROM etl_runs WHERE finished_at IS NULL AND started_at > now() - interval '3 hours') AS running
"""


class ResponseCache:
    def __init__(self, max_bytes: int, ttl_sec: float = 600.0, clock=time.monotonic):
        self.max_bytes, self.ttl, self.clock = max_bytes, ttl_sec, clock
        self._d: OrderedDict[tuple, tuple] = OrderedDict()      # key -> (version, expires, status, headers, body)
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    def get(self, key: tuple, version: str):
        with self._lock:
            e = self._d.get(key)
            if e is None or e[0] != version or e[1] < self.clock():
                if e is not None:
                    self._drop(key)
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return e

    def put(self, key: tuple, version: str, headers: dict, body: bytes) -> None:
        if len(body) > MAX_ENTRY_BYTES or len(body) > self.max_bytes:
            return
        with self._lock:
            if key in self._d:
                self._drop(key)
            self._d[key] = (version, self.clock() + self.ttl, 200, headers, body)
            self._bytes += len(body)
            while self._bytes > self.max_bytes and self._d:
                self._drop(next(iter(self._d)))

    def _drop(self, key: tuple) -> None:
        e = self._d.pop(key, None)
        if e is not None:
            self._bytes -= len(e[4])

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0

    @property
    def size_bytes(self) -> int:
        return self._bytes


def data_version(conn) -> str | None:
    """Token that changes whenever the ETL has touched the data; None while a job is running (do not cache)."""
    r = conn.execute(VERSION_SQL).fetchone()
    if r["running"]:
        return None
    return f"{r['meta']}|{r['last_run']}|{r['last_finished']}"


def _key() -> tuple:
    return (request.path, tuple(sorted(request.args.items(multi=True))), "gzip" in request.headers.get("Accept-Encoding", "").lower())


def lookup(cache: ResponseCache):
    """before_request: serve a cached response, or remember what to store afterwards."""
    if not cache.enabled or request.method != "GET" or not CACHEABLE.match(request.path):
        return None
    version = data_version(db.get_conn())
    if version is None:
        return None
    key = _key()
    hit = cache.get(key, version)
    if hit is not None:
        resp = Response(hit[4], status=hit[2], headers=dict(hit[3]))
        resp.headers["X-Cache"] = "hit"
        return resp
    g._cache_slot = (key, version)
    return None


def store(cache: ResponseCache, resp: Response) -> Response:
    """after_request (last): keep a finished 200."""
    slot = g.pop("_cache_slot", None)
    if slot is None or resp.status_code != 200 or resp.direct_passthrough:
        return resp
    keep = {k: v for k, v in resp.headers.items() if k.lower() in ("content-type", "content-encoding", "vary", "x-data-scope", "cache-control",
                                                                   "x-robots-tag", "x-content-type-options", "x-frame-options", "referrer-policy")}
    cache.put(slot[0], slot[1], keep, resp.get_data())
    resp.headers["X-Cache"] = "miss"
    return resp
