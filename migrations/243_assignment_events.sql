-- 243: history of each production assignment on /priorities: who assigned a concept to a vendor, and who moved it between stages (brief sent, creative delivered, ready to launch, launched, dropped) and when.
-- One row per change, written by app/priorities_assign.py in the same transaction as the change. The columns on creative_priorities_assignments keep only the first time each stage was reached and the last person who
-- touched the row, so this table is the only place that says who did which step. Rows before this migration are rebuilt from those timestamps (the person is not known, only for the first step). Additive and idempotent.
CREATE TABLE IF NOT EXISTS creative_priorities_assignment_events (
  id            bigserial   PRIMARY KEY,
  assignment_id bigint      NOT NULL REFERENCES creative_priorities_assignments (id) ON DELETE CASCADE,
  at            timestamptz NOT NULL DEFAULT now(),
  actor         text,
  kind          text        NOT NULL,
  stage_before  text,
  stage_after   text,
  detail        text        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS creative_priorities_assignment_events_idx ON creative_priorities_assignment_events (assignment_id, at, id);

-- rebuild the history of rows that existed before this migration, once (only for assignments with no events yet)
INSERT INTO creative_priorities_assignment_events (assignment_id, at, actor, kind, stage_before, stage_after, detail)
SELECT a.id, a.created_at, a.created_by, 'assigned', NULL, 'assigned', a.vendor || ', week of ' || to_char(a.for_week, 'YYYY-MM-DD')
FROM creative_priorities_assignments a
WHERE NOT EXISTS (SELECT 1 FROM creative_priorities_assignment_events e WHERE e.assignment_id = a.id);
INSERT INTO creative_priorities_assignment_events (assignment_id, at, actor, kind, stage_before, stage_after, detail)
SELECT a.id, s.at, NULL, s.kind, NULL, s.stage, ''
FROM creative_priorities_assignments a
CROSS JOIN LATERAL (VALUES (a.brief_at, 'brief_added', 'brief_sent'), (a.creative_at, 'creative_added', 'creative_in'), (a.ready_at, 'ready', 'ready'), (a.launched_at, 'launched', 'launched'), (a.dropped_at, 'dropped', 'dropped')) AS s(at, kind, stage)
WHERE s.at IS NOT NULL AND (SELECT count(*) FROM creative_priorities_assignment_events e WHERE e.assignment_id = a.id) = 1;

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_priorities_assignment_events IS 'The history of each production assignment on /priorities: who assigned the concept to a vendor, and who moved it between stages (brief sent, creative delivered, ready to launch, launched, dropped) and when. Grain: one row per change to an assignment. Source: app/priorities_assign.py, written in the same transaction as the change (create, edit, vendor link page); rows before migration 243 rebuilt from the stage timestamps of creative_priorities_assignments. Refresh: on every change made on /priorities/production or a vendor link page. Coverage: every assignment from creation; for assignments older than migration 243 only the first time each stage was reached, with no person except the creator. Caveats: actor is NULL for rebuilt history and when sign-in was off; a vendor link page writes vendor:<name>. | stage_before and stage_after are the stage the assignment itself shows (its links and Ready / Launched / Dropped buttons); a stage reached only because a linked Creative Tracker row moved on is not an event.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.id IS 'Row number (primary key).';
COMMENT ON COLUMN public.creative_priorities_assignment_events.assignment_id IS 'The assignment that changed (creative_priorities_assignments.id; rows go with the assignment).';
COMMENT ON COLUMN public.creative_priorities_assignment_events.at IS 'When the change was made.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.actor IS 'Who made it: the signed-in user name, vendor:<name> for a vendor link page, NULL when not known.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.kind IS 'What changed: assigned, brief_added, brief_removed, creative_added, creative_removed, ready, ready_cleared, launched, launched_cleared, dropped, restored, vendor, week or format.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.stage_before IS 'Stage the assignment showed before the change (assigned, brief_sent, creative_in, ready, launched, dropped); NULL on the first row.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.stage_after IS 'Stage it showed after the change.';
COMMENT ON COLUMN public.creative_priorities_assignment_events.detail IS 'Short plain text about the change, for example ''Madre to Victoria''; empty when the kind says it all.';
