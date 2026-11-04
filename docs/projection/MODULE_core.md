# Projection core (foundation)

What it replaced: nothing in the sheet directly. It is the shared plumbing every module sits on: scenarios, monthly series, loaded actuals, audit and undo, the module registry, the engine that runs the modules in order, and the reconcile harness.

Auth: projection has none of its own. `/api/projection/*` sits under the app's `/api/*` guard (`app/auth.py`): login on gives 401 without a session, the app's `AUTH_DISABLED` makes it open. Nothing in `app/projection/` reads a password or issues a cookie.

## Files

```
migrations/100_proj_core.sql                  tables below + seed 'current'
app/projection/__init__.py                    register(app): guard rails, core API, module blueprints, /projection pages
app/projection/interfaces.py                  Month/Series, MONTHS, Scenario, result dataclasses, Row/Table (grid payload), Module
app/projection/rwdb.py                        read-write pool (get_rw)
app/projection/scenarios.py                   CRUD, clone, params merge, history snapshots, restore
app/projection/series.py                      get_series / get / apply_items / actuals helpers
app/projection/audit.py                       proj_audit writer and reader, current_actor()
app/projection/limits.py                      in-memory per-IP rate limits
app/projection/registry.py                    module discovery + blueprint mounting
app/projection/engine.py                      run(): modules in order, stubs for unbuilt ones
app/projection/stubs.py                       fixture stubs read from the sheet snapshot
app/projection/api.py                         /api/projection blueprint + install_guards()
scripts/projection/reconcile.py               engine vs sheet, per cell
tests/projection/core/                        160 tests
```

`app/__init__.py` has two changed lines: `from . import api, auth, db, projection` and `projection.register(app)` right after `app.register_blueprint(api.bp)`.

## Tables (migration 100)

| table | purpose |
|---|---|
| `proj_scenarios(id, slug unique, name, base_slug, is_default, params jsonb, version, created_at, updated_at)` | one default row `current` ("Current plan"); one default only (partial unique index); `version` +1 on every change |
| `proj_series(scenario_id, line_key, month, value numeric(18,4) not null, kind 'input'/'override', note, updated_at)` | typed monthly cells; month must be the 1st; cascades with the scenario |
| `proj_actuals(line_key, month, value, source, loaded_at)` | scenario-independent loaded actuals (aggregates only); months may lie before 2025 (cohort history) |
| `proj_audit(id, at, actor, scenario_slug, action, line_key, month, old, new, note, version)` | one row per changed cell / scenario change; `version` = scenario version the change produced |
| `proj_scenario_history(scenario_id, version, snapshot jsonb, at, action)` | params + every series cell at each version, newest 200 per scenario |

`kind`: `input` replaces a typed constant of the sheet (spend plan, CPA, AOV, Amazon revenue...); `override` replaces a computed value for one month.

## Endpoints (all JSON, all under `/api/projection`)

| method + path | body / query | returns |
|---|---|---|
| `GET /modules` | | `{modules:[{key,label,group,built,ui}]}` for the 7 catalog modules in nav order; `built` = package imports and exports MODULE |
| `GET /scenarios` | | `{scenarios:[{id,slug,name,base_slug,is_default,params,effective_params,version,created_at,updated_at}]}` |
| `POST /scenarios` | `{name, clone_from?}` | 201 `{scenario}`; a copy of `clone_from` (default: the default scenario): params and own series copied, `base_slug` = source |
| `POST /scenarios/<slug>/clone` | `{name}` | same, alias of the above |
| `PATCH /scenarios/<slug>` | `{name?, params?, expected_version?}` | `{scenario}`; params are a merge patch per namespace: objects merge, `null` removes a key, lists replace |
| `DELETE /scenarios/<slug>` | `{confirm:"<slug>"}` | `{deleted}`; `current`/default is 409, missing or wrong confirm is 400 |
| `POST /scenarios/<slug>/restore` | `{version, expected_version?}` | `{scenario}`; params and series go back to that version; itself a new version, audited, undoable |
| `GET /scenarios/<slug>/history?limit=` | | `{history:[{version,at,action}]}` newest first |
| `GET /series?scenario=&keys=a,b` or `&prefix=drivers.` `&from=&to=&detail=1` | | `{scenario, version, series:{key:{iso:value}}, detail?:{key:{iso:{kind,note,origin}}}}`; scenario -> base -> base's base fallback per cell; `origin` = the scenario that holds the cell |
| `PUT /series` | `{scenario, items:[{line_key,month,value\|null,kind,note}], expected_version?}` | `{version, changed}`; `null` deletes the cell (reverts to the base's value); max 500 items; unchanged cells are not written, audited or versioned |
| `GET /audit?scenario=&line_key=&limit=` | | `{audit:[...]}` newest first, limit <= 1000 |
| `GET /run?scenario=` | | `{scenario, version, actual_through, modules:{key:{built,stub,ms,error?}}, headline:{fy,ev,lifetime_multiple,lowest_ending_cash}}` |

Validation: `line_key` `^[a-z0-9_.]{1,120}$`; `month` ISO date, first of month, inside 2025-01..2029-12; `value` finite number below 1e13 (never a string); `kind` input/override; `note` <= 500 chars; duplicate (line_key, month) in one request, unknown item fields and non-object items are 400 naming `items[i]`. Errors are `{error}`.

Pages: `/projection` and `/projection/<module>` serve `app/static/projection.html` (the UI-shell agent's file; a one-line placeholder is served only if it is missing).

## Guard rails (no password)

One set of app-level hooks, installed by `api.install_guards(app)` from `projection.register(app)`; `app.before_request` is registered after `auth.init_auth`, so the app's guard runs first (anonymous requests get 401 before any projection check when the app's login is on).

- `_write_guard` (before_request, acts only on non-GET/HEAD/OPTIONS under `/api/projection/`, so every module blueprint is covered with no code in the module): Origin must match `request.host` when present (403 `origin_mismatch`; `Origin: null` is rejected); per-IP sliding window 120 writes/minute (429 `{error:"rate_limited"}` + `Retry-After`); paths with a segment starting `upload` also count against 20/hour; body must be `application/json` (415 `json_required`) except `multipart/form-data` on an `upload*` path; request cap set per request to 1 MB (8 MB for multipart; the app-wide 64 KB cap is overridden for projection only, 413 above). Config: `PROJ_RATE_MUTATIONS_PER_MIN`, `PROJ_RATE_UPLOADS_PER_HOUR` (app config or env). Limits are per process: with N gunicorn workers the effective ceiling is N times higher.
- after_request: `X-Robots-Tag: noindex, nofollow` on `/projection*` and `/api/projection*`; `/robots.txt` fallback (Disallow `/projection`, `/api/projection`) served only when the app has no `/robots.txt` route (it replaces a 404 only, never an existing route).
- Audit: every mutation writes `proj_audit` with old and new; actor = the app's signed-in user (`auth.current_user()` when the app exposes it, else a `user`/`u` session value), `anonymous` otherwise (`guest` from AUTH_DISABLED counts as anonymous), `system` outside a request.
- Undo: every version bump snapshots the scenario; the state before the first API change is captured as a baseline, so data a loader wrote straight to `proj_series` is restorable too.
- Tests for auth: login on gives 401 for every route including a module endpoint added with no guard code; open mode is tested through the app's `AUTH_DISABLED` when `app/auth.py` has it, and by stubbing `auth.is_authenticated` on this branch (which predates the flag).

## How a module plugs in

1. Create `app/projection/<key>/__init__.py` exporting `MODULE` (keyword arguments only):

```python
# app/projection/drivers/__init__.py   (key = drivers; blueprint is mounted at /api/projection/drivers)
from flask import Blueprint, jsonify, request
from .. import engine, series
from ..interfaces import MONTHS, DriverResult, Module, Table
from ... import db

bp = Blueprint("proj_drivers", __name__)

def load_inputs(conn, scn):                          # read-only; rows are dicts
    return series.get_series(conn, scn, prefix="drivers.")          # scenario -> base fallback

def compute(scn, inputs):                            # plus any upstream by name: drivers=, cohorts=, pnl=, demand=, inventory=, financing=
    spend = inputs.get("drivers.spend_total", {})
    cpa = scn.params.get("drivers", {}).get("nc_cpa", 95.0)         # effective params (base chain merged)
    nc_rev = {m: spend.get(m, 0.0) / cpa * 69.0 for m in MONTHS}
    return DriverResult(spend_total=spend, nc_rev=nc_rev, actual_through=None)

@bp.get("/table")                                    # GET /api/projection/drivers/table?scenario=current
def table():
    res = engine.run(request.args.get("scenario"), db.get_conn())
    t = Table.for_axis(res.actual_through)
    t.add("spend_total", "Ad spend", res.drivers.spend_total, input=True, missing=None)
    t.add("nc_rev", "New-customer revenue", res.drivers.nc_rev, kind="total")
    t.captions.append("Source: scenario inputs")
    return jsonify(t.to_payload())

MODULE = Module(key="drivers", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="drivers.js")
```

2. Calling convention (engine.py): `load_inputs(conn, scn)` or `load_inputs(conn)`; `compute(scn, inputs, **upstream)` where only the upstream results named in the signature are passed (or all, with `**kw`): `drivers`, `cohorts`, `pnl`, `demand` (inventory_demand), `inventory` (inventory_supply), `financing`, plus the shortcut `nc_rev` (= `drivers.nc_rev`). Order: drivers, cohorts, pnl, inventory_demand, inventory_supply, financing, pacing. `scn.params` is the effective view. A module that raises is replaced by its stub and the error is listed in `/run` (`strict=True` re-raises; reconcile runs strict).
3. Schema: your migration number (cohorts 110, drivers 120, pnl 130, inventory_demand 140, inventory_supply 150, financing 160, pacing 170), tables prefixed `proj_`, additive and idempotent. Scenario-independent facts have no `scenario_id`; anything a scenario can change is a lever in `params[<key>]` or a series in `proj_series` (`line_key` prefixed with your module, e.g. `drivers.spend_total`).
4. Writes: edits go through the core `PUT /series` and `PATCH /scenarios` (audited, versioned, undoable). Loaders (`etl/projection_<module>.py`) use `rwdb.get_rw()` and `series.upsert_actuals(conn, rows, source)`. Upload endpoints must have a path segment starting `upload` (e.g. `/api/projection/pnl/upload`) to get the multipart/8 MB allowance and the upload rate limit; do preview-then-commit (`?commit=1`).
5. Reads inside a request use `db.get_conn()` (read-only snapshot); never write from it.
6. Grid payloads: `Table.for_axis(actual_through)`, `Table.add(key, label, series_or_list, kind=line|subtotal|total|header|memo, fmt=money|money2|int|num2|pct|pct1|x|text|date, input=bool, missing=0.0|None)`, `to_payload()`; NaN/Infinity become null.
7. Add `app/projection/<key>/reconcile_map.py` (below) and `MODULE_<key>.md`.

Registry: a package that is missing, raises on import, exports no `MODULE`, has a `MODULE.key` that differs from the package name, or was built with positional arguments is logged and listed `built:false`; nothing else is affected.

## Fixture stubs

Until a module is built the engine uses `stubs.py`: values from `scripts/projection/snapshot_read.py` (env `PROJECTION_SNAPSHOT_DIR`), P&L rows 11-22, 24, 31, 46, 47, 51-56, 95, 100, Curves B/C 4:63 and C65; `actual_through` from `proj_actuals` if any, else the snapshot's last "Actual" column. Stub pnl line keys (`total_revenue`, `noi`, ...) are not the real `LINE_KEYS`. Demand, inventory and financing stubs are empty/zero. If the snapshot cannot be read every series is zero and the engine still runs.

## reconcile

```
PYTHONPATH=. DATABASE_URL=postgresql://localhost/tagexp_proj_<module> ~/lonvera-tag-explorer/.venv/bin/python scripts/projection/reconcile.py \
    --module <key>|all [--scenario current] [--tolerance 0.005] [--abs 1] [--format table|json] [--live]
```

`app/projection/<module>/reconcile_map.py`:

```python
MAP = [
  {"key": "nc_rev",  "tab": "P&L",    "row": 12, "c0": "C", "c1": "BJ", "kind": "values", "result": "drivers.nc_rev"},   # month of c0 = month0 (default 2025-01-01)
  {"key": "curve",   "tab": "Curves", "kind": "column", "c0": "C", "r0": 4, "r1": 63, "result": "cohorts.curve_live"},   # list element i <-> row r0+i
  {"key": "lifetime","tab": "Curves", "kind": "scalar", "row": 65, "c0": "C", "result": "cohorts.lifetime_multiple"},
]  # optional per entry: month0 'YYYY-MM-01', scale, tol, abs
```

`result` is a dotted path into the EngineResult (`drivers.nc_rev`, `pnl.lines.noi`, `demand.units.revamp|bull`; dict keys may contain dots, `a|b` addresses a tuple key). A cell passes when abs diff <= `--abs` or relative diff <= `--tolerance` (relative = |engine - sheet| / max(|engine|, |sheet|)). Blank sheet cells count as 0; text and error cells are skipped and counted. Output per line: cells, skipped, max abs diff (and month), max rel diff, count over tolerance, status; lines on a stub show `stub` and `--module <key>` on an unbuilt module exits 2. Exit 0 all pass, 1 over tolerance or unresolved path, 2 usage. `--live` re-reads the sheet read-only (one `values.get` per tab through `Desktop/Google Sheets/sheets.py`, env `SHEETS_PY_DIR`); not exercised in the tests.

## Commands

```
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
PY=~/lonvera-tag-explorer/.venv/bin/python; cd ~/lonvera-tag-explorer-projection
createdb tagexp_proj_<module>
DATABASE_URL=postgresql://localhost/tagexp_proj_<module> PYTHONPATH=. $PY -m etl.migrate        # applies 001..004, 100 and every migration present
PYTHONPATH=. $PY -m pytest tests/projection/<module> -q                                          # core: tests/projection/core (creates tagexp_proj_core itself)
DATABASE_URL=postgresql://localhost/tagexp_proj_<module> APP_PASSWORD=<local value> SECRET_KEY=<any> DEV_INSECURE_COOKIES=1 \
  PYTHONPATH=. $PY -m flask --app wsgi run --port <free port>
curl -c jar.txt -H 'Content-Type: application/json' -d '{"password":"<local value>"}' http://127.0.0.1:<port>/api/login    # login-on mode on this branch
curl -b jar.txt http://127.0.0.1:<port>/api/projection/modules
```

Test pattern for a module: copy the `core_db` / `app` / `client` / `clean` fixtures from `tests/projection/core/conftest.py` (own database, base migrations plus your own, `synchronous_commit=off`), keep test file names unique (`test_<module>_*.py`) because the test directories are not packages.

## Differences from the contract

- `Scenario` has extra optional fields (`base_slug`, `version`, `is_default`); every result dataclass and `Module` field after `key` has a default so partial results and stubs construct; `Module` takes keywords.
- `POST /scenarios` without `clone_from` clones the default scenario (a blank scenario would have no inputs).
- Extra files beyond the contract list: `limits.py`, `stubs.py`. `proj_audit` has an extra `version` column; `proj_series.value` is NOT NULL (null means delete); month first-of-day CHECKs; one-default partial unique index.
- Extra endpoints: `GET /scenarios/<slug>/history`, `POST /scenarios/<slug>/clone`, `expected_version` (409 on conflict) on PATCH/PUT/restore, `detail=1` and `prefix=` on `GET /series`.
- `/modules` lists exactly the seven catalog modules; `ui` is the file name the module declares (for example `cohorts.js`).

## Open items

- A clone is a snapshot plus a link: cells the clone never held, and cells it deletes, show the base's value. If a clone should be fully detached, delete its `base_slug`; there is no endpoint for it yet.
- Delete is not restorable (history cascades with the scenario); the audit row keeps the name, params and series row count only.
- `proj_audit` and `proj_scenario_history` have no age-based pruning (history keeps the last 200 versions per scenario; a 500-cell PUT is 500 audit rows).
- Rate limits and the login-free write path are per process; the public deployment's protection is the app's `AUTH_DISABLED` choice plus these rails.
- The `AUTH_DISABLED` flag is not in this worktree's `app/auth.py`; re-run `tests/projection/core/test_core_guards.py::test_open_when_the_apps_auth_is_off` after merging the parent (it switches to the real flag by itself).
- `--live` reconcile and the Python 3.12 run were not executed here (syntax checked against 3.12; tests ran on 3.14).
