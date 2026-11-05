-- New-customer revenue by Pacific clock hour (feed nc_hourly, ExpandFi Shopify first orders). Feeds the hourly share curve behind the
-- "NC revenue today" card on Projections > Pacing. Additive and idempotent; aggregates only (no customer or order identifiers).
-- No sheet twin: the feed is native (default mode shadow) and this one table is both its main and its shadow table.
CREATE TABLE IF NOT EXISTS proj_feed_nc_hourly (
  day        date        NOT NULL,                         -- Pacific day of the first order's processed_at
  hour       smallint    NOT NULL CHECK (hour BETWEEN 0 AND 23),   -- Pacific wall-clock hour; the 25-hour fall-back day merges both 1 a.m. hours into 1
  nc_rev     numeric(18,2) NOT NULL DEFAULT 0,             -- order totals of that hour's first orders (tax included, before refunds, like upstack_orders_daily.nc_rev)
  nc_orders  integer     NOT NULL DEFAULT 0,               -- first orders in that hour
  loaded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, hour)
);
COMMENT ON TABLE public.proj_feed_nc_hourly IS 'New-customer revenue and first orders by Pacific day and clock hour (Shopify first orders from the ExpandFi warehouse); the hourly share curve behind the ''NC revenue today'' card on Projections > Pacing. Grain: one row per (day, hour) with at least one first order. Source: etl.feeds nc_hourly (ExpandFi warehouse shopify_orders); no sheet tab carries it. Refresh: feeds service 21:00 UTC, trailing 35 closed days. Coverage: from the first pull (60-day backfill once). Caveats: Sparse: an hour with no first order has no row. Today is never stored. The 25-hour fall-back day merges both 1 a.m. hours into hour 1. Order totals include tax and are before refunds, like upstack_orders_daily.nc_rev; day sums equal proj_feed_nc_product. Aggregates only.';
COMMENT ON COLUMN public.proj_feed_nc_hourly.day IS 'Pacific day of the first order.';
COMMENT ON COLUMN public.proj_feed_nc_hourly.hour IS 'Pacific wall-clock hour 0-23.';
COMMENT ON COLUMN public.proj_feed_nc_hourly.nc_rev IS 'Sum of order total_price of the first orders in that hour (tax included, before refunds); test orders and orders without priced lines excluded.';
COMMENT ON COLUMN public.proj_feed_nc_hourly.nc_orders IS 'Number of first orders in that hour.';
COMMENT ON COLUMN public.proj_feed_nc_hourly.loaded_at IS 'When loaded.';
