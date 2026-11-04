# Meta | Upstack source switch

Every number on the Tags pages (products, product, creative, ad pages, trend charts, campaigns, "not on the tracker") can be read from Meta's own reporting
(default) or from Upstack's attribution. The choice is in the URL (`source=meta|upstack`, `basis=nc|all`), set with the **Numbers from** switch in the header
bar, and the page always says which one it shows. Nothing changes unless it is flipped.

## What Upstack mode counts

| | Meta mode | Upstack mode |
|---|---|---|
| Spend, clicks, impressions | Meta Marketing API | **the same Meta numbers** (per ad and day) |
| Purchases | Meta-attributed purchases (returning customers included) | `basis=nc` (default): **new customers**, first orders only. `basis=all`: **every order** credited to the ad |
| Revenue | Meta conversion value | `nc`: new-customer revenue. `all`: revenue of every credited order. Upstack `orders.total_revenue` / `orders.new_customer_total_revenue` (order totals, tax included, before refunds) |
| ROAS, CPA | revenue / spend, spend / purchases | the same ratios on the Upstack counts (labelled NC ROAS / NC CPA for `nc`, ROAS / Cost per order for `all`) |
| Since launch | Meta all-time lifetime per ad | every day Upstack holds (history starts 2026-05-06, the app's `daily_start`); the page says so |
| Attribution | Meta's | **Upstack any-click, 30-day window**, `utm_source = "Meta Ads"`: an order is credited to the ads clicked in the 30 days before it |

Why this model: it is the one the sheet already uses for Meta-ad new customers (`Upstack Ads - Daily`, `Upstack Any-Click - By Campaign`, the pacing tabs' NC ROAS /
NC CPA), so the numbers can be read next to what Josh already reads. Last-click 7-day (the projection `attribution` feed) is exclusive per order but is not what the
ad-level sheet tabs use. Stage (Testing / Scaling), compare windows, "active only", per-day and every drill level work unchanged in both modes.

Why spend stays Meta's: Upstack's ad-level channel feed cannot be read completely (63,960 ads, only sortable by update time, no spend sort or platform filter; a
7-day read found 541 ads / $30.4K against Meta's 891 ads / $70.9K). Its campaign-level channel feed is complete and matches Meta's spend to the cent, so it is used
as the spend check in the tie-out. With the same spend in both modes, ROAS / CPA differences are purely attribution.

## How it is built

```
Upstack API ──etl.upstack_daily──> upstack_attr_daily        campaign x ad name x day (utm values as clicked): nc, nc_rev, orders, rev
  query-attribution (any-click,    upstack_campaign_daily    campaign totals (reconciliation)
  30d, Meta Ads) + channel feed    upstack_channel_daily     Upstack's own campaign spend / platform purchases (tie-out)
                                   upstack_attr_days         which days were pulled completely
                    │
   etl.upstack_build.build  ──>    ad_daily_upstack          one row per Meta ad and day: Meta spend/clicks/impressions + Upstack orders and new customers
                    │             upstack_unmapped_daily     attribution rows that match no Meta ad (in no ad/family/tag total)
   build_metrics.rebuild_source ─> per basis (nc, all): family_daily, family_daily_active, ad_metrics, unmatched_meta (+ views ad_daily, meta_ad_lifetime)
                                   named <table>_upstack_<basis>; built by the SAME SQL as the Meta tables with the names swapped
```

The API swaps table names per request (`queries.source_sql`, set in a `before_request` hook that always runs, so a worker thread never keeps the previous request's
source). Table names are whole tokens, so `family_daily` is never rewritten inside `family_daily_active` and column names are never touched. Migration `008_upstack_source.sql`
is additive.

### Mapping Upstack's names to Meta ads

Upstack reports campaign **names** and ad **names** (UTM values), not ad ids. Rules, in order: exact ad name inside the utm campaign (the campaign may be a name or a raw
campaign id), then the same ad family inside the campaign, then the exact ad name in any campaign, then the same family anywhere. When several Meta ads match (one ad copied
into several ad sets), the day's orders are split by each ad's Meta spend that day (else lifetime spend, else equally) and always sum back exactly, so family, tag and
product totals are exact and only the twin ads' own rows are an allocation. Rows matching nothing (`(not set)`, truncated ids, deleted ads, campaigns Upstack could not split by ad)
are kept in `upstack_unmapped_daily`, in no row, and reported by the tie-out.

## Daily job and backfill

`etl.upstack_daily` runs in the `etl` cron after `etl.run_daily` (`scripts/deploy/run_etl.sh`), pulls the trailing 7 complete Pacific days (always re-pulled), replaces them,
rebuilds `ad_daily_upstack` against Meta's freshly restated spend and rebuilds both bases, all in one transaction. An Upstack failure aborts before any write, leaves the last
good Upstack data in place, marks the run failed and exits 1; Meta's refresh is independent. It refuses a window where a day has no Meta-attributed orders while Meta spent
more than $500 (an empty Upstack answer, not a quiet day). Env: `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID` (set on the Railway `etl` service).

```bash
python -m etl.upstack_daily                                  # trailing 7 days (what the cron runs)
python -m etl.upstack_daily --since 2026-05-06 --skip-done   # backfill / resume (about 5-30 s per day; commits every 10 days)
python -m etl.upstack_daily --build-only                     # rebuild from the stored raw pulls, no API calls
python -m etl.upstack_daily --dry-run --days 3               # pull and report, write nothing
```

## Depth of Upstack's ad-level history

Attribution answers from **2025-05-13** (no Meta Ads orders before; a probe every 7 days from 2025-04-01 shows 0 orders until that week), at campaign and at ad (`utm_ad`) grain.
This app backfills from 2026-05-06 only, because that is where its Meta daily data starts and a day without Meta spend would show orders against no spend. Going earlier is a
backfill flag away (`--since`), but needs `daily_start` and Meta's own history to go with it.

BACKFILL_RESULTS_PLACEHOLDER

## Known limits

- Any-click credits an order to every ad clicked in the 30 days before it. Across ads the credits overlap, so ad-level orders can exceed unique orders in periods where people click
  several ads of one campaign; the tie-out shows the ad-level / campaign-level ratio. New customers are first orders only, which keeps the overlap small.
- Upstack credits some orders to a campaign but to no identifiable ad (the ad rows carry less than the campaign totals). Those are in no row.
- Revenue is Upstack's order total as the pacing sheet reads it (tax included, before refunds); Meta's conversion value may be defined differently, so revenue and ROAS are not
  directly comparable to the cent between modes.
- Days are Pacific for Upstack and each ad account's own day for Meta (Main = Los Angeles, BackUp = New York).
- Per-ad numbers inside a group of same-named twin ads are an allocation (see above); everything from the family level up is exact.
