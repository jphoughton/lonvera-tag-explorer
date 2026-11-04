-- Projection module `pnl`: cost-driver defaults, engine settings, COGS model inputs, QuickBooks account map, vendor retainers. Additive and idempotent.
-- QuickBooks history itself lives in proj_actuals (line_key 'pnl.<line key>'); per-scenario changes live in proj_scenarios.params['pnl'] and
-- proj_series ('pnl.<line key>', kind override). Nothing here is scenario specific.

-- How each leaf line is projected: a list of segments ordered by "from" (null = from the start of the axis); the last segment whose "from"
-- is on or before the month applies. Modes: fixed {value}, carry, avg {window}, rate {base, rate_kind: window|ratio_avg|typed|cogs_model,
-- window?, rate?}, headcount. `source` names the sheet cell the default was read from.
CREATE TABLE IF NOT EXISTS proj_pnl_drivers (
  line_key   text PRIMARY KEY,
  segments   jsonb NOT NULL,
  note       text,
  source     text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Engine settings by name: 'engine' (rate_window_months, opex_inflation, cogs_inflation), 'headcount', 'cogs', 'ev'.
CREATE TABLE IF NOT EXISTS proj_pnl_settings (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  note       text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Landed unit cost by SKU and the sales mix that weights it (the COGS model: forward cost per bottle / price per bottle = COGS % of revenue).
CREATE TABLE IF NOT EXISTS proj_pnl_cogs_sku (
  sku             text PRIMARY KEY,
  label           text NOT NULL,
  china_unit_cost numeric(12,4) NOT NULL DEFAULT 0,
  us_unit_cost    numeric(12,4) NOT NULL DEFAULT 0,
  sales_mix       numeric(10,6) NOT NULL DEFAULT 0,
  in_stock_qty    numeric(18,2),
  sort            int NOT NULL DEFAULT 0,
  note            text,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- QuickBooks account name (lower-cased, whitespace collapsed, "Total for" rows never mapped) -> P&L line key. Several accounts may feed one line.
CREATE TABLE IF NOT EXISTS proj_pnl_qb_map (
  account    text PRIMARY KEY,
  line_key   text NOT NULL,
  note       text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Vendor retainers, current versus proposed, monthly. Reference only: not wired into the P&L.
CREATE TABLE IF NOT EXISTS proj_pnl_vendors (
  vendor           text PRIMARY KEY,
  current_monthly  numeric(18,2),
  proposed_monthly numeric(18,2),
  sort             int NOT NULL DEFAULT 0,
  note             text
);
