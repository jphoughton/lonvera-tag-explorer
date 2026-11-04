"""Projection drivers: seed from the sheet snapshot, daily hooks (freshness, refresh).

    python -m etl.projection_drivers seed [--force] [--with-nc]      seed 'Current plan' + loaded actuals from the frozen sheet snapshot
    python -m etl.projection_drivers refresh [--days 14]             roll complete months of Meta spend (ad_daily) into proj_actuals
    python -m etl.projection_drivers freshness                       print the feeds this module depends on

Hooks called by the projection runner (etl.run_daily runs ad_daily first):
    freshness(conn) -> [{"feed","label","cadence","data_through","stale_after_days"}]
    refresh(conn, days=14) -> {"rows": int, "data_through": "YYYY-MM-DD" | None}     idempotent; does NOT commit (the caller's transaction does)

Seeding is once-only for the scenario cells (they are the plan Josh edits): `seed` skips 'Current plan' when it already holds any drivers.* cell
unless --force. Loaded actuals are inserted only where missing unless --force. nc.* (new-customer actuals) are NOT seeded by default: the
cohorts module owns the cohort table (proj_cohorts), which the drivers module reads first; --with-nc writes the fallback keys for a database
that has no cohort table.
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from typing import Any, Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.projection import audit, rwdb, scenarios, series, stubs
from app.projection.drivers.model import DEFAULT_PAYBACK, DEFAULT_SHARES, META_FEED_KEY, QB_CHANNEL_KEYS, SERIES_PREFIX
from app.projection.interfaces import MONTHS, Month

PNL = "P&L"
SEED_ACTUALS_THROUGH = date(2026, 7, 1)             # last month whose channel spend / revenue the sheet holds as QuickBooks-typed constants
SEED_PLAN_FROM = date(2026, 7, 1)                   # typed constants from here on are plan inputs / overrides
SEED_ACTOR = "seed:drivers"
SHEET_NOTE = "P&L row {row} (typed in the sheet, snapshot 2026-11-03)"
ACTUAL_ROWS = {                                     # proj_actuals key -> P&L row (constants Jan-25 .. Jul-26)
    "spend.amazon_ads": 52, "spend.meta": 53, "spend.google": 54, "spend.bing": 55, "spend.tiktok": 56,
    "dtc_rev": 18, "amazon_rev": 19, "tiktok_rev": 20,
}
FEED_STALE_DAYS = 2
BOOKS_STALE_DAYS = 45


# ---------------------------------------------------------------------------------------------------------------- sheet reading

class Sheet:
    """P&L month columns of the snapshot: constants vs formulas per row."""

    def __init__(self) -> None:
        self.sr = stubs.snapshot_module()
        if self.sr is None:
            raise RuntimeError("sheet snapshot reader not importable")
        hdr = self.sr.row(PNL, 8, "C", "BJ")
        self.months: list[Month] = [self.sr.serial_to_date(v) for v in hdr]

    def row(self, r: int, kind: str = "values") -> list:
        return self.sr.row(PNL, r, "C", "BJ", kind)

    def consts(self, r: int, lo: Month | None = None, hi: Month | None = None) -> dict[Month, float]:
        """Numeric cells of row r that are typed constants (not formulas) within [lo, hi]."""
        out: dict[Month, float] = {}
        for m, f, v in zip(self.months, self.row(r, "formulas"), self.row(r)):
            if m not in MONTHS or (lo and m < lo) or (hi and m > hi):
                continue
            if isinstance(f, (int, float)) and not isinstance(f, bool) and isinstance(v, (int, float)):
                out[m] = float(v)
        return out

    def values(self, r: int) -> dict[Month, float]:
        return {m: float(v) for m, v in zip(self.months, self.row(r)) if m in MONTHS and isinstance(v, (int, float)) and not isinstance(v, bool)}

    def cell(self, ref: str) -> Any:
        return self.sr.cell(PNL, ref)


def plan_items(sheet: Sheet) -> tuple[list[series.Item], dict[str, Any]]:
    """The 'Current plan' cells and levers read from the sheet's typed constants."""
    from decimal import Decimal
    items: list[series.Item] = []

    def add(key: str, month: Month, value: float, kind: str, note: str) -> None:
        items.append(series.Item(SERIES_PREFIX + key, month, Decimal(repr(round(value, 4))), kind, note))

    lo = SEED_PLAN_FROM
    v11, v17, v51 = sheet.values(11), sheet.values(17), sheet.values(51)
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    # plan inputs
    for m, v in sheet.consts(51, lo).items():
        add("spend_total", m, v, "input", SHEET_NOTE.format(row=51))
    add("spend_total", aug, v51[aug], "input", "P&L V51 = V11: Ad Spend typed for August 2026")
    for m, v in sheet.consts(14, lo).items():
        add("nc_cpa", m, v, "input", SHEET_NOTE.format(row=14))
    for m, v in sheet.consts(15, lo).items():
        add("nc_aov", m, v, "input", SHEET_NOTE.format(row=15))
    for m, v in sheet.consts(19, date(2026, 8, 1)).items():
        add("amazon_rev", m, v, "input", SHEET_NOTE.format(row=19))
    for m, v in sheet.consts(20, date(2026, 8, 1)).items():
        add("tiktok_rev", m, v, "input", SHEET_NOTE.format(row=20))
    for m in MONTHS:
        if m >= sep and m not in sheet.consts(20):            # blank in the sheet = no TikTok revenue planned
            add("tiktok_rev", m, 0.0, "input", "P&L row 20 blank in the sheet: no TikTok revenue planned")
    # typed over a computed value
    for m, v in sheet.consts(11, lo).items():
        note = SHEET_NOTE.format(row=11)
        if m == date(2026, 7, 1):
            note += "; QuickBooks Meta + Google for July 2026 is higher, the sheet kept this value"
        add("ad_spend", m, v, "override", note)
    add("ad_spend", sep, v11[sep], "override", "P&L W11 = W51: September 2026 ad spend equals total marketing spend (channel split not entered)")
    for m, v in sheet.consts(12, lo).items():
        add("nc_rev", m, v, "override", SHEET_NOTE.format(row=12) + "; the cohort table holds this month")
    for m, v in sheet.consts(13, lo).items():
        add("nc_orders", m, v, "override", SHEET_NOTE.format(row=13) + "; the cohort table holds this month")
    for m, v in sheet.consts(17, lo).items():
        add("repeat_rev", m, v, "override", SHEET_NOTE.format(row=17))
    add("repeat_rev", aug, v17[aug], "override", "P&L V17 = 1,499,426 (August 2026 total DTC revenue, typed) less new-customer revenue")
    levers = {"channel_share": {k: float(sheet.cell(f"B{r}")) for k, r in zip(("amazon", "meta", "google", "bing", "tiktok"), range(52, 57))},
              "sales_tax_rate": 0.07, "payback": dict(DEFAULT_PAYBACK), "ad_spend_channels": ["meta", "google"]}
    return items, levers


def actual_rows(sheet: Sheet, with_nc: bool) -> list[tuple[str, date, float]]:
    rows: list[tuple[str, date, float]] = []
    for key, r in ACTUAL_ROWS.items():
        for m, v in sheet.consts(r, None, SEED_ACTUALS_THROUGH).items():
            rows.append((key, m, v))
    if with_nc:
        sr = sheet.sr
        tab = "Cohort Data - Unified"
        for r in range(2, 60):
            a, b, g = sr.cell(tab, f"A{r}"), sr.cell(tab, f"B{r}"), sr.cell(tab, f"G{r}")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and isinstance(g, (int, float)):
                m = sr.serial_to_date(a)
                if m in MONTHS:
                    rows += [("nc.orders", m, float(b)), ("nc.rev", m, float(b) * float(g))]
    return rows


# ---------------------------------------------------------------------------------------------------------------- seed

def _rows(conn, sql: str, args: tuple = ()) -> list[Mapping]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def seed(conn, *, force: bool = False, with_nc: bool = False, sheet: Sheet | None = None) -> dict:
    """Seed loaded actuals and the 'Current plan' cells / levers. Returns counts. Does not commit.

    The plan is written straight into proj_series / proj_scenarios.params (one audit row), then captured in the scenario history: on a fresh
    scenario (version 1, no history) the seeded state IS version 1, so History can never restore to an empty plan; on one that already has
    history the seed becomes its own version."""
    sheet = sheet or Sheet()
    out = {"actuals": 0, "cells": 0, "levers": False, "skipped_plan": False}
    wanted = actual_rows(sheet, with_nc)
    have = {(r["line_key"], r["month"]) for r in _rows(conn, "SELECT line_key, month FROM proj_actuals WHERE line_key = ANY(%s)",
                                                       (sorted({k for k, _, _ in wanted}),))}
    fresh = [(k, m, v) for k, m, v in wanted if force or (k, m) not in have]
    if fresh:
        out["actuals"] = series.upsert_actuals(conn, fresh, "sheet snapshot 2026-11-03 (typed constants)", actor=SEED_ACTOR)
    existing = _rows(conn, "SELECT 1 FROM proj_series s JOIN proj_scenarios p ON p.id = s.scenario_id WHERE p.slug = %s AND left(s.line_key, %s) = %s LIMIT 1",
                     (scenarios.CURRENT_SLUG, len(SERIES_PREFIX), SERIES_PREFIX))
    if existing and not force:
        out["skipped_plan"] = True
        return out
    items, levers = plan_items(sheet)
    scn = scenarios.get_row(conn, scenarios.CURRENT_SLUG, lock=True)
    sid = scn["id"]
    history = _rows(conn, "SELECT count(*) AS n FROM proj_scenario_history WHERE scenario_id = %s", (sid,))[0]["n"]
    before = {(r["line_key"], r["month"]): (r["value"], r["kind"], r["note"]) for r in _rows(
        conn, "SELECT line_key, month, value, kind, note FROM proj_series WHERE scenario_id = %s AND left(line_key, %s) = %s", (sid, len(SERIES_PREFIX), SERIES_PREFIX))}
    changed = [it for it in items if before.get((it.line_key, it.month)) != (it.value, it.kind, it.note)]
    merged = scenarios.merge_patch(scn["params"], {"drivers": levers})
    params_changed = merged != scn["params"]
    if not changed and not params_changed:
        return out
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (scenario_id, line_key, month) DO UPDATE SET value = EXCLUDED.value, kind = EXCLUDED.kind, note = EXCLUDED.note, updated_at = now()",
                        [(sid, it.line_key, it.month, it.value, it.kind, it.note) for it in changed])
        if params_changed:
            cur.execute("UPDATE proj_scenarios SET params = %s WHERE id = %s", (Jsonb(merged), sid))
    audit.record(conn, actor=SEED_ACTOR, scenario=scenarios.CURRENT_SLUG, action="drivers.seed", note="sheet snapshot 2026-11-03",
                 new={"cells": len(changed), "levers": params_changed})
    if history == 0 and scn["version"] == 1:
        scenarios.lock_for_change(conn, scenarios.CURRENT_SLUG)               # baseline = the seeded plan (version 1)
    else:
        scenarios.finish_change(conn, sid, "seed")
    out["cells"], out["levers"] = len(changed), params_changed
    return out


# ---------------------------------------------------------------------------------------------------------------- daily hooks

def _first(row: Any) -> Any:
    if row is None:
        return None
    return next(iter(row.values())) if isinstance(row, Mapping) else row[0]


def _month_end(m: date | None) -> date | None:
    return None if m is None else m.replace(day=calendar.monthrange(m.year, m.month)[1])


def _books_keys() -> list[str]:
    keys = [k for pair in QB_CHANNEL_KEYS.values() for k in pair]
    keys += ["amazon_rev", "pnl.amazon_rev", "tiktok_rev", "pnl.tiktok_rev"]
    return keys


def freshness(conn) -> list[dict]:
    """The feeds the spend plan depends on and how current each is."""
    meta_day = _first(conn.execute("SELECT max(day) FROM ad_daily").fetchone())
    books_month = _first(conn.execute("SELECT max(month) FROM proj_actuals WHERE line_key = ANY(%s) AND month < date_trunc('month', current_date)::date",
                                      (_books_keys(),)).fetchone())
    return [
        {"feed": "drivers.meta_spend", "label": "Meta ad spend (ad_daily)", "cadence": "daily", "data_through": meta_day,
         "stale_after_days": FEED_STALE_DAYS},
        {"feed": "drivers.books", "label": "Amazon revenue and non-Meta ad spend (monthly upload)", "cadence": "monthly-upload",
         "data_through": _month_end(books_month), "stale_after_days": BOOKS_STALE_DAYS},
    ]


def refresh(conn, days: int = 14, today: date | None = None) -> dict:
    """Roll complete, closed months of Meta spend from ad_daily into proj_actuals ('spend.meta_feed'). Recomputes the months touched by the last
    `days` days plus any complete month not loaded yet; writes only values that changed, so a no-change day writes nothing. Does not commit."""
    today = today or date.today()
    this_month = today.replace(day=1)
    window_start = date.fromordinal(today.toordinal() - max(0, int(days))).replace(day=1)
    loaded = {r["month"]: float(r["value"]) for r in _rows(conn, "SELECT month, value FROM proj_actuals WHERE line_key = %s", (META_FEED_KEY,))}
    rows = []
    for r in _rows(conn, "SELECT month, spend, days FROM proj_meta_spend_monthly WHERE month < %s ORDER BY month", (this_month,)):
        m = r["month"]
        if r["days"] < calendar.monthrange(m.year, m.month)[1]:
            continue                                                  # a month with missing days is not an actual yet
        if m < window_start and m in loaded:
            continue
        v = float(r["spend"])
        if m not in loaded or abs(loaded[m] - v) >= 0.005:
            rows.append((META_FEED_KEY, m, v))
    n = series.upsert_actuals(conn, rows, "ad_daily (Meta spend, complete months)", actor="system") if rows else 0
    through = _first(conn.execute("SELECT max(day) FROM ad_daily").fetchone())
    return {"rows": n, "data_through": through.isoformat() if through else None}


# ---------------------------------------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--force", action="store_true")
    s.add_argument("--with-nc", action="store_true")
    r = sub.add_parser("refresh")
    r.add_argument("--days", type=int, default=14)
    sub.add_parser("freshness")
    args = ap.parse_args(argv)
    with rwdb.get_rw() as conn:
        if args.cmd == "seed":
            print(json.dumps(seed(conn, force=args.force, with_nc=args.with_nc)))
        elif args.cmd == "refresh":
            print(json.dumps(refresh(conn, args.days)))
        else:
            print(json.dumps(freshness(conn), default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
