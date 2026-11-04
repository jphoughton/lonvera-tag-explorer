"""Database access for inventory demand: read the loaded units, write loads (daily rows, monthly history, country split, stock snapshot).

Every function takes any psycopg connection and opens its own dict-row cursor, so it works with the app's pool, `rwdb.get_rw()` and the ETL
runner's connection alike. Writes never commit: the caller owns the transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from psycopg.rows import dict_row

from .. import audit
from ..interfaces import month_floor
from .constants import LOCATIONS
from .engine import DemandInputs


def _rows(conn, sql: str, args: tuple | list = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def _exec(conn, sql: str, args: tuple | list = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.rowcount


# ------------------------------------------------------------------------------------------------------------------ reads

def read_inputs(conn) -> DemandInputs:
    """All complete daily rows, the monthly history, estimate flags and each location's latest load."""
    inp = DemandInputs()
    for r in _rows(conn, "SELECT location, product, day, units, is_estimate FROM proj_units_daily"):
        inp.daily.setdefault((r["location"], r["product"]), {})[r["day"]] = float(r["units"])
        if r["is_estimate"]:
            inp.estimates.setdefault(r["location"], set()).add(r["day"])
    for r in _rows(conn, "SELECT location, product, month, units FROM proj_units_monthly"):
        inp.monthly.setdefault((r["location"], r["product"]), {})[r["month"]] = float(r["units"])
    for r in _rows(conn, "SELECT DISTINCT ON (location) location, at, source, rows, first_day, last_day, actor "
                         "FROM proj_units_loads ORDER BY location, id DESC"):
        inp.loads[r["location"]] = dict(r)
    return inp


def read_daily_window(conn, location: str, product: str | None, start: date, end: date) -> list[dict]:
    """[{day, units, is_estimate}] summed over products when `product` is None."""
    if product is None:
        return _rows(conn, "SELECT day, sum(units)::float AS units, bool_or(is_estimate) AS is_estimate FROM proj_units_daily "
                           "WHERE location = %s AND day BETWEEN %s AND %s GROUP BY day ORDER BY day", (location, start, end))
    return _rows(conn, "SELECT day, units::float AS units, is_estimate FROM proj_units_daily "
                       "WHERE location = %s AND product = %s AND day BETWEEN %s AND %s ORDER BY day", (location, product, start, end))


def read_countries(conn) -> list[dict]:
    return _rows(conn, "SELECT month, country, units::float AS units, days FROM proj_units_country ORDER BY month, units DESC, country")


def read_stock_snapshot(conn) -> list[dict]:
    """Latest Warehance stock snapshot per product at the US warehouse: [{location, product, as_of, on_hand, allocated}]."""
    return _rows(conn, "SELECT DISTINCT ON (location, product) location, product, as_of, on_hand::float AS on_hand, allocated::float AS allocated, source "
                       "FROM proj_demand_snapshot ORDER BY location, product, as_of DESC")


def data_range(conn, location: str) -> tuple[date | None, date | None]:
    r = _rows(conn, "SELECT min(day) AS first_day, max(day) AS last_day FROM proj_units_daily WHERE location = %s", (location,))[0]
    return r["first_day"], r["last_day"]


# ------------------------------------------------------------------------------------------------------------------ writes

@dataclass(frozen=True)
class DailyRow:
    location: str
    product: str
    day: date
    units: float
    source: str
    is_estimate: bool = False


def upsert_daily(conn, rows: Iterable[DailyRow]) -> tuple[int, int]:
    """Insert or update daily rows. Returns (rows seen, rows inserted or changed). A real count is never replaced by an estimate; a row
    whose values did not change is left alone (so loaded_at only moves when something moved)."""
    seen = changed = 0
    sql = ("INSERT INTO proj_units_daily (location, product, day, units, source, is_estimate) VALUES (%s,%s,%s,%s,%s,%s) "
           "ON CONFLICT (location, product, day) DO UPDATE SET units = EXCLUDED.units, source = EXCLUDED.source, is_estimate = EXCLUDED.is_estimate, "
           "loaded_at = now() "
           "WHERE (proj_units_daily.units, proj_units_daily.source, proj_units_daily.is_estimate) IS DISTINCT FROM (EXCLUDED.units, EXCLUDED.source, EXCLUDED.is_estimate) "
           "AND NOT (EXCLUDED.is_estimate AND NOT proj_units_daily.is_estimate)")
    with conn.cursor() as cur:
        for r in rows:
            if r.location not in LOCATIONS:
                raise ValueError(f"unknown location {r.location!r}")
            cur.execute(sql, (r.location, r.product, r.day, r.units, r.source, r.is_estimate))
            seen += 1
            changed += cur.rowcount
    return seen, changed


def delete_missing_in_window(conn, location: str, sources: list[str], first: date, last: date, keep: set[tuple[str, date]]) -> int:
    """Remove rows of this feed (loaded from `sources`) inside the window that the feed no longer reports; rows loaded by other sources
    (an upload, for example) stay."""
    have = _rows(conn, "SELECT product, day FROM proj_units_daily WHERE location = %s AND day BETWEEN %s AND %s AND source = ANY(%s)",
                 (location, first, last, sources))
    gone = [(location, r["product"], r["day"]) for r in have if (r["product"], r["day"]) not in keep]
    with conn.cursor() as cur:
        for loc, prod, day in gone:
            cur.execute("DELETE FROM proj_units_daily WHERE location = %s AND product = %s AND day = %s", (loc, prod, day))
    return len(gone)


def upsert_monthly(conn, rows: Iterable[tuple[str, str, date, float]], source: str) -> int:
    n = 0
    with conn.cursor() as cur:
        for loc, prod, month, units in rows:
            cur.execute("INSERT INTO proj_units_monthly (location, product, month, units, source) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (location, product, month) DO UPDATE SET units = EXCLUDED.units, source = EXCLUDED.source, loaded_at = now() "
                        "WHERE (proj_units_monthly.units, proj_units_monthly.source) IS DISTINCT FROM (EXCLUDED.units, EXCLUDED.source)",
                        (loc, prod, month_floor(month), units, source))
            n += cur.rowcount
    return n


def replace_countries(conn, rows: Iterable[tuple[date, str, float, int | None]], source: str) -> int:
    rows = list(rows)
    if not rows:
        return 0
    _exec(conn, "DELETE FROM proj_units_country WHERE month = ANY(%s)", ([r[0] for r in rows],))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_units_country (month, country, units, days, source) VALUES (%s,%s,%s,%s,%s)",
                        [(m, c, u, d, source) for m, c, u, d in rows])
    return len(rows)


def upsert_stock(conn, rows: Iterable[tuple[str, str, date, float, float]], source: str) -> int:
    n = 0
    with conn.cursor() as cur:
        for loc, prod, as_of, on_hand, allocated in rows:
            cur.execute("INSERT INTO proj_demand_snapshot (location, product, as_of, on_hand, allocated, source) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (location, product, as_of) DO UPDATE SET on_hand = EXCLUDED.on_hand, allocated = EXCLUDED.allocated, "
                        "source = EXCLUDED.source, loaded_at = now() "
                        "WHERE (proj_demand_snapshot.on_hand, proj_demand_snapshot.allocated) IS DISTINCT FROM (EXCLUDED.on_hand, EXCLUDED.allocated)",
                        (loc, prod, as_of, on_hand, allocated, source))
            n += cur.rowcount
    return n


def log_load(conn, *, location: str, source: str, rows: int, first_day: date | None, last_day: date | None, actor: str | None = None,
             note: str | None = None) -> None:
    _exec(conn, "INSERT INTO proj_units_loads (location, source, rows, first_day, last_day, actor, note) VALUES (%s,%s,%s,%s,%s,%s,%s)",
          (location, source, rows, first_day, last_day, actor or audit.current_actor(), note))


def audit_load(conn, *, action: str, summary: dict[str, Any], note: str | None = None, actor: str | None = None) -> None:
    """One proj_audit row for a load (the trail of what moved the demand numbers)."""
    audit.record(conn, actor=actor or audit.current_actor(), scenario=None, action=action, new=summary, note=note)
