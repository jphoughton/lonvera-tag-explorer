-- Google data from Upstack for the Marketing metrics pages (channel switch Meta | Google). Additive.
--
-- Two Upstack feeds, both at campaign grain (Google has no creatives to tag):
--   channel feed (query-channel-details, Google rows): spend, impressions, clicks and the platform's own purchases / conversion value
--   attribution (query-attribution, utm_source "Google Ads", any-click, 30-day window): Upstack-credited new customers / orders and their revenue,
--     keyed by the utm campaign NAME (Google campaign names match the channel feed's names)
-- google_daily joins them per day and campaign (rebuilt by etl.upstack_daily after every pull). Attribution rows whose utm campaign matches no
-- Google campaign ('(not set)', raw ids, deleted campaigns) stay as rows with in_feed = false so the totals tie to Upstack's Google total.

CREATE TABLE upstack_google_campaigns (
  campaign_id   text PRIMARY KEY,
  name          text NOT NULL,
  status        text,
  bid_strategy  text,
  campaign_type text NOT NULL,                       -- derived from the name (Upstack exposes no campaign type): Shopping | Search | Performance Max | Demand Gen | Other
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE upstack_google_channel_daily (
  day             date NOT NULL,
  campaign_id     text NOT NULL,
  spend           numeric(14,2) NOT NULL DEFAULT 0,
  impressions     numeric(14,0) NOT NULL DEFAULT 0,
  clicks          numeric(12,0) NOT NULL DEFAULT 0,
  purchases       numeric(12,2) NOT NULL DEFAULT 0,  -- channel.purchases: what Google counts as purchases (delivered through Upstack)
  purchases_value numeric(14,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, campaign_id)
);
CREATE INDEX upstack_google_channel_daily_day ON upstack_google_channel_daily (day);

CREATE TABLE upstack_google_attr_daily (
  day      date NOT NULL,
  campaign text NOT NULL,                            -- utm_campaign as clicked
  nc       numeric(12,2) NOT NULL DEFAULT 0,         -- orders.new_customers
  nc_rev   numeric(14,2) NOT NULL DEFAULT 0,         -- orders.new_customer_total_revenue
  orders   numeric(12,2) NOT NULL DEFAULT 0,         -- orders.count
  rev      numeric(14,2) NOT NULL DEFAULT 0,         -- orders.total_revenue
  PRIMARY KEY (day, campaign)
);

-- days whose Google attribution was pulled completely (a day with no Google orders is still a pulled day)
CREATE TABLE upstack_google_days (
  day       date PRIMARY KEY,
  pulled_at timestamptz NOT NULL DEFAULT now(),
  campaigns int NOT NULL DEFAULT 0,
  nc        numeric(12,2) NOT NULL DEFAULT 0,
  orders    numeric(12,2) NOT NULL DEFAULT 0
);

-- derived, rebuilt in full: one row per (day, campaign)
CREATE TABLE google_daily (
  day             date NOT NULL,
  campaign_key    text NOT NULL,                     -- Google campaign id, or 'name:<utm campaign>' when the utm value is in no Google campaign
  campaign_name   text NOT NULL,
  campaign_type   text NOT NULL,
  in_feed         boolean NOT NULL,
  spend           numeric(14,2) NOT NULL DEFAULT 0,
  impressions     numeric(14,0) NOT NULL DEFAULT 0,
  clicks          numeric(12,0) NOT NULL DEFAULT 0,
  purchases       numeric(12,2) NOT NULL DEFAULT 0,  -- Google-reported
  purchases_value numeric(14,2) NOT NULL DEFAULT 0,
  nc              numeric(12,2) NOT NULL DEFAULT 0,  -- Upstack attribution
  nc_rev          numeric(14,2) NOT NULL DEFAULT 0,
  orders          numeric(12,2) NOT NULL DEFAULT 0,
  rev             numeric(14,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, campaign_key)
);
CREATE INDEX google_daily_day ON google_daily (day);
CREATE INDEX google_daily_type_day ON google_daily (campaign_type, day);
