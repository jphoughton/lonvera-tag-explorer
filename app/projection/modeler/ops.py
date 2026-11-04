"""Quick edits for the three plan rows: validation of typed cells and the expansion of a short instruction into cells. Pure functions, no database.

The rows are the Spend plan page's own series (`drivers.spend_total`, `drivers.nc_aov`, `drivers.nc_cpa`), so an edit made here is the same edit there.

    parse_edits(raw)                          -> {(line_key, month): value | None}      typed cells (value None = take the typed value away)
    expand(op, ctx)                           -> Expansion(edits, skipped, text)         one instruction -> cells

Instructions (`op["type"]`), all with `row` (spend_total | nc_aov | nc_cpa):
    fill      from, to, value                       the same value in every month
    step      from, to, value, then_value, step_month   `value` before step_month, `then_value` from it
    growth    from, to, value, rate_pct             first month `value`, then +rate_pct % a month
    ramp      from, to, end_value[, start_value]    straight line (start_value defaults to the current value in the first planned month)
    percent   from, to, pct                         every month in the range times (1 + pct / 100)
    add       from, to, amount                      every month in the range plus `amount` (negative to cut)
    copy      month, to                             the value in `month` repeated in every later month through `to`
    reset     from, to                              back to the plan of record's cell (or no cell of its own)
Months that hold loaded figures are skipped and reported; a range with nothing left to change is an error.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..drivers import patterns
from ..interfaces import AXIS_END, MONTHS, Month, parse_month
from ...queries import ApiError

ROWS = {"spend_total": "drivers.spend_total", "nc_aov": "drivers.nc_aov", "nc_cpa": "drivers.nc_cpa"}
KEY_TO_ROW = {v: k for k, v in ROWS.items()}
LABELS = {"spend_total": "Ad spend", "nc_aov": "Average order value", "nc_cpa": "Cost per new customer"}
MAX_EDITS = 500
TYPES = ("fill", "step", "growth", "ramp", "percent", "add", "copy", "reset")
PCT_LIMITS = (-99.0, 1000.0)          # a cut can't reach -100% (a price of zero); +1000% is far beyond any plan

Edit = tuple[str, Month]


def month_text(m: Month) -> str:
    return m.strftime("%b %Y")


def money(v: float, row: str) -> str:
    return f"${v:,.2f}" if row != "spend_total" else f"${v:,.0f}"


def limits(row: str) -> tuple[float, float, bool]:
    """(lowest, highest, lowest-is-excluded) a cell of the row may hold: the Spend plan page's own limits (a cost per customer of 0 would divide)."""
    try:
        from ..drivers.validation import BOUNDS
        b = BOUNDS[row]
        return b.lo, b.hi, b.lo_open
    except (ImportError, KeyError):                    # a drivers module without the shared limits: its pattern limits
        _label, lo, hi = patterns.TARGETS[row]
        return lo, hi, lo > 0


def check_value(row: str, month: Month, value: float) -> None:
    lo, hi, open_ = limits(row)
    if not math.isfinite(value) or value > hi or (value <= lo if open_ else value < lo):
        floor = f"above {money(lo, row)}" if open_ else f"at least {money(lo, row)}"
        raise ApiError(400, f"{LABELS[row]} for {month_text(month)} must be {floor} and at most {money(hi, row)}.")


def _num(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ApiError(400, f"{name} must be a number")
    return float(v)


def _month(v: Any, name: str) -> Month:
    try:
        return parse_month(v)
    except ValueError:
        raise ApiError(400, f"{name} must be the first of a month between Jan 2025 and Dec 2029") from None


def parse_edits(raw: Any) -> dict[Edit, float | None]:
    """Typed cells from the request: [{line_key, month, value | null}]. Only the three plan rows; a later entry for the same cell wins."""
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ApiError(400, "edits must be a list")
    if len(raw) > MAX_EDITS:
        raise ApiError(400, f"at most {MAX_EDITS} edits at a time")
    out: dict[Edit, float | None] = {}
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise ApiError(400, f"edits[{i}] must be an object")
        key = e.get("line_key")
        if key not in KEY_TO_ROW:
            raise ApiError(400, f"edits[{i}]: line_key must be one of {', '.join(ROWS.values())}")
        month = _month(e.get("month"), f"edits[{i}].month")
        value = e.get("value")
        if value is None:
            out[(key, month)] = None
            continue
        out[(key, month)] = _num(value, f"edits[{i}].value")
    return out


def check_unlocked(edits: dict[Edit, float | None], codes: dict[str, dict[str, str]]) -> None:
    """A typed cell in a month that holds loaded figures changes nothing, so it is refused with the months named."""
    locked = sorted({m for (k, m) in edits if (codes.get(k) or {}).get(m.isoformat()) == "actual"})
    if locked:
        raise ApiError(400, f"{_months_text(locked)} {'holds' if len(locked) == 1 else 'hold'} loaded figures from the books. Only planned months can change.")


def check_values(edits: dict[Edit, float | None]) -> None:
    for (key, m), v in edits.items():
        if v is not None:
            check_value(KEY_TO_ROW[key], m, v)


def _months_text(ms: list[Month]) -> str:
    return ", ".join(month_text(m) for m in ms[:4]) + (f" and {len(ms) - 4} more" if len(ms) > 4 else "")


def _span(first: Month, last: Month) -> str:
    return month_text(first) if first == last else f"{month_text(first)} to {month_text(last)}"


@dataclass
class OpContext:
    """What an instruction needs to know about the model right now (with the typed edits already in place)."""
    current: dict[str, dict[Month, float]]                 # row -> the value each month has now (typed or carried)
    codes: dict[str, dict[str, str]]                       # series key -> {iso month: 'actual' | 'typed' | 'carried' | 'slid'}
    por: dict[str, dict[Month, float]] | None = None       # row -> the plan of record's cells
    months: tuple[Month, ...] = MONTHS


@dataclass
class Expansion:
    edits: dict[Edit, float | None] = field(default_factory=dict)
    skipped: list[Month] = field(default_factory=list)
    text: str = ""
    row: str = ""
    count: int = 0


def _range(op: dict, ctx: OpContext, first_key: str = "from", last_key: str = "to") -> list[Month]:
    first = _month(op.get(first_key), first_key)
    last = _month(op.get(last_key), last_key) if op.get(last_key) else AXIS_END
    if last < first:
        raise ApiError(400, f"{last_key} must not be before {first_key}")
    return [m for m in ctx.months if first <= m <= last]


def _split_locked(row: str, months: list[Month], ctx: OpContext) -> tuple[list[Month], list[Month]]:
    codes = ctx.codes.get(ROWS[row]) or {}
    free = [m for m in months if codes.get(m.isoformat()) != "actual"]
    return free, [m for m in months if m not in free]


def _value_of(op: dict, name: str, row: str, month: Month) -> float:
    v = _num(op.get(name), name)
    check_value(row, month, v)
    return v


def expand(op: Any, ctx: OpContext) -> Expansion:
    if not isinstance(op, dict):
        raise ApiError(400, "op must be an object")
    kind = op.get("type")
    row = op.get("row")
    if kind not in TYPES:
        raise ApiError(400, f"op type must be one of {', '.join(TYPES)}")
    if row not in ROWS:
        raise ApiError(400, f"row must be one of {', '.join(ROWS)}")
    key, label = ROWS[row], LABELS[row]
    if kind == "copy":
        src = _month(op.get("month"), "month")
        months = [m for m in _range({"from": op.get("month"), "to": op.get("to")}, ctx) if m > src]
        if not months:
            raise ApiError(400, "Pick a last month after the month you copy from.")
        have = ctx.current.get(row, {}).get(src)
        if have is None:
            raise ApiError(400, f"{label} has no value in {month_text(src)} to copy.")
    else:
        months = _range(op, ctx)
    if not months:
        raise ApiError(400, "No months in that range.")
    free, locked = _split_locked(row, months, ctx)
    if not free:
        raise ApiError(400, f"{_months_text(locked)} {'holds' if len(locked) == 1 else 'hold'} loaded figures from the books. Pick planned months.")
    first, last = free[0], free[-1]
    n = len(free)
    cells: dict[Month, float | None]
    if kind == "fill":
        value = _value_of(op, "value", row, first)
        cells = dict(patterns.generate({"type": "flat", "value": value}, free))
        text = f"Set {label.lower()} to {money(value, row)} for {_span(first, last)}"
    elif kind == "step":
        before, after = _value_of(op, "value", row, first), _value_of(op, "then_value", row, first)
        at = _month(op.get("step_month"), "step_month")
        cells = dict(patterns.generate({"type": "step", "value": before, "then_value": after, "step_month": at.isoformat()}, free))
        text = f"Set {label.lower()} to {money(before, row)}, then {money(after, row)} from {month_text(at)}, for {_span(first, last)}"
    elif kind == "growth":
        value, rate = _value_of(op, "value", row, first), _num(op.get("rate_pct"), "rate_pct")
        try:
            cells = dict(patterns.generate({"type": "growth", "value": value, "rate": rate / 100.0}, free))
        except patterns.PatternError as e:
            raise ApiError(400, str(e)) from None
        text = f"Start {label.lower()} at {money(value, row)} and change it {rate:+g}% a month, for {_span(first, last)}"
    elif kind == "ramp":
        end = _value_of(op, "end_value", row, last)
        start = _value_of(op, "start_value", row, first) if op.get("start_value") is not None else ctx.current.get(row, {}).get(first)
        if start is None:
            raise ApiError(400, f"{label} has no value in {month_text(first)} to start the ramp from. Give a start value.")
        cells = dict(patterns.generate({"type": "ramp", "value": start, "end_value": end}, free))
        text = f"Move {label.lower()} in a straight line from {money(start, row)} to {money(end, row)}, {_span(first, last)}"
    elif kind == "percent":
        pct = _num(op.get("pct"), "pct")
        if not PCT_LIMITS[0] < pct <= PCT_LIMITS[1]:
            raise ApiError(400, f"pct must be between {PCT_LIMITS[0]:g} and {PCT_LIMITS[1]:g}")
        cur = ctx.current.get(row, {})
        missing = [m for m in free if cur.get(m) is None]
        if missing:
            raise ApiError(400, f"{label} has no value in {_months_text(missing)} to change by a percent.")
        cells = {m: round(cur[m] * (1.0 + pct / 100.0), 2) for m in free}
        text = f"Change {label.lower()} by {pct:+g}% for {_span(first, last)}"
    elif kind == "add":
        amount = _num(op.get("amount"), "amount")
        cur = ctx.current.get(row, {})
        missing = [m for m in free if cur.get(m) is None]
        if missing:
            raise ApiError(400, f"{label} has no value in {_months_text(missing)} to change by an amount.")
        cells = {m: round(cur[m] + amount, 2) for m in free}
        text = f"{'Raise' if amount >= 0 else 'Cut'} {label.lower()} by {money(abs(amount), row)} for {_span(first, last)}"
    elif kind == "copy":
        value = ctx.current[row][src]
        cells = {m: round(value, 2) for m in free}
        text = f"Repeat {label.lower()} from {month_text(src)} ({money(value, row)}) through {month_text(last)}"
    else:                                                  # reset
        if ctx.por is None:
            raise ApiError(400, "There is no plan of record to go back to.")
        theirs = ctx.por.get(row, {})
        cells = {m: theirs.get(m) for m in free}            # None: the plan of record holds no cell there, so this scenario keeps none either
        text = f"Put {label.lower()} back to the plan of record for {_span(first, last)}"
    if kind not in ("reset",):
        patterns_check = {m: v for m, v in cells.items() if v is not None}
        for m, v in patterns_check.items():
            check_value(row, m, v)
    out = Expansion(edits={(key, m): v for m, v in cells.items()}, skipped=locked, row=row, count=n)
    out.text = f"{text} ({n} month{'' if n == 1 else 's'})." + (f" Skipped {_months_text(locked)}: loaded figures." if locked else "")
    return out
