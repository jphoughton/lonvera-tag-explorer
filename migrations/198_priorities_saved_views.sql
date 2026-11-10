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
