# Financing ("Cash & debt")

What it replaced: `Term Debt Monthly Paydown`, `P&L` rows 108-131 (cash waterfall), `Payback`, `Spend-ROAS Matrix` and the scale-payback / media-buyer payout block `P&L!E177:P206`. Page: `/projection/financing`.

## Files

```
app/projection/financing/__init__.py        MODULE (key financing, ui financing.js, blueprint at /api/projection/financing)
app/projection/financing/params.py          defaults (the sheet's typed cells), merge with the scenario, validation of lever patches
app/projection/financing/calc.py            pure functions: debt_schedule, seller_note, tax_payments, cash_waterfall, payback, spend_roas_matrix, payout_block
app/projection/financing/model.py           load_inputs / compute -> FinancingDetail (a FinancingResult plus everything the page reads)
app/projection/financing/tables.py          grid payloads (cash, debt) and the payback / matrix / payout payloads
app/projection/financing/api.py             blueprint
app/projection/financing/reconcile_map.py   47 lines against Term Debt, P&L 108-131 / 177-206 and Payback
etl/projection_financing.py                 seed from the sheet snapshot; emits migrations/160_proj_financing.sql (--sql), --check, --apply
migrations/160_proj_financing.sql           seeds the Current plan (params + 23 series cells); creates no tables (see QUESTIONS_financing.md 1)
migrations/161_proj_financing_payback_store.sql   one store for the payback inputs (below)
app/static/js/projection/financing.js       the page
tests/projection/financing/                 240 tests (own database tagexp_proj_financing)
```

## Inputs and outputs

Reads upstream by name: `pnl.lines['noi']` and `['china_cogs']` (the stub's `cogs` is accepted for the add-back) plus the variable-cost lines for the loaded payback, `inventory.outlay` (cash by month), `cohorts.curve_live` / `curve_goal` (ages 1..60), `drivers.nc_aov` / `nc_cpa` / `actual_through` (payback fallback only). `pnl` and `inventory` are required (compute has no default for them), so a failed upstream degrades this module (503 `upstream_degraded` through `degraded.ensure_ok`) instead of showing cash built on zeros. Without `cohorts` the payback, grid and payout read "unavailable".

Returns `FinancingDetail(FinancingResult)`. `lines` (monthly series from the close month; earlier months are absent): `interest`, `principal`, `balance`, `loan_payment`, `tax`, `equity`, `cash_after_inventory`, `ending_cash` (the two are the same number, as in the sheet), `noi_before_inventory`, `opening_cash`, `inventory_buys`, `net_cash_flow`, `headroom`, `seller_principal`, `seller_interest`, `seller_balance`. Extras: `debt` (schedule lists), `seller`, `seller_flags`, `cash` (lowest month and value, first negative month, counts), `payback`, `matrix`, `payout`, `summary`, `warnings`, `recon`.

For the pacing module: `from app.projection.financing.calc import payback` (`payback(aov, cac, product_cost_pct, fulfillment, curve, max_age=36) -> Payback` with `.months`, `.months_interpolated`, `.cumulative`, `.cm_first`). It raises `ValueError` on a non-positive AOV, negative CAC, a cost percent outside 0..1 or negative fulfillment.

## Levers and series

`params['financing']` (defaults in `params.py`; `GET /inputs` lists effective values, defaults and sources): `close_month` (2026-09-01), `debt` {principal 3,000,000, rate 0.15, mode typed|fixed_payment, fixed_payment 125,000}, `seller_note` {enabled false, principal, rate, first_payment, installments, ebitda_floor}, `tax` {rate 0.21, deduct_interest false, payments [{month, from, to, label}]}, `cash` {opening, floor}, `payback` {aov, cac, product_cost_pct 0.1726, fulfillment 5.53, max_age 36, curve live|goal}, `matrix` {target_month 3, basis cm|loaded, fixed_cost 110,000, spends, roas}, `payout` {media_spend 800,000, cpa 90, aov 73, lazar 5,000, designer 3,000, samples 150 x $10, creative_production_rate 0.09, cogs_rate 0.14, fulfillment_rate 0.09, repeat_source goal|live|typed, scales, breakpoint, columns}.

Series (`proj_series`, prefix `financing.`): `principal_paid` (input, typed per month; 21 seeded cells from Term Debt column E), `noi_before_inventory` (override; Sep-26 = 150,000), `opening_cash` (override; Oct-26 = 0), `tax` (override), `equity_payback` (input). Every typed value carries its sheet cell in `note`.

## One store for the payback inputs

Product cost (Payback D11), fulfillment per order (B12) and the horizon are the same cells on the Spend plan page and this page. They live in `params['drivers']['payback']` {`product_cost_pct`, `fulfillment_per_order`, `horizon_months`}, the store the Spend plan page and the pacing payback box read. `params.effective` resolves them as: that store, else a legacy `financing.payback.*` copy (scenarios restored from an older version), else the default; a stored value outside the Spend plan's ranges is ignored. `PUT /financing/inputs` with `payback.product_cost_pct|fulfillment|max_age` writes the drivers keys and clears the financing copies in one audited patch (null clears to the default), so an edit on either page moves both. `GET /inputs` returns `payback_sources` (`drivers` | `financing` | `default`). Average order value and cost per customer stay in `financing.payback.aov|cac`, blank by default. Default, in order: a typed value (override); what new customers really cost and spent over the last 28 days (`drivers.reference.load_reference`, the Spend plan page's "Plan $95 vs last 28 days $x" figure: the chosen channels' ad spend over Shopify new customers, and the AOV of the same days from the orders feed; label `last 28 days (Oct 5 to Nov 1), Meta + Google + Shopify feeds`); only if the feeds do not cover the window or the figure is not believable (drivers.reference guards: every counted feed covers at least window - 2 days, CPA $20 to $500, AOV $15 to $400), the drivers' last closed month (`last closed month (Jul 2026)`), which can be a rosy month. Each input falls back on its own, and the panel shows the reference's plain reason (`inputs.cac_fallback_reason` / `aov_fallback_reason`, for example "Meta spend feed has 9 of 28 days"). `GET /payback` returns the headline readings (`result`, `loaded.result`), `inputs.*_source_label`, `inputs.realized` and `plan` (both readings at the spend plan's own AOV and cost per acquisition for the first plan-driven month from the open month); the panel shows both rows, each on both bases. Migration 161 drops the seeded copies (a value somebody changed moves to the single store first, typed AOV and CAC stay) for every scenario; it is idempotent. Reconcile: with AOV and CAC blank the Payback lines compare the app's last closed month with the sheet's month-to-date figures, so `payback_*` lines that depend on them differ by design; typing the sheet's two numbers ties them (tests do this).

## Payback on two bases (skeptic S3 / S6)

`payback(aov, cac, product_cost_pct, fulfillment, curve, max_age=36, *, fully_loaded=False, loaded_rate=None)`: with `fully_loaded=True` the cost per order is `loaded_rate x AOV` ("after all variable costs"); otherwise it is product cost + fulfillment ("on product margin", the sheet's Payback tab, still 10 months and still what the reconcile map checks). `calc.payback_pair(...)` returns both. `model.loaded_rate_info(params, pnl)` reads the P&L's own lines by name (`china_cogs`, `fulfillment_services`, `inbound_shipping`, `payment_processing`, `chargeback_management`; lever `payback.loaded_lines`) as a share of DTC revenue over the first 12 projected months (`payback.loaded_months`); a typed `payback.loaded_rate` replaces it, and a missing line gives no loaded result (never a lower cost). On the sheet's own rates the cost is 32.4% of order value (margin 67.6%, the skeptic's figure) and payback is 18 months vs 10 on product margin; the skeptic reads 19, the one-month difference is the curve rounding. The page, the headline (loaded first, product margin in the note) and `GET /payback` (`result` and `loaded.result`, `loaded.info.parts`) show both in those plain words; the spend x ROAS grid can take either margin (`matrix.margin`, default product, unchanged).

## Top-up and what drives the low point

`calc.top_up_to_zero` finds, on the exact waterfall (typed starting-cash resets included), the cash funded once at close (the close month's shortfall) and the smallest constant amount added each later month that keeps every month at zero or above. `summary.low_driver.sentence` is built from the waterfall: cash earned before inventory through the lowest month, what was paid out largest first (the typed term-loan schedule's principal, inventory buys, interest, tax), and the top-up. On the sheet's own inputs: through Dec-28 the plan earns $6.2M before inventory and pays out $7.5M ($3.4M inventory, $2.999M term-loan principal, $576K interest, $518K tax), so cash needs about $85.5K at close and about $43K a month after.

## Validation

`params.validate_params(params)` (exported as `app.projection.financing.validate_params`) refuses the values `PUT /inputs` and `PUT /debt` refuse. `compute` runs it on the effective params too, so a value that reaches the store through the generic scenario PATCH (the core's `Module.validate_params` hook is not in `Module` yet; the package passes it when the field exists) makes the module degrade with `invalid financing input: financing.tax.rate: must be at most 1` rather than compute with it.

## Behavior

- **Term loan**: interest = beginning balance x rate / 12; principal is the typed series (a month without a cell pays interest only) or fixed payment minus interest; principal never exceeds the balance owed. Month 1 is the close month.
- **Cash** (from the close month; the seller's months are blank, and the sheet's `#REF!` months are not reproduced): ending cash = starting cash + (net operating income + China COGS) - loan interest - loan principal - seller note - tax - equity payback - inventory buys. Starting cash is the previous month's ending cash unless overridden. Lowest cash and month, first negative month, months below zero and a cash floor (headroom) are computed.
- **Payout block**: the sheet types the repeat ratios M1..M7; they equal the Goal curve ages 1..7 exactly, so the engine reads `curve_goal` (option `live` or typed). The kicker is creative production cost, an expense line inside marketing expenses, not pay. Step-ups are per payback column (15% / 10% for 4 and 2 months, 16% / 12% for 3 months, breakpoint $1.0M) as the sheet's formulas have them; total pay = bonus + the retainer (linked to the Lazar input, was hard-coded +5000).
- **Spend x ROAS grid**: profit = spend x ROAS x margin rate x (1 + repeat curve to the target month) - spend, less fixed cost on the loaded basis; marginal break-even ROAS = 1 / (revenue multiple x margin rate). Rebuilt on the live curve: the sheet's version reads a deleted tab (`Payback Summary`) and shows `#REF!`, so there is nothing to reconcile it against.

## API (`/api/projection/financing`)

`GET cash` (alias `table`), `GET debt`, `GET payback?aov&cac&product_cost_pct&fulfillment&curve&max_age`, `GET matrix?target_month&basis&fixed_cost&spends&roas` (+ the payback overrides), `GET payout?media_spend&cpa&aov&repeat_source`, `GET inputs`; `PUT debt {scenario, payments:[{month, principal_paid|null}], terms, expected_version}`, `PUT inputs {scenario, params, expected_version}` (merge patch, null removes). Query overrides are what-ifs and write nothing. Writes go through `series.apply_items` / `scenarios.update`: audited per cell, versioned, restorable; the core write guard (Origin, JSON, rate limit, 1 MB) covers them. The other input rows (tax, starting cash, equity payback, net operating income override) save through the core `PUT /series`.

## Reconciliation against the sheet (snapshot 2026-11-03)

`scripts/projection/reconcile.py --module financing` (47 lines). Two runs: with the sheet's own net operating income / inventory buys / curves injected as upstream (`tests/projection/financing/fin_helpers.py`), and with the real upstream modules (scratch database built from the other modules' seeders).

| line | cells | sheet-input upstream | real upstream |
|---|---|---|---|
| Term Debt C, D, E, F, G, H, J (beginning, interest, principal, ending, cumulative principal / interest, total payment) | 40 each (G 31, J 39) | exact (max diff $0.00) | exact |
| P&L 112-115 loan interest, principal, payment, balance | 40 each | exact | exact |
| P&L 117, 118 seller note | 40 each | exact (zeros) | exact |
| P&L 122 equity payback | 40 | exact | exact |
| P&L 125 inventory buys | 40 | exact (input) | exact ($0.00 vs inventory_supply) |
| P&L 108 net operating income before inventory | 40 | exact | 22 cells over, max $8,786 (2.1%), upstream: `pnl.noi` is itself 3.6% off the sheet and `china_cogs` 1.0% |
| P&L 120 tax | 40 | 1 cell over: Sep-27 $191,782 vs $63,927 (sheet formula error, below) | 3 cells over: same cell, plus Jun-28 $320,517 vs $315,317 (1.6%) and Sep-29 $222,089 vs $220,008 (0.9%) from upstream NOI |
| P&L 110 starting cash, 128 cash after inventory, 131 ending cash | 40 each | 27 / 28 / 28 cells over, all a constant $127,854.51 from Sep-27: the one tax cell cascading | the same plus the upstream NOI difference (max $87,707) |
| Payback B14, B15, B16, D8, B58 (10 months), B59, F59, D20:D56, C20:C56, B21:B56 | 1 / 36 / 37 | exact | exact |
| P&L F181, F184, F186, F187, F189, G183, G189, J180:M186 | 1 / 7 | exact | exact |
| P&L G193:I202 bonus and N193:P202 total pay | 9-10 each | exact | exact |

With the sheet's single tax cell (`63,927.25`) entered as an override for Sep-27, all 47 lines tie to the cent on the sheet-input run (`test_cash_ties_to_the_sheet_when_its_one_tax_cell_is_used`). Lowest ending cash: sheet -$1,097,692 (Dec-28); engine on the sheet's inputs -$1,225,546 (26 negative months); engine on the real upstream -$1,173,778 (25 negative months).

## Differences from the sheet (which one is wrong)

1. **Sep-27 tax understated by $127,855 in the sheet.** `P&L!AI120 = ((BM100/12)*4)*0.21`, but `BM100` is `=SUM(W100:Z100)`, already the 4-month Sep-Dec 2026 total ($913,246), so the 4/12 is applied twice (tax on one third of the period). The engine taxes the full period: $191,782. The later two payments are `BN100*0.21` and `BO100*0.21` and match.
2. **Oct-26 starting cash typed 0** (`P&L!X110`), which drops the Sep-26 shortfall (-$85,475) from every later month. Kept as a seeded override with that explanation in its note; clearing it carries the shortfall (lowers all later months by $85,475). Sep-26 itself uses the typed $150,000 for net operating income before inventory (W108).
3. **Typed principal leaves $1,000 unpaid.** The 21 typed payments total $2,999,000 of $3,000,000, so $12.50 of interest accrues every month to Dec-29 and `Months to Full Payoff` is `#N/A`. The engine reproduces it and warns. The tab title says "$3.5M" and its fixed $125,000 payment (B6) has not been used since the 2026-10-08 revert; it is the `fixed_payment` mode here.
4. **Tax payment months are inconsistent** in the sheet: Sep-27 and Sep-29, but Jun-28 for the 2027 tax (column AR, where Sep-28 would be AU). Seeded as typed (2027-09, 2028-06, 2029-09), editable.
5. **Tax base is net operating income**, as in the sheet; term-loan interest ($143K to $311K a year) is not deducted. Lever `tax.deduct_interest` exists, default off.
6. P&L row 128 is `#REF!` for Jan-25 to Aug-26 and `X128` has `--X120` (adds tax instead of subtracting it; tax is 0 that month). Not reproduced: cash starts at the close month and every flow is subtracted.
7. The payout block's +5000 retainer is linked to the Lazar input; the repeat ratios are read from the Goal curve instead of typed copies.
8. Spend-ROAS Matrix uses the live curve and Payback inputs (the sheet's inputs are gone).

## Open items

- Josh: decide items 1 (tax), 2 (Oct-26 opening cash) and 4 (tax months); confirm whether the term loan should be fully paid (the last typed payment is $1,000 short).
- `payback.aov` / `cac` are typed levers from the pacing snapshot; the pacing module should supply live values (or write the levers).
- Seller note and EBITDA floor: off, floor blank (amount not in the sheet).
- The post-ship levers (`payback.loaded_*`, `matrix.margin`) live in the code defaults only; migration 160 is unchanged.
- Pacing's variables panel still writes `financing.payback.*`; the single store is `drivers.payback.*` (ticket in TICKETS_financing.md).
- Grid payload naming mismatch and the restore-baseline observation are in `QUESTIONS_financing.md`.
- Net operating income and China COGS differences against the sheet belong to pnl and drivers (their reconcile reports); this module inherits them one for one.
