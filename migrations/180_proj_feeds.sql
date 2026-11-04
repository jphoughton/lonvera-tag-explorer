-- Projection feeds (server-side pulls from the source APIs): the per-feed mode switch, shadow tables for the dual-run, the attempt log,
-- the verification results and the (encrypted) ExpandFi refresh token. Additive and idempotent. No customer-level data.
-- The shadow tables repeat the column layout of the sheet-fed tables in migrations 140 and 170 on purpose (this file must apply on its own);
-- `python -m etl.feeds verify` reports any column drift between a shadow table and its main table.

-- Which source the app reads for each feed: 'sheet' (sheet raw tabs, today's behaviour), 'shadow' (sheet stays authoritative; the direct pull is
-- written to proj_feedx_* and compared), 'direct' (the direct pull writes the main table; the sheet read is the fallback). No row = 'sheet'.
CREATE TABLE IF NOT EXISTS proj_feed_config (
  feed        text PRIMARY KEY,
  mode        text NOT NULL DEFAULT 'sheet' CHECK (mode IN ('sheet', 'shadow', 'direct')),
  changed_at  timestamptz NOT NULL DEFAULT now(),
  changed_by  text,
  note        text CHECK (note IS NULL OR length(note) <= 300)
);

-- One row per direct pull attempt, any mode. note is scrubbed and at most 300 characters.
CREATE TABLE IF NOT EXISTS proj_feed_attempts (
  id          bigserial PRIMARY KEY,
  feed        text NOT NULL,
  mode        text NOT NULL,
  source      text NOT NULL,                     -- 'api:upstack' | 'api:warehance' | 'api:meta' | 'api:expandfi' | 'sheet:<tab>'
  started_at  timestamptz NOT NULL,
  finished_at timestamptz NOT NULL DEFAULT now(),
  status      text NOT NULL CHECK (status IN ('ok', 'failed', 'fallback', 'skipped')),
  rows        int,
  first_day   date,
  last_day    date,
  note        text CHECK (note IS NULL OR length(note) <= 300)
);
CREATE INDEX IF NOT EXISTS proj_feed_attempts_feed ON proj_feed_attempts (feed, id DESC);

-- Verification: latest result per feed and day, and one row per verify run (drives "N consecutive clean days").
CREATE TABLE IF NOT EXISTS proj_feed_verify (
  feed        text NOT NULL,
  day         date NOT NULL,
  status      text NOT NULL CHECK (status IN ('ok', 'fail', 'soft', 'sheet_stale')),
  n_keys      int NOT NULL DEFAULT 0,
  n_bad       int NOT NULL DEFAULT 0,
  worst_col   text,
  worst_abs   numeric(18,4),
  worst_rel   numeric(12,6),
  detail      text CHECK (detail IS NULL OR length(detail) <= 300),
  checked_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (feed, day)
);
CREATE TABLE IF NOT EXISTS proj_feed_verify_runs (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  feed        text NOT NULL,
  window_from date,
  window_to   date,
  days_checked int NOT NULL DEFAULT 0,
  days_failed  int NOT NULL DEFAULT 0,
  days_soft    int NOT NULL DEFAULT 0,
  days_stale   int NOT NULL DEFAULT 0,
  schema_ok    boolean NOT NULL DEFAULT true,
  note         text CHECK (note IS NULL OR length(note) <= 300)
);
CREATE INDEX IF NOT EXISTS proj_feed_verify_runs_feed ON proj_feed_verify_runs (feed, id DESC);

-- ExpandFi OAuth: the refresh token, encrypted with FEEDS_ENC_KEY. The only table in this module that holds a credential; no API route reads it.
CREATE TABLE IF NOT EXISTS feed_oauth (
  provider           text PRIMARY KEY,
  client_id          text NOT NULL,
  enc_refresh_token  bytea NOT NULL,
  scopes             text,
  access_expires_at  timestamptz,
  rotated_at         timestamptz NOT NULL DEFAULT now(),
  last_error         text CHECK (last_error IS NULL OR length(last_error) <= 300)
);

-- ---------------------------------------------------------------------------------------------------------------- shadow tables
CREATE TABLE IF NOT EXISTS proj_feedx_orders (
  day          date PRIMARY KEY,
  nc_rev       numeric(18,2) NOT NULL DEFAULT 0,
  rc_rev       numeric(18,2) NOT NULL DEFAULT 0,
  nc_orders    numeric(18,2) NOT NULL DEFAULT 0,
  total_rev    numeric(18,2) NOT NULL DEFAULT 0,
  total_orders numeric(18,2) NOT NULL DEFAULT 0,
  tax          numeric(18,2) NOT NULL DEFAULT 0,
  refunds      numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proj_feedx_amazon (
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

CREATE TABLE IF NOT EXISTS proj_feedx_platform (
  day               date NOT NULL,
  platform          text NOT NULL,
  spend             numeric(18,2) NOT NULL DEFAULT 0,
  purchases         numeric(18,2) NOT NULL DEFAULT 0,
  purchase_value    numeric(18,2) NOT NULL DEFAULT 0,
  nc_purchases      numeric(18,2) NOT NULL DEFAULT 0,
  nc_purchase_value numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks       numeric(18,2) NOT NULL DEFAULT 0,
  clicks            numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, platform)
);

CREATE TABLE IF NOT EXISTS proj_feedx_attribution (
  day       date NOT NULL,
  ad_source text NOT NULL,
  nc_orders numeric(18,2) NOT NULL DEFAULT 0,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  orders    numeric(18,2) NOT NULL DEFAULT 0,
  rev       numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, ad_source)
);

CREATE TABLE IF NOT EXISTS proj_feedx_traffic (
  day          date PRIMARY KEY,
  new_visitors numeric(18,2) NOT NULL DEFAULT 0,
  visitors     numeric(18,2) NOT NULL DEFAULT 0,
  sessions     numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proj_feedx_meta_reach (
  grain                  text NOT NULL CHECK (grain IN ('day','week','month')),
  period_key             text NOT NULL,
  period_start           date NOT NULL,
  period_end             date NOT NULL,
  impressions            numeric(18,2) NOT NULL DEFAULT 0,
  reach                  numeric(18,2) NOT NULL DEFAULT 0,
  unique_link_clicks     numeric(18,2) NOT NULL DEFAULT 0,
  link_clicks            numeric(18,2) NOT NULL DEFAULT 0,
  unique_outbound_clicks numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grain, period_key)
);

CREATE TABLE IF NOT EXISTS proj_feedx_product_reach (
  grain                  text NOT NULL CHECK (grain IN ('day','week','month')),
  period_key             text NOT NULL,
  period_start           date NOT NULL,
  period_end             date NOT NULL,
  product                text NOT NULL,
  unique_outbound_clicks numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grain, period_key, product)
);

CREATE TABLE IF NOT EXISTS proj_feedx_nc_product (
  day       date NOT NULL,
  product   text NOT NULL,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  nc_orders numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, product)
);

CREATE TABLE IF NOT EXISTS proj_feedx_anyclick (
  day       date NOT NULL,
  stage     text NOT NULL CHECK (stage IN ('Scaling','Testing')),
  nc        numeric(18,2) NOT NULL DEFAULT 0,
  nc_rev    numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, stage)
);

CREATE TABLE IF NOT EXISTS proj_feedx_bids (
  day            date PRIMARY KEY,
  spend          numeric(18,2) NOT NULL DEFAULT 0,
  capped_spend   numeric(18,2) NOT NULL DEFAULT 0,
  spend_x_cap    numeric(18,2) NOT NULL DEFAULT 0,
  floor_spend    numeric(18,2) NOT NULL DEFAULT 0,
  spend_x_floor  numeric(18,2) NOT NULL DEFAULT 0,
  loaded_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proj_feedx_units_daily (
  location    text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),
  product     text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),
  day         date NOT NULL,
  units       numeric(18,2) NOT NULL CHECK (units >= 0),
  source      text NOT NULL,
  is_estimate boolean NOT NULL DEFAULT false,
  loaded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, day)
);
CREATE INDEX IF NOT EXISTS proj_feedx_units_daily_day ON proj_feedx_units_daily (location, day);

CREATE TABLE IF NOT EXISTS proj_feedx_demand_snapshot (
  location  text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),
  product   text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),
  as_of     date NOT NULL,
  on_hand   numeric(18,2) NOT NULL,
  allocated numeric(18,2) NOT NULL DEFAULT 0,
  source    text NOT NULL,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, as_of)
);

-- ---------------------------------------------------------------------------------------------------------------- levers and the international non-US feed
-- Small numeric levers the feed jobs read at run time (edit with `python -m etl.feeds lever KEY VALUE`); seeded here, never overwritten by a re-run.
CREATE TABLE IF NOT EXISTS proj_feed_levers (
  key        text PRIMARY KEY,
  value      numeric(12,6) NOT NULL,
  note       text CHECK (note IS NULL OR length(note) <= 300),
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text
);
INSERT INTO proj_feed_levers (key, value, note, updated_by) VALUES
  ('intl_ratio.bull',     0.905, 'Sheet''s Intl estimate since 2026-09-16: Warehance non-US units x ratio = overseas 3PL units (fitted Aug 17 - Sep 15 against the 3PL export; Shopify routes non-US orders to the D1/overseas 3PL, Warehance auto-cancels them)', 'seed'),
  ('intl_ratio.liver',    0.895, 'Same source as intl_ratio.bull', 'seed'),
  ('intl_ratio.boneflex', 0.868, 'Same source as intl_ratio.bull', 'seed'),
  ('intl_ratio.default',  0.890, 'Blended factor (3PL units / Warehance non-US units, 2026-09-16 to 2026-10-12: 12,611 / 14,135); used for any product without its own ratio', 'seed')
ON CONFLICT (key) DO NOTHING;

-- Daily units on Warehance orders shipped to a non-US address (any Warehance status: those orders are auto-cancelled there because Shopify routes them
-- to the overseas warehouse), by Pacific order date, product and destination country. Raw bottle counts, not yet scaled by the ratio lever.
-- Written by the `intl_estimate` feed; no sheet tab carries this. Aggregates only.
CREATE TABLE IF NOT EXISTS proj_feedx_intl_nonus_daily (
  day        date NOT NULL,
  product    text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),
  country    text NOT NULL,                      -- ISO 3166 alpha-2, upper case
  units_raw  numeric(18,2) NOT NULL CHECK (units_raw >= 0),
  loaded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, product, country)
);
CREATE INDEX IF NOT EXISTS proj_feedx_intl_nonus_daily_day ON proj_feedx_intl_nonus_daily (day);

-- The estimate itself: raw units x the lever (the product's own ratio, else intl_ratio.default), flagged estimated. Half-up rounding at the
-- (day, product) level; the python job rounds half-to-even, so a tie can differ by one unit from proj_feedx_units_daily.
CREATE OR REPLACE VIEW proj_feedx_intl_estimate AS
SELECT r.day, r.product, sum(r.units_raw) AS units_raw, coalesce(p.value, d.value) AS factor,
       floor(sum(r.units_raw) * coalesce(p.value, d.value) + 0.5) AS units, true AS estimated
FROM proj_feedx_intl_nonus_daily r
LEFT JOIN proj_feed_levers p ON p.key = 'intl_ratio.' || r.product
LEFT JOIN proj_feed_levers d ON d.key = 'intl_ratio.default'
GROUP BY r.day, r.product, p.value, d.value;
