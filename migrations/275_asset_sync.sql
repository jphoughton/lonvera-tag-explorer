-- 275: asset sync. Every media file linked from a Production ticket (creative_url, variations[].url) is copied once into the Drive folder "Lonvera - Assets" (etl/asset_sync.py, hourly in the `live` service).
-- Three bookkeeping tables, additive and idempotent. Nothing here is ever deleted by the job and the job never deletes, moves or renames anything in Drive.
CREATE TABLE IF NOT EXISTS asset_sync_links (
  assignment_id  bigint      NOT NULL REFERENCES creative_priorities_assignments (id) ON DELETE CASCADE,
  url            text        NOT NULL,
  link_type      text        NOT NULL,
  field          text        NOT NULL DEFAULT 'creative_url',
  status         text        NOT NULL DEFAULT 'new' CHECK (status IN ('new','done','failed','unresolvable','skipped')),
  files_found    integer     NOT NULL DEFAULT 0,
  files_copied   integer     NOT NULL DEFAULT 0,
  files_duplicate integer    NOT NULL DEFAULT 0,
  attempts       integer     NOT NULL DEFAULT 0,
  reason         text        NOT NULL DEFAULT '',
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_checked_at timestamptz,
  next_check_at  timestamptz DEFAULT now(),
  PRIMARY KEY (assignment_id, url)
);
CREATE INDEX IF NOT EXISTS asset_sync_links_due_idx ON asset_sync_links (next_check_at) WHERE next_check_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS asset_sync_files (
  id             bigserial   PRIMARY KEY,
  assignment_id  bigint      NOT NULL REFERENCES creative_priorities_assignments (id) ON DELETE CASCADE,
  url            text        NOT NULL,
  source_key     text        NOT NULL,
  source_name    text        NOT NULL DEFAULT '',
  mime           text        NOT NULL DEFAULT '',
  size_bytes     bigint,
  md5            text,
  status         text        NOT NULL CHECK (status IN ('synced','duplicate','failed','skipped')),
  dest_file_id   text,
  dest_path      text        NOT NULL DEFAULT '',
  reason         text        NOT NULL DEFAULT '',
  attempts       integer     NOT NULL DEFAULT 0,
  last_attempt_at timestamptz NOT NULL DEFAULT now(),
  synced_at      timestamptz,
  UNIQUE (assignment_id, source_key)
);
CREATE TABLE IF NOT EXISTS asset_sync_known_md5 (
  md5           text        PRIMARY KEY,
  drive_file_id text        NOT NULL DEFAULT '',
  name          text        NOT NULL DEFAULT '',
  seen_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.asset_sync_links IS 'Every link found on a Production ticket (creative_url or a variation url) that the asset sync looked at, with what came of it. Use it to see which ticket assets reached the Drive folder Lonvera - Assets and which links held no file. Grain: one row per ticket x link (assignment_id, url). Source: etl/asset_sync.py. Refresh: hourly (live service, non-fatal); folder links are re-checked every 6 hours while the ticket is recent, failed links each run, unresolvable links weekly. Coverage: from the first run (2026-11). Caveats: link_type comes from the URL: drive_file, drive_folder, google_doc, figma, frameio, loom, vimeo, youtube, dropbox, facebook_preview, facebook_post, direct, other. | status skipped (Google Docs, Figma) and unresolvable (private, expired or unsupported links) mean no media file could be taken; reason says why.';
COMMENT ON COLUMN public.asset_sync_links.assignment_id IS 'creative_priorities_assignments.id of the ticket.';
COMMENT ON COLUMN public.asset_sync_links.url IS 'The link as typed on the ticket.';
COMMENT ON COLUMN public.asset_sync_links.link_type IS 'Kind of link, from the URL.';
COMMENT ON COLUMN public.asset_sync_links.field IS 'Where on the ticket the link sits: creative_url or variation:<label>.';
COMMENT ON COLUMN public.asset_sync_links.status IS 'new | done | failed (retried) | unresolvable (re-tried weekly) | skipped (no file by nature, never retried).';
COMMENT ON COLUMN public.asset_sync_links.files_found IS 'Media files found behind the link on the last look.';
COMMENT ON COLUMN public.asset_sync_links.files_copied IS 'Files of this link copied into Lonvera - Assets (running total).';
COMMENT ON COLUMN public.asset_sync_links.files_duplicate IS 'Files not copied because the same md5 was already there (running total).';
COMMENT ON COLUMN public.asset_sync_links.attempts IS 'Looks that ended in an error since the last success.';
COMMENT ON COLUMN public.asset_sync_links.reason IS 'Plain-English reason for failed, unresolvable or skipped; empty otherwise.';
COMMENT ON COLUMN public.asset_sync_links.first_seen_at IS 'When the sync first saw the link.';
COMMENT ON COLUMN public.asset_sync_links.last_checked_at IS 'When the sync last looked at the link.';
COMMENT ON COLUMN public.asset_sync_links.next_check_at IS 'When it will look again; NULL = never.';
COMMENT ON TABLE public.asset_sync_files IS 'Every media file the asset sync took from a ticket link: copied into Lonvera - Assets, skipped as a duplicate by md5, skipped on purpose, or failed. Grain: one row per ticket x source file (assignment_id, source_key). Source: etl/asset_sync.py. Refresh: hourly (live service, non-fatal). Coverage: from the first run (2026-11). Caveats: synced = copied (dest_file_id, dest_path); duplicate = the md5 was already under Lonvera - Assets, nothing copied; skipped = not taken on purpose (over the 150 MB cap for non-Drive downloads, not an image or video); failed = error, retried next run.';
COMMENT ON COLUMN public.asset_sync_files.id IS 'Primary key.';
COMMENT ON COLUMN public.asset_sync_files.assignment_id IS 'Ticket id.';
COMMENT ON COLUMN public.asset_sync_files.url IS 'The ticket link the file came from.';
COMMENT ON COLUMN public.asset_sync_files.source_key IS 'Drive file id for Drive files, else the URL downloaded.';
COMMENT ON COLUMN public.asset_sync_files.source_name IS 'Original file name (kept in the destination).';
COMMENT ON COLUMN public.asset_sync_files.mime IS 'Mime type.';
COMMENT ON COLUMN public.asset_sync_files.size_bytes IS 'Size in bytes when known.';
COMMENT ON COLUMN public.asset_sync_files.md5 IS 'md5 of the content when known.';
COMMENT ON COLUMN public.asset_sync_files.status IS 'synced | duplicate | skipped | failed.';
COMMENT ON COLUMN public.asset_sync_files.dest_file_id IS 'Drive id of the copy, or of the existing file for a duplicate.';
COMMENT ON COLUMN public.asset_sync_files.dest_path IS 'Folder inside Lonvera - Assets, e.g. Beef Liver/Videos.';
COMMENT ON COLUMN public.asset_sync_files.reason IS 'Why a file was skipped or failed.';
COMMENT ON COLUMN public.asset_sync_files.attempts IS 'Attempts that ended in an error.';
COMMENT ON COLUMN public.asset_sync_files.last_attempt_at IS 'Last attempt.';
COMMENT ON COLUMN public.asset_sync_files.synced_at IS 'When the copy was made.';
COMMENT ON TABLE public.asset_sync_known_md5 IS 'Cache of the md5 of every file under the Drive folder Lonvera - Assets, used by the asset sync to skip duplicates. Grain: one row per md5. Source: etl/asset_sync.py (full re-list every 6 hours plus each file the job copies). Refresh: hourly when there is work. Coverage: the whole folder, about 28K files. Caveats: A cache: safe to truncate; it is rebuilt from Drive at the next run.';
COMMENT ON COLUMN public.asset_sync_known_md5.md5 IS 'md5 checksum (hex).';
COMMENT ON COLUMN public.asset_sync_known_md5.drive_file_id IS 'Drive id of one file with that md5.';
COMMENT ON COLUMN public.asset_sync_known_md5.name IS 'Its name.';
COMMENT ON COLUMN public.asset_sync_known_md5.seen_at IS 'When it was last seen in a listing.';
