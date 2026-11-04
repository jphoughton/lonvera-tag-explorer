# Projection module: cohorts

## What it replaced

Three sheet tabs of Lonvera Projections: `Cohort Data - Unified` (the cohort table and its last-N average row), `Curves` (Live and Goal retention curves, tail decay, lifetime and 12-month sums) and `Hist Waterfall - Last 4 Months Curve` (layers 1 to 5: age grid, curve lookup, repeat dollars, total revenue, actual repeat). Output feeds P&L row 17 (Repeat Revenue) through `CohortResult.repeat_rev`.

What the module does: loads the cohort export (one row per acquisition month, cumulative dollars per customer by month of age), builds the Live curve from the latest cohorts and keeps a saved Goal curve, and applies the chosen curve to every cohort's first-order revenue to get repeat revenue by calendar month, plus the repeat revenue the cohorts actually earned.

## Files

```
migrations/110_proj_cohorts.sql              proj_cohorts, proj_cohort_cum, proj_curve_goal
app/projection/cohorts/__init__.py           MODULE (key "cohorts", ui "cohorts.js", blueprint at /api/projection/cohorts)
app/projection/cohorts/engine.py             pure math: Cohort, Levers, live_curve, choose_curve, waterfall, actual_repeat, frontiers
app/projection/cohorts/model.py              load_inputs / compute, CohortResultX, today() (the one date seam)
app/projection/cohorts/store.py              reads, upserts, Goal snapshots, freshness
app/projection/cohorts/upload.py             CSV parsing (two layouts), closed-months rules, diff
app/projection/cohorts/views.py              grid / curves / heatmap / summary payloads
app/projection/cohorts/api.py                blueprint
app/projection/cohorts/reconcile_map.py      sheet cells the reconcile compares
etl/projection_cohorts.py                    seeder from the sheet snapshot, freshness(conn)
app/static/js/projection/cohorts.js          the page
tests/projection/cohorts/                    102 tests
```

## Tables (migration 110, additive, idempotent)

| table | holds |
|---|---|
| `proj_cohorts(cohort_month pk, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, payback_months, nc_rev_reference, source, loaded_at)` | one row per acquisition month (33 loaded: Feb-24 to Oct-26). `nc_rev_reference` is the new-customer revenue typed on the sheet's Hist Waterfall tab, kept only to tie the engine out to the sheet; the engine never reads it for the default result |
| `proj_cohort_cum(cohort_month, age 1..36, cum_per_customer)` | cumulative dollars per customer, first order included (sheet M1..M36). No row = not observed. 522 cells loaded |
| `proj_curve_goal(snapshot_date, age 1..60, value, note)` | Goal curve snapshots; the newest `snapshot_date` is active. Seeded from `Curves!B4:B63` (snapshot 2026-09-29) |

Per-customer amounts use `numeric(18,6)` and rates `numeric(10,6)`, not the money default `numeric(18,2)`: these are per-customer rates and the curve differences them, so two decimals would distort late months (and make every re-upload look changed).

## Levers (scenario params `cohorts`, edited with PUT `/api/projection/cohorts/levers`, versioned and audited through the core)

| lever | default | meaning |
|---|---|---|
| `n_cohorts` | 3 | most recent cohorts averaged at each month (sheet row 36 "Last 3 Cohorts Avg") |
| `min_cohorts_per_age` | 1 | fewest cohorts needed to trust a month; fewer falls back to decay (sheet: `MIN(3, count)`) |
| `tail_from_age` | 28 | Live curve reads the table up to month 27, decays from month 28 |
| `tail_decay` | 0.95 | share of the previous month kept (`Curves!C2`) |
| `max_age` | 60 | no repeat revenue beyond this month (sheet: age above 60 returns 0) |
| `curve` | `live` | `live`, `goal` or `blend:<w>` (w = weight on the Goal curve, 0 to 1) |

Bad values are rejected with 400 and a message; a scenario written with a bad lever (outside the API) is read leniently (defaults for the bad cell) so the engine never stops.

## Method (what the numbers are)

- **Increment** of a cohort at month `a` = `cum[a] - cum[a-1]` with `cum[0]` = first order; blank when `cum[a]` is blank or zero (sheet: `IF(OR(M="",M=0),"",M-prev)`).
- **Live curve** at month `a` (before `tail_from_age`) = weighted average (weights: new customers) of `increment / first order` over the most recent `n_cohorts` cohorts that have an increment at that month; blanks and zeros are excluded from numerator and denominator, as in the sheet. From `tail_from_age` on: previous value x `tail_decay`.
- **Repeat revenue** in a month = sum over cohorts of `cohort revenue x curve(age)`, age = whole months between the first-of-month dates (the sheet's `DATEDIF(...,"M")`); age 0 is the first order, age above `max_age` is 0, never negative. Table cohorts use new customers x first order; every axis month without a table row uses the drivers' `nc_rev`. Cohorts before Jan-25 still repeat into the axis.
- **Actual repeat** = increment x new customers by calendar month (sheet layer 5), shown beside the modeled figure. Closed months are modeled with today's curve (as in the sheet), so older months can read below what those cohorts earned; the page says so in a tooltip.
- Month status on the page: months up to `observed_through` (the last closed month a loaded cell lands in) are actual, the rest projected.

## Outputs (`CohortResultX`, a `CohortResult` with extras; the engine reads only the declared fields)

`curve_live`, `curve_goal` (ages 1..60), `lifetime_multiple` (sum of the curve in use, ages 1..60; Live by default, = `Curves!C65`), `repeat_rev` (60 axis months, modeled), `repeat_rev_actual`, `cohort_rows`. Extras: `curve_used`, `curve_points` (per age: value, source `data`/`tail`, cohorts used), `twelve_month_multiple`, `lifetime_live/goal`, `twelve_live/goal`, `repeat_by_year`, `first_order_rev`, `new_customers`, `repeat_rev_sheet_basis` (tie-out), `data_through`, `observed_through`, `levers`, `warnings`, `cohort_table`.

## API (`/api/projection/cohorts`, all behind the app's own auth and the projection write guard)

| method + path | |
|---|---|
| `GET /table?scenario=` | grid payload: repeat revenue by cohort year, modeled total, actual, modeled vs actual, first-order revenue, total revenue from cohorts, repeat share, new customers; one source caption |
| `GET /curves` | ages, live, goal, used, cumulative, source and cohorts used per age, lifetime and 12-month summaries, Goal snapshot date |
| `GET /cohorts` | heatmap data: cumulative dollars per customer and increment ratios by cohort and month, with the cells that feed the Live curve flagged |
| `GET /summary` | headline numbers, backtest (modeled vs actual, last 3 months), forward 12-month repeat, freshness and the plain freshness line |
| `GET /levers`, `PUT /levers {scenario?, levers:{...}, expected_version?}` | effective levers, own levers, defaults, bounds. `null` removes a lever. Goes through `scenarios.update`: version bump, history snapshot, `scenario.params` audit row, restorable |
| `POST /upload` (multipart `file`) | preview only. `?commit=1` plus `expect_sha256` (from the preview) writes. Commit without the hash is 400, a changed file is 409 |
| `POST /goal {from:"live", scenario?}` | saves the scenario's Live curve as a new dated Goal snapshot (older snapshots stay); audited `cohorts.goal.snapshot` |

Writes are audited in `proj_audit`: `cohorts.upload` (file hash, counts, through month), `cohorts.goal.snapshot`, `scenario.params` (levers), `cohorts.seed`.

### Upload rules (standing rule: never include a month that is not over)

Two layouts: the sheet tab layout (Cohort Month, New Customers, 90-Day Repeat Rate, CAC, Payback, First Order (Realized), First Order, M1..M36 as cumulative dollars) and a long Shopify-style export (Month, Months since first purchase, Customers in cohort, Amount spent per customer). `through` = last closed month (Pacific), pulled back by a filename ending `to-YYYY-MM-DD`, or by `?through=`.
- cohorts after `through` are skipped ("In progress, skipped"); cohorts under 10 new customers are skipped (`?min_customers=`)
- a cumulative cell landing after `through` is blanked; a zero cell is "no data" and dropped
- the newest closed cohort keeps no 90-day repeat rate or cost to acquire (not final until the month matures), as Josh does by hand
- stored values are only added or revised, never erased; the preview shows cohorts added and changed, cells added, changed and blanked, the largest revisions old to new, skipped months and warnings (a cumulative total that falls)
- limits: 2 MB, 5,000 rows; Origin, rate limit (20 uploads an hour) and multipart rules come from the core guard

Verified against the real file: the 2026-11-03 export `cohorts-revenue-2023-11-02-to-2026-11-02.csv` previewed against the table seeded from the sheet snapshot gives 0 cohorts added, 0 changed, 0 cells added, 0 changed, 41 cells blanked for open months (the 41 the sheet's refresh note records), Nov-26 skipped, Oct-26 repeat rate and cost held back. The upload pipeline reproduces the sheet's own refresh exactly.

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

1. N = 3 or 6 as the plan-of-record default (see above).
2. The long (Shopify cohort) upload layout follows the description in `context/ma-calano.md`; no real file of that layout was available, so it is covered by synthetic tests only. The sheet layout is verified on the real 2026-11-03 file.
3. The Aug to Oct 2026 typed new-customer revenue (point 4) could be honored by the cohorts module as a lever (`nc_rev` first); not done, effect under 0.8%.
4. Hand-typed repeat months (Jul-26, Aug-26, Sep-26 in P&L row 17) belong to drivers and pnl overrides.
5. Not exercised: `reconcile.py --live` and Python 3.12 (syntax is 3.12-compatible; tests ran on 3.14).
6. See `QUESTIONS_cohorts.md` (grid vocabulary between core `Table` and the shell grid).
