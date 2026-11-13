# Competitor ads (`python -m etl.competitor_ads`)

Weekly, unattended: what competitors run on Meta right now, classified into Lonvera's taxonomy. Feeds the Creative priorities one-sheeter through the view `competitor_concepts`.

## Method (and what does not work)
- **Meta Ad Library API (`/ads_archive`): not usable.** With our token it answers error 10 / subcode 2332002 "Application does not have permission for this action" (identity confirmation for the Ad Library API is required, and it does not serve commercial US ads anyway). Checked 2026-11-09.
- **Works: headless Chromium (Playwright) on the public Ad Library page**, no login: `ads/library/?active_status=active&country=US&q="<phrase>"&search_type=keyword_exact_phrase&sort_data[mode]=total_impressions`. Scrolls (2.2 s pauses) until ~150 cards, 6 s between searches. Chromium is installed in the Dockerfile (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`).
- `view_all_page_id` lookups return "No ads match" headless for the page ids visible in cards, so brands are found by exact phrase (brand name, landing domain) and filtered by an advertiser-name regex; creator ads "X with Brand" are dropped.
- No paid scraper is used. No cost beyond Claude plan usage for classification.

## Flow
1. `competitor_brands` (editable; seeded from `BRANDS` in `etl/competitor_ads.py`): search phrases + `name_match` regex. Add a brand by inserting a row.
2. Each run writes `competitor_ads` (one row per Library ID per week, `week_start` = Monday Pacific). A brand that fails (wall, age gate, zero cards without an empty-result message) keeps its last good rows, copied forward with `carried_from` set; the view shows `is_stale`.
3. New `concept_key`s (brand + hash of the first 160 characters of normalised copy) are classified by headless Claude (`CLAUDE_CODE_OAUTH_TOKEN`, text only, 8 concepts per call, cap `--limit` 150): an existing line, `NEW` (suggested cluster / ICP / pain point) or `OUT`. Cached in `competitor_concept_class`, never re-asked.
4. Nightly `scripts/deploy/run_etl.sh` calls `etl.competitor_ads --if-sunday` after `etl.amazon_mtd` and before `etl.auto_tag` (non-fatal).

## Limits
No spend, impressions or ROAS are visible. `days_running` (start date to the day read) and `variant_count` are the only strength proxies; start dates reset when a brand re-uploads. Each search is a top slice by impressions (about 150 cards), not a census. Format is video vs image only (carousels look like images). Classification sees copy text only, never the image or video.

## Competitors page and API (read only)
`/competitors` (Creative > Competitors) is the weekly view of this data. One sortable table, one row per brand: active ads, change against the week before, new and stopped ads, average and longest days running, video share, most common landing site, and a Status of Read or Stale (a stale brand could not be read and shows an earlier week's ads). Click a brand for its detail: longest-running ads (one per concept), new this week, the angles it pushes (our market cluster and pain point, new angles flagged), landing pages, and an Ad Library link for each ad plus a brand search link. Above the table, "What changed this week" lists up to 8 facts computed in SQL and Python (no model calls): brands that started 10 or more new ads, brands that fell under half of last week's ads, brands that could not be read, angles we have no line for, the longest-running ads. A Week picker and an All / Video / Image toggle sit above it; both live in the URL (`?week=YYYY-MM-DD&format=video|image`).
- `GET /api/competitors?week=&format=` returns `{weeks, week, prior_week, format, first_snapshot, notice, brands[], changes[], caveats[]}` and, only when the table `competitor_posts` exists, `chatter[]` (top 10 posts by score that week).
- `GET /api/competitors/<brand>?week=&format=` returns `{longest_running[], new_this_week[], new_count, angles[], landing_pages[], brand_ad_library_url}` and `chatter[]` (top 5, that brand) when `competitor_posts` exists.
- `format` limits every number (counts, new and stopped, longest, angles, landing pages, the changes box) to that format. The Library only tells video from image, so carousels count as image.
- With one stored week, change / new / stopped are null, the new-this-week list is empty and the page says "First snapshot: changes start next week". A brand with no rows in the earlier week gets null changes too. Carried-forward (stale) rows have the same Library IDs as before, so they show no new or stopped ads.
- Ad links are `https://www.facebook.com/ads/library/?id=<Library ID>`. No images are stored and nothing calls Meta, Google or Reddit from the web process. Code: `app/competitors.py`, `app/static/js/pages/competitors.js`.

## Chatter sources (`python -m etl.competitor_chatter`, table `competitor_posts`, migration 244)
Non-Meta signal: what people and the press say about watched brands and the category. Tested 2026-11-12 from a Mac (residential IP), logged out, descriptive User-Agent, no paid scrapers, no captcha or bot-detection bypass.

| Source | Works | Evidence / limit |
|---|---|---|
| Google News RSS (`news.google.com/rss/search?q="Brand" when:14d`) | yes | 200, 1-15 items per brand query (Heart & Soil 15, Nutrafol 9, Primal Queen 4, My Marrow 2); category phrases return 0-3. 1.5 s between queries. Datacenter IPs generally fine. Google's feed terms say personal / non-commercial use: internal competitor monitoring, headlines only, no republishing. |
| Reddit subreddit RSS (`www.reddit.com/r/<sub>/new/.rss?limit=100`) | yes, slowly | 200 with 100 entries (r/Supplements, r/carnivore). Budget is about one request per minute per IP (`x-ratelimit-remaining: 0` after one call, reset about 45 s; extra calls get 429). We wait 65 s between subreddits and stop the source on the first 403 / 429. The RSS carries no score or comment count. Reddit has historically blocked datacenter IPs: UNVERIFIED from Railway, the run records the failure and still completes. |
| Reddit search (`search.json`, `search.rss`, subreddit `search.rss`) | no | 403 (blocked for logged-out clients); `old.reddit.com` search RSS redirected (302); a later probe of `old.reddit.com/r/carnivore/new/.rss` returned 200 with a rate-limit budget of 199 but an HTML body, not Atom (0 entries, probably a login / consent page, not inspected): not usable as tested. |
| Reddit `.json` endpoints (`new.json`) | no | 403 with a block page. |
| Trustpilot public review pages | no | 403 "Verifying Connection" bot wall; not attempted further. |
| Hacker News (Algolia API) | works but useless | 200, but zero relevant hits for brand / category terms (tech audience). Not built. |

Not used: any paid API, login, OAuth, proxy or captcha solving. Rate limits respected: 1.5 s between Google News queries, 65 s between Reddit requests (about 8 subreddits = about 9 minutes), hard cap 400 rows per run.

Flow: brands = active rows of `competitor_brands` (first non-domain search phrase, exact-phrase query) plus the `CATEGORY_TERMS` constant; Reddit reads `SUBREDDITS` newest posts and keeps those that mention a brand phrase or category term; spam regex drops junk; only the last `--days` (14) are kept; upsert on `(source, url)`; `week_start` = Monday (Pacific) of `posted_at`; one failing query or source is logged (scrubbed) and skipped. The job fails (etl_runs) only when every source returned nothing and something failed.
