"""CSV upload of the international warehouse's shipments (the monthly tracking export), preview first, then commit.

Accepted layouts (header row required, columns found by name, any order):
    wide   date, Vital Bull, Beef Liver, Boneflex, Vital Flow          one row per ship day (blank cell = not reported, kept as is)
    long   date, product (or sku), units (or qty / quantity / bottles)  one row per line; lines of the same day and product are added up
Product names, slugs (bull, liver, ...) and the warehouse item codes are recognised. Only complete days before the as-of date are accepted.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date

from ...queries import ApiError
from .constants import HEADER_PRODUCT, PRODUCT_LABELS, SKU_PRODUCT

MAX_LINES = 50_000
MAX_SPAN_DAYS = 400
MAX_UNITS = 1_000_000
MAX_ERRORS = 20
DATE_KEYS = ("date", "ship date", "shipdate", "day", "shipped")
UNIT_KEYS = ("units", "qty", "quantity", "bottles", "count")
LONG_KEYS = ("product", "sku", "item")


@dataclass
class Parsed:
    cells: dict[tuple[str, date], float] = field(default_factory=dict)       # (product, day) -> units
    errors: list[str] = field(default_factory=list)
    lines: int = 0

    @property
    def digest(self) -> str:
        body = json.dumps(sorted((p, d.isoformat(), round(u, 4)) for (p, d), u in self.cells.items()))
        return hashlib.sha256(body.encode()).hexdigest()[:16]


def _product(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    up = s.upper()
    if up in SKU_PRODUCT:
        return SKU_PRODUCT[up]
    low = s.lower()
    if low in HEADER_PRODUCT:
        return HEADER_PRODUCT[low]
    if low in PRODUCT_LABELS:
        return low
    return None


def _date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", s)
        if m:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _num(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return float("nan")
    return v


def parse_csv(data: bytes, as_of: date) -> Parsed:
    """Parse and validate. Problems are collected (first MAX_ERRORS kept) rather than raised, so a preview can list them all."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(400, "the file must be UTF-8 text (CSV)") from None
    if not text.strip():
        raise ApiError(400, "the file is empty")
    delim = "\t" if text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if len(rows) > MAX_LINES:
        raise ApiError(400, f"at most {MAX_LINES} lines per upload")
    head = [h.strip().lower() for h in rows[0]]
    out = Parsed(lines=len(rows) - 1)

    def err(msg: str) -> None:
        if len(out.errors) < MAX_ERRORS:
            out.errors.append(msg)

    di = next((i for i, h in enumerate(head) if h in DATE_KEYS), None)
    if di is None:
        raise ApiError(400, "no date column: the header needs one of " + ", ".join(DATE_KEYS))
    pi = next((i for i, h in enumerate(head) if h in LONG_KEYS), None)
    ui = next((i for i, h in enumerate(head) if h in UNIT_KEYS), None)
    wide = {i: _product(h) for i, h in enumerate(head) if i != di and _product(h)}
    if pi is not None and ui is not None:
        layout = "long"
    elif wide:
        layout = "wide"
    else:
        raise ApiError(400, "no product columns: use product names (Vital Bull, Beef Liver, Boneflex, Vital Flow) or a product and units column")

    def add(prod: str | None, day: date, raw_units: str, line: int) -> None:
        if prod is None:
            return
        n = _num(raw_units)
        if n is None:
            return                                                       # blank = not reported
        if n != n or n < 0 or n > MAX_UNITS:
            err(f"line {line}: units must be a number between 0 and {MAX_UNITS:,}")
            return
        out.cells[(prod, day)] = out.cells.get((prod, day), 0.0) + n

    for k, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        day = _date(r[di] if di < len(r) else "")
        if day is None:
            err(f"line {k}: unreadable date {(r[di] if di < len(r) else '')!r} (use YYYY-MM-DD or M/D/YYYY)")
            continue
        if day >= as_of:
            err(f"line {k}: {day.isoformat()} is not a complete day (the as-of date is {as_of.isoformat()})")
            continue
        if layout == "long":
            raw_p = r[pi] if pi < len(r) else ""
            prod = _product(raw_p)
            if prod is None:
                err(f"line {k}: unknown product {raw_p!r}")
                continue
            add(prod, day, r[ui] if ui < len(r) else "", k)
        else:
            for i, prod in wide.items():
                add(prod, day, r[i] if i < len(r) else "", k)
    if out.cells:
        days = [d for _, d in out.cells]
        if (max(days) - min(days)).days > MAX_SPAN_DAYS:
            err(f"the file spans more than {MAX_SPAN_DAYS} days")
    elif not out.errors:
        err("the file has no usable rows")
    return out


def diff_against(existing: dict[tuple[str, date], dict], parsed: Parsed, limit: int = 40) -> dict:
    """What committing would change. `existing[(product, day)] = {units, is_estimate, source}`."""
    new = changed = same = replaced_estimate = 0
    moves = []
    for (prod, day), units in sorted(parsed.cells.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        old = existing.get((prod, day))
        if old is None:
            new += 1
            moves.append({"day": day.isoformat(), "product": prod, "old": None, "new": units, "kind": "new"})
        elif abs(old["units"] - units) < 0.005 and not old["is_estimate"]:
            same += 1
        else:
            if old["is_estimate"]:
                replaced_estimate += 1
            else:
                changed += 1
            moves.append({"day": day.isoformat(), "product": prod, "old": old["units"], "new": units,
                          "kind": "replaces estimate" if old["is_estimate"] else "changes loaded count"})
    moves.sort(key=lambda m: -abs((m["new"] or 0) - (m["old"] or 0)))
    days = sorted({d for _, d in parsed.cells})
    return {"rows": len(parsed.cells), "days": len(days), "first_day": days[0].isoformat() if days else None,
            "last_day": days[-1].isoformat() if days else None, "new": new, "unchanged": same, "replaces_estimate": replaced_estimate,
            "changes_loaded": changed, "units_total": sum(parsed.cells.values()), "moves": moves[:limit], "moves_total": len(moves)}


def span(parsed: Parsed) -> tuple[date, date] | None:
    days = [d for _, d in parsed.cells]
    return (min(days), max(days)) if days else None
