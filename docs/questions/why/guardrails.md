# Guardrails for the lonvera-brain why tool

Each is a trap Josh caught or Claude retracted. Enforce in the tool as a check or a forced label. IDs are Lonvera item ids from `lonvera_items.jsonl`.

## G01. Name the basis before comparing

- Rule: Three different "purchases" exist: Meta-reported (counts returning buyers), Upstack new customers (first orders, any-click 30-day), Upstack all orders. Compare only within one basis; never set Upstack new customers against Meta all purchases.
- What happened: 2026-11-04 "Upstack purchases 20-36% below Meta" compared Upstack NEW customers with Meta ALL purchases; like for like, Upstack is above Meta for Vita Bull and Beef Liver (source: josh-hq decision log, no item id).
- Applies to trees: meta_vs_actual, nc_roas_cpa
- Evidence items: L2-168, L2-166, T_08-35

## G02. Meta spend comes from the Graph API (both ad accounts), never Upstack

- Rule: Upstack omits the BackUp account. Use Graph API (analytics Meta tables) for any Meta number; Upstack for business-wide new customers and revenue.
- What happened: Jun spend Upstack $375,303 vs Graph $453,845; Jul $764,305 vs $864,614; NC ROAS Jun 0.75 to 0.62 after switching.
- Applies to trees: all spend-based trees
- Evidence items: S_secondary-184, T_20-52, T_20-53, T_20-55

## G03. Partial day / partial window artifacts

- Rule: Never read today, an unfinished month, a window with a missing feed, or a cohort age not yet baked. Print last loaded day beside every number.
- What happened: Daily NC ROAS 0.96 on a partial pull vs about 0.68 after re-run (Oct 7); Amazon - Daily had no rows after Sep 24 so about half of one "drop" was data (Sep 29); W43 0.53 was Oct 25 spend with no orders; M1-M4 down 21-30% was a partial-window artifact (Oct 16); Month Snapshot included today spend (CPA $104.36 vs $97.99).
- Applies to trees: all
- Evidence items: T_20-58, T_17-5, T_20-63, T_26-29, T_26-32

## G04. Never include a month that is not over; no zeros for unbaked ages

- Rule: Standing rule since 2026-10-02. Averages over cohort ages use only cohorts that reached that age; require at least 3.
- What happened: Curve average showed 1.38% where the real M20 average is 2.46% because zeros were weighted in; M24, M29, M30 values existed for ages no cohort reached.
- Applies to trees: cohort_curve, repeat_vs_plan
- Evidence items: H_2026-08_1-266, H_2026-08_1-269, H_2026-08_1-291, H_2026-08_1-413, H_2026-08_1-414, H_2026-08_1-158

## G05. Gross vs net: say which

- Rule: Revenue is gross (tax included, before refunds) in the DB and in Upstack column Q (includes tax, shipping, cancelled and refunded). P&L targets are net of the 7% sales-tax estimate. Refunds are 9-10% of DTC revenue.
- What happened: EBITDA pacing 175% (tax-inclusive) vs 136% after the tax line (Oct 13); net DTC pacing 89.5% vs components 99.6 / 99.8 was mostly refunds (about $10,900 of $12,038), not tax as Josh guessed.
- Applies to trees: pacing_tieout, model_outputs, cohort_curve
- Evidence items: T_18-64, T_26-7, S_secondary-299, H_2026-08_1-52

## G06. Day buckets are Pacific

- Rule: Upstack, orders, Google are Pacific; Meta days are each ad account own day (Main Los Angeles, BackUp New York). UTC buckets caused a 0.5-13% gap.
- What happened: Pacific matched the Upstack dashboard to the dollar once switched (Sep 19).
- Applies to trees: data_freshness
- Evidence items: T_08-28, S_secondary-294, T_08-6

## G07. Rates with denominators, never counts

- Rule: Cancels per rebill due, not cancels per week; NC per 1,000 impressions or per click; returning share of purchases; retention vs repeat dollars (smaller cohorts mean fewer first rebills).
- What happened: Josh asked why repeat is down if retention is up: smaller cohorts, not lower rates (Oct 17). Cancel holdout read as 9.8% vs 1.4% of rebills due.
- Applies to trees: repeat_vs_plan, rebill_failure, checkout
- Evidence items: T_26-53, L2-143, L2-157, T_26-29

## G08. Mix vs rate: shift-share before naming a cause

- Rule: Split any per-dollar or per-customer change into spend moving between structures (campaign, ad-set age, product, country, cohort) vs change inside the same structure.
- What happened: Spend moved off 0.6-0.8 structures onto 0.3-0.5 structures was the largest verified per-dollar driver; the blended "efficiency collapse" was mix; ad sets under 7 days old return 25-35% less in the same campaign.
- Applies to trees: nc_roas_cpa, spend_capacity, cpm_reach, country_intl
- Evidence items: L2-196, T_26-65, S_secondary-88, L2-188

## G09. Validate an experiment before reading it

- Rule: Check exposure (who got treatment, who did not), balance and contamination. Recurring flows overwrite tags. "Randomized" claims need the assignment record.
- What happened: Billing-reminder 50/50 holdout: the no-email side was visible only through a tag that ran from about Oct 15, and Claude first said "not randomized" wrongly (Oct 23).
- Applies to trees: repeat_vs_plan, klaviyo, spend_capacity
- Evidence items: L2-158, L2-159, L2-148

## G10. A timing or decomposition story is not attribution

- Rule: "Mostly timing" came from modeling how much timing could explain. Cause words (caused, explains, proves) need a randomized holdout or a clean like-for-like control; otherwise "consistent with" plus the test that would settle it.
- What happened: Oct 21-23 repeat miss called mostly timing for two days; the billing-reminder email Josh raised was the larger driver (global rule 13).
- Applies to trees: repeat_vs_plan, klaviyo, cohort_curve
- Evidence items: L2-150, L2-161, L2-11, L2-45

## G11. Exclusions and targeting: read the sentence lines, not the targeting field

- Rule: The Graph API targeting field omits exclusions on ad sets built in the newer audience-controls format; use targetingsentencelines and the activity log. Pixel audiences that report 20 people exclude nobody.
- What happened: Claude withdrew a "no exclusions" claim; Oct 26 chain: Josh was right.
- Applies to trees: meta_vs_actual, spend_capacity
- Evidence items: L2-190, T_10-25

## G12. Launch volume comes from Meta ad-level first-spend, deduped; never the tracker; never a stale number

- Rule: Tracker launch dates, status and Launch Date counts differ from Meta launches. Renamed re-uploads, relaunch copies and rebuilds are not new concepts. Re-pull before quoting a weekly figure.
- What happened: Tracker said 24 to 48 ads per week (no drop) vs Meta 205 to 80 concepts per week; Aug count 1,228 incl. rebuild vs 151; "80 per week" repeated as current after October launches were 322 / 272 / 886 per week.
- Applies to trees: launch_volume, test_to_scale, creative_perf
- Evidence items: T_18-33, T_18-34, T_17-39, L2-181, L2-241, T_24-49

## G13. Frozen, first-row-only and shifted columns; pull the sheet before writing

- Rule: Lifetime tracker columns froze at 2026-09-17; creative_tracker metrics sit on the FIRST row of each ad family (sum rows, never average; unique ads = count distinct family); rebuild scripts silently revert Josh edits.
- What happened: Meta Ads - Daily stuck at Sep 28 for an unmerged PR; script rebuild deleted and recreated and reverted Josh edits.
- Applies to trees: creative_perf, launch_volume, data_freshness
- Evidence items: T_11-66, S_secondary-165, S_secondary-166

## G14. Small-spend ROAS and fixed thresholds

- Rule: Do not call a winner or loser on tiny spend; use a spend floor. Thresholds: works >= 1.00, does not <= 0.80, between = average (later plus or minus percent vs account). 0.85 on $1.6M is not a loser.
- What happened: Josh: "0.85 vs 0.9, do not be stupid"; trend charts hid that old ROAS sat on almost no spend (spend floor added).
- Applies to trees: creative_perf
- Evidence items: T_05-79, S_secondary-161, T_11-8

## G15. Denominators across scope: Main-only vs Main + BackUp + Google

- Rule: Meta ROAS covers Main; actual NC ROAS divides by all spend. Half of the Meta-vs-actual gap (0.061 of 0.119) was this.
- What happened: Oct 19-24: 0.705 vs 0.586; BackUp live from about Oct 21-22.
- Applies to trees: meta_vs_actual, pacing_tieout
- Evidence items: L2-196, L2-168, T_20-63

## G16. Warehance cancelled is not cancelled; shipped, not sold; carrier feed is the truth

- Rule: Non-US orders auto-cancel in Warehance within about 15 seconds because Shopify routes them to D1; real US cancel rate 5.9%. Warehance tracking for UniUni freezes after an early mis-mapped delivered event. Inventory runs on shipped units.
- What happened: The "42.5% cancelled" finding was retracted Oct 4; 773 of 928 stuck rows were delivered.
- Applies to trees: fulfillment, inventory
- Evidence items: S_secondary-102, S_secondary-183, T_18-68, S_secondary-103, T_07-31, T_07-32

## G17. Trybe $0 gift orders inflate counts and AOV

- Rule: Orders with tag trybe / note "Trybe sample request" are not attributed sales.
- What happened: Josh: "that is zero $$, that is a Trybe gift" (Oct 3); also part of the NC AOV give-back read.
- Applies to trees: nc_roas_cpa, fulfillment
- Evidence items: T_19-29, T_19-28, T_19-26

## G18. Name the data source and the store

- Rule: Wrong-source findings: a discount-depth finding came from the seller diligence memo; Shopify data that looked like the Hydrant store; Upstack missing BackUp.
- What happened: Sep 27 "discounting fell 31% to 11.9%" downgraded after Josh flagged the source.
- Applies to trees: nc_roas_cpa, creative_perf
- Evidence items: T_10-23, T_10-22, S_secondary-184

## G19. Results are not causes

- Rule: Women 11% to 31%, men 55+ 58% to 44%, reach 417K vs 2.3M, Beef Liver share, placement mix: all results of the bid structure or the mix, not the cause. Do not rank a single-cut ratio as a driver.
- What happened: Oct 26 why-chain listed these as results, not causes; country mix explains about 0% of the CPM rise.
- Applies to trees: cpm_reach, country_intl, creative_perf
- Evidence items: T_10-30, T_25-41, T_11-55, S_secondary-87

## G20. Cohort source, tool and pull date

- Rule: Same source and same pull date on both sides; the same history restates between pulls (Nov-26 repeat $773K vs $674,650); Shopify dollars run 5-11% above Upstack; the tool behind the cohort CSV is still unnamed.
- What happened: Oct 2 pull believed ExpandFi; an Upstack paste was mislabelled ExpandFi; Shopify then reversed.
- Applies to trees: cohort_curve, repeat_vs_plan
- Evidence items: L2-258, L2-260, L2-261, L2-262

## G21. Checkout rates: numerator and denominator system

- Rule: lonvera.com LP orders land on Shopify "web", shop.lonvera.com on CheckoutChamp (LP orders never enter CheckoutChamp). Count all new-customer orders over both before reading a rate. Meta is 92-97% of attempts so no non-Meta control exists.
- What happened: A third of the 0.330 to 0.238 "fall" was LP orders moving to Shopify web (65 to about 195 per week).
- Applies to trees: checkout
- Evidence items: L2-217, L2-223, L2-229

## G22. Do not re-recommend what was already done; check the change list first

- Rule: Josh Oct 25: stop recommending things we already did; whenever you have an idea, check whether we did it. Retractions were frequent (loosen caps, test in incremental, non-US only).
- What happened: Loosening recommended then retracted Oct 18; "test in incremental" retracted same day; non-US-only admitted unsupported.
- Applies to trees: nc_roas_cpa, spend_capacity
- Evidence items: L2-186, T_26-61, T_24-74, T_26-87

## G23. Sample size and significance

- Rule: LP and PDP tests: not significant at first, uneven splits; Josh ships only on an overwhelming winner (20% lift, not 1%). 90% of ad-set-weeks run under 50 conversions.
- What happened: Oct 21 A ahead; Oct 29 B 36.8% vs A 20.9% (z about 3.75).
- Applies to trees: checkout, launch_volume
- Evidence items: L2-2, L2-3, L2-243, T_08-44

## G24. Metric definition before the number

- Rule: First try vs ever; order vs customer; cycle 2 vs cycle 3; processed-only cohort vs Shopify. Reconcile definitions before comparing two tables.
- What happened: Cycle 2 success read 15.8% by one definition and about 80% by another (Jul 28).
- Applies to trees: rebill_failure, repeat_vs_plan
- Evidence items: HC_2026-07_6-74, HC_2026-07_6-168, HC_2026-07_6-172

## G25. Survivorship and self-selected spend

- Rule: Meta allocates spend to ads that look good, so spend is partly an outcome of performance; hit-rate denominators must be ads that got first spend; ads with spend are survivors.
- What happened: Josh Jul 16: "ads got more spend because they were doing well".
- Applies to trees: launch_volume, test_to_scale, creative_perf
- Evidence items: HC_2026-07_4-28, HC_2026-07_4-13, S_secondary-82

## G26. Blip, ebb and flow, or real: use an external control and do not alarm

- Rule: Compare with Hydrant (no restructure) and the same weeks last year before saying the takeover caused it or that it is noise.
- What happened: Hydrant showed the same CPM and ROAS pattern (Oct 8); Josh asked "is this just ebbs and flows".
- Applies to trees: nc_roas_cpa, cpm_reach
- Evidence items: T_21-53, T_26-56, T_21-57

## G27. Attribution windows differ

- Rule: Meta 7-day click + 1-day view vs Upstack any-click 30-day vs 1-day click. 1DC CPA tracks actual NC CPA in total but not who was served; incremental attribution on new ad sets inflates.
- What happened: Oct 23 1DC discussion; window extras 3.7% to 8.4% of Meta purchases.
- Applies to trees: meta_vs_actual, spend_capacity
- Evidence items: L2-165, L2-166, L2-167, T_26-82

## G28. Curve construction: effective vs nominal decay

- Rule: A 0.8 tail multiplier on a rolling average behaved as about 0.94 per month; read curves by effective decay and by ages with at least 3 cohorts.
- What happened: Replaced by explicit 0.94 input on Sep 13.
- Applies to trees: cohort_curve
- Evidence items: T_04-30, S_secondary-60, H_2026-08_1-413

## G29. Read the edit history before explaining a model number

- Rule: When a model output jumps, first diff versions and read the change history; do not narrate a cause.
- What happened: Bear EBITDA jump traced to a thin-tail fallback, not the assumption Josh edited (Aug 3).
- Applies to trees: model_outputs
- Evidence items: H_2026-08_1-16, H_2026-08_1-17, HC_2026-07_7-36, H_2026-08_1-252

## G30. Copy-only tags and tracker text can describe a different ad

- Rule: Tracker transcript sometimes belongs to a different video than the one running (14 of 237 flagged); copy-only tagging changed the line on 61% of Vita Bull rows once the picture was read.
- What happened: Nov 3: "Did you open every ad and look at it?" "No".
- Applies to trees: creative_perf
- Evidence items: L2-267, L2-270, L2-271

## Quick enforcement list (what the tool should do automatically)

- Print `last loaded day` and the basis label (Meta-reported / Upstack NC / Upstack all) beside every number.
- Refuse to compare across bases; refuse to sum purchases or revenue across platforms.
- Drop today, unfinished months and cohort ages with fewer than 3 cohorts; say so.
- Show gross or net on every revenue figure.
- Output rates with their denominators; show counts only as secondary.
- Run shift-share (mix vs rate) before naming a driver; show the log-share of spend vs per-dollar.
- Before any "caused/explains": list the change events in the window and name the control; otherwise label "consistent with" and name the test that would settle it.
- Attach the contamination and exposure check to any holdout or experiment read.
- For launch volume use Meta first-spend families, deduped; never tracker rows.
- Never restate an old weekly figure as current; re-pull.
