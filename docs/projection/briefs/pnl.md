# Module brief: pnl (P1) — migration 130, `app/projection/pnl/`, page `pnl.js`

Replaces `P&L` rows 24–102 (costs → NOI), 134–175 (TTM/FTM, growth, implied EV) and the hidden inputs `COGS`, `COGS Adjustments`, `Fixed Expenses`, plus the QuickBooks history. Rows 1–22 are the drivers module; rows 108–131 (cash) are financing; E177:P206 payout block is financing.

## What the sheet does (read formulas in the snapshot: `P&L` columns B, C, T, W of every row; `P&L.rowmap.json` helps)
- Cost lines and how forward months are computed (column B holds the driver; forward col W):
  - Amazon fees (24) = FBA (25) `B25*AmazonRev`, seller fees (26), subscription (27 fixed $39.99), FBA inbound transport (28); B = sum(R:U)/sum(R19:U19) i.e. trailing 4 months (Apr–Jul 2026) ratio of Amazon revenue.
  - COGS (31) = China COGS (32) = `$B$32*TotalCompanyRevenue` with B32 = `COGS!L44` (weighted landed cost %); row 33 US COGS and row 35 Direct Product Cost are memo ratios.
  - Fulfillment Services (37) = `B37*DTC revenue`; Inbound Shipping (38) = rate × total revenue; Payment Processing (40) = shipping supplies (42, 0) + Shopify subscriptions (43) + Seller Fees (44), each rate × DTC revenue.
  - Total COGS block (46) = rows 40+38+37+31+24; Gross profit (47) = total revenue − (46); GP% (48).
  - Marketing Spend (51) is the drivers module's output; Marketing (59) = agency (60, fixed carry), services (61 typed), influencers (62), software (63 rate × revenue); Other Business Expenses (66) rate × revenue with sub-lines 67–73; Headcount (75) = `15000+FLOOR(MAX(0,Rev*12-20000000)/5000000)*15000`; Overhead (78) = lines 79–91 (accounting, chargeback mgmt (rate × DTC), consulting, customer service, insurance, interest, legal, memberships (rate), office, outside services, sales-tax filing, samples, software (rate)); Uncategorized (94); Total expenses (95) = 51+59+66+75+78+94; NOI (100) = GP − expenses; margins (96, 98, 101).
  - Cost-driver rates are trailing-window ratios; the window is hard-wired to R:U (Apr–Jul 2026) and drifts. Make it a lever: `rate_window_months` (default 4) over the last closed months, with an explicit "as of" shown, plus per-line override (fixed $ or rate).
  - `Opex Inflation` / `COGS Inflation` levers (A4/A5, both 0) apply to forward fixed/rate lines.
- TTM (139–141), FTM (135–136), growth rows 144–157 (MoM, 6-month avg MoM, YoY), FY roll-ups in BL:BP via SUMIFS, implied EV rows 159–162 (FY27 NOI × 3–8x; "N/A" if NOI<0).
- History (Jan-25…Jul-26) is PASTED QuickBooks values in the P&L itself (the Actuals tab is gone). Seed `proj_actuals` from the snapshot (`etl/projection_pnl.py`): one `line_key` per sheet row (stable snake_case in `app/projection/pnl/lines.py`: LINE_KEYS with label, sheet row, kind, fmt, parent). 44 QuickBooks sub-accounts exist; known QB facts: Inventory Shrinkage ($22,631.40, Feb-25) is a COGS line; Marketing Services belongs in the Marketing Agency & Services line.

## Build
1. `lines.py` (the contract's LINE_KEYS), the compute (`compute(scn, drivers, cohorts) -> PnlResult` with FY/TTM/FTM/EV), inflation + rate-window levers, per-line overrides through scenario series (`pnl.<line_key>` kind override), fixed vs rate vs carry-forward behaviours as data (`proj_pnl_drivers(line_key, mode('rate'|'fixed'|'carry'|'formula'), base ref, value)`), seeded from the sheet's column B/forward patterns.
2. QuickBooks upload: `POST /api/projection/pnl/upload` (xlsx or csv: QBO "Profit and Loss by Month" export as Josh pastes it; accounts mapped by name to line_keys through `proj_qb_map`, unmapped accounts listed in the preview, nothing silently dropped; preview then `?commit=1`; closed months only; never overwrites a month without showing the diff). Keep a copy of the raw file hash + row count in `proj_audit`.
3. API `/api/projection/pnl/…`: `table` (full P&L grid with subtotals and the FY roll-up columns; actual months from `proj_actuals`, projected from engine), `ev` (EV table: scenario × multiple), `drivers` (GET/PUT of the cost-driver table), `compare?scenarios=` (revenue, GP, NOI by scenario/FY), `upload`.
4. Page `pnl.js`: P&L grid (collapsible groups, FY toggles), a margin strip, EV table with the LOI ($7.5M) marker, scenario compare of FY NOI, driver table editor (rate/fixed/carry per line with the trailing value it came from), QuickBooks upload with preview.
5. Reconcile map: P&L rows 24–48, 51–101, 135–157 C..BJ and BL..BP FY columns, plus EV row 162. History months must match exactly (they are the same typed values); projected months within 0.5%. Expect differences from the sheet's moving window and from drivers' overrides; list them.

## Gotchas
- COGS rate comes from `COGS!L44` (weighted cost / mix); read that tab's formulas; keep it a lever with source note "COGS tab weighted landed cost".
- Historical expense lines can have blanks (not zero): blank = no data, treat as 0 in sums but do not store as 0.
- FY roll-ups of ratio rows are recomputed, not summed.
