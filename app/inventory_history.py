"""Inventory history (/inventory/history): units on hand and allocated for any past day, per product and location, plus a daily trend per location.

GET /api/inventory/history?date=YYYY-MM-DD&days=N   (date defaults to the newest day with any snapshot, days to 30, at most 180)

Source: proj_demand_snapshot (view analytics.inventory_stock_daily), one row per location x product x day. The US warehouse rows come from Warehance every night;
Amazon and the international warehouse are estimates (etl/inventory_snapshots.py: the latest typed count minus units shipped since, plus landed receipts) except on
the day a count was typed. A row is an estimate when its source starts with `estimate`. A location that has a series but no row on a day of the range is a gap and
is listed, never filled with a guess; days before a location's first snapshot are not gaps (the series had not started).
"""
from __future__ import annotations

import datetime as dt

from . import queries as q

LOCATIONS = (("revamp", "US warehouse"), ("amazon", "Amazon (fulfilled by Amazon)"), ("intl", "International warehouse"))
PRODUCTS = (("bull", "Vital Bull"), ("liver", "Beef Liver"), ("boneflex", "Boneflex"), ("belle", "Vital Belle"), ("flow", "Vital Flow"))
DEFAULT_DAYS = 30
MAX_DAYS = 180


def is_estimate(source: str | None) -> bool:
    return str(source or "").startswith("estimate")


def _date(args, name: str) -> dt.date | None:
    v = args.get(name)
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise q.ApiError(400, f"{name} must be a date like 2026-11-04") from None


def parse(args) -> tuple[dt.date | None, int]:
    d = _date(args, "date")
    raw = args.get("days")
    if raw in (None, ""):
        return d, DEFAULT_DAYS
    try:
        n = int(raw)
    except ValueError:
        raise q.ApiError(400, "days must be a whole number") from None
    if not 1 <= n <= MAX_DAYS:
        raise q.ApiError(400, f"days must be between 1 and {MAX_DAYS}")
    return d, n


def build(rows_range: list[dict], first_days: dict, picked: dt.date, days: int, newest: dt.date | None) -> dict:
    """Pure payload builder. rows_range = every snapshot row of the range (location, product, as_of, on_hand, allocated, source); first_days = {location: first day of its series}."""
    start = picked - dt.timedelta(days=days - 1)
    by_day: dict[tuple, list[dict]] = {}
    for r in rows_range:
        by_day.setdefault((r["location"], r["as_of"]), []).append(r)

    snap_rows = []
    for r in sorted((r for r in rows_range if r["as_of"] == picked), key=lambda r: ([k for k, _ in LOCATIONS].index(r["location"]) if r["location"] in dict(LOCATIONS) else 99, r["product"])):
        snap_rows.append({"location": r["location"], "product": r["product"], "on_hand": float(r["on_hand"]), "allocated": float(r["allocated"]),
                          "source": r["source"], "estimate": is_estimate(r["source"])})
    missing_on_picked = [k for k, _ in LOCATIONS if k in first_days and first_days[k] <= picked and (k, picked) not in by_day]

    trend = []
    for loc, label in LOCATIONS:
        first = first_days.get(loc)
        series, gaps = [], []
        d = start
        while d <= picked:
            rs = by_day.get((loc, d))
            if rs:
                series.append({"day": d.isoformat(), "on_hand": sum(float(r["on_hand"]) for r in rs), "allocated": sum(float(r["allocated"]) for r in rs),
                               "estimate": any(is_estimate(r["source"]) for r in rs)})
            else:
                series.append({"day": d.isoformat(), "on_hand": None, "allocated": None, "estimate": False})
                if first is not None and d >= first:
                    gaps.append(d.isoformat())
            d += dt.timedelta(days=1)
        trend.append({"location": loc, "label": label, "first_day": first.isoformat() if first else None, "series": series, "gaps": gaps})

    return {"date": picked.isoformat(), "newest": newest.isoformat() if newest else None, "from": start.isoformat(), "days": days,
            "locations": [{"key": k, "label": v, "first_day": first_days[k].isoformat() if k in first_days else None} for k, v in LOCATIONS],
            "products": [{"key": k, "label": v} for k, v in PRODUCTS],
            "snapshot": snap_rows, "missing_on_date": missing_on_picked, "trend": trend}


def history(conn, args) -> dict:
    picked, days = parse(args)
    meta = conn.execute("SELECT max(as_of) AS newest FROM proj_demand_snapshot").fetchone()
    newest = meta["newest"] if meta else None
    firsts = {r["location"]: r["first_day"] for r in conn.execute("SELECT location, min(as_of) AS first_day FROM proj_demand_snapshot GROUP BY location").fetchall()}
    if picked is None:
        picked = newest
    if picked is None:                                     # nothing loaded at all
        return {"date": None, "newest": None, "from": None, "days": days, "locations": [{"key": k, "label": v, "first_day": None} for k, v in LOCATIONS],
                "products": [{"key": k, "label": v} for k, v in PRODUCTS], "snapshot": [], "missing_on_date": [], "trend": []}
    start = picked - dt.timedelta(days=days - 1)
    rows = conn.execute("SELECT location, product, as_of, on_hand, allocated, source FROM proj_demand_snapshot WHERE as_of BETWEEN %s AND %s", (start, picked)).fetchall()
    return build(rows, firsts, picked, days, newest)
