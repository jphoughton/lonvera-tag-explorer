# Landing pages: copy store and top ads per page

What it is: every page a Meta ad sends people to, the copy on that page (kept as versions), and which ads drive each page. Migration `207_landing_pages.sql`, job `python -m etl.landing_pages` (`etl/landing_pages.py`), read-only API `GET /api/landing-pages` and `GET /api/landing-pages/<id>` (`app/landing_pages.py`). No page in the UI yet.

## Tables

| Table | Holds |
|---|---|
| `landing_pages` | one row per normalised page address (https, lower-case host, tracking parameters and `#` part removed, no trailing slash), first/last seen, last read, last HTTP status, last error, `current_version_id` |
| `landing_page_versions` | the copy: title, meta description, ordered text `blocks` (tag, heading level, text), `ctas` (button and link text), `prices`, `guarantees`, `reviews`, image alt text, `full_text`; a new row only when the text hash changes |
| `landing_page_ads` | ad id to page, how the link was found (`creative`, `post`, `preview`), first and last seen |
| `landing_page_ad_checks` | ads already looked at, so an ad with no link is not asked about every night (looked at again after 14 days) |
| view `landing_page_top_ads` | per page, window (7 or 30 days) and ad: spend, Meta-reported purchases, revenue, ROAS, rank by spend |

## How ads are tied to pages

For each ad with spend in the last 30 days (`ad_daily`), in this order:

1. The ad creative's own links (Graph `creative.object_story_spec` link, call to action link, carousel cards; `asset_feed_spec.link_urls`).
2. Ads that reuse an existing Page post carry no link on the creative. The post's call to action link is read, which works for posts of other Pages but not for our own Page (Meta answers error 10, the token lacks the `pages_read_engagement` permission).
3. For those, the ad preview (`/<ad id>/previews`) is fetched and its Facebook link shim (`l.facebook.com/l.php?u=...`) is decoded. This is one Graph call plus one web request per ad (about 3.5 seconds), so a run reads at most `--max-previews` (default 150) of them, biggest spenders first. Ads left over are picked up on later runs, because only unchecked ads are tried.

Only Facebook link shims count in a preview; the rest of that page holds unrelated Facebook links.

## How page copy is read

Plain public GET (one request per second, browser-like User-Agent, 20 second timeout), parsed with the Python standard library. The pages checked so far (`shop.lonvera.com/bl/sp`, `lonvera.com/pages/...`) are rendered on the server, so the plain read gets the full copy. If a page's server HTML holds under 120 words it is read again with headless Chromium (Playwright, already used by `etl.competitor_ads`) and `rendered_in_browser` is set; `--no-render` turns that off. Text that only appears after a click, or inside an iframe, is not captured. The hash covers text only, so a changed picture with the same alt text is not a new version. One page failing never stops the run (`landing_pages.last_error`).

## Run it

```
python -m etl.landing_pages --dry-run               # reads and parses, writes nothing
python -m etl.landing_pages                          # ads of the last 30 days, then every page not read in the last 20 hours
python -m etl.landing_pages --no-pages               # only map ads to pages
python -m etl.landing_pages --no-ads --max-pages 5   # only read pages
python -m etl.landing_pages --refresh-ads            # look at every ad again (creatives rarely change)
```

Needs `DATABASE_URL`, and a Meta token (`META_ACCESS_TOKEN`) for the ad step. Runs are logged in `etl_runs` (job `landing_pages`).

**Follow-up, not done:** the job is NOT in `scripts/deploy/run_etl.sh`. When added it should be non-fatal like `etl.competitor_ads` (`python -m etl.landing_pages || echo ... non-fatal`), after `run_daily` so new ads exist. First production run: expect about 1,400 preview reads (about 80 minutes) for ads on our own Page's posts, so run it once by hand with a high `--max-previews` or let the nightly run work through them at 150 a night. Whether Chromium is installed on the `etl` service decides whether browser rendering is available there (not needed for the pages seen so far).

## Top ads per landing page (SQL)

```sql
-- top 5 ads by spend per page over the last 30 days
SELECT url, rank_by_spend, ad_id, ad_name, round(spend) AS spend, purchases, round(revenue) AS revenue, roas
FROM landing_page_top_ads WHERE window_days = 30 AND rank_by_spend <= 5 ORDER BY url, rank_by_spend;

-- copy of the current version of one page
SELECT p.url, v.title, v.word_count, v.prices, v.guarantees, v.full_text
FROM landing_pages p JOIN landing_page_versions v ON v.id = p.current_version_id WHERE p.url = 'https://shop.lonvera.com/bl/sp';

-- what changed on a page: every version in order
SELECT v.id, v.fetched_at AS first_seen, v.word_count, v.prices FROM landing_page_versions v JOIN landing_pages p ON p.id = v.landing_page_id
WHERE p.url = 'https://shop.lonvera.com/bl/sp' ORDER BY v.fetched_at;
```

API: `GET /api/landing-pages?window=7|30&top=5` (pages ordered by spend, each with title, word count, price count and top ads) and `GET /api/landing-pages/<id>` (full copy of the current version, all versions, top ads). Login or a read key, like every other GET.

## Caveats

- Spend, purchases and revenue are what Meta reports (all customers). For Upstack new-customer numbers join `analytics.meta_ad_daily` on `ad_id`.
- An ad whose creative lists several links appears under each page with its full spend; do not add spend across pages.
- Variants of one page by query string are separate rows only when the parameter changes which page loads; tracking parameters (`utm_*`, `fbclid`, `trybe`, and others listed in `TRACKING_PARAMS`) are dropped. Two addresses that redirect to one page (for example `lonvera.com/pages/lonvera-grassfed-beef-liver-landing-a`, which redirects to `try-beef-liver`) stay two rows, with `final_url` showing the redirect.
- The window is the last N Pacific calendar days including today, which is a partial day.
