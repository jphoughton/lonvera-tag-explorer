# Skeptic report: Lonvera projection app vs the sheet

Written 2026-11-04 by an agent that built nothing. Method: an independent model written from the frozen sheet snapshot (2026-11-03) without importing app code, a throwaway local copy of the database (`tagexp_proj_skeptic`) for app behavior tests, read-only GETs on the live app, and the sheet's own raw feeds in the snapshot. Scripts: `/private/tmp/claude-501/-Users-joshhoughton-Desktop-Google-Sheets/932f404b-d705-460f-8d9b-9b06f5b4c739/scratchpad/skeptic/` (`indep.py`, `model.py`, `cash.py`, `sens*.py`, `backtest.py`, `backcast.py`, `invsim.py`, `apptests.py`). Nothing was written to the sheet, Meta, or the live app.

Live numbers quoted below reflect Josh's edited `current` plan (version 120). Audit window of those edits: 193 anonymous writes between 05:36 and 05:49 UTC on Nov 4 (21:36 to 21:49 PT Nov 3). Everything else is the seeded plan of record (sheet inputs).

## TLDR

1. The arithmetic is right. My independent model matches the app to the dollar for FY27, FY28, FY29 revenue and NOI, the 3x to 8x values, and the Nov 2026 to Dec 2027 repeat waterfall. It matches the sheet to the cent when fed the sheet's own typed inputs. The model does what it says. The question is whether its inputs are right.
2. The plan of record's FY27 NOI of $1.526M survives only because two input errors offset. (a) Realized cost per new customer is $111 over the last 28 days, up four months running, against $95 in the plan: FY27 NOI -$426K. (b) The plan deducts 7% "sales tax" from DTC revenue that is already net of tax and refunds (it ties to QuickBooks within 1% for 19 months): FY27 NOI +$535K. Fix only one and you move the answer by half a million dollars.
3. Cash is the weaker side. The typed term-loan schedule pays $2.999M principal by Dec 2028 and cash is negative in 25 months, bottoming at -$1.17M. Quarterly tax payments make it -$1.39M. The loan schedule is not a plan the cash flow can carry.
4. FY27 is the peak of a declining NOI path (FY27 $1.53M, FY28 $1.06M, FY29 $0.89M). The $7.5M offer is 4.9x FY27, 7.1x FY28, 8.4x FY29. The valuation basis also rewards cutting spend: zero spend gives FY27 NOI $2.35M.
5. Several app surfaces can mislead: the Payback chip says 10 months (fully loaded it is 19), the live plan of record is one mutable object that anyone can edit and that clones inherit from, and (at HEAD and on live) a failing module is replaced by snapshot numbers with no flag.

## 1. Independent recomputation

Tolerance for "agree" is $1 per cell. My model: cohort table to live curve (last 3 cohorts per age, tail 0.95 from age 28, 60-month cap) to waterfall; spend x AOV / CPA for new customers; the sheet's trailing Apr to Jul-26 cost rates and fixed lines; the sheet's typed principal and inventory-buy rows for cash.

| Item | Independent | App (local seeded copy) | Sheet | Agreement |
|---|---|---|---|---|
| Live curve, ages 1 to 60 | max abs diff 7e-18 | same | `Curves!C4:C63` | exact |
| Lifetime multiple | 1.5788 | 1.5788 | 1.5788 | exact |
| FY27 revenue / NOI | $10,379,147 / $1,526,273 | same | $1.502M NOI | app = mine exactly; sheet +/-1.6% (see below) |
| FY28 revenue / NOI | $9,670,857 / $1,057,565 | same | n/a | exact |
| FY29 revenue / NOI | $9,416,524 / $889,262 | same | n/a | exact |
| FY27 gross profit | $6,947,703 | $6,947,703 | n/a | exact |
| FY26 NOI | $2,709,410 | $2,693,808 | sums Sep to Dec only | +0.6%: Aug to Oct typed overrides; unclosed |
| Value 3x / 5x / 8x of FY27 NOI | $4.579M / $7.631M / $12.210M | same | n/a | exact |
| Repeat revenue Nov-26, Dec-26, Jan-27 ... Dec-27 | 684,038 / 601,530 / 570,840 ... 460,744; FY27 sum 6,018,831 | same, every month | 674,650 / 593,708 / 565,233 for Nov to Jan | app exact; sheet gap explained |
| Lowest ending cash | -$1,169,735 (Dec-28), 25 negative months | -$1,173,778 (Dec-28) | -$1,097,692 | mine vs app $4.0K (Oct-26 NOI treatment); sheet gap $60K from the sheet's lower NOI and its one wrong tax cell |
| Payback month | 10 (cum CM $105.56 vs CAC $105.49) | 10 | 10 | exact |
| Nov 2026 pacing goal: revenue / NOI | $1,034,625 / $239,485 | same | $1,025,894 / $233,786 | app exact; sheet -0.8% / -2.4% |

Why the sheet differs from the app by +$24K FY27 NOI: I ran my waterfall on the sheet's typed new-customer revenue (`Hist Waterfall!B6:B35`) and it reproduces `P&L` row 17 for Nov-26 to Dec-29 with max difference $0.00. So the whole gap is the stale typed revenue the app replaced with the cohort table. Closed. The sheet's lowest-cash gap I could not close to the dollar; it is the same NOI gap flowing through 25 months plus the Sep-27 tax cell, and I make no stronger claim.

Months of cover (stock count / trailing 28-day shipments, ignoring inbound; Intl uses 30 days to Oct 12):

| Product | Revamp (count Nov 3) | Amazon FBA (count Oct 13) | Intl 3PL (count Oct 13, an estimate) |
|---|---|---|---|
| Vital Bull | 2.84 | 3.27 (2.58 rolled forward) | 0.12 (out) |
| Beef Liver | 5.68 | 3.97 (3.28) | 1.05 (0.36) |
| Boneflex | 8.92 | 7.64 (6.95) | 0.93 (0.24) |

My month-by-month simulation reproduces the app's first-OUT months: Intl Bull Nov-26, Intl Boneflex Nov-26, Intl Liver Apr-27, Revamp Liver Sep-27. The model's mechanics are right; the credibility problem is the inputs (threat 8).

## 2. Attacks on load-bearing assumptions

All FY27 NOI figures below are from my independent model, one change at a time against the $1,526,273 base. Value at 5x is 5 times the NOI change.

| Test | FY27 NOI | change | FY28 NOI | FY29 NOI | Lowest cash |
|---|---|---|---|---|---|
| Plan of record | 1,526,273 | | 1,057,565 | 889,262 | -1,169,735 (Dec-28) |
| (c) CPA / AOV = last 28 days ($111.17 / $70.55) | 1,100,337 | -425,936 | 547,589 | 343,681 | -2,358,895 |
| (c) CPA $105 | 1,204,980 | -321,293 | 672,879 | 477,718 | -2,007,276 |
| (c) CPA $120, AOV $70 | 862,151 | -664,122 | 262,407 | 38,590 | -3,159,238 |
| (c) $409K spend at $111 (holds 3,482 new customers a month) | 889,225 | -637,048 | 434,516 | 272,143 | -2,432,130 |
| (a) repeat -5% | 1,341,066 | -185,207 | 895,794 | 735,906 | -1,596,647 |
| (a) repeat -10% | 1,155,860 | -370,413 | 734,023 | 582,550 | -2,023,559 |
| (a) forward cohorts retain at 80% of the live curve | 1,206,470 | -319,803 | 604,636 | 379,933 | -2,132,194 |
| (a) N = 6 cohorts instead of 3 | 1,598,676 | +72,403 | 1,091,969 | 902,707 | -1,043,204 |
| (a) tail decay 0.90 / 0.97 | 1,492,849 / 1,543,219 | -33K / +17K | 919,570 / 1,136,686 | 625,384 / 1,067,563 | |
| (a) Goal curve (static 9/29 snapshot, 1.877x) | 2,086,659 | +560,386 | 1,763,204 | 1,590,853 | -85,475 |
| (d) COGS 16.33% ($21.62 price) | 1,085,532 | -440,741 | 646,901 | 489,398 | -1,037,946 |
| (f) Amazon $158.7K flat (28-day feed) | 1,408,613 | -117,660 | 939,905 | 771,601 | -1,446,939 |
| (h) refunds -8% of DTC, if the table were gross | 1,087,978 | -438,295 | 656,766 | 501,927 | -2,207,955 |
| Remove the 7% "sales tax" deduction | 2,061,488 | +535,215 | 1,546,992 | 1,362,247 | -85,475 |

### (a) Retention curve, backtested

Method: for each cutoff month T from Jan to Sep 2026, rebuild the curve using only cells observed by T, predict repeat dollars for every existing cohort in months after T, compare to the cohort table's realized increments.

| Months ahead | 1 | 2 | 3 | 4 | 6 | 9 |
|---|---|---|---|---|---|---|
| Predicted over actual, N=3 (the plan's method) | +5.5% | +5.2% | +14.9% | +24.0% | +44.9% | +40.2% |
| Same, N=6 | +8.9% | +8.6% | +19.9% | +28.1% | +43.3% | +44.3% |
| Same, N=1 (latest cohort only) | +3.1% | +3.3% | +11.4% | +18.3% | +38.6% | +38.9% |

By age (N=3, pooled): age 1 +15%, age 2 -11%, age 3 0%, ages 4 to 6 +16%, ages 7 to 12 +32%, ages 13 to 24 +27%, ages 25 to 36 -32% (small old cohorts). One-month-ahead error by target month: Feb +6%, Mar +5%, Apr +9%, May +13%, Jun +13%, Jul -7%, Aug -2%, Sep -1%, Oct +16%.

Reading, with confidence words: the method has over-predicted at every horizon. The long-horizon miss is mostly two outlier vintages (May and Jun 2025 cohorts retained 1.4x to 1.5x the curve) that fed ages 7 to 24 in early-2026 curves; the current curve no longer leans on them, so the live bias is probably smaller than the backtest. But the newest in-sample months still miss: the model's October repeat $737,889 is +11.0% above what the cohort table says October earned ($664,586), even though October's cells are inside the curve. 2026 cohorts (Apr to Aug) sit 1% to 11% below the live curve at ages 2 to 6. Consistent with a calendar shock to renewals (Josh's own note: renewals per due 0.84 to 0.70 after the Sep 14 billing-reminder flow); I did not test that cause. The test that settles it: November repeat actual against the $684,038 goal on Nov 30, and Skio renewals per due by week.

### (b) Repeat vs actuals

Cohort-table actual repeat vs the app's modeled figure: Aug typed $984,287 vs $839,643 (+17%; the typed Aug figure is Upstack gross, not net); Sep $784,041 vs $809,317 (-3.1%); Oct $737,889 vs $664,586 (+11.0%). Projected months are consistent with the books only for Sep.

### (c) New-customer revenue

Plan: $350K total marketing, 94.5% on Meta and Google, CPA $95, AOV $69, so 3,482 customers and $240K a month, for 38 months. Realized (Pacing - Daily tab, complete days):

| Window | Ad spend a month | CPA | AOV | NC ROAS |
|---|---|---|---|---|
| Last 14 days | $347K | $113.26 | $68.59 | 0.606 |
| Last 28 days | $357K | $111.17 | $70.55 | 0.635 |
| Last 56 days | $441K | $102.55 | $70.77 | 0.690 |
| Last 84 days | $534K | $101.96 | $69.14 | 0.678 |
| Plan | $331K | $95.00 | $69.00 | 0.726 |

Monthly CPA: Jul $88.4, Aug $96.4, Sep $97.5, Oct $107.8. AOV is fine; CPA is the problem. Spend is already near plan, so it is not that spend is cut. Holding the plan's 3,482 customers a month at $111 needs about $409K of ad spend, and that lowers FY27 NOI to $889K because the first order does not pay back. Spend elasticity: across 48 weeks log(CPA) on log(spend) has slope -0.21 (r -0.65), so higher-spend weeks had lower CPA; controlling for a time trend it is -0.12 with CPA drifting +0.55% a week. Buyers set spend in reaction to CPA, so this does not show causation; it does show the drift is with time, not with spend. Q4 seasonality: cohort-table CAC ran $77.4 in Oct-25, $91.9 in Nov, $86.7 in Dec (+19%, +12%); the plan has no seasonal CPA.

### (d) COGS basis

The data supports $29.22 (12.08%), not $21.62 (16.33%), as a blended number. Evidence: realized net revenue per unit shipped across all channels was $29.08 (Aug) and $29.89 (Sep); the purchase-order plan lands units at a blended $3.40 a unit over the next 12 months (46.6% from KeyStone) against the model's $3.53; books COGS was 9.5% to 9.8% of revenue in May to Jul 2026 (13.2% in Apr). 12.08% is above the last three booked months and in line with the cost-per-unit and price-per-unit data. Not testable here: bottles per first order, so the $21.62 figure may be right for first-order economics only. FY27 NOI under 16.33% is $1.086M (-$441K) if wrong.

### (e) Term loan and cash

The sheet's cash arithmetic reproduces, and it says: typed principal totals $2,999,000 by Dec 2028, interest total $575,900, cash below zero in Sep 2026 and then in every month from Jan 2028 through Dec 2029 (25 months), lowest -$1,169,735 in Dec 2028 and still -$436,214 at Dec 2029. Cash is positive through 2027 (peak about $461K); the hole opens in 2028 when $1.47M of principal, $321K of tax and $1.53M of inventory buys land against $2.2M of operating cash.

| Variant | Lowest cash |
|---|---|
| Plan of record | -$1,169,735 (Dec-28) |
| Taxes paid quarterly in the year earned (the app and sheet pay 9 to 18 months late) | -$1,391,824 |
| Oct-26 opening cash carries the Sep shortfall of -$85,475 (the sheet types 0) | -$1,255,210 |
| No tax at all | -$651,247 |
| Pay principal only as cash allows (floor $50K) | loan balance still $1.10M at Dec-28; interest $717K |

To keep cash at or above zero from Nov 2026 the plan needs $44,990 a month of extra cash flow ($540K a year, 35% of FY27 NOI). Cash also omits working capital, deal costs, and the fact that the Sep 2026 close month has already passed (today is Nov 3). The "lowest cash" headline is not comparable across plans: the live edited plan deleted six principal cells and reads "cash bottoms at -$85.5K" while $1.241M of 15% debt stays unpaid.

### (f) Amazon

Plan $175K flat for 38 months. The feed (ExpandFi sales, which tracked QuickBooks Amazon revenue at 0.98x on average through Jul-26) shows Aug $207K, Sep $169K, Oct $163K, last 28 days $158.7K a month; Nov 1 to 3 projects $154K. The typed Aug to Oct figures in the P&L ($218K, $197K, $193K) run +5%, +17%, +18% above the feed, so either the typed basis differs or the feed is incomplete. Unresolved. Units per day are falling for Vital Bull and Boneflex (28 vs 90 days: -21%, -23%).

### (g) Inventory

Shipments per day, last 7 / 28 / 60 / 90 days. US direct: Bull 221 / 225 / 308 / 302, Liver 261 / 266 / 267 / 293, Boneflex 146 / 146 / 158 / 190. The forecast holds the 28-day average flat for 36 months while the plan's revenue falls: FY27 revenue $10.4M against about $13.9M gross annualized on the last 28 days, so buys are sized to roughly 25% more units than the revenue plan implies (conservative for cash, about $0.3M of FY27 outlay of $1.32M). The out-by dates are credible for Revamp (count is today's) and not credible for Intl: the count is a 21-day-old estimate; PO rows 4 to 6 (36,000 units, "ordered", land Oct 1) and row 2 (5,000 Bull, in transit, land Oct 15) are in neither stock nor inbound; the 3PL reports 5,038 Bull inbound that the model ignores. Adding row 2's 5,000 or the 3PL's 5,038 still leaves Intl Bull out in Nov-26; adding both moves it to Feb-27. Conversely rows 7 to 9 (45,000 units, Revamp, land Nov 1, not marked landed) count as November inbound and may already be inside the Nov 3 count.

### (h) Refunds

Upstack refunds rose from 4.2% of revenue in Apr to 8.1% in Oct. The cohort table is already net: cohort-table DTC revenue / QuickBooks DTC revenue is 1.003 on average (0.96 to 1.02) for Jan-25 to Jul-26, and cohort DTC is 0.90 to 0.92 of Upstack gross in Jun to Oct 2026. So refunds are not ignored; they are inside the revenue, and the 7% deduction then takes a second cut. If the intent really is a gross basis, refunds at 8% would cost $438K of FY27 NOI; that is not what the numbers are.

## 3. How the app could mislead Josh

Each item was checked with a real call or run. Live items quote evidence from the live app (version 120 edits noted).

| # | Finding | Evidence | Severity |
|---|---|---|---|
| M1 | Payback chip reads 10 months | `Payback` CM is 74.7% of AOV (product 17.26% + $5.53 fulfillment). The P&L's own cost rates give 67.6% (fulfillment 12.95% of DTC, payment 4.1%, chargebacks 0.7%, inbound 1.1%, others). Fully loaded: 19 months at CAC $105.49, 23 at $111.17, none inside 36 with the 7% deduction. Live page: "PAYBACK ON A NEW CUSTOMER 10 months, Acquisition cost $105.49" | high |
| M2 | Plan of record is one mutable object; headlines move with it | Live P&L page: "FY27 net operating income is $1.2M*: $3.6M to $9.6M*" and revenue $13.3M against the seeded $1.526M / $10.4M. Cash & debt: "Cash bottoms at -$85.5K". Pacing goals read the plan of record, so a spend edit moves the goals Josh paces against. No baseline marker says the plan departed from the sheet | high |
| M3 | Clones inherit later base edits for lever groups they never held | Local test: clone of `current`; set COGS price on `current` only; clone FY27 NOI went from $1,526,273 to $1,085,532. The clone's own params held drivers, financing, inventory_demand only; pnl and cohorts levers (n_cohorts, tail_decay) are inherited live. Series cells the clone holds do not leak; cells it deletes or never held do (deleted spend cell came back at the base's $500,000) | high |
| M4 | Failing module becomes snapshot numbers | At the committed HEAD (and the live /run, which has no `degraded` key): renaming `proj_cohorts` locally made `/run` show `cohorts stub:true` and `pnl/summary` returned FY27 NOI $1,500,725 with no flag; the numbers came from the frozen sheet, not the data. The in-tree engine.py change removes this; it is uncommitted and undeployed | high until deployed |
| M5 | Closed months and nonsense inputs accepted | `PUT /series` returns 200 for edits to closed months (no effect, silent), CPA 0, CPA -5 and 1e12. CPA 0 for Jan-27 cut FY27 NOI by $306K | medium |
| M6 | "Fresh" labels | `purchase_orders` freshness is the last edit time of any order or transfer (live: age 0 on Nov 4) while the same page lists 8 data warnings. QuickBooks (95 days), Intl demand (22), FBA and Intl counts (21) are correctly stale | medium |
| M7 | Pacing green on two days of data | Revenue "106%, good" is a projection from Nov 1 to 2 (DTC $33.8K a day against an October average of $32.3K) while CPA and ad spend read "bad" (+11%). Goal and projection both take the 7% deduction, so the comparison is like for like but inherits threat 2. Separately, the typed Aug repeat figure is Upstack gross while other months are net | medium |
| M8 | Lowest-cash headline can be gamed | See (e): deleting principal payments lifts it from -$1.17M to -$85K | medium |
| M9 | Optimism in defaults | Value shown at FY27 (the peak year) and 3x to 8x; headline revenue and NOI carry asterisks (good); live curve default is conservative against Goal | medium |

Checked and fine: restore (back to v2 returned NOI $1,526,273 and all 234 seeded series cells; restoring `current` also moved the clone, consistent with M3); QuickBooks, Intl and FBA staleness chips; the pacing page flags CPA and spend as bad.

## 4. Ranked

### (i) Ten biggest threats to a correct decision

| # | Threat | $ at stake | Likelihood | Evidence | Cheapest test |
|---|---|---|---|---|---|
| 1 | CPA above plan and rising | FY27 NOI -$426K, FY28 -$510K, value at 5x -$2.1M; cash gap to -$2.36M | high | measured: $88 Jul, $96 Aug, $98 Sep, $108 Oct, $111 last 28 days; Q4 CAC ran +12% to +19% over Oct last year | watch weekly CPA through Nov 30; run one two-week $250K cell to see if CPA falls with spend |
| 2 | 7% "sales tax" deduction on net revenue | FY27 NOI +$535K, value at 5x +$2.7M, lowest cash to -$85K | high | 19-month tie-out ratio 1.003; backcast Jan to Jul-26 with the deduction understates QuickBooks NOI by $104K a month (-$728K total), without it +$4K a month (+$31K total) | open the Aug QuickBooks DTC revenue: net basis reads about $1.36M, the typed figure is $1.499M |
| 3 | Repeat revenue optimism | -5% is -$185K FY27 NOI (-$0.9M value at 5x); -10% is -$370K | medium-high | backtest +5.5% at 1 month, +14.9% at 3; Oct in-sample +11.0%; 2026 cohorts 1% to 11% under the curve | Nov 30 actual repeat against $684,038 |
| 4 | Typed paydown the cash cannot carry | funding gap $1.17M ($1.39M with quarterly tax); needs $540K a year more | high | arithmetic; 25 negative months | decide amortization, equity or seller-note terms before close; replace typed principal with a sweep |
| 5 | Valuation basis | offer is 4.9x FY27, 7.1x FY28, 8.4x FY29; zero spend floor FY27 to FY29 NOI $4.34M (7% kept) | high | NOI path falls 31% then 16%; FY27 NOI rises as spend falls | value on FY28 NOI or a through-cycle number |
| 6 | Payback understated | decisions to scale spend | high | M1 | use the loaded CM in the chip |
| 7 | Amazon $175K flat | FY27 NOI -$118K; basis gap $30K a month unresolved | medium | feed $158.7K 28-day; typed vs feed +17% to +18% in Sep and Oct | QuickBooks Amazon income for Sep and Oct |
| 8 | Inventory inputs | out-by dates and about $0.3M FY27 buys | medium | stale estimate counts; 41,000 + 45,000 units in unclear status; demand flat vs falling revenue | portal count of Intl; mark rows 2 to 9 landed or not |
| 9 | Plan of record mutable by anyone; clones inherit | misread headline of $0.3M to $0.4M NOI | medium | M2, M3 | lock `current` as a baseline; copy lever groups into every clone on creation |
| 10 | Snapshot fallback on module failure | any number | low-medium | M4 | deploy the in-tree engine change |

### (ii) What survived, with confidence words

| Claim | Survived? | Wording the evidence supports |
|---|---|---|
| Engine arithmetic, waterfall, EV table, payback arithmetic | yes | verified exactly by independent code; "ties" |
| Live curve construction (N=3, tail 0.95, 60-month cap) | yes, with a bias flag | N=3 vs 6 is within +/-$72K and 0.4% of lifetime; tail decay 0.90 to 0.97 moves FY27 by -$33K to +$17K; the backtest bias is "consistent with" optimism, not proof |
| COGS 12.08% | yes | the data supports it; 16.33% is rejected as a blended rate |
| Restore and seeded data | yes | tested on a local copy |
| Stale-feed flags for QuickBooks, Intl, FBA | yes | present and correct |
| Pacing flags CPA and spend as bad | yes | present |
| "Refunds are ignored (gross basis)" | no | the cohort basis ties to net revenue; refunds are inside it |
| "FY27 NOI $1.526M" | partly | right number, wrong reasons: two errors of about $0.5M cancel |
| Payback 10 months | no | 19 months loaded |
| Cash picture | no | not feasible without more capital or a slower schedule |

### (iii) The app's differences from the sheet (`SHEET_FINDINGS.md`)

| # | Endorse / reject / flag | With numbers |
|---|---|---|
| 1 COGS price | endorse showing both, keep $29.22 | 12.08% supported; $0.44M if wrong |
| 2 Sep-27 tax fix | endorse | tax $191,782 not $63,927; but flag timing (quarterly adds -$222K) and interest not deductible (conservative) |
| 3 Oct-26 opening cash typed 0 | reject | carrying the Sep shortfall lowers every later month by $85,475 |
| 4 Typed new-customer revenue replaced by cohort table | endorse | +$24K FY27 NOI, proven by the $0.00 waterfall tie on sheet inputs |
| 5 Layer-5 pointer bug | endorse | display only |
| 6 Amazon Product Daily over Daily Units | endorse | but see (f): typed P&L Amazon is a different basis |
| 7 Intl stale | endorse | stale shown; nothing fills it |
| 8 Intl lead time 60 | endorse | moves order-by 30 days earlier |
| 9 and 10 stock counts and PO status | flag | 41,000 and 45,000 units, M-level inputs |
| 11 July spend typed vs QuickBooks | endorse books | $93K |
| 12 to 14, 18 to 20 | endorse | immaterial or display |
| 15 FY26 NOI full year | endorse | |
| 16, 17 loan schedule and tax months | flag | $1,000 short; tax paid 9 to 18 months late |
| 21 cohort count | endorse N=3 | N=6 is +$72K |
| (not listed) 7% sales tax | reject | +$535K FY27 NOI |

## 5. What I could not verify

- The Aug QuickBooks close (due Oct 15), the real Intl count, whether PO rows 2, 4 to 9 landed, Skio renewals per due, bottles per first order, the intended deal close date, and the basis of the typed Amazon revenue.
- Josh's intent on a gross revenue basis: I only show what the numbers tie to.
- Deployed code beyond API behavior; Python 3.12 behavior in production.
- I used the frozen snapshot, not a fresh live-sheet read. The live app's FY26 differs from the snapshot copy (NOI $2.577M vs $2.694M at first read) because the live app uses Meta-feed actuals for Aug to Oct spend; that does not touch FY27.
