# Marketing metrics: renamed section, channel switch, Google from Upstack

## Rename
The section formerly called **Tags** (top bar `Lonvera | Marketing metrics | Projections`, page titles, headings, breadcrumb root, login heading) is now **Marketing metrics**. URLs, route names and code identifiers are unchanged
(`/`, `/p/<slug>`, `/ad/<id>`, `tag_set` ...), so bookmarks and the API keep working. The product page's inner **View by Tags | Campaigns** switch keeps its name: it really is the tag hierarchy view.

## Channel switch
`Channel: Meta | Google` is the first control in the header bar. Meta is the existing pages. **Google** is a new page at `/google`. The date range, comparison and the Numbers-from choice carry over when switching;
Stage, Active only and the Meta trend picks are Meta-only and are dropped. On the Google page the stage chips and the Meta-vs-Upstack tie-out button are hidden.

## What the Google page shows
Campaign level (Google has no creatives, so no tag tree): a trend chart (Bars: spend, purchases, revenue, clicks, impressions; Line: ROAS, CPA, CTR, CPC, conv rate; Day | Week; comparison drawn as outline bars),
a **Campaign types** table (Shopping / Search / Performance Max / Demand Gen / Other; a row filters the campaigns table with `type=`), and a **Campaigns** table (status, type, `not in the Google feed` badge).
Columns: spend, % of spend, impressions, clicks, CTR, CPC, CPM, conv rate, purchases, revenue, ROAS, CPA, with the same per-day and comparison (A vs B) behaviour as the Meta tables.

**Numbers from** (same switch as the Meta pages; `source=meta` is the default and here means Google's own reporting):

| Switch | Purchases / revenue | Source |
|---|---|---|
| Google (default) | purchases and conversion value as Google counts them | Upstack's ad-platform feed (`query-channel-details`, Google rows, campaign grain) |
| Upstack · new customers (`source=upstack`) | first orders and their revenue | Upstack any-click attribution, 30-day window, `utm_source = "Google Ads"`, keyed by utm campaign name |
| Upstack · all orders (`source=upstack&basis=all`) | every credited order and its revenue | same |

Spend, impressions and clicks are Google's in all three (clicks are all clicks; Google has no link-click event). Revenue is gross (Upstack's order total, tax included, before refunds; Google's conversion value as Google reports it).
The banner states which one is showing. Any-click credits an order to every campaign clicked in the 30 days before it, so Upstack rows overlap and can add up to more than the real orders.

**Campaign type** is read from the campaign name because Upstack exposes none (`etl.upstack_google.campaign_type`): `PMax` is Performance Max, `DG...` / `IL | Demand Gen` is Demand Gen, `Standard Shopping` / `SS |` is Shopping,
`Brand Search` / `Prospecting Search` / `Competitors Search` / `IL | Search` is Search; anything else (for example `Prospecting | Top 3 Products | ...`) is **Other**, never guessed.

## Data
Migration `010_upstack_google.sql` (additive): `upstack_google_campaigns`, `upstack_google_channel_daily`, `upstack_google_attr_daily`, `upstack_google_days`, and the derived `google_daily` (one row per day and campaign, rebuilt by every run).
`etl.upstack_daily` pulls it after the Meta side, in its own transaction (a failure on one side never rolls back the other; either failing marks the run failed):

```bash
python -m etl.upstack_daily                                   # nightly: Meta side + Google side, trailing 7 days
python -m etl.upstack_daily --google-only --google-since 2025-05-13 --until 2026-11-03    # Google backfill (attribution from 2025-05-13; channel feed from --google-since)
python -m etl.upstack_daily --build-only                      # rebuild Meta and Google derived tables from the stored raw pulls
```
The Google job refuses a day with more than $200 of Google spend and no attributed Google orders at all (an empty Upstack answer, not a quiet day).
API: `GET /api/google/rollup?group=type|campaign[&type=]` and `GET /api/google/trend?grain=day|week[&type=][&campaign=]`, same window / compare / source / basis params as the Meta endpoints; `/api/status` gains `sources.google`.

## Depth and backfill (loaded 2026-11-04)
- **Upstack attribution (Google Ads source): from 2025-05-13**, the same day Upstack's Meta attribution starts (nothing before it, probed). 540 days loaded, 2025-05-13 to 2026-11-03, one call per day.
- **Google's own numbers (channel feed): from 2024-03-25**, the first day with Google spend (asked from 2023-01-01; nothing earlier). Zero-spend days are not stored.
- 90 Google campaigns, 8,851 channel rows, 4,835 attribution rows, 9,386 `google_daily` rows. Upstack mode starts 2025-05-13 on the page (stated in the banner); Google mode starts 2024-03-25.
- Attribution rows whose utm campaign is no Google campaign (`(not set)`, raw ids, deleted campaigns): 575 of 18,391 orders (3.1%) and 377 of 10,180 new customers (3.7%) over the whole history. They stay visible as `(utm campaign not in the Google feed)` rows so the totals tie to Upstack's Google total.

## Last 30 days on the page (2026-10-05 to 2026-11-03)
| Numbers from | Spend | Clicks | Purchases | Revenue | ROAS | CPA |
|---|---|---|---|---|---|---|
| Google (reported) | $17,573 | 5,285 | 338.8 | $24,692 | 1.41 | $51.87 |
| Upstack · new customers | $17,573 | 5,285 | 209 | $15,396 | 0.88 | $84.08 |
| Upstack · all orders | $17,573 | 5,285 | 536 | $37,447 | 2.13 | $32.79 |

By type (Google-reported): Shopping $11,459 / ROAS 1.45, Search $2,994 / 1.64, Demand Gen $1,746 / 0.57, Other $934 / 1.64, Performance Max $440 / 1.54. Upstack's any-click credits Google with far more orders than Google claims
(536 against 339) because brand-search and shopping clicks that happen near a Meta click are credited too; new-customer ROAS (0.88) is the stricter read.

## Cross-checks (Google spend)
`google_daily` (Upstack's campaign grain, summed by day) against (a) the platform-grain Upstack feed that the projection `feeds` service pulls into `proj_feed_platform` / `proj_feedx_platform` (same API call, run directly; the production tables were not queried) and
(b) the Lonvera Projections sheet tab `Platform - History`, Google Ads rows:

| Window | Spend: campaign sum vs platform feed | vs sheet | Days differing by > $1 and 0.5% | Purchases vs feed / sheet | Clicks |
|---|---|---|---|---|---|
| 30d | $17,573.43 vs $17,573.46 | vs $17,573.44 | 0 of 30 | 338.82 vs 338.82 / 337.82 (+0.3%) | identical |
| 90d | $69,558.90 vs $69,555.97 (+0.004%) | vs $69,555.95 | 0 of 90 | 1,228.6 vs 1,240.4 (-1.0%) / 1,237.9 (-0.8%) | 24,543 vs 24,532 |
| 2025-01-01 to 2026-11-03 | $617,430.87 vs $617,428.02 (+0.0005%) | vs $617,428.00 | 0 of 672 | 14,303.0 vs 14,319.5 (-0.12%) / 14,317.0 (-0.10%) | 254,125 vs 254,114 |

Spend matches to the cent on every day. Purchases and conversion value differ by up to about 1% on a handful of days (5 of 672 for purchases): Google restates conversions for days after the fact and this pull is newer than the feed and sheet snapshots. Not forced equal.

## Known limits
- Upstack exposes Google campaigns, ad groups (`adset` dimension) and ads, but no keywords and no campaign type. Only campaign grain is loaded.
- Campaign type comes from the name; `Prospecting | Top 3 Products | ...` says none and is Other (2 campaigns, 5% of spend in the last 30 days).
- Google's new-customer measures are empty in the channel feed (0 for Google), so there is no Google-reported new-customer view; the Upstack switch is the new-customer read.
- Compare windows, per-day values and the trend chart follow the Meta pages; stage and Active only do not exist for Google.
