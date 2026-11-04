"""Feed freshness for projection: what each module reports through `etl.projection_<module>.freshness(conn)`, evaluated against today.

Used by the daily refresh runner (`python -m etl.projection_daily`, which fails on a stale daily feed) and by GET /api/projection/freshness.

Hook contract (module side, in etl/projection_<module>.py; conn rows are dicts):
    freshness(conn) -> [{"feed": str, "label": str, "cadence": "daily" | "monthly-upload", "data_through": date | ISO str | None, "stale_after_days": int}]
    refresh(conn, days=14) -> {"rows": int, "data_through": "YYYY-MM-DD", ...}      (idempotent, incremental, trailing-days upsert, no secrets in output)
"""
from __future__ import annotations

import importlib
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .interfaces import MODULE_CATALOG

MODULE_KEYS = tuple(MODULE_CATALOG)
CADENCES = ("daily", "monthly-upload")
ERROR_LIMIT = 300
PACIFIC = ZoneInfo("America/Los_Angeles")
_CRED_URL = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s]+@", re.I)
_SECRETS = [
    (re.compile(r"((?:access_token|api[_-]?key|token|password|secret)=)[^&\s'\"]+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=]+", re.I), r"\1***"),
    (re.compile(r"\bEA[A-Za-z0-9]{40,}\b"), "***"),
]
_ENV_SECRETS = ("META_ACCESS_TOKEN", "GOOGLE_SA_JSON", "APP_PASSWORD", "APP_USERS", "SECRET_KEY", "DATABASE_URL", "WAREHANCE_API_KEY", "UPSTACK_API_KEY")


def scrub(text: Any, limit: int = ERROR_LIMIT) -> str:
    """Text safe to store or print: credentials in URLs, token-looking values and the literal values of known secret env vars removed; cut to `limit`."""
    s = str(text)
    for name in _ENV_SECRETS:
        v = os.environ.get(name)
        if v and len(v) > 8:
            s = s.replace(v, "***")
    s = _CRED_URL.sub(r"\1***@", s)
    for pat, repl in _SECRETS:
        s = pat.sub(repl, s)
    return s[:limit]


def today_pacific() -> date:
    return datetime.now(PACIFIC).date()


def hook_module(key: str, importer: Callable[[str], Any] = importlib.import_module):
    """The module's etl.projection_<key> (None when that module does not exist; any other import problem is raised)."""
    name = f"etl.projection_{key}"
    try:
        return importer(name)
    except ModuleNotFoundError as e:
        if e.name == name:
            return None
        raise


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def evaluate(module: str, entry: dict, today: date) -> dict:
    """One freshness entry from a module -> the reported feed (age and status filled in). ValueError for a malformed entry."""
    feed = entry.get("feed")
    if not isinstance(feed, str) or not feed:
        raise ValueError("feed name missing")
    cadence = entry.get("cadence")
    if cadence not in CADENCES:
        raise ValueError(f"feed {feed}: cadence must be one of {CADENCES}")
    stale_after = entry.get("stale_after_days")
    if isinstance(stale_after, bool) or not isinstance(stale_after, int) or stale_after < 0:
        raise ValueError(f"feed {feed}: stale_after_days must be a non-negative integer")
    through = _to_date(entry.get("data_through"))
    age = max(0, (today - through).days) if through else None
    status = "unknown" if through is None else ("stale" if age > stale_after else "fresh")
    out = {"module": module, "feed": feed, "label": entry.get("label") or feed, "cadence": cadence,
           "data_through": through.isoformat() if through else None, "age_days": age, "stale_after_days": stale_after, "status": status}
    out["message"] = message(out)
    return out


def message(f: dict) -> str:
    """Precise one-liner: 'feed X last data 2026-11-01, 3 days old (stale after 2 days)'."""
    if f["data_through"] is None:
        return f"feed {f['feed']} has no data yet"
    n = f["age_days"]
    text = f"feed {f['feed']} last data {f['data_through']}, {n} day{'' if n == 1 else 's'} old"
    return text + f" (stale after {f['stale_after_days']} day{'' if f['stale_after_days'] == 1 else 's'})" if f["status"] == "stale" else text


def collect(conn, hooks: dict[str, Any], today: date, keys: tuple[str, ...] | list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(feeds, errors): evaluate every module's freshness hook. A hook that raises or returns a malformed entry is reported as an error for
    that module and never stops the others."""
    feeds: list[dict] = []
    errors: list[dict] = []
    for key in keys or MODULE_KEYS:
        fn = getattr(hooks.get(key), "freshness", None)
        if fn is None:
            continue
        try:
            feeds.extend(evaluate(key, e, today) for e in list(fn(conn)))
        except Exception as e:                                       # noqa: BLE001  (a module bug must not hide the other modules' feeds)
            errors.append({"module": key, "error": scrub(f"freshness hook failed: {type(e).__name__}: {e}")})
            try:
                conn.rollback()                                       # a failed query leaves the transaction aborted
            except Exception:                                         # noqa: BLE001
                pass
    return feeds, errors


def load_hooks(keys=None, importer: Callable[[str], Any] = importlib.import_module) -> tuple[dict[str, Any], dict[str, str]]:
    """({key: etl module}, {key: import error}) for modules that have an etl.projection_<key>."""
    hooks, problems = {}, {}
    for key in keys or MODULE_KEYS:
        try:
            mod = hook_module(key, importer)
        except Exception as e:                                       # noqa: BLE001
            problems[key] = scrub(f"import failed: {type(e).__name__}: {e}")
            continue
        if mod is not None:
            hooks[key] = mod
    return hooks, problems


def last_runs(conn) -> dict[str, dict]:
    """Latest ok/failed run per module (skipped rows are bookkeeping, not a run)."""
    rows = conn.execute("SELECT DISTINCT ON (module) module, status, finished_at, rows, error FROM proj_refresh_runs "
                        "WHERE status IN ('ok', 'failed') ORDER BY module, id DESC").fetchall()
    return {r["module"]: {"status": r["status"], "finished_at": r["finished_at"], "rows": r["rows"], "error": r["error"]} for r in rows}


def report(conn, today: date | None = None, importer: Callable[[str], Any] = importlib.import_module) -> dict:
    """The GET /api/projection/freshness payload."""
    today = today or today_pacific()
    hooks, problems = load_hooks(importer=importer)
    feeds, errors = collect(conn, hooks, today)
    runs = last_runs(conn)
    out = [{**f, "last_run": runs.get(f["module"])} for f in feeds]
    for e in [*errors, *({"module": k, "error": v} for k, v in problems.items())]:
        out.append({"module": e["module"], "feed": "(module)", "label": "Freshness check", "cadence": None, "data_through": None, "age_days": None,
                    "stale_after_days": None, "status": "unknown", "message": e["error"], "last_run": runs.get(e["module"])})
    summary = {s: sum(1 for f in out if f["status"] == s) for s in ("fresh", "stale", "unknown")}
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "feeds": out, "summary": summary}
