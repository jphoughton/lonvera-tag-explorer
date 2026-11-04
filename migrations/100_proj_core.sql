-- Projection core: scenarios, monthly series, loaded actuals, audit log, scenario history. Additive and idempotent.

CREATE TABLE IF NOT EXISTS proj_scenarios (
  id          serial PRIMARY KEY,
  slug        text UNIQUE NOT NULL,
  name        text NOT NULL,
  base_slug   text,                                        -- fallback scenario for series cells this one does not hold
  is_default  boolean NOT NULL DEFAULT false,
  params      jsonb NOT NULL DEFAULT '{}',                 -- params[<module>] = that module's levers
  version     int NOT NULL DEFAULT 1,                      -- +1 on every change to the scenario (name, params, series)
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS proj_scenarios_one_default ON proj_scenarios (is_default) WHERE is_default;

-- Typed monthly cells that a scenario can change: 'input' replaces a typed constant, 'override' replaces a computed value for one month.
CREATE TABLE IF NOT EXISTS proj_series (
  scenario_id int NOT NULL REFERENCES proj_scenarios (id) ON DELETE CASCADE,
  line_key    text NOT NULL,
  month       date NOT NULL CHECK (EXTRACT(day FROM month) = 1),
  value       numeric(18,4) NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('input', 'override')),
  note        text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scenario_id, line_key, month)
);

-- Scenario-independent facts loaded from exports and feeds (QuickBooks lines, cohort rollups, ...). Aggregates only.
CREATE TABLE IF NOT EXISTS proj_actuals (
  line_key  text NOT NULL,
  month     date NOT NULL CHECK (EXTRACT(day FROM month) = 1),
  value     numeric(18,4) NOT NULL,
  source    text,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (line_key, month)
);

CREATE TABLE IF NOT EXISTS proj_audit (
  id             bigserial PRIMARY KEY,
  at             timestamptz NOT NULL DEFAULT now(),
  actor          text,
  scenario_slug  text,                                     -- text, not a key: the trail outlives a deleted scenario
  action         text NOT NULL,
  line_key       text,
  month          date,
  old            jsonb,
  new            jsonb,
  note           text,
  version        int                                       -- scenario version this change produced (what restore takes); null when not versioned
);
CREATE INDEX IF NOT EXISTS proj_audit_scn_at ON proj_audit (scenario_slug, at DESC);
CREATE INDEX IF NOT EXISTS proj_audit_at ON proj_audit (at DESC);

-- One snapshot (params + every series cell of the scenario) per version, newest 200 kept per scenario; POST .../restore reads these.
CREATE TABLE IF NOT EXISTS proj_scenario_history (
  scenario_id int NOT NULL REFERENCES proj_scenarios (id) ON DELETE CASCADE,
  version     int NOT NULL,
  snapshot    jsonb NOT NULL,
  at          timestamptz NOT NULL DEFAULT now(),
  action      text NOT NULL,
  PRIMARY KEY (scenario_id, version)
);

INSERT INTO proj_scenarios (slug, name, is_default, params)
VALUES ('current', 'Current plan', true, '{}')
ON CONFLICT (slug) DO NOTHING;
