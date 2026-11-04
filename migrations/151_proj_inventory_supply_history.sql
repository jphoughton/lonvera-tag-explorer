-- inventory_supply history: one row per changed record of a plan-of-record write (orders, transfers, stock counts, price lists), with its before and after.
-- Each write bumps the default scenario's version once (scenario_version), so History lists it and the change can be reverted or the supply restored to a time.
-- Additive and idempotent.

CREATE TABLE IF NOT EXISTS proj_supply_history (
  id                  bigserial PRIMARY KEY,
  at                  timestamptz NOT NULL DEFAULT now(),
  scenario_slug       text NOT NULL,
  scenario_version    int NOT NULL,                        -- the version this change produced (one per change; many rows may share it)
  actor               text,
  action              text NOT NULL,                       -- 'po.create', 'plan.accept', 'revert v12', 'restore', ...
  kind                text NOT NULL CHECK (kind IN ('po', 'transfer', 'stock', 'vendor', 'vendor_cost', 'reorder_param', 'product_cost')),
  record_id           text NOT NULL,                       -- row id, or the key columns joined with '|'
  op                  text NOT NULL CHECK (op IN ('create', 'update', 'delete', 'put')),
  old                 jsonb,                               -- before (changed columns for an update, the whole row for a delete); null for a create
  new                 jsonb,                               -- after; null for a delete
  reverts_version     int,                                 -- set on the rows a revert / restore wrote: the version whose change they undo
  reverted_by_version int                                  -- set on a row once a later revert / restore undid it
);
CREATE INDEX IF NOT EXISTS proj_supply_history_version ON proj_supply_history (scenario_slug, scenario_version);
CREATE INDEX IF NOT EXISTS proj_supply_history_at ON proj_supply_history (at DESC);
