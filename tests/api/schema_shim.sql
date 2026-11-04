-- Schema the API codes against that migration 007 (agent A) adds. The test database applies it after migrations/*.sql: everything is
-- IF NOT EXISTS, so it does nothing once the real migration is in place (names and meanings per the coordinator's v1.9 update).
ALTER TABLE tracker_rows ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE TABLE IF NOT EXISTS family_daily_active (LIKE family_daily INCLUDING ALL);
ALTER TABLE ad_metrics
  ADD COLUMN IF NOT EXISTS spend_launch_active numeric(14,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS purchases_launch_active numeric(12,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS revenue_launch_active numeric(14,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS impressions_launch_active numeric(14,0) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS clicks_launch_active numeric(12,0) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS ads_active_count int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS live_active boolean NOT NULL DEFAULT false;
ALTER TABLE unmatched_meta
  ADD COLUMN IF NOT EXISTS spend_launch numeric(14,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS purchases_launch numeric(12,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS revenue_launch numeric(14,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS impressions_launch numeric(14,0) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS clicks_launch numeric(12,0) NOT NULL DEFAULT 0;
