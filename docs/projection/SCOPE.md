# Lonvera Projections → Tag Explorer: scope and build plan

Written 2026-11-03 from a read-only pass over the live sheet (`1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI`, 73 tabs, 25 visible) and `context/ma-calano.md`. Nothing in the sheet or Meta was written.

## TLDR

Build it in three phases, all in this app, each ending in a reconciliation report against the sheet. The sheet is far simpler than its 72 tabs suggest: one engine (cohort curve → repeat revenue; typed monthly spend × NC-ROAS → new-customer revenue; cost-rate lines → NOI) plus three satellites (inventory + POs, cash/debt/payback, pacing views). The scenario selector (Base/Bull/Bear/Apocalypse) is gone from the sheet; scenarios become named lever sets in Postgres.

What the model says today (P&L, current plan, "Flat"): spend falls from $9.2M (FY26) to a typed flat $350K/mo ($4.2M/yr); total revenue $18.9M → $10.3M (FY27); NOI $0.91M → $1.50M (FY27); 3x–8x FY27 NOI = $4.5M–$12.0M against the $7.5M LOI. That is a harvest posture, not a growth case. The app will make that explicit (the spend plan is the lever that drives everything).

## Phases

| Phase | What | Sheet tabs replaced |
|---|---|---|
| P1 Engine | Cohort table → retention curves → repeat-revenue waterfall; spend plan × NC-CPA/AOV → NC revenue; Amazon/TikTok revenue; P&L revenue, COGS, fees, marketing, opex, NOI, FY/TTM/FTM, growth, implied EV; scenarios + overrides; QuickBooks history upload | Cohort Data - Unified, Curves, Hist Waterfall, P&L (rows 1–102, 134–175), COGS, COGS Adjustments, Fixed Expenses |
| P2 Supply + cash | Demand forecasts (Warehance, Amazon, international 3PL), POs, FBA transfers, burn, reorder, inventory value + cash outlay; term-debt schedule, tax, equity payback, ending cash; payback + spend×ROAS grid + media-buyer payout block | Warehance/Amazon/Intl/Total demand + burn + reorder tabs, Purchase Orders, FBA Transfers, Vendor COGS, Inventory Value, Inventory Cash Outlay, Term Debt Monthly Paydown, P&L rows 108–131, Payback, Spend-ROAS Matrix, P&L E177:P206 |
| P3 Pacing | Automated Pacing (EBITDA/NOI MTD pacing), Month Snapshot, Daily/Weekly/Monthly rollups with per-product Meta bands and testing-vs-scaling band, Meta product pacing | Automated Pacing, Month Snapshot, Pacing - Daily (180d), Rollup - Weekly/Monthly, Meta - Daily/Weekly/Monthly |

Not moving: the 8 Klaviyo tabs and Fulfillment/Stuck Orders/Transit tabs. They are marketing and ops analytics, not projection inputs; separate project if wanted.

## Tab inventory

Live = referenced by the P&L or visible and maintained. Source = where the numbers come from today.

### Engine (P1)

| Tab | Computes | Inputs | Live | Source | Module |
|---|---|---|---|---|---|
| Cohort Data - Unified (45×160) | One row per acquisition month, Feb-24 → Oct-26 (33): new customers, first-order $/customer, M1..M36 cumulative $/customer, 90-day repeat, CAC; Avg and Last-3-cohort-avg rows | Pasted cohort export, refreshed by hand; closed months only | yes | Shopify/Upstack cohort CSV (tool unnamed), pasted | cohorts |
| Curves (60 ages) | Retention curve = repeat $ per $1 of first order by age. "Live" = weighted avg of last N cohorts per age (ages 1–27), tail decays 5%/mo from age 28; "Goal" = static snapshot 2026-09-29; lifetime and 12-month sums | Cohort tab row 36, tail-decay cell C2 | yes | derived | cohorts |
| Hist Waterfall - Last 4 Months Curve (933×91) | 5 layers: age grid (cohort × month), curve lookup, repeat $ = NC-Rev × curve(age), total revenue, actual repeat. Row 228 = total repeat revenue per month, feeds P&L row 17 | Cohort NC-Rev (P&L row 12), Curves | yes | derived | cohorts |
| P&L (1179×86) | 60 months Jan-25 → Dec-29 + FY roll-ups. Revenue: Ad spend, NC-Rev, NC-orders, NC-CPA/AOV/MER, repeat, Amazon, TikTok, sales-tax estimate (7% of DTC). Costs: Amazon fees (FBA, seller, subs, transport), COGS (China COGS = rate × revenue), fulfillment, inbound, payment processing, marketing (channel split + agency/services/software), other, headcount, 11 overhead lines. Gross profit, NOI, TTM/FTM, MoM/YoY growth, implied EV (FY27 NOI × 3–8x) | Column B holds each line's driver (trailing Apr–Jul-26 rate, or fixed $) | yes | History cols hold pasted QuickBooks values; forward spend/CPA/AOV/Amazon are typed constants | drivers + pnl |
| COGS (hidden) | SKU on-hand qty × China landed unit cost; sales mix; weighted cost → P&L B32 | typed | yes | Warehance on-hand + vendor quotes | pnl |
| COGS Adjustments (hidden) | Monthly sales by channel (DTC US, intl, Amazon, TikTok) for the US-COGS split | typed | yes | QuickBooks | pnl |
| Fixed Expenses (hidden) | Vendor retainers current vs proposed (Madre, Lazer, Victoria, 10x, BM Digital, MP, …) | typed | not wired | planning | pnl (input) |

### Supply and cash (P2)

| Tab | Computes | Inputs | Live | Source | Module |
|---|---|---|---|---|---|
| Warehance Daily / Weekly / Fcst Inputs / Demand Fcst (hidden) | Units shipped per day by product (Revamp Utah 3PL), 28-day baseline × growth × seasonality → 36-month demand | Warehance API | yes | daily sync (`warehance_sync.py`) | inventory-demand |
| Amazon Monthly / Daily Units / Fcst Inputs / Demand Fcst (hidden) | Same for Amazon (FBA), 30-day baseline, 2-day lag | ExpandFi `sales` | yes | desktop routine | inventory-demand |
| Intl Daily Units / By Country / Fcst Inputs / Demand Fcst (hidden) | Same for the international 3PL (~37% of units) | Josh's monthly `Lonvera-Tracking-YYYY-MM.xlsx`; estimates since 09-16 | yes | manual xlsx | inventory-demand |
| Total Demand Fcst (hidden) | DTC + Amazon + Intl units | the three above | yes | derived | inventory-demand |
| Purchase Orders | Josh's PO entries + auto-plan rows: units, vendor, land date, deposit %, down payment, balance, order date = land − lead time | typed; Vendor COGS | yes | manual | inventory-supply |
| Vendor COGS (hidden) | Unit cost by vendor × product; lead times (China 8w, KeyStone 12w) | typed | yes | quotes | inventory-supply |
| FBA Transfers (hidden) | Units leaving Revamp for FBA by ship/receive month | typed | yes | manual | inventory-supply |
| Warehance / Intl / Amazon / Total Inventory Burn | Opening → demand → inbound (POs, transfers) → closing, months of cover, OUT flags | demand + POs + on-hand snapshot | yes | derived | inventory-supply |
| Warehance Reorder / Intl Reorder (hidden) | Stock-out date, must-land, order-by, OVERDUE/ORDER SOON/OK, suggested PO | burn + lead times | yes | derived | inventory-supply |
| Inventory Value (hidden) | Units × landed cost at month close | burn + COGS | yes | derived | inventory-supply |
| Inventory Cash Outlay | Deposits by order month + balances by land month → P&L row 125 | Purchase Orders | yes | derived | inventory-supply |
| Term Debt Monthly Paydown | Amortization of $3.0M at 15%, typed monthly principal; feeds P&L rows 112–115 | typed | yes | LOI terms | financing |
| P&L rows 108–131 | NOI before inventory, loan interest/principal, seller financing, tax, equity payback, inventory buys → cash after inventory buys, ending cash | Term Debt, Inventory Cash Outlay | yes (row 126 pre-existing #REF!) | derived | financing |
| Payback | Today's AOV/CAC (from Automated Pacing), product cost 17.26% + $5.53 fulfillment → CM per customer; month-by-month cumulative CM on the live curve; payback month | Automated Pacing M6/M7, Curves | yes | derived | financing |
| Spend-ROAS Matrix (hidden) | Profit at a target month by monthly spend × NC-ROAS; marginal breakeven ROAS | Payback (references a deleted `Payback Summary` tab, partly broken) | broken | derived | financing |
| P&L E177:P206 | Scale-payback and media-buyer payout schedule (bonus by scale × payback month, 15%/10% bumps, Lazar retainer, media kicker 9%) | typed | yes | deal terms | financing |

### Pacing (P3)

| Tab | Computes | Source | Live | Module |
|---|---|---|---|---|
| Automated Pacing | Current-month MTD vs P&L goal for revenue, DTC NC/repeat, NC-ROAS/CPA, Amazon, COGS, ad spend by channel, EBITDA/NOI; EOM projection | Orders-History, Amazon-Daily, Meta Campaign - History, Ad Spend - Daily, P&L | yes | pacing |
| Month Snapshot | Month dropdown: Actual / Projected* / Target / Diff / Diff % for revenue, NC, ads, EBITDA, NOI, payback | Rollup - Monthly, P&L | yes | pacing |
| Pacing - Daily (180d), Rollup - Weekly, Rollup - Monthly | Day/week/month rollups: Meta, Google, Amazon, per-product bands (VitaBull/Beef Liver/Boneflex × 9 metrics), testing-vs-scaling, projections vs targets | raw feed tabs below | yes | pacing |
| Meta - Daily/Weekly/Monthly | Meta pacing by All/Boneflex/VitaBull/Beef Liver/Scaling/Testing | Meta Campaign - History | yes | pacing |
| Raw feeds (hidden): Ad Spend - Daily, Orders - Daily, Attribution/Platform/Orders/Product - History, Amazon - Daily, Amazon - Product Daily, Pacing - By Product, Meta Campaign - History, Meta Spend - By Product, Meta Product - Reach, NC Rev - By Product, Upstack Any-Click - By Campaign, Meta Account - Reach, Meta Bids - Daily, Traffic - Daily | Inputs to pacing; written by sync jobs (`lonvera-sync-tick`) | Upstack, Meta Graph, ExpandFi, Klaviyo | yes | pacing (read) |

Meta spend, purchases, revenue by ad × day already live in this app (`ad_daily`, `family_daily`) and refresh daily, so Meta actuals need no new pull. Upstack, ExpandFi and Amazon feeds are not reachable from Railway directly (ExpandFi issues 30-minute credentials through the MCP), so the app's ETL reads those sheet raw tabs through the service account until the sync jobs are repointed at Postgres.

Dead or superseded (not rebuilt): Campaign Spend by Product (static snapshot, superseded by per-product Meta bands), Sheet10 (scratch), `Assumptions - DEAD` references in P&L A6/B94, the scenario selector, Cash Flow / Financing Scenarios / Scenario Analysis / scenario Rev tabs (already deleted).

## Model hygiene found (affects the build, not alarms)

1. Forward drivers are typed constants: spend $350K from Oct-26 to Dec-29 (P&L row 51), NC-CPA $95, NC-AOV $69, Amazon $175K flat. The "Actual/Projected" flag is hard-coded to Jul-26 while Aug–Oct 26 NC revenue, spend and Amazon are also hand-typed actuals. The app derives actual-vs-projected from loaded data instead.
2. `P&L!W17` (Sep-26) blends 50% waterfall + 50% current-month pacing, wired to one column, so it is stale. The app uses the waterfall for projected months and real actuals for closed months; this will show as a reconciliation difference.
3. Cost-rate lines use trailing Apr–Jul-26 actuals (`sum(R:U)`), so they drift every time a month is pasted. The app stores the window as a lever (default last 4 closed months).
4. Hist Waterfall applies the live curve to historical cohorts too, so the sheet's "repeat revenue" for closed months is modeled, not actual, except typed months. The app reports both (modeled and actual from the cohort table).
5. Spend-ROAS Matrix points at a deleted tab; `P&L` row 128 ("Cash after Inventory Buys") is `#REF!` for Jan-25 to Aug-26.

## Architecture

- Pure-Python deterministic engine, `app/projection/`: `run(scenario, inputs) → Result` over 60 months (Jan-25 → Dec-29), computed per request (60 × ~130 lines is milliseconds), no cached numbers to go stale.
- Inputs in Postgres (`proj_*`): cohort table, QuickBooks actuals by line × month, scenario levers and monthly series, POs, vendor costs, snapshots of on-hand stock. Scenarios are named lever sets with a base to clone from; per-month overrides replace the sheet's typed cells. Every edit is audited (who, key, month, old, new).
- Write path: the app's pool is read-only by design. Projection adds a separate read-write connection in its own module; the Tag Explorer code path stays read-only.
- UI: `/projection` with a module nav, scenario picker, editable monthly grid (yellow cells = inputs), asterisks on projected values, currency formats, one-line source caption per table, scenario compare, charts.
- Reconciliation: `scripts/projection/reconcile.py` runs the engine with the sheet's own typed inputs (seeded as the "Current plan" scenario) and diffs every P&L line × month against the sheet's values; tolerance 0.5%, differences listed with the cause. A frozen snapshot of the sheet (`~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03/`) is the test fixture; a live diff re-reads the sheet.
- Deploy: new migrations numbered 100+ so they never collide with Tag Explorer migrations; same web and ETL services; nothing goes to the live URL until the reconciliation report is clean and Josh has seen it locally.

## Build order and agents

1. Foundation (one agent): migration 100, read-write helper, scenario/series/audit store + API, module registry, `/projection` UI shell, sheet snapshot, reconcile harness.
2. P1 in parallel: `cohorts` (cohort table, curves, waterfall), `drivers` (spend plan, NC revenue, Amazon/TikTok, actual-vs-projected boundary), `pnl` (costs → NOI, FY/TTM/EV, QuickBooks upload, history).
3. P2 in parallel: `inventory-demand`, `inventory-supply`, `financing` (debt, cash, payback, matrix, payout block).
4. P3: `pacing`.
5. Integration, full reconciliation, QA agent (numbers + Playwright on the real page), skeptic pass, PR.

## Decisions (defaults in bold; proceeding on these)

1. Scope: **everything model-related in the three phases; Klaviyo/fulfillment/transit tabs stay out.**
2. Scenarios: **named lever sets in Postgres, seeded with "Current plan" = today's sheet inputs; clone for Bull/Bear.** The July Base/Bull/Bear/Apocalypse definitions no longer exist in the sheet.
3. Actuals: **QuickBooks P&L and the cohort export stay pasted exports, via upload pages; no QuickBooks API.** Meta from the existing ETL; Upstack/ExpandFi/Amazon/Warehance via the sheet's raw feed tabs for now.
4. Editing: **levers and monthly inputs are edited in the app (write path, audit log); the sheet becomes read-only reference.** Access: **no password on projection, same as Tag Explorer (Josh's call, 2026-11-03): it follows the app's auth setting; guard rails are an audit log with restore, delete confirmation, write rate limits and noindex.**
5. Cutover: **sheet untouched; retire it only after three consecutive monthly refreshes tie within 0.5% per line. Media-buyer payout block included.**
