# Meta breakdowns

Every breakdown the Meta Insights API offers, pulled at ad x day grain, stored in Postgres and served read-only. Code: `etl/meta_breakdowns.py` (pull), `app/meta_breakdowns.py` (read API),
migration `240_meta_breakdowns.sql`. Meta access is read only: GET requests through `etl/meta_api.py`; the only other call is the standard async report run (`POST /act_<id>/insights`,
creates a report, writes nothing to the ad account), used only when a one-day plain request is too big or keeps timing out. The run stats list the sets that needed it (`async_sets`).

## Tables and view

| Object | What it holds |
| --- | --- |
| `meta_breakdown_daily` | One row per day x ad account x level x entity x breakdown set x dimension values. Partitioned by month on `day` (partitions 2025-01 to 2028-12 exist; the pull creates later ones). Columns: ids (`entity_id`, `campaign_id`, `adset_id`), up to three dimension values (`dim1` to `dim3`, in the order of the names in `breakdown_set`), `dims_key`, spend, impressions, reach, clicks, link clicks, outbound clicks, unique outbound clicks, purchases, purchase value. |
| `meta_breakdown_coverage` | One row per set x level: status (`ok`, `unsupported`, `error`), first and last day held, row count, last error. Updated after every window. |
| `meta_breakdown_progress` | One row per finished backfill window x set x level x account (resume bookkeeping). |
| `analytics.meta_breakdown_daily` | Read-only view for agents: ten everyday breakdowns, one row per day x ad x breakdown x value (no aggregation above ad). Columns: day, account, campaign_id, adset_id, ad_id, breakdown, value, spend, impressions, reach, link_clicks, unique_outbound_clicks, purchases, purchase_value. Breakdowns: audience_segment (user_segment_key), age, gender, age_gender (value `age|gender`), country, region, publisher_platform, platform_position, device_platform, hourly (advertiser time zone). |

## Grain

Every set is requested at **ad** level. Only when Meta itself refuses a set at ad level is it requested at **campaign** level, then **account** level; the refusal and its reason stay in
`meta_breakdown_coverage` (a row at level `ad` with status `unsupported`), and the rows land at the lowest level Meta accepts. In the view, `ad_id` and `adset_id` are NULL only for those
fallback rows (and `campaign_id` too at account level).

## Sets

The catalog (`python -m etl.meta_breakdowns --list`) holds every breakdown in the Graph API version `etl/meta_api.py` uses, as listed by Meta's own SDK enums, plus combinations:

* Cheap, pulled first in every window: `user_segment_key`, `age`, `gender`, `age,gender`, `country`, `publisher_platform`, `platform_position`, `publisher_platform,platform_position`, `impression_device`, `device_platform`.
* Normal: `frequency_value`, `is_conversion_id_modeled`, `landing_destination`, `place_page_id`, `product_id`, the creative asset sets (`image_asset`, `video_asset`, `title_asset`, `body_asset`,
  `description_asset`, `call_to_action_asset`, `link_url_asset`, `ad_format_asset`, flexible and generated asset types), the `media_*` sets, app, messaging, `mmm`, `skan_*` and the other documented
  names. Many of these are internal or need a special product; Meta refuses them and coverage says so.
* Action breakdowns (they split purchases and purchase value only; spend, impressions and clicks are NULL): `action_device`, `action_destination`, `action_reaction`, `action_video_sound`,
  `action_video_type`, `action_carousel_card_id`, `action_carousel_card_name` (and the pair), `action_canvas_component_name`, `action_target_id`, `conversion_destination`,
  `signal_source_bucket`, `standard_event_content_type`, `matched_persona_id`, `matched_persona_name`, `is_business_ai_assisted`. Three names are both a standard and an action breakdown at Meta; the
  standard reading is stored as `std:conversion_destination`, `std:signal_source_bucket`, `std:standard_event_content_type`.
* Heavy, pulled last in every window and one day per request: `region`, `dma`, `zip`, `comscore_market`, `affiliate_click_region`, both hourly sets, `impression_view_time_advertiser_hour_v2`,
  `publisher_platform,platform_position,impression_device`.

A request Meta refuses with code 100 is retried without `reach`, then with the core fields only, before the set is called unsupported at that level.

## Running it

A dedicated Railway cron service, **`breakdowns`**, runs `scripts/deploy/run_breakdowns.sh` every 2 hours (`0 */2 * * *`): `etl.migrate`, then `python -m etl.meta_breakdowns --auto`. It is
separate from the main `etl` service, so the nightly never waits for it and never shares its time budget. No arguments are needed:

* First start: stores the backfill anchor (`--until` = yesterday, `--max-days` = 365) in `meta_breakdown_progress`. Newest 30-day window first (`--window-days`), every set in a window (cheap
  first, region / dma / hourly last) committed before the next older window starts; coverage `first_day` moves back window by window. Progress is recorded per window x set x level x account.
* Every run first catches up once a day: the days since the last catch-up (at least the trailing 7, which Meta restates), so recent data stays current during a multi-night backfill. Then it
  continues the backfill; when that is finished it only does the daily catch-up (trailing 7 days).
* Quiet hours: it never starts a request batch between 12:30 and 15:30 UTC (the main nightly Meta pull; override `META_BREAKDOWNS_QUIET_UTC=HH:MM-HH:MM`, `off` disables). At the boundary it
  stops cleanly after committing and exits 0.
* Budget and overlap: `--max-minutes` (default 110) ends a run cleanly with exit 0, so the next cron run resumes; a Postgres advisory lock makes an overlapping run exit 0 with one log line.
* Rate limits: it waits (15 minutes per wait, up to 24, never into the quiet hours) and continues. Exit code is non-zero only for a failed migration, a rejected Meta token or waits that ran out.
* If Meta refuses an older window for a set it already served (its own retention limit), coverage keeps the earliest day served with the reason and the set stops there.
* `--restart` forgets the anchor and all progress and starts over. `--sets 'age;age+gender'`, `--dry-run`, `--list`, `--ignore-quiet` for manual runs.
* Manual explicit backfill (no stored anchor): `python -m etl.meta_breakdowns --since 2026-01-01 --until 2026-11-09` (same windows, same resume; pass the same `--until`).
* `--probe` (read only: one request per set for one day on the main account, with row estimates per day; needs a local Meta token).

The job records itself in `etl_runs` as `meta_breakdowns` (no row for a quiet-hours or lock-skipped start) and is watched by `etl/alerts.py` (`job:meta_breakdowns`, alert after 9 hours without a
successful run, silent until the first row exists).

## Read API (GET, behind the app login)

* `GET /api/meta/breakdowns/coverage`: what exists per set and level.
* `GET /api/meta/breakdowns?set=age,gender&start=2026-11-01&end=2026-11-07&level=ad&product=vita-bull&campaign_id=...`: rows aggregated over the range by the set's dimensions: spend,
  impressions, clicks, link clicks, outbound clicks, purchases, revenue, ROAS, cost per purchase, cost per 1,000 impressions, click rate, link click rate. Default range: the last 7 days held.
  Up to 1,000 rows, highest spend first. The product filter uses the same product rule as the rest of the app (`_pfa`); a campaign that runs several products counts whole under each.

## Caveats

* Purchases and revenue are Meta-reported: they count returning customers and overlap with Google and Upstack. Never mix them with Upstack new-customer numbers in one ratio.
* Reach and unique outbound clicks are per row (one ad, one day, one value). They are stored but never summed by the API, and must not be summed across days, ads or sets. The API returns no reach or frequency.
* Different sets are different cuts of the same spend: add within one set only.
* Each ad account buckets days in its own time zone (Main Pacific, BackUp Eastern); the last 3 to 7 days still move (the nightly pull re-pulls 7).
* Meta can return purchases on a day with no spend (view-through); those rows are kept.
* Sets whose Insights data Meta serves for a shorter period than 365 days stop at the earliest day served; coverage says where.
