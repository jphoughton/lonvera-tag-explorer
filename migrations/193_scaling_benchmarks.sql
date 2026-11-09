-- 193: the scaling benchmark of the Test to scale page's two simple views ("What should be scaled", "Performance in scaling"): per product and Numbers basis, what the
-- Scaling campaigns spent and returned over the last 30 days of data. A testing creative is held against its own product's number. PRECOMPUTED like
-- creative_stage_moves (migration 016): refresh_scaling_benchmarks() runs in the same nightly step (python -m etl.stage_moves, same transaction) and once here.
CREATE TABLE IF NOT EXISTS scaling_benchmarks (
  basis        text    NOT NULL CHECK (basis IN ('meta', 'upstack_nc', 'upstack_all')),
  product_slug text    NOT NULL,
  day_from     date    NOT NULL,
  day_to       date    NOT NULL,
  creatives    integer NOT NULL,
  spend        numeric NOT NULL,
  purchases    numeric NOT NULL,
  revenue      numeric NOT NULL,
  PRIMARY KEY (basis, product_slug)
);

CREATE OR REPLACE FUNCTION refresh_scaling_benchmarks() RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
  sfx text;
BEGIN
  DELETE FROM scaling_benchmarks;
  FOREACH sfx IN ARRAY ARRAY['', '_upstack_nc', '_upstack_all'] LOOP
    EXECUTE format($q$
      INSERT INTO scaling_benchmarks (basis, product_slug, day_from, day_to, creatives, spend, purchases, revenue)
      SELECT %2$L, x.product_slug, x.through - 29, x.through, x.creatives, x.spend, x.purchases, x.revenue
      FROM (
        SELECT COALESCE(NULLIF(p.slug, 'unassigned'), 'none') AS product_slug, max(b.through) AS through,
               count(DISTINCT f.family_key) FILTER (WHERE f.spend > 0) AS creatives,
               sum(f.spend) AS spend, sum(f.purchases) AS purchases, sum(f.revenue) AS revenue
        FROM family_daily%1$s f
        CROSS JOIN (SELECT max(day) AS through FROM family_daily%1$s) b
        LEFT JOIN ad_metrics am ON am.family_key = f.family_key AND am.is_first_row
        LEFT JOIN products p ON p.id = am.product_id
        WHERE f.stage = 'scaling' AND f.day > b.through - 30
        GROUP BY COALESCE(NULLIF(p.slug, 'unassigned'), 'none')
      ) x$q$, sfx, CASE WHEN sfx = '' THEN 'meta' ELSE substr(sfx, 2) END);
  END LOOP;
END
$fn$;

SELECT refresh_scaling_benchmarks();

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.scaling_benchmarks IS 'What the Scaling campaigns spent and returned over the last 30 days of data, per product and Numbers basis: the benchmark the Test to scale page holds a testing creative against (''What should be scaled'': is its testing ROAS or CPA at least as good as its product''s scaling average?). Grain: one row per (basis, product_slug) with scaling spend in the 30 days. Source: refresh_scaling_benchmarks() from public.family_daily (stage ''scaling'', and its _upstack_nc / _upstack_all twins) x ad_metrics first row x products. Refresh: nightly (etl.stage_moves, same transaction as creative_stage_moves): DELETE + INSERT. Coverage: 3 bases x up to 4 products (incl. ''none''); 30 days ending on the last day of family_daily. Caveats: All scaling spend of the product counts, including creatives that moved in a mass relaunch and creatives that never tested. | product_slug ''none'' = creatives of no active product (products.slug ''unassigned'' or no tracker row). Spend is Meta''s in every basis; purchases and revenue are Upstack''s in the upstack_* bases (not additive across bases).';
COMMENT ON COLUMN public.scaling_benchmarks.basis IS '''meta'' (Meta''s own reporting), ''upstack_nc'' (Upstack new customers) or ''upstack_all'' (Upstack all orders).';
COMMENT ON COLUMN public.scaling_benchmarks.product_slug IS 'products.slug of the creative''s product, or ''none''.';
COMMENT ON COLUMN public.scaling_benchmarks.day_from IS 'First day of the 30-day window (day_to - 29).';
COMMENT ON COLUMN public.scaling_benchmarks.day_to IS 'Last day of family_daily: the window ends there.';
COMMENT ON COLUMN public.scaling_benchmarks.creatives IS 'Creatives (tracker families) with scaling spend in the window.';
COMMENT ON COLUMN public.scaling_benchmarks.spend IS 'Scaling spend in the window, dollars.';
COMMENT ON COLUMN public.scaling_benchmarks.purchases IS 'Purchases in Scaling campaigns in the window (the basis''s meaning: Meta purchases, Upstack new customers, or Upstack orders).';
COMMENT ON COLUMN public.scaling_benchmarks.revenue IS 'Purchase value in the window, dollars (the basis''s meaning).';
