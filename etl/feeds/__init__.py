"""Server-side feeds: pull the projection's inputs straight from the source APIs into Postgres (no sheet, no desktop).

    python -m etl.feeds run [--only a,b] [--days N]        refresh every feed whose mode is shadow or direct
    python -m etl.feeds mode [FEED|all] [sheet|shadow|direct]
    python -m etl.feeds status
    python -m etl.feeds verify [--feed a,b] [--days 30] [--need 7] [--live-sheet]
    python -m etl.feeds dryrun --feed a [--days 5]         real API pull (read-only), no database writes, diffed against the live sheet tab

Modes (table proj_feed_config, default `sheet`): sheet = today's behaviour; shadow = the sheet-fed table stays authoritative and the direct pull is written to
proj_feedx_* for comparison; direct = the direct pull writes the main table and the sheet read is the fallback when a pull fails.

Hook for the projection modules (docs/projection/FEEDS_PLAN.md, step 5):
    feeds.load(feed_key, conn, days, sheet_fallback=callable) -> dict      call instead of reading the sheet tab directly
    feeds.freshness(conn) -> [{"feed", "label", "cadence", "data_through", "stale_after_days"}]   same contract as MODULE_core.md "Daily refresh"
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Iterable

from psycopg import sql

from . import common
from .common import Ctx, FeedError, make_ctx, replace_window, scrub
from .feed import Feed

SOURCE_ORDER = ("upstack", "warehance", "meta", "expandfi")


def registry() -> dict[str, Feed]:
    """All feeds by key, in source order (imports are lazy so a missing optional dependency only affects its source)."""
    from . import meta_feeds, upstack, warehance
    feeds: list[Feed] = [*upstack.FEEDS, *warehance.FEEDS, *meta_feeds.FEEDS]
    try:
        from . import expandfi_feeds                      # added with the ExpandFi spike (step 8); absent until then
        feeds += expandfi_feeds.FEEDS
    except ImportError:
        pass
    feeds.sort(key=lambda f: SOURCE_ORDER.index(f.source))
    return {f.key: f for f in feeds}


def _dates(feed: Feed, rows: list[tuple]) -> tuple[dt.date | None, dt.date | None]:
    if not rows:
        return None, None
    d = [feed.day_of(r) for r in rows]
    return min(d), max(d)


def pull_and_write(conn, feed: Feed, target: str, days: int | None = None, ctx: Ctx | None = None) -> dict:
    """One direct pull written to the shadow or main table (replacing the trailing window), then committed. Logs the attempt either way."""
    ctx = ctx or make_ctx()
    ctx.warnings = []
    started = dt.datetime.now(dt.timezone.utc)
    mode = "direct" if target == "main" else "shadow"
    source = f"api:{feed.source}"
    try:
        ctx.levers = common.get_levers(conn)
        span = feed.span(ctx, days or feed.days)
        rows = feed.pull(ctx, span)
        table = feed.main if target == "main" else feed.shadow
        where, params = feed.where(span)
        with conn.transaction():
            n = replace_window(conn, table, feed.cols, rows, where, params, feed.conflict_nothing)
        first, last = _dates(feed, rows)
        note = "; ".join(ctx.warnings)[:common.NOTE_LIMIT] or None
        common.log_attempt(conn, feed.key, mode, source, "ok", started, n, first, last, note)
        if target == "main":
            common.mirror_main_run(conn, feed.key, source, n, first, last, note)
        conn.commit()
        return {"feed": feed.key, "mode": mode, "source": source, "rows": n, "first_day": first, "data_through": last, "warnings": list(ctx.warnings)}
    except Exception as e:                                  # noqa: BLE001
        conn.rollback()
        msg = scrub(f"{type(e).__name__}: {e}")
        common.log_attempt(conn, feed.key, mode, source, "failed", started, note=msg)
        conn.commit()
        raise FeedError(msg) from None


def _hook_result(r: dict) -> dict:
    """The result shape the projection modules' refresh hooks return: rows, changed, first_day / data_through as ISO strings."""
    return {"rows": r["rows"], "changed": r["rows"], "first_day": r["first_day"].isoformat() if r["first_day"] else None,
            "data_through": r["data_through"].isoformat() if r["data_through"] else None, "source": r["source"], "warnings": r["warnings"]}


def load(feed_key: str, conn, days: int | None = None, sheet_fallback: Callable[[], dict] | None = None, ctx: Ctx | None = None) -> dict:
    """Entry point for a projection module's refresh. By the feed's mode:
    sheet  -> sheet_fallback();   shadow -> sheet_fallback() plus a direct pull into the shadow table (a shadow failure never fails the module);
    direct -> direct pull into the main table; if it fails, sheet_fallback() (recorded as a `fallback` attempt) or the error when there is none."""
    feed = registry()[feed_key]
    mode = common.get_mode(conn, feed_key, feed.default_mode)
    if mode == "sheet":
        return {"mode": "sheet", **(sheet_fallback() if sheet_fallback else {})}
    if mode == "shadow":
        out = {"mode": "shadow", **(sheet_fallback() if sheet_fallback else {})}
        try:
            out["shadow"] = _hook_result(pull_and_write(conn, feed, "shadow", days, ctx))
        except FeedError as e:
            out["shadow"] = {"error": str(e)}
        return out
    try:
        return {"mode": "direct", **_hook_result(pull_and_write(conn, feed, "main", days, ctx))}
    except FeedError as e:
        if sheet_fallback is None:
            raise
        started = dt.datetime.now(dt.timezone.utc)
        out = {"mode": "direct", "fallback": True, "error": str(e), **sheet_fallback()}
        common.log_attempt(conn, feed_key, "direct", f"sheet:{feed.sheet_tab}", "fallback", started, note=f"FALLBACK to sheet: {e}")
        conn.commit()
        return out


def run(conn, only: Iterable[str] | None = None, days: int | None = None, ctx: Ctx | None = None, out: Callable[[str], None] = print) -> int:
    """Refresh every feed whose mode is shadow or direct (a sheet-mode feed is skipped). One feed failing never stops the others.
    Returns the exit code: 1 when any pull failed or a direct-mode feed is stale."""
    feeds = registry()
    want = list(feeds) if not only else [k for k in only if k in feeds]
    unknown = [k for k in (only or []) if k not in feeds]
    modes = common.all_modes(conn)
    failed = 0
    ctx = ctx or make_ctx()                                 # one context per run: feeds that read the same API response share it
    for key in want:
        mode = modes.get(key, feeds[key].default_mode)
        if mode == "sheet":
            out(f"[feeds] {key}: mode sheet, skipped")
            continue
        try:
            r = pull_and_write(conn, feeds[key], "main" if mode == "direct" else "shadow", days, ctx)
            out(f"[feeds] {key}: {mode} ok, {r['rows']} rows, {r['first_day']}..{r['data_through']}" + (f"; warnings: {'; '.join(r['warnings'])}" if r["warnings"] else ""))
        except FeedError as e:
            failed += 1
            out(f"[feeds] {key}: {mode} FAILED: {e}")
    for k in unknown:
        out(f"[feeds] unknown feed {k}")
        failed += 1
    stale = 0
    for e in freshness(conn):
        if e["status"] == "stale" and e["mode"] == "direct":
            stale += 1
            out(f"[feeds] feed {e['key']} last data {e['data_through']}, {e['age_days']} days old (stale after {e['stale_after_days']} days)")
    return 1 if failed or stale else 0


def freshness_of(conn, feed: Feed, mode: str, today: dt.date | None = None) -> dict:
    table = feed.shadow if mode == "shadow" else feed.main
    where = [w for w in (feed.scope_where, feed.fresh_where) if w]
    q = sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(feed.day_col if feed.day_col != "period_start" else "period_end"), sql.Identifier(table))
    if where:
        q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(sql.SQL(w) for w in where)
    through = conn.execute(q).fetchone()[0]
    today = today or common.pacific_today()
    age = (today - through).days if through else None
    status = "unknown" if through is None else ("fresh" if age <= feed.stale_after else "stale")
    return {"key": feed.key, "feed": feed.key if feed.native else f"feeds.{feed.key}", "label": f"{feed.label} (direct)", "cadence": "daily", "data_through": through,
            "stale_after_days": feed.stale_after, "age_days": age, "status": status, "mode": mode}


def freshness(conn, today: dt.date | None = None) -> list[dict]:
    """Freshness of every feed that is not in sheet mode (a sheet-mode feed is covered by its module's own entry). Hook contract of MODULE_core.md."""
    modes = common.all_modes(conn)
    return [freshness_of(conn, f, modes.get(k, f.default_mode), today) for k, f in registry().items() if modes.get(k, f.default_mode) != "sheet"]


def status(conn) -> list[dict]:
    modes = common.all_modes(conn)
    out = []
    for k, f in registry().items():
        mode = modes.get(k, f.default_mode)
        r = conn.execute("SELECT status, finished_at, rows, last_day, note FROM proj_feed_attempts WHERE feed = %s ORDER BY id DESC LIMIT 1", (k,)).fetchone()
        out.append({"feed": k, "mode": mode, "source": f.source, "last_status": r[0] if r else None, "last_at": r[1] if r else None,
                    "rows": r[2] if r else None, "last_day": r[3] if r else None, "note": r[4] if r else None})
    return out
