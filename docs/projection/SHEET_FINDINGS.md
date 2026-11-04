# Lonvera Projections sheet: findings from the rebuild

Compiled 2026-11-04 from the module reports (frozen snapshot 2026-11-03). "App" = what the rebuilt model does today. Impact is on the current plan unless stated. Decision = needs Josh.

## Changes the numbers (decision-relevant)

| # | Where in the sheet | Finding | Impact | App behavior | Decision |
|---|---|---|---|---|---|
| 1 | `COGS!L44` ("Forward COGS % Rev") | **Superseded by the skeptic pass (2026-11-04): the sheet's 12.08% is supported.** It divides forward cost per bottle ($3.53) by $29.22; realized net revenue per unit shipped is $29.08-$29.89, the purchase-order plan lands units at $3.40, and books show 9.5-9.8% in May-Jul. The 16.33% ($21.62 current price) is rejected as a blended rate. Untested: bottles per first order. | none (earlier $0.44M claim is void) | Keeps the sheet's 12.08%; both prices stay settings | Closed unless bottles-per-first-order says otherwise |
| 2 | `P&L!AI120` (Sep-27 tax) | `((BM100/12)*4)*0.21` re-applies 4/12 to a figure that is already the Sep-Dec 2026 total. Tax $63,927 vs $191,782 | Cash overstated by a constant $127,855 from Sep-27; lowest ending cash -$1.226M (Dec-28) on the sheet's own NOI inputs, -$1.174M with the app's integrated NOI, vs -$1.098M in the sheet | Uses the correct tax; the sheet value is available as an override | Confirm the correct tax |
| 3 | `P&L!X110` | Oct-26 starting cash typed 0, dropping the Sep-26 shortfall of -$85,475 from every later month | Cash overstated $85K throughout | Seeded as an override, clearable | Keep or clear |
| 4 | `Hist Waterfall!B6:B35` | Typed new-customer revenue is stale: -4.2% to +5.5% vs new customers x first order per cohort (29 months) | Repeat revenue up to 1.9% off; FY27 NOI $1.526M (app) vs $1.502M (sheet) | Uses the cohort table | none |
| 5 | `Hist Waterfall` layer 5, rows 336-339 | Point at the Jul-26 cohort's row; show $426,701 of repeat revenue for Nov-26 that no cohort has earned | Actual-repeat view wrong after Aug-26 | Reads each cohort's own increments | none |
| 6 | `Amazon Daily Units` tab | Last refreshed by hand Oct 13 (`inventory_rebalance` has only run as a dry run); runs above `Amazon - Product Daily` by 3.1% (Sep) and 7.2% (Oct 1-9) | Amazon units/day 139.5 -> 122.5 (Vital Bull 43.8 -> 31.4, Boneflex 41.0 -> 32.1, Beef Liver 54.7 -> 59.0); FY27 units -1.3% | Uses `Amazon - Product Daily` (ties to monthly history within 0.02%) | Confirm |
| 7 | `Intl Daily Units` tab | Stale since Oct 12 (22 days); 27 estimated days in the baseline | International is ~37% of units | Shown stale; server-side Warehance feed will fill the estimate | How does the overseas 3PL report? |
| 8 | `Warehance Reorder` / `Intl Reorder` | Intl lead time hard-coded 30 days; its own input says 60 | Intl order-by dates 30 days later than they should be | Uses 60 | none |
| 9 | Stock counts | FBA and international stock counts are dated 2026-10-13 (21 days old; international is an estimate) | App shows Vital Bull at the international 3PL out Nov 4, order was due Aug 22 | Flags as stale | Enter fresh counts |
| 10 | `Purchase Orders` | Rows 7-9 (45,000 units) past land date, not marked landed; rows 2, 4, 5, 6 (41,000 units) landed before this month and in neither stock nor inbound; rows 2-3 possible duplicate 5,000 Vital Bull; rows 13-15 order dates already past | Inventory and cash timing | Shown as warnings, not changed | Review each |
| 11 | July 2026 ad spend | Typed $794,965 vs QuickBooks Meta + Google $888,015 | $93K | Uses books where loaded | Which is right? |

## Added by the skeptic pass (2026-11-04), decision-relevant

| # | Finding | Impact | Test that settles it |
|---|---|---|---|
| S1 | **7% sales-tax deduction.** History months carry no deduction (`P&L` row 21 is 0 before Jul-26) and the model's DTC revenue is 96.6% of QuickBooks DTC over Jan-25 to Jul-26 (97.8-98.3% recently), so the cohort table is not tax-inflated; from Jul-26 the formula deducts 7% on top. Skeptic backcast Jan-Jul-26: with the deduction NOI is $728K below books, without it $31K above | Removing it: FY27 NOI +$535K (to about $2.06M), value +$2.7M at 5x, lowest cash -$85K instead of -$1.17M | Aug QuickBooks close (model net about $1.36M vs typed $1.499M); or set `drivers.sales_tax_rate` to 0 |
| S2 | **CPA drift.** Plan $95; measured $111 (last 28 days), $108 Oct, $97 Sep, $96 Aug, $88 Jul; Q4 ran +12% to +19% over Oct last year | At $105-111: FY27 NOI -$426K, FY28 -$510K, value -$2.1M | Weekly CPA through Nov 30 |
| S3 | **Cash.** Typed term-loan schedule pays $2.999M by Dec-28; cash negative Sep-26 and 25 months (Jan-28 to Dec-29), -$1.17M at Dec-28 (-$1.39M with quarterly tax); needs about +$45K/month | Size equity / seller note / amortization before close | Depends on S1 |
| S4 | **Repeat revenue.** Curve method backtests +5.5% over at 1 month, +15% at 3, +45% at 6; October +11% over the cohort table in-sample | Each -5% repeat = -$185K FY27 NOI | Nov 30 repeat vs $684,038 |
| S5 | **Valuation basis.** $7.5M is 4.9x FY27, 7.1x FY28, 8.4x FY29 NOI (NOI falls 31% then 16%); zero spend gives FY27 NOI $2.35M, so FY27 rewards cutting spend | Value on FY28 or a through-cycle number | n/a |
| S6 | **Payback chip.** 10 months is payback on product margin; fully loaded with the P&L's own cost rates it is 19 months (23 at $111 CPA) | Mislabelled | Relabel, show both |
| S7 | Amazon revenue $175K flat vs $158.7K feed run-rate; typed Aug-Oct run +5/+17/+18% above the feed | -$118K NOI | Resolve the typed figures |
| S8 | App flaws: clones silently inherit later edits to the base (a base COGS-price edit moved a clone's FY27 NOI from $1.526M to $1.086M); CPA 0, negative and closed-month edits accepted silently (CPA 0 cut FY27 NOI $306K); the plan of record is editable by anyone | Fix: clone = frozen copy; validation; plan-of-record guard (in progress) | Post-vet fixes |

Engine arithmetic verified exact by independent code: FY27 revenue $10,379,147 / NOI $1,526,273, FY28 NOI $1,057,565, FY29 $889,262, 3x-8x values $4.58M-$12.21M, payback 10 months (product margin), Nov goal $1,034,625 revenue / $239,485 NOI. The 3 vs 6 cohort question is immaterial (+$72K, 0.4% of lifetime): stay at 3.

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
