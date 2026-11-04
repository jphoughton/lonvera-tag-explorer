"""Dry run: a real, read-only API pull for a few days, diffed against the live sheet tab the main table is fed from. No database writes, no sheet writes.
Prints counts and differences only. Credentials come from the environment or, locally, from the files the desktop jobs use (never printed)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from . import common
from .common import FeedError, make_ctx
from .feed import Feed
from .verify import compare_rows, summarize

HOME = Path.home()


def local_env() -> dict[str, str]:
    """os.environ plus any missing source credential read from the local files (Upstack ~/.upstackrc, Warehance .env). Meta falls back inside etl.meta_api."""
    env = dict(os.environ)
    try:
        if not env.get("UPSTACK_API_KEY"):
            p = json.load(open(HOME / ".upstackrc"))["profiles"]["default"]
            env["UPSTACK_API_KEY"], env["UPSTACK_PIXEL_ID"] = p["apiKey"], p["pixelId"]
    except (OSError, KeyError, ValueError):
        pass
    try:
        if not env.get("WAREHANCE_API_KEY"):
            for line in open(HOME / "Desktop/Google Sheets/lonvera_fulfillment/.env"):
                if line.startswith("WAREHANCE_API_KEY="):
                    env["WAREHANCE_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def dryrun(feed: Feed, days: int = 5, out: Callable[[str], None] = print, levers: dict | None = None) -> dict:
    ctx = make_ctx(env=local_env())
    ctx.levers = dict(levers or {})
    span = feed.span(ctx, days)
    rows = feed.pull(ctx, span)
    pulled = {feed.key_of(r) for r in rows}
    out(f"{feed.key}: pulled {len(rows)} rows for {span.since}..{span.until}" + (f"; warnings: {'; '.join(ctx.warnings)}" if ctx.warnings else ""))
    if not feed.sheet_parser:
        out(f"{feed.key}: native feed, nothing to diff with")
        return {"feed": feed.key, "rows": len(rows), "results": {}}
    from etl import projection_pacing
    sheet_all = feed.parse_sheet(projection_pacing.read_grid(feed.sheet_tab))
    lo = span.since
    sheet = [r for r in sheet_all if feed.key_of(r) in pulled or feed.day_of(r) >= lo]
    sheet = [r for r in sheet if feed.day_of(r) <= max(feed.day_of(x) for x in rows)] if rows else sheet
    b_through = max((feed.day_of(r) for r in sheet_all), default=None)
    res = compare_rows(feed, rows, sheet, ctx.today, b_through=b_through)
    s = summarize(res)
    out(f"{feed.key}: sheet tab '{feed.sheet_tab}' newest day {b_through}; sheet rows in window {len(sheet)}; days compared {s['checked']} "
        f"(ok {s['ok']}, failed {s['failed']}, soft {s['soft']}), sheet behind on {s['stale']} day(s)")
    for d in sorted(res):
        r = res[d]
        if r.status in ("fail", "soft"):
            out(f"    {d} {r.status}: {r.n_bad}/{r.n_keys} keys; worst {r.worst_col} abs {r.worst_abs:,.2f} rel {r.worst_rel:.2%}  {r.detail[:120]}")
    return {"feed": feed.key, "rows": len(rows), "sheet_rows": len(sheet), "results": res, **s}
