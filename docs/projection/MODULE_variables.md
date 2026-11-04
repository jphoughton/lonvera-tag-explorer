# Variables (module `variables`)

What it is: one page, `/projection/variables`, that lists every input of the model (typed constants, levers, monthly series, tables, and numbers the sheet buries inside formulas), shows the Lonvera Projections sheet's value next to the current value, and edits them in place. It has no engine step: it is a settings surface over the seven calculating modules, and every edit goes through the owning module's own mechanism, so the core audits, versions and can restore it.

The registry of record is `docs/projection/VARIABLES.md` (generated from the code; 300 variables, how the sheet was audited, the literal-by-literal decisions, the selectors). Gaps found in the modules are in `docs/projection/TICKETS_<module>.md`.

## Files

```
app/projection/variables/__init__.py            MODULE (key variables, group "Settings", ui variables.js, blueprint /api/projection/variables)
app/projection/variables/spec.py                VariableSpec: id, module, group, label, unit, kind, default, bounds, sheet_ref, where_used, status, binding, path, ...
app/projection/variables/build.py               Builder (short constructors) and plain(): acronyms written out in labels
app/projection/variables/specs_<module>.py      the variables of each module, one file each (cohorts, drivers, pnl, inventory_demand, inventory_supply, financing, pacing)
app/projection/variables/registry.py            aggregation, id lookup, counts
app/projection/variables/access.py              Context, resolve, set_value(s), reset, scenario_with (in-memory copy), module validators
app/projection/variables/proof.py               fingerprint of every engine output (what a test or the sensitivity run compares)
app/projection/variables/sensitivity.py         one-at-a-time +/-10% on every scalar number, cached per scenario version
app/projection/variables/api.py                 the endpoints below
app/static/js/projection/variables.js           the page
tests/projection/variables/                     tests, tools (sheet audit, code audit, doc renderer, screenshots), fixtures
docs/projection/VARIABLES.md  MODULE_variables.md  TICKETS_<module>.md
```

Core edits (the only ones): `app/projection/interfaces.py` `MODULE_CATALOG` gained `"variables": ("Variables", "Settings")` (an eighth entry, group `Settings`, listed last); `tests/projection/core/test_core_interfaces.py` and `test_core_registry_engine.py` now expect eight catalog entries (`test_core_guards.py` comment only). `engine.ORDER` is unchanged: the module has no `compute`.

## Spec fields

`id` (`<module>.<name>`, snake_case parts), `module`, `group`, `label` (plain words, no bare acronyms), `unit`, `kind` (`scalar` | `series` | `table`), `value_type` (number, integer, fraction, money, bool, choice, month, date, text, list), `default` (the sheet's value), `lo` / `hi` / `step` / `choices`, `sheet_ref`, `where_used`, `status` (IMPLEMENTED | PARTIAL | MISSING), `binding`, `path` or `series_key`, `page` (module page that edits series and tables), `scope` (`scenario`, `all` = shared by every scenario, `feed` = a loader rule), `notes`, `ticket`, `mirrors` (other params paths written with it), `sheet_cells` (a test checks the default against the snapshot cell), `probe` (`engine` | `plan` | `view` | `none`), `also_used_by`, `extra` (`alt` value and `setup` variables for the proof test, `computed` for a value the model fills when blank, `readonly`).

Bindings: `param` = a path inside `scenario.params` (cohorts, drivers, pnl, inventory_demand, financing, pacing, the 22 inventory_supply settings), written with `scenarios.update` as a merge patch after the module's own validator ran (`cohorts.engine.parse_levers(strict)`, `drivers.api._check_levers`, `pnl.api.check_settings`, `inventory_demand.levers.validate_patch`, `financing.params.validate_patch`, `pacing.levers.validate_patch`, `inventory_supply.overlay._check_lever`). `supply` = a vendor, price, reorder or product-cost cell of inventory_supply, written through `overlay.set_variables` (stored in `params.inventory_supply`). `pnl_driver` = the long-run step of a cost line's driver: fixed dollars, or a typed rate on a revenue base; written as that line's `segments` after `validate_segments`. `series` and `table` bindings are read-only here: a summary and an "Edit in <page>" link.

Reset to the sheet value removes the scenario's own value; when the scenario's base scenario holds a different value it writes the sheet value instead, so a copy always ends on the sheet's number. Channel shares must add to 100%, so editing one rescales the other four in proportion. The payback inputs have one store, `params.drivers.payback` (`drivers.payback.*`, read by the Spend plan, Cash and debt and pacing); `financing.payback.aov|cac` are blank (live from pacing) unless typed. The China landed cost has one store, `params.pnl.cogs.china_cost.<sku>` (the P&L cost model and the inventory stock value both read it), so there is one variable per product. `VariableSpec.mirrors` stays for a future duplicate but no spec uses it.

## API (`/api/projection/variables`, behind the app's own auth and the projection write guard)

| method + path | |
|---|---|
| `GET /?scenario=&module=&q=&changed=1` | `{scenario, modules, counts, variables:[...], changed_count, shown}`; each variable carries `value`, `default`, `changed`, `origin` (scenario, inherited, default), `status`, `kind`, `editable_inline`, `summary` (series and tables), `computed`, `where_used`, `sheet_ref`, `notes`, `ticket`, `edit_link` |
| `PUT /` | `{scenario, items:[{id, value}], expected_version?}`, at most 100 items; `value: null` goes back to the sheet value; 400 names the variable (bounds, whole numbers, choices, the module's own message), 404 unknown id, 409 version conflict; one scenario version for all param-bound items; returns the updated variables |
| `PUT /<id>` | `{scenario, value, expected_version?}` |
| `POST /reset` | `{scenario, ids? \| module? (with group?) \| all: true}`; only changed, editable variables; returns what was reset |
| `GET /sensitivity?scenario=` | each scalar number moved to 90% and 110% (integers by at least one), the whole engine rerun in memory, effect on FY27 net operating income, FY27 revenue and the lowest month-end cash; sorted by the net operating income swing; capped at 15 seconds (`truncated`), cached per scenario version and day |

Every write is a scenario version (`scenario.params` audit row with old and new, history snapshot, restorable). Same-scenario reads never run the engine more than once.

## Page

`Variables` is the last tab of the bar (group Settings). Header, one-sentence TLDR ("3 variables differ from the sheet. These 5 move FY27 net operating income most.") and five KPIs (the five biggest movers with their swing; the counts before the ranking has loaded). A search box and a "Changed only" toggle, jump chips per module, the scenario cue sentence ("You are editing Current plan (version 5) ... This is the plan of record" or "This is a copy"). "What matters most": a tornado table of the top 12 (all on request): blue bar = number moved down 10%, orange = up 10%, left of the centre line lowers net operating income, right raises it; on a phone the bars give way to a sentence. Then one collapsible section per module with the table Variable | Value | Sheet value | Unit | Used by | Source; amber inputs (fractions are typed as percent), a Reset link on every changed row, "Reset all to sheet" per module and for the page; series and tables show a summary and "Edit in <page>"; the last section lists the sheet items the model does not carry, each with why. Phones show each row as a card. Light and dark, keyboard (tab, Enter or leaving a cell saves, Esc restores), no sideways scroll at 390px.

## Tests (`tests/projection/variables`, own database `tagexp_proj_variables_test`, 511 tests: 505 pass, 6 skip)

- `test_variables_registry.py`: ids, wording (no bare acronyms, no build history), every default ties to its sheet cell on the frozen snapshot (85 cells), paths match module namespaces, the formula-literal tool.
- `test_variables_access.py`: every spec resolves; the seeded plan equals the sheet; set -> read -> reset round trip for each of the 245 editable numbers; **moving each implemented variable changes an engine output** (or the planner's proposal) for 213 (the typed demand rates are editable again: the core now validates by explicit lists, not by name); in-memory copy equals the stored change; channel-share rebalance; validation messages; reset to the sheet value in a copy; series change detection; mirrored payback inputs.
- `test_variables_api.py`: listing and filters, audited edit and restore, validation, bulk, reset by group, copies, agreement both ways with the cohorts, spend plan, cash and debt, inventory demand, inventory supply, P&L and pacing endpoints, sensitivity (shape, cache, equals the stored change), login guard, Origin and JSON rules, page and static file served.
- `test_variables_page.py`: Chromium at 1440 and 390 in light and dark: sections, TLDR length, edit saves and flags, reset, bad value shows the reason, the plan-of-record guard (cancel sends nothing), search, series links, no console output, no sideways scroll.
- `test_variables_docs.py`: VARIABLES.md equals the generated text, every ticket named by a spec exists, the audit tools run.

`tools/`: `formula_literals.py` (numbers inside formulas), `code_literals.py` (numbers in module code), `fetch_sheet_fills.py` + `yellow_inventory.py` (input cells from the live sheet's fills, read-only), `render_registry.py` (writes VARIABLES.md), `shoot_variables.py` (screenshots).

## Provisional and open

- inventory_supply, pacing and pnl were still being edited while this was built. Their specs bind to their current params layout and read `inventory_supply.model.SETTING_SPECS` and `pacing.levers.SPECS` directly, so a renamed lever fails `test_every_spec_resolves_on_the_seeded_plan` instead of drifting.
- The shell's group names do not know group `Settings` (the narrow menu shows the raw name).
- The shell's plan-of-record guard (`lib/guard.js`) asks before the first edit of the plan of record; this page inherits it through `ctx.api` and treats a cancelled choice as no change.
- The cohorts agent extended `specs_cohorts.py` for its own new levers (curve source, as-of month, upload checks); the registry picks them up unchanged.
- Core validates params centrally (`validators.py`, explicit lists, not names); a module hook whose signature the core cannot call blocks every write to that module (seen once for `pacing.levers.validate_params`); `test_set_then_read_then_reset_round_trips` marks that case xfail with the reason.
- Series and tables are not edited inline by design; `changed` for a series compares with the oldest history snapshot of the plan of record (the state before the first edit), so a plan edited before any history existed reads as unchanged.
