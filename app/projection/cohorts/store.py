"""Cohort tables in Postgres: read the cohort table and Goal curve, upsert rows. Parameterized SQL only.

Reads work on any connection (dict rows). Writes take a read-write connection inside one transaction (rwdb.get_rw) and are audited by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Sequence

from ..freshness import today_pacific
from ..interfaces import Series
from .engine import COHORT_AGES, CURVE_AGES, Cohort


def _f(v) -> float | None:
    return None if v is None else float(v)


# ---------------------------------------------------------------------------------------------------------------- reads

def load_cohorts(conn) -> list[Cohort]:
    heads = conn.execute(
        "SELECT cohort_month, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, payback_months, nc_rev_reference "
        "FROM proj_cohorts ORDER BY cohort_month").fetchall()
    cum: dict[date, list[float | None]] = {}
    for r in conn.execute("SELECT cohort_month, age, cum_per_customer FROM proj_cohort_cum").fetchall():
        cum.setdefault(r["cohort_month"], [None] * COHORT_AGES)[r["age"] - 1] = float(r["cum_per_customer"])
    return [Cohort(month=h["cohort_month"], new_customers=float(h["new_customers"]), first_order=_f(h["first_order_per_customer"]),
                   first_order_realized=_f(h["first_order_realized"]), repeat_90d=_f(h["repeat_90d"]), cac=_f(h["cac"]),
                   payback_months=_f(h["payback_months"]), cum=tuple(cum.get(h["cohort_month"], [None] * COHORT_AGES)),
                   nc_rev_reference=_f(h["nc_rev_reference"]))
            for h in heads]


def load_goal(conn) -> tuple[list[float], date | None]:
    """(Goal curve for ages 1..60 - zeros when none is loaded, snapshot date of the newest snapshot)."""
    snap = conn.execute("SELECT max(snapshot_date) AS d FROM proj_curve_goal").fetchone()["d"]
    goal = [0.0] * CURVE_AGES
    if snap is not None:
        for r in conn.execute("SELECT age, value FROM proj_curve_goal WHERE snapshot_date = %s", (snap,)).fetchall():
            goal[r["age"] - 1] = float(r["value"])
    return goal, snap


def first_order_revenue(conn) -> Series:
    """New-customer revenue by cohort month = new customers x first order (for the drivers module's actual months)."""
    return {c.month: c.first_order_rev for c in load_cohorts(conn) if c.first_order is not None}


def new_customers(conn) -> Series:
    return {c.month: c.new_customers for c in load_cohorts(conn)}


# ---------------------------------------------------------------------------------------------------------------- writes

@dataclass
class CohortRow:
    """One cohort as it is written: head fields (None = leave as is on update) and observed cumulative cells {age: cum $/customer}."""
    month: date
    new_customers: float
    first_order: float | None = None
    first_order_realized: float | None = None
    repeat_90d: float | None = None
    cac: float | None = None
    payback_months: float | None = None
    nc_rev_reference: float | None = None
    cum: dict[int, float] = field(default_factory=dict)


def upsert_cohorts(conn, rows: Iterable[CohortRow], source: str) -> dict:
    """Insert or update cohort rows and their cumulative cells. Head fields given as None keep the stored value; cumulative cells absent from
    the row keep the stored value (an export can add and revise cells, never silently erase them). Returns counts."""
    n_heads = n_cells = 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, "
                "payback_months, nc_rev_reference, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (cohort_month) DO UPDATE SET new_customers = EXCLUDED.new_customers, "
                "first_order_per_customer = COALESCE(EXCLUDED.first_order_per_customer, proj_cohorts.first_order_per_customer), "
                "first_order_realized = COALESCE(EXCLUDED.first_order_realized, proj_cohorts.first_order_realized), "
                "repeat_90d = COALESCE(EXCLUDED.repeat_90d, proj_cohorts.repeat_90d), cac = COALESCE(EXCLUDED.cac, proj_cohorts.cac), "
                "payback_months = COALESCE(EXCLUDED.payback_months, proj_cohorts.payback_months), "
                "nc_rev_reference = COALESCE(EXCLUDED.nc_rev_reference, proj_cohorts.nc_rev_reference), "
                "source = EXCLUDED.source, loaded_at = now()",
                (r.month, r.new_customers, r.first_order, r.first_order_realized, r.repeat_90d, r.cac, r.payback_months,
                 r.nc_rev_reference, source))
            n_heads += 1
            cells = [(r.month, a, v) for a, v in sorted(r.cum.items()) if 1 <= a <= COHORT_AGES and v is not None]
            if cells:
                cur.executemany(
                    "INSERT INTO proj_cohort_cum (cohort_month, age, cum_per_customer) VALUES (%s,%s,%s) "
                    "ON CONFLICT (cohort_month, age) DO UPDATE SET cum_per_customer = EXCLUDED.cum_per_customer", cells)
                n_cells += len(cells)
    return {"cohorts": n_heads, "cells": n_cells}


def replace_goal(conn, snapshot_date: date, values: Sequence[float], note: str | None = None) -> int:
    """Write (or rewrite) the Goal curve snapshot for `snapshot_date`; ages 1..len(values)."""
    conn.execute("DELETE FROM proj_curve_goal WHERE snapshot_date = %s", (snapshot_date,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_curve_goal (snapshot_date, age, value, note) VALUES (%s,%s,%s,%s)",
                        [(snapshot_date, i + 1, float(v), note) for i, v in enumerate(values[:CURVE_AGES])])
    return min(len(values), CURVE_AGES)


# ---------------------------------------------------------------------------------------------------------------- freshness

STALE_AFTER_DAYS = 45


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def freshness(conn, today: date | None = None) -> list[dict]:
    """How current the cohort export is. The feed loads by monthly upload (no daily pull).

    `data_through` = last day of the last fully closed cohort month with data, so the shared freshness check (age > stale_after_days) gives a
    month about two weeks to be uploaded after it closes: loaded through Oct, fresh until Dec 15, stale from Dec 16.
    `behind` is the literal rule: the last closed calendar month is not loaded (either the cohort frontier or the last month a loaded cumulative
    cell lands in is older than it). `data_through_month` / `observed_through` are first-of-month dates for the page."""
    from .engine import data_through, last_closed_month, observed_through
    today = today or today_pacific()
    cohorts = load_cohorts(conn)
    closed = last_closed_month(today)
    through = data_through(cohorts, today)
    observed = observed_through(cohorts, today)
    frontier = min(through, observed) if through and observed else None
    end = _month_end(through) if through else None
    return [{
        "feed": "cohort_export", "label": "Cohort export", "cadence": "monthly-upload",
        "data_through": end, "data_through_month": through, "observed_through": observed, "last_closed_month": closed,
        "behind": frontier is None or frontier < closed,
        "stale": end is None or (today - end).days > STALE_AFTER_DAYS,
        "stale_after_days": STALE_AFTER_DAYS,
        "age_days": (today - end).days if end else None,
        "next_close": _month_end(today) + timedelta(days=1),     # the open month's cohort can be loaded from this date
        "cohorts": len(cohorts),
    }]
