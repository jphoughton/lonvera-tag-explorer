"""CLI: python -m etl.feeds {run,mode,status,verify,dryrun,lever,freshness} ... (see etl/feeds/__init__.py)."""
from __future__ import annotations

import argparse
import sys

from . import common, freshness, registry, run, status
from .common import FeedError, scrub


def _conn():
    from etl import db
    return db.connect()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etl.feeds", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="refresh every feed in shadow or direct mode")
    p.add_argument("--only")
    p.add_argument("--days", type=int)
    p = sub.add_parser("mode", help="show or set a feed's mode")
    p.add_argument("feed", nargs="?")
    p.add_argument("mode", nargs="?", choices=common.MODES)
    sub.add_parser("status")
    sub.add_parser("freshness", help="every feed: mode, newest day, age and the stale-after limit that applies in that mode")
    p = sub.add_parser("verify")
    p.add_argument("--feed")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--need", type=int, default=7)
    p.add_argument("--live-sheet", action="store_true")
    p = sub.add_parser("dryrun", help="real read-only API pull diffed against the live sheet tab; no writes")
    p.add_argument("--feed", required=True)
    p.add_argument("--days", type=int, default=5)
    p = sub.add_parser("lever", help="show or set a lever (proj_feed_levers)")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?", type=float)
    p.add_argument("--note")
    a = ap.parse_args(argv)
    feeds = registry()
    try:
        if a.cmd == "dryrun":
            from .dryrun import dryrun
            if a.feed not in feeds:
                sys.exit(f"unknown feed {a.feed}; known: {', '.join(feeds)}")
            dryrun(feeds[a.feed], a.days)
            return 0
        with _conn() as conn:
            if a.cmd == "run":
                return run(conn, a.only.split(",") if a.only else None, a.days)
            if a.cmd == "mode":
                modes = common.all_modes(conn)
                if a.feed and a.mode:
                    targets = list(feeds) if a.feed == "all" else [a.feed]
                    for k in targets:
                        if k not in feeds:
                            sys.exit(f"unknown feed {k}")
                        if feeds[k].native and a.mode == "sheet":
                            print(f"{k}: native feed (no sheet twin), left as is")
                            continue
                        common.set_mode(conn, k, a.mode, "cli")
                        print(f"{k}: mode {a.mode}")
                    conn.commit()
                else:
                    for k, f in feeds.items():
                        print(f"{k:16s} {modes.get(k, f.default_mode):7s} {f.source}")
                return 0
            if a.cmd == "status":
                for r in status(conn):
                    print(f"{r['feed']:16s} {r['mode']:7s} last {r['last_status'] or '-':8s} {str(r['last_at'] or '')[:19]:19s} rows {r['rows'] or '-'} through {r['last_day'] or '-'}"
                          + (f"  {r['note']}" if r["note"] else ""))
                return 0
            if a.cmd == "freshness":
                for e in freshness(conn, include_sheet=True):                  # every feed, in whatever mode, against that mode's stale-after limit
                    print(f"{e['feed']:24s} {e['mode']:7s} {e['status']:8s} through {e['data_through']} (age {e['age_days']}, stale after {e['stale_after_days']})")
                return 0
            if a.cmd == "verify":
                from .verify import run_verify
                want = [feeds[k] for k in (a.feed.split(",") if a.feed else feeds) if k in feeds]
                return run_verify(conn, want, a.days, a.need, a.live_sheet)
            if a.cmd == "lever":
                if a.key and a.value is not None:
                    common.set_lever(conn, a.key, a.value, a.note)
                    conn.commit()
                for k, v in sorted(common.get_levers(conn).items()):
                    print(f"{k:24s} {v}")
                return 0
    except FeedError as e:
        print(f"[feeds] {scrub(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
