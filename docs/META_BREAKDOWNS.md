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

* First start: stores the backfill anchor (`--until` = yesterday, `--max-days` = 365) in `meta_breakdown_progress`. Windows are 30 days (`--window-days`), newest first, each fully committed
  before the next older one; coverage `first_day` moves back window by window. Progress is recorded per window x set x level x account, so every run resumes.
* **Run order.** Each run does, in this order: (1) the recent-days catch-up, (2) the backfill.
  * *Catch-up*: once a day, the days since the last catch-up (at least the trailing 7, which Meta restates), for every set, cheap sets first. It is resumable and done at most once a day per
    set: each set x level x account is stamped `refreshed on day D` (rows `__catchup__<set>`, level `<level>:<D>` in `meta_breakdown_progress`), a set stamped for the day is skipped, and the
    "caught up" marker is written only when every set is stamped. A catch-up that does not finish (budget, quiet hours) resumes in the next run instead of starting from the top. It gets
    at most 40% of a run (`CATCHUP_SHARE`); when that share is used up the run says so and goes on to the backfill, so the backfill always has the other 60% or more.
  * *Backfill*, in four stages, each resuming from recorded progress:
    1. every set **except** `comscore_market`, `region`, `hourly_stats_aggregated_by_audience_time_zone`, `hourly_stats_aggregated_by_advertiser_time_zone` ("light" sets; cheap first, the other
       tier-2 sets `dma`, `zip` ... last inside a window), newest 30-day window;
    2. **one probe pass** of the four heavy sets on that same newest window, attempted once (marker `__heavy_probed__`), capped at 25% of a run (`PROBE_SHARE`); it exists to learn about
       slowness, async needs and rate limits. Whatever it did not finish is kept (progress is per set x account) and resumed in stage 4; it never holds up the light sets;
    3. the light sets through all older windows out to 365 days;
    4. only when stage 3 has completed, the four heavy sets through all windows out to 365 days.
* **Sets that are dead.** A (set, level) Meta refuses (code 100) is asked again after 7 days (`RECHECK_UNSUPPORTED_DAYS`), not every run; sets refused at all three levels cost no request in
  between. A set whose response rows never carry the requested keys (`user_persona_*`, `rule_set_*`, `std:conversion_destination` ...) is **quarantined** for 7 days at once; a set that fails with
  anything else and writes no rows is quarantined after 3 failing runs in a row (`FAIL_LIMIT`); a clean pull clears the count. Quarantine rows are `__quarantine__:<set>` / `__fails__:<set>`
  in `meta_breakdown_progress` (named with `--sets`, a set is always asked). A quarantined `failing` set keeps the backfill from being marked finished; a `shape` set does not.
* **Transient failures** (HTTP 5xx, code 2 "Service temporarily unavailable"): the retry waiting one set pull may spend without a single request getting through is capped at 90 s
  (`TRANSIENT_BUDGET_S`: three retries of 15, 30, 45 s; the allowance is refilled by every request that succeeds, so a long productive pull with sporadic errors is not cut off). It is shared
  by both accounts; when it is used up the set is an error for this run and the next run tries again (before: 7 retries per request, up to 14 minutes per set). Request windows (7 days, or 1 day
  for heavy sets) that were committed inside an unfinished window are recorded as they finish (rows in `meta_breakdown_progress` with the chunk's own dates), so the next run does not pull them
  again. When a set has already delivered rows in this pull but one request window keeps failing (budget used up), that window is split in two, down to one day and then an async report (at most 4 such steps per set pull); a set that has delivered nothing just fails fast. Rate limits are separate: the run waits and continues as before. Async report jobs are polled every 5 s for at most 10 minutes.
* **Too big for one day** (`action_destination` ...): the first time a one-day request is refused as too big the set is remembered (`__async__:<set>`) and from then on is requested as an async
  report straight away, with no plain attempts and no halving (before: three failed plain requests per window plus one async job per day). The async job itself runs on Meta's side
  (about 80-90 s per day for `action_destination`); if an async job for several days fails, the span is halved.
* **Log lines**: the per-set lines are unchanged. Each phase also logs `run start: phase=<catch-up | backfill/light-newest-window | backfill/heavy-probe | backfill/light | backfill/heavy> window=<from..to>
  sets_done=N sets_remaining=M` and the run ends with `run end: phase=... window=... sets_done=... sets_remaining=... backfill_advanced=yes|no` (yes when new backfill window records were written this run).
  The heavy probe also logs one `heavy probe:` line with each set's status, rows and seconds.
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

## Funnel steps and the `why` breakdown step (migration 255, 2026-11-13)

* `meta_breakdown_daily` carries `add_to_cart` and `initiate_checkout` (Meta-reported, `omni_*` then plain then pixel action type, the same rule as purchases; NULL = not pulled yet, never 0; action-breakdown sets leave them NULL). The pull and the daily catch-up fill them with the rest of the row; a re-pull never blanks a value it already has (COALESCE).
* Rows pulled before the columns existed: `python -m etl.meta_breakdowns --fill-funnel --since 2026-10-12 [--until D] [--sets 'age;gender;publisher_platform+platform_position']` (default sets: the seven cuts the `why` step uses). Fill-only and resumable: one weekly request window per set at ad level (read-only GETs), an UPDATE of those two columns on existing rows that still have `add_to_cart` NULL, nothing inserted, nothing else written; a window with nothing left to fill costs no request. Own advisory lock (a fill may run beside the `breakdowns` cron), quiet hours and `--max-minutes` as in the main pull; recorded in `etl_runs` as `meta_breakdowns_fill`.
* `analytics.meta_breakdown_daily` now exposes thirteen breakdown names (the ten plus `impression_device`, `placement` = publisher|position and `placement_device` = publisher|position|device) and the columns `add_to_carts`, `initiated_checkouts`. `platform_position` alone is refused by Meta (it needs `publisher_platform`), so position-level reads use `placement`.
* **The `why` breakdown step** (`brain/why_breakdowns.py` arithmetic, `brain/why_breakdown_step.py` SQL and ledger join, called from `Why.finalize`; the answer key is `breakdown_changes`, also in the compact answer). For the same target / comparison windows as the rest of `why`, for each of publisher platform, placement, impression device, device platform, age, gender and country (top 12 segments by spend, the rest as `(other)`), it takes CPM, link click rate, cost per link click, link click to add to cart, add to cart to initiated checkout, initiated checkout to purchase, add to cart to purchase and cost per purchase, and splits the aggregate change exactly into per-segment mix (share of the denominator moving) and within-segment rate shift (midpoint form: mix = (w1 - w0) x (r avg - R avg), rate = (r1 - r0) x w avg; the two sum to the aggregate move to rounding, a new or vanished segment is all mix). Material = segment at 3% or more of spend and either 10% of the aggregate move or 1 point of the metric (2% of the level for dollar metrics). Controls: the same weekdays of the prior four weeks (windows shifted by 7 / 14 / 21 / 28 days, median, 3 complete windows needed; a window of 7+ days uses the adjacent windows of its own length, 2 needed; a control window that includes a day the breakdown was not loaded on is skipped) and the pooled prior seven days; noise = standard deviation of the segment's own daily ratio over the 28 days before the window, divided by the square root of the window's days; verdict 'moved beyond noise' only when the window's ratio is outside 2 noise units of BOTH controls on the side the move went, else 'inside noise' (or 'not enough history' under 14 valid days or fewer than 3 weekday windows). Spend-share shifts of 1 point or more are listed the same way. Funnel metrics are skipped, with a gap line, when any day of either window is not filled. The block reports how many comparisons ran and how many would pass one 2-sigma test by chance. Each mover is joined to `meta_change_ledger` / `meta_change_log` (people-made events only: bid strategy, budget, run status, targeting, optimisation goal, ad set creation, ad creation and status) on the ad sets (and their campaigns) that carry most of its spend, within one day of the window, structure changes first, as 'coincident with', never cause; the account-wide counts of those records and the landing page / Klaviyo / feed events already in `changes_in_window` ride along.
* Basis: Meta-reported only. Breakdown rows carry no Upstack new-customer attribution, so none of these ratios is an NC ROAS or NC CPA. Breakdown history starts 2026-10-12 and moves back with the backfill; the step needs 14 valid days of history per segment for a verdict. `why` runs the step for every metric except Google, repeat / Klaviyo / Amazon ones, and only when `max_steps` is at least 20 (it adds up to 14 queries of its own).
