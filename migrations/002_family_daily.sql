-- Derived: ad_daily re-keyed to TRACKER family (via meta_family_map). Rebuilt in full by etl.build_metrics.
-- Powers any date window (last N days, custom from/to) without touching ad_daily at request time.
CREATE TABLE family_daily (
  family_key   text NOT NULL,            -- tracker family key (lower)
  day          date NOT NULL,
  spend        numeric(14,2) NOT NULL DEFAULT 0,
  purchases    numeric(12,2) NOT NULL DEFAULT 0,
  revenue      numeric(14,2) NOT NULL DEFAULT 0,
  link_clicks  numeric(12,0) NOT NULL DEFAULT 0,
  PRIMARY KEY (family_key, day)
);
CREATE INDEX family_daily_day ON family_daily (day);
