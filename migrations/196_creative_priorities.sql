-- 196: the weekly creative priorities one-sheeter (page /priorities, python -m etl.creative_priorities). One snapshot per week, keyed by the Sunday (Pacific); the full ranked
-- rows, the thresholds used and the product baselines are stored as JSON so a past week reads exactly as it was produced. Re-running a week replaces its snapshot.
CREATE TABLE IF NOT EXISTS creative_priorities_snapshots (
  week_start   date        PRIMARY KEY,
  built_at     timestamptz NOT NULL DEFAULT now(),
  data_through date        NOT NULL,
  basis        text        NOT NULL DEFAULT 'meta',
  thresholds   jsonb       NOT NULL,
  baselines    jsonb       NOT NULL,
  rows         jsonb       NOT NULL,
  stats        jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_priorities_snapshots IS 'The weekly creative priorities one-sheeter: per product, the market cluster / ICP / pain point / story lines to iterate on (ads that work) and to produce for the first time, with the number of new concepts to brief and the numbers behind each row. One snapshot per week, kept as produced. Grain: one row per week (week_start = the Sunday, Pacific). Source: etl.creative_priorities from ad_metrics, family_daily (and the _upstack_nc twin), creative_stage_moves, ad_tags (linemap), taxonomy_lines, line_ingredients, ad_hooks. Refresh: Sundays, in the nightly etl (gated on Pacific time, non-fatal); by hand with python -m etl.creative_priorities [--week YYYY-MM-DD]; re-running a week replaces its snapshot. Coverage: from the week the migration was deployed; one row per Sunday. Caveats: Scored on Meta-reported numbers; the Upstack new-customer ROAS only raises a flag. Prioritisation, not a causal claim. | rows is a JSON array; each element carries section (''iterate'' | ''new''), product, rank, the line (cluster, icp, pain_point, story), concepts, score, confidence, flags, why, metrics and hooks. Scoring and thresholds: docs/CREATIVE_PRIORITIES.md.';
COMMENT ON COLUMN public.creative_priorities_snapshots.week_start IS 'The Sunday (Pacific) the snapshot belongs to (primary key).';
COMMENT ON COLUMN public.creative_priorities_snapshots.built_at IS 'When the snapshot was last written.';
COMMENT ON COLUMN public.creative_priorities_snapshots.data_through IS 'Last day of Meta data the snapshot used.';
COMMENT ON COLUMN public.creative_priorities_snapshots.basis IS 'Numbers basis of the scoring: ''meta'' (Meta-reported).';
COMMENT ON COLUMN public.creative_priorities_snapshots.thresholds IS 'JSON object: every threshold and weight constant of etl/creative_priorities.py as used for this snapshot.';
COMMENT ON COLUMN public.creative_priorities_snapshots.baselines IS 'JSON object by product slug: the product''s own averages (ROAS since launch / last 30 days, CPA, hit rate, new ads in 30 days) the lines are compared with.';
COMMENT ON COLUMN public.creative_priorities_snapshots.rows IS 'JSON array of the ranked rows (iterate and new sections, all products).';
COMMENT ON COLUMN public.creative_priorities_snapshots.stats IS 'JSON object: candidate counts, skipped counts, tagged and untagged spend, data_through.';
