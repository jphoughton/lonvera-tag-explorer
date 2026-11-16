-- 269: image or video on a production ticket (creative_priorities_assignments.media_type), chosen when the ticket is made or later, so the Creative Pipeline By cut view can break the pipeline out by it.
-- NULL = not chosen (the By cut view then treats a ticket that has a creative format, F1 to F6, as video). Additive and idempotent.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS media_type text CHECK (media_type IN ('image', 'video'));
COMMENT ON COLUMN public.creative_priorities_assignments.media_type IS 'image or video, chosen when the ticket is made or later; NULL when not chosen (the By cut view then treats a ticket with a creative format F1 to F6 as video).';
