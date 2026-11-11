# Shopify orders, last 120 days

Last verified: 2026-11-10 (read-only pull of the real window from ExpandFi and a load into a local scratch database; the production migration and load are not run yet).

Two tables hold order-level Shopify data for the last 120 Pacific days, rolling: `shopify_orders` (one row per order) and `shopify_order_lines` (one row per line item). Migration `210_shopify_orders.sql` (the numbers 202 to 209 are skipped on purpose: 202 and 203 live on an unmerged branch). Column meanings are in `docs/DATA_SCHEMA.md` (group "Actual orders").

## Source and privacy

ExpandFi warehouse, merchant 1452, table `shopify_orders` (read-only), through the same login and minted read-only credential as the other ExpandFi feeds (`etl.feeds.expandfi_feeds.get_warehouse`). The warehouse has no separate line-item table: lines come from the `line_items` array of each order's raw Shopify JSON. Nothing personal is stored: no email, name, address or phone. `customer_id` is the Shopify customer id, the ship-to is country and region only.

## Loader

`python -m etl.shopify_orders [--days N] [--full] [--dry-run]` (`etl/shopify_orders.py`).

- Schedule: it rides the `feeds` service cron `0 21 * * *` UTC (`scripts/deploy/run_feeds.sh`, after the feeds and `etl.amazon_mtd`, before verify). No new service, no new schedule.
- Window: orders whose `processed_at` falls in the last 120 Pacific days including today. `order_date` is the Pacific day of `processed_at`. The warehouse `created_at` is a sync time (September backfill) and is never used.
- First load, and any run where the table is empty or does not reach back to the window start: the whole 120 days. Every Sunday (Pacific): the whole 120 days. Every other night: the trailing 14 days, so restated, refunded and cancelled orders overwrite. `--days N` and `--full` force a window.
- One transaction per run: upsert orders, replace the lines of the pulled orders, delete orders older than 120 days (lines cascade). A run that finds no order writes nothing and fails.
- Idempotent: a re-run overwrites the same rows; nothing is duplicated.
- Failure is loud: the run is recorded in `etl_runs` (job `shopify_orders`, status `failed`, error text), the process exits 1, `run_feeds.sh` exits non-zero so the Railway `feeds` deployment shows Failed, and the alert watcher (`etl/alerts.py`, job `shopify_orders`, nightly cadence) raises a finding when no good run exists in the cadence window. The tables keep the last good rows; `shopify_orders.loaded_at` shows their age.

## Rules

- New customer: the customer's first Shopify order ever (customer key = Shopify customer id, else lower-cased email; earliest `processed_at`, then id), judged over the whole warehouse history. Same definition as the new-customer revenue feed.
- Rebill: `source_name = subscription_contract_checkout_one`. Gift: tag `trybe` ($0 sample orders). Test and cancelled orders are kept and flagged (`is_test`, `is_cancelled`); filter on the flags.
- Product from the SKU prefix: `LV-VBULL` VitaBull; `LV-BLC180` and `DPCD1-GN180001` BeefLiver; `DPCD1-GN187001` Boneflex; `LV-VBELLE` VitaBelle; `DPCD1-GN179001` VitaFlow; anything else Other.
- `refunded_amount = max(total_price - current_total_price, 0)`. The warehouse's refund transactions are empty for many refunds, while Shopify's current total already nets refunds and edits. It is a net-down amount, not a refund ledger.
- Orders restated after leaving the trailing 14 days are picked up by the Sunday full pull, so a late refund can lag up to a week.

## Size (read-only count, 2026-11-10)

87,383 orders and 126,703 lines for 2026-07-14 to 2026-11-10 (Pacific): 25,540 first orders, 5,749 cancelled, 25,986 rebills, 88 Trybe gifts, 5,352 with a refund or edit down, 0 test orders. Pull about 30 seconds in 13 queries; local write of the first load about 14 seconds.

## Backup

The nightly `backup` service runs `pg_dump -Fc` of the whole database (no table list), so both tables and their data are in every dump (checked with `pg_restore -l` on a dump of a migrated scratch database). `etl/backup.py` also lists both tables in `KEY_TABLES`, so the restore check compares their row counts with the live database.

Restore test: restore the newest good dump into a scratch database (steps in `docs/BACKUP.md`), then

```
psql restore_check -c "select count(*), min(order_date), max(order_date) from shopify_orders"
psql restore_check -c "select count(*) from shopify_order_lines"
```

The counts must match the live tables as of the dump (about 87K orders and 127K lines, dates through the dump day). The nightly job already performs the count comparison; this is the manual check.

## Deploy (main session)

1. Merge the PR into `build/v1`; the `feeds` service migrates on start (`python -m etl.migrate` in `run_feeds.sh`), which creates the tables.
2. First load: `railway run -s feeds python -m etl.shopify_orders` (whole 120 days, empty table). Needs `FEEDS_ENC_KEY` and the stored ExpandFi login, which `feeds` already has.
3. Check `select count(*), max(loaded_at) from shopify_orders`, the `etl_runs` row, and the next 21:00 UTC run.
