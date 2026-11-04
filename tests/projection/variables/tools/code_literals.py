"""Code audit tool: numeric literals in the projection module packages (not tests).

    python tests/projection/variables/tools/code_literals.py [--module drivers] [--all]

Lists every numeric constant that is not structural: skips 0, 1, 2, -1, 12 (months), 100, 1000, 30 and 365 inside calendar arithmetic is NOT skipped on purpose
(a day count can be a business assumption). Also skips literals inside docstrings/f-string text, `range`/slice bounds of 0/1, HTTP status codes in ApiError(...),
dict keys named min/max (validation bounds), limits named MAX_*/LIMIT, and tolerance names (EPS, TOL). Output: file:line  literal  | source line.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "app" / "projection"
SKIP = {0, 1, 2, -1, 12, 100, 0.0, 1.0, 2.0, -1.0, 100.0}
SKIP_FILES = {"api.py", "routes.py", "views.py", "table.py", "tables.py", "upload.py", "qb.py", "xlsx_read.py", "reconcile_map.py", "freshness.py", "store.py"}
SKIP_NAME_PARTS = ("MAX_", "LIMIT", "EPS", "TOL", "WIDTH", "HEIGHT", "ROUND", "PALETTE", "TIMEOUT", "PAGE", "SIZE")


class V(ast.NodeVisitor):
    def __init__(self, src: list[str], path: Path):
        self.src, self.path, self.out = src, path, []
        self.stack: list[ast.AST] = []

    def generic_visit(self, node):
        self.stack.append(node)
        super().generic_visit(node)
        self.stack.pop()

    def _in(self, cls) -> bool:
        return any(isinstance(n, cls) for n in self.stack)

    def visit_Constant(self, node: ast.Constant):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v in SKIP:
            return
        parent = self.stack[-1] if self.stack else None
        if isinstance(parent, ast.Call) and getattr(parent.func, "id", "") in ("ApiError", "range", "round", "_fail"):
            return
        if isinstance(parent, ast.Subscript) or (isinstance(parent, ast.Slice)):
            return
        # assignment target name
        for n in reversed(self.stack):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                tg = n.targets[0] if isinstance(n, ast.Assign) else n.target
                name = getattr(tg, "id", getattr(tg, "attr", ""))
                if any(p in str(name).upper() for p in SKIP_NAME_PARTS):
                    return
                break
        line = self.src[node.lineno - 1].strip()
        if line.startswith(("#", '"', "'")):
            return
        self.out.append((node.lineno, v, line[:150]))


def scan(path: Path):
    src = path.read_text().splitlines()
    tree = ast.parse("\n".join(src))
    v = V(src, path)
    v.visit(tree)
    return v.out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module")
    ap.add_argument("--all", action="store_true", help="include api/views/store/upload files")
    a = ap.parse_args()
    mods = [a.module] if a.module else sorted(p.name for p in PKG.iterdir() if p.is_dir() and not p.name.startswith("_") and p.name != "variables")
    total = 0
    for m in mods:
        for f in sorted((PKG / m).glob("*.py")):
            if f.name in SKIP_FILES and not a.all:
                continue
            hits = scan(f)
            for ln, val, line in hits:
                print(f"{m}/{f.name}:{ln:<4} {val!s:<10} | {line}")
            total += len(hits)
    print(f"# {total} literals", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
