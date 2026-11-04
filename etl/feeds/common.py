"""Shared plumbing for the server-side feeds: errors, secret scrubbing, HTTP with retry and per-source rate limits, trailing-window writes,
the per-feed mode switch and the attempt log. No source-specific logic lives here.

Nothing in this module prints or stores a credential: errors pass through `scrub()`, headers are never part of an error message.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from psycopg import sql

PT = ZoneInfo("America/Los_Angeles")
DENVER = ZoneInfo("America/Denver")
MODES = ("sheet", "shadow", "direct")
NOTE_LIMIT = 300


class FeedError(RuntimeError):
    """A pull failed. The message is already scrubbed and safe to store."""


class FeedEmpty(FeedError):
    """The source answered but returned nothing for the window: nothing is written."""


class AuthError(FeedError):
    """The source rejected the credential (HTTP 401/403): never retried."""


# ---------------------------------------------------------------------------------------------------------------- scrubbing

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|DSN|SA_JSON|ENC|DATABASE_URL|DATABASE_PUBLIC_URL|APP_USERS)", re.I)
_PATTERNS = [
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s]+@", re.I), r"\1***@"),
    (re.compile(r"((?:access_token|refresh_token|code|client_secret|api[_-]?key|token|password|secret)=)[^&\s'\"]+", re.I), r"\1***"),
    (re.compile(r"((?:x-api-key|authorization|x-pixel-id)['\"]?\s*[:=]\s*['\"]?)(?:Bearer\s+)?[A-Za-z0-9_\-\.=/+]+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=/+]+", re.I), r"\1***"),
    (re.compile(r"\bEA[A-Za-z0-9]{40,}\b"), "***"),
]


def scrub(text: Any, limit: int = NOTE_LIMIT, env: Mapping[str, str] | None = None) -> str:
    """Text safe to log or store: the literal values of every secret-looking env var, credentials in URLs and token-looking strings removed."""
    s = str(text)
    for name, v in (os.environ if env is None else env).items():
        if v and len(v) > 8 and _SECRET_NAME.search(name):
            s = s.replace(v, "***")
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s[:limit]


def log(*a: Any) -> None:
    print("[feeds]", scrub(" ".join(str(x) for x in a), 1500), flush=True)


# ---------------------------------------------------------------------------------------------------------------- time

def pacific_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(PT).date()


def denver_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(DENVER).date()


def days_between(a: dt.date, b: dt.date) -> Iterable[dt.date]:
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


@dataclass
class Ctx:
    """Everything a pull needs from its surroundings; tests replace `session`, `sleep` and `today`."""
    today: dt.date                                      # Pacific date of the run
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    session: Any = None                                 # requests.Session-like (request(method, url, **kw)); None = a real one
    sleep: Callable[[float], None] = time.sleep
    now_utc: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    warnings: list[str] = field(default_factory=list)
    levers: dict[str, float] = field(default_factory=dict)   # proj_feed_levers, loaded by the runner before each pull
    cache: dict = field(default_factory=dict)                 # per-run memo shared by feeds that read the same API response
    history: Callable[[str, dt.date, dt.date], dict] | None = None   # (feed, since, until) -> {day: total}; for day-level sanity screens

    def warn(self, msg: str) -> None:
        self.warnings.append(scrub(msg))

    def require(self, name: str) -> str:
        v = (self.env.get(name) or "").strip()
        if not v:
            raise FeedError(f"missing env var {name}")
        return v


def make_ctx(**kw: Any) -> Ctx:
    now = kw.pop("now_utc", None) or dt.datetime.now(dt.timezone.utc)
    return Ctx(today=kw.pop("today", None) or pacific_today(now), now_utc=now, **kw)


# ---------------------------------------------------------------------------------------------------------------- HTTP

class RateLimiter:
    """At least `min_interval` seconds between calls through one limiter (one per source)."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.min_interval, self._clock, self._sleep, self._last = min_interval, clock, sleep, None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now


class Http:
    """JSON over HTTP with retry/backoff. 429 and 5xx and network errors are retried (Retry-After honoured); 401/403 raise AuthError at once;
    other 4xx raise FeedError with a scrubbed body snippet. The credential headers are passed per call and never appear in an error."""

    RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)

    def __init__(self, ctx: Ctx, source: str, *, min_interval: float = 0.0, retries: int = 5, backoff: float = 2.0, max_backoff: float = 60.0,
                 timeout: float = 120.0):
        self.ctx, self.source, self.retries, self.backoff, self.max_backoff, self.timeout = ctx, source, retries, backoff, max_backoff, timeout
        self.limiter = RateLimiter(min_interval, sleep=ctx.sleep) if min_interval else None
        self.calls = 0

    def _session(self):
        if self.ctx.session is None:
            import requests
            self.ctx.session = requests.Session()
        return self.ctx.session

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None, params: Mapping[str, Any] | None = None,
                json_body: Any = None, what: str = "") -> Any:
        import requests
        label = f"{self.source} {what or url.rsplit('/', 1)[-1]}"
        delay = self.backoff
        last = "no attempt"
        for attempt in range(1, self.retries + 2):
            if self.limiter:
                self.limiter.wait()
            self.calls += 1
            try:
                r = self._session().request(method, url, headers=dict(headers or {}), params=params, json=json_body, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"{type(e).__name__}"
            else:
                code = r.status_code
                if code == 200:
                    try:
                        return r.json()
                    except ValueError:
                        raise FeedError(f"{label}: response was not JSON") from None
                if code in (401, 403):
                    raise AuthError(f"{label}: credential rejected (HTTP {code})")
                if code not in self.RETRY_STATUS:
                    raise FeedError(scrub(f"{label}: HTTP {code} {str(getattr(r, 'text', ''))[:160]}"))
                last = f"HTTP {code}"
                ra = (getattr(r, "headers", None) or {}).get("Retry-After")
                if ra and str(ra).replace(".", "", 1).isdigit():
                    delay = min(float(ra), self.max_backoff)
            if attempt > self.retries:
                break
            log(f"{label}: {last}, retry {attempt}/{self.retries} in {delay:.0f}s")
            self.ctx.sleep(delay)
            delay = min(delay * 2, self.max_backoff)
        raise FeedError(f"{label}: gave up after {self.retries} retries ({last})")

    def get(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw)


# ---------------------------------------------------------------------------------------------------------------- trailing-window writes

@dataclass(frozen=True)
class Span:
    since: dt.date
    until: dt.date

    def __contains__(self, d: dt.date) -> bool:
        return self.since <= d <= self.until


def trailing_span(today: dt.date, days: int, include_today: bool = False) -> Span:
    """The last `days` days ending yesterday (Pacific), or today when include_today."""
    if days < 1:
        raise ValueError("days must be >= 1")
    until = today if include_today else today - dt.timedelta(days=1)
    return Span(until - dt.timedelta(days=days - 1), until)


def replace_window(conn, table: str, cols: Sequence[str], rows: Sequence[Sequence[Any]], delete_where: sql.Composable, delete_params: Sequence[Any] = (),
                   conflict_nothing: bool = False) -> int:
    """Delete the window's rows and insert the pulled rows in one transaction (the caller commits). Idempotent: running twice leaves the same rows.
    Table and column names are identifiers composed with psycopg.sql; values are parameters."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(table)) + delete_where, tuple(delete_params))
        if rows:
            ins = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)), sql.SQL(", ").join(sql.Placeholder() * len(cols)))
            if conflict_nothing:
                ins += sql.SQL(" ON CONFLICT DO NOTHING")
            cur.executemany(ins, [tuple(r) for r in rows])
    return len(rows)


def day_floor_where(col: str) -> sql.Composable:
    return sql.SQL("{} >= %s").format(sql.Identifier(col))


# ---------------------------------------------------------------------------------------------------------------- mode switch and attempt log

def get_mode(conn, feed: str, default: str = "sheet") -> str:
    """The feed's mode; `default` when no row exists ('sheet' for feeds with a sheet twin, 'shadow' for native feeds)."""
    r = conn.execute("SELECT mode FROM proj_feed_config WHERE feed = %s", (feed,)).fetchone()
    return (r[0] if r else default)


def all_modes(conn) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT feed, mode FROM proj_feed_config").fetchall()}


def get_levers(conn) -> dict[str, float]:
    return {r[0]: float(r[1]) for r in conn.execute("SELECT key, value FROM proj_feed_levers").fetchall()}


def set_lever(conn, key: str, value: float, note: str | None = None, by: str = "cli") -> None:
    conn.execute(
        "INSERT INTO proj_feed_levers (key, value, note, updated_at, updated_by) VALUES (%s, %s, %s, now(), %s) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, note = coalesce(excluded.note, proj_feed_levers.note), updated_at = now(), updated_by = excluded.updated_by",
        (key, value, scrub(note)[:NOTE_LIMIT] if note else None, by))


def set_mode(conn, feed: str, mode: str, by: str = "cli", note: str | None = None) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    conn.execute(
        "INSERT INTO proj_feed_config (feed, mode, changed_at, changed_by, note) VALUES (%s, %s, now(), %s, %s) "
        "ON CONFLICT (feed) DO UPDATE SET mode = excluded.mode, changed_at = now(), changed_by = excluded.changed_by, note = excluded.note",
        (feed, mode, by, scrub(note)[:NOTE_LIMIT] if note else None))


def log_attempt(conn, feed: str, mode: str, source: str, status: str, started_at: dt.datetime, rows: int | None = None,
                first_day: dt.date | None = None, last_day: dt.date | None = None, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO proj_feed_attempts (feed, mode, source, started_at, finished_at, status, rows, first_day, last_day, note) "
        "VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s)",
        (feed, mode, source, started_at, status, rows, first_day, last_day, scrub(note)[:NOTE_LIMIT] if note else None))


def mirror_main_run(conn, feed: str, source: str, rows: int, first: dt.date | None, last: dt.date | None, note: str | None) -> None:
    """Keep the pacing module's proj_feed_runs row current when a feed writes its main table directly (the table exists once migration 170 ran)."""
    if conn.execute("SELECT to_regclass('proj_feed_runs')").fetchone()[0] is None:
        return
    conn.execute(
        "INSERT INTO proj_feed_runs (feed, source, loaded_at, rows_loaded, first_day, last_day, note) VALUES (%s, %s, now(), %s, %s, %s, %s) "
        "ON CONFLICT (feed) DO UPDATE SET source = excluded.source, loaded_at = now(), rows_loaded = excluded.rows_loaded, "
        "first_day = excluded.first_day, last_day = excluded.last_day, note = excluded.note",
        (feed, source, rows, first, last, scrub(note)[:NOTE_LIMIT] if note else None))


def table_exists(conn, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


def round2(x: float) -> float:
    return round(float(x or 0), 2)


def num(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0
