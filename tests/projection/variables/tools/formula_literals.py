"""Sheet audit tool: numeric literals inside formulas of the frozen snapshot.

    python tests/projection/variables/tools/formula_literals.py [--tabs "P&L,Payback"] [--all] [--json out.json]

For every formula it removes string literals, cell/range references, sheet names and function names, and lists the numbers that remain. A literal is
ignored when it is structural: 0, 1, 2, 12 (months), 100 (percent), 0.5 at ROUND/ABS, date constructors and array indexes. Formulas that differ only in
their references are grouped (R1C1-style pattern) so a 40-column row prints once with a count.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts" / "projection"))
import snapshot_read as S                                     # noqa: E402
from snapshot_read import col_letter                           # noqa: E402

MODEL_TABS = ["P&L", "Payback", "Term Debt Monthly Paydown", "Curves", "Hist Waterfall - Last 4 Months Curve", "Cohort Data - Unified", "COGS",
              "COGS Adjustments", "Fixed Expenses", "Vendor COGS", "Spend-ROAS Matrix", "Automated Pacing ", "Month Snapshot", "Purchase Orders",
              "FBA Transfers", "Warehance Fcst Inputs", "Amazon Fcst Inputs", "Intl Fcst Inputs", "Warehance Demand Fcst", "Amazon Demand Fcst",
              "Intl Demand Fcst", "Total Demand Fcst", "Warehance Inventory Burn", "Intl Inventory Burn", "Amazon Inventory Burn", "Total Inventory Burn",
              "Warehance Reorder", "Intl Reorder", "Inventory Value", "Inventory Cash Outlay", "Rollup - Monthly (MoM)", "Rollup - Weekly (WoW)",
              "Pacing - Daily (180d)", "Meta - Daily (180d)", "Meta - Weekly (WoW)", "Meta - Monthly (MoM)", "Warehance Weekly", "Warehance Daily",
              "Amazon Monthly Units", "Intl By Country"]
IGNORE = {"0", "1", "2", "12", "100"}
_STR = re.compile(r'"(?:[^"]|"")*"')
_SHEET = re.compile(r"(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!")
_REF = re.compile(r"\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|\$?\d+:\$?\d+")
_FUNC = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?=\()")
_NUM = re.compile(r"(?<![A-Za-z_$.\d])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![A-Za-z_\d])")
_DATE = re.compile(r"DATE\([^)]*\)", re.I)


def literals(formula: str) -> list[str]:
    f = _STR.sub('""', formula)
    f = _SHEET.sub("", f)
    f = _DATE.sub("DATE()", f)
    f = _FUNC.sub("F", f)
    f = _REF.sub("REF", f)
    out = []
    for m in _NUM.finditer(f):
        n = m.group(1)
        if n in IGNORE:
            continue
        out.append(n)
    return out


def pattern(formula: str) -> str:
    f = _STR.sub('""', formula)
    f = _REF.sub("REF", f)
    return re.sub(r"\s+", "", f)[:160]


def scan(tab: str) -> list[dict]:
    try:
        obj = S.load(tab, "formulas")
    except KeyError:
        return []
    rows = obj.get("values")
    if rows is None:                                           # head/tail capture
        rows = list(obj["head"]) + list(obj.get("tail") or [])
        base = [(i + 1) for i in range(len(obj["head"]))] + [obj["tail_start_row"] + i for i in range(len(obj.get("tail") or []))]
    else:
        base = list(range(1, len(rows) + 1))
    groups: dict[tuple, dict] = {}
    for r, row in zip(base, rows):
        for c, v in enumerate(row, start=1):
            if isinstance(v, str) and v.startswith("="):
                lits = literals(v)
                if not lits:
                    continue
                key = (pattern(v), tuple(lits))
                g = groups.setdefault(key, {"tab": tab, "cell": f"{col_letter(c)}{r}", "row": r, "col": c, "formula": v[:220], "literals": lits, "count": 0, "cells": []})
                g["count"] += 1
                if len(g["cells"]) < 4:
                    g["cells"].append(f"{col_letter(c)}{r}")
    return sorted(groups.values(), key=lambda g: (g["row"], g["col"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs")
    ap.add_argument("--json")
    a = ap.parse_args()
    tabs = [t.strip() for t in a.tabs.split(",")] if a.tabs else MODEL_TABS
    result = []
    for t in tabs:
        g = scan(t)
        result += g
        if not a.json:
            print(f"== {t}: {len(g)} patterns")
            for x in g:
                print(f"  {x['cell']:<7} x{x['count']:<3} {x['literals']}  {x['formula'][:130]}")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
