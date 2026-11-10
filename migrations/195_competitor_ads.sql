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

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_* lines only)
COMMENT ON TABLE public.competitor_brands IS 'The competitor brands whose Meta ads are watched weekly (Heart & Soil, Primal Viking, My Marrow ...): exact search phrases for the public Ad Library, the advertiser-name regex that keeps only the brand''s own page, and the freshness of the last scrape. Editable: insert / update rows to add or retire a brand (etl.competitor_ads only seeds missing brands). Grain: one row per brand. Source: seeded by etl.competitor_ads (BRANDS constant), edited by hand; last_* columns written by each run. Refresh: weekly (Sundays Pacific, etl.competitor_ads). Coverage: 18 brands seeded 2026-11-09. Caveats: last_error is set when the brand failed in the latest run (blocked, age-gated, layout change); its rows are then carried forward and flagged stale in competitor_concepts. | Brands whose ads are age-gated for logged-out visitors (Hims, Keeps, Men''s Edge, Reclaim Co) cannot be read and are not seeded.';
COMMENT ON COLUMN public.competitor_brands.brand IS 'Brand name (primary key), as shown in the views.';
COMMENT ON COLUMN public.competitor_brands.product_type IS 'Free text: what the brand sells (beef organ capsules, joint supplement ...).';
COMMENT ON COLUMN public.competitor_brands.search_phrases IS 'Exact phrases searched in the Ad Library (brand name, landing domain); results are filtered by name_match.';
COMMENT ON COLUMN public.competitor_brands.name_match IS 'Case-insensitive regex the advertiser (page) name must match; creator ads named ''X with Brand'' are dropped.';
COMMENT ON COLUMN public.competitor_brands.active IS 'False retires the brand without deleting its history.';
COMMENT ON COLUMN public.competitor_brands.last_attempt_at IS 'Last time a run tried this brand.';
COMMENT ON COLUMN public.competitor_brands.last_ok_at IS 'Last time the brand loaded without error.';
COMMENT ON COLUMN public.competitor_brands.last_error IS 'Reason the last attempt failed; NULL after a good run.';
COMMENT ON COLUMN public.competitor_brands.last_ad_count IS 'Active US ads read in the last good run (a top slice by impressions: capped at about 150 cards per phrase).';
COMMENT ON TABLE public.competitor_ads IS 'Weekly snapshot of the ACTIVE US Meta ads of each watched competitor, one row per Library ID, read from the public Meta Ad Library with headless Chromium. No spend, impressions or performance exist for these ads: days_running and variant_count are the only strength signals. Grain: one row per (week_start, library_id). Source: etl.competitor_ads: public Ad Library cards (exact-phrase search, impressions-sorted, advertiser filtered). Refresh: weekly, Sundays Pacific; a failed brand has its previous week copied forward (carried_from set). Coverage: from 2026-11-09; about 100-150 ads per large brand (a top slice, not a census). Caveats: The Library lazy-loads: each phrase returns at most about 150 cards, so large brands (Heart & Soil about 1,000 ads) are a top slice by impressions. | start_date resets when a brand re-uploads an ad, so days_running understates the age of re-uploaded creatives. | format is ''video'' when the card has a video, else ''image'' (carousels are not distinguishable from images). hook is the first sentence of the copy (video speech and on-image text are not read). | Cards with ''N ads use this creative and text'' count N in variant_count.';
COMMENT ON COLUMN public.competitor_ads.week_start IS 'Monday (Pacific) of the week of the run.';
COMMENT ON COLUMN public.competitor_ads.library_id IS 'Meta Ad Library ID.';
COMMENT ON COLUMN public.competitor_ads.brand IS 'competitor_brands.brand.';
COMMENT ON COLUMN public.competitor_ads.advertiser IS 'Page name shown on the card.';
COMMENT ON COLUMN public.competitor_ads.start_date IS '''Started running on'' date (resets on re-upload).';
COMMENT ON COLUMN public.competitor_ads.days_running IS 'seen_on minus start_date.';
COMMENT ON COLUMN public.competitor_ads.variant_count IS 'Ads sharing this creative and text (card says ''N ads use this creative and text''), else 1.';
COMMENT ON COLUMN public.competitor_ads.format IS '''video'' or ''image''.';
COMMENT ON COLUMN public.competitor_ads.primary_text IS 'Ad copy as shown (up to 2,500 characters).';
COMMENT ON COLUMN public.competitor_ads.headline IS 'Card headline (first line after the landing domain that is not a button label).';
COMMENT ON COLUMN public.competitor_ads.hook IS 'First sentence of the copy, else the headline.';
COMMENT ON COLUMN public.competitor_ads.landing_url IS 'Landing domain and path (tracking query removed).';
COMMENT ON COLUMN public.competitor_ads.concept_key IS 'Brand + hash of the first 160 characters of normalised copy: groups near-duplicate copy into one concept.';
COMMENT ON COLUMN public.competitor_ads.seen_on IS 'Date the Library was read (Pacific); for carried rows the original date.';
COMMENT ON COLUMN public.competitor_ads.carried_from IS 'NULL for fresh rows; for carried-forward rows the week_start they were copied from.';
COMMENT ON TABLE public.competitor_concept_class IS 'Lonvera taxonomy classification of each competitor concept (concept_key), made once by headless Claude from the copy text and cached, so only new concepts use plan usage: an existing line (product / cluster / ICP / pain point from the autotag catalogs), a NEW angle with suggested cluster / ICP / pain point, or out of category. Grain: one row per concept_key. Source: etl.competitor_ads classification step (claude -p, text only, batches of 8 concepts, catalogs etl/autotag/catalogs/*.json). Refresh: weekly; concepts already classified are never re-asked. Coverage: from 2026-11-09. Caveats: Text only: the model never sees the image or video, so low confidence is common for statics whose message is on the image. | line_code is NULL for NEW angles (cluster / icp / pain_point hold the suggestion) and for out-of-category ads (mapped_product ''none'').';
COMMENT ON COLUMN public.competitor_concept_class.concept_key IS 'competitor_ads.concept_key (primary key).';
COMMENT ON COLUMN public.competitor_concept_class.brand IS 'Brand.';
COMMENT ON COLUMN public.competitor_concept_class.mapped_product IS '''vita-bull'' | ''beef-liver'' | ''boneflex'' | ''none'': the Lonvera product the angle competes with.';
COMMENT ON COLUMN public.competitor_concept_class.line_code IS 'Catalog line code (L.. Vital Bull, B.. Beef Liver, BF.. Boneflex) or NULL.';
COMMENT ON COLUMN public.competitor_concept_class.cluster IS 'Market cluster (catalog value, or suggested for NEW).';
COMMENT ON COLUMN public.competitor_concept_class.icp IS 'ICP (catalog value, or suggested for NEW).';
COMMENT ON COLUMN public.competitor_concept_class.pain_point IS 'Pain point (catalog value, or suggested for NEW).';
COMMENT ON COLUMN public.competitor_concept_class.confidence IS 'high | medium | low.';
COMMENT ON COLUMN public.competitor_concept_class.is_new_angle IS 'True when no catalog line covers the angle.';
COMMENT ON COLUMN public.competitor_concept_class.new_angle_label IS '4-8 word label of a NEW angle.';
COMMENT ON COLUMN public.competitor_concept_class.why IS 'The model''s reason, 15 words or fewer.';
COMMENT ON COLUMN public.competitor_concept_class.model IS 'Model id used.';
COMMENT ON COLUMN public.competitor_concept_class.classified_at IS 'When it was classified.';
COMMENT ON VIEW public.competitor_concepts IS 'What competitors run on Meta right now, grouped by concept (near-duplicate copy), with the Lonvera line each maps to. The contract read by the weekly Creative priorities one-sheeter. Latest week: WHERE week_start = (SELECT max(week_start) FROM competitor_concepts). Grain: one row per (week_start, brand, concept_key). Source: public.competitor_ads x competitor_brands x competitor_concept_class. Refresh: weekly (Sundays Pacific). Coverage: from 2026-11-09. Caveats: No spend, impressions or ROAS: days_running (max across the concept''s ads) and variant_count (ads sharing the creative) are proxies, and start dates reset on re-upload. | is_stale = the brand failed to load this week and the rows are the last good snapshot carried forward, or the data is older than 8 days. | Unclassified concepts show confidence ''low'' and NULL mapped_product / line_code. For NEW angles cluster / icp / pain_point are the model''s suggestion.';
COMMENT ON COLUMN public.competitor_concepts.week_start IS 'Monday (Pacific) of the run week.';
COMMENT ON COLUMN public.competitor_concepts.brand IS 'Brand.';
COMMENT ON COLUMN public.competitor_concepts.concept_key IS 'Concept (near-duplicate copy of one brand).';
COMMENT ON COLUMN public.competitor_concepts.first_ad_start_date IS 'Earliest start date among the concept''s ads.';
COMMENT ON COLUMN public.competitor_concepts.days_running IS 'Max days running among the concept''s ads (as of as_of).';
COMMENT ON COLUMN public.competitor_concepts.variant_count IS 'Ads sharing the creative / copy, summed over the concept''s cards.';
COMMENT ON COLUMN public.competitor_concepts.format IS '''video'' or ''image'' of the longest-running ad.';
COMMENT ON COLUMN public.competitor_concepts.hook IS 'First sentence of the longest-running ad''s copy.';
COMMENT ON COLUMN public.competitor_concepts.primary_text_excerpt IS 'First 280 characters of that ad''s copy.';
COMMENT ON COLUMN public.competitor_concepts.mapped_product IS '''vita-bull'' | ''beef-liver'' | ''boneflex'' | ''none'' | NULL.';
COMMENT ON COLUMN public.competitor_concepts.line_code IS 'Lonvera catalog line, NULL for NEW / out-of-category / unclassified.';
COMMENT ON COLUMN public.competitor_concepts.cluster IS 'Market cluster.';
COMMENT ON COLUMN public.competitor_concepts.icp IS 'ICP.';
COMMENT ON COLUMN public.competitor_concepts.pain_point IS 'Pain point.';
COMMENT ON COLUMN public.competitor_concepts.confidence IS 'high | medium | low (unclassified = low).';
COMMENT ON COLUMN public.competitor_concepts.is_new_angle IS 'No catalog line covers it.';
COMMENT ON COLUMN public.competitor_concepts.new_angle_label IS 'Label of the NEW angle.';
COMMENT ON COLUMN public.competitor_concepts.as_of IS 'Date the Library was read.';
COMMENT ON COLUMN public.competitor_concepts.is_stale IS 'Carried forward or older than 8 days.';
