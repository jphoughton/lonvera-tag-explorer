"""Read-only check of the nc_hourly feed against production: pulls the window from the ExpandFi warehouse (no writes anywhere), ties each day's sum to
upstack_orders_daily.nc_rev (and to proj_feed_nc_product when it is loaded) and prints the share curve the 'NC revenue today' card would use.

    python -m etl.feeds.nc_hourly_check [--days 35] [--window 30]

Needs DATABASE_URL (read: upstack_orders_daily, proj_feed_nc_product) and FEEDS_ENC_KEY (the stored ExpandFi login). Prints aggregates only."""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from app.projection.pacing import nc_curve
from . import common, expandfi_feeds as E
from .common import make_ctx, trailing_span


def tie_out(hourly: list[tuple], upstack: dict[dt.date, float], nc_product: dict[dt.date, float] | None = None) -> list[dict]:
    """Per day: hourly sum, Upstack nc_rev, difference in dollars and percent (and the same against nc_product)."""
    by_day: dict[dt.date, float] = {}
    for day, _h, rev, _n in hourly:
        by_day[day] = by_day.get(day, 0.0) + float(rev)
    out = []
    for d in sorted(by_day):
        u = upstack.get(d)
        row = {"day": d, "hourly": round(by_day[d], 2), "upstack": u, "diff": None if u is None else round(by_day[d] - u, 2),
               "pct": None if not u else (by_day[d] - u) / u}
        if nc_product is not None:
            p = nc_product.get(d)
            row["nc_product"] = p
            row["diff_nc_product"] = None if p is None else round(by_day[d] - p, 2)
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--window", type=int, default=nc_curve.WINDOW_DAYS)
    a = ap.parse_args(argv)
    ctx = make_ctx()
    span = trailing_span(ctx.today, a.days)
    rows = E.pull_nc_hourly(ctx, span)
    print(f"pulled {len(rows)} hour rows for {span.since}..{span.until} (today {ctx.today} excluded)")
    from etl import db
    with db.connect() as conn:
        up = {r[0]: float(r[1]) for r in conn.execute("SELECT day, nc_rev FROM upstack_orders_daily WHERE day BETWEEN %s AND %s", (span.since, span.until)).fetchall()}
        try:
            ncp = {}
            for d, v in conn.execute("SELECT day, sum(nc_rev) FROM proj_feed_nc_product WHERE day BETWEEN %s AND %s GROUP BY day", (span.since, span.until)).fetchall():
                ncp[d] = float(v)
        except Exception:                                                    # noqa: BLE001
            conn.rollback()
            ncp = None
    print(f"{'day':10s} {'hourly sum':>12s} {'upstack nc_rev':>15s} {'diff $':>9s} {'diff %':>8s}" + (f" {'nc_product':>12s} {'diff $':>9s}" if ncp else ""))
    ties = tie_out(rows, up, ncp)
    for t in ties:
        line = f"{t['day']} {t['hourly']:12,.2f} {t['upstack'] if t['upstack'] is not None else float('nan'):15,.2f} {t['diff'] if t['diff'] is not None else float('nan'):9,.2f} " \
               f"{(t['pct'] if t['pct'] is not None else float('nan')):8.2%}"
        if ncp:
            line += f" {t.get('nc_product') or float('nan'):12,.2f} {t.get('diff_nc_product') if t.get('diff_nc_product') is not None else float('nan'):9,.2f}"
        print(line)
    with_u = [t for t in ties if t["upstack"]]
    if with_u:
        tot_h, tot_u = sum(t["hourly"] for t in with_u), sum(t["upstack"] for t in with_u)
        worst = max(with_u, key=lambda t: abs(t["pct"]))
        print(f"total over {len(with_u)} days: hourly {tot_h:,.2f} vs Upstack {tot_u:,.2f} ({(tot_h - tot_u) / tot_u:+.2%}); worst day {worst['day']} {worst['pct']:+.2%}")
    cur = nc_curve.build(rows, ctx.today, 12.0, None)
    w = cur["window"]
    print(f"curve window {w['from']}..{w['to']}: {w['days_used']} days used of {w['requested_days']}, ${w['total_nc_rev']:,.0f} NC revenue, {w['total_nc_orders']:,} orders; newest day {cur['stale']['newest_closed_day']}")
    print("cumulative share of the day's NC revenue in by (Pacific):")
    full = nc_curve.share_curve(nc_curve._by_day(rows, *nc_curve.window_dates(ctx.today, a.window)))
    for h in (6, 8, 10, 12, 14, 16, 18, 20, 22, 23):
        print(f"  {h:02d}:00  {full['cum'][h]:.1%}   (mean of daily ratios {full['mean_of_ratios'][h]:.1%})")
    d = cur["dow_check"]
    print(f"weekday vs weekend at 12:00: {d['weekday_share']:.1%} ({d['weekday_days']} d) vs {d['weekend_share']:.1%} ({d['weekend_days']} d), gap {d['gap_points']:+.1%}, differs={d['differs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
