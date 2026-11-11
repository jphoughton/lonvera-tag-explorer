-- Stand-ins for the tables of three open PRs (206 Klaviyo, 207 landing pages, 210 Shopify orders), DDL only, used by test_brain_views to prove migration 231 creates its views when the sources exist.
-- Copied from those branches; delete this file once they are merged into build/v1 (migration 231 then runs on the real tables).
-- 206: Klaviyo flow and campaign metrics. Five tables and one view: the campaign and flow lists (klaviyo_campaigns, klaviyo_flows, klaviyo_flow_messages) and one row per Pacific day x campaign/flow x message x channel (klaviyo_daily), the copy of every message (klaviyo_message_content) and a per-message performance view (klaviyo_message_performance).
-- Written by python -m etl.klaviyo (etl/klaviyo.py): it replaces the pulled window of klaviyo_daily and upserts the lists. Additive and idempotent.
CREATE TABLE IF NOT EXISTS klaviyo_campaigns (
  id           text        PRIMARY KEY,
  name         text,
  channel      text        NOT NULL CHECK (channel IN ('email', 'sms')),
  status       text,
  send_time    timestamptz,
  subject      text,
  created_at   timestamptz,
  updated_at   timestamptz,
  raw          jsonb,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS klaviyo_flows (
  id            text        PRIMARY KEY,
  name          text,
  status        text,
  trigger_type  text,
  created_at    timestamptz,
  updated_at    timestamptz,
  raw           jsonb,
  loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS klaviyo_flow_messages (
  id         text        PRIMARY KEY,
  flow_id    text        NOT NULL,
  name       text,
  channel    text,
  subject    text,
  raw        jsonb,
  loaded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS klaviyo_flow_messages_flow_idx ON klaviyo_flow_messages (flow_id);

CREATE TABLE IF NOT EXISTS klaviyo_daily (
  day              date        NOT NULL,
  source           text        NOT NULL CHECK (source IN ('campaign', 'flow')),
  parent_id        text        NOT NULL,
  message_id       text        NOT NULL DEFAULT '',
  channel          text        NOT NULL CHECK (channel IN ('email', 'sms')),
  parent_name      text,
  sends            bigint      NOT NULL DEFAULT 0,
  delivered        bigint      NOT NULL DEFAULT 0,
  bounced          bigint      NOT NULL DEFAULT 0,
  opens            bigint      NOT NULL DEFAULT 0,
  unique_opens     bigint      NOT NULL DEFAULT 0,
  clicks           bigint      NOT NULL DEFAULT 0,
  unique_clicks    bigint      NOT NULL DEFAULT 0,
  unsubscribes     bigint      NOT NULL DEFAULT 0,
  spam_complaints  bigint      NOT NULL DEFAULT 0,
  orders           bigint      NOT NULL DEFAULT 0,
  revenue          numeric(14,2) NOT NULL DEFAULT 0,
  loaded_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, source, parent_id, message_id, channel)
);
CREATE INDEX IF NOT EXISTS klaviyo_daily_parent_idx ON klaviyo_daily (source, parent_id, day);


CREATE TABLE IF NOT EXISTS klaviyo_message_content (
  message_id    text        PRIMARY KEY,
  source        text        NOT NULL CHECK (source IN ('campaign', 'flow')),
  parent_id     text        NOT NULL,
  channel       text        NOT NULL CHECK (channel IN ('email', 'sms')),
  subject       text,
  preview_text  text,
  from_name     text,
  from_email    text,
  template_id   text,
  editor_type   text,
  html          text,
  body_text     text,
  image_count   integer,
  link_urls     text[],
  content_hash  text,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  raw           jsonb
);
CREATE INDEX IF NOT EXISTS klaviyo_message_content_parent_idx ON klaviyo_message_content (source, parent_id);

-- One row per message with its copy and its totals over everything loaded in klaviyo_daily. Uniques are SUMS of per-day uniques (a person who opens on two days counts twice), so the two rates built on them read a little high.
CREATE OR REPLACE VIEW klaviyo_message_performance AS
SELECT d.message_id, d.source, d.parent_id, d.channel,
       max(d.parent_name) AS parent_name,
       c.subject, c.preview_text, c.body_text, c.image_count, c.editor_type,
       min(d.day) FILTER (WHERE d.sends > 0) AS first_send_day,
       max(d.day) FILTER (WHERE d.sends > 0) AS last_send_day,
       sum(d.sends)::bigint AS sends, sum(d.delivered)::bigint AS delivered, sum(d.bounced)::bigint AS bounced,
       sum(d.opens)::bigint AS opens, sum(d.unique_opens)::bigint AS unique_opens_daily_sum,
       sum(d.clicks)::bigint AS clicks, sum(d.unique_clicks)::bigint AS unique_clicks_daily_sum,
       sum(d.unsubscribes)::bigint AS unsubscribes, sum(d.spam_complaints)::bigint AS spam_complaints,
       sum(d.orders)::bigint AS orders, sum(d.revenue)::numeric(14,2) AS revenue,
       round(sum(d.unique_opens)::numeric / NULLIF(sum(d.delivered), 0), 4) AS open_rate_daily_uniques,
       round(sum(d.unique_clicks)::numeric / NULLIF(sum(d.delivered), 0), 4) AS click_rate_daily_uniques,
       round(sum(d.orders)::numeric / NULLIF(sum(d.delivered), 0), 5) AS orders_per_delivered,
       round(sum(d.revenue) / NULLIF(sum(d.delivered), 0), 4) AS revenue_per_delivered,
       (c.message_id IS NOT NULL AND c.body_text IS NOT NULL) AS has_copy
FROM klaviyo_daily d
LEFT JOIN klaviyo_message_content c ON c.message_id = d.message_id
WHERE d.message_id <> ''
GROUP BY d.message_id, d.source, d.parent_id, d.channel, c.message_id, c.subject, c.preview_text, c.body_text, c.image_count, c.editor_type;

-- ---------------------------------------------------------------- comments

-- 207: landing page copy store and ad to landing page map. etl/landing_pages.py finds the page each Meta ad sends people to (the ad's destination link), reads every such page with a plain public
-- web request, stores the page copy as versions (a new version only when the text changes), and keeps one row per ad and page. View landing_page_top_ads ranks the ads that drive each page by
-- spend over the last 7 and 30 days, using public.ad_daily (Meta-reported purchases and revenue). Additive and idempotent. Not yet scheduled: see docs/LANDING_PAGES.md.
CREATE TABLE IF NOT EXISTS landing_pages (
  id               bigserial   PRIMARY KEY,
  url              text        NOT NULL UNIQUE,
  host             text        NOT NULL,
  path             text        NOT NULL,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  last_fetched_at  timestamptz,
  last_status      int,
  last_error       text,
  current_version_id bigint,
  active           boolean     NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS landing_pages_host ON landing_pages (host);

CREATE TABLE IF NOT EXISTS landing_page_versions (
  id               bigserial   PRIMARY KEY,
  landing_page_id  bigint      NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
  content_hash     text        NOT NULL,
  fetched_at       timestamptz NOT NULL DEFAULT now(),
  http_status      int,
  final_url        text,
  title            text,
  meta_description text,
  rendered_in_browser boolean  NOT NULL DEFAULT false,
  word_count       int         NOT NULL DEFAULT 0,
  blocks           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  ctas             jsonb       NOT NULL DEFAULT '[]'::jsonb,
  prices           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  guarantees       jsonb       NOT NULL DEFAULT '[]'::jsonb,
  reviews          jsonb       NOT NULL DEFAULT '[]'::jsonb,
  images           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  full_text        text        NOT NULL DEFAULT '',
  UNIQUE (landing_page_id, content_hash)
);
CREATE INDEX IF NOT EXISTS landing_page_versions_page ON landing_page_versions (landing_page_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS landing_page_ads (
  ad_id            text        NOT NULL,
  landing_page_id  bigint      NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
  raw_url          text        NOT NULL,
  source           text        NOT NULL,
  first_seen_date  date        NOT NULL DEFAULT current_date,
  last_seen_date   date        NOT NULL DEFAULT current_date,
  PRIMARY KEY (ad_id, landing_page_id)
);
CREATE INDEX IF NOT EXISTS landing_page_ads_page ON landing_page_ads (landing_page_id);

-- Ads already looked at, so an ad with no readable link is not asked about again every night. result: 'linked' | 'no_link' | 'error'.
CREATE TABLE IF NOT EXISTS landing_page_ad_checks (
  ad_id       text        PRIMARY KEY,
  checked_at  timestamptz NOT NULL DEFAULT now(),
  result      text        NOT NULL,
  detail      text
);

-- Per landing page, per window (7 or 30 days back from today in Pacific time, matching the Pacific dates stored in ad_daily), per ad: spend, Meta-reported purchases and revenue, ROAS, and the ad's rank by spend inside the page.
-- An ad whose creative lists more than one link appears under each page with its FULL spend, so do not add spend across pages.
CREATE OR REPLACE VIEW landing_page_top_ads AS
SELECT lp.id AS landing_page_id, lp.url, w.window_days, a.ad_id, ma.ad_name, ma.campaign_id,
       s.spend, s.purchases, s.conv_value AS revenue,
       CASE WHEN s.spend > 0 THEN round(s.conv_value / s.spend, 3) END AS roas,
       row_number() OVER (PARTITION BY lp.id, w.window_days ORDER BY s.spend DESC, a.ad_id) AS rank_by_spend
FROM landing_pages lp
JOIN landing_page_ads a ON a.landing_page_id = lp.id
CROSS JOIN (VALUES (7), (30)) AS w(window_days)
JOIN LATERAL (SELECT sum(d.spend) AS spend, sum(d.purchases) AS purchases, sum(d.conv_value) AS conv_value
              FROM ad_daily d WHERE d.ad_id = a.ad_id AND d.day > (now() AT TIME ZONE 'America/Los_Angeles')::date - w.window_days) s ON s.spend > 0
LEFT JOIN meta_ads ma ON ma.ad_id = a.ad_id;

-- ---------------------------------------------------------------- comments

-- 210: order-level Shopify orders of the last 120 days (rolling), loaded nightly from the ExpandFi warehouse (merchant 1452, table shopify_orders, read-only) by
-- python -m etl.shopify_orders (feeds service, 21:00 UTC). shopify_orders = one row per order, shopify_order_lines = one row per line item.
-- No personal data: no email, name, address or phone; the customer is the Shopify customer id only; the ship-to is country and region only.
-- Rows older than 120 days are deleted each run (lines go with their order); a re-run overwrites the same orders. Both tables are in the nightly pg_dump (whole database).
-- Numbers: order_id is the Shopify order id (not the ExpandFi row id); order_date is the Pacific day of processed_at (created_at in the warehouse is sync time, not order time).
-- Additive and idempotent. Numbering skips 202-209 on purpose (202 and 203 live on an unmerged branch).
CREATE TABLE IF NOT EXISTS shopify_orders (
  order_id             bigint        PRIMARY KEY,
  order_number         text,
  processed_at         timestamptz   NOT NULL,
  order_date           date          NOT NULL,
  customer_id          text,
  is_new_customer      boolean       NOT NULL DEFAULT false,
  currency             text,
  subtotal_price       numeric(12,2),
  total_discounts      numeric(12,2),
  shipping_price       numeric(12,2),
  total_tax            numeric(12,2),
  total_price          numeric(12,2),
  current_total_price  numeric(12,2),
  refunded_amount      numeric(12,2),
  financial_status     text,
  fulfillment_status   text,
  source_name          text,
  app_id               text,
  tags                 text,
  discount_codes       text,
  ship_country         text,
  ship_region          text,
  cancelled_at         timestamptz,
  is_cancelled         boolean       NOT NULL DEFAULT false,
  is_test              boolean       NOT NULL DEFAULT false,
  is_rebill            boolean       NOT NULL DEFAULT false,
  is_trybe_gift        boolean       NOT NULL DEFAULT false,
  line_count           integer       NOT NULL DEFAULT 0,
  loaded_at            timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shopify_orders_processed_at_idx ON shopify_orders (processed_at);
CREATE INDEX IF NOT EXISTS shopify_orders_order_date_idx ON shopify_orders (order_date);
CREATE INDEX IF NOT EXISTS shopify_orders_customer_idx ON shopify_orders (customer_id);

CREATE TABLE IF NOT EXISTS shopify_order_lines (
  order_id       bigint        NOT NULL REFERENCES shopify_orders (order_id) ON DELETE CASCADE,
  line_no        integer       NOT NULL,
  sku            text,
  product_name   text,
  product        text          NOT NULL,
  quantity       integer       NOT NULL DEFAULT 0,
  price          numeric(12,2),
  line_discount  numeric(12,2),
  line_net       numeric(12,2),
  PRIMARY KEY (order_id, line_no)
);
CREATE INDEX IF NOT EXISTS shopify_order_lines_product_idx ON shopify_order_lines (product);

-- ---------------------------------------------------------------- comments
