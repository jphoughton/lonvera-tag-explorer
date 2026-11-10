-- 201: per-view "briefed" checkmarks on saved priorities views. One JSON object per view: row key -> {"at": ISO timestamp, "note": short text}. Absent key = not briefed. Everyone who opens the view
-- (the share link /priorities/v/<token> or /priorities?view=<token>) sees and can toggle the same state. Additive and idempotent.
ALTER TABLE creative_priorities_views ADD COLUMN IF NOT EXISTS briefed jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.creative_priorities_views.briefed IS 'JSON object: stable row key (product|section|cluster|icp|pain_point, or manual:<id>) -> {at, note} for the concepts marked briefed in this view; absent = not briefed.';
