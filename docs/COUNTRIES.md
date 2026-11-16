# Countries (Marketing metrics, platform Meta)

`/countries` (View by Products | Campaigns | Countries on the Meta platform): one row per country for any date window, Meta spend next to the new customers and new-customer revenue
the business actually recorded. API: `GET /api/meta/countries?window=30d` or `?from=YYYY-MM-DD&to=YYYY-MM-DD` (code: `app/countries.py`, page: `app/static/js/pages/countries.js`).

## Where each number comes from

| Column | Source | Basis |
|---|---|---|
| Meta spend, impressions, link clicks, Meta-reported purchase value | `analytics.meta_breakdown_daily`, `breakdown = 'country'`, `value` = ISO code, both ad accounts | Meta-reported; starts 2026-10-12 |
| New customers, NC revenue | `public.nc_by_country_daily` (`analytics.nc_by_country_daily`), feed `nc_by_country_daily` | Shopify first orders through ExpandFi merchant 1452, every channel |

New customer = the customer's FIRST Shopify order ever (customer key = `shopify_customer_id`, else `lower(email)`; earliest `processed_at`, then id). Day = `processed_at` (stored UTC) as an
`America/Los_Angeles` date. Country = `shipping_address.country_code`, else `billing_address.country_code`, else `unknown`. Revenue = `total_price` (US dollars, tax included, before refunds).
A first order flagged `test` is dropped. Upstack has no country dimension, so these are NOT Upstack new-customer numbers; they include Google and organic buyers.

## Rules

* Ratios are ratios of sums (NC ROAS = NC revenue / spend, NC CPA = spend / new customers, average order value = NC revenue / new customers, CPM = spend / impressions x 1,000, CTR = link clicks / impressions, cost per link click, conversion rate = new customers / link clicks, Meta-reported ROAS = Meta purchase value / spend). Never an average of daily ratios.
* The window is cut to the days both tables hold, so spend and new customers always cover the same days; the page says what was cut. Days before coverage are blank, not zero; a window wholly before coverage shows an empty state.
* Total NC ROAS = new-customer revenue of ALL countries (including countries with no Meta spend) / total Meta spend.
* A country with new customers but no spend shows blank spend columns; a country with spend but no new customers shows zeros.

## The feed

`etl/feeds/expandfi_feeds.py`, feed `nc_by_country_daily` (native, non-fatal), part of the `feeds` service run (21:00 UTC). Trailing 21 days per run (delete the window, insert the pull). The first-order
statement reads only non-jsonb columns over the full history to find each customer's first order, then reads `raw_data` for the first orders whose Pacific day is in the window.

One-time backfill (everything from 2024-01-01 in 90-day windows; needs the same ExpandFi login the feeds service uses):

    railway run -s feeds python -m etl.feeds run --only nc_by_country_daily --backfill

Check: `SELECT min(day), max(day), count(*), sum(new_customers) FROM nc_by_country_daily;`
