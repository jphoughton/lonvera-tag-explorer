# Lonvera Projections sheet: findings from the rebuild

Compiled 2026-11-04 from the module reports (frozen snapshot 2026-11-03). "App" = what the rebuilt model does today. Impact is on the current plan unless stated. Decision = needs Josh.

## Changes the numbers (decision-relevant)

| # | Where in the sheet | Finding | Impact | App behavior | Decision |
|---|---|---|---|---|---|
| 1 | `COGS!L44` ("Forward COGS % Rev") | Forward cost per bottle $3.53 is divided by the OLD average sale price $29.22 (`L36`), not the current $21.62 (`L37`). 12.08% vs 16.33% | about $0.44M of FY27 cost, 29% of FY27 net operating income ($1.50M) | Reproduces the sheet's 12.08%; both prices are settings | Which price should forward COGS use? |
| 2 | `P&L!AI120` (Sep-27 tax) | `((BM100/12)*4)*0.21` re-applies 4/12 to a figure that is already the Sep-Dec 2026 total. Tax $63,927 vs $191,782 | Cash overstated by a constant $127,855 from Sep-27; lowest ending cash -$1.226M (Dec-28) vs -$1.098M in the sheet | Uses the correct tax; the sheet value is available as an override | Confirm the correct tax |
| 3 | `P&L!X110` | Oct-26 starting cash typed 0, dropping the Sep-26 shortfall of -$85,475 from every later month | Cash overstated $85K throughout | Seeded as an override, clearable | Keep or clear |
| 4 | `Hist Waterfall!B6:B35` | Typed new-customer revenue is stale: -4.2% to +5.5% vs new customers x first order per cohort (29 months) | Repeat revenue up to 1.9% off; FY27 NOI $1.526M (app) vs $1.502M (sheet) | Uses the cohort table | none |
| 5 | `Hist Waterfall` layer 5, rows 336-339 | Point at the Jul-26 cohort's row; show $426,701 of repeat revenue for Nov-26 that no cohort has earned | Actual-repeat view wrong after Aug-26 | Reads each cohort's own increments | none |
| 6 | `Amazon Daily Units` tab | Last refreshed by hand Oct 13 (`inventory_rebalance` has only run as a dry run); runs above `Amazon - Product Daily` by 3.1% (Sep) and 7.2% (Oct 1-9) | Amazon units/day 139.5 -> 122.5 (Vital Bull 43.8 -> 31.4, Boneflex 41.0 -> 32.1, Beef Liver 54.7 -> 59.0); FY27 units -1.3% | Uses `Amazon - Product Daily` (ties to monthly history within 0.02%) | Confirm |
| 7 | `Intl Daily Units` tab | Stale since Oct 12 (22 days); 27 estimated days in the baseline | International is ~37% of units | Shown stale; server-side Warehance feed will fill the estimate | How does the overseas 3PL report? |
| 8 | `Warehance Reorder` / `Intl Reorder` | Intl lead time hard-coded 30 days; its own input says 60 | Intl order-by dates 30 days later than they should be | Uses 60 | none |
| 9 | Stock counts | FBA and international stock counts are dated 2026-10-13 (21 days old; international is an estimate) | App shows Vital Bull at the international 3PL out Nov 4, order was due Aug 22 | Flags as stale | Enter fresh counts |
| 10 | `Purchase Orders` | Rows 7-9 (45,000 units) past land date, not marked landed; rows 2, 4, 5, 6 (41,000 units) landed before this month and in neither stock nor inbound; rows 2-3 possible duplicate 5,000 Vital Bull; rows 13-15 order dates already past | Inventory and cash timing | Shown as warnings, not changed | Review each |
| 11 | July 2026 ad spend | Typed $794,965 vs QuickBooks Meta + Google $888,015 | $93K | Uses books where loaded | Which is right? |

## Formula bugs and stale constants (fixed or flagged in the app)

| # | Where | Finding | App behavior |
|---|---|---|---|
| 12 | Marketing software line | Divides Apr-Jul cost by Feb-May revenue; runs 2% high ($83-123/month) | Correct window; tolerance noted |
| 13 | `P&L!W17` | Sep-26 repeat revenue blends 50% waterfall with 50% current-month pacing, wired to one column | Not reproduced (waterfall for projected months) |
| 14 | `P&L` row 9 | Actual/Projected flag hard-coded to Jul-26 while Aug-Oct values are hand-typed | Boundary derived from loaded data |
| 15 | Sheet FY2026 NOI cell | Sums Sep-Dec only | App reports the full year ($2.67M) |
| 16 | Term loan schedule | Typed principal totals $2,999,000 of $3,000,000; "Months to Full Payoff" is #N/A | Reproduced with a warning |
| 17 | Tax months | Inconsistent (Sep-27, Jun-28, Sep-29); tax base ignores interest deductibility | Lever for deductibility (default off) |
| 18 | Aug/Sep-26 channel split | Blank | Typed total split by the mix lever |
| 19 | Aug-Oct 2026 typed new-customer revenue | Differs from the cohort table by +3.7%, -0.2%, +2.7% | Cohort table drives |
| 20 | `#REF!` cells | `P&L` row 128 (C..V), `P&L!B94`, `COGS!C22:C24`, `Term Debt!B67:B69`, Spend-ROAS Matrix, Campaign Spend by Product | Not reproduced; Spend-ROAS rebuilt |
| 21 | Cohort count for the live curve | Row label says "Last 3 Cohorts Avg" and the formula ties at 3; the decision log says the live curve moved to the last 6 on 2026-11-03 | Lever, default 3 | 

## Not bugs, but the weakest assumptions

- A flat $95 cost per new customer at any spend level (no spend elasticity); the ROAS slide lever is the only brake.
- Spend plan: $350K/month flat from Oct-26 to Dec-29, Amazon revenue $175K flat; FY27 revenue $10.3M vs FY26 $18.9M (harvest posture).
- Transfers 4-6 arrive before they ship; cancelled orders counted as inbound in the sheet; Vital Flow valued at $0 (no vendor cost); Vital Belle has no vendor price so the planner proposes nothing.

Pacing findings are added when the pacing module reports.
