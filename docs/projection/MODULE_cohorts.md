# Projection module: cohorts

## What it replaced

Three sheet tabs of Lonvera Projections: `Cohort Data - Unified` (the cohort table and its last-N average row), `Curves` (Live and Goal retention curves, tail decay, lifetime and 12-month sums) and `Hist Waterfall - Last 4 Months Curve` (layers 1 to 5: age grid, curve lookup, repeat dollars, total revenue, actual repeat). Output feeds P&L row 17 (Repeat Revenue) through `CohortResult.repeat_rev`.

What the module does: loads the cohort export (one row per acquisition month, cumulative dollars per customer by month of age), builds the Live curve from the latest cohorts and keeps a saved Goal curve, and applies the chosen curve to every cohort's first-order revenue to get repeat revenue by calendar month, plus the repeat revenue the cohorts actually earned.

## Files

```
migrations/110_proj_cohorts.sql              proj_cohorts, proj_cohort_cum, proj_curve_goal
migrations/111_proj_cohorts_history.sql      proj_cohort_uploads (one row per committed upload, with the replaced rows, for undo)
migrations/112_proj_cohorts_goal_history.sql every Goal save or restore is its own snapshot (snapshot_id, saved_at, saved_by, restored_from)
app/projection/cohorts/__init__.py           MODULE (key "cohorts", ui "cohorts.js", blueprint at /api/projection/cohorts)
app/projection/cohorts/engine.py             pure math: Cohort, Levers, live_curve, as-of masking and curves, choose_curve, waterfall, actual_repeat, frontiers
app/projection/cohorts/checks.py             upload plausibility checks: blocking errors, warnings, before/after multiples
app/projection/cohorts/compare.py            curve options and the in-memory comparison of curve choices (no writes)
app/projection/cohorts/model.py              load_inputs / compute, CohortResultX, today() (the one date seam)
app/projection/cohorts/store.py              reads, upserts, Goal snapshots, freshness
app/projection/cohorts/upload.py             CSV parsing (two layouts), closed-months rules, diff
app/projection/cohorts/views.py              grid / curves / heatmap / summary payloads
app/projection/cohorts/api.py                blueprint
app/projection/cohorts/reconcile_map.py      sheet cells the reconcile compares
etl/projection_cohorts.py                    seeder from the sheet snapshot, freshness(conn)
app/static/js/projection/cohorts.js          the page
app/static/js/projection/cohorts_curve_picker.js   the Retention curve card (mounted by pnl.js) and the curve controls (used by cohorts.js)
tests/projection/cohorts/                    194 tests (engine, as-of, API, curve API, upload checks and undo, sheet tie-out, store, upload, Chromium page tests)
```

## Tables (migration 110, additive, idempotent)

| table | holds |
|---|---|
| `proj_cohorts(cohort_month pk, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, payback_months, nc_rev_reference, source, loaded_at)` | one row per acquisition month (33 loaded: Feb-24 to Oct-26). `nc_rev_reference` is the new-customer revenue typed on the sheet's Hist Waterfall tab, kept only to tie the engine out to the sheet; the engine never reads it for the default result |
| `proj_cohort_cum(cohort_month, age 1..36, cum_per_customer)` | cumulative dollars per customer, first order included (sheet M1..M36). No row = not observed. 522 cells loaded |
| `proj_curve_goal(snapshot_date, age 1..60, value, note)` | Goal curve snapshots; the newest `snapshot_date` is active. Seeded from `Curves!B4:B63` (snapshot 2026-09-29) |

Per-customer amounts use `numeric(18,6)` and rates `numeric(10,6)`, not the money default `numeric(18,2)`: these are per-customer rates and the curve differences them, so two decimals would distort late months (and make every re-upload look changed).

## Variables (every lever of the module; scenario params `cohorts`, saved with PUT `/api/projection/cohorts/levers`, versioned and audited through the core)

All are also listed on the Variables page (`app/projection/variables/specs_cohorts.py`) with the same defaults and sources.

| variable | default | sheet / source | what it does |
|---|---|---|---|
| `curve_source` | `live` | `P&L!A2` "Repeat Case" (Last 4 Months Curve) | which curve drives repeat revenue: `live` (shown as Current), `goal`, `asof`, or `blend:<w>` (w = weight on Goal, 0 to 1). Replaces the earlier lever name `curve`, which is still read |
| `asof_month` | none | no sheet cell (new) | first of a month. With `curve_source = asof`, the Live-curve rule is applied to the cohort table as it stood at the end of this month. Required when the source is `asof`; must be a month the curve can be built for |
| `n_cohorts` | 3 | `Cohort Data - Unified` row 36, the literal 3 in "Last 3 Cohorts Avg" | most recent cohorts averaged at each month of age. The P&L page offers 3 or 6 (the pending 3-versus-6 question is now a first-class selector); the Cohorts page and the API take 1 to 36. Applies to Current and As-of |
| `min_cohorts_per_age` | 1 | row 36, `MIN(3, count)` | fewest cohorts that make a month of age trustworthy; fewer falls back to decay |
| `tail_from_age` | 28 | `Curves` rows 4-30 read the table (ages 1-27), row 31 starts the decay | the curve decays from this month of age |
| `tail_decay` | 0.95 | `Curves!C2` | share of the previous month kept in the tail |
| `max_age` | 60 | `Hist Waterfall` literal 60 in `IF(age>60,0,...)` | no repeat revenue beyond this month of age |
| `upload_lifetime_min` / `upload_lifetime_max` | 0.5x / 5x | no sheet cell (new) | allowed band for the 60-month repeat multiple an upload would produce; outside it the upload cannot be loaded |
| `upload_twelve_min` / `upload_twelve_max` | 0.2x / 3x | no sheet cell (new) | the same for the first-12-month multiple |
| `upload_first_order_min` / `upload_first_order_max` | $5 / $500 | no sheet cell (new) | allowed first order per customer |
| `upload_first_order_tol` | 0.40 | no sheet cell (new) | how far a new or changed cohort of 1,000 customers or more may sit from the median first order of the 3 cohorts on each side |
| `upload_customers_tol` | 0.60 | no sheet cell (new) | how far a stored cohort's customer count may change in a re-upload (also the "far from neighbours" note for a new cohort) |

Bad values are rejected with 400 and a message; a scenario written with a bad lever (outside the API) is read leniently (defaults for the bad cell) so the engine never stops. A scenario that never saved a curve choice follows its base scenario's (the core's clone-with-link rule); every save writes `curve_source` explicitly, so a scenario that has saved a choice keeps its own.

## Method (what the numbers are)

- **Increment** of a cohort at month `a` = `cum[a] - cum[a-1]` with `cum[0]` = first order; blank when `cum[a]` is blank or zero (sheet: `IF(OR(M="",M=0),"",M-prev)`).
- **Live curve** at month `a` (before `tail_from_age`) = weighted average (weights: new customers) of `increment / first order` over the most recent `n_cohorts` cohorts that have an increment at that month; blanks and zeros are excluded from numerator and denominator, as in the sheet. From `tail_from_age` on: previous value x `tail_decay`.
- **Repeat revenue** in a month = sum over cohorts of `cohort revenue x curve(age)`, age = whole months between the first-of-month dates (the sheet's `DATEDIF(...,"M")`); age 0 is the first order, age above `max_age` is 0, never negative. Table cohorts use new customers x first order; every axis month without a table row uses the drivers' `nc_rev`. Cohorts before Jan-25 still repeat into the axis.
- **Actual repeat** = increment x new customers by calendar month (sheet layer 5), shown beside the modeled figure. Closed months are modeled with today's curve (as in the sheet), so older months can read below what those cohorts earned; the page says so in a tooltip.
- **As-of curve.** The Live-curve rule applied to the cohort table as it stood at the end of month `M`: cohort `C` counts only if `C <= M`, and its month-`a` cell counts only if `C + a <= M` (the calendar months fully closed by `M`). Month `M` itself is closed, so a cell landing in `M` counts; the cohort acquired in `M` counts with a first order and no repeat yet. Everything else is the Live rule unchanged: weighted average over the last `n_cohorts` cohorts that had reached each age, `min_cohorts_per_age`, decay from `tail_from_age` or, when an age had no cohort at all, from the last age that had data, 60-month cap. As of the latest closed month it reproduces the current curve exactly (tested). A month is buildable when each of ages 1 to 12 has at least `n_cohorts` cohorts behind it: with 3 cohorts that is May 2025 to Oct 2026 in the data today (18 months), with 6 it is Aug 2025 onward. "As of Aug 2026" means data through the end of Aug 2026, so a snapshot taken during August, before it closed, is the Jul 2026 entry.
- Month status on the page: months up to `observed_through` (the last closed month a loaded cell lands in) are actual, the rest projected.

## Outputs (`CohortResultX`, a `CohortResult` with extras; the engine reads only the declared fields)

`curve_live`, `curve_goal` (ages 1..60), `lifetime_multiple` (sum of the curve in use, ages 1..60; Live by default, = `Curves!C65`), `repeat_rev` (60 axis months, modeled), `repeat_rev_actual`, `cohort_rows`. Extras: `curve_used`, `curve_label` (e.g. "As of Aug 2026, last 3 cohorts"), `curve_asof`, `curve_points` (per age: value, source `data`/`tail`, cohorts used), `twelve_month_multiple`, `lifetime_live/goal`, `twelve_live/goal`, `repeat_by_year`, `first_order_rev`, `new_customers`, `repeat_rev_sheet_basis` (tie-out), `data_through`, `observed_through`, `levers`, `warnings`, `cohort_table`.

## API (`/api/projection/cohorts`, all behind the app's own auth and the projection write guard)

| method + path | |
|---|---|
| `GET /table?scenario=` | grid payload: repeat revenue by cohort year, modeled total, actual, modeled vs actual, first-order revenue, total revenue from cohorts, repeat share, new customers; one source caption |
| `GET /curves` | ages, live, goal, used, cumulative, source and cohorts used per age, lifetime and 12-month summaries, Goal snapshot date |
| `GET /cohorts` | heatmap data: cumulative dollars per customer and increment ratios by cohort and month, with the cells that feed the Live curve flagged |
| `GET /summary` | headline numbers, backtest (modeled vs actual, last 3 months), forward 12-month repeat, freshness and the plain freshness line |
| `GET /levers`, `PUT /levers {scenario?, levers:{...}, expected_version?}` | effective levers, own levers, defaults, bounds. `null` removes a lever. Goes through `scenarios.update`: version bump, history snapshot, `scenario.params` audit row, restorable. An as-of choice is checked against the data: a month that cannot be built is 400 with the range that can (for example "choose a month from Oct 2025 to Oct 2026") |
| `GET /curve-options?scenario=&n=` | what the picker offers: the saved choice and its label, the sources, the cohort counts (3 and 6), every month with whether it is buildable, its 60-month and 12-month multiples and the fewest cohorts behind it, the default month (Aug 2026), the Goal snapshot date |
| `GET /curve-compare?scenario=&asof=2026-08&n=3&asof2=` | Current, Goal and As-of (and an optional second As-of), each run through the whole engine in memory with the lever overridden on a copy of the scenario: curve ages 1..60, lifetime and first-12-month multiples, the P&L basis year's (FY27) repeat revenue, total revenue, net operating income, implied value at each of the P&L's multiples, and the change against Current; `active` marks the saved choice. Reads only (tested: no rows written), about 150 ms for three options. Not cached: its inputs (cohort uploads, QuickBooks months, spend feeds) change without a scenario version bump, so a version-keyed cache would go stale |
| `POST /upload` (multipart `file`) | preview only (counts, diff, `committable`, `errors`, `warnings`, `impact`). `?commit=1` plus `expect_sha256` (from the preview) writes; commit without the hash is 400, a changed file is 409, a file with errors is 400 |
| `GET /upload/history`, `POST /upload/undo {upload_id}` | the uploads and which one is undoable; undo the newest one exactly (see below) |
| `GET /goal`, `POST /goal {from:"live", scenario?}`, `POST /goal/restore {snapshot_id?}` | the Goal in use, the one it replaced and the history; saving the current curve adds a snapshot (same-day saves each keep their own; a curve equal to the Goal is 409) and reports what it replaced; restoring saves a copy of the previous snapshot (or the one named) as the new Goal, so the Goal replaced stays in history and a second restore flips back. Audited `cohorts.goal.snapshot` / `cohorts.goal.restore` with the snapshot id as the version |

Writes are audited in `proj_audit`: `cohorts.upload` (file hash, counts, through month), `cohorts.goal.snapshot`, `scenario.params` (levers), `cohorts.seed`.

## Retention curve picker

Card "Retention curve" near the top of the P&L page (`cohorts_curve_picker.js`, mounted by three lines in `pnl.js`): a segmented control Current | Goal | As of month, a month select (buildable months only, default Aug 2026) and Cohorts averaged 3 | 6 (hidden for Goal); an overlay chart (months 1 to 36: Current solid, Goal, the chosen as-of dashed); a table with one row per option: repeat per first-order dollar over 60 months, first 12 months, FY27 repeat revenue, FY27 revenue, FY27 net operating income with the implied value at 3x to 8x, and the change against Current. FY figures are marked * (projected). The saved row is highlighted "In use". Clicking a row, or choosing in the controls and pressing "Use this curve", saves the choice in the open scenario (PUT `/levers`: audited, versioned, restorable from History); the whole P&L re-renders and a toast says "Repeat revenue now uses: ...". The headline strip of the P&L and of the Cohorts page carries "Repeat revenue uses: <label>". A note under the controls says whether the choice is saved in the plan of record or in a copy only; the shell's plan-of-record prompt (edit it or make a copy) applies to the first save. The Cohorts page uses the same controls in its settings (saved as you change them). Colors are tokens only; checked in dark and light at 1440 and 390 px.

What the three options give today (current plan, snapshot data, 3 cohorts): Current 1.58x lifetime, FY27 net operating income $1,526,273; Goal 1.88x, $2,086,659 (+$560,386); As of Aug 2026 1.74x, $1,817,282 (+$291,009). With 6 cohorts: Current 1.59x, $1,600,008; As of Aug 2026 1.79x, $1,952,789.

### Upload rules (standing rule: never include a month that is not over)

Two layouts: the sheet tab layout (Cohort Month, New Customers, 90-Day Repeat Rate, CAC, Payback, First Order (Realized), First Order, M1..M36 as cumulative dollars) and a long Shopify-style export (Month, Months since first purchase, Customers in cohort, Amount spent per customer). `through` = last closed month (Pacific), pulled back by a filename ending `to-YYYY-MM-DD`, or by `?through=`.
- cohorts after `through` are skipped ("In progress, skipped"); cohorts under 10 new customers are skipped (`?min_customers=`)
- a cumulative cell landing after `through` is blanked; a zero cell is "no data" and dropped
- the newest closed cohort keeps no 90-day repeat rate or cost to acquire (not final until the month matures), as Josh does by hand
- stored values are only added or revised, never erased; the preview shows cohorts added and changed, cells added, changed and blanked, the largest revisions old to new, skipped months and warnings (a cumulative total that falls)
- limits: 2 MB, 5,000 rows; Origin, rate limit (20 uploads an hour) and multipart rules come from the core guard

Verified against the real file: the 2026-11-03 export `cohorts-revenue-2023-11-02-to-2026-11-02.csv` previewed against the table seeded from the sheet snapshot gives 0 cohorts added, 0 changed, 0 cells added, 0 changed, 41 cells blanked for open months (the 41 the sheet's refresh note records), Nov-26 skipped, Oct-26 repeat rate and cost held back. The upload pipeline reproduces the sheet's own refresh exactly.

### Goal curve: confirm, history, restore

"Save current as Goal" (Cohorts page, under the Current against Goal table) opens a confirmation that states what changes for this scenario: the Goal in use (saved date, 60-month multiple, FY27 net operating income) against the current curve that would replace it, and the change (for example 1.88x to 1.58x, $2.09M* to $1.53M*, -$560K*), computed with the same in-memory comparison as the P&L card. Confirming adds a snapshot and shows a toast with Undo, which restores the Goal that was replaced. "Restore previous Goal" puts the previous snapshot back in use after a confirmation; both steps are audited and the Goal replaced is never deleted. The Goal is shared by every scenario, so saving or restoring it does not trigger the plan-of-record prompt.

### Upload checks and undo (a file that parses is not yet a file worth loading)

The preview runs plausibility checks on the table as it WOULD be after the upload and returns `committable`, `errors` (plain sentences), `warnings`, `issues` (level, code, month), and `impact` (the 60-month and 12-month multiples before and after, with the allowed band). A commit with any error is refused (400, "This file cannot be loaded: ..."), checked again at commit time.
- **Blocking:** a negative customer count, first order or cumulative amount; a first order outside the allowed band; a first order more than 40% from the median of its neighbours (new or changed cohorts of 1,000 or more customers); a stored cohort whose customer count changes by more than 60%; cumulative dollars that fall by more than 5% month to month (new or changed cohorts); a resulting 60-month or 12-month multiple outside its band.
- **Warnings:** a dip of 0.5% to 5%; a new cohort whose customer count is more than 60% from its neighbours; a multiple that moves more than 15%; a file holding under a quarter of the stored cohorts (a one-cohort file is checked against the table and noted); a new cohort with no first order. Months not yet closed are not an error: they are skipped or blanked and counted, as before.
- The bands are the `upload_*` variables above, read from the plan of record (an upload belongs to no scenario) and editable on the Variables page.
- Verified against the real file: the 2026-11-03 export and the sheet's own snapshot rows pass as a first load and as a refresh with no errors and no warnings, multiple 1.58x before and after. The QA probes ($99,999 first order, a negative first order, customers 3,614 down to 12, a one-cohort file) are tests; a negative first order on a cohort with repeat flips the multiple to a negative number and is blocked twice.

**Undo.** Every committed upload stores, in `proj_cohort_uploads`, the saved state of each cohort it wrote (exact stored text of every head field and cumulative cell, or "did not exist"), plus its counts, file hash and the multiples before and after. `POST /upload/undo {upload_id}` restores all of it in one transaction (cohorts the upload created are removed) and is audited as `cohorts.upload.undo`; only the newest upload that has not been undone can be undone (409 otherwise: a newer upload first, already undone), and 404 for an unknown id. `GET /upload/history` lists uploads with `undoable`; `GET /summary` carries `last_upload`. The Cohorts page shows "Undo last upload" (with a confirmation) whenever there is one. The sheet seeder is not an upload and is not undoable.

## Freshness

`etl.projection_cohorts.freshness(conn)` returns one feed: `cohort_export`, label "Cohort export", cadence `monthly-upload`, `stale_after_days` 45. `data_through` is the last day of the last fully closed cohort month with data (Oct 31, 2026 today), so the shared check (`age > 45 days`) gives a month about two weeks to be uploaded after it closes: loaded through Oct, fresh until Dec 15, stale from Dec 16. Extra keys for the page: `data_through_month`, `observed_through`, `last_closed_month`, `behind` (the literal rule: the last closed calendar month is not loaded; true from Dec 1), `stale`, `age_days`, `next_close`. It shows in `GET /api/projection/freshness` and the daily runner. The page's TLDR strip says it plainly: "Cohorts loaded through Oct 2026; Nov closes Dec 1." (warn chip "Oct 2026 is closed and not loaded yet" once the month is behind, bad chip when stale). Test: `test_cohorts_store.py::test_freshness_reports_a_monthly_upload_feed_relative_to_today`.

## Reconciliation (`scripts/projection/reconcile.py --module cohorts`, frozen snapshot 2026-11-03, drivers module real and seeded)

| line | cells | max abs diff | max rel diff | over 0.5% | status |
|---|---|---|---|---|---|
| curve_live (Curves C4:C63) | 60 | 0.00 | 0.0001% | 0 | ok |
| curve_goal (Curves B4:B63) | 60 | 0.00 | 0.0000% | 0 | ok |
| lifetime (Curves C65) | 1 | 0.00 | 0.0000% | 0 | ok |
| twelve_month (Curves C66) | 1 | 0.00 | 0.0000% | 0 | ok |
| repeat_rev_sheet_basis (Hist Waterfall row 228) | 60 | $0.04 | 0.0000% | 0 | ok |
| repeat_rev (Hist Waterfall row 228, default engine) | 60 | $14,868 (Jul-26) | 1.87% (Sep-26) | 29 | explained below |
| repeat_rev_actual (Hist Waterfall row 377) | 60 | $426,701 | 100% | 35 | explained below |
| pnl_row17 (P&L row 17) | 60 | $188,950 (Aug-26) | 19.2% (Aug-26) | 29 | explained below |

(The engine's curve differs from the sheet by at most 1e-6 relative: the six-decimal storage of per-customer amounts.)

Every difference above tolerance, with its cause:

1. **`repeat_rev`, 29 months over 0.5% (max 1.87%).** Cause: a stale hard-code in the sheet. The new-customer revenue in `Hist Waterfall!B6:B35` (Feb-24 to Jul-26) is typed and was not refreshed when the cohort tab was reloaded on 2026-11-03; it differs from new customers x first order in the cohort table by -4.2% to +5.5% per cohort (28 of 33 cohorts differ by more than 0.5%, none match exactly), while `P&L` row 12 for Jan-25 to Jul-26 is the formula new customers x first order and does match. The engine uses the cohort table. Proof it is the whole cause: `repeat_rev_sheet_basis` runs the same waterfall on the sheet's typed revenue and ties to $0.04 in every month, Jan-25 to Dec-29.
2. **`repeat_rev_actual`: Jan-25 to Aug-26 tie; Sep-26 on differ (35 cells).** Cause: a sheet bug in layer 5. `Hist Waterfall` rows 336 to 339 (cohorts Aug-26 to Nov-26) all point at the Jul-26 cohort's row in the cohort tab (`$AS$31:$CB$31` and `$B$31`) instead of their own rows, so the sheet counts Jul-26's increments for those cohorts and misses the real Aug, Sep and Oct increments, and shows $426,701 of repeat revenue for Nov-26 to Dec-29 that no cohort has earned (engine: 0). The engine reads each cohort's own increments. Nothing else reads layer 5.
3. **`pnl_row17`: 26 months are cause 1 (max 1.65%); 3 months are hand-typed in the P&L.** Jul-26 (`U17`) is the typed number 944,174 (engine 885,989, 6.2%); Aug-26 (`V17`) is `=1499426-V12` = 984,287 (engine 795,336, 19.2%); Sep-26 (`W17`) is a 50/50 blend of the waterfall with month-to-date pacing (770,614 vs 784,041, 1.7%). These are overrides owned by the drivers and pnl modules, not by cohorts.
4. **Typed new-customer revenue for Aug to Oct 2026** (drivers overrides 515,139 / 401,590 / 240,355) differs from the cohort table (534,185 / 400,643 / 246,904) for those three cohorts. The cohorts module uses the table for any month that has a cohort row (the brief's rule). Effect on repeat revenue if the drivers' figures were used instead: at most 0.72% in any month ($5,341).

## Other differences from the sheet

- **N = 3.** The snapshot's row 36 and the Curves caption say "Last 3 Cohorts Avg"; `context/ma-calano.md` says the live curve moved to the last 6 on 2026-11-03. The snapshot is what was reconciled, so the default is 3; it is a lever (`n_cohorts`). Josh to confirm which he wants as the plan of record.
- The waterfall covers the 60 axis months (Jan-25 to Dec-29); the sheet runs to Mar-30.
- A gap in a cohort's cumulative cells (a missing month between two observed months) makes that increment undefined; the sheet would subtract zero and book the whole cumulative as one month. No cohort in the data has a gap.
- Cohorts with no new customers never feed the curve (they occupy no slot).
- The Goal curve is data (`proj_curve_goal`, newest snapshot wins), re-takeable from the page ("Save Live as Goal"); in the sheet it is a typed column.
- `curve = goal` with no Goal curve loaded falls back to Live and says so (warning chip), instead of returning zero repeat.

## Interface notes for downstream modules

- `CohortResult.repeat_rev` is modeled repeat revenue for all 60 months on the curve the scenario selects. `curve_live` is always the Live curve (the drivers module's payback reads it); the curve actually applied is `curve_used`. A scenario on `goal` or `blend` makes the two differ.
- Forward cohorts' first-order revenue is read from the `nc_rev` argument (the engine passes `drivers.nc_rev`). A month that has a cohort row ignores `nc_rev`.
- For the drivers module: `app.projection.cohorts.store.first_order_revenue(conn)` and `new_customers(conn)` return `{month: value}` from the cohort table; the drivers module already reads `proj_cohorts(cohort_month, new_customers, first_order_per_customer)` directly, which matches this schema.
- Use `app.projection.cohorts.model.today()` (Pacific date) for any "is the month over" rule; tests patch it.
- The endpoints run the whole engine and fall back to the stub of any other module that fails (`strict` is off), so this page works while other modules are incomplete.

## Open items

1. N = 3 or 6 as the plan-of-record default (see above): the P&L card now lets Josh choose per scenario; the default stays 3.
2. The long (Shopify cohort) upload layout follows the description in `context/ma-calano.md`; no real file of that layout was available, so it is covered by synthetic tests only. The sheet layout is verified on the real 2026-11-03 file.
3. The Aug to Oct 2026 typed new-customer revenue (point 4) could be honored by the cohorts module as a lever (`nc_rev` first); not done, effect under 0.8%.
4. Hand-typed repeat months (Jul-26, Aug-26, Sep-26 in P&L row 17) belong to drivers and pnl overrides.
5. Not exercised: `reconcile.py --live` and Python 3.12 (syntax is 3.12-compatible; tests ran on 3.14).
6. See `QUESTIONS_cohorts.md` (grid vocabulary between core `Table` and the shell grid).
