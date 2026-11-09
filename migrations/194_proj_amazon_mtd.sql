-- 194: nightly snapshot of the completed Amazon month-to-date (python -m etl.amazon_mtd). Amazon revenue reaches the feed with a lag, so month to date reads low;
-- the pacing page projects the missing days at the trailing 7-day average of the reported days (Josh's rule) and shows the result with an asterisk. The page reads
-- the newest snapshot when it is current and computes live otherwise (the payload says which). One row per Pacific day and month, history kept: it lets us later
-- compare each day's projection with the month's final actual. Additive and idempotent. Aggregates only.
CREATE TABLE IF NOT EXISTS proj_amazon_mtd_snapshots (
  snapshot_day    date          NOT NULL,                   -- the Pacific day of the run (a re-run the same day replaces the row)
  month           date          NOT NULL,                   -- first day of the month that was completed
  computed_at     timestamptz   NOT NULL,
  data_through    date          NOT NULL,                   -- last day that should have been complete when it ran (yesterday)
  last_actual_day date,                                     -- last day of the month with reported Amazon revenue (null before the first one)
  days_actual     integer       NOT NULL CHECK (days_actual >= 0),
  days_projected  integer       NOT NULL CHECK (days_projected >= 0),
  daily_rate      numeric(18,2),                            -- projected dollars per missing day (trailing 7 available days); null when no rate
  actual_mtd      numeric(18,2) NOT NULL,
  projected_mtd   numeric(18,2) NOT NULL,                   -- actual_mtd + projected days + partial-day top-up
  status          text          NOT NULL CHECK (status IN ('complete','projected','stale','no_rate','no_days')),
  method          text          NOT NULL,
  flags           text[]        NOT NULL DEFAULT '{}',
  projected_days  jsonb         NOT NULL DEFAULT '[]'::jsonb,   -- [{"day": "2026-11-06", "revenue": 5123.45}, ...]
  partial_topup   numeric(18,2) NOT NULL DEFAULT 0,
  lag_days        integer,
  rate_source     text,
  note            text,
  PRIMARY KEY (snapshot_day, month)
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.proj_amazon_mtd_snapshots IS 'Completed Amazon month-to-date revenue, one snapshot per Pacific day and month: reported days plus the missing days projected at the trailing 7-day average of the reported days; read by Projections > Pacing instead of computing on every page load. Grain: one row per (snapshot_day, month). Source: proj_feed_amazon.revenue through etl.amazon_mtd. Refresh: after the Amazon feed load in the feeds service (21:00 UTC) and again in the etl service (13:00 UTC); a same-day re-run replaces the row. Coverage: from the first run; history kept so each day''s projection can be compared with the final month. Caveats: projected_mtd is an estimate, never an actual; a later feed load restates days (the page then computes live); Amazon revenue is total_amount_usd by purchase day, Amazon.com plus B2B. Aggregates only.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.snapshot_day IS 'Pacific day of the run.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.month IS 'First day of the month that was completed.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.computed_at IS 'When the run computed it (UTC).';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.data_through IS 'Last day that should have been complete at the run (yesterday by default).';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.last_actual_day IS 'Last day of the month with reported Amazon revenue; null before the first reported day.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.days_actual IS 'Calendar days from the 1st through the last reported day.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.days_projected IS 'Days after the last reported day, through data_through, that were projected (0 when the feed is current or stale).';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.daily_rate IS 'Dollars per projected day: mean of the last 7 reported days with revenue above zero (fewer when fewer exist).';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.actual_mtd IS 'Reported Amazon revenue from the 1st through the last reported day, dollars.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.projected_mtd IS 'actual_mtd plus the projected days plus any partial-day top-up, dollars.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.status IS 'complete (nothing to project), projected, stale (feed more than 5 days behind: nothing projected), no_rate (no revenue in the feed), no_days (month has no complete day).';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.method IS 'Rule and version that made the row, e.g. trailing-7-available/v1.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.flags IS 'Plain flags: feed_stale, short_window, window_has_gaps, gap_days_inside_month, newest_day_partial, no_data.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.projected_days IS 'The projected days as json: [{day, revenue}], so a reader can rebuild the completed month without recomputing.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.partial_topup IS 'Dollars added because the newest reported day looked partial (below half the days before it); 0 otherwise.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.lag_days IS 'Days between data_through and the last reported day.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.rate_source IS 'trailing_7_available, or previous_month_last_7 when the month has no reported day yet.';
COMMENT ON COLUMN public.proj_amazon_mtd_snapshots.note IS 'The plain-language line the page shows.';
