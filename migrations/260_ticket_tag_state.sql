-- 260: a ticket needs only a name, a vendor and a brief; the system fills in the rest from the brief. tag_state says where that stands: 'complete' (every tag was given, or the system added them), 'pending' (a brief is there and tags are
-- still missing; etl/ticket_tags.py will fill them), 'failed' (the tagger gave up after three tries; a person can still fill the tags in the ticket). tagged_at is when the system added tags; tag_note holds its confidence and one-line reason.
-- The tagger only fills fields that are empty (product '', cluster '', icp '', pain_point '', concept_kind NULL, line_id NULL): anything a person or a partner gave is never overwritten. Additive and idempotent.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS tag_state text NOT NULL DEFAULT 'complete' CHECK (tag_state IN ('complete', 'pending', 'failed'));
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS tagged_at timestamptz;
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS tag_note text NOT NULL DEFAULT '';

-- ---------------------------------------------------------------- comments
COMMENT ON COLUMN public.creative_priorities_assignments.tag_state IS '''complete'' when every tag of the ticket was given or has been added by the system, ''pending'' while a brief is there and tags are still missing (etl/ticket_tags.py fills them), ''failed'' when the tagger gave up after three tries.';
COMMENT ON COLUMN public.creative_priorities_assignments.tagged_at IS 'When the system added tags from the brief (etl/ticket_tags.py); NULL when it did not.';
COMMENT ON COLUMN public.creative_priorities_assignments.tag_note IS 'The tagger''s confidence and one-line reason, or why it failed; empty when nothing was auto-tagged.';
