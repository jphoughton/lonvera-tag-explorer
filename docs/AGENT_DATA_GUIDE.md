# Agent quick start: Lonvera marketing data

One page for an AI agent (or a person) that has been handed read-only access. Full dictionary: `docs/DATA_SCHEMA.md` (machine-readable: `docs/schema.json`).

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
select source, first_day, last_day, loaded_at from analytics.data_coverage;   -- what is loaded, through which day
```

## The views that answer most questions

| View | Grain | Use it for |
|---|---|---|
| `meta_ad_daily` | ad x day | spend, impressions, clicks, Meta-reported and Upstack-attributed purchases / new customers / revenue; carries ad, ad set, campaign, stage, product names |
| `meta_adset_daily`, `meta_campaign_daily` | ad set / campaign x day | the same, rolled up |
| `meta_ads` | ad | names, status, product, all-time totals |
| `platform_daily` + `orders_daily` | platform x day / day | Meta vs Google spend; Total NC ROAS = `orders_daily.new_customer_revenue` / total spend of the same days |
| `google_campaign_daily` | campaign x day | Google spend, conversions, Upstack attribution (no ad group / keyword data exists) |
| `creative_tracker` | tracker row | tags (cluster / ICP / pain point), hooks, since-launch metrics per creative |

Also: `meta_reach` (account-level reach / frequency), `meta_campaign_daily_history` (campaign grain back to 2024-12-31), `etl_runs` (did the pipeline run).

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

## Not in the database (say so instead of guessing)

Reach / frequency below account level, video and hook-rate metrics, age / gender / placement / country breakdowns, ad budgets and bids, Google ad groups / keywords / search terms, TikTok / Bing,
customer- or order-level data, margins / COGS / P&L, email and SMS. The complete list is under "Gaps" in `docs/DATA_SCHEMA.md`.

## Example: ad-level 30-day spend and ROAS

```sql
select ad_id, ad_name, round(sum(spend), 2) spend, round(sum(meta_revenue) / nullif(sum(spend), 0), 2) roas
from analytics.meta_ad_daily
where day > (select max(last_day) from analytics.data_coverage where source = 'meta_ad_daily') - 30
group by 1, 2 having sum(spend) > 0 order by spend desc limit 25;
```
