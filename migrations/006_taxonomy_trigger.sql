-- 006: a Communication Overview line is identified by cluster + ICP + pain point + emotional trigger.
-- Two Boneflex lines (BF36 Shame, BF38 Desire) share cluster + ICP + pain point and differ only by trigger, which UNIQUE (product_id, cluster, icp,
-- pain_point) from 001 could not hold. (product_id, line_code) stays unique. NULL trigger counts as '' so lines without a trigger stay unique too.
ALTER TABLE taxonomy_lines DROP CONSTRAINT IF EXISTS taxonomy_lines_product_id_cluster_icp_pain_point_key;
CREATE UNIQUE INDEX IF NOT EXISTS taxonomy_lines_product_cluster_icp_pain_trigger
  ON taxonomy_lines (product_id, cluster, icp, pain_point, COALESCE(emotional_trigger, ''));
