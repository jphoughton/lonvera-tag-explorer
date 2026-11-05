# Feeds plan: refresh the projection and pacing data without Josh's Mac

Phase 1 (investigation and design), written 2026-11-03. Nothing was built, set, deployed or written anywhere. Reads only: local scripts and logs, the live sheet (tab dates), Railway variable names, the scheduled-task list, Meta `debug_token`, public ExpandFi OAuth metadata. No secret value appears in this file (env var names and file paths only). (V) = verified today, (U) = could not verify.

## TLDR

1. Every external feed the projection reads is written to the sheet by the desktop routine `lonvera-sync-tick` (hourly, runs only while the Claude app is open and the Mac is awake) or by two other desktop tasks. Today 17 of 19 feed tabs checked are fresh (newest day 2026-11-02 or 11-03, V). Two are not: `Amazon Daily Units` ends 2026-10-11 and `Intl Daily Units` ends 2026-10-12 (V). The job that refreshes them, `inventory_rebalance`, has only ever run as a dry run because the `Inventory Plan` tab does not exist (log line 1). The inventory-demand module is therefore reading 3-week-old Amazon and international units. Intl was about 37% of units in mid-September.
2. **Class A, can move to Railway now with credentials Josh already has: 11 feeds in 3 bundles.** Upstack (orders, platform spend, attribution, traffic, any-click; 5 feeds), Warehance (US 3PL shipments, stock, international estimate; 3 feeds), Meta (account reach, product reach, bid settings; 3 feeds; plus ad x day, which is already in Postgres). Meta needs nothing new (`META_ACCESS_TOKEN` is already on the `etl` service). Upstack and Warehance need 3 variables set on `etl` (names below; none is set yet, V).
3. **Class B, needs Josh: Amazon daily (+ Amazon units by product) and new-customer revenue by product.** Both come from the ExpandFi warehouse, which only hands out 30-minute Postgres credentials through its MCP login. Recommended route: one server-side ExpandFi OAuth client on Railway (ExpandFi's OAuth metadata supports public clients and refresh tokens, V). Josh does one consent click as josh@drinkhydrant.com. Unknowns: refresh-token lifetime, and whether ExpandFi's database accepts connections from Railway (U). Hedge: Shopify custom-app token (for new customers by product only) or Amazon SP-API plus Ads API (loses the new-customer split, takes days to weeks of approvals).
4. **Class C, stays manual:** QuickBooks P&L, cohort export, international 3PL tracking export, international and FBA stock counts, POs and transfers. Each shows a staleness chip through the existing `monthly-upload` freshness cadence.
5. **Dual-run first.** New jobs write to shadow tables (`proj_feedx_*`) while the sheet-fed tables stay authoritative. `verify_feeds` diffs them daily. A per-feed mode switch (`sheet` / `shadow` / `direct`) flips the app's source with no deploy and rolls back instantly, with a sheet fallback if a direct pull fails.
6. **Schedule:** keep the `etl` cron (13:00 UTC) untouched; add one new Railway cron service `feeds` at 21:00 UTC (second daily run). Cost is cents per month.
7. **Cutover of the app's source is independent of turning off the desktop writers.** The desktop jobs can keep running (and keep the old sheet alive) until Josh retires the sheet.
8. **Effort:** about 8 to 9 agent-days of build; Josh about 5 minutes for class A, 15 for ExpandFi, 15 optional for a Meta system-user token. Calendar about 3.5 weeks: A feeds cut over about day 14 (build 5 working days, then 7 clean days), B feeds about day 24 (14 clean days).
9. **Do first (no new credential):** point inventory-demand's Amazon units at `Amazon - Product Daily` (fresh to 11-03) instead of `Amazon Daily Units` (frozen 10-11).

## Phase 2 status (2026-11-04): steps 1 to 5 built, step 7 prepared, flips are Josh's

Built and tested (`tests/projection/feeds`): common plumbing, the mode switch, shadow tables, the attempt log, `verify`, `dryrun`, Upstack (5 feeds), Warehance (4 feeds, incl. the international estimate), Meta (3 feeds), ExpandFi (3 feeds), `run_feeds.sh`, `apply_feeds_service.py`, `etl/feeds/expandfi_auth.py`, and (step 5, branch `feat/feeds-cutover-wiring`) the **module hook-ups**: `etl/projection_pacing.py`, `etl/projection_inventory_demand.py` and `etl/projection_inventory_supply.py` now follow each feed's mode. Details: `MODULE_feeds.md`, section "Module hook-ups (step 5)", which also holds the flip procedure and the list of inputs that still need a human.

**Step 5 changes nothing in production until a feed is flipped.** Every feed is `sheet` or `shadow` today, and in both modes the three modules read the sheet tabs exactly as before and call no API (the `feeds` service keeps filling the shadow tables). Only `python -m etl.feeds mode <feed> direct` makes a module read from the feed.

**Dry-run against the live sheet (real read-only API pulls, local keys, no writes; 2026-11-04):**

| feed | rows pulled | window | days compared | ok | failed | soft (open day) | sheet behind |
|---|---|---|---|---|---|---|---|
| orders | 7 | 10-27..11-02 | 7 | 7 | 0 | 0 | 0 |
| platform | 14 | 10-27..11-02 | 7 | 7 | 0 | 0 | 0 |
| attribution | 14 | 10-27..11-02 | 7 | 7 | 0 | 0 | 0 |
| traffic | 7 | 10-28..11-03 | 7 | 6 | 0 | 1 (today: sessions +26% since the sheet's snapshot) | 0 |
| anyclick | 14 | 10-27..11-02 | 7 | 7 | 0 | 0 | 0 |
| units_revamp | 28 | 10-27..11-02 | 7 | 7 | 0 | 0 | 0 |
| stock_revamp | 4 | 11-03 | 1 | 1 | 0 | 0 | 0 |
| units_intl | 192 | 09-16..11-02 | 27 | 27 | 0 | 0 | 21 (10-13..11-02: the tab is stale) |
| meta_reach | 9 | 10-30..11-03 | 6 | 6 | 0 | 0 | 0 |
| product_reach | 27 | 10-30..11-03 | 6 | 6 | 0 | 0 | 0 |
| bids | 3 | 11-01..11-03 | 3 | 2 | 0 | 1 (today: spend accrued since the sheet's run) | 0 |

**International estimate vs `Intl Daily Units`, Sep 16 to Oct 12 (27 days x Vital Bull / Beef Liver / Boneflex):** Warehance non-US units 14,135 (6,302 / 3,697 / 4,136); x per-product ratios 12,607 vs the tab's 12,611 (-0.03%); summed absolute difference 6 units over 81 product-days; 23 of 27 days identical, the others differ by 1 to 3 units (Warehance restated an order after the sheet wrote the day). A flat 0.89 would differ by 208 units, so the stored levers are the per-product ratios; their blend is 12,611 / 14,135 = 0.892, which is the "0.89" quoted elsewhere. Vital Flow never appears on non-US Warehance orders, so nothing estimates its international demand (0 in the sheet too). Oct 13 to Nov 2 (21 days the sheet lacks) is available from the direct pull as soon as `units_intl` runs.

**Blocked: Railway variables on `etl`.** The permission system refused `railway variable set ... --stdin` (secret-store write) in this session, so `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID` and `WAREHANCE_API_KEY` are still NOT set; verified with `railway variable list -s etl --json` (names: DATABASE_PUBLIC_URL, DATABASE_URL, GOOGLE_SA_JSON, META_ACCESS_TOKEN, SECRET_KEY). Until they are set the `feeds` references resolve to empty and the three sources cannot run on Railway (Meta can). Josh runs the four commands in section 4, or allows the write.

**Service plan (dry run, nothing created):** `python3 scripts/deploy/apply_feeds_service.py` from `~/lonvera-tag-explorer` prints: create service `feeds` (no GitHub source, like `etl`), Dockerfile, start `scripts/deploy/run_feeds.sh`, cron `0 21 * * *` UTC, restart NEVER, variables by reference `${{etl.NAME}}` for DATABASE_URL, GOOGLE_SA_JSON, META_ACCESS_TOKEN, SECRET_KEY, UPSTACK_API_KEY, UPSTACK_PIXEL_ID, WAREHANCE_API_KEY (+ FEEDS_ENC_KEY once it exists) and `FEEDS_VERIFY=1`. `--apply` creates it (untested against the live API); then `railway up -s feeds --ci`.

**Operational finding: Meta rate limit.** During the dry runs the `adsets` listing used by `bids` (and by the desktop `meta_bids_daily`) hit Meta error 17 (user request limit) twice (60 s and 120 s waits) while the desktop sync was also running; the pull then completed. All Meta pulls share one user token with the Tag Explorer ETL and the desktop jobs, so run them sequentially and keep `bids` out of the 13:00 UTC slot when `etl.run_daily` is pulling.

### International non-US schema (for the inventory_demand loader, step 5)
```sql
proj_feedx_intl_nonus_daily (day date, product text, country text, units_raw numeric(18,2) >= 0, loaded_at timestamptz,  PRIMARY KEY (day, product, country))
proj_feed_levers (key text PRIMARY KEY, value numeric(12,6), note text <= 300, updated_at timestamptz, updated_by text)
  seeded: intl_ratio.bull 0.905, intl_ratio.liver 0.895, intl_ratio.boneflex 0.868, intl_ratio.default 0.890
  note on every row: sheet's Intl estimate since 2026-09-16 (fitted Aug 17 - Sep 15 against the 3PL export; non-US orders go to the D1/overseas 3PL, Warehance auto-cancels them)
proj_feedx_intl_estimate (VIEW: day, product, units_raw, factor, units, estimated = true)   -- raw x the product's lever (else intl_ratio.default), half-up
proj_feedx_units_daily (location = 'intl', product, day, units, source = 'estimate', is_estimate = true)   -- the python job's rounded estimate, half-to-even; Vital Flow = 0
```
Freshness entry: `{"feed": "intl_estimate", "label": "International units shipped, estimated (Warehance non-US orders) (direct)", "cadence": "daily", "data_through": <max day>, "stale_after_days": 2}` from `etl.feeds.freshness(conn)`. The loader should read `proj_feedx_units_daily` where `location = 'intl'` (same shape as its own rows) or `proj_feedx_intl_estimate`; changing a lever re-prices the view immediately, and `units_intl` on its next run.

### Step 5: module hook-ups (built 2026-11-04)
Each module calls `feeds.load(key, conn, days, sheet_fallback, ctx, module=True)`: **sheet** returns the sheet result untouched, **shadow** the same (the module pulls into the shadow table only when env `FEEDS_MODULE_SHADOW=1`; otherwise the `feeds` service owns the shadow tables), **direct** writes the main table from the feed inside a savepoint and falls back to the sheet read when the pull fails. A module reads a sheet tab up front only for feeds that are not direct, so a direct feed's tab is never touched unless its own pull failed.

Feed to module map (every feed the modules read now has a registry entry that matches what the module aggregates; a test pins table, columns, tab and sheet-side stale limit):

| module | feeds | sheet tab it replaces | notes |
|---|---|---|---|
| pacing | orders, platform, attribution, traffic, anyclick, meta_reach, product_reach, bids | Orders - History, Platform - History, Attribution - History, Traffic - Daily, Upstack Any-Click - By Campaign, Meta Account - Reach, Meta Product - Reach, Meta Bids - Daily | |
| pacing | amazon, nc_product (ExpandFi) | Amazon - Daily, NC Rev - By Product | |
| inventory_demand | units_revamp, units_intl, units_amazon (ExpandFi), stock_revamp | Warehance Daily, Intl Daily Units, Amazon - Product Daily (fallback tab Amazon Daily Units stays sheet-only), Warehance Fcst Inputs | `units_amazon` was not in the original plan; Amazon units come from the product tab, not Amazon - Daily |
| inventory_supply | stock_revamp | Warehance Fcst Inputs A33:D39 | follows the count the demand module pulled (`proj_demand_snapshot`); reads the cells only if that table holds no Revamp count |

What changed against the diffs first written here:
- **Windows are per feed.** A module passes its trailing `days` (14); `Feed.module_days` widens it to the feed's own window (Amazon and product reach 21, international 21) and never narrows it. `bids` is `fixed_window`: its past days cannot be re-pulled (they are rebuilt from the activity log), so a module never widens it beyond 3 days.
- **Transactions.** The module owns and commits its transaction. `pull_and_write(..., commit=False)` writes the feed and its log row in one savepoint, so a failed direct pull (or a write the database refuses) leaves the module's earlier writes in the same run alone, and the failure and fallback rows are logged inside the module's transaction.
- **Row factories and old databases.** The runner hands hooks dict-row connections; the feeds helpers read through tuple cursors and treat a missing `proj_feed_config` / `proj_feed_levers` as `sheet` / no levers.
- **Time cap.** `projection_daily`'s `RefreshTimeout` passes through a pull instead of being turned into a fallback.
- **Backfill and `--snapshot-dir`** always read the sheet (full history).
- **Supply** shares one writer (`_write_revamp_counts`) between the sheet path and the feed path, so the audit rows and the "same values: skip" rule are identical.
- **Warnings.** A module that fell back returns `warnings`; the runner prints one `warning <module>: ...` line each (the run is ok, exit code unchanged).
- **Freshness chips.** While a feed is direct its chip uses the feed's own limit and says `(direct)`: pacing feeds 2 (Amazon 3), demand revamp 2, Amazon 3, international 2 (cadence `daily`, so a failed pull that fell back to an old sheet fails the run; it is 4 days and `monthly-upload` while the feed is sheet or shadow), supply `stock_revamp` 2. `python -m etl.feeds freshness` lists all 15 feeds with their mode and the limit that applies in that mode (the sheet-side limit lives in `Feed.sheet_stale_after`).

## 1. Inventory of the desktop sync

Scheduler: `~/Desktop/Google Sheets/lonvera_sync_local/tick.py` (JOBS dict, Pacific slots; routine `lonvera-sync-tick` fires at :20 hourly; catch-up on the next tick after a closed Mac; 3 tries per slot, 25 min apart; state in `state.json`, logs in `logs/`). Jobs run sequentially in one detached batch; secrets are read from local files at job start. Writers: all target `1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI` (Lonvera Projections) unless noted. Runtime = last run from `state.json` (V).

| Job (script) | Source and endpoint | Auth, credential location (NAME or path) | Target tab(s) | Slots PT / window rewritten | Run | Consumer | Class |
|---|---|---|---|---|---|---|---|
| `upstack` (`sync.py`, repo `~/lonvera-sync-main`) | Upstack `POST api.upstackdata.com/db-mgmt`: `/api/query` (orders.* measures), `/api/query-channel-details` (platform, campaign, ad grain), `/api/query-attribution` (ad_source, last_click and first_click, 1 call/day) | static key: header `x-api-key` + `x-pixel-id`; file `~/.upstackrc`; env `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID`; also pinned header `x-upstack-cli-version` 1.0.7 | `Orders - History`, `Platform - History`, `Attribution - History` (+ `Product - History`, `Ad Spend - Daily`, `Orders - Daily`, `Attribution - Daily`, not read by projection) | 01:20 06:20 10:20 14:20 18:20 22:20; trailing 30 days merged into history, Pacific days, never today | 126 s | pacing | A |
| `upstack_traffic_daily` (`lonvera_traffic/`) | Upstack `/api/query` `tracked.new_visitors/visitors/sessions` | same Upstack key | `Traffic - Daily` | 03:20 12:20; `--days 3` | 6 s | pacing | A |
| `upstack_anyclick_campaign` | Upstack `/api/query-attribution` dimension `utm_campaign`, source Meta Ads, model any_click, window 30, 1 call/day, 502/504 retried x6 | same Upstack key | `Upstack Any-Click - By Campaign` | 02:40 11:40; 7 days | 9 s | pacing (testing vs scaling) | A |
| `upstack_ads_daily` | Upstack `/api/query-attribution` utm_campaign + utm_ad | same; keeps local state file `~/.lonvera/upstack_ads_daily_state.json` | `Upstack Ads - Daily`, `Campaigns - Daily`, `Ads - Windows` | 03:10 12:10 | 142 s | creative analysis only | not needed |
| `meta_account_reach` | Meta Graph `/act_*/insights` account level, day (190d), ISO week (28), month, `reach, frequency, unique_outbound_clicks` | static user token, env `META_ACCESS_TOKEN`; local helper `lonvera_creative_key/meta_tok.py` (Odylic Lens db) | `Meta Account - Reach` | 03:55 12:55; whole tab rewritten (partial week and month move daily) | 27 s | pacing (reach, frequency, CPM) | A |
| `product_daily_sync --only meta` (desktop task `lonvera-product-daily-sync`, 07:00, not in tick) | Meta Graph ad-set daily insights both accounts; account insights filtered `adset.id IN (...)` per product at day, week, month | same Meta token | `Meta Spend - By Product`, `Meta Product - Reach` (unique clicks from 2026-07-05 only: Meta serves ~120 days) | trailing 21 days | 2 to 4 min | pacing product bands | A |
| `product_daily_sync --only nc` (same task) | ExpandFi `shopify_orders` (merchant 1452): first order per customer, line items split by SKU | 30-minute Postgres credential minted per run through the ExpandFi MCP tool `readonly_db_connection` | `NC Rev - By Product` | trailing 21 days | 1 to 3 min | pacing product bands | **B** |
| `meta_bids_daily` (`lonvera_bids/`) | Meta Graph adsets (`bid_strategy, bid_amount, bid_constraints`), ad account activity log (AD_SET updates), ad-set spend by day | Meta token | `Meta Bids - Daily` | 03:52 09:52 15:52; 3 days | 31 s | pacing diagnostics | A (history seeded once from the sheet) |
| `meta_campaign_history` | Meta Graph campaign x day | Meta token | `Meta Campaign - History` | 03:50 09:50 15:50; 7 days | 9 s | pacing | covered by `ad_daily` (Tag Explorer, same token) |
| `meta_history`, `meta_last30` | Meta Graph ad level, Creative Key sheet `12LmoK...` | Meta token | `Meta Ads - Daily/Weekly/Windows/Last 30` | 03:45 14:45 / 03:30 14:30 | 669 s / 359 s | Creative Key | not needed (Tag Explorer `ad_daily`) |
| `warehance` (`warehance_sync.py`) | Warehance `api.warehance.com/v1`: `/shipments` (cursor, limit 100, from `created_at_date_start`), `/products` | static key, header `X-API-KEY`; file `~/Desktop/Google Sheets/lonvera_fulfillment/.env` (chmod 600); env `WAREHANCE_API_KEY` | `Warehance Daily` (units by product per **Denver** day, voided excluded), `Warehance Fcst Inputs` B35:C39 (on hand, allocated, as-of) | 06:17 12:17; last date minus 3 days; +-20% drift check exits 1 after writing | 18 s | inventory-demand, inventory-supply | A |
| `inventory_rebalance`, demand half | Amazon: copies `Amazon - Product Daily` into `Amazon Daily Units`. Intl: Warehance `/orders` (`order_date_start`), non-US ship-to, units = metadata quantity x SKU bundle multiplier x calibration ratio (0.905 / 0.895 / 0.868 for Vital Bull / Beef Liver / Boneflex, fitted Aug 17 to Sep 15) | sheet key + `WAREHANCE_API_KEY` | `Amazon Daily Units`, `Intl Daily Units` | 08:30; **dry run only, so nothing written since 10-11/10-12** (V) | 222 s | inventory-demand | Intl A; Amazon via B feed |
| `inventory_rebalance`, planning half | reads and rewrites `Purchase Orders`, `FBA Transfers` | sheet key | PO tabs | 08:30 | | replaced by the app's `inventory_supply` planner | not a feed |
| desktop task `lonvera-amazon-daily-pacing` (06:08, not in tick) | headless Claude calls ExpandFi MCP `readonly_db_connection`, then `amazon_daily.py` SQL on `sales`, `customers.cohort_day`, `amazon_ads_campaign_data` (ads deduped v3 > v2 > v1 per campaign-day) | 30-minute Postgres credential via the ExpandFi MCP (OAuth held by the Claude app) | `Amazon - Daily` (orders, units, revenue, NC orders, NC rev, ad spend, attributed sales, clicks, impressions), `Amazon - Product Daily` | daily; rewrites the whole lookback (ads restate as Amazon backfills API generations) | minutes | pacing, inventory-demand | **B** |
| `pacing_extend_rows` | sheet row housekeeping | sheet key | `Rollup - Monthly` | 01:40 | 2 s | sheet only | not a feed |
| `status_guard`, `madre_sync`, `trybe_samples`, `trybe-sample-roster` | Creative Key housekeeping and Trybe | sheet key / Warehance / Trybe MCP | Creative Key tabs | hourly / daily | | none | out of scope (`trybe_samples` has failed 3x since 10-26 with a Sheets HTTP 400: unrelated) |
| manual: `Lonvera-Tracking-YYYY-MM.xlsx` | overseas 3PL daily units, Josh's monthly file | none | `Intl Daily Units` (real data to 09-15, estimates after) | monthly | | inventory-demand | C |
| manual: cohort export, QuickBooks P&L, FBA and Intl stock counts, POs, FBA transfers | ExpandFi / Shopify cohort export (tool unconfirmed), QBO export, ExpandFi Dash pull, 3PL portal | n/a | `Cohort Data - Unified`, `P&L`, snapshots | monthly or ad hoc | | cohorts, pnl, inventory | C |

Freshness of the feed tabs read at 2026-11-03 ~15:50 PT (V): Orders, Platform, Attribution History, Product History, Any-Click: through 11-02. Traffic, Amazon - Daily, Amazon - Product Daily, NC Rev - By Product, Meta Spend - By Product, Meta Campaign - History, Meta Bids, both Reach tabs, Warehance Daily: through 11-03. **Amazon Daily Units 10-11, Intl Daily Units 10-12.**

Known failure modes, with evidence:
- GitHub cron fired 8 to 12 hours late and then failed on account billing; workflows are manual-only since 2026-10-13 (`ma-calano.md`, `daily.yml`).
- Mac dependence: the tick only runs while the Claude app is open and the Mac is awake. Of the last 8 runs of the Amazon task, 2 show `succeeded` and 6 still show `running` (up to 6 days after start) (V, `list_task_runs`). The tab is nevertheless fresh, so I cannot tell which session did the work.
- Amazon on a retired ExpandFi client (410 `legacy_mcp_retired`) and then "OAuth session expired and could not be refreshed" on 2026-09-29 (`ma-calano.md`). It works again today. The headless refresh path is the part with a track record of failing.
- ExpandFi data lags: `sales` about 2 days, so yesterday's Amazon revenue is partial (task summary 11-03).
- Meta rate limit 80004 (`meta_api.graph_get` already backs off 60 s doubling to 900 s).
- Upstack days are Pacific; UTC buckets were a 0.5 to 13% error earlier (`ma-calano.md`, `sync.py` TIMEZONE).
- ExpandFi `shopify_orders`: the first-order scan reads about 430K rows and can hit the statement timeout; the runbook says rerun once.
- CheckoutChamp warehouse stopped updating 2026-10-12: no feed above reads it.
- Meta token `META_ACCESS_TOKEN`: type USER, `expires_at` never, but `data_access_expires_at` 2026-08-28 is already past; the token is `is_valid` today and byte-identical to the local Odylic Lens token (V, `debug_token`).

## 2. Consumers and what Postgres already has

| Projection module | Feed it needs | Where it comes from today | Postgres today |
|---|---|---|---|
| pacing | Meta spend, impressions, clicks, purchases by campaign, product, stage | Tag Explorer `ad_daily` + `meta_campaigns` + `meta_ads`, rolled by `projection_pacing.refresh_meta_from_app` | **already there** (`etl.run_daily`, trailing 7 days, 13:00 UTC) |
| pacing | orders, platform, attribution, traffic, any-click, meta reach, product reach, nc by product, bids, amazon | sheet raw tabs through `GOOGLE_SA_JSON` (`FEEDS` list in `etl/projection_pacing.py`) | `proj_feed_*` tables (migration 170), fed from the sheet |
| inventory-demand | Warehance daily units, Amazon units, Intl units, Warehance stock | tabs `Warehance Daily`, `Amazon Daily Units`, `Intl Daily Units`, `Warehance Fcst Inputs` (`etl/projection_inventory_demand.py`) | `proj_units_daily`, `proj_demand_snapshot`, `proj_units_loads` (migration 140) |
| inventory-supply | Revamp stock snapshot (daily); FBA and Intl stock are manual | `Warehance Fcst Inputs` B35:C39 (`etl/projection_inventory_supply.py refresh`) | `proj_demand_snapshot` |
| drivers | Meta spend of complete months | `ad_daily` | already there |
| cohorts, pnl, financing | monthly uploads only | upload pages | n/a |

The existing `etl` service already reads the sheet server-side, so the sheet-fed path works on Railway today. The problem is only that the sheet's writers are on the Mac.

## 3. Classification

### A. Runs on Railway today with a credential Josh already has
`railway variable list -s etl --json` (names only, V) shows exactly: `DATABASE_PUBLIC_URL`, `DATABASE_URL`, `GOOGLE_SA_JSON`, `META_ACCESS_TOKEN`, `SECRET_KEY`.

| Bundle | Feeds (target) | Env var names on `etl` | Set on `etl` today? | Josh action |
|---|---|---|---|---|
| Upstack | orders, platform, attribution, traffic, anyclick (`proj_feed_orders/platform/attribution/traffic/anyclick`) | `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID` | **no** | copy from `~/.upstackrc` (command in section 4) |
| Warehance | US shipments (`proj_units_daily` revamp), stock (`proj_demand_snapshot` revamp), international estimate (`proj_units_daily` intl, `is_estimate`) | `WAREHANCE_API_KEY` | **no** | copy from `lonvera_fulfillment/.env` |
| Meta | account reach, product reach, bids (`proj_feed_meta_reach/product_reach/bids`); ad x day and campaign x day (`ad_daily` -> `proj_feed_meta_day/meta_product`) | `META_ACCESS_TOKEN` | **yes** (V) | none |
| Fallback | sheet reads through the service account | `GOOGLE_SA_JSON` | **yes** (V) | none |

Both keys have been used unattended from GitHub Actions secrets since September (Upstack since 2026-09-17, Warehance since 2026-09-28; READMEs), so they are static. Key files exist and are non-empty (V: Upstack key 48 chars, pixel id 36, Warehance `.env` has the line and is chmod 600).

### B. Needs something new from Josh

**B1. ExpandFi-derived feeds: Amazon daily (sales, NC split, ads), Amazon units by product, NC revenue by product.**
Why no A route: ExpandFi is the only place that has Amazon orders keyed to a customer cohort (`customers.cohort_day`, so NC orders and NC revenue) and the v3 > v2 > v1 deduped Ads spend, and the only source of Shopify first-order logic with line items that matches Upstack within 0.2% (`product_daily_sync.py` docstring). Upstack has no Amazon data and returns `product: null` on this account (README); Warehance ships DTC only; the CheckoutChamp warehouse is stale.

Options, in order of recommendation:
1. **Server-side ExpandFi OAuth client (recommended, one click).** `https://app.expandfi.com/.well-known/oauth-authorization-server` (V) advertises: grant types `authorization_code` and `refresh_token`, PKCE S256, token endpoint auth `none` (public client), dynamic client registration, scopes `mcp_full mcp_read mcp_write mcp_db`. `get_auth_status` (V): `remote_bearer`, refresh token present, account josh@drinkhydrant.com. The job refreshes the access token, calls MCP tools over HTTP (`get_docs_version`, `get_skills_version`, then `readonly_db_connection` for merchant 1452, the same sequence the desktop runbooks use), connects to the returned Postgres DSN with psycopg, runs the existing SQL read-only. Unknowns (U): refresh-token lifetime and rotation (the Sep-29 failure suggests it expires), whether the database host accepts Railway egress, ExpandFi's stance on unattended use (ask support, see section 4).
2. **Ask ExpandFi support for a service credential** (long-lived read-only token or static DB role for merchant 1452). Their design mints 30-minute credentials, so this is a low-probability ask, but it costs one email and removes the refresh risk if granted.
3. **Direct Amazon APIs** (hedge). SP-API self-authorization (Seller Central > Apps and Services > Develop Apps; private developer profile approval takes days) gives orders and units by SKU with a long-lived refresh token, but Amazon hides buyer identity, so the NC split is lost. The Amazon Ads API needs its own access application (days to weeks) plus client id, secret, refresh token and profile id; it would remove the dedupe problem. Not recommended unless option 1 fails.
4. **Shopify custom app for NC by product only** (hedge). Josh creates a custom app in Lonvera's Shopify admin with `read_orders`, `read_customers`, `read_all_orders`, and saves the Admin API token. Lonvera has no Shopify access on this machine today (`ma-calano.md`, 2026-10-03). New first-order logic would differ slightly from ExpandFi's (guest checkouts), so the dual-run tolerance matters.
5. **Derive from A-class data: not possible exactly.** Upstack any-click NC is Meta-attributed and campaign-level (`Mixed` for All_* campaigns), not actual first orders by product. Keep as a labelled approximation only.
6. **Keep the desktop routine (class C fallback)** and show its age in the freshness chip. It works today.

**B2. Meta durable token (hardening, not blocking).** The shared user token has no expiry but its data-access date already passed and it hangs off Josh's Facebook login. A System User token (Business Settings) never expires and cannot be invalidated by a password change. It is also the single point of failure for the Tag Explorer ETL, so swap only after a dry run passes (section 4).

**B3. International 3PL truth after 2026-09-15.** Open question, blocks nothing. If the overseas 3PL has an API or a scheduled export, that replaces the Warehance-derived estimate (error unknown since the ratios were fitted Aug 17 to Sep 15).

### C. Manual or desktop, and how staleness stays visible
| Feed | Why manual | Staleness visibility |
|---|---|---|
| QuickBooks P&L | no API by decision (SCOPE decision 3) | `pnl.freshness`, monthly-upload chip |
| Cohort export | export tool unconfirmed (ExpandFi or Shopify), Josh trims open months | `cohort_export` monthly-upload chip, stale after 45 days |
| Intl tracking xlsx | Josh's file | `upload-intl` chip, stale after 45 days |
| FBA and Intl stock counts, POs, transfers, vendor costs | typed in the app | `proj_demand_snapshot.as_of` chip: warn at 14 days, stale at 30 (same thresholds `inventory_rebalance` uses) |
| Amazon and NC by product while B1 is open | the desktop tasks keep writing the sheet | `daily` chips with `stale_after_days >= 2` (Amazon already lags 2 days) |

## 4. Josh's steps (exact)

Run from a terminal on the Mac; values pass through stdin and are never printed. `--skip-deploys` stops Railway redeploying (and re-running) the cron service on every variable. Jobs `strip()` the values.

**A (about 5 minutes, after the orchestrator approves):**
```
cd ~/lonvera-tag-explorer      # the linked Railway project
python3 -c "import json;print(json.load(open('/Users/joshhoughton/.upstackrc'))['profiles']['default']['apiKey'],end='')" | railway variable set -s etl --stdin UPSTACK_API_KEY --skip-deploys
python3 -c "import json;print(json.load(open('/Users/joshhoughton/.upstackrc'))['profiles']['default']['pixelId'],end='')" | railway variable set -s etl --stdin UPSTACK_PIXEL_ID --skip-deploys
grep '^WAREHANCE_API_KEY=' "$HOME/Desktop/Google Sheets/lonvera_fulfillment/.env" | cut -d= -f2- | tr -d "\"'\n" | railway variable set -s etl --stdin WAREHANCE_API_KEY --skip-deploys
railway variable list -s etl --json | python3 -c "import sys,json;print(sorted(json.load(sys.stdin)))"   # names only
```

**B1 ExpandFi (about 15 minutes, once Phase 2 ships the helper):**
1. `cd ~/lonvera-tag-explorer-projection && PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m etl.feeds.expandfi_auth login`. The helper runs the Railway CLI inside `~/lonvera-tag-explorer` (the linked directory; override with `FEEDS_RAILWAY_DIR`), opens https://app.expandfi.com/mcp/oauth/authorize in the browser. Log in as josh@drinkhydrant.com and approve scopes `mcp_read mcp_db`.
2. The helper then generates `FEEDS_ENC_KEY`, sets it on `etl` through the Railway CLI over stdin, encrypts the refresh token with it and stores it in `feed_oauth` through `DATABASE_PUBLIC_URL`. It prints only "stored" and the expiry it saw. No value is shown or written to a file.
3. Optional, recommended: email ExpandFi support from the same account: "We run a read-only unattended job for merchant 1452 against your MCP OAuth refresh token and `readonly_db_connection`. Is that supported, what is the refresh-token lifetime, and can you issue a long-lived service credential?" The answer decides the refresh-failure risk.

**B2 Meta system user (optional, about 15 minutes):** business.facebook.com > Business Settings > Users > System users > Add, name `lonvera-feeds` > Add assets > Ad accounts: Lonvera Main (`act_1671338033619223`) and BackUp (`act_1543579384022317`) with view access > Generate new token for app "Hydrant - Ad Account", scope `ads_read`, expiry Never > `pbpaste | railway variable set -s etl --stdin META_ACCESS_TOKEN_NEXT --skip-deploys`. Phase 2 tests it with `etl.meta_pull --dry-run` before anything swaps it into `META_ACCESS_TOKEN`.

## 5. Target design

### Tables (migration `180_proj_feeds.sql`, additive; reconcile with proj-pacing's migration 170, which already owns the `proj_feed_*` data tables)
Reuse, do not duplicate: `proj_feed_orders`, `_amazon`, `_platform`, `_attribution`, `_traffic`, `_meta_reach`, `_product_reach`, `_nc_product`, `_anyclick`, `_bids`, `_runs` (170); `proj_units_daily`, `proj_demand_snapshot`, `proj_units_loads` (140). Add only:
- `proj_feedx_<x>`: one shadow table per table above (12), `CREATE TABLE ... (LIKE proj_feed_<x> INCLUDING ALL)` in a DO block. Direct jobs write here during the dual-run.
- `proj_feed_config(feed text PRIMARY KEY, mode text CHECK (mode IN ('sheet','shadow','direct')) DEFAULT 'sheet', changed_at, changed_by, note)`: the switch. No redeploy to flip or roll back.
- `proj_feed_verify(feed, day, metric, db_value, sheet_value, abs_diff, rel_diff, ok, checked_at)` plus `proj_feed_verify_runs(run_id, feed, window_from, window_to, days_checked, days_failed, sheet_stale bool, at)`.
- `feed_oauth(provider PRIMARY KEY, enc_refresh_token bytea, access_expires_at, rotated_at)`: the only credential-bearing table, encrypted with `FEEDS_ENC_KEY`, read by no API route (CONTRACT: no secrets in `proj_*` tables, hence no `proj_` prefix). Needs `cryptography` in `REQUIREMENTS_REQUESTS.md`; skipped entirely if ExpandFi issues a static credential.
- Amazon revenue/orders by product: reuse `proj_units_daily(amazon)` for units; add `proj_feed_amazon_product` only if pacing asks for revenue by product.

### Code layout
```
etl/feeds/__init__.py      registry FEEDS = {key: Feed(source, tables, window_days, tz, fresh_after)}; run(conn, keys, days, mode_override)
etl/feeds/common.py        http() with retry/backoff, trailing_window(), replace_window() (same DELETE-then-INSERT-in-window as projection_pacing._write),
                           record_run() into proj_feed_runs (source 'api:upstack' | 'api:warehance' | 'api:meta' | 'api:expandfi' | 'sheet:<tab>'), scrub()
etl/feeds/upstack.py       refresh(conn, days=14): orders, platform, attribution, traffic, anyclick; freshness(conn)
etl/feeds/warehance.py     refresh(conn, days=5): shipments (Denver day), stock, intl estimate (Pacific day); freshness(conn)
etl/feeds/meta_feeds.py    refresh(conn, days=14): reach (day/week/month), product_reach (21d), bids (3d); reuses etl.meta_api.graph_get
etl/feeds/expandfi.py      OAuth refresh + MCP-over-HTTP + psycopg read-only session; refresh(conn, days=45): amazon, amazon_product, nc_product
etl/feeds/expandfi_auth.py one-time `login` helper
etl/feeds/verify.py        python -m etl.feeds.verify [--feed X] [--days 30] -> proj_feed_verify, exit 1 on failure
etl/feeds/__main__.py      python -m etl.feeds run [--only a,b] [--days N] | mode <feed> sheet|shadow|direct | status
scripts/deploy/run_feeds.sh  migrate, then python -m etl.feeds run
```
Contract fit (`MODULE_core.md` "Daily refresh"): each module's existing hook stays the entry point. `etl/projection_pacing.refresh` replaces `read_grid(feed.tab)` + `load_feed` with `feeds.load(feed.key, conn, days, sheet_fallback=lambda: read_grid(feed.tab))`; `projection_inventory_demand.refresh` and `projection_inventory_supply.refresh` do the same for their feeds. Both return the usual `{"rows", "data_through"}`; `freshness(conn)` is unchanged because it reads the target tables (`proj_feed_*`, `proj_units_daily`). `feeds.load` behaves by `proj_feed_config.mode`:
- `sheet`: today's behaviour.
- `shadow`: sheet path writes the main table as before; the direct pull writes `proj_feedx_*`. A direct failure never fails the module; it is recorded in `proj_feed_runs.note`.
- `direct`: direct pull writes the main table, `source='api:<x>'`. If it raises, fall back to the sheet read for that feed, record `FALLBACK to sheet: <scrubbed error>` in `proj_feed_runs.note`, and surface the feed as a warning in the runner output. Staleness still fails the run through the `daily` freshness rule.

Standalone entry for the second schedule: `python -m etl.feeds run` refreshes every direct-capable feed with no module involved.

### Windows, idempotency, limits
Every refresh replaces a trailing window (DELETE rows in window, INSERT the pull, one transaction per feed; a feed with an empty pull raises and writes nothing). Windows are at least 3x the cadence so a skipped cron run self-heals (Railway skips a run if the previous one is still active and does not catch up).

| Feed | Window per run | Day basis | API calls per run | Notes |
|---|---|---|---|---|
| orders, platform, traffic | 14 d | Pacific, through yesterday | 1 each | Upstack restates recent days |
| attribution, anyclick | 14 d / 7 d | Pacific | 14 / 7 (1 per day) | 6 retries on 502/504, 3 workers |
| meta reach | day 14 d, weeks current + 2 prior, months current + prior | Pacific (BackUp account is New York, dormant since July) | about 12 | whole period rewritten (reach is not additive) |
| product reach | 21 d | Pacific | about 60 | only from 2026-07-05; older stays untouched |
| bids | 3 d | Pacific | about 10 + log pages | past days cannot be re-pulled except from the activity log; seed once from the sheet |
| warehance shipments | 5 d | **Denver** (matches the sheet) | about 30 pages | voided excluded; unmapped SKU units reported, alert if > 0 |
| warehance stock | snapshot | as-of date | 2 pages | `proj_demand_snapshot` revamp |
| intl estimate | 21 d complete days only | Pacific | about 230 pages (~1.1K orders/day) | ratios from settings; `is_estimate`; a real 3PL count is never overwritten |
| amazon, amazon product | 45 d | `purchase_date` | 3 SQL + 3 MCP calls | ads restate; yesterday partial (2-day lag), do not load incomplete days |
| nc product | 21 d | Pacific | 2 to 3 SQL | the first-order scan needs a window-restricted customer list to dodge the statement timeout; store aggregates only (CONTRACT forbids customer-level rows) |

Rate limits: Meta calls go through `graph_get` (backoff to 900 s; codes incl. 80004). Run Meta feeds sequentially in one process. Upstack: 3 workers, sleep 0.2 s. Warehance: 0.2 s between pages, 6 attempts. ExpandFi: respect the statement timeout (150 s), retry a query once, mint a new credential if the DSN is older than 5 minutes (stale cache seen 2026-10-04), at most 3 mints per run. The shared runner caps a module at 600 s (`PROJECTION_REFRESH_TIMEOUT_SEC`); the `feeds` service sets 1800 s for itself.

### Secrets
Railway variables on `etl` (and the new service) only, names in section 3, set from local files over stdin. Code reads env, never logs a value; `feeds.common.scrub()` wraps `app.projection.freshness.scrub` (which already masks `UPSTACK_API_KEY` and `WAREHANCE_API_KEY`) and masks any env value whose name contains KEY, TOKEN, SECRET, PASSWORD, DSN or SA_JSON, plus Postgres URLs. Errors stored in `proj_refresh_runs` stay under 300 chars. The ExpandFi DSN lives in memory for one run.

### Alerting
1. Runner exit code 1 when a refresh fails or a `daily` feed is stale: Railway marks the deployment Failed and its dashboard notification fires. Whether Josh enabled Railway deploy-failure emails is (U).
2. In-app: `GET /api/projection/freshness` chips (fresh / stale / unknown) with `last_run`, plus the `FALLBACK` warnings.
3. Optional: `FEEDS_ALERT_WEBHOOK` (a Slack incoming webhook Josh creates) posts once per day per failing feed.
4. `verify_feeds` exits 1 on any failed day, so a dual-run regression fails the deployment.

## 6. Schedule and cost
- Keep `etl` at `0 13 * * *` UTC (05:00 or 06:00 Pacific): `run_etl.sh` runs ad data, then `projection_daily` (module hooks, which now include the feeds). Closed Pacific days are final enough by then; Amazon yesterday is partial by design.
- Add **one** Railway cron service `feeds`: same image, start `scripts/deploy/run_feeds.sh`, `0 21 * * *` (13:00 or 14:00 Pacific). It re-runs all direct feeds (idempotent) to pick up late Upstack, ExpandFi and Warehance days and Meta restatements, and runs `verify` during the dual-run. A separate service avoids changing the existing cron and avoids re-running the heavy all-time Meta pull. Changing `etl` to `0 13,21 * * *` is the alternative and is not recommended for that reason.
- Intraday "today so far" on the pacing page (Meta hourly curve exists in `proj_pacing_hourly`) would need a light job every 2 to 3 hours (Meta account spend today, Upstack today's orders). Josh's call; not in the estimate below.
- Cost (estimate, rates from memory, verify on Railway's pricing page): about 0.5 vCPU and 0.5 GB for 10 to 20 minutes per run is about $0.01 per run, so 2 to 3 runs a day is under $1 per month; feed tables stay under about 50 MB. Setting variables with `--skip-deploys` costs nothing. Creating the `feeds` service is the one infra change to approve.

## 7. Dual-run, verification, cutover

`python -m etl.feeds.verify` compares, per feed and per day over the last 30 days, the shadow table with the sheet-fed main table and (optionally, `--live-sheet`) with the live tab:

| Feed | Compare | Tolerance | Rule |
|---|---|---|---|
| orders | nc_rev, rc_rev, total_rev, tax, refunds; counts | $1 or 0.1%; counts exact | same API and Pacific days; settled days (older than 3) |
| platform | spend, purchases, value | 0.5% | |
| attribution, anyclick, traffic | counts exact; revenue $1 | | |
| meta reach, product reach | closed periods | 0.5% (the sheet's own reconciliation tolerance) | current partial week and month skipped |
| bids | spend, capped and floor sums | 1% | only days both sides have |
| warehance units | per product-day | exact | complete Denver days |
| warehance stock | on hand, allocated | exact if as-of within 2 h, else +-3% info only | |
| intl estimate | per product-day | exact (same algorithm and ratios) | |
| amazon | orders, units, revenue, NC | cents exact on settled days | ads 0.5% |
| nc product | per product-day | $1 or 0.2%; total within 3% of Upstack NC rev (the existing `verify_product_tabs` rule) | |

Settled days only: the last 3 days are compared at 3x tolerance and never fail a day. If the sheet tab itself is older than its slot (Mac asleep), the result is recorded as `sheet_stale` and neither passes nor fails (it is the very problem being fixed).

**Clean** = for N consecutive daily runs: runner status ok, every compared day within tolerance, `data_through` at least yesterday (Pacific) by 14:00 UTC for daily feeds, no `FALLBACK` events, no unmapped SKU units. N = 7 for class A, 14 for ExpandFi (also needs at least 3 successful refresh-token rotations or a documented stable lifetime).

**Cutover per feed (reversible, the app's source only):**
1. `python -m etl.feeds mode <feed> shadow` as soon as the credential exists.
2. After N clean days, show Josh the verify table and ask for approval.
3. `python -m etl.feeds mode <feed> direct`; watch 3 runs.
4. **Rollback:** `python -m etl.feeds mode <feed> sheet`; the next refresh restores the main table from the sheet (`backfill` for a longer span). Takes seconds, no deploy.

**Turning off a desktop writer** is a separate decision. Do it only when: the feed has been `direct` and clean for 7 more days; the alert path was tested once with a deliberately bad credential in a one-off run; the rollback was rehearsed once; and Josh accepts that the old sheet's own pacing tabs go stale for that feed (or has retired the sheet, SCOPE decision 5). Mechanism: remove the job from `tick.py` `JOBS` (keep the script) or pause the task. Rollback: re-add it and `tick.py --run NAME`; the 21 to 30 day windows heal the gap (`sync.py --backfill-days N` for longer). A Railway-to-sheet mirror would keep the old sheet alive after the Mac stops; it needs its own build and is out of this plan.

## 8. Risks
| Risk | Effect | Mitigation |
|---|---|---|
| ExpandFi refresh token expires or rotates unexpectedly (the 2026-09-29 headless failure) | Amazon and NC by product go stale | store rotated token transactionally, alert on first refresh failure, `sheet` fallback keeps the desktop routine's data, ask support for a service credential |
| ExpandFi DB refuses Railway egress or enforces per-hour limits (U) | B1 not possible | Phase 2 spike (30 minutes) before any code is written beyond the auth helper; hedge options 3 and 4 |
| Meta user token: past data-access date, tied to Josh's login | Tag Explorer ETL and all Meta feeds stop together | B2 system-user token; `TokenRejected` already fails loudly |
| Meta 80004 and run time (reach plus product reach add about 70 calls) | runner timeout, partial days | sequential, per-feed timeouts, `feeds` service at 1800 s, product reach limited to 21 d |
| Day-basis mismatch (Warehance Denver, Meta BackUp New York, Upstack Pacific) | false verify failures | each feed keeps the sheet's basis; documented per feed |
| Restatements (Meta, Amazon ads, Upstack) | drift between jobs on different clocks | trailing windows, settled-days-only checks |
| New SKU or product name | units silently dropped or classed Other | unmapped-unit counters raise an alert |
| Upstack undocumented endpoints and pinned CLI-version header | break without notice | contract tests on measure names; fail loudly on empty pulls |
| Intl estimate has no truth after 09-15 | demand error of unknown size | keep `is_estimate` flag visible; ask Josh for the 3PL source |
| Railway cron skips, no catch-up | one missed day | windows >= 3x cadence; second daily run |
| Credential exposure | key leak | stdin-only setting, scrub wrapper, no values in logs or DB except the encrypted refresh token |

## 9. Build order and effort (agent-days, including tests and backfill)
| # | Step | Effort | Needs |
|---|---|---|---|
| 0 | Quick win: inventory-demand reads Amazon units from `Amazon - Product Daily` (fresh) instead of `Amazon Daily Units` (frozen 10-11) | 0.25 | nothing |
| 1 | Foundation: `etl/feeds/common.py`, registry, mode switch, shadow tables, `verify` skeleton, migration 180, `run_feeds.sh` | 1.0 | |
| 2 | Warehance (shipments, stock, intl estimate; verify vs sheet) | 1.0 | `WAREHANCE_API_KEY` |
| 3 | Upstack (5 feeds; one-time history seed from the sheet) | 1.0 | `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID` |
| 4 | Meta reach, product reach, bids (bids history seed) | 1.5 | existing token |
| 5 | Wire module hooks (pacing, inv-demand, inv-supply) with sheet fallback; second cron service; runbook | 1.0 | orchestrator approval for the new service |
| 6 | Dual-run starts (7 days) | 0 (calendar) | |
| 7 | ExpandFi spike: auth helper, MCP call, DB reachability, one refresh | 0.5 | Josh's consent click |
| 8 | ExpandFi feeds: amazon, amazon product, nc product, rotation handling | 2.0 to 3.0 | step 7 passes |
| 9 | ExpandFi dual-run (14 days), then cutovers | 0 (calendar) | |
| | **Total** | **about 8 to 9 agent-days** | |

Calendar: steps 1 to 5 in about 5 working days; A feeds cut over about day 14; B feeds about day 24. If the spike fails, steps 8 and 9 are replaced by hedge options (Shopify token for NC by product, 1 day; Amazon via SP-API and Ads API, 3 to 4 days plus approvals) or by keeping the desktop task with a visible chip.

## 10. Open questions
1. ExpandFi server-side OAuth, with or without asking ExpandFi first, or hedge with a Shopify custom app / keep the desktop task?
2. After the Mac stops writing a tab, should a Railway job mirror the feeds back into the old sheet until it is retired?
3. Does the overseas 3PL have an API or scheduled export (replaces the Warehance-derived estimate)? Who produces the monthly tracking file now?
4. Does the pacing page need "today so far" (adds an intraday job)?
5. New Railway service `feeds` (recommended) or a second schedule on `etl`?
6. Slack webhook for feed alerts, and are Railway deploy-failure emails on?
7. FBA fulfillable stock: from ExpandFi (a table or the Dash inventory dataset, not checked) or stay a typed snapshot?
8. Is the cohort export an ExpandFi report or a Shopify export (it decides whether the ExpandFi client can later automate it)?
