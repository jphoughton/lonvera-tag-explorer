-- 221: the brain endpoint while the app is open (AUTH_DISABLED). A key can now be made with no login: username is NULL, created_by = 'open-app', and creator_hash holds a salted hash of the
-- creator's IP (never the IP). Signed-in keys are unchanged (created_by = 'signed-in', username set). Additive and idempotent. The comments of brain_keys and brain_audit were rewritten here
-- (220's comment section is frozen history).
ALTER TABLE brain_keys ALTER COLUMN username DROP NOT NULL;
ALTER TABLE brain_keys ADD COLUMN IF NOT EXISTS created_by   text NOT NULL DEFAULT 'signed-in';
ALTER TABLE brain_keys ADD COLUMN IF NOT EXISTS creator_hash text;
DO $$ BEGIN
  ALTER TABLE brain_keys ADD CONSTRAINT brain_keys_origin CHECK ((created_by = 'signed-in' AND username IS NOT NULL) OR (created_by = 'open-app' AND username IS NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS brain_keys_open_day_idx ON brain_keys (created_at) WHERE created_by = 'open-app';

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.brain_keys IS 'Agent keys for the hosted brain endpoint (/mcp). With sign-in on: one row per user who made a key on the Connect an agent page (a reset replaces it). With the app open (AUTH_DISABLED): one row per key anyone made, with no user. A brain key opens /mcp and nothing else. Grain: one row per key (id is the primary key; key_hash is unique; username is unique and NULL for open-app keys). Source: the Connect an agent page (POST /api/brain/key in app/brain_mcp.py); last_used_at is touched by /mcp. Refresh: on demand (a person makes a key; last_used_at moves at most once a minute while the key is in use). Coverage: from the day migration 220 was deployed; open-app keys from migration 221. Caveats: Only the sha256 hash of the key is stored. The key is shown once and cannot be recovered; a signed-in user resets it to get a new one (the old one stops working at once). Open-app keys cannot be reset, only made again. | Separate from api_keys: a brain key never works on any /api/ route, and an api_keys key also works on /mcp. | Open-app keys (created_by = ''open-app'') are capped at 50 per day for the whole app and keep only a salted hash of the creator''s IP, never the IP.';
COMMENT ON COLUMN public.brain_keys.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.brain_keys.username IS 'App login of the owner (a name from APP_USERS), unique: one key per user. NULL for open-app keys.';
COMMENT ON COLUMN public.brain_keys.key_hash IS 'sha256 hex digest of the full key (unique); the key is looked up by this hash.';
COMMENT ON COLUMN public.brain_keys.prefix IS 'First 8 characters of the key (''lvb_'' plus 4), for display only.';
COMMENT ON COLUMN public.brain_keys.created_at IS 'When the key was made or last reset.';
COMMENT ON COLUMN public.brain_keys.last_used_at IS 'When the key last made a /mcp request (at most once a minute); NULL while unused.';
COMMENT ON COLUMN public.brain_keys.created_by IS 'How the key was made: ''signed-in'' (a logged-in user, username is set) or ''open-app'' (made while AUTH_DISABLED was on, username is NULL).';
COMMENT ON COLUMN public.brain_keys.creator_hash IS 'For open-app keys: HMAC-SHA256 of the creator''s IP address under the app''s secret key (first 32 hex characters); never the IP itself. NULL for signed-in keys.';
COMMENT ON TABLE public.brain_audit IS 'Audit log of the hosted brain endpoint: one row per tool call (who, which tool, how long, how many rows). Never holds the question, the SQL or the result. Grain: one row per tool call (id is the primary key). Source: app/brain_mcp.py, written after every tools/call on /mcp. Refresh: live (one row per call). Coverage: from the day migration 220 was deployed; no pruning. Caveats: actor is the login name for a signed-in session, ''brain:<username>'' for a brain key, ''brain:open-app:<key prefix>'' for a key made while the app was open, ''key:<name>'' for an API key. | row_count is NULL for tools that return no row list (describe, app_map).';
COMMENT ON COLUMN public.brain_audit.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.brain_audit.at IS 'When the call finished.';
COMMENT ON COLUMN public.brain_audit.actor IS 'Who called: a login name, ''brain:<username>'' (brain key), ''brain:open-app:<prefix>'' (open-app brain key) or ''key:<name>'' (API key).';
COMMENT ON COLUMN public.brain_audit.tool IS 'Tool name: catalog, describe, search, metric, freshness, sql, past_questions or app_map.';
COMMENT ON COLUMN public.brain_audit.duration_ms IS 'How long the tool ran, in milliseconds.';
COMMENT ON COLUMN public.brain_audit.row_count IS 'Rows (or hits) returned; NULL when the tool returns no list.';
COMMENT ON COLUMN public.brain_audit.ok IS 'True when the tool answered, false when it failed or the input was rejected.';
