# Drivers (page "Spend plan")

What it replaced: `P&L` rows 11-16 (ad spend, new-customer revenue, orders, cost per acquisition, average order value, return on ad spend), rows 19-20 (Amazon and TikTok revenue plan), rows 50-56 (marketing spend and its channel split), the typed override rows 7 and 10, and the revenue stack of rows 17, 18, 21 and 22 (repeat, direct-to-consumer, sales tax, total company revenue) as a function the P&L calls. This module owns the levers that make the plan a growth or harvest case: the spend plan.

Short names used below: NC = new customer, CPA = cost per acquisition (ad spend per new customer), AOV = average order value (first-order revenue per new customer), ROAS = return on ad spend, DTC = direct-to-consumer.

## Files

```
migrations/120_proj_drivers.sql             view proj_meta_spend_monthly (ad_daily by month, with days present)
app/projection/drivers/__init__.py          MODULE (key drivers, ui drivers.js), exports revenue_lines
app/projection/drivers/model.py             engine (pure): compute_drivers(params, DriverInputs) -> DriversResult; levers; plan/override keys
app/projection/drivers/revenue.py           revenue_lines(): repeat, DTC, sales tax, total company revenue (called by pnl)
app/projection/drivers/payback.py           payback hint (same arithmetic as the sheet's Payback tab)
app/projection/drivers/patterns.py          flat / step / growth / ramp generators and bounds
app/projection/drivers/loaders.py           load_inputs(conn, scn) and the source priorities
app/projection/drivers/table.py             /table payload, cell codes, headline summary
app/projection/drivers/api.py               blueprint at /api/projection/drivers
app/projection/drivers/reconcile_map.py     P&L rows 11-16, 18-22, 51-56
etl/projection_drivers.py                   seed from the sheet snapshot, freshness(conn), refresh(conn, days)
app/static/js/projection/drivers.js         the page
tests/projection/drivers/                   227 tests
```

## How it works

Per month, actual or planned:

- **Month status, three words on every page:** `books` = closed month with QuickBooks loaded (`pnl.mkt_*` from the upload, else the seeded `spend.*`); `feeds` = closed month with no books yet but cohort-table customers or a complete month of Meta spend from `ad_daily` (Meta exact, the other channels estimated from the channel mix lever); `projected` = everything else, and the open month always. `DriversResult.month_status`, `books_through` and `feeds_through` carry it; `actual_through` equals `books_through` (Jul 2026 today), so every grid splits actual and projected exactly where the P&L does. The Spend plan page says it in chips and in the source line ("Books through Jul 2026. Feeds only, no books yet: Aug 2026 to Oct 2026. Projected from Nov 2026*.") and in a Month status row.
- **Planned month**: marketing spend (P&L row 51, all channels) is the typed cell, else the month before carried forward; it is split over Amazon ads / Meta / Google / Bing / TikTok by the channel mix lever. Ad spend (row 11, the CPA denominator) is the channels chosen by the `ad_spend_channels` lever (Meta and Google).
- **New customers**: typed override > cohort table (closed months) > plan. Plan: orders = ad spend / CPA, revenue = orders x AOV. CPA and AOV are typed per month, else the month before carries; an optional NC-ROAS slide sets CPA = AOV / ROAS across a month range.
- **Amazon and TikTok revenue**: loaded actuals for closed months, else the typed plan, else carried.
- **Revenue stack** (`revenue_lines(drivers, repeat_rev, dtc_actual)`, needs the cohort repeat revenue so it runs after cohorts): repeat = typed override else cohorts; DTC = books value for closed months else repeat + NC revenue; sales tax = 0 for books months, else -(rate x DTC) (negative); total = DTC + Amazon + TikTok + tax. The pnl module calls it; the Spend plan page calls it; both tie to the cent.

Effective levers carry forward: the month after a measured month (cohort or override) carries the implied CPA and AOV, not the stale typed one.

## Inputs

| Source | Keys | Notes |
|---|---|---|
| `proj_series`, scenario, kind `input` | `drivers.spend_total`, `drivers.nc_cpa`, `drivers.nc_aov`, `drivers.amazon_rev`, `drivers.tiktok_rev` | the spend plan; core `PUT /series`, patterns, or the grid |
| `proj_series`, scenario, kind `override` | `drivers.ad_spend`, `drivers.nc_orders`, `drivers.nc_rev`, `drivers.repeat_rev` | typed over a computed value |
| `proj_scenarios.params.drivers` | levers (see Variables) | `PUT /drivers/levers` or core `PATCH /scenarios` |
| `proj_actuals` | `pnl.mkt_amazon_ads / facebook_ads / google_ads / bing_ads / tiktok_ads`, then `spend.amazon_ads / meta / google / bing / tiktok`; `spend.meta_feed`; `pnl.amazon_rev / tiktok_rev / dtc_rev`, then `amazon_rev / tiktok_rev / dtc_rev`; fallback `nc.orders / nc.rev` | `pnl.*` (QuickBooks upload) beats the seeded keys month by month |
| `proj_cohorts` | `new_customers`, `first_order_per_customer` | read directly (the cohorts module's table); `nc.*` is used only when the table is empty |
| `ad_daily` via `proj_meta_spend_monthly` | monthly Meta spend | rolled into `spend.meta_feed` by `refresh` |

Source per month shows in the grid ("Spend source": Books, Meta feed, Plan) and each cell is coded typed / carried / slid / actual (blue "Carried" marker, `cellmeta` in the payload).

## Outputs (`DriversResult`, a subclass of `DriverResult`)

Interchange fields, all 60 months: `spend_total` (**P&L row 51, all channels**, every month), `spend_meta`, `spend_google`, `spend_amazon_ads`, `spend_tiktok`, `nc_cpa`, `nc_aov`, `nc_orders`, `nc_rev`, `nc_roas`, `amazon_rev`, `tiktok_rev`, `actual_through`. `nc_cpa`, `nc_aov` and `nc_roas` are the implied values (ad spend / orders, revenue / orders, revenue / ad spend), so they always agree with orders and revenue.

Extra: `spend_bing`, `month_status`, `books_through`, `feeds_through`, `marketing_spend` (= `spend_total`), `ad_spend` (row 11), `plan_cpa` / `plan_aov` (the effective levers), `sales_tax_rate`, `repeat_override` (typed repeat months), `dtc_actual`, `nc_rev_cohort` (what the cohort table says, shown beside typed values), `source`, `carried`, `slid`, `nc_basis` (`override | cohort | plan` per month), `levers`, `cell_info`. `sales_tax`, `dtc_rev`, `repeat_rev`, `total_revenue` are empty until `revenue_lines()` attaches them (cohorts run after drivers); use its return value.

## API (`/api/projection/drivers`)

| method and path | body / query | returns |
|---|---|---|
| `GET /table?scenario=` | | grid payload in the shell's vocabulary (`kind` input / calc / subtotal / total / header / ratio, `fmt` usd / usd0 / num / x / text), plus `cellmeta`, `summary` (next / previous window spend, posture growth / harvest / hold, NC-ROAS, payback, FY table), `levers`, `scenario`, `actual_through` |
| `PUT /series` | `{scenario, items:[{line_key, month, value\|null, kind}], expected_version?}` | the core `PUT /series` for Spend plan lines only, with the bounds and the closed-month refusal below; the page saves through it |
| `GET /levers?scenario=` | | effective levers and the defaults |
| `PUT /levers` | `{scenario, levers:{...}, expected_version?}` | validated merge into `params.drivers` (audited, versioned) |
| `POST /patterns` | `{scenario, target, pattern, from, to, preview?, expected_version?}` | preview: before / after for the range (spend, NC revenue, NC-ROAS, CPA, payback), nothing written; apply: writes the cells (one audit row per cell, one version) |
| `POST /undo` | `{scenario, version, expected_version?}` | puts back exactly the cells (and NC-ROAS slide) the change at `version` wrote (a range fill returns that version), as one audited, versioned change; cells edited again since are left and counted in `skipped` |
| `GET /compare?scenarios=a,b` | up to 6; the first is the viewing scenario | monthly series and FY numbers per scenario, plus `vs_view` (viewing scenario minus the other, so more spend in the scenario you are in reads positive) |

Patterns: targets `spend_total`, `nc_cpa`, `nc_aov`, `amazon_rev`, `tiktok_rev` with `flat {value}`, `step {value, then_value, step_month}`, `growth {value, rate, cap?}`, `ramp {value, end_value}`; target `nc_roas` with `roas_slide {start, end}` (sets the lever for the range). Closed months with loaded actuals are refused (400 naming the months). Edits to plan cells go through the core `PUT /series`; auth, Origin check, JSON only, rate limits and noindex are the core's.

## The page

Header, purpose line (expands CPA and AOV), TLDR strip (spend next 12 months and change on the last 12, NC revenue, NC-ROAS, payback, total revenue), a scenario cue (Plan of record chip, or amber "Scenario copy" with "Clone to try a change"), chart (spend, NC revenue, repeat revenue), the grid (yellow rows = the plan; "Typed over the model" rows; NC-ROAS and payback sit directly under the inputs), "Fill a range" (line, from, to, shape, live before / after preview, then Apply), Plan settings, Compare scenarios (view only, directly under the grid; a copy opens with its base already picked, rows read "Bull compared with Current plan: +$60K (+1%)"). After "Fill a range" the toast, the cue strip and Ctrl+Z all offer Undo, which restores exactly the cells the fill changed. The copy cue names its base scenario. Edits to closed months are refused with a plain message. Checked at 1440 and 390 px, light and dark; console clean.

## Variables (every input, named, scenario-aware, with its sheet default and source)

Levers live in `proj_scenarios.params.drivers` and are set with `PUT /api/projection/drivers/levers` (Plan settings card) or `PATCH /api/projection/scenarios/<slug>`. Defaults are seeded into "Current plan" by `etl/projection_drivers.py seed`.

| Variable | Unit | Default | Sheet cell(s) | Set where | Proof a change moves outputs |
|---|---|---|---|---|---|
| `channel_share.amazon / meta / google / bing / tiktok` | fraction, sum 1 | 0.0492 / 0.8971 / 0.0481 / 0 / 0.0056 | `P&L!B52:B56` | levers | `test_drivers_variables::test_a_lever_change_moves_the_output` (channel mix), `test_drivers_model::test_channel_share_lever_changes_the_split_not_the_total` |
| `sales_tax_rate` | fraction of DTC revenue | 0.07 | `P&L!V21:BJ21` (literal `*0.07`), `Assumptions - DEAD!B98` | levers | same test (sales tax, total revenue), `test_drivers_revenue::test_sales_tax_rate_lever_applies` |
| `ad_spend_channels` | list of channels | meta, google | `P&L` row 11 (`=C53+C54`) | levers | `test_ad_spend_channels_lever_in_the_pure_engine`, lever cases |
| `roas_slide.enabled / from / to / start / end` | month range, ROAS | off | none (replaces typed CPA, row 14, with CPA = AOV / ROAS) | levers or `POST /patterns` target `nc_roas` | `test_roas_slide_is_a_lever_too`, `test_drivers_api::test_roas_slide_is_a_lever_that_governs_the_range` |
| `payback.product_cost_pct` | fraction of AOV | 0.1726 | `Payback!D11` | levers | lever cases (payback row) |
| `payback.fulfillment_per_order` | dollars | 5.53 | `Payback!B12` | levers | lever cases (payback row) |
| `payback.horizon_months` | months | 36 | `Payback!E20:E56` (36-month table) | levers | `test_payback_horizon_cuts_the_search` |
| `reference_days` | days, 7 to 90 | 28 | none (app default: window of the read-only feed references) | levers | `test_drivers_reference::test_the_window_length_is_a_lever` |
| `reference_tolerance` | fraction | 0.05 | none (app default: a typed plan beyond 5% of its feed reference gets a note and a warn chip) | levers | `test_the_chip_is_quiet_inside_the_tolerance` |
| `posture_band` | fraction | 0.10 | none (app default: change beyond +-10% reads as growth / harvest) | levers | `test_summary_window_and_band_lever_change_the_headline` |
| `summary_months` | months | 12 | none (app default: the headline compares the next 12 months with the last 12) | levers | same test |
| `drivers.spend_total` (monthly) | dollars | $350,000 Oct-26 to Dec-29, $847,183 Aug-26, $546,201 Sep-26 | `P&L` row 51 (typed from Aug-26) | grid, patterns, core `PUT /series` | `test_a_plan_series_cell_moves_the_output` |
| `drivers.nc_cpa` (monthly) | dollars | $95 Nov-26 on, $100 Oct-26, $95 Sep-26 | row 14 (typed from Sep-26) | same | same |
| `drivers.nc_aov` (monthly) | dollars | $72 Sep-26, $69 Nov-26 on; Oct-26 carries Sep (a formula in the sheet) | row 15 | same | same |
| `drivers.amazon_rev` (monthly) | dollars | $218,012 Aug-26, $196,807 Sep, $192,711 Oct, $175,000 Nov-26 on | row 19 (typed from Aug-26) | same | same |
| `drivers.tiktok_rev` (monthly) | dollars | $2,000 Aug-26, then 0 (blank in the sheet) | row 20 | same | same |
| `drivers.ad_spend` (override) | dollars | Jul-26 $794,965, Aug $847,183, Sep $546,201 | row 11 (typed) | grid | `test_an_override_replaces_the_computed_value` |
| `drivers.nc_orders` (override) | orders | Aug-26 8,375 | `P&L!V13` | grid | same |
| `drivers.nc_rev` (override) | dollars | Aug $515,139.33, Sep $401,590, Oct $240,354.88 | `P&L!V12:X12` | grid | same |
| `drivers.repeat_rev` (override) | dollars | Jul-26 $944,173.73, Aug-26 $984,286.67 | `P&L!U17`, `V17` | grid | same |
| actual spend by channel | dollars | Jan-25 to Jul-26 from the sheet | `P&L` rows 52-56 (QuickBooks values) | QuickBooks upload (`pnl.mkt_*`), seed (`spend.*`), Meta feed (`spend.meta_feed`) | `test_a_loaded_actual_moves_the_closed_month` |
| actual Amazon / TikTok / DTC revenue | dollars | Jan-25 to Jul-26 | rows 18-20 | QuickBooks upload (`pnl.*`), seed | same |

Not variables on purpose (technical limits): validation ranges of the levers and patterns, the 60-month axis, the 500-cell batch cap.

## Validation

One table of bounds (`validation.BOUNDS`): marketing spend 0 to 50,000,000; CPA and AOV above 0 and at most 1,000; Amazon and TikTok revenue 0 to 100,000,000; ad spend override 0 to 50,000,000; NC orders override 0 to 5,000,000; NC revenue and repeat revenue overrides 0 to 100,000,000. A typed plan input (spend, CPA, AOV, Amazon, TikTok) is refused for a closed month that already holds loaded data (400 naming the months); clearing such a cell and typing an override there are allowed. The same checks run on `PUT /drivers/series` (the Spend plan page saves through it, with `expected_version`), on `POST /drivers/patterns`, and as `MODULE.validate_series(conn, scenario, items)`, which the core now calls from every write path that goes through `series.apply_items` (raw `PUT /series` and patterns included) after its own rules (negatives, 0..50M spend, (0,1000] CPA and AOV, and a typed `input` refused for any month before the current Pacific month). The hook also rejects unknown `drivers.*` names. A closed month is corrected with `kind: override`: the page and the pattern endpoint send `override` for months before the open month and `input` from it. Tests: `test_drivers_validation.py`.

## Feed references (read only)

Beside the CPA input row: "Realized CPA" by closed month (ad spend of the channels chosen in Plan settings, from the Meta feed `ad_daily`, the ad-platform feed and the Amazon feed, over Shopify new customers from the orders feed; complete months only) and a chip "Plan $95 vs last 28 days $111" (warn when beyond the reference band). Beneath the Amazon plan row: "Amazon feed run-rate (last 28 days)": the feed's own monthly total for closed months it covers, then one flat run-rate (daily average of the window times the average month length) from the open month on, and a one-line note when a typed Amazon value differs from it by more than the band ("Typed Amazon revenue against the feed: Aug 2026 +5%, Sep 2026 +17%, Oct 2026 +18%, Nov 2026 on +8% (last 28 days)"). The window drops the newest feed day (usually partial) and never reaches today. Nothing is written and no plan value changes. Missing feed tables or empty feeds give no rows and no chip. Tests: `test_drivers_reference.py` (12).

## Daily refresh

`etl/projection_drivers.py` exposes the core runner's hooks (the runner commits; neither hook does):

- `freshness(conn)` returns two feeds: `drivers.meta_spend` (daily, data through the latest day in `ad_daily`, stale after 2 days) and `drivers.books` (monthly-upload, last day of the last month with loaded books or Amazon / TikTok revenue, stale after 45 days).
- `refresh(conn, days=14)` rolls complete, closed months of Meta spend from `ad_daily` into `proj_actuals` as `spend.meta_feed`. It recomputes the months touched by the last `days` days plus any complete month not loaded yet, writes only values that changed (a no-change day writes nothing and adds no audit row), and returns `{"rows": n, "data_through": "YYYY-MM-DD"}`. Months with a missing day are not loaded, so the open month stays a plan month (the pacing module covers the open month). Run order on Railway: `etl.run_daily` (refreshes `ad_daily`), then `etl.projection_daily`.
- Tests: `tests/projection/drivers/test_drivers_hooks.py` (10 tests: complete months only, missing day, idempotence, restated recent month, empty, engine picks up the feed, freshness entries, the core evaluator accepts them).

## Reconciliation

`scripts/projection/reconcile.py --module drivers`, frozen snapshot 2026-11-03, "Current plan" seeded from the sheet, 60 cells per line, tolerance 0.5% or $1 (ratio lines carry a tighter floor: CPA and AOV 0.005, ROAS 0.0001, orders 0.5). Two runs: with the foundation stubs for cohorts and pnl (the sheet's own repeat revenue), and with the real cohorts and pnl modules seeded.

| Line (P&L row) | Stub upstream: max abs / max rel | Real upstream: max abs / max rel | Cells over tolerance |
|---|---|---|---|
| ad spend (11) | 0.00 / 0% | 0.00 / 0% | 0 |
| NC revenue (12) | 0.01 / 0.0000% | 0.01 / 0.0000% | 0 |
| NC orders (13) | 305.66 / 8.46% (Oct-26) | same | 2 (Sep, Oct 2026) |
| CPA (14) | 8.46 / 8.46% | same | 2 (Sep, Oct 2026) |
| AOV (15) | 5.49 / 7.63% | same | 2 (Sep, Oct 2026) |
| ROAS (16) | 0.02 / 2.99% | same | 2 (Sep, Oct 2026) |
| Amazon revenue (19), TikTok revenue (20) | 0.00 / 0% | same | 0 |
| marketing spend (51) | 0.00 / 0% | same | 0 |
| Amazon ads (52), Meta (53), Google (54) | 41,688 / 760,050 / 40,741 (100%) | same | 2 each (Aug, Sep 2026) |
| Bing (55) | 0.00 | same | 0 |
| TikTok ads (56) | 4,704 / 100% | same | 1 (Aug 2026) |
| DTC revenue (18) | 0.00 / 0% | 13,428 / 1.25% | 0 / 8 |
| sales tax (21) | 0.00 / 0% | 940 / 1.25% | 0 / 8 |
| total company revenue (22) | 0.00 / 0% | 12,488 / 1.03% | 0 / 6 |

Every cell over tolerance, with its cause (all pinned by `test_engine_reproduces_the_sheet_and_every_difference_is_known`):

1. **Sep and Oct 2026 new-customer orders, CPA, AOV, ROAS (intended change).** The sheet derives those orders as spend / typed CPA ($546,201 / $95 = 5,749; $330,834 / $100 = 3,308). Both months are closed and the cohort table holds the real counts (5,641 and 3,614), so the engine uses them. Typed NC revenue (an override, unchanged) over real orders gives AOV $71.19 / $66.51 against the sheet's typed $72, CPA $96.83 / $91.54 against $95 / $100.
2. **Aug and Sep 2026 channel split (sheet blank).** The sheet types only the total for those months and leaves rows 52-55 blank (Sep row 56 is a formula). The engine splits the typed total by the channel mix lever. Marketing spend (row 51) ties.
3. **DTC revenue, sales tax and total revenue with the real upstream (inherited).** The difference is the cohorts module's repeat revenue against the sheet's row 17, passed through one for one: Sep-26 (1.7%, the sheet blends the waterfall 50/50 with month-to-date pacing, wired to one column, so the engine does not reproduce it), Oct-26 to May-27 (0.6% to 1.7%, documented in `MODULE_cohorts.md`). With the sheet's own repeat revenue (stub run) all three lines tie to $0.00, including the typed Jul-26 and Aug-26 months.

**Typed in the sheet, kept as overrides** (new-customer revenue Aug-Oct 2026, orders Aug 2026, ad spend Jul-Sep 2026, repeat revenue Jul and Aug 2026; listed in Variables). Three are worth a decision:
- Jul-26 ad spend is typed $794,965; QuickBooks Meta plus Google for the month is $888,015 (11% higher). The sheet's CPA ($78.41) uses the typed number. Which is right?
- Aug-Oct new-customer revenue typed 515,139 / 401,590 / 240,355 against the cohort table 534,185 / 400,643 / 246,904 (+3.7%, -0.2%, +2.7%). Clearing the four overrides in the grid moves the plan to the table (the "Cohort table revenue" row shows both).
- Sep-26 repeat revenue is not carried as an override (the sheet's blend), so Sep differs as in cause 3.

## Interface notes for downstream modules

- `spend_total` is row 51 (all channels), every month; `ad_spend` is row 11. pnl splits nothing itself: use `spend_meta`, `spend_google`, `spend_amazon_ads`, `spend_bing`, `spend_tiktok`.
- Call `from app.projection.drivers.revenue import revenue_lines` and pass the cohorts' `repeat_rev` (and QuickBooks DTC actuals if you hold them); do not re-implement the stack. pnl already does.
- Reconcile rows 18, 21, 22 resolve through `pnl.lines.dtc_rev / sales_tax / total_revenue`; with pnl stubbed they show `stub`.
- Payload vocabulary is the shell's (as cohorts and pnl), not `Table`'s.
- Financing's payback reads `drivers.nc_aov / nc_cpa / actual_through`; the page's payback hint uses the `payback.*` levers above and the live curve (`cohorts.curve_live`), and is not the financing model.

## Open items

- **Per-cell lock for closed months in the grid.** The plan rows are amber in closed months but refuse the edit with a plain message; a row option "editable only in projected months" in `grid.js` would remove the mismatch (QUESTIONS_drivers.md).
- **A growth case that holds CPA flat is the rosiest input.** The plan carries $95 CPA at any spend; more spend usually costs more per customer. The NC-ROAS slide is the lever for it; there is no spend-elasticity lever yet. Say if you want one (CPA rises with spend relative to a base spend).
- Amazon revenue and non-Meta spend actuals arrive by monthly upload (QuickBooks through the pnl page); there is no daily Amazon feed here.
- With `ad_daily` loaded on Railway, `refresh` will make Aug-Oct 2026 feed actual months; typed overrides still win until cleared.
- The Meta feed vs books gap row appears only where both exist; not exercised against production data.
