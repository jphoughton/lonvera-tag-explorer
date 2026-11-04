# Tickets for `pacing` (from the variables audit)

### typed-intraday-inputs: three typed inputs of the Automated Pacing tab are not taken

- **Variables:** `pacing.todays_meta_spend` (`Automated Pacing!M14`, 7,528 typed each day), `pacing.time_override` (`M15`, optional clock time), `pacing.hourly_curve_days` (`M20`, 30). The hourly spend table `M23:N46` is in `pacing.hourly_spend_curve`.
- **Where:** the intraday end-of-day projection in `app/projection/pacing/month.py` (lines 300-320) reads the hourly feed and the current time.
- **Fix:** if the end-of-day projection should accept a typed value, add `POST /pacing/intraday {meta_spend_so_far, time}` (page input, not a scenario lever) and a global setting for the curve length; otherwise leave it feed-driven and mark the sheet cells as retired.
- **Test:** with `meta_spend_so_far = 7528` at 20:00 the projected end-of-day spend equals `7528 / share_of_day(20:00)`.

### view-only-levers: eleven levers move only what the page shows

- `pace_good`, `pace_warn`, `cost_warn`, `early_days`, `payback_bad_months`, `stale_bad_days`, `rollup_days`, `rollup_weeks`, `last_days`, `rates_month`, `below_ebitda_rows` change thresholds, windows and the goal construction at request time. They are wired (the module's own tests move them); the registry marks them `probe: view` because `engine.run` does not contain them. No change needed.
