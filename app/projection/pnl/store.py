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
from . import charts
from .model import PnlInputs

_WS = re.compile(r"\s+")


def norm_account(name: str) -> str:
    """Key of a QuickBooks account name: lower-cased, whitespace collapsed, a trailing ':' dropped."""
    return _WS.sub(" ", str(name or "").strip().rstrip(":")).strip().lower()


# ---------------------------------------------------------------------------------------------------------------- reads

def load_actuals(conn, chart: str = charts.SHEET) -> dict[str, dict[date, float]]:
    """{line key: {month: value}} for every QuickBooks line of `chart`, from proj_actuals ('pnl.<key>' for the sheet chart, 'qb26.<key>' for qb2026;
    axis months only). The two prefixes never overlap, so one chart never reads the other's books."""
    c = charts.get(chart)
    raw = series.get_actuals(conn, prefix=c.actuals_prefix, from_=AXIS_START, to=AXIS_END)
    out: dict[str, dict[date, float]] = {}
    for k, s in raw.items():
        key = k[len(c.actuals_prefix):]
        if key in c.qb_keys and s:
            out[key] = s
    return out


def load_overrides(conn, scn: Scenario | str | None, chart: str = charts.SHEET) -> dict[str, dict[date, float]]:
    c = charts.get(chart)
    raw = series.get_series(conn, scn, prefix=c.series_prefix)
    return {k[len(c.series_prefix):]: s for k, s in raw.items() if k[len(c.series_prefix):] in c.line_keys and s}


def load_driver_defaults(conn, chart: str = charts.SHEET) -> dict[str, list[dict]]:
    return {r["line_key"]: r["segments"] for r in conn.execute("SELECT line_key, segments FROM proj_pnl_drivers WHERE chart = %s", (chart,)).fetchall()}


def load_driver_notes(conn, chart: str = charts.SHEET) -> dict[str, dict]:
    return {r["line_key"]: {"note": r["note"], "source": r["source"]}
            for r in conn.execute("SELECT line_key, note, source FROM proj_pnl_drivers WHERE chart = %s", (chart,)).fetchall()}


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


def load_qb_map(conn, chart: str = charts.SHEET) -> dict[str, str]:
    return {r["account"]: r["line_key"] for r in conn.execute("SELECT account, line_key FROM proj_pnl_qb_map WHERE chart = %s", (chart,)).fetchall()}


def today() -> date:                                                   # a seam for tests; Pacific, one clock for every module
    try:
        from ..clock import today_pt
        return today_pt()
    except ImportError:                                                # app/projection/clock.py is added by the core module
        from ..freshness import today_pacific
        return today_pacific()


def load_inputs(conn, scn: Scenario | None = None, today_: date | None = None) -> PnlInputs:
    inp = PnlInputs(actuals=load_actuals(conn), overrides=load_overrides(conn, scn), drivers=load_driver_defaults(conn),
                    settings=load_settings(conn), cogs_sku=load_cogs_sku(conn), today=today_ or today())
    if charts.chart_of(((scn.params if scn is not None else None) or {}).get("pnl")) == charts.QB2026:      # only a scenario on the new chart reads its data
        inp.qb_actuals = load_actuals(conn, charts.QB2026)
        inp.qb_overrides = load_overrides(conn, scn, charts.QB2026)
        inp.qb_drivers = load_driver_defaults(conn, charts.QB2026)
    return inp


def actual_sources(conn, chart: str = charts.SHEET) -> dict[date, str]:
    """month -> source label of the loaded P&L lines of `chart` (for the grid's note)."""
    pre = charts.get(chart).actuals_prefix
    rows = conn.execute("SELECT month, max(source) AS source FROM proj_actuals WHERE left(line_key, %s) = %s GROUP BY month ORDER BY month", (len(pre), pre)).fetchall()
    return {r["month"]: r["source"] for r in rows}


# ---------------------------------------------------------------------------------------------------------------- writes

def upsert_driver_defaults(conn, rows: Iterable[tuple[str, list[dict], str | None, str | None]], chart: str = charts.SHEET) -> int:
    """(line_key, segments, note, source) rows into proj_pnl_drivers. Idempotent."""
    n = 0
    with conn.cursor() as cur:
        for key, segs, note, source in rows:
            cur.execute("INSERT INTO proj_pnl_drivers (chart, line_key, segments, note, source) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (chart, line_key) DO UPDATE SET segments = EXCLUDED.segments, note = EXCLUDED.note, source = EXCLUDED.source, updated_at = now()",
                        (chart, key, Jsonb(segs), note, source))
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


def upsert_qb_map(conn, rows: Iterable[tuple[str, str, str | None]], *, actor: str | None = None, audit_it: bool = False, chart: str = charts.SHEET) -> int:
    n = 0
    with conn.cursor() as cur:
        for account, line_key, note in rows:
            acct = norm_account(account)
            if not acct:
                continue
            old = None
            if audit_it:
                old = conn.execute("SELECT line_key FROM proj_pnl_qb_map WHERE chart = %s AND account = %s", (chart, acct)).fetchone()
            cur.execute("INSERT INTO proj_pnl_qb_map (chart, account, line_key, note) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (chart, account) DO UPDATE SET line_key = EXCLUDED.line_key, note = EXCLUDED.note, updated_at = now()",
                        (chart, acct, line_key, note))
            if audit_it:
                audit.record(conn, actor=actor, scenario=None, action="pnl.qb_map",
                             old={"account": acct, "line_key": old["line_key"] if old else None, **({"chart": chart} if chart != charts.SHEET else {})},
                             new={"account": acct, "line_key": line_key, **({"chart": chart} if chart != charts.SHEET else {})})
            n += 1
    return n


def delete_qb_map(conn, account: str, *, actor: str | None = None, chart: str = charts.SHEET) -> bool:
    acct = norm_account(account)
    old = conn.execute("DELETE FROM proj_pnl_qb_map WHERE chart = %s AND account = %s RETURNING line_key", (chart, acct)).fetchone()
    if old:
        audit.record(conn, actor=actor, scenario=None, action="pnl.qb_map.delete",
                     old={"account": acct, "line_key": old["line_key"], **({"chart": chart} if chart != charts.SHEET else {})})
    return bool(old)


def data_through(conn, today: date, chart: str = charts.SHEET) -> date | None:
    """Last closed month holding any loaded P&L line of `chart`."""
    pre = charts.get(chart).actuals_prefix
    r = conn.execute("SELECT max(month) AS m FROM proj_actuals WHERE left(line_key, %s) = %s AND month < %s", (len(pre), pre, today.replace(day=1))).fetchone()
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


# ---------------------------------------------------------------------------------------------------------------- upload checks and history

def upload_checks(conn) -> dict:
    """The coverage settings for a QuickBooks upload (global): proj_pnl_settings 'upload' over the built-in starting values."""
    from .model import DEFAULT_SETTINGS
    row = conn.execute("SELECT value FROM proj_pnl_settings WHERE key = 'upload'").fetchone()
    return {**DEFAULT_SETTINGS["upload"], **((row or {}).get("value") or {})}


def record_upload(conn, *, actor: str | None, filename: str, sha256: str, size: int, file_rows: int, months: list[str],
                  replaced: list[dict], created: list[dict], chart: str = charts.SHEET) -> int:
    row = conn.execute(
        "INSERT INTO proj_upload_history (actor, filename, sha256, bytes, file_rows, months, new_cells, changed_cells, replaced, created, chart) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (actor, filename[:200], sha256, size, file_rows, Jsonb(months), len(created), len(replaced), Jsonb(replaced), Jsonb(created), chart)).fetchone()
    return row["id"]


def list_uploads(conn, limit: int = 10, chart: str | None = None) -> list[dict]:
    """The last uploads (every chart unless `chart` is given). Only the newest applied upload OF EACH CHART can be undone."""
    if chart is None:
        rows = conn.execute("SELECT id, at, actor, filename, sha256, months, new_cells, changed_cells, status, undone_at, undone_by, chart FROM proj_upload_history "
                            "ORDER BY id DESC LIMIT %s", (max(1, min(limit, 50)),)).fetchall()
    else:
        rows = conn.execute("SELECT id, at, actor, filename, sha256, months, new_cells, changed_cells, status, undone_at, undone_by, chart FROM proj_upload_history "
                            "WHERE chart = %s ORDER BY id DESC LIMIT %s", (chart, max(1, min(limit, 50)))).fetchall()
    latest = {r["chart"]: r["id"] for r in conn.execute("SELECT chart, max(id) AS id FROM proj_upload_history WHERE status = 'applied' GROUP BY chart").fetchall()}
    return [{**r, "can_undo": r["id"] == latest.get(r["chart"])} for r in rows]


def undo_upload(conn, upload_id: int | None, *, actor: str | None, chart: str = charts.SHEET) -> dict:
    """Put back exactly what the latest applied upload of `chart` replaced and remove what it created. Refuses unless it is the latest applied upload of
    that chart and nothing has changed those cells since. The other chart's books are never touched."""
    from ...queries import ApiError
    c = charts.get(chart)
    row = conn.execute("SELECT * FROM proj_upload_history WHERE status = 'applied' AND chart = %s ORDER BY id DESC LIMIT 1 FOR UPDATE", (chart,)).fetchone()
    if row is None:
        raise ApiError(404, "there is no upload to undo")
    if upload_id is not None and upload_id != row["id"]:
        raise ApiError(409, "only the latest upload can be undone; undo the newer ones first")
    pre = c.actuals_prefix
    cur = {(r["line_key"], r["month"]): float(r["value"]) for r in conn.execute(
        "SELECT line_key, month, value FROM proj_actuals WHERE left(line_key, %s) = %s AND month = ANY(%s)",
        (len(pre), pre, [date.fromisoformat(m) for m in row["months"]])).fetchall()}
    for ch in row["created"]:
        if abs(cur.get((c.actuals_key(ch["line_key"]), date.fromisoformat(ch["month"])), float("nan")) - float(ch["value"])) > 0.005:
            raise ApiError(409, "loaded values changed after this upload, so it cannot be undone safely")
    for ch in row["replaced"]:
        if abs(cur.get((c.actuals_key(ch["line_key"]), date.fromisoformat(ch["month"])), float("nan")) - float(ch["new"])) > 0.005:
            raise ApiError(409, "loaded values changed after this upload, so it cannot be undone safely")
    with conn.cursor() as cur_:
        for ch in row["created"]:
            cur_.execute("DELETE FROM proj_actuals WHERE line_key = %s AND month = %s", (c.actuals_key(ch["line_key"]), date.fromisoformat(ch["month"])))
        for ch in row["replaced"]:
            cur_.execute("UPDATE proj_actuals SET value = %s, source = %s, loaded_at = now() WHERE line_key = %s AND month = %s",
                         (ch["old"], ch["source"], c.actuals_key(ch["line_key"]), date.fromisoformat(ch["month"])))
    conn.execute("UPDATE proj_upload_history SET status = 'undone', undone_at = now(), undone_by = %s WHERE id = %s", (actor, row["id"]))
    extra = {} if chart == charts.SHEET else {"chart": chart}
    audit.record(conn, actor=actor, scenario=None, action="pnl.upload.undo", note=row["filename"],
                 old={"upload_id": row["id"], "sha256": row["sha256"], **extra},
                 new={"restored": len(row["replaced"]), "removed": len(row["created"]), "months": row["months"], **extra})
    return {"upload_id": row["id"], "filename": row["filename"], "restored": len(row["replaced"]), "removed": len(row["created"]), "months": row["months"],
            "chart": chart}
