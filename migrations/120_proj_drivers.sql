-- Projection / drivers: monthly Meta spend from the app's own ad_daily. Read by etl/projection_drivers.py (freshness and the daily refresh that
-- rolls complete months into proj_actuals as 'spend.meta_feed'). Everything else the drivers module stores lives in the core tables
-- (proj_series for the spend plan and overrides, proj_actuals for loaded actuals, proj_scenarios.params -> 'drivers' for levers).
-- Additive and idempotent. Aggregates only.

CREATE OR REPLACE VIEW proj_meta_spend_monthly AS
SELECT date_trunc('month', day)::date AS month,
       sum(spend)::numeric(18,2)      AS spend,
       count(DISTINCT day)::int       AS days,        -- days with any ad_daily row (a month is complete when days = days in the month)
       max(day)                       AS last_day
FROM ad_daily
GROUP BY 1;
