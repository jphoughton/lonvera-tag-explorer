# Test to scale: creatives that moved from Testing to Scaling

Page `/promotions` (top nav: **Test to scale**), endpoint `GET /api/promotions`, SQL view `creative_stage_moves` (migration `015_creative_stage_moves.sql`).
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

## SQL view, for agents

```sql
-- creatives that moved in the last 30 days, biggest scaling spend first (Meta's numbers)
SELECT display, product_slug, first_day, scaling_first_day, days_in_test, test_spend, test_revenue / NULLIF(test_spend, 0) AS test_roas,
       scale_spend, scale_revenue / NULLIF(scale_spend, 0) AS scale_roas, bulk_move_day
FROM creative_stage_moves
WHERE moved AND scaling_first_day > data_through - 30
ORDER BY scale_spend DESC;
```

`creative_stage_moves` (Meta), `creative_stage_moves_upstack_nc`, `creative_stage_moves_upstack_all`: one row per creative that ever spent; every column is described in `docs/DATA_SCHEMA.md` (and as `COMMENT ON` in the database; the text lives in `docs/schema/descriptions.py`, migration 015 carries the generated comments). Ad-level detail (which ad sets a creative scaled in) is `ad_daily` x `meta_ads` x `meta_campaigns` where `stage = 'scaling'` and `day >= scaling_first_day`; the API's `placements` are exactly that and add up to `scale_spend`.

## Caveats

- A move is inferred from spend, so a creative the buyer relaunches under the same name after a pause can look like a move; a creative scaled from a copy with a different name is a different creative.
- Creatives are tracker families: a creative scaled in five ad sets is one row.
- Recent launches have had no time to scale: read % reached scaling together with the launch week funnel.
- Scaling since the move covers however many days the creative has been in scaling: a move from yesterday has one day. The Held up? call waits for $200 and 3 days.
- `unclassified` stage (days with no campaign) is carried in `unclassified_spend` and `path = 'unclassified_only'`; none exists in the data today.

## Tests

`tests/api/test_api_promotions.py` (own throwaway database built through the real ETL builders: moved, never moved, scaling only, same day, scaling first, truncated launch, a 45-creative bulk day, window edges, the three bases, filters, series, ad-level tie-out), `tests/ui/test_promotions.py` (mock server `tests/ui/mock_promotions.py`, which runs the real payload builder `app.promotions.assemble` over made-up creatives; 375 px and 1,440 px screenshots land in `tests/ui/shots/`).
