"""Projected end-of-day NC ROAS and NC CPA for the 'Projected NC ROAS and NC CPA today' card on Projections > Pacing.

Definitions (the same as Total NC ROAS and NC CPA on the Trends page, app/trends.py):
    NC ROAS = new-customer revenue / total spend (Meta + Google)
    NC CPA  = total spend / new customers
Projected inputs:
    Meta end of day      = Meta so far / share of a typical day's Meta spend out by now (hourly curve, month.intraday_projection)
    NC revenue end of day = NC revenue so far / revenue share curve (nc_curve.build, 30 closed days)
    New customers end of day = first orders so far / ORDER-COUNT share curve (nc_curve.build_orders, same days and weekday switch)
    Google end of day    = Google so far x (Meta end of day / Meta so far). Google has no hourly curve; this assumes it follows Meta's shape.
Pure functions, no database.
"""
from __future__ import annotations

import datetime as dt
from typing import Sequence

from . import month as month_mod
from . import nc_curve


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def project(*, meta_hourly: Sequence[float], meta_spent: float | None, meta_hours: float,
            nc_rows: Sequence, today: dt.date, nc_rev: float | None, nc_orders: float | None, nc_hours: float,
            google: float | None, dow: str = "all") -> dict:
    """Everything the card shows. Missing inputs give None for what depends on them, never a guess."""
    meta_eod = None
    if meta_spent is not None:
        meta_eod = month_mod.intraday_projection(list(meta_hourly), meta_spent, meta_hours).get("projected_eod")
    rev = nc_curve.build(nc_rows, today, nc_hours, nc_rev, dow)
    rev_eod = rev.get("projected_eod")
    ords = nc_curve.build_orders(nc_rows, today, nc_hours, nc_orders, dow)
    ord_eod = ords.get("projected_eod")
    google_eod = None
    if google is not None and meta_eod is not None and meta_spent is not None:
        google_eod = 0.0 if google == 0 else (google * meta_eod / meta_spent if meta_spent > 0 else None)
    google_in = google is not None and google_eod is not None
    total_eod = None
    if meta_eod is not None and (google is None or google_eod is not None):
        total_eod = meta_eod + (google_eod or 0.0)
    total_sofar = None if meta_spent is None else meta_spent + (google or 0.0)
    return {
        "meta_eod": meta_eod, "google_eod": google_eod, "google_included": bool(google_in), "total_spend_eod": total_eod,
        "nc_rev_eod": rev_eod, "nc_orders_eod": ord_eod,
        "nc_roas_eod": _div(rev_eod, total_eod) if total_eod else None,
        "nc_cpa_eod": _div(total_eod, ord_eod) if ord_eod else None,
        "total_spend_so_far": total_sofar,
        "nc_roas_so_far": _div(nc_rev, total_sofar) if nc_rev is not None else None,
        "nc_cpa_so_far": _div(total_sofar, nc_orders) if nc_orders else None,
        "orders_share": ords.get("share"), "orders_reason": ords.get("reason"), "rev_loaded": rev.get("loaded"),
        "rev_reason": rev.get("reason"),
    }
