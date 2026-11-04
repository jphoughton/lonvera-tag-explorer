-- Source flip (Meta | Upstack) for the Tags pages. Additive: nothing existing is altered.
--
-- Upstack mode = the same ads, the same Meta spend, but purchases / revenue are what Upstack's pixel attribution credits to the ad
-- (any-click, 30-day window, utm_source "Meta Ads"; one call per day and campaign at utm_ad grain). Two bases:
--   nc  = new customers (first orders) and their revenue  (default; the NC ROAS / NC CPA the pacing sheets read)
--   all = every attributed order and its revenue          (the like-for-like count for Meta's purchases)
--
-- Raw pulls (written by etl.upstack_daily) -> ad_daily_upstack (same shape as ad_daily + the NC measures; Meta spend / clicks / impressions copied
-- from ad_daily on every build so a Meta restatement flows through) -> per basis the same derived objects the Meta source has, with a
-- `_upstack_<basis>` suffix (the API swaps table names per request; see app/queries.py source_sql).

-- days that were pulled completely (a day with zero Meta orders is still a pulled day)
CREATE TABLE upstack_attr_days (
  day           date PRIMARY KEY,
  pulled_at     timestamptz NOT NULL DEFAULT now(),
  campaigns     int NOT NULL DEFAULT 0,              -- campaigns with orders or new customers that day
  ad_rows       int NOT NULL DEFAULT 0,
  api_calls     int NOT NULL DEFAULT 0,
  nc_campaigns  numeric(12,2) NOT NULL DEFAULT 0,    -- campaign-level new customers (what Upstack credits to Meta campaigns)
  nc_ads        numeric(12,2) NOT NULL DEFAULT 0,    -- the same, summed over the ad rows
  note          text
);

-- Upstack attribution at campaign x ad name grain (utm values as clicked; same ad name in several ad sets is summed)
CREATE TABLE upstack_attr_daily (
  day        date NOT NULL,
  campaign   text NOT NULL,                          -- utm_campaign value (campaign name, sometimes a raw id or '(not set)')
  ad         text NOT NULL,                          -- utm_ad value; '(ad not identified)' when Upstack could not split a campaign by ad
  nc         numeric(12,2) NOT NULL DEFAULT 0,       -- orders.new_customers
  nc_rev     numeric(14,2) NOT NULL DEFAULT 0,       -- orders.new_customer_total_revenue
  orders     numeric(12,2) NOT NULL DEFAULT 0,       -- orders.count
  rev        numeric(14,2) NOT NULL DEFAULT 0,       -- orders.total_revenue
  PRIMARY KEY (day, campaign, ad)
);
CREATE INDEX upstack_attr_daily_day ON upstack_attr_daily (day);

-- Upstack's own campaign-level numbers for the tie-out: attribution totals per campaign, and the ad platform feed (channel.*, Meta only).
CREATE TABLE upstack_campaign_daily (
  day       date NOT NULL,
  campaign  text NOT NULL,
  nc        numeric(12,2) NOT NULL DEFAULT 0,
  nc_rev    numeric(14,2) NOT NULL DEFAULT 0,
  orders    numeric(12,2) NOT NULL DEFAULT 0,
  rev       numeric(14,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, campaign)
);
CREATE TABLE upstack_channel_daily (
  day                date NOT NULL,
  campaign_id        text NOT NULL,                  -- Meta campaign id (Upstack campaign dimension)
  campaign_name      text,
  spend              numeric(14,2) NOT NULL DEFAULT 0,
  purchases          numeric(12,2) NOT NULL DEFAULT 0,   -- channel.purchases: the platform's own count (Meta-reported, delivered through Upstack)
  purchases_value    numeric(14,2) NOT NULL DEFAULT 0,
  nc_purchases       numeric(12,2) NOT NULL DEFAULT 0,
  nc_purchases_value numeric(14,2) NOT NULL DEFAULT 0,
  link_clicks        numeric(12,0) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, campaign_id)
);

-- Attribution rows the build could not tie to a Meta ad (utm values with no campaign / ad match: '(not set)', truncated ids, deleted ads).
-- Kept out of every ad, family and tag total; the tie-out reports them.
CREATE TABLE upstack_unmapped_daily (
  day      date NOT NULL,
  campaign text NOT NULL,
  ad       text NOT NULL,
  reason   text NOT NULL,                            -- 'not_identified' | 'no_campaign' | 'no_ad'
  nc       numeric(12,2) NOT NULL DEFAULT 0,
  nc_rev   numeric(14,2) NOT NULL DEFAULT 0,
  orders   numeric(12,2) NOT NULL DEFAULT 0,
  rev      numeric(14,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, campaign, ad)
);

-- ad_daily-shaped table, one row per (Meta ad, day) for every pulled day. purchases / conv_value = all attributed orders; nc_* = new customers.
CREATE TABLE ad_daily_upstack (
  ad_id           text NOT NULL,
  day             date NOT NULL,
  meta_family_key text NOT NULL,
  meta_family     text NOT NULL,
  campaign_id     text NOT NULL DEFAULT '',
  spend           numeric(14,2) NOT NULL DEFAULT 0,      -- Meta's spend for the ad that day (copy of ad_daily)
  purchases       numeric(12,2) NOT NULL DEFAULT 0,      -- Upstack orders credited to the ad (all)
  conv_value      numeric(14,2) NOT NULL DEFAULT 0,
  nc_purchases    numeric(12,2) NOT NULL DEFAULT 0,      -- Upstack new customers credited to the ad
  nc_conv_value   numeric(14,2) NOT NULL DEFAULT 0,
  link_clicks     numeric(12,0) NOT NULL DEFAULT 0,
  impressions     numeric(14,0),
  source          text NOT NULL DEFAULT 'upstack',
  loaded_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ad_id, day)
);
CREATE INDEX ad_daily_upstack_day ON ad_daily_upstack (day);
CREATE INDEX ad_daily_upstack_family_day ON ad_daily_upstack (meta_family_key, day);
CREATE INDEX ad_daily_upstack_campaign_day ON ad_daily_upstack (campaign_id, day);

-- Per basis: views with the exact column set of ad_daily / meta_ad_lifetime, and tables shaped like the Meta derived tables.
DO $$
DECLARE b text;
BEGIN
  FOREACH b IN ARRAY ARRAY['nc', 'all'] LOOP
    EXECUTE format($v$CREATE VIEW ad_daily_upstack_%1$s AS
      SELECT ad_id, day, meta_family_key, meta_family, campaign_id, spend,
             %2$s AS purchases, %3$s AS conv_value, link_clicks, source, loaded_at, impressions
      FROM ad_daily_upstack$v$, b,
      CASE b WHEN 'nc' THEN 'nc_purchases' ELSE 'purchases' END,
      CASE b WHEN 'nc' THEN 'nc_conv_value' ELSE 'conv_value' END);
    -- since launch in Upstack mode = every day held (Upstack has no all-time endpoint at ad grain)
    EXECUTE format($v$CREATE VIEW meta_ad_lifetime_upstack_%1$s AS
      SELECT ad_id, meta_family_key, NULL::text AS tracker_family_key, sum(spend) AS spend, sum(purchases) AS purchases, sum(conv_value) AS conv_value,
             sum(impressions) AS impressions, sum(link_clicks) AS link_clicks, 'meta_api'::text AS source, max(loaded_at) AS pulled_at, min(day) AS first_day
      FROM ad_daily_upstack_%1$s GROUP BY ad_id, meta_family_key$v$, b);
    EXECUTE format('CREATE TABLE family_daily_upstack_%1$s (LIKE family_daily INCLUDING ALL)', b);
    EXECUTE format('CREATE TABLE family_daily_active_upstack_%1$s (LIKE family_daily_active INCLUDING ALL)', b);
    EXECUTE format('CREATE TABLE ad_metrics_upstack_%1$s (LIKE ad_metrics INCLUDING ALL)', b);
    EXECUTE format('CREATE TABLE unmatched_meta_upstack_%1$s (LIKE unmatched_meta INCLUDING ALL)', b);
  END LOOP;
END $$;
