# Tickets for `inventory_demand` (from the variables audit)

### feed-rules-constants: loader and freshness rules are fixed in code

- **Variables (not editable today):** `inventory_demand.feed.partial_tail_days.revamp|amazon|intl` (1, 1, 0; sheet `Warehance Fcst Inputs!B5`, `Amazon Fcst Inputs!B5`, `Intl Fcst Inputs!B5`), `inventory_demand.feed.stale_after_days` (2, 2, 4), and `inventory_demand.feed.intl_estimate_factor` (0.89, the international estimate = Warehance non-US orders x 0.89, computed outside the app).
- **Where:** `app/projection/inventory_demand/constants.py` `PARTIAL_TAIL_DAYS` (line 40), `FALLBACK_PARTIAL_TAIL_DAYS` (41), `STALE_AFTER_DAYS` (47), `DEFAULT_BASELINE_DAYS` (44, already a lever default); used in `etl/projection_inventory_demand.py` lines 126, 169, 334 and `inventory_demand/table.py` line 89.
- **Fix:** store them as global settings (not scenario params), the way `proj_pacing_setting` holds the pacing rules: table `proj_demand_setting(key, value)`, read by the loaders and the freshness hook, edited with `PUT /inventory-demand/rules`, defaults as in the constants. The 0.89 factor stays where the estimate is computed, but its value should be recorded beside the estimated days.
- **Test:** set `partial_tail_days.amazon = 2`; the next refresh holds back two days and `data_through` moves back one day.
