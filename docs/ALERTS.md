# Failure and staleness alerts (Slack)

Nobody should learn from a stale page that a job died. `python -m etl.alerts` runs every hour as the Railway cron service **`alerts`**, runs read-only checks over `etl_runs`, the heartbeat tables, the key data tables and the public web app, and posts one Slack message per problem. Code: `etl/alerts.py`. State: table `alert_state` (migration 197). Tests: `tests/alerts/`.

It deliberately never writes `etl_runs` (an hourly row there would hide a stale pipeline from the stale banner and flush the response cache every hour).

## Checks and thresholds (constants at the top of `etl/alerts.py`)

| Check id | Fires when | Threshold |
| --- | --- | --- |
| `job:run_daily`, `job:upstack_daily`, `job:stage_moves` | latest run failed; latest run still `running` after 6 h (killed / crashed); no successful run in time; never ran | nightly: 36 h |
| `job:meta_breakdowns` | same, silent until the first row exists; the `breakdowns` service runs every 2 h but writes no row in the quiet hours (12:30-15:30 UTC) or when another run holds the lock | 9 h |
| `job:auto_tag`, `job:backup` | same, but silent until the first row exists (not deployed yet) | nightly: 36 h |
| `job:competitor_ads`, `job:creative_priorities` | same; Sunday jobs (the nightly run is a no-op other days and writes no row) | 8 days |
| `job:feeds` | newest successful attempt in `proj_feed_attempts`, or a feed whose latest pull failed (feeds named) | 36 h |
| `job:live` | newest `proj_live_today_snapshots.pulled_at` | 3 h |
| `job:amazon_mtd` | newest `proj_amazon_mtd_snapshots.computed_at` | 36 h |
| `job:projection_daily` | newest ok `proj_refresh_runs`, or a module whose latest run failed (module named) | 36 h |
| `claude:token` | auto_tag or competitor_ads stopped on an authentication error, or `CLAUDE_CODE_OAUTH_TOKEN` is unset. Message: token expired, run `claude setup-token`, update the Railway `etl` variable | next failed run |
| `claude:limit` | auto_tag or competitor_ads stopped on a Claude usage limit (retries by itself next night) | next failed run |
| `fresh:meta` / `fresh:upstack` / `fresh:orders` / `fresh:google` | newest day in `app_meta` data_through / upstack_through, `upstack_orders_daily`, `google_daily` is more than 2 / 2 / 2 / 3 days behind today (Los Angeles). The 2-day limit is the stale banner's (`app.queries.STALE_DATA_DAYS`; a test keeps them equal) | days |
| `fresh:feeds` | `daily` projection feeds past their own `stale_after_days` (the `freshness()` hooks of the projection modules, same data as Projections > Freshness; inventory, pacing, drivers ...). `monthly-upload` and `manual` feeds are never alerted | per feed |
| `web:health` | `GET <web>/healthz` is not 200 or takes more than 5 s, twice in a row 20 s apart (a deploy restarts the app for about a minute). `/healthz` is public, returns `{"ok": true}` or 503 when the web app cannot reach Postgres, and carries no data | 5 s |
| `alerts:check:<name>` | a check itself crashed (its alerts are not being evaluated) | immediately |

A `fresh:*` finding is not sent when the job that feeds it already has a finding in the same run (the gap is a symptom). When the 5-message cap bites, job and web findings go first.

## Behaviour

* Alert once when a problem appears; repeat every 24 h while it lasts (`STILL FAILING (alert N, open since ...)`); at once with `CHANGED:` when its fingerprint changes (another feed started failing); one `RECOVERED:` line when it clears. State lives in `alert_state`.
* At most 5 messages per run; anything beyond is collapsed into one summary message listing the rest. Recoveries count towards the cap.
* Message shape: `ALERT: <what broke>` / `Since <time PT>. Fix: <what to do>` / link to the web app. Error text is scrubbed of tokens.
* The job cannot fail silently: a crashing check, an unreachable database, a missing `alert_state` table or an undeliverable Slack message makes the run exit 1 (Railway shows Failed). Where a webhook is reachable the problem is also sent to Slack (database down repeats hourly, because there is nowhere to remember it).
* `SLACK_ALERT_WEBHOOK_URL` unset: alerts are only logged, with `WARNING: SLACK_ALERT_WEBHOOK_URL is not set`, and exit 0. Nothing is marked as alerted, so everything open goes out on the first run after the webhook is set. A webhook URL is never logged or put in an error message.
* Not covered: the `alerts` service itself not running (there is no one left to tell). Cheap fix when it matters: have `run_alerts.sh` ping an external dead-man's-switch (healthchecks.io style) URL after each good run.

## Set it up (one time)

1. **Slack webhook** (Josh): Slack > Apps > Incoming Webhooks (or create a Slack app, enable Incoming Webhooks, Add New Webhook to Workspace) > pick the channel > copy the URL (`https://hooks.slack.com/services/...`). Do not paste it in chat or commit it.
2. **Create the Railway service** (same pattern as `live` / `feeds`; from a directory linked to the project):
   ```
   python3 scripts/deploy/apply_alerts_service.py            # dry run: prints the plan
   python3 scripts/deploy/apply_alerts_service.py --apply    # creates service `alerts`: cron 0 * * * *, start scripts/deploy/run_alerts.sh, DATABASE_URL by reference to etl
   ```
   (or by hand in the dashboard: new empty service `alerts`, Settings > Start Command `scripts/deploy/run_alerts.sh`, Cron Schedule `0 * * * *`, Restart policy Never, Variables: `DATABASE_URL` = `${{etl.DATABASE_URL}}`.)
3. **Set the webhook on the `alerts` service only** (Railway dashboard > alerts > Variables > `SLACK_ALERT_WEBHOOK_URL`). Optional: `ALERTS_WEB_URL` if the public web URL changes (default `https://web-production-358da.up.railway.app`).
4. **Deploy the code** after the PR is merged: `railway up -s alerts --ci` (migration 197 is applied by `run_alerts.sh` on the first run).
5. **Verify the webhook**: `railway run -s alerts sh -c 'python -m etl.alerts --test'` sends a message starting `TEST ONLY`. Then `... python -m etl.alerts --dry-run` shows what would go out now.

A cron service only runs on its schedule: deploying does not start a run, so the first real run is at the next full hour.

## Commands

* `python -m etl.alerts --dry-run` reads everything (state included, if the table exists), writes and sends nothing, prints each message and the action per check id. Safe against production: `railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" python -m etl.alerts --dry-run'`.
* `python -m etl.alerts --test` sends the labelled test message (exit 2 if the webhook is unset, 1 if Slack refuses it).

## Add a check

1. Write `def check_x(conn, ctx) -> list[Finding]` in `etl/alerts.py`. Read-only SQL (dict rows). `Finding(id, title, fix, since, fingerprint)`: `id` is the stable de-duplication key (`job:`, `fresh:`, `web:`, `claude:` prefixes), `title` says what broke in one sentence, `fix` says what to do, `fingerprint` changes when the problem changes shape.
2. Register it in `CHECKS`. A crash inside it is reported by the runner; it never stops the other checks.
3. A pipeline job: add a `JobSpec` to `JOBS` instead (cadence in hours; `required=False` keeps it silent until its first row). A job that writes `etl_runs` needs nothing else; one that writes its own table gets a `heartbeat:<name>` branch in `check_heartbeats`.
4. Add a test in `tests/alerts/test_alerts_checks.py`; update the table above.

## Silence a check

* A job or data source you retired: delete its `JobSpec` / source entry (and its row in `alert_state`: `DELETE FROM alert_state WHERE check_id = '...'`).
* A noisy one for a while: raise its threshold constant, or mark the job `required=False`.
* Pause everything: remove `SLACK_ALERT_WEBHOOK_URL` from the `alerts` service (alerts keep being logged and stay pending), or set the cron schedule to something rare.

## The `backup` hook

The nightly database backup job is expected to record itself in `etl_runs` with `job = 'backup'` (`db.JobRun("backup")`, status `failed` plus an error on failure). Until the first such row exists the check is silent; afterwards a failed or missing backup (more than 36 h) alerts as `job:backup`. If the backup runs outside this repo, insert the row from its wrapper script instead.
