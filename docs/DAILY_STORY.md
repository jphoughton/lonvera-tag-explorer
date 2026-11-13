# Daily story

Every morning one very concise explanation (170 words or fewer) of why yesterday's performance was what it was, built from every daily metric in the database, plus a pattern memory that judges patterns over time. Page `/daily`, API `/api/daily-story?day=YYYY-MM-DD`, analytics views `daily_story`, `daily_story_facts`, `daily_story_patterns`, `daily_story_pattern_scoreboard`, `daily_story_metric_registry` (domain `story`).

## Definitions (Josh's rules)

- Headline = new-customer return on ad spend (NC ROAS) = `orders_daily.new_customer_revenue / (Meta + Google spend)` of the same Pacific day. Ad-attributed ROAS (sum of `meta_ad_daily.upstack_new_customer_revenue`) is only a cut for comparing ads, products and structures. Meta-reported ROAS is a side data point.
- Baseline = the same weekday in the previous four weeks (fewer early on; `n_baseline` says how many). The metric table shows the median and the pooled value; the exact decomposition uses the pooled value. Noise = sample standard deviation of the previous 28 days; flag at |z| >= 2.
- Decomposition (exact, in logs): NC ROAS = AOV x Meta share of spend x (1000 / CPM) x CTR x click-to-page-view x page-view-to-cart x cart-to-new-customer (cart-to-initiated-checkout and initiated-checkout-to-new-customer when the column exists in `meta_ad_daily`). Conversion = all new customers / Meta link clicks.
- Mix vs rate (exact): over product, stage, campaign structure (tROAS vs cost cap, 7-day vs 1-day click, read from the campaign name), market cluster, ICP, pain point, trigger, line, video vs image, format, vendor, concept age and ad family, on the ad-attributed ROAS.
- Creative: ad families are `coalesce(tracker_family_key, family_key, ad_id)`, counted with count(distinct); age = days since the first day of Meta spend; tags, format, vendor and hook come from the Creative Tracker's first row of each family.
- Klaviyo revenue is Klaviyo-attributed and Amazon is a separate channel: neither is added to NC revenue. Google spend is in the headline denominator.

## Pieces

`etl/daily_story_math.py` (arithmetic), `_registry.py` (metric registry), `_panel.py` (loads the data), `_facts.py` (metrics, decomposition, cuts, source sections), `_events.py` (change ledger; reads `meta_change_ledger` when it exists, else detects changes from the daily tables), `_narrative.py` (template, model step, number validation), `_patterns.py` (pattern memory), `daily_story.py` (job). Nightly: `scripts/deploy/run_etl.sh` runs `python -m etl.daily_story` after Klaviyo (non-fatal, etl_runs job `daily_story`). `deliver(story)` is an empty hook: Slack or email is not chosen yet.

## Model step

Order: `ANTHROPIC_API_KEY` (Messages API, `claude-sonnet-5-5`), else the headless `claude -p` CLI on `CLAUDE_CODE_OAUTH_TOKEN` (the route `etl.auto_tag` uses), else the deterministic template. `DAILY_STORY_LLM=0` turns it off. The model sees a condensed copy of the facts only; every number in its answer must be found in the facts or the template is stored instead (daily_story.note says why).

## Commands

- `python -m etl.daily_story` yesterday; `--day D` one day; `--backfill [--since 2026-05-06] [--llm-days D1,D2]`; `--dry-run --day D` prints, writes nothing.
- A day whose core source (Meta ads, Upstack orders, Google, Upstack Meta attribution) is not loaded gets a "data not ready" row naming it.

## Limits

No Meta change log (unless the feed exists), no checkout or payment events, no promotions or price changes, no site changes except landing page content hashes (history from 2026-11-11). Order mix starts 2026-07-16, placement 2026-10-12, Klaviyo 2026-07-13. Patterns are correlation, not cause.
