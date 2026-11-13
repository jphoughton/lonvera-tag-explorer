-- 258: the two ticket fields of a Production card that were only implied before: the concept's own NAME (for example "Grandfather used to eat well") and whether it is NEW or an ITERATION of an earlier concept.
-- The card face now reads Concept name / Product / Variations / Market Cluster / New vs Iteration; the name used to be the first line of the story and the kind lived only in the Creative Tracker.
-- Both are optional (empty name, NULL kind) and editable in the ticket. Additive and idempotent. Read and written by app/priorities_assign.py; the pages keep working when this migration is not applied yet.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS concept_name text NOT NULL DEFAULT '';
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS concept_kind text CHECK (concept_kind IN ('new', 'iteration'));

-- ---------------------------------------------------------------- comments
COMMENT ON COLUMN public.creative_priorities_assignments.concept_name IS 'The concept''s own name as the team and the vendor call it (for example Grandfather used to eat well); empty until set. Shown as the title of the card.';
COMMENT ON COLUMN public.creative_priorities_assignments.concept_kind IS '''new'' for a new concept or ''iteration'' for a variation made from an earlier concept; NULL when not set. The Creative Tracker holds the same fact in its New Concept / Iteration column.';
