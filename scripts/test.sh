#!/usr/bin/env bash
# Parallel test runner (pytest-xdist). The plain serial command still works unchanged:
#   PYTHONPATH=. python -m pytest tests -q -rf -p no:cacheprovider
#
#   scripts/test.sh [pytest args]     whole suite, one xdist worker per core (default; same as --full)
#   scripts/test.sh --full  [args]    whole suite
# Env: TEST_WORKERS (default 6; n|auto), PY (python; default .venv or ~/lonvera-tag-explorer/.venv), TEST_API_DB (base name; every worker adds its own suffix), TEST_STRICT=0 (let missing Postgres/Chromium skip)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
PY="${PY:-}"
if [ -z "$PY" ]; then
  for c in "$ROOT/.venv/bin/python" "$HOME/lonvera-tag-explorer/.venv/bin/python" python3; do [ -x "$(command -v "$c" || true)" ] && PY="$c" && break; done
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TEST_STRICT="${TEST_STRICT:-1}"      # a missing Postgres / Chromium FAILS tests here instead of skipping them (see tests/_xdist_env.py)
if [ "$TEST_STRICT" = "1" ] && ! pg_isready -q -h "${TEST_PG_HOST:-localhost}" 2>/dev/null; then
  echo "[test.sh] Postgres is not answering on localhost: start it, or run with TEST_STRICT=0 to let the database tests skip" >&2; exit 2
fi
export TEST_API_DB="${TEST_API_DB:-tagexp_apitest_par}"
# Postgres over its unix socket when there is one: no TCP ports used up (a parallel run opens thousands of short connections and macOS runs out of ephemeral ports)
if [ -z "${TEST_PG_BASE:-}" ] && [ -S /tmp/.s.PGSQL.5432 ]; then export TEST_PG_BASE="postgresql://%2Ftmp"; fi
[ "${1:-}" = "--full" ] && shift
# capped at 6: Postgres allows 100 connections in total and other sessions share the server; TEST_WORKERS=auto uses every core
WORKERS="${TEST_WORKERS:-$(c=$(sysctl -n hw.ncpu 2>/dev/null || nproc); [ "$c" -gt 6 ] && c=6; echo "$c")}"
echo "[test.sh] workers=$WORKERS cores=$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
TARGET=(tests)
for a in "$@"; do case "$a" in tests*|./tests*) TARGET=();; esac; done      # explicit test paths replace the default `tests`
# --rootdir: tests/ui and tests/projection/ui each hold a pytest.ini, so explicit file paths from there would move the rootdir into that directory and turn the OTHER directory's
# conftest fixtures into global ones (a tests/ui test then runs with the projection UI's autouse _reset and times out). Pinning the rootdir keeps every conftest scoped to its folder.
exec "$PY" -m pytest --rootdir "$ROOT" ${TARGET[@]+"${TARGET[@]}"} -q -rf -p no:cacheprovider -p tests._xdist_env -n "$WORKERS" --dist loadfile "$@"
