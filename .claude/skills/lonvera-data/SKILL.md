---
name: lonvera-data
description: Answer any Lonvera marketing data question (ad spend, ROAS, CPA, new customers, creatives, tags, coverage) from the analytics database without guessing. Use when the lonvera-brain MCP tools are not available, or as the checklist behind them.
---

# Lonvera data workflow

The same order as the lonvera-brain tools (catalog, then search or metric, then describe, then sql). Without MCP, do it with files and `psql`.

1. **What exists.** Read `docs/AGENT_DATA_GUIDE.md` (one page: views, the five rules, what is not in the database). Then find the view in `docs/DATA_SCHEMA.md` (TLDR first; machine-readable twin `docs/schema.json`). Search other docs with `grep -ril <word> docs/`.
2. **What a metric means.** Look for it in `docs/schema.json` under `"metrics"` (formula, source, default basis, traps) when that list exists; otherwise in `docs/AGENT_DATA_GUIDE.md` rules 1 to 5 and the column descriptions. Name the basis in every answer: Meta-reported, Upstack new customers, or Upstack all orders.
3. **Was it asked before.** Search `docs/questions/` (`lonvera_items.jsonl`, `open_questions.md`) for the topic, especially before answering any "why did X move" question. Re-check the numbers; never reuse an old answer blind.
4. **Is the data fresh.** `select source, first_day, last_day, loaded_at from analytics.data_coverage;` and `select job, started_at, status, error from analytics.etl_runs where status = 'failed' order by id desc limit 10;`
5. **Query it.** The connection URL is in a file the admin gave you (mode 600, never in the repo, never pasted into chat), by default `~/lonvera-tag-explorer-data/analytics_reader_url.txt`:

   ```bash
   psql "$(cat ~/lonvera-tag-explorer-data/analytics_reader_url.txt)" -c "select source, last_day from analytics.data_coverage"
   ```
   ```python
   import psycopg; conn = psycopg.connect(open("/path/to/analytics_reader_url.txt").read().strip())
   ```
   You are the role `analytics_reader`: SELECT on the `analytics` views only, 30 second limit. Run ONE SELECT at a time and cap the rows. Never print the URL.
6. **Say what you could not check.** If a column or table is not in the dictionary, it is not there: say so instead of guessing (list under "Gaps" in `docs/DATA_SCHEMA.md`).

## Traps (each one has caused a wrong answer)

- Never mix bases in one ratio. Spend adds across platforms; purchases and revenue never do. Revenue is gross.
- Ratios come from sums, not averages of daily ratios. NULL is not zero. The last 3 to 7 days still move.
- A hit rate needs its threshold. A cause needs a like-for-like control; counts are not rates; say "consistent with" unless there is a randomized holdout.

Where things run, which page or route serves what, and the job order: `docs/APP_MAP.md`. Orientation for everything else: `docs/AGENT_GUIDE.md`.
