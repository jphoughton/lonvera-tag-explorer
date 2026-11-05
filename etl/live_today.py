"""Live today: one hourly Upstack pull of the Pacific day so far -> a snapshot row in proj_live_today_snapshots.

    python -m etl.live_today [--dry-run] [--keep-days 90]

Fills the 'Meta spend today' and 'NC revenue today' cards on Projections > Pacing. Same Upstack calls and measures as the closed-day feeds
(etl/feeds/upstack.py: orders.new_customer_total_revenue, orders.new_customers, channel.spend), Pacific day bucketing, so a closed day ties to
upstack_orders_daily / the platform feed. Fast (three API calls), and it only ever writes a complete, sane snapshot: on any Upstack error or a
response that fails the checks it writes nothing (the last good snapshot stays), logs, and exits non-zero so the Railway run shows Failed.
Env: UPSTACK_API_KEY, UPSTACK_PIXEL_ID, DATABASE_URL. --dry-run reads the same two env vars (or ~/.upstackrc locally), prints, and never touches the database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass

from etl.feeds import upstack as U
from etl.feeds.common import Ctx, FeedError, PT, log, make_ctx, num, round2, scrub

KEEP_DAYS = 90
EARLIEST_CHECK_HOUR = 3          # before 03:00 Pacific a day with no sales row at all is believable; after it, nothing at all means a broken response
PAGE = 20


@dataclass(frozen=True)
class Snapshot:
    pulled_at: dt.datetime       # UTC, truncated to the minute
    day: dt.date                 # Pacific day
    pacific_time: dt.time        # Pacific wall clock, to the minute
    meta_spend: float
    google_spend: float
    total_spend: float
    nc_rev: float
    nc_orders: int

    def row(self) -> tuple:
        return (self.pulled_at, self.day, self.pacific_time, self.meta_spend, self.google_spend, self.total_spend, self.nc_rev, self.nc_orders)


def _channel_rows(up: "U.Upstack", day: dt.date) -> list[dict]:
    """Every ad-platform resource for the day, paged (Upstack caps one page at 20)."""
    out: list[dict] = []
    offset = 0
    while True:
        body = {"pixelId": up.pixel, "granularity": "day", "timezone": U.TIMEZONE, "limit": PAGE, "offset": offset,
                "sortBy": "sourceUpdatedAt", "sortDirection": "desc", "dateRange": {"start": day.isoformat(), "end": day.isoformat()},
                "measures": [{"measure": "channel.spend"}]}
        page = up.post("/api/query-channel-details", body).get("results") or []
        out += page
        if len(page) < PAGE or offset > 10 * PAGE:
            return out
        offset += PAGE


def spend_by_platform(rows: list[dict], day: dt.date) -> dict[str, float]:
    """{platform name: spend of `day`} from query-channel-details results. Rows of other days are ignored."""
    agg: dict[str, float] = {}
    for r in rows:
        src = (r.get("basicDetails") or {}).get("resourceSource")
        platform = U.PLATFORM_NAMES.get(src, src)
        if not platform:
            continue
        for p in (r.get("data") or {}).get("primary") or []:
            if U._day(p) == day:
                agg[platform] = agg.get(platform, 0.0) + num(p.get("channel.spend"))
    return agg


def build_snapshot(now_utc: dt.datetime, day: dt.date, orders: list[dict], channels: list[dict]) -> Snapshot:
    """Parse the two Upstack responses for `day` into a Snapshot, or raise FeedError when they are not trustworthy."""
    ptime = now_utc.astimezone(PT)
    if ptime.date() != day:
        raise FeedError(f"the Pacific day moved on during the pull ({day} -> {ptime.date()}); try again")
    today_rows = [p for p in orders if U._day(p) == day]
    if len(today_rows) > 1:
        raise FeedError(f"upstack returned {len(today_rows)} rows for {day}")
    nc_rev = round2(num(today_rows[0].get("orders.new_customer_total_revenue"))) if today_rows else 0.0
    nc_orders = int(num(today_rows[0].get("orders.new_customers"))) if today_rows else 0
    spend = spend_by_platform(channels, day)
    meta, google = round2(spend.get(U.PLATFORM_NAMES["facebookIntegration"], 0.0)), round2(spend.get("Google Ads", 0.0))
    total = round2(sum(spend.values()))
    if min(nc_rev, nc_orders, meta, google, total) < 0:
        raise FeedError("upstack returned a negative figure; nothing written")
    if ptime.hour >= EARLIEST_CHECK_HOUR and not today_rows and not spend:
        raise FeedError(f"upstack returned no sales row and no spend for {day} at {ptime:%H:%M} Pacific; nothing written")
    return Snapshot(now_utc.replace(second=0, microsecond=0), day, ptime.time().replace(second=0, microsecond=0), meta, google, total, nc_rev, nc_orders)


def pull(ctx: Ctx, up: "U.Upstack | None" = None) -> Snapshot:
    up = up or U.Upstack(ctx)
    now = ctx.now_utc
    day = now.astimezone(PT).date()
    orders = up.query(day, day, ["orders.new_customer_total_revenue", "orders.new_customers"])
    channels = _channel_rows(up, day)
    return build_snapshot(now, day, orders, channels)


def save(conn, snap: Snapshot, keep_days: int = KEEP_DAYS) -> int:
    """Insert (or, inside the same minute, replace) the snapshot and prune the old ones. The caller commits. Returns rows pruned."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO proj_live_today_snapshots (pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (pulled_at) DO UPDATE SET day = EXCLUDED.day, pacific_time = EXCLUDED.pacific_time, "
                    "meta_spend = EXCLUDED.meta_spend, google_spend = EXCLUDED.google_spend, total_spend = EXCLUDED.total_spend, "
                    "nc_rev = EXCLUDED.nc_rev, nc_orders = EXCLUDED.nc_orders", snap.row())
        cur.execute("DELETE FROM proj_live_today_snapshots WHERE day < %s", (snap.day - dt.timedelta(days=keep_days),))
        return cur.rowcount


def main(argv: list[str] | None = None, ctx: Ctx | None = None, up: "U.Upstack | None" = None, conn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="pull and print; no database access at all")
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    a = ap.parse_args(argv)
    try:
        if ctx is None:
            if a.dry_run:
                from etl.feeds.dryrun import local_env
                ctx = make_ctx(env=local_env())
            else:
                ctx = make_ctx()
        snap = pull(ctx, up)
        log(f"live today {snap.day} {snap.pacific_time:%H:%M} PT: meta {snap.meta_spend:,.2f}, google {snap.google_spend:,.2f}, total spend {snap.total_spend:,.2f}, "
            f"NC revenue {snap.nc_rev:,.2f} on {snap.nc_orders} new customers")
        if a.dry_run:
            log("dry run: nothing written")
            return 0
        if conn is not None:
            save(conn, snap, a.keep_days); conn.commit()
        else:
            from etl import db
            with db.connect() as c:
                save(c, snap, a.keep_days)
                c.commit()
        return 0
    except FeedError as e:
        print(f"[live_today] {scrub(e)}", file=sys.stderr)
        return 1
    except Exception as e:                                           # noqa: BLE001  (never a stack trace with a header in it)
        print(f"[live_today] failed: {type(e).__name__}: {scrub(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
