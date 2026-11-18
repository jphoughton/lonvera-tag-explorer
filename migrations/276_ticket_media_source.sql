-- 276: where a ticket's Image / video tag came from. creative_priorities_assignments.media_type (migration 258) stays the one tag; these two columns say how it got there so an automatic
-- fill (the backfill and the hourly asset sync) never overwrites a choice a person made in the ticket panel. Additive and idempotent; no existing column is changed.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS media_type_source text CHECK (media_type_source IN ('auto', 'manual'));
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS media_type_basis text;

-- every Image / video value that exists today was chosen by a person (at ticket creation or in the panel): protect it
UPDATE creative_priorities_assignments SET media_type_source = 'manual' WHERE media_type IS NOT NULL AND media_type_source IS NULL;

COMMENT ON COLUMN public.creative_priorities_assignments.media_type_source IS 'Who set media_type: manual = a person (ticket creation or the ticket panel, including clearing it), auto = the backfill or the asset sync from evidence; NULL = never set. Automatic fills only touch rows whose source is not manual and whose media_type is empty.';
COMMENT ON COLUMN public.creative_priorities_assignments.media_type_basis IS 'The evidence behind an automatic media_type: format_line (note line Format: Video or Image), type_line (Type: VID or IMG), concept_ids (Video or Static in the Concept IDs), format_code (a creative format F1 to F6 is video), asset_files (mime types of the files copied from the ticket links), drive_link (mime types of the files behind a Drive link), video_host (a Vimeo, YouTube or Loom link). NULL for a manual choice.';
