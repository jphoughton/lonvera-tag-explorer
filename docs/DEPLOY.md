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
