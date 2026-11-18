-- 276: theme previews. Preview (unpublished) Shopify themes created through the app, and an audit row for every edit. Additive and idempotent. The app only writes preview themes it created itself.
CREATE TABLE IF NOT EXISTS theme_previews (
  id               bigserial   PRIMARY KEY,
  shopify_theme_id text        NOT NULL UNIQUE,
  name             text        NOT NULL,
  created_by       text        NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  source_theme_id  text
);
CREATE TABLE IF NOT EXISTS theme_edit_audit (
  id                bigserial   PRIMARY KEY,
  theme_preview_id  bigint      REFERENCES theme_previews (id),
  user_login        text        NOT NULL,
  action            text        NOT NULL,
  file_key          text,
  content_sha256    text,
  size_bytes        integer,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS theme_edit_audit_preview_idx ON theme_edit_audit (theme_preview_id, created_at);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.theme_previews IS 'Preview (unpublished) Shopify themes created through the Theme previews page, each a copy of the live theme at the time. Use it to see which themes the app may edit and who made them. Grain: one row per preview theme. Source: app/theme_api.py. Refresh: a row is added when someone creates a preview. Coverage: from the first preview. Caveats: the app refuses to write to any theme that is not in this table or that Shopify reports as the live theme.';
COMMENT ON COLUMN public.theme_previews.id IS 'Primary key.';
COMMENT ON COLUMN public.theme_previews.shopify_theme_id IS 'Shopify theme id (the number at the end of the theme gid).';
COMMENT ON COLUMN public.theme_previews.name IS 'Theme name in Shopify: preview-<login>-<yyyymmdd>-<slug>.';
COMMENT ON COLUMN public.theme_previews.created_by IS 'Login of the person who made the preview.';
COMMENT ON COLUMN public.theme_previews.created_at IS 'When the preview was made.';
COMMENT ON COLUMN public.theme_previews.source_theme_id IS 'Shopify id of the live theme that was copied.';
COMMENT ON TABLE public.theme_edit_audit IS 'Every create and file save done through the Theme previews page: who, which preview, which file, and a checksum of what was saved. Grain: one row per action. Source: app/theme_api.py. Refresh: a row is added for each action. Coverage: from the first preview. Caveats: the file text itself is not stored here, only its sha256 and size; the text lives in the preview theme in Shopify and is not in git.';
COMMENT ON COLUMN public.theme_edit_audit.id IS 'Primary key.';
COMMENT ON COLUMN public.theme_edit_audit.theme_preview_id IS 'theme_previews.id.';
COMMENT ON COLUMN public.theme_edit_audit.user_login IS 'Login of the person who acted.';
COMMENT ON COLUMN public.theme_edit_audit.action IS 'create_preview or save_file.';
COMMENT ON COLUMN public.theme_edit_audit.file_key IS 'Theme file path for save_file, such as sections/header.liquid; empty for create_preview.';
COMMENT ON COLUMN public.theme_edit_audit.content_sha256 IS 'sha256 of the saved text (hex).';
COMMENT ON COLUMN public.theme_edit_audit.size_bytes IS 'Size of the saved text in bytes.';
COMMENT ON COLUMN public.theme_edit_audit.created_at IS 'When the action happened.';
