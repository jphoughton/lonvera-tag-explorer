# Marketing metrics: Platform picker, All platforms, platform-level Campaigns

Josh: "i can choose by platform at the highest level", "by campaign by platform... not just by product", "nowhere to click flip to just look at upstack data per creative", "I hit marketing metrics view... it goes to that all view... where i see by platform rolled up... then I also choose to view by campaign... across all platforms".

## Platform = the first control, and the default landing is All

`Platform: All | Meta | Google`, first control of the bar, kept in the URL (`platform=all|meta|google`). It only means something on the platform pages (`/` and `/campaigns`); product and ad pages are Meta.

**The default depends on the path, so old links keep their meaning:** a bare `/` (what the top-bar **Marketing metrics** link and the Lonvera brand link point at, and where a login lands) is the **All platforms roll-up**; `/campaigns` without a platform is still **Meta's** Campaigns. A link to the Meta landing therefore carries `platform=meta`, and `platform=all` is explicit on `/campaigns`. Every other parameter (`window=`, `source=`, `basis=`, `compare=`, ...) is honored on whichever platform page it lands on; a root with parameters but no platform is the roll-up with them applied.

| URL | What it shows |
|---|---|
| `/` (bare, or with window / source / basis ...) | **All platforms**: the platforms rolled up in a table (own reporting, Upstack new customers, Upstack all orders per platform) with the platform trend chart. `View by: Platforms \| Campaigns` |
| `/campaigns?platform=all` | **All campaigns**: every Meta and Google campaign in one table (below), reached from `View by: Campaigns` |
| `/?platform=meta` | Meta: the Products table, tags and creatives (the old landing, one click away from the picker). `View by: Products \| Campaigns` |
| `/campaigns` (= `/campaigns?platform=meta`) | every Meta campaign across all products (below) |
| `/?platform=google` | Google overview: trend, campaign types, campaigns (see MARKETING_METRICS_GOOGLE.md) |
| `/campaigns?platform=google` | Google campaigns first, the campaign types as a filter row |
| `/p/<slug>`, `/ad/<id>` | unchanged (Meta); their breadcrumb starts at **Meta** (back up = the Products table, not the roll-up) |
| `/google` | the first Google URL; redirects to `/?platform=google` |

Switching platform keeps the range, comparison and Numbers-from choice; Stage, Active only and the chart picks (Meta only) are dropped when leaving Meta. On a Campaigns page the switch keeps you on Campaigns (All, Meta and Google each have a Campaigns view). The `View by` switch has the same pattern as the product page's `View by: Tags | Campaigns`: Platforms | Campaigns on All, Products | Campaigns on Meta, Overview | Campaigns on Google. `/api/status` lists the platforms (`sources.platforms`: key, name, available, first_day, through); the Google button is disabled when Google is not loaded.

### Which platforms Upstack serves (checked 2026-11-04)

| Platform | Upstack channel feed (spend) | Upstack attribution (any-click, 30 days) |
|---|---|---|
| Meta (`facebookIntegration`) | from 2024-01-14, 183 campaigns with spend, $13.67M to date | `utm_source` Meta Ads, from 2025-05-13 |
| Google (`googleAdsIntegration`) | from 2024-03-25, 77 campaigns with spend, $634K to date | `utm_source` Google Ads, from 2025-05-13 |
| TikTok (`tiktokAdsIntegration`) | **no rows**: the integration is known to the feed code but returns nothing in 2024-2026 | no TikTok source in the sampled days |
| Bing / Microsoft | not in the channel feed | appears only as an attribution source (`Bing`, 28 orders over 21-day samples since 2025-05); it carries no spend, so it has no row here |

So the picker lists exactly Meta and Google. A platform that starts returning spend and attributed orders is a loader plus one entry in `sources.platforms` away; the page code does not hard-code two platforms beyond the buttons.

## All platforms (`/?platform=all`, `GET /api/platforms/overview`)

Per platform three rows: its own reporting, then Upstack's any-click attribution on **new customers** and on **all orders**. Spend, clicks and impressions are the platform's in every row (Upstack mode only changes what counts as a purchase and its revenue). Meta's numbers are the whole Meta account (tracker products plus ads off the tracker: the Products table's "All spend" row). Meta rows and Google rows are never added up: both credit the same order, so purchases overlap between platforms. Stage, Active only and comparison do not apply (Google has none of them) and are ignored; the page says so. Each row carries the window it actually covers: Upstack answers only for the days it has pulled, so a clamped row shows its date range under its name. The Numbers-from switch is hidden on All because all three bases are on the page.

## Campaigns across products (`/campaigns`, `GET /api/campaigns`)

One row per Meta campaign with the same columns as the product Campaigns view (account, stage, ads, creatives, spend, % of spend, impressions, clicks, CTR, CPC, purchases or new customers, revenue, ROAS, CPA, live, last day, trend) plus **Product**. Window, Stage, Active only, compare and Numbers from (Meta | Upstack, New customers | All orders) behave exactly as on the product pages. Search, "with spend only", column chips and sorting are in the URL like the other tables.

- **Product column.** A campaign whose ads belong to one product shows that product. A campaign that runs ads of several products (a shared All_US / test campaign) reads **Mixed**, with each product's share of the spend under it; each share links to that product's ads in the campaign.
- **Product filter.** `All | <every product that spent in the window> | No product`; it keeps only that product's ads in every row (so the row's numbers are that product's, as in the product Campaigns view except for families shared with another product, see Ties). `product=<slug>` in the URL and the API; `none` = families that belong to no active product. A product literally called `unassigned` exists and is a normal product.
- **Where a row goes.** The campaign name opens that campaign's ads inside the product (`/p/<slug>?view=ads&campaign_id=`), the one product of the row or, for Mixed, the product that spends most; the product links in a Mixed cell open each product's ads in it.
- **Ties.** The Total row equals the Products table's "All spend (Meta account total)" for the same date window, to the cent, in Meta mode, both Upstack modes, with a stage filter and with Active only (asserted in `tests/api/test_api_platforms.py` and `test_api_source.py`; checked on the full local data 2026-11-04: 30d $340,583.67 = raw `ad_daily`). Each Meta family counts once, under the product of its first row across all products (the Products table's rule, `ad_metrics.is_first_row`), so the rows add up to the account. **The per-product Campaigns views do not follow that rule**: they count a family that sits under two products for both, so on today's data 18 shared families ($7,129.91 over 30 days, 2.1%) make those views add up to $347,713.58 and put $7,060 of campaign spend under the product `unassigned` that the Products table shows as $0. The platform view and its Product filter therefore differ from a product's own Campaigns view by exactly those families; the product views were left unchanged. Since launch has no meaning here (as in the product view): it shows every day held, and the page says so; the tie holds for date windows.
- **Google.** Google has one page for both views, so its Campaigns view is the same data with the campaigns table first and the campaign types as the grouping filter.

## The Numbers-from switch is where the explanation is

On a 4K window the switch sat at the far left of a 2,000 px bar and the explanation row and tie-out button at the far right. Now:

- the **explanation row carries its own `Numbers from: [Meta] [Upstack]` (and `Count: [New customers] [All orders]`)** right before the tie-out button (`#source-ctl-inline`; the bar's own control is still there, `#source-ctl`; both read and write the URL, so they are one state in two places);
- the words **"Switch to Upstack" in the note are a button**; on the Upstack side the same place offers "Switch to Meta's own numbers" (or Google's);
- **the bar and the page are capped at 1,600 px and centered**, so left and right controls are never more than 1,600 px apart (checked at 2,100, 1,024 and 390 px, no horizontal page scroll);
- **every table head says which numbers it shows**: a `Numbers: Upstack · new customers` badge in the Columns row (Tags tables, All creatives, the product Ads and Campaigns views, the ad page's Ads table, Google, the new Campaigns view);
- the bar and the note exist on every page, so the product Creatives views and the ad page carry the same switch.

## Trend charts (Campaigns, All platforms, All campaigns)

The same panel as the product page (`createTrendPanel`, mode `source`): Bars and Line selectors (any metric), Day / Week / Month grain, Swap, Hide, compare overlay where comparison exists, the same Numbers-from and Count basis as the table underneath, state in the URL (`tb`, `tl`, `tg`, `ts`).

| Page | Endpoint | Bars | Split bars by | Line |
|---|---|---|---|---|
| Meta Campaigns (`/campaigns`) | `GET /api/campaigns/trend` | any metric (default Spend) | None / Stage / Product | any metric (default ROAS; NC ROAS on the new-customer basis) |
| All platforms (`/?platform=all`) | `GET /api/platforms/trend?numbers=own\|nc\|all` | Spend, Impressions, Link clicks (what may be added across platforms) | Platform | **NC ROAS (total)** by default; ROAS gives one line per platform plus the total beside them |
| All campaigns (`/campaigns?platform=all`) | `GET /api/platforms/trend` | same | Platform | same |

- **Ties.** The chart's points are the table's rows summed: `campaigns_trend` reads the same `ad_daily` rows, product assignment (`_pfa`), stage and active filters as `campaigns_all`; the platform trend reads the same windows and queries as the overview / All campaigns table (Google through `google.trend`, now with a month grain). Asserted for every window, stage, grain and split in `tests/api/test_api_platforms.py`, `test_api_google.py`, `test_api_source.py`; checked on the full local data (30d: $340,583.67, 3,472 purchases, in every split and grain; Upstack new customers 2,614, all orders 4,189).
- **Campaigns page.** The Product filter narrows the chart; the table's **Chart** button narrows the chart to one campaign (`chart=<campaign id>`, shown as "Chart shows only X, Show all campaigns"); the table stays whole. Comparison draws window B as the dashed overlay.
- **All platforms.** The overview table shows every basis at once, so the chart has its own `Numbers` choice (Own reporting | Upstack new customers | Upstack all orders, `nb` in the URL). Ratio bars are not offered (a ROAS bar across platforms would be an added ratio); ROAS is a line per platform.
- **Upstack depth.** Upstack attribution starts 2026-05-06 for Meta and 2025-05-13 for Google (the platforms' own feeds: Meta daily data 2026-05-06, Google 2024-03-25). On the Upstack bases each series is drawn only where it exists and the note under the chart names where each starts.
- **Since launch on the All views** means the days every platform has data (today May 6, 2026 to Nov 2, 2026), never each platform's own history: Google's own reporting alone would otherwise cover 2.5 years against Meta's six months. The header note says so. The overview, the All campaigns table and the chart all use it. Trailing windows (7d, 30d ...) end on each platform's own last day, so Meta and Google can differ by a day (Meta Nov 2, Google Nov 3); the page lists the days each platform covers when they differ.

## All campaigns (`/campaigns?platform=all`, `GET /api/campaigns/all`)

Every Meta and Google campaign in one table, reachable from the landing page (`View by: Campaigns`), from the Platform picker and by URL; the picker keeps you on Campaigns when you change platform.

- **Columns.** Campaign, a sparkline (Meta rows), **Platform**, **Product / type** (a Meta product or Mixed with shares; a Google campaign type), **Numbers** (the basis that row is counted on: "Meta's own reporting", "Google's own reporting", "Upstack · new customers", "Upstack · all orders"), account, stage, ads, then the usual metrics with neutral headers (Purchases / orders, Cost per purchase) because the two platforms' counts mean different things.
- **Numbers from + Count** apply per row: own reporting means each platform's own (Meta's for Meta rows, Google's for Google rows), Upstack reads every row on Upstack's attribution. Spend, impressions and clicks are the platform's in every basis.
- **Filters.** Platform (`only=meta|google`), Meta product (`product=<slug>`), Google campaign type (`type=`); picking one clears the others (Google has no products, Meta has no types). Search, "with spend only", columns chips and sort (by spend by default) work as elsewhere.
- **Totals.** With both platforms in view the Total row adds **spend, impressions and clicks only**, followed by a subtotal row per platform with the full metrics. **Purchases, revenue, ROAS and CPA are never added across platforms, in either basis:** under own reporting each platform credits the orders it touched, and under Upstack's any-click attribution an order is credited to every ad or campaign clicked in the 30 days before it, so one order can sit on a Meta ad and on a Google campaign. (Order-level overlap cannot be measured from the Upstack API, so the size of the double credit is unknown; the rule does not depend on it.) With one platform in view the Total row is that platform's full total, and **the Meta-only total equals the Meta Campaigns page total** (30d on the full local data: $340,583.67, 3,472 purchases, $240,864.76 revenue; asserted in `test_api_google.py`).
- **Today's table, 30d (Oct 4 - Nov 2 Meta, Oct 5 - Nov 3 Google):** 63 rows (30 Meta, 33 Google), spend $358,157.10 = Meta $340,583.67 + Google $17,573.43; Upstack new customers 66 rows, all orders 74 rows (Meta rows with Upstack credit); since launch 142 rows (77 + 65).

## Total spend and Total NC ROAS (All platforms and All campaigns)

Meta and Google are the only spend platforms, so a **totals strip** sits above the chart on both All views: **Total spend**, **Total NC revenue**, **Total NC ROAS** for the selected window (and window B under each tile when Compare is on; comparison shows in the strip only). `GET /api/platforms/kpis` (same window and `only` / `product` / `type` filters as the table and chart).

- **Total spend** = Meta spend + Google spend over the window, each platform over its own days (so it equals the All campaigns table's Total row, to the cent; Meta and Google shares underneath).
- **Total NC revenue = the business's actual new-customer revenue, counted once**: Upstack's order-level new-customer revenue per Pacific day (`upstack_orders_daily`, migration 012; the same source and day buckets as the pacing "NC Rev"; validated equal to the pacing sheet's `Orders - Daily (NC-RC Rev)` for all 30 days Oct 5 to Nov 3, $224,242.41). It is **not** the sum of Meta-attributed plus Google-attributed new-customer revenue: both platforms credit the same orders (overlap) and some orders are credited to neither (unattributed), so that sum is the wrong numerator.
- **Total NC ROAS = Total NC revenue / spend of the same days.** A ratio of sums over the days Meta, Google and the orders feed all cover; a day without orders is **blank, never zero**, and the strip says "N of M days". The chart's Day / Week / Month buckets are ratios of sums too (never a mean of daily ratios) and weigh back to the strip.
- **The platforms' attributed sum is shown beside the actual**: "Platforms attribute $X (Meta + Google) · gap" where gap = attributed sum minus actual = overlap net of unattributed (Upstack any-click new-customer basis, same days).
- **Chart.** Total spend is the stack: the sum is written above each bar when there are 16 bars or fewer (Week / Month), and every hover lists Meta spend, Google spend, Total spend and Total NC ROAS of the bucket. **NC ROAS (total)** is a Line choice (a dark line, a line only: it cannot be swapped into Bars) and the **default Line** on both pages; with Line = ROAS the per-platform lines are drawn and the dark total line sits beside them. If orders do not reach back to the chart's first day the note says from when it exists and the earlier days are blank.
- **Filters hide the ratio.** With a Platform, Product or Campaign type filter the tile, the NC revenue tile and the line are hidden and one sentence says why (Upstack orders have no product or platform dimension, so new-customer revenue cannot be split); Total spend stays and follows the filter (ties to the filtered table). Default Line is ROAS again then.

Today (full local data, orders from 2025-01-01 to 2026-11-03). Meta's last day is Nov 2 and Google's Nov 3, so the ratio uses the days all three cover:

| Window | Total spend (whole window) | Total NC revenue (actual) | Spend of the covered days | Total NC ROAS | Platforms attribute | Gap |
|---|---|---|---|---|---|---|
| 30d (Oct 4 - Nov 3; ratio over Oct 5 - Nov 2, 29 of 31 days) | $358,157.10 = Meta $340,583.67 (95.1%) + Google $17,573.43 (4.9%) | $218,437.52 | $342,431.72 | **0.64x** (0.6379) | $187,583.83 (Meta $172,883.45 + Google $14,700.38) | -$30,853.69 (-14.1%) |
| Since launch (May 6 - Nov 2, 181 days) | $4,175,701.81 | $3,001,173.77 | $4,175,701.81 | **0.72x** (0.7187) | $2,676,786.55 | -$324,387.22 (-10.8%) |

Both gaps are negative: the platforms together credit less new-customer revenue than the business actually had, so unattributed orders outweigh the overlap between the platforms.

Ties asserted in `tests/api/test_api_platform_totals.py` (strip = table total row = chart total = Meta + Google from the two independent trend endpoints; Total NC ROAS recomputed from the raw orders; attributed sum and gap; every grain a ratio of sums; blank-not-zero; the filters; compare; since launch; no orders loaded) and in `tests/ui/test_ui.py` (strip text and `data-value` against the chart and the table total, the default Line, the tooltip lines, filters, compare, 375 / 1,024 / 2,100 px with no page-level sideways scroll).

The orders feed is `etl/upstack_orders.py` (Upstack `/api/query`, `orders.*` measures, Pacific days, 60-day chunks; refuses an empty answer) run by `python -m etl.upstack_daily` with the Meta and Google sides (`--orders-since` to backfill, `--orders-only`, `--no-orders`); the nightly etl cron restates the trailing days.

## Not done

- Product filter chips list products with spend in the window only; a product with no spend is not offered.
- Comparison (window B) shows in the totals strip of the All views only (the page says so); the All charts and tables show window A. The Meta Campaigns chart does draw it.
- Clicking a row narrows the chart on the Meta Campaigns page only; the All campaigns chart follows the Platform / Product / Type filters.
