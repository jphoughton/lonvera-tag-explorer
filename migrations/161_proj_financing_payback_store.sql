-- Payback inputs have one store. The Spend plan page and the Cash & debt page edit the same three sheet cells (Payback D11, B12 and the 36-month
-- table); they live in params['drivers']['payback'] {product_cost_pct, fulfillment_per_order, horizon_months}. The copies migration 160 seeded under
-- params['financing']['payback'] are removed (a value somebody changed moves to the single store first), and the seeded average order value and
-- cost per new customer, typed copies of a live sheet figure, are dropped so the payback follows the last closed month. A value somebody typed
-- stays. Additive and idempotent. The reader keeps a fallback to the legacy financing keys for scenarios restored from an older version.

DO $$
DECLARE
  r record;
  fp jsonb;
  dp jsonb;
  old_dp jsonb;
  np jsonb;
BEGIN
  FOR r IN SELECT id, params FROM proj_scenarios WHERE jsonb_typeof(params #> '{financing,payback}') = 'object' LOOP
    fp := r.params #> '{financing,payback}';
    old_dp := CASE WHEN jsonb_typeof(r.params #> '{drivers,payback}') = 'object' THEN r.params #> '{drivers,payback}' ELSE '{}'::jsonb END;
    dp := old_dp;
    IF jsonb_typeof(fp -> 'product_cost_pct') = 'number' AND abs((fp ->> 'product_cost_pct')::numeric - 0.1726) > 1e-9 THEN
      dp := dp || jsonb_build_object('product_cost_pct', fp -> 'product_cost_pct');
    END IF;
    IF jsonb_typeof(fp -> 'fulfillment') = 'number' AND abs((fp ->> 'fulfillment')::numeric - 5.53) > 1e-9 THEN
      dp := dp || jsonb_build_object('fulfillment_per_order', fp -> 'fulfillment');
    END IF;
    IF jsonb_typeof(fp -> 'max_age') = 'number' AND (fp ->> 'max_age')::numeric <> 36 THEN
      dp := dp || jsonb_build_object('horizon_months', fp -> 'max_age');
    END IF;
    fp := fp - 'product_cost_pct' - 'fulfillment' - 'max_age';
    IF jsonb_typeof(fp -> 'aov') = 'number' AND abs((fp ->> 'aov')::numeric - 69.15600858369098) < 1e-9 THEN
      fp := fp - 'aov';
    END IF;
    IF jsonb_typeof(fp -> 'cac') = 'number' AND abs((fp ->> 'cac')::numeric - 105.4916738197425) < 1e-9 THEN
      fp := fp - 'cac';
    END IF;
    np := jsonb_set(r.params, '{financing,payback}', fp);
    IF dp <> old_dp THEN
      np := jsonb_set(np, '{drivers}', coalesce(np -> 'drivers', '{}'::jsonb), true);
      np := jsonb_set(np, '{drivers,payback}', dp, true);
    END IF;
    IF np <> r.params THEN
      UPDATE proj_scenarios SET params = np WHERE id = r.id;
    END IF;
  END LOOP;
END $$;
