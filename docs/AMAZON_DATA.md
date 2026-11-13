# Amazon data layer

Ten tables filled nightly from ExpandFi (merchant 1452, read only) by the `feeds` service, each with a read-only view in the `analytics` schema (domain `amazon`). There is no Amazon page yet; this is the data underneath it. Tables are created by migration 250, the views, the `analytics.data_coverage` rows and the data dictionary comments by migration 251. Column meanings live in `docs/DATA_SCHEMA.md` (generated); this page says what each table is for and where it can mislead.

How it runs: `python -m etl.feeds run` (the 21:00 UTC cron) pulls every feed. The Amazon feeds are native (no sheet twin, mode `shadow`, they write their own table) and non-fatal: a failed or empty pull is logged in `proj_feed_attempts` and printed as a warning, never turns the run red and never stops another feed. They run after the older ExpandFi feeds and share the same login and credential. An empty pull writes nothing and deletes nothing. Every pull deletes its trailing window and inserts the pulled rows, so a rerun leaves the same rows.

| Table (view) | Grain | Source in ExpandFi | History | Nightly window |
|---|---|---|---|---|
| `amazon_asin_products` | asin, sku | `asin_skus` (Amazon.com) | current | full refresh |
| `amazon_ads_campaign_daily` | day, marketplace, campaign, ad group, ad type | `amazon_ads_campaign_data` | 2025-12-30 | 21 days |
| `amazon_ads_asin_daily` | day, campaign, ad group, ASIN, ad type (SP, SD) | `amazon_ads_raw_data` v3 advertised product | 2026-01-04 | 21 days |
| `amazon_sqp_weekly` (`amazon_search_queries_weekly`) | week, marketplace, ASIN, query | `brand_analytics_search_query_performances` WEEK | 2025-07-13 | 6 weeks |
| `amazon_sqp_monthly` (`amazon_search_queries_monthly`) | month, marketplace, ASIN, query | same, MONTH | 2025-06 | 3 months |
| `amazon_orders_asin_daily` | day, ASIN, channel | `sales` + `customers` | 2024-09-24 | 21 days |
| `amazon_traffic_daily` | day (US account) | `sales_and_traffic_reports` | 2024-04-07 | 14 days |
| `amazon_market_baskets` | week, ASIN, companion ASIN | `brand_analytics_market_baskets` WEEK | 2026-06-28 | 6 weeks |
| `amazon_repeat_purchases` | week, ASIN | `brand_analytics_repeat_purchases` WEEK | 2026-06-28 | 6 weeks |
| `amazon_settlement_fees_daily` | posted day, fee and transaction type | `amazon_settlement_reports` | 2024-03-18 | 45 days |

Product on every table is the app's product slug (`vita-bull`, `beef-liver`, `boneflex`, `vita-belle`) from `amazon_asin_products`. Campaign-level ad rows get a product only when the campaign name contains one of our ASINs (about 88% of spend); "All Products", "Full-Funnel" and similar campaigns stay NULL.

## Ads

- Campaign table: the same v3 > v2 > v1 rule per campaign-day as `proj_feed_amazon`, applied across report types, so `sum(spend)` per day equals `proj_feed_amazon.ad_spend` (tested, and checked live to the cent). Raw ExpandFi rows are about 2x that.
- `ad_type` is `sp`, `sb`, `sbv` or `sd` from the report type. ExpandFi files the newer unified v1 report under `spCampaigns`, so v1 rows labelled `sp` can be Sponsored Brands, Display or Full-Funnel campaigns. `api_version` tells you which rows these are.
- Attribution is kept per type: the 1, 7, 14 and 30 day columns exist for Sponsored Products v3 only (older reports store zeros there, which would read as no sales, so they are NULL). Brands and Display have one reported sales and purchases figure (`sales`, `purchases`); new-to-brand is populated for Display only. `sales_reported` is Sponsored Products 14-day sales or the single reported figure of the other types: use it for a total-ads return on spend. Never add it to Meta or Google revenue.
- 14-day sales mature for 14 days, so the last two weeks read low; the 21-day window re-pulls them.
- ASIN table: v3 advertised product reports only (the v1 copies are one report stored twice under two names). Sponsored Products v3 spend ties to the campaign table. There is no Sponsored Brands ASIN grain anywhere in ExpandFi, and no keyword, search term, placement or impression share data (see "Not available").
- `proj_feed_amazon` and everything that reads it are unchanged. Its `attributed_sales` is Sponsored Products only while its spend includes Brands and Display, which understates a total-ads return; use `amazon_ads_campaign_daily.sales_reported` instead.

## Organic search (the search query tables)

- One row per search query and each of our ASINs per week (or month): the query's market size and our share of impressions, clicks, cart adds and purchases.
- SUM vs MAX: `search_query_volume` and every `total_*` column are market totals repeated on each ASIN row of the query. Summing them across ASINs overstates them (five ASIN rows read 5x). Use MAX per query and period; to total our side, SUM the `asin_*` columns.
- Top 100 cap: Amazon reports about the top 100 queries per ASIN and period. Absent means not in that list, not zero, and a query dropping out can look like a share collapse.
- Shares are percent 0 to 100, not fractions. Impressions include sponsored placements, so our own ads count in them. Purchases are search-origin and same-day and will not tie to orders.
- No organic rank position exists in ExpandFi. The click, cart add and purchase shares are the proxy (purchase share is the least contaminated).
- Source duplicates (restated re-pulls with different hashes) are collapsed to the latest `updated_at`. `query_key` is the query lower-cased, trimmed, single-spaced; `search_query` keeps the original text. `is_branded` comes from one regular expression in `etl/feeds/amazon_feeds.py` (`BRANDED_RE`): Lonvera, Vita or Vital Bull, Vita or Vital Belle, Vital Flow, Bone Flex.
- Weeks land about 5 days after their Saturday (the newest week can be partial for a while); months about 2 weeks after they end. US and one MX ASIN are present; filter on `marketplace`.
- Joining paid to organic by query is not possible yet: ExpandFi stores no paid search terms.

## Orders, traffic, baskets, repeat purchases, fees

- Orders: same date basis (purchase date), channels (Amazon.com and B2B), inner join to customers and new-customer rule (the order is on the customer's first purchase day) as `proj_feed_amazon`; daily units, revenue and new-customer revenue sums equal it (tested, and checked live). `orders` counts distinct orders per ASIN, so do not sum it across ASINs.
- Traffic: account level (each ASIN row in the source repeats the account total); the latest restatement of each day is kept. Ordered units and sales here are Amazon's ordered basis, not shipped orders.
- Baskets and repeat purchases: weekly, from 2026-06-28 only (that is all ExpandFi holds at weekly grain). Percentages are 0 to 100.
- Settlement fees: signed as stored (sales positive, fees, promotions and ad debits negative). Posted-day basis; settlements arrive in batches, so the newest day can be two weeks old. "Cost of Advertising" is the settlement debit, not the Ads report spend.

## Freshness

`analytics.data_coverage` has one row per table (period tables report weeks or months held in `days`). Stale limits used by the feeds freshness check: ads, orders, traffic 3 days; settlements 21; weekly tables 16; monthly search 50.

## Backfill

All history ExpandFi holds, in chunks that fit the 150 second warehouse timeout (about 10 to 15 seconds per table when measured against the live warehouse). Needs the ExpandFi login in `feed_oauth`, so run it where the feeds service runs (Railway `feeds`, `railway run` or a shell on that service):

    python -m etl.feeds run --backfill --only amazon_asin_products,amazon_ads_campaign_daily,amazon_ads_asin_daily,amazon_sqp_weekly,amazon_sqp_monthly,amazon_orders_asin_daily,amazon_traffic_daily,amazon_market_baskets,amazon_repeat_purchases,amazon_settlement_fees_daily
    python -m etl.feeds run --only amazon_sqp_weekly --since 2026-01-01        # one table from a date
    python -m etl.feeds run --only amazon_traffic_daily --days 400            # or a number of days

`--backfill` and `--since` never narrow a feed below its normal window. Each feed's first day (`Feed.history_start`) is the first day the source holds.

## Not available from ExpandFi

Keyword, target, search term and placement performance, impression share, Sponsored Brands keywords or ASIN grain, campaign-level negatives (these exist only as on-demand Data Dash pulls, with short retention) and a true organic rank position. Top Search Terms (`brand_analytics_search_terms`) is empty.
