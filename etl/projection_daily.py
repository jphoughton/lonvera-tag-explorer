"""Daily refresh for projection (Railway cron, right after etl.run_daily): run every module's refresh hook, record each run, check feed freshness.

    python -m etl.projection_daily [--only cohorts,drivers] [--days 14] [--dry-run]

A module takes part by adding etl/projection_<module>.py with
    refresh(conn, days=14) -> {"rows": int, "data_through": "YYYY-MM-DD", ...}     idempotent, incremental (upsert of the trailing days), no secrets in the output
    freshness(conn) -> [{"feed", "label", "cadence": "daily" | "monthly-upload", "data_through": date | None, "stale_after_days": int}]
Either hook may be missing: a module without `refresh` is skipped. Connections handed to the hooks use dict rows, like the app's.

Each refresh runs in its own try/except, on its own connection, under a time cap (env PROJECTION_REFRESH_TIMEOUT_SEC, default 600). One module failing never
stops the others. Every run is recorded in proj_refresh_runs (error text scrubbed, <= 300 chars). Exit code is 1 when any module failed OR any `daily` feed is
stale beyond its stale_after_days (a stale `monthly-upload` feed is reported, never fatal), so Railway marks the deployment failed. Migrations are not
applied here (scripts/deploy/run_etl.sh does that first).
"""
from __future__ import annotations

import argparse
import inspect
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from app.projection import freshness
from app.projection.freshness import MODULE_KEYS, scrub

DEFAULT_DAYS = 14
DEFAULT_TIMEOUT_SEC = 600.0
TIMEOUT_ENV = "PROJECTION_REFRESH_TIMEOUT_SEC"


class RefreshTimeout(Exception):
    pass


def log(*a) -> None:
    print("[projection_daily]", scrub(" ".join(str(x) for x in a), 1000), flush=True)


@contextmanager
def time_cap(seconds: float | None):
    """Raise RefreshTimeout in the running code after `seconds` (SIGALRM; main thread only, otherwise no cap)."""
    usable = seconds and seconds > 0 and hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
    if not usable:
        yield
        return

    def on_alarm(_sig, _frame):
        raise RefreshTimeout(f"timed out after {seconds:g}s")

    old = signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _timeout_from_env() -> float:
    try:
        return float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_SEC)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC


def _parse_date(v: Any) -> date | None:
    try:
        return freshness._to_date(v)
    except (ValueError, TypeError):
        return None


def _call_refresh(fn: Callable, conn, days: int) -> Any:
    ps = inspect.signature(fn).parameters
    takes_days = "days" in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())
    return fn(conn, days=days) if takes_days else fn(conn)


@dataclass
class ModuleRun:
    module: str
    status: str                      # ok | failed | skipped
    started_at: datetime
    finished_at: datetime
    rows: int | None = None
    data_through: date | None = None
    error: str | None = None

    @property
    def seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def run_module(key: str, mod: Any, connect: Callable[[], Any], days: int, timeout: float | None, import_error: str | None = None) -> ModuleRun:
    started = datetime.now(timezone.utc)

    def done(status: str, **kw) -> ModuleRun:
        return ModuleRun(key, status, started, datetime.now(timezone.utc), **kw)

    if import_error:
        return done("failed", error=import_error)
    fn = getattr(mod, "refresh", None)
    if fn is None:
        return done("skipped", error="no etl module" if mod is None else "no refresh hook")
    conn = None
    try:
        conn = connect()
        with time_cap(timeout):
            out = _call_refresh(fn, conn, days)
        conn.commit()
        out = out if isinstance(out, dict) else {}
        rows = out.get("rows")
        return done("ok", rows=int(rows) if isinstance(rows, (int, float)) and not isinstance(rows, bool) else None, data_through=_parse_date(out.get("data_through")))
    except Exception as e:                                          # noqa: BLE001  (one module's failure is recorded, never raised)
        return done("failed", error=scrub(f"{type(e).__name__}: {e}"))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                       # noqa: BLE001
                pass


def record(rec_conn, r: ModuleRun) -> None:
    rec_conn.execute("INSERT INTO proj_refresh_runs (module, started_at, finished_at, status, rows, data_through, error) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (r.module, r.started_at, r.finished_at, r.status, r.rows, r.data_through, scrub(r.error) if r.error else None))


def line(r: ModuleRun) -> str:
    if r.status == "ok":
        return f"{r.module}: ok rows={r.rows if r.rows is not None else '-'} data_through={r.data_through or '-'} ({r.seconds:.1f}s)"
    if r.status == "skipped":
        return f"{r.module}: skipped ({r.error})"
    return f"{r.module}: FAILED {r.error} ({r.seconds:.1f}s)"


def run_all(keys: list[str], connect: Callable[[], Any], *, days: int = DEFAULT_DAYS, timeout: float | None = None, dry_run: bool = False,
            importer: Callable[[str], Any] | None = None, today: date | None = None, rec_connect: Callable[[], Any] | None = None) -> int:
    """Run the refresh hooks for `keys`, record the runs, evaluate freshness. Returns the process exit code (0 ok, 1 failure)."""
    today = today or freshness.today_pacific()
    hooks, problems = freshness.load_hooks(keys, importer) if importer else freshness.load_hooks(keys)
    failures: list[str] = []

    if dry_run:
        for key in keys:
            mod = hooks.get(key)
            if key in problems:
                what = f"would fail: {problems[key]}"
            elif mod is None:
                what = "skip (no etl module)"
            elif not hasattr(mod, "refresh"):
                what = "skip (no refresh hook)"
            else:
                what = f"would refresh (days={days}, time cap {timeout:g}s)" if timeout else f"would refresh (days={days}, no time cap)"
            log(f"{key}: {what}")
        conn = connect()
        try:
            feeds, errors = freshness.collect(conn, hooks, today, keys)
        finally:
            conn.close()
        _print_freshness(feeds, errors)
        log("dry run: nothing was refreshed or recorded")
        return 0

    rec = (rec_connect or connect)()
    rec.autocommit = True                                           # every run row is kept even if a later step fails
    try:
        for key in keys:
            r = run_module(key, hooks.get(key), connect, days, timeout, problems.get(key))
            log(line(r))
            if r.status == "failed":
                failures.append(f"{key} refresh failed")
            try:
                record(rec, r)
            except Exception as e:                                  # noqa: BLE001
                log(f"{key}: could not record the run: {type(e).__name__}: {e}")
                failures.append(f"{key} run not recorded")
    finally:
        rec.close()

    conn = connect()
    try:
        feeds, errors = freshness.collect(conn, hooks, today, keys)
    finally:
        conn.close()
    stale_daily = _print_freshness(feeds, errors)
    failures += [f"{f['module']}: {f['message']}" for f in stale_daily] + [f"{e['module']}: {e['error']}" for e in errors]
    if failures:
        log("FAILED: " + "; ".join(failures))
        return 1
    log("all modules refreshed and fresh")
    return 0


def _print_freshness(feeds: list[dict], errors: list[dict]) -> list[dict]:
    stale_daily = []
    for f in feeds:
        tag = f["status"].upper() if f["status"] != "fresh" else "fresh"
        note = ""
        if f["status"] == "stale":
            if f["cadence"] == "daily":
                stale_daily.append(f)
            else:
                note = " (monthly upload: not a failure)"
        log(f"  {tag:<7} {f['module']}/{f['feed']} [{f['cadence']}]: {f['message']}{note}")
    for e in errors:
        log(f"  ERROR   {e['module']}: {e['error']}")
    return stale_daily


def default_connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    return psycopg.connect(url, row_factory=dict_row)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh every projection module's feeds and check their freshness.")
    ap.add_argument("--only", help="comma-separated module keys (default: all)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="trailing days each refresh re-pulls (default 14)")
    ap.add_argument("--dry-run", action="store_true", help="list what would run and show freshness; refresh and record nothing")
    args = ap.parse_args(argv)
    keys = list(MODULE_KEYS)
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        bad = [k for k in keys if k not in MODULE_KEYS]
        if bad:
            print(f"unknown module(s): {', '.join(bad)}; one of {', '.join(MODULE_KEYS)}", file=sys.stderr)
            return 2
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2
    return run_all(keys, default_connect, days=args.days, timeout=_timeout_from_env(), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
