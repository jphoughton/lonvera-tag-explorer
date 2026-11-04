# Marketing metrics: Platform picker, All platforms, platform-level Campaigns

Josh: "i can choose by platform at the highest level", "by campaign by platform... not just by product", "nowhere to click flip to just look at upstack data per creative".

## Platform = the first control

`Platform: All | Meta | Google`, first control of the bar, kept in the URL (`platform=all|google`; absent = Meta, so every existing link is unchanged). It only means something on the platform pages (`/` and `/campaigns`); product and ad pages are Meta.

| URL | What it shows |
|---|---|
| `/` (Meta, default) | the Products table, as before. `View: Products \| Campaigns` is new. |
| `/campaigns` (Meta) | every Meta campaign across all products (below) |
| `/?platform=google` | Google overview: trend, campaign types, campaigns (see MARKETING_METRICS_GOOGLE.md) |
| `/campaigns?platform=google` | Google campaigns first, the campaign types as a filter row |
| `/?platform=all` | All platforms overview (below) |
| `/google` | the first Google URL; redirects to `/?platform=google` |

Switching platform keeps the range, comparison and Numbers-from choice; Stage, Active only and the chart picks (Meta only) are dropped when leaving Meta. On a Campaigns page the switch keeps you on Campaigns (All has no Campaigns view and goes to the overview). `/api/status` lists the platforms (`sources.platforms`: key, name, available, first_day, through); the Google button is disabled when Google is not loaded.

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

## Not done

- A platform-level trend chart on the Meta Campaigns view (the product views have one per product; here each row has its sparkline).
- Product filter chips list products with spend in the window only; a product with no spend is not offered.
