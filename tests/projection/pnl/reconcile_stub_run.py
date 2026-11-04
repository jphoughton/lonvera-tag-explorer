"""Dev tool: reconcile the pnl module alone against the sheet, with every upstream module replaced by its fixture stub (the sheet's own typed inputs).

    DATABASE_URL=postgresql://localhost/tagexp_proj_pnl PYTHONPATH=. python tests/projection/pnl/reconcile_stub_run.py [--format json] [--worst N]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "projection"))

import psycopg                                        # noqa: E402
from psycopg.rows import dict_row                     # noqa: E402

import reconcile as rec                               # noqa: E402
from app.projection import engine, registry           # noqa: E402


def stub_package() -> str:
    root = Path(tempfile.mkdtemp())
    name = "pnlonly_" + uuid.uuid4().hex[:6]
    (root / name / "pnl").mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (root / name / "pnl" / "__init__.py").write_text(textwrap.dedent("from app.projection.pnl import MODULE\n"))
    sys.path.insert(0, str(root))
    return name


def run(url: str, scenario: str = "current", tol: float = 0.005, abs_tol: float = 1.0):
    pkg = stub_package()
    with psycopg.connect(url, row_factory=dict_row) as conn:
        result = engine.run(scenario, conn, strict=True, package=pkg)
    maps = {"pnl": rec.load_map("pnl")}
    return rec.reconcile(maps, rec.SnapshotSource(), result, tol, abs_tol), result


def main() -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=("table", "json"), default="table")
    ap.add_argument("--worst", type=int, default=3)
    a = ap.parse_args()
    reports, _ = run(os.environ["DATABASE_URL"])
    if a.format == "json":
        print(json.dumps([r.to_dict() for r in reports], indent=1, default=str))
    else:
        print(rec.render_table(reports, []))
    return 1 if any(r.status in ("FAIL", "ERROR") for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
