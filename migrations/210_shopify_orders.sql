-- 210: order-level Shopify orders of the last 120 days (rolling), loaded nightly from the ExpandFi warehouse (merchant 1452, table shopify_orders, read-only) by
-- python -m etl.shopify_orders (feeds service, 21:00 UTC). shopify_orders = one row per order, shopify_order_lines = one row per line item.
-- No personal data: no email, name, address or phone; the customer is the Shopify customer id only; the ship-to is country and region only.
-- Rows older than 120 days are deleted each run (lines go with their order); a re-run overwrites the same orders. Both tables are in the nightly pg_dump (whole database).
-- Numbers: order_id is the Shopify order id (not the ExpandFi row id); order_date is the Pacific day of processed_at (created_at in the warehouse is sync time, not order time).
-- Additive and idempotent. Numbering skips 202-209 on purpose (202 and 203 live on an unmerged branch).
CREATE TABLE IF NOT EXISTS shopify_orders (
  order_id             bigint        PRIMARY KEY,
  order_number         text,
  processed_at         timestamptz   NOT NULL,
  order_date           date          NOT NULL,
  customer_id          text,
  is_new_customer      boolean       NOT NULL DEFAULT false,
  currency             text,
  subtotal_price       numeric(12,2),
  total_discounts      numeric(12,2),
  shipping_price       numeric(12,2),
  total_tax            numeric(12,2),
  total_price          numeric(12,2),
  current_total_price  numeric(12,2),
  refunded_amount      numeric(12,2),
  financial_status     text,
  fulfillment_status   text,
  source_name          text,
  app_id               text,
  tags                 text,
  discount_codes       text,
  ship_country         text,
  ship_region          text,
  cancelled_at         timestamptz,
  is_cancelled         boolean       NOT NULL DEFAULT false,
  is_test              boolean       NOT NULL DEFAULT false,
  is_rebill            boolean       NOT NULL DEFAULT false,
  is_trybe_gift        boolean       NOT NULL DEFAULT false,
  line_count           integer       NOT NULL DEFAULT 0,
  loaded_at            timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shopify_orders_processed_at_idx ON shopify_orders (processed_at);
CREATE INDEX IF NOT EXISTS shopify_orders_order_date_idx ON shopify_orders (order_date);
CREATE INDEX IF NOT EXISTS shopify_orders_customer_idx ON shopify_orders (customer_id);

CREATE TABLE IF NOT EXISTS shopify_order_lines (
  order_id       bigint        NOT NULL REFERENCES shopify_orders (order_id) ON DELETE CASCADE,
  line_no        integer       NOT NULL,
  sku            text,
  product_name   text,
  product        text          NOT NULL,
  quantity       integer       NOT NULL DEFAULT 0,
  price          numeric(12,2),
  line_discount  numeric(12,2),
  line_net       numeric(12,2),
  PRIMARY KEY (order_id, line_no)
);
CREATE INDEX IF NOT EXISTS shopify_order_lines_product_idx ON shopify_order_lines (product);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.shopify_orders IS 'Order-level Shopify orders of the last 120 days (rolling), one row per order, with money, status, source, discount codes and ship-to country / region. The place to answer order-level questions (refunds, rebills, gifts, discounts) that the daily roll-ups hide. Grain: one row per Shopify order (order_id is the primary key). Source: ExpandFi warehouse, merchant 1452, table shopify_orders (read-only), loaded by python -m etl.shopify_orders (etl/shopify_orders.py); fields come from the order''s raw Shopify JSON. Refresh: nightly (feeds service 21:00 UTC): the trailing 14 days are re-pulled every run, the whole 120 days every Sunday (Pacific) and on the first load; orders older than 120 days are deleted. Coverage: orders processed in the last 120 Pacific days, including today so far; the warehouse trails Shopify by minutes to hours. Caveats: No personal data is stored: no email, name, address or phone. customer_id is the Shopify customer id (NULL when the order has none); the ship-to is country and region only. | order_date is the Pacific day of processed_at. The warehouse''s own created_at is the sync time of a September backfill and is NOT used. | is_new_customer = this is the customer''s first Shopify order EVER (customer key = Shopify customer id, else the lower-cased email, as in the new-customer revenue feed), judged over the whole warehouse history, not only the 120 days. | refunded_amount = total_price - current_total_price (never below 0): Shopify''s current total already nets refunds and order edits, and the warehouse''s refund transactions are empty for many refunds. It is a net-down amount, not a refund ledger. | Orders restated after they fall out of the trailing 14 days are picked up by the Sunday full 120-day pull, so a late refund can lag up to a week. | is_rebill = source_name is subscription_contract_checkout_one. is_trybe_gift = the order carries the tag trybe (the $0 gift orders). Gift, test and cancelled orders are kept and flagged, never dropped; filter on the flags. | Revenue by product is NOT the sum of total_price by product: use shopify_order_lines.line_net for a split.';
COMMENT ON COLUMN public.shopify_orders.order_id IS 'Shopify order id (primary key). Not the ExpandFi row id.';
COMMENT ON COLUMN public.shopify_orders.order_number IS 'Human-readable order number as the store shows it.';
COMMENT ON COLUMN public.shopify_orders.processed_at IS 'When the order was processed (UTC instant).';
COMMENT ON COLUMN public.shopify_orders.order_date IS 'Pacific calendar day of processed_at; the day to group by.';
COMMENT ON COLUMN public.shopify_orders.customer_id IS 'Shopify customer id; NULL when the order has none. No other customer data is stored.';
COMMENT ON COLUMN public.shopify_orders.is_new_customer IS 'True when this is the customer''s first Shopify order ever.';
COMMENT ON COLUMN public.shopify_orders.currency IS 'Order currency code (shop currency).';
COMMENT ON COLUMN public.shopify_orders.subtotal_price IS 'Order subtotal after discounts, before shipping and tax.';
COMMENT ON COLUMN public.shopify_orders.total_discounts IS 'Sum of all discounts on the order.';
COMMENT ON COLUMN public.shopify_orders.shipping_price IS 'Shipping charged (shop money).';
COMMENT ON COLUMN public.shopify_orders.total_tax IS 'Tax charged.';
COMMENT ON COLUMN public.shopify_orders.total_price IS 'Order total at checkout: subtotal + shipping + tax.';
COMMENT ON COLUMN public.shopify_orders.current_total_price IS 'Order total after refunds and edits, as Shopify reports it now.';
COMMENT ON COLUMN public.shopify_orders.refunded_amount IS 'total_price minus current_total_price, never below 0 (see the caveat).';
COMMENT ON COLUMN public.shopify_orders.financial_status IS 'Shopify financial status (paid, refunded, partially_refunded, voided, ...).';
COMMENT ON COLUMN public.shopify_orders.fulfillment_status IS 'Shopify fulfillment status; NULL = unfulfilled.';
COMMENT ON COLUMN public.shopify_orders.source_name IS 'Shopify source name (web, subscription_contract_checkout_one for rebills, tiktok, or an app id).';
COMMENT ON COLUMN public.shopify_orders.app_id IS 'Shopify app id that created the order; NULL when none.';
COMMENT ON COLUMN public.shopify_orders.tags IS 'Order tags, comma separated, as Shopify stores them.';
COMMENT ON COLUMN public.shopify_orders.discount_codes IS 'Discount codes used, comma separated; NULL when none.';
COMMENT ON COLUMN public.shopify_orders.ship_country IS 'Ship-to ISO country code; NULL when the order has no shipping address.';
COMMENT ON COLUMN public.shopify_orders.ship_region IS 'Ship-to province / state code; NULL when none.';
COMMENT ON COLUMN public.shopify_orders.cancelled_at IS 'When the order was cancelled; NULL when it was not.';
COMMENT ON COLUMN public.shopify_orders.is_cancelled IS 'True when cancelled_at is set.';
COMMENT ON COLUMN public.shopify_orders.is_test IS 'True for a Shopify test order.';
COMMENT ON COLUMN public.shopify_orders.is_rebill IS 'True for a subscription rebill (source_name subscription_contract_checkout_one).';
COMMENT ON COLUMN public.shopify_orders.is_trybe_gift IS 'True when the order has the tag trybe (free sample / gift orders).';
COMMENT ON COLUMN public.shopify_orders.line_count IS 'Number of line items stored in shopify_order_lines for this order.';
COMMENT ON COLUMN public.shopify_orders.loaded_at IS 'When this row was last written by the loader.';
COMMENT ON TABLE public.shopify_order_lines IS 'Line items of shopify_orders: one row per product line with SKU, product bucket, quantity, price and discount. Grain: one row per order line (order_id, line_no is the primary key; line_no counts from 1 in the order''s own line order). Source: the line_items array of the same ExpandFi shopify_orders rows, loaded together with shopify_orders by python -m etl.shopify_orders. Refresh: with shopify_orders (nightly 21:00 UTC); a re-pulled order has its lines replaced, an order that leaves the 120-day window takes its lines with it. Coverage: the same orders as shopify_orders. Caveats: product is mapped from the SKU prefix: LV-VBULL = VitaBull, LV-BLC180 and DPCD1-GN180001 = BeefLiver, DPCD1-GN187001 = Boneflex, LV-VBELLE = VitaBelle, DPCD1-GN179001 = VitaFlow, anything else = Other. | line_net = max(price x quantity - line_discount, 0); the order''s total_price also carries shipping and tax and order-level discounts, so the lines do not add up to it.';
COMMENT ON COLUMN public.shopify_order_lines.order_id IS 'Shopify order id; foreign key to shopify_orders.order_id (cascade delete).';
COMMENT ON COLUMN public.shopify_order_lines.line_no IS 'Position of the line in the order, from 1.';
COMMENT ON COLUMN public.shopify_order_lines.sku IS 'SKU as sold; NULL when the line has none.';
COMMENT ON COLUMN public.shopify_order_lines.product_name IS 'Line title as it shows on the order.';
COMMENT ON COLUMN public.shopify_order_lines.product IS 'Product bucket mapped from the SKU (VitaBull, BeefLiver, Boneflex, VitaBelle, VitaFlow, Other).';
COMMENT ON COLUMN public.shopify_order_lines.quantity IS 'Units on the line.';
COMMENT ON COLUMN public.shopify_order_lines.price IS 'Unit price before discount.';
COMMENT ON COLUMN public.shopify_order_lines.line_discount IS 'Total discount on the line (all units).';
COMMENT ON COLUMN public.shopify_order_lines.line_net IS 'price x quantity minus line_discount, never below 0.';
