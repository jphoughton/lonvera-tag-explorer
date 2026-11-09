# Amazon month to date, completed

Rule (Josh): if 5 days are reported and 8 days of the month have passed, take the trailing 7-day average of the reported days, project the 3 missing days at that rate, and overwrite each projected day when its actual arrives. Code: `app/projection/pacing/amazon_mtd.py` (pure `compute`), nightly cache `etl/amazon_mtd.py` -> `proj_amazon_mtd_snapshots`.

What is measured (production, read-only, 2026-11-09)
- `proj_feed_amazon` runs through yesterday every day: every feed pull on record (14 pulls since Nov 4, `proj_feed_attempts`, Wed to Mon) ended one day behind the pull date. Today (Mon Nov 9, 05:34 Pacific pull) data runs through Sun Nov 8, 773 days held since 2024-09-24.
- Newest days are not materially incomplete. Same days in two pulls, Nov 4 21:45 UTC (shadow table) against Nov 9 (live table): Nov 3 went up 4.1% ($4,860.70 to $5,061.79); Oct 24 to Nov 2 went down 1.8% to 7.3% (about -4% on average; older days restate down, cause not checked). Only one restatement window was available, so the size of the newest day's gap on its first pull is not measured; today's partial day is never counted.
- Weekday pattern is weak (Mon-Sun index since May: 0.985, 0.997, 1.002, 0.996, 0.983, 0.993, 1.043).

Backtest on 21 closed months (Jan 2025 to Oct 2026), projecting 1 to 4 missing days at day 8, 15 and 22 (error = projected minus actual, share of actual)

| Rule | Missing days sum, mean error (3 missing) | Mean absolute | Worst | Full MTD at day 8 / 15 / 22 (3 missing), mean absolute |
|---|---|---|---|---|
| none (actuals only) | -100% | 100% | 100% | 36.6% / 20.9% / 13.5% |
| trailing 7 available (chosen) | +2.5% | 12.0% | 43.2% | 4.5% / 2.6% / 1.6% |
| trailing 14 | +2.2% | 12.8% | 43.0% | 5.0% / 2.9% / 1.5% |
| trailing 3 | +2.3% | 11.0% | 53.2% | not run |
| same weekday (last 4 weeks) | +4.1% | 16.6% | 75.0% | 6.1% / 3.4% / 2.2% |
| trailing 7 scaled by weekday index | +2.7% | 12.1% | 45.0% | 4.5% / 2.7% / 1.6% |

No rule is a material win over the trailing 7-day average (trailing 3 is 1 point better on average and noisier). The large worst cases are the May 2026 and holiday spikes, not the rule. The prior-period ratio rule was not run.

Edge cases (all in `tests/projection/pacing/test_amazon_mtd.py`): zero reported days this month falls back to the previous month's last 7 reported days (labelled); fewer than 7 reported days uses what exists (`short_window`); a zero or absent day inside the window is left out of the rate (`window_has_gaps`) and never projected; a feed more than 5 days behind is `feed_stale` and nothing is projected; a newest day under half of the 7 days before it is flagged `newest_day_partial` and topped up to the rate (real data is never dropped); Pacific dates handle daylight saving.
