# pnl: P&L (costs to net operating income)

## What it replaced

Lonvera Projections `P&L` rows 24-102 (costs, gross profit, marketing, other business expenses, headcount, overhead, net operating income) and 134-175 (trailing and forward metrics, growth rates, implied enterprise value), the hidden tabs `COGS`, `COGS Adjustments` (not used by any projected cell; its sales split is superseded by the revenue the drivers module supplies) and `Fixed Expenses` (kept as a read-only reference table), and the QuickBooks history pasted into the P&L (now `proj_actuals`, loaded by an upload page). Revenue rows 11-22 stay with the drivers and cohorts modules; the P&L passes them through (`nc_rev`, `repeat_rev`, `dtc_rev`, `amazon_rev`, `tiktok_rev`, `sales_tax`, `total_revenue`) so downstream modules read one shared vocabulary. Rows 108-131 (cash) and the payout block belong to financing.

## Files

```
migrations/130_proj_pnl.sql                 proj_pnl_drivers, proj_pnl_settings, proj_pnl_cogs_sku, proj_pnl_qb_map, proj_pnl_vendors
app/projection/pnl/__init__.py              MODULE (key pnl, ui pnl.js)
app/projection/pnl/lines.py                 LINE_KEYS, ROW_TO_KEY, SECTIONS, RATIOS (the shared vocabulary)
app/projection/pnl/model.py                 engine: compute_pnl, drivers and modes, COGS model, FY / TTM / FTM / growth / EV (pure, no database)
app/projection/pnl/store.py                 reads (inputs, driver table, QuickBooks map) and writes (map, driver defaults, freshness)
app/projection/pnl/qb.py, xlsx_read.py      QuickBooks upload: parse csv / xlsx, map, closed-month rule, diff, diff hash (standard library only)
app/projection/pnl/views.py, api.py         grid payloads and the /api/projection/pnl blueprint
app/projection/pnl/reconcile_map.py         engine versus the sheet: 147 lines
etl/projection_pnl.py                       seed from the sheet snapshot; freshness hook
app/static/js/projection/pnl.js             the page
tests/projection/pnl/                       tests, one file per layer (+ dev tools reconcile_stub_run.py, shoot_pnl.py)
```

## How a month is computed

`qb_through` is the last closed month with any loaded `pnl.*` actual (the P&L's own actual boundary; today Jul 2026, so Aug-Oct 2026 are closed months without books and are projected). Actual months: every leaf line is its loaded value (blank = no data, counted as 0 in sums, never stored as 0); parents are sums of their children. Projected months: each leaf follows its driver on top of revenue from drivers and cohorts, unless the scenario holds a monthly override (`pnl.<line>`, kind `override`).

A driver is a list of steps ordered by start month; the last step on or before the month applies (`proj_pnl_drivers.segments`, JSON). Modes: `fixed {value}`, `carry` (last month), `avg {window}` (mean of the previous N months), `rate {base, rate_kind, window?, rate?}` and `headcount`. A rate is a line divided by its base (`amazon_rev`, `dtc_rev`, `total_revenue`) and comes from `window` (sum over sum of the last N actual months, default the `rate_window_months` setting = 4), `ratio_avg` (mean of monthly ratios), `typed` or `cogs_model`. The window is a lever with an explicit "as of" (the page and `GET /drivers` print the months). The seeded defaults are the sheet's column B and forward cells; every typed number carries its sheet cell in `proj_pnl_drivers.source`. Scenario edits live in `scenario.params.pnl` (`drivers.<line>.segments` replaces that line's default; `engine`, `headcount`, `cogs`, `ev` merge over the defaults in `proj_pnl_settings`), so every change is versioned, audited and restorable through the core.

Cost of goods sold: `china_cogs = cogs_model rate x total revenue`; the model is blended landed cost per bottle (China and US cost per SKU weighted by sales mix, US share 70%) divided by average price per bottle ($29.22), seeded from the `COGS` tab (12.0799% = `COGS!L44` to the digit).

Totals: amazon fees = 25+26+27+28; cost of goods = China + US COGS; payment processing = shipping supplies + Shopify subscriptions + seller fees; total cost of goods sold = payment processing + inbound shipping + fulfillment + cost of goods + amazon fees + inventory shrinkage; gross profit = total revenue - that; total expenses = marketing spend + marketing + other business expenses + headcount + overhead + uncategorized; net operating income = gross profit - total expenses. Other business expenses is the sum of its sub-lines in actual months and one driven line (rate x total revenue) in projected months. Headcount = base + floor(max(0, annualised revenue - threshold) / step) x increment from the start month (Sep-26, deal close); every number in that formula is a variable (see Variables).

## Interface for downstream modules

`PnlResult` is a subclass, `PnlOutput`, so `isinstance(x, PnlResult)` holds.

- `lines: dict[str, Series]`: a monthly Series on the 60-month axis for EVERY key in `app/projection/pnl/lines.py` `LINE_KEYS` (89 keys: 46 QuickBooks leaves, subtotals, memo ratios, TTM / FTM, growth; 88 have a sheet row, `inventory_shrinkage` has none). `ROW_TO_KEY[sheet_row] -> key` (for example 22 total_revenue, 47 gross_profit, 95 total_expenses, 100 noi, 24 amazon_fees, 31 cogs, 32 china_cogs, 37 fulfillment_services, 38 inbound_shipping, 40 payment_processing, 46 total_cogs, 51 marketing_spend, 59 marketing, 66 other_business_expenses, 75 headcount, 78 overhead, 94 uncategorized). Keys are stable. Blank actual cells are absent from the Series; read a missing month as 0.
- `fy: dict[int, dict[str, float]]`: calendar-year roll-up of every money line (sum of months) and every ratio (recomputed from the sums), plus `ttm_noi` and `ttm_revenue` at December. The headline strip reads `fy[year]['total_revenue']` and `['noi']`. `fy_flat` is the same keyed `'2027.noi'` for the reconcile harness.
- `ttm` = trailing-12-month net operating income Series, `ftm` = month x 12 net operating income Series (the sheet's "FTM" is a run-rate, not a forward sum).
- `actual_through`: the P&L's own actual boundary (last month of loaded QuickBooks data). It can be earlier than `drivers.actual_through`, because the drivers module has Meta and cohort actuals through Oct-26 before the books close.
- `window`, `driver_info`, `cogs_model`, `settings`, `warnings`: what the rate drivers read, each driver's resolved rate or value, the COGS model inputs and result, effective settings, and plain-language warnings (no history, no drivers).
- `ev`: `{'3x': ..., '4x': ..., ... '8x': ..., 'multiples': [3..8], 'basis_year': 2027, 'basis_noi': ..., 'loi': 7500000.0}`. The flat `'<n>x'` keys are what the UI shell headline strip reads (`^\d+(\.\d+)?x?$`); each is full-year net operating income of `basis_year` x the multiple, or `null` when that net operating income is negative. Year, multiples and the offer are settings (`ev.year`, `ev.multiples`, `ev.loi`). `GET /pnl/ev` returns the multiples x scenario table: `{year, multiples, loi, rows:[{slug, name, basis_noi, values:{'3x':v}, vs_loi:{'3x':v - loi}}]}`.
- Inputs it reads from upstream: `drivers.nc_rev`, `amazon_rev`, `tiktok_rev`, `sales_tax` (negative), `marketing_spend` (row 51 semantics, else `spend_total`), `spend_amazon_ads|meta|google|bing|tiktok`, `repeat_override`; `cohorts.repeat_rev`. It calls `drivers.revenue.revenue_lines(drivers, repeat_rev, dtc_actual)` when it exists (same function the Spend plan page uses) with a local fallback of the same rules.
- QuickBooks actuals live in `proj_actuals` under `pnl.<key>` (revenue lines `pnl.dtc_rev|amazon_rev|tiktok_rev`; channel lines `pnl.mkt_amazon_ads|mkt_facebook_ads|mkt_google_ads|mkt_bing_ads|mkt_tiktok_ads`). Other modules may read them; they must not write them.

## API (`/api/projection/pnl`)

| method + path | what |
|---|---|
| `GET /table?scenario=` | the grid payload: 10 collapsible sections, 60 months, FY columns (`fy.rows` supplied), status per month, one caption. Rows are read-only (see QUESTIONS item 1); driven rows carry an "i" note with how they are projected |
| `GET /metrics` | trailing twelve months, run-rate, growth rows |
| `GET /summary` | TLDR numbers: FY basis year revenue / net operating income / margins, implied value range, QuickBooks text ("QuickBooks loaded through Jul 2026; Aug-Oct are projected"), freshness, COGS rate at both prices |
| `GET /ev?scenarios=` , `GET /compare?scenarios=` | multiples x scenario; revenue / gross profit / net operating income by fiscal year per scenario (default: all, max 8) |
| `GET /drivers`, `PUT /drivers` | driver table with resolved rates and the trailing window; `PUT {scenario, key, segments}` replaces a line's driver in that scenario (`segments: null` returns it to the default) |
| `PUT /settings` | `{scenario, settings:{engine?, headcount?, cogs?, ev?}}`, validated, null removes a scenario value |
| `GET /qb-map`, `PUT /qb-map`, `DELETE /qb-map` | QuickBooks account name to line key |
| `POST /upload`, `POST /upload?commit=1`, `GET /uploads`, `POST /upload/undo`, `PUT /upload-checks`, `GET /checks` | (see Fix wave) |
| `POST /upload`, `POST /upload?commit=1` | multipart `file` (.xlsx or .csv, QuickBooks "Profit and Loss by Month"). Preview returns months (new / changed / unchanged), mapped and unmapped accounts, ignored computed rows, skipped months with reasons, changed values old to new, and `diff_sha256`. Commit needs that hash in the form; a different hash is 409 |
| `GET /freshness`, `PUT /freshness` | the QuickBooks feed entry evaluated against today; the lateness threshold (global setting, audited) |

All writes pass the core guard rails (Origin, JSON-only except upload multipart, rate limits, size caps); driver and setting edits are scenario versions (restorable, `scenario.params` audit rows); QuickBooks loads write `actuals.load` and one `pnl.upload` audit row with the file's sha256, byte size, row count, cells and months.

## QuickBooks upload rules

Header row = first row (of 30) with 3+ month labels (`Jan 2026`, `January 2026`, `Jan-26`, `2026-01`, `1/1/2026`, Excel dates). Rows with numbers and a name: `Total ...` rows and Gross Profit / Net Operating Income / Net Income are listed as "calculated here, not loaded"; mapped accounts add into their line; unmapped accounts with numbers are listed first and mapped from the preview (saved in `proj_pnl_qb_map`, audited). Only closed months inside Jan 2025 to Dec 2029 load; every other month column is listed with its reason. Blank cells leave what is loaded untouched. xlsx is read with the standard library (member count and size caps, any DOCTYPE or ENTITY refused); 8 MB cap. Revenue accounts are not pre-mapped (the sheet never recorded the QuickBooks income account names), so the first upload asks for them in the preview.

## Variables

Every input that drives the P&L is a named variable with the sheet's value as its default and its sheet cell as the source. Nothing else in the module code names a business number: the one starting-values table is `DEFAULT_SETTINGS` in `model.py` (used only for a group `proj_pnl_settings` does not hold, with a warning), and `test_no_business_number_is_written_into_module_code_outside_the_starting_values` fails the build if 0.07, 29.22, 21.62, 2027, 7.5M, 20M, 15,000 or 5M appear anywhere else in the module or the page. All scenario variables are `scenario.params.pnl.<group>.<key>` (set from the page's Settings card or `PUT /api/projection/pnl/settings {scenario, settings}`, null returns to the default), versioned, audited and restorable. Tests below are in `tests/projection/pnl/test_pnl_variables.py` unless noted.

| key (`params.pnl.`) | unit | default | sheet cell(s) | set on the page | test |
|---|---|---|---|---|---|
| `engine.rate_window_months` | months | 4 | trailing sums `sum(R:U)` in B25 B26 B28 B32 B37 B38 B43 B44 B63 B66 B80 B86 B91 | Settings: Months behind trailing rates | `test_rate_window_months_moves_every_trailing_rate` |
| `engine.opex_inflation` | % a year | 0 | A4 (referenced by no sheet formula) | Settings: Operating cost inflation | `test_inflation_levers_move_their_lines_only[opex_inflation-accounting]` |
| `engine.cogs_inflation` | % a year | 0 | A5 (likewise) | Settings: Product cost inflation | `test_inflation_levers_move_their_lines_only[cogs_inflation-china_cogs]` |
| `trend.avg_window` | months | 6 | H145 and rows 147 150 152 155 157 (`COUNT(...)=6`) | Settings: Months in growth averages | `test_growth_average_window_is_a_setting` |
| `headcount.base` | $ a month | 15,000 | W75 | Settings: Headcount base | `test_headcount_base_and_increment[base]` |
| `headcount.increment` | $ a month per step | 15,000 | W75 | Settings: Added per step | `test_headcount_base_and_increment[increment]` |
| `headcount.threshold` | $ annual revenue | 20,000,000 | W75 | Settings: Annual revenue before headcount steps up | `test_headcount_threshold_and_step[threshold]` |
| `headcount.step` | $ annual revenue | 5,000,000 | W75 | Settings: Revenue per step | `test_headcount_threshold_and_step[step]` |
| `headcount.start` | first of month | 2026-09-01 (deal close) | first filled cell of row 75 (W) | Settings: Headcount starts | `test_headcount_start_month_moves_when_it_begins` |
| `cogs.price_per_bottle` | $ | 29.22 | COGS!L38 (feeds L36, L44) | Settings: Average price per bottle | `test_cogs_model_variables_move_the_rate_and_the_line[price]` |
| `cogs.current_price_per_bottle` | $ | 21.62 | COGS!L37 | Settings: Current price per bottle (comparison only) | `test_current_price_only_changes_the_comparison_not_the_projection` |
| `cogs.us_share` | share | 0.70 | COGS!N24 (China 0.30 is N23) | Settings: Bottles sourced in the US | `...[us_share]` |
| `cogs.mix.<sku>` | share | 0.50 / 0.25 / 0.25 (Vital Bull, Boneflex, Beef Liver; others 0) | COGS!F4:F15 | Settings: SKU table, Sales mix | `...[mix]` |
| `cogs.china_cost.<sku>` | $ per unit | per SKU, 1.00 to 4.16 | COGS!D4:D15 | Settings: SKU table, China cost | `...[china_cost]` |
| `cogs.us_cost.<sku>` | $ per unit | per SKU, 0 to 5.07 | COGS!D32:D43 | Settings: SKU table, US cost | `...[us_cost]` |
| `ev.year` | calendar year | 2027 | C162:H162 read `$BN$100` | Settings: Value year | `test_ev_year_multiples_and_offer` |
| `ev.multiples` | x | 3, 4, 5, 6, 7, 8 | C161:H161 and the factors in C162:H162 | Settings: Multiples | same |
| `ev.loi` | $ | 7,500,000 | note in A160 | Settings: Offer | same |
| `drivers.<line>.segments` | steps: mode, value, base, rate kind, window, rate, start month | per line, table below | column B and forward cells | Cost drivers: Change | `test_every_driver_mode_is_editable_per_scenario`, `test_a_step_changes_a_line_from_its_month_only` |
| `pnl.<line>` series, kind override | $ for one month | none | typed forward cells | Cost drivers: Change, "Single months typed over" | `test_monthly_override_and_loaded_actual_both_move_the_outputs` |

Not scenario-specific: `proj_pnl_settings.freshness.stale_after_days` (days, 45, the coordinator's rule; Settings: Days before QuickBooks is late, `PUT /pnl/freshness`, audited as `pnl.setting`; `test_freshness_threshold_is_a_setting` in `test_pnl_api.py`). Loaded inputs: QuickBooks values `pnl.<line>` in `proj_actuals` (Upload QuickBooks export; a changed loaded value moves the actual months and every trailing rate, same override-and-actual test) and the account-to-line map `proj_pnl_qb_map` (mapped in the upload preview, `test_qb_map_crud_is_audited`). Reference only, not inputs: vendor retainers (`proj_pnl_vendors`).

Calendar definitions, not assumptions, so not variables: TTM is 12 months, FTM is month x 12, year-over-year compares 12 months back, annualised revenue (headcount) is month x 12.

Not this module's variables (read from upstream): marketing spend and its channel split, new-customer revenue, Amazon and TikTok revenue, repeat revenue and the sales tax rate (drivers and cohorts). The fallback when the drivers module carries no sales tax uses `drivers.sales_tax_rate`, never a constant.

Default per driven line (all editable per scenario; first step shown, later steps after a semicolon):

| line key | default | sheet source |
|---|---|---|
| accounting | $2,500 | P&L!W79 |
| amazon_fba_fees, amazon_seller_fees, amazon_inbound_transport | rate of Amazon revenue, trailing window | P&L!B25, B26, B28 |
| amazon_subscriptions | $39.99 | P&L!W27 |
| chargeback_management, memberships | rate of DTC revenue, trailing window | P&L!B80, B86 |
| china_cogs | COGS model rate x total revenue; Aug-26 mean monthly ratio of the last 3 actual months; COGS model from Sep-26 | P&L!B32, V32 |
| consulting | $0; $8,000 from Oct-26 | P&L!X81 |
| customer_service | $8,500 | P&L!W82 |
| fulfillment_services, shopify_subscriptions, seller_fees | rate of DTC revenue, trailing window | P&L!B37, B43, B44 |
| headcount | headcount formula (settings above) | P&L!W75 |
| inbound_shipping, marketing_software, software_apps, other_business_expenses | rate of total revenue, trailing window | P&L!B38, B63, B91, B66 |
| insurance | $59; $1,200 from Sep-26 | P&L!V83, W83 |
| interest_expense, inventory_shrinkage, marketing_influencers, shipping_supplies, uncategorized, us_cogs | $0 | P&L!W84, D46, W62, W42, W94, W33 |
| legal | $0; $2,500 from Oct-26 | P&L!X85 |
| marketing_agency | last month carried forward | P&L!V60 |
| marketing_services | $22,000 | P&L!W61 |
| office_admin | $544.47 | P&L!W87 |
| outside_services | mean of the previous 5 months; $5,584.30 from Sep-26 | P&L!V88, W88 |
| sales_tax_filing | $1,112.50 | P&L!W89 |
| samples | $549.71 | P&L!W90 |

Each seeded row keeps its sheet cell in `proj_pnl_drivers.source`; `GET /pnl/drivers` returns it with the resolved rate or amount and the months a trailing rate reads.

## Fix wave (QA H2 and M5, skeptic S1 and S5)

- **Upload guard.** The preview computes coverage per month: a month becomes books only when revenue and every required account family (cost of goods, fulfillment and shipping, ad spend, operating expenses; settings `upload.required_families`, revenue always) are present once the file is applied. Otherwise `committable:false` with plain reasons in `blockers` and commit is refused (400). A month whose income or cost is below `upload.low_ratio` (50%) or above `upload.high_ratio` (200%) of the median of its neighbouring months and the plan's own figure is listed under `review` and needs `confirm_review=1` on commit. Each month shows income, cost, net operating income in the file and the plan's figure. Both ratios and the families are global settings (`PUT /pnl/upload-checks`, audited), editable in Settings.
- **Undo.** Every commit stores what it replaced and created in `proj_upload_history` (migration 131); `POST /pnl/upload/undo` restores exactly (values and sources) and removes what the upload added, only for the latest applied upload and only if nothing changed those cells since (409 otherwise); audited as `pnl.upload.undo`; `GET /pnl/uploads` lists history; the page has a QuickBooks uploads card with "Undo last upload". Uploads are scenario-independent facts, so they have their own history rather than a scenario version.
- **One month status.** `PnlOutput.month_status` (`books` | `feeds` | `projected`), `books_through`, `feeds_through` (read from `drivers.feeds_through`); the grid stays two-state (books = actual) and the legend, TLDR and caption use the three words: "Books through Jul 2026. Feeds only, no books yet: Aug-Oct 2026. Projected from Nov 2026." `/run` reads the same attributes (core).
- **Clock.** `store.today()` uses `app/projection/clock.today_pt` (Pacific); nothing in the module calls `date.today()` any more.
- **Data checks card.** `GET /pnl/checks` and the page card show, for the months the P&L uses a typed figure, new-customer revenue used vs the cohort table and repeat revenue used / typed / Cohorts model / cohort export, with the gaps. Nothing there changes a number.
- **Implied value.** Each scenario row also shows the offer as a multiple of the basis year, the year after and the year after that (4.9x, 7.1x, 8.4x today).
- **Sales tax.** Settings shows the rate (the drivers lever, edited through `PUT /drivers/levers`) with "History months carry no deduction; projected months deduct this."
- **Write rules.** `validate_series` (only lines with a driver, marketing spend and its channels can be typed over; not a books month; not negative except uncategorized) and `validate_params` (the settings limits) are exposed on MODULE for the core's generic endpoints. Tickets from the variables audit: `other_business_expenses` driver is now editable (fixed); the China cost has one source, the scenario param `pnl.cogs.china_cost.<sku>` (inventory_supply reads it and writes its product-cost edits to it; `proj_pnl_cogs_sku` and `proj_product_cost` are only seed and fallback; tests `test_pnl_shared_cost.py` both ways); vendor retainers are Josh's decision, left open.

## Freshness

`etl.projection_pnl.freshness(conn)` (shared hook, `app/projection/freshness.py`) returns one `monthly-upload` feed `quickbooks_pnl`, "QuickBooks P&L". `data_through` = last day of the last loaded closed month; it is stale when the first month not loaded closed more than `freshness.stale_after_days` (45) days ago, implemented as `stale_after_days = that setting + days in that next month` (Jul loaded: 76, so stale from Oct 16 for a data_through of Jul 31). The page states it in the TLDR strip ("Through Jul 2026", note "Aug-Oct projected", crimson when stale) and the line under it: "QuickBooks loaded through Jul 2026; Aug-Oct are projected. The August books were due Oct 15, 2026." Test: `test_freshness_entry_rule`.

## Reconciliation (frozen snapshot 2026-11-03)

Two runs, both strict:

1. **Module alone**, every upstream module replaced by its fixture stub (the sheet's own typed inputs): `PYTHONPATH=. DATABASE_URL=... python tests/projection/pnl/reconcile_stub_run.py`. 147 lines (82 line rows C:BJ, 59 fiscal-year cells, 6 implied-value cells), all within 0.5% or $1 except the one explained tolerance below (marketing software). History months (Jan-25 to Jul-26) match exactly, all 82 lines.

| line | max abs diff | max relative | note |
|---|---|---|---|
| total_revenue, amazon_fees, fulfillment, inbound shipping, payment processing, headcount, overhead, other business expenses (every month) | $0.00 | 0.0000% | |
| cogs, total_cogs, gross_profit | $0.10 (Sep-26) | 0.0001% | the sheet adds a ratio (0.12) into one cell, `W33` |
| marketing | $122.77 (Aug-26) | 0.27% | marketing software, below |
| marketing_software | $122.77 (Aug-26) | 2.07% | sheet bug, explained (QUESTIONS item 7); map tolerance 2.5% |
| total_expenses | $122.77 | 0.018% | |
| noi | $122.77 (Aug-26) | 0.086% | |
| ttm_noi, ftm_noi | $922.83, $1,473.30 | 0.081%, 0.086% | |
| FY27 net operating income | $786.36 | 0.052% | |
| implied value 3x ... 8x | $2,359 ... $6,291 | 0.052% | |

2. **Integrated**, real drivers and cohorts seeded (`python scripts/projection/reconcile.py --module pnl`): 113 of 147 lines pass. The rest differ only because of upstream: total revenue Sep-26 to Dec-29 runs 0.5% to 1.0% from the sheet exactly as the cohorts module's repeat revenue differs from the sheet's `Hist Waterfall` (revenue difference = 0.93 x repeat difference, checked month by month, 40 months), which moves every rate-driven cost with it (net operating income Sep-26 +3.6%, FY27 net operating income $1.526M against $1.502M, +1.6%, implied value at 3x +$74K); and rows 52-55 for Aug and Sep 2026 (sheet blank, drivers module splits the typed total by channel shares). Aug-26 ties because the drivers module carries the sheet's typed Aug repeat revenue as an override.

## Differences from the sheet

1. Marketing software rate window (above). 2. FY2026 net operating income is the full year (sheet cell sums Sep-Dec). 3. The trailing window is a lever (4 actual months), shown with its months, not wired to columns R:U; it moves when a month is uploaded, as designed. 4. Jul-26 typed values are loaded as actual (QUESTIONS item 2). 5. Row 33 `W33`, the stray ratio, is not reproduced. 6. Opex and COGS inflation levers now do something (default 0). 7. TTM and FTM are available for every month (the sheet fills TTM from Dec-25 and FTM from Jun-26). 8. Headline "FTM" is month x 12, as the sheet. 9. Inventory shrinkage ($22,631.40, Feb-25) is its own COGS line; the sheet hid it inside `D46`. 10. Implied value uses `settings.ev.year` (2027) full-year net operating income x 3 to 8; the sheet's note text names cell `BA27`, the formula uses `BN100`.

## Open items (decision-relevant first)

- **COGS price assumption**: 12.1% of revenue at $29.22 per bottle versus 16.3% at today's $21.62, $0.44M of FY27 cost (29% of FY27 net operating income). Josh's call; Settings shows both.
- Aug-Oct 2026 are closed months without books: their costs are projections until the QuickBooks export lands (preview, review, commit).
- Per-cell editing needs a grid gate (QUESTIONS item 1).
- Revenue accounts have no seeded QuickBooks name mapping; the first upload maps them.
- Vendor retainers (current $50,270, proposed $58,770 a month) are reference only; Madre, Lazer, Victoria etc. are not in any projected line.
- The cohorts and drivers modules' own reconcile carries the revenue gap shown in the integrated run.

## Commands

```
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH; PY=~/lonvera-tag-explorer/.venv/bin/python; cd ~/lonvera-tag-explorer-projection
createdb tagexp_proj_pnl      # then apply 001-004, 100, 130 (and other modules' migrations for the integrated run) with MIGRATIONS_DIR=<temp dir>
DATABASE_URL=postgresql://localhost/tagexp_proj_pnl PYTHONPATH=. $PY -m etl.projection_pnl seed            # idempotent
DATABASE_URL=... PYTHONPATH=. $PY tests/projection/pnl/reconcile_stub_run.py                               # module alone, 147 lines
PYTHONPATH=. $PY -m pytest tests/projection/pnl -q                                                          # own database tagexp_proj_pnl_test
DATABASE_URL=... AUTH_DISABLED=1 SECRET_KEY=x DEV_INSECURE_COOKIES=1 PYTHONPATH=. $PY -m flask --app wsgi run --port 5203
$PY tests/projection/pnl/shoot_pnl.py http://127.0.0.1:5203 /tmp/pnl_shots dark                              # screenshots, desktop and 390px, light and dark
```
