# Trends (`/trends`)

One page, one toggle: **Day | Week | Month** (DoD / WoW / MoM) of the key marketing metrics, laid out like the rollup tabs of Josh's Google Sheet (see "What the sheet looks like"): periods as rows, oldest first and the open period last, labelled bands of columns, the change against the previous period as one block at the right. Code: `app/trends.py` (payload and layout), `app/static/js/pages/trends.js` (page), API `GET /api/trends` and `GET /api/trends/plan` (month grain).

## Parameters

`interval=day|week|month` (default week) · `periods=<n>` (default 14 / 12 / 12, max 120 / 104 / 36) · `numbers=own|nc|all` (what Meta's and Google's purchases / revenue / ROAS columns count, same switch as the All platforms pages) · `as_of=YYYY-MM-DD` (pretend "today"; tests only) · page only: `groups=plan,totals,meta,google,products,stage,change` or `all` (the bands shown; default = DTC / Shopify + Change, and Plan on the month grain; the old `vitabull` / `beefliver` / `boneflex` values mean By product), `delta=1` (a Δ column after every metric), `line=<metric>` (chart line).
`GET /api/trends/plan?from=YYYY-MM-DD&to=YYYY-MM-DD` (month grain) answers the plan block (below); it never fails the page: on an engine problem it answers `available: false` with the reason.

## What the sheet looks like (Lonvera Projections, read 2026-11-09)

Tabs `Pacing - Daily (180d)`, `Rollup - Weekly (WoW)`, `Rollup - Monthly (MoM)` (also `Month Snapshot`, a one-month view, not mirrored). All three share one structure:

* **Rows are periods, oldest first, the current (partial) period last.** Daily: one row per day (`Wed 1-Jan-25` ...). Weekly: ISO week, from 2025-W01, one row added per new week; the last row is the partial week (it showed W46 with zeros before any data). Monthly: 2025-01 onward, the current month last.
* **Row 1** is a caption, **row 2 the band titles** (white bold text on a dark fill, one title per band), **row 3 machine keys** (e.g. `meta_nc_roas`), **row 4 the column labels** (light tint of the band colour, each with a one-line source/definition note under the label). Frozen: 4 header rows (daily and weekly; monthly none) and the first 4 columns (daily) / 5 columns (weekly, monthly: key, start, end, days, partial). No column groups except the five projection groups on the monthly tab.
* **Bands, left to right** (weekly; the daily tab is the same without the period-end/days/partial columns and without the change block): `BUSINESS-WIDE` (Amazon-inclusive totals: Total Revenue, Total Ad Spend, MER, NC Revenue, Repeat Revenue, NC Orders, NC CPA, NC ROAS, NC AOV, NC Conversion, Repeat Share %) · `DTC / SHOPIFY` (Total Revenue, NC Revenue, Repeat Revenue, Total Orders, NC Orders, Conversion Rate Overall, AOV, NC AOV, DTC Ad Spend, NC CPA, NC ROAS, MER, NC Conversion, Repeat Share %, New Visitors, Cost per New Visitor, Cost per Unique Outbound Click) · `META` (Spend, Link Clicks, CPC, Purchases, Purchase Value, CVR, AOV, NC Orders, NC Rev, NC ROAS, NC CPA, NC AOV, Platform ROAS, Impressions, Reach, First-Time Impression %, Cost per 1,000 Reached, CPM, Frequency, Unique Outbound Clicks, Unique Outbound CTR, Avg Cost Cap, Avg ROAS Floor, % Spend on Capped Ad Sets / Cost Cap / tROAS Floor) · nine **per-product metric bands**, each `VitaBull | Beef Liver | Boneflex | Total`: `SPEND`, `NC REVENUE`, `NC ROAS`, `NC ORDERS`, `NC CPA`, `UNIQUE OUTBOUND CLICKS`, `CONV RATE`, `NC REV / UNIQUE OUTBOUND CLICK`, `COST / UNIQUE OUTBOUND CLICK` · `SPEND BY STAGE` (Scaling, Testing, Total) · `TESTING % OF SPEND` · `NC (UPSTACK ANY-CLICK)` (Scaling, Testing, Total) · `NC CPA BY STAGE` (Scaling, Testing, Total, Testing CPA vs Scaling) · `GOOGLE` (Spend, Clicks, CPC, Purchases, Purchase Value, CVR, AOV, NC Orders / Rev / ROAS / CPA / AOV, Platform ROAS) · `AMAZON` (ad spend, revenue, orders, units, TACOS ...) · `EBITDA (LIVE P&L)` (NOI / EBITDA target and estimate, gaps) · `WoW Δ` / `MoM Δ`.
* **Change** is not shown next to each metric. It is one block at the far right, `WoW Δ` (weekly) / `MoM Δ` (monthly): the % change vs the row above (the prior period) for 14 headline metrics: Total Revenue, Total Spend, NC Revenue, Repeat Revenue, NC ROAS, NC CPA, NC Orders, DTC NC ROAS, Meta NC ROAS, Google NC ROAS, Amazon Revenue, Amazon NC ROAS, EBITDA Est., Gap vs Target (monthly: the same 14). Format `0.0%` (monthly `+0.0%;-0.0%`), cells filled light green when above 0 and light red when below 0 (by sign only: a CPA increase also shows green). The Daily tab has no change block. The change is a plain row-above comparison: the open period is compared with the full previous one (the app's like-for-like rule is stricter and kept).
* **Monthly tab only: a leading `Projected* | Target | % Diff` block** (columns F to V, each triple a collapsible column group): Total Rev, Total Spend, NC Rev, EBITDA, NOI, then two dollar-gap columns (EBITDA, NOI) headed `Gap vs Target`. `Projected*` is the month-to-date run-rate for the running month (the asterisk) and the actual for a closed one; `Target` is the P&L goal; `% Diff` is `+0.0%` coloured green/red by sign; the gaps are `+$#,##0;-$#,##0`.
* **Formats:** money `$#,##0` (per-unit money `$#,##0.00`), counts `#,##0`, ratios `0.00"x"` (ROAS, MER), rates `0.00%` (testing share `0.0%`), frequency `0.00`, dates `mmm d, yyyy`. Band colours (title / head tint): Business-wide navy `#1b294a` / `#c3c7ce`, DTC teal `#0d7b7a` / `#c0dcdc`, Meta blue `#1777f1` / `#c1dbfa`, per-metric product bands alternate purple `#4a3375` and slate `#2e596b` with head tints VitaBull `#dcc4e6`, Beef Liver `#eec1d4`, Boneflex `#dcd1ce`, Total `#d1d9dc`, Google green `#33a753` / `#cae7d1`, Amazon orange `#ff9900` / `#ffe4bb`, EBITDA dark red `#6b1313` / `#d8c1c1`, change block grey `#4a4a4a` / `#cecece`, plan block `#2b3d4f` / `#eef1f6`.

## How the page mirrors it

One header row of band titles, one of column labels, the period column and both header rows frozen, periods as rows oldest first (the table scrolls to its last row, the newest, on load), the sheet's bands in the sheet's order for everything the app has data for, the sheet's labels, number formats and per-product head tints. Band fills are the sheet's, darkened where white text on the original fell under 4.5:1 (Meta `#1259b8`, Google `#1e7a3a`; Amazon orange is not used). All text is 12 px or larger.

| Sheet band | On the page |
|---|---|
| DTC / SHOPIFY | `DTC / SHOPIFY` chip: Total Revenue, NC Revenue, Repeat Revenue, NC Orders, NC AOV, Ad Spend (Meta + Google), NC CPA, NC ROAS, MER, NC Conversion, Repeat Share %, New Visitors, Cost per New Visitor |
| META | `Meta` chip: Spend, Link Clicks, CPC, Purchases, Purchase Value (Platform basis; follows the Numbers-from switch), CVR, Platform ROAS, Cost per Purchase, Impressions, Reach, First-Time Impression %, Cost per 1,000 Reached, CPM, Frequency, Unique Outbound Clicks, Unique Outbound CTR, Link CTR |
| per-product bands | `By product` chip: SPEND, NC REVENUE, NC ROAS, NC ORDERS, NC CPA, each VitaBull / Beef Liver / Boneflex / Total |
| SPEND BY STAGE, TESTING %, NC (ANY-CLICK), NC CPA BY STAGE | `Testing vs scaling` chip: Scaling / Testing columns (no Total columns, no 'Testing CPA vs Scaling') |
| GOOGLE | `Google` chip |
| WoW / MoM Δ | `Change` chip: the nine headline changes the app can supply (Total Revenue, Total Spend, NC Revenue, Repeat Revenue, NC ROAS, NC CPA, New Customers, Meta ROAS, Google ROAS), signed `0.0%`, tinted green/red **by polarity** (CPA down and ROAS up are good, spend is neutral; the sheet colours by sign only), on Day too (the sheet's Daily tab has none) |
| Projected* / Target / % Diff (monthly) | `Plan` chip, month grain only, see below |

Switch **Change after every column** (off by default) adds a `Δ` column after each metric, as the page used to show it (▲/▼, percentage points for rates, same colours); the Change block stays.

Two columns are new, both the sheet's own definition: the Total column of the per-product NC ROAS and NC CPA bands divide actual NC revenue / Meta spend (`meta_nc_roas`) and Meta spend / actual new customers (`meta_nc_cpa`) by **Meta spend alone**, as the sheet does (the DTC band's NC ROAS / NC CPA use Meta + Google). Also new: per-product NC Orders (`p_<product>_nc_orders`, the same new-customer counts the product NC CPA already used). No existing number changed: `tests/test_trends_golden.py` pins every value and change of the old payload.

## Plan block (month grain)

`Projected* | Target | % Diff` for Total Rev, Total Spend, NC Rev, EBITDA, NOI plus the EBITDA and NOI dollar gaps, read from `/api/trends/plan`, which calls the Pacing page's own `app.projection.pacing.views.plan_block` through `rollup_payload` (same engine run, same plan of record, same `Projected*` rule: month-to-date over the days with data times the days in the month for a running month, the actual for a closed one). Gap = projected minus target. These figures are **business-wide** (they include Amazon, as the sheet's do), so they will not equal the DTC-only columns beside them. Polarity: revenue, NC revenue, EBITDA and NOI above target are good; ad spend above target is bad (the Pacing page's direction for advertising). On the day and week views there is no plan block: the sheet has none there, and the plan is monthly.


## Definitions

* **Days** are Pacific days (the date as stored; the 25-hour day on Nov 1 2026 is one day). **Weeks** are ISO weeks, Monday to Sunday, labelled `W45` + date range (`Dec 29, 2025 – Jan 4, 2026` is ISO W01 of 2026). **Months** are calendar months.
* **Every ratio is a ratio of the period's sums**, never an average of daily ratios (ROAS, CPA, CPM, CTR, CPC, conversion, MER, AOV, shares). Spend adds across platforms; purchases and revenue never do (Meta and Google credit the same orders).
* **Total NC ROAS** = actual new-customer revenue (`upstack_orders_daily.nc_rev`) / Meta + Google spend of the same period (as `docs/PLATFORMS.md`). **NC CPA** = total spend / actual new customers (`upstack_orders_daily.nc_orders`). **MER** = total revenue / total spend (no Amazon revenue, no Amazon / TikTok spend). Revenue is gross (tax in, refunds not out).
* **Meta** spend, impressions, link clicks, purchases, revenue: `ad_daily` (both ad accounts, each in its own day, as every other page). Days before `ad_daily` begins (2026-05-06) come from the sheet-fed campaign history (`proj_feed_meta_day`, source `sheet`; the same rule as `analytics.meta_campaign_daily_history`), Meta's own reporting only. Upstack-basis Meta numbers start 2026-05-06; Google's start 2025-05-13.
* **Reach, frequency, first-time impression %, unique outbound clicks**: read from the feed's own day / week / month rows (`proj_feed_meta_reach`), never summed over days (Meta de-duplicates per period). Cost / 1,000 reached = Meta spend / reach, closed periods only.
* **Per product** (VitaBull, Beef Liver, Boneflex): Meta spend (`proj_feed_meta_product`: campaign name, then ad set name, then ad name), actual NC revenue and new customers (`proj_feed_nc_product`), NC ROAS and NC CPA from those.
* **Testing vs scaling**: Meta spend by campaign stage (campaign name contains `test` = testing; `meta_campaigns.stage`), testing share of spend; new customers by stage are Upstack's any-click credits (`proj_feed_anyclick`, not additive to actual new customers) and the NC CPA by stage is spend over those.

## Like for like (the open period)

A period is **open** when it ends after the last loaded day (the newest of Meta, Google and the orders feed, never today: a day is loaded after it ends). It is flagged **so far** and compared with the **same elapsed days of the previous period**: month to date against the first N days of last month, week to date against the same weekdays of last week. If more days have elapsed than the previous month has (March 30 against February), there is no base and the change is a dash with the reason. Reach has no like-for-like change while its period is open (Meta cannot cut a de-duplicated reach to the same days). Closed periods are compared with the whole previous period (note that months differ in length).

**Today** has no numbers in the database (sources load once a day; `proj_feed_nc_hourly` never stores today), so in the Day view today is a "not loaded yet" row and its change is not shown rather than a misleading -70%. Today's intraday NC revenue is a typed input on Projections > Pacing.

## Dashes

A metric is shown only when every source it needs covers **every** day of the period: otherwise a dash with the reason on hover (period starts before the source, source loaded only through a date, a gap such as Meta's missing 2025-03-04 to 2025-03-13). A zero is a real zero only where the source has a day without rows by nature (a day without orders is absent in the orders feed and counts as 0). The "Data through" chips show each source's newest day and turn red when it is two or more days behind yesterday.

## Performance

About 18 queries, each a `GROUP BY day` over an indexed daily table (or a min / max), run in one request; everything else is arithmetic on a per-day dictionary in Python. Measured uncached on production through the public DB proxy: 0.3 to 0.6 s for every interval and range (the figures are with the response cache off). `statement_timeout` is 8 s for the endpoint; a timeout answers `503 {"error": "query timed out"}` and the page shows its error box with Retry.

## Not built (what the sheet has and the app does not)

* Bands the app has no data for: BUSINESS-WIDE and AMAZON (no Amazon sales or ad data here), EBITDA (LIVE P&L) (the Plan block gives EBITDA and NOI against target on the month grain only), TikTok spend (the Ad Spend column is Meta + Google), Total Orders / AOV / overall conversion rate, Avg Cost Cap / Avg ROAS Floor and the capped-spend shares (`proj_feed_bids`), Meta / Google AOV and the last-click NC columns of Meta and Google.
* Per-product Unique Outbound Clicks, Conv Rate, NC Rev per click and Cost per click bands (`proj_feed_product_reach` only holds unique outbound clicks from 2026-07-05).
* The Total columns and `Testing CPA vs Scaling` of the stage bands (testing + scaling does not always add up to Meta spend: a campaign without a stage is in neither).
* Per-metric collapsible column groups (the sheet only has them on the plan block); the chips show or hide whole bands instead.
* The sheet's EBITDA / NOI change columns, Amazon changes, and its row-above comparison for an open period (the app compares like for like).
* Intraday like-for-like for today (no hourly data for today is stored).

## New Visitors and Cost per New Visitor (added 2026-11-09)

The sheet puts both at the end of its DTC / SHOPIFY band (after Repeat Share %, before Cost per Unique Outbound Click); the page does the same, in the `DTC / SHOPIFY` chip, and both are in the change block (after NC Orders).

* **New Visitors** = Upstack `tracked.new_visitors` (first-time visitors, all sources, pixel-tracked), the app's `proj_feed_traffic` feed (Pacific days, from 2025-05-08, the last 14 days rewritten daily). A period's value is the SUM of its days (the sheet does the same; a person who clears cookies or switches device counts again, so it is a first-visit count, not a de-duplicated head count). The feed's row for today is partial and never read, like every other source here.
* **Cost per New Visitor** = total ad spend of the period (Meta + Google, the same figure as the Ad Spend column) / new visitors of the period: a ratio of sums, `$0.00`. The sheet's and the Pacing page's `DTC ad spend` also include TikTok spend (about 0.5% of Meta), so their cost is marginally higher than this page's; the other difference is that the sheet's open period includes today's partial visitors.
* Dashes with a reason, never zero: a period that starts before 2025-05-08, one that holds a day the feed lacks, or a feed that is not loaded through the period. A day with a real 0 visitors is shown as 0 and its cost as a dash (undefined).
* Polarity: more new visitors is good (green when up), a higher cost per new visitor is bad. The open period is compared like for like (same elapsed days of the previous period).
