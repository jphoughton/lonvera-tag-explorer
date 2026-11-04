-- Projection module inventory_supply: purchase orders, FBA transfers, vendor price list and lead times, per-SKU reorder parameters,
-- stock-on-hand snapshots, unit costs used to value stock. Scenario-independent facts (no scenario_id). Additive and idempotent.

CREATE SEQUENCE IF NOT EXISTS proj_po_id_seq START 1000;                 -- seeded rows keep their sheet row as id (< 1000); app-created rows start at 1000

CREATE TABLE IF NOT EXISTS proj_po (
  id           bigint PRIMARY KEY DEFAULT nextval('proj_po_id_seq'),
  sheet_row    int,                                                       -- row on the sheet's Purchase Orders tab when seeded
  po_number    text,
  product      text NOT NULL,                                             -- bull | liver | boneflex | belle | flow
  vendor       text NOT NULL,                                             -- key of proj_vendor_lead
  warehouse    text NOT NULL DEFAULT 'revamp' CHECK (warehouse IN ('revamp', 'intl')),
  units        numeric(18,2) NOT NULL CHECK (units > 0),
  order_date   date,                                                      -- null = land date minus the vendor lead time
  land_date    date NOT NULL,
  status       text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'ordered', 'in transit', 'landed', 'cancelled')),
  deposit_paid boolean,
  shipping_paid boolean,
  balance_paid boolean,
  deposit_pct  numeric(10,6) CHECK (deposit_pct IS NULL OR (deposit_pct >= 0 AND deposit_pct <= 1)),   -- null = the vendor price list default
  unit_cost    numeric(18,4) CHECK (unit_cost IS NULL OR unit_cost >= 0),  -- null = vendor price list
  link         text,
  note         text,
  source       text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto-plan')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (order_date IS NULL OR order_date <= land_date)
);
CREATE INDEX IF NOT EXISTS proj_po_land ON proj_po (land_date);

CREATE TABLE IF NOT EXISTS proj_fba_transfer (
  id           bigint PRIMARY KEY DEFAULT nextval('proj_po_id_seq'),
  sheet_row    int,
  transfer_no  text,
  product      text NOT NULL,
  units        numeric(18,2) NOT NULL CHECK (units > 0),
  ship_date    date NOT NULL,                                             -- leaves Revamp stock in this month
  receive_date date NOT NULL,                                             -- arrives in FBA stock in this month
  status       text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'shipped', 'received', 'cancelled')),
  note         text,
  source       text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto-plan')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proj_vendor_lead (
  vendor            text PRIMARY KEY,
  lead_weeks        numeric(6,2) NOT NULL CHECK (lead_weeks > 0),         -- order date = land date - lead weeks
  moq_units         numeric(18,2) NOT NULL DEFAULT 0 CHECK (moq_units >= 0),
  default_warehouse text NOT NULL DEFAULT 'revamp' CHECK (default_warehouse IN ('revamp', 'intl')),
  note              text,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proj_vendor_cogs (
  vendor      text NOT NULL,
  product     text NOT NULL,
  unit_cost   numeric(18,4) NOT NULL CHECK (unit_cost >= 0),
  deposit_pct numeric(10,6) CHECK (deposit_pct IS NULL OR (deposit_pct >= 0 AND deposit_pct <= 1)),   -- default for new POs
  note        text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vendor, product)
);

-- Unit cost used to value stock on hand (China landed cost per SKU).
CREATE TABLE IF NOT EXISTS proj_product_cost (
  product         text PRIMARY KEY,
  sku             text,
  china_unit_cost numeric(18,4) NOT NULL DEFAULT 0 CHECK (china_unit_cost >= 0),
  us_unit_cost    numeric(18,4),
  note            text,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Per location x product reorder parameters (lead time = purchase order placed to landed at that location).
CREATE TABLE IF NOT EXISTS proj_reorder_param (
  location    text NOT NULL CHECK (location IN ('revamp', 'intl')),
  product     text NOT NULL,
  lead_days   int NOT NULL CHECK (lead_days >= 0),
  safety_days int NOT NULL DEFAULT 14 CHECK (safety_days >= 0),
  moq_units   numeric(18,2) NOT NULL DEFAULT 0 CHECK (moq_units >= 0),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product)
);

-- Stock on hand per location x product, one row per count; the burn opens from the latest as_of per pair.
CREATE TABLE IF NOT EXISTS proj_stock_snapshot (
  location        text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),
  product         text NOT NULL,
  as_of           date NOT NULL,
  on_hand         numeric(18,2) NOT NULL,
  allocated       numeric(18,2) NOT NULL DEFAULT 0,                        -- revamp: allocated to open orders; intl: pending fulfillment
  inbound_per_3pl numeric(18,2) NOT NULL DEFAULT 0,
  estimate        boolean NOT NULL DEFAULT false,
  note            text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, as_of)
);
