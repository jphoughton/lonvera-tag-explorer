-- 259: the variations of a ticket as data. A ticket's face says how many VARIATIONS the concept has and the ticket shows each one (Original, Hook 2 ...) as a tab with its link; until now that list lived as text inside the story.
-- variations is a JSON list of {label, url, concept_id}: label (Original, Hook 2 ...), url (the video or image, usually Google Drive; may be empty), concept_id (the Creative Tracker Concept ID of that variation; may be empty).
-- Data pushed into the tool through the API must carry it (see docs/CREATIVE_PRIORITIES.md, Ticket contract). Additive and idempotent; the pages fall back to the story text and the Concept IDs when it is empty.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS variations jsonb NOT NULL DEFAULT '[]'::jsonb;

-- ---------------------------------------------------------------- comments
COMMENT ON COLUMN public.creative_priorities_assignments.variations IS 'The variations of the concept as a JSON list of {label, url, concept_id}: label (Original, Hook 2 ...), url (the video or image, usually Google Drive; may be empty) and concept_id (the Creative Tracker Concept ID of that variation; may be empty). Its length is the Variations number on the ticket.';
