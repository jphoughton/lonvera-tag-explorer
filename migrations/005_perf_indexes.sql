-- API read paths (agent B): drive lifetime lookups from a Meta family key instead of scanning meta_ad_lifetime.
-- ad_daily (meta_family_key, day) and (ad_id, day), meta_ads (meta_family_key) and family_daily (family_key, ...) already exist.
CREATE INDEX IF NOT EXISTS meta_ad_lifetime_family ON meta_ad_lifetime (meta_family_key);
