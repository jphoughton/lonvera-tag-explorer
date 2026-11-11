#!/usr/bin/env bash
# Parallel test runner (pytest-xdist). The plain serial command still works unchanged:
#   PYTHONPATH=. python -m pytest tests -q -rf -p no:cacheprovider
#
#   scripts/test.sh [pytest args]     whole suite, one xdist worker per core (default; same as --full)
#   scripts/test.sh --full  [args]    whole suite
# Env: TEST_WORKERS (default: all cores), PY (python, default: .venv or ~/lonvera-tag-explorer/.venv), TEST_API_DB / UI_MOCK_PORT (base names; each worker adds a suffix)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
PY="${PY:-}"
if [ -z "$PY" ]; then
  for c in "$ROOT/.venv/bin/python" "$HOME/lonvera-tag-explorer/.venv/bin/python" python3; do [ -x "$(command -v "$c" || true)" ] && PY="$c" && break; done
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TEST_API_DB="${TEST_API_DB:-tagexp_apitest_par}"
# Postgres over its unix socket when there is one: no TCP ports used up (a parallel run opens thousands of short connections and macOS runs out of ephemeral ports)
if [ -z "${TEST_PG_BASE:-}" ] && [ -S /tmp/.s.PGSQL.5432 ]; then export TEST_PG_BASE="postgresql://%2Ftmp"; fi
export UI_MOCK_PORT="${UI_MOCK_PORT:-5099}"
[ "${1:-}" = "--full" ] && shift
# capped at 6: Postgres allows 100 connections in total and other sessions share the server; TEST_WORKERS=auto uses every core
WORKERS="${TEST_WORKERS:-$(c=$(sysctl -n hw.ncpu 2>/dev/null || nproc); [ "$c" -gt 6 ] && c=6; echo "$c")}"
echo "[test.sh] workers=$WORKERS cores=$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
TARGET=(tests)
for a in "$@"; do case "$a" in tests*|./tests*) TARGET=();; esac; done      # explicit test paths replace the default `tests`
exec "$PY" -m pytest ${TARGET[@]+"${TARGET[@]}"} -q -rf -p no:cacheprovider -p tests._xdist_env -n "$WORKERS" --dist loadfile "$@"
