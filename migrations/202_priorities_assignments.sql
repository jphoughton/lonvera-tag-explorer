-- 202: production assignments on /priorities. Josh ticks concepts on /priorities and assigns each to a vendor for a week (Assign to vendor). One row per concept x vendor x week; many rows per concept over time.
-- The concept itself stays in the ideation list unchanged: concept_key is the stable handle (line:<taxonomy_lines.id>, manual:<id>, or text:<product>|<cluster>|<icp>|<pain point>), and the tag text is copied onto the row so
-- history survives a taxonomy reload (line_id is deliberately not a foreign key). The stage (assigned, brief sent, creative delivered, ready, launched) is derived at read time from the links and timestamps plus the linked
-- Creative Tracker rows; nothing is written to the Google Sheet or Meta. creative_priorities_vendor_links holds one permanent unguessable link per vendor (/priorities/vendor/<token>). Additive and idempotent.
-- Written by app/priorities_assign.py through its own read-write pool.
CREATE TABLE IF NOT EXISTS creative_priorities_assignments (
  id                  bigserial   PRIMARY KEY,
  concept_key         text        NOT NULL,
  text_key            text        NOT NULL,
  product             text        NOT NULL,
  vendor              text        NOT NULL,
  line_id             integer,
  manual_id           bigint,
  line_code           text        NOT NULL DEFAULT '',
  cluster             text        NOT NULL DEFAULT '',
  icp                 text        NOT NULL DEFAULT '',
  pain_point          text        NOT NULL DEFAULT '',
  story               text        NOT NULL DEFAULT '',
  section_at_assign   text        NOT NULL DEFAULT '',
  note                text        NOT NULL DEFAULT '',
  vendor_note         text        NOT NULL DEFAULT '',
  for_week            date        NOT NULL CHECK (extract(isodow from for_week) = 1),
  brief_url           text,
  creative_url        text,
  brief_at            timestamptz,
  creative_at         timestamptz,
  ready_at            timestamptz,
  launched_at         timestamptz,
  dropped_at          timestamptz,
  tracker_concept_ids text[]      NOT NULL DEFAULT '{}',
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  created_by          text,
  updated_by          text
);
CREATE UNIQUE INDEX IF NOT EXISTS creative_priorities_assignments_open_uq ON creative_priorities_assignments (concept_key, vendor, for_week) WHERE dropped_at IS NULL;
CREATE INDEX IF NOT EXISTS creative_priorities_assignments_concept_idx ON creative_priorities_assignments (concept_key);
CREATE INDEX IF NOT EXISTS creative_priorities_assignments_week_idx ON creative_priorities_assignments (for_week);

CREATE TABLE IF NOT EXISTS creative_priorities_vendor_links (
  vendor      text        PRIMARY KEY,
  token       text        NOT NULL UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  rotated_at  timestamptz
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_priorities_assignments IS 'Production assignments from /priorities: a concept given to a vendor to make for a week (Assign to vendor), with the brief link, the creative link, the stage timestamps and the Creative Tracker Concept IDs of the ads that came out of it. The concept itself stays in the ideation list unchanged. Grain: one row per concept x vendor x week (open rows are unique; a dropped row can be assigned again). Source: the /priorities and /priorities/production pages and the vendor link page via /api/priorities/assignments (app/priorities_assign.py). Refresh: on demand (a person assigns, adds links, marks ready or launched, drops; a vendor adds a creative link and a note). Coverage: from the day the migration was deployed. Caveats: The stage is not stored: it is derived at read time from the links, the timestamps and the status of the linked Creative Tracker rows (the tracker wins when it is further along). | Nothing here is written to the Google Sheet or Meta. The tag text is copied from the row at assign time, so history survives a taxonomy reload; line_id has no foreign key on purpose. | created_by and updated_by hold the signed-in user, or vendor:<name> for a write made on a vendor link page.';
COMMENT ON COLUMN public.creative_priorities_assignments.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_priorities_assignments.concept_key IS 'Stable concept handle: ''line:<taxonomy_lines.id>'' when the row has a line, ''manual:<id>'' for a manual concept, else ''text:<product>|<cluster>|<icp>|<pain point>'' (lowercased, spaces collapsed).';
COMMENT ON COLUMN public.creative_priorities_assignments.text_key IS 'The text form of the key (''text:<product>|<cluster>|<icp>|<pain point>''), always stored, so a proposal that later becomes a real line still matches.';
COMMENT ON COLUMN public.creative_priorities_assignments.product IS '''vita-bull'' | ''beef-liver'' | ''boneflex''.';
COMMENT ON COLUMN public.creative_priorities_assignments.vendor IS 'Vendor, spelled as in the Creative Tracker Vendor column (Madre, Victoria, TrybeDCShop, TrybeOrion, 10xROAS, Internal, PuredMedia, CreateMastery, OpenProgram).';
COMMENT ON COLUMN public.creative_priorities_assignments.line_id IS 'taxonomy_lines.id at assign time; NULL for manual or proposed concepts. No foreign key.';
COMMENT ON COLUMN public.creative_priorities_assignments.manual_id IS 'creative_priorities_manual.id for a manual concept; NULL otherwise.';
COMMENT ON COLUMN public.creative_priorities_assignments.line_code IS 'Line code (for example BL12) at assign time; empty when none.';
COMMENT ON COLUMN public.creative_priorities_assignments.cluster IS 'Market cluster at assign time.';
COMMENT ON COLUMN public.creative_priorities_assignments.icp IS 'ICP at assign time.';
COMMENT ON COLUMN public.creative_priorities_assignments.pain_point IS 'Pain point at assign time.';
COMMENT ON COLUMN public.creative_priorities_assignments.story IS 'Story at assign time; empty when none.';
COMMENT ON COLUMN public.creative_priorities_assignments.section_at_assign IS 'Action the concept had when assigned: ''revive'' | ''make_more'' | ''test'' | ''add'' | ''stop'' | ''hold'' (rows written before 2026-11-10 hold the old section names ''faded'' | ''iterate'' | ''new'' | ''proposal'' | ''competitor'' | ''rest'' | ''other'', shown as the new Action).';
COMMENT ON COLUMN public.creative_priorities_assignments.note IS 'Note typed when assigning or changing the assignment.';
COMMENT ON COLUMN public.creative_priorities_assignments.vendor_note IS 'Note typed by the vendor on their own link page.';
COMMENT ON COLUMN public.creative_priorities_assignments.for_week IS 'The Monday of the week the vendor works on it (a CHECK enforces a Monday).';
COMMENT ON COLUMN public.creative_priorities_assignments.brief_url IS 'Link to the brief; NULL until added.';
COMMENT ON COLUMN public.creative_priorities_assignments.creative_url IS 'Link to the finished creative; NULL until added.';
COMMENT ON COLUMN public.creative_priorities_assignments.brief_at IS 'When the brief link was first added; NULL until then.';
COMMENT ON COLUMN public.creative_priorities_assignments.creative_at IS 'When the creative link was first added; NULL until then.';
COMMENT ON COLUMN public.creative_priorities_assignments.ready_at IS 'When someone clicked Ready to launch; NULL if not (or cleared).';
COMMENT ON COLUMN public.creative_priorities_assignments.launched_at IS 'When it was marked launched by hand; NULL if not. A linked tracker row with status 8 or 9 also counts as launched.';
COMMENT ON COLUMN public.creative_priorities_assignments.dropped_at IS 'When the assignment was dropped (soft cancel); NULL while active. Rows are never deleted.';
COMMENT ON COLUMN public.creative_priorities_assignments.tracker_concept_ids IS 'Creative Tracker Concept IDs of the ads made from this assignment, entered by hand.';
COMMENT ON COLUMN public.creative_priorities_assignments.created_at IS 'When the assignment was created.';
COMMENT ON COLUMN public.creative_priorities_assignments.updated_at IS 'When it was last changed.';
COMMENT ON COLUMN public.creative_priorities_assignments.created_by IS 'Who created the assignment: the signed-in user name; NULL when none.';
COMMENT ON COLUMN public.creative_priorities_assignments.updated_by IS 'Who last changed it: the signed-in user name, or vendor:<name> for a write made on a vendor link page; NULL when none.';
COMMENT ON TABLE public.creative_priorities_vendor_links IS 'One permanent unguessable link per vendor for the vendor page /priorities/vendor/<token>, where the vendor sees only their own concepts and adds a creative link and a note. Grain: one row per vendor (vendor is the primary key). Source: the Production page via /api/priorities/vendor-links (app/priorities_assign.py). Refresh: on demand (a link is created on first use and replaced by Reset link). Coverage: from the day the migration was deployed. Caveats: The link is the access: the page needs no login. Reset link replaces the token and the old link stops working.';
COMMENT ON COLUMN public.creative_priorities_vendor_links.vendor IS 'Vendor name (primary key), as in creative_priorities_assignments.vendor.';
COMMENT ON COLUMN public.creative_priorities_vendor_links.token IS 'Unguessable link token (unique).';
COMMENT ON COLUMN public.creative_priorities_vendor_links.created_at IS 'When the link was first created.';
COMMENT ON COLUMN public.creative_priorities_vendor_links.rotated_at IS 'When the token was last replaced by Reset link; NULL if never.';
