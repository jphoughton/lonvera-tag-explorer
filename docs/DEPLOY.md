# Lonvera Tag Explorer: deploy, daily refresh, ops

Last verified: 2026-11-03 (infra smoke-tested with a stub app; real code not yet deployed).

## Architecture

One Railway project, one environment (`production`), three services, one Docker image built from this repo.

| Service | What | Start | Runs |
|---|---|---|---|
| `web` | Flask API + SPA behind a login (`wsgi:app`, gunicorn 2 workers x 8 threads) | image CMD `scripts/deploy/start_web.sh` = `python -m etl.migrate` then gunicorn | always; public domain; healthcheck `/healthz` |
| `etl` | Daily refresh (`python -m etl.run_daily`) | service start command `scripts/deploy/run_etl.sh` = migrate, then run_daily | Railway cron `0 13 * * *` (13:00 UTC = 06:00 PDT / 05:00 PST), exits when done |
| `Postgres` | Railway Postgres 18 (`ghcr.io/railwayapp-templates/postgres-ssl:18`), volume `postgres-volume` | template | always; private network `postgres.railway.internal`; public TCP proxy for local backfill/dumps |

- Project `lonvera-tag-explorer` (workspace "jphoughton's Projects"), project id `5ea06da2-cc55-4bb7-ac5c-8462d0d95338`, environment `production` id `cf231125-225f-487d-b491-4739d9a3a303`.
- Dashboard: https://railway.com/project/5ea06da2-cc55-4bb7-ac5c-8462d0d95338
- Public URL (web): https://web-production-358da.up.railway.app (login password is in `~/lonvera-tag-explorer-data/APP_PASSWORD.txt`, chmod 600, outside the repo).
- web and etl talk to Postgres over the private network (`DATABASE_URL`). Only the local machine uses the public proxy.

## Why settings are not in railway.json

Railway deprecated Config-as-Code: services created now ignore `railway.json` / `railway.toml`, and legacy files stop being read on 2026-12-01. `railway.json` in the repo is kept only as a human-readable reference of the web settings. The live settings are service settings, applied by an idempotent script:

```bash
python3 scripts/deploy/apply_service_config.py            # apply web + etl settings
python3 scripts/deploy/apply_service_config.py --show     # read back
python3 scripts/deploy/apply_service_config.py --cron "0 14 * * *"   # move the daily refresh (UTC, runs must be >= 5 min apart)
```

Settings it owns: both services `dockerfilePath=Dockerfile` (Railway auto-detects the Dockerfile; there is no "DOCKERFILE" builder enum, the `builder` field still reads RAILPACK); web `healthcheckPath=/healthz`, `healthcheckTimeout=300`, restart ON_FAILURE x5; etl `startCommand=scripts/deploy/run_etl.sh`, `cronSchedule`, restart NEVER (a failed run must show as failed, not loop and hammer Meta). The script talks to Railway's GraphQL API with the CLI's own login token (nothing printed). Equivalent dashboard fields: Service > Settings > Deploy (Custom Start Command, Cron Schedule, Healthcheck Path, Restart Policy).

## Environment variables (names only; values live in Railway)

| Name | web | etl | Purpose |
|---|---|---|---|
| `DATABASE_URL` | ref `${{Postgres.DATABASE_URL}}` | same | private-network Postgres URL |
| `DATABASE_PUBLIC_URL` | - | ref `${{Postgres.DATABASE_PUBLIC_URL}}` | public TCP-proxy URL, used only from a laptop (see Local runs). Defined on the Postgres service from `RAILWAY_TCP_PROXY_DOMAIN/PORT` |
| `SECRET_KEY` | set (96 hex chars) | ref `${{web.SECRET_KEY}}` | Flask session signing |
| `APP_PASSWORD` | set (24 chars) | ref `${{web.APP_PASSWORD}}` | the single login password |
| `PORT` | `8080` | - | port gunicorn binds and the public domain targets |
| `META_ACCESS_TOKEN` | - | set | Meta Graph read token (both ad accounts) |
| `GOOGLE_SA_JSON` | - | set | service-account JSON (one line) for the Creative Key sheet `12LmoKXLB-fXw9jA5VvfqzVPdgZrVnglowqVbwrKCaNI` (SA has read/Editor access) |
| `WEB_WORKERS`, `WEB_THREADS` | optional | - | gunicorn sizing overrides (defaults 2 / 8) |

Never put these in files or chat. Set with stdin so the value never appears in argv or shell history:

```bash
printf '%s' "$VALUE" | railway variable set -s etl NAME --stdin        # redeploys the service (add --skip-deploys to defer)
railway variable list -s etl --json | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'   # names only
```

## Deploy

Link the repo dir once (stores the link in `~/.railway`, not in the repo): `railway link -p lonvera-tag-explorer -e production`.

```bash
railway up -s web -c -m "web: <what changed>"     # build + deploy the web service; -c streams build logs then exits
railway up -s etl -c -m "etl: <what changed>"     # build + deploy etl; the new deployment first runs at the next cron tick (not immediately)
railway service status -s web                      # Status: SUCCESS when healthy
railway logs -s web --lines 50
curl -s https://web-production-358da.up.railway.app/healthz     # {"ok":true}
```

`railway up` uploads the working directory (respects `.gitignore`; `.dockerignore` trims the image: `.venv`, `tests`, `data`, `.git`). Both services build the same Dockerfile. Deploy `etl` whenever `etl/**` changes, `web` whenever `app/**` changes, both for `requirements.txt`/migrations. Migrations run on every web boot and every etl run (idempotent); concurrent runners must be safe (advisory lock in `etl.migrate`).

First-time bring-up order: `web` (creates schema) then local backfill (below) then `etl`.

## Daily refresh

- `etl` is a Railway cron service: at 13:00 UTC Railway starts the container, runs `scripts/deploy/run_etl.sh` (`etl.migrate` then `etl.run_daily` = tracker_sync, meta_pull trailing 7 days, family mapping, build_metrics), and the container exits. A non-zero exit marks that execution Failed. If a run is still going at the next tick Railway skips the new run.
- 13:00 UTC is after Meta has finalized yesterday for the account timezone (Pacific). Change with `apply_service_config.py --cron`.
- Check the last run: dashboard > etl > Deployments (each cron execution appears in the deployment's log), or `railway logs -s etl --lines 100`, or the app's `GET /api/status` (`last_refresh`), or `select * from etl_runs order by started_at desc limit 5`.
- Failure alerts: Railway dashboard > Project Settings > Notifications (email / Slack / webhook on deploy failed/crashed). Turn this on; it is not scriptable from the CLI.

### Run it manually

A cron service only runs on its schedule: deploying or `railway redeploy` does NOT start a run (verified 2026-11-03), and `railway ssh -s etl` only works while a run's container is alive. Options:

```bash
# 1. From the laptop against the production DB (uses etl's secrets via the public proxy URL). Preferred; run from the repo dir:
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.run_daily'
# 2. One job with flags (e.g. the 185-day Meta pull):
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.meta_pull --days 185'
# 3. Inside Railway (same container, private DB): set the schedule to the next 5-minute slot, wait, then put it back
python3 scripts/deploy/apply_service_config.py --cron "*/5 * * * *"    # ...after the run shows in `railway logs -s etl`:
python3 scripts/deploy/apply_service_config.py                          # back to 0 13 * * *
```

`railway run` executes locally with the service's variables injected; `DATABASE_URL` points at the private host, which a laptop cannot resolve, hence the `DATABASE_PUBLIC_URL` swap.

### Local backfill (initial load of the snapshot into production)

```bash
cd ~/lonvera-tag-explorer
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.backfill_sheet --snapshot-dir ~/lonvera-tag-explorer-data/snapshot-2026-11-03 --as-of 2026-11-02'
```

The variable to use locally is `DATABASE_PUBLIC_URL` (name only; resolve it through `railway run`, never copy the value into a file).

### Ad hooks (migration 010, `etl.load_hooks`)

A hook is an ad's first spoken line plus its on-image headline, boiled down to one line. A separate extraction cycle writes a JSON file per product (`[{row, concept_id, ad_family, unit_id, hook, spoken, on_screen, hook_source, confidence, note}]`); `etl.load_hooks` loads it into `ad_hooks`, which feeds the Hook column of the All creatives table and the Hook card on the ad page. Order matters: schema first, then the data.

```bash
# 1. Apply migration 010 (creates ad_hooks). It also runs by itself on every web boot and every etl run, so after a web deploy this is already done.
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.migrate'
# 2. Load the hooks, one product at a time (the file is read from the laptop). Rows are matched to the tracker like etl.load_tags does: by (concept id, ad family) + ordinal, never by sheet row.
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.load_hooks --file <hooks_vita_bull.json> --version hooks-v1-2026-11-05 --product vita-bull'
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.load_hooks --file <hooks_beef_liver.json> --version hooks-v1-2026-11-05 --product beef-liver'
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.load_hooks --file <hooks_boneflex.json> --version hooks-v1-2026-11-05 --product boneflex'
```

- The load fails (nothing written) when fewer than 90% of the product's tracker rows match the file (`--min-coverage 0.90`; `--allow-mismatch` overrides), or when a `hook_source` / `confidence` value is not one of `spoken|on_screen|both|copy|none` / `high|medium|low`. Entries with an empty hook are skipped after the match; hooks whose key left the tracker are skipped and listed as a warning.
- One hook per tracker row: re-running a version replaces its rows, a newer `--version` replaces the older hook of every row it covers, rows a file does not cover keep what they have. The whole load is one transaction. Try it first on a local DB (`tagexp_dev` after `python -m etl.migrate`).
- The load is an `etl_runs` job (`load_hooks`), which is what moves the web service's response-cache footprint: the new hooks show up on the next request after the run finishes, no restart needed. Before migration 010 is applied the API simply returns no hooks.

## Rotating secrets

- **APP_PASSWORD** (redeploys web):
  ```bash
  python3 - <<'EOF' | railway variable set -s web APP_PASSWORD --stdin
  import os, secrets, string, sys
  pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
  p = os.path.expanduser('~/lonvera-tag-explorer-data/APP_PASSWORD.txt')
  fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
  open(fd, 'w').write(pw + '\n'); sys.stdout.write(pw)
  EOF
  ```
  etl follows automatically (it references `web`). Existing browser sessions stay valid until `SECRET_KEY` changes; rotate `SECRET_KEY` the same way (`secrets.token_hex(48)`) to force everyone to log in again.
- **META_ACCESS_TOKEN**: `printf '%s' "$NEW_TOKEN" | railway variable set -s etl META_ACCESS_TOKEN --stdin`. The next cron run uses it; test now with the manual run above (`railway run -s etl ...etl.meta_pull --days 1`). Current token: a Facebook **user** token (debug_token 2026-11-03: type USER, valid, `expires_at` 0, `data_access_expires_at` 2026-08-28) pulled from the local Odylic Lens store. It dies if its owner changes their password, is removed from the ad accounts, or revokes the app. Recommended: a **system-user token** (Business Settings > System Users > add an Employee system user, assign both Lonvera ad accounts with read access, generate a token with `ads_read` only, "never expires"), then set it as above. `etl.meta_pull` must exit non-zero on a Meta auth error (codes 190 / 102 / 463 / 467) so the cron execution shows Failed; falling back to the sheet source keeps the data fresh but must not turn the run green.
- **GOOGLE_SA_JSON**: the service account is `kill-scale-sheet-writer@shopify-data-451316.iam.gserviceaccount.com`. To rotate, create a new key in Google Cloud and `python3 -c "import json,sys;sys.stdout.write(json.dumps(json.load(open('KEY.json')),separators=(',',':')))" | railway variable set -s etl GOOGLE_SA_JSON --stdin`, then delete the old key.

## Read-only analytics access (`analytics_reader`)

Other people and AI agents read the marketing data through ONE login role, `analytics_reader`, that can `SELECT` from the views of the `analytics` schema and from nothing else.
The schema, its views and their `COMMENT`s come from migrations 013 (core views), 190 (views over the projection pacing feeds) and 191 (comments on the `proj_*` tables), which run on every web
boot / etl run like any other. The ROLE is not a migration (roles are cluster-wide and need a password): `etl/reader_role.py` creates it. Dictionary: `docs/DATA_SCHEMA.md`; agent quick start:
`docs/AGENT_DATA_GUIDE.md`.

```bash
cd ~/lonvera-tag-explorer
# create (first time): role + grants + a random password; the connection URL is written to the file, mode 600, outside the repo
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.reader_role create'
# optional: --out PATH (default ~/lonvera-tag-explorer-data/analytics_reader_url.txt, must be outside the repo), --sslmode MODE (default require)

# new password (someone left, a URL leaked): writes the file again and ends every session that used the old password
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.reader_role rotate'
# grants / settings / drift report (exit 1 on drift; prints no secret); `create` on an existing role re-applies the policy without touching the password
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.reader_role status'
# remove it (ends sessions, drops its privileges, drops the role)
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python -m etl.reader_role drop --yes'
```

The command prints only the file path and a success line. The password is never printed or logged and never reaches Postgres in clear text (the role is given a SCRAM-SHA-256 verifier). The URL host / port / database
come from `DATABASE_PUBLIC_URL` (the Railway TCP proxy; `postgres-ssl` accepts TLS, hence `sslmode=require`). Hand the file's content to a person through a password manager, never through chat, email or git.

**Expected grants** (what `status` checks, and `tests/api/test_reader_role.py` proves on a scratch cluster with real password authentication):

| Object | Reader's privilege |
|---|---|
| role attributes | LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS, NOINHERIT, CONNECTION LIMIT 5, member of no role |
| role defaults | `default_transaction_read_only = on`, `statement_timeout = 30s`, `idle_in_transaction_session_timeout = 60s`, `temp_file_limit = 512MB`, `search_path = analytics, pg_catalog` |
| database | CONNECT (explicit; PUBLIC also has CONNECT and TEMP, so the role can create temp tables inside its 512MB cap) |
| schema `analytics` | USAGE only (no CREATE) |
| every view in `analytics` | SELECT only; `ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES` means views added later by the migration role are readable without another grant |
| schema `public`, every table / view / sequence in it (`feed_oauth`, `proj_*`, `ad_daily`, ...) | nothing: `permission denied` |
| catalogs | `pg_catalog` / `information_schema` are readable by every role, so the NAMES of tables are visible; their contents, `pg_authid` and files are not |

Notes
* `default_transaction_read_only` is a convenience: a session can switch it off. What stops a write or a read of a public table is that the role has no privilege for it (views run with their owner's rights, so SELECT on a view needs
  nothing on the tables behind it). The tests run every forbidden statement twice, once with the default and once with it switched off.
* Postgres refuses to drop or retype a table column that an `analytics` view reads ("other objects depend on it"): a future migration that changes such a column must `DROP VIEW` / recreate the views in the same migration.
* A new table, view or column anywhere in `public` or `analytics` fails `tests/api/test_schema_docs.py` until it is described in `docs/schema/descriptions.py` (then `python scripts/gen_schema_doc.py --write` against a migrated database,
  and a new migration for the COMMENTs: `python scripts/gen_schema_doc.py --comments all --out migrations/NNN_schema_comments_<what>.sql`). Migrations that already ran are immutable.
* To expose more (say `proj_feed_nc_product`), add a view to `analytics` in a new migration and describe it; to expose less, drop its view. Nothing else grants access.
* Verify the documented schema against production without writing: `railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" .venv/bin/python scripts/gen_schema_doc.py --verify-live'` (opens a READ ONLY session).

## Failure and staleness alerts (hourly cron service `alerts`)

`python -m etl.alerts` checks every job and key table once an hour and posts to Slack (`SLACK_ALERT_WEBHOOK_URL`, set on the `alerts` service only). Start command `scripts/deploy/run_alerts.sh`, cron `0 * * * *`, `DATABASE_URL` by reference to `etl`; create it with `scripts/deploy/apply_alerts_service.py`. Checks, thresholds, setup and how to add or silence a check: `docs/ALERTS.md`.

## Live today (hourly cron service `live`)

Fills the "Meta spend today" and "NC revenue today" cards on Projections > Pacing without typing: once an hour it asks Upstack for the Pacific day so far (Meta spend, Google spend, total spend, new-customer revenue, new customers) and stores one snapshot row in `proj_live_today_snapshots` (migration 192, history kept 90 days). The page reads the newest row through `GET /api/projection/pacing/live-today`.

- Service: Railway cron service `live` on the same image as `etl` / `feeds` (`Dockerfile`), restart policy Never.
- **Start command: `scripts/deploy/run_live.sh`** (runs `python -m etl.migrate`, then `python -m etl.live_today`; the pull takes a few seconds). **Cron schedule: `0 * * * *`** (UTC, every hour on the hour).
- Variables (reference the `etl` service's values): `DATABASE_URL`, `UPSTACK_API_KEY`, `UPSTACK_PIXEL_ID`. No other secret is needed.
- Failure behaviour: on an Upstack error, an auth failure or an unusable response nothing is written (the last good snapshot stays), the error is logged without credentials and the run exits 1 so Railway shows Failed. The page marks a snapshot older than 150 minutes, or one from a previous Pacific day, as stale (a previous-day pull is never used to fill a card).
- Manual run / check: `scripts/deploy/run_live.sh --dry-run` (or `python -m etl.live_today --dry-run`) pulls and prints without touching the database; `python -m etl.feeds live` is the same entry point.
- Upstack lag: Upstack's current-hour spend and orders trail real time by roughly 30 to 60 minutes (measured 2026-11-05: at 15:05 Pacific the 14:00 hour held about half of a normal hour for both Meta spend and new-customer revenue, and nothing for 15:00; the figures did not change between pulls two minutes apart). Treat the numbers as a floor, so the projections lean low early in the day.

## Amazon month-to-date snapshot (`etl.amazon_mtd`, migration 194)

Amazon revenue reaches the feed with a lag, so the pacing page completes the month: the missing days after the last reported day are projected at the trailing 7-day average of the reported days and shown with an asterisk (rule, backtest and edge cases: `docs/AMAZON_MTD.md`). The completed number is computed nightly and stored in `proj_amazon_mtd_snapshots` (one row per Pacific day and month, history kept); the page reads it instead of computing on load.

- **Entry point:** `python -m etl.amazon_mtd [--dry-run] [--month YYYY-MM] [--lag N]` (alias `python -m etl.feeds amazon-mtd`). Reads `proj_feed_amazon`, so it must run after the Amazon load. Idempotent per Pacific day (the same day's row is replaced). Writes nothing on any error (single transaction) and exits 1; a stale Amazon feed is recorded (status `stale`, nothing projected), not an error.
- **`feeds` service (`scripts/deploy/run_feeds.sh`, cron `0 21 * * *` UTC):** `migrate`, `etl.feeds run`, **`etl.amazon_mtd`**, `etl.feeds verify`. The step sits right after the load so it sees the newest Amazon days.
- **`etl` service (`scripts/deploy/run_etl.sh`, cron `0 13 * * *` UTC):** `migrate`, `run_daily`, `upstack_daily`, `stage_moves`, `projection_daily`, **`etl.amazon_mtd`** (safety net; last because `projection_daily` loads the Amazon feed when it is sheet-fed).
- **Exit codes:** both scripts keep their pattern: every step always runs, and the first failure in priority order sets the exit code. `etl.amazon_mtd` is last in both, so a failure of this step alone exits with its own code (Railway shows Failed), and it never masks an earlier step's code. Its failure never leaves a wrong number on the page.
- **How the page uses it:** `GET /api/projection/pacing/amazon-mtd` and the `amazon_mtd` block of `/month` and `/snapshot` read the newest snapshot of the month when it is current: written within 1 Pacific day, same rule version, same data cutoff as now (yesterday) and not older than the last load of `proj_feed_amazon`. Otherwise they compute live and say so (`source: "computed live"`, `live_reason`). The page prints "Computed live" or "From the nightly snapshot". `?amazon=actuals` (the page's "Amazon reported days only" switch) turns the completion off.
- **Manual run:** `railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" python -m etl.amazon_mtd'`. Deploy order: web (migration 194 is applied by `etl.migrate` on any service start), etl, feeds. Until the first run the page computes live.
- **Compare each day's projection with the final month:** `select s.snapshot_day, s.projected_mtd, f.final, round((s.projected_mtd / f.final - 1) * 100, 1) as pct_error from proj_amazon_mtd_snapshots s join (select date_trunc('month', day)::date as month, sum(revenue) as final from proj_feed_amazon group by 1) f using (month) where s.month < date_trunc('month', current_date)::date order by s.snapshot_day;` (that compares to the month-to-date at snapshot time; for the end-of-month comparison use the last day's `actual_mtd`).

## Backup and restore (Postgres)

Railway volumes are not a backup. Postgres here is **18.x**; `pg_dump` must be version 18 or newer (the local `postgresql@16` refuses). Install once: `brew install postgresql@18`.

```bash
export PATH=/opt/homebrew/opt/postgresql@18/bin:$PATH
mkdir -p ~/lonvera-tag-explorer-data/backups
# dump (custom format, compressed)
railway run -s etl sh -c 'pg_dump "$DATABASE_PUBLIC_URL" -Fc --no-owner -f ~/lonvera-tag-explorer-data/backups/tagexp-$(date +%F).dump'
# restore into the production DB (destructive: drops and recreates objects)
railway run -s etl sh -c 'pg_restore --clean --if-exists --no-owner -d "$DATABASE_PUBLIC_URL" ~/lonvera-tag-explorer-data/backups/tagexp-YYYY-MM-DD.dump'
# restore into a local scratch DB to inspect
createdb tagexp_restore && pg_restore --no-owner -d tagexp_restore ~/lonvera-tag-explorer-data/backups/tagexp-YYYY-MM-DD.dump
```

Everything in the DB is re-derivable (tracker + Meta + sheet snapshot), so the cheapest recovery is also: create schema (`web` boot), re-run backfill, run `etl.run_daily`. Dump before any migration that drops or rewrites columns.

## Rollback

- **Code**: dashboard > service > Deployments > pick the last good one > Rollback (or `git checkout` the good commit and `railway up -s web -c`). Rolling back web does not undo migrations; migrations are additive (new columns/tables), so older code keeps working. Restore from a dump only if a migration damaged data.
- **Bad cron change**: `python3 scripts/deploy/apply_service_config.py` re-applies the known-good settings.
- **Pause the refresh**: clear Service > Settings > Cron Schedule on `etl` in the dashboard (or `railway down -s etl -y` to remove its deployment); `python3 scripts/deploy/apply_service_config.py` restores the schedule.

## Cost (Railway usage pricing checked 2026-11-03)

Rates: $20 per vCPU-month, $10 per GB-RAM-month, $0.15 per GB-month volume, $0.05 per GB egress. Hobby is $5/month and includes $5 of usage; Pro is $20 and includes $20. Estimate for this project (usage only): web (~0.15 GB RAM, ~0.02 vCPU average) $1.5-3; Postgres (~0.2-0.3 GB RAM, ~0.02 vCPU, <1 GB volume) $2-4; etl cron (one 3-10 minute run per day) under $0.20; egress under $0.50. **About $4-8 per month, realistic $5-6**, which fits inside one plan's included credit if the workspace has headroom, otherwise it is that much extra on the bill.

## Smoke-test record (2026-11-03)

A stub app (`/healthz` runs `select 1` on `DATABASE_URL`) and a stub `etl.run_daily` were deployed with this Dockerfile, `requirements.txt`, `scripts/deploy/*` to prove: image builds, gunicorn runs non-root, Railway healthcheck passes, public domain serves, private DB connection works, cron service fires on schedule, writes to the DB and exits. The stub is replaced by the first real `railway up`.
