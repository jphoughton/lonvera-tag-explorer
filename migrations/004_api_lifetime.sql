-- API-driven since-launch: lifetime per Meta ad straight from the Graph API (replaces the sheet's Lifetime snapshot + cutoff rule).
ALTER TABLE meta_ad_lifetime ADD COLUMN impressions numeric(14,0);
ALTER TABLE meta_ad_lifetime ADD COLUMN link_clicks numeric(12,0);
ALTER TABLE meta_ad_lifetime ADD COLUMN source text NOT NULL DEFAULT 'sheet';      -- 'meta_api' | 'sheet'
ALTER TABLE meta_ad_lifetime ADD COLUMN pulled_at timestamptz;
ALTER TABLE meta_ad_lifetime ADD COLUMN first_day date;                              -- earliest delivery day if known
ALTER TABLE ad_metrics ADD COLUMN impressions_launch numeric(14,0) NOT NULL DEFAULT 0;
ALTER TABLE ad_metrics ADD COLUMN clicks_launch      numeric(12,0) NOT NULL DEFAULT 0;
