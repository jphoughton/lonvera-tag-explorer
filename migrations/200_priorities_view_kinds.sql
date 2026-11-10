-- 200: saved priorities views become switchable views of the main /priorities page (/priorities?view=<token>). A view is either kind 'rows' (the selected row keys, plus a frozen copy of those rows) or kind
-- 'filters' (the product / section / search / sort state, applied LIVE to the current snapshot; the payload still holds a frozen copy of the rows that matched when it was saved, for the shareable
-- /priorities/v/<token> page). Existing views are kind 'rows'. Additive and idempotent. (199 belongs to the manual-concepts migration.)
ALTER TABLE creative_priorities_views ADD COLUMN IF NOT EXISTS kind       text        NOT NULL DEFAULT 'rows';
ALTER TABLE creative_priorities_views ADD COLUMN IF NOT EXISTS filters    jsonb;
ALTER TABLE creative_priorities_views ADD COLUMN IF NOT EXISTS updated_at timestamptz;

COMMENT ON COLUMN public.creative_priorities_views.kind IS '''rows'' (a fixed set of selected rows, shown with the latest numbers where the row still exists) or ''filters'' (a saved filter state applied live to the current snapshot).';
COMMENT ON COLUMN public.creative_priorities_views.filters IS 'JSON object for kind ''filters'': products (slugs), sections (keys), q (search text), key and dir (sort). NULL for kind ''rows''.';
COMMENT ON COLUMN public.creative_priorities_views.updated_at IS 'When the view was last replaced by Update this view; NULL if never.';
