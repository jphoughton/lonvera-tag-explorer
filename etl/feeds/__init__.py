"""Server-side feeds: pull the projection's inputs straight from the source APIs into Postgres (no sheet, no desktop).

    python -m etl.feeds run [--only a,b] [--days N]        refresh every feed whose mode is shadow or direct
    python -m etl.feeds mode [FEED|all] [sheet|shadow|direct]
    python -m etl.feeds status
    python -m etl.feeds verify [--feed a,b] [--days 30] [--need 7] [--live-sheet]
    python -m etl.feeds dryrun --feed a [--days 5]         real API pull (read-only), no database writes, diffed against the live sheet tab

Modes (table proj_feed_config, default `sheet`): sheet = today's behaviour; shadow = the sheet-fed table stays authoritative and the direct pull is written to
proj_feedx_* for comparison; direct = the direct pull writes the main table and the sheet read is the fallback when a pull fails.

Hook for the projection modules (docs/projection/FEEDS_PLAN.md, step 5; wired into etl/projection_pacing.py, projection_inventory_demand.py and
projection_inventory_supply.py):
    feeds.load(feed_key, conn, days, sheet_fallback=callable, module=True) -> dict   call instead of reading the sheet tab directly
    feeds.mode_of(conn, feed_key) -> 'sheet' | 'shadow' | 'direct'                    the module reads its sheet tab up front only when this is not 'direct'
    feeds.direct_chip(conn, entry, feed_key) -> entry                                  freshness chip of a module: direct feeds get the feed's own stale-after limit
    feeds.freshness(conn, include_sheet=False) -> [{"feed", "label", "cadence", "data_through", "stale_after_days", ...}]   MODULE_core.md "Daily refresh"
With module=True (a projection module's refresh owns the transaction and commits it): a sheet-mode feed returns the sheet result untouched, a shadow-mode
feed pulls only when env FEEDS_MODULE_SHADOW=1 (the `feeds` service fills the shadow tables otherwise), a direct-mode feed writes the main table inside a
savepoint (a failed pull leaves the module's other writes alone) and falls back to the sheet read when the pull fails.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Callable, Iterable

from psycopg import sql
from psycopg.rows import tuple_row

from . import common
from .common import Ctx, FeedError, make_ctx, replace_window, scrub
from .feed import Feed

SOURCE_ORDER = ("upstack", "warehance", "meta", "expandfi")
MODULE_SHADOW_ENV = "FEEDS_MODULE_SHADOW"             # "1": a projection module's refresh also fills the shadow tables of the shadow-mode feeds it reads
PASS_THROUGH = ("RefreshTimeout",)                    # exception names that are control flow of the caller (projection_daily's time cap), never a failed pull


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


def pull_and_write(conn, feed: Feed, target: str, days: int | None = None, ctx: Ctx | None = None, commit: bool = True) -> dict:
    """One direct pull written to the shadow or main table (replacing the trailing window), then committed. Logs the attempt either way.

    commit=False is for a caller that owns the transaction (a projection module's refresh): the write and its log row go in one savepoint, nothing is
    committed or rolled back here, so a failed pull leaves the caller's earlier writes alone (the failure row is logged inside the caller's transaction)."""
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
        first, last = _dates(feed, rows)
        note = "; ".join(ctx.warnings)[:common.NOTE_LIMIT] or None
        with conn.transaction():
            n = replace_window(conn, table, feed.cols, rows, where, params, feed.conflict_nothing)
            common.log_attempt(conn, feed.key, mode, source, "ok", started, n, first, last, note)
            if target == "main":
                common.mirror_main_run(conn, feed.key, source, n, first, last, note)
        if commit:
            conn.commit()
        return {"feed": feed.key, "mode": mode, "source": source, "rows": n, "first_day": first, "data_through": last, "warnings": list(ctx.warnings)}
    except Exception as e:                                  # noqa: BLE001
        if type(e).__name__ in PASS_THROUGH:
            raise
        if commit:
            conn.rollback()
        msg = scrub(f"{type(e).__name__}: {e}")
        try:
            with conn.transaction():
                common.log_attempt(conn, feed.key, mode, source, "failed", started, note=msg)
            if commit:
                conn.commit()
        except Exception:                                   # noqa: BLE001  (the pull failure is the news; an unwritable log row must not hide it)
            if commit:
                conn.rollback()
        raise FeedError(msg) from None


def _hook_result(r: dict) -> dict:
    """The result shape the projection modules' refresh hooks return: rows, changed, first_day / data_through as ISO strings."""
    return {"rows": r["rows"], "changed": r["rows"], "first_day": r["first_day"].isoformat() if r["first_day"] else None,
            "data_through": r["data_through"].isoformat() if r["data_through"] else None, "source": r["source"], "warnings": r["warnings"]}


def mode_of(conn, feed_key: str) -> str:
    """The feed's mode as a projection module needs it: 'sheet' for a feed the registry does not know, or when the switch table is not migrated yet."""
    try:
        feed = registry().get(feed_key)
    except ImportError:
        return "sheet"
    return "sheet" if feed is None else common.get_mode(conn, feed_key, feed.default_mode)


def module_shadow_enabled(env: dict | None = None) -> bool:
    return (os.environ if env is None else env).get(MODULE_SHADOW_ENV, "").strip() == "1"


def load(feed_key: str, conn, days: int | None = None, sheet_fallback: Callable[[], dict] | None = None, ctx: Ctx | None = None, *,
         module: bool = False) -> dict:
    """Entry point for a projection module's refresh. By the feed's mode:
    sheet  -> sheet_fallback();   shadow -> sheet_fallback() plus a direct pull into the shadow table (a shadow failure never fails the module);
    direct -> direct pull into the main table; if it fails, sheet_fallback() (recorded as a `fallback` attempt) or the error when there is none.

    module=True (called from a projection module's refresh, which owns and commits the transaction): sheet and shadow return the module's own sheet result
    exactly as before (the shadow pull runs only when env FEEDS_MODULE_SHADOW=1), and a direct pull uses the feed's module window (`Feed.module_days`)
    and a savepoint instead of a commit."""
    feed = registry()[feed_key]
    mode = common.get_mode(conn, feed_key, feed.default_mode)
    commit = not module
    if mode == "sheet":
        res = sheet_fallback() if sheet_fallback else {}
        return res if module else {"mode": "sheet", **res}
    if mode == "shadow":
        res = sheet_fallback() if sheet_fallback else {}
        if module and not module_shadow_enabled():
            return res
        out = {**res} if module else {"mode": "shadow", **res}
        try:
            out["shadow"] = _hook_result(pull_and_write(conn, feed, "shadow", feed.module_days(days) if module else days, ctx, commit))
        except FeedError as e:
            out["shadow"] = {"error": str(e)}
        return out
    try:
        return {"mode": "direct", **_hook_result(pull_and_write(conn, feed, "main", feed.module_days(days) if module else days, ctx, commit))}
    except FeedError as e:
        if sheet_fallback is None:
            raise
        started = dt.datetime.now(dt.timezone.utc)
        out = {"mode": "direct", "fallback": True, "error": str(e), **sheet_fallback()}
        common.log_attempt(conn, feed_key, "direct", f"sheet:{feed.sheet_tab}", "fallback", started, note=f"FALLBACK to sheet: {e}")
        if commit:
            conn.commit()
        return out


def fallback_warning(feed_key: str, res: dict) -> str | None:
    """The line a module reports when `load` fell back to the sheet (None otherwise); the error text is already scrubbed."""
    if not res.get("fallback"):
        return None
    return scrub(f"{feed_key}: the direct pull failed, the sheet was read instead ({res.get('error') or 'unknown error'})", 240)


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


def _one(conn, query, params=()):
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def stale_limit(feed: Feed, mode: str) -> int:
    """Days after which the feed's data counts as stale in this mode: the feed's own limit when the feed is the source (shadow, direct), the sheet-fed
    path's limit (the module's own threshold) in sheet mode."""
    return feed.sheet_stale_after if mode == "sheet" else feed.stale_after


def freshness_of(conn, feed: Feed, mode: str, today: dt.date | None = None) -> dict:
    table = feed.shadow if mode == "shadow" else feed.main
    where = [w for w in (feed.scope_where, feed.fresh_where) if w]
    q = sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(feed.day_col if feed.day_col != "period_start" else "period_end"), sql.Identifier(table))
    if where:
        q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(sql.SQL(w) for w in where)
    through = _one(conn, q)[0]
    today = today or common.pacific_today()
    limit = stale_limit(feed, mode)
    age = (today - through).days if through else None
    status = "unknown" if through is None else ("fresh" if age <= limit else "stale")
    return {"key": feed.key, "feed": feed.key if feed.native else f"feeds.{feed.key}", "label": f"{feed.label} ({mode})", "cadence": "daily", "data_through": through,
            "stale_after_days": limit, "age_days": age, "status": status, "mode": mode}


def freshness(conn, today: dt.date | None = None, include_sheet: bool = False) -> list[dict]:
    """Freshness of every feed that is not in sheet mode (a sheet-mode feed is covered by its module's own entry). Hook contract of MODULE_core.md.
    include_sheet=True lists every feed in the registry, a sheet-mode feed measured on its main table against the sheet-fed path's limit (CLI)."""
    modes = common.all_modes(conn)
    return [freshness_of(conn, f, modes.get(k, f.default_mode), today) for k, f in registry().items()
            if include_sheet or modes.get(k, f.default_mode) != "sheet"]


def direct_chip(conn, entry: dict, feed_key: str) -> dict:
    """A projection module's own freshness entry for a feed, adjusted to the feed's mode: while the feed is direct the chip uses the feed's stale-after limit
    (and says so in its label); sheet and shadow leave the module's entry exactly as it was. The entry is returned (copied when changed)."""
    if mode_of(conn, feed_key) != "direct":
        return entry
    feed = registry()[feed_key]
    return {**entry, "stale_after_days": feed.stale_after, "label": f"{entry.get('label') or feed_key} (direct)"}


def status(conn) -> list[dict]:
    modes = common.all_modes(conn)
    out = []
    for k, f in registry().items():
        mode = modes.get(k, f.default_mode)
        r = _one(conn, "SELECT status, finished_at, rows, last_day, note FROM proj_feed_attempts WHERE feed = %s ORDER BY id DESC LIMIT 1", (k,))
        out.append({"feed": k, "mode": mode, "source": f.source, "last_status": r[0] if r else None, "last_at": r[1] if r else None,
                    "rows": r[2] if r else None, "last_day": r[3] if r else None, "note": r[4] if r else None})
    return out
