-- 278: which Facebook Page (handle) each Meta ad runs from. Filled by `python -m etl.meta_pages` (nightly, non-fatal; bulk-lists ads per ad account with the creative's
-- page fields). Additive and idempotent. The analytics views, the data_coverage row and the dictionary comments are in migration 279.
CREATE TABLE IF NOT EXISTS meta_pages (
  page_id     text PRIMARY KEY,
  name        text,                                   -- NULL until resolved (the Graph needs a page permission for it; the job tries once per unnamed page)
  is_lonvera  boolean     NOT NULL DEFAULT false,     -- true only for the Lonvera handle itself
  active      boolean     NOT NULL DEFAULT true,
  loaded_at   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO meta_pages (page_id, name, is_lonvera) VALUES ('161085663758792', 'Lonvera', true) ON CONFLICT (page_id) DO NOTHING;
INSERT INTO meta_pages (page_id, name, is_lonvera) VALUES ('830267930177476', NULL, false) ON CONFLICT (page_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS meta_ad_pages (
  ad_id                     text PRIMARY KEY,
  ad_account                text,                     -- 'Lonvera Main' | 'Lonvera BackUp' (same labels as meta_ads.account)
  page_id                   text,                     -- NULL = the ad's creative names no page (deleted creative, catalog / dynamic ad)
  instagram_user_id         text,
  effective_object_story_id text,                     -- '<page_id>_<post_id>' of the post the ad serves
  creative_id               text,
  first_seen                date        NOT NULL,
  last_seen                 date        NOT NULL,     -- last day a listing returned this ad
  source                    text        NOT NULL,     -- 'effective_object_story_id' | 'object_story_id' | 'object_story_spec' | 'unresolved'
  loaded_at                 timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meta_ad_pages_page ON meta_ad_pages (page_id);
