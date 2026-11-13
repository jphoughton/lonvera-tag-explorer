-- 252: Amazon Ads keyword layer, tables (Sponsored Products only). Filled by hand with `python -m etl.amazon_dash_load --dir <folder>` from ExpandFi Data Dash pulls of Amazon Ads
-- reports and entity lists; NOT by the feeds service (it cannot reach Dash) and not by any cron. docs/AMAZON_DATA.md. The analytics views and the data dictionary comments are migration 253.
-- Additive and idempotent: nothing existing is altered. Every id is text (Amazon sends ints in reports and strings in entity lists; text matches amazon_ads_campaign_daily.campaign_id).
-- The loader upserts on the natural keys, so loading a file twice leaves the same rows.

CREATE TABLE IF NOT EXISTS amazon_ads_search_term_daily (
  day             date NOT NULL,
  campaign_id     text NOT NULL,
  ad_group_id     text NOT NULL,
  keyword_id      text NOT NULL,
  match_type      text NOT NULL,
  search_term     text NOT NULL,
  campaign_name   text,
  ad_group_name   text,
  keyword         text,
  search_term_key text NOT NULL,
  impressions     bigint,
  clicks          bigint,
  cost            numeric,
  purchases_7d    bigint,
  sales_7d        numeric,
  loaded_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, campaign_id, ad_group_id, keyword_id, match_type, search_term)
);
CREATE INDEX IF NOT EXISTS amazon_ads_search_term_daily_key ON amazon_ads_search_term_daily (search_term_key, day);
CREATE INDEX IF NOT EXISTS amazon_ads_search_term_daily_kw ON amazon_ads_search_term_daily (keyword_id, day);

CREATE TABLE IF NOT EXISTS amazon_ads_targeting_daily (
  day             date NOT NULL,
  campaign_id     text NOT NULL,
  ad_group_id     text NOT NULL,
  keyword_id      text NOT NULL,
  match_type      text NOT NULL,
  targeting       text NOT NULL,
  campaign_name   text,
  ad_group_name   text,
  keyword         text,
  impressions     bigint,
  clicks          bigint,
  cost            numeric,
  purchases_7d    bigint,
  sales_7d        numeric,
  loaded_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, campaign_id, ad_group_id, keyword_id, match_type, targeting)
);
CREATE INDEX IF NOT EXISTS amazon_ads_targeting_daily_kw ON amazon_ads_targeting_daily (keyword_id, day);

CREATE TABLE IF NOT EXISTS amazon_ads_keywords_snapshot (
  snapshot_date date NOT NULL,
  keyword_id    text NOT NULL,
  ad_group_id   text NOT NULL,
  campaign_id   text NOT NULL,
  keyword_text  text,
  match_type    text,
  bid           numeric,
  state         text,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, keyword_id)
);

CREATE TABLE IF NOT EXISTS amazon_ads_targets_snapshot (
  snapshot_date       date NOT NULL,
  target_id           text NOT NULL,
  ad_group_id         text NOT NULL,
  campaign_id         text NOT NULL,
  bid                 numeric,
  state               text,
  expression_type     text,
  expression_text     text,
  expression          jsonb,
  resolved_expression jsonb,
  loaded_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, target_id)
);

CREATE TABLE IF NOT EXISTS amazon_ads_negative_keywords_snapshot (
  snapshot_date date NOT NULL,
  keyword_id    text NOT NULL,
  ad_group_id   text NOT NULL,
  campaign_id   text NOT NULL,
  keyword_text  text,
  match_type    text,
  state         text,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, keyword_id)
);

CREATE TABLE IF NOT EXISTS amazon_ads_campaigns_snapshot (
  snapshot_date     date NOT NULL,
  campaign_id       text NOT NULL,
  name              text,
  state             text,
  targeting_type    text,
  start_date        date,
  budget            numeric,
  budget_type       text,
  portfolio_id      text,
  bidding_strategy  text,
  placement_top_pct            numeric,
  placement_product_page_pct   numeric,
  placement_rest_of_search_pct numeric,
  dynamic_bidding   jsonb,
  loaded_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, campaign_id)
);

CREATE TABLE IF NOT EXISTS amazon_ads_product_ads (
  ad_id         text PRIMARY KEY,
  ad_group_id   text NOT NULL,
  campaign_id   text NOT NULL,
  asin          text,
  sku           text,
  state         text,
  snapshot_date date NOT NULL,
  loaded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS amazon_ads_product_ads_group ON amazon_ads_product_ads (campaign_id, ad_group_id);
