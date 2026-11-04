# Projection feeds (server-side pulls)

What it replaced (once cut over): the desktop routine `lonvera-sync-tick` and the two desktop tasks as the writers of the sheet raw tabs the projection reads. It pulls straight from Upstack, Warehance and Meta into Postgres on a Railway cron. Nothing here writes to the sheet, Meta, Upstack or Warehance. Design, inventory and cutover plan: `FEEDS_PLAN.md`.

## Files
```
migrations/180_proj_feeds.sql        mode switch, attempt log, verify tables, levers, raw non-US table + estimate view, 12 shadow tables, feed_oauth
etl/feeds/__init__.py                registry, pull_and_write, load() (module hook), run(), freshness(), status()
etl/feeds/common.py                  Ctx, scrub, Http (retry/backoff/limits), RateLimiter, trailing_span, replace_window, mode/lever/attempt helpers
etl/feeds/feed.py                    Feed and Metric definitions
etl/feeds/upstack.py                 orders, platform, attribution, traffic, anyclick
etl/feeds/warehance.py               units_revamp, stock_revamp, units_intl, intl_estimate
etl/feeds/meta_feeds.py              meta_reach, product_reach, bids
etl/feeds/verify.py                  compare_rows, verify_feed, clean_streak, run_verify
etl/feeds/dryrun.py                  real read-only API pull diffed with the live sheet tab
etl/feeds/expandfi_auth.py           OAuth login/refresh, encrypted token storage, MCP client, spike, tcp check
etl/feeds/__main__.py                CLI
scripts/deploy/run_feeds.sh          service entrypoint (migrate, run, verify)
scripts/deploy/apply_feeds_service.py   dry-run-by-default creation of the Railway cron service `feeds`
tests/projection/feeds/              88 tests
```

## Commands
```
python -m etl.feeds mode [FEED|all] [sheet|shadow|direct]   show / set the source for a feed (stored in proj_feed_config; default sheet; native feeds default shadow)
python -m etl.feeds run [--only a,b] [--days N]              pull every feed in shadow or direct mode; exit 1 on a failed pull or a stale direct feed
python -m etl.feeds verify [--feed a,b] [--days 30] [--need 7] [--live-sheet]   shadow vs sheet-fed table per day; prints consecutive clean days
python -m etl.feeds dryrun --feed X [--days 5]               real read-only pull, diff with the live sheet tab, no writes
python -m etl.feeds status | freshness | lever [KEY VALUE --note ...]
python -m etl.feeds.expandfi_auth {login|refresh|spike|tcp HOST PORT}
```
Local run needs `DATABASE_URL`. `dryrun` needs no database; credentials come from the environment or the local files the desktop jobs use.

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

Meta spend, clicks, purchases and revenue by ad and day are the Tag Explorer's `ad_daily` (no new pull). Amazon sales and ads, and new-customer revenue by product, need ExpandFi (shadow tables exist; the feeds are not built until the spike passes).

## International estimate (`intl_estimate`, `units_intl`)
Non-US ship-to orders on Warehance in any status (Shopify routes them to the overseas warehouse and Warehance auto-cancels them), bottles = metadata quantity x the bundle multiplier in the SKU suffix, dated by Pacific order date. `proj_feedx_intl_nonus_daily(day, product, country, units_raw)` holds the raw counts. The estimate is raw x the lever `intl_ratio.<product>` in `proj_feed_levers` (seeded 0.905 / 0.895 / 0.868, default 0.89; edit with `python -m etl.feeds lever intl_ratio.bull 0.9 --note ...`). View `proj_feedx_intl_estimate(day, product, units_raw, factor, units, estimated)` applies the levers. `units_intl` writes the rounded estimate as `proj_units_daily` rows exactly as the sheet-fed loader does (source `estimate`, `is_estimate` true, Vital Flow written as 0). Vital Flow lines never appear on non-US Warehance orders, so its international demand is not estimated by anything (it is 0 in the sheet too).

## Dry-run against the live sheet (2026-11-04, read-only)
See the table in `FEEDS_PLAN.md` section "Phase 2 status". Intl estimate vs `Intl Daily Units`, Sep 16 to Oct 12 (27 days x 3 products): totals 12,607 vs 12,611 (-0.03%), summed absolute difference 6 units, 23 of 27 days identical; a flat 0.89 would differ by 208 units.

## Differences from the sheet-fed path
- Stock and shipments keep the sheet's day bases (Denver); the international estimate uses Pacific days.
- Days the desktop job froze when written (international estimates are written once) can differ by a unit or two when Warehance restates an order afterwards.
- The native raw table `proj_feedx_intl_nonus_daily` stores only positive counts; the main-table feeds write zero rows like the sheet does.
- Rounding: python half-to-even in `units_intl`, half-up in the view `proj_feedx_intl_estimate`.

## Open items
- Railway variables `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID`, `WAREHANCE_API_KEY` on `etl`: the write was refused by the permission system in this session; the commands are in `FEEDS_PLAN.md`.
- `feeds` service not created (`apply_feeds_service.py` is dry-run by default; `--apply` and `railway up -s feeds --ci` are the production steps).
- The module hook-ups (step 5) are written as diffs in `FEEDS_PLAN.md`; the modules' files were not touched.
- ExpandFi: `login` not run (Josh); the TCP reachability of the warehouse host is untested because the host is only reported by the MCP after login.
- `cryptography` is requested in `REQUIREMENTS_REQUESTS.md`.
