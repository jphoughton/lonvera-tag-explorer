# Test to scale: creatives that moved from Testing to Scaling

Page `/promotions` (top nav: **Test to scale**), endpoint `GET /api/promotions`, precomputed tables `creative_stage_moves*` and `creative_scaling_placements*` (migration `016`, rebuilt nightly by `python -m etl.stage_moves` after run_daily and upstack_daily; the logic is migration 015's view; refresh by hand with the same command; `app_meta` keys `stage_moves_built_at` / `stage_moves_data_through`, also in the API's `stage_moves` and on the page).
Built on `family_daily` (creative x day x stage), so it needs nothing the Tags data does not already have. Meta only: Google has no creative grain.

## What a move is

Meta cannot move an ad between campaigns. The buyer duplicates the creative into a scaling campaign, so a move is read off the daily spend:

> A creative (tracker family) **moved** when it had spend in a **Testing** campaign and its **first spend in a Scaling campaign** came on a **later day**.

- Stage is the campaign's stage on that day (`meta_campaigns.stage`: Testing when the campaign name contains `test`, otherwise Scaling; renaming a campaign reclassifies its history).
- Both stages on the same first day (`same_day`), scaling before testing (`scaling_first`), scaling only (`scaling_only`) are **not** moves. Stage unknown (`unclassified_only`, sheet-backfilled days with no campaign) is not either. The view's `path` column says which one a creative is.
- **Launch day** = the first day with spend in any stage. Tracker `launch_date` is not used (it undercounts the older ads).
- Data starts 2026-05-06. A creative already spending on that day has an unknown launch day: `launched_before_data_start = true`, `days_in_test` is a lower bound (shown as `≥ n`), it is never counted as "launched in the range" and is left out of the median days to scale.

## Bulk moves

On some days hundreds of creatives entered scaling at once: a mass relaunch into new scaling campaigns (the original scaling campaigns were switched off), not a set of test winners. A **bulk move day** is a day on which **40 or more** creatives moved (`bulk_move_day`, `moves_that_day` in the view; `bulk_min` in the API, the "Bulk day from" box on the page).
Bulk moves are flagged with a chip on each row and a note under the strip; **Hide bulk moves** (`hide_bulk=1`) removes those creatives from *every* number on the page (table, strip, chart, funnel: out of numerator and denominator alike).
In production on 2026-11-04 the 40 line flags Aug 22 (76 moves), Oct 22 (187) and Oct 23 (80); Oct 24 (34) and Oct 25 (19), the tail of the October relaunch, stay under it. Ordinary days run 0 to 25 moves.

## Populations and rates (all within the chosen range)

| Name | Definition | Used for |
|---|---|---|
| moved | `moved` and first scaling day in the range | the table, the chart, testing-vs-scaling numbers, median days |
| launched | first spend day in the range, not running on day one | the funnel, the launch counts |
| testers | launched creatives that began in testing (`moved` or `testing_only`) | the base of **% reached scaling** |
| no test | launched straight into scaling (or scaling first, or both the same day) | counted separately, never in a test-to-scale rate |

- **% that reached scaling** = (testers that have moved) / (testers). It is a cohort rate: creatives launched in the last days of the range have had no time to scale, so recent weeks read low and keep rising (the funnel marks partial weeks). The strip's "Moved to scaling" is the *event* count (moves in the range, whatever the launch date), so it is not the numerator of that rate.
- **Min test spend** (`min_test_spend`) keeps only testers whose testing spend (before any move) is at least that much, everywhere; creatives that never tested are unaffected.
- All ratios are ratios of sums (ROAS = sum revenue / sum spend), never averages of ratios. Purchases and revenue are never added across platforms.
- **Test** numbers = Testing-stage numbers on days before the first scaling day. **Scaling** numbers = Scaling-stage numbers since the move, **through the latest day of data** (not to the end of a custom range; the page says so). Testing spend that continues after the move (the original keeps running) is reported as `test_after_spend` and is in neither.
- **Held up?** compares scaling ROAS since the move with testing ROAS before it: held up at 90% or more, softer from 70%, fell below. Too early until the creative has spent $200 and run 3 days in scaling; no baseline when its testing earned nothing. Thresholds are in `app/promotions.py` (`HELD_*`) and in `definition.held_thresholds`.
- Numbers from: Meta's own reporting (default), or Upstack (new customers, or all orders), through the same Numbers from / Count switch as the rest of the app. Spend, link clicks and the move itself are Meta's in every basis; purchases and revenue change. The Upstack views start with Upstack's history (2026-05-06, the same as Meta's daily data today).

## The simple views (tabs 1 to 3)

`/promotions` opens on **What should be scaled** (the default; the original page is the last tab, `?view=detail`). Tabs, in order: `view=should_scale` | `should_kill` | `in_scaling` | `detail` (What should be scaled | What should be killed | Performance in scaling | All moves, with detail). The three simple views are plain ranked tables from `GET /api/promotions/scale?view=...` (`app/scale_views.py`, UI `app/static/js/pages/scale_views.js`), read from the precomputed tables only (`creative_stage_moves*` plus `scaling_benchmarks`, migration 193, rebuilt in the same nightly `etl.stage_moves` transaction); no aggregation on request, same 8 s statement timeout, error box with Retry, same Numbers from switch (Meta own / Upstack new customers / Upstack all orders).

**What should be scaled** (`view=should_scale`): creatives with `path = 'testing_only'` (spent in a Testing campaign, never in a Scaling campaign) that spent on one of the last 3 days of data. Editable defaults (all in the chosen Numbers basis; URL params):

| Rule | Default | Param | Where it comes from |
|---|---|---|---|
| Minimum testing spend | $500 | `min_test_spend` (the page's existing parameter name; here it is a floor with a default) | `SV_MIN_SPEND`, my default, Josh to set |
| Minimum purchases | 5 | `min_purchases` | `SV_MIN_PURCHASES`, my default |
| Return bar | 100% of the product's scaling average | `bar_pct` | testing ROAS at or above the average scaling ROAS of the SAME product over the last 30 days of data (`scaling_benchmarks`), or testing CPA at or below that product's scaling CPA (divided by the bar fraction) |
| Recent spend | last 3 days | fixed (`SV_RECENT_DAYS`) | a dead creative is not recommended |

Excluded: anything with scaling spend (moved, same day, scaling first, scaling only). A product with no scaling spend in 30 days has no average, so none of its creatives qualifies (a note says so). The scaling average counts all scaling spend of the product, including mass-relaunch creatives.
Ranking: extra revenue over what the product's scaling average would have earned on the same spend (revenue - bar ROAS x spend), biggest first (rewards spend as well as return; one lucky small test does not top the list). Columns: rank, creative (link), product, days in testing, testing spend, purchases, ROAS, CPA, vs scaling average (above / near / below; "above (on cost per purchase only)" when only CPA beats it; near = testing ROAS from 90% of the bar), last 7 days ROAS, confidence chip (more than 50 purchases high, 20 to 50 medium, under 20 low). It is a screen, not a verdict, and says so. Empty state: plain sentence plus the 5 nearest misses (floors met, bar missed, closest first).

**What should be killed** (`view=should_kill`, the second tab; `build_should_kill` in `app/scale_views.py`): the mirror of the scale view. Creatives with `path = 'testing_only'` that spent on one of the last 3 days of data (one that already stopped needs no kill), judged against the SAME per-product scaling average from `scaling_benchmarks` (last 30 days, chosen Numbers basis), nothing new is precomputed. No Lonvera kill rule exists in the repo, docs or Google Sheets workspace (Josh's Hydrant kill/scale sheet is another brand and is not used), so these are my defaults, Josh to set:

| Rule | Default | Param | Meaning |
|---|---|---|---|
| Enough spend to judge | 2 x the product's scaling CPA (resolves per product, shown on the page: e.g. Vita Bull $180, Beef Liver $170, Boneflex $195 on 2026-11-09) | `kill_min_spend` (dollars; blank = the 2 x rule, `KV_SPEND_X`) | testing spend at or above it |
| Clearly below the bar | zero purchases, OR testing ROAS under 60% of the product's scaling ROAS AND testing CPA above 1.5 x its scaling CPA | `kill_roas_pct` (default 60, `KV_ROAS_PCT`; the 1.5 is the constant `KV_CPA_X`) | both ROAS and CPA must be bad, so a high-order-value creative is not killed on ROAS alone |
| Recent spend | last 3 days | fixed | as in the scale view |

Anything that would pass the scale view's bar (ROAS at or above the average, or CPA at or below it) is never a kill candidate, so the two views never list the same creative. A product with no scaling spend or no scaling purchases in 30 days has no line: none of its creatives is listed (a note says so).
Ranking: **wasted spend**, biggest first = testing spend minus purchases x the product's scaling CPA, floored at 0 (money spent beyond what a typical scaling creative needs to land the same purchases). Columns: rank, creative (link), product, days in testing, testing spend, purchases, ROAS, CPA, vs scaling average (`far below` = under the ROAS percentage, else `below`), last 7 days spend and ROAS, wasted spend, a plain reason ("0 purchases after $412" or "ROAS 0.31 vs 0.80 and CPA $240 vs $91"), confidence chip (spend at or above 3 x the scaling CPA high, 2 to 3 x medium, under 2 x low, which only appears when the spend line is lowered). Total line: creatives, testing spend, last 7 days spend (the money that stops if they are turned off), wasted. **Watch list** (collapsed, `watch`, biggest 25 of `watch_total`): not killed yet, either zero purchases after 1 to 2 x the product CPA (under the kill line) or enough spend with ROAS under 80% of the scaling average without being a kill. **Copy list** copies the creative names, one per line (`names`), to paste into Ads Manager: the app is read-only toward Meta and turns nothing off, and the page says so. Empty state: a plain sentence suggesting a lower spend line or higher ROAS percentage.

**Performance in scaling** (`view=in_scaling`): creatives whose first scaling day is in the page's window (range bar, default last 30 days), from `creative_stage_moves`; product filter and the page's Hide bulk moves control (`hide_bulk`, `bulk_min`, default 40) reused; bulk days carry the same chip and note. Sorted by scaling spend, with a spend-weighted total row per product (ratio of sums) and an All products row. "Vs its testing" and the too-early floor reuse `app.promotions.held_up` (scaling ROAS since the move / testing ROAS before it; $200 and 3 days): better from 110%, about the same from 90% (the page's held-up line), worse below; too early / no baseline as the page says. Status: **too early** (the page's floor), **stopped** (no scaling spend in the last 7 days), **fading** (last 7 days ROAS more than 10% below the since-move ROAS), else **holding**. Deviation from the brief: too early uses the page's existing floor ($200, 3 days) instead of a 7-day / purchase minimum, and "stopped" is a fourth word so a dead creative is never called holding.

`scaling_benchmarks` (basis `meta` | `upstack_nc` | `upstack_all`, product_slug incl. `none`): scaling spend, purchases, revenue and creatives over the 30 days ending on the last day of `family_daily`; described in `docs/DATA_SCHEMA.md`.
Tests: `tests/api/test_api_scale_views.py` (hand-built database, plain-Python oracles from the daily rows for the scale and kill views), `tests/ui/test_scale_views.py` (mock server).

## API

`GET /api/promotions`

| Parameter | Meaning |
|---|---|
| `window` / `from`+`to` / `days` | the range; `days=n` = the last n days of data; default **the last 30 days**; `window=launch` = every day held. Stage, Active only and comparison are ignored. |
| `product` | a product slug, or `none` for creatives of no product |
| `hide_bulk` | `1` drops bulk-move creatives from every number |
| `bulk_min` | bulk-day threshold (default 40) |
| `min_test_spend` | dollars |
| `grain` | `auto` (day up to 45 days, else week), `day`, `week` for the chart series |
| `source`, `basis` | `meta` (default) or `upstack` with `nc` (default) or `all` |

Response: `window_info`, `data_through`, `labels` (metric names of the basis), `filters`, `definition`, `products` (chips with counts), `summary` (strip), `bulk_days`, `series` (chart points), `moved` (one row per creative: launch / move day, days in test, `test`, `scale`, `last7`, `held`, `bulk`, `placements` = scaling campaigns / ad sets / ads since the move with spend), `not_scaled` (began in testing, never scaled: still testing or stopped), `cohorts` (funnel by launch week), `notes`. Cached like the other metrics endpoints (`app/respcache.py`).

## Tables, for agents

```sql
-- creatives that moved in the last 30 days, biggest scaling spend first (Meta's numbers)
SELECT display, product_slug, first_day, scaling_first_day, days_in_test, test_spend, test_revenue / NULLIF(test_spend, 0) AS test_roas,
       scale_spend, scale_revenue / NULLIF(scale_spend, 0) AS scale_roas, bulk_move_day
FROM creative_stage_moves
WHERE moved AND scaling_first_day > data_through - 30
ORDER BY scale_spend DESC;
```

`creative_stage_moves` (Meta), `creative_stage_moves_upstack_nc`, `creative_stage_moves_upstack_all` (tables since 016, same names and columns as 015's views): one row per creative that ever spent; every column is described in `docs/DATA_SCHEMA.md` (and as `COMMENT ON` in the database; the text lives in `docs/schema/descriptions.py`, migration 016 carries the generated comments). Ad-level detail (which ad sets a creative scaled in) is `creative_scaling_placements*` (= `ad_daily` x `meta_ads` x `meta_campaigns` where `stage = 'scaling'` and `day >= scaling_first_day`); the API's `placements` are exactly that and add up to `scale_spend`.

## Speed and staleness

Migration 015 evaluated the view per request: 6 to 20+ seconds, and windows past about 30 days hit the 20 s gateway limit. 016 precomputes (see top). Every request answers from indexed tables; the endpoint also caps its own queries at 8 s (`QUERY_TIMEOUT_MS`) and answers a JSON error instead of hanging, and the page shows an error with Retry after 15 s. The tables are as fresh as the last nightly run: `/api/promotions` returns `stage_moves.built_at` and `data_through`, the page prints them, and a note appears when they are older than the latest day of data. If the nightly `etl.stage_moves` step fails the deployment goes red and the page keeps the previous rows.

## Caveats

- A move is inferred from spend, so a creative the buyer relaunches under the same name after a pause can look like a move; a creative scaled from a copy with a different name is a different creative.
- Creatives are tracker families: a creative scaled in five ad sets is one row.
- Recent launches have had no time to scale: read % reached scaling together with the launch week funnel.
- Scaling since the move covers however many days the creative has been in scaling: a move from yesterday has one day. The Held up? call waits for $200 and 3 days.
- `unclassified` stage (days with no campaign) is carried in `unclassified_spend` and `path = 'unclassified_only'`; none exists in the data today.

## Tests

`tests/api/test_api_promotions.py` (own throwaway database built through the real ETL builders: moved, never moved, scaling only, same day, scaling first, truncated launch, a 45-creative bulk day, window edges, the three bases, filters, series, ad-level tie-out), `tests/ui/test_promotions.py` (mock server `tests/ui/mock_promotions.py`, which runs the real payload builder `app.promotions.assemble` over made-up creatives; 375 px and 1,440 px screenshots land in `tests/ui/shots/`).
