-- 284: the "This week" whiteboard on the Creative priorities page. The team picks the concepts it wants made this week, sorts them into lanes (must, if time, parked) and drops the ones it decides against.
-- A drop is a status flip, never a delete; every change is also written to an event log. Additive and idempotent.
CREATE TABLE IF NOT EXISTS creative_whiteboard_picks (
  id            bigserial        PRIMARY KEY,
  week          date             NOT NULL CHECK (extract(isodow FROM week) = 1),
  concept_key   text             NOT NULL,
  product       text             NOT NULL DEFAULT '',
  cluster       text             NOT NULL DEFAULT '',
  icp           text             NOT NULL DEFAULT '',
  pain_point    text             NOT NULL DEFAULT '',
  line_code     text             NOT NULL DEFAULT '',
  title         text             NOT NULL DEFAULT '' CHECK (char_length(title) <= 200),
  lane          text             NOT NULL DEFAULT 'must' CHECK (lane IN ('must', 'if_time', 'parked')),
  status        text             NOT NULL DEFAULT 'picked' CHECK (status IN ('picked', 'dropped')),
  rank          double precision NOT NULL DEFAULT 0,
  note          text             NOT NULL DEFAULT '' CHECK (char_length(note) <= 500),
  rev           integer          NOT NULL DEFAULT 1,
  assignment_id bigint,
  created_by    text,
  updated_by    text,
  created_at    timestamptz      NOT NULL DEFAULT now(),
  updated_at    timestamptz      NOT NULL DEFAULT now(),
  UNIQUE (week, concept_key)
);
CREATE INDEX IF NOT EXISTS creative_whiteboard_picks_week_idx ON creative_whiteboard_picks (week, status, rank);

CREATE TABLE IF NOT EXISTS creative_whiteboard_events (
  id      bigserial   PRIMARY KEY,
  week    date        NOT NULL,
  pick_id bigint,
  at      timestamptz NOT NULL DEFAULT now(),
  actor   text,
  kind    text        NOT NULL CHECK (kind IN ('pick', 'drop', 'restore', 'move', 'note', 'remove')),
  batch   uuid,
  before  jsonb,
  after   jsonb
);
CREATE INDEX IF NOT EXISTS creative_whiteboard_events_week_idx ON creative_whiteboard_events (week, id);

COMMENT ON TABLE public.creative_whiteboard_picks IS 'The This week whiteboard on the Creative priorities page: the concepts the team picked to make in a week, their lane (must, if_time, parked), order and note, plus the ones it dropped. Grain: one row per concept x week (unique). Source: the whiteboard on /priorities, via /api/priorities/whiteboard (app/priorities_whiteboard.py). Refresh: on demand (a person picks, moves, notes or drops; the page saves the whole board at once). Coverage: from the day migration 284 was deployed. Caveats: A drop sets status = dropped and the row stays, so it can be restored. | The tag text is copied from the concept when it is first picked; assignment_id is a soft link with no foreign key.';
COMMENT ON COLUMN public.creative_whiteboard_picks.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_whiteboard_picks.week IS 'The Monday of the week the concept is picked for (a CHECK enforces a Monday).';
COMMENT ON COLUMN public.creative_whiteboard_picks.concept_key IS 'Stable concept handle: line:<taxonomy_lines.id>, manual:<id>, or text:<product>|<cluster>|<icp>|<pain point>.';
COMMENT ON COLUMN public.creative_whiteboard_picks.product IS 'Product slug copied when first picked; empty when unknown.';
COMMENT ON COLUMN public.creative_whiteboard_picks.cluster IS 'Market cluster of the concept, copied when first picked.';
COMMENT ON COLUMN public.creative_whiteboard_picks.icp IS 'ICP (ideal customer profile) of the concept, copied when first picked.';
COMMENT ON COLUMN public.creative_whiteboard_picks.pain_point IS 'Pain point of the concept, copied when first picked.';
COMMENT ON COLUMN public.creative_whiteboard_picks.line_code IS 'Communication Overview line code (for example BL12) at the time of the pick; empty when none.';
COMMENT ON COLUMN public.creative_whiteboard_picks.title IS 'Card title shown on the whiteboard (up to 200 characters).';
COMMENT ON COLUMN public.creative_whiteboard_picks.lane IS 'must, if_time or parked.';
COMMENT ON COLUMN public.creative_whiteboard_picks.status IS 'picked (on the board) or dropped (taken off; the row stays).';
COMMENT ON COLUMN public.creative_whiteboard_picks.rank IS 'Position across all picked cards of the week (1 is first).';
COMMENT ON COLUMN public.creative_whiteboard_picks.note IS 'Free note on the card, up to 500 characters.';
COMMENT ON COLUMN public.creative_whiteboard_picks.rev IS 'Counts the changes to this card; starts at 1.';
COMMENT ON COLUMN public.creative_whiteboard_picks.assignment_id IS 'creative_priorities_assignments.id when the concept was later given to a vendor; NULL until then.';
COMMENT ON COLUMN public.creative_whiteboard_picks.created_by IS 'Signed-in user who first picked the concept for the week.';
COMMENT ON COLUMN public.creative_whiteboard_picks.updated_by IS 'Signed-in user who last changed the card.';
COMMENT ON COLUMN public.creative_whiteboard_picks.created_at IS 'When the concept was first picked for the week.';
COMMENT ON COLUMN public.creative_whiteboard_picks.updated_at IS 'When the card last changed.';
COMMENT ON TABLE public.creative_whiteboard_events IS 'Change log of the This week whiteboard: one row for every card whose lane, order, status or note actually changed in a save. Grain: one row per card change. Source: /api/priorities/whiteboard (app/priorities_whiteboard.py). Refresh: on demand, with each save that changes something. Coverage: from the day migration 284 was deployed. Caveats: The largest id of a week is the version the page uses to detect changes (ETag). | A save that changes nothing writes no events.';
COMMENT ON COLUMN public.creative_whiteboard_events.id IS 'Primary key (bigserial); the largest id of a week is that week''s version.';
COMMENT ON COLUMN public.creative_whiteboard_events.week IS 'The Monday of the whiteboard week.';
COMMENT ON COLUMN public.creative_whiteboard_events.pick_id IS 'creative_whiteboard_picks.id of the card that changed. No foreign key.';
COMMENT ON COLUMN public.creative_whiteboard_events.at IS 'When the change was saved.';
COMMENT ON COLUMN public.creative_whiteboard_events.actor IS 'Signed-in user who saved it.';
COMMENT ON COLUMN public.creative_whiteboard_events.kind IS 'pick, drop, restore, move, note or remove (sent back to the backlog; the row is deleted).';
COMMENT ON COLUMN public.creative_whiteboard_events.batch IS 'Identifier shared by every event written by the same save.';
COMMENT ON COLUMN public.creative_whiteboard_events.before IS 'Card state before the change as JSON (lane, rank, status, note); NULL for a first pick.';
COMMENT ON COLUMN public.creative_whiteboard_events.after IS 'Card state after the change as JSON.';
