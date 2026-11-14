-- 267: our OWN landing pages next to the competitors' (python -m etl.competitor_landing_pages --own) and the link from each Lonvera ad family to its page.
--   competitor_landing_pages.is_own  true = a Lonvera page (brand 'Lonvera'); competitor views and charts must filter is_own = false.
--   ad_family_lp                     one row per (ad family, week): the own landing page that family sends most of its spend to.
ALTER TABLE competitor_landing_pages ADD COLUMN IF NOT EXISTS is_own boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS ad_family_lp (
  family_key  text NOT NULL,
  lp_id       text NOT NULL,
  week_start  date NOT NULL,
  PRIMARY KEY (family_key, week_start)
);
CREATE INDEX IF NOT EXISTS ad_family_lp_lp ON ad_family_lp (week_start, lp_id);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (ad_family_lp and competitor_landing_pages.is_own lines only)
COMMENT ON COLUMN public.competitor_landing_pages.is_own IS 'True for one of Lonvera''s own landing pages (brand ''Lonvera'', added by etl.competitor_landing_pages --own); false for a competitor. Competitor views and charts filter is_own = false.';
COMMENT ON TABLE public.ad_family_lp IS 'Which of our own landing pages each Lonvera ad family sends most of its spend to, per week, so ad tags and landing page tags can be put side by side. Grain: one row per (family_key, week_start) (joins to competitor_landing_pages on lp_id with is_own = true). Source: etl.competitor_landing_pages --own: landing_page_ads (ad to page map, migration 207) x ad_daily spend over the last 120 days, ad to family through meta_family_map. Refresh: by hand: python -m etl.competitor_landing_pages --own (rewritten for the week on every run). Coverage: from the first --own run after migration 267; only the top 25 own pages by spend. Caveats: An ad that lists several links appears under each page with its full spend; the family row keeps the page with the largest spend share (ties: alphabetical url_key). | Only families whose ads have a row in landing_page_ads and whose page is in the top 25 are linked; a missing row means unlinked, not ''no landing page''.';
COMMENT ON COLUMN public.ad_family_lp.family_key IS 'Ad family (primary key with week_start).';
COMMENT ON COLUMN public.ad_family_lp.lp_id IS 'competitor_landing_pages.lp_id of the family''s page.';
COMMENT ON COLUMN public.ad_family_lp.week_start IS 'Monday (Pacific) of the run week (primary key with family_key).';
