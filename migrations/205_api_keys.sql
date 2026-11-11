-- 205: scoped API keys for the Tag Explorer API. A key is handed to a colleague who needs to read what the app serves and to assign items on the production board (/priorities/production), nothing else.
-- Only the sha256 hash of the key is stored (key_hash); the key itself is shown once when it is made (python -m etl.api_keys create) and can never be read back. prefix is the first 8 characters of the key, for display only.
-- scope 'read_assign' means: every GET under /api/ and /media/, plus POST /api/priorities/assignments and PATCH /api/priorities/assignments/<id>. The scope is enforced in app/auth.py and app/api_keys.py.
-- Written by etl/api_keys.py (create, revoke) and touched by app/api_keys.py (last_used_at, at most once a minute). Additive and idempotent.
CREATE TABLE IF NOT EXISTS api_keys (
  id            bigserial   PRIMARY KEY,
  name          text        NOT NULL UNIQUE,
  key_hash      text        NOT NULL UNIQUE,
  prefix        text        NOT NULL,
  scope         text        NOT NULL DEFAULT 'read_assign' CHECK (scope IN ('read_assign')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text,
  last_used_at  timestamptz,
  revoked_at    timestamptz
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.api_keys IS 'Scoped API keys for the Tag Explorer API: one row per key handed to a colleague. A key can read everything the app serves and can only assign items on the production board. Grain: one row per key (id is the primary key; name and key_hash are each unique). Source: python -m etl.api_keys create / revoke (etl/api_keys.py); last_used_at is touched by the web app (app/api_keys.py). Refresh: on demand (a person creates or revokes a key; last_used_at moves at most once a minute while a key is in use). Coverage: from the day the migration was deployed. Caveats: Only the sha256 hash of the key is stored. The key itself is shown once when it is made and cannot be recovered; make a new key and revoke the old one instead. | A revoked key is never deleted, so the history stays. Assignments made with a key carry created_by / updated_by ''key:<name>'' in creative_priorities_assignments.';
COMMENT ON COLUMN public.api_keys.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.api_keys.name IS 'Short name of the key, for example the colleague it was given to (unique). Shown in the audit columns as key:<name>.';
COMMENT ON COLUMN public.api_keys.key_hash IS 'sha256 hex digest of the full key (unique). The key is looked up by this hash.';
COMMENT ON COLUMN public.api_keys.prefix IS 'First 8 characters of the key (''lvk_'' plus 4), for display only.';
COMMENT ON COLUMN public.api_keys.scope IS 'What the key may do. ''read_assign'' (the only scope): every GET, plus POST /api/priorities/assignments and PATCH /api/priorities/assignments/<id>.';
COMMENT ON COLUMN public.api_keys.created_at IS 'When the key was made.';
COMMENT ON COLUMN public.api_keys.created_by IS 'Operating-system user who ran the create command; NULL when unknown.';
COMMENT ON COLUMN public.api_keys.last_used_at IS 'When the key last made a request (updated at most once a minute); NULL while unused.';
COMMENT ON COLUMN public.api_keys.revoked_at IS 'When the key was revoked; NULL while active. A revoked key is refused with 401.';
