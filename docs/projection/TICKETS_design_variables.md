# Design tickets: Variables (variables.js, still being built)

Owner: proj-variables. Last audit: 2026-11-03, 72 page loads (`DESIGN_REVIEW.md`).

1. **Page scrolled sideways at 390px (was high).** Resolved: 0 overflow in all 72 loads, light and dark, at 1440, 1024, 768 and 390. Keep wide tables inside their own scrolling box.
2. **Text under 12px (was medium).** Resolved: 0 findings (the 11px value labels on the small bar charts are gone). Keep `var(--lv-fs-xs)` as the floor.
3. **Amber input borders.** Fine: `.va-in select/input` use `--lv-input-edge`, a 3:1 border color.
4. **Local server answered 500 on `GET /api/projection/variables?scenario=current` (resolved by proj-variables, 2026-11-03).** Cause: the database that server used had not run the newest migrations (`106_proj_scenario_lifecycle.sql` adds `frozen` and `archived_at`; `/scenarios` and every scenario read failed with "column frozen does not exist", not only this endpoint). With `python -m etl.migrate` applied the endpoint answers 200 with 307 rows, the page renders and `tests/projection/variables/test_variables_page.py` (7 tests, 1440 and 390, light and dark) is green. Run the migrations on any long-lived local database after a core migration lands.
