# Tickets for `drivers` (from the variables audit)

Written by proj-variables. Variable ids refer to `docs/projection/VARIABLES.md`. Each ticket names the variable, where it sits in the code, the proposed fix and the test that proves it.

### tiktok-ads-formula: the sheet's TikTok ads line is 0.5% of Facebook spend

- **Variable:** `drivers.channel_share.tiktok` (default 0.005552, the trailing mix, `P&L!B56`). The sheet's forward cells `P&L!V56:BJ56` are `=V53*0.005`: TikTok ads = 0.5% of Facebook ads, so about 0.45% of total marketing spend with the Facebook share at 89.7%.
- **Where:** `drivers/model.py` `DEFAULT_SHARES`.
- **Effect:** the app spends 0.56% of marketing on TikTok where the sheet spends about 0.45%; at $350,000 a month that is about $390 a month.
- **Decision for Josh:** keep the trailing mix (current) or take the sheet's formula. If the formula: add a lever `tiktok_share_of_meta` (0.005) and compute TikTok as that share of the Facebook line.
- **Test:** with the lever set, `drivers.spend_tiktok[m] == 0.005 * drivers.spend_meta[m]` for planned months.

### channel-share-sum: one channel share cannot be saved alone

- **Variables:** `drivers.channel_share.amazon|meta|google|bing|tiktok`.
- **Where:** `drivers/api.py` `_check_levers` requires all five keys and a sum of 100% (`SHARE_TOLERANCE`).
- **Effect:** correct for the Spend plan page, which edits the five together. The Variables page edits one at a time, so it rebalances the other four in proportion and writes all five (`access._rebalance_shares`).
- **Fix (optional):** accept a partial `channel_share` patch and rebalance on the server, so every editor behaves the same.
- **Test:** `PUT /drivers/levers {channel_share: {meta: 0.8}}` returns shares that sum to 1.

### reference-guards: bounds and coverage of the realized CPA and AOV (added by proj-drivers)

New levers, all in `params.drivers`, set with `PUT /drivers/levers` (Plan settings card on the Spend plan page), please list them on the Variables page:

| key | unit | default | sheet cell | set where | proof |
|---|---|---|---|---|---|
| `reference_gap_days` | days | 2 | none (app default: feed days that may be missing from the window) | levers | `test_drivers_reference::test_a_feed_missing_up_to_the_allowed_gap_still_counts_and_one_more_does_not` |
| `reference_cpa_min` / `reference_cpa_max` | dollars | 20 / 500 | none | levers (sent together) | `test_a_cpa_outside_the_believable_range_is_withheld_with_the_reason`, `test_the_guard_bounds_are_levers_in_plan_settings` |
| `reference_aov_min` / `reference_aov_max` | dollars | 15 / 400 | none | levers (sent together) | `test_aov_is_offered_inside_its_range_and_withheld_outside` |

Rule: `drivers.reference.load_reference(conn, channels, window_days, today, levers)` offers a realized CPA or AOV only when every counted spend feed and the orders feed have at least window days minus `reference_gap_days` distinct days in the window and the value sits inside its bounds; otherwise the value is None and `Reference.unavailable['cpa' | 'aov' | 'amazon']` holds the plain reason ("Meta spend feed has 9 of 28 days"). Pass `levers` so the scenario's bounds apply.

**Registered on the Variables page (proj-variables):** `drivers.reference_days` (28), `reference_tolerance` (0.05), `reference_gap_days` (2), `reference_cpa_min` / `reference_cpa_max` ($20 / $500) and `reference_aov_min` / `reference_aov_max` ($15 / $400) are editable there (a bound is saved with its partner, as `PUT /drivers/levers` requires). Each has a mover proof in `tests/projection/variables/test_variables_reference.py` on synthetic feeds: the gap lever decides whether a feed with 3 missing days still counts, each bound withholds a figure outside its range with the plain reason, and the window lever changes the days read. Closed.
