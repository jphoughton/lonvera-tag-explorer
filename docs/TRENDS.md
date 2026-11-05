# Trends (`/trends`)

One page, one toggle: **Day | Week | Month** (DoD / WoW / MoM) of the key marketing metrics, newest period first, every value followed by its change against the previous period. Code: `app/trends.py` (payload), `app/static/js/pages/trends.js` (page), API `GET /api/trends`.

## Parameters

`interval=day|week|month` (default week) · `periods=<n>` (default 14 / 12 / 12, max 120 / 104 / 36) · `numbers=own|nc|all` (what Meta's and Google's purchases / revenue / ROAS columns count, same switch as the All platforms pages) · `as_of=YYYY-MM-DD` (pretend "today"; tests only) · page only: `groups=totals,meta,...`, `line=<metric>` (chart line).

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

* Projected (end of month)* and Target / % Diff columns and the EBITDA / NOI gap (needs the projection module's targets; not straightforward and exact for an open period at every grain).
* Amazon, TikTok and Klaviyo blocks, Business-wide (Amazon-inclusive) totals, Total ad spend with Amazon / TikTok.
* New visitors and cost per new visitor (`proj_feed_traffic` exists; not wired), average cost cap / ROAS floor and capped-spend % (`proj_feed_bids`), Meta / Google AOV and last-click NC AOV.
* Per-product unique outbound clicks, conversion rate, NC revenue per click and cost per click (`proj_feed_product_reach` only holds unique outbound clicks from 2026-07-05).
* Intraday like-for-like for today (no hourly data for today is stored).
