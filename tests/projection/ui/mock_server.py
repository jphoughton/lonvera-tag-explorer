#!/usr/bin/env python3
"""Mock of the /api/projection API and page routes, for developing and testing the projection UI shell.

Serves app/static plus canned-but-consistent JSON: a tiny model (ad spend x CPA x AOV -> new-customer revenue -> NOI) so
edits made in the grid change the calculated rows. No auth (Josh's decision: projection follows the app's auth setting);
`/__mock/app_auth` switches on a simulated app login so 401 handling can be tested.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/ui/mock_server.py [port]      # default 5191

Test hooks (mock only): GET /__mock/state, POST /__mock/reset, POST /__mock/built {keys}, POST /__mock/degraded {modules, table_503}, POST /__mock/bump {scenario}, POST /__mock/app_auth {on}, POST /__mock/rate_limit {n},
POST /__mock/fail_next {n}.
"""
import copy
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request, send_from_directory

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "app" / "static"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
LOCK = threading.Lock()

N = 60
ACTUAL_THROUGH = 21                      # Oct 2026
MONTHS = [f"{2025 + i // 12}-{i % 12 + 1:02d}-01" for i in range(N)]
MONTH_LABEL = {m: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m[5:7]) - 1] + " " + m[:4] for m in MONTHS}
LINES = ("spend", "nc_cpa", "nc_aov", "amazon_rev")

MODULES = [
    {"key": "cohorts", "label": "Cohorts", "group": "P1", "built": True, "ui": "_mockpage.js"},
    {"key": "drivers", "label": "Drivers", "group": "P1", "built": True, "ui": "_mockpage.js"},
    {"key": "pnl", "label": "P&L", "group": "P1", "built": False, "ui": "pnl.js"},
    {"key": "inventory_demand", "label": "Inventory demand", "group": "P2", "built": False, "ui": "inventory_demand.js"},
    {"key": "inventory_supply", "label": "Inventory supply", "group": "P2", "built": False, "ui": "inventory_supply.js"},
    {"key": "financing", "label": "Financing", "group": "P2", "built": False, "ui": "financing.js"},
    {"key": "pacing", "label": "Pacing", "group": "P3", "built": False, "ui": "pacing.js"},
]


def defaults():
    return {
        "spend": [round(900000 - i * 14000 + (i % 3) * 22000) if i <= ACTUAL_THROUGH else 350000 for i in range(N)],
        "nc_cpa": [78 + (i % 5) * 4 if i <= ACTUAL_THROUGH else 95 for i in range(N)],
        "nc_aov": [66 + (i % 4) * 2 if i <= ACTUAL_THROUGH else 69 for i in range(N)],
        "amazon_rev": [150000 + i * 1200 if i <= ACTUAL_THROUGH else 175000 for i in range(N)],
    }


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fresh_state():
    inputs = defaults()
    s = {"slug": "current", "name": "Current plan", "base_slug": None, "version": 1, "is_default": True, "updated_at": now(), "inputs": inputs, "history": []}
    s["history"].append({"version": 1, "at": s["updated_at"], "action": "seed", "summary": "Seeded from the sheet", "inputs": copy.deepcopy(inputs)})
    return {"scenarios": {"current": s}, "puts": [], "app_auth": False, "rate_limit": 0, "fail_next": 0, "audit": [], "audit_id": 0, "degraded": [], "table_503": False, "archived": {}}


STATE = fresh_state()


# ------------------------------------------------------------------------------------------------- model
def compute(inp):
    orders = [inp["spend"][i] / inp["nc_cpa"][i] if inp["nc_cpa"][i] else 0 for i in range(N)]
    nc_rev = [orders[i] * inp["nc_aov"][i] for i in range(N)]
    repeat = []
    for i in range(N):
        repeat.append(sum(nc_rev[i - k] * 0.045 * 0.93 ** (k - 1) for k in range(1, 13) if i - k >= 0))
    dtc = [nc_rev[i] + repeat[i] for i in range(N)]
    total = [dtc[i] + inp["amazon_rev"][i] for i in range(N)]
    cogs = [t * 0.27 for t in total]
    fees = [inp["amazon_rev"][i] * 0.31 + dtc[i] * 0.09 for i in range(N)]
    noi = [total[i] - cogs[i] - fees[i] - inp["spend"][i] - 215000 for i in range(N)]
    return {"orders": orders, "nc_rev": nc_rev, "repeat": repeat, "dtc": dtc, "total": total, "noi": noi}


def table_payload(scn):
    inp = scn["inputs"]
    c = compute(inp)

    def row(key, label, kind, fmt, values, **extra):
        return {"key": key, "label": label, "kind": kind, "fmt": fmt, "values": values, "input": False, **extra}

    return {
        "months": MONTHS,
        "status": ["actual" if i <= ACTUAL_THROUGH else "projected" for i in range(N)],
        "rows": [
            row("h_rev", "Revenue", "header", "text", []),
            row("spend", "Ad spend", "input", "usd0", inp["spend"], input=True, note="Typed monthly plan"),
            row("nc_cpa", "New-customer CPA", "input", "usd", inp["nc_cpa"], input=True, agg="avg"),
            row("nc_aov", "New-customer AOV", "input", "usd", inp["nc_aov"], input=True, agg="avg"),
            row("nc_orders", "New-customer orders", "calc", "num", c["orders"]),
            row("nc_rev", "New-customer revenue", "calc", "usd0", c["nc_rev"]),
            row("repeat_rev", "Repeat revenue", "calc", "usd0", c["repeat"]),
            row("dtc_rev", "DTC revenue", "subtotal", "usd0", c["dtc"]),
            row("amazon_rev", "Amazon revenue", "input", "usd0", inp["amazon_rev"], input=True),
            row("total_rev", "Total revenue", "total", "usd0", c["total"]),
            row("h_cost", "Costs", "header", "text", []),
            row("noi", "Net operating income", "total", "usd0", c["noi"]),
            row("noi_margin", "NOI margin", "ratio", "pct", [c["noi"][i] / c["total"][i] if c["total"][i] else None for i in range(N)]),
        ],
        "captions": ["Source: mock model (spend x CPA x AOV)."],
    }


def fy(values, year):
    return sum(v for m, v in zip(MONTHS, values) if m.startswith(str(year)))


# ------------------------------------------------------------------------------------------------- guards
def err(message, status):
    return make_response(jsonify(error=message), status)


@app.before_request
def guard():
    p = request.path
    if p.startswith("/__mock"):
        return None
    if not p.startswith("/api/projection/"):
        return None
    with LOCK:
        if STATE["app_auth"] and request.cookies.get("mock_app") != "1":
            return err("unauthorized", 401)
        if request.method in ("PUT", "POST", "PATCH", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != request.host:
                return err("bad_origin", 403)
            if not (request.mimetype == "application/json"):
                return err("json_required", 415)
            if STATE["rate_limit"] > 0:
                STATE["rate_limit"] -= 1
                return err("rate_limited", 429)
    return None


@app.after_request
def headers(resp):
    resp.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    if request.path.startswith(("/projection", "/api/projection")):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ------------------------------------------------------------------------------------------------- pages
@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.get("/projection")
@app.get("/projection/<path:_p>")
def projection_page(_p=None):
    return send_from_directory(STATIC, "projection.html", max_age=0)


@app.get("/static/js/projection/_mockpage.js")
def mockpage():
    return send_from_directory(FIXTURES, "_mockpage.js", max_age=0, mimetype="text/javascript")


LOGIN_HTML = """<!doctype html><title>Sign in</title><body><h1 id="login-title">Marketing metrics sign in</h1>
<button id="login-go">Sign in</button>
<script>document.getElementById('login-go').onclick = async () => {
  await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  const next = new URLSearchParams(location.search).get('next') || '/';
  location.assign(next);
};</script>"""


@app.get("/login")
def login_page():
    return Response(LOGIN_HTML, mimetype="text/html")


@app.get("/")
def home():
    return Response("<!doctype html><title>Marketing metrics</title><h1>Marketing metrics</h1>", mimetype="text/html")


@app.post("/api/login")
def app_login():
    resp = make_response("", 204)
    resp.set_cookie("mock_app", "1")
    return resp


# ------------------------------------------------------------------------------------------------- API
def public(s):
    return {**{k: s[k] for k in ("slug", "name", "base_slug", "version", "is_default", "updated_at")}, "frozen": s.get("frozen", True)}


def get_scn(slug):
    return STATE["scenarios"].get(slug)


def slugify(name):
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "scenario"


def audit_row(scn, at, action, line_key=None, month=None, old=None, new=None):
    STATE["audit_id"] += 1
    STATE["audit"].append({"id": STATE["audit_id"], "at": at, "actor": "projection", "scenario_slug": scn["slug"], "action": action,
                           "line_key": line_key, "month": month, "old": old, "new": new, "note": None, "version": scn["version"]})


def bump(scn, action, summary, rows=()):
    scn["version"] += 1
    scn["updated_at"] = now()
    scn["history"].append({"version": scn["version"], "at": scn["updated_at"], "action": action, "summary": summary, "inputs": copy.deepcopy(scn["inputs"])})
    scn["history"] = scn["history"][-200:]
    for r in rows or [dict(action="scenario." + action)]:
        audit_row(scn, scn["updated_at"], **r)


@app.get("/api/projection/modules")
def modules():
    built = set(STATE.get("built", []))
    return jsonify(modules=[dict(m, built=True, ui="_mockpage.js") if m["key"] in built else m for m in MODULES])


@app.get("/api/projection/scenarios")
def scenarios_list():
    return jsonify(scenarios=[public(s) for s in STATE["scenarios"].values()])


def create_scenario(name, clone_from):
    name = (name or "").strip()
    if not name:
        return err("name is required", 400)
    with LOCK:
        if any(x["name"].lower() == name.lower() for x in STATE["scenarios"].values()):
            return err("a scenario with that name already exists", 409)
        base = get_scn(clone_from) if clone_from else get_scn("current")
        if clone_from and not base:
            return err("not_found", 404)
        slug, i = slugify(name), 1
        while slug in STATE["scenarios"]:
            i += 1
            slug = f"{slugify(name)}-{i}"
        s = {"slug": slug, "name": name, "base_slug": base["slug"] if clone_from else None, "version": 1, "is_default": False, "updated_at": now(),
             "inputs": copy.deepcopy(base["inputs"]), "history": []}
        s["history"].append({"version": 1, "at": s["updated_at"], "action": "create", "summary": "Created", "inputs": copy.deepcopy(s["inputs"])})
        STATE["scenarios"][slug] = s
        audit_row(s, s["updated_at"], "scenario.create", new={"name": name})
        return make_response(jsonify(scenario=public(s)), 201)


@app.post("/api/projection/scenarios")
def scenarios_create():
    b = request.get_json(silent=True) or {}
    return create_scenario(b.get("name"), b.get("clone_from"))


@app.post("/api/projection/scenarios/<slug>/clone")
def scenarios_clone(slug):
    b = request.get_json(silent=True) or {}
    return create_scenario(b.get("name"), slug)


@app.patch("/api/projection/scenarios/<slug>")
def scenarios_patch(slug):
    b = request.get_json(silent=True) or {}
    with LOCK:
        s = get_scn(slug)
        if not s:
            return err("not_found", 404)
        name = (b.get("name") or "").strip()
        if not name:
            return err("name is required", 400)
        if any(x["name"].lower() == name.lower() and x["slug"] != slug for x in STATE["scenarios"].values()):
            return err("a scenario with that name already exists", 409)
        old_name, s["name"] = s["name"], name
        bump(s, "update", f"Renamed to {name}", [dict(action="scenario.rename", old={"name": old_name}, new={"name": name})])
        return jsonify(scenario=public(s))


@app.delete("/api/projection/scenarios/<slug>")
def scenarios_delete(slug):
    b = request.get_json(silent=True) or {}
    with LOCK:
        s = get_scn(slug)
        if not s:
            return err("not_found", 404)
        if s["is_default"] or slug == "current":
            return err("the current scenario cannot be deleted", 409)
        if b.get("confirm") != slug:
            return err(f'send {{"confirm": "{slug}"}} to delete this scenario', 400)
        STATE["archived"][slug] = STATE["scenarios"].pop(slug)             # archived for 30 days, not removed
        STATE["archived"][slug]["archived_at"] = now()
        return jsonify(deleted=slug)


@app.get("/api/projection/scenarios/archived")
def scenarios_archived():
    with LOCK:
        return jsonify(archived=[{**public(s), "archived_at": s["archived_at"], "days_left": 30} for s in STATE["archived"].values()])


@app.post("/api/projection/scenarios/<slug>/undelete")
def scenarios_undelete(slug):
    with LOCK:
        s = STATE["archived"].pop(slug, None)
        if not s:
            return err("no deleted scenario with that slug", 404)
        STATE["scenarios"][slug] = s
        return jsonify(scenario=public(s))


@app.post("/api/projection/scenarios/<slug>/detach")
def scenarios_detach(slug):
    b = request.get_json(silent=True) or {}
    with LOCK:
        s = get_scn(slug)
        if not s:
            return err("not_found", 404)
        ev = b.get("expected_version")
        if ev is not None and ev != s["version"]:
            return err(f"version conflict: the scenario is at version {s['version']}", 409)
        s["frozen"] = True
        bump(s, "update", "Detached from its base", [dict(action="scenario.detach")])
        return jsonify(scenario=public(s))


@app.post("/api/projection/scenarios/<slug>/restore")
def scenarios_restore(slug):
    b = request.get_json(silent=True) or {}
    with LOCK:
        s = get_scn(slug)
        if not s:
            return err("not_found", 404)
        ev = b.get("expected_version")
        if ev is not None and ev != s["version"]:
            return err(f"version conflict: the scenario is at version {s['version']}", 409)
        snap = next((h for h in s["history"] if h["version"] == b.get("version")), None)
        if not snap:
            return err("version_not_found", 404)
        s["inputs"] = copy.deepcopy(snap["inputs"])
        bump(s, "restore", f"Restored version {snap['version']}", [dict(action="scenario.restore", new={"version": snap["version"]})])
        return jsonify(scenario=public(s))


@app.put("/api/projection/series")
def series_put():
    b = request.get_json(silent=True) or {}
    items = b.get("items")
    with LOCK:
        STATE["puts"].append(copy.deepcopy(b))
        if STATE["fail_next"] > 0:
            STATE["fail_next"] -= 1
            return err("Series write failed", 500)
        s = get_scn(b.get("scenario"))
        if not s:
            return err("not_found", 404)
        ev = b.get("expected_version")
        if ev is not None and ev != s["version"]:
            return err(f"version conflict: the scenario is at version {s['version']}", 409)
        if not isinstance(items, list) or not items:
            return err("items must be a non-empty list", 400)
        if len(items) > 500:
            return err("at most 500 items per request", 400)
        seen = set()
        for n, it in enumerate(items):
            extra = set(it) - {"line_key", "month", "value", "kind", "note"}
            if extra:
                return err(f"items[{n}]: unknown field {sorted(extra)[0]!r}", 400)
            if it.get("line_key") not in LINES:
                return err(f"items[{n}]: unknown line_key {it.get('line_key')}", 400)
            if it.get("month") not in MONTHS:
                return err(f"items[{n}]: bad month {it.get('month')}", 400)
            if "value" not in it or (it["value"] is not None and (isinstance(it["value"], bool) or not isinstance(it["value"], (int, float)))):
                return err(f"items[{n}]: value must be a number or null", 400)
            if it["line_key"] == "spend" and isinstance(it.get("value"), (int, float)) and not (0 <= it["value"] <= 10_000_000):
                return err("value must be between 0 and 10,000,000", 400)
            if it["line_key"] == "amazon_rev" and isinstance(it.get("value"), (int, float)) and it["value"] > 5_000_000:
                return err("value must be at most 5,000,000", 400)          # a limit the page cannot know ahead of time
            if (it["line_key"], it["month"]) in seen:
                return err(f"items[{n}]: duplicate cell", 400)
            seen.add((it["line_key"], it["month"]))
        rows = []
        for it in items:
            i = MONTHS.index(it["month"])
            old = s["inputs"][it["line_key"]][i]
            v = it.get("value")
            s["inputs"][it["line_key"]][i] = v if v is not None else defaults()[it["line_key"]][i]
            rows.append(dict(action="series.set", line_key=it["line_key"], month=it["month"], old={"value": old, "kind": "input"}, new={"value": s["inputs"][it["line_key"]][i], "kind": "input"}))
        bump(s, "series", f"Edited {len(items)} cells", rows)
        return jsonify(version=s["version"], changed=len(items))


@app.get("/api/projection/run")
def run():
    s = get_scn(request.args.get("scenario"))
    if not s:
        return err("not_found", 404)
    c = compute(s["inputs"])
    fy_rows = {str(y): {"revenue": fy(c["total"], y), "noi": fy(c["noi"], y)} for y in range(2025, 2030)}
    return jsonify(scenario=s["slug"], version=s["version"], actual_through=MONTHS[ACTUAL_THROUGH], modules={}, degraded=STATE.get("degraded", []),
                   books_through=MONTHS[ACTUAL_THROUGH], feeds_through="2026-11-02",
                   headline={"fy": fy_rows, "ev": {"3x": fy(c["noi"], 2027) * 3, "8x": fy(c["noi"], 2027) * 8}, "lifetime_multiple": 1.86,
                             "lowest_ending_cash": {"month": "2027-03-01", "value": 154000.0}})


@app.get("/api/projection/scenarios/<slug>/history")
def scenario_history(slug):
    s = get_scn(slug)
    if not s:
        return err("not_found", 404)
    out = [{"version": h["version"], "at": h["at"], "action": h["action"]} for h in s["history"]]
    return jsonify(history=sorted(out, key=lambda x: -x["version"])[: int(request.args.get("limit", 50))])


@app.get("/api/projection/audit")
def audit():
    slug = request.args.get("scenario")
    rows = [r for r in STATE["audit"] if not slug or r["scenario_slug"] == slug]
    return jsonify(audit=sorted(rows, key=lambda r: -r["id"])[: int(request.args.get("limit", 100))])


@app.get("/api/projection/<module>/table")
def module_table(module):
    s = get_scn(request.args.get("scenario"))
    if not s:
        return err("not_found", 404)
    if module not in ("cohorts", "drivers", "pacing"):
        return err("not_found", 404)
    if STATE.get("table_503"):
        return make_response(jsonify(error="upstream_degraded", modules=[{"module": "pnl", "error": "The P&L refresh failed at 21:30"}]), 503)
    return jsonify(table_payload(s))


# ------------------------------------------------------------------------------------------------- test hooks
@app.get("/__mock/state")
def hook_state():
    with LOCK:
        return jsonify(puts=STATE["puts"], scenarios=[public(s) for s in STATE["scenarios"].values()])


@app.post("/__mock/built")
def hook_built():
    """Mark modules as built (they render the mock page). Body {keys:["pacing"]}. Cleared by /__mock/reset."""
    with LOCK:
        STATE["built"] = list((request.get_json(silent=True) or {}).get("keys", []))
    return jsonify(ok=True)


@app.post("/__mock/degraded")
def hook_degraded():
    """Body {modules:[{module, error}], table_503: bool}: what /run reports as degraded, and whether module tables answer 503 upstream_degraded."""
    b = request.get_json(silent=True) or {}
    with LOCK:
        STATE["degraded"] = list(b.get("modules", []))
        STATE["table_503"] = bool(b.get("table_503"))
    return jsonify(ok=True)


@app.post("/__mock/bump")
def hook_bump():
    """Simulate a change from another tab: bump a scenario's version without telling this client. Body {scenario}."""
    b = request.get_json(silent=True) or {}
    with LOCK:
        s = get_scn(b.get("scenario"))
        if not s:
            return err("not_found", 404)
        bump(s, "series", "Edited 1 cell (another tab)", [dict(action="series.set", line_key="spend", month=MONTHS[0], old={"value": 1}, new={"value": 2})])
        return jsonify(version=s["version"])


@app.post("/__mock/legacy_clone")
def hook_legacy_clone():
    """A clone made before copies were frozen: it still reads its base. Body {name, base}."""
    b = request.get_json(silent=True) or {}
    res = create_scenario(b.get("name"), b.get("base") or "current")
    with LOCK:
        for s in STATE["scenarios"].values():
            if s["name"] == b.get("name"):
                s["frozen"] = False
    return res


@app.post("/__mock/reset")
def hook_reset():
    global STATE
    with LOCK:
        STATE = fresh_state()
    return jsonify(ok=True)


@app.post("/__mock/app_auth")
def hook_auth():
    with LOCK:
        STATE["app_auth"] = bool((request.get_json(silent=True) or {}).get("on"))
    return jsonify(ok=True)


@app.post("/__mock/rate_limit")
def hook_rate():
    with LOCK:
        STATE["rate_limit"] = int((request.get_json(silent=True) or {}).get("n", 1))
    return jsonify(ok=True)


@app.post("/__mock/fail_next")
def hook_fail():
    with LOCK:
        STATE["fail_next"] = int((request.get_json(silent=True) or {}).get("n", 1))
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5191
    sys.path.insert(0, str(ROOT))
    from tests._servers import make_server          # keep-alive server: werkzeug's closes every connection, which exhausts the ephemeral ports in a parallel run
    make_server(os.environ.get("MOCK_HOST", "127.0.0.1"), port, app).serve_forever()
