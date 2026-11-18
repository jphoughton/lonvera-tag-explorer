# Ad -> Facebook Page identity (Lonvera handle vs other pages)

Which Facebook Page (handle) each Meta ad runs from, refreshed nightly, so Lonvera-page vs other-page performance can be queried any time.

| Piece | What |
|---|---|
| `public.meta_pages` | page_id, name, `is_lonvera`, active. Seeded: 161085663758792 = Lonvera, 830267930177476 = another handle. Unseen pages are added by the job, unnamed. |
| `public.meta_ad_pages` | one row per ad: page_id, Instagram account, `effective_object_story_id` (`<page>_<post>`), creative id, first/last seen, source of the page id. |
| `analytics.meta_ad_pages` | read-only: ad_id, family_key, page_id, page_name, is_lonvera_page (+ account, story, source, seen dates). |
| `analytics.meta_page_performance_daily` | day x page x product x stage: spend, impressions, link clicks, Meta purchases, Meta purchase value. |
| job `etl.meta_pages` | nightly step in `scripts/deploy/run_etl.sh` (non-fatal), etl_runs job `meta_pages`, freshness source `meta_ad_pages` in `analytics.data_coverage`. |

Migrations 278 (tables, seed) and 279 (views, coverage row, catalog, comments).

## The job (`etl/meta_pages.py`)

* Bulk listing per ad account (`GET /act_<id>/ads`, fields `id,created_time,updated_time,creative{effective_object_story_id,object_story_id,object_story_spec{page_id,instagram_user_id},instagram_actor_id}`,
  `limit=500`, `after` cursor). Page id = prefix of `effective_object_story_id`, else of `object_story_id`, else `object_story_spec.page_id`. Never one call per ad.
* `python -m etl.meta_pages --full`: every ad of both accounts (the one-time backfill). Nightly: ads with `updated_time` in the last 14 days, plus the ads with Meta spend in the last 14 days and no row
  (batched `/?ids=` lookups, 50 per call, at most 200 ids a run; this is also the only way deleted ads resolve: Meta does not accept DELETED as a list filter).
* "Reduce the amount of data" halves the page size on the same cursor; rate limits (4, 17, 32, 613, 80000-80014) back off exponentially inside `meta_api.graph_get`. Read-only GETs.
* Fails loudly: a rejected token, a listing that gives up, or more than 5% of the last 14 days' Meta spend on ads with no page identity ends the etl_runs row `failed`. A smaller unresolved share is a warning in `etl_runs.error` on an ok row.
* The page of an ad is its CURRENT creative's page; a swapped creative changes it.

## Reading it

Basis: Meta-reported (counts returning customers); Meta ad-level data starts 2026-05-06. Clicks are Meta link clicks (unique outbound clicks are not stored per ad). Ratios come from sums:
CPM = spend / impressions x 1000, CTR = link clicks / impressions, cost per link click, conversion rate = purchases / link clicks, AOV = purchase value / purchases, cost per purchase, ROAS = purchase value / spend.
For new-customer ROAS join `analytics.meta_ad_pages` to `analytics.meta_ad_daily` on ad_id and use its Upstack columns.

**Confounded:** the Lonvera page and the other handles carry different products, stages (testing / scaling), ad ages and budgets. Compare like-for-like within product x stage x period, and say "consistent with",
never "caused by". Spend by page adds up to total Meta ad-level spend; ads with no page sit in the `(unknown page)` row (page_id NULL).

```sql
SELECT product_slug, stage, is_lonvera_page, round(sum(spend)) AS spend, round(sum(meta_revenue) / NULLIF(sum(spend), 0), 2) AS meta_roas
FROM analytics.meta_page_performance_daily WHERE day > current_date - 31 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
```

## The page: By page (`/pages`, Marketing metrics, platform Meta, "View by: By page")

`GET /api/meta/pages?window=30d&stage=testing,scaling&product=<slug>` (code `app/meta_pages.py`, page `app/static/js/pages/pages.js`). Window picker is the usual range control (7 / 30 / 90 / 180 days, custom, since launch;
Meta ad-level history is only ~200 days, so "365 days" = since launch, 2026-05-06). Two tables: Lonvera page vs all other pages vs unknown, then one row per page. Columns: ads with spend, Meta spend, share,
NC ROAS and NC CPA (Upstack new customers, over the days Upstack data exists), Meta-reported ROAS (side column), cost per purchase, average order value, CPM, CTR (link clicks / impressions), cost per link click,
conversion rate (Meta purchases / link clicks). Ratios of sums. The like-for-like warning prints on the page. Unique outbound clicks are not stored per ad, so click metrics are link clicks.

## Backfill notes (2026-11-18)

Lonvera Main is on Meta's development access tier and its cpu budget is shared with every other job, so a full listing of ~40K ads is throttled (code 80004). The listing paces itself by the
`x-business-use-case-usage` header (waits the reported regain time, pauses above 90% use, halves / doubles its page size). `python -m etl.meta_pages --spend-since 2026-05-06` backfills in order of spend instead:
ads with Meta spend since that day and no row, biggest first, 50 ids per `/?ids=` call; ads Meta no longer returns get an `unresolved` row so they leave the queue. Resumable.
