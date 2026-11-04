-- Projection / cohorts: the cohort table (one row per acquisition month), its cumulative $/customer by age, and the Goal retention curve.
-- Scenario-independent facts (loaded from exports); anything a scenario can change is a lever in proj_scenarios.params -> 'cohorts'.
-- Additive and idempotent. Aggregates only: no customer-level data.

CREATE TABLE IF NOT EXISTS proj_cohorts (
  cohort_month             date PRIMARY KEY CHECK (EXTRACT(day FROM cohort_month) = 1),
  new_customers            numeric(18,2) NOT NULL CHECK (new_customers >= 0),
  first_order_per_customer numeric(18,6),                 -- "First Order": average first-order $ per customer (cohort revenue = new_customers x this)
  first_order_realized     numeric(18,6),                 -- "First Order (Realized)": after refunds, kept for reference
  repeat_90d               numeric(10,6),                 -- 90-day repeat rate (fraction)
  cac                      numeric(18,6),                 -- cost to acquire a customer, as exported (per-customer rate: kept at full precision so a re-upload shows no false change)
  payback_months           numeric(12,6),
  nc_rev_reference         numeric(18,2),                 -- new-customer revenue typed on the sheet's Hist Waterfall tab; used only to tie the engine out to the sheet
  source                   text NOT NULL DEFAULT '',
  loaded_at                timestamptz NOT NULL DEFAULT now()
);

-- Cumulative $ per cohort customer at month `age` after acquisition (first order included). Absent row = not observed yet.
-- numeric(18,6): these are per-customer rates, not ledger money, and the curve differences them (two decimals would distort late ages).
CREATE TABLE IF NOT EXISTS proj_cohort_cum (
  cohort_month     date NOT NULL REFERENCES proj_cohorts (cohort_month) ON DELETE CASCADE,
  age              int  NOT NULL CHECK (age BETWEEN 1 AND 36),
  cum_per_customer numeric(18,6) NOT NULL,
  PRIMARY KEY (cohort_month, age)
);

-- Goal retention curve: repeat $ per $1 of first-order revenue by age (1..60). Snapshots are kept; the newest snapshot_date is the active one.
CREATE TABLE IF NOT EXISTS proj_curve_goal (
  snapshot_date date NOT NULL,
  age           int  NOT NULL CHECK (age BETWEEN 1 AND 60),
  value         numeric(14,10) NOT NULL,
  note          text,
  PRIMARY KEY (snapshot_date, age)
);
