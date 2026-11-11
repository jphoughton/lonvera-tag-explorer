-- 220: the hosted "brain" endpoint (/mcp). brain_keys = one agent key per signed-in user (only the sha256 hash is stored; the key is shown once on the Connect an agent page and a reset replaces it).
-- brain_audit = one row per brain tool call (who, which tool, how long, how many rows; never the question, the SQL or the result). Additive and idempotent.
CREATE TABLE IF NOT EXISTS brain_keys (
  id            bigserial   PRIMARY KEY,
  username      text        NOT NULL UNIQUE,
  key_hash      text        NOT NULL UNIQUE,
  prefix        text        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz
);
CREATE TABLE IF NOT EXISTS brain_audit (
  id           bigserial   PRIMARY KEY,
  at           timestamptz NOT NULL DEFAULT now(),
  actor        text        NOT NULL,
  tool         text        NOT NULL,
  duration_ms  integer     NOT NULL,
  row_count    integer,
  ok           boolean     NOT NULL
);
CREATE INDEX IF NOT EXISTS brain_audit_at_idx ON brain_audit (at DESC);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.brain_keys IS 'Agent keys for the hosted brain endpoint (/mcp): one row per signed-in user who made a key on the Connect an agent page. A brain key opens /mcp and nothing else. Grain: one row per user (id is the primary key; username and key_hash are each unique). Source: the Connect an agent page (POST /api/brain/key in app/brain_mcp.py); last_used_at is touched by /mcp. Refresh: on demand (a user makes or resets a key; last_used_at moves at most once a minute while the key is in use). Coverage: from the day migration 220 was deployed. Caveats: Only the sha256 hash of the key is stored. The key is shown once and cannot be recovered; reset it to get a new one (the old one stops working at once). | Separate from api_keys: a brain key never works on any /api/ route, and an api_keys key also works on /mcp.';
COMMENT ON COLUMN public.brain_keys.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.brain_keys.username IS 'App login of the owner (a name from APP_USERS), unique: one key per user.';
COMMENT ON COLUMN public.brain_keys.key_hash IS 'sha256 hex digest of the full key (unique); the key is looked up by this hash.';
COMMENT ON COLUMN public.brain_keys.prefix IS 'First 8 characters of the key (''lvb_'' plus 4), for display only.';
COMMENT ON COLUMN public.brain_keys.created_at IS 'When the key was made or last reset.';
COMMENT ON COLUMN public.brain_keys.last_used_at IS 'When the key last made a /mcp request (at most once a minute); NULL while unused.';
COMMENT ON TABLE public.brain_audit IS 'Audit log of the hosted brain endpoint: one row per tool call (who, which tool, how long, how many rows). Never holds the question, the SQL or the result. Grain: one row per tool call (id is the primary key). Source: app/brain_mcp.py, written after every tools/call on /mcp. Refresh: live (one row per call). Coverage: from the day migration 220 was deployed; no pruning. Caveats: actor is the login name for a signed-in session, ''brain:<username>'' for a brain key, ''key:<name>'' for an API key. | row_count is NULL for tools that return no row list (describe, app_map).';
COMMENT ON COLUMN public.brain_audit.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.brain_audit.at IS 'When the call finished.';
COMMENT ON COLUMN public.brain_audit.actor IS 'Who called: a login name, ''brain:<username>'' (brain key) or ''key:<name>'' (API key).';
COMMENT ON COLUMN public.brain_audit.tool IS 'Tool name: catalog, describe, search, metric, freshness, sql, past_questions or app_map.';
COMMENT ON COLUMN public.brain_audit.duration_ms IS 'How long the tool ran, in milliseconds.';
COMMENT ON COLUMN public.brain_audit.row_count IS 'Rows (or hits) returned; NULL when the tool returns no list.';
COMMENT ON COLUMN public.brain_audit.ok IS 'True when the tool answered, false when it failed or the input was rejected.';
