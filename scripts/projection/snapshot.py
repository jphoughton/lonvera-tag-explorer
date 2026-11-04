#!/usr/bin/env python
"""Freeze the live Lonvera Projections Google Sheet into JSON fixtures (read-only).

Writes, per tab, ``<gid>__<slug>.formulas.json`` (FORMULA) and
``<gid>__<slug>.values.json`` (UNFORMATTED_VALUE, dates as Sheets serial numbers), plus
``_manifest.json``, ``P&L.rowmap.json`` and ``P&L.months.json``, into
``~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03/`` (outside the repo).

Modes
  full       whole used range (trimmed of trailing empty rows/columns, starting at A1)
  head_tail  first HEAD_ROWS rows as ``head`` + the last TAIL_ROWS rows ending at the last
             non-empty row as ``tail`` (``tail_start_row`` = sheet row of tail[0])
  skipped    listed in the manifest, never read

The script only ever calls ``spreadsheets.get`` and ``spreadsheets.values.batchGet``; it
never writes to the sheet. Re-running overwrites the snapshot files in place.

Usage (venv with google-api-python-client, e.g. the lonvera-sync one):

    /Users/joshhoughton/lonvera-sync-main/.venv/bin/python scripts/projection/snapshot.py
    ... snapshot.py --out /tmp/snap --only "P&L,Curves"
    ... snapshot.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import snapshot_read as sr  # noqa: E402

SHEET_ID = "1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI"
SHEETS_HELPER_DIR = "/Users/joshhoughton/Desktop/Google Sheets"
DEFAULT_OUT = sr.DEFAULT_DIR

HEAD_ROWS = 6
TAIL_ROWS = 400
MIN_REQUEST_GAP_S = 2.0          # quota is ~60 read requests/minute
MAX_ATTEMPTS = 6
GROUP_MAX_CELLS = 150_000        # grid cells per full-tab batchGet request (estimate)
GROUP_MAX_TABS = 6
ERROR_RE = re.compile(r"^#(REF!|N/A|ERROR!|VALUE!|DIV/0!|NAME\?|NUM!|NULL!)")

FULL_TABS = [
    "Automated Pacing ", "Month Snapshot", "Payback", "Pacing - Daily (180d)", "Curves",
    "Hist Waterfall - Last 4 Months Curve", "Rollup - Weekly (WoW)", "Rollup - Monthly (MoM)",
    "P&L", "COGS", "Fixed Expenses", "Vendor COGS", "COGS Adjustments",
    "Cohort Data - Unified", "Term Debt Monthly Paydown", "Spend-ROAS Matrix",
    "Campaign Spend by Product", "Warehance Reorder", "Purchase Orders",
    "Warehance Inventory Burn", "FBA Transfers", "Amazon Inventory Burn", "Warehance Daily",
    "Warehance Weekly", "Warehance Fcst Inputs", "Warehance Demand Fcst",
    "Amazon Monthly Units", "Amazon Daily Units", "Amazon Fcst Inputs", "Amazon Demand Fcst",
    "Inventory Cash Outlay", "Intl Daily Units", "Intl By Country", "Intl Fcst Inputs",
    "Intl Demand Fcst", "Intl Inventory Burn", "Total Inventory Burn", "Inventory Value",
    "Intl Reorder", "Total Demand Fcst", "Meta - Daily (180d)", "Meta - Weekly (WoW)",
    "Meta - Monthly (MoM)", "Sheet10", "Pacing - By Product",
]
HEAD_TAIL_TABS = [
    "Ad Spend - Daily", "Orders - Daily (NC-RC Rev)", "Attribution - Daily", "Amazon - Daily",
    "Platform - History", "Orders - History", "Attribution - History", "Product - History",
    "Amazon - Product Daily", "Meta Campaign - History", "NC Rev - By Product",
    "Meta Spend - By Product", "Meta Product - Reach", "Upstack Any-Click - By Campaign",
    "Meta Account - Reach", "Traffic - Daily", "Meta Bids - Daily",
]
SKIPPED_TABS = {
    "Klaviyo - Daily": "klaviyo analytics, not a projection input",
    "Klaviyo - Message Key": "klaviyo analytics, not a projection input",
    "Klaviyo - Daily (365d)": "klaviyo analytics, not a projection input",
    "Klaviyo - Weekly (WoW)": "klaviyo analytics, not a projection input",
    "Klaviyo - Monthly (MoM)": "klaviyo analytics, not a projection input",
    "Klaviyo - Format (WoW)": "klaviyo analytics, not a projection input",
    "Klaviyo - Format (MoM)": "klaviyo analytics, not a projection input",
    "Klaviyo - Campaign Rev by Order Type": "klaviyo analytics, not a projection input",
    "Fulfillment - Weekly": "ops analytics, not a projection input",
    "Stuck Orders": "ops analytics, not a projection input",
    "Transit - 14d Sample": "ops analytics, not a projection input",
}


# ------------------------------------------------------------------ small utils

def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _norm(title: str) -> str:
    return title.strip()


def _quote(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _is_empty(v: Any) -> bool:
    return v is None or v == ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trim_grid(values: list[list]) -> list[list]:
    """Drop trailing empty rows and columns; keep what remains rectangular (pad with "")."""
    rows = [list(r) for r in values]
    while rows and all(_is_empty(c) for c in rows[-1]):
        rows.pop()
    width = 0
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if not _is_empty(r[i]):
                width = max(width, i + 1)
                break
    return [(r[:width] + [""] * (width - len(r[:width]))) for r in rows]


def pad_grid(values: list[list], nrows: int, ncols: int) -> list[list]:
    out = [(list(r) + [""] * (ncols - len(r)))[:ncols] for r in values[:nrows]]
    out.extend([[""] * ncols for _ in range(nrows - len(out))])
    return out


def shape(values: list[list]) -> tuple[int, int]:
    return len(values), (len(values[0]) if values else 0)


def a1_end(nrows: int, ncols: int) -> str:
    return f"A1:{sr.col_letter(max(ncols, 1))}{max(nrows, 1)}"


def find_errors(values: list[list]) -> tuple[int, list[str]]:
    n, samples = 0, []
    for ri, r in enumerate(values, start=1):
        for ci, v in enumerate(r, start=1):
            if isinstance(v, str) and ERROR_RE.match(v):
                n += 1
                if len(samples) < 10:
                    samples.append(f"{sr.col_letter(ci)}{ri}={v}")
    return n, samples


def write_json(path: Path, obj: Any) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)
    return path.stat().st_size


# ------------------------------------------------------------------ Sheets access (read-only)

class Reader:
    """Thin read-only wrapper: paces requests and retries transient failures. The only
    API methods it exposes are spreadsheets.get and spreadsheets.values.batchGet."""

    def __init__(self, sheet_id: str = SHEET_ID, log: Callable[[str], None] = print):
        self.sheet_id = sheet_id
        self.log = log
        self._svc = None
        self._last = 0.0
        self.requests = 0

    def _service(self):
        if self._svc is None:
            if SHEETS_HELPER_DIR not in sys.path:
                sys.path.insert(0, SHEETS_HELPER_DIR)
            import sheets  # type: ignore

            svc = sheets._svc()
            try:  # the Google token/API calls have timed out before; give them room
                svc._http.http.timeout = 180
            except Exception:
                pass
            self._svc = svc
        return self._svc

    def _call(self, desc: str, build: Callable[[Any], Any]) -> Any:
        delay = 3.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = MIN_REQUEST_GAP_S - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                self.requests += 1
                result = build(self._service()).execute(num_retries=0)
                self._last = time.monotonic()
                return result
            except Exception as exc:  # noqa: BLE001 - classify below
                self._last = time.monotonic()
                status = getattr(getattr(exc, "resp", None), "status", None)
                transient = (
                    status in (429, 500, 502, 503, 504)
                    or isinstance(exc, (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError))
                    or type(exc).__name__ in ("TransportError", "ServerNotFoundError", "RefreshError",
                                              "IncompleteRead", "RemoteDisconnected", "HttpLib2Error")
                )
                if not transient or attempt == MAX_ATTEMPTS:
                    raise
                pause = max(delay, 30.0) if status == 429 else delay
                self.log(f"  retry {attempt}/{MAX_ATTEMPTS - 1} on {desc}: "
                         f"{type(exc).__name__} {status or ''} - sleeping {pause:.0f}s")
                if status is None:
                    self._svc = None  # rebuild credentials/connection
                time.sleep(pause)
                delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable")

    def metadata(self) -> dict:
        return self._call("spreadsheets.get", lambda s: s.spreadsheets().get(
            spreadsheetId=self.sheet_id,
            fields="properties(title,timeZone),sheets(properties,merges)"))

    def batch_get(self, ranges: list[str], render: str) -> list[list[list]]:
        """One batchGet; returns one value grid per requested range (same order)."""
        res = self._call(f"batchGet({len(ranges)} ranges, {render})", lambda s: s.spreadsheets().values().batchGet(
            spreadsheetId=self.sheet_id, ranges=ranges, valueRenderOption=render,
            dateTimeRenderOption="SERIAL_NUMBER", majorDimension="ROWS"))
        vrs = res.get("valueRanges", [])
        if len(vrs) != len(ranges):
            raise RuntimeError(f"batchGet returned {len(vrs)} ranges for {len(ranges)} requested")
        return [vr.get("values", []) for vr in vrs]


# ------------------------------------------------------------------ capture

def _tab_info(sheet: dict) -> dict:
    p = sheet["properties"]
    g = p.get("gridProperties", {})
    merges = []
    for m in sheet.get("merges", []):
        merges.append(f"{sr.col_letter(m['startColumnIndex'] + 1)}{m['startRowIndex'] + 1}:"
                      f"{sr.col_letter(m['endColumnIndex'])}{m['endRowIndex']}")
    return {
        "gid": p["sheetId"], "title": p["title"], "hidden": bool(p.get("hidden", False)),
        "rowCount": g.get("rowCount", 0), "columnCount": g.get("columnCount", 0),
        "frozenRowCount": g.get("frozenRowCount", 0), "frozenColumnCount": g.get("frozenColumnCount", 0),
        "merged_ranges": merges,
    }


def plan_modes(tabs: list[dict]) -> dict[int, tuple[str, str | None]]:
    """gid -> (mode, reason). Tabs not named in any list are skipped as 'unlisted'."""
    full = {_norm(t) for t in FULL_TABS}
    ht = {_norm(t) for t in HEAD_TAIL_TABS}
    skip = {_norm(t): why for t, why in SKIPPED_TABS.items()}
    out: dict[int, tuple[str, str | None]] = {}
    for t in tabs:
        key = _norm(t["title"])
        if key in full:
            out[t["gid"]] = ("full", None)
        elif key in ht:
            out[t["gid"]] = ("head_tail", None)
        elif key in skip:
            out[t["gid"]] = ("skipped", skip[key])
        else:
            out[t["gid"]] = ("skipped", "unlisted in snapshot plan")
    return out


def _group(tabs: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cells = 0
    for t in tabs:
        est = min(t["rowCount"], 3000) * t["columnCount"]
        if cur and (cells + est > GROUP_MAX_CELLS or len(cur) >= GROUP_MAX_TABS):
            groups.append(cur)
            cur, cells = [], 0
        cur.append(t)
        cells += est
    if cur:
        groups.append(cur)
    return groups


def _remove_stale(out: Path, gid: int, keep_stem: str) -> None:
    for p in out.glob(f"{gid}__*.json"):
        if not p.name.startswith(keep_stem + "."):
            p.unlink()


def _capture_full_group(reader: Reader, group: list[dict], out: Path) -> dict[int, dict]:
    ranges = [_quote(t["title"]) for t in group]
    formulas = reader.batch_get(ranges, "FORMULA")
    values = reader.batch_get(ranges, "UNFORMATTED_VALUE")
    results: dict[int, dict] = {}
    for t, f_raw, v_raw in zip(group, formulas, values):
        f, v = trim_grid(f_raw), trim_grid(v_raw)
        nrows = max(shape(f)[0], shape(v)[0])
        ncols = max(shape(f)[1], shape(v)[1])
        f, v = pad_grid(f, nrows, ncols), pad_grid(v, nrows, ncols)
        stem = f"{t['gid']}__{slugify(t['title'])}"
        _remove_stale(out, t["gid"], stem)
        base = {"title": t["title"], "gid": t["gid"], "dateTimeRenderOption": "SERIAL_NUMBER",
                "mode": "full", "range": a1_end(nrows, ncols), "rows": nrows, "cols": ncols}
        size = write_json(out / f"{stem}.formulas.json", {**base, "renderOption": "FORMULA", "values": f})
        size += write_json(out / f"{stem}.values.json", {**base, "renderOption": "UNFORMATTED_VALUE", "values": v})
        nerr, samples = find_errors(v)
        results[t["gid"]] = {"stem": stem, "used_rows": nrows, "used_cols": ncols,
                             "bytes": size, "error_cells": nerr, "error_samples": samples}
    return results


def capture_full(reader: Reader, tabs: list[dict], out: Path, log: Callable[[str], None],
                 failures: dict[int, str]) -> dict[int, dict]:
    results: dict[int, dict] = {}
    for gi, group in enumerate(_group(tabs), start=1):
        log(f"[full {gi}] " + ", ".join(t["title"].strip() for t in group))
        try:
            results.update(_capture_full_group(reader, group, out))
        except Exception as exc:  # noqa: BLE001 - retry each tab of the group once on its own
            log(f"  group failed ({type(exc).__name__}: {exc}); retrying its tabs one at a time")
            for t in group:
                try:
                    results.update(_capture_full_group(reader, [t], out))
                except Exception as exc2:  # noqa: BLE001
                    failures[t["gid"]] = f"{type(exc2).__name__}: {exc2}"
                    log(f"  FAILED {t['title']!r}: {failures[t['gid']]}")
    return results


def capture_head_tail(reader: Reader, tabs: list[dict], out: Path, log: Callable[[str], None],
                      failures: dict[int, str]) -> dict[int, dict]:
    if not tabs:
        return {}
    try:
        return _capture_head_tail(reader, tabs, out, log)
    except Exception as exc:  # noqa: BLE001 - retry each tab once on its own
        log(f"  head_tail batch failed ({type(exc).__name__}: {exc}); retrying tabs one at a time")
    results: dict[int, dict] = {}
    for t in tabs:
        try:
            results.update(_capture_head_tail(reader, [t], out, log))
        except Exception as exc2:  # noqa: BLE001
            failures[t["gid"]] = f"{type(exc2).__name__}: {exc2}"
            log(f"  FAILED {t['title']!r}: {failures[t['gid']]}")
    return results


def _capture_head_tail(reader: Reader, tabs: list[dict], out: Path, log: Callable[[str], None]) -> dict[int, dict]:
    results: dict[int, dict] = {}
    log(f"[head_tail] locating last row of {len(tabs)} tabs")
    col_a = reader.batch_get([f"{_quote(t['title'])}!A:A" for t in tabs], "UNFORMATTED_VALUE")
    last_row: dict[int, int] = {}
    for t, a in zip(tabs, col_a):
        n = len(a)
        while n and (not a[n - 1] or _is_empty(a[n - 1][0])):
            n -= 1
        last_row[t["gid"]] = n
    # safety: anything below the last non-empty row of column A (data starting in col B, etc.)
    probe = [(t, f"{_quote(t['title'])}!A{last_row[t['gid']] + 1}:{sr.col_letter(t['columnCount'])}{t['rowCount']}")
             for t in tabs if last_row[t["gid"]] < t["rowCount"]]
    if probe:
        extra = reader.batch_get([r for _, r in probe], "UNFORMATTED_VALUE")
        for (t, _), rows in zip(probe, extra):
            trimmed = trim_grid(rows)
            if trimmed:
                last_row[t["gid"]] += len(trimmed)
                log(f"  note: {t['title']} has data below the last column-A value; last row = {last_row[t['gid']]}")
    ranges: list[str] = []
    spans: list[tuple[dict, int]] = []
    for t in tabs:
        last = last_row[t["gid"]]
        q = _quote(t["title"])
        endcol = sr.col_letter(max(t["columnCount"], 1))
        head_end = min(HEAD_ROWS, max(last, 1))
        tail_start = max(head_end + 1, last - TAIL_ROWS + 1)
        ranges.append(f"{q}!A1:{endcol}{head_end}")
        spans.append((t, tail_start))
        if last >= tail_start:
            ranges.append(f"{q}!A{tail_start}:{endcol}{last}")
        else:
            ranges.append(f"{q}!A1:{endcol}1")  # placeholder keeps the range list aligned; discarded
    log(f"[head_tail] reading head/tail for {len(tabs)} tabs")
    formulas = reader.batch_get(ranges, "FORMULA")
    values = reader.batch_get(ranges, "UNFORMATTED_VALUE")
    for i, (t, tail_start) in enumerate(spans):
        last = last_row[t["gid"]]
        parts = {}
        for kind, data in (("formulas", formulas), ("values", values)):
            head = data[2 * i]
            tail = data[2 * i + 1] if last >= tail_start else []
            parts[kind] = (head, tail)
        # shared shape across kinds and across head/tail
        ncols = 0
        for kind in parts:
            for grid in parts[kind]:
                ncols = max(ncols, shape(trim_grid(grid))[1])
        head_n = min(HEAD_ROWS, max(last, 0))
        tail_n = max(0, last - tail_start + 1)
        stem = f"{t['gid']}__{slugify(t['title'])}"
        _remove_stale(out, t["gid"], stem)
        size = 0
        err_n, err_samples = 0, []
        for kind, render in (("formulas", "FORMULA"), ("values", "UNFORMATTED_VALUE")):
            head, tail = parts[kind]
            head_g = pad_grid(head, head_n, ncols)
            tail_g = pad_grid(tail, tail_n, ncols)
            payload = {"title": t["title"], "gid": t["gid"], "dateTimeRenderOption": "SERIAL_NUMBER",
                       "mode": "head_tail", "renderOption": render, "range": a1_end(last, ncols),
                       "rows": last, "cols": ncols, "head_rows": head_n, "tail_start_row": tail_start,
                       "head": head_g, "tail": tail_g}
            size += write_json(out / f"{stem}.{kind}.json", payload)
            if kind == "values":
                err_n, err_samples = find_errors(head_g)
                n2, s2 = find_errors(tail_g)
                err_n += n2
                err_samples = (err_samples + s2)[:10]
        results[t["gid"]] = {"stem": stem, "used_rows": last, "used_cols": ncols, "bytes": size,
                             "error_cells": err_n, "error_samples": err_samples,
                             "head_rows": head_n, "tail_rows": tail_n, "tail_start_row": tail_start}
    return results


# ------------------------------------------------------------------ P&L helper files

def build_pnl_rowmap(out: Path, last_row: int = 215) -> Path:
    """P&L.rowmap.json: one entry per row 1..last_row so agents can map sheet rows to line keys.

    kind: 'formula' when the Sep-2026 cell (W) is a formula (column C is used when W is
    empty); 'input' when it is a typed constant; 'blank' when both W and C are empty
    (section headers and spacer rows keep their label)."""
    entry = _entry_from_disk(out, "P&L")
    f = json.load(open(out / f"{entry['stem']}.formulas.json", encoding="utf-8"))["values"]
    v = json.load(open(out / f"{entry['stem']}.values.json", encoding="utf-8"))["values"]

    def at(grid: list[list], r: int, c: int) -> Any:
        if r - 1 < len(grid) and c - 1 < len(grid[r - 1]):
            return grid[r - 1][c - 1]
        return ""

    cC, cW = sr.col_index("C"), sr.col_index("W")
    rows = []
    for r in range(1, last_row + 1):
        label, b_f = at(f, r, 1), at(f, r, 2)
        c_f, w_f = at(f, r, cC), at(f, r, cW)
        probe = w_f if not _is_empty(w_f) else c_f
        if _is_empty(probe):
            kind = "blank"
        elif isinstance(probe, str) and probe.startswith("="):
            kind = "formula"
        else:
            kind = "input"
        rows.append({
            "row": r,
            "label": label if not _is_empty(label) else "",
            "driver_text": b_f if not _is_empty(b_f) else "",
            "driver_value": at(v, r, 2) if not _is_empty(at(v, r, 2)) else "",
            "kind": kind,
            "c_formula": c_f if not _is_empty(c_f) else "",
            "w_formula": w_f if not _is_empty(w_f) else "",
            "c_value": at(v, r, cC) if not _is_empty(at(v, r, cC)) else "",
            "w_value": at(v, r, cW) if not _is_empty(at(v, r, cW)) else "",
        })
    path = out / "P&L.rowmap.json"
    write_json(path, {"sheet": "P&L", "gid": entry["gid"], "rows_covered": f"1-{last_row}",
                      "kind_rule": "formula if W (else C) starts with '='; input if W (else C) is a typed constant; "
                                   "blank if both empty",
                      "rows": rows})
    return path


def build_pnl_months(out: Path) -> Path:
    """P&L.months.json: the 60 month columns (C..BJ of row 8) and the FY roll-up columns (BL..BP)."""
    entry = _entry_from_disk(out, "P&L")
    v = json.load(open(out / f"{entry['stem']}.values.json", encoding="utf-8"))["values"]
    f = json.load(open(out / f"{entry['stem']}.formulas.json", encoding="utf-8"))["values"]
    hdr_v, hdr_f = v[7], f[7]

    def cellv(idx: int) -> Any:
        return hdr_v[idx - 1] if idx - 1 < len(hdr_v) else ""

    months = []
    for i, c in enumerate(range(sr.col_index("C"), sr.col_index("BJ") + 1)):
        raw = cellv(c)
        if not isinstance(raw, (int, float)):
            raise ValueError(f"P&L {sr.col_letter(c)}8 is not a date serial: {raw!r}")
        d = sr.serial_to_date(raw)
        months.append({"index": i, "col": sr.col_letter(c), "col_index": c, "date": d.isoformat(),
                       "serial": raw, "iso_first_of_month": d.replace(day=1).isoformat()})
    fy = []
    for c in range(sr.col_index("BL"), sr.col_index("BP") + 1):
        raw, formula = cellv(c), (hdr_f[c - 1] if c - 1 < len(hdr_f) else "")
        year = None
        if isinstance(raw, (int, float)):
            year = raw if 1990 < raw < 2100 else sr.serial_to_date(raw).year
        else:
            m = re.search(r"(20\d\d)", str(raw))
            year = int(m.group(1)) if m else None
        fy.append({"col": sr.col_letter(c), "col_index": c, "year": int(year) if year is not None else None,
                   "header_value": raw, "header_formula": formula})
    path = out / "P&L.months.json"
    write_json(path, {"sheet": "P&L", "gid": entry["gid"], "header_row": 8, "month_count": len(months),
                      "first_month": months[0]["date"], "last_month": months[-1]["date"],
                      "months": months, "fy_rollups": fy})
    return path


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt manifest is simply rebuilt
        return {}


def _entry_from_disk(out: Path, title: str) -> dict:
    manifest = json.load(open(out / "_manifest.json", encoding="utf-8"))
    for e in manifest["tabs"]:
        if _norm(e["title"]) == _norm(title) and e.get("stem"):
            return e
    raise KeyError(f"{title!r} not in manifest at {out}")


# ------------------------------------------------------------------ main

def _spot_check(reader: Reader, out: Path, log: Callable[[str], None]) -> list[dict]:
    """Re-read three P&L cells from the live sheet and compare with the snapshot."""
    entry = _entry_from_disk(out, "P&L")
    snap = json.load(open(out / f"{entry['stem']}.values.json", encoding="utf-8"))["values"]
    refs = ["W22", "C12", "BJ100"]
    live = reader.batch_get([f"{_quote(entry['title'])}!{r}" for r in refs], "UNFORMATTED_VALUE")
    rows = []
    for ref, grid in zip(refs, live):
        r, c = sr.parse_ref(ref)
        live_v = grid[0][0] if grid and grid[0] else None
        snap_v = snap[r - 1][c - 1] if r - 1 < len(snap) and c - 1 < len(snap[r - 1]) else None
        if isinstance(live_v, (int, float)) and isinstance(snap_v, (int, float)):
            ok = abs(live_v - snap_v) <= max(1e-6, abs(snap_v) * 1e-6)
        else:
            ok = live_v == snap_v
        rows.append({"cell": f"P&L!{ref}", "snapshot": snap_v, "live": live_v, "match": ok})
        log(f"  spot-check P&L!{ref}: snapshot={snap_v!r} live={live_v!r} -> {'OK' if ok else 'DIFFERS'}")
    return rows


def main(out_dir: str | Path | None = None, tabs: list[str] | None = None, dry_run: bool = False,
         spot_check: bool = True, log: Callable[[str], None] = print) -> dict:
    """Capture the sheet into ``out_dir``. ``tabs`` limits the run to those titles (matched
    ignoring surrounding whitespace); the manifest keeps entries for tabs not re-captured."""
    out = Path(out_dir).expanduser() if out_dir else DEFAULT_OUT
    reader = Reader(log=log)
    meta = reader.metadata()
    infos = [_tab_info(s) for s in meta["sheets"]]
    modes = plan_modes(infos)

    wanted = None
    if tabs:
        wanted = {_norm(t) for t in tabs}
        known = {_norm(t["title"]) for t in infos}
        missing = sorted(wanted - known)
        if missing:
            raise SystemExit(f"unknown tab(s): {missing}")
    selected = [t for t in infos if wanted is None or _norm(t["title"]) in wanted]

    full = [t for t in selected if modes[t["gid"]][0] == "full"]
    head_tail = [t for t in selected if modes[t["gid"]][0] == "head_tail"]
    listed = {_norm(t) for t in FULL_TABS + HEAD_TAIL_TABS} | {_norm(t) for t in SKIPPED_TABS}
    unlisted = [t["title"] for t in infos if _norm(t["title"]) not in listed]
    missing_cfg = sorted(listed - {_norm(t["title"]) for t in infos})

    log(f"sheet {meta['properties']['title']!r}: {len(infos)} tabs; capturing {len(full)} full + "
        f"{len(head_tail)} head_tail; skipping {sum(1 for t in selected if modes[t['gid']][0] == 'skipped')}")
    if unlisted:
        log(f"  tabs not in the plan (recorded as skipped): {unlisted}")
    if missing_cfg:
        log(f"  planned tabs NOT found in the sheet: {missing_cfg}")

    if dry_run:
        for t in selected:
            mode, why = modes[t["gid"]]
            log(f"  {mode:9} gid={t['gid']:<11} grid={t['rowCount']}x{t['columnCount']:<4} "
                f"{'hidden ' if t['hidden'] else '       '}{t['title']!r}{'  ('+why+')' if why else ''}")
        n_req = 1 + 2 * len(_group(full)) + (6 if head_tail else 0)
        log(f"dry run: ~{n_req} read requests (metadata already fetched), nothing written")
        return {"dry_run": True, "full": [t["title"] for t in full], "head_tail": [t["title"] for t in head_tail]}

    out.mkdir(parents=True, exist_ok=True)
    started = _now()
    results: dict[int, dict] = {}
    failures: dict[int, str] = {}
    results.update(capture_full(reader, full, out, log, failures))
    results.update(capture_head_tail(reader, head_tail, out, log, failures))

    # manifest (merge with an existing one so --only keeps untouched tabs)
    mpath = out / "_manifest.json"
    old = {e["gid"]: e for e in _load_manifest(mpath).get("tabs", [])}
    entries = []
    for t in infos:
        mode, why = modes[t["gid"]]
        gid = t["gid"]
        if gid in results:
            res = results[gid]
            files = [f"{res['stem']}.formulas.json", f"{res['stem']}.values.json"]
            entry = {**t, "slug": slugify(t["title"]), "stem": res["stem"], "files": files, "mode": mode,
                     "used_rows": res["used_rows"], "used_cols": res["used_cols"], "bytes": res["bytes"],
                     "error_cells": res["error_cells"], "error_samples": res["error_samples"],
                     "snapshot_utc": _now()}
            for k in ("head_rows", "tail_rows", "tail_start_row"):
                if k in res:
                    entry[k] = res[k]
        elif gid in failures:
            entry = {**t, "slug": slugify(t["title"]), "stem": None, "files": [], "mode": mode,
                     "failed": failures[gid], "snapshot_utc": _now()}
        elif mode == "skipped":
            entry = {**t, "slug": slugify(t["title"]), "stem": None, "files": [], "mode": "skipped",
                     "reason": why, "snapshot_utc": _now()}
        elif gid in old:
            entry = {**old[gid], **{k: t[k] for k in ("title", "hidden", "rowCount", "columnCount")}}
        else:
            entry = {**t, "slug": slugify(t["title"]), "stem": None, "files": [], "mode": mode,
                     "not_captured": True, "snapshot_utc": None}
        entries.append(entry)
    prior = _load_manifest(mpath)
    captured = started
    refreshes = list(prior.get("partial_refreshes", []))
    if wanted is not None and prior.get("captured_utc"):  # partial run: keep the original capture time
        captured = prior["captured_utc"]
        refreshes.append({"utc": started, "tabs": sorted(wanted)})
    manifest = {"sheet_id": SHEET_ID, "spreadsheet_title": meta["properties"]["title"],
                "time_zone": meta["properties"].get("timeZone"), "captured_utc": captured,
                "partial_refreshes": refreshes,
                "finished_utc": _now(), "dateTimeRenderOption": "SERIAL_NUMBER",
                "head_rows": HEAD_ROWS, "tail_rows": TAIL_ROWS,
                "script": "scripts/projection/snapshot.py", "tab_count": len(infos), "tabs": entries}
    write_json(mpath, manifest)

    extras = []
    if any(_norm(e["title"]) == "P&L" and e.get("stem") for e in entries):
        try:
            extras = [build_pnl_rowmap(out), build_pnl_months(out)]
        except Exception as exc:  # noqa: BLE001
            log(f"  could not build P&L helper files: {type(exc).__name__}: {exc}")

    spot = []
    pnl_ok = any(_norm(t["title"]) == "P&L" and t["gid"] in results for t in selected)
    if spot_check and pnl_ok:
        try:
            spot = _spot_check(reader, out, log)
        except Exception as exc:  # noqa: BLE001
            log(f"  spot-check failed: {type(exc).__name__}: {exc}")

    # report
    total = sum(p.stat().st_size for p in out.iterdir() if p.is_file())
    log("")
    log(f"{'gid':<11} {'mode':<10} {'rows':>7} {'cols':>5} {'errs':>5} {'KB':>8}  title")
    for e in entries:
        kb = (e.get("bytes") or 0) / 1024
        log(f"{e['gid']:<11} {e['mode']:<10} {e.get('used_rows', ''):>7} {e.get('used_cols', ''):>5} "
            f"{e.get('error_cells', ''):>5} {kb:>8.0f}  {e['title']!r}"
            f"{'  FAILED' if e.get('failed') else ''}{'  hidden' if e['hidden'] else ''}")
    log(f"\ntotal snapshot size: {total / 1e6:.1f} MB in {sum(1 for p in out.iterdir() if p.is_file())} files "
        f"({reader.requests} read requests); out dir {out}")
    if failures:
        log(f"FAILED tabs: {[infos_t['title'] for infos_t in infos if infos_t['gid'] in failures]}")
    manifest["_spot_check"] = spot
    manifest["_failures"] = {str(k): v for k, v in failures.items()}
    return manifest


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    ap.add_argument("--only", default=None, help='comma-separated tab titles, e.g. "P&L,Curves"')
    ap.add_argument("--dry-run", action="store_true", help="list the plan, read no cell data, write nothing")
    ap.add_argument("--no-spot-check", action="store_true")
    args = ap.parse_args(argv)
    tabs = [t.strip() for t in args.only.split(",") if t.strip()] if args.only else None
    result = main(args.out, tabs, dry_run=args.dry_run, spot_check=not args.no_spot_check)
    return 1 if result.get("_failures") else 0


if __name__ == "__main__":
    sys.exit(_cli())
