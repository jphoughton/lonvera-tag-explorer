# Tickets from the modeler

### registry-specs (proj-variables)

Every knob of the modeler already has a registered spec except one. Add:

- `modeler.ebitda_addbacks`: scenario param `params.modeler.ebitda_addbacks`, a list drawn from `interest_expense`, `marketing_influencers`, `uncategorized`; default `["interest_expense"]` (sheet value: none, the P&L has no EBITDA line; Pacing's version is all three). Used by: yearly EBITDA on the Scenario modeler page. Validator: `app.projection.modeler.model.check_addbacks`. Set with `PUT /api/projection/modeler/settings {scenario, ebitda_addbacks}`. Proof: `tests/projection/modeler/test_modeler_model.py::test_the_ebitda_definition_is_a_setting_that_can_match_pacing` (moves FY EBITDA).
- Cross-reference only (already specs): `drivers.spend_total`, `drivers.nc_aov`, `drivers.nc_cpa` (series, edited on this page too: `also_used_by` modeler), `cohorts.curve_source`, `cohorts.asof_month`, `cohorts.n_cohorts`.

### preview-rate-limit (proj-core)

`api._write_guard` counts every non-GET under `/api/projection/` as a mutation (120 a minute per IP), including `POST /modeler/preview`, which writes nothing. A burst of quick edits (each edit is one preview plus one batched save) can hit the limit; the page then says "Too many changes in a short time" and the results stop following the typing for a minute. Fix: let a path whose last segment is `preview` (or a body flag the guard trusts, `preview:true`) count against a separate, higher read-style limiter (for example 600 a minute) and not against mutations. Test: `tests/projection/modeler/test_modeler_api.py::test_a_preview_counts_as_a_write_for_the_per_minute_limit` documents today's behaviour; flip it when the guard changes.

### series-overlay-hook (proj-core)

The draft run wraps `series.fetch_cells` from `app/projection/modeler/overlay.py` (a ContextVar wrapper installed at import; identity when no draft is active). A first-class `series.overlay(draft)` context manager in `series.py` would remove the wrapper. Test: `test_a_draft_is_only_visible_to_the_request_that_made_it`, `test_the_preview_of_spend_cpa_and_aov_equals_what_saving_them_gives`.

### grid-lock-and-edit-hook (proj-ui-shell)

1. A row option that renders actual-month cells as calculated and not editable (same ask as `QUESTIONS_drivers.md` 2). The modeler styles locked cells with a highlight tone and refuses the edit in `onEdit`.
2. A public `grid.applyEdits([{line_key, month, value, previous}])` that goes through `_commit` (status, flash, undo stack). The modeler pushes quick-edit entries onto `grid.undoStack` and calls `grid._syncToolbar()` so Undo and Ctrl+Z cover them.
3. Range selection by drag (Shift-click and Shift-arrows work).

### overview-purpose (proj-ui-shell)

`shell.js` `PURPOSE` has no `modeler` entry; the Overview card shows no purpose line. Suggested: `modeler: 'Try a spend plan and a retention model and see yearly revenue and EBITDA.'`.
