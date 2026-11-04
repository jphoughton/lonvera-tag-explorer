#!/bin/sh
# Web service entrypoint: apply DB migrations, then serve.
# Migrations are idempotent (etl.migrate tracks schema_migrations), so every deploy/restart is safe.
set -eu

: "${PORT:=8080}"
: "${WEB_WORKERS:=2}"
: "${WEB_THREADS:=8}"

echo "[start_web] running migrations"
python -m etl.migrate

echo "[start_web] starting gunicorn on 0.0.0.0:${PORT} (${WEB_WORKERS} workers x ${WEB_THREADS} threads)"
exec gunicorn wsgi:app \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WEB_WORKERS}" \
  --threads "${WEB_THREADS}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
