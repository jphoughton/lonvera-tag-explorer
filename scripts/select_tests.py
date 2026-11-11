#!/usr/bin/env python3
"""Pick the tests affected by a change (scripts/test.sh --fast).

    scripts/select_tests.py [--base origin/build/v1] [--files a b c ...] [--json] [--root DIR]

stdout: the word FULL, or one pytest target per line (test files, plus a few single test node ids of the always-run smoke set).
stderr: which rule picked which tests, per changed file.

It keeps no cached state (nothing to go stale): every run reads the file tree it is run in (work tree at HEAD plus uncommitted and untracked files).

1. Hard rules, checked first. A change to a migration, a conftest, the test harness itself, a requirements file, the Dockerfile, a deploy script, a test
   fixture file, a hub module, or any file the selector cannot place selects the FULL suite. A hub is a module that more than 30% of the test files import
   (directly, or through the conftest files above them) plus the hand-made list in HUB_FILES. Documentation selects the smoke set plus any test that reads it.
2. A dependency graph of the repository: Python imports (static; string references such as `-m etl.foo`; the run-time module discovery in app.projection;
   `ROOT / "docs" / "x.md"` style paths), JavaScript imports, script tags, and the route table of app.js. A changed file selects every test file that reaches it,
   but the walk never passes THROUGH a hub (everything imports the hubs, so following them selects everything).
3. Ties the import graph cannot see: a Flask module that defines /api/trends is exercised by every test that requests /api/trends; a page script is
   exercised by every UI test that opens its route; every browser test loads the shared shell files; the conftest files above a test are imports of it.
4. The smoke set always runs: auth, the schema documentation, the CSS class-name check, and one UI test per page.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL = "FULL"
HUB_FRACTION = 0.30

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "shots", "analysis"}
PY_ROOTS = ("app", "etl", "brain", "tests", "scripts")

FULL_FILES = {
    "tests/_xdist_env.py", "tests/_servers.py", "scripts/test.sh", "Dockerfile", "railway.json", ".dockerignore", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
    "wsgi.py", "requirements.txt", "requirements-dev.txt",
}
FULL_PATTERNS = [
    r"^migrations/", r"(^|/)conftest\.py$", r"^requirements[^/]*\.txt$", r"^scripts/deploy/", r"^tests/fixtures/", r"(^|/)pytest\.ini$", r"^\.github/workflows/",
]
# hubs named by hand; the 30% rule adds to these
HUB_FILES = {
    "app/__init__.py", "app/api.py", "app/auth.py", "app/db.py", "app/config.py", "app/queries.py", "etl/db.py", "etl/__init__.py", "app/projection/__init__.py",
}
# imports everything (app.js imports every page script) but is not itself "everything breaks": the walk stops there, a change to it selects every UI test
WALK_BARRIERS = {"app/static/app.js", "app/static/index.html"}
SMOKE_FILES = ["tests/api/test_api_auth.py", "tests/api/test_schema_docs.py", "tests/test_static_classnames.py"]
# one UI test per page (node ids; ones that no longer exist are dropped with a note)
SMOKE_NODES = [
    "tests/ui/test_ui.py::test_login_flow",                                                                    # login
    "tests/ui/test_ui.py::test_products_landing",                                                              # products (Meta home)
    "tests/ui/test_ui.py::test_campaigns_view",                                                                # campaigns
    "tests/ui/test_ui.py::test_google_campaigns_view_has_the_campaigns_first_and_types_as_a_filter",           # google
    "tests/ui/test_ui.py::test_all_platforms_overview",                                                        # all platforms
    "tests/ui/test_ui.py::test_platform_campaigns_view_product_column_and_filter",                             # all campaigns
    "tests/ui/test_ui.py::test_creative_ad_vocabulary_headings_legend_and_columns",                            # product page
    "tests/ui/test_ui.py::test_see_the_ad_section_formats_iframe_and_sizes",                                   # ad page
    "tests/ui/test_promotions.py::test_page_renders_every_section_and_defaults_to_the_last_30_days",           # promotions
    "tests/ui/test_scale_views.py::test_the_page_opens_on_what_should_be_scaled_and_has_four_tabs",            # scale views
    "tests/ui/test_trends.py::test_nav_link_and_default_week_view",                                            # trends
    "tests/ui/test_priorities.py::test_everything_is_collapsed_on_load_and_the_detail_panels_are_hidden",      # priorities
    "tests/ui/test_priorities_production.py::test_table_renders_with_plain_columns_and_open_rows_only",        # production board
    "tests/ui/test_lookup.py::test_exact_name_shows_the_new_tags_and_the_link",                                # tag lookup
    "tests/projection/ui/test_shell.py::test_nav_renders_seven_modules_with_built_state",                      # projection shell
]
NO_TEST_PREFIXES = ("analysis/", ".claude/", ".mcp.json", "README.md", "CLAUDE.md", ".gitignore", "scripts/auth/")

ROUTE_RE = re.compile(r"""\.(?:route|get|post|put|delete|patch)\(\s*[rf]?["'](/[^"'<{]*)""")
URL_PREFIX_RE = re.compile(r"""url_prefix\s*=\s*["'](/[^"']*)["']""")
STR_MODULE_RE = re.compile(r"^(?:app|etl|brain|scripts|tests)(?:\.[A-Za-z_][A-Za-z_0-9]*)+$")
JS_IMPORT_RE = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.M)


class Repo:
    """An index of the files under `root`: Python modules and their imports, JS modules and their imports, test files, and the graph between them."""

    def __init__(self, root: Path, files: list[str] | None = None):
        self.root = Path(root)
        self.files: set[str] = set(files) if files is not None else self._walk()
        self.py: dict[str, str] = {}                      # module name -> relative path
        self.mod_of: dict[str, str] = {}                  # relative path -> module name
        for f in self.files:
            if f.endswith(".py") and (f.split("/")[0] in PY_ROOTS or f == "wsgi.py"):
                parts = f[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                self.py[".".join(parts)] = f
                self.mod_of[f] = ".".join(parts)
        self._text: dict[str, str] = {}
        self.tests = sorted(f for f in self.files if self.is_test(f))
        self.conftests = sorted(f for f in self.files if f.endswith("conftest.py"))
        self.edges: dict[str, set[str]] = defaultdict(set)        # file -> files it depends on
        self._build_edges()
        self._add_discovery_edges()
        self._add_route_edges()
        self._add_shell_edges()
        self.rev: dict[str, set[str]] = defaultdict(set)
        for a, bs in self.edges.items():
            for b in bs:
                self.rev[b].add(a)
        self._hubs: set[str] | None = None

    # ---- files ----
    def _walk(self) -> set[str]:
        out: set[str] = set()
        for dp, dns, fns in os.walk(self.root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            rel = os.path.relpath(dp, self.root)
            for fn in fns:
                out.add(fn if rel == "." else f"{rel}/{fn}")
        return out

    def text(self, f: str) -> str:
        if f not in self._text:
            try:
                self._text[f] = (self.root / f).read_text(errors="replace")
            except OSError:
                self._text[f] = ""
        return self._text[f]

    @staticmethod
    def is_test(f: str) -> bool:
        return f.startswith("tests/") and bool(re.search(r"(^|/)test_[^/]*\.py$", f))

    def is_browser_test(self, t: str) -> bool:
        return "playwright" in self.text(t) or any("playwright" in self.text(c) for c in self.conftests_above(t))

    def conftests_above(self, f: str) -> list[str]:
        d = os.path.dirname(f)
        return [c for c in self.conftests if os.path.dirname(c) == "" or d == os.path.dirname(c) or d.startswith(os.path.dirname(c) + "/")]

    # ---- python imports ----
    def _resolve(self, name: str) -> str | None:
        while name:
            if name in self.py:
                return self.py[name]
            name = name.rpartition(".")[0]
        return None

    def _py_deps(self, f: str) -> set[str]:
        try:
            tree = ast.parse(self.text(f))
        except SyntaxError:
            return set()
        pkg = f[:-3].split("/")[:-1]
        out: set[str] = set()

        here = "/".join(pkg)

        def add(modname: str) -> None:
            r = self._resolve(modname)
            if r:
                out.add(r)
                return
            top = modname.split(".")[0]                                # a bare import of a sibling file ("import why_seed", "from conftest import x"): pytest puts the test's directory on sys.path
            for cand in (f"{here}/{top}.py", f"{here}/{top}/__init__.py"):
                if cand in self.files:
                    out.add(cand)
                    return

        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    add(a.name)
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base_parts = pkg[: len(pkg) - (n.level - 1)] if n.level > 1 else pkg
                    base = ".".join(base_parts + ([n.module] if n.module else []))
                else:
                    base = n.module or ""
                if base:
                    add(base)
                for a in n.names:
                    add(f"{base}.{a.name}" if base else a.name)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 200:
                v = n.value
                if STR_MODULE_RE.match(v):                                   # "etl.projection_pacing" (subprocess -m, import_module)
                    add(v)
                elif "/" in v or re.search(r"\.(js|css|html|json|md|csv|sql|txt)$", v):
                    self._string_path(f, v, out)
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                p = self._div_chain(n)
                if p and len(p) > 1:
                    self._string_path(f, "/".join(p), out)
        out.discard(f)
        return out

    @staticmethod
    def _div_chain(n) -> list[str] | None:
        """ROOT / "docs" / "projection" / "X.md" -> ["docs", "projection", "X.md"] when every right-hand side is a string"""
        parts: list[str] = []
        cur = n
        while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                parts.append(cur.right.value)
            else:
                return None
            cur = cur.left
        if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
            parts.append(cur.value)
        parts.reverse()
        return parts

    def _string_path(self, f: str, s: str, out: set[str]) -> None:
        s = s.strip()
        if s.startswith(("http", "/api/")) or "{" in s or " " in s:
            return
        s = s.lstrip("/")
        if s.startswith("static/"):
            s = "app/" + s
        if not s:
            return
        here = os.path.dirname(f)
        for cand in (s, f"{here}/{s}" if here else s):
            cand = os.path.normpath(cand).replace(os.sep, "/")
            if cand in self.files:
                out.add(cand)
                return
        d = os.path.normpath(s).replace(os.sep, "/").rstrip("/") + "/"
        if "." not in os.path.basename(d.rstrip("/")):
            hits = {x for x in self.files if x.startswith(d)}
            if hits and len(hits) <= 400:
                out.update(hits)

    def _js_deps(self, f: str) -> set[str]:
        out: set[str] = set()
        base = os.path.dirname(f)
        for m in JS_IMPORT_RE.finditer(self.text(f)):
            t = m.group(1)
            if t.startswith("."):
                p = os.path.normpath(os.path.join(base, t)).replace(os.sep, "/")
            elif t.startswith("/static/"):
                p = "app/static/" + t[len("/static/"):]
            else:
                continue
            if p in self.files:
                out.add(p)
        return out

    def _html_deps(self, f: str) -> set[str]:
        out: set[str] = set()
        for m in re.finditer(r"""(?:src|href)=["']([^"':]+\.(?:js|css))["']""", self.text(f)):
            p = m.group(1)
            p = "app/static/" + p[len("/static/"):] if p.startswith("/static/") else os.path.normpath(os.path.join(os.path.dirname(f), p)).replace(os.sep, "/")
            if p in self.files:
                out.add(p)
        return out

    def _build_edges(self) -> None:
        for f in self.files:
            if f == "scripts/select_tests.py":                      # it names modules in strings; it is not part of the app
                continue
            if f in self.mod_of:
                self.edges[f] |= self._py_deps(f)
            elif f.endswith(".js") and f.startswith("app/static/"):
                self.edges[f] |= self._js_deps(f)
            elif f.endswith(".html") and f.startswith("app/static/"):
                self.edges[f] |= self._html_deps(f)
        for t in self.tests:                                       # the conftest files above a test are imports of it
            self.edges[t].update(self.conftests_above(t))

    def _add_discovery_edges(self) -> None:
        """app.projection finds its module packages at run time (pkgutil). A test that itself walks the discovered modules (registry.discover / listing) reaches
        every module package; a test that only builds an app on top of the registry does not (a module that fails to import is skipped, with a warning)."""
        pkgs = {f for m, f in self.py.items() if re.fullmatch(r"app\.projection\.[a-z_0-9]+", m) and f.endswith("/__init__.py")}
        for t in self.tests:
            if re.search(r"\b(?:discover|listing|register_modules)\(", self.text(t)):
                self.edges[t] |= pkgs

    def _add_route_edges(self) -> None:
        """a test that requests /api/trends depends on the module that defines that route"""
        routes: dict[str, set[str]] = defaultdict(set)         # "/api/trends" -> modules defining a route below it
        for f in self.files:
            if f.endswith(".py") and f.startswith(("app/", "brain/")) and f in self.mod_of:
                src = self.text(f)
                prefixes = URL_PREFIX_RE.findall(src) or [""]
                for m in ROUTE_RE.finditer(src):
                    path = m.group(1).rstrip("/")
                    for pre in prefixes:
                        full = path if path.startswith(pre) else pre.rstrip("/") + path
                        segs = [s for s in full.split("/") if s][:2]
                        if len(segs) == 2 and segs[0] == "api":
                            routes["/" + "/".join(segs)].add(f)
        for t in self.tests:
            src = self.text(t)
            for prefix, mods in routes.items():
                if re.search(re.escape(prefix) + r"(?![A-Za-z0-9_-])", src):
                    self.edges[t] |= mods

    def _add_shell_edges(self) -> None:
        """UI tests load the shell (index.html, app.js, app.css) and the page scripts of the routes they open; projection browser tests load projection.html"""
        static = "app/static/"
        shell = [f for f in (static + "index.html", static + "app.js", static + "app.css") if f in self.files]
        proj_shell = [f for f in self.files if f.startswith(static + "projection") or f.startswith(static + "js/projection/shell") or f.startswith(static + "js/projection/lib/")]
        ui_tests = [t for t in self.tests if t.startswith("tests/ui/")]
        for t in ui_tests:
            self.edges[t].update(shell)
        for t in self.tests:
            if t.startswith("tests/projection/") and self.is_browser_test(t):
                self.edges[t].update(proj_shell)
        appjs = static + "app.js"
        if appjs not in self.files:
            return
        src = self.text(appjs)
        imports: dict[str, str] = {}
        for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*'(\./js/pages/[^']+)'", src):
            p = os.path.normpath(os.path.join(static, m.group(2))).replace(os.sep, "/")
            for name in m.group(1).split(","):
                imports[name.strip().split(" as ")[-1].strip()] = p
        helper: dict[str, set[str]] = {}
        for m in re.finditer(r"const\s+(\w+)\s*=\s*\(ctx\)\s*=>\s*(.*)", src):
            helper[m.group(1)] = {imports[n] for n in re.findall(r"\b(\w+)\(ctx\)", m.group(2)) if n in imports}
        for m in re.finditer(r"\{\s*re:\s*/\^([^,]+?),\s*page:\s*(\w+)", src):
            rx, fn = m.group(1), m.group(2)
            pages = helper.get(fn) or ({imports[fn]} if fn in imports else set())
            segs: list[str] = []
            for s in [x for x in re.sub(r"\\/", "/", rx).split("/") if x != ""]:
                if re.fullmatch(r"[A-Za-z0-9_-]+", s):
                    segs.append(s)
                else:
                    break
            prefix = "/" + "/".join(segs) if segs else "/"
            for t in ui_tests:
                if prefix == "/":
                    if t != "tests/ui/test_ui.py":
                        continue
                elif not re.search(r"""["'`(]""" + re.escape(prefix) + r"(?![A-Za-z0-9_-])", self.text(t)):
                    continue
                self.edges[t] |= pages

    # ---- hubs and reach ----
    def hubs(self) -> set[str]:
        if self._hubs is None:
            hubs = {h for h in HUB_FILES if h in self.files}
            total = max(1, len(self.tests))
            direct: dict[str, int] = defaultdict(int)
            for t in self.tests:
                for d in self.edges.get(t, ()):
                    if not self.is_test(d) and not d.endswith("conftest.py"):
                        direct[d] += 1
            hubs |= {f for f, c in direct.items() if c / total > HUB_FRACTION}
            self._hubs = hubs
        return self._hubs

    def dependants(self, target: str) -> set[str]:
        """test files that reach `target` along the dependency edges, never walking through a hub other than the target itself"""
        hubs = self.hubs() | WALK_BARRIERS
        seen = {target}
        todo = [target]
        tests: set[str] = set()
        while todo:
            cur = todo.pop()
            for p in self.rev.get(cur, ()):
                if p in seen:
                    continue
                seen.add(p)
                if self.is_test(p):
                    tests.add(p)
                elif p not in hubs:
                    todo.append(p)
        return tests


def classify_full(f: str) -> str | None:
    if f in FULL_FILES or any(re.search(p, f) for p in FULL_PATTERNS):
        return "migration, conftest, harness, dependency list, deploy script or fixture"
    return None


def select(changed: list[str], repo: Repo) -> tuple[str, list[str], list[str], list[str]]:
    """-> (mode 'full'|'subset', test files, smoke node ids, reason lines)"""
    why: list[str] = []
    chosen: set[str] = set()
    hubs = repo.hubs()
    static = "app/static/"

    def full(f: str, rule: str):
        why.append(f"{f}: {rule} -> FULL suite")
        return "full", [], [], why

    def add(f: str, rule: str, tests) -> None:
        tests = {t for t in tests if t in repo.files}
        chosen.update(tests)
        shown = ", ".join(sorted(t.replace("tests/", "") for t in tests)[:5]) + (" ..." if len(tests) > 5 else "")
        why.append(f"{f}: {rule} -> {len(tests)} file(s)" + (f": {shown}" if tests else ""))

    for f in changed:
        exists = (repo.root / f).exists()
        r = classify_full(f)
        if r:
            return full(f, r)
        if f in hubs:
            return full(f, "hub module (imported by more than 30% of the test files, or named in HUB_FILES)")
        if f.startswith(NO_TEST_PREFIXES) and not f.startswith("docs/"):
            add(f, "nothing tests this", [])
            continue
        if f in ("scripts/select_tests.py", "tests/test_select_tests.py"):
            add(f, "the selector itself: its unit tests", {"tests/test_select_tests.py"})
            continue
        if f.startswith("docs/") or f.endswith(".md"):
            add(f, "documentation: the smoke set plus any test that reads it", repo.dependants(f))
            continue
        if f in repo.mod_of:
            if repo.is_test(f):
                add(f, "changed test file", [f] if exists else [])
                continue
            deps = repo.dependants(f)
            if not deps and exists:
                if f.startswith("scripts/"):
                    add(f, "a script no test runs", [])
                    continue
                return full(f, "no test file reaches this Python module; the selector cannot place it")
            add(f, "python module: tests that import it (not through a hub), request its routes, or read it", deps)
            continue
        if f.startswith(static):
            deps = repo.dependants(f)
            add(f, "static file: tests that load it (JS imports, page routes, script tags, python mentions)", deps)
            if f.startswith(static + "vendor/") or f.endswith(".css") or f.endswith("index.html"):
                browser = [t for t in repo.tests if t.startswith("tests/ui/")] if not f.startswith(static + "projection") and "/projection/" not in f else \
                          [t for t in repo.tests if t.startswith("tests/projection/") and repo.is_browser_test(t)]
                add(f, "shared stylesheet / vendor file / shell page: every browser test of that UI", browser)
            if f.endswith(".js") or f.endswith(".css"):
                add(f, "class names used by scripts and styles", ["tests/test_static_classnames.py"])
            continue
        if f.startswith("tests/"):
            deps = repo.dependants(f)
            if not deps and exists:
                return full(f, "test support file that no test reaches")
            add(f, "test support file: tests that use it", deps)
            continue
        refs = {t for t in repo.tests if os.path.basename(f) in repo.text(t)} | repo.dependants(f)
        if refs:
            add(f, "data file mentioned by tests or code", refs)
            continue
        return full(f, "unknown file the selector cannot place")
    nodes = [n for n in SMOKE_NODES if n.split("::")[0] not in chosen and n.split("::")[0] in repo.files and re.search(r"^def " + re.escape(n.split("::")[1]) + r"\(", repo.text(n.split("::")[0]), re.M)]
    gone = [n for n in SMOKE_NODES if n.split("::")[0] not in repo.files or not re.search(r"^def " + re.escape(n.split("::")[1]) + r"\(", repo.text(n.split("::")[0]), re.M)]
    if gone:
        why.append("note: " + ", ".join(gone) + " no longer exist and were dropped from the smoke set")
    smoke_files = [t for t in SMOKE_FILES if t in repo.files]
    chosen.update(smoke_files)
    why.append(f"smoke set always: {len(smoke_files)} file(s), {len(nodes)} node(s)")
    return "subset", sorted(chosen), nodes, why


def git(root: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout


def changed_files(base: str, root: Path) -> list[str]:
    mb = git(root, "merge-base", base, "HEAD").strip()
    out = set(git(root, "diff", "--name-only", mb).split()) | set(git(root, "ls-files", "-o", "--exclude-standard").split())
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/build/v1")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    changed = a.files if a.files is not None else changed_files(a.base, root)
    repo = Repo(root)
    mode, tests, nodes, why = select(changed, repo)
    print(f"[select_tests] {len(changed)} changed file(s)", file=sys.stderr)
    for line in why:
        print(f"[select_tests] {line}", file=sys.stderr)
    if a.json:
        print(json.dumps({"mode": mode, "tests": tests, "nodes": nodes, "reasons": why}))
    elif mode == "full":
        print(FULL)
    else:
        print(f"[select_tests] {len(tests)} test file(s) + {len(nodes)} smoke node(s)", file=sys.stderr)
        for t in tests:
            print(t)
        for n in nodes:
            print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
