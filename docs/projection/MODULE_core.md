# Projection core (foundation)

What it replaced: nothing in the sheet directly. It is the shared plumbing every module sits on: scenarios, monthly series, loaded actuals, audit and undo, the module registry, the engine that runs the modules in order, and the reconcile harness.

Auth: projection has none of its own. `/api/projection/*` sits under the app's `/api/*` guard (`app/auth.py`, named users): login on gives 401 without a session, the app's `AUTH_DISABLED` makes it open. Nothing in `app/projection/` reads a password or issues a cookie.

## Files

```
migrations/100_proj_core.sql                  tables below + seed 'current'
migrations/105_proj_refresh.sql               proj_refresh_runs
etl/projection_daily.py                       daily refresh runner (python -m etl.projection_daily)
app/projection/__init__.py                    register(app): guard rails, core API, module blueprints, /projection pages
app/projection/interfaces.py                  Month/Series, MONTHS, Scenario, result dataclasses, Row/Table (grid payload), Module
app/projection/rwdb.py                        read-write pool (get_rw)
app/projection/scenarios.py                   CRUD, clone, params merge, history snapshots, restore
app/projection/series.py                      get_series / get / apply_items / actuals helpers
app/projection/audit.py                       proj_audit writer and reader, current_actor()
app/projection/limits.py                      in-memory per-IP rate limits
app/projection/clock.py                       today_pt(): the Pacific date, PROJECTION_TODAY pin
app/projection/degraded.py                    module dependencies, ensure_ok, the 503 response
app/projection/validators.py                  series and params validation, module hooks
migrations/106_proj_scenario_lifecycle.sql    frozen, archived_at
app/projection/freshness.py                   feed freshness: hook loading, evaluation, GET /freshness payload
app/projection/registry.py                    module discovery + blueprint mounting
app/projection/engine.py                      run(): modules in order, stubs for unbuilt ones
app/projection/stubs.py                       fixture stubs read from the sheet snapshot
app/projection/api.py                         /api/projection blueprint + install_guards()
scripts/projection/reconcile.py               engine vs sheet, per cell
tests/projection/core/                        300 tests
tests/projection/conftest.py                  makes `pytest tests/projection` work as one run (see Running the tests)
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
| `POST /scenarios` | `{name, clone_from?}` | 201 `{scenario}`; a FROZEN COPY of `clone_from` (default: the default scenario): its effective params and series are materialized now; `base_slug` only records where it came from |
| `POST /scenarios/<slug>/clone` | `{name}` | same, alias of the above |
| `PATCH /scenarios/<slug>` | `{name?, params?, expected_version?}` | `{scenario}`; params are a merge patch per namespace: objects merge, `null` removes a key, lists replace |
| `DELETE /scenarios/<slug>` | `{confirm:"<slug>"}` | `{deleted}`; archives the scenario for 30 days (see Lifecycle); `current`/default is 409, missing or wrong confirm is 400 |
| `POST /scenarios/<slug>/restore` | `{version, expected_version?}` | `{scenario, restore:{cells_changed, kept}}`; goes back to that version by the rule in Restore; itself a new version, audited cell by cell, undoable |
| `GET /scenarios/<slug>/history?limit=` | | `{history:[{version,at,action}]}` newest first |
| `GET /scenarios/archived` | | `{archived:[{...scenario, archived_at, days_left}]}`: deleted scenarios still inside the 30-day archive |
| `POST /scenarios/<slug>/undelete` | `{}` | `{scenario}`: brings a deleted scenario back whole (a name now taken gets ' (restored)'); 410 after 30 days |
| `POST /scenarios/<slug>/detach` | `{expected_version?}` | `{scenario}`: freezes a clone made before copies were frozen (copies what it inherited, stops reading its base) |
| `GET /series?scenario=&keys=a,b` or `&prefix=drivers.` `&from=&to=&detail=1` | | `{scenario, version, series:{key:{iso:value}}, detail?:{key:{iso:{kind,note,origin}}}}`; scenario -> base -> base's base fallback per cell; `origin` = the scenario that holds the cell |
| `PUT /series` | `{scenario, items:[{line_key,month,value\|null,kind,note}], expected_version?}` | `{version, changed}`; `null` deletes the cell (reverts to the base's value); max 500 items; unchanged cells are not written, audited or versioned |
| `GET /audit?scenario=&line_key=&limit=` | | `{audit:[...]}` newest first, limit <= 1000 |
| `GET /freshness` | | `{generated_at, feeds:[{module,feed,label,cadence,cadence_label,data_through,age_days,stale_after_days,status,message,last_run:{status,finished_at,rows,error}\|null}], summary:{fresh,stale,unknown}, degraded:[{module,error}]}`; see Daily refresh |
| `GET /run?scenario=` | | `{scenario, version, actual_through, books_through?, feeds_through?, month_status?, modules:{key:{built,stub,ms,error?}}, degraded:[{module,error}], headline:{fy,ev,lifetime_multiple,lowest_ending_cash}}`; see Degraded modules |

Validation: `line_key` `^[a-z0-9_.]{1,120}$`; `month` ISO date, first of month, inside 2025-01..2029-12; `value` finite number below 1e13 (never a string); `kind` input/override; `note` <= 500 chars; duplicate (line_key, month) in one request, unknown item fields and non-object items are 400 naming `items[i]`. Errors are `{error}`.

Pages: `/projection` and `/projection/<module>` serve `app/static/projection.html` (the UI-shell agent's file; a one-line placeholder is served only if it is missing).

## Guard rails (no password)

One set of app-level hooks, installed by `api.install_guards(app)` from `projection.register(app)`; `app.before_request` is registered after `auth.init_auth`, so the app's guard runs first (anonymous requests get 401 before any projection check when the app's login is on).

- `_write_guard` (before_request, acts only on non-GET/HEAD/OPTIONS under `/api/projection/`, so every module blueprint is covered with no code in the module): Origin must match `request.host` when present (403 `origin_mismatch`; `Origin: null` is rejected); per-IP sliding window 120 writes/minute (429 `{error:"rate_limited"}` + `Retry-After`); a POST whose path ends `/preview` (computes, writes nothing) has its own budget of 600/minute, `PROJ_RATE_PREVIEWS_PER_MIN`, and keeps the Origin and JSON checks; paths with a segment starting `upload` also count against 20/hour; body must be `application/json` (415 `json_required`) except `multipart/form-data` on an `upload*` path; request cap set per request to 1 MB (8 MB for multipart; the app-wide 64 KB cap is overridden for projection only, 413 above). Config: `PROJ_RATE_MUTATIONS_PER_MIN`, `PROJ_RATE_UPLOADS_PER_HOUR` (app config or env). Limits are per process: with N gunicorn workers the effective ceiling is N times higher.
- Noindex is the app's: `create_app` sends `X-Robots-Tag: noindex, nofollow` on every response and serves `/robots.txt` with `Disallow: /`; projection adds nothing of its own and a test checks the header on `/projection*` and `/api/projection/*`.
- Audit: every mutation writes `proj_audit` with old and new; actor = the app's signed-in username (`auth.current_user()`, e.g. `JoshHoughton`), `anonymous` when the app is open (`guest` from AUTH_DISABLED), `system` outside a request.
- Undo: every version bump snapshots the scenario; the state before the first API change is captured as a baseline, so data a loader wrote straight to `proj_series` is restorable too.
- Tests for auth: login on gives 401 for every route including a module endpoint added with no guard code; with `AUTH_DISABLED` the same routes work without a cookie (`test_api_projection_is_open_with_auth_disabled_and_401_with_it_off`, `test_open_when_the_apps_auth_is_off`).

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
6. Grid payloads: see Table vocabulary below.
7. Add `app/projection/<key>/reconcile_map.py` (below) and `MODULE_<key>.md`.

Registry: a package that is missing, raises on import, exports no `MODULE`, has a `MODULE.key` that differs from the package name, or was built with positional arguments is logged and listed `built:false`; nothing else is affected.

## Degraded modules are loud

`degraded.DEPENDS` (cohorts<-drivers, pnl<-drivers+cohorts, inventory_supply<-inventory_demand, financing<-pnl+inventory_supply, pacing<-pnl+drivers+cohorts) says which modules read which. When a module fails, every module that reads it, directly or through another, is degraded too (`upstream degraded: pnl`), whether or not its own code could have run on a missing upstream: its result is `None`. Then:

- `/run` carries `degraded:[{module,error}]` (root cause first) and no headline numbers that rest on a degraded module.
- Any GET under `/api/projection/<module>/...` whose module or upstream the engine degraded during that request is answered **503** `{error:"upstream_degraded", modules:[{module,error}]}` by an app-level `after_request`, replacing whatever the module returned (a 200 built on nothing, or its own 500). No module code is needed. Writes (POST/PUT/PATCH/DELETE) are never rewritten: the change was already committed. Core endpoints (`/run`, `/freshness`, `/scenarios`, `/series`, `/audit`) and page-only modules are not gated.
- Explicit form: `degraded.ensure_ok(res, "<key>")` after `engine.run` raises `UpstreamDegraded`, answered 503 the same way.
- With no snapshot (production) an unbuilt module's result is `None`, never zeros.

## Draft overlays

`with series.with_overlay(fn): engine.run(...)` lets a what-if run (the scenario modeler) see a read-only draft laid over the scenario's series: `fn(conn, scn, cells, keys, prefix, from_, to)` receives the winning cells and returns the cells the run sees. It is a ContextVar, so another request's thread never sees it, and nothing is written.

## Table vocabulary

`interfaces.Table` / `Row` speak the shared grid's names and emit them, so a module can return `Table(...).to_payload()` instead of hand-building rows:

- `kind`: `input` (yellow; editable when `input=True`), `calc`, `subtotal`, `total`, `ratio`, `header` (section band, no values). Default: `input` when `input=True`, else `calc`. Aliases (accepted, emitted as the grid name): `line` -> `calc`, `memo` -> `ratio`.
- `fmt`: `usd` ($1,234.50), `usd0` ($1,235; default), `pct` (fraction), `num`, `x`, `text`, `date`. Aliases: `money` -> `usd0`, `money2` -> `usd`, `int` -> `num` with `digits=0`, `num2` -> `num` with `digits=2`, `pct1` -> `pct` with `digits=1` (an explicit `digits` wins).
- Optional per row, emitted only when set: `group` (owning header key), `note` (tooltip, put the source of an input here), `agg` (`sum`|`avg`|`last`|`none`, how the FY column combines months), `digits` (0-6), `indent` (0-6). `Table.fy = {years, rows}` adds the grid's `fy` block.
- `Table.for_axis(actual_through)`, `Table.add(key, label, series_or_list, kind=, fmt=, input=, missing=0.0|None, group=, note=, agg=, digits=, indent=)`, `to_payload()`; NaN/Infinity become null. Unknown names raise `ValueError`; `interfaces.normalize_kind` / `normalize_fmt` expose the mapping.

## Fixture stubs and degraded modules

A module that is not built uses the fixture stub in `stubs.py` when the sheet snapshot is readable (development): values from `scripts/projection/snapshot_read.py` (env `PROJECTION_SNAPSHOT_DIR`), P&L rows 11-22, 24, 31, 46, 47, 51-56, 95, 100, Curves B/C 4:63 and C65; `actual_through` from `proj_actuals` if any, else the snapshot's last "Actual" column. Stub pnl line keys (`total_revenue`, `noi`, ...) are not the real `LINE_KEYS`. Demand, inventory and financing stubs are empty/zero. **With no snapshot (production) there is no stub: the result is `None`**, never zeros.

A module that is built but fails is **degraded**: its result is `None` and nothing stands in for it (not the stub, not zeros). Causes: the module raised (`ValueError: ...`), it returned nothing (`the module returned no result`), or a required upstream result is missing (`upstream unavailable: drivers`; a compute parameter named like an upstream result and given no default is required, one with a default such as `pnl=None` is optional). `EngineResult.errors` / `.degraded` and the `/run` summary (`degraded:[{module,error}]`; a degraded module's entry also carries `error`) carry the reasons; `GET /freshness` carries `degraded` for modules whose latest refresh failed or whose freshness check failed. `strict=True` raises instead. A module API that needs its own result checks for `None` and answers with its error (all of them already do).

## Validation

Every write is checked in `validators.py` before it is saved, so no endpoint can bypass a rule; one bad cell rejects the whole request (400, `items[i] <key> <Mon YYYY>: <reason>`). The core enforces generic sanity by explicit lists and never guesses from a name (a key called `rate` can be a percentage or units per day); a key or group a module has no rule for is accepted.

- Series, core rules: the key must start with a module key or a prefix added with `validators.register_prefix`; calculated P&L lines (`pnl.noi`, `total_*`, `gross_profit*`, `ttm_*`, `ftm_*`, `*_pct`, `*_mom`, `*_yoy`) are read-only; values of 1e11 or more are refused (a stray paste such as 500000350000); keys under `NON_NEGATIVE_PREFIXES` (`drivers.`, `inventory_demand.`, `inventory_supply.`) or in `NON_NEGATIVE_KEYS` (`financing.principal_paid`, `financing.equity_payback`) cannot be negative; `RANGES` bounds explicit keys (`drivers.spend_total` and `drivers.ad_spend` 0..50,000,000; `drivers.nc_cpa` and `drivers.nc_aov` above 0, at most 1,000); a spend-plan input (kind `input`, `drivers.*`) cannot be typed on a closed month (before the current Pacific month), kind `override` can. Deleting a cell is always allowed. Add rules with `register_range(regex, low, high)` and `register_non_negative(prefix_or_key)`.
- Params, core rules (on `PATCH /scenarios`): the group must be a module key; numbers finite and below 1e12.
- Module rules are hooks, found by name on `MODULE`, on the package `app.projection.<key>`, or on its `params` / `validation` submodule, and called with the values their parameters are named for: `validate_series(conn, scenario, items)` on every series write, `validate_params(params, conn=None)` for the changed group (the whole group after the patch). They raise `ApiError(400, plain message)`. A module whose tables are not installed in the database is skipped with a note. Adoption status is in TICKETS_validation.md (financing and inventory_supply `validate_params`, drivers `validate_series` are live).

## Lifecycle: copies, detach, delete

- **A clone is a frozen copy.** Creating a scenario from another materializes the source's effective params and series into it at that moment (`proj_scenarios.frozen = true`). `base_slug` is lineage only; later edits to the source never reach the copy and deleting a cell of the copy never surfaces the source's value.
- **Detach** (`POST /scenarios/<slug>/detach`) does the same for a scenario made before copies were frozen (`frozen = false`, it still reads its base for cells and params it does not hold): the inherited params and cells are copied into it, then it stops reading the base. Audited, versioned, shown in History.
- **Delete archives** for 30 days (`archived_at`). An archived scenario is gone from the list, 404 for reads and edits, its name can be reused, and `POST /scenarios/<slug>/undelete` brings it back whole (a taken name gets " (restored)"). The daily run and every later delete purge scenarios archived more than 30 days ago, except one that a not-yet-detached clone still reads.
- **Restore calls module hooks.** After a restore of the plan of record, every built module with an `on_restore(conn, slug, to_version, new_version, actor)` hook is called in the same transaction (supply puts its orders, transfers and counts back); their return values are in the response (`restore.modules`) and the audit row, and a failing hook rolls the whole restore back.
- The Pacific clock: `clock.today_pt()` (honors `PROJECTION_TODAY`) is the one source of today; see TICKETS_clock.md.

## Restore

`POST /scenarios/<slug>/restore {version}` writes back what the scenario held at that version, and never deletes what was seeded outside the audited path:

- Snapshots: the state at version N is stored when N is produced, and the snapshot of the current version is taken (or refreshed, when loaders wrote since) from the live state at the next audited write, so it contains seeded data.
- Every series cell and every param value in the snapshot is written back.
- A cell that is not in the snapshot is deleted only if the audited path created it after that version (its first audit row after the version is `series.set`). A cell with no audit row after the version, or whose first audit row is `series.update` or `series.delete`, was loaded outside the audited path and is left as it is (`restore.kept` counts them). Param keys follow the same rule: keys changed through audited param edits since the version and absent from the snapshot are removed (emptied namespaces are pruned), others stay.
- The restore writes `series.set` / `series.update` / `series.delete` audit rows for the cells it changes, so restoring again (including to undo) treats them as audited.
- Cloning copies every cell, seeded or not.

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

## Daily refresh

Railway's `etl` service (cron 13:00 UTC, start command `scripts/deploy/run_etl.sh`) now runs `etl.migrate`, then `etl.run_daily` (ad data), then `etl.projection_daily`. Both always run; the script exits non-zero if either failed, so the deployment shows Failed and the dashboard notification fires. One-offs are unchanged: `run_etl.sh projection_daily --dry-run`, `run_etl.sh meta_pull --days 185`.

Module hooks, in `etl/projection_<module>.py` (one per module key; conn rows are dicts; either hook may be missing, a module without `refresh` is recorded as skipped):

```python
def refresh(conn, days=14) -> {"rows": int, "data_through": "YYYY-MM-DD", ...}   # idempotent, incremental (upsert of the trailing days), no secrets in the output
def freshness(conn) -> [{"feed": "ad_daily", "label": "Ad spend (Meta)", "cadence": "daily" | "monthly-upload" | "manual",
                         "data_through": date | None, "stale_after_days": 2}]
```

`python -m etl.projection_daily [--only a,b] [--days 14] [--dry-run]`:
- each `refresh` runs on its own connection in its own try/except under a time cap (`PROJECTION_REFRESH_TIMEOUT_SEC`, default 600, SIGALRM so the running code is interrupted); its work is committed on success and rolled back on failure; one module failing never stops the others;
- every module is recorded in `proj_refresh_runs(module, started_at, finished_at, status ok|failed|skipped, rows, data_through, error)`; `error` is scrubbed (credentials in URLs, bearer/token values, known secret env values) and capped at 300 characters by the code and by a CHECK constraint; skipped rows say `no etl module` or `no refresh hook`;
- afterwards each module's `freshness` is evaluated against today (Pacific): `fresh` (age <= stale_after_days), `stale`, or `unknown` (no data yet; reported, not fatal);
- exit 1 when any refresh failed, any run could not be recorded, any freshness hook raised or returned a malformed entry, or any `daily` feed is stale. Stale `monthly-upload` and `manual` feeds (a hand-refreshed source such as a typed or uploaded count) and `unknown` feeds are printed but never fail the run; `/freshness` shows them as `stale` with `cadence_label` `Monthly upload` / `Manual`. Message form: `feed ad_daily last data 2026-11-01, 3 days old (stale after 2 days)`;
- `--dry-run` lists what would run, shows freshness, refreshes and records nothing, exits 0. `--only` limits both the refreshes and the freshness check to those modules.

`GET /api/projection/freshness` returns the same evaluation live (modules' `freshness(conn)` on the app's read-only connection) plus each module's latest ok/failed run as `last_run`; a module whose hook raises appears as one `unknown` feed named `(module)` with the error text.

Timing: the cron runs at 13:00 UTC (05:00-06:00 Pacific). Feeds that Josh's desktop routine writes to the sheet (about 07:00 local) are not there yet at that hour, so a `daily` feed backed by them needs `stale_after_days` >= 2 or it will fail every morning; a failure that persists means the routine did not run.

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

### Running the tests

```
PYTHONPATH=. $PY -m pytest tests/projection -q                  # the whole tree in one run (each directory builds its own database)
PYTHONPATH=. $PY -m pytest tests/projection/<dir> -q            # one directory; same result
```

`tests/projection/conftest.py` is what lets the directories share a process: (0) the UI tests run last, because they hold one Playwright session for the whole run and the sync API cannot start a second one while it is open; (1) several directories keep helpers in their own `conftest.py` and import them as `from conftest import ...`; a router takes over the name `conftest` and answers each import from the conftest.py of the importing file's own directory; (2) a `pytest_runtest_setup` hook, which runs before any fixture of the test, puts `registry.PACKAGE` back to the real package for every test that does not opt in, so a directory's lazily created app fixtures always mount the real modules (a directory that needs a fake package patches it per test with `monkeypatch`, or for its session through a fixture named `only_this_module`, whose package is restored for the tests that use it). Concurrent runs by several people share the databases named in each conftest; override with `TEST_PROJ_<DIR>_DB` (for example `TEST_PROJ_CORE_DB=my_core`) to keep a run isolated.

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
- `--live` reconcile and the Python 3.12 run were not executed here (syntax checked against 3.12; tests ran on 3.14).
