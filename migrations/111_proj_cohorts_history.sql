-- Projection / cohorts: one row per committed cohort upload, with the state of every cohort it touched before it ran, so the last upload can be undone exactly.
-- Additive and idempotent. Aggregates only (cohort rollups); no customer-level data.

CREATE TABLE IF NOT EXISTS proj_cohort_uploads (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text,
  filename    text,
  sha256      text,
  through     date,                       -- last month the upload was allowed to contribute
  summary     jsonb NOT NULL DEFAULT '{}',   -- counts shown in the preview (cohorts added / changed, cells added / changed, impact on the multiples)
  before      jsonb NOT NULL DEFAULT '[]',   -- [{month, existed, head:{...}, cum:{age: value}}] for every cohort the upload wrote, as it was before
  undone_at   timestamptz,
  undone_by   text
);
CREATE INDEX IF NOT EXISTS proj_cohort_uploads_open ON proj_cohort_uploads (id DESC) WHERE undone_at IS NULL;
