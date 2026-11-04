-- Projection daily refresh: one row per module per run of `python -m etl.projection_daily` (Railway cron, after etl.run_daily).
-- Additive and idempotent. error is scrubbed text, never a secret, at most 300 characters.

CREATE TABLE IF NOT EXISTS proj_refresh_runs (
  id            bigserial PRIMARY KEY,
  module        text NOT NULL,
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
  rows          int,
  data_through  date,
  error         text CHECK (error IS NULL OR length(error) <= 300)
);
CREATE INDEX IF NOT EXISTS proj_refresh_runs_module ON proj_refresh_runs (module, id DESC);
