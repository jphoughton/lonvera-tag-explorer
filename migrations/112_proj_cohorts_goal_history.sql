-- Projection / cohorts: every Goal curve save and restore is its own snapshot (same-day saves no longer overwrite each other); the active Goal is the newest snapshot.
-- Additive and idempotent: existing snapshots get ids in date order.

CREATE SEQUENCE IF NOT EXISTS proj_curve_goal_snapshot_seq;
ALTER TABLE proj_curve_goal ADD COLUMN IF NOT EXISTS snapshot_id bigint;
ALTER TABLE proj_curve_goal ADD COLUMN IF NOT EXISTS saved_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE proj_curve_goal ADD COLUMN IF NOT EXISTS saved_by text;
ALTER TABLE proj_curve_goal ADD COLUMN IF NOT EXISTS restored_from bigint;           -- snapshot_id this snapshot is a copy of (a restore)

-- ids in date order, assigned without calling nextval inside the join (a volatile function there can run once per row and split a snapshot)
WITH base AS (SELECT coalesce(max(snapshot_id), 0) AS b FROM proj_curve_goal),
     d AS (SELECT snapshot_date, row_number() OVER (ORDER BY snapshot_date) AS rn
           FROM (SELECT DISTINCT snapshot_date FROM proj_curve_goal WHERE snapshot_id IS NULL) x)
UPDATE proj_curve_goal g SET snapshot_id = d.rn + base.b
FROM d, base
WHERE g.snapshot_id IS NULL AND g.snapshot_date = d.snapshot_date;
SELECT setval('proj_curve_goal_snapshot_seq', greatest((SELECT coalesce(max(snapshot_id), 0) FROM proj_curve_goal), 1),
              (SELECT count(*) > 0 FROM proj_curve_goal));

ALTER TABLE proj_curve_goal ALTER COLUMN snapshot_id SET NOT NULL;
ALTER TABLE proj_curve_goal DROP CONSTRAINT IF EXISTS proj_curve_goal_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS proj_curve_goal_snap_age ON proj_curve_goal (snapshot_id, age);
CREATE INDEX IF NOT EXISTS proj_curve_goal_date ON proj_curve_goal (snapshot_date);
