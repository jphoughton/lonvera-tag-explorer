-- ETL hardening after the skeptic QA (applies after 007_active.sql).
-- 1. tracker rows: soft delete + content fingerprint (stable identity for duplicate (concept_id, ad_family) groups)
ALTER TABLE tracker_rows ADD COLUMN deleted_at timestamptz;          -- set when a sync no longer sees the row, cleared if it reappears; deleted rows are excluded from ad_metrics / rollups
ALTER TABLE tracker_rows ADD COLUMN fingerprint text;                 -- md5(Concept Name | Short Description | Hypothesis | Brief Date)
CREATE INDEX tracker_rows_live_family ON tracker_rows (family_key) WHERE deleted_at IS NULL;

-- 2. Meta ads: exact creation instant (created_date = its Pacific date), current family display name, status check time
ALTER TABLE meta_ads ADD COLUMN created_at timestamptz;
ALTER TABLE meta_ads ADD COLUMN family_name text;                     -- fam(ad_name) in original case; meta_family_key = lower(family_name)
ALTER TABLE meta_ads ADD COLUMN status_checked_at timestamptz;

-- 3. product guess for a Meta family name: first products.meta_regex (by meta_priority) that matches the text
CREATE FUNCTION product_for(txt text) RETURNS int LANGUAGE sql STABLE AS
  $$ SELECT id FROM products WHERE meta_regex IS NOT NULL AND txt ~* meta_regex ORDER BY meta_priority LIMIT 1 $$;

-- 4. 'Active ads only' (CONTRACT v1.9): family_daily_active = family_daily restricted to Meta ads whose current status is ACTIVE; the *_active columns of
--    ad_metrics are the same since-launch figures over the ACTIVE ads only (carried by the family's first row; ads_active_count sits on every row of the creative)
CREATE TABLE family_daily_active (LIKE family_daily INCLUDING ALL);
ALTER TABLE ad_metrics ADD COLUMN ads_active_count          int           NOT NULL DEFAULT 0;   -- ACTIVE Meta ads of the family (same on every tracker row of the family)
ALTER TABLE ad_metrics ADD COLUMN spend_launch_active       numeric(14,2) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN purchases_launch_active   numeric(12,2) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN revenue_launch_active     numeric(14,2) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN impressions_launch_active numeric(14,0) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN clicks_launch_active      numeric(12,0) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN live_active               boolean       NOT NULL DEFAULT false;

-- 5. unmatched Meta families carry their since-launch (API lifetime) numbers, incl. families that only exist in meta_ad_lifetime,
--    and say where the product guess came from ('name' | 'campaign' | 'adset' | 'none')
ALTER TABLE unmatched_meta ADD COLUMN spend_launch       numeric(14,2) NOT NULL DEFAULT 0;
ALTER TABLE unmatched_meta ADD COLUMN purchases_launch   numeric(12,2) NOT NULL DEFAULT 0;
ALTER TABLE unmatched_meta ADD COLUMN revenue_launch     numeric(14,2) NOT NULL DEFAULT 0;
ALTER TABLE unmatched_meta ADD COLUMN impressions_launch numeric(14,0) NOT NULL DEFAULT 0;
ALTER TABLE unmatched_meta ADD COLUMN clicks_launch      numeric(12,0) NOT NULL DEFAULT 0;
ALTER TABLE unmatched_meta ADD COLUMN product_source     text          NOT NULL DEFAULT 'name';
