# Projection feeds (server-side pulls)

What it replaced (once cut over): the desktop routine `lonvera-sync-tick` and the two desktop tasks as the writers of the sheet raw tabs the projection reads. It pulls straight from Upstack, Warehance, Meta and the ExpandFi warehouse into Postgres on a Railway cron, and (once a feed is `direct`) from the nightly projection refresh itself. Nothing here writes to the sheet, Meta, Upstack or Warehance. Design, inventory and cutover plan: `FEEDS_PLAN.md`.

## Files
```
migrations/180_proj_feeds.sql        mode switch, attempt log, verify tables, levers, raw non-US table + estimate view, 12 shadow tables, feed_oauth
etl/feeds/__init__.py                registry, pull_and_write, load() (module hook), mode_of(), direct_chip(), run(), freshness(), status()
etl/feeds/common.py                  Ctx, scrub, Http (retry/backoff/limits), RateLimiter, trailing_span, replace_window, mode/lever/attempt helpers
etl/feeds/feed.py                    Feed and Metric definitions
etl/feeds/upstack.py                 orders, platform, attribution, traffic, anyclick
etl/feeds/warehance.py               units_revamp, stock_revamp, units_intl, intl_estimate
etl/feeds/meta_feeds.py              meta_reach, product_reach, bids
etl/feeds/expandfi_feeds.py          amazon, nc_product, units_amazon (ExpandFi warehouse, read-only SQL)
etl/feeds/verify.py                  compare_rows, verify_feed, clean_streak, run_verify
etl/feeds/dryrun.py                  real read-only API pull diffed with the live sheet tab
etl/feeds/expandfi_auth.py           OAuth login/refresh, encrypted token storage, MCP client, spike, tcp check
etl/feeds/__main__.py                CLI
scripts/deploy/run_feeds.sh          service entrypoint (migrate, run, verify)
scripts/deploy/apply_feeds_service.py   dry-run-by-default creation of the Railway cron service `feeds`
etl/projection_pacing.py, projection_inventory_demand.py, projection_inventory_supply.py   the hook-ups (step 5): follow each feed's mode, sheet fallback, mode-aware chips
tests/projection/feeds/              feed tests, plus test_feeds_module_hooks.py (hook-ups per module and mode) and test_feeds_cutover_e2e.py (the whole nightly run, no sheet)
```

## Commands
```
python -m etl.feeds mode [FEED|all] [sheet|shadow|direct]   show / set the source for a feed (stored in proj_feed_config; default sheet; native feeds default shadow)
python -m etl.feeds run [--only a,b] [--days N]              pull every feed in shadow or direct mode; exit 1 on a failed pull or a stale direct feed
python -m etl.feeds verify [--feed a,b] [--days 30] [--need 7] [--live-sheet]   shadow vs sheet-fed table per day; prints consecutive clean days
python -m etl.feeds dryrun --feed X [--days 5]               real read-only pull, diff with the live sheet tab, no writes
python -m etl.feeds status | freshness | lever [KEY VALUE --note ...]
        freshness lists EVERY feed (sheet, shadow and direct): mode, newest day, age and the stale-after limit that applies in that mode
python -m etl.feeds.expandfi_auth {login|refresh|spike|tcp HOST PORT}
```
Local run needs `DATABASE_URL`. `dryrun` needs no database for Upstack, Warehance and Meta (credentials come from the environment or the local files the desktop jobs use); the ExpandFi feeds read the stored login from `DATABASE_URL` and need `FEEDS_ENC_KEY` too (both are rotated or read, never printed).

## Feeds
| key | target (main / shadow) | source | window per run | day basis |
|---|---|---|---|---|
| orders | proj_feed_orders / proj_feedx_orders | Upstack `/api/query` | 14 d | Pacific, through yesterday |
| platform | proj_feed_platform | Upstack `/api/query-channel-details` | 14 d | Pacific |
| attribution | proj_feed_attribution | Upstack `/api/query-attribution` last_click | 14 d (1 call/day) | Pacific |
| traffic | proj_feed_traffic | Upstack `/api/query` tracked.* | 14 d incl. today | Pacific |
| anyclick | proj_feed_anyclick | Upstack any_click, window 30, Meta | 7 d (1 call/day) | Pacific |
| units_revamp | proj_units_daily (revamp) / proj_feedx_units_daily | Warehance `/shipments` | 5 d | Denver, complete days |
| stock_revamp | proj_demand_snapshot (revamp) | Warehance `/products` | snapshot | Denver date |
| units_intl | proj_units_daily (intl, is_estimate) | Warehance `/orders` x ratio levers | 21 d from 2026-09-16 | Pacific |
| intl_estimate | proj_feedx_intl_nonus_daily (native, no sheet twin) | Warehance `/orders` | 21 d | Pacific |
| meta_reach | proj_feed_meta_reach | Graph account insights, day/week/month | 14 d + whole open periods | Pacific, includes today |
| product_reach | proj_feed_product_reach | Graph insights filtered by ad set ids | 21 d | Pacific, from 2026-07-05 |
| bids | proj_feed_bids | Graph adsets + activity log + ad set spend | 3 d | Pacific (+1 day for the New York account) |
| amazon | proj_feed_amazon / proj_feedx_amazon | ExpandFi warehouse `sales` + `customers` + `amazon_ads_campaign_data` (v3 > v2 > v1 per campaign-day) | 21 d | `purchase_date`, through yesterday |
| nc_product | proj_feed_nc_product / proj_feedx_nc_product | ExpandFi `shopify_orders` first orders, split by line price | 14 d | Pacific `processed_at` day, through yesterday |
| units_amazon | proj_units_daily (amazon) / proj_feedx_units_daily | ExpandFi `sales.quantity_shipped` by product | 21 d | `purchase_date`, through yesterday |

Meta spend, clicks, purchases and revenue by ad and day are the Tag Explorer's `ad_daily` (no new pull). The three ExpandFi feeds replicate the desktop jobs (`amazon_daily.py`, `product_daily_sync.py` + `nc_core.py`) with the same SQL and rules; see the next section.

## ExpandFi feeds (`amazon`, `nc_product`, `units_amazon`)
One credential per run: `get_warehouse(ctx)` refreshes the stored OAuth login under a Postgres advisory lock (the refresh token rotates, so never two at once), mints the 30-minute read-only warehouse credential through the MCP tool `readonly_db_connection` and keeps it in memory in `ctx.cache`, shared by the three feeds. Nothing secret is printed, logged or stored; the connection string is blanked out of any error. A failed login, an empty revenue or Ads result, or a failed query raises: nothing is written, the feed goes stale and the attempt is logged as failed. If the warehouse rejects the credential mid-run (30-minute lifetime) a new one is minted once and the query retried.

- `amazon`: Amazon.com + B2B by `purchase_date`, inner-joined to `customers` as the desktop query is; new customer = `purchase_date` equals `customers.cohort_day`; Ads de-duplicated v3 > v2 > v1 per campaign-day (raw is about 1.3x high). Ads for the newest sales day may not be loaded yet: that day's spend reads 0 until the next run (a warning is recorded).
- `units_amazon`: `quantity_shipped` by product, SKU prefix first, product-name fallback with 'Liver' last (Vital Belle's title says "Bovine Liver"); 'Other' is not stored. Rows carry source `expandfi-product-daily`, the value the sheet-fed loader writes.
- `nc_product`: the customer's first Shopify order (`shopify_customer_id`, else lower(email); earliest `processed_at`, then id), dated on its Pacific day; test orders and orders without priced lines are skipped after the first order is chosen; revenue = `total_price` split across the order's lines by net price; NC orders = the order's largest line, chosen before Vital Belle, Vital Flow and unmatched SKUs roll into Other. The first-order choice runs server-side in one SQL statement instead of the desktop job's scan of the whole table.
- Verify: Ads `sales_14d` matures for 14 days and late-shipped orders post to old purchase days for about two weeks, so for `amazon` and `units_amazon` the last 14 days are compared at 3x tolerance and never failed (`settle_days` 14); the 21-day pull keeps days 15 to 21 refreshed for the strict comparison. Count and money tolerances are 2 / 1 to 2 percent plus a small absolute floor.
- Default mode stays `sheet`; set `shadow` per feed (`python -m etl.feeds mode amazon shadow`) to start the dual-run.

## International estimate (`intl_estimate`, `units_intl`)
Non-US ship-to orders on Warehance in any status (Shopify routes them to the overseas warehouse and Warehance auto-cancels them), bottles = metadata quantity x the bundle multiplier in the SKU suffix, dated by Pacific order date. `proj_feedx_intl_nonus_daily(day, product, country, units_raw)` holds the raw counts. The estimate is raw x the lever `intl_ratio.<product>` in `proj_feed_levers` (seeded 0.905 / 0.895 / 0.868, default 0.89; edit with `python -m etl.feeds lever intl_ratio.bull 0.9 --note ...`). View `proj_feedx_intl_estimate(day, product, units_raw, factor, units, estimated)` applies the levers. `units_intl` writes the rounded estimate as `proj_units_daily` rows exactly as the sheet-fed loader does (source `estimate`, `is_estimate` true, Vital Flow written as 0). Vital Flow lines never appear on non-US Warehance orders, so its international demand is not estimated by anything (it is 0 in the sheet too).

## Module hook-ups (step 5)
Three projection modules read the feeds' data; each follows the feed's mode (`python -m etl.feeds mode`; no row = `sheet`, native feeds `shadow`).

| mode | pacing, inventory demand, inventory supply |
|---|---|
| `sheet` | exactly as before: the sheet tabs are read through the service account, no API is called, results and tables are identical (tested against the module's output with no mode row at all). |
| `shadow` | the same as `sheet` for the module. The `feeds` service (21:00 UTC) fills `proj_feedx_*` and `verify` compares. The module also pulls into the shadow table only when env `FEEDS_MODULE_SHADOW=1` is set on the service that runs `projection_daily`. |
| `direct` | the module's main table is filled from the feed (`source` api:upstack, api:warehance, api:meta, api:expandfi); that feed's sheet tab is NOT read. If the pull raises (or the database refuses the rows) the tab is read as before for that feed only, the result carries `warnings`, the runner prints `warning <module>: <feed>: the direct pull failed, the sheet was read instead (...)`, `proj_feed_attempts` holds a `failed` and a `fallback` row, and the freshness chip is judged against the feed's own limit, so a fallback to an old sheet goes stale loudly. |

Wiring (`feeds.load(key, conn, days, sheet_fallback, ctx, module=True)`):
- **pacing** `refresh`: the ten feeds orders, platform, attribution, traffic, anyclick, meta_reach, product_reach, bids, amazon, nc_product. Tabs are read up front only for feeds that are not direct. Backfill and `--snapshot-dir` always read the sheet. One-time sheet seeds stay sheet-fed: Meta campaign history before `ad_daily` and the typed hourly curve (both already loaded in production; a brand-new database would read them once).
- **inventory demand** `refresh`: locations revamp, amazon, intl through units_revamp, units_amazon, units_intl, and the stock snapshot through stock_revamp. A direct location also writes its `proj_units_loads` row (`note` "direct feed") and the module's audit row. The older `Amazon Daily Units` tab stays the sheet-side fallback of the Amazon product tab. International: only estimate days are replaced and a real count (an upload) is never overwritten.
- **inventory supply** `refresh`: while `stock_revamp` is direct it follows `proj_demand_snapshot` (written by the demand module earlier in the same run; the default module order has demand first) and does not read `Warehance Fcst Inputs`; with no Revamp count to follow it reads the cells and warns. FBA and international counts, purchase orders, transfers and vendor costs are typed in the app and untouched.
- **Windows**: the module's `days` (14) is widened to each feed's own window (`Feed.module_days`): Amazon, product reach, international 21; never wider than 3 for `bids` (its past days cannot be rebuilt). A direct pull never backfills; use the sheet backfill for history.
- **Transactions**: the module commits. A direct pull and its log row go in one savepoint (`pull_and_write(commit=False)`), so a failed feed never takes the module's other writes with it.
- **Chips**: a direct feed's chip says `(direct)` and uses the feed's limit (`Feed.stale_after`: 2 days, Amazon 3, international 2). The sheet-side limits are `Feed.sheet_stale_after` (a test pins them to the modules' own constants): international 4 and `monthly-upload` while the feed is sheet or shadow, 2 and `daily` while direct.

### Flip procedure (per feed, reversible, no deploy)
Prerequisites: the variable names for that source are on the service that runs `projection_daily` (the `etl` service): Upstack `UPSTACK_API_KEY` `UPSTACK_PIXEL_ID`, Warehance `WAREHANCE_API_KEY`, Meta `META_ACCESS_TOKEN`, ExpandFi `FEEDS_ENC_KEY` (check names only: `railway variable list -s etl --json | python3 -c "import sys,json;print(sorted(json.load(sys.stdin)))"`). Without them the direct pull fails and the module falls back to the sheet (safe, but not a cutover). The runner's time cap is 600 s per module (`PROJECTION_REFRESH_TIMEOUT_SEC`). In the 2026-11-04 shadow run the pulls of one module took about 2 minutes (international orders about 90 s, bids about 85 s), more when Meta rate-limits (waits of 60 to 900 s): raise the cap to 1800 on `etl` if a module's duration in `proj_refresh_runs` nears 500 s. The cap firing aborts the module loudly; it is never turned into a fallback.

`RUN <args>` below means `cd ~/lonvera-tag-explorer && railway run -s etl sh -c 'cd <a checkout of build/v1 with this PR merged> && DATABASE_URL="$DATABASE_PUBLIC_URL" PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m etl.feeds <args>'` (`~/lonvera-tag-explorer` is the linked Railway project; its own checkout was behind origin on 2026-11-04, so `git pull` there first or point `cd` at another checkout). `mode`, `status`, `freshness` and `verify` only read or write the small config tables.
1. Evidence first: `RUN verify --need 7` shows consecutive clean days per feed (7 for Upstack, Warehance, Meta; 14 for the ExpandFi feeds).
2. Flip one source at a time, in this order: Upstack (`mode orders direct`, `platform`, `attribution`, `traffic`, `anyclick`), Warehance (`units_revamp`, `stock_revamp`, `units_intl`), Meta (`meta_reach`, `product_reach`, `bids`), ExpandFi (`amazon`, `nc_product`, `units_amazon`). Command: `RUN mode orders direct`.
3. Confirm after the next 13:00 UTC run (or run it now: `railway run -s etl ... python -m etl.projection_daily --only pacing,inventory_demand,inventory_supply`):
   - `RUN mode` lists the feed as `direct`;
   - the runner log shows `pacing: ok ...` / `inventory_demand: ok ...` with no `warning ...` lines for that feed and `all modules refreshed and fresh`;
   - `RUN status` shows the feed `direct`, last `ok` today; `RUN freshness` shows it `fresh` with the direct limit;
   - the app's freshness chips (`/api/projection/freshness`) show the chip labelled `(direct)` and fresh; for `proj_feed_runs` the `source` reads `api:<source>`.
4. **Rollback**: `RUN mode <feed> sheet`. The next refresh reads the sheet tab again and replaces the module's trailing window from it (the sheet is only as fresh as the desktop writer: keep that running for 7 more days after each flip and rehearse one rollback). A failed direct pull needs no rollback: the module already fell back for that run.
5. Do not turn off a desktop writer until the feed has been direct and clean for 7 more days (FEEDS_PLAN section 7).

### Inputs that still need a human (no feed exists)
- QuickBooks P&L months (upload page; `quickbooks_pnl` chip, 45 days).
- Cohort export (upload page; `cohort_export` chip, 45 days; the export tool is unconfirmed).
- Purchase orders, FBA transfers, vendor costs and lead times (typed in the app; `purchase_orders` chip, 30 days).
- FBA stock count and international 3PL stock count (typed in the app; `stock_fba` / `stock_intl` chips, 14 days).
- International 3PL monthly tracking export (real units before 2026-09-16; since then the Warehance estimate stands in, scaled by the `intl_ratio.*` levers in `proj_feed_levers`).
- One-time sheet seeds: Meta campaign history before `ad_daily` begins, the typed Meta hourly curve (loaded; only a new database re-reads them).
- Credentials: the ExpandFi login (`python -m etl.feeds.expandfi_auth login` again if the refresh token ever dies), the Upstack, Warehance and Meta keys on the `etl` service.

## Dry-run against the live sheet (2026-11-04, read-only)
See the table in `FEEDS_PLAN.md` section "Phase 2 status". Intl estimate vs `Intl Daily Units`, Sep 16 to Oct 12 (27 days x 3 products): totals 12,607 vs 12,611 (-0.03%), summed absolute difference 6 units, 23 of 27 days identical; a flat 0.89 would differ by 208 units.

## Dry-run of the ExpandFi feeds against the live sheet (2026-11-04, read-only, real warehouse)
Definitions: the direct pull equals the desktop job's own SQL run at the same instant, row for row (`amazon` 14 of 14 days identical, `units_amazon` 42 of 42 rows identical).

| feed | rows pulled | window | days compared | ok | failed | soft |
|---|---|---|---|---|---|---|
| amazon | 21 | 10-14..11-03 | 21 | 15 | 0 | 6 |
| nc_product | 56 | 10-21..11-03 | 14 | 14 | 0 | 0 |
| units_amazon | 42 | 10-21..11-03 | 13 (+1 day held back by the sheet's partial-day rule) | 13 | 0 | 0 |

The six soft `amazon` days are Ads `attributed_sales` still maturing (up to 6 percent) and impressions or spend restated since the sheet's 12:13 UTC write. Before the tolerances were set, Oct 20 and Oct 23 differed by one late-shipped order ($23 to $30) between the sheet's write and the pull.

## Differences from the sheet-fed path
- Stock and shipments keep the sheet's day bases (Denver); the international estimate uses Pacific days.
- Days the desktop job froze when written (international estimates are written once) can differ by a unit or two when Warehance restates an order afterwards.
- The native raw table `proj_feedx_intl_nonus_daily` stores only positive counts; the main-table feeds write zero rows like the sheet does.
- Rounding: python half-to-even in `units_intl`, half-up in the view `proj_feedx_intl_estimate`.

## Open items
- Railway variables `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID`, `WAREHANCE_API_KEY` on `etl`: the write was refused by the permission system in this session; the commands are in `FEEDS_PLAN.md`.
- `feeds` service not created (`apply_feeds_service.py` is dry-run by default; `--apply` and `railway up -s feeds --ci` are the production steps).
- The module hook-ups (step 5) are built and tested (branch `feat/feeds-cutover-wiring`, section "Module hook-ups"); production behaviour is unchanged until a feed is flipped to `direct`.
- Not rehearsed against production: a real `direct` run of the modules (the tests use fixture rows at the pull boundary; the pulls themselves were dry-run against the live sources earlier). The first flip is the first real one: flip one feed, run `projection_daily --only <module>` once by hand and read the log.
- ExpandFi: login done 2026-11-04; the refresh, minted credential, TCP connect and `SELECT 1` pass from Josh's Mac (`expandfi_auth spike`). Reachability of `data.expandfi.com:5432` from Railway is not yet tested (the `feeds` container is asleep between runs): the first scheduled run proves it. The refresh-token lifetime is unknown.
- `cryptography` is requested in `REQUIREMENTS_REQUESTS.md`.
