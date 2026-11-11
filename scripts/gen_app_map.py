#!/usr/bin/env python3
"""Generate the app map from the code itself: docs/app_map.json (machine readable, read by the brain's app_map tool) and docs/APP_MAP.md.

    python scripts/gen_app_map.py --write     regenerate both files
    python scripts/gen_app_map.py --check     exit 1 when the committed files are stale (tests/brain/test_app_map.py does the same)

Sources: the Flask url_map (routes, methods, whether the server-side login check is skipped), app/static/app.js (pages and their JS files), scripts/deploy/apply_*.py and run_*.sh
(Railway services, schedules, start commands, the order of the ETL jobs) and etl/*.py (the list of runnable jobs). No database is contacted and nothing is called over the network.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JSON_PATH = ROOT / "docs" / "app_map.json"
MD_PATH = ROOT / "docs" / "APP_MAP.md"
DEPLOY = ROOT / "scripts" / "deploy"


# ----------------------------------------------------------------------------------------------------------------------------------- routes
def _app():
    os.environ.setdefault("DEV_INSECURE_COOKIES", "1")
    from app import create_app
    return create_app({"DATABASE_URL": "postgresql://map:map@127.0.0.1:1/map", "APP_USERS": {"map": "x"}, "SECRET_KEY": "app-map", "DB_POOL_MIN": 0, "DEV_INSECURE_COOKIES": True,
                       "AUTH_DISABLED": False, "RESPONSE_CACHE_MB": 0})


def routes() -> list[dict]:
    from app import auth
    app = _app()
    try:
        out = []
        for r in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods))):
            if r.endpoint == "static":
                continue
            methods = sorted(set(r.methods) - {"HEAD", "OPTIONS"})
            sample = re.sub(r"<(?:[^:>]+:)?[^>]+>", "A" * 30, r.rule)
            if r.rule == "/mcp":
                gate, exempt = "own check: signed-in session or Bearer key (never vendor tokens); 403 when AUTH_DISABLED", False
            elif not r.rule.startswith(auth.PROTECTED_PREFIXES):
                gate, exempt = "none (HTML page or health check; its data calls are protected)", True
            elif r.rule in auth.PUBLIC_API or auth.PUBLIC_API_RE.fullmatch(sample):
                gate, exempt = "none (login routes, or a vendor link: the unguessable link is the access)", True
            else:
                gate, exempt = "signed-in session or API key", False
            fn = app.view_functions.get(r.endpoint)
            doc = (fn.__doc__ or "").strip().splitlines()[0].strip() if fn is not None and fn.__doc__ else ""
            kind = "api" if r.rule.startswith("/api/") else "media" if r.rule.startswith("/media/") else "page" if r.endpoint.startswith("shell:") else "other"
            out.append({"rule": r.rule, "methods": methods, "kind": kind, "endpoint": r.endpoint, "auth": gate, "auth_exempt": exempt, "summary": doc})
        return out
    finally:
        pool = app.extensions.get("pool")
        if pool is not None:
            try:
                pool.close()
            except Exception:                                    # noqa: BLE001
                pass


# ----------------------------------------------------------------------------------------------------------------------------------- pages
def pages(route_list: list[dict]) -> list[dict]:
    js = (ROOT / "app" / "static" / "app.js").read_text()
    imports: dict[str, str] = {}
    for m in re.finditer(r"import \{([^}]*)\} from '\./js/pages/([A-Za-z_]+\.js)';", js):
        for name in m.group(1).split(","):
            imports[name.strip()] = "app/static/js/pages/" + m.group(2)
    fns: dict[str, str] = {}                                     # page id -> page function, from PAGE_FNS in app.js
    block = re.search(r"const PAGE_FNS = \{(.*?)\};", js, re.S)
    for m in re.finditer(r"""['"]?([\w-]+)['"]?\s*:\s*(\w+)""", block.group(1) if block else ""):
        fns[m.group(1)] = m.group(2)
    cfg = (ROOT / "app" / "static" / "js" / "pageconfig.js").read_text()      # URL patterns, public / bare flags: one table, js/pageconfig.js
    out = []
    for m in re.finditer(r"\{ id: '([\w-]+)', re: /(.+?)/,(.*)", cfg):
        pid, pat, rest = m.group(1), m.group(2), m.group(3)
        page = fns.get(pid, pid)
        out.append({"url_pattern": "/" + re.sub(r"\\/\?\$|\$|\^|\\|\(\[.*?\]\+\)|\(\[.*?\]\+\)", lambda x: "<token>" if x.group(0).startswith("(") else "", pat).strip("/?"),
                    "page_function": page, "js_file": imports.get(page) or "app/static/app.js (local function that picks a page by platform)", "public": "public: true" in rest, "bare": "bare: true" in rest})
    out.append({"url_pattern": "/projection[/<module>]", "page_function": "projection shell", "js_file": "app/static/js/projection/shell.js", "public": False, "bare": False})
    return sorted(out, key=lambda p: p["url_pattern"])


# ----------------------------------------------------------------------------------------------------------------------------------- services and jobs
def _py_assign(path: Path, name: str):
    tree = ast.parse(path.read_text())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            try:
                return ast.literal_eval(n.value)
            except ValueError:
                return None
    return None


def _modules_in(script: Path) -> list[str]:
    return re.findall(r"python -m (etl(?:\.[a-z_]+)+)", script.read_text())


def etl_jobs() -> list[dict]:
    jobs = []
    text = (DEPLOY / "run_etl.sh").read_text()
    order = 0
    for line in text.splitlines():
        m = re.match(r"\s*python -m (etl\.[a-z_]+)((?: --[a-z-]+)*)(.*)", line)
        if not m or m.group(1) == "etl.migrate":
            continue
        order += 1
        rest = m.group(3)
        jobs.append({"service": "etl", "order": order, "module": m.group(1), "args": m.group(2).strip(), "fatal": "||" not in rest or "non-fatal" not in rest and "echo" not in rest,
                     "note": "failure is logged and does not fail the run" if "echo" in rest else "failure fails the run (after the later steps ran)"})
    feeds = (DEPLOY / "run_feeds.sh").read_text()
    n = 0
    for mod, arg in re.findall(r"python -m (etl\.[a-z_.]+?)( (?:run|verify))?(?: \|\||\n)", feeds):
        if mod == "etl.migrate":
            continue
        n += 1
        jobs.append({"service": "feeds", "order": n, "module": mod, "args": arg.strip(), "fatal": True, "note": ""})
    for svc, script in (("live", "run_live.sh"), ("alerts", "run_alerts.sh"), ("backup", "run_backup.sh"), ("breakdowns", "run_breakdowns.sh"), ("launch worker (no service)", "run_launch_worker.sh")):
        for mod in dict.fromkeys(_modules_in(DEPLOY / script)):
            if mod != "etl.migrate":
                jobs.append({"service": svc, "order": 1, "module": mod, "args": "", "fatal": True, "note": ""})
    return jobs


def etl_modules() -> list[dict]:
    out = []
    for p in sorted((ROOT / "etl").glob("*.py")):
        if p.name == "__init__.py":
            continue
        try:
            doc = ast.get_docstring(ast.parse(p.read_text())) or ""
        except SyntaxError:
            doc = ""
        out.append({"module": f"etl.{p.stem}", "summary": doc.strip().splitlines()[0].strip() if doc.strip() else ""})
    return out


def services() -> list[dict]:
    web = {"name": "web", "kind": "web", "start": "scripts/deploy/start_web.sh (etl.migrate, then gunicorn 2 workers x 8 threads, 60 s timeout)", "schedule": None, "healthcheck": "/healthz",
           "source": "Dockerfile, railway.json, scripts/deploy/start_web.sh, scripts/deploy/apply_service_config.py"}
    etl = {"name": "etl", "kind": "cron", "start": re.search(r'"startCommand": "([^"]+)"', (DEPLOY / "apply_service_config.py").read_text()).group(1), "schedule": _py_assign(DEPLOY / "apply_service_config.py", "DEFAULT_CRON"),
           "healthcheck": None, "source": "scripts/deploy/apply_service_config.py"}
    out = [web, etl]
    for p in sorted(DEPLOY.glob("apply_*_service.py")):
        inst = _py_assign(p, "INSTANCE") or {}
        name = _py_assign(p, "SERVICE")
        out.append({"name": name, "kind": "cron", "start": inst.get("startCommand"), "schedule": _py_assign(p, "DEFAULT_CRON"), "healthcheck": None, "source": f"scripts/deploy/{p.name}"})
    out.append({"name": "live", "kind": "cron", "start": "scripts/deploy/run_live.sh (etl.live_today)", "schedule": None, "healthcheck": None,
                "source": "scripts/deploy/run_live.sh; no apply script in the repo, so the schedule is not verifiable here (project notes say hourly)"})
    out.append({"name": "breakdowns", "kind": "cron", "start": "scripts/deploy/run_breakdowns.sh (etl.meta_breakdowns --auto)", "schedule": None, "healthcheck": None,
                "source": "scripts/deploy/run_breakdowns.sh; no apply script in the repo, so the Railway service and its schedule (proposed: 0 */2 * * *) are not verifiable here"})
    out.append({"name": "launch worker", "kind": "none", "start": "scripts/deploy/run_launch_worker.sh (etl.launch_worker)", "schedule": None, "healthcheck": None,
                "source": "code exists, wired to no Railway service"})
    out.append({"name": "Postgres", "kind": "database", "start": None, "schedule": None, "healthcheck": None, "source": "docs/DEPLOY.md"})
    return sorted(out, key=lambda s: ({"web": 0, "etl": 1}.get(s["name"], 2), s["name"]))


def build() -> dict:
    r = routes()
    return {"generated_by": "scripts/gen_app_map.py", "services": services(), "etl_jobs": etl_jobs(), "etl_modules": etl_modules(), "pages": pages(r), "routes": r}


# ----------------------------------------------------------------------------------------------------------------------------------- output
def _cell(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def render_md(m: dict) -> str:
    L = ["# App map", "", "GENERATED by `python scripts/gen_app_map.py --write` from the code. Do not edit by hand; a test fails when it is stale. Machine-readable twin: `docs/app_map.json` (the brain's `app_map` tool reads it).", ""]
    L += ["## Services (Railway)", "", "| Service | Kind | Schedule (UTC) | Start | Source |", "|---|---|---|---|---|"]
    L += [f"| {_cell(s['name'])} | {s['kind']} | {_cell(s['schedule'])} | {_cell(s['start'])} | {_cell(s['source'])} |" for s in m["services"]]
    L += ["", "## ETL jobs in the order they run", "", "| Service | # | Module | Args | On failure |", "|---|---|---|---|---|"]
    L += [f"| {_cell(j['service'])} | {j['order']} | `{j['module']}` | {_cell(j['args'])} | {_cell(j['note'])} |" for j in m["etl_jobs"]]
    L += ["", "## Every runnable job module (`python -m etl.<name>`)", "", "| Module | What it does |", "|---|---|"]
    L += [f"| `{j['module']}` | {_cell(j['summary'])} |" for j in m["etl_modules"]]
    L += ["", "## Pages", "", "| URL | Page function | JS file | Login-free |", "|---|---|---|---|"]
    L += [f"| `{p['url_pattern']}` | {p['page_function']} | {_cell(p['js_file'])} | {'yes' if p['public'] else 'no'} |" for p in m["pages"]]
    L += ["", "## Routes", "", "Auth column: what the server checks. Page routes serve the same HTML shell; their data calls are the protected `/api/` routes.", "",
          "| Route | Methods | Kind | Auth | What it does |", "|---|---|---|---|---|"]
    L += [f"| `{r['rule']}` | {' '.join(r['methods'])} | {r['kind']} | {_cell(r['auth'])} | {_cell(r['summary'])} |" for r in m["routes"]]
    return "\n".join(L) + "\n"


def render_json(m: dict) -> str:
    return json.dumps(m, indent=1, ensure_ascii=False) + "\n"


def stale(m: dict | None = None) -> list[str]:
    m = m or build()
    out = []
    for path, text in ((JSON_PATH, render_json(m)), (MD_PATH, render_md(m))):
        if not path.exists() or path.read_text() != text:
            out.append(f"docs/{path.name} is stale: run `python scripts/gen_app_map.py --write`")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    m = build()
    if a.write:
        JSON_PATH.write_text(render_json(m))
        MD_PATH.write_text(render_md(m))
        print(f"wrote {JSON_PATH.relative_to(ROOT)} and {MD_PATH.relative_to(ROOT)} ({len(m['routes'])} routes, {len(m['services'])} services, {len(m['etl_jobs'])} job steps)")
        return 0
    problems = stale(m)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
