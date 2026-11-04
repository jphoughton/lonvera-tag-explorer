"""Turns the sheet fills (fills_all.json from fetch_sheet_fills.py) and the frozen snapshot into fixtures/yellow_cells.json:
for every model tab, the yellow (input) cells by row, split into typed values and formulas. Yellow = the amber / butter / pure yellow fills Josh uses for inputs.

    python tests/projection/variables/tools/yellow_inventory.py [fills_all.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "projection"))
import snapshot_read as S                          # noqa: E402
from snapshot_read import col_letter               # noqa: E402


def is_yellow(rgb) -> bool:
    r, g, b = rgb
    return r >= 0.99 and g >= 0.9 and b <= 0.82


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "fills_all.json"
    fills = json.loads(src.read_text())
    out = {}
    for tab, cells in fills.items():
        rows: dict[int, dict] = {}
        for k, rgb in cells.items():
            if not is_yellow(rgb):
                continue
            r, c = map(int, k.split(","))
            try:
                f = S.cell(tab, f"{col_letter(c)}{r}", "formulas")
            except KeyError:
                continue
            kind = "formula" if isinstance(f, str) and f.startswith("=") else ("typed" if f not in (None, "") else "empty")
            d = rows.setdefault(r, {"typed": 0, "formula": 0, "empty": 0, "first": c, "last": c})
            d[kind] += 1
            d["first"], d["last"] = min(d["first"], c), max(d["last"], c)
        if rows:
            out[tab] = {"cells": sum(d["typed"] + d["formula"] + d["empty"] for d in rows.values()), "typed": sum(d["typed"] for d in rows.values()),
                        "formula": sum(d["formula"] for d in rows.values()), "empty": sum(d["empty"] for d in rows.values()),
                        "rows": {str(r): {**d, "range": f"{col_letter(d['first'])}:{col_letter(d['last'])}"} for r, d in sorted(rows.items())}}
    (HERE.parent / "fixtures" / "yellow_cells.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    for tab, d in out.items():
        print(f"{tab:<40} yellow {d['cells']:>5}  typed {d['typed']:>5}  formula {d['formula']:>5}  empty {d['empty']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
