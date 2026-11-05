-- Marketing metrics, All views: the business's actual new-customer revenue per day (Upstack order-level measures, Pacific-day buckets), counted once.
-- Used for Total NC ROAS = new-customer revenue / total spend (Meta + Google). Written by etl.upstack_orders (nightly, trailing days restated).
CREATE TABLE IF NOT EXISTS upstack_orders_daily (
  day         date PRIMARY KEY,
  nc_rev      numeric(18,2) NOT NULL DEFAULT 0,      -- orders.new_customer_total_revenue (first orders, order totals incl. tax, before refunds)
  rc_rev      numeric(18,2) NOT NULL DEFAULT 0,      -- orders.returning_customer_total_revenue
  nc_orders   numeric(18,2) NOT NULL DEFAULT 0,      -- orders.new_customers
  total_rev   numeric(18,2) NOT NULL DEFAULT 0,      -- orders.total_revenue
  orders      numeric(18,2) NOT NULL DEFAULT 0,      -- orders.count
  pulled_at   timestamptz NOT NULL DEFAULT now()
);
