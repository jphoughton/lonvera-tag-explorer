-- 010: per-ad hooks. A hook is the first spoken line plus the on-image headline / first on-screen text of an ad, boiled down to one short line
-- (about 18 words at most). A separate extraction cycle produces them; etl.load_hooks loads a cycle file for one product, matching its rows to
-- tracker_rows by (concept_id, ad family) + ordinal like etl.load_tags. One row per tracker row: a later load of the same row replaces it
-- (`version` says which cycle wrote the row now), rows a file does not cover keep what they have. The API reads it for the creatives table and
-- the ad page (app.queries.families / ad_detail); nothing else depends on it.
CREATE TABLE IF NOT EXISTS ad_hooks (
  tracker_row_id bigint PRIMARY KEY REFERENCES tracker_rows(id) ON DELETE CASCADE,
  hook           text NOT NULL,                     -- the one-line hook, whitespace collapsed (about 18 words at most)
  spoken         text,                              -- first spoken line (video audio / transcript)
  on_screen      text,                              -- on-image headline / first on-screen text
  hook_source    text,                              -- where the hook comes from: spoken | on_screen | both | copy | none
  confidence     text,                              -- high | medium | low | NULL
  note           text,                              -- extractor remark (e.g. 'no audio, text overlay only')
  version        text NOT NULL,                     -- 'hooks-v1-2026-11-05'
  extractor      text,                              -- who / what produced the row ('claude-sonnet-5-5', 'manual', ...)
  extracted_at   timestamptz NOT NULL DEFAULT now() -- when this row was written (a re-load stamps it again)
);
CREATE INDEX IF NOT EXISTS ad_hooks_version ON ad_hooks (version);
