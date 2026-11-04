"""Database access for the pnl module: reads (inputs for the engine, driver table, QuickBooks map) and writes (QuickBooks load, QuickBooks map).

Reads work on any connection (rows are dicts). Writes take a read-write connection from rwdb.get_rw() and audit what they do.
"""
from __future__ import annotations

import re
import calendar
from datetime import date
from typing import Iterable

from psycopg.types.json import Jsonb

from .. import audit, series
from ..interfaces import AXIS_END, AXIS_START, Scenario, add_months
from . import lines as L
from .model import PnlInputs

_WS = re.compile(r"\s+")


def norm_account(name: str) -> str:
    """Key of a QuickBooks account name: lower-cased, whitespace collapsed, a trailing ':' dropped."""
    return _WS.sub(" ", str(name or "").strip().rstrip(":")).strip().lower()


# ---------------------------------------------------------------------------------------------------------------- reads

def load_actuals(conn) -> dict[str, dict[date, float]]:
    """{line key: {month: value}} for every QuickBooks line, from proj_actuals 'pnl.<key>' (axis months only)."""
    raw = series.get_actuals(conn, prefix=L.ACTUALS_PREFIX, from_=AXIS_START, to=AXIS_END)
    out: dict[str, dict[date, float]] = {}
    for k, s in raw.items():
        key = k[len(L.ACTUALS_PREFIX):]
        if key in L.QB_KEYS and s:
            out[key] = s
    return out


def load_overrides(conn, scn: Scenario | str | None) -> dict[str, dict[date, float]]:
    raw = series.get_series(conn, scn, prefix=L.ACTUALS_PREFIX)
    return {k[len(L.ACTUALS_PREFIX):]: s for k, s in raw.items() if k[len(L.ACTUALS_PREFIX):] in L.LINE_KEYS and s}


def load_driver_defaults(conn) -> dict[str, list[dict]]:
    return {r["line_key"]: r["segments"] for r in conn.execute("SELECT line_key, segments FROM proj_pnl_drivers").fetchall()}


def load_driver_notes(conn) -> dict[str, dict]:
    return {r["line_key"]: {"note": r["note"], "source": r["source"]}
            for r in conn.execute("SELECT line_key, note, source FROM proj_pnl_drivers").fetchall()}


def load_settings(conn) -> dict[str, dict]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM proj_pnl_settings").fetchall()}


def load_cogs_sku(conn) -> list[dict]:
    rows = conn.execute("SELECT sku, label, china_unit_cost, us_unit_cost, sales_mix, in_stock_qty, note FROM proj_pnl_cogs_sku ORDER BY sort, sku").fetchall()
    return [{**r, "china_unit_cost": float(r["china_unit_cost"]), "us_unit_cost": float(r["us_unit_cost"]), "sales_mix": float(r["sales_mix"]),
             "in_stock_qty": float(r["in_stock_qty"]) if r["in_stock_qty"] is not None else None} for r in rows]


def load_vendors(conn) -> list[dict]:
    rows = conn.execute("SELECT vendor, current_monthly, proposed_monthly, note FROM proj_pnl_vendors ORDER BY sort, vendor").fetchall()
    return [{"vendor": r["vendor"], "current": float(r["current_monthly"]) if r["current_monthly"] is not None else None,
             "proposed": float(r["proposed_monthly"]) if r["proposed_monthly"] is not None else None, "note": r["note"]} for r in rows]


def load_qb_map(conn) -> dict[str, str]:
    return {r["account"]: r["line_key"] for r in conn.execute("SELECT account, line_key FROM proj_pnl_qb_map").fetchall()}


def today() -> date:                                                   # a seam for tests
    return date.today()


def load_inputs(conn, scn: Scenario | None = None, today_: date | None = None) -> PnlInputs:
    return PnlInputs(actuals=load_actuals(conn), overrides=load_overrides(conn, scn), drivers=load_driver_defaults(conn),
                     settings=load_settings(conn), cogs_sku=load_cogs_sku(conn), today=today_ or today())


def actual_sources(conn) -> dict[date, str]:
    """month -> source label of the loaded P&L lines (for the grid's note)."""
    rows = conn.execute("SELECT month, max(source) AS source FROM proj_actuals WHERE left(line_key, 4) = 'pnl.' GROUP BY month ORDER BY month").fetchall()
    return {r["month"]: r["source"] for r in rows}


# ---------------------------------------------------------------------------------------------------------------- writes

def upsert_driver_defaults(conn, rows: Iterable[tuple[str, list[dict], str | None, str | None]]) -> int:
    """(line_key, segments, note, source) rows into proj_pnl_drivers. Idempotent."""
    n = 0
    with conn.cursor() as cur:
        for key, segs, note, source in rows:
            cur.execute("INSERT INTO proj_pnl_drivers (line_key, segments, note, source) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (line_key) DO UPDATE SET segments = EXCLUDED.segments, note = EXCLUDED.note, source = EXCLUDED.source, updated_at = now()",
                        (key, Jsonb(segs), note, source))
            n += 1
    return n


def upsert_settings(conn, rows: Iterable[tuple[str, dict, str | None]]) -> int:
    n = 0
    with conn.cursor() as cur:
        for key, value, note in rows:
            cur.execute("INSERT INTO proj_pnl_settings (key, value, note) VALUES (%s,%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, note = EXCLUDED.note, updated_at = now()", (key, Jsonb(value), note))
            n += 1
    return n


def upsert_cogs_sku(conn, rows: Iterable[dict]) -> int:
    n = 0
    with conn.cursor() as cur:
        for i, r in enumerate(rows):
            cur.execute("INSERT INTO proj_pnl_cogs_sku (sku, label, china_unit_cost, us_unit_cost, sales_mix, in_stock_qty, sort, note) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (sku) DO UPDATE SET label = EXCLUDED.label, "
                        "china_unit_cost = EXCLUDED.china_unit_cost, us_unit_cost = EXCLUDED.us_unit_cost, sales_mix = EXCLUDED.sales_mix, "
                        "in_stock_qty = EXCLUDED.in_stock_qty, sort = EXCLUDED.sort, note = EXCLUDED.note, updated_at = now()",
                        (r["sku"], r["label"], r.get("china_unit_cost", 0), r.get("us_unit_cost", 0), r.get("sales_mix", 0),
                         r.get("in_stock_qty"), r.get("sort", i), r.get("note")))
            n += 1
    return n


def upsert_vendors(conn, rows: Iterable[dict]) -> int:
    n = 0
    with conn.cursor() as cur:
        for i, r in enumerate(rows):
            cur.execute("INSERT INTO proj_pnl_vendors (vendor, current_monthly, proposed_monthly, sort, note) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (vendor) DO UPDATE SET current_monthly = EXCLUDED.current_monthly, proposed_monthly = EXCLUDED.proposed_monthly, "
                        "sort = EXCLUDED.sort, note = EXCLUDED.note", (r["vendor"], r.get("current"), r.get("proposed"), i, r.get("note")))
            n += 1
    return n


def upsert_qb_map(conn, rows: Iterable[tuple[str, str, str | None]], *, actor: str | None = None, audit_it: bool = False) -> int:
    n = 0
    with conn.cursor() as cur:
        for account, line_key, note in rows:
            acct = norm_account(account)
            if not acct:
                continue
            old = None
            if audit_it:
                old = conn.execute("SELECT line_key FROM proj_pnl_qb_map WHERE account = %s", (acct,)).fetchone()
            cur.execute("INSERT INTO proj_pnl_qb_map (account, line_key, note) VALUES (%s,%s,%s) "
                        "ON CONFLICT (account) DO UPDATE SET line_key = EXCLUDED.line_key, note = EXCLUDED.note, updated_at = now()", (acct, line_key, note))
            if audit_it:
                audit.record(conn, actor=actor, scenario=None, action="pnl.qb_map", old={"account": acct, "line_key": old["line_key"] if old else None},
                             new={"account": acct, "line_key": line_key})
            n += 1
    return n


def delete_qb_map(conn, account: str, *, actor: str | None = None) -> bool:
    acct = norm_account(account)
    old = conn.execute("DELETE FROM proj_pnl_qb_map WHERE account = %s RETURNING line_key", (acct,)).fetchone()
    if old:
        audit.record(conn, actor=actor, scenario=None, action="pnl.qb_map.delete", old={"account": acct, "line_key": old["line_key"]})
    return bool(old)


def data_through(conn, today: date) -> date | None:
    """Last closed month holding any loaded P&L line."""
    r = conn.execute("SELECT max(month) AS m FROM proj_actuals WHERE left(line_key, 4) = 'pnl.' AND month < %s", (today.replace(day=1),)).fetchone()
    return r["m"]


def stale_after_days(conn) -> int:
    """The freshness threshold (days after a month closes before an unloaded month counts as late): proj_pnl_settings 'freshness', else the built-in start."""
    from .model import DEFAULT_SETTINGS
    row = conn.execute("SELECT value FROM proj_pnl_settings WHERE key = 'freshness'").fetchone()
    value = (row["value"] if row else {}).get("stale_after_days", DEFAULT_SETTINGS["freshness"]["stale_after_days"])
    return int(value)


def freshness_entries(conn, today: date | None = None) -> list[dict]:
    """The QuickBooks P&L feed (loaded by monthly upload). data_through is the last day of the last loaded month. The feed is stale when the first
    month that is not loaded closed more than `stale_after_days` ago (default 45), so the entry's stale_after_days is that threshold plus the length of
    that next month (the shared freshness check compares today with data_through)."""
    if today is None:
        from ..freshness import today_pacific
        today = today_pacific()
    limit = stale_after_days(conn)
    m = data_through(conn, today)
    if m is None:
        through, stale_after = None, limit
    else:
        nxt = add_months(m, 1)
        through = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
        stale_after = limit + calendar.monthrange(nxt.year, nxt.month)[1]
    return [{"feed": "quickbooks_pnl", "label": "QuickBooks P&L", "cadence": "monthly-upload", "data_through": through, "stale_after_days": stale_after}]
