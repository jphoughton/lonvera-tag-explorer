-- 199: manual concepts on /priorities (Add concept). Josh adds a concept by hand (product, market cluster, ICP, pain point, story, notes). NOT tied to a weekly snapshot: a row persists across
-- Sundays until it is marked done or deleted. new_* flag a tag that is not in the Communication Overview catalog (a typed, brand-new value). The app has no auth, so only timestamps are recorded.
-- Additive and idempotent. Written by app/priorities_manual.py through its own read-write pool.
CREATE TABLE IF NOT EXISTS creative_priorities_manual (
  id              bigserial   PRIMARY KEY,
  product         text        NOT NULL,
  section         text        NOT NULL,
  cluster         text        NOT NULL,
  icp             text        NOT NULL,
  pain_point      text        NOT NULL,
  story           text        NOT NULL DEFAULT '',
  notes           text        NOT NULL DEFAULT '',
  why_now         text        NOT NULL DEFAULT '',
  status          text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
  new_cluster     boolean     NOT NULL DEFAULT false,
  new_icp         boolean     NOT NULL DEFAULT false,
  new_pain_point  boolean     NOT NULL DEFAULT false,
  new_story       boolean     NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  done_at         timestamptz
);
CREATE INDEX IF NOT EXISTS creative_priorities_manual_status_idx ON creative_priorities_manual (status, created_at DESC);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_priorities_manual IS 'Concepts added by hand on /priorities (Add concept): product, market cluster, ICP, pain point, story, notes and a why-now line. Not tied to a weekly snapshot: a row stays on the sheet every week until it is marked done or deleted. Grain: one row per manual concept. Source: the /priorities page via POST /api/priorities/manual (app/priorities_manual.py). Refresh: on demand (a person adds, edits, marks done or deletes). Coverage: from the day the migration was deployed. Caveats: No author column: the app has no login of its own, only timestamps. | A cluster / ICP / pain point / story that is not in the Communication Overview catalog is stored as typed and flagged with the new_* columns (a proposal, not a line of ours).';
COMMENT ON COLUMN public.creative_priorities_manual.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_priorities_manual.product IS '''vita-bull'' | ''beef-liver'' | ''boneflex''.';
COMMENT ON COLUMN public.creative_priorities_manual.section IS 'Page section the concept sits in: ''iterate'' | ''faded'' | ''new'' | ''other''.';
COMMENT ON COLUMN public.creative_priorities_manual.cluster IS 'Market cluster (catalog value or typed).';
COMMENT ON COLUMN public.creative_priorities_manual.icp IS 'ICP (catalog value or typed).';
COMMENT ON COLUMN public.creative_priorities_manual.pain_point IS 'Pain point (catalog value or typed).';
COMMENT ON COLUMN public.creative_priorities_manual.story IS 'Story; empty when none.';
COMMENT ON COLUMN public.creative_priorities_manual.notes IS 'Free-text notes, up to 2,000 characters, line breaks kept.';
COMMENT ON COLUMN public.creative_priorities_manual.why_now IS 'Optional short why-now / what-to-test line, up to 300 characters.';
COMMENT ON COLUMN public.creative_priorities_manual.status IS '''open'' (shown) or ''done'' (hidden until Show done is ticked).';
COMMENT ON COLUMN public.creative_priorities_manual.new_cluster IS 'True when the cluster is not in the catalog for that product.';
COMMENT ON COLUMN public.creative_priorities_manual.new_icp IS 'True when the ICP is not in the catalog under that cluster.';
COMMENT ON COLUMN public.creative_priorities_manual.new_pain_point IS 'True when the pain point is not in the catalog under that ICP.';
COMMENT ON COLUMN public.creative_priorities_manual.new_story IS 'True when the story is typed text not in the catalog for that pain point.';
COMMENT ON COLUMN public.creative_priorities_manual.created_at IS 'When the concept was added.';
COMMENT ON COLUMN public.creative_priorities_manual.updated_at IS 'When it was last edited or its status changed.';
COMMENT ON COLUMN public.creative_priorities_manual.done_at IS 'When it was marked done; NULL while open.';
