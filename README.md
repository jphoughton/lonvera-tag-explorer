# Lonvera Tag Explorer

Railway web app + Postgres: drill product → market cluster → ICP → pain point → ad family → ad, with Meta spend / purchases / revenue served from Postgres. Behind a single-password login. Refreshed daily.

- `docs/CONTRACT.md` build contract (metric rules, HTTP contract, ETL jobs)
- `docs/DEPLOY.md` deploy, daily refresh, secrets, backup/restore, rollback, cost

## Layout

```
app/            Flask API (wsgi:app) + app/static SPA
etl/            python -m etl.<job>  (migrate, backfill_sheet, tracker_sync, meta_pull, build_metrics, run_daily, ...)
migrations/     SQL migrations applied by etl.migrate
scripts/deploy/ start_web.sh (migrate, gunicorn), run_etl.sh (migrate, run_daily), apply_service_config.py (Railway settings)
Dockerfile      one image for web and etl (python:3.12-slim, non-root)
```

## Run locally

```bash
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
createdb tagexp_dev
export DATABASE_URL=postgresql://localhost/tagexp_dev SECRET_KEY=dev APP_PASSWORD=dev
.venv/bin/python -m etl.migrate
.venv/bin/gunicorn wsgi:app --bind 127.0.0.1:8080 --workers 2 --threads 8
```

Tests: `.venv/bin/pytest tests/etl tests/api` (UI tests in `tests/ui` need `playwright install chromium`).

## Production

Railway project `lonvera-tag-explorer`: services `web` (public, healthcheck `/healthz`), `etl` (cron `0 13 * * *` UTC, `python -m etl.run_daily`), `Postgres`. Deploy: `railway up -s web -c` / `railway up -s etl -c`. Secrets are Railway variables only (names in `docs/DEPLOY.md`).
