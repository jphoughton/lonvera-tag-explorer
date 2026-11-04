-- Projection module `pnl`: every QuickBooks upload commit keeps what it replaced, so the latest one can be undone exactly. Additive and idempotent.
-- replaced = [{line_key, month, old, new, source}] cells that existed and were changed; created = [{line_key, month, value}] cells that did not exist.
CREATE TABLE IF NOT EXISTS proj_upload_history (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text,
  filename    text,
  sha256      text,
  bytes       int,
  file_rows   int,
  months      jsonb NOT NULL DEFAULT '[]',
  new_cells   int NOT NULL DEFAULT 0,
  changed_cells int NOT NULL DEFAULT 0,
  replaced    jsonb NOT NULL DEFAULT '[]',
  created     jsonb NOT NULL DEFAULT '[]',
  status      text NOT NULL DEFAULT 'applied' CHECK (status IN ('applied', 'undone')),
  undone_at   timestamptz,
  undone_by   text
);
CREATE INDEX IF NOT EXISTS proj_upload_history_status ON proj_upload_history (status, id DESC);
