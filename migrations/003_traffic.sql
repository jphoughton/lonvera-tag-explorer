-- Traffic metrics: impressions per ad per day (NULL = unknown, e.g. sheet-backfilled days), plus family_daily traffic sums.
ALTER TABLE ad_daily ADD COLUMN impressions numeric(14,0);
ALTER TABLE family_daily ADD COLUMN impressions   numeric(14,0) NOT NULL DEFAULT 0;   -- sum over rows with traffic data
ALTER TABLE family_daily ADD COLUMN clicks_tr     numeric(12,0) NOT NULL DEFAULT 0;   -- link clicks over rows with traffic data
ALTER TABLE family_daily ADD COLUMN spend_tr      numeric(14,2) NOT NULL DEFAULT 0;   -- spend over rows with traffic data (the CPC / CPM base)
