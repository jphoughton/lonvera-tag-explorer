-- Pacing module (projection): daily actual feeds read from the sheet's raw tabs plus Meta aggregates derived from ad_daily. Additive and idempotent.
-- Scenario-independent facts only (no scenario_id). Aggregates only: no customer-level data. Money numeric(18,2), units numeric(18,2).

-- One row per feed: what was last loaded and through which day (feeds the freshness chips and /api/projection/freshness).
CREATE TABLE IF NOT EXISTS proj_feed_runs (
  feed        text PRIMARY KEY,
  source      text NOT NULL,                        -- 'sheet:<tab>' | 'ad_daily' | 'snapshot'
  loaded_at   timestamptz NOT NULL DEFAULT now(),
  rows_loaded int NOT NULL DEFAULT 0,
  first_day   date,
  last_day    date,
  note        text
);

-- Orders - History: Shopify order totals by Pacific day (not ad-attributed).
CREATE TABLE IF NOT EXISTS proj_feed_orders (
  day          date PRIMARY KEY,
  nc_rev       numeric(18,2) NOT NULL DEFAULT 0,    -- new-customer revenue
  rc_rev       numeric(18,2) NOT NULL DEFAULT 0,    -- returning-customer revenue
  nc_orders    numeric(18,2) NOT NULL DEFAULT 0,    -- new customers
  total_rev    numeric(18,2) NOT NULL DEFAULT 0,
  total_orders numeric(18,2) NOT NULL DEFAULT 0,
  tax          numeric(18,2) NOT NULL DEFAULT 0,
  refunds      numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

-- Amazon - Daily: Amazon.com + B2B sales and Amazon Ads (deduped v3 > v2 > v1), by purchase day.
CREATE TABLE IF NOT EXISTS proj_feed_amazon (
  day              date PRIMARY KEY,
  orders           numeric(18,2) NOT NULL DEFAULT 0,
  units            numeric(18,2) NOT NULL DEFAULT 0,
  revenue          numeric(18,2) NOT NULL DEFAULT 0,
  nc_orders        numeric(18,2) NOT NULL DEFAULT 0,
  nc_rev           numeric(18,2) NOT NULL DEFAULT 0,
  ad_spend         numeric(18,2) NOT NULL DEFAULT 0,
  attributed_sales numeric(18,2) NOT NULL DEFAULT 0,
  clicks           numeric(18,2) NOT NULL DEFAULT 0,
  impressions      numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at        timestamptz NOT NULL DEFAULT now()
);

-- Platform - History: ad-platform spend and platform-reported conversions (Google Ads, TikTok Ads, Meta) by day.
CREATE TABLE IF NOT EXISTS proj_feed_platform (
  day              date NOT NULL,
  platform         text NOT NULL,
  spend            numeric(18,2) NOT NULL DEFAULT 0,
  purchases        numeric(18,2) NOT NULL DEFAULT 0,
  purchase_value   numeric(18,2) NOT NULL DEFAULT 0,
  nc_purchases     numeric(18,2) NOT NULL DEFAULT 0,
  nc_purchase_value numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks      numeric(18,2) NOT NULL DEFAULT 0,
  clicks           numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, platform)
);

-- Attribution - History: last-click new customers by ad source (Meta Ads, Google Ads, ...).
CREATE TABLE IF NOT EXISTS proj_feed_attribution (
  day       date NOT NULL,
  ad_source text NOT NULL,
  nc_orders numeric(18,2) NOT NULL DEFAULT 0,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  orders    numeric(18,2) NOT NULL DEFAULT 0,
  rev       numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, ad_source)
);

-- Traffic - Daily: pixel-tracked visitors (unique per day, not additive across periods).
CREATE TABLE IF NOT EXISTS proj_feed_traffic (
  day          date PRIMARY KEY,
  new_visitors numeric(18,2) NOT NULL DEFAULT 0,
  visitors     numeric(18,2) NOT NULL DEFAULT 0,
  sessions     numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

-- Meta Account - Reach: account-level impressions, reach and unique outbound clicks pulled AT EACH GRAIN (reach is not additive), both ad accounts summed.
CREATE TABLE IF NOT EXISTS proj_feed_meta_reach (
  grain                 text NOT NULL CHECK (grain IN ('day','week','month')),
  period_key            text NOT NULL,              -- '2026-11-02' | '2026-W45' | '2026-11'
  period_start          date NOT NULL,
  period_end            date NOT NULL,
  impressions           numeric(18,2) NOT NULL DEFAULT 0,
  reach                 numeric(18,2) NOT NULL DEFAULT 0,
  unique_link_clicks    numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks           numeric(18,2) NOT NULL DEFAULT 0,
  unique_outbound_clicks numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grain, period_key)
);

-- Meta Product - Reach: unique outbound clicks de-duplicated across a product's ad sets, per grain (available from 2026-07-05).
CREATE TABLE IF NOT EXISTS proj_feed_product_reach (
  grain                  text NOT NULL CHECK (grain IN ('day','week','month')),
  period_key             text NOT NULL,
  period_start           date NOT NULL,
  period_end             date NOT NULL,
  product                text NOT NULL,
  unique_outbound_clicks numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grain, period_key, product)
);

-- NC Rev - By Product: actual Shopify first-order revenue and new customers by largest product line.
CREATE TABLE IF NOT EXISTS proj_feed_nc_product (
  day       date NOT NULL,
  product   text NOT NULL,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  nc_orders numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, product)
);

-- Upstack Any-Click - By Campaign, summed by stage: new customers (30-day any-click) on Scaling and Testing campaigns.
CREATE TABLE IF NOT EXISTS proj_feed_anyclick (
  day       date NOT NULL,
  stage     text NOT NULL CHECK (stage IN ('Scaling','Testing')),
  nc        numeric(18,2) NOT NULL DEFAULT 0,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, stage)
);

-- Meta Bids - Daily, summed by day: spend on capped / floor ad sets and spend-weighted bid sums.
CREATE TABLE IF NOT EXISTS proj_feed_bids (
  day            date PRIMARY KEY,
  spend          numeric(18,2) NOT NULL DEFAULT 0,
  capped_spend   numeric(18,2) NOT NULL DEFAULT 0,
  spend_x_cap    numeric(18,2) NOT NULL DEFAULT 0,
  floor_spend    numeric(18,2) NOT NULL DEFAULT 0,
  spend_x_floor  numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at      timestamptz NOT NULL DEFAULT now()
);

-- Meta by campaign and day. source 'app' = aggregated from ad_daily (Graph API ad grain, refreshed by etl.run_daily); 'sheet' = the sheet's
-- Meta Campaign - History, used only for days before ad_daily begins. A day is read from 'app' when it has app rows, else from 'sheet'.
CREATE TABLE IF NOT EXISTS proj_feed_meta_day (
  day            date NOT NULL,
  campaign_id    text NOT NULL,
  source         text NOT NULL CHECK (source IN ('app','sheet')),
  campaign_name  text,
  product        text NOT NULL DEFAULT 'Unclassified',   -- from the campaign name: VitaBull | Beef Liver | Boneflex | Unclassified
  stage          text NOT NULL DEFAULT 'Scaling',        -- Testing when the campaign name contains 'test'
  spend          numeric(18,2) NOT NULL DEFAULT 0,
  impressions    numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks    numeric(18,2) NOT NULL DEFAULT 0,
  purchases      numeric(18,2) NOT NULL DEFAULT 0,
  purchase_value numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, campaign_id, source)
);
CREATE INDEX IF NOT EXISTS proj_feed_meta_day_day ON proj_feed_meta_day (day);

-- Meta spend by product, classified campaign name -> ad set name -> ad name -> Other (the sheet's Meta Spend - By Product rule).
CREATE TABLE IF NOT EXISTS proj_feed_meta_product (
  day         date NOT NULL,
  product     text NOT NULL,                              -- VitaBull | Beef Liver | Boneflex | Other
  source      text NOT NULL CHECK (source IN ('app','sheet')),
  spend       numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, product, source)
);

-- Meta time-of-day spend curve (hourly spend of the last 30 days, Meta Ads Manager time-of-day report): typed input for the intraday projection.
CREATE TABLE IF NOT EXISTS proj_pacing_hourly (
  hour        int PRIMARY KEY CHECK (hour BETWEEN 0 AND 23),   -- hour of day, Pacific
  spend_30d   numeric(18,2) NOT NULL,
  days_in_curve int NOT NULL DEFAULT 30,
  note        text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Feed classification rules (global, applied when the ETL loads a day): product patterns and the testing keyword. Defaults are the sheet's rules
-- (meta_campaign_history.classify_product / classify_stage and the by-product sync's classify). Editable through /api/projection/pacing/rules.
CREATE TABLE IF NOT EXISTS proj_pacing_setting (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  note       text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
