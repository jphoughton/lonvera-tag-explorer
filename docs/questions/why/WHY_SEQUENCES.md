# Why-sequences mined from Josh Lonvera questions

Window: 2026-05-22 to 2026-11-09. Source: `lonvera_items.jsonl` (2,938 items), the two relationship ledgers, the Oct 26 why-chain and the checkout split report. Research only, no repo touched.

Terms: NC = new-customer; ROAS = return on ad spend; CPA = cost per acquisition; CPM = cost per 1,000 impressions; CPC = cost per click; CTR = click-through rate; tROAS = target ROAS (floor); 1DC = one-day-click attribution; 7DC1DV = 7-day click, 1-day view; AOV = average order value; LP = landing page; PDP = product detail page; NOI = net operating income; QBO = QuickBooks Online; DB = the Postgres app database (analytics views).

## TLDR

- Josh asked 646 why-type questions (causal, correlation, hypothesis, diagnosis_ask or any item with a relationship). I grouped them into 19 candidate trees. Counts below are item counts after linking each tree to the ledger rows that cover it (ledger links add asks of other kinds), so they overlap by design.
- Most asked: Cohort repeat curve shape and projection accuracy (141); Repeat revenue (106); Why a model number jumped or does not tie (104); Spend fell or will not scale: bid floors, caps, budgets (69); New-customer ROAS down / CPA up / acquisition "free fall" (64).
- Josh never asks a why once. He walks a fixed order: (1) is the number real and fresh, (2) rates not counts, (3) list what changed in the window, (4) split into mix vs rate, (5) ask for the control ("is this correlation or causation", "what is the holdout"), (6) "keep digging, what is fundamentally different", (7) "stop recommending what we already did". The tool should force steps 1-5 before it ever names a cause. Items for each step in order: T_20-58, T_26-53, L2-186, L2-188, HC_2026-07_4-20 and L2-158, T_10-30 and L2-180, L2-186.
- Entirely outside the DB: repeat-revenue drivers (Skio, CheckoutChamp, Klaviyo, cohorts), rebill failures, checkout funnel, fulfillment, Klaviyo, inventory stock counts, Amazon, refunds, and the Meta change log (bid, budget and structure edits). The DB covers spend, impressions, clicks, purchases (Meta and Upstack basis), actual daily NC revenue and the tag set well enough for the Meta-side trees.
- The biggest decision-weight gaps are not in the DB: the change log (Graph API activity log, Klaviyo flow dates, CheckoutChamp config) and per-ad-set budgets and bids. Without them the "what changed in the window" step cannot be automated.
- Note on ranks 1-3: cohort curve and model-output questions are mostly July-August finance-model audits (the Projections sheet, now rebuilt in the app); repeat-vs-plan is the October one and the highest decision weight. Meta-side trees (spend capacity, launch volume, NC ROAS, Meta vs actual, CPM) rank lower by count but carry the live operating decisions.
- Repeat misses of the same trap: mixing bases (Upstack NC vs Meta all purchases), partial windows, gross vs net, counts vs rates, and a story standing in for a control. 30 guardrails in `guardrails.md`.

## Ranked table

Ranked by linked item count, ties by recency. Build order (decision weight, effort, DB coverage) is under the table.

| rank | outcome | items (core / linked) | last asked | in DB? | decision weight | build effort |
|---|---|---|---|---|---|---|
| 1 | Cohort repeat curve shape and projection accuracy (tail, M0/M1, haircut, model vs actual) | 88 / 141 | 2026-11-09 | Partial | H | M |
| 2 | Repeat revenue (and week-4 repeat) below plan | 84 / 106 | 2026-11-03 | No | H | L |
| 3 | Why a model number jumped or does not tie (EBITDA margin, Bear/Bull cases, cash, scenario vs P&L) | 54 / 104 | 2026-10-26 | Partial | M | M |
| 4 | Spend fell or will not scale: bid floors, caps, budgets (spend capacity) | 30 / 69 | 2026-10-25 | Partial | H | L |
| 5 | New-customer ROAS down / CPA up / acquisition "free fall" (the root question) | 24 / 64 | 2026-10-27 | Yes | H | M |
| 6 | Creative launch volume, hit rate and scale ("do more ads drive more spend?") | 33 / 61 | 2026-10-28 | Partial | H | M |
| 7 | Which creative / ICP / pain point / offer / vendor / format used to work and stopped | 23 / 49 | 2026-11-03 | Yes | M | S |
| 8 | Subscription rebill failures and declines | 33 / 48 | 2026-10-22 | No | M | L |
| 9 | Meta-reported results vs actual (Upstack / Shopify) gap: ROAS, CPA, returning buyers, spend source | 22 / 42 | 2026-10-27 | Yes | H | S |
| 10 | CPM, cost per reach and frequency up | 22 / 40 | 2026-10-25 | Partial | M | M |
| 11 | Is the data fresh / did a feed or sync break (cross-cutting first check) | 23 / 34 | 2026-11-04 | Yes | H | S |
| 12 | Pacing vs plan and NOI / EBITDA pacing vs booked (tie-outs) | 14 / 26 | 2026-10-29 | Partial | M | M |
| 13 | Checkout and landing conversion (checkout start to order, click to NC order) | 11 / 25 | 2026-10-29 | No | H | L |
| 14 | Test-to-scale rate (do tested ads graduate?) | 5 / 21 | 2026-11-09 | Yes | M | S |
| 15 | Country mix and international (US vs intl) | 13 / 20 | 2026-10-25 | Partial | M | M |
| 16 | Fulfillment: stuck, late, cancelled or delivered orders; Shopify vs Warehance counts | 12 / 16 | 2026-10-15 | No | L | M |
| 17 | Stock-outs and inventory cover (why ordering so much / short) | 4 / 11 | 2026-10-15 | Partial | M | M |
| 18 | Klaviyo (email / SMS) revenue and flow changes | 6 / 10 | 2026-10-13 | No | M | M |
| 19 | Google, Amazon, refunds, price/offer (small, scattered) | 7 / 10 | 2026-10-18 | No | L | M |

Not trees (excluded from ranking): website build and debug asks 47 (CheckoutChamp routing popup, page speed, formatting), creative tooling asks 46 (prompts, tag scrapers, tracker sync), tooling status pings 11, diligence 16 (red-flag judgments), and 18 non-diagnostic asks. Counts are keyword plus session assignment of the 646 why-type items and are approximate (about plus or minus 10%).

Recommended build order: (1) data freshness gate, (2) Meta vs actual gap and nc_roas_cpa additions (both mostly in DB, S/M), (3) spend capacity with a bid-edit log feed (H weight, needs Graph activity log), (4) repeat vs plan (highest asked, but L: needs Skio, CheckoutChamp and Klaviyo feeds), (5) launch volume and test-to-scale (in DB, M and S). The remaining trees are lower weight or sit mostly outside the DB.

## Cross-cutting checks Josh always asks first

1. Is the data fresh and complete? Did a feed break? ("did this run this AM", "are these meta numbers or upstack, what exact days", "all the data is fresh now? and running daily?"). Items: T_20-58, T_20-61, T_18-73, L2-292, T_17-5.
2. Which basis and which source? Meta-reported vs Upstack new customers vs Upstack all orders; Meta spend from the Graph API (both accounts). Items: T_08-35, T_20-52, S_secondary-184, T_20-55.
3. Is today, the unfinished month, or an unbaked cohort age in the number? Items: T_20-63, T_26-32, H_2026-08_1-266.
4. Gross or net? (sales tax, refunds, shipping). Items: T_18-64, T_26-7.
5. What changed in the window? List first, then test each Josh-named hypothesis fairly before ranking it down (rule 13). Items: L2-186, L2-150.
6. Spend or yield? Split the change into spend (log share) and per-dollar yield, then mix vs rate. Items: L2-188, L2-196, S_secondary-81.
7. Is it ours or everyone? Hydrant control, benchmarks. Items: T_21-53, T_26-56.

## Event types Josh checks under "what changed in the window"

| event type | examples with dates | where it lives | in DB? |
|---|---|---|---|
| Bid, cap and ROAS-floor edits | Sep 14 tightening of 29 ad sets; Sep 23-24 loosening; Oct 2-3 floor raises; Oct 13 floor 1.0 to 0.9; Oct 24-25 1DC caps | Graph API activity log; ad set bid fields | No (only daily summed bids in proj_feed_bids) |
| Campaign and ad-set structure changes | Aug 22-29 rebuild; Oct 1-5 Rodolfo paused 37 ad sets; Oct 20-24 originals turned off; Oct 14 new uncapped Highest-Volume testing; Oct 5 LP relaunch ad sets | Graph API activity log, ad set names | Partial (names in meta_ads; first-spend and last-spend day derivable) |
| Account-level moves | BackUp account relaunched about Oct 21-22; Main dark Jun 23-Jul 5; July relaunch on new ids | Graph API | Partial (both accounts in the Meta tables) |
| Attribution, pixel and exclusions | 1DC adoption Oct 23; pixel audiences report 20 people; new spoofed-domain pixel proposal; Purchase vs NC Purchase optimization event | Ads Manager, Graph targetingsentencelines | No |
| Checkout and landing changes | Generic Checkout page Oct 3; PayPal Backup page Sep 28-Oct 3; Sep 21 8am change; Aug 27-Sep 14 checkout A/B; checkout module swap; lonvera.com LP orders moving to Shopify web | CheckoutChamp, theme repo | No |
| Offer, pack and price changes | 3- and 5-packs from March; Aug 21 upsell pages; Aug 24 offer lifted bottles per first order; Skio to CheckoutChamp price cut (March, about $40 to $30-33 per bottle) | Shopify, CheckoutChamp, Skio | No |
| Billing and rebill interval | Pack subscribers on 56 / 84-day billing; Oct 12-13 PayPal target_client_id errors; Skio run-off | Skio, CheckoutChamp | No |
| Klaviyo flow and email changes | Billing-reminder flows scaled Sep 14-15 (old flow about 9x, new CKC flow); agency sweeps (delays, splits, status); popup | Klaviyo API created / updated | No |
| Creative supply and tracker state | Weekly new concepts 205 to 80 to 322 / 272 / 886; relaunch of 13 winners Oct 28; Madre status sync; Concept ID renames Oct 13 | Meta ad level; Creative Tracker | Partial |
| Sync and data plumbing breaks | Amazon - Daily after Sep 24; GitHub cron 8-12h late; UTC buckets; unmerged PR stalling Meta Ads - Daily at Sep 28 | feeds, etl_runs | Yes (data_coverage, etl_runs) |
| Routing and logistics | Revamp Utah go-live (US orders off the intl 3PL, 2,100 per month to 10); D1 routing of non-US orders; UniUni feed freeze | Shopify, Warehance | No |
| Model edits | Taxes added Jul 28; Stage-2 gross-up removed Aug 3; Bull refresh; tail rule; NC-ROAS slide 0.81 to 0.70; calibration factor added and removed Sep 24 | proj_audit, sheet history | Partial |
| Market and external | Q4 CPM ramp; health and wellness CPM +19-38% YoY; Advantage+ default | benchmarks, Hydrant account | No |

## Trees

### 1. Cohort repeat curve shape and projection accuracy (tail, M0/M1, haircut, model vs actual)

Items: 88 core, 141 linked; first asked 2026-07-16, last asked 2026-11-09. Decision weight H. In DB: P (proj_cohorts and proj_cohort_cum exist in the app database but are excluded from analytics; source is a pasted cohort export). Build effort: M.

**Decomposition.** Lifetime repeat per customer = sum over ages of [cumulative dollars per customer at age a, minus age a-1] / first-order dollars. A curve value at age a is trustworthy only if enough cohorts have reached age a (rule: at least 3) and no unfinished month is included. Projection error = curve level (recent vs older cohorts) x cohort size x first-order AOV, plus tail decay beyond observed ages.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Does the curve show values for ages no cohort has reached (M24, M29, M30)? Data: cohorts that reached age a; tail rule. In DB: partial. Source or view: Curves tab / proj_cohorts. Josh: "well you just cut off a ton of liver data history... after M23.... I need that data" (H_2026-08_1-158, 2026-08-06) ; "back to this... why is AG206 in the hist waterfalls 3.8% for M30?" (H_2026-08_1-281, 2026-08-06)
2. Are M0 and M1 mapped correctly (spot-check a known cohort against Josh own number)? Data: cohort export columns. In DB: no. Source or view: cohort CSV. Josh: "bull-July.csv Liver July.csv Bone-July.csv ok, this this is where you are wrong. I just stop checked bull for Mar 2025 a..." (H_2026-08_1-146, 2026-08-06) ; "how could M1 be 1.7 if incremental in M1 is only 21%" (H_2026-09_1-206, 2026-09-08)
3. Zeros or unfinished months in the averages? Data: weights per age. In DB: partial. Source or view: Curves tab. Josh: "but the curve in column B of Curves shows 1.38% for the company average... it should be 2.46... im assuming your include..." (H_2026-08_1-413, 2026-08-07)
4. Did new cohort data shift the 3 or 4 month average used to project? Data: curve before vs after refresh. In DB: partial. Source or view: Cohort Data - Unified backups. Josh: "Based on the most recent data, has LTV / repeat level gone down from the last data we had in there that you overwrote" (H_2026-09_1-201, 2026-09-08) ; "did any of the new data shift the 3 / 4 month average of repeat rate materially thats being used to project out the futu..." (H_2026-09_1-202, 2026-09-08)
5. Are recent cohorts baking worse than older ones? What haircut by age? Data: last 4 months vs history at same age. In DB: partial. Source or view: cohort export. Josh: "ok... so now looking at this and seeing the last 4 months are not baking as well by pretty much any metrics... what do y..." (H_2026-08_1-395, 2026-08-07) ; "what is a realistic haircut I should applhy to my repeat curve? Company level. Stop arguing, give me an adjustment per M..." (H_2026-08_1-403, 2026-08-07)
6. Unified vs per-product cohorts: is the story different? Data: per-product exports. In DB: no. Source or view: Shopify / cohort tool. Josh: "this tells a very different repeat story?" (H_2026-08_1-441, 2026-08-07)
7. Why do two model versions disagree on the same month (Aug repeat: pacing $855,616 vs new $578K vs old $901,844)? Data: model versions, recency weight. In DB: no. Source or view: sheet history. Josh: "ok without makeing any changes at all.... why is this so different than the other model you built earlier?" (H_2026-08_1-252, 2026-08-06) ; "so I also can see this month is pacing at $855,616k repeat... and I feel like this would bring it way down?" (H_2026-08_1-262, 2026-08-06)
8. Why does the model repeat differ from the seller repeat figure (about 22% gap)? Gross vs net, shipping, refunds Data: QBO shipping income, seller doc. In DB: no. Source or view: QBO + seller data room. Josh: intent: Fix the ~28% Stage-2 repeat gross-up band-aid by diagnosing the 22% gap. (H_2026-08_1-40, 2026-08-03) ; "so a big gap in the repeat revenue was the old number was total revenue for repeat vs net revenue. So the cohort buuld d..." (H_2026-08_1-52, 2026-08-03)
9. Which window sets the live curve: last 3, 6 or 12 cohorts, and does that move the 12-month number? Data: curve by window. In DB: partial. Source or view: Curves tab. Josh: "Does using last 3 month or last 6 months curve push the 12 month repeat numbver up?" (L2-263, 2026-11-03) ; "I think last months repeat, is dragging it all down and its a blip" (L2-264, 2026-11-03)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Fallback logic extrapolated Bull tail from one cohort (blanket age <= max-3 cutoff): Bull lifetime multiple 3.79x to 2.23x, Bear 2027 NOI $6.32M to $5.64M after fix (rule: age computes from data only when >= 3 cohorts reached it) | verified / fixed | 2026-08-03 | S_secondary-55, H_2026-08_1-9, H_2026-08_1-17 |
| Tail multiplier 0.8 behaved as about 0.94 per month because of the rolling average; replaced by explicit 0.94 input | verified / fixed | 2026-09-13 | T_04-30, S_secondary-60 |
| Gap between seller repeat and cohort export is mostly shipping revenue (gross vs net; shipping 53-69% of the Apr-Jun gap vs QBO); seller actuals row was unverified seller data | partial (old gross-up validity never resolved) | 2026-08-03 | H_2026-08_1-52, S_secondary-57, H_2026-08_1-62 |
| Recent cohorts bake worse: October ran 74-80% of model net, M1 0.63 / M2 0.81 / M3 0.73 / M4 0.75 actual vs model; sheet pacing flattered about 12% by Upstack gross basis | partial | 2026-10-16 | H_2026-08_1-395, T_26-33 |
| Pulling the same history at different dates restates it (Sep pull vs Oct pull: Nov-26 repeat $773K vs $674,650; Oct close -$24K) | verified | 2026-11-03 | L2-260, L2-261 |
| Cohort export tool is unnamed (ExpandFi vs GetKleio vs Upstack vs Shopify, 5-11% apart in dollars) | open | 2026-11-03 | L2-258, L2-261, L2-262 |

**Controls rule 13 needs.** Compare cohort at same age only; require >= 3 cohorts per age; same source and same pull date for both sides; never include a month that is not over; calendar-matched pre-pack-change cohorts.

**Guardrails.** G04 Never include a month that is not over; no zeros for unbaked ages; G20 Cohort source, tool and pull date; G28 Curve construction: effective vs nominal decay; G05 Gross vs net: say which

**Supporting item ids** (latest 15 of 141; all in `why_candidates.json`): H_2026-09_1-206, S_secondary-60, T_04-30, T_fix-10, T_21-58, T_26-33, T_26-39, L2-254, L2-258, L2-260, L2-261, L2-262, L2-263, L2-264, L2-324

### 2. Repeat revenue (and week-4 repeat) below plan

Items: 84 core, 106 linked; first asked 2026-10-13, last asked 2026-11-03. Decision weight H. In DB: N (orders_daily has only daily new-customer revenue; cohorts live in proj_cohorts, not in analytics; billing data in Skio, CheckoutChamp, Klaviyo). Build effort: L.

**Decomposition.** Repeat revenue (period) = sum over cohorts of [rebills due in the period x paid share of rebills due x order value]. Gap vs plan = actual minus what the prior cohort at the SAME age and pack size would have produced, split into: cohort size (fewer first orders), timing (pack size sets the first rebill day: 3-pack about day 57, 5-pack about day 84; before the packs about every 28 days), cancels per rebill due, failed or declined per rebill attempted, order value (price per bottle, insurance), and billing platform (Skio run-off vs CheckoutChamp).

**Ordered child questions (the order Josh actually walked, with his words)**

1. Is the window complete, and is the plan comparison cohort-size neutral? Data: repeat by day/week/month vs waterfall. In DB: no. Source or view: sheet Projections (waterfall) + cohort export. Josh: "Ok keep digging, know we are also not througbentire month" (T_26-32, 2026-10-16) ; "Again compare to my repeat projections… so that equalizes cohort size" (T_26-33, 2026-10-16)
2. Rates first: are customers retained but fewer rebills due? (Josh: repeat down while retention up) Data: rebills due, paid rebills, by cohort age. In DB: no. Source or view: Skio + CheckoutChamp order_details. Josh: "im not sure i follow why repeat is down if customer retention seems up?" (T_26-53, 2026-10-17) ; "you have access to my projection doc, repeat rates by day, week and month, we are way down on repeat this month... whats..." (T_26-29, 2026-10-16)
3. Cancels, refunds, returns: more than usual, per rebill due? Data: cancel events / rebills due; refunds / orders. In DB: no. Source or view: Skio, CheckoutChamp, Shopify refunds. Josh: "you have access to my projection doc, repeat rates by day, week and month, we are way down on repeat this month... whats..." (T_26-29, 2026-10-16) ; "More returns, etc... like really get into specifics." (T_26-30, 2026-10-16)
4. Bundle and pack mix: did first-order pack size change, and when does each pack rebill? Data: first-order bottles, pack size, billing interval by cohort week. In DB: no. Source or view: Shopify orders + Skio/CC plan intervals. Josh: "Has the bundle mix increased causing that?" (T_26-31, 2026-10-16) ; "so pack size is way up?" (L2-9, 2026-10-21)
5. Which cohort is most off plan, and what is different about its first order (pack size, upsell attach, AOV)? Data: cohort week, first-order composition. In DB: no. Source or view: cohort export + order lines. Josh: "Which cohorts are failing?" (L2-7, 2026-10-21) ; "ok... why is it failing? Whats different about it?" (L2-8, 2026-10-21)
6. Price and order value: price per bottle, insurance on rebills, net price after refunds Data: sales / bottles by month, rebill order value. In DB: no. Source or view: Shopify + CheckoutChamp. Josh: "Look at total sales per SKU and divide by total units out per month <pasted_content id="e670"> Month Product variant SKU..." (L2-35, 2026-10-21) ; "why did the net price per bottle change?" (L2-39, 2026-10-21)
7. Day-21 / week-4 repeat: active cancels vs failed billing vs signup timing Data: paid share of rebills due by day since due date. In DB: no. Source or view: Skio + CheckoutChamp + Klaviyo cancel events. Josh: "Fewer of them rebilling by day 21: 37% vs 44%. <- can you dig into this. why is this the case... are active cancellation..." (L2-143, 2026-10-22) ; "The August cohort’s M2 is short about $48K; I haven’t split it yet. <- dig into this... is this also active cancels?" (L2-146, 2026-10-22)
8. Billing-reminder email (and any other flow change in the window): holdout read, with exposure validated Data: Klaviyo flow created/updated dates, holdout tag, cancels before due. In DB: no. Source or view: Klaviyo API. Josh: "these are set to get 3 days before bill... is there a strong correlation?" (L2-29, 2026-10-21) ; "we did run a holdout. check the klaviyo ckc billing flow... it tagged users who didnt get the flow" (L2-148, 2026-10-22)
9. Payment errors: PayPal gateway or batch failures on specific days Data: failed charges by day, error text. In DB: no. Source or view: CheckoutChamp. Josh: "Check this again: The billing batch ran late: on Oct 12 to 14 CheckoutChamp created far fewer orders, about $21K short....." (T_26-45, 2026-10-16) ; "I think I solved the target client id not set… can you see if those eventually processed?" (L2-111, 2026-10-22)
10. Size the waterfall: price cut, billing interval, pack mix, cohort shortfall, each as dollars with its control Data: bars summing to the miss. In DB: no. Source or view: sheet "Lonvera Subscriber Value YoY". Josh: "ok, so build me a waterfall of whats causing the miss this month on revenue, put it in this Lonvera Subscriber Value YoY" (L2-113, 2026-10-22) ; "For the longer billing intervals... showing -92k is that from september cohort or august? same for lower prices" (L2-116, 2026-10-22)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Skio (Shopify-native) rebill run-off is about 80% of the gap (first read); later why-chain: legacy Skio base -$42.5K/wk, renewals per due 0.84 to 0.70 starting the Sep 14-15 week | consistent-with (not randomized) | 2026-10-16 | T_26-29, T_26-53 |
| Pack mix and long billing intervals (3- and 5-packs from March; Aug 24 offer lifted bottles per first order 2.65 to 3.27): week-4 repeat 33.2% to 23.2% between cohorts | answer ranked it the largest driver (decomposition, not a control); delayed vs lost still open (settles when W35 week 8 closes Oct 26-Nov 1) | 2026-10-21 | L2-8, L2-9, L2-32, L2-45 |
| Billing-reminder email: recipients cancelled before due 9.8% vs 1.4% for the no-email half (7,082 vs 6,889 rebills due, Sep 21-Oct 16) | consistent-with; randomization real (Klaviyo 50/50) but no-email side visible only via a tag from about Oct 15, so exposure needed validation | 2026-10-23 | L2-157, L2-158, L2-159, L2-148 |
| "Mostly timing" explanation | refuted / withdrawn: it modeled how much timing could explain, never tested against a like-for-like control | 2026-10-23 | L2-150, L2-161 |
| Price cut Skio to CheckoutChamp (about $40 to $30-33 per bottle, about -$171K per month) | verified numbers; share of the October miss is a bar in the waterfall | 2026-10-21 | L2-35, L2-39, L2-88 |
| Shipping insurance missing on rebills lowers rebill order value | refuted (7,731 successful CheckoutChamp rebills since Sep 15 carry none; order value flat) | 2026-10-22 | L2-106, L2-142 |
| More cancels / refunds / failures overall; M1-M4 down 21-30%; billing batch ran late (about $21K short) | refuted / retracted: partial-window artifact; batch ran on time | 2026-10-16 | T_26-29, T_26-45 |
| PayPal target_client_id errors on Oct 12-13 caused failed rebills (245 rebills, about $13K, left un-retried) | verified for those days; Josh fixed it by Oct 22 | 2026-10-22 | L2-111, L2-141 |
| Failed-payment recovery missing in Klaviyo (about 2,870 declined rebills per month) | open (Klaviyo audit Oct 13) | 2026-10-13 | S_secondary-91, T_small-14, T_small-15 |

**Controls rule 13 needs.** Prior cohort at the same age AND same pack size; calendar-matched Skio-2025 vs CheckoutChamp-2026 comparison (Skio April/May 5-pack points were 9 and 23 signups, not data); billing-reminder holdout only after exposure is validated (who actually got the email, tag start date, contamination); pre/post on the Sep 14-15 flow scale-up with a flat pre-trend; every rate with its denominator (cancels per rebill due, not cancels per week).

**Guardrails.** G03 Partial day / partial window artifacts; G07 Rates with denominators, never counts; G09 Validate an experiment before reading it; G10 A timing or decomposition story is not attribution; G20 Cohort source, tool and pull date; G22 Do not re-recommend what was already done; check the change list first; G24 Metric definition before the number

**Supporting item ids** (latest 15 of 106; all in `why_candidates.json`): L2-146, L2-147, L2-148, L2-149, L2-93, L2-97, L2-98, L2-150, L2-157, L2-158, L2-159, L2-160, L2-161, L2-255, L2-264

### 3. Why a model number jumped or does not tie (EBITDA margin, Bear/Bull cases, cash, scenario vs P&L)

Items: 54 core, 104 linked; first asked 2026-07-17, last asked 2026-10-26. Decision weight M. In DB: P (proj_scenarios, proj_audit and proj_scenario_history are in the app database, excluded from analytics; the sheet is the other source). Build effort: M.

**Decomposition.** Any model output = f(inputs, structure). Josh question is always "what changed": diff model version A vs B with inputs frozen, then change one input at a time (taxes added, gross-up removed, tail rule, NC-ROAS slide, cost-line formulas), then tie to QuickBooks actuals.

**Ordered child questions (the order Josh actually walked, with his words)**

1. What changed? Pull the change history before explaining. Data: proj_audit / sheet version history. In DB: partial. Source or view: proj_audit, Sheets revision history. Josh: "My bear case is now looking crazy good in terms of EBIOTDA< why did it jump so much? Check like change hustory etc" (H_2026-08_1-16, 2026-08-03) ; "can yo check the history of those? Like they feel like they are bigger than they were earlier today" (HC_2026-07_7-36, 2026-07-28)
2. Without changing anything: why is this model different from the earlier one for the same month? Data: two builds, same inputs. In DB: partial. Source or view: proj_scenario_history. Josh: "ok without makeing any changes at all.... why is this so different than the other model you built earlier?" (H_2026-08_1-252, 2026-08-06)
3. Does a historical month tie to QuickBooks to the penny? Which line is missing or double counted? Data: P&L lines vs QBO. In DB: no. Source or view: QuickBooks, sheet. Josh: intent: Shows monthly NOI from QBO does not match the model's NOI. (HC_2026-07_7-101, 2026-07-30) ; "the historic EBITDA numbers are wrong... kinda makes me question everything. Tripple check that all historics are accura..." (HC_2026-07_4-118, 2026-07-19)
4. Do the scenario page, P&L and cash flow tie (NOI, ending cash)? Data: scenario vs P&L NOI. In DB: partial. Source or view: proj_scenarios. Josh: "Why is 2028 NOI for basecase $5,221,550 on scenario page but $4,681,550 on P&L? those should map perectly" (HC_2026-07_6-99, 2026-07-27) ; "why is december ending cash balance $2,553,375 but scenario shows EOY Cash balance $2,298,751" (HC_2026-07_6-127, 2026-07-27)
5. Does the cost structure behave like history (EBITDA about 8%, not 30%; fixed cost step vs earnings)? Data: each cost line tied to its driver. In DB: partial. Source or view: proj_pnl_* (not exposed). Josh: "EBITDA should not jump to 30% all of a sudden, it should sit around 8% EBITDA, figure out where the huge jump is coming..." (HC_2026-07_4-126, 2026-07-19) ; intent: Investigate why a ~$60K fixed cost increase yields $200K+ less earnings at same ROAS. (HC_2026-07_7-65, 2026-07-28)
6. Is the assumption credible vs actuals (NC-ROAS above any of the last 5 months; spend above prior peak)? Data: trailing actuals. In DB: yes. Source or view: analytics.orders_daily + platform_daily. Josh: "Forward NC-ROAS (0.82x) is set above every one of the last 5 months... This is a variable I can set, I moved it down to..." (HC_2026-07_5-84, 2026-07-24)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Thin-tail fallback inflated Bull and Bear (see cohort tree) | verified / fixed | 2026-08-03 | S_secondary-55 |
| Cash Flow read the dropdown-following P&L while Financing was pinned to Base: off $208K-$2.2M per year; re-pointed and tied across 4 scenarios x 4 years | verified / fixed | 2026-08-03 | S_secondary-56, HC_2026-07_6-99 |
| P&L sub-item summing bugs overstated historic NOI by up to $63K per month (missing shrinkage row, omitted marketing services, blank Jun-26 cell) | verified / fixed | 2026-07-30 | S_secondary-52, S_secondary-78 |
| COGS and expense double count plus scope mismatch inflated NOI | verified / fixed | 2026-07-24 | S_secondary-64, HC_2026-07_5-94 |
| Removing the Stage-2 gross-up: Bear 2027 repeat -28%, NOI -35%; most of the gap was shipping (gross vs net) | verified effect; right method not resolved | 2026-08-03 | H_2026-08_1-49, H_2026-08_1-62 |
| Daily EBITDA estimate ran 25-56% above booked NOI: payment processing double count $55,217 hidden by a 0.715 calibration factor; Upstack missing BackUp spend; tax and refunds in revenue | verified | 2026-09-24 | S_secondary-174, S_secondary-175, T_06-76 |
| Adding corporate taxes moved scenarios by about $1M | open (no answer on record) | 2026-07-28 | HC_2026-07_7-35, HC_2026-07_7-36, HC_2026-07_7-37 |
| Fixed cost +$60K vs earnings -$200K+ (disproportionate) | open | 2026-07-28 | HC_2026-07_7-65 |

**Controls rule 13 needs.** Freeze inputs and diff two versions; change one input at a time; tie to QuickBooks for closed months; never explain a jump before reading the edit history.

**Guardrails.** G05 Gross vs net: say which; G13 Frozen, first-row-only and shifted columns; pull the sheet before writing; G29 Read the edit history before explaining a model number; G03 Partial day / partial window artifacts

**Supporting item ids** (latest 15 of 104; all in `why_candidates.json`): S_secondary-174, S_secondary-175, T_06-76, T_18-3, S_secondary-70, T_21-58, T_21-59, T_26-39, T_26-57, T_26-63, L2-93, L2-97, L2-170, L2-203, L2-338

### 4. Spend fell or will not scale: bid floors, caps, budgets (spend capacity)

Items: 30 core, 69 linked; first asked 2026-07-16, last asked 2026-10-25. Decision weight H. In DB: P (spend by ad set Y; budgets and bids per ad set N, only daily summed bid totals in proj_feed_bids; edit history lives in the Graph activity log). Build effort: L.

**Decomposition.** Spend = sum over ad sets of min(budget, what auction delivers at the bid limit). Budget utilization = spend / budget. An ad set is bid-bound when realized cost per purchase exceeds the cost cap or realized ROAS is under the ROAS floor. Spend change = edit events x touched ad sets x delivery response + structure shifts (spend moved between structures).

**Ordered child questions (the order Josh actually walked, with his words)**

1. Do floors or caps change true business NC ROAS or just cap spend? Data: spend and business NC ROAS around floor edits. In DB: partial. Source or view: meta_adset_daily + orders_daily. Josh: "yeah I want to understand, does raising the tROAS goal actually imapct true busines NC-ROAS or just cap spend" (T_09-8, 2026-09-22) ; "even if i drop the goals in platform, is there data to support the NC-ROAS blended will drop? Seems like not much data t..." (T_08-45, 2026-09-23)
2. Is the ad set hitting its daily budget, or do caps bind? Data: budget utilization by ad set. In DB: no. Source or view: Graph API budgets (not in DB). Josh: "is boneflex hitting its daily budgets? or are the caps preventing it from spending, such that "push boneflex up" isnt re..." (T_18-10, 2026-09-30) ; "its not hitting its daily budget" (T_25-50, 2026-10-13)
3. What would blended ROAS have been at 0.90 vs 1.00? Data: counterfactual from spend curve. In DB: partial. Source or view: meta_campaign_daily. Josh: intent: Counterfactual blended ROAS for Oct 17 under the 0.90 floor. (T_26-83, 2026-10-18) ; "ok... ahnd what do you think it woeld hgave been if i had to .9 not 1?" (T_26-85, 2026-10-18)
4. Did tightening help or just cut spend to what was working? Data: scaling campaigns spend and ROAS before/after, touched vs untouched. In DB: partial. Source or view: meta_campaign_daily. Josh: "Did us tightening the ROAS goal in the old campaings at the start of the month help or just reduce spend to what was wor..." (T_26-65, 2026-10-18) ; "brining the total ROAS up" (T_26-67, 2026-10-18)
5. Would loosening back to prior give more spend with little ROAS hit? (price of the removed dollars) Data: marginal ROAS of removed spend. In DB: partial. Source or view: meta_adset_daily. Josh: "so loosening the caps to what they were prior will allow more spend with not a huge hit to ROAS" (T_26-68, 2026-10-18) ; "ok... so what should I do? slightly adjust caps? maybe on Vitabull troas? Should get spend up there with resutls? seend..." (T_26-74, 2026-10-18)
6. If caps were tightened, why did NC ROAS still fall off a cliff? Data: mix, testing share, structure. In DB: partial. Source or view: meta_adset_daily. Josh: "so if caps were tightened... why has NC-ROAS fallen off a cliff?" (L2-188, 2026-10-25) ; "so give me 2 sentence. Explicit dianosis" (L2-182, 2026-10-25)
7. Which bid type ran when the account spent $1M+ a month? Data: 2025 campaign setup. In DB: no. Source or view: Graph API history. Josh: "when the account spend 1M+ were they using cost caps / troas?" (T_17-67, 2026-10-04) ; "Can you pull down upstack data and meta data and do an analysis on which campaing type is better for NC-CPA and NC-ROAS,..." (T_08-35, 2026-09-22)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Floors and caps bind delivery, not efficiency: +0.10 floor = -29% spend, +0.12 attributed ROAS, no business effect (coefficient negative, t -1.35); raises cut new customers 21% | answered with a regression, not a randomized test (floors below 0.85 untested; recommended 14-day test at 0.90) | 2026-09-23 | T_09-8, T_08-45 |
| Sep 23-24 loosening bought almost nothing (39 edited ad sets 0.863 to 0.689 vs rest 0.652 to 0.832) | verified like-for-like | 2026-09-29 | S_secondary-79, T_17-12 |
| Sep 14 tightening of 29 ad sets caused about 98% of the Sep 14 cliff (touched -41%, rest of account -2%); later waves cut touched ad sets 52-75% | verified like-for-like | 2026-10-26 | L2-196 |
| Buyers each moved after the previous 7 days and the signal reversed (273 tightenings, 144 loosenings; tighten recovered +4.4% for -27% spend) | verified | 2026-10-26 | L2-196 |
| Oct 2-3 tightening mostly cut spend to what was working; account ROAS 0.70 to 0.68 while scaling campaigns 0.74 to 0.83; removed dollars returned about 0.61-0.67 | verified | 2026-10-18 | T_26-65, T_26-68 |
| Caps / ROAS floors raise CPM (Josh: "seems like direct correlation") | refuted: uncapped ad sets paid more CPM ($78.65 vs $60.17 capped vs $38.44 tROAS) | 2026-10-09 | T_21-60, T_17-44 |
| Bid limits bound every week (Meta ROAS 0.79-0.92 of floors; realized CPA about 30% above cost caps); scaling used 3-14% of budget in October | verified | 2026-10-26 | L2-196, T_25-50 |

**Controls rule 13 needs.** Touched vs untouched ad sets in the same days (event study); budget utilization before blaming budget; pre/post with flat pre-trend; price the marginal dollar (removed spend ROAS), not the average.

**Guardrails.** G08 Mix vs rate: shift-share before naming a cause; G09 Validate an experiment before reading it; G11 Exclusions and targeting: read the sentence lines, not the targeting field; G24 Metric definition before the number; G27 Attribution windows differ

**Supporting item ids** (latest 15 of 69; all in `why_candidates.json`): T_24-64, T_24-70, T_24-72, T_24-85, T_26-91, T_26-92, T_26-93, T_26-94, T_26-95, T_26-96, L2-182, L2-184, L2-185, L2-188, L2-196

### 5. New-customer ROAS down / CPA up / acquisition "free fall" (the root question)

Items: 24 core, 64 linked; first asked 2026-07-16, last asked 2026-10-27. Decision weight H. In DB: Y for spend, NC revenue, NC ROAS, Meta and Upstack attribution (analytics.platform_daily, orders_daily, meta_*_daily); N for breakdowns, bids, change log. Build effort: M (additions to the revenue tree).

**Decomposition.** Already in the revenue tree: NC revenue = spend x NC ROAS; NC ROAS = NC AOV / NC CPA; CPA = CPC / conversion; CPC = CPM / CTR. Additions mined from how Josh walked it: first split the fall into SPEND (log share) vs PER-DOLLAR yield; then split per-dollar into clicks per dollar x NC per click x AOV; then shift-share the per-dollar change into MIX (structure, ad-set age, product, country) vs RATE inside the same structure.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Is the number real and fresh? (half of one "drop" was a missing Amazon feed; partial-day spend) Data: data_coverage, last loaded day. In DB: yes. Source or view: analytics.data_coverage, etl_runs. Josh: "do a deep dive on performance over the last 30 days and then the last 7 days and then the last 3 days to see whats happe..." (T_17-5, 2026-09-29) ; intent: Meta click-to-purchase over time (pasted sources). (L2-231, 2026-10-27)
2. Josh hypothesis list, in the order he types it: creative that was working, bids, country, product spend and NC-AOV, CPC/CTR/CPM/spend, testing share, click-to-purchase, campaign changes, Google vs Meta Data: one comparison window then vs now. In DB: partial. Source or view: meta_*_daily, creative_tracker. Josh: "Creative that was working isnt working" (T_17-11, 2026-09-29) ; "change in bids" (T_17-12, 2026-09-29)
3. Choose THEN window carefully (spring Apr 13-May 24 at $200-343K per week vs now) and report spend vs per-dollar separately Data: weekly spend, NC, NC ROAS. In DB: yes. Source or view: analytics.platform_daily + orders_daily. Josh: "Lonvera Meta then-vs-now (2026-09-29; THEN = Apr 13-May 24 at $200-343K/wk, ROAS 0.90; NOW = Aug 31-Sep 27, ROAS 0.78):..." (S_secondary-81, 2026-09-29) ; "do a deep dive, stop recommending things we already did... whenever you have an idea... check to see fi we did it" (L2-186, 2026-10-25)
4. Why not just state data: keep digging for the mechanism, check what we already did before proposing Data: change list. In DB: no. Source or view: Graph API activity log, Klaviyo, CheckoutChamp. Josh: "so we have not really solve the why... why just again stated data... what about creative type? Keep digging into you unc..." (T_10-30, 2026-09-27) ; "do a deep dive, stop recommending things we already did... whenever you have an idea... check to see fi we did it" (L2-186, 2026-10-25)
5. Is it market-wide or ours? (control: Hydrant account, benchmark) Data: Hydrant CPM and ROAS pattern. In DB: no. Source or view: Hydrant Meta account. Josh: "im freaking out that performance is in what seems to be a free fall right as we took over, can you read industry news, r..." (T_21-53, 2026-10-08) ; "Or is this just ebbs and flows of a business" (T_26-56, 2026-10-18)
6. Did acquisition drop after the old campaign set stopped and caps tightened? Spend or efficiency? Data: spend by campaign set. In DB: yes. Source or view: analytics.meta_campaign_daily. Josh: "my entire question is round why aquisition dropped off." (T_26-59, 2026-10-18) ; "so if caps were tightened... why has NC-ROAS fallen off a cliff?" (L2-188, 2026-10-25)
7. What is fundamentally different: fewer new ads, narrower audience, auction price, conversion Data: CPM, click to NC rate. In DB: partial. Source or view: meta_ad_daily; breakdowns missing. Josh: "but like... whats fundementally different? such that it should be so bad now" (L2-180, 2026-10-25) ; "so what do you think the prioblem is? or causing the rise?" (L2-183, 2026-10-25)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Spend -63% explains about 3/4 of the NC fall (Jul 12 week 2,871 NC to Oct 18 week 737; log share 73%); the rest is per-dollar yield -27% vs Sep 6 peak | verified | 2026-10-26 | L2-196, L2-182, L2-186 |
| Sep 23-24 bid loosening: spend +12.4% per day, Meta revenue +0.4%; real DTC decline 6-7% (rest was a missing Amazon feed) | verified | 2026-09-29 | T_17-5, S_secondary-79 |
| Spend moved off structures returning 0.6-0.8 NC ROAS onto structures returning 0.3-0.5 (37 ad sets paused Oct 1-5; originals off Oct 23-24) | verified timing; consistent-with causation | 2026-10-26 | L2-196, L2-188 |
| September was a 5-week conversion pocket at 1.6x normal; Oct reverted to the July per-click rate | numbers verified; mechanism consistent-with | 2026-10-26 | L2-196 |
| CPM about doubled in a mid-August step in every age cell (US $39 Jul to $70 Sep) | verified fact; cause untested | 2026-10-26 | L2-183, L2-196 |
| Market-wide ad cost inflation or the takeover restructure | refuted for the restructure (Hydrant control shows same CPM/ROAS pattern); weekly NC ROAS flat since close | 2026-10-08 | T_21-53 |
| Product mix, placement, device, demographic, creative format, country, audience exhaustion, pixel, rebills, price | refuted across lanes | 2026-10-26 | L2-191, T_10-30, T_25-41 |
| NC AOV drop: upsell pages Aug 21 lifted AOV about $10; give-back is more single bottles, fewer Buy 3 Get 2, lower insurance attach, cost-cap campaigns; Trybe $0 gifts inflate counts | verified | 2026-10-03 | T_19-26, T_19-27 |

**Controls rule 13 needs.** Shift-share every per-dollar change into mix vs within-structure rate; like-for-like ad-set age; Hydrant as external control; each "then vs now" window shown with its spend level.

**Guardrails.** G01 Name the basis before comparing; G02 Meta spend comes from the Graph API (both ad accounts), never Upstack; G03 Partial day / partial window artifacts; G08 Mix vs rate: shift-share before naming a cause; G15 Denominators across scope: Main-only vs Main + BackUp + Google; G22 Do not re-recommend what was already done; check the change list first; G26 Blip, ebb and flow, or real: use an external control and do not alarm

**Supporting item ids** (latest 15 of 64; all in `why_candidates.json`): L2-164, L2-168, L2-169, L2-171, L2-180, L2-182, L2-183, L2-184, L2-186, L2-188, L2-191, L2-196, L2-208, L2-211, L2-231

### 6. Creative launch volume, hit rate and scale ("do more ads drive more spend?")

Items: 33 core, 61 linked; first asked 2026-07-16, last asked 2026-10-28. Decision weight H. In DB: P (meta_ad_daily from 2026-05-06; first spend day derivable; spring (Apr-May) ad grain not in DB; tracker launch dates unreliable). Build effort: M.

**Decomposition.** Spend capacity ~ winners x spend per winner. Winners per week = new unique concepts (name-deduped, first spend day, excluding renamed re-uploads and relaunch copies) x hit rate (share reaching $1K in 14 days / $5K). Reverse path to test: spend causes launches (Meta allocates spend to ads that look good), not only launches cause spend.

**Ordered child questions (the order Josh actually walked, with his words)**

1. What is the hit rate of ads that spent more than $5K, how many were launched, how many per week? Data: first-spend-day ad families, spend threshold hits. In DB: partial. Source or view: meta_ad_daily. Josh: "ok, do a deep analysis of these ads, what the hit rate on ads that were launched and spent more than 5k. How many ads we..." (HC_2026-07_4-13, 2026-07-16)
2. Did more ads launching mean more performance, and more scale? Correlation or causation? Data: weekly launches vs weekly spend. In DB: partial. Source or view: meta_ad_daily. Josh: "Did more ads launching = better performance?" (HC_2026-07_4-14, 2026-07-16) ; "and more scale" (HC_2026-07_4-15, 2026-07-16)
3. Ads are the real variable, campaigns are conduits: ad count AND raw count as features Data: ad count in scale vs test. In DB: partial. Source or view: meta_ad_daily. Josh: "campaigns dont drive results, they are a meere conduite of ads. Use them as like a holding, but ads are the real variabl..." (HC_2026-07_4-23, 2026-07-16) ; "and then also try to understand raw ad count and scale, use both of those as feautes and try to understand the relations..." (HC_2026-07_4-24, 2026-07-16)
4. Reverse causation: ad spend itself comes from Meta allocating to ads that look good Data: spend before/after launch. In DB: partial. Source or view: meta_ad_daily. Josh: "then also understand that the ads indiviudal spend themselves is also from a meta algo and that the ads got more spend b..." (HC_2026-07_4-28, 2026-07-16)
5. Has creative volume actually dropped? (tracker said no; Meta ad-level said 205 to 80 per week) Data: new concepts per week from Meta, not tracker. In DB: partial. Source or view: meta_ad_daily. Josh: "has my creative volumne actually dropped?" (T_18-33, 2026-09-30) ; "no... pull the launch / ad data from meta not my tracker" (T_18-34, 2026-09-30)
6. Hit rate and weekly launches at scale vs now; images vs video split Data: concepts reaching $1K / $5K. In DB: partial. Source or view: meta_ad_daily. Josh: "whats my hit rate for ads?" (T_11-52, 2026-10-01) ; "when we were spending the msot we were spending, how many vidoes and images was that a week that was the 205 concepts pe..." (T_11-79, 2026-10-05)
7. Does tripling launches turn it around? (thesis test) Data: spend capacity vs launches. In DB: partial. Source or view: meta_ad_daily. Josh: "is my thesis of more ad launches should turn it around work?" (T_21-54, 2026-10-08) ; "we are launching way more than 80 now." (L2-181, 2026-10-25)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Volume problem, not quality: new unique concepts 205 per week (Apr 13-May 24, $276K per week) to about 80 (Sep, $125K per week); concepts reaching $1K in 14 days 9.2 to 5.8 to 4.2; hit rate flat about 5% | verified (Meta ad level); the 80 per week is a Sep number and STALE | 2026-09-30 | S_secondary-82, T_18-33 |
| Current volume: wk Oct 5 322, Oct 12 272, Oct 19 886 first-spend families (not checked for renamed re-uploads) | open | 2026-10-28 | L2-181, L2-241 |
| Aug 17-30 rebuild launches were renamed re-uploads, not real launches (Aug 1,228 vs 151) | verified (Josh correction) | 2026-10-01 | T_17-39 |
| Total launches fell only 20% because 74% of launches are now copies | verified | 2026-10-01 | T_17-36 |
| More ads = more spend capacity: right mechanism but not a way to deploy $650K at 0.7 | partial judgment | 2026-10-08 | T_21-54 |
| 90% of ad-set-weeks run under 50 conversions (253 active ad sets in 23 campaigns on Sep 23); budget (about $20 per ad per day), not ad supply, limits how many ads can be read | verified (correlational) | 2026-09-23 | T_08-44, T_09-12, T_24-64 |

**Controls rule 13 needs.** Pull launches from Meta ad level, never the tracker; dedupe by name and drop renamed re-uploads and relaunch copies; match spend levels when comparing launch weeks; test the reverse arrow (spend to launches); denominator for hit rate is ads that got first spend.

**Guardrails.** G12 Launch volume comes from Meta ad-level first-spend, deduped; never the tracker; never a stale number; G13 Frozen, first-row-only and shifted columns; pull the sheet before writing; G25 Survivorship and self-selected spend; G23 Sample size and significance

**Supporting item ids** (latest 15 of 61; all in `why_candidates.json`): T_24-61, T_24-62, T_24-63, T_24-64, T_24-65, T_24-66, T_24-67, T_24-68, T_24-72, T_23-21, T_23-22, T_23-23, L2-180, L2-181, L2-241

### 7. Which creative / ICP / pain point / offer / vendor / format used to work and stopped

Items: 23 core, 49 linked; first asked 2026-06-09, last asked 2026-11-03. Decision weight M. In DB: Y (analytics.creative_tracker joined to meta_ads / meta_ad_daily; Meta-reported and Upstack columns). Build effort: S (tag mix and shift-share already in the revenue tree; add thresholds and survivorship guards).

**Decomposition.** Performance by tag = spend-weighted ROAS over ad families in that tag over a window; "used to work, stopped" = tag ROAS in THEN window vs NOW with spend floor, split into tag mix shift vs within-tag decay.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Which type of ad did better on ROAS, CPA and scale over 14 days? (source, vendor) Data: ROAS, CPA by ad source. In DB: yes. Source or view: creative_tracker + meta_ad_daily. Josh: "over the last 14 days, which type of ad has done better on ROAS, CPA, and liek scale." (H_2026-06_1-367, 2026-06-09)
2. What have we launched, what works, where is arbitrage, what used to work that does not now Data: tag cuts THEN vs NOW. In DB: yes. Source or view: creative_tracker. Josh: "spin up a sub agent that does an analysis of what we have launched, whats working, what isnt, where is there arbitrage....." (T_08-31, 2026-09-19) ; "Creative that was working isnt working" (T_17-11, 2026-09-29)
3. Top 3 ICPs and top 3 pain points per product, last 30 days Data: tag ROAS with spend floor. In DB: yes. Source or view: creative_tracker. Josh: "Give me a VERY tight, breakout of what ICPs and pain points are working per product. Like top 3 ICPs and top 3 pain poin..." (T_11-22, 2026-09-29) ; "look at the creative tracker and see what ICP was working and isnt anymore" (T_20-37, 2026-10-08)
4. Video vs image breakout when scaled; can video absorb more spend? Data: format cut by window. In DB: yes. Source or view: meta_ad_daily. Josh: "show me the video vs image breakout for when we were scaled at good efficency" (T_17-32, 2026-10-01) ; "videos could spend more than images though?" (T_17-34, 2026-10-01)
5. Which demographic did the ad deliver to? Data: age/gender. In DB: no. Source or view: Meta breakdowns. Josh: "make sure to also check which ads are delivered to what demographic and see if thats helpful on what works and what does..." (T_11-55, 2026-10-02)
6. Is a setting (Related media / Advantage+) on top performers? Data: creative spec. In DB: no. Source or view: Graph API creative spec. Josh: "so some of my top performing ads have this... are you able to see if thats on for the newer ads?" (T_17-26, 2026-10-01) ; "Bull33-B3-Hook 2_reup this haws re3lated media on" (T_17-29, 2026-10-01)
7. Which 2025 statics worked that we are not running? Data: last-year ad families. In DB: partial. Source or view: creative_tracker (lifetime frozen 9/17). Josh: "what beef liver statics were working last year that we arent running this year?" (T_17-30, 2026-10-01)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Offer + clock is the only construction above 1.0 ROAS in every window; offer-led is best on every product; advantage falling now | verified (strongest repeated finding) | 2026-10-01 | T_08-31, T_05-52, T_11-17 |
| ICP is a weak lever (VB tags within about 0.05 ROAS); best-paying audiences were abandoned; Won't Slow Down biggest then collapsed | verified | 2026-10-08 | T_20-37, T_22-4 |
| Vendor (Madre) as a THEN-to-NOW cause | refuted | 2026-09-30 | S_secondary-81, T_08-31 |
| Format (image vs video) broke efficiency | refuted: both decayed alike; video absorbs more spend and hits $1K twice as often | 2026-10-01 | T_17-32, T_17-34 |
| Creative fatigue: families older than 60 days down 7-15%; no new Vital Bull winner since early Sep | partial | 2026-09-29 | T_17-11 |
| Demographics explain the decline | refuted | 2026-09-30 | T_11-55, S_secondary-87 |
| Copy-only tags are wrong: looking at the ad changed the line on 61% of VB rows (48% of spend) | verified | 2026-11-03 | L2-267, L2-271 |

**Controls rule 13 needs.** Spend floor (tiny-spend ROAS misleads); fixed thresholds (works >= 1.00, not <= 0.80, in between is average); tag mix shift-share; first-row-only metrics in creative_tracker; Meta data not the tracker for launch facts.

**Guardrails.** G14 Small-spend ROAS and fixed thresholds; G13 Frozen, first-row-only and shifted columns; pull the sheet before writing; G19 Results are not causes; G25 Survivorship and self-selected spend; G12 Launch volume comes from Meta ad-level first-spend, deduped; never the tracker; never a stale number

**Supporting item ids** (latest 15 of 49; all in `why_candidates.json`): T_20-37, T_22-4, T_22-50, T_23-2, S_secondary-97, T_24-6, T_25-6, T_24-22, T_26-75, S_secondary-92, T_38-28, T_24-87, L2-191, L2-267, L2-271

### 8. Subscription rebill failures and declines

Items: 33 core, 48 linked; first asked 2026-07-17, last asked 2026-10-22. Decision weight M. In DB: N (Adyen, CheckoutChamp, Skio, Shopify; no order or subscription level data in the DB). Build effort: L.

**Decomposition.** Paid rebills = rebills due x (1 - cancel before due) x first-attempt approval + recovered on retry. Report as per-rebill-due and per-attempt rates, by cycle (cycle 2 vs 3), with a stated metric definition (first try vs ever; order vs customer).

**Ordered child questions (the order Josh actually walked, with his words)**

1. How many second rebills actually went through; first-try success? Data: cycle reach counts, attempts. In DB: no. Source or view: Adyen TransactionDetails, CheckoutChamp. Josh: "How does that line up with this data? Cycle ReachedCount: 1 27,190; 2 13,087 (48.1%); 3 3,243 (11.9%); 4 587; 5 2" (HC_2026-07_5-29, 2026-07-22) ; "whats a super clear what to ourline this data with the explicit question of only 15.8% of second orders worked on the fi..." (HC_2026-07_6-74, 2026-07-27)
2. Retries vs not: share of orders needing retries Data: attempt counts. In DB: no. Source or view: Adyen. Josh: "Is there any way to cut the transaction data by retries? how many of these rejections were tries vs not. what percent of..." (HC_2026-07_5-42, 2026-07-23)
3. Why are subscription rebills declined while non-subscription repeat purchases succeed? (Josh: how do they know to block it?) Data: decline codes by flag. In DB: no. Source or view: Adyen risk and dispute summary. Josh: "This feels a bit off... there is a high rate of subs getting blocked, but then people are repeat purchasing. how are the..." (HC_2026-07_5-51, 2026-07-23) ; "like how do they know to block it?" (HC_2026-07_5-52, 2026-07-23)
4. Reconcile metric definitions (15.8% vs about 80% on cycle 2) Data: definition table. In DB: no. Source or view: pasted cycle tables. Josh: "this data is from the exact period you have and looks like its closer to 80% sucsess rate on cycle 2" (HC_2026-07_6-168, 2026-07-28) ; intent: Challenge why 15.8% was reported vs ~80% (HC_2026-07_6-172, 2026-07-28)
5. If fixed, how many more charges and revenue per month? Data: failing subs x value. In DB: no. Source or view: Skio. Josh: "assuming I do this... how many more charges / rev a month will i realize?" (HC_2026-07_5-61, 2026-07-23) ; "but isnt there like 15k subs, that just arent working? Feels like more than 30k in rev" (HC_2026-07_5-63, 2026-07-23)
6. Specific day failures (PayPal target_client_id, Oct 12-13) Data: failed charges by day. In DB: no. Source or view: CheckoutChamp. Josh: "I think I solved the target client id not set… can you see if those eventually processed?" (L2-111, 2026-10-22) ; "Check this again: The billing batch ran late: on Oct 12 to 14 CheckoutChamp created far fewer orders, about $21K short....." (T_26-45, 2026-10-16)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Josh two numbers conflict: "only 15.8% of second orders worked on the first try" vs same-period "closer to 80% success on cycle 2"; Oct 16 first-attempt rebill success flat 71-74% | contradicted; definitions never reconciled | 2026-07-28 | HC_2026-07_6-74, HC_2026-07_6-168, HC_2026-07_6-172 |
| Oct 12-13 PayPal gateway permission error left 245 rebills (about $13K) un-retried | verified; fixed by Josh | 2026-10-22 | L2-111, L2-141, T_26-45 |
| Whether upsell orders or non-subscriber orders inflate apparent repeat | open (no answer) | 2026-07-23 | HC_2026-07_5-39, HC_2026-07_5-50, HC_2026-07_5-49 |
| Failed-payment flow missing in Klaviyo (about 2,870 declined rebills per month, 18-26% recover in 14 days) | open (Klaviyo audit Oct 13) | 2026-10-13 | S_secondary-91, T_small-14, T_small-15 |

**Controls rule 13 needs.** Fix the metric definition first; same period; per-attempt and per-due denominators; separate retries.

**Guardrails.** G24 Metric definition before the number; G07 Rates with denominators, never counts; G03 Partial day / partial window artifacts

**Supporting item ids** (latest 15 of 48; all in `why_candidates.json`): HC_2026-07_6-72, HC_2026-07_6-73, HC_2026-07_6-74, HC_2026-07_6-77, HC_2026-07_6-168, HC_2026-07_6-172, HC_2026-07_7-39, S_secondary-91, T_small-14, T_small-15, T_small-23, T_26-45, L2-111, L2-112, L2-141

### 9. Meta-reported results vs actual (Upstack / Shopify) gap: ROAS, CPA, returning buyers, spend source

Items: 22 core, 42 linked; first asked 2026-08-25, last asked 2026-10-27. Decision weight H. In DB: Y (analytics.meta_ad_daily carries Meta and Upstack-attributed columns; platform_daily + orders_daily for actual); N for exclusions, optimization event, customer-level. Build effort: S.

**Decomposition.** Gap = Meta ROAS minus actual NC ROAS = denominator gap (account scope: Main only vs Main + BackUp + Google spend) + numerator gap (Meta counts returning buyers, longer or incremental windows, same-day second checkouts). Always state the basis: Meta-reported, Upstack new customers (NC), Upstack all orders.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Which basis and which days? (Meta numbers or Upstack, exact dates) Data: basis label, date range. In DB: yes. Source or view: analytics views (named columns). Josh: "Can you pull down upstack data and meta data and do an analysis on which campaing type is better for NC-CPA and NC-ROAS,..." (T_08-35, 2026-09-22) ; "check to make sure all like spend numbers align across all the chart" (T_20-55, 2026-10-07)
2. Does the spend source include the BackUp account? Use Graph API not Upstack for Meta spend. Data: Meta spend both accounts. In DB: yes. Source or view: analytics.meta_campaign_daily. Josh: "upstack have the backup account? Im assuming not" (T_20-52, 2026-10-07) ; "For ANY Meta number use the Graph API source, not Upstack" (S_secondary-184, 2026-10-06)
3. Is 1-day-click a clickier audience? Does 1DC CPA match actual NC CPA, and does matching on total mean matching on who? Data: 1DC vs 7DC1DV, NC CPA. In DB: partial. Source or view: meta_ad_daily + orders_daily. Josh: "is 1dc for meta a "clickier" audienc e" (L2-165, 2026-10-23) ; "the cpa on 1dc, is much closer to my actual cpa" (L2-166, 2026-10-23)
4. Pull business data to get the real CPA and NC ROAS, not Meta-reported Data: NC revenue / spend. In DB: yes. Source or view: analytics.orders_daily. Josh: "ok... but pull in business data to understand the real CPA and NC-ROAS" (L2-168, 2026-10-23) ; "we tightened caps becasue NC-CPA and NC-ROAS was very bad... even thought meta says it was fine" (L2-169, 2026-10-23)
5. Are exclusions really on? (Josh: "there is exclusions") Data: targetingsentencelines, activity log. In DB: no. Source or view: Graph API. Josh: "1. The latest step down is the 1DC campaigns: cheap clicks, customer overlap, no exclusion. <- there is exclusions. Am i..." (L2-190, 2026-10-25)
6. What does the algorithm optimize on (pixel Purchase vs NC Purchase)? Data: optimization event. In DB: no. Source or view: Ads Manager. Josh: "Check my current bid and attribution strucutre in meta + performance in meta, then check the data in upstack. See wtf is..." (L2-179, 2026-10-25)
7. Does the attribution setting (7DC1DV, incremental) explain the ROAS? Data: campaign attribution spec. In DB: no. Source or view: Graph API. Josh: "how incremental vs non incremental attribution?" (T_26-82, 2026-10-18) ; "also myabe something to do with change in attrinution?" (T_10-26, 2026-09-27)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Meta ROAS 0.705 vs actual 0.586 (Oct 19-24): 0.061 (51%) is denominators (BackUp $4,123 from Oct 21, Google $3,200), 0.058 (49%) Meta counting non-new buyers (returning share of Meta purchases 9% to 17.6%) | verified | 2026-10-26 | L2-196, L2-168 |
| Upstack omits the BackUp account: Jun spend $375,303 vs Graph API $453,845; Jul $764,305 vs $864,614; NC ROAS Jun 0.75 to 0.62 after switch | verified; rule: Meta spend from Graph API | 2026-10-07 | S_secondary-184, T_20-53, T_20-52 |
| Exclusions: Josh was right; Graph targeting field hides exclusions on newer-format ad sets; only gap was 3 OffWinners1DC ad sets Oct 23 17:16 to Oct 24 08:29 PT ($1.4K, 19 existing-customer purchases) | verified (resolution in Josh favor) | 2026-10-26 | L2-190, T_10-25 |
| 1DC CPA is closer to actual NC CPA because it is "clickier"; 30-day 1DC CPA tracks 30-day NC CPA but total match is not who | verified in part | 2026-10-23 | L2-165, L2-166 |
| "Upstack purchases 20-36% below Meta" compared Upstack NEW customers with Meta ALL purchases; like for like (all orders) Upstack is above Meta for Vita Bull and Beef Liver | corrected (source: josh-hq ma-calano decision log 2026-11-04; no item id) | 2026-11-04 | none (see source in status) |
| Pixel audiences report 20 people so pixel purchaser exclusions exclude nobody; an unused "NC Purchase" custom conversion matches Upstack within 1% while 74% of pixel Purchase events are returning | verified; learning-drift to known buyers untested | 2026-10-26 | L2-196 |

**Controls rule 13 needs.** Same-basis comparison table (Meta-reported, Upstack NC, Upstack all) over identical days and account scope; NC-Purchase-optimized ad set as the control for Purchase-event drift.

**Guardrails.** G01 Name the basis before comparing; G02 Meta spend comes from the Graph API (both ad accounts), never Upstack; G15 Denominators across scope: Main-only vs Main + BackUp + Google; G11 Exclusions and targeting: read the sentence lines, not the targeting field; G27 Attribution windows differ; G03 Partial day / partial window artifacts

**Supporting item ids** (latest 15 of 42; all in `why_candidates.json`): L2-165, L2-166, L2-167, L2-168, L2-169, L2-173, L2-174, L2-175, L2-176, L2-177, L2-179, L2-190, L2-196, L2-231, L2-232

### 10. CPM, cost per reach and frequency up

Items: 22 core, 40 linked; first asked 2026-08-25, last asked 2026-10-25. Decision weight M. In DB: P (impressions in meta_ad_daily; reach only account level in analytics.meta_reach; age, gender, placement, country breakdowns are not in the DB). Build effort: M.

**Decomposition.** Cost per 1,000 reached = CPM x frequency / 1000. Log-decompose the gap vs a reference window into: price per impression inside each country/age cell, overlap between ad sets (same-day), within-ad-set frequency, and mix (country, age, product). Check exhaustion separately (share of reach that is new in 7-28 days).

**Ordered child questions (the order Josh actually walked, with his words)**

1. Why are CPMs up? Mix first: product, spend, creative type, geo, launch frequency Data: CPM by cut. In DB: partial. Source or view: meta_ad_daily (no geo/age). Josh: "Why do you think CPMs are up?" (H_2026-08_1-943, 2026-08-25) ; "product mix, spend mix, creative type, geo, creative launch, etc" (H_2026-08_1-941, 2026-08-25)
2. Share of impressions going to NEW people over time; keep digging beyond stating data Data: reach new vs total. In DB: no. Source or view: Meta reach API. Josh: "you never ansewred clearly the % of new impressions per platform cahnge over time" (T_10-29, 2026-09-27) ; "so we have not really solve the why... why just again stated data... what about creative type? Keep digging into you unc..." (T_10-30, 2026-09-27)
3. Product mix, image vs video, broad vs narrow, uncapped volume bidding, other mechanisms Data: CPM by cut. In DB: partial. Source or view: meta_ad_daily. Josh: "maybe spend on different product mox" (T_10-31, 2026-09-27) ; "check cost per reach on images vs video" (T_10-32, 2026-09-27)
4. International vs US reach, frequency, cost per reach Data: country cut. In DB: no. Source or view: Meta breakdowns. Josh: "for meta, test if international vs US has any impact on reach, frequencey and cost per reeacj" (T_10-28, 2026-09-27) ; "see why my frequncy and csot per account center reached is so high?" (T_17-41, 2026-10-02)
5. Rolling and incremental reach, now vs then (Upstack) Data: reach windows. In DB: no. Source or view: Upstack / Meta. Josh: "pull upstack data and look at meta rolling reach, and inrementalm reach and see whats different now vs then" (T_17-42, 2026-10-02)
6. Do caps / tROAS floors raise CPM (retargeting to hit goals)? Data: CPM by bid type, same week. In DB: partial. Source or view: meta_adset_daily. Josh: "i think attribution, and bid type is interesting... the caps / troas stuff is driving the cost up since its retargeting..." (T_17-44, 2026-10-02) ; "is it the cost caps / troas casuing the CPMs to raise? seems like direct correlation" (T_21-60, 2026-10-09)
7. How do I reach more new people vs overlap? (many ad sets in the scaling days) Data: ad set count, overlap. In DB: no. Source or view: Meta reach. Josh: "but I gues how do I make it reach the more new people vs ovelap? When i was scaling there was all these adsets?" (L2-184, 2026-10-25) ; "so what do you think the prioblem is? or causing the rise?" (L2-183, 2026-10-25)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Cost per 1,000 accounts reached $448.5 (Sep 3-30) vs $146.5 (Apr 13-May 10); weekly gap +103.7 log points = price per impression +47.4, ad-set overlap +29.4, in-set frequency +19.9, mix +7.1 | verified decomposition | 2026-10-02 | S_secondary-88, T_17-41 |
| Audience exhaustion | refuted (share new 0.62 vs 0.54-0.65) | 2026-10-02 | S_secondary-88 |
| Caps or tROAS floors drive CPM | refuted: uncapped paid more; Sep 14 tightening cut CPM about 16%; loosening cut frequency about 5% with ROAS -33% | 2026-10-09 | T_21-60, T_17-44 |
| Product mix, image vs video, placement mix, country mix, concentration | refuted for CPM rise (country mix about 0%; placement shift to FB Reels is a wash); VB share and men 45+ share are top weekly predictors | 2026-10-02 | T_10-30, T_25-41, S_secondary-88 |
| CPM about doubled in a mid-August step in every age cell, +41% YoY, not seasonal (2025 flat Jul-Oct) | verified fact; cause untested (bid strategy, rebuild, auction, Meta side) | 2026-10-26 | L2-183, L2-196 |
| Market-wide inflation (health and wellness +24-38% YoY; Hydrant shows same pattern) | consistent-with for part | 2026-09-27 | T_10-30, T_21-53 |
| Q4 CPM ramp arrived early | refuted (CPM fell Sep to Oct) | 2026-10-26 | L2-196 |

**Controls rule 13 needs.** Hydrant as an external control; reference-window decomposition (log points) not a story; reach de-duplicated (never summed across ad sets).

**Guardrails.** G08 Mix vs rate: shift-share before naming a cause; G03 Partial day / partial window artifacts; G26 Blip, ebb and flow, or real: use an external control and do not alarm; G19 Results are not causes

**Supporting item ids** (latest 15 of 40; all in `why_candidates.json`): T_19-27, T_20-25, T_20-26, T_21-53, T_21-60, T_21-61, T_25-41, T_25-42, L2-180, L2-182, L2-183, L2-184, L2-185, L2-190, L2-196

### 11. Is the data fresh / did a feed or sync break (cross-cutting first check)

Items: 23 core, 34 linked; first asked 2026-07-19, last asked 2026-11-04. Decision weight H (gate for every other tree). In DB: Y (analytics.data_coverage and analytics.etl_runs; feeds service logs). Build effort: S.

**Decomposition.** Before any why: last loaded day per source, partial-day and partial-window flags, source of each series (Graph API vs Upstack), timezone bucket (Pacific), and whether an upstream sync or login failed.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Did the daily pull run this morning? Which sources are missing? Data: last loaded day per source. In DB: yes. Source or view: analytics.data_coverage. Josh: intent: Why the 10-06 row disagrees with the Upstack 0.68x. (T_20-58, 2026-10-07) ; "yesterdays data did not pull in correctly. I need you to do a deep dive and make sure this never happens again. fix what..." (T_20-61, 2026-10-08)
2. Is a number partial (today, unfinished month, missing Amazon rows after 9/24)? Data: row counts vs calendar. In DB: yes. Source or view: data_coverage. Josh: "do a deep dive on performance over the last 30 days and then the last 7 days and then the last 3 days to see whats happe..." (T_17-5, 2026-09-29) ; "Why on automnated pacing tab for the lonvera projections tracker is DTC CPA $97.99 but on the monthly snapshot its $104...." (T_20-63, 2026-10-09)
3. Which source and day basis? (UTC vs Pacific; Graph API vs Upstack) Data: source labels. In DB: yes. Source or view: schema docs. Josh: "hmmm, but the NC-Rev is wrong... maybe it needs to wait until like 4AM PST to reun?" (T_08-28, 2026-09-19) ; "upstack have the backup account? Im assuming not" (T_20-52, 2026-10-07)
4. Does it run without my laptop? Is everything updated and checked? Data: feeds service status. In DB: yes. Source or view: etl_runs. Josh: "ok so all the data is fresh now? and running daily?" (L2-292, 2026-11-04) ; "ok... and this will all update and run tomorrow at like 3AM? Everything updated and checked? Did you write all the test..." (L2-310, 2026-11-04)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| GitHub Actions cron fired 3-4h then 8-12h late; Amazon task stuck on approval; moved to hourly desktop routine 10-13, then to Railway feeds | verified / fixed | 2026-10-13 | T_20-61, S_secondary-73, L2-287 |
| UTC day buckets caused 0.5-13% gap vs manual Shopify; Pacific matches the dashboard to the dollar | verified / fixed | 2026-09-19 | T_08-28, S_secondary-294 |
| Amazon - Daily had no rows after 2026-09-24 and made NC ROAS look worse; filled, last 3 days 0.747 not 0.662 | verified | 2026-09-29 | T_17-5 |
| Meta pull stale at read time: daily NC ROAS 0.96 vs about 0.68 after re-run | verified | 2026-10-07 | T_20-58 |
| Snapshot included today partial spend with no NC orders (CPA $104.36 vs $97.99) | verified / fixed | 2026-10-09 | T_20-63 |
| Feeds that stay manual: QuickBooks, cohorts, POs, FBA and Intl stock counts, ExpandFi login | open | 2026-11-04 | L2-292, L2-310 |

**Controls rule 13 needs.** Run the freshness check before the analysis and print last loaded day beside every number.

**Guardrails.** G03 Partial day / partial window artifacts; G06 Day buckets are Pacific; G02 Meta spend comes from the Graph API (both ad accounts), never Upstack; G13 Frozen, first-row-only and shifted columns; pull the sheet before writing

**Supporting item ids** (latest 15 of 34; all in `why_candidates.json`): T_20-52, T_20-55, T_20-58, T_fix-50, S_secondary-73, T_20-61, T_20-63, T_20-64, T_22-66, S_secondary-72, L2-287, L2-291, L2-292, L2-294, L2-310

### 12. Pacing vs plan and NOI / EBITDA pacing vs booked (tie-outs)

Items: 14 core, 26 linked; first asked 2026-07-19, last asked 2026-10-29. Decision weight M. In DB: P (proj_pacing_* tables exist in the app DB, not in analytics; revenue and spend pacing inputs are in analytics). Build effort: M.

**Decomposition.** Pacing % = MTD actual / (plan x days elapsed share) and projection = MTD / full days elapsed x days in month. Tie-out check: same basis (gross vs net of sales tax and refunds), same days (yesterday, not today), same source (Graph API), same scope (BackUp included).

**Ordered child questions (the order Josh actually walked, with his words)**

1. Why does pacing look too high or too low (EOM EBITDA vs P&L)? Data: pacing basis, estimate vs booked. In DB: partial. Source or view: proj_pacing_*. Josh: "so the EBITDA at EOM should line up with my P&L now?" (T_06-74, 2026-09-24) ; "I dont want any calibration factor etc, it should be pretty simple to just line up all the cost, fixed cost spread acros..." (T_06-76, 2026-09-24)
2. Why do components say 99.6 / 99.8 but net says 89.5? Data: gross vs net. In DB: no. Source or view: Upstack Q column (gross). Josh: "If new rev is 99.6 and repeat is 99.8, why is net 89.5.... those should line up. I think its sales tax, but we can remov..." (T_18-64, 2026-10-04)
3. Why is NOI off across Automated Pacing and Monthly Snapshot? Data: sum-by-day vs rebuild. In DB: partial. Source or view: sheet. Josh: "why NOI off across automated pacing and monthly snapshot?" (L2-242, 2026-10-29)
4. Why does CPA differ between two tabs ($97.99 vs $104.36)? Data: partial-day spend. In DB: yes. Source or view: analytics.platform_daily. Josh: "Why on automnated pacing tab for the lonvera projections tracker is DTC CPA $97.99 but on the monthly snapshot its $104...." (T_20-63, 2026-10-09)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Net DTC pacing gap: refunds about $10,900 of $12,038, tax about $1,300 (Josh guessed sales tax) | verified | 2026-10-04 | T_18-64 |
| Tax-inclusive pacing revenue vs net goal inflated EBITDA pacing 175% to 136% after the tax line | verified / fixed | 2026-10-13 | T_26-7, S_secondary-299 |
| Daily EBITDA estimate +25-56% vs booked NOI: processing double count, missing BackUp spend, tax and refunds in revenue | verified | 2026-09-24 | S_secondary-174, S_secondary-175 |
| Refunds 9-10% of DTC revenue, so forward P&L may be overstated | open | 2026-10-04 | T_18-64 |

**Controls rule 13 needs.** Always say gross or net; days end yesterday; same spend source.

**Guardrails.** G05 Gross vs net: say which; G03 Partial day / partial window artifacts; G02 Meta spend comes from the Graph API (both ad accounts), never Upstack; G13 Frozen, first-row-only and shifted columns; pull the sheet before writing

**Supporting item ids** (latest 15 of 26; all in `why_candidates.json`): S_secondary-174, S_secondary-175, T_06-65, T_06-72, T_06-74, T_06-76, T_09-24, T_08-64, T_18-64, T_20-63, T_20-64, S_secondary-299, T_26-7, T_26-46, L2-242

### 13. Checkout and landing conversion (checkout start to order, click to NC order)

Items: 11 core, 25 linked; first asked 2026-09-29, last asked 2026-10-29. Decision weight H. In DB: N (CheckoutChamp order_details, Klaviyo checkout events, Shopify; the DB has clicks and NC orders only at daily/ad level). Build effort: L.

**Decomposition.** NC per click = clicks to checkout starts x starts to attempts x attempts to landing completes x upsell accepts. Split by source (Meta in-app vs other browsers), landing domain (lonvera.com LP orders land on Shopify "web", shop.lonvera.com on CheckoutChamp), card vs PayPal, product.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Are we losing people in the checkout stage? Data: checkout start to NC order, before and after. In DB: no. Source or view: CheckoutChamp + Klaviyo. Josh: "So we are losing people in the checkout stage?" (L2-210, 2026-10-26) ; "conversion from clicks to purchased" (T_17-8, 2026-09-29)
2. If the rate went back, what would CPA be? ($110 vs $85) Data: rate x CPA. In DB: no. Source or view: CPA from orders_daily. Josh: "Assuming my CPA is currently $110, if the checkout rate went back to what it was... what would my CPA drop to?" (L2-218, 2026-10-27)
3. Intent problem or checkout page? Split by traffic source and landing domain Data: source x domain table. In DB: no. Source or view: CheckoutChamp. Josh: "If nothing changed on checkout... do you think this is a buyer intent problem... or something in checkout is all of a su..." (L2-219, 2026-10-27) ; "Do you think this is a intent problem such that the intent of the people reaching checkout is lesser, or something is up..." (L2-223, 2026-10-27)
4. Did completion decline start-to-complete; did we swap modules? Data: landing completion %. In DB: no. Source or view: CheckoutChamp. Josh: "so it this saying there was not a decline in checkout start to checkout cpomplete?" (L2-229, 2026-10-27) ; "check all checkout modules, we swapped the checkout modules" (L2-230, 2026-10-27)
5. US vs outside US decline Data: country. In DB: no. Source or view: CheckoutChamp. Josh: "Can you show me decline by inside US vs outside the US?" (L2-221, 2026-10-27)
6. Stat sig before changing page variants Data: variant counts. In DB: no. Source or view: CheckoutChamp A/B. Josh: "Stat sig on this? I would only change it if its an overwhelming winner from A to B, and this is not looking like it will..." (L2-2, 2026-10-21)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Not a checkout fault: CheckoutChamp landing completion 42.4% to 40.5%, flat on every page, card and PayPal split | verified | 2026-10-27 | L2-217, L2-229 |
| A third of the 0.330 to 0.238 fall was lonvera.com LP orders moving to Shopify "web" (65 to about 195 per week); real fall 0.332 to 0.261 (-19%) | verified (measurement artifact removed) | 2026-10-27 | L2-217, L2-223 |
| Real -19%: checkout starts -15% (Meta in-app starts 318 to 254 per day, other browsers flat) plus upsell accepts 10.0% to 5.6% | verified counts; cause consistent-with lower-intent Meta traffic | 2026-10-27 | L2-223, L2-219 |
| Express-PayPal completions per start halved Oct 5-6 | withdrawn (Josh: not true) | 2026-10-27 | L2-227 |
| Sep 21 8am change and the Aug 27-Sep 14 checkout A/B | open (unanswered) | 2026-10-04 | S_secondary-90 |
| LP/PDP variant B vs A: Oct 21 A ahead; Oct 29 B 36.8% vs A 20.9% (z about 3.75) | answered | 2026-10-29 | L2-2, L2-243 |

**Controls rule 13 needs.** No non-Meta control exists (Meta is 92-97% of attempts); use ad-set age cohorts and NC per start by product; validate numerator and denominator sources before reading a rate.

**Guardrails.** G21 Checkout rates: numerator and denominator system; G07 Rates with denominators, never counts; G23 Sample size and significance; G03 Partial day / partial window artifacts

**Supporting item ids** (latest 15 of 25; all in `why_candidates.json`): L2-209, L2-210, L2-217, L2-218, L2-219, L2-221, L2-222, L2-223, L2-226, L2-227, L2-229, L2-230, L2-234, L2-235, L2-243

### 14. Test-to-scale rate (do tested ads graduate?)

Items: 5 core, 21 linked; first asked 2026-07-16, last asked 2026-11-09. Decision weight M. In DB: Y in the app (creative_stage_moves view and /promotions page); not in analytics views. Build effort: S.

**Decomposition.** Graduation rate = testers that moved to scaling / testers launched in the window; median days to move; performance after move vs in testing; exclude bulk-move days.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Which ads moved from testing to scaling and how do they perform? Data: stage moves. In DB: partial. Source or view: creative_stage_moves. Josh: "ok... push this testing vs scaling to the projections doc on the DoD, WoW, MoM tabs on rollups" (L2-204, 2026-10-26) ; "Can you flag all ads that have been in testing and laucnhed into scaling in the last 30 days?" (L2-207, 2026-10-26)
2. Does more scaling spend blend the account better than testing? (testing share) Data: spend by stage. In DB: yes. Source or view: meta_campaign_daily (campaign name contains test). Josh: "and that spend would be net better than testing." (T_26-69, 2026-10-18) ; "say in less words. So testing is doing worse.. but if scaling had more spend... would it blend the overall a bit better?" (T_26-71, 2026-10-18)
3. How many were actually duplicated to scaling and is it too early to read? Data: count, age. In DB: partial. Source or view: creative_stage_moves. Josh: "I also want a view that shows like ads that have launched and if they have scaled. So like a view where I can see ads th..." (L2-314, 2026-11-04) ; "On test to scale... can you build two very simple views that is "what should be scaled" and "Performance In Scaling"" (L2-325, 2026-11-09)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Oct 5-Nov 3: 352 moves (267 on bulk days Oct 22-23); 154 of 924 testers reached scaling (16.7%; 5.1% excluding bulk); median 20 days; testing ROAS 0.53 vs scaling since move 0.67 | verified (345 of 352 too early for held-up verdict) | 2026-11-03 | L2-314, L2-325 |
| Testing campaigns are the biggest leak in several windows ($82K at NC ROAS 0.46); more scaling spend helps only if added dollars return above about 0.68 | verified | 2026-10-18 | T_08-35, T_26-69 |
| Corrected count: 51 testing-to-scaling copies reported, 43 true (double count) | corrected | 2026-10-20 | T_23-21, T_23-22 |

**Controls rule 13 needs.** Exclude bulk move days; match age; do not read held-up verdict before 7 days.

**Guardrails.** G12 Launch volume comes from Meta ad-level first-spend, deduped; never the tracker; never a stale number; G25 Survivorship and self-selected spend

**Supporting item ids** (latest 15 of 21; all in `why_candidates.json`): T_17-34, T_26-67, T_26-69, T_26-71, T_26-72, T_26-76, T_26-78, T_24-85, T_23-21, T_23-22, T_23-23, L2-204, L2-207, L2-314, L2-325

### 15. Country mix and international (US vs intl)

Items: 13 core, 20 linked; first asked 2026-08-25, last asked 2026-10-25. Decision weight M. In DB: P (campaign names carry US / WW / INTL, no country breakdown in analytics). Build effort: M.

**Decomposition.** Blended metric change = within-country rate change + country mix shift (shift-share), per cut: CPM, reach per $1K, CPA, NC share of orders.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Did country of spend change and does it move NC ROAS / CPM? Data: country cut over time. In DB: no. Source or view: Meta breakdown export. Josh: "change in country of spend" (T_17-13, 2026-09-29) ; "product mix, spend mix, creative type, geo, creative launch, etc" (H_2026-08_1-941, 2026-08-25)
2. Does US vs intl change reach, frequency, cost per reach? Data: country cut. In DB: no. Source or view: Meta breakdown. Josh: "for meta, test if international vs US has any impact on reach, frequencey and cost per reeacj" (T_10-28, 2026-09-27)
3. Should I break out US vs international again? Data: market ROAS spread. In DB: partial. Source or view: campaign-name parse. Josh: "Should I go back to breaking out US vs international?" (T_18-14, 2026-09-30)
4. International campaign not spending: ROAS floor, caps, country list Data: budget utilization. In DB: no. Source or view: Graph API. Josh: "I have a dedicated international campaing with them in it" (T_25-46, 2026-10-12) ; intent: Show campaign's country targeting and ask what follows. (T_25-52, 2026-10-13)
5. Did I relaunch the old account and cut international caps too early? Data: spend and NC ROAS after. In DB: partial. Source or view: meta_campaign_daily. Josh: "did I pull the trigger to early on relaunching the old add acount and pulling down cost caps in interntional? Spend will..." (T_25-56, 2026-10-13) ; "only non US...?" (T_24-74, 2026-10-19)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Country mix explains about 0% of the CPM rise; intl is not a relief valve (reach per $1K fell in every country) | verified | 2026-09-27 | T_10-28, T_17-13 |
| AU share doubled while its CPA rose; cheap markets (IE, NZ, MX, ES, IT) went near zero in Sep | verified | 2026-10-12 | T_25-43, T_25-46 |
| NC orders went MORE US (57% to 70%) | verified | 2026-10-26 | L2-196 |
| Non-US-only BackUp test | proposal admitted unsupported after Josh question | 2026-10-19 | T_24-74 |

**Controls rule 13 needs.** Shift-share by country; do not infer cause from the ratio of one country.

**Guardrails.** G08 Mix vs rate: shift-share before naming a cause; G19 Results are not causes

**Supporting item ids** (latest 15 of 20; all in `why_candidates.json`): S_secondary-84, T_18-14, T_17-25, T_17-41, T_25-43, T_25-44, T_25-45, T_25-46, T_25-52, T_25-53, T_25-54, T_25-56, T_24-74, T_26-88, L2-196

### 16. Fulfillment: stuck, late, cancelled or delivered orders; Shopify vs Warehance counts

Items: 12 core, 16 linked; first asked 2026-09-15, last asked 2026-10-15. Decision weight L. In DB: N (Warehance API, UniUni API, Shopify; sheet tabs). Build effort: M.

**Decomposition.** Stuck = shipped but no carrier movement past N days from the CARRIER feed (not Warehance). Units gap Shopify sold vs Warehance shipped = non-US routed to D1 + cancels + timing.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Why do Shopify units sold and Warehance units shipped differ? Data: by product and period. In DB: no. Source or view: Warehance + Shopify. Josh: "If I look at units sold in shopify vs units shipped in warehance for this prpoduct (and others I assume) they are differ..." (T_07-31, 2026-09-16) ; "So for inventory forecasting persective, I jsut care about order shipped. Is thats whats making it to my spreadsheet?" (T_07-32, 2026-09-16)
2. Dig into these orders: stuck or really delivered? Data: tracking status from carrier. In DB: no. Source or view: UniUni public API, USPS. Josh: "Can you dig into these orders for me?" (T_07-52, 2026-09-30) ; "It looks like a lot of these are showing delivered if you click into them. Obviously some are not." (T_07-53, 2026-09-30)
3. Look across Warehance, UniUni API, Shopify for order status Data: status per system. In DB: no. Source or view: three APIs. Josh: "Can you look across all my systems. warehance, the uniuni api, shopufy... and find out the satus of these orders? I dont..." (T_18-65, 2026-10-04) ; "why do you think "Five Create Mastery creators were cancelled and never re-ordered" just two examples" (T_18-68, 2026-10-04)
4. Why does my China 3PL show these cancelled? Data: routing. In DB: no. Source or view: Shopify routing. Josh: "my chine 3pl says they look cancelled on their end" (T_22-24, 2026-10-09)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Warehance tracking feed freezes for UniUni after an early mis-mapped delivered event; 773 of 928 no-movement rows were delivered | verified | 2026-09-30 | S_secondary-103, T_07-53 |
| "42.5% of Shopify orders reaching Warehance are cancelled": 100% of non-US orders auto-cancel in Warehance because Shopify routes them to D1; real US cancel rate 5.9% | verified correction (earlier finding retracted) | 2026-10-04 | S_secondary-102, S_secondary-183, T_18-68 |
| US orders routed through the intl 3PL dropped from about 2,100 per month to 10 in Sep (Revamp go-live) | verified | 2026-09-17 | S_secondary-296 |

**Controls rule 13 needs.** Carrier feed is the truth; a Warehance "cancelled" order with non-US ship-to is not cancelled.

**Guardrails.** G16 Warehance cancelled is not cancelled; shipped, not sold; carrier feed is the truth; G17 Trybe $0 gift orders inflate counts and AOV

**Supporting item ids** (latest 15 of 16; all in `why_candidates.json`): T_07-17, T_07-31, T_07-32, S_secondary-296, S_secondary-103, T_07-52, T_07-53, T_07-54, S_secondary-102, S_secondary-183, T_18-65, T_18-68, T_19-38, T_22-24, T_47-20

### 17. Stock-outs and inventory cover (why ordering so much / short)

Items: 4 core, 11 linked; first asked 2026-09-16, last asked 2026-10-15. Decision weight M. In DB: P (proj_po, proj_stock_snapshot, proj_supply_history in the app DB but excluded from analytics; items show Josh almost never asks "why" here, he asks the model to plan). Build effort: M.

**Decomposition.** Months of cover = on-hand / run-rate demand (shipped units, by warehouse: Revamp, FBA, Intl 3PL) with inbound by landing month; stockout date interpolated in first negative month.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Why does the plan order so much of one SKU? Data: planned PO rows vs run-rate. In DB: partial. Source or view: proj_po. Josh: intent: Understand why the plan has so much Vital Bull landing in Oct-27 / 85,815 closing stock. (T_26-11, 2026-10-14)
2. What is the demand basis: shipped, not sold Data: shipped units. In DB: no. Source or view: Warehance. Josh: "So for inventory forecasting persective, I jsut care about order shipped. Is thats whats making it to my spreadsheet?" (T_07-32, 2026-09-16) ; "If I look at units sold in shopify vs units shipped in warehance for this prpoduct (and others I assume) they are differ..." (T_07-31, 2026-09-16)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Josh planned rows 28 and 40 (50,000 Vital Bull each) were sized on old higher demand; cover rule 1.5-6 months; re-plan saves about $255K on the line | verified | 2026-10-14 | T_26-11, S_secondary-71 |
| US orders left the intl 3PL when Revamp went live (routing change) | verified | 2026-09-17 | S_secondary-296 |

**Controls rule 13 needs.** Use shipped units; run rate window stated.

**Guardrails.** G16 Warehance cancelled is not cancelled; shipped, not sold; carrier feed is the truth

**Supporting item ids** (all of 11; all in `why_candidates.json`): T_07-31, T_07-32, S_secondary-296, S_secondary-102, S_secondary-183, T_18-68, T_22-24, S_secondary-301, S_secondary-71, T_26-11, T_47-20

### 18. Klaviyo (email / SMS) revenue and flow changes

Items: 6 core, 10 linked; first asked 2026-09-30, last asked 2026-10-13. Decision weight M. In DB: N (Klaviyo not in the DB). Build effort: M.

**Decomposition.** Campaign revenue = non-rebill campaign revenue + rebill revenue attributed to campaigns (75-80% of campaign revenue all year was rebills). Flow changes = created / updated dates from the API, split into content edits vs delay / split / status changes.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Exclude active subscribers from campaign revenue to test the agency claim Data: campaign revenue by subscriber status. In DB: no. Source or view: Klaviyo segments. Josh: "is there a way we can look at the subscribers segement and exclude them from email campaign revenue reporting to check t..." (T_18-82, 2026-09-30) ; "Lonvera Klaviyo campaign revenue (2026-09-30): agency (Tomasz Rutkowski, Mailer Profit) excluded active subscribers from..." (S_secondary-83, 2026-09-30)
2. Summary of all flow changes in the last 90 days; are they content edits? Data: flows created/updated. In DB: no. Source or view: Klaviyo API. Josh: "Can you look at the lonvera account and give me a summary of all the changes that have been made to the flows in the las..." (T_22-27, 2026-10-09) ; "ope the flows" (T_22-33, 2026-10-09)
3. Designed vs plain format; flows with no links Data: format key. In DB: no. Source or view: Klaviyo templates. Josh: "where are there flows with like no links in them etc. What percent of flows are plain text vs designed" (T_small-16, 2026-10-13)
4. Who runs the popup Data: tool. In DB: no. Source or view: site scan. Josh: "whos running the pop up on https://lonvera.com/" (T_19-57, 2026-10-06)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Agency claim holds: campaign revenue drop is rebill attribution, not lost non-rebill revenue | verified | 2026-09-30 | S_secondary-83, T_18-82 |
| Agency "sweeps" were almost never email edits; mostly delays, splits, status | verified | 2026-10-09 | T_22-27, T_22-33 |
| Format (designed vs plain) is not driving the decline; plain slightly higher per send | verified | 2026-09-30 | T_small-16 |

**Controls rule 13 needs.** Change log first (rule 13): created / updated dates before analysis.

**Guardrails.** G03 Partial day / partial window artifacts; G10 A timing or decomposition story is not attribution

**Supporting item ids** (all of 10; all in `why_candidates.json`): S_secondary-83, T_18-80, T_18-82, T_19-57, T_22-27, T_22-33, S_secondary-91, T_small-14, T_small-15, T_small-16

### 19. Google, Amazon, refunds, price/offer (small, scattered)

Items: 7 core, 10 linked; first asked 2026-07-07, last asked 2026-10-18. Decision weight L. In DB: N for Amazon and refunds (proj_feed_amazon not exposed); P for Google (analytics.google_campaign_daily, campaign grain only). Build effort: M.

**Decomposition.** Google: spend x ROAS by campaign type with Meta spend as covariate (brand search follows Meta Boneflex spend). Amazon: parked. Refunds: rate = refunds / orders by cohort.

**Ordered child questions (the order Josh actually walked, with his words)**

1. Google vs Meta spend and performance; what used to work on Google? Data: campaign type by month. In DB: partial. Source or view: analytics.google_campaign_daily. Josh: "google vs meta spend / performance" (T_17-10, 2026-09-29) ; "What used to work, that is no loger working ?" (T_17-25, 2026-10-01)
2. Does Google (Upstack) carry NC revenue? Data: Upstack Google attribution from 2025-05-13. In DB: yes. Source or view: google_campaign_daily. Josh: "google use upstack, that should have NC-rev?" (T_05-99, 2026-09-23)

**Known causes from history**

| cause | status | date | item ids |
|---|---|---|---|
| Google brand search still returns 2x+ but volume follows Meta Boneflex spend; Sep budget moved from PMax to weak non-brand Shopping; total $41.5K/$70.3K/1.70 (Apr) to $19.3K/$24.4K/1.26 (Sep) | partial | 2026-09-30 | S_secondary-84, T_17-25 |
| Amazon fell by half Jan to April; fees fell in lockstep with sales | answered (fees are mechanical on sales) | 2026-07-07 | HC_2026-07_1-189 |

**Controls rule 13 needs.** Meta spend as covariate for Google brand.

**Guardrails.** G05 Gross vs net: say which

**Supporting item ids** (all of 10; all in `why_candidates.json`): HC_2026-07_1-189, HC_2026-07_4-203, H_2026-08_1-68, T_05-52, T_05-99, T_17-10, S_secondary-84, T_17-25, T_38-8, T_38-9
