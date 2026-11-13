# Marketing metrics: Amazon pages

Amazon is its own channel. It has a page, `/amazon`, reached from the **Amazon** button of the Platform picker (All | Meta | Google | Amazon) and the Amazon tab of the Performance section. It is **not** added to anything on the All platforms pages: no Amazon spend, purchases or revenue enters Total spend, Total new-customer ROAS, the Meta and Google charts or any existing number. Amazon sales overlap what Meta and Google credit, and Upstack has no Amazon data, so only Amazon's own reporting exists and the **Numbers from** switch is absent on Amazon pages (the page says so in one line). Stage, Active only and comparison are absent too.

The data is the ten tables of migration 249 (see [AMAZON_DATA.md](AMAZON_DATA.md)), filled nightly from ExpandFi. Code: `app/amazon.py` (all SQL and every builder), routes in `app/api.py`, page `app/static/js/pages/amazon.js`, style `app/static/css/amazon.css`, mock `tests/ui/mock_amazon.py`.

## Views
`/amazon?view=overview|campaigns|products|search|fees` (View switch at the top right; the range of the bar above applies to all of them and carries between them; the page opens on the last 30 days).

| View | Shows | Tables read |
|---|---|---|
| Overview | tiles: Amazon revenue, orders, units, new-customer revenue, ad spend, ad-reported sales, ad return, ad spend share of revenue, sessions, conversion; a Day / Week / Month trend (bars: revenue, new-customer revenue, orders, sessions; line: ad spend, ad return, ad spend share of revenue, ad-reported sales); a **by product** table (Vita Bull, Beef Liver, Boneflex, Vital Belle and "No single product") and a **by ad type** table (Sponsored Products, Sponsored Brands, Sponsored Brands video, Sponsored Display) | `amazon_orders_asin_daily`, `amazon_ads_campaign_daily`, `amazon_traffic_daily` |
| Campaigns | one row per campaign: ad type, product, status, spend, impressions, clicks, click-through rate, cost per click, ad-reported orders and sales, ad return, cost per order; filters for ad type, product and text; click a campaign for its daily trend | `amazon_ads_campaign_daily` |
| Products | one row per ASIN: revenue, units, new-customer revenue, ad spend, ad-reported sales, ad return, ad spend share of revenue | `amazon_orders_asin_daily`, `amazon_ads_asin_daily`, `amazon_asin_products` |
| Search terms | organic search, below | `amazon_sqp_weekly` |
| Fees | settlement lines by fee type and month | `amazon_settlement_fees_daily` |

Every table sorts by clicking its headers and has a **Download CSV** button (the rows now shown). Keyword-level ad tables do not exist yet, so nothing is shown for them.

## Rules the numbers follow
- **Ratios are ratios of sums**, never an average of daily ratios. **NULL is not zero**: a range before the ads data starts shows blank ad figures, not $0; ratios with no denominator are blank.
- **Dates**: each page's range is clamped to the days its tables share (Overview: orders and ads; Search terms: the weeks held). Ads start 2025-12-30, orders 2024-09-24: when a range starts before the ads, the ad spend share of revenue uses only the revenue of days the ads cover, and the page says so. Traffic lags a few days, so sessions and conversion cover the days traffic holds and conversion divides the orders of those same days.
- **Attribution** is labelled per ad type: Sponsored Products sales and orders are 14-day (the last two weeks read low while they mature); Sponsored Brands, Brands video and Sponsored Display are as Amazon reports them. The ad return is ad-reported sales / ad spend: it is **not** comparable with the new-customer ROAS of Meta and Google.
- **Orders** are counted per ASIN (the source counts orders per ASIN), so an order with two of our products counts once for each. By-product and total orders add up the same way; the tile says so.
- **Products**: ASIN-level ad data is Sponsored Products and Sponsored Display only. Sponsored Brands has no product grain anywhere in ExpandFi, so that spend is in no row; the page shows the gap in dollars.
- Campaign product comes from the campaign name (about 88% of spend); "All Products" and "Full-Funnel" style campaigns are "No single product".

## Search terms (organic)
For the **full weeks (Sunday to Saturday) inside the chosen range**: a 30-day range holds four weeks; a range with no full week says so. Filters: product, All / Branded / Non-branded (the one regular expression in `etl/feeds/amazon_feeds.py`), text search (server side, so it covers every term, not only the rows shown). US only. At most 2,000 terms (the most searched) are returned and the page says when it cut.

Columns: search term; search volume (the latest week of the range the term was listed, and the weekly average over the weeks it was listed); our impression, click, cart-add and purchase share; our clicks and purchases; change vs the prior equal period (click and purchase share in percentage points, search volume in percent; blank when the term was not listed in the prior period or the prior weeks are not held); weeks listed ("3 of 4").

Rules (they are the traps in AMAZON_DATA.md):
- **Market totals take the MAX across the ASIN rows of one term-week, our own counts take the SUM.** A term shown for five ASINs carries the same search volume and totals five times; summing them multiplies the market.
- **Shares are percent 0 to 100 and are always recomputed**: our summed count / the market total, over the weeks the term was listed (a ratio of sums). Several ASINs rolled into a product or "All products" therefore give one honest share, never a sum of stored shares.
- **A missing week is a gap, not zero.** Amazon reports about the top 100 terms per product per week; a week without a row means "not in that list". Shares and averages use only weeks with a row; the weekly history draws the gap as a break in the line.
- Amazon gives **no rank position**. Click share and purchase share are the closest measure (purchase share is the least contaminated); impressions include our own sponsored placements; purchases are search-origin and same-day, so they will not tie to orders. These words are on the page.

Click a term for its **weekly history** (every week held): search volume as bars and, per product plus all products together, a share line (purchase, click, cart-add or impression share). `term=<query>` in the URL opens it on load.

## API (read-only, signed-in session or API key, cached like the Google routes; `window` / `from` / `to` only, other params are ignored)
| Route | Returns |
|---|---|
| `GET /api/amazon/overview` | `tiles`, `by_product`, `by_ad_type`, `notes`, the ranges each table covers |
| `GET /api/amazon/trend?grain=day\|week\|month` | `points` of revenue, orders, new-customer revenue, ad spend, ad-reported sales, ad return, ad spend share, sessions (`partial` marks an unfinished week or month) |
| `GET /api/amazon/campaigns?ad_type=&product=&q=` | `rows` (one per campaign, metrics in `m`), `total`, `facets` |
| `GET /api/amazon/campaign-trend?campaign_id=&grain=` | the campaign's `points` |
| `GET /api/amazon/products` | `rows` per ASIN, `total`, `spend_without_product` |
| `GET /api/amazon/search-terms?product=&branded=all\|branded\|nonbranded&q=` | `rows`, the `weeks` used and the `prior_weeks`, `summary`, `truncated` |
| `GET /api/amazon/search-terms/history?term=&product=` | `weeks`, `volume`, `series` per product and "all" |
| `GET /api/amazon/fees` | `months`, `rows` (type x month), `total` |

Every route answers **200 with `loaded: false` and the message "Amazon data has not loaded yet"** while its tables are empty (before the backfill); the pages show that as an empty state, never an error. `/api/status` gains `sources.amazon` (`available`, `through`, `first_day`, per table); it is **not** added to `sources.platforms`, which stays the spend platforms of the All views. The Platform picker disables the Amazon button elsewhere while Amazon is not loaded.

Cache: the response cache keys on `app_meta` and `etl_runs`, which the Amazon feeds do not touch, so after the nightly feeds run a cached Amazon answer can be up to the cache's 10 minute lifetime old.

## Tests
`tests/api/test_api_amazon.py` (every route with seeded rows, including MAX vs SUM, the share recompute, gaps, aligned ad days, and that All / Meta pages are unchanged), `test_api_amazon_empty.py` (empty state), `tests/ui/test_amazon.py` (picker, every view, history chart, CSV, 375 px, empty state) against `tests/ui/mock_amazon.py`.
