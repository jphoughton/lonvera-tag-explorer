"""Upstack source refresh (Railway cron, after etl.run_daily). READ phase: Upstack only, nothing is written. WRITE phase: one transaction replaces the
pulled days in the raw tables and rebuilds ad_daily_upstack plus the derived tables of both bases. Any Upstack failure aborts before the write phase, so the
Upstack source keeps its last good data; the run is recorded failed (etl_runs) and the exit code is 1. Meta's refresh is never affected.

  python -m etl.upstack_daily                          trailing 7 complete Pacific days (default; always re-pulled)
  python -m etl.upstack_daily --days 30
  python -m etl.upstack_daily --since 2026-05-06       backfill from a day through yesterday (resumable: --skip-done skips days already stored)
  python -m etl.upstack_daily --build-only             rebuild from the stored raw pulls (e.g. after the Meta refresh), no API calls
  python -m etl.upstack_daily --dry-run                pull and report, write nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import db, upstack_api as api, upstack_build, upstack_google

PT = ZoneInfo("America/Los_Angeles")
DEFAULT_DAYS = 7
CHANNEL_CHUNK_DAYS = 31
WRITE_EVERY = 10                     # backfills commit every N pulled days so an interrupted run keeps what it has


def yesterday_pt() -> dt.date:
    return dt.datetime.now(PT).date() - dt.timedelta(days=1)


def days_between(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def pull_channel(client: api.Client, since: dt.date, until: dt.date) -> list[tuple]:
    rows: list[tuple] = []
    d = since
    while d <= until:
        e = min(d + dt.timedelta(days=CHANNEL_CHUNK_DAYS - 1), until)
        rows += api.channel_rows(client.channel_campaigns(d, e))
        d = e + dt.timedelta(days=1)
    return rows


def write_days(conn, recs: dict[dt.date, dict]) -> None:
    """Replace the raw rows of these days (attribution + campaign totals + the day registry)."""
    days = sorted(recs)
    conn.execute("DELETE FROM upstack_attr_daily WHERE day = ANY(%s)", (days,))
    conn.execute("DELETE FROM upstack_campaign_daily WHERE day = ANY(%s)", (days,))
    ads, camps = [], []
    for d in days:
        r = recs[d]
        ads += [(d, a[0], a[1], a[2], a[3], a[4], a[5]) for a in r["ads"]]
        camps += [(d, c[0], c[1], c[2], c[3], c[4]) for c in r["camps"]]
    db.bulk_copy(conn, "upstack_attr_daily", ["day", "campaign", "ad", "nc", "nc_rev", "orders", "rev"], _sum_dupes(ads, 3))
    db.bulk_copy(conn, "upstack_campaign_daily", ["day", "campaign", "nc", "nc_rev", "orders", "rev"], _sum_dupes(camps, 2))
    for d in days:
        r = recs[d]
        note = ("unidentified: " + "; ".join(r["unidentified"][:5])) if r["unidentified"] else None
        conn.execute("""INSERT INTO upstack_attr_days (day, pulled_at, campaigns, ad_rows, api_calls, nc_campaigns, nc_ads, note)
                        VALUES (%s, now(), %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (day) DO UPDATE SET pulled_at = now(), campaigns = EXCLUDED.campaigns, ad_rows = EXCLUDED.ad_rows,
                          api_calls = EXCLUDED.api_calls, nc_campaigns = EXCLUDED.nc_campaigns, nc_ads = EXCLUDED.nc_ads, note = EXCLUDED.note""",
                     (d, len([c for c in r["camps"] if api.needs_ads(c)]), len(r["ads"]), r["calls"], r["nc_campaigns"], r["nc_ads"], note))


def _sum_dupes(rows: list[tuple], key_len: int) -> list[tuple]:
    """Rows keyed by their first key_len+1 columns (day + names); duplicates (the same name twice) are summed."""
    agg: dict[tuple, list[float]] = {}
    for r in rows:
        k = r[:key_len]
        cur = agg.setdefault(k, [0.0] * (len(r) - key_len))
        for i, v in enumerate(r[key_len:]):
            cur[i] += float(v)
    return [k + tuple(round(x, 2) for x in v) for k, v in agg.items()]


def write_channel(conn, rows: list[tuple], since: dt.date, until: dt.date) -> None:
    conn.execute("DELETE FROM upstack_channel_daily WHERE day BETWEEN %s AND %s", (since, until))
    agg: dict[tuple, list] = {}
    for d, cid, name, *vals in rows:
        cur = agg.setdefault((d, cid), [name] + [0.0] * len(vals))
        cur[0] = cur[0] or name
        for i, v in enumerate(vals, start=1):
            cur[i] += v
    db.bulk_copy(conn, "upstack_channel_daily",
                 ["day", "campaign_id", "campaign_name", "spend", "purchases", "purchases_value", "nc_purchases", "nc_purchases_value", "link_clicks"],
                 [(d, cid, v[0], *[round(x, 2) for x in v[1:]]) for (d, cid), v in agg.items()])


def check_days(conn, recs: dict[dt.date, dict]) -> None:
    """Refuse a window that looks empty: a day with no Meta-attributed orders at all while Meta spent real money that day means Upstack answered
    with nothing (outage / permission), not that nobody bought."""
    spend = {d: float(s) for d, s in conn.execute("SELECT day, sum(spend) FROM ad_daily WHERE day = ANY(%s) GROUP BY day", (sorted(recs),)).fetchall()}
    for d, r in recs.items():
        if not r["camps"] and spend.get(d, 0) > 500:
            raise db.JobFailed(f"upstack returned no Meta-attributed orders for {d} (Meta spend {spend[d]:,.0f})")


def run(since: dt.date, until: dt.date, workers: int = 4, dry_run: bool = False, skip_done: bool = False, client: api.Client | None = None) -> dict:
    client = client or api.Client()
    conn = db.connect()
    stats: dict = {}
    try:
        done = {r[0] for r in conn.execute("SELECT day FROM upstack_attr_days").fetchall()} if skip_done else set()
        todo = [d for d in days_between(since, until) if d not in done]
        recs: dict[dt.date, dict] = {}
        batch: dict[dt.date, dict] = {}
        t0 = time.time()
        for i, d in enumerate(todo, start=1):
            rec = api.pull_day(client, d, workers)
            recs[d] = batch[d] = rec
            gap = rec["nc_campaigns"] - rec["nc_ads"]
            db.log(f"{d}: {len(rec['camps'])} campaigns, {len(rec['ads'])} ad rows, NC campaigns {rec['nc_campaigns']:,.0f} vs ads {rec['nc_ads']:,.0f} "
                   f"(gap {gap:,.0f}), {rec['calls']} calls ({i}/{len(todo)}, {time.time() - t0:.0f}s)")
            if len(todo) > WRITE_EVERY and len(batch) >= WRITE_EVERY and not dry_run:      # backfill: keep progress
                check_days(conn, batch)
                write_days(conn, batch)
                conn.commit()
                batch = {}
        if not recs and not skip_done:
            raise db.JobFailed("nothing to pull")
        chan_since = min(recs) if recs else since             # a resumed backfill with nothing left still refreshes the channel feed and the build
        chan = pull_channel(client, chan_since, until)
        stats.update(days_pulled=len(recs), api_calls=client.calls, channel_rows=len(chan))
        if dry_run:
            db.log("dry run: nothing written")
            return stats
        if batch:
            check_days(conn, batch)
            write_days(conn, batch)
        write_channel(conn, chan, chan_since, until)
        db.set_meta(conn, "upstack_pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        stats["build"] = upstack_build.build(conn)
        conn.commit()
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_only() -> dict:
    with db.connect() as conn:
        st = upstack_build.build(conn)
        g = upstack_google.build(conn)
        conn.commit()
    return {"build": st, "google": g}


def run_google(since: dt.date, until: dt.date, workers: int, dry_run: bool, skip_done: bool, channel_since: dt.date | None = None,
               client: api.Client | None = None) -> dict:
    """Google side (own transaction, so a Google failure never rolls back the Meta side and vice versa)."""
    client = client or api.Client()
    conn = db.connect()
    try:
        st = upstack_google.run(conn, client, since, until, workers, dry_run, skip_done, channel_since)
        if not dry_run:
            conn.commit()
        return st
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="trailing complete Pacific days to (re)pull, ending --until (default 7)")
    ap.add_argument("--since", help="first day (YYYY-MM-DD); overrides --days")
    ap.add_argument("--until", help="last day (default: yesterday, Pacific; today is partial and never pulled)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-done", action="store_true", help="skip days already in upstack_attr_days (resumable backfill)")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--google-since", help="Google backfill start (YYYY-MM-DD): attribution from this day, channel feed from the same day; default = --since")
    ap.add_argument("--google-only", action="store_true", help="skip the Meta side")
    ap.add_argument("--no-google", action="store_true", help="skip the Google side")
    a = ap.parse_args()
    until = dt.date.fromisoformat(a.until) if a.until else yesterday_pt()
    since = dt.date.fromisoformat(a.since) if a.since else until - dt.timedelta(days=a.days - 1)
    g_since = dt.date.fromisoformat(a.google_since) if a.google_since else since
    with db.JobRun("upstack_daily") as run_:
        res: dict = {}
        errors: list[Exception] = []
        if a.build_only:
            res = build_only()
        else:
            if not a.google_only:                                              # Meta side and Google side fail independently; both are attempted
                try:
                    res = run(since, until, a.workers, a.dry_run, a.skip_done)
                except Exception as e:                                         # noqa: BLE001
                    db.log("upstack (Meta) FAILED:", db.scrub(e))
                    errors.append(e)
            if not a.no_google:
                try:
                    res["google"] = run_google(g_since, until, a.workers, a.dry_run, a.skip_done)
                except Exception as e:                                         # noqa: BLE001
                    db.log("upstack (Google) FAILED:", db.scrub(e))
                    errors.append(e)
        run_.stats = res
        b = res.get("build") or {}
        run_.rows_written = b.get("ad_daily_rows")
        if b.get("through"):
            run_.data_through = dt.date.fromisoformat(b["through"])
        db.log("upstack_daily:", {k: v for k, v in res.items() if k not in ("build", "google")}, {k: v for k, v in b.items() if k not in ("derived", "match_rules")})
        db.log("  match rules:", b.get("match_rules"))
        db.log("  google:", res.get("google"))
        if errors:
            raise errors[0] if isinstance(errors[0], db.JobFailed) else db.JobFailed("; ".join(db.scrub(e)[:200] for e in errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                   # noqa: BLE001
        db.log("upstack_daily FAILED:", db.scrub(e))
        sys.exit(1)
