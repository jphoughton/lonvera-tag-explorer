-- Scenario lifecycle: a clone is a frozen copy (frozen = it no longer reads its base), deleting archives for 30 days instead of dropping rows.
-- Additive and idempotent. Existing clones stay `frozen = false` (they keep reading their base) until someone presses Detach.

ALTER TABLE proj_scenarios ADD COLUMN IF NOT EXISTS frozen boolean NOT NULL DEFAULT false;
ALTER TABLE proj_scenarios ADD COLUMN IF NOT EXISTS archived_at timestamptz;
CREATE INDEX IF NOT EXISTS proj_scenarios_archived ON proj_scenarios (archived_at) WHERE archived_at IS NOT NULL;
