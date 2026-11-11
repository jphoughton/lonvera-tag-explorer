# Lonvera Tag Explorer: instructions for any AI agent

One Flask app (`app/`) plus one Postgres database on Railway. It serves Tags / Marketing metrics (Meta and Google ad numbers), Projection (P&L and cash model), Creative priorities and the
Production board. Live: https://web-production-358da.up.railway.app. Default branch `build/v1`. Read `docs/AGENT_GUIDE.md` first for the whole map.

## For any data question, use the lonvera-brain tools first

The brain is an MCP server, registered in `.mcp.json` (hosted at `/mcp`, needs a personal key, see below). Order of use:
1. `catalog` (what views exist) then `search` or `metric` (where it lives, what it means) then `describe` (columns) then `sql` (read-only SELECT on the `analytics` views).
2. `freshness` before trusting a recent number. `past_questions` before re-answering any "why did X move" question. `app_map` for services, schedules, pages and routes.
3. Never guess a column or a number. If it is not in the views, say so (see "Not in the database" in `docs/AGENT_DATA_GUIDE.md`).

Get a key: open `/connect-agent` (sign in first when sign-in is on; while the app is open anyone can make one), press "Make my key", and export it as `LONVERA_BRAIN_KEY` (or run the Claude Code command shown on that page).
No MCP, or offline: run the local server, `claude mcp add lonvera-brain-local -- .venv/bin/python -m brain.server` (venv: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`;
SQL then needs `ANALYTICS_READER_URL` or the file named by `LONVERA_ANALYTICS_URL_FILE`). Without the tools, follow `.claude/skills/lonvera-data/SKILL.md`.

## Metric traps that cause repeat mistakes

- **Name the basis.** Meta-reported (counts returning customers) vs Upstack new customers (first orders only, the "NC" in NC ROAS / NC CPA) vs Upstack all orders. Never mix bases in one ratio.
- **Spend adds across platforms; purchases and revenue never do** (Meta and Google credit the same orders). Total NC ROAS = actual new-customer revenue / total spend over the same days.
- **Gross, not net.** Revenue includes tax and is before refunds. Say so when comparing with the P&L.
- **Hit rate has a threshold.** Check the definition with `metric` (spend or count cut-off) before quoting one; a rate without its threshold is not comparable.
- **Ratios from sums, creatives once.** `sum(revenue) / sum(spend)`, never an average of daily ratios. In `creative_tracker` metrics sit on the first row of each ad family.
- **NULL is not zero**, and the last 3 to 7 loaded days still move. The last loaded day is yesterday (Pacific).
- **A cause needs a like-for-like control.** Counts are not rates: divide by the right denominator. Say "consistent with" unless there is a randomized holdout or a clean control. List every change in the window first.

## Where things are

- `docs/AGENT_GUIDE.md` (orientation), `docs/AGENT_DATA_GUIDE.md` (data quick start), `docs/DATA_SCHEMA.md` and `docs/schema.json` (every view and column), `docs/APP_MAP.md` (generated: services, routes, jobs).
- `docs/questions/` (Josh's past questions and answers, when present), `docs/projection/` (the model), `migrations/` (numbered SQL, applied ones are immutable).
- Code: `app/` (Flask), `etl/` (jobs, `python -m etl.<name>`), `brain/` (the brain tools), `scripts/` (generators, deploy scripts), `tests/`.
- Generated files have checks: `python scripts/gen_app_map.py --write` after any route, page, service or job change; `python scripts/gen_schema_doc.py --write` after any table or column change.

## Rules

- Branch, pull request, merge. Never commit or push to `build/v1` or `main`. Applied migrations are immutable: add a new one.
- Read-only on Google Sheets and the Creative Tracker. Meta writes only through `etl/meta_write.py`, PAUSED only, and switched off. No Meta, Google or ExpandFi calls to "test" something.
- No secrets in the repo or in chat: never print a key, token or database URL. Point to the Railway variable instead.
- Plain tables in the UI, no em dashes, expand every acronym on first use, no compliance hedging in ad copy or prompts.

## Tests

Python is the virtualenv at `~/lonvera-tag-explorer/.venv/bin/python`; tests need local Postgres 16. The full suite takes 8 to 9 minutes: run it in the background, one run at a time.

```bash
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH PYTHONPATH=.
export TEST_API_DB=tagexp_apitest_$RANDOM UI_MOCK_PORT=$((5100 + RANDOM % 800))
~/lonvera-tag-explorer/.venv/bin/python -m pytest tests -q -rf -p no:cacheprovider
```

The one known clock-dependent failure is `tests/api/test_api_v19_extras.py::test_stale_combines_reasons_and_falls_back_to_etl_runs`. The brain tests are `tests/brain/` (the server test needs the `mcp` package from `requirements-dev.txt`).
