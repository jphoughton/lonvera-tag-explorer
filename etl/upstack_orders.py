"""Upstack orders per Pacific day: new-customer revenue counted once (the business's actual NC revenue, not the platforms' attributed sums).

Same source and buckets as the pacing 'NC Rev' (feeds `orders`: /api/query, granularity day, America/Los_Angeles). Read by Marketing metrics' Total NC ROAS
(app/platforms.py). Part of the nightly etl.upstack_daily; a trailing window is replaced on every pull because Upstack restates recent days.
"""
from __future__ import annotations

import datetime as dt

from . import upstack_api as api

MEASURES = ("orders.new_customer_total_revenue", "orders.returning_customer_total_revenue", "orders.new_customers", "orders.total_revenue", "orders.count")
CHUNK_DAYS = 60
FIRST_DAY = dt.date(2025, 1, 1)                   # default backfill start (Meta's daily data starts 2026-05-06, Google's feed 2024-03-25)


def pull(client: api.Client, since: dt.date, until: dt.date) -> list[tuple]:
    """[(day, nc_rev, rc_rev, nc_orders, total_rev, orders)] for since..until (days Upstack has no orders for are absent)."""
    out: dict[dt.date, tuple] = {}
    d = since
    while d <= until:
        e = min(d + dt.timedelta(days=CHUNK_DAYS - 1), until)
        body = {"pixelId": client.pixel, "granularity": "day", "timezone": api.TIMEZONE, "dateRange": {"start": d.isoformat(), "end": e.isoformat()},
                "measures": [{"measure": m} for m in MEASURES]}
        res = client.post("/api/query", body, f"orders {d}..{e}").get("results") or []
        for p in ((res[0].get("data") or {}).get("primary") or []) if res else []:
            day = dt.date.fromisoformat(str(p["dt"])[:10])
            vals = [api.num(p.get(m)) for m in MEASURES]
            if any(vals):
                out[day] = (day, round(vals[0], 2), round(vals[1], 2), vals[2], round(vals[3], 2), vals[4])
        d = e + dt.timedelta(days=1)
    return sorted(out.values())


def write(conn, rows: list[tuple], since: dt.date, until: dt.date) -> None:
    conn.execute("DELETE FROM upstack_orders_daily WHERE day BETWEEN %s AND %s", (since, until))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO upstack_orders_daily (day, nc_rev, rc_rev, nc_orders, total_rev, orders) VALUES (%s,%s,%s,%s,%s,%s)", rows)


def run(conn, client: api.Client, since: dt.date, until: dt.date, dry_run: bool = False) -> dict:
    rows = pull(client, since, until)
    if not rows and (until - since).days >= 2:
        raise api.UpstackError(f"Upstack returned no orders for {since}..{until}: refusing to replace the stored days with nothing")
    if not dry_run:
        write(conn, rows, since, until)
    return {"days": len(rows), "from": rows[0][0].isoformat() if rows else None, "to": rows[-1][0].isoformat() if rows else None,
            "nc_rev": round(sum(r[1] for r in rows), 2), "dry_run": dry_run}
