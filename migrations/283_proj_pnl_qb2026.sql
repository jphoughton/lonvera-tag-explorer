-- Projection module `pnl`: a second chart of accounts ("qb2026", the numbered QuickBooks chart) beside the sheet chart. Additive and idempotent.
-- A scenario selects it with params.pnl.chart = 'qb2026'; every scenario without that setting keeps the sheet chart and reads exactly what it read before.
-- Chart-specific tables gain a `chart` column ('sheet' default, so every existing row is a sheet row) and the primary key includes it.
-- Loaded actuals of the new chart live in proj_actuals under 'qb26.<line key>' (the sheet chart reads only 'pnl.<line key>'); scenario overrides of the
-- new chart live in proj_series under 'pnl.qb26.<line key>' and its driver edits in params.pnl.qb26.drivers.
-- Seeded here: structure only (which account feeds which line, which base a rate reads). No dollar amounts and no rates: every number is derived from
-- loaded books at run time.

ALTER TABLE proj_pnl_drivers ADD COLUMN IF NOT EXISTS chart text NOT NULL DEFAULT 'sheet';
ALTER TABLE proj_pnl_drivers DROP CONSTRAINT IF EXISTS proj_pnl_drivers_pkey;
ALTER TABLE proj_pnl_drivers ADD PRIMARY KEY (chart, line_key);

ALTER TABLE proj_pnl_qb_map ADD COLUMN IF NOT EXISTS chart text NOT NULL DEFAULT 'sheet';
ALTER TABLE proj_pnl_qb_map DROP CONSTRAINT IF EXISTS proj_pnl_qb_map_pkey;
ALTER TABLE proj_pnl_qb_map ADD PRIMARY KEY (chart, account);

ALTER TABLE proj_upload_history ADD COLUMN IF NOT EXISTS chart text NOT NULL DEFAULT 'sheet';
CREATE INDEX IF NOT EXISTS proj_upload_history_chart ON proj_upload_history (chart, status, id DESC);

-- The analytics views keep their columns and gain `chart` at the end (CREATE OR REPLACE VIEW may only append columns). Only where the views exist
-- (databases that have migration 230).
DO $$
BEGIN
  IF to_regclass('analytics.projection_pnl_drivers') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW analytics.projection_pnl_drivers AS SELECT d.line_key, d.segments, d.note, d.source, d.updated_at, d.chart FROM public.proj_pnl_drivers d';
  END IF;
  IF to_regclass('analytics.projection_pnl_account_map') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW analytics.projection_pnl_account_map AS SELECT m.account, m.line_key, m.note, m.updated_at, m.chart FROM public.proj_pnl_qb_map m';
  END IF;
END $$;

COMMENT ON COLUMN public.proj_pnl_drivers.chart IS 'Chart of accounts the driver belongs to: sheet (the sheet''s lines, the default) or qb2026 (the numbered QuickBooks accounts).';
COMMENT ON COLUMN public.proj_pnl_qb_map.chart IS 'Chart of accounts the map belongs to: sheet or qb2026.';
COMMENT ON COLUMN public.proj_upload_history.chart IS 'Chart of accounts the upload loaded: sheet or qb2026.';
DO $$
BEGIN
  IF to_regclass('analytics.projection_pnl_drivers') IS NOT NULL THEN
    COMMENT ON COLUMN analytics.projection_pnl_drivers.chart IS 'Chart of accounts the driver belongs to: sheet (the sheet''s lines, the default) or qb2026 (the numbered QuickBooks accounts).';
  END IF;
  IF to_regclass('analytics.projection_pnl_account_map') IS NOT NULL THEN
    COMMENT ON COLUMN analytics.projection_pnl_account_map.chart IS 'Chart of accounts the map belongs to: sheet or qb2026.';
  END IF;
END $$;

-- QuickBooks account (lower-cased) -> line key, chart qb2026. Accounts are matched by full name, then by their four-digit code, then (children of
-- "6165 Software & Apps", whose bare names are ambiguous) by "<parent> > <child>" with the codes removed.
INSERT INTO proj_pnl_qb_map (chart, account, line_key, note) VALUES
  ('qb2026', '4010 amazon sales', 'amazon_sales', 'Account 4010'),
  ('qb2026', '4020 dtc sales', 'dtc_sales', 'Account 4020'),
  ('qb2026', '4030 tiktok sales', 'tiktok_sales', 'Account 4030'),
  ('qb2026', '4200 shipping income', 'shipping_income', 'Account 4200'),
  ('qb2026', '4400 refunds & discounts to customers', 'refunds_discounts', 'Account 4400'),
  ('qb2026', '5010 amazon cogs', 'cogs_amazon', 'Account 5010'),
  ('qb2026', '5020 shopify cogs', 'cogs_shopify', 'Account 5020'),
  ('qb2026', '5030 tiktok cogs', 'cogs_tiktok', 'Account 5030'),
  ('qb2026', '5310 3pl services', 'threepl_services', 'Account 5310'),
  ('qb2026', '5320 fba fees', 'fba_fees', 'Account 5320'),
  ('qb2026', '5410 adyen fees', 'adyen_fees', 'Account 5410'),
  ('qb2026', '5420 amazon fees', 'amazon_fees', 'Account 5420'),
  ('qb2026', '5430 paypal fees', 'paypal_fees', 'Account 5430'),
  ('qb2026', '5440 shopify fees', 'shopify_fees', 'Account 5440'),
  ('qb2026', '5450 tiktok fees', 'tiktok_fees', 'Account 5450'),
  ('qb2026', '6011 creative agency', 'creative_agency', 'Account 6011'),
  ('qb2026', '6012 email agency', 'email_agency', 'Account 6012'),
  ('qb2026', '6031 amazon ads', 'amazon_ads', 'Account 6031'),
  ('qb2026', '6032 facebook ads', 'facebook_ads', 'Account 6032'),
  ('qb2026', '6033 google ads', 'google_ads', 'Account 6033'),
  ('qb2026', '6035 tiktok ads', 'tiktok_ads', 'Account 6035'),
  ('qb2026', '6110 accounting', 'accounting', 'Account 6110'),
  ('qb2026', '6115 chargeback management', 'chargeback_management', 'Account 6115'),
  ('qb2026', '6120 consulting', 'consulting', 'Account 6120'),
  ('qb2026', '6125 customer service', 'customer_service', 'Account 6125'),
  ('qb2026', '6135 legal', 'legal', 'Account 6135'),
  ('qb2026', '6145 office supplies', 'office_supplies', 'Account 6145'),
  ('qb2026', '6166 marketing', 'software_marketing', 'Account 6166, child of 6165 Software & Apps'),
  ('qb2026', '6167 operations', 'software_operations', 'Account 6167, child of 6165 Software & Apps'),
  ('qb2026', 'software & apps > marketing', 'software_marketing', 'Child of 6165 Software & Apps when the export drops the account code'),
  ('qb2026', 'software & apps > operations', 'software_operations', 'Child of 6165 Software & Apps when the export drops the account code'),
  ('qb2026', '6170 bank fees', 'bank_fees', 'Account 6170'),
  ('qb2026', '7000 other income', 'other_income', 'Account 7000'),
  ('qb2026', '8000 interest expense', 'interest_expense', 'Account 8000')
ON CONFLICT (chart, account) DO NOTHING;

-- How each qb2026 line is projected (structure only). rate / window = the line divided by its base over the trailing loaded books months;
-- books_mean = the mean of the line over the same trailing books months. facebook_ads and google_ads have no row: they come from the spend plan.
INSERT INTO proj_pnl_drivers (chart, line_key, segments, note, source) VALUES
  ('qb2026', 'shipping_income', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales over the trailing books months', 'Account 4200'),
  ('qb2026', 'refunds_discounts', '[{"from": null, "mode": "rate", "base": "gross_sales", "rate_kind": "window"}]', 'Share of gross sales over the trailing books months (negative: reduces income)', 'Account 4400'),
  ('qb2026', 'cogs_amazon', '[{"from": null, "mode": "rate", "base": "amazon_sales", "rate_kind": "window"}]', 'Share of Amazon sales', 'Account 5010'),
  ('qb2026', 'cogs_shopify', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales', 'Account 5020'),
  ('qb2026', 'cogs_tiktok', '[{"from": null, "mode": "rate", "base": "tiktok_sales", "rate_kind": "window"}]', 'Share of TikTok sales', 'Account 5030'),
  ('qb2026', 'threepl_services', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales', 'Account 5310'),
  ('qb2026', 'fba_fees', '[{"from": null, "mode": "rate", "base": "amazon_sales", "rate_kind": "window"}]', 'Share of Amazon sales', 'Account 5320'),
  ('qb2026', 'adyen_fees', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales', 'Account 5410'),
  ('qb2026', 'amazon_fees', '[{"from": null, "mode": "rate", "base": "amazon_sales", "rate_kind": "window"}]', 'Share of Amazon sales', 'Account 5420'),
  ('qb2026', 'paypal_fees', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales', 'Account 5430'),
  ('qb2026', 'shopify_fees', '[{"from": null, "mode": "rate", "base": "dtc_sales", "rate_kind": "window"}]', 'Share of DTC sales', 'Account 5440'),
  ('qb2026', 'tiktok_fees', '[{"from": null, "mode": "rate", "base": "tiktok_sales", "rate_kind": "window"}]', 'Share of TikTok sales', 'Account 5450'),
  ('qb2026', 'amazon_ads', '[{"from": null, "mode": "rate", "base": "amazon_sales", "rate_kind": "window"}]', 'Share of Amazon sales', 'Account 6031'),
  ('qb2026', 'tiktok_ads', '[{"from": null, "mode": "rate", "base": "tiktok_sales", "rate_kind": "window"}]', 'Share of TikTok sales', 'Account 6035'),
  ('qb2026', 'creative_agency', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6011'),
  ('qb2026', 'email_agency', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6012'),
  ('qb2026', 'accounting', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6110'),
  ('qb2026', 'chargeback_management', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6115'),
  ('qb2026', 'consulting', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6120'),
  ('qb2026', 'customer_service', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6125'),
  ('qb2026', 'legal', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6135'),
  ('qb2026', 'office_supplies', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6145'),
  ('qb2026', 'software_marketing', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6166'),
  ('qb2026', 'software_operations', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6167'),
  ('qb2026', 'bank_fees', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 6170'),
  ('qb2026', 'other_income', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 7000'),
  ('qb2026', 'interest_expense', '[{"from": null, "mode": "books_mean"}]', 'Mean of the trailing books months', 'Account 8000')
ON CONFLICT (chart, line_key) DO NOTHING;
