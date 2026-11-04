-- Lonvera Tag Explorer: initial schema. Product-agnostic. Applied by `python -m etl.migrate` (idempotent runner, tracks schema_migrations).

CREATE TABLE products (
  id            serial PRIMARY KEY,
  slug          text UNIQUE NOT NULL,                 -- 'vita-bull'
  name          text NOT NULL,                        -- 'Vita Bull'
  tracker_names text[] NOT NULL DEFAULT '{}',         -- values of the Creative Tracker 'Product' column ({'Boneflex','BoneFlex'})
  meta_regex    text,                                 -- fallback product match on a Meta family name (case-insensitive), e.g. 'bull'
  meta_priority int NOT NULL DEFAULT 100,             -- lower = tested first (sheet order: bone, liver, bull, belle)
  tag_set       text NOT NULL DEFAULT 'tracker',      -- which ad_tags.tag_set drives the hierarchy: 'linemap' | 'tracker'
  sort_order    int NOT NULL DEFAULT 0,
  active        boolean NOT NULL DEFAULT true
);

-- Communication Overview: one line per cluster x ICP x pain point (L01..). Ingredient rows hang off a line.
CREATE TABLE taxonomy_lines (
  id                serial PRIMARY KEY,
  product_id        int NOT NULL REFERENCES products(id),
  line_code         text NOT NULL,                    -- 'L01'
  cluster           text NOT NULL,
  icp               text NOT NULL,
  pain_point        text NOT NULL,
  emotional_trigger text,
  story             text,
  cluster_color     text,                             -- hex, from the sheet
  sort_order        int NOT NULL DEFAULT 0,           -- Communication Overview order
  UNIQUE (product_id, line_code),
  UNIQUE (product_id, cluster, icp, pain_point)
);
CREATE TABLE line_ingredients (
  id          serial PRIMARY KEY,
  line_id     int NOT NULL REFERENCES taxonomy_lines(id) ON DELETE CASCADE,
  ingredient  text,
  attacks     text,                                   -- 'What It Attacks'
  supports    text,                                   -- 'What It Supports'
  sort_order  int NOT NULL DEFAULT 0
);

-- Creative Tracker rows (all products). Key = concept_id + ordinal among rows sharing (concept_id, ad_family). Never key on sheet_row.
CREATE TABLE tracker_rows (
  id            bigserial PRIMARY KEY,
  product_id    int REFERENCES products(id),          -- NULL never: unassigned rows use the 'unassigned' product
  row_key       text UNIQUE NOT NULL,                 -- 'Bull-526-Hook2 - Copy#1'
  concept_id    text NOT NULL,                        -- TEXT always ('0510' <> '510')
  ad_family     text NOT NULL DEFAULT '',             -- as typed in the tracker; '' when blank
  family_key    text NOT NULL,                        -- lower(trim(ad_family)); 'id:'||lower(trim(concept_id)) when blank. The "unique ad" key.
  sheet_row     int,                                  -- informational; ONLY used to order rows (first row of a family = min sheet_row)
  status        text,
  ad_type       text,                                 -- VID / IMG
  style         text,
  vendor        text,
  fidelity      text,
  proof         text,
  launch_date   date,
  concept_name  text,
  description   text,
  hypothesis    text,
  meta_ad_name  text,                                 -- tracker 'Name'
  primary_text  text[] ,                              -- Primary Text, 2, 3
  headlines     text[] ,
  ad_description text,
  transcript    text,
  asset_url     text,
  tracker_cluster text,                               -- tracker's own tags (fallback hierarchy)
  tracker_icp   text,
  tracker_icp_legacy text,
  tracker_pain  text,
  tracker_emotion text,
  tracker_ingredient text,
  raw           jsonb,                                -- every tracker column by header
  synced_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tracker_rows_product_family ON tracker_rows (product_id, family_key, sheet_row);
CREATE INDEX tracker_rows_family ON tracker_rows (family_key);

-- Tags. A row can have one current tag per tag_set. 'linemap' = Communication Overview line (image/video-based);
-- 'tracker' = the tracker's own Market Cluster / ICP / Pain Point columns (refreshed on every tracker sync).
CREATE TABLE ad_tags (
  id               bigserial PRIMARY KEY,
  tracker_row_id   bigint NOT NULL REFERENCES tracker_rows(id) ON DELETE CASCADE,
  tag_set          text NOT NULL,                     -- 'linemap' | 'tracker'
  version          text NOT NULL,                     -- 'linemap-v2-2026-11-03'
  is_current       boolean NOT NULL DEFAULT true,
  line_id          int REFERENCES taxonomy_lines(id),
  cluster          text,
  icp              text,
  pain_point       text,
  emotional_trigger text,
  confidence       text,                              -- high | medium | low | NULL
  method           text,                              -- 'image', 'image + audio transcript', 'text only', ...
  seen             text,                              -- 'What Was Seen'
  why              text,
  runner_up        text,
  copy_only_line   text,                              -- line the copy-only first pass picked (when different)
  tagger           text,
  tagged_at        timestamptz NOT NULL DEFAULT now(),
  extra            jsonb
);
CREATE UNIQUE INDEX ad_tags_current ON ad_tags (tracker_row_id, tag_set) WHERE is_current;
CREATE INDEX ad_tags_group ON ad_tags (tag_set, cluster, icp, pain_point) WHERE is_current;

-- Meta daily results, one row per (Meta ad family, day), both ad accounts summed. Kept forever (the sheet only keeps 180d).
CREATE TABLE ad_daily (
  meta_family_key text NOT NULL,                      -- lower(family(ad_name)); family() strips ' - Copy N' suffixes
  day             date NOT NULL,
  meta_family     text NOT NULL,                      -- display name (original case)
  spend           numeric(14,2) NOT NULL DEFAULT 0,
  purchases       numeric(12,2) NOT NULL DEFAULT 0,
  conv_value      numeric(14,2) NOT NULL DEFAULT 0,
  link_clicks     numeric(12,0) NOT NULL DEFAULT 0,
  ads             int NOT NULL DEFAULT 0,
  source          text NOT NULL,                      -- 'sheet' (backfill) | 'meta_api'
  loaded_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (meta_family_key, day)
);
CREATE INDEX ad_daily_day ON ad_daily (day);

-- Lifetime snapshot (Meta Ads - Lifetime, through lifetime_cutoff), keyed by TRACKER family key.
CREATE TABLE ad_lifetime (
  family_key  text PRIMARY KEY,
  spend       numeric(14,2) NOT NULL DEFAULT 0,
  purchases   numeric(12,2) NOT NULL DEFAULT 0,
  conv_value  numeric(14,2) NOT NULL DEFAULT 0
);

-- Meta family -> tracker family (resolver output). tracker_family_key NULL = no tracker match.
CREATE TABLE meta_family_map (
  meta_family_key    text PRIMARY KEY,
  meta_family        text NOT NULL,
  tracker_family_key text,
  rule               text,                            -- 'sheet-windows' | 'exact' | 'relaunch' | 'adname' | 'prefix' | 'triple' | NULL
  resolved_at        timestamptz NOT NULL DEFAULT now()
);

-- Individual Meta ads (ids/names/status), used for assets and ad-count context.
CREATE TABLE meta_ads (
  ad_id           text PRIMARY KEY,
  account         text,
  ad_name         text,
  meta_family_key text,
  created_date    date,
  status          text
);
CREATE INDEX meta_ads_family ON meta_ads (meta_family_key);

-- Per tracker-family content: Meta copy, transcript, post link. Keyed by tracker family_key.
CREATE TABLE family_content (
  family_key    text PRIMARY KEY,
  meta_body     text,
  meta_title    text,
  meta_desc     text,
  transcript    text,
  transcript_seconds numeric,
  story_url     text,                                 -- facebook post link
  is_video      boolean,
  ad_id         text,
  video_id      text,
  source        text
);

CREATE TABLE creative_assets (
  id          bigserial PRIMARY KEY,
  family_key  text NOT NULL,
  kind        text NOT NULL,                          -- 'thumb' | 'frame'
  idx         int NOT NULL DEFAULT 0,
  mime        text NOT NULL DEFAULT 'image/jpeg',
  width       int,
  height      int,
  bytes       bytea NOT NULL,
  source      text,                                   -- 'cycle-2026-11-03' | 'meta-api'
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (family_key, kind, idx)
);

-- Derived: one row per tracker row, metrics attributed to the FIRST row of each (product, family_key); others are zero.
-- Rebuilt in full by etl.build_metrics after any load. See docs/CONTRACT.md "Metric rules".
CREATE TABLE ad_metrics (
  tracker_row_id    bigint PRIMARY KEY REFERENCES tracker_rows(id) ON DELETE CASCADE,
  product_id        int NOT NULL,
  family_key        text NOT NULL,
  is_first_row      boolean NOT NULL,
  launch_date       date,
  spend_launch      numeric(14,2) NOT NULL DEFAULT 0,
  purchases_launch  numeric(12,2) NOT NULL DEFAULT 0,
  revenue_launch    numeric(14,2) NOT NULL DEFAULT 0,
  spend_7d          numeric(14,2) NOT NULL DEFAULT 0,
  purchases_7d      numeric(12,2) NOT NULL DEFAULT 0,
  revenue_7d        numeric(14,2) NOT NULL DEFAULT 0,
  spend_30d         numeric(14,2) NOT NULL DEFAULT 0,
  purchases_30d     numeric(12,2) NOT NULL DEFAULT 0,
  revenue_30d       numeric(14,2) NOT NULL DEFAULT 0,
  live              boolean NOT NULL DEFAULT false,
  built_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ad_metrics_product ON ad_metrics (product_id);

-- Derived: Meta families with spend in the data window that map to NO tracker family, assigned to a product by name regex.
CREATE TABLE unmatched_meta (
  meta_family_key text PRIMARY KEY,
  meta_family     text NOT NULL,
  product_id      int NOT NULL,
  spend_7d        numeric(14,2) NOT NULL DEFAULT 0,
  purchases_7d    numeric(12,2) NOT NULL DEFAULT 0,
  revenue_7d      numeric(14,2) NOT NULL DEFAULT 0,
  spend_30d       numeric(14,2) NOT NULL DEFAULT 0,
  purchases_30d   numeric(12,2) NOT NULL DEFAULT 0,
  revenue_30d     numeric(14,2) NOT NULL DEFAULT 0,
  spend_all       numeric(14,2) NOT NULL DEFAULT 0,   -- all days held in ad_daily
  purchases_all   numeric(12,2) NOT NULL DEFAULT 0,
  revenue_all     numeric(14,2) NOT NULL DEFAULT 0,
  live            boolean NOT NULL DEFAULT false
);

CREATE TABLE app_meta (
  key   text PRIMARY KEY,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
-- keys: daily_start (D0, first day of ad_daily at backfill), lifetime_cutoff ('2026-09-17'), data_through (max ad_daily.day), metrics_built_at

CREATE TABLE etl_runs (
  id            bigserial PRIMARY KEY,
  job           text NOT NULL,                        -- 'backfill', 'meta_pull', 'tracker_sync', 'build_metrics', 'load_tags', 'assets_pull'
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running',      -- running | ok | failed
  data_through  date,
  rows_written  int,
  stats         jsonb,
  error         text
);

INSERT INTO products (slug, name, tracker_names, meta_regex, meta_priority, tag_set, sort_order) VALUES
  ('vita-bull',  'Vita Bull',  '{VitaBull}',          'bull',  30, 'linemap', 1),
  ('beef-liver', 'Beef Liver', '{BeefLiver}',         'liver', 20, 'tracker', 2),
  ('boneflex',   'Boneflex',   '{Boneflex,BoneFlex}', 'bone',  10, 'tracker', 3),
  ('vita-belle', 'Vita Belle', '{VitaBelle}',         'belle', 40, 'tracker', 4),
  ('vital-flow', 'Vital Flow', '{VitalFlow}',         'vital[-_ ]?flow', 50, 'tracker', 5),
  ('unassigned', 'No product', '{}',                  NULL,    999, 'tracker', 9);
