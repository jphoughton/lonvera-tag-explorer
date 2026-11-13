-- 244: the Production board. A stored status per assignment (the board column), a due date, a priority, a position inside the column, and two new tables: comments on an assignment and the weekly capacity per vendor.
-- Before this migration the stage was derived on every read; now creative_priorities_assignments.status is the truth the board shows, written by one helper (app/priorities_assign.py set_status) for every writer.
-- Additive and idempotent: the backfill only touches rows that have not been backfilled yet (status_changed_at IS NULL).
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned','brief_sent','in_production','in_review','ready','launched'));
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS due_date date;
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','high'));
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS position double precision NOT NULL DEFAULT 0;
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS status_changed_at timestamptz;

-- backfill, once: status from the links and timestamps the old stage was derived from; due date = the Friday of the week; position by the old table order
UPDATE creative_priorities_assignments SET
  status = CASE WHEN launched_at IS NOT NULL THEN 'launched' WHEN ready_at IS NOT NULL THEN 'ready' WHEN creative_url IS NOT NULL THEN 'in_review' WHEN brief_url IS NOT NULL THEN 'brief_sent' ELSE 'assigned' END,
  due_date = COALESCE(due_date, for_week + 4),
  status_changed_at = COALESCE(launched_at, ready_at, creative_at, brief_at, created_at)
WHERE status_changed_at IS NULL;
UPDATE creative_priorities_assignments a SET position = r.rn
FROM (SELECT id, row_number() OVER (ORDER BY for_week DESC, vendor, id)::double precision AS rn FROM creative_priorities_assignments) r
WHERE a.id = r.id AND a.position = 0;

CREATE TABLE IF NOT EXISTS creative_priorities_assignment_comments (
  id            bigserial   PRIMARY KEY,
  assignment_id bigint      NOT NULL REFERENCES creative_priorities_assignments (id) ON DELETE CASCADE,
  author_kind   text        NOT NULL CHECK (author_kind IN ('app','vendor')),
  author_name   text        NOT NULL,
  body          text        NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS creative_priorities_assignment_comments_idx ON creative_priorities_assignment_comments (assignment_id, created_at, id);

CREATE TABLE IF NOT EXISTS creative_priorities_vendor_capacity (
  vendor     text        PRIMARY KEY,
  per_week   integer     NOT NULL CHECK (per_week BETWEEN 0 AND 1000),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO creative_priorities_vendor_capacity (vendor, per_week) VALUES
  ('Madre', 10), ('Victoria', 10), ('TrybeDCShop', 10), ('TrybeOrion', 10), ('10xROAS', 10), ('Internal', 10), ('PuredMedia', 10), ('CreateMastery', 10), ('OpenProgram', 10)
ON CONFLICT (vendor) DO NOTHING;

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_priorities_assignments IS 'Production assignments from /priorities: a concept given to a vendor to make for a week (Assign to vendor), with the brief link, the creative link, the stage timestamps and the Creative Tracker Concept IDs of the ads that came out of it. The concept itself stays in the ideation list unchanged. Grain: one row per concept x vendor x week (open rows are unique; a dropped row can be assigned again). Source: the /priorities and /priorities/production pages and the vendor link page via /api/priorities/assignments (app/priorities_assign.py). Refresh: on demand (a person assigns, adds links, marks ready or launched, drops; a vendor adds a creative link and a note). Coverage: from the day the migration was deployed. Caveats: The board column is the stored status (migration 244); the older stage field is derived from it for compatibility. A linked Creative Tracker row can pull the status forward, never back. | Nothing here is written to the Google Sheet or Meta. The tag text is copied from the row at assign time, so history survives a taxonomy reload; line_id has no foreign key on purpose. | created_by and updated_by hold the signed-in user, or vendor:<name> for a write made on a vendor link page.';
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
COMMENT ON COLUMN public.creative_priorities_assignments.format_code IS 'Creative format chosen when assigning or later (creative_formats.code, foreign key); NULL when none was chosen. Set by a signed-in user only; a vendor sees it but cannot change it.';
COMMENT ON COLUMN public.creative_priorities_assignments.status IS 'The board column, stored: assigned, brief_sent, in_production, in_review, ready or launched (a dropped assignment keeps its last status and has dropped_at set). Written only through set_status in app/priorities_assign.py; a linked Creative Tracker row can pull it forward (7 = ready, 8 or 9 = launched), never back.';
COMMENT ON COLUMN public.creative_priorities_assignments.due_date IS 'When the vendor should be done: the Friday of for_week unless changed; NULL when cleared.';
COMMENT ON COLUMN public.creative_priorities_assignments.priority IS '''normal'' or ''high''; high cards sort first on the board.';
COMMENT ON COLUMN public.creative_priorities_assignments.position IS 'Order of the card inside its board column (ascending, fractional so a card can be dropped between two neighbours).';
COMMENT ON COLUMN public.creative_priorities_assignments.status_changed_at IS 'When status last changed (backfilled from the stage timestamps for rows older than migration 244).';
COMMENT ON TABLE public.creative_priorities_assignment_events IS 'The history of each production assignment on /priorities: who assigned the concept to a vendor, and who moved it between stages (brief sent, creative delivered, ready to launch, launched, dropped) and when. Grain: one row per change to an assignment. Source: app/priorities_assign.py, written in the same transaction as the change (create, edit, vendor link page); rows before migration 243 rebuilt from the stage timestamps of creative_priorities_assignments. Refresh: on every change made on /priorities/production or a vendor link page. Coverage: every assignment from creation; for assignments older than migration 243 only the first time each stage was reached, with no person except the creator. Caveats: actor is NULL for rebuilt history and when sign-in was off; a vendor link page writes vendor:<name>. | stage_before and stage_after are the stage the assignment itself shows (its links and Ready / Launched / Dropped buttons); a stage reached only because a linked Creative Tracker row moved on is not an event.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.id IS 'Row number (primary key).';
COMMENT ON COLUMN public.creative_priorities_assignment_events.assignment_id IS 'The assignment that changed (creative_priorities_assignments.id; rows go with the assignment).';
COMMENT ON COLUMN public.creative_priorities_assignment_events.at IS 'When the change was made.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.actor IS 'Who made it: the signed-in user name, vendor:<name> for a vendor link page, NULL when not known.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.kind IS 'What changed: assigned, brief_added, brief_removed, creative_added, creative_removed, ready, ready_cleared, launched, launched_cleared, dropped, restored, vendor, week or format (migration 243), plus status_changed, due_changed, priority_changed and comment_added (migration 244).';
COMMENT ON COLUMN public.creative_priorities_assignment_events.stage_before IS 'Stage or status the assignment showed before the change (assigned, brief_sent, creative_in, in_production, in_review, ready, launched, dropped); NULL on the first row. Events of kind status_changed use the status values.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.stage_after IS 'Stage it showed after the change.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.detail IS 'Short plain text about the change, for example ''Madre to Victoria''; empty when the kind says it all.';
COMMENT ON TABLE public.creative_priorities_assignment_comments IS 'Comments on a production assignment, the thread between the team and the vendor on the card (including ''Changes requested: ...'' when a creative is sent back). Grain: one row per comment. Source: app/priorities_assign.py, from the Production page (author_kind app) and the vendor link page (author_kind vendor). Refresh: on every comment. Coverage: from migration 244. Caveats: A vendor sees only the comments of their own assignments; the internal note (creative_priorities_assignments.note) is not a comment and is never shown to a vendor.';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.id IS 'Row number (primary key).';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.assignment_id IS 'The assignment commented on (creative_priorities_assignments.id; comments go with the assignment).';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.author_kind IS '''app'' for a signed-in team member (or an API key), ''vendor'' for a comment written on a vendor link page.';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.author_name IS 'Display name: the signed-in user (or Team when sign-in is off), or the vendor name.';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.body IS 'The comment text, 1 to 2,000 characters.';
COMMENT ON COLUMN public.creative_priorities_assignment_comments.created_at IS 'When the comment was posted.';
COMMENT ON TABLE public.creative_priorities_vendor_capacity IS 'How many concepts a vendor can take per week, the capacity the Production board compares each vendor''s open cards with. Grain: one row per vendor (vendor is the primary key). Source: seeded by migration 244 at 10 per vendor; edited on the Production page (PUT /api/priorities/vendor-capacity). Refresh: on demand. Coverage: the nine vendors. Caveats: A soft limit only: going over shows a warning and blocks nothing.';
COMMENT ON COLUMN public.creative_priorities_vendor_capacity.vendor IS 'Vendor, spelled as in creative_priorities_assignments.vendor (primary key).';
COMMENT ON COLUMN public.creative_priorities_vendor_capacity.per_week IS 'Concepts the vendor can take in one week (0 to 1,000).';
COMMENT ON COLUMN public.creative_priorities_vendor_capacity.updated_at IS 'When the capacity was last changed.';
