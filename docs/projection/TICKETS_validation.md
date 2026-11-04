# Tickets for module owners: validators and restore hooks

Filed by proj-core. The core now validates every write in one place (`app/projection/validators.py`), whichever endpoint carries it: `PUT /series`, the pattern endpoints and anything else that calls `series.apply_items`, and `PATCH /scenarios/<slug>` for params. Core rules are always on (see MODULE_core.md, "Validation"). Module rules are hooks the core finds by name, as an attribute of `MODULE` or of the package `app.projection.<key>`, and calls with the values its parameters are named for (so declare only what you need, in any order). A hook rejects with `ApiError(400, plain message)`.

| hook | parameters the core can pass | called |
|---|---|---|
| `validate_series` | `conn`, `scenario`, `items` (parsed `series.Item` list: `line_key, month, value or None, kind, note`) | on every series write, for every module that defines it, after the core rules (filter for your own prefix) |
| `validate_params` | `params` (your whole group after the patch), `conn`, `scenario`, `namespace` | on `PATCH /scenarios` for the module whose key is the changed group, after the core rules |
| `on_restore` | `conn`, `slug`, `to_version`, `new_version`, `actor` | after a restore of the plan of record, inside the same transaction; return a JSON-able dict (it goes into the response and the audit row) |

Already wired with no change: drivers `MODULE.validate_series(conn, scenario, items)`, inventory_supply `validate_params(params, conn=None)` and `on_restore(conn, slug, to_version, new_version, actor=None)`.

To do:

- **financing:** done (`validate_params(params)` is on the module; `tax.rate`, debt and payback limits run on the generic PATCH).
- **drivers:** `drivers.sales_tax_rate`, channel shares and the other drivers params have no params rule yet (QA M2: `sales_tax_rate: 5` is stored and ignored); add `validate_params(params)` for the `drivers` group.
- **Why the core does not guess:** an earlier version bounded every key named `rate` to 0..1 and broke `inventory_demand`'s rate (units per day, e.g. 43.8). Limits are the owning module's to declare; the core only keeps the explicit lists in validators.py.
- **pnl:** add `validate_series` to reject keys that are not input lines and bounds your upload/override rules already know; the core only rejects the calculated totals (`noi`, `total_*`, `gross_profit*`, `ttm_*`, `ftm_*`, `*_pct`, `*_mom`, `*_yoy`).
- **drivers:** your hook now also runs on `PUT /series`; unknown `drivers.*` keys (the 500 junk keys class) are yours to reject. Your own endpoints can keep their checks.
- **cohorts, pacing, inventory_demand, variables:** add `validate_params` if your group has limits the generic rules (rates 0..1, `*_months`/`*_days`/`*_weeks`/`*_units` numbers not negative) do not express.
- **Series prefixes:** keys must start with a module key (`drivers.`, `pnl.`, ...). A module that needs another prefix calls `validators.register_prefix("name")` at import.
