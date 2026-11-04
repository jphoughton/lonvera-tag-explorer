"""Reconcile the projection engine against the Lonvera Projections sheet, cell by cell.

    PYTHONPATH=. DATABASE_URL=postgresql://localhost/<db> python scripts/projection/reconcile.py --module cohorts
    ... --module all --scenario current --tolerance 0.005 --abs 1 --format json
    ... --live          # re-read the sheet (read-only) instead of the frozen snapshot

Each module supplies `app/projection/<module>/reconcile_map.py` exposing MAP, a list of entries:

    {"key": "nc_rev", "tab": "P&L", "row": 12, "c0": "C", "c1": "BJ", "kind": "values", "result": "drivers.nc_rev"}

    kind "values" (default)  one row, columns c0..c1; the month of column c0 is `month0` (default 2025-01-01), one column per month
    kind "column"            one column `c0`, rows r0..r1; result is a list, element i <-> row r0+i (e.g. a curve by age)
    kind "scalar"            one cell: tab, row, c0
    result                   dotted path into the EngineResult: 'drivers.nc_rev', 'pnl.lines.noi', 'cohorts.curve_live', 'demand.units.revamp|bull'
    scale (1.0)              sheet value is multiplied by this before comparing
    tol / abs                per-line override of --tolerance / --abs

A cell passes when |engine - sheet| <= abs  OR  relative <= tolerance, where relative = |engine - sheet| / max(|engine|, |sheet|).
Blank sheet cells count as 0; text and error cells (#REF!, labels) are skipped and counted. Exit code 0 = every line passes, 1 = a line is over
tolerance or could not be resolved, 2 = usage / configuration problem.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SHEET_ID = "1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI"
SHEETS_PY_DIR = Path(os.environ.get("SHEETS_PY_DIR", "~/Desktop/Google Sheets")).expanduser()
from app.projection.engine import ALIAS                          # result name ('demand') -> module key ('inventory_demand')

MODULE_KEYS = ("cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing", "pacing")


# ------------------------------------------------------------------------------------------------------------------ A1 helpers

def col_idx(letters: str) -> int:
    n = 0
    for ch in letters.strip().upper():
        if not ch.isalpha():
            raise ValueError(f"bad column {letters!r}")
        n = n * 26 + ord(ch) - 64
    return n


def col_letters(i: int) -> str:
    out = ""
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------------------------------------------------------------ sheet sources

class GridSource:
    """Rows/columns/cells (1-based, inclusive) over a `grid(tab) -> list[list]` of unformatted values."""

    def grid(self, tab: str) -> list[list]:
        raise NotImplementedError

    def row(self, tab: str, r: int, c0: str, c1: str) -> list:
        g = self.grid(tab)
        line = g[r - 1] if 1 <= r <= len(g) else []
        return [_blank(line[c - 1]) if c <= len(line) else None for c in range(col_idx(c0), col_idx(c1) + 1)]

    def col(self, tab: str, c: str, r0: int, r1: int) -> list:
        g, ci = self.grid(tab), col_idx(c)
        return [_blank(g[r - 1][ci - 1]) if 1 <= r <= len(g) and ci <= len(g[r - 1]) else None for r in range(r0, r1 + 1)]

    def cell(self, tab: str, r: int, c: str) -> Any:
        return self.row(tab, r, c, c)[0]


def _blank(v: Any) -> Any:
    return None if v == "" else v


class DictSource(GridSource):
    """In-memory tabs, for tests: {tab: [[...], ...]}."""

    def __init__(self, tabs: dict[str, list[list]]):
        self.tabs = tabs

    def grid(self, tab: str) -> list[list]:
        if tab not in self.tabs:
            raise KeyError(f"tab not found: {tab!r}")
        return self.tabs[tab]


class SnapshotSource(GridSource):
    """The frozen snapshot via scripts/projection/snapshot_read.py."""

    def __init__(self):
        from scripts.projection import snapshot_read as sr
        self.sr = sr

    def grid(self, tab: str) -> list[list]:
        return self.sr.load(tab)["values"]

    def row(self, tab: str, r: int, c0: str, c1: str) -> list:
        return self.sr.row(tab, r, c0, c1)

    def col(self, tab: str, c: str, r0: int, r1: int) -> list:
        return self.sr.col(tab, c, r0, r1)


class LiveSource(GridSource):
    """The live sheet, read-only, one values.get per tab (cached)."""

    def __init__(self, sheet_id: str = DEFAULT_SHEET_ID):
        sys.path.insert(0, str(SHEETS_PY_DIR))
        import sheets                                         # noqa: PLC0415  (Desktop/Google Sheets/sheets.py)
        self.svc = sheets._svc()
        self.sheet_id = sheet_id
        meta = self.svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties(title,gridProperties)").execute()
        self.size = {s["properties"]["title"]: s["properties"]["gridProperties"] for s in meta["sheets"]}
        self._cache: dict[str, list[list]] = {}

    def grid(self, tab: str) -> list[list]:
        if tab not in self._cache:
            gp = self.size[tab]
            rng = f"'{tab}'!A1:{col_letters(gp['columnCount'])}{gp['rowCount']}"
            res = self.svc.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=rng, valueRenderOption="UNFORMATTED_VALUE",
                                                       dateTimeRenderOption="SERIAL_NUMBER").execute()
            self._cache[tab] = res.get("values", [])
        return self._cache[tab]


# ------------------------------------------------------------------------------------------------------------------ comparison

@dataclass
class LineReport:
    module: str
    key: str
    result: str
    cells: int = 0
    skipped: int = 0
    max_abs: float = 0.0
    max_abs_at: str = ""
    max_rel: float = 0.0
    max_rel_at: str = ""
    over: int = 0
    status: str = "ok"                  # ok | FAIL | ERROR | stub
    error: str = ""
    worst: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("module", "key", "result", "cells", "skipped", "max_abs", "max_abs_at", "max_rel", "max_rel_at",
                                              "over", "status", "error", "worst")}


def _month_add(d: date, n: int) -> date:
    k = d.year * 12 + d.month - 1 + n
    return date(k // 12, k % 12 + 1, 1)


def _sheet_cells(entry: dict, source: GridSource) -> list[tuple[str, Any]]:
    """[(label, sheet value)] for the entry; label is the month ISO date, the row number, or the key."""
    kind = entry.get("kind", "values")
    if kind == "values":
        vals = source.row(entry["tab"], int(entry["row"]), entry["c0"], entry["c1"])
        m0 = date.fromisoformat(entry.get("month0", "2025-01-01"))
        return [(_month_add(m0, i).isoformat(), v) for i, v in enumerate(vals)]
    if kind == "column":
        vals = source.col(entry["tab"], entry["c0"], int(entry["r0"]), int(entry["r1"]))
        return [(f"row {int(entry['r0']) + i}", v) for i, v in enumerate(vals)]
    if kind == "scalar":
        return [(entry["key"], source.cell(entry["tab"], int(entry["row"]), entry["c0"]))]
    raise ValueError(f"unknown kind {kind!r}")


def _engine_value(got: Any, kind: str, i: int, label: str) -> float:
    if kind == "scalar":
        return float(got) if _num(got) else 0.0
    if isinstance(got, dict):
        v = got.get(date.fromisoformat(label)) if kind == "values" else got.get(i)
    elif isinstance(got, (list, tuple)):
        v = got[i] if i < len(got) else None
    else:
        v = None
    return float(v) if _num(v) else 0.0


def reconcile_entry(module: str, entry: dict, source: GridSource, result: Any, tol: float, abs_tol: float) -> LineReport:
    kind = entry.get("kind", "values")
    rep = LineReport(module=module, key=entry["key"], result=entry["result"])
    tol, abs_tol = float(entry.get("tol", tol)), float(entry.get("abs", abs_tol))
    scale = float(entry.get("scale", 1.0))
    try:
        got = result.resolve(entry["result"])
        cells = _sheet_cells(entry, source)
    except Exception as e:                                    # unresolved path, missing tab, bad entry
        rep.status, rep.error = "ERROR", f"{type(e).__name__}: {e}"
        return rep
    for i, (label, sv) in enumerate(cells):
        if sv is not None and not _num(sv):
            rep.skipped += 1
            continue
        sheet = (float(sv) if sv is not None else 0.0) * scale
        eng = _engine_value(got, kind, i, label)
        if not (math.isfinite(sheet) and math.isfinite(eng)):
            rep.skipped += 1
            continue
        d = abs(eng - sheet)
        denom = max(abs(eng), abs(sheet))
        rel = d / denom if denom > 0 else 0.0
        rep.cells += 1
        if d > rep.max_abs:
            rep.max_abs, rep.max_abs_at = d, label
        if rel > rep.max_rel:
            rep.max_rel, rep.max_rel_at = rel, label
        if d > abs_tol and rel > tol:
            rep.over += 1
            if len(rep.worst) < 10:
                rep.worst.append({"at": label, "engine": eng, "sheet": sheet, "abs": d, "rel": rel})
    rep.status = "FAIL" if rep.over else "ok"
    return rep


def load_map(module: str, package: str = "app.projection") -> list[dict] | None:
    try:
        return list(importlib.import_module(f"{package}.{module}.reconcile_map").MAP)
    except ModuleNotFoundError as e:
        if e.name and e.name.endswith(("reconcile_map", f".{module}")):
            return None
        raise


def reconcile(maps: dict[str, list[dict]], source: GridSource, result: Any, tol: float = 0.005, abs_tol: float = 1.0) -> list[LineReport]:
    out = []
    for module, entries in maps.items():
        for entry in entries:
            rep = reconcile_entry(module, entry, source, result, tol, abs_tol)
            root = entry["result"].split(".")[0]
            alias = getattr(result, "stubbed", {})
            if alias.get(ALIAS.get(root, root)):
                rep.status = "stub"                                   # the engine used the fixture stub: the comparison proves nothing
            out.append(rep)
    return out


# ------------------------------------------------------------------------------------------------------------------ output

def _fmt(x: float, nd: int = 4) -> str:
    return f"{x:,.{nd}f}" if abs(x) < 1e6 else f"{x:,.0f}"


def render_table(reports: list[LineReport], skipped_modules: list[str]) -> str:
    head = ["module", "line", "cells", "skipped", "max abs diff", "at", "max rel diff", "over tol", "status"]
    rows = [head] + [[r.module, r.key, str(r.cells), str(r.skipped), _fmt(r.max_abs, 2), r.max_abs_at, f"{r.max_rel:.4%}", str(r.over),
                      r.status if not r.error else f"{r.status}: {r.error}"] for r in reports]
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = ["  ".join(c.ljust(widths[i]) if i in (0, 1, 5, 8) else c.rjust(widths[i]) for i, c in enumerate(row)) for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    bad = [r for r in reports if r.status in ("FAIL", "ERROR")]
    stub = [r for r in reports if r.status == "stub"]
    lines.append("")
    lines.append(f"{len(reports)} lines: {len(reports) - len(bad) - len(stub)} ok, {len(bad)} over tolerance or unresolved, {len(stub)} on a stub (not reconciled)"
                 + (f"; no reconcile_map: {', '.join(skipped_modules)}" if skipped_modules else ""))
    for r in bad:
        for w in r.worst[:3]:
            lines.append(f"  {r.module}.{r.key} {w['at']}: engine {_fmt(w['engine'], 2)} vs sheet {_fmt(w['sheet'], 2)} (abs {_fmt(w['abs'], 2)}, rel {w['rel']:.2%})")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile the projection engine against the Lonvera Projections sheet.")
    ap.add_argument("--module", required=True, help="module key or 'all'")
    ap.add_argument("--scenario", default="current")
    ap.add_argument("--tolerance", type=float, default=0.005, help="relative tolerance per cell (default 0.5%%)")
    ap.add_argument("--abs", dest="abs_tol", type=float, default=1.0, help="absolute tolerance per cell (default 1)")
    ap.add_argument("--format", choices=("table", "json"), default="table")
    ap.add_argument("--live", action="store_true", help="read the live sheet (read-only) instead of the frozen snapshot")
    args = ap.parse_args(argv)

    wanted = list(MODULE_KEYS) if args.module == "all" else [args.module]
    if args.module != "all" and args.module not in MODULE_KEYS:
        print(f"unknown module {args.module!r}; one of {', '.join(MODULE_KEYS)} or all", file=sys.stderr)
        return 2
    maps, missing = {}, []
    for m in wanted:
        mp = load_map(m)
        if mp is None:
            missing.append(m)
        else:
            maps[m] = mp
    if not maps:
        print(f"no reconcile_map found for: {', '.join(missing)}", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    import psycopg
    from psycopg.rows import dict_row
    from app.projection import engine

    try:
        source: GridSource = LiveSource() if args.live else SnapshotSource()
    except Exception as e:
        print(f"cannot open the sheet source: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    with psycopg.connect(url, row_factory=dict_row) as conn:
        result = engine.run(args.scenario, conn, strict=True)
    reports = reconcile(maps, source, result, args.tolerance, args.abs_tol)
    if args.format == "json":
        print(json.dumps({"scenario": args.scenario, "source": "live" if args.live else "snapshot", "tolerance": args.tolerance, "abs": args.abs_tol,
                          "no_map": missing, "lines": [r.to_dict() for r in reports]}, indent=2, default=str))
    else:
        print(render_table(reports, missing))
    if args.module != "all" and any(r.status == "stub" for r in reports):
        print(f"module {args.module} is not built: its stub reads the snapshot, so the comparison is not meaningful", file=sys.stderr)
        return 2
    return 1 if any(r.status in ("FAIL", "ERROR") for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
