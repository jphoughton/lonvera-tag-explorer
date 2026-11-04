# Tickets for `inventory_supply` (from the variables audit)

All 22 settings and every vendor, price, reorder and product-cost cell are named scenario variables with a mover test; the findings below are small.

### reorder-default-literal: two fallbacks repeat the default safety days as a literal

- **Variable:** `inventory_supply.default_safety_days` (14, sheet `Warehance Fcst Inputs!C44:C47`).
- **Where:** `app/projection/inventory_supply/store.py` line 381 and `overlay.py` line 118 write `14` when a reorder row is created without safety days; `model.py` `ReorderParam` defaults (`lead_days = 90`, `safety_days = 14`) repeat the lead and safety defaults.
- **Fix:** read `Settings.default_safety_days` and the location's `default_lead_days_*` instead of the literals.
- **Test:** set `default_safety_days = 21`, create a reorder row for a new product without safety days; the row carries 21.

### unused-defaults-today: three settings and the vendor deposit share are never read with today's data

- **Variables:** `default_safety_days`, `default_lead_days_revamp`, `default_lead_days_intl` (every SKU has its own reorder row) and `inventory_supply.price.*.deposit_pct` (every order carries its own deposit share). They are wired in code; no output moves with the seeded data. The registry marks them `probe: none` with this reason.
- **Test to add when data changes:** add a SKU without a reorder row and a purchase order without a deposit share and move each default.

See also `TICKETS_pnl.md#china-cost-duplicate` (product cost stored in two places).
