-- 204: creative format of a production assignment on /priorities. When Josh assigns a concept to a vendor he can optionally pick a FORMAT (how the creative is made: animated story, talking head, acted scene, whiteboard,
-- sound opener, song). It belongs to the assignment (production side), not to the concept taxonomy. creative_formats is the small list of formats (edit it here to add one); creative_priorities_assignments.format_code is
-- the optional choice. Additive and idempotent. Read and written by app/priorities_assign.py; the pages keep working when this migration is not applied yet (no formats, nothing chosen).
CREATE TABLE IF NOT EXISTS creative_formats (
  code    text    PRIMARY KEY,
  name    text    NOT NULL,
  length  text,
  sort    integer NOT NULL DEFAULT 0,
  active  boolean NOT NULL DEFAULT true
);
INSERT INTO creative_formats (code, name, length, sort) VALUES
  ('F1', 'Long animated story, openly AI, told by the character', '90s to 3 min', 1),
  ('F2', 'Talking head (realistic AI)',                           '45 to 90s',    2),
  ('F3', 'Short acted scene with two people',                     '30 to 60s',    3),
  ('F4', 'Whiteboard explainer',                                  '60 to 90s',    4),
  ('F5', 'Opens on a sound',                                      '30 to 60s',    5),
  ('F6', 'Animated song',                                         '45 to 90s',    6)
ON CONFLICT (code) DO NOTHING;
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS format_code text REFERENCES creative_formats (code);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_formats IS 'The creative formats a production assignment can be made in (for example Whiteboard explainer): a short list that Josh picks from when assigning a concept to a vendor. Production side only; not part of the concept taxonomy. Grain: one row per format (code is the primary key). Source: seeded by migration 204; edited by hand in the database (no page edits it); read by /api/priorities/assignments (app/priorities_assign.py). Refresh: on demand (a format is added or retired by hand). Coverage: six formats, F1 to F6, from the day the migration was deployed. Caveats: A format with active = false stays on the assignments that already use it but can no longer be chosen.';
COMMENT ON COLUMN public.creative_formats.code IS 'Short code (primary key), F1 to F6.';
COMMENT ON COLUMN public.creative_formats.name IS 'What the format is, shown in the pick list.';
COMMENT ON COLUMN public.creative_formats.length IS 'Typical running time, as text (for example ''45 to 90s''); NULL when not set.';
COMMENT ON COLUMN public.creative_formats.sort IS 'Order in the pick list (ascending).';
COMMENT ON COLUMN public.creative_formats.active IS 'True while the format can be chosen on a new assignment.';
COMMENT ON COLUMN public.creative_priorities_assignments.format_code IS 'Creative format chosen when assigning or later (creative_formats.code, foreign key); NULL when none was chosen. Set by a signed-in user only; a vendor sees it but cannot change it.';
