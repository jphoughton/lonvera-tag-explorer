"""Pacing module wiring for the engine: light inputs (the running month and the last 7 days), compute -> PacingResult."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import feeds, levers as lv, month as month_mod, periods, rollup as rollup_mod
from .targets import TargetBook

FULL_ENV = "PROJECTION_PACING_FULL"          # reconcile only: load the whole history and add the rollup figures to the result

# Row of the sheet's Automated Pacing tab for each month-pacing row (reconcile layout)
SHEET_ROWS = {
    "total_rev": 4, "dtc_rev": 5, "nc_rev": 6, "repeat_rev": 7, "nc_roas": 8, "nc_cpa": 9, "amazon_rev": 10, "sales_tax": 11, "cogs": 12,
    "amazon_fees": 13, "cogs_direct": 14, "fulfillment": 15, "inbound": 16, "payment": 17, "advertising": 19, "ad_meta": 20, "ad_google": 21,
    "ad_amazon": 22, "ad_tiktok": 23, "opex": 25, "marketing_opex": 26, "other_business": 27, "headcount": 28, "overhead": 29, "ebitda": 31,
    "below_ebitda": 32, "noi": 33, "contribution_margin": 34, "required_daily_ebitda": 35, "required_extra_revenue": 36, "nc_count": 38,
    "dtc_ad_spend": 39, "nc_rev2": 40, "nc_cpa2": 41, "nc_aov": 42, "nc_roas2": 43, "co_nc_rev": 46, "co_nc_count": 47, "co_nc_cpa": 48,
    "co_nc_aov": 49, "co_nc_roas": 50,
}
INVERTED_PACING = ("nc_cpa", "nc_cpa2")          # the sheet shows goal / projection for the cost-per-customer rows
COLUMNS = {"B": "pacing", "C": "mtd", "D": "last7", "E": "daily_needed", "F": "should_be", "G": "goal", "H": "projection"}
ROW0, ROW1 = 4, 50


# Month Snapshot rows of the sheet (reconcile layout): sheet row -> snapshot key
SNAPSHOT_SHEET_ROWS = {7: "total_rev", 8: "dtc_rev", 9: "nc_rev", 10: "repeat_rev", 11: "amazon_rev", 13: "nc_orders", 14: "dtc_ad_spend", 15: "nc_cpa", 16: "nc_aov",
                       17: "nc_roas", 19: "total_spend", 20: "meta", 21: "google", 22: "amazon_ads", 24: "ebitda", 25: "noi"}


@dataclass
class PacingInputs:
    today: date
    month: date
    frame: feeds.Frame
    full: bool = False
    cpa_ref: Any = None          # drivers.reference.Reference for the 28-day cost per new customer (None: not read)


@dataclass
class PacingResult:
    as_of: date
    data_through: date
    month: date
    summary: dict = field(default_factory=dict)          # month_mod.pacing_month() output
    automated: dict[str, list] = field(default_factory=dict)   # sheet column letter -> values for rows 4..50 (reconcile layout)
    snapshot: dict[str, list] = field(default_factory=dict)    # Month Snapshot columns B..D for rows 7..28 (reconcile layout, PROJECTION_PACING_FULL only)
    rollup_month: dict = field(default_factory=dict)           # last closed month / last complete ISO week / last complete day (PROJECTION_PACING_FULL only)
    rollup_week: dict = field(default_factory=dict)
    rollup_day: dict = field(default_factory=dict)


def load_inputs(conn, scn=None) -> PacingInputs:
    L = lv.parse(getattr(scn, "params", None))
    today = periods.as_of()
    month = periods.default_month(today, L.data_lag_days)
    thr = periods.data_through(today, L.data_lag_days)
    start = min(month, thr - timedelta(days=max(L.last_days, 7) - 1))
    full = os.environ.get(FULL_ENV, "") not in ("", "0")
    if full:
        start = date(2024, 12, 30)
    return PacingInputs(today, month, feeds.load_frame(conn, start, thr), full, feeds.cpa_reference(conn, getattr(scn, "params", None), L.cpa_window_days, today))


def automated_columns(summary: dict) -> dict[str, list]:
    """The month pacing table laid out like the sheet's Automated Pacing B4:H50, one list per column (index = sheet row - 4)."""
    cols = {c: [None] * (ROW1 - ROW0 + 1) for c in COLUMNS}
    for r in summary.get("rows", []):
        row = SHEET_ROWS.get(r["key"])
        if row is None:
            continue
        for letter, field_ in COLUMNS.items():
            v = r.get(field_)
            if letter == "B" and r["key"] in INVERTED_PACING and v:
                v = 1.0 / v
            cols[letter][row - ROW0] = v
        if r["key"] in ("required_daily_ebitda", "required_extra_revenue"):         # the sheet keeps these two figures in the MTD column
            cols["C"][row - ROW0] = r["daily_needed"]
            cols["E"][row - ROW0] = None
    return cols


def compute(scn, inputs: PacingInputs, pnl, drivers=None, cohorts=None) -> PacingResult:
    # `pnl` has no default: the engine marks pacing degraded when the P&L is, instead of pacing against goals of zero
    params = getattr(scn, "params", None) or {}
    L = lv.parse(params)
    book = TargetBook(pnl, drivers, cohorts, levers=L)
    summary = month_mod.pacing_month(inputs.frame, inputs.month, inputs.today, book, cohorts, params, getattr(drivers, "actual_through", None), inputs.cpa_ref)
    res = PacingResult(inputs.today, periods.data_through(inputs.today, L.data_lag_days), inputs.month, summary, automated_columns(summary))
    if inputs.full:
        _reconcile_extras(res, inputs, book, cohorts, params)
    return res


def _reconcile_extras(res: PacingResult, inputs: PacingInputs, book: TargetBook, cohorts: Any, params: dict) -> None:
    today = inputs.today
    thr = periods.data_through(today, book.levers.data_lag_days)
    fr = inputs.frame
    rollup_mod.apply_costs(fr, book, lv.rates_month(book.levers, today))
    last_month = periods.month_start(thr) if thr == periods.month_end(thr) else periods.add_months(periods.month_start(thr), -1)
    last_week = periods.week_start(thr) if thr.weekday() == 6 else periods.week_start(thr) - timedelta(days=7)
    res.rollup_month = rollup_mod.rollup(fr, "month", last_month, periods.month_end(last_month), thr)[0].values
    res.rollup_week = rollup_mod.rollup(fr, "week", last_week, last_week + timedelta(days=6), thr)[0].values
    res.rollup_day = rollup_mod.rollup(fr, "day", thr, thr, thr)[0].values
    snap = month_mod.snapshot_month(fr, inputs.month, today, book, cohorts, params)
    by = {r["key"]: r for r in snap["rows"]}
    cols = {c: [None] * 22 for c in "BCD"}
    for row, key in SNAPSHOT_SHEET_ROWS.items():
        r = by[key]
        for c, field_ in (("B", "actual"), ("C", "projected"), ("D", "target")):
            cols[c][row - 7] = r[field_]
    pb = snap.get("payback")
    if pb:
        for c, k in (("B", "actual"), ("C", "actual"), ("D", "target")):
            cols[c][27 - 7] = pb["first_order_margin"][k]
            cols[c][28 - 7] = pb["months"][k]
    res.snapshot = cols
