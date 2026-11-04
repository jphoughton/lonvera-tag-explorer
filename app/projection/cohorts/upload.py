"""Cohort export upload: parse a CSV, apply the closed-months-only rules, diff against the stored table, commit on request.

Accepted layouts
    wide  one row per cohort, the layout of the sheet tab: Cohort Month, New Customers, 90-Day Repeat Rate, CAC, Payback Period (Months),
          First Order (Realized), First Order, M1 ... M36 (cumulative $ per customer, first order included)
    long  Shopify / Upstack cohort export: Month, Months since first purchase, Customers (in cohort), Amount spent per customer (cumulative);
          months since 0 = the first order

Rules (standing rule: never include a month that is not over)
    - cohorts whose month is after `through` (the last complete month) are skipped, as are cohorts under `min_customers` customers
    - a cumulative cell that lands after `through` is blanked; a zero cell means "no data" and is dropped
    - the newest closed cohort keeps no 90-day repeat rate or CAC (not final until the month matures)
    - cells already stored are never erased by an upload: only added or revised
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..interfaces import add_months
from .engine import COHORT_AGES, Cohort, last_closed_month, months_between
from .store import CohortRow

MAX_BYTES = 2_000_000
MAX_ROWS = 5000
MIN_CUSTOMERS = 10
REL_EPS = 1e-7                  # cumulative cells are stored to six decimals
HEAD_EPS = 1e-5                 # head fields: rates are stored to six decimals, so a smaller change is rounding, not a revision
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


class UploadError(ValueError):
    """The file cannot be read as a cohort export (message goes to the user)."""


# ---------------------------------------------------------------------------------------------------------------- cell parsing

def parse_number(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "").replace(" ", "")
    if s in ("", "-", "--", "n/a", "N/A", "null", "None"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    pct = s.endswith("%")
    s = s.strip("()%").strip()
    try:
        x = float(s)
    except ValueError:
        raise UploadError(f"not a number: {v!r}") from None
    if x != x or x in (float("inf"), float("-inf")):
        raise UploadError(f"not a finite number: {v!r}")
    x = -x if neg else x
    return x / 100.0 if pct else x


def parse_month_cell(v: Any) -> date:
    """'2026-10', '2026-10-01', 'Oct 2026', 'October 2026', 'Oct-26', '10/1/2026', or a Sheets serial number -> first of month."""
    s = str(v).strip()
    if not s:
        raise UploadError("empty cohort month")
    if re.fullmatch(r"\d{5}(\.0+)?", s):                                 # Sheets serial date (days since 1899-12-30)
        return (date(1899, 12, 30) + timedelta(days=int(float(s)))).replace(day=1)
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?", s)
    if m:
        return _mk(int(m[1]), int(m[2]), s)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return _mk(int(m[3]), int(m[1]), s)
    m = re.fullmatch(r"([A-Za-z]{3,9})[ \-,.]+(\d{4}|\d{2})", s)
    if m and m[1][:3].lower() in _MONTHS:
        y = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
        return _mk(y, _MONTHS[m[1][:3].lower()], s)
    raise UploadError(f"cannot read the cohort month {v!r}")


def _mk(y: int, mo: int, raw: str) -> date:
    try:
        return date(y, mo, 1)
    except ValueError:
        raise UploadError(f"cannot read the cohort month {raw!r}") from None


# ---------------------------------------------------------------------------------------------------------------- file parsing

@dataclass
class RawCohort:
    month: date
    new_customers: float | None = None
    first_order: float | None = None
    first_order_realized: float | None = None
    repeat_90d: float | None = None
    cac: float | None = None
    payback_months: float | None = None
    cum: dict[int, float | None] = field(default_factory=dict)      # as in the file (zeros kept so they can be counted)


@dataclass
class Parsed:
    layout: str
    rows: list[RawCohort]
    rows_read: int
    sha256: str


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("﻿", ""))


def _read_table(raw: bytes) -> list[list[str]]:
    if len(raw) > MAX_BYTES:
        raise UploadError(f"file too large (limit {MAX_BYTES // 1_000_000} MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError("the file is not UTF-8 text; export it as CSV") from None
    first = text.split("\n", 1)[0]
    delim = max((",", "\t", ";"), key=first.count)
    table = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if len(table) < 2:
        raise UploadError("the file has no data rows")
    if len(table) > MAX_ROWS:
        raise UploadError(f"too many rows (limit {MAX_ROWS})")
    return table


def _find(header: list[str], *needles: str, exclude: tuple[str, ...] = ()) -> int | None:
    for i, h in enumerate(header):
        if all(n in h for n in needles) and not any(x in h for x in exclude):
            return i
    return None


def parse_csv(raw: bytes) -> Parsed:
    sha = hashlib.sha256(raw).hexdigest()
    table = _read_table(raw)
    header = [_norm(h) for h in table[0]]
    body = table[1:]
    if _find(header, "new customers") is not None and any(re.fullmatch(r"m\d+", h) for h in header):
        return Parsed("wide", _parse_wide(header, body), len(body), sha)
    if _find(header, "months since") is not None and _find(header, "per customer") is not None:
        return Parsed("long", _parse_long(header, body), len(body), sha)
    raise UploadError("unrecognised layout: expected the sheet layout (Cohort Month, New Customers, ..., M1 ... M36) "
                      "or a cohort export with Month, Months since first purchase, Customers in cohort, Amount spent per customer")


def _parse_wide(header: list[str], body: list[list[str]]) -> list[RawCohort]:
    col = lambda *n, **k: _find(header, *n, **k)                                  # noqa: E731
    i_month = col("cohort month")
    if i_month is None:
        i_month = col("cohort") if col("cohort") is not None else col("month")
    i_n, i_rep, i_cac, i_pay = col("new customers"), col("90-day"), col("cac"), col("payback")
    i_real, i_fo = col("first order", "realized"), col("first order", exclude=("realized",))
    m_cols = {int(h[1:]): i for i, h in enumerate(header) if re.fullmatch(r"m\d+", h) and 1 <= int(h[1:]) <= COHORT_AGES}
    if i_month is None or i_n is None or not m_cols:
        raise UploadError("the sheet layout needs Cohort Month, New Customers and M1...M36 columns")
    out, seen = [], set()
    for line_no, r in enumerate(body, start=2):
        get = lambda i: r[i] if i is not None and i < len(r) else None             # noqa: E731
        if not (get(i_month) or "").strip():
            continue
        try:
            month = parse_month_cell(get(i_month))
            c = RawCohort(month=month, new_customers=parse_number(get(i_n)), first_order=parse_number(get(i_fo)),
                          first_order_realized=parse_number(get(i_real)), repeat_90d=parse_number(get(i_rep)), cac=parse_number(get(i_cac)),
                          payback_months=parse_number(get(i_pay)), cum={a: parse_number(get(i)) for a, i in m_cols.items()})
        except UploadError as e:
            raise UploadError(f"line {line_no}: {e}") from None
        if month in seen:
            raise UploadError(f"line {line_no}: cohort {month:%Y-%m} appears twice")
        seen.add(month)
        out.append(c)
    return out


def _parse_long(header: list[str], body: list[list[str]]) -> list[RawCohort]:
    i_month = _find(header, "cohort month")
    if i_month is None:
        i_month = next((i for i, h in enumerate(header) if h in ("month", "cohort", "cohort month", "acquisition month")), None)
    i_since = _find(header, "months since")
    i_amt = _find(header, "per customer")
    i_size = _find(header, "in cohort")
    if i_size is None:
        i_size = _find(header, "cohort size")
    if i_size is None:
        i_size = _find(header, "customers", exclude=("per customer", "months since"))
    if None in (i_month, i_since, i_amt, i_size):
        raise UploadError("the cohort export needs Month, Months since first purchase, Customers in cohort and Amount spent per customer columns")
    by_month: dict[date, RawCohort] = {}
    for line_no, r in enumerate(body, start=2):
        if len(r) <= max(i_month, i_since, i_amt, i_size) or not r[i_month].strip():
            continue
        try:
            month = parse_month_cell(r[i_month])
            since = parse_number(r[i_since])
            amt = parse_number(r[i_amt])
            size = parse_number(r[i_size])
        except UploadError as e:
            raise UploadError(f"line {line_no}: {e}") from None
        if since is None or since != int(since) or since < 0:
            raise UploadError(f"line {line_no}: months since first purchase must be 0, 1, 2 ...")
        c = by_month.setdefault(month, RawCohort(month=month))
        if size is not None:
            c.new_customers = size
        if int(since) == 0:
            c.first_order = amt
        elif int(since) <= COHORT_AGES:
            c.cum[int(since)] = amt
    return list(by_month.values())


# ---------------------------------------------------------------------------------------------------------------- rules

def default_through(today: date, filename: str | None = None, through: date | None = None) -> date:
    """Last complete month the file may contribute: never later than the last closed month; a filename ending '...-to-YYYY-MM-DD' (the
    export's end date) pulls it back to the last month that was complete on that date."""
    limit = last_closed_month(today)
    if through is not None:
        return min(through.replace(day=1), limit)
    m = re.search(r"to[-_](\d{4})-(\d{2})-(\d{2})", filename or "")
    if m:
        try:
            end = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return limit
        complete = end.replace(day=1) if (end + timedelta(days=1)).day == 1 else add_months(end, -1)
        return min(complete, limit)
    return limit


@dataclass
class Applied:
    rows: list[CohortRow]
    skipped: list[dict]
    blanked_open_month: int
    zeros_dropped: int
    warnings: list[str]
    held_back: list[dict] = field(default_factory=list)       # head fields not taken from the newest closed cohort (not final yet)


def apply_rules(parsed: Parsed, through: date, min_customers: float = MIN_CUSTOMERS) -> Applied:
    rows: list[CohortRow] = []
    skipped: list[dict] = []
    blanked = zeros = 0
    warnings: list[str] = []
    held: list[dict] = []
    for c in sorted(parsed.rows, key=lambda r: r.month):
        label = c.month.isoformat()
        if c.month > through:
            skipped.append({"month": label, "reason": "month not over"})
            continue
        if c.new_customers is None or 0 <= c.new_customers < min_customers:           # a negative count is not "small": the checks block it
            skipped.append({"month": label, "reason": f"fewer than {min_customers:g} new customers"})
            continue
        cum: dict[int, float] = {}
        for age, v in sorted(c.cum.items()):
            if v is None:
                continue
            if v == 0:
                zeros += 1
                continue
            if months_between(c.month, through) < age:
                blanked += 1                                         # lands in a month that is not over
                continue
            cum[age] = v
        fo = c.first_order if c.first_order else None                          # zero = no value
        newest = c.month == through                                  # just closed: its 90-day repeat and CAC are not final yet
        if newest and (c.repeat_90d is not None or c.cac):
            held.append({"month": label, "fields": [f for f, v in (("repeat_90d", c.repeat_90d), ("cac", c.cac)) if v is not None and v != 0]})
        rows.append(CohortRow(month=c.month, new_customers=c.new_customers, first_order=fo,
                              first_order_realized=c.first_order_realized or None, repeat_90d=None if newest else c.repeat_90d,
                              cac=None if newest else (c.cac or None), payback_months=c.payback_months, cum=cum))
    return Applied(rows, skipped, blanked, zeros, warnings, held)


# ---------------------------------------------------------------------------------------------------------------- diff

def _differs(old: float | None, new: float | None, eps: float = REL_EPS) -> bool:
    if old is None or new is None:
        return old is not new
    return abs(old - new) > eps * max(1.0, abs(old), abs(new))


_HEAD = (("new_customers", "new_customers"), ("first_order", "first_order"), ("first_order_realized", "first_order_realized"),
         ("repeat_90d", "repeat_90d"), ("cac", "cac"), ("payback_months", "payback_months"))


def diff(applied: Applied, existing: list[Cohort], limit: int = 60) -> dict:
    """Summary counts plus the largest revisions. Nothing is written."""
    have = {c.month: c for c in existing}
    added = changed = unchanged = 0
    cells_added = cells_changed = kept = 0
    changes: list[dict] = []
    for r in applied.rows:
        old = have.get(r.month)
        if old is None:
            added += 1
            cells_added += len(r.cum)
            continue
        row_changed = False
        for attr, oattr in _HEAD:
            new_v = getattr(r, attr)
            if new_v is None:
                continue
            old_v = getattr(old, oattr)
            if _differs(old_v, new_v, HEAD_EPS):
                row_changed = True
                changes.append({"month": r.month.isoformat(), "field": attr, "old": old_v, "new": new_v,
                                "rel": None if not old_v else (new_v - old_v) / abs(old_v)})
        for age in range(1, COHORT_AGES + 1):
            new_v, old_v = r.cum.get(age), old.cum_at(age)
            if new_v is None:
                kept += old_v is not None
            elif old_v is None:
                cells_added += 1
                row_changed = True
            elif _differs(old_v, new_v):
                cells_changed += 1
                row_changed = True
                changes.append({"month": r.month.isoformat(), "field": f"M{age}", "old": old_v, "new": new_v,
                                "rel": (new_v - old_v) / abs(old_v) if old_v else None})
        changed += row_changed
        unchanged += not row_changed
    changes.sort(key=lambda c: abs(c["rel"]) if c["rel"] is not None else 1e9, reverse=True)
    return {"cohorts_in_file": len(applied.rows) + len(applied.skipped), "cohorts_loaded": len(applied.rows), "cohorts_added": added,
            "cohorts_changed": changed, "cohorts_unchanged": unchanged, "cells_added": cells_added, "cells_changed": cells_changed,
            "cells_blanked_open_month": applied.blanked_open_month, "cells_zero_dropped": applied.zeros_dropped,
            "cells_kept_not_in_file": kept, "skipped": applied.skipped, "held_back": applied.held_back, "changes": changes[:limit], "changes_total": len(changes)}
