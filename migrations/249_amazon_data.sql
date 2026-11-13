-- 249: the Amazon data layer (ExpandFi merchant 1452, filled by the `feeds` service: etl/feeds/amazon_feeds.py; docs/AMAZON_DATA.md). Tables only; the analytics views,
-- the data_coverage rows and the data dictionary comments are in migration 250. Additive and idempotent. Every table is replaced by trailing window (delete the window, insert
-- the pull), so a rerun leaves the same rows; an empty pull writes nothing.

CREATE TABLE IF NOT EXISTS amazon_asin_products (
  asin          text NOT NULL,
  sku           text NOT NULL,
  product_slug  text,
  product_name  text,
  friendly_name text,
  refreshed_on  date NOT NULL,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (asin, sku)
);

CREATE TABLE IF NOT EXISTS amazon_ads_campaign_daily (
  day            date NOT NULL,
  marketplace    text NOT NULL,
  campaign_id    text NOT NULL,
  ad_group_id    text NOT NULL DEFAULT '',
  ad_type        text NOT NULL,
  api_version    text,
  campaign_name  text,
  ad_group_name  text,
  campaign_status text,
  budget_amount  numeric,
  budget_type    text,
  bidding_strategy text,
  product_slug   text,
  product_name   text,
  spend          numeric,
  impressions    bigint,
  clicks         bigint,
  sales_1d       numeric, sales_7d numeric, sales_14d numeric, sales_30d numeric,
  purchases_1d   bigint, purchases_7d bigint, purchases_14d bigint, purchases_30d bigint,
  units_1d       bigint, units_7d bigint, units_14d bigint, units_30d bigint,
  sales          numeric,
  purchases      bigint,
  units_sold     bigint,
  ntb_purchases  bigint,
  ntb_sales      numeric,
  ntb_units      bigint,
  video_complete_views bigint,
  video_5s_views bigint,
  video_q1_views bigint,
  video_midpoint_views bigint,
  video_q3_views bigint,
  brand_searches bigint,
  sales_reported numeric,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, marketplace, campaign_id, ad_group_id, ad_type)
);
CREATE INDEX IF NOT EXISTS amazon_ads_campaign_daily_product ON amazon_ads_campaign_daily (product_slug, day);

CREATE TABLE IF NOT EXISTS amazon_ads_asin_daily (
  day            date NOT NULL,
  marketplace    text NOT NULL,
  campaign_id    text NOT NULL,
  ad_group_id    text NOT NULL DEFAULT '',
  asin           text NOT NULL,
  ad_type        text NOT NULL,
  sku            text,
  campaign_name  text,
  ad_group_name  text,
  product_slug   text,
  product_name   text,
  spend          numeric,
  impressions    bigint,
  clicks         bigint,
  sales_1d       numeric, sales_7d numeric, sales_14d numeric, sales_30d numeric,
  purchases_1d   bigint, purchases_7d bigint, purchases_14d bigint, purchases_30d bigint,
  units_1d       bigint, units_7d bigint, units_14d bigint, units_30d bigint,
  sales          numeric,
  purchases      bigint,
  sales_reported numeric,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, marketplace, campaign_id, ad_group_id, asin, ad_type)
);
CREATE INDEX IF NOT EXISTS amazon_ads_asin_daily_asin ON amazon_ads_asin_daily (asin, day);

CREATE TABLE IF NOT EXISTS amazon_sqp_weekly (
  period_start   date NOT NULL,
  period_end     date NOT NULL,
  marketplace_id text NOT NULL,
  marketplace    text,
  asin           text NOT NULL,
  query_key      text NOT NULL,
  search_query   text,
  product_slug   text,
  product_name   text,
  is_branded     boolean NOT NULL,
  search_query_score bigint,
  search_query_volume bigint,
  total_impressions bigint, asin_impressions bigint, asin_impression_share numeric,
  total_clicks bigint, total_click_rate numeric, asin_clicks bigint, asin_click_share numeric, total_median_click_price numeric, asin_median_click_price numeric,
  total_cart_adds bigint, total_cart_add_rate numeric, asin_cart_adds bigint, asin_cart_add_share numeric, total_median_cart_add_price numeric, asin_median_cart_add_price numeric,
  total_purchases bigint, total_purchase_rate numeric, asin_purchases bigint, asin_purchase_share numeric, total_median_purchase_price numeric, asin_median_purchase_price numeric,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (period_start, marketplace_id, asin, query_key)
);
CREATE INDEX IF NOT EXISTS amazon_sqp_weekly_query ON amazon_sqp_weekly (query_key, period_start);

CREATE TABLE IF NOT EXISTS amazon_sqp_monthly (LIKE amazon_sqp_weekly INCLUDING ALL);

CREATE TABLE IF NOT EXISTS amazon_orders_asin_daily (
  day           date NOT NULL,
  asin          text NOT NULL,
  sales_channel text NOT NULL,
  product_slug  text,
  product_name  text,
  orders        bigint NOT NULL,
  units         bigint NOT NULL,
  revenue       numeric NOT NULL,
  nc_orders     bigint NOT NULL,
  nc_revenue    numeric NOT NULL,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, asin, sales_channel)
);

CREATE TABLE IF NOT EXISTS amazon_traffic_daily (
  day            date NOT NULL,
  marketplace_id text NOT NULL,
  sessions       bigint, browser_sessions bigint, mobile_app_sessions bigint, page_views bigint,
  buy_box_pct    numeric,
  unit_conversion_pct numeric,
  units_ordered  bigint,
  ordered_product_sales numeric,
  total_order_items bigint,
  sessions_b2b   bigint, page_views_b2b bigint, buy_box_pct_b2b numeric,
  source_updated_at timestamp,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, marketplace_id)
);

CREATE TABLE IF NOT EXISTS amazon_market_baskets (
  week_start     date NOT NULL,
  week_end       date NOT NULL,
  marketplace_id text NOT NULL,
  asin           text NOT NULL,
  product_slug   text,
  product_name   text,
  purchased_with_asin text NOT NULL,
  purchased_with_slug text,
  purchased_with_rank integer,
  combination_pct numeric,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (week_start, marketplace_id, asin, purchased_with_asin)
);

CREATE TABLE IF NOT EXISTS amazon_repeat_purchases (
  week_start     date NOT NULL,
  week_end       date NOT NULL,
  marketplace_id text NOT NULL,
  asin           text NOT NULL,
  product_slug   text,
  product_name   text,
  orders         bigint,
  unique_customers bigint,
  repeat_customers_pct_total numeric,
  repeat_purchase_revenue numeric,
  repeat_purchase_revenue_pct_total numeric,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (week_start, marketplace_id, asin)
);

CREATE TABLE IF NOT EXISTS amazon_settlement_fees_daily (
  day               date NOT NULL,
  marketplace_name  text NOT NULL DEFAULT '',
  transaction_type  text NOT NULL DEFAULT '',
  amount_type       text NOT NULL DEFAULT '',
  amount_description text NOT NULL DEFAULT '',
  lines             bigint NOT NULL,
  amount            numeric,
  amount_usd        numeric,
  currency          text,
  loaded_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, marketplace_name, transaction_type, amount_type, amount_description)
);
