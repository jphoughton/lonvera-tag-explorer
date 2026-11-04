# Pacing (module `pacing`, migration 170)

What it replaced: the sheet tabs `Automated Pacing`, `Month Snapshot`, `Pacing - Daily (180d)`, `Rollup - Weekly (WoW)`, `Rollup - Monthly (MoM)`, `Meta - Daily (180d)`, `Meta - Weekly (WoW)` and `Meta - Monthly (MoM)`. Read-only consumer of the engine (scenario P&L goals, cohort curves) and of daily actual feeds; nothing downstream reads it. Page: `/projection/pacing`. API: `/api/projection/pacing/...`.

## TLDR

The month table, the snapshot and the day / week / month rollups reproduce the sheet cell by cell from the snapshot: 389 of the 409 reconcile lines pass with the real upstream modules, and every remaining difference is listed below with its cause. 160 pacing tests pass (the design tests pass alongside). Every number or rule that drove the sheet is now a named variable (18 scenario levers, 3 feed rules) with the sheet's value as the default.

## Files

```
migrations/170_proj_pacing.sql                feed tables (proj_feed_*), hourly curve, feed-rule settings
etl/projection_pacing.py                      refresh / freshness hooks, parsers, Meta aggregates from ad_daily, CLI (refresh | backfill | seed-hourly)
app/projection/pacing/__init__.py             MODULE (key pacing, ui pacing.js)
  model.py                                    load_inputs / compute -> PacingResult; reconcile layout (automated, snapshot, rollup_*)
  month.py                                    month pacing table, Month Snapshot, payback, intraday projection
  rollup.py                                   periods (day / ISO week / month), daily cost estimate, aggregation, projection
  catalog.py                                  declarative metric catalog (201 metrics: 8 bands and 6 Meta campaign groups)
  targets.py                                  P&L goals by month, cost lines (rate on a driver or fixed), tax rate
  levers.py                                   the variables: defaults, sources, owners, validation
  feeds.py / feedspec.py / views.py / periods.py / api.py / reconcile_map.py
app/static/js/projection/pacing.js            the page (styles injected, tokens only)
tests/projection/pacing/                      160 tests (+ snapshot P&L double, snapshot loader)
```

## What the page shows

Header with the TLDR (finding first, five numbers), a row of six pace cards (revenue, net operating income, ad spend, cost per new customer, new-customer return on ad spend, payback; green at or above the on-pace line, amber to the watch line, crimson below, inverted for costs, each with icon and word), the month table (pace bar, month to date, last 7-day average, daily pace needed, on-plan to date, goal, month-end projection*), payback box, intraday Meta spend projection, a collapsible Variables panel, Month Snapshot for the picked month, trends by day / week / month (band toggles, change from the prior period, Meta campaign view, copy as text), and one freshness chip per feed. At 390px the tables scroll inside their boxes; the page never scrolls sideways.

## Inputs and outputs

- Upstream (engine): `pnl` (goals by P&L sheet row through `ROW_TO_KEY`), `drivers` (spend, new-customer figures, sales-tax lever), `cohorts` (`curve_live`, `curve_goal`).
- Output: `PacingResult(as_of, data_through, month, summary, automated, snapshot, rollup_month|week|day)`. `summary` is the month table for the default month; the last four fields are the reconcile layout and are filled only when `PROJECTION_PACING_FULL=1`.
- `engine.run` loads only the running month and the last days (a few small queries); the API loads wider frames per request. `PROJECTION_AS_OF=YYYY-MM-DD` pins "today" (the sheet's `TODAY()` was 2026-11-03 when the snapshot was read); the default is the Pacific date.

| endpoint | returns |
|---|---|
| `GET /month?month=&scenario=&as_of=` | month table rows, TLDR, payback, month list, freshness chips |
| `GET /snapshot?month=` | Actual / Projected* / Target / Diff / Diff % and payback |
| `GET /rollup?grain=day\|week\|month&from=&to=&view=rollup\|meta&bands=&delta=1` | periods as columns, metrics as rows, plan block (month grain) |
| `GET /catalog`, `GET /freshness`, `GET /intraday?spent=&time=` | metric list, feed age, projected end-of-day Meta spend |
| `GET/PUT /levers` | variables of a scenario; PUT `{scenario, levers:{key: value\|null}, expected_version?}` is validated, audited, versioned |
| `GET/PUT /rules` | feed classification rules (all scenarios), audited |

## Feed dependencies (read-only from the sheet's raw tabs, plus the app's own tables)

Sheet `1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI`, read through `GOOGLE_SA_JSON` (read-only scope). Every feed is written by the sheet's sync routines daily; each should reach yesterday. Staleness at the snapshot (2026-11-03 evening):

| feed key | tab and columns used | through |
|---|---|---|
| orders | `Orders - History` A date, B new-customer revenue, C returning revenue, D new customers, E total revenue, F orders, G tax, H refunds | Nov 2 |
| amazon | `Amazon - Daily` A date, B orders, C units, D revenue, E new orders, F new revenue, G ad spend, H attributed sales, I clicks, J impressions | Nov 3 (partial day; the engine ignores today) |
| platform | `Platform - History` A date, B platform, C spend, D purchases, E purchase value, F-G new-customer purchases, H link clicks, I clicks | Nov 2 |
| attribution | `Attribution - History` A date, B model (last_click), C ad source, D new customers, E new revenue, F orders, G revenue | Nov 2 |
| traffic | `Traffic - Daily` A date, B new visitors, C visitors, D sessions | Nov 3 (partial) |
| meta_reach | `Meta Account - Reach` A grain, B period key, C-D period, G impressions, H reach, J unique link clicks, K link clicks, L unique outbound clicks (both ad accounts summed) | Nov 3 |
| product_reach | `Meta Product - Reach` A grain, B key, C-D period, E product, F unique outbound clicks (from 2026-07-05) | Nov 3 |
| nc_product | `NC Rev - By Product` A date, B product, C new-customer revenue, D new customers | Nov 3 |
| anyclick | `Upstack Any-Click - By Campaign` A date, E stage, F new customers, G new revenue | Nov 2 |
| bids | `Meta Bids - Daily` A date, I spend, L capped spend, M spend x cap, N floor spend, O spend x floor | Nov 3 |
| meta_ad_daily | the app's `ad_daily`, `meta_ads`, `meta_campaigns` (spend, impressions, link clicks, purchases, value by ad and day; refreshed by `etl.run_daily` just before this module) | Nov 2 |
| (history, loaded once) | `Meta Campaign - History` A-K and `Meta Spend - By Product` A-E for days before `ad_daily` begins (2026-05-06); `Automated Pacing` L23:M46 for the hourly curve | n/a |

The Meta numbers from `ad_daily` match the sheet's `Meta Campaign - History` to the cent on every complete day of the snapshot; purchases and purchase value differ by up to 1.2% on the last two weeks because Meta restates them days later and the two pulls read at different times.

## Daily refresh

`etl/projection_pacing.py` exposes the hooks `etl/projection_daily.py` calls:

- `refresh(conn, days=14) -> {"rows", "data_through", "feeds"}`: reads every feed tab first (a missing, unreadable or empty tab raises `FeedError` naming the tab and nothing is written), then replaces the trailing `days` of each feed table (a table that is still empty loads in full), then rebuilds the Meta aggregates from `ad_daily` for the same window. Idempotent. Commits. The first run also loads the sheet's Meta history once.
- `freshness(conn) -> [{"feed","label","cadence":"daily","data_through","stale_after_days":2}]`: twelve entries (eleven daily feeds and the hourly curve), read from the tables themselves.
- Verified live: `python -m etl.projection_daily --only pacing` ran in 7.6 s against the real sheet (read-only) and reported every feed fresh.
- CLI: `python -m etl.projection_pacing refresh|backfill|seed-hourly [--snapshot-dir DIR]`. Railway needs `GOOGLE_SA_JSON` on the ETL service (as the other sheet readers do); no Upstack, ExpandFi or Klaviyo credentials are used.

## Variables

Every number or rule behind the pacing outputs is a named lever. Scenario levers live in `params`, are edited in the Variables panel of the page (or through `PUT /levers`), are validated, audited in `proj_audit`, snapshot into scenario history (restorable), and `null` restores the sheet's value. Four belong to the drivers namespace (the sales-tax estimate and the three payback inputs, one store shared with Spend plan and Cash & debt; owner column); the Variables page of the app can list them all from `GET /levers`.

| key | unit | default | sheet cell(s) | stored in | moves |
|---|---|---|---|---|---|
| `pace_good` | share of goal | 100% | brief (green at 100% or more; the sheet shows the ratio only) | `pacing.pace_good` | revenue and profit status colors |
| `pace_warn` | share of goal | 90% | brief (amber 90-100%, red below) | `pacing.pace_warn` | same, the red line |
| `cost_warn` | share of goal | 110% | brief (costs read inversely) | `pacing.cost_warn` | cost and cost-per-customer status colors |
| `early_days` | days | 5 | Automated Pacing day-1 renewal skew | `pacing.early_days` | the early-month notice |
| `last_days` | days | 7 | Automated Pacing D (TODAY()-7) | `pacing.last_days` | the last-days average column |
| `data_lag_days` | days | 1 | Automated Pacing B54 (DAY(TODAY())-1) | `pacing.data_lag_days` | days elapsed, data-through date, default month, feed age |
| `rates_month` | month | empty = month being paced | P&L column B (trailing rates) | `pacing.rates_month` | cost rates of the daily EBITDA estimate |
| `below_ebitda_rows` | P&L rows | 62, 94 | Automated Pacing G31, G32 | `pacing.below_ebitda_rows` | EBITDA goal and the items below it |
| `cost_drivers` | map | {} (the sheet's definitions) | Automated Pacing rows 13-29 | `pacing.cost_drivers` | each cost line as rate on Amazon / DTC / total revenue or fixed |
| `payback_bad_months` | months | 2 | page convention | `pacing.payback_bad_months` | payback status |
| `stale_bad_days` | days | 5 | DESIGN.md | `pacing.stale_bad_days` | feed chip colors |
| `cpa_window_days` | days | 28 | skeptic review (no sheet cell) | `pacing.cpa_window_days` | the 'last N days' cost per new customer beside the month's |
| `rollup_days`, `rollup_weeks` | days, weeks | 60, 26 | Pacing - Daily window | `pacing.rollup_*` | default trend windows |
| `sales_tax_rate` | share of DTC revenue | 7% | P&L row 21 `=-(Y18*0.07)` | `drivers.sales_tax_rate` | tax estimate in revenue pacing and plan-basis revenue |
| `product_cost_pct` | share of order value | 17.26% | Payback!D11 | `drivers.payback.product_cost_pct` (the one store; `financing.payback.*` is a legacy read-only fallback) | payback box, snapshot payback, and the same cell on Cash & debt and Spend plan |
| `fulfillment_per_order` | dollars | 5.53 | Payback!B12 | `drivers.payback.fulfillment_per_order` | same |
| `payback_horizon` | months | 36 | Automated Pacing M9 (Curves rows 4-39) | `drivers.payback.horizon_months` | the "n+" cut-off |

Feed rules (all scenarios, applied when the ETL loads a day; `proj_pacing_setting`, `GET/PUT /rules`): `stage_keyword` ("test"), `product_rules` (campaign groups) and `product_level_rules` (spend by product), defaults the sheet's regular expressions.

One test per class proves a change moves the outputs (`tests/projection/pacing/test_pacing_variables.py`; the fix-wave behaviors are in `test_pacing_fixwave.py`): thresholds (`test_status_thresholds_move_the_colors`), early notice, recent window, lag (`test_days_elapsed_rule_follows_the_lag`), rates month, rows below EBITDA, cost drivers, sales tax, payback inputs, trend windows, payback and feed-age bands, feed rules (`test_feed_classification_rules_change_the_loaded_days`), plus the API contract (validation, audit, version, reset, owner namespaces) and a browser test that edits a lever in the panel and watches the card change.

Constants that are not business variables: the 150% scale of the pace bar (a drawing range), request caps (`MAX_PERIODS`), the 60-month axis, and the P&L row numbers that define which lines are costs (structure, shown in `targets.COST_LINES`; the driver of each is overridable above).

## Fix wave (2026-11-04)

- **Hourly curve.** `proj_pacing_hourly` was never loaded in production. The daily refresh now loads it the first time it runs with the table empty (a failure to read the tab is a warning, never a stop), and `freshness()` lists it as the feed `hourly_curve` (monthly-upload, flagged "no data yet" until loaded). Seed by hand: `railway run python -m etl.projection_pacing seed-hourly` on the ETL service, or with `DATABASE_URL=<live url> GOOGLE_SA_JSON=<service account json> python -m etl.projection_pacing seed-hourly` (reads the sheet's Automated Pacing L23:M46 read-only; prints `{"hours": 24}`). Verified here against the live sheet: 24 rows, $453,210 over 30 days, projected end of day $8,505 at 7:52 PM with $7,528 spent. Until it is loaded the card no longer dead-ends: it projects from an even-day curve, marks the answer ESTIMATE, and says how to load the curve.
- **Payback inputs, one store.** Product cost share, fulfillment per order and horizon are `params['drivers']['payback']`; pacing reads and writes those keys (names and UI unchanged), reads `financing.payback.*` only as a legacy fallback, and an edit in Pacing is what Cash & debt (`GET /financing/payback`, `/financing/inputs` `payback_sources`) and the Spend plan page (`GET /drivers/levers`) read next; `test_editing_the_payback_inputs_in_pacing_moves_cash_and_debt_and_the_spend_plan` proves it both ways and that a write to the legacy key is ignored. Test databases apply migrations 160 and 161.
- **Payback two ways, one source.** Payback box, pace card and TLDR show "on product margin" (product cost and fulfillment only, the sheet's Payback tab: 10 months, goal 5) and "after all variable costs" (18 months, goal 8), leading with the loaded figure. Both come from the financing module's own functions (`calc.payback(..., fully_loaded=True, loaded_rate=...)`) and the loaded rate from `model.loaded_rate_info(params, pnl)`: the P&L's product cost, fulfillment, inbound shipping, payment processing and chargeback lines over its first 12 projected months, 32.4% of order value on the snapshot P&L (a typed `financing.payback.loaded_rate` replaces it; if a line is missing the box says so and shows no loaded figure rather than a lower cost). Pacing keeps no payback arithmetic of its own, so Pacing and Cash & debt cannot drift apart: `test_the_pacing_payback_box_equals_cash_and_debts_payback` asks `GET /financing/payback` for the same scenario with the pacing box's AOV and CPA (month to date on the live curve, plan on the goal curve) and requires the same months, margins and rate on both bases. The earlier local version read 19 months from a rate of the paced month; the difference was the rate window and the curve rounding.
- **Degraded upstream.** `degraded.ensure_ok(res, "pacing")` after every engine run: with the P&L, drivers or cohorts degraded, `/month`, `/snapshot` and `/rollup` answer 503 `upstream_degraded` (the shell shows its banner and plain words), never goals of zero. `compute` requires `pnl`, so the engine marks pacing degraded too.
- **Month state.** Every month payload carries `state`: `books` (the books hold it), `feeds` (complete in the daily feeds, not yet in the books), `projected`; shown as a chip beside the month picker and the snapshot heading. It uses `actual_through` from the engine until proj-drivers and proj-pnl publish their own three-state helper.
- **Cost per new customer.** The cost-per-customer pace rows show the last 28 days (lever `cpa_window_days`) beside the month to date: $111.17 against $105.50 and a goal of $95. Only for the month holding the last complete day.
- **Clock.** Every path reads the Pacific date from `periods.today_pacific()` (uses proj-core's `clock.today_pt()` when that file exists): `as_of`, `load_inputs`, `/intraday`, the page's time field. The engine is handed that date, so its own `date.today()` is never used by pacing. Test: 06:10 UTC on Nov 4 reads Nov 3.
- **Validation.** `as_of` outside 2025-2029, `from` after `to`, unknown bands and views are 400. The pacing variable group has a core `validate_params` hook, so a generic scenario PATCH of `params.pacing` is held to the same rules as `PUT /levers`.
- The first edit of a session on the plan of record now opens the shell's "edit a copy or the plan of record" choice; the Variables panel goes through it like every other edit.

## Differences from the sheet

1. **Projection of a running period.** The sheet's rollup projected with `DAY(period end)` as the multiplier, so a running month projected to its month to date. The app uses days with data and days in the month (`rollup.project`). The sheet's Month Snapshot already did this right.
2. **Pace and status.** Pace is projection / goal for every row (the sheet inverted it for the two cost-per-customer rows). Status reads the direction of the metric and the thresholds above.
3. **The 1st of the month.** The default month is the one holding the last complete day, so on Nov 1 it shows October complete (the sheet divided by zero days).
4. **One EBITDA and one revenue basis on the page.** Month Snapshot and the monthly plan block use the numbers of the month table (revenue net of the estimated sales tax, the basis of the P&L goal). The sheet's snapshot compared gross Shopify revenue plus Amazon with the net goal (Nov: $77,866 against $1,025,894, 6% hot) and took EBITDA from the daily estimate ($14,855 against $16,282 in its own pacing tab). The plan block's new-customer revenue compares the DTC figure with the DTC goal (the sheet compared business-wide new-customer revenue, Amazon included).
5. **Company-wide block.** Excludes today's partial Amazon row (the sheet's C46 and C47 included it, the rest of its table did not).
6. **Daily EBITDA estimate.** Payment processing uses the P&L's row 40 / row 18 rate (4.136%); the sheet's Daily tab used a typed 4.1%. EBITDA and NOI gap lines differ 0.5-2% for this reason. Rates come from the pnl module's cost lines, whose window is the last closed months, not the sheet's hard-wired Apr-Jul.
7. **Spend by product.** Classified by campaign name then ad set name (no ad-name fallback), from the app's tables; identical to the sheet's `Meta Spend - By Product` on the snapshot days.
8. **Added**: a refunds memo row (see open items), an editable variables panel, freshness chips per feed, per-metric tooltips, change-from-prior-period and the Meta campaign view as a toggle, a lever for every constant.

## Reconciliation

`PROJECTION_AS_OF=2026-11-03 PROJECTION_PACING_FULL=1 DATABASE_URL=... PYTHONPATH=. python scripts/projection/reconcile.py --module pacing` against a database holding the real cohorts, drivers and pnl modules seeded from the same snapshot: 409 lines, 389 within 0.5% or $1, 20 over, none unresolved. Lines: Automated Pacing B-H rows 4-50 (47 cells each), Month Snapshot B-D rows 7-28, and 128 metrics for Oct 2026 (month), 128 for 2026-W44 (week) and 143 for Nov 2 (day). The over-tolerance cells, by cause:

| cause | cells |
|---|---|
| Upstream: the cohorts module's repeat-revenue goal for Nov is $684,038 against the sheet's $674,650 (+1.4%) | `eom_goal`, `mtd_should_be`, `daily_needed` for rows 4, 5, 7 (revenue, DTC revenue, repeat revenue) |
| Upstream: the pnl module's net operating income goal is 2.4-2.6% above the sheet's (same repeat revenue, plus its own cost-rate window) | `noi_target`, `ebitda_target`, `ebitda_gap`, `noi_gap` on all three grains; Automated Pacing C35 and C36 (required daily EBITDA and extra revenue) |
| Difference 4 above | Month Snapshot actual / projected for revenue (6.1%) and EBITDA (8.7%); total ad spend 0.5% (the TikTok plan is included) |
| Difference 5 above | Automated Pacing rows 46 and 47 (company new-customer revenue and customers) |

With the snapshot's own P&L as the plan (the test double) every cell of the Automated Pacing table matches except those company-wide cells, and every rollup metric matches on Oct 2026, W42-W44 and four single days, except what the snapshot cannot hold: it keeps only the last 400 rows of the bids and any-click tabs (those metrics are checked on the day grain only), Meta purchases restated after the sheet read them (W43: 0.9%), and the payment-rate effect on the gap lines.

## Tests

`PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/pacing -q` (own database `tagexp_proj_pacing_t`, snapshot loaded through the real loaders): 130 tests. Periods and calendar (8), catalog and classification (9), fix wave (29: payback two ways and equal to Cash & debt, cost-per-customer context, month state, hourly curve, degraded plan, clock, query validation), loaders and refresh (11: parsers, idempotent refresh, trailing-window replacement, empty-tab error, freshness, `ad_daily` aggregates, app-over-sheet precedence, dict and tuple rows), rollups against the sheet (13), month pacing, snapshot, payback, intraday (29), API and wiring (20), variables (33), browser (8: render with a clean console, month picker, trend controls, intraday box, 390px light and dark, variables panel edit and refusal).

## Open items

- **Decision-relevant: the plan's revenue ignores refunds.** Refunds are 11% of DTC revenue in the first two days of November ($7,521 of $67,568), so revenue on a net-of-refunds basis paces about 101% of goal, not the 106% shown. The page shows the refunds row and keeps the sheet's basis; which basis the goal is meant to be on is Josh's call (the QuickBooks history is net of refunds).
- Month Snapshot's retention comparison block (J5:Q19, goal against the live curve as it read at the end of the month) is not built: it needs the cohorts table by cohort row.
- The hourly Meta spend curve (24 typed values) is loaded once by the daily refresh or `seed-hourly` and has no editor on the page.
- First deploy: `GOOGLE_SA_JSON` on the ETL service and `python -m etl.projection_daily` after `etl.run_daily` (the pacing hook loads the Meta history from the sheet once).
