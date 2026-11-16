-- 273: new customers and their revenue by ship-to country and Pacific day (feed nc_by_country_daily, ExpandFi merchant 1452 shopify_orders; etl/feeds/expandfi_feeds.py).
-- Feeds the Countries view of Marketing metrics (Meta spend by country is already in meta_breakdown_daily). Additive and idempotent; aggregates only (no customer or order ids).
-- Replaced by trailing window (delete the window, insert the pull), so a rerun leaves the same rows; an empty pull writes nothing. The analytics view and the dictionary comments are in migration 274.
CREATE TABLE IF NOT EXISTS nc_by_country_daily (
  day                  date          NOT NULL,                       -- Pacific day of the first order's processed_at
  country_code         text          NOT NULL,                       -- ISO 3166 alpha-2 of the ship-to address, else the billing address, else 'unknown'
  new_customers        integer       NOT NULL DEFAULT 0,             -- customers whose first Shopify order ever fell on that day
  new_customer_revenue numeric(14,2) NOT NULL DEFAULT 0,             -- total_price of those first orders (US dollars, tax included, before refunds)
  loaded_at            timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (day, country_code)
);
