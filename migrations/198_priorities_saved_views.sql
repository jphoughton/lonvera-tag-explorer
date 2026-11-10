-- 198: saved views of the creative priorities sheet (/priorities -> select rows -> Save view -> link /priorities/v/<token>). One row per view: an unguessable token (the link IS the access;
-- the app has no password), an optional name, the snapshot week, the selected stable row keys, and `payload` = a frozen copy of the selected rows with the snapshot's baselines / legend /
-- competitor as-of date, so the view does not change when a later snapshot replaces that week. Additive and idempotent. Written by app/creative_priorities.py through its own read-write pool.
CREATE TABLE IF NOT EXISTS creative_priorities_views (
  id          bigserial   PRIMARY KEY,
  token       text        NOT NULL UNIQUE,
  name        text        NOT NULL DEFAULT '',
  week_start  date        NOT NULL,
  keys        text[]      NOT NULL,
  payload     jsonb       NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS creative_priorities_views_created_idx ON creative_priorities_views (created_at DESC);

-- comments (data dictionary)
COMMENT ON TABLE public.creative_priorities_views IS 'Saved views of the creative priorities sheet: the rows someone selected on /priorities, frozen with the snapshot''s baselines and legend, opened by an unguessable link /priorities/v/<token>. Grain: one row per saved view (token is unique). Source: app/creative_priorities.py (Save view on /priorities), copied from creative_priorities_snapshots. Refresh: written on demand by the app; never rebuilt. Coverage: from the day migration 198 was applied. Caveats: payload is a frozen copy: a view does not change when a later snapshot replaces its week. The link is the access (the app has no password).';
COMMENT ON COLUMN public.creative_priorities_views.id IS 'Surrogate key (primary key).';
COMMENT ON COLUMN public.creative_priorities_views.token IS 'Unguessable link token (unique).';
COMMENT ON COLUMN public.creative_priorities_views.name IS 'View name, '''' when unnamed; shown as the page title and used in the CSV file name.';
COMMENT ON COLUMN public.creative_priorities_views.week_start IS 'The snapshot week (Sunday, Pacific) the rows came from.';
COMMENT ON COLUMN public.creative_priorities_views.keys IS 'Stable row keys of the selected rows.';
COMMENT ON COLUMN public.creative_priorities_views.payload IS 'JSON object: the selected rows plus the snapshot''s baselines, legend and competitor as-of date.';
COMMENT ON COLUMN public.creative_priorities_views.created_at IS 'When the view was saved.';
