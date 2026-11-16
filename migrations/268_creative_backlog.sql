-- 268: the Needs brief queue on the Creative Pipeline page. A cut (product / market cluster / ICP / pain point) is added to the backlog for a week; later a partner and a brief are assigned to it,
-- which creates the real board ticket (creative_priorities_assignments) and links it here. Additive and idempotent.
CREATE TABLE IF NOT EXISTS creative_backlog (
  id            bigserial   PRIMARY KEY,
  product       text        NOT NULL DEFAULT '' CHECK (product IN ('', 'vita-bull', 'beef-liver', 'boneflex', 'vita-belle')),
  cluster       text        NOT NULL DEFAULT '',
  icp           text        NOT NULL DEFAULT '',
  pain_point    text        NOT NULL DEFAULT '',
  media_type    text        CHECK (media_type IN ('image', 'video')),
  title         text        NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
  for_week      date        NOT NULL CHECK (extract(isodow FROM for_week) = 1),
  note          text        NOT NULL DEFAULT '' CHECK (char_length(note) <= 500),
  status        text        NOT NULL DEFAULT 'needs_brief' CHECK (status IN ('needs_brief', 'assigned')),
  assignment_id bigint,
  created_by    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS creative_backlog_open_uq ON creative_backlog (product, lower(cluster), lower(icp), lower(pain_point), COALESCE(media_type, ''), for_week) WHERE status = 'needs_brief';
CREATE INDEX IF NOT EXISTS creative_backlog_week ON creative_backlog (for_week, status);

COMMENT ON TABLE public.creative_backlog IS 'The Needs brief queue on the Creative Pipeline page: cuts (product, market cluster, ICP, pain point) the team decided to work on in a given week that still need a brief and a partner. Grain: one row per cut x week (open rows are unique). Source: the Creative Pipeline Needs brief and By cut views, via /api/priorities/backlog (app/priorities_backlog.py). Refresh: on demand (a person adds, edits, removes or assigns). Coverage: from the day migration 268 was deployed. Caveats: Assigning a partner and a brief creates a ticket in creative_priorities_assignments and sets status = assigned and assignment_id here; the row stays as history.';
COMMENT ON COLUMN public.creative_backlog.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_backlog.product IS 'Product slug (vita-bull, beef-liver, boneflex, vita-belle) or empty when the cut had no product.';
COMMENT ON COLUMN public.creative_backlog.cluster IS 'Market cluster of the cut; empty when the cut did not include one.';
COMMENT ON COLUMN public.creative_backlog.icp IS 'ICP (ideal customer profile) of the cut; empty when the cut did not include one.';
COMMENT ON COLUMN public.creative_backlog.pain_point IS 'Pain point of the cut; empty when the cut did not include one.';
COMMENT ON COLUMN public.creative_backlog.media_type IS 'image or video when chosen; NULL when not. Passed on to the ticket when assigned.';
COMMENT ON COLUMN public.creative_backlog.title IS 'Name the ticket gets: the cut labels joined, or what the person typed.';
COMMENT ON COLUMN public.creative_backlog.for_week IS 'The Monday of the week the team wants this made.';
COMMENT ON COLUMN public.creative_backlog.note IS 'Free note, up to 500 characters.';
COMMENT ON COLUMN public.creative_backlog.status IS 'needs_brief (waiting for a partner and a brief) or assigned (a ticket was created).';
COMMENT ON COLUMN public.creative_backlog.assignment_id IS 'creative_priorities_assignments.id of the ticket created from this item; NULL until assigned. No foreign key.';
COMMENT ON COLUMN public.creative_backlog.created_by IS 'Signed-in user who added the item.';
COMMENT ON COLUMN public.creative_backlog.created_at IS 'When the item was added.';
COMMENT ON COLUMN public.creative_backlog.updated_at IS 'When the item last changed.';
