# Pacing (module `pacing`, migration 170)

What it replaced: the sheet tabs `Automated Pacing`, `Month Snapshot`, `Pacing - Daily (180d)`, `Rollup - Weekly (WoW)`, `Rollup - Monthly (MoM)`, `Meta - Daily (180d)`, `Meta - Weekly (WoW)` and `Meta - Monthly (MoM)`. Read-only consumer of the engine (scenario P&L goals, cohort curves) and of daily actual feeds; nothing downstream reads it. Page: `/projection/pacing`. API: `/api/projection/pacing/...`.

## TLDR

The month table, the snapshot and the day / week / month rollups reproduce the sheet cell by cell from the snapshot: 389 of the 409 reconcile lines pass with the real upstream modules, and every remaining difference is listed below with its cause. 130 pacing tests plus 53 design tests pass. Every number or rule that drove the sheet is now a named variable (17 scenario levers, 3 feed rules) with the sheet's value as the default.

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
tests/projection/pacing/                      130 tests (+ snapshot P&L double, snapshot loader)
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
- `freshness(conn) -> [{"feed","label","cadence":"daily","data_through","stale_after_days":2}]`: eleven entries, read from the tables themselves.
- Verified live: `python -m etl.projection_daily --only pacing` ran in 7.6 s against the real sheet (read-only) and reported all eleven feeds fresh.
- CLI: `python -m etl.projection_pacing refresh|backfill|seed-hourly [--snapshot-dir DIR]`. Railway needs `GOOGLE_SA_JSON` on the ETL service (as the other sheet readers do); no Upstack, ExpandFi or Klaviyo credentials are used.

## Variables

Every number or rule behind the pacing outputs is a named lever. Scenario levers live in `params`, are edited in the Variables panel of the page (or through `PUT /levers`), are validated, audited in `proj_audit`, snapshot into scenario history (restorable), and `null` restores the sheet's value. Four belong to other modules and are stored in their namespace (owner column); the Variables page of the app can list them all from `GET /levers`.

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
| `rollup_days`, `rollup_weeks` | days, weeks | 60, 26 | Pacing - Daily window | `pacing.rollup_*` | default trend windows |
| `sales_tax_rate` | share of DTC revenue | 7% | P&L row 21 `=-(Y18*0.07)` | `drivers.sales_tax_rate` | tax estimate in revenue pacing and plan-basis revenue |
| `product_cost_pct` | share of order value | 17.26% | Payback!D11 | `financing.payback.product_cost_pct` (reads `drivers.payback` as a fallback) | payback box, snapshot payback |
| `fulfillment_per_order` | dollars | 5.53 | Payback!B12 | `financing.payback.fulfillment` | same |
| `payback_horizon` | months | 36 | Automated Pacing M9 (Curves rows 4-39) | `financing.payback.max_age` | the "n+" cut-off |

Feed rules (all scenarios, applied when the ETL loads a day; `proj_pacing_setting`, `GET/PUT /rules`): `stage_keyword` ("test"), `product_rules` (campaign groups) and `product_level_rules` (spend by product), defaults the sheet's regular expressions.

One test per class proves a change moves the outputs (`tests/projection/pacing/test_pacing_variables.py`): thresholds (`test_status_thresholds_move_the_colors`), early notice, recent window, lag (`test_days_elapsed_rule_follows_the_lag`), rates month, rows below EBITDA, cost drivers, sales tax, payback inputs, trend windows, payback and feed-age bands, feed rules (`test_feed_classification_rules_change_the_loaded_days`), plus the API contract (validation, audit, version, reset, owner namespaces) and a browser test that edits a lever in the panel and watches the card change.

Constants that are not business variables: the 150% scale of the pace bar (a drawing range), request caps (`MAX_PERIODS`), the 60-month axis, and the P&L row numbers that define which lines are costs (structure, shown in `targets.COST_LINES`; the driver of each is overridable above).

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

`PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/pacing -q` (own database `tagexp_proj_pacing_t`, snapshot loaded through the real loaders): 130 tests. Periods and calendar (8), catalog and classification (9), loaders and refresh (11: parsers, idempotent refresh, trailing-window replacement, empty-tab error, freshness, `ad_daily` aggregates, app-over-sheet precedence, dict and tuple rows), rollups against the sheet (13), month pacing, snapshot, payback, intraday (29), API and wiring (20), variables (32), browser (8: render with a clean console, month picker, trend controls, intraday box, 390px light and dark, variables panel edit and refusal).

## Open items

- **Decision-relevant: the plan's revenue ignores refunds.** Refunds are 11% of DTC revenue in the first two days of November ($7,521 of $67,568), so revenue on a net-of-refunds basis paces about 101% of goal, not the 106% shown. The page shows the refunds row and keeps the sheet's basis; which basis the goal is meant to be on is Josh's call (the QuickBooks history is net of refunds).
- Month Snapshot's retention comparison block (J5:Q19, goal against the live curve as it read at the end of the month) is not built: it needs the cohorts table by cohort row.
- Payback inputs exist twice in the app (`drivers.payback` and `financing.payback`); pacing reads financing first. One of them should go (see QUESTIONS_pacing.md).
- The hourly Meta spend curve (24 typed values) is loaded by `seed-hourly` and has no editor on the page.
- First deploy: `GOOGLE_SA_JSON` on the ETL service and `python -m etl.projection_daily` after `etl.run_daily` (the pacing hook loads the Meta history from the sheet once).
- `PUT /levers` on `financing.payback.*` bypasses the financing module's own validator; the ranges in `levers.py` are the same as its defaults require.
