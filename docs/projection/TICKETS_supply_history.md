# Tickets for proj-core from inventory_supply (history and restore)

Filed by proj-inventory-supply (fix wave, QA H4). **Closed 2026-11-03:** core landed both hooks. `scenarios.restore` calls `on_restore` and the response carries `restore.modules.inventory_supply`; `scenarios.update` calls `validate_params` with the module's whole group. Covered by `test_the_cores_scenario_restore_takes_the_supply_data_back_with_it` and `test_validate_params_hook_rejects_what_the_variables_endpoint_rejects`. The text below is kept as the record of what was asked.

### core-restore-hook: scenario restore must call module restore hooks

- **Problem:** `scenarios.restore` puts params and series back and nothing else. The plan of record's orders, transfers, stock counts and price lists live in tables, so restoring `current` in the History drawer leaves them as they are.
- **Done in the module:** every plan-of-record supply write bumps the default scenario's version (action `supply: <what>`), keeps before/after in `proj_supply_history`, and shows in History with its audit rows (`app/projection/inventory_supply/history.py`). The module can undo what happened after a version: `app.projection.inventory_supply.on_restore(conn, slug, to_version, new_version, actor=None)` (default scenario only; writes its history under `new_version`, never bumps again; returns `{reverted: n}`). Also available without core: `POST /api/projection/inventory-supply/history/revert {version}` and `/history/restore {to_version | at}`, used by the page's "History of orders and stock" panel.
- **Fix in core (`app/projection/scenarios.py`, `restore`):** after `finish_change` returns `new_version`, for the default scenario call, for every built module whose package defines `on_restore`, `on_restore(conn, slug, version, new_version, actor)` inside the same transaction; add the returned counts to the `scenario.restore` audit row's `new`. Discover by attribute name on `app.projection.<module>` (the `Module` dataclass has no field for it).
- **Test:** add an order on `current`, restore `current` to the version before it; the order is gone, the scenario version moved once, and `GET /inventory-supply/history` shows the restore.

### validators: register the module's validators when the hooks exist

- `app.projection.inventory_supply.validate_params(params, conn=None)` validates `params["inventory_supply"]` (variables in range and typed, unknown keys, overlay shapes, and against the stored rows when given a connection) and raises `ApiError(400)`. It is what `PUT /inventory-supply/variables` and the write endpoints already run.
- The module has no monthly series (`validate_series` has nothing to check for it).
- **Fix in core:** call `validate_params(ns_params, conn)` from `scenarios.update` for each namespace whose module package defines it (core's `PATCH /scenarios/<slug>` currently accepts any `inventory_supply` value; the engine ignores bad ones and falls back to the sheet value).
