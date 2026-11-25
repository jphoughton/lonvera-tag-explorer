"""QuickBooks "Profit and Loss by Month" upload: parse (csv or xlsx), map accounts to P&L lines, preview the diff, commit.

Rules
    * The header row is the first row (in the first 30) with at least three month labels (month-per-column layout); when there is none, the first row with
      a month label right of column A (the single-month layout: one month column, plus a "% of Income" column that is ignored). Every later row whose
      first cell is an account name and which carries numbers is an account. "Total ..." rows and the computed subtotals (Gross Profit, Net Operating Income, Net Income, ...) are never
      loaded, and the preview says so. Accounts with numbers and no mapping are listed as unmapped; nothing is dropped silently.
    * Only closed months (before the first of the current month) inside the model axis (2025-01 .. 2029-12) are loaded; every other month column is
      listed with the reason it was skipped.
    * Several accounts may map to one line; their amounts add. A blank cell is "no data" and leaves what is loaded untouched.
    * Preview first, then commit with the diff hash the preview returned: if anything changed in between, the commit is refused (409).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..interfaces import AXIS_END, AXIS_START, add_months
from . import charts
from . import lines as L
from .store import norm_account
from .xlsx_read import XlsxError, read_first_sheet

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MAX_BYTES = 8_000_000
MAX_ROWS = 5000
MAX_COLS = 200
HEADER_SCAN_ROWS = 30
MAX_CHANGES_SHOWN = 300
TOL = 0.005                                                      # a loaded value counts as unchanged within half a cent

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_RE_MON_YEAR = re.compile(r"^([A-Za-z]{3,9})\.?[\s\-/,]*(\d{2}|\d{4})$")
_RE_ISO = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?")
_RE_NUM_SLASH = re.compile(r"^(\d{1,2})/(?:(\d{1,2})/)?(\d{4})$")
SUBTOTALS = frozenset(("gross profit", "net operating income", "net income", "net other income", "net profit", "profit", "total"))


class UploadError(ValueError):
    pass


def _month_from_label(x: Any) -> date | None:
    """A month header cell -> first of that month, or None when the cell is not a month."""
    if isinstance(x, datetime):
        return x.date().replace(day=1)
    if isinstance(x, date):
        return x.replace(day=1)
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        if 36526 <= x <= 73050:                                 # Excel serial dates 2000-2099
            return (date(1899, 12, 30) + timedelta(days=int(x))).replace(day=1)
        return None
    if not isinstance(x, str):
        return None
    s = x.strip()
    m = _RE_MON_YEAR.match(s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        yr = int(m.group(2))
        yr += 2000 if yr < 100 else 0
        return date(yr, _MONTHS[m.group(1)[:3].lower()], 1)
    m = _RE_ISO.match(s)
    if m and 1 <= int(m.group(2)) <= 12:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = _RE_NUM_SLASH.match(s)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(3)), int(m.group(1)), 1)
    return None


def _amount(x: Any) -> float | None:
    """A numeric cell -> float; blanks, '-' and text -> None. Handles $, thousands separators, parentheses and leading minus."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    if not isinstance(x, str):
        return None
    s = x.strip().replace("\u2212", "-")
    if not s or s in ("-", "--"):
        return None
    neg = "(" in s or s.lstrip("$ ").startswith("-")
    t = re.sub(r"[()$,\s]", "", s).lstrip("-")
    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", t):
        return None
    v = float(t)
    return -v if neg else v


def read_rows(data: bytes, filename: str) -> list[list[Any]]:
    if not data:
        raise UploadError("the file is empty")
    if len(data) > MAX_BYTES:
        raise UploadError("the file is larger than 8 MB")
    if data[:2] == b"PK":
        try:
            rows = read_first_sheet(data)
        except XlsxError as e:
            raise UploadError(str(e)) from None
    else:
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            rows = [r for r in csv.reader(io.StringIO(text))]
        except csv.Error as e:
            raise UploadError(f"the file is not a readable csv ({e})") from None
    if len(rows) > MAX_ROWS:
        raise UploadError(f"the file has more than {MAX_ROWS} rows")
    return [list(r)[:MAX_COLS] for r in rows]


@dataclass
class Account:
    name: str
    row: int
    values: dict[date, float]
    parent: str | None = None                                    # the header row (an account line with no amounts) the account sits under


@dataclass
class Parsed:
    months: list[date] = field(default_factory=list)
    header_row: int = 0
    accounts: list[Account] = field(default_factory=list)
    ignored: list[dict] = field(default_factory=list)            # computed rows not loaded, with the reason
    sha256: str = ""
    filename: str = ""
    bytes: int = 0
    rows: int = 0
    chart_guess: str = charts.SHEET                              # qb2026 when the accounts carry four-digit codes


_CODE = re.compile(r"^(\d{4})\b\s*")


def strip_code(name: str) -> str:
    return _CODE.sub("", str(name or "").strip())


def lookup_account(qb_map: dict[str, str], name: str, parent: str | None = None) -> str | None:
    """The line an account feeds. Tried in order: the full name; the parent-qualified name without codes ("software & apps > marketing", for exports
    that drop the account code of the ambiguous children of "6165 Software & Apps"); the four-digit account code (an account renamed in the books but
    keeping its number)."""
    key = norm_account(name)
    if key in qb_map:
        return qb_map[key]
    if parent:
        q = norm_account(strip_code(parent)) + " > " + norm_account(strip_code(name))
        if q in qb_map:
            return qb_map[q]
    m = _CODE.match(name.strip())
    if m:
        pre = m.group(1) + " "
        hits = {line for k, line in qb_map.items() if k.startswith(pre)}
        if len(hits) == 1:
            return hits.pop()
    return None


def parse(data: bytes, filename: str = "") -> Parsed:
    rows = read_rows(data, filename)
    out = Parsed(sha256=hashlib.sha256(data).hexdigest(), filename=filename[:200], bytes=len(data), rows=len(rows))
    header = None
    for need in (3, 1):                                            # month-per-column layout first, then the single-month layout
        for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
            cols = {j: _month_from_label(c) for j, c in enumerate(r)}
            found = {j: m for j, m in cols.items() if m is not None and j > 0}
            if len(found) >= need:
                header = (i, found)
                break
        if header is not None:
            break
    if header is None:
        raise UploadError("no month header found: expected a row with month columns such as 'Jan 2026' (QuickBooks 'Profit and Loss by Month' or a single month)")
    hi, colmonth = header
    out.header_row = hi + 1
    seen_months = sorted(set(colmonth.values()))
    if len(seen_months) != len(colmonth):
        raise UploadError("the month header repeats a month")
    out.months = seen_months
    parent: str | None = None
    for ri in range(hi + 1, len(rows)):
        r = rows[ri]
        if not r or r[0] is None or str(r[0]).strip() == "":
            continue
        name = re.sub(r"\s+", " ", str(r[0])).strip()
        vals = {m: a for j, m in colmonth.items() if j < len(r) for a in [_amount(r[j])] if a is not None}
        low = norm_account(name)
        if not vals:
            parent = None if (low.startswith("total") or low in SUBTOTALS) else name       # a section or parent header such as "Expenses" or "6165 Software & Apps"
            continue                                              # (an empty account is skipped the same way)
        if low.startswith("total") or low in SUBTOTALS:
            out.ignored.append({"account": name, "row": ri + 1, "reason": "Computed row, not loaded"})
            parent = None
            continue
        out.accounts.append(Account(name=name, row=ri + 1, values=vals, parent=parent))
    out.chart_guess = charts.QB2026 if any(_CODE.match(a.name) for a in out.accounts) else charts.SHEET
    if not out.accounts and not out.ignored:
        raise UploadError("the file has a month header but no account rows with numbers")
    return out


# ---------------------------------------------------------------------------------------------------------------- mapping and diff

def build_cells(parsed: Parsed, qb_map: dict[str, str], today: date, chart: str = charts.SHEET) -> dict:
    """Apply the account map and the closed-month rule. Returns {cells: {(line_key, month): value}, mapped, unmapped, skipped_months}."""
    qb_keys = charts.get(chart).qb_keys
    cap = today.replace(day=1)
    months_ok: list[date] = []
    skipped_months: list[dict] = []
    for m in parsed.months:
        if m >= cap:
            skipped_months.append({"month": m.isoformat(), "reason": "Month is not closed yet"})
        elif m < AXIS_START or m > AXIS_END:
            skipped_months.append({"month": m.isoformat(), "reason": "Outside the model's January 2025 to December 2029 months"})
        else:
            months_ok.append(m)
    cells: dict[tuple[str, date], float] = {}
    mapped: list[dict] = []
    unmapped: list[dict] = []
    for a in parsed.accounts:
        key = lookup_account(qb_map, a.name, a.parent)
        vals = {m: v for m, v in a.values.items() if m in months_ok}
        if key is None:
            unmapped.append({"account": a.name, "row": a.row, "months": len(a.values), "total": round(sum(a.values.values()), 2)})
            continue
        if key not in qb_keys:
            unmapped.append({"account": a.name, "row": a.row, "months": len(a.values), "total": round(sum(a.values.values()), 2),
                             "error": f"mapped to {key!r}, which is not a loadable line"})
            continue
        mapped.append({"account": a.name, "line_key": key, "months": len(vals)})
        for m, v in vals.items():
            cells[(key, m)] = cells.get((key, m), 0.0) + v
    for k in list(cells):
        cells[k] = round(cells[k], 4)
    return {"cells": cells, "mapped": mapped, "unmapped": unmapped, "skipped_months": skipped_months, "months": months_ok}


def diff_cells(cells: dict[tuple[str, date], float], existing: dict[str, dict[date, float]]) -> dict:
    """new / changed / same cells against what is loaded (existing = {line_key: {month: value}} without the 'pnl.' prefix)."""
    new, changed, same = [], [], 0
    for (key, m), v in sorted(cells.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        old = existing.get(key, {}).get(m)
        if old is None:
            new.append({"line_key": key, "month": m.isoformat(), "new": v})
        elif abs(old - v) > TOL:
            changed.append({"line_key": key, "month": m.isoformat(), "old": old, "new": v})
        else:
            same += 1
    return {"new": new, "changed": changed, "same": same}


def diff_hash(cells: dict[tuple[str, date], float]) -> str:
    """Hash of exactly what a commit would write: the acknowledgement the commit must present."""
    body = json.dumps([[k, m.isoformat(), v] for (k, m), v in sorted(cells.items(), key=lambda kv: (kv[0][1], kv[0][0]))], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def months_summary(cells, diff) -> list[dict]:
    per: dict[date, dict] = {}
    for (key, m) in cells:
        per.setdefault(m, {"month": m.isoformat(), "cells": 0, "new": 0, "changed": 0})["cells"] += 1
    for kind in ("new", "changed"):
        for d in diff[kind]:
            per[date.fromisoformat(d["month"])][kind] += 1
    out = []
    for m in sorted(per):
        e = per[m]
        e["status"] = "changed" if e["changed"] else ("new" if e["new"] else "unchanged")
        out.append(e)
    return out


# Account families a closed month must carry before it can become "books". Membership is the structure of the chart (which line belongs to which
# family, charts/sheet.py and charts/qb2026.py); which families are required, and how far a month may stray from its neighbours before it needs a second
# look, are settings (proj_pnl_settings 'upload', defaults in model.DEFAULT_SETTINGS). Both charts use the same family names.
from .charts import sheet as _sheet                                 # noqa: E402

FAMILIES: dict[str, tuple[str, ...]] = _sheet.FAMILIES               # the sheet chart's families (kept under this name for existing callers)
FAMILY_LABEL = _sheet.FAMILY_LABEL


def _money(v: float) -> str:
    return f"${v:,.0f}" if abs(v) < 1e6 else f"${v / 1e6:,.2f}M"


def _totals(merged: dict[tuple[str, date], float], m: date, chart: str = charts.SHEET) -> tuple[float, float, bool]:
    """(income, cost, has_data) for one month of a merged {(line, month): value} map. Below-the-line lines (other income, interest) are neither."""
    c = charts.get(chart)
    income = sum(v for (k, mm), v in merged.items() if mm == m and k in c.income_keys)
    below = ("other_income", "interest_expense") if chart == charts.QB2026 else ()
    cost = sum(v for (k, mm), v in merged.items() if mm == m and k not in c.income_keys and k not in below)
    return income, cost, any(mm == m for (_, mm) in merged)


def coverage_checks(cells: dict[tuple[str, date], float], existing: dict[str, dict[date, float]], months: list[date], cfg: dict,
                    estimates: dict[date, dict] | None, chart: str = charts.SHEET) -> dict:
    """Per loaded month: which account families are present once the file is applied, whether the totals look right next to the neighbouring
    months and the model's own figure, and what that means for net operating income. Blockers stop a commit; review items need a confirmation."""
    fams, flabel = charts.get(chart).families, charts.get(chart).family_label
    required = [f for f in cfg["required_families"] if f in fams]
    merged: dict[tuple[str, date], float] = {(k, m): v for k, mv in existing.items() for m, v in mv.items()}
    merged.update(cells)
    lo, hi = float(cfg["low_ratio"]), float(cfg["high_ratio"])
    blockers: list[str] = []
    review: list[str] = []
    rows: list[dict] = []
    loaded = sorted({m for (_, m) in cells})
    for m in loaded:
        label = f"{MONTH_NAMES[m.month - 1]} {m.year}"
        present = {f for f, keys in fams.items() if any((k, m) in merged for k in keys)}
        missing = [f for f in required if f not in present]
        income, cost, _ = _totals(merged, m, chart)
        refs_i, refs_c = [], []
        for nb in (add_months(m, -1), add_months(m, 1)):
            i, c, has = _totals(merged, nb, chart)
            if has and i > 0 and c > 0:
                refs_i.append(i)
                refs_c.append(c)
        est = (estimates or {}).get(m)
        if est and est.get("income", 0) > 0 and est.get("cost", 0) > 0:
            refs_i.append(est["income"])
            refs_c.append(est["cost"])
        flags = []
        for name, value, refs in (("income", income, refs_i), ("cost", cost, refs_c)):
            if value > 0 and refs:
                ref = sorted(refs)[len(refs) // 2]
                ratio = value / ref
                if ratio < lo or ratio > hi:
                    flags.append(f"{label} {name} {_money(value)} is {ratio * 100:.0f}% of the {_money(ref)} its neighbouring months and the model suggest")
        if missing:
            blockers.append(f"{label} has no {' and no '.join(flabel[f] for f in missing)} accounts, so it cannot become books. Map the missing accounts or upload the full report")
        review.extend(flags)
        rows.append({"month": m.isoformat(), "families": {f: (f in present) for f in fams}, "missing": [flabel[f] for f in missing],
                     "income": income, "cost": cost, "noi": income - cost, "model_noi": (est or {}).get("noi"), "flags": flags, "ok": not missing and not flags})
    return {"rows": rows, "blockers": blockers, "review": review}


def preview(parsed: Parsed, qb_map: dict[str, str], existing: dict[str, dict[date, float]], today: date, estimates: dict[date, dict] | None = None,
            checks: dict | None = None, chart: str = charts.SHEET) -> dict:
    if checks is None:
        from .model import DEFAULT_SETTINGS
        checks = DEFAULT_SETTINGS["upload"]
    cd = charts.get(chart)
    b = build_cells(parsed, qb_map, today, chart)
    d = diff_cells(b["cells"], existing)
    cov = coverage_checks(b["cells"], existing, b["months"], checks, estimates, chart)
    revenue_mapped = {x["line_key"] for x in b["mapped"]} & set(cd.families["revenue"])
    notes = []
    if not revenue_mapped:
        notes.append("No revenue account is mapped: DTC, Amazon and TikTok revenue for these months will keep coming from the spend plan and cohorts.")
    if d["changed"]:
        notes.append(f"{len(d['changed'])} value(s) already loaded would be replaced; the old and new values are listed below.")
    by_month = {r["month"]: r for r in cov["rows"]}
    months = months_summary(b["cells"], d)
    for m in months:
        m.update({k: by_month[m["month"]][k] for k in ("families", "missing", "income", "cost", "noi", "model_noi", "flags", "ok")})
    return {"chart": chart, "file": {"name": parsed.filename, "sha256": parsed.sha256, "bytes": parsed.bytes, "rows": parsed.rows, "header_row": parsed.header_row,
                     "months": [m.isoformat() for m in parsed.months]},
            "months": months,
            "mapped": b["mapped"], "unmapped": b["unmapped"], "ignored": parsed.ignored, "skipped_months": b["skipped_months"],
            "cells": {"total": len(b["cells"]), "new": len(d["new"]), "changed": len(d["changed"]), "same": d["same"]},
            "changes": d["changed"][:MAX_CHANGES_SHOWN], "changes_truncated": max(0, len(d["changed"]) - MAX_CHANGES_SHOWN),
            "new_sample": d["new"][:20], "diff_sha256": diff_hash(b["cells"]), "notes": notes,
            "blockers": cov["blockers"], "review": cov["review"], "needs_confirmation": bool(cov["review"]) and not cov["blockers"],
            "required_families": [cd.family_label[f] for f in checks["required_families"] if f in cd.family_label],
            "committable": bool(b["cells"]) and not cov["blockers"]}, b["cells"]
