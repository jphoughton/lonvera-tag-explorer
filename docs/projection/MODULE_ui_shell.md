# UI shell and shared library

What it is: `/projection` page shell (top bar, scenario picker, module nav, headline strip, history drawer), the module loader, and `lib/` (grid, formats, charts, api, scenario store). Module agents write one page file each and reuse this; nothing below needs a build step (ES modules, vendored Chart.js, no CDN).

## Files

| Path | What |
|---|---|
| `app/static/projection.html` | The one HTML document for `/projection` and `/projection/<module>` (the Flask routes serve it). `noindex`. |
| `app/static/js/projection/shell.js`, `shell.css` | Routing, nav, scenario bar, headline strip, history drawer, module loader. |
| `app/static/js/projection/lib/grid.js`, `grid.css` | `Grid` (monthly grid). |
| `.../lib/format.js` | Money / percent / date formats, `parse()` for typed input. |
| `.../lib/charts.js` | Chart.js helpers for month series. |
| `.../lib/api.js` | `fetchJSON`, `saveSeries` (batch + debounce), `makeContextApi`. |
| `.../lib/scenario.js` | Scenario store. |
| `.../lib/util.js` | `h()`, `toast`, `askText`, `confirmBox`, `confirmTyped`, `ui.loading/error/empty`. |
| `.../_demo.js` | Library demo at `/projection/_demo?demo=1` (sample data, nothing saved). |
| `tests/projection/ui/` | Mock server + Playwright tests (see "Run it"). |

`app/static/app.js` got one line: a "Projections" link in the top bar (`target="_self"` so the SPA click handler leaves it alone).

## Module contract

The registry (`GET /api/projection/modules`) returns `{modules:[{key,label,group,built,ui}]}`. For a module with `built: true` the shell does `import('/static/js/projection/' + ui)` (`ui` must match `^[A-Za-z0-9_-]+\.js$`) and calls

```js
export async function render(el, ctx) { ...; return cleanup /* optional function */ }
```

`el` is an empty `div.pg-module` inside `<main id="view">`. Render into it. Return a cleanup function if you hold anything (grid, timers, listeners); the shell calls it before the next navigation and also calls `charts.destroyAll()`. Throw (or reject) to show the shell's error box with Retry; do not catch `AuthError`.

URLs: `/projection` (overview), `/projection/<module-with-dashes>?scenario=<slug>` (`inventory_demand` and `inventory-demand` both resolve). `built:false` modules show a "Not built yet" card. Changing the scenario re-runs `render` with a new `ctx`.

### ctx

| Field | Meaning |
|---|---|
| `scenario` | current slug. `scenarioInfo` = `{slug,name,base_slug,version,is_default,updated_at}`, `scenarioVersion` = number. |
| `module` | `{key,label,group,built,ui,dash}`; `dash` is the API prefix form (`inventory-demand`). |
| `params` | URL query as an object. `setParams({k: v|null})` writes the query with `replaceState`, no re-render. |
| `api` | `get(path, params)`, `post/put/patch/del(path, body)`, `table(module, params)`, `saveSeries(items)`, `run()`, `fetchJSON(path, opts)`. Paths without `/api/` are relative to `/api/projection`. `get`/`table`/`run` add `scenario=<slug>` for you. |
| `grid` | factory `ctx.grid(el, opts)` -> `Grid`. The class is `ctx.Grid`. |
| `fmt` | `lib/format.js` (see below). `charts` = `lib/charts.js`. `h` = DOM builder. `toast(msg, 'ok'|'error'|'info')`. |
| `ui` | `ui.loading(el, msg)`, `ui.error(el, err, retryFn)`, `ui.empty(el, msg)`. |
| `reload()` | re-run your `render` (keeps scroll). |
| `alive()` | `false` after the user navigated away; check after awaits before touching the DOM. |
| `onVersion(fn)` | called when the scenario version changes (after a save, restore or edit elsewhere); removed at teardown. |
| `refreshHeadline()` | ask the shell to refetch the headline strip (it already does so after every saved edit). |

Auth, rate limits and batching are handled once in `lib/api.js`, never in module pages:
- HTTP 401 (only when the app's own login is on) sends the browser to the Tag Explorer `/login?next=<this page>`; the promise rejects with `AuthError` which the shell ignores.
- HTTP 429 `{error:"rate_limited"}` becomes `ApiError` with `err.code === 'rate_limited'` and the message "Too many changes in a short time. Wait a minute and try again."
- Any other `{error}` becomes `ApiError(err.message = the error string, err.status, err.code)`.
- Mutations always send `Content-Type: application/json` (`{}` body when none is given; `DELETE` sends only a body you pass).
- `ctx.api.saveSeries(items)` merges calls made within 250 ms into one `PUT /api/projection/series {scenario, items}` (the last value wins per `line_key|month`), splits at 500 items (server cap), sends one request at a time, and resolves with `{version}`. Items: `{line_key, month, value|null, kind?='input', note?}`; `note` is only sent when you pass it. `value: null` clears the override. Extra fields (such as `previous` from the grid) are dropped.

### Grid payload (from `/api/projection/<module>/table?scenario=`)

```
{ months:['2025-01-01', ...], status:['actual'|'projected', ...],
  rows:[{ key, label, kind, fmt, values:[...], input:bool, group?, note?, agg?, digits?, indent? }],
  captions:['one short source line'],
  fy?:{ years:[2025,...], rows:{ <key>:[value per year] } } }
```

- `kind`: `input` (yellow, editable when `input:true`), `calc`, `subtotal` (bold, top rule), `total` (bold, heavy rule), `ratio` (italic, muted), `header` (section band; collapsible; no values).
- `fmt`: `usd` ($1,234.50), `usd0` ($1,235), `pct` (value is a fraction: 0.125 shows 12.5%), `num` (1,234; 2 decimals when under 100 and not whole; override with `digits`), `x` (1.20x), `text` (status chips for OUT / OVERDUE / ORDER SOON / OK ...), `date` (ISO in, "Nov 5, 2026" shown).
- Display rules: zero is an em dash, missing is blank, money and `num` negatives in parentheses and red, `pct` / `x` negatives with a minus and red. Projected months get an asterisk on every non-zero value and a light-blue header; the first projected month has a boundary rule. Wide tables scroll inside the grid (sticky label column, sticky header); the page never scrolls sideways.
- Groups: a `header` row owns the rows after it (until the next header). Rows may also carry `group` to override. Collapsing hides `calc` / `input` / `ratio` rows of the group and keeps `subtotal` and `total` rows visible.
- FY columns (toolbar toggle "FY totals", or `fy:true` option): per row `agg` = `sum` | `avg` | `last` | `none`. Default sum for `usd`, `usd0`, `num`; none for rates, ratios and text. `payload.fy.rows[key]` overrides the computed value (use it for rows where a sum is wrong, such as end-of-year balances; or set `agg:'last'`).
- `row.note` shows an "i" next to the label with the note as tooltip (put the source of an input there).

### Grid API

```js
const grid = ctx.grid(el, { payload, onEdit, frozenCols:1, density:'compact'|'comfortable', groupCollapse:true|false|[groupIdsCollapsedAtStart],
                            yearBands:true, fy:false, highlight:null, readOnly:false, title:null, toolbar:true, rowHeader:'', onSelect:null });
grid.update(payload)            // re-render in place: scroll, selection and collapsed groups are kept; deferred while a cell editor is open
grid.setOptions({density, fy, yearBands, highlight, readOnly})
grid.getSelection()             // {cells:[{key,month,value}], rows:[key], months:[iso]}
grid.select(rowKey, monthIso)   grid.undo()   grid.copy({selectionOnly})   grid.toTSV()
grid.collapseAll() / expandAll() / toggleGroup(id)   grid.destroy()
```

- `highlight`: `(cell) => 'good'|'warn'|'bad'|'info'|null` with `cell = {key, month, value, status, row}`; or an array of row keys to emphasise.
- **Edits.** Only rows with `input:true`, month columns (never FY), and only when `onEdit` is given. Click a yellow cell (or Enter / F2 / double-click / just type) to edit. Enter commits and moves down, Tab / Shift+Tab commit and move sideways, arrows Up / Down commit and move, Esc cancels, clicking elsewhere commits. Empty or `-` clears the cell (`value: null`). Delete / Backspace on a selection clears every editable cell in it.
- **Typed values** (`fmt.parse`): `$1,234.50`, `(1,234)` (negative), `12%`, `1.2x`, `350k`, `1.5m`, `2b`. In a `pct` row a bare number is percent points (`12` = 12%). Dates: `2026-11-05` or `11/5/2026`. Invalid input keeps the editor open with a red outline and the reason in the toolbar status.
- **Paste.** Select a month cell, paste tab-separated text (from Sheets or Excel): fields go across the months starting at the selection, lines go down the visible rows. Calculated rows and invalid fields are skipped (a toast says how many). A single value pasted over a shift-selected range fills the range. Pasting several cells while the editor is open pastes into the grid. A paste is one `onEdit` call.
- **`onEdit(edits)`** receives `[{line_key, month, value|null, previous}]` (one entry per changed cell, a paste or clear in one array) and returns a Promise. Resolve = saved (cells flash green, status "Saved n cells", entry on the Undo stack). Reject = not saved: cells revert to `previous`, red outline with the message, toast. If it resolves with a fresh grid payload (`{months, rows, ...}`) the grid re-renders from it, which is how calculated rows refresh. The grid shows the new value immediately while the promise is pending.
- Undo (toolbar or Ctrl/Cmd+Z) re-sends the previous values through `onEdit`. Up to 50 steps.
- Keyboard: arrows, Shift+arrows (range), Home / End, Ctrl+Home, PageUp / PageDown, Tab, Esc (collapse range), Ctrl/Cmd+C (copies the selection as tab-separated plain numbers, parseable by paste), Ctrl/Cmd+Z.
- Toolbar "Copy" puts the whole visible table on the clipboard as TSV (month labels as headers, no asterisks or currency symbols).

### Formats (`ctx.fmt`)

`money(v,{digits,parens})`, `pct(v,digits)`, `x(v,digits)`, `num(v,digits)`, `compact(v)` ($1.2M, -$350K), `monthLabel(iso,long)` ("Nov 2026"), `shortMonth(iso)`, `date(iso)`, `dateTime(s)`, `display(v, fmt, {digits})` -> `{text, neg, blank, zero}`, `value(v, fmt)` (plain text, zero as 0), `parse(text, fmt)` -> `{ok, value}` or `{ok:false, error}`, `editText(v, fmt)`, `statusTone(text)` -> good / warn / bad / null, `yearOf(iso)`.

### Charts (`ctx.charts`)

```js
charts.lineChart(canvas, {months, status, series:[{label, values, color?, fmt?, axis?, dashed?, hidden?}], yFmt:'usd0', title?})
charts.barChart(canvas, {... same, stacked?: true})
charts.comboChart(canvas, {months, status, series:[{label, values, type:'bar'|'line', axis:'y'|'y1', fmt?}], yFmt:'usd0', y1Fmt:'pct'})
charts.scenarioCompare(canvas, {months, status, baseline:{name, values}, others:[{name, values}], fmt:'usd0', title?})
charts.monthChart(...)   // the base of all four; also accepts compare:[{name, values}] overlays
charts.destroy(chart)    charts.destroyAll()      charts.PALETTE
```
`status` is the same array as the grid payload: projected months are dashed (lines) or lighter (bars) on a tinted band, tooltips say "(projected)". Put each canvas in `div.pg-chartbox` (280 px tall, responsive). Colors come from `PALETTE` in order. The app has no dark mode, so the helpers read axis / grid colors from CSS variables but ship no dark palette.

### CSS you can use (shell.css / grid.css / app.css)

`.pg-page` (column with 14 px gaps), `.pg-title` (+ `.sub`), `.pg-section`, `.pg-card`, `.pg-chartbox`, `.pg-cards`, `.pg-chip.good|warn|bad|info`, `.btn`, `.btn.primary`, `.btn.small`, `.btnlink`, `.muted`, `.err`, `.empty`, `.errorbox`, `.pg-input`. Dialog helpers: `askText`, `confirmBox`, `confirmTyped` from `ctx` imports of `lib/util.js` (`import { askText } from './lib/util.js'`).

## Shell behavior

- Scenario picker: select + New (`POST /scenarios {name}`), Clone (`POST /scenarios {name, clone_from}`; falls back to `POST /scenarios/<slug>/clone {name}` on 404 / 405), Rename (`PATCH /scenarios/<slug> {name}`), Delete (not shown for the default scenario; confirm dialog needs the slug typed back; `DELETE /scenarios/<slug>` with body `{confirm:"<slug>"}`; 400 / 409 messages from the server show inside the dialog). The chip shows `v<version>`. Switching scenario re-renders the active module and writes `?scenario=` to the URL (replaceState); the choice is remembered in `localStorage` (`projection.scenario`).
- History drawer (button "History"): versions from `GET /scenarios/<slug>/history` (`{history:[{version,at,action}]}`) with the audit rows from `GET /audit?scenario=<slug>` (`{audit:[{at,actor,action,line_key,month,old,new,note,version}]}`) attached by `version` (rows without one fall back to the version written at the same time, within 2 s). Every version except the current one has a Restore button: confirm dialog, then `POST /scenarios/<slug>/restore {version}`, toast, and the active module re-renders. Audit rows that match no version are listed under "Other activity".
- Headline strip: `GET /run?scenario=` -> shows "Actual through <month>" from `actual_through` and, from `headline`, the year after the actual-through year for revenue and NOI (`headline.fy`), the implied EV range (`headline.ev`, keys like `3x`..`8x`), `lifetime_multiple` and `lowest_ending_cash`. An array of `{key,label,value,fmt}` or a flat object of numbers also works. Hidden when the response has neither. Refetched after every saved edit (debounced).
- States: loading text while a module imports, error box with Retry when the script or `render` fails, "Not built yet", "Not found", `data-pg-state` on `<body>` (`loading` / `ready` / `error`) for tests.

## 30-line example page

`app/static/js/projection/drivers.js` (this exact file is what the mock server serves as `_mockpage.js` and what the tests drive):

```js
export async function render(el, ctx) {
  const { h, ui, charts } = ctx;
  const gridBox = h('div');
  const canvas = h('canvas', { 'aria-label': 'New-customer revenue' });
  el.replaceChildren(h('div', { class: 'pg-page' },
    h('div', { class: 'pg-title' }, h('h1', ctx.module.label), h('span', { class: 'sub' }, ctx.scenarioInfo.name)),
    h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, canvas)),
    gridBox));
  const load = () => ctx.api.table(ctx.module.dash);                // GET /api/projection/<module>/table?scenario=<slug>
  const draw = (p) => {
    charts.destroyAll();
    const nc = p.rows.find((r) => r.key === 'nc_rev');
    charts.comboChart(canvas, { months: p.months, status: p.status, series: [{ label: nc.label, values: nc.values, type: 'bar' }] });
  };
  ui.loading(gridBox);
  try {
    const payload = await load();
    draw(payload);
    const grid = ctx.grid(gridBox, {
      payload, title: ctx.module.label,
      onEdit: async (edits) => {                                    // edits = [{line_key, month, value, previous}]
        await ctx.api.saveSeries(edits);                            // batched PUT /api/projection/series
        const fresh = await load(); draw(fresh); return fresh;      // a returned payload re-renders the grid
      },
    });
    return () => grid.destroy();
  } catch (e) { ui.error(gridBox, e, ctx.reload); }
}
```

Rules for module pages: build DOM with `h()` or `textContent` (never `innerHTML`); months as columns, entities as rows; one short source caption per table (`captions`); labels read as a finished document; no CDN, no build step; edits go through `onEdit` -> `ctx.api.saveSeries` so they are audited and versioned.

## Run it

```
cd ~/lonvera-tag-explorer-projection
~/lonvera-tag-explorer/.venv/bin/python tests/projection/ui/mock_server.py 5191      # http://127.0.0.1:5191/projection/_demo?demo=1
~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/ui                # starts its own mock on a free port
```

`.claude/launch.json` has an entry `projection-ui` for the same server. The mock implements modules, scenarios (create / clone / rename / delete with `confirm` / restore, 409 on a duplicate name), series PUT (validated like the real one: unknown fields, duplicate cells and non-numbers are 400; versioned; history + audit rows), `/run` (real `headline` shape), `/scenarios/<slug>/history`, `/audit`, and `drivers` + `cohorts` tables with a small spend x CPA x AOV model. Hooks for tests: `POST /__mock/reset`, `/__mock/app_auth {on}` (simulated app login: 401 without the cookie), `/__mock/rate_limit {n}`, `/__mock/fail_next {n}`, `GET /__mock/state` (recorded PUT bodies). A module agent testing against the real Flask app uses the same Playwright fixtures (`tests/projection/ui/conftest.py`) with their own base URL.

## Known gaps / notes

- After a Tag Explorer login triggered by a 401, the existing SPA login does a client-side `navigate(next)` and the SPA has no `/projection` route, so the user lands on its "Not found" page until a reload. One line fixes it (not applied: outside this agent's file ownership): at the top of `render()` in `app/static/app.js`, `if (/^\/projection(\/|$)/.test(location.pathname)) { location.replace(location.pathname + location.search); return; }`. Only matters when the app's login is on.
- No dark mode (the app has none). No redo. Drag-selection is not supported (shift+click and shift+arrows are).
- Response shapes were checked against `app/projection/api.py` (create / patch / restore return `{scenario}`, delete returns `{deleted}`, `PUT /series` returns `{version, changed}`); the open points are in `QUESTIONS_ui_shell.md`.
