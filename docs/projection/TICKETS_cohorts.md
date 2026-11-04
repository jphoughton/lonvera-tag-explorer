# Tickets for `cohorts` (from the variables audit)

### upload-rules: three rules are constants

- **Variables (not editable today):** `cohorts.upload.min_customers` (10), `cohorts.freshness.stale_after_days` (45), `cohorts.backtest_months` (3).
- **Where:** `app/projection/cohorts/upload.py` line 31 `MIN_CUSTOMERS` (the upload preview takes `?min_customers=` for one run); `cohorts/store.py` line 110 `STALE_AFTER_DAYS`; `cohorts/views.py` line 124 `[-3:]`.
- **Fix:** settings under `params.cohorts` for the backtest window (scenario) and a global setting table or the module's existing settings for the other two (they describe the data feed, not a scenario).
- **Test:** `backtest_months = 6` changes `GET /cohorts/summary` `backtest.months` to six months.
