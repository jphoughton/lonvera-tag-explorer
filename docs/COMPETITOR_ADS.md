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
