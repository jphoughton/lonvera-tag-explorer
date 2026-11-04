-- Projection / inventory_demand: units shipped or sold per day by location and product, monthly history, international units by country.
-- Scenario-independent facts (loaded from the Warehance, ExpandFi and 3PL feeds); the levers (baseline window, lag, growth, seasonality) live in
-- proj_scenarios.params -> inventory_demand. Additive and idempotent. Aggregates only: no customer or order level data.

CREATE TABLE IF NOT EXISTS proj_units_daily (
  location    text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),     -- revamp = US 3PL (Warehance), amazon = FBA, intl = overseas 3PL
  product     text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),                -- bull, liver, boneflex, belle, flow
  day         date NOT NULL,
  units       numeric(18,2) NOT NULL CHECK (units >= 0),
  source      text NOT NULL,                                                      -- 'warehance' | 'expandfi' | 'tracking-xlsx' | 'estimate' | 'upload' ...
  is_estimate boolean NOT NULL DEFAULT false,                                     -- true = modeled from another feed, not the channel's own export
  loaded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, day)
);
CREATE INDEX IF NOT EXISTS proj_units_daily_day ON proj_units_daily (location, day);

-- Monthly history for months older than the daily window (Amazon units since Sep-2024).
CREATE TABLE IF NOT EXISTS proj_units_monthly (
  location  text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),
  product   text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),
  month     date NOT NULL CHECK (EXTRACT(day FROM month) = 1),
  units     numeric(18,2) NOT NULL CHECK (units >= 0),
  source    text NOT NULL,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, month)
);

-- International units by destination country and month (informational split of the overseas 3PL volume).
CREATE TABLE IF NOT EXISTS proj_units_country (
  month     date NOT NULL CHECK (EXTRACT(day FROM month) = 1),
  country   text NOT NULL,
  units     numeric(18,2) NOT NULL CHECK (units >= 0),
  days      int,                                                                  -- days covered when the month is partial
  source    text NOT NULL,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (month, country)
);

-- One row per load (seed, sheet refresh, upload): drives the data-freshness chips.
CREATE TABLE IF NOT EXISTS proj_units_loads (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  location   text NOT NULL,
  source     text NOT NULL,
  rows       int NOT NULL,
  first_day  date,
  last_day   date,
  actor      text,
  note       text
);
CREATE INDEX IF NOT EXISTS proj_units_loads_loc ON proj_units_loads (location, at DESC);

-- Stock at the US warehouse (Warehance on hand and allocated), refreshed daily; inventory_supply may read the latest as_of per product.
CREATE TABLE IF NOT EXISTS proj_demand_snapshot (
  location  text NOT NULL CHECK (location IN ('revamp', 'amazon', 'intl')),
  product   text NOT NULL CHECK (product ~ '^[a-z0-9_]{1,40}$'),
  as_of     date NOT NULL,
  on_hand   numeric(18,2) NOT NULL,
  allocated numeric(18,2) NOT NULL DEFAULT 0,
  source    text NOT NULL,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (location, product, as_of)
);
