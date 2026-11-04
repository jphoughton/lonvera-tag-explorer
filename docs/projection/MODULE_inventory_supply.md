# Inventory supply (module `inventory_supply`)

What it replaced: the sheet tabs `Purchase Orders`, `Vendor COGS`, `FBA Transfers`, `Warehance Inventory Burn`, `Intl Inventory Burn`, `Amazon Inventory Burn`, `Total Inventory Burn`, `Warehance Reorder`, `Intl Reorder`, `Inventory Value`, `Inventory Cash Outlay`, plus the stock-on-hand blocks and reorder parameters of the three `Fcst Inputs` tabs and the unit costs the `COGS` tab gives `Inventory Value`.

It answers: which product runs out where and when, when the order has to be placed and land, what is already on order, what the stock is worth, and what the orders cost in cash by month. It proposes orders and transfers; it writes nothing until a person accepts or edits.

## Files

```
migrations/150_proj_inventory_supply.sql               tables below
app/projection/inventory_supply/__init__.py            MODULE (key inventory_supply, ui inventory_supply.js)
  model.py        vocabulary, Inputs, Settings (levers), SupplyResult
  calc.py         burn, reorder, value, purchase-order cash, warnings, headline (pure functions, no database, no clock)
  planner.py      plan proposal (orders and transfers), never writes
  loaders.py      Inputs from the tables, reference-day rule
  store.py        validated, audited writes
  freshness.py    freshness of the manual inputs
  tables.py       grid payloads
  api.py          /api/projection/inventory-supply/*
  overlay.py      scenario overlay: variables a scenario changes (levers, vendors, prices, reorder, costs, orders, transfers), change lists, catalog
  reconcile_map.py   274 lines against the sheet
etl/projection_inventory_supply.py                     seed, refresh (daily Revamp stock), freshness
app/static/js/projection/inventory_supply.js           the page
tests/projection/inventory_supply/                     183 tests
```

## Tables (migration 150, scenario-independent facts)

| table | holds |
|---|---|
| `proj_po` | purchase orders: product, vendor, ship-to (`revamp` / `intl`), units, typed order date (null = land date minus vendor lead time), land date, status (`planned`, `ordered`, `in transit`, `landed`, `cancelled`), deposit / shipping / balance paid flags, typed deposit % and unit cost (null = vendor price list), link, note, `source` (`manual` / `auto-plan`). Seeded rows have id = their sheet row; new rows start at 1000 |
| `proj_fba_transfer` | Revamp to Amazon FBA: units, ship date (leaves Revamp stock that month), receive date (arrives in FBA stock that month), status (`planned`, `shipped`, `received`, `cancelled`), source |
| `proj_vendor_lead` | vendor: lead weeks, minimum order, default ship-to (which location the planner buys it for) |
| `proj_vendor_cogs` | vendor x product unit cost and default deposit % |
| `proj_product_cost` | China landed unit cost per product (values stock) |
| `proj_reorder_param` | per location x product: lead days (order placed to landed), safety days, minimum order |
| `proj_stock_snapshot` | stock on hand per location x product x count date (append-only by date; the burn opens from the latest) |

Products are `bull`, `liver`, `boneflex`, `belle`, `flow`; locations `revamp`, `amazon`, `intl`. Sheet names ("Vital Bull", "International 3PL") are accepted on input and normalised.

Scenario variables (`params.inventory_supply`): the 22 scalar variables listed under Variables, plus the table overlays `vendors`, `prices`, `reorder`, `product_costs`, `pos`, `transfers`. The stored rows are the plan of record's values (seeded from the sheet); a scenario copy keeps only what it changes, so the plan of record is never touched by a copy and every change is versioned, audited and restorable by the core. Editing the plan of record edits the stored rows (audited in `proj_audit`); editing a copy writes an override (`?scenario=<slug>` on the write endpoints, or `PUT /variables`). Stock counts are measurements, shared by every scenario.

## Inputs and outputs

`compute(scn, inputs, demand=DemandResult)`. Demand is `inventory_demand`'s `DemandDetail`: `units[(loc, prod)]` = full-month units; `units[(f'{loc}_mtd', prod)]` = `{current month: shipped so far}`. The first month burns `units - mtd` (the sheet's `Demand Fcst` row 14 minus row 13). The reference day is `params.inventory_supply.as_of`, else env `PROJECTION_TODAY`, else today; the burn starts in that day's month and runs to Dec 2029.

Returns `SupplyResult` (a subclass of `InventoryResult`, so `isinstance` and the interface hold):

| field | meaning |
|---|---|
| `outlay` | purchase-order cash by month over the whole 60-month axis: deposit in the order month, balance (value minus deposit) in the landing month; cancelled orders carry none. Financing reads this (`inventory.outlay`, months from the deal close on) |
| `value` | inventory value at month close (closing units x China landed cost), all locations |
| `burn` | `{(location, product, line): Series}`; locations `revamp`, `amazon`, `intl` and `total` (all locations per product); lines `opening`, `demand`, `inbound`, `out` (Revamp transfers out), `closing`, `cover` (closing / next month's demand), `to_send` (FBA: target cover minus closing, floored at 0), `check` and `value` (total) |
| `reorder` | list sorted by urgency (OVERDUE, ORDER SOON, OK; then days to order): stock-out month, opening and demand that month, estimated stock-out date (interpolated inside the month), must land by (minus safety days), order by (minus lead days), days until order, status, average monthly demand, suggested order = max(minimum, cover months x average demand) |
| `deposits`, `balances`, `intl_outlay`, `landing_value`, `landing_deposits`, `outlay_cumulative` | the rows of `Inventory Cash Outlay` |
| `value_by_location` | `{(location, product): Series}` |
| `stats`, `warnings`, `po_rows`, `transfer_rows`, `tldr`, `scalars`, `po_cells` | page and reconcile detail |

Rules carried from the sheet: inbound at Revamp / the international 3PL = open orders by land month (status not `landed` or `cancelled`); a landed order is already in the stock count. Revamp opens on available units (on hand minus allocated); Amazon FBA and the international 3PL on units on hand. Status `OUT` = closing stock below zero. Reorder looks for the first OUT month inside the horizon (36 months).

## Planner

`GET .../plan/suggest` proposes, `POST .../plan/accept` writes. Rules (the 2026-10-13 rebalance, generalised from a flat daily rate to the demand series):

1. FBA transfers: month by month, when FBA closing stock is below `fba_target_months` of demand, ship from Revamp in the previous month and receive in this one, sized to restore `fba_restore_months`, in steps of 500 (minimum 500). The current month cannot be fixed (nothing arrives in time) and is reported.
2. Revamp buys (the vendor priced for Revamp) and 3. international 3PL buys (the vendor priced for it): at the first month below safety (safety days of that month's demand), land an order in that month, or on today plus the vendor lead time if that is later; size = shortfall plus `reorder_cover_months` of forward demand, rounded up to 1,000, not below the vendor minimum. If the earliest possible landing is after the breach, the order still lands then and the month is reported under "What cannot be fixed in time" (the app does not buy again for a month that no order can reach).

`replace_auto=1` (default) plans on top of the non-auto rows and lists the existing auto-plan rows as removed. Accept requires `expect_replaced {pos, transfers}` with `replace_auto`; a mismatch (rows changed since the plan was made) is 409 `plan_stale`. One transaction: deletes, inserts, an audit row per row, one `inventory_supply.plan.accept` row.

## API (`/api/projection/inventory-supply`)

Reads: `summary` (headline, reorder, stats, warnings, freshness), `variables` (every variable with value, default, origin, source, and the scenario's own changes), `table` (all-locations burn; the shell's default), `burn?location=revamp|amazon|intl|total`, `value`, `outlay`, `reorder`, `warnings`, `freshness`, `po`, `transfers`, `stock`, `vendors`, `plan/suggest?replace_auto=`. Grid payloads follow the shell's grid (`fmt` usd/usd0/num/x/text, `kind` header/calc/subtotal/ratio/total), one caption each.

Writes (all audited in `proj_audit`, action `inventory_supply.*`, `line_key` `inventory_supply.<kind>:<id>`, old and new values; validated, 400 with `{error}`): `POST /po`, `PATCH /po/<id>`, `DELETE /po/<id>`, `POST /transfers`, `PATCH|DELETE /transfers/<id>`, `POST /stock` (a second count for the same day replaces that day's row), `PUT /vendors/<vendor>`, `PUT /vendor-costs`, `PUT /reorder-params`, `PUT /product-costs`, `POST /plan/accept`, and `PUT /variables` (`{scenario?, items:[{kind: lever|vendor|price|reorder|product_cost|po|transfer, ..., value}], expected_version?}`; `value: null` goes back to the default; all items validated together, one scenario version). Every write endpoint takes `?scenario=<slug>`: for the plan of record (or no scenario) it edits the stored rows, for any other scenario it records the change as a scenario variable. Validation: units > 0; status, warehouse and product enums; dates ISO and inside the model window (Jan 2025 to Dec 2029); land date on or after order date; receive date on or after ship date; deposit % a fraction 0 to 1; unknown fields rejected; at most 200 rows per plan. Auth, origin, rate limits and JSON-only are the core's app-level guards (tests: 401 with the app's login on, open with `AUTH_DISABLED`).

## Page

`/projection/inventory-supply`. Header with TLDR ("Vital Bull at the International 3PL runs out Nov 4, 2026*; the order was due Aug 22, 2026." plus first stock-out, order by, cash out in the next 90 days, stock at landed cost, oldest stock count), data checks (freshness chips and the warnings below), "What to order" (urgency order, status chips, one-click prefill of a purchase order), suggested plan (comparison, nothing saved until accepted), stock by month per location with chart and grid (OUT / LOW chips), purchase orders and transfers edited in place (amber cells; add row on top; delete asks to confirm), stock counts (edit saves today's count), cash and value charts with grids, lead times and prices, and the planning variables with the sheet value, source and status of each. In a scenario copy the amber band and "Copy" badge show, edits say they change only that scenario, changed rows carry a "Changed here" or "Only here" chip, changed values are underlined with the plan of record's value in the tooltip, and each changed row can be reverted. Checked in headless Chromium at 1440 and 390 px, light and dark; console clean; no sideways page scroll.

## Loaders

`python -m etl.projection_inventory_supply seed [--replace]`: loads the frozen sheet snapshot (74 orders, 44 transfers, 2 vendors, 6 prices, 5 unit costs, 8 reorder parameters, 12 stock counts). Inserts what is missing and never overwrites, so edits survive a re-run; `--replace` reloads the rows that came from the sheet. Order dates are stored only where the sheet typed one that differs from land date minus lead time (9 rows). Land dates that the sheet chained by formula to other rows' dates are stored as plain dates.

`refresh(conn, days=14)` (picked up by `etl.projection_daily`): reads `Warehance Fcst Inputs` A33:D39 read-only with the service account (env `GOOGLE_SA_JSON`), appends one dated Revamp count per product; an identical row is skipped (idempotent), the same day with different values is replaced, earlier days stay (append-only); a count older than `days` is not loaded. FBA and the international 3PL are never refreshed (no feed): enter them on the page.

## Freshness

`freshness(conn)` returns four feeds for the runner and the page: `purchase_orders` (monthly upload, data through = last edit of any order or transfer, stale after 30 days), `stock_revamp` (daily, 2), `stock_fba` (manual, 14), `stock_intl` (manual, 14, labelled estimate while the count is one). The runner only accepts `daily` and `monthly-upload` cadences, so the two manual counts report `monthly-upload` (reported, never fatal) with `input: "manual"` for the page. At the snapshot date: Revamp fresh, FBA and international 3PL stale at 21 days (the sheet's own count dates are 2026-10-13, the international one an estimate). The TLDR names the oldest count. Landed-but-still-inbound orders are a warning (below).

## Variables

Every input that drives the engine is a named variable with the sheet value as its default and its source noted; a scenario can change any of them. Scalars are scenario params (`params.inventory_supply.<key>`), set on the page ("Planning variables", an empty value goes back to the sheet) or with `PUT /api/projection/inventory-supply/variables`; `GET .../variables?scenario=` lists every variable with value, default, origin (sheet value, changed in this scenario, from the base scenario) and source. Out-of-range or mistyped values are rejected on write (400) and, if they ever reach the engine, fall back to the sheet default.

### Scalar variables (22)

| key | meaning | unit | sheet value | sheet cell(s) / rule | min to max |
|---|---|---|---|---|---|
| `as_of` | Reference day | date | today | Sheet TODAY() (every burn tab starts at this month) |  |
| `horizon_months` | Planning window | months | 36 | Burn tabs: 36 month columns B:AK | 1 to 120 |
| `stale_stock_days` | Stock count counts as stale after | days | 14 | Freshness rule (no sheet cell) | 1 to 365 |
| `safety_days` | Safety stock for every SKU (overrides the per-SKU days) | days of demand | none (SKU's own) | Warehance Fcst Inputs C44:C47, Intl Fcst Inputs C45:C48 (14) | 0 to 365 |
| `default_safety_days` | Safety stock when a SKU has no row | days of demand | 14 | Warehance Fcst Inputs C44:C47 (14) | 0 to 365 |
| `default_lead_days_revamp` | Lead time at Revamp when a SKU has no row | days | 90 | Warehance Fcst Inputs B44:B47 (90) | 0 to 730 |
| `default_lead_days_intl` | Lead time at the international 3PL when a SKU has no row | days | 60 | Intl Fcst Inputs B45:B48 (60) | 0 to 730 |
| `order_soon_days` | Order soon when the order-by date is within | days | 21 | Warehance Fcst Inputs B49, Intl Fcst Inputs B50 (21) | 0 to 365 |
| `reorder_cover_months` | Suggested order covers | months of average demand | 3 | Warehance Fcst Inputs B48, Intl Fcst Inputs B49 (3) | 0 to 24 |
| `out_below_units` | A month is OUT when closing stock is below | units | 0 | Burn tabs Status row: IF(closing<0,"OUT") (0) | -1e+07 to 1e+07 |
| `low_cover_months` | A month reads LOW below | months of cover | 1 | No sheet cell (added) | 0 to 24 |
| `fba_target_months` | Amazon FBA target cover | months of next month's demand | 1.5 | Amazon Fcst Inputs B42 (1.5) | 0 to 12 |
| `plan_cover_months` | Plan: each new order covers | months of forward demand | 3 | 2026-10-13 rebalance (Purchase Orders notes: 3-mo cover) | 0 to 24 |
| `plan_safety_days` | Plan: safety stock to keep | days of demand | none (SKU's own) | 2026-10-13 rebalance (14); empty = the SKU's own safety days | 0 to 365 |
| `po_round_units` | Plan: round orders up to a multiple of | units | 1000 | 2026-10-13 rebalance (1,000) | 1 to 1e+06 |
| `transfer_round_units` | Plan: round FBA transfers up to a multiple of | units | 500 | 2026-10-13 rebalance (500) | 1 to 1e+06 |
| `transfer_min_units` | Plan: smallest FBA transfer | units | 500 | 2026-10-13 rebalance (500) | 0 to 1e+06 |
| `fba_restore_months` | Plan: a transfer restores FBA to | months of cover | 2.5 | 2026-10-13 rebalance (2.5) | 0 to 24 |
| `transfer_lag_months` | Plan: months from ship to FBA receive | months | 1 | FBA Transfers: ship month = receive month - 1 | 0 to 6 |
| `days_per_month` | Plan: days in a month for run rates | days | 30.4 | 2026-10-13 rebalance (rate x 30.4) | 1 to 31 |
| `duplicate_window_days` | Duplicate check: orders landing within | days | 21 | No sheet cell (added) | 0 to 365 |
| `cash_window_days` | Headline cash window | days | 90 | No sheet cell (added) | 1 to 730 |

Test for the class: `test_each_scalar_variable_moves_an_output[<key>]` (one case per key: the variable is set through the scenario-params path and a named output of the engine or planner must change), `test_every_scalar_variable_has_a_mover_and_a_sheet_default` (no variable ships without a mover, every default equals the sheet value, every spec has label, unit and source), `test_the_reference_day_variable_moves_the_start_month`, `test_out_of_range_or_mistyped_variables_fall_back_to_the_sheet_default`. API: `test_variables_catalog_and_changes_with_revert_audit_and_history`, `test_variable_changes_are_validated_and_write_nothing_when_one_is_bad`.

### Table variables (rows and prices)

| variable | fields | unit | sheet value / cells | where set | test (change moves the outputs) |
|---|---|---|---|---|---|
| Vendor lead time | `vendors[<vendor>].lead_weeks` | weeks | `Vendor COGS` H4:H5 (China 8, KeyStone 12) | Lead times, prices and safety stock panel | `test_vendor_lead_time_override_moves_the_order_date_and_the_deposit_month` |
| Vendor minimum order, supplied location | `vendors[<vendor>].moq_units`, `.default_warehouse` | units, location | rebalance script (5,000 China to the international 3PL, 20,000 KeyStone to Revamp) | same panel | `test_vendor_minimum_order_override_moves_the_plan` |
| Price and deposit | `prices["<vendor>|<product>"].unit_cost`, `.deposit_pct` | dollars per unit, fraction | `Vendor COGS` D4:D9; `Purchase Orders` P (0.3 to 0.5) | same panel; per order in the Unit cost and Deposit columns | `test_price_unit_cost_and_deposit_overrides_move_value_and_cash` |
| Reorder timing per SKU | `reorder["<location>|<product>"].lead_days`, `.safety_days`, `.moq_units` | days, days, units | `Warehance Fcst Inputs` B44:D47 (90, 14, 0); `Intl Fcst Inputs` B45:D48 (60, 14, 0) | same panel | `test_reorder_parameter_overrides_move_the_order_by_date_and_the_suggested_order` |
| Landed unit cost for stock value | `product_costs[<product>].china_unit_cost` | dollars per unit | `COGS` D4:D13 | same panel | `test_product_cost_override_moves_inventory_value` |
| Purchase order rows | `pos["<id>"]` override of any column, `deleted`, or a scenario-only row `pos["new:<n>"]` | order columns | `Purchase Orders` A2:R78 (74 rows) | Purchase orders table (edits, add row, delete; accepted plans) | `test_po_row_override_moves_inbound_cash_and_marks_the_changed_fields`, `test_po_row_can_be_removed_and_scenario_only_rows_can_be_added`, `test_a_po_edit_in_a_copy_is_an_override_and_the_plan_of_record_keeps_its_value`, `test_burn_and_cash_follow_the_copy_while_the_plan_of_record_does_not_move` |
| FBA transfer rows | `transfers[...]` same shape | transfer columns | `FBA Transfers` A4:G47 (44 rows) | Transfers table | `test_transfer_overrides_move_the_fba_inbound_and_revamp_outflow`, `test_transfers_follow_the_same_rules_in_a_copy` |
| Accepted plan | the plan's rows as scenario-only orders and transfers, replacing the scenario's auto-planned rows | rows | n/a (planner output) | Suggested plan | `test_a_plan_accepted_into_a_copy_lives_in_the_copy_only` |

Class tests: scenario isolation `test_a_po_edit_in_a_copy_is_an_override_and_the_plan_of_record_keeps_its_value`, `test_vendor_price_reorder_and_cost_edits_in_a_copy_are_overrides_with_their_defaults_shown`; inheritance `test_a_copy_inherits_the_plan_of_records_variables_until_it_changes_them`; validation `test_invalid_scenario_edits_are_rejected_and_change_nothing`, `test_strict_mode_rejects_what_the_tolerant_mode_skips`, `test_build_patch_rejects_malformed_changes`; a change that no longer fits (its row was deleted later) is skipped and shown as a data check, `test_a_change_that_points_at_a_row_that_was_later_deleted_is_flagged_not_fatal`.

### Not variables, and why

- Stock counts (`proj_stock_snapshot`): measurements with a date, entered on the page or read from Warehance, shared by every scenario.
- Definitions the sheet states as structure, not inputs: deposit lands in the order month and the balance in the landing month; order date = land date minus lead weeks x 7; cover = closing stock / next month's demand; Revamp opens on available units (on hand minus allocated), Amazon FBA and the international 3PL on units on hand; OVERDUE = order-by date already passed; the reorder search covers `horizon_months`.
- Feed freshness thresholds (purchase orders 30 days, Revamp stock 2, FBA and international counts 14) are loader rules (scope `feed`): the daily runner evaluates them with no scenario. `stale_stock_days` only sets when the data check on the page warns.
- `MAX_ROUNDS` (60) in the planner is a loop guard, not a business input.
- The vocabulary (statuses, products, locations) is code; adding a product is a migration.

Variables not in the plan of record's sheet at all (added by this module, default noted as no sheet cell): `low_cover_months`, `duplicate_window_days`, `stale_stock_days`, `cash_window_days`.

## Reconciliation (frozen sheet, reference day 2026-11-03)

`PROJECTION_TODAY=2026-11-03 DATABASE_URL=... python scripts/projection/reconcile.py --module inventory_supply`, through the real `inventory_demand` module: **274 lines, 4,966 cells, 0 over tolerance, 0 skipped, 0 on a stub**. Largest absolute difference anywhere 5.8e-11.

| group | lines | cells | max abs diff |
|---|---|---|---|
| Revamp burn (opening, demand, inbound, transfers out, closing, cover x 4 products) | 24 | 860 | 0 |
| Intl burn | 20 | 716 | 0 |
| Amazon burn (incl. units to send) | 24 | 856 | 0 |
| Total burn (incl. check row) | 26 | 931 | 2.9e-11 |
| Bottom tables (available today, average demand, cover today, months out) | 65 | 65 | 7.3e-12 |
| Inventory Value (by location x product, by product, total, value today) | 21 | 651 | 5.8e-11 |
| Inventory Cash Outlay rows 5 to 11 and totals | 11 | 424 | 1.5e-11 |
| Purchase Orders columns F, L, M, N, Q (order date, cost, value, down payment, balance) | 5 | 385 | 0 |
| Reorder tabs (dates to 0.01 day) | 78 | 78 | 1.5e-11 |

Cover and units-to-send are compared for the first 35 columns (see difference 2).

## Differences vs the sheet (all explained)

1. **Start month rolls.** The sheet starts at `TODAY()`; the app starts at the reference day's month (scenario `as_of`, env `PROJECTION_TODAY`, else today). Reconciled at 2026-11-03.
2. **Window end.** The burn runs to Dec 2029 (38 months from Nov 2026), the sheet stops at column AK (36). Averages, stock-out search and "months stocked out" use `horizon_months` = 36, so they match. The sheet's last column divides cover and units-to-send by its own month's demand because no next month exists; the app has the next month, so those two lines are compared for 35 columns.
3. **International 3PL lead time.** `Intl Fcst Inputs` says 60 days; `Intl Reorder` column B is typed 30. The app uses the input (60), so Intl order-by dates and days-until-order are 30 days earlier than the sheet (liver 75 days to order instead of 105; Vital Bull and Boneflex OVERDUE either way). Stock-out, must-land, suggested units and statuses match; a test proves the order-by dates tie to the sheet with the lead time set to 30. Those three columns are not in the reconcile map for that reason.
4. **Cancelled rows.** The sheet counted cancelled orders as inbound and as cash; the app excludes them and says so (info check). No cancelled rows exist today, so nothing moves.
5. **Transfer status.** The sheet ignored it. `shipped` units count in FBA inbound only (they already left Revamp), `received` and `cancelled` count nowhere. All current rows are `planned`, so nothing moves.
6. **Added statuses and checks.** `LOW` (under one month of cover) and the data checks below are additions; the sheet had OUT only.
7. **Missing lead time or price.** The sheet silently dropped a deposit it could not date; the app dates it at landing, flags it, and carries no cash for an order with no price.
8. **Vendor and price lists** live in tables (`proj_vendor_lead`, `proj_vendor_cogs`) instead of the `Vendor COGS` tab; the minimum orders (5,000 China, 20,000 KeyStone) and the supplied location come from the 2026-10-13 rebalance script, not from any tab.

## Data flags carried as visible warnings (counts at the snapshot)

| check | at 2026-11-03 |
|---|---|
| Possible duplicate purchase orders (same product, vendor, ship-to and size landing within 21 days) | rows 2 and 3 (5,000 Vital Bull, China to the international 3PL) |
| Planned orders with an order date already past | rows 13 to 15 (order date Nov 1) |
| Orders past their land date still counted as inbound (not marked landed) | rows 7 to 9, 45,000 units, land date Nov 1 |
| Orders with a land date before this month, not marked landed (in neither stock nor inbound) | rows 2, 4, 5, 6, 41,000 units |
| Transfers that arrive before they ship | rows 4, 5, 6 |
| Vital Flow stock valued at $0 (no landed cost) | yes |
| FBA stock count 21 days old; international 3PL count 21 days old and an estimate | yes |
| Cancelled rows excluded | none present |

## Open items

- FBA and international counts are three weeks old and the international one is an estimate: the model says Vital Bull and Boneflex at the international 3PL are already overdue to order (Vital Bull out Nov 4, order was due Aug 22). A fresh portal count decides how real that is.
- Orders 7 to 9 and 4 to 6 need a status or date decision (see checks). Until then 45,000 units may be double counted this month and 41,000 are in no column.
- Vital Belle has no vendor price, so the planner proposes nothing for it and it shows OUT from Feb 2029.
- `inventory_demand` also appends Revamp stock to its own `proj_demand_snapshot` (daily). This module reads only `proj_stock_snapshot` (one source of truth) and refreshes Revamp into it by itself.
- Stored orders and transfers are the plan of record; a scenario copy keeps its own changes as overrides on top of them. A change to a row that the plan of record later deletes is skipped and flagged.
- Not run: the app's login-on mode in a browser (API 401 is tested); Python 3.12 byte-compile checked, tests ran on 3.14.

## Interface notes for downstream modules

- financing: `inventory.outlay` (Series over all 60 months, dollars); it includes history months and cancelled orders are excluded.
- Anything wanting stock: `inventory.burn[(location, product, 'closing')]` and `inventory.value`; reorder urgency: `inventory.reorder`.
- The module needs `demand` only for burn and reorder; `outlay` does not depend on it. Without demand the module still runs and adds a `demand_missing` check.

## Run

```
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH; PY=~/lonvera-tag-explorer/.venv/bin/python
PYTHONPATH=. $PY -m pytest tests/projection/inventory_supply -q                      # own database tagexp_proj_inventory_supply_test
DATABASE_URL=postgresql://localhost/tagexp_proj_inventory_supply PYTHONPATH=. $PY -m etl.projection_inventory_supply seed
PROJECTION_TODAY=2026-11-03 DATABASE_URL=... PYTHONPATH=. $PY scripts/projection/reconcile.py --module inventory_supply
```
