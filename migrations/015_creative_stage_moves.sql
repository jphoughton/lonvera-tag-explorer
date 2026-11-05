-- 015: creative_stage_moves, the data behind the Test to scale page (/promotions). Additive: three views, nothing existing is altered. Idempotent (CREATE OR REPLACE).
--
-- A creative (tracker family) is MOVED from testing to scaling when it had spend in a TESTING campaign and its FIRST spend in a SCALING campaign came on a LATER
-- day. Meta cannot move an ad between campaigns: the buyer duplicates the creative into a scaling campaign, so the move is read off the daily spend, not off an
-- event. Stage is the campaign's stage on the day (family_daily.stage: 'testing' = campaign name contains 'test', everything else 'scaling').
--
-- One row per creative that ever spent. Three attribution bases, like the rest of the app: creative_stage_moves (Meta's own numbers, from family_daily) and
-- creative_stage_moves_upstack_nc / _upstack_all (Upstack new customers / all orders, from family_daily_upstack_<basis>). Spend and link clicks are Meta's in every
-- view; purchases and revenue differ. The API reads the view through app/queries.source_sql, which swaps the name per request (source=meta|upstack, basis=nc|all).
--
-- Columns
--   path                 moved | testing_only | scaling_only | same_day (testing and scaling both first spent that day: not a move) | scaling_first
--                        (scaling spend came first, testing later: not a move) | unclassified_only
--   moved                path = 'moved'
--   began_in_testing     path in (moved, testing_only): the creatives a test-to-scale rate is about
--   first_day            first day with spend in any stage = the launch day as far as the data goes
--   launched_before_data_start   first_day is the first day the table holds: the real launch day is earlier, days_in_test is a lower bound
--   days_in_test         scaling_first_day - testing_first_day (moved only)
--   test_*               TESTING-stage figures on days before the first scaling day (all testing days when it never scaled): the evidence the buyer had
--   test_after_spend     testing-stage spend on or after the first scaling day (the original kept running in testing)
--   scale_*              SCALING-stage figures since the move (every scaling day through data_through)
--   last7_*              last seven days of data, any stage; last7_scale_* scaling only
--   live / live_scaling  spent on data_through or the day before (the app's 'live now'), any stage / scaling only
--   moves_that_day       creatives that moved on the same scaling_first_day; bulk_move_day = moved and 40 or more did (a mass relaunch, not a test winner;
--                        the API takes its own threshold, bulk_min)
-- The creative's product, concept, tags and primary tracker row (the one that carries its metrics, opened by /ad/<tracker_row_id>) come from ad_metrics.is_first_row.
DO $$
DECLARE
  sfx text;
  tpl text;
BEGIN
  tpl := $tpl$
CREATE OR REPLACE VIEW creative_stage_moves%1$s AS
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
    EXECUTE format(tpl, sfx);
  END LOOP;
END $$;

COMMENT ON VIEW creative_stage_moves IS 'One row per creative family that ever spent: first spend in testing and in scaling, days in test, testing-before-move and scaling-since-move numbers (Meta''s own purchases / revenue). moved = testing spend followed by a later first scaling day. Variants _upstack_nc and _upstack_all read the Upstack attribution. See docs/TEST_TO_SCALE.md.';
