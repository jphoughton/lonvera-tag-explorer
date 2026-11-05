-- 192: intraday snapshots of TODAY (Pacific day so far) pulled from Upstack once an hour (python -m etl.live_today). Feeds the two cards on
-- Projections > Pacing ('Meta spend today', 'NC revenue today') so nobody types "spent so far". One row per pull, history kept (it also lets us check
-- later how accurate the intraday projections were). Additive and idempotent. Aggregates only.
CREATE TABLE IF NOT EXISTS proj_live_today_snapshots (
  pulled_at     timestamptz   PRIMARY KEY,                  -- when Upstack was asked, truncated to the minute (a re-run inside the same minute replaces the row)
  day           date          NOT NULL,                     -- the Pacific day the numbers belong to
  pacific_time  time          NOT NULL,                     -- Pacific wall-clock time of the pull, to the minute (the clock the hourly curves use)
  meta_spend    numeric(18,2) NOT NULL CHECK (meta_spend >= 0),
  google_spend  numeric(18,2) NOT NULL CHECK (google_spend >= 0),
  total_spend   numeric(18,2) NOT NULL CHECK (total_spend >= 0),
  nc_rev        numeric(18,2) NOT NULL CHECK (nc_rev >= 0),
  nc_orders     integer       NOT NULL CHECK (nc_orders >= 0)
);
CREATE INDEX IF NOT EXISTS proj_live_today_snapshots_day ON proj_live_today_snapshots (day, pulled_at DESC);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.proj_live_today_snapshots IS 'Today''s Meta spend, Google spend, new-customer revenue and new customers so far, one snapshot per hourly pull from Upstack; fills the ''Meta spend today'' and ''NC revenue today'' cards on Projections > Pacing. Grain: one row per pull (pulled_at, to the minute). Source: etl.live_today (Upstack /api/query and /api/query-channel-details for the Pacific day so far); no sheet tab carries it. Refresh: Railway cron `live`, every hour on the hour. Coverage: from the first pull; history kept 90 days. Caveats: Upstack''s spend for the current day trails the ad platforms by some minutes, so the figures are a floor of what has been spent and sold. Same measures as upstack_orders_daily (nc_rev = orders.new_customer_total_revenue, tax included, before refunds), so a closed day ties. Aggregates only.';
COMMENT ON COLUMN public.proj_live_today_snapshots.pulled_at IS 'When Upstack was asked, to the minute (UTC).';
COMMENT ON COLUMN public.proj_live_today_snapshots.day IS 'Pacific day the numbers belong to.';
COMMENT ON COLUMN public.proj_live_today_snapshots.pacific_time IS 'Pacific wall-clock time of the pull, to the minute.';
COMMENT ON COLUMN public.proj_live_today_snapshots.meta_spend IS 'Meta (Facebook / Instagram) spend so far that day, dollars, all ad accounts Upstack sees.';
COMMENT ON COLUMN public.proj_live_today_snapshots.google_spend IS 'Google Ads spend so far that day, dollars.';
COMMENT ON COLUMN public.proj_live_today_snapshots.total_spend IS 'Spend so far across every ad platform Upstack reports (Meta, Google, TikTok), dollars.';
COMMENT ON COLUMN public.proj_live_today_snapshots.nc_rev IS 'New-customer revenue so far that day, dollars (orders.new_customer_total_revenue).';
COMMENT ON COLUMN public.proj_live_today_snapshots.nc_orders IS 'New customers (first orders) so far that day.';
