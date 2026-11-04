# Core: open items and deploy notes (end of fix wave)

Status: done and tested unless listed here. Whole tree: `pytest tests/projection` = 2537 passed, 6 skipped, 0 failed (4 min 17 s); each directory also passes alone.

## Not done / owned elsewhere
- **Module validators still to add** (TICKETS_validation.md): drivers `validate_params` (QA M2: `drivers.sales_tax_rate: 5` is stored and ignored), pnl `validate_series` (only the calculated totals are read-only in the core), cohorts / pacing / inventory_demand `validate_params` where their groups have limits. Until then those groups are checked only by the core's generic rules (finite, below 1e12, known group).
- **Clock tickets** (TICKETS_clock.md): inventory_supply `loaders.py:22` and `freshness.py:27`, pnl `store.py:75`, `model.py:274`, `views.py:164` still call `date.today()`.
- **UI:** the shell does not send `expected_version` (QA M3), so two tabs can still overwrite each other; the API supports it (409).
- **Optional:** modeler can drop its `series.fetch_cells` wrapper for `series.with_overlay` (ticket sent).
- Not run: Python 3.12 (syntax checked only), `reconcile.py --live`, the production database.
- `only_this_module` (inventory_demand conftest) is still session-scoped; the parent `tests/projection/conftest.py` hook neutralizes its leak, so it was left alone.

## Before deploying (what could touch existing live data)
- Migrations 105 and 106 are additive and idempotent. **106** adds `proj_scenarios.frozen` (default false) and `archived_at` (null): no row is rewritten, no existing scenario changes behaviour. Scenarios that already exist as clones keep reading their base until someone presses Detach (`POST /scenarios/<slug>/detach`); `current` is unaffected.
- **Delete now archives** (30 days, `POST /scenarios/<slug>/undelete` brings it back); the daily run purges older ones. Nothing is dropped at deploy.
- **Restore changed**: it no longer deletes cells that were not created through the audited path, refreshes the baseline snapshot of the current version from the live state at the next audited write, and for the plan of record calls each module's `on_restore` (inventory_supply puts orders, transfers and counts back to that version, history kept). A restore of `current` after supply edits will therefore revert supply rows too: intended, audited, undoable.
- **Writes the API used to accept are now refused (400)**: negative or absurd spend, `kind=input` on a closed spend-plan month (use `override`), unknown key prefixes, calculated P&L keys, group names that are not module keys, and whatever the modules' own validators refuse. Seeders that write SQL directly are unaffected; anything that writes through `series.apply_items` or `PATCH /scenarios` with such values will now fail loudly.
- **A failing module now degrades its dependents** (503 `upstream_degraded` on their GET endpoints, `/run.degraded`); before, they showed zeros or snapshot numbers. Expect a visible 503 banner instead of a quiet wrong number if a feed or a module breaks.
- `/robots`, auth and the daily run are unchanged; previews have their own 600/min budget.
