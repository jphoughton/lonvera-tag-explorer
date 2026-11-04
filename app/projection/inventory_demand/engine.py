"""Unit demand forecast by location and product, month by month.

The sheet's rule (Warehance / Amazon / Intl Demand Fcst), generalised to any month:

    baseline          = units in the trailing `baseline_days` complete days / baseline_days            (units per day)
    rate(month)       = baseline x (1 + monthly_growth) ^ months_since_as_of_month x seasonality[calendar month]
    units(month)      = actual units shipped so far in the month + rate x days not yet covered by actuals
                        (months before the one the actuals end in are pure actuals; later months are pure forecast)

`as_of` is the sheet's TODAY(): the first of its month is month 0 of growth. Everything stored is a complete day (loaders keep the partial tail
out), so `last complete day` = the newest stored day minus `lag_days`.

Pure functions over plain dicts: no database, no Flask. `compute_demand` returns a `DemandDetail` (a `DemandResult` plus the intermediate
numbers the page and the reconcile map read).
"""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..interfaces import MONTHS, DemandResult, Month, Series, month_floor
from . import levers as lv
from .constants import LOCATIONS, location_products

PACIFIC = ZoneInfo("America/Los_Angeles")
ENV_TODAY = "PROJECTION_TODAY"
EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class DemandInputs:
    """What the store loads: complete daily units, monthly history and the estimate flags."""
    daily: dict[tuple[str, str], dict[date, float]] = field(default_factory=dict)       # (location, product) -> day -> units
    estimates: dict[str, set[date]] = field(default_factory=dict)                       # location -> days that are estimates
    monthly: dict[tuple[str, str], dict[Month, float]] = field(default_factory=dict)    # (location, product) -> month -> units
    loads: dict[str, dict] = field(default_factory=dict)                                # location -> latest load {at, source, rows, last_day}


@dataclass
class DemandDetail(DemandResult):
    """DemandResult plus the working numbers. `units[(loc, prod)]` is the full-month forecast; `units[(loc + '_mtd', prod)]` holds the actual
    units shipped so far in the as-of month (one entry), so a consumer can burn only the remaining part."""
    as_of: date | None = None
    current_month: Month | None = None
    last_full_day: dict[str, date | None] = field(default_factory=dict)
    first_day: dict[str, date | None] = field(default_factory=dict)
    window: dict[str, tuple[date, date] | None] = field(default_factory=dict)
    serial: dict[str, float] = field(default_factory=dict)              # last complete day as a spreadsheet serial number
    window_start_serial: dict[str, float] = field(default_factory=dict)
    rates: dict[tuple[str, str], float] = field(default_factory=dict)   # baseline units per day (typed rate when one is set)
    window_units: dict[tuple[str, str], float] = field(default_factory=dict)
    mtd: dict[tuple[str, str], float] = field(default_factory=dict)     # actual units in the as-of month through the last complete day
    actual_days: dict[str, int] = field(default_factory=dict)           # days of the as-of month covered by actuals
    daily_rate: dict[tuple[str, str], Series] = field(default_factory=dict)   # units per day each forward month (after growth, seasonality)
    estimate_days_in_window: dict[str, int] = field(default_factory=dict)
    location_total: dict[str, Series] = field(default_factory=dict)
    product_total: dict[str, Series] = field(default_factory=dict)
    total: Series = field(default_factory=dict)
    share: dict[str, Series] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------------ dates

def today_pacific() -> date:
    return datetime.now(PACIFIC).date()


def resolve_as_of(params: dict | None, today: date | None = None) -> date:
    """The forecast date: the scenario's pinned `as_of`, else env PROJECTION_TODAY (reconcile and tests), else today in Pacific time."""
    pinned = lv.parse(params).as_of
    if pinned is not None:
        return pinned
    env = lv.parse_date(os.environ.get(ENV_TODAY))
    return env or today or today_pacific()


def days_in(m: Month) -> int:
    return calendar.monthrange(m.year, m.month)[1]


def month_end(m: Month) -> date:
    return date(m.year, m.month, days_in(m))


def months_between(a: Month, b: Month) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def serial(d: date) -> float:
    return float((d - EXCEL_EPOCH).days)


# ------------------------------------------------------------------------------------------------------------------ pieces

def _day_sum(by_day: dict[date, float], start: date, end: date) -> float:
    return sum(v for d, v in by_day.items() if start <= d <= end)


def _rev_per_day(rev: Series | None, d: date) -> float:
    return (rev or {}).get(month_floor(d), 0.0) / days_in(month_floor(d))


def _reference_revenue(rev: Series, window: tuple[date, date]) -> float:
    """Average daily revenue over the baseline window (each day carries its month's revenue / days in month)."""
    start, end = window
    n = (end - start).days + 1
    return sum(_rev_per_day(rev, start + timedelta(days=i)) for i in range(n)) / n if n > 0 else 0.0


def _tie_factor(rev: Series | None, ref: float, m: Month) -> float:
    if not rev or ref <= 0:
        return 1.0
    return max(0.0, (rev.get(m, 0.0) / days_in(m)) / ref)


# ------------------------------------------------------------------------------------------------------------------ main

def compute_demand(inputs: DemandInputs, params: dict | None, *, as_of: date | None = None, dtc_revenue: Series | None = None) -> DemandDetail:
    """The forecast for every location and product. `params` = scenario effective params; `dtc_revenue` = nc_rev + repeat_rev per month."""
    levers = lv.parse(params)
    as_of = as_of or resolve_as_of(params)
    m0 = month_floor(as_of)
    out = DemandDetail(as_of=as_of, current_month=m0)

    for loc in LOCATIONS:
        L = levers.loc(loc)
        seen = {p for (l, p) in list(inputs.daily) + list(inputs.monthly) if l == loc}
        seen |= {p for p, pl in L.products.items() if pl.rate is not None}
        products = location_products(loc, seen) if seen else ()
        days_all = [d for p in products for d in inputs.daily.get((loc, p), {})]
        first_day = min(days_all) if days_all else None
        newest = max(days_all) if days_all else None
        last_full = newest - timedelta(days=L.lag_days) if newest is not None else None
        window = (last_full - timedelta(days=L.baseline_days - 1), last_full) if last_full is not None else None
        out.first_day[loc], out.last_full_day[loc], out.window[loc] = first_day, last_full, window
        if last_full is not None:
            out.serial[loc] = serial(last_full)
            out.window_start_serial[loc] = serial(window[0])
        est = inputs.estimates.get(loc, set())
        out.estimate_days_in_window[loc] = sum(1 for d in est if window and window[0] <= d <= window[1])

        if not products:
            continue
        monthly_all = [m for p in products for m in inputs.monthly.get((loc, p), {})]
        starts = ([month_floor(first_day)] if first_day is not None else []) + monthly_all
        first_month = min(starts) if starts else m0
        last_month = month_floor(last_full) if last_full is not None else m0
        ref = _reference_revenue(dtc_revenue, window) if (L.tie_to_dtc_revenue and dtc_revenue and window) else 0.0
        if L.tie_to_dtc_revenue and ref <= 0:
            out.warnings.append(f"{loc}: tied to revenue, but no projected revenue overlaps the baseline window, so units are not scaled")
        out.actual_days[loc] = max(0, min(days_in(m0), (last_full - m0).days + 1)) if last_full is not None and last_full >= m0 else 0

        for prod in products:
            by_day = inputs.daily.get((loc, prod), {})
            by_month = inputs.monthly.get((loc, prod), {})
            prod_first = min(by_day) if by_day else None                  # a product's daily history may start later than the location's
            wsum = _day_sum(by_day, *window) if window else 0.0
            override = L.rate_override(prod)
            rate = override if override is not None else wsum / L.baseline_days
            out.window_units[(loc, prod)] = wsum
            out.rates[(loc, prod)] = rate
            growth, season = L.growth(prod), L.season(prod)
            units: Series = {}
            dr: Series = {}
            for m in MONTHS:
                if m < first_month:
                    continue
                eom = month_end(m)
                if m < last_month:                                   # closed month with actuals only
                    covered = prod_first is not None and last_full is not None and prod_first <= m and eom <= last_full
                    if covered:
                        v = _day_sum(by_day, m, eom)
                    elif m in by_month:
                        v = by_month[m]
                    else:
                        v = _day_sum(by_day, m, eom)
                else:                                                # the month the actuals end in, and every month after it
                    actual = _day_sum(by_day, m, min(eom, last_full)) if last_full is not None and last_full >= m else 0.0
                    covered_days = (min(eom, last_full) - m).days + 1 if last_full is not None and last_full >= m else 0
                    k = max(0, months_between(m0, m))
                    r = rate * (1.0 + growth) ** k * season[m.month - 1]
                    if L.tie_to_dtc_revenue and m >= m0:
                        r *= _tie_factor(dtc_revenue, ref, m)
                    v = actual + max(0, days_in(m) - covered_days) * r
                    if m >= min(m0, last_month):
                        dr[m] = r
                units[m] = v
            out.units[(loc, prod)] = units
            out.daily_rate[(loc, prod)] = dr
            mtd = _day_sum(by_day, m0, min(month_end(m0), last_full)) if last_full is not None and last_full >= m0 else 0.0
            out.mtd[(loc, prod)] = mtd
            out.units[(loc + "_mtd", prod)] = {m0: mtd}

    _totals(out)
    return out


def _totals(out: DemandDetail) -> None:
    """Location, product and grand totals plus each location's share of the grand total (zero denominators give 0)."""
    for (loc, prod), s in list(out.units.items()):
        if loc not in LOCATIONS:
            continue
        for key, bucket in ((loc, out.location_total), (prod, out.product_total)):
            t = bucket.setdefault(key, {})
            for m, v in s.items():
                t[m] = t.get(m, 0.0) + v
    for s in out.location_total.values():
        for m, v in s.items():
            out.total[m] = out.total.get(m, 0.0) + v
    for loc, s in out.location_total.items():
        out.share[loc] = {m: (v / out.total[m] if out.total.get(m) else 0.0) for m, v in s.items()}
