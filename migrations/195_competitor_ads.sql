-- 195: competitor ads on Meta (weekly snapshot of what others run now, classified into Lonvera's taxonomy). Filled by `python -m etl.competitor_ads` (Sundays, Pacific).
--   competitor_brands        who we watch (editable): name, product type, exact search phrases (brand name, landing domain), advertiser-name regex, freshness / last error
--   competitor_ads           one row per ACTIVE US ad (Meta Ad Library card) per week; a brand that fails to load carries its last good rows forward (carried_from set)
--   competitor_concept_class one classification per concept_key (near-duplicate copy of one brand), cached so only new concepts use Claude plan usage
--   competitor_concepts      VIEW, the contract read by the Creative priorities one-sheeter: week x brand x concept
-- No spend, impressions or performance: the Ad Library shows none for commercial ads; days_running (start date to the day we looked) is the only signal.
CREATE TABLE IF NOT EXISTS competitor_brands (
  brand           text PRIMARY KEY,
  product_type    text NOT NULL DEFAULT '',
  search_phrases  text[] NOT NULL,
  name_match      text NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  last_attempt_at timestamptz,
  last_ok_at      timestamptz,
  last_error      text,
  last_ad_count   integer
);

CREATE TABLE IF NOT EXISTS competitor_ads (
  week_start     date NOT NULL,
  library_id     text NOT NULL,
  brand          text NOT NULL REFERENCES competitor_brands(brand) ON DELETE CASCADE,
  advertiser     text,
  start_date     date,
  days_running   integer,
  variant_count  integer NOT NULL DEFAULT 1,
  format         text,
  primary_text   text,
  headline       text,
  hook           text,
  landing_url    text,
  concept_key    text NOT NULL,
  seen_on        date NOT NULL,
  carried_from   date,
  PRIMARY KEY (week_start, library_id)
);
CREATE INDEX IF NOT EXISTS competitor_ads_brand_week ON competitor_ads (brand, week_start);
CREATE INDEX IF NOT EXISTS competitor_ads_concept ON competitor_ads (concept_key);

CREATE TABLE IF NOT EXISTS competitor_concept_class (
  concept_key      text PRIMARY KEY,
  brand            text NOT NULL,
  mapped_product   text,
  line_code        text,
  cluster          text,
  icp              text,
  pain_point       text,
  confidence       text CHECK (confidence IN ('high', 'medium', 'low')),
  is_new_angle     boolean NOT NULL DEFAULT false,
  new_angle_label  text,
  why              text,
  model            text,
  classified_at    timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW competitor_concepts AS
SELECT a.week_start,
       a.brand,
       a.concept_key,
       min(a.start_date)                              AS first_ad_start_date,
       max(a.days_running)::integer                   AS days_running,
       sum(a.variant_count)::integer                  AS variant_count,
       (array_agg(a.format ORDER BY a.days_running DESC NULLS LAST, a.library_id))[1]        AS format,
       (array_agg(a.hook ORDER BY a.days_running DESC NULLS LAST, a.library_id))[1]          AS hook,
       left((array_agg(a.primary_text ORDER BY a.days_running DESC NULLS LAST, a.library_id))[1], 280) AS primary_text_excerpt,
       c.mapped_product,
       c.line_code,
       c.cluster,
       c.icp,
       c.pain_point,
       coalesce(c.confidence, 'low')                  AS confidence,
       coalesce(c.is_new_angle, false)                AS is_new_angle,
       c.new_angle_label,
       max(a.seen_on)                                 AS as_of,
       (bool_or(a.carried_from IS NOT NULL) OR bool_or(b.last_error IS NOT NULL) OR max(a.seen_on) < current_date - 8) AS is_stale
FROM competitor_ads a
JOIN competitor_brands b ON b.brand = a.brand
LEFT JOIN competitor_concept_class c ON c.concept_key = a.concept_key
GROUP BY a.week_start, a.brand, a.concept_key, c.mapped_product, c.line_code, c.cluster, c.icp, c.pain_point, c.confidence, c.is_new_angle, c.new_angle_label;
