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
