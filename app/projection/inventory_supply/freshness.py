"""Data freshness of the manual inputs: purchase orders (monthly upload) and the stock counts per location."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg

from .. import clock

# feed key -> (label, cadence, stale_after_days)
FEEDS = {
    "purchase_orders": ("Purchase orders", "monthly-upload", 30),
    "stock_revamp": ("Revamp stock on hand", "daily", 2),
    "stock_fba": ("Amazon FBA stock on hand", "manual", 14),
    "stock_intl": ("International 3PL stock on hand (estimate)", "manual", 14),
}
_LOC_FEED = {"revamp": "stock_revamp", "amazon": "stock_fba", "intl": "stock_intl"}


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def freshness(conn, today: date | None = None) -> list[dict]:
    """[{feed, label, cadence, data_through, stale_after_days, age_days, status}] for the manual inputs; status = fresh | STALE | missing."""
    today = today or clock.today_pt()
    data: dict[str, date | None] = {k: None for k in FEEDS}
    estimate = False
    try:
        r = conn.execute("SELECT GREATEST((SELECT max(updated_at) FROM proj_po), (SELECT max(updated_at) FROM proj_fba_transfer)) AS d").fetchone()
        data["purchase_orders"] = _day(r["d"] if r else None)
        for r in conn.execute("SELECT location, max(as_of) AS d FROM proj_stock_snapshot GROUP BY location").fetchall():
            if r["location"] in _LOC_FEED:
                data[_LOC_FEED[r["location"]]] = r["d"]
        est = conn.execute("SELECT 1 FROM proj_stock_snapshot s WHERE s.location = 'intl' AND s.estimate "
                           "AND s.as_of = (SELECT max(as_of) FROM proj_stock_snapshot WHERE location = 'intl')").fetchone()
        estimate = est is not None
    except psycopg.errors.UndefinedTable:
        conn.rollback()
    out = []
    for feed, (label, cadence, stale) in FEEDS.items():
        d = data[feed]
        age = (today - d).days if d else None
        status = "missing" if d is None else ("STALE" if age is not None and age > stale else "fresh")
        lab = label if feed != "stock_intl" or estimate else "International 3PL stock on hand"
        out.append({"feed": feed, "label": lab, "cadence": cadence, "data_through": d.isoformat() if d else None, "stale_after_days": stale,
                    "age_days": age, "status": status})
    return out
