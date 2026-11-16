-- 271: a story on every Needs brief item (creative_backlog.story): the one-sentence situation of the buyer the brief is built around, 15 words or fewer by convention (the Communication Overview stories).
-- It is passed on to the ticket's story when a partner is assigned. Optional. Additive and idempotent.
ALTER TABLE creative_backlog ADD COLUMN IF NOT EXISTS story text NOT NULL DEFAULT '' CHECK (char_length(story) <= 300);
COMMENT ON COLUMN public.creative_backlog.story IS 'The one-sentence situation of the buyer the brief is built around (15 words or fewer by convention); empty when none was given. Passed on to the ticket when a partner is assigned.';
