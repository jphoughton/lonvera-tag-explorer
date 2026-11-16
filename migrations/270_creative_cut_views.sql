-- 270: saved views of the Creative Pipeline By cut page: a name and the page state (cuts, dates, filters, metrics and their order, status columns, quick view, sort). Shared by the team. Additive and idempotent.
CREATE TABLE IF NOT EXISTS creative_cut_views (
  id         bigserial   PRIMARY KEY,
  name       text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  state      jsonb       NOT NULL,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS creative_cut_views_name_uq ON creative_cut_views (lower(name));
COMMENT ON TABLE public.creative_cut_views IS 'Saved views of the Creative Pipeline By cut page: a name and the page state (cuts, dates, filters, metrics and their order, status columns, quick view, sort), shared by the team. Grain: one row per saved view (the name is unique, any case). Source: the Creative Pipeline By cut view, via /api/priorities/cut-views (app/priorities_cut_views.py). Refresh: on demand (a person saves, replaces or deletes a view). Coverage: from the day migration 270 was deployed. Caveats: state is the page''s own JSON; the server keeps only the keys the page knows and caps it at 8 KB.';
COMMENT ON COLUMN public.creative_cut_views.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_cut_views.name IS 'The view''s name (1 to 80 characters; unique ignoring case; saving under an existing name replaces that view).';
COMMENT ON COLUMN public.creative_cut_views.state IS 'The page state as JSON: by, preset, from, to, f, metrics, order, stages, quick, sort, pipelineOnly, product, vendor, week.';
COMMENT ON COLUMN public.creative_cut_views.created_by IS 'Signed-in user (or key:<name>) who first saved the view.';
COMMENT ON COLUMN public.creative_cut_views.created_at IS 'When the view was first saved.';
COMMENT ON COLUMN public.creative_cut_views.updated_at IS 'When the view was last replaced.';
