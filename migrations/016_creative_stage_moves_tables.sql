-- 016: the Test to scale data is PRECOMPUTED. Migration 015 made creative_stage_moves* views; evaluated per request (and joined to ad_daily for the ad sets a
-- creative scaled in) they took 6 to 20+ seconds, and anything wider than 30 days hit the 20 s gateway limit. Now:
--   creative_stage_moves, _upstack_nc, _upstack_all           TABLES with the same names and columns as the 015 views (agents and the app query them unchanged)
--   creative_scaling_placements, _upstack_nc, _upstack_all    one row per (moved creative, scaling campaign, ad set): spend / purchases / revenue since the move
--   refresh_creative_stage_moves()   rebuilds all six in ONE transaction (DELETE + INSERT: readers keep seeing the old rows until commit) and stamps
--                                    app_meta stage_moves_built_at / stage_moves_data_through. The nightly etl runs it (python -m etl.stage_moves) after run_daily
--                                    and upstack_daily; run it by hand the same way. The logic is exactly migration 015's view (its header lists every column).
-- This migration fills the tables once, so the page works the moment it is applied.
DO $$
DECLARE
  sfx text;
BEGIN
  FOREACH sfx IN ARRAY ARRAY['', '_upstack_nc', '_upstack_all'] LOOP
    IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('public.creative_stage_moves' || sfx)) = 'v' THEN
      EXECUTE format('CREATE TABLE csm_new%1$s AS SELECT * FROM creative_stage_moves%1$s WITH NO DATA', sfx);
      EXECUTE format('DROP VIEW creative_stage_moves%1$s', sfx);
      EXECUTE format('ALTER TABLE csm_new%1$s RENAME TO creative_stage_moves%1$s', sfx);
      EXECUTE format('ALTER TABLE creative_stage_moves%1$s ADD PRIMARY KEY (family_key)', sfx);
      EXECUTE format('CREATE INDEX creative_stage_moves%1$s_scaling ON creative_stage_moves%1$s (scaling_first_day) WHERE moved', sfx);
      EXECUTE format('CREATE INDEX creative_stage_moves%1$s_first ON creative_stage_moves%1$s (first_day)', sfx);
    END IF;
    EXECUTE format($t$CREATE TABLE IF NOT EXISTS creative_scaling_placements%1$s (
        family_key  text NOT NULL,
        campaign_id text NOT NULL,
        campaign    text NOT NULL,
        adset_id    text NOT NULL,
        adset       text NOT NULL,
        ads         int NOT NULL,
        spend       numeric NOT NULL,
        purchases   numeric NOT NULL,
        revenue     numeric NOT NULL,
        first_day   date NOT NULL,
        last_day    date NOT NULL,
        PRIMARY KEY (family_key, campaign_id, adset_id))$t$, sfx);
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION refresh_creative_stage_moves() RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
  sfx text;
  tpl text;
BEGIN
  tpl := $tpl$
WITH bounds AS (
  SELECT min(day) AS data_start, max(day) AS data_through FROM family_daily%1$s
), firsts AS (
  SELECT family_key,
         min(day) FILTER (WHERE spend > 0)                          AS first_day,
         min(day) FILTER (WHERE spend > 0 AND stage = 'testing')    AS testing_first_day,
         min(day) FILTER (WHERE spend > 0 AND stage = 'scaling')    AS scaling_first_day,
         max(day) FILTER (WHERE spend > 0)                          AS last_spend_day,
         max(day) FILTER (WHERE spend > 0 AND stage = 'scaling')    AS scaling_last_day
  FROM family_daily%1$s GROUP BY family_key
), sums AS (
  SELECT g.family_key,
    coalesce(sum(f.spend)       FILTER (WHERE f.stage = 'testing' AND (g.scaling_first_day IS NULL OR f.day < g.scaling_first_day)), 0) AS test_spend,
    coalesce(sum(f.purchases)   FILTER (WHERE f.stage = 'testing' AND (g.scaling_first_day IS NULL OR f.day < g.scaling_first_day)), 0) AS test_purchases,
    coalesce(sum(f.revenue)     FILTER (WHERE f.stage = 'testing' AND (g.scaling_first_day IS NULL OR f.day < g.scaling_first_day)), 0) AS test_revenue,
    coalesce(sum(f.link_clicks) FILTER (WHERE f.stage = 'testing' AND (g.scaling_first_day IS NULL OR f.day < g.scaling_first_day)), 0) AS test_link_clicks,
    count(*)                    FILTER (WHERE f.stage = 'testing' AND f.spend > 0 AND (g.scaling_first_day IS NULL OR f.day < g.scaling_first_day)) AS test_days,
    coalesce(sum(f.spend)       FILTER (WHERE f.stage = 'testing' AND g.scaling_first_day IS NOT NULL AND f.day >= g.scaling_first_day), 0) AS test_after_spend,
    coalesce(sum(f.spend)       FILTER (WHERE f.stage = 'scaling'), 0)     AS scale_spend,
    coalesce(sum(f.purchases)   FILTER (WHERE f.stage = 'scaling'), 0)     AS scale_purchases,
    coalesce(sum(f.revenue)     FILTER (WHERE f.stage = 'scaling'), 0)     AS scale_revenue,
    coalesce(sum(f.link_clicks) FILTER (WHERE f.stage = 'scaling'), 0)     AS scale_link_clicks,
    count(*)                    FILTER (WHERE f.stage = 'scaling' AND f.spend > 0) AS scale_days,
    coalesce(sum(f.spend)       FILTER (WHERE f.stage NOT IN ('testing', 'scaling')), 0) AS unclassified_spend,
    coalesce(sum(f.spend)       FILTER (WHERE f.day > b.data_through - 7), 0)     AS last7_spend,
    coalesce(sum(f.purchases)   FILTER (WHERE f.day > b.data_through - 7), 0)     AS last7_purchases,
    coalesce(sum(f.revenue)     FILTER (WHERE f.day > b.data_through - 7), 0)     AS last7_revenue,
    coalesce(sum(f.spend)       FILTER (WHERE f.day > b.data_through - 7 AND f.stage = 'scaling'), 0) AS last7_scale_spend,
    coalesce(sum(f.purchases)   FILTER (WHERE f.day > b.data_through - 7 AND f.stage = 'scaling'), 0) AS last7_scale_purchases,
    coalesce(sum(f.revenue)     FILTER (WHERE f.day > b.data_through - 7 AND f.stage = 'scaling'), 0) AS last7_scale_revenue
  FROM firsts g
  JOIN family_daily%1$s f ON f.family_key = g.family_key
  CROSS JOIN bounds b
  GROUP BY g.family_key
), shaped AS (
  SELECT g.family_key, g.first_day, g.testing_first_day, g.scaling_first_day, g.last_spend_day, g.scaling_last_day,
         CASE WHEN g.testing_first_day IS NULL AND g.scaling_first_day IS NULL THEN 'unclassified_only'
              WHEN g.testing_first_day IS NULL THEN 'scaling_only'
              WHEN g.scaling_first_day IS NULL THEN 'testing_only'
              WHEN g.testing_first_day < g.scaling_first_day THEN 'moved'
              WHEN g.testing_first_day = g.scaling_first_day THEN 'same_day'
              ELSE 'scaling_first' END AS path
  FROM firsts g WHERE g.first_day IS NOT NULL
)
SELECT s.family_key,
       am.tracker_row_id,
       p.id AS product_id, p.slug AS product_slug, p.name AS product_name,
       r.concept_id,
       COALESCE(NULLIF(btrim(r.ad_family), ''), r.concept_id) AS display,
       r.ad_type, r.status AS tracker_status, r.launch_date AS tracker_launch_date,
       CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.cluster, tl.cluster)), ''), '(none)') END AS cluster,
       CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.icp, tl.icp)), ''), '(none)') END AS icp,
       CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.pain_point, tl.pain_point)), ''), '(none)') END AS pain,
       b.data_start, b.data_through,
       s.path,
       (s.path IN ('moved', 'testing_only')) AS began_in_testing,
       (s.path = 'moved') AS moved,
       s.first_day, s.testing_first_day, s.scaling_first_day, s.last_spend_day, s.scaling_last_day,
       CASE WHEN s.path = 'moved' THEN s.scaling_first_day - s.testing_first_day END AS days_in_test,
       (s.first_day = b.data_start) AS launched_before_data_start,
       m.test_spend, m.test_purchases, m.test_revenue, m.test_link_clicks, m.test_days, m.test_after_spend,
       m.scale_spend, m.scale_purchases, m.scale_revenue, m.scale_link_clicks, m.scale_days,
       m.unclassified_spend,
       m.last7_spend, m.last7_purchases, m.last7_revenue, m.last7_scale_spend, m.last7_scale_purchases, m.last7_scale_revenue,
       (s.last_spend_day >= b.data_through - 1) AS live,
       (s.scaling_last_day IS NOT NULL AND s.scaling_last_day >= b.data_through - 1) AS live_scaling,
       count(*) FILTER (WHERE s.path = 'moved') OVER (PARTITION BY s.scaling_first_day) AS moves_that_day,
       (s.path = 'moved' AND count(*) FILTER (WHERE s.path = 'moved') OVER (PARTITION BY s.scaling_first_day) >= 40) AS bulk_move_day
FROM shaped s
JOIN sums m ON m.family_key = s.family_key
CROSS JOIN bounds b
LEFT JOIN ad_metrics am ON am.family_key = s.family_key AND am.is_first_row
LEFT JOIN tracker_rows r ON r.id = am.tracker_row_id
LEFT JOIN products p ON p.id = am.product_id
LEFT JOIN ad_tags t ON t.tracker_row_id = am.tracker_row_id AND t.is_current AND t.tag_set = p.tag_set
LEFT JOIN taxonomy_lines tl ON tl.id = t.line_id
$tpl$;
  FOREACH sfx IN ARRAY ARRAY['', '_upstack_nc', '_upstack_all'] LOOP
    EXECUTE format('DELETE FROM creative_stage_moves%1$s', sfx);
    EXECUTE format('INSERT INTO creative_stage_moves%1$s ', sfx) || format(tpl, sfx);
    EXECUTE format('DELETE FROM creative_scaling_placements%1$s', sfx);
    EXECUTE format($q$
      INSERT INTO creative_scaling_placements%1$s
      SELECT m.tracker_family_key, c.campaign_id, c.name, COALESCE(a.adset_id, ''), COALESCE(a.adset_name, ''), count(DISTINCT d.ad_id),
             sum(d.spend), sum(d.purchases), sum(d.conv_value), min(d.day), max(d.day)
      FROM ad_daily%1$s d
      LEFT JOIN meta_ads a ON a.ad_id = d.ad_id
      JOIN meta_family_map m ON m.meta_family_key = COALESCE(a.meta_family_key, d.meta_family_key) AND m.tracker_family_key IS NOT NULL
      JOIN meta_campaigns c ON c.campaign_id = d.campaign_id AND c.stage = 'scaling'
      JOIN creative_stage_moves%1$s mv ON mv.family_key = m.tracker_family_key AND mv.moved AND d.day >= mv.scaling_first_day
      WHERE d.spend > 0
      GROUP BY 1, 2, 3, 4, 5$q$, sfx);
  END LOOP;
  INSERT INTO app_meta (key, value) VALUES ('stage_moves_built_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'))
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
  INSERT INTO app_meta (key, value) VALUES ('stage_moves_data_through', COALESCE((SELECT max(day)::text FROM family_daily), ''))
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
END
$fn$;

SELECT refresh_creative_stage_moves();

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_stage_moves IS 'One row per creative (tracker family) that ever spent: the first day it spent in a Testing campaign and in a Scaling campaign, whether it MOVED from testing to scaling (testing spend, then a later first scaling day), days in test, its numbers in testing before the move and in scaling since, the last 7 days and whether it is live. Behind the Test to scale page. Meta''s own purchases and revenue; the _upstack_nc / _upstack_all tables read the Upstack attribution. Precomputed (migration 016; it was a view in 015). Grain: one row per family_key that has spend (family_daily rows with spend > 0). Source: refresh_creative_stage_moves() from public.family_daily (stage per campaign name: ''test'' = testing) + ad_metrics first row + products + ad_tags + taxonomy_lines. Refresh: nightly (etl.stage_moves, after run_daily and upstack_daily): DELETE + INSERT in one transaction; app_meta stage_moves_built_at / stage_moves_data_through say when. Coverage: 2026-05-06 through yesterday; about 2.6K creatives, 610 moved on 2026-11-04. Caveats: A move is inferred from daily spend, not from an event: Meta cannot move an ad between campaigns, the buyer duplicates the creative into a scaling campaign. Testing and scaling on the same first day, scaling first and scaling only are NOT moves (see path). | Bulk moves: on 2026-08-22, 2026-10-22 and 2026-10-23 76, 187 and 80 creatives moved in one day (mass relaunches into new scaling campaigns, not test winners). bulk_move_day marks days with 40 or more moves; the app lets you hide them. | first_day is the first day with spend in any stage: creatives already running on the first day of data (launched_before_data_start, 2026-05-06) have an earlier real launch day, so days_in_test is a lower bound. | test_* counts TESTING-stage rows on days before the first scaling day (all testing days when it never scaled); scale_* counts SCALING-stage rows since the move through the last day. Testing spend that continues after the move is test_after_spend only. | Spend and link clicks are Meta''s in every view; purchases and revenue are Meta-reported here, Upstack''s in the _upstack_ views. Not additive across platforms.';
COMMENT ON COLUMN public.creative_stage_moves.family_key IS 'Tracker family key (lower); joins family_daily, ad_metrics, creative_assets.';
COMMENT ON COLUMN public.creative_stage_moves.tracker_row_id IS 'tracker_rows.id of the family''s first row (the one that carries its metrics); the app opens /ad/<id>.';
COMMENT ON COLUMN public.creative_stage_moves.product_id IS 'products.id of that row (NULL when the family has no first row).';
COMMENT ON COLUMN public.creative_stage_moves.product_slug IS 'Product slug (''unassigned'' for creatives of no product).';
COMMENT ON COLUMN public.creative_stage_moves.product_name IS 'Product name.';
COMMENT ON COLUMN public.creative_stage_moves.concept_id IS 'Concept id of the first tracker row.';
COMMENT ON COLUMN public.creative_stage_moves.display IS 'Display name: the ad family as typed in the tracker, else the concept id.';
COMMENT ON COLUMN public.creative_stage_moves.ad_type IS 'VID / IMG (tracker).';
COMMENT ON COLUMN public.creative_stage_moves.tracker_status IS 'Tracker status of the first row, e.g. ''8 - Launched''.';
COMMENT ON COLUMN public.creative_stage_moves.tracker_launch_date IS 'Launch date typed in the tracker (undercounts the older ads; first_day is the one the page uses).';
COMMENT ON COLUMN public.creative_stage_moves.cluster IS 'Market cluster of the current tag (''(untagged)'' when none).';
COMMENT ON COLUMN public.creative_stage_moves.icp IS 'ICP of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves.pain IS 'Pain point of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves.data_start IS 'First day this view''s family_daily holds (2026-05-06 today).';
COMMENT ON COLUMN public.creative_stage_moves.data_through IS 'Last day this view''s family_daily holds.';
COMMENT ON COLUMN public.creative_stage_moves.path IS 'moved | testing_only | scaling_only | same_day (testing and scaling both first spent that day) | scaling_first (scaling before testing) | unclassified_only.';
COMMENT ON COLUMN public.creative_stage_moves.began_in_testing IS 'true for moved and testing_only: the creatives a test-to-scale rate is about.';
COMMENT ON COLUMN public.creative_stage_moves.moved IS 'true when path = ''moved'': spent in testing and its first scaling day is later.';
COMMENT ON COLUMN public.creative_stage_moves.first_day IS 'First day with spend in any stage = the launch day as far as the data goes.';
COMMENT ON COLUMN public.creative_stage_moves.testing_first_day IS 'First day with spend in a Testing campaign (NULL if none).';
COMMENT ON COLUMN public.creative_stage_moves.scaling_first_day IS 'First day with spend in a Scaling campaign (NULL if none): the day of the move for a moved creative.';
COMMENT ON COLUMN public.creative_stage_moves.last_spend_day IS 'Last day with spend in any stage.';
COMMENT ON COLUMN public.creative_stage_moves.scaling_last_day IS 'Last day with spend in a Scaling campaign.';
COMMENT ON COLUMN public.creative_stage_moves.days_in_test IS 'scaling_first_day - testing_first_day, for moved creatives only.';
COMMENT ON COLUMN public.creative_stage_moves.launched_before_data_start IS 'true when first_day is the first day of data: the creative was already running and its real launch day is earlier.';
COMMENT ON COLUMN public.creative_stage_moves.test_spend IS 'Testing-stage spend on days before the first scaling day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.test_purchases IS 'Purchases (Meta-reported here, Upstack in the _upstack_ views) on those days.';
COMMENT ON COLUMN public.creative_stage_moves.test_revenue IS 'Purchase value on those days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.test_link_clicks IS 'Link clicks on those days.';
COMMENT ON COLUMN public.creative_stage_moves.test_days IS 'Days with testing spend before the first scaling day.';
COMMENT ON COLUMN public.creative_stage_moves.test_after_spend IS 'Testing-stage spend on or after the first scaling day (the original kept running), dollars.';
COMMENT ON COLUMN public.creative_stage_moves.scale_spend IS 'Scaling-stage spend since the move, through the last day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.scale_purchases IS 'Purchases in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves.scale_revenue IS 'Purchase value in the Scaling stage, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.scale_link_clicks IS 'Link clicks in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves.scale_days IS 'Days with scaling spend.';
COMMENT ON COLUMN public.creative_stage_moves.unclassified_spend IS 'Spend on days with no campaign (stage ''unclassified''), dollars; none today.';
COMMENT ON COLUMN public.creative_stage_moves.last7_spend IS 'Spend over the last 7 days of data, any stage, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.last7_purchases IS 'Purchases over the last 7 days, any stage.';
COMMENT ON COLUMN public.creative_stage_moves.last7_revenue IS 'Purchase value over the last 7 days, any stage, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.last7_scale_spend IS 'Scaling-stage spend over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.last7_scale_purchases IS 'Scaling-stage purchases over the last 7 days.';
COMMENT ON COLUMN public.creative_stage_moves.last7_scale_revenue IS 'Scaling-stage purchase value over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves.live IS 'true when the creative spent on the last day of data or the day before (any stage).';
COMMENT ON COLUMN public.creative_stage_moves.live_scaling IS 'true when it spent in a Scaling campaign on the last day of data or the day before.';
COMMENT ON COLUMN public.creative_stage_moves.moves_that_day IS 'Creatives that moved on the same scaling_first_day (all creatives, not only this product).';
COMMENT ON COLUMN public.creative_stage_moves.bulk_move_day IS 'true when moved and 40 or more creatives moved that day: a mass relaunch, not a test winner.';
COMMENT ON TABLE public.creative_stage_moves_upstack_nc IS 'creative_stage_moves on the Upstack new customers basis: the same creatives and the same Meta spend, with purchases / revenue as Upstack credits them. Grain: one row per family_key that has spend. Source: refresh_creative_stage_moves() from public.family_daily_upstack_nc (same rules as creative_stage_moves). Refresh: nightly with creative_stage_moves (etl.stage_moves). Coverage: 2026-05-06 through yesterday. Caveats: Spend, link clicks and the move itself are Meta''s; test_purchases / test_revenue / scale_* / last7_* purchases and revenue change meaning. | A creative''s first spend day can differ from the Meta view only where Upstack''s history starts later than Meta''s daily data (both start 2026-05-06 today).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.family_key IS 'Tracker family key (lower); joins family_daily, ad_metrics, creative_assets.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.tracker_row_id IS 'tracker_rows.id of the family''s first row (the one that carries its metrics); the app opens /ad/<id>.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.product_id IS 'products.id of that row (NULL when the family has no first row).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.product_slug IS 'Product slug (''unassigned'' for creatives of no product).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.product_name IS 'Product name.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.concept_id IS 'Concept id of the first tracker row.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.display IS 'Display name: the ad family as typed in the tracker, else the concept id.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.ad_type IS 'VID / IMG (tracker).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.tracker_status IS 'Tracker status of the first row, e.g. ''8 - Launched''.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.tracker_launch_date IS 'Launch date typed in the tracker (undercounts the older ads; first_day is the one the page uses).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.cluster IS 'Market cluster of the current tag (''(untagged)'' when none).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.icp IS 'ICP of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.pain IS 'Pain point of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.data_start IS 'First day this view''s family_daily holds (2026-05-06 today).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.data_through IS 'Last day this view''s family_daily holds.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.path IS 'moved | testing_only | scaling_only | same_day (testing and scaling both first spent that day) | scaling_first (scaling before testing) | unclassified_only.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.began_in_testing IS 'true for moved and testing_only: the creatives a test-to-scale rate is about.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.moved IS 'true when path = ''moved'': spent in testing and its first scaling day is later.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.first_day IS 'First day with spend in any stage = the launch day as far as the data goes.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.testing_first_day IS 'First day with spend in a Testing campaign (NULL if none).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scaling_first_day IS 'First day with spend in a Scaling campaign (NULL if none): the day of the move for a moved creative.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last_spend_day IS 'Last day with spend in any stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scaling_last_day IS 'Last day with spend in a Scaling campaign.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.days_in_test IS 'scaling_first_day - testing_first_day, for moved creatives only.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.launched_before_data_start IS 'true when first_day is the first day of data: the creative was already running and its real launch day is earlier.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_spend IS 'Testing-stage spend on days before the first scaling day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_purchases IS 'Upstack new customers credited on those days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_revenue IS 'First-order revenue of those customers, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_link_clicks IS 'Link clicks on those days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_days IS 'Days with testing spend before the first scaling day.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.test_after_spend IS 'Testing-stage spend on or after the first scaling day (the original kept running), dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scale_spend IS 'Scaling-stage spend since the move, through the last day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scale_purchases IS 'Upstack new customers credited in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scale_revenue IS 'First-order revenue of those customers, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scale_link_clicks IS 'Link clicks in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.scale_days IS 'Days with scaling spend.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.unclassified_spend IS 'Spend on days with no campaign (stage ''unclassified''), dollars; none today.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_spend IS 'Spend over the last 7 days of data, any stage, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_purchases IS 'Upstack new customers over the last 7 days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_revenue IS 'First-order revenue over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_scale_spend IS 'Scaling-stage spend over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_scale_purchases IS 'Upstack new customers in the Scaling stage, last 7 days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.last7_scale_revenue IS 'First-order revenue in the Scaling stage, last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.live IS 'true when the creative spent on the last day of data or the day before (any stage).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.live_scaling IS 'true when it spent in a Scaling campaign on the last day of data or the day before.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.moves_that_day IS 'Creatives that moved on the same scaling_first_day (all creatives, not only this product).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_nc.bulk_move_day IS 'true when moved and 40 or more creatives moved that day: a mass relaunch, not a test winner.';
COMMENT ON TABLE public.creative_stage_moves_upstack_all IS 'creative_stage_moves on the Upstack all orders basis: the same creatives and the same Meta spend, with purchases / revenue as Upstack credits them. Grain: one row per family_key that has spend. Source: refresh_creative_stage_moves() from public.family_daily_upstack_all (same rules as creative_stage_moves). Refresh: nightly with creative_stage_moves (etl.stage_moves). Coverage: 2026-05-06 through yesterday. Caveats: Spend, link clicks and the move itself are Meta''s; test_purchases / test_revenue / scale_* / last7_* purchases and revenue change meaning. | A creative''s first spend day can differ from the Meta view only where Upstack''s history starts later than Meta''s daily data (both start 2026-05-06 today).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.family_key IS 'Tracker family key (lower); joins family_daily, ad_metrics, creative_assets.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.tracker_row_id IS 'tracker_rows.id of the family''s first row (the one that carries its metrics); the app opens /ad/<id>.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.product_id IS 'products.id of that row (NULL when the family has no first row).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.product_slug IS 'Product slug (''unassigned'' for creatives of no product).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.product_name IS 'Product name.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.concept_id IS 'Concept id of the first tracker row.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.display IS 'Display name: the ad family as typed in the tracker, else the concept id.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.ad_type IS 'VID / IMG (tracker).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.tracker_status IS 'Tracker status of the first row, e.g. ''8 - Launched''.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.tracker_launch_date IS 'Launch date typed in the tracker (undercounts the older ads; first_day is the one the page uses).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.cluster IS 'Market cluster of the current tag (''(untagged)'' when none).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.icp IS 'ICP of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.pain IS 'Pain point of the current tag.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.data_start IS 'First day this view''s family_daily holds (2026-05-06 today).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.data_through IS 'Last day this view''s family_daily holds.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.path IS 'moved | testing_only | scaling_only | same_day (testing and scaling both first spent that day) | scaling_first (scaling before testing) | unclassified_only.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.began_in_testing IS 'true for moved and testing_only: the creatives a test-to-scale rate is about.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.moved IS 'true when path = ''moved'': spent in testing and its first scaling day is later.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.first_day IS 'First day with spend in any stage = the launch day as far as the data goes.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.testing_first_day IS 'First day with spend in a Testing campaign (NULL if none).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scaling_first_day IS 'First day with spend in a Scaling campaign (NULL if none): the day of the move for a moved creative.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last_spend_day IS 'Last day with spend in any stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scaling_last_day IS 'Last day with spend in a Scaling campaign.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.days_in_test IS 'scaling_first_day - testing_first_day, for moved creatives only.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.launched_before_data_start IS 'true when first_day is the first day of data: the creative was already running and its real launch day is earlier.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_spend IS 'Testing-stage spend on days before the first scaling day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_purchases IS 'Upstack orders credited on those days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_revenue IS 'Revenue of those orders, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_link_clicks IS 'Link clicks on those days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_days IS 'Days with testing spend before the first scaling day.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.test_after_spend IS 'Testing-stage spend on or after the first scaling day (the original kept running), dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scale_spend IS 'Scaling-stage spend since the move, through the last day, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scale_purchases IS 'Upstack orders credited in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scale_revenue IS 'Revenue of those orders, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scale_link_clicks IS 'Link clicks in the Scaling stage.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.scale_days IS 'Days with scaling spend.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.unclassified_spend IS 'Spend on days with no campaign (stage ''unclassified''), dollars; none today.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_spend IS 'Spend over the last 7 days of data, any stage, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_purchases IS 'Upstack orders over the last 7 days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_revenue IS 'Revenue over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_scale_spend IS 'Scaling-stage spend over the last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_scale_purchases IS 'Upstack orders in the Scaling stage, last 7 days.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.last7_scale_revenue IS 'Revenue in the Scaling stage, last 7 days, dollars.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.live IS 'true when the creative spent on the last day of data or the day before (any stage).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.live_scaling IS 'true when it spent in a Scaling campaign on the last day of data or the day before.';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.moves_that_day IS 'Creatives that moved on the same scaling_first_day (all creatives, not only this product).';
COMMENT ON COLUMN public.creative_stage_moves_upstack_all.bulk_move_day IS 'true when moved and 40 or more creatives moved that day: a mass relaunch, not a test winner.';
COMMENT ON TABLE public.creative_scaling_placements IS 'Where each MOVED creative scales: one row per (creative, scaling campaign, ad set) with the spend, purchases and revenue of its ads there since the move, and how many ads. Behind the ad-set list of the Test to scale page. Grain: one row per (family_key, campaign_id, adset_id) for creatives with creative_stage_moves.moved. Source: refresh_creative_stage_moves() from ad_daily x meta_ads x meta_campaigns (stage ''scaling'') x meta_family_map, days on or after the creative''s first scaling day. Refresh: nightly (etl.stage_moves), with creative_stage_moves. Coverage: 2026-05-06 through yesterday; about 600 moved creatives. Caveats: Spend adds up, per creative, to creative_stage_moves.scale_spend (spend > 0 days only; zero-spend days with purchases are not here). | adset_id / adset are '''' for ad_daily rows whose ad has no meta_ads row. Meta-reported purchases / revenue here; the _upstack_ tables hold Upstack''s credit.';
COMMENT ON COLUMN public.creative_scaling_placements.family_key IS 'Tracker family key (creative_stage_moves.family_key).';
COMMENT ON COLUMN public.creative_scaling_placements.campaign_id IS 'Meta scaling campaign id.';
COMMENT ON COLUMN public.creative_scaling_placements.campaign IS 'Campaign name.';
COMMENT ON COLUMN public.creative_scaling_placements.adset_id IS 'Meta ad set id ('''' when unknown).';
COMMENT ON COLUMN public.creative_scaling_placements.adset IS 'Ad set name.';
COMMENT ON COLUMN public.creative_scaling_placements.ads IS 'Distinct Meta ads of the creative that spent in this ad set since the move.';
COMMENT ON COLUMN public.creative_scaling_placements.spend IS 'Spend since the move, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements.purchases IS 'Purchases (Meta-reported here) since the move.';
COMMENT ON COLUMN public.creative_scaling_placements.revenue IS 'Purchase value since the move, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements.first_day IS 'First day with spend there.';
COMMENT ON COLUMN public.creative_scaling_placements.last_day IS 'Last day with spend there.';
COMMENT ON TABLE public.creative_scaling_placements_upstack_nc IS 'creative_scaling_placements on the Upstack new customers basis: same ad sets and Meta spend, purchases / revenue as Upstack credits them. Grain: one row per (family_key, campaign_id, adset_id). Source: refresh_creative_stage_moves() from ad_daily_upstack_nc. Refresh: nightly (etl.stage_moves). Coverage: 2026-05-06 through yesterday. Caveats: Spend is Meta''s; purchases and revenue change meaning.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.family_key IS 'Tracker family key (creative_stage_moves.family_key).';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.campaign_id IS 'Meta scaling campaign id.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.campaign IS 'Campaign name.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.adset_id IS 'Meta ad set id ('''' when unknown).';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.adset IS 'Ad set name.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.ads IS 'Distinct Meta ads of the creative that spent in this ad set since the move.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.spend IS 'Spend since the move, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.purchases IS 'Upstack new customers credited since the move.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.revenue IS 'First-order revenue of those customers, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.first_day IS 'First day with spend there.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_nc.last_day IS 'Last day with spend there.';
COMMENT ON TABLE public.creative_scaling_placements_upstack_all IS 'creative_scaling_placements on the Upstack all orders basis: same ad sets and Meta spend, purchases / revenue as Upstack credits them. Grain: one row per (family_key, campaign_id, adset_id). Source: refresh_creative_stage_moves() from ad_daily_upstack_all. Refresh: nightly (etl.stage_moves). Coverage: 2026-05-06 through yesterday. Caveats: Spend is Meta''s; purchases and revenue change meaning.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.family_key IS 'Tracker family key (creative_stage_moves.family_key).';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.campaign_id IS 'Meta scaling campaign id.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.campaign IS 'Campaign name.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.adset_id IS 'Meta ad set id ('''' when unknown).';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.adset IS 'Ad set name.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.ads IS 'Distinct Meta ads of the creative that spent in this ad set since the move.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.spend IS 'Spend since the move, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.purchases IS 'Upstack orders credited since the move.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.revenue IS 'Revenue of those orders, dollars.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.first_day IS 'First day with spend there.';
COMMENT ON COLUMN public.creative_scaling_placements_upstack_all.last_day IS 'Last day with spend there.';
