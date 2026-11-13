# Agent quick start: Lonvera data

One page for an AI agent (or a person) that has been handed read-only access. Full dictionary: `docs/DATA_SCHEMA.md` (machine-readable: `docs/schema.json`, which also carries the `metrics` glossary).

## Connect

The connection URL is in a file the admin gave you (mode 600, never in the repo, never pasted into chat or committed): by default
`~/lonvera-tag-explorer-data/analytics_reader_url.txt`.

```bash
psql "$(cat ~/lonvera-tag-explorer-data/analytics_reader_url.txt)"         # psql, DBeaver, TablePlus: same URL
```
```python
import psycopg; conn = psycopg.connect(open("/path/to/analytics_reader_url.txt").read().strip())
```

You are the role `analytics_reader`: SELECT on the `analytics` views and nothing else. Writes, DDL and every table outside `analytics` fail with "permission denied". Queries are cut at
30 seconds, 5 connections at once. `search_path` is `analytics`, so `select * from meta_ad_daily` works. Introspect with `\d+ analytics.*` or `information_schema`: every view and column
carries a description (`COMMENT`).

## Start here

```sql
select view_name, domain, grain, first_day, last_day from analytics.catalog order by domain, view_name;   -- every view: what it is, its grain, the days it covers
select name, label, default_basis from analytics.metric_glossary order by domain, name;                     -- the canonical metrics: formula, default basis, traps
select source, first_day, last_day, loaded_at from analytics.data_coverage;                                 -- what is loaded, through which day
```

Before you quote any ROAS, CPA, hit rate, payback or revenue number, read its row in `analytics.metric_glossary` (`select * from analytics.metric_glossary where name = 'total_nc_roas'`):
it names the default basis, the other bases and the traps. The same list is the `metrics` key of `docs/schema.json`.

## You want / Use (15 domains)

| You want | Domain | Use |
|---|---|---|
| spend, impressions, clicks, purchases, ROAS per ad / ad set / campaign per day (Meta-reported and Upstack-attributed) | marketing | `meta_ad_daily`, `meta_adset_daily`, `meta_campaign_daily`, `meta_ads` (names, status, product, all-time totals) |
| Meta vs Google spend per day; spend back to 2025; Meta by product; bid caps; reach and frequency | marketing | `platform_daily` + `orders_daily` (Total NC ROAS), `platform_daily_history`, `meta_product_daily`, `meta_product_reach`, `meta_bids_daily`, `meta_reach`, `google_campaign_daily`, `meta_campaign_daily_history` |
| last-click credit by source, any-click new customers by stage, orders no ad gets credit for | attribution | `attribution_source_daily`, `attribution_anyclick_daily`, `attribution_unmapped_daily` |
| actual revenue and new customers (counted once), by product or hour; tax and refunds; Amazon; site traffic | orders | `orders_daily`, `orders_daily_detail`, `orders_by_product_daily`, `orders_hourly`, `amazon_daily`, `site_traffic_daily` |
| creative tracker rows with tags and hooks; family spend and results by basis; test to scale; copy and transcripts (what is said in each ad video: `ad_transcripts`) | creative | `creative_tracker`, `creative_family_daily`, `creative_family_metrics`, `creative_stage_moves`, `creative_scaling_placements`, `creative_scaling_benchmarks`, `creative_unmatched_meta`, `creative_content`, `ad_transcripts` |
| products, taxonomy lines (cluster / ICP / pain point), ingredients, every tag and hook | tags | `tag_products`, `tag_taxonomy_lines`, `tag_line_ingredients`, `tracker_tags`, `tracker_hooks` |
| the weekly priorities list: lines, status, score, hit rates, thresholds, baselines | priorities | `priorities_concepts`, `priorities_snapshots`, `priorities_baselines`, `priorities_thresholds`, `priorities_manual_concepts` |
| who is making which concept for which week | production | `production_assignments`, `production_formats` |
| Klaviyo campaigns, flows and messages; landing page copy and the ads behind each page; Shopify orders and lines (no customer data) | email, landing_pages, orders | `klaviyo_*`, `landing_pages`, `landing_page_versions`, `landing_page_ads`, `landing_page_top_ads`, `shopify_orders`, `shopify_order_lines` (created by migration 231 only when their tables exist on the database; check `analytics.catalog`) |
| scenarios, levers, P&L inputs, QuickBooks actuals, COGS, retainers | projection | `projection_scenarios`, `projection_levers`, `projection_series`, `projection_actuals`, `projection_pnl_drivers`, `projection_pnl_settings`, `projection_pnl_account_map`, `projection_cogs_sku`, `projection_vendor_retainers` |
| purchase orders, FBA transfers, vendor terms, stock, units shipped | inventory | `inventory_purchase_orders`, `inventory_fba_transfers`, `inventory_vendor_lead_times`, `inventory_vendor_costs`, `inventory_product_costs`, `inventory_reorder_params`, `inventory_stock_counts`, `inventory_stock_daily`, `inventory_units_daily`, `inventory_units_monthly`, `inventory_units_by_country` |
| acquisition cohorts, the retention curve inputs (Live) and the Goal curve | cohorts | `cohorts_monthly`, `cohorts_curve_cumulative`, `cohorts_curve_goal` |
| today so far, Amazon month to date, the pacing levers and hourly curve | pacing | `pacing_live_today`, `pacing_amazon_month_to_date`, `pacing_levers`, `pacing_hourly_spend_curve`, `pacing_meta_spend_monthly` |
| competitor ads and the angles they run | competitors | `competitor_ads`, `competitor_concepts`, `competitor_brands` |
| is the data fresh, which feed or job failed, what the views and metrics are | pipeline | `data_coverage`, `etl_runs`, `pipeline_feeds`, `pipeline_feed_attempts`, `pipeline_feed_verify`, `pipeline_refresh_runs`, `pipeline_alerts`, `catalog`, `metric_glossary` |

Grain, columns, coverage and caveats of each view are in its `COMMENT` (`\d+ analytics.<view>`) and in `docs/DATA_SCHEMA.md`.

## The 5 rules for correct answers

1. **Name the basis.** `meta_*` = Meta-reported (counts returning customers). `upstack_new_customer*` = first orders only (the "NC" in NC ROAS / NC CPA). `upstack_order*` = all orders Upstack credits.
   Spend is Meta's / Google's in all of them. Never mix bases in one ratio. Revenue is gross (tax in, refunds not out).
2. **Spend adds across platforms; purchases and revenue never do** (Meta and Google credit the same orders). Total NC ROAS = actual `orders_daily.new_customer_revenue` / total spend over days
   that Meta, Google and `orders_daily` all cover, as a ratio of sums.
3. **Days.** The last loaded day is yesterday (Pacific); today is not loaded. "Last 30 days" = the 30 days ending at `data_coverage.last_day`, e.g.
   `day > (select max(last_day) from analytics.data_coverage where source = 'meta_ad_daily') - 30`. The last 3-7 days still move (Meta restates conversions). Meta days are each ad account's own day,
   everything else is Pacific.
4. **NULL is not zero.** `upstack_*` is NULL on days Upstack was not pulled (Meta before 2026-05-06, Google before 2025-05-13). A day without orders is missing from `orders_daily`.
   A row with `spend = 0` and `upstack_orders > 0` is normal (any-click credits an ad on days it did not spend).
5. **Ratios from sums, creatives once.** `sum(revenue) / sum(spend)`, never an average of daily ratios. In `creative_tracker` the metrics sit on the FIRST row of each ad family: sum rows, count
   unique ads with `count(distinct family_key)`. Ad / ad set data starts 2026-05-06; before that only campaign grain (`meta_campaign_daily_history`).

## More examples

New ads launched per week = ad families by their FIRST day of Meta spend (not `creative_tracker.launch_date`, which undercounts old ads, and not `ad_id`, which counts copies):

```sql
select date_trunc('week', fd)::date as week_start, count(*) as new_ad_families
from (select a.family_key, min(d.day) as fd
      from analytics.meta_ad_daily d join analytics.meta_ads a using (ad_id)
      where d.spend > 0 and a.family_key is not null group by 1) t
group by 1 order by 1;
```

ROAS by product on both bases (the winner can differ by basis, so show both):

```sql
select product_slug, sum(spend) as spend,
       round(sum(meta_revenue) / nullif(sum(spend), 0), 2) as meta_reported_roas,
       round(sum(upstack_new_customer_revenue) / nullif(sum(spend), 0), 2) as upstack_new_customer_roas
from analytics.meta_ad_daily where day between '2026-10-01' and '2026-10-31' group by 1;
```

Testing vs scaling: `stage` is 'testing' when the campaign name contains 'test', otherwise 'scaling'. Group `meta_campaign_daily` by `stage`; cost per new customer = `sum(spend) / sum(upstack_new_customers)` (Upstack basis).

Monthly CPM before 2026-05-06 comes from `meta_campaign_daily_history` (`sum(spend) / sum(impressions) * 1000`); reach and frequency per month come from `meta_reach` at month grain, never from summed days.

## Not in the database (say so instead of guessing)

Reach / frequency below account level, video and hook-rate metrics, age / gender / placement / country breakdowns before 2026-10-12 and Upstack new-customer attribution for any breakdown (the breakdown views in `analytics.meta_breakdown_daily` are Meta-reported), ad budgets and bids per ad set before the change feeds (`meta_change_ledger`, `meta_change_log`, `meta_adset_state_daily`) began, Google ad groups / keywords / search terms,
TikTok / Bing, customer- or order-level data, email and SMS. **Projection outputs** (the computed P&L, cash, financing and inventory-burn results) are calculated by the app on demand and are not stored:
the views hold their inputs (levers, series, actuals, cohorts, purchase orders, stock); read the results from the HTTP API (`/api/projection/...`). Nothing here names a person (audit trails and
`*_by` columns are not exposed) and nothing is a secret. The complete list is under "Gaps" and "Excluded from the analytics schema" in `docs/DATA_SCHEMA.md`.

## Example: ad-level 30-day spend and ROAS

```sql
select ad_id, ad_name, round(sum(spend), 2) spend, round(sum(meta_revenue) / nullif(sum(spend), 0), 2) roas
from analytics.meta_ad_daily
where day > (select max(last_day) from analytics.data_coverage where source = 'meta_ad_daily') - 30
group by 1, 2 having sum(spend) > 0 order by spend desc limit 25;
```
