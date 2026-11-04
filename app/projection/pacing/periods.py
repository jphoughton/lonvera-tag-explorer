"""Calendar helpers for pacing: Pacific "today", ISO weeks, month lengths, period keys.

The sheet's TODAY() is the Pacific date and its feeds run through yesterday, so `data_through = as_of - 1 day`. Tests and reconcile pin the date with
the PROJECTION_AS_OF environment variable (YYYY-MM-DD); nothing else changes the clock.
"""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
ENV_AS_OF = "PROJECTION_AS_OF"


def as_of(override: date | str | None = None) -> date:
    """The pacing "today": override, else PROJECTION_AS_OF, else the Pacific calendar date now."""
    if isinstance(override, date):
        return override
    raw = override or os.environ.get(ENV_AS_OF)
    if raw:
        return date.fromisoformat(str(raw))
    return datetime.now(PACIFIC).date()


def data_through(today: date, lag: int = 1) -> date:
    """Last day with complete data: `lag` days back, yesterday by default (today's feed rows are partial). Lever: pacing.data_lag_days."""
    return today - timedelta(days=lag)


def month_start(d: date) -> date:
    return d.replace(day=1)


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def month_end(d: date) -> date:
    return d.replace(day=days_in_month(d))


def add_months(d: date, n: int) -> date:
    k = d.year * 12 + d.month - 1 + n
    return date(k // 12, k % 12 + 1, 1)


def week_start(d: date) -> date:
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def day_key(d: date) -> str:
    return d.isoformat()


def iter_days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def default_month(today: date, lag: int = 1) -> date:
    """The month shown by default: the month holding the last complete day (so on the 1st it is the month that just ended)."""
    return month_start(data_through(today, lag))


def days_elapsed(month: date, today: date, lag: int = 1) -> int:
    """Full days of `month` with data: 0 for a future month, all of them for a past month, day-of-month of the last complete day for the current one."""
    thr = data_through(today, lag)
    if thr < month:
        return 0
    if thr >= month_end(month):
        return days_in_month(month)
    return thr.day
