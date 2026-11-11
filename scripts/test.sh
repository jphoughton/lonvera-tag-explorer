#!/usr/bin/env bash
# Parallel test runner (pytest-xdist). The plain serial command still works unchanged:
#   PYTHONPATH=. python -m pytest tests -q -rf -p no:cacheprovider
#
#   scripts/test.sh [pytest args]     whole suite, up to 8 xdist workers, a private throwaway Postgres (default; same as --full)
#   scripts/test.sh --full  [args]    whole suite
#
# Env: TEST_WORKERS (default 8; n|auto), PY (python; default .venv or ~/lonvera-tag-explorer/.venv),
#      TEST_PRIVATE_PG=0 (use the shared local Postgres on :5432 instead of a private one), TEST_PG_BASE (use that Postgres, e.g. postgresql://localhost),
#      TEST_API_DB (base name for databases; every worker adds its own suffix), TEST_STRICT=0 (let a missing Postgres / Chromium skip instead of fail)
#
# Why a private Postgres: the shared server allows 100 connections for every session on this machine, and a parallel run alone needs 50-70, so a run that
# overlapped another session's suite died with "too many clients". A private server (own data directory in /tmp, unix socket only, 600 connections,
# fsync off: a throwaway cluster) cannot meet anybody else's connections or database names, and creates the per-worker databases faster. It is removed on exit
# (also after Ctrl-C), and the leftovers of an earlier run that was killed are removed at the next start.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
PY="${PY:-}"
if [ -z "$PY" ]; then
  for c in "$ROOT/.venv/bin/python" "$HOME/lonvera-tag-explorer/.venv/bin/python" python3; do [ -x "$(command -v "$c" || true)" ] && PY="$c" && break; done
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TEST_STRICT="${TEST_STRICT:-1}"      # a missing Postgres / Chromium FAILS tests here instead of skipping them (see tests/_xdist_env.py)
export TEST_API_DB="${TEST_API_DB:-tagexp_apitest_par}"
export TEST_TIMEOUT_FACTOR="${TEST_TIMEOUT_FACTOR:-2}"      # Playwright waits x2 on this busy machine (tests/_xdist_env.py); 1 = as written

PGDIR=""
cleanup() {
  if [ -n "$PGDIR" ]; then
    pg_ctl -D "$PGDIR/data" -m immediate stop >/dev/null 2>&1
    rm -rf "$PGDIR"
    PGDIR=""
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

start_private_pg() {
  local d
  for d in /tmp/lvtestpg.*; do                                     # leftovers of a killed run: owner process gone
    [ -f "$d/owner.pid" ] || continue
    kill -0 "$(cat "$d/owner.pid")" 2>/dev/null && continue
    pg_ctl -D "$d/data" -m immediate stop >/dev/null 2>&1; rm -rf "$d"
  done
  PGDIR="$(mktemp -d /tmp/lvtestpg.XXXXXX)"
  echo $$ > "$PGDIR/owner.pid"
  if ! LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 initdb -D "$PGDIR/data" -U "$(id -un)" -E UTF8 --locale=en_US.UTF-8 -A trust -N >"$PGDIR/initdb.log" 2>&1 \
     || ! LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 pg_ctl -D "$PGDIR/data" -l "$PGDIR/server.log" -w -t 60 \
          -o "-p 5432 -k $PGDIR -c listen_addresses= -c max_connections=600 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c shared_buffers=256MB -c max_locks_per_transaction=128" start >/dev/null 2>&1; then
    echo "[test.sh] could not start a private Postgres (see $PGDIR/initdb.log, $PGDIR/server.log); use TEST_PRIVATE_PG=0 for the shared one" >&2
    tail -5 "$PGDIR/server.log" 2>/dev/null >&2
    exit 2
  fi
  export TEST_PG_BASE="postgresql://%2F$(echo "$PGDIR" | sed 's|/|%2F|g;s|^%2F||')"
  echo "[test.sh] private Postgres up ($PGDIR, unix socket only)"
}

if [ -z "${TEST_PG_BASE:-}" ]; then
  if [ "${TEST_PRIVATE_PG:-1}" = "1" ]; then
    start_private_pg
  elif [ -S /tmp/.s.PGSQL.5432 ]; then
    export TEST_PG_BASE="postgresql://%2Ftmp"                          # the shared server over its unix socket: no TCP ports used up
  fi
fi
if [ "$TEST_STRICT" = "1" ] && ! pg_isready -q -h "$(echo "${TEST_PG_BASE:-postgresql://localhost}" | sed -E 's|^postgresql://||;s|%2F|/|g')" 2>/dev/null; then
  echo "[test.sh] Postgres is not answering (${TEST_PG_BASE:-localhost}): start it, or run with TEST_STRICT=0 to let the database tests skip" >&2; exit 2
fi

[ "${1:-}" = "--full" ] && shift
# capped at 8: measured on this 10-core Mac (4 efficiency cores), whole suite 159 s with 4 workers, 143 s with 5, 134 s with 6, 125 s with 8 and with 10; TEST_WORKERS=auto uses every core
WORKERS="${TEST_WORKERS:-$(c=$(sysctl -n hw.ncpu 2>/dev/null || nproc); [ "$c" -gt 8 ] && c=8; echo "$c")}"
echo "[test.sh] workers=$WORKERS cores=$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
TARGET=(tests)
for a in "$@"; do case "$a" in tests*|./tests*) TARGET=();; esac; done      # explicit test paths replace the default `tests`
# --rootdir: tests/ui and tests/projection/ui each hold a pytest.ini, so explicit file paths from there would move the rootdir into that directory and turn the OTHER directory's
# conftest fixtures into global ones (a tests/ui test then runs with the projection UI's autouse _reset and times out). Pinning the rootdir keeps every conftest scoped to its folder.
"$PY" -m pytest --rootdir "$ROOT" ${TARGET[@]+"${TARGET[@]}"} -q -rf -p no:cacheprovider -p tests._xdist_env -n "$WORKERS" --dist loadfile "$@" &
CHILD=$!
trap 'kill -TERM $CHILD 2>/dev/null; wait $CHILD 2>/dev/null; exit 130' INT TERM
wait $CHILD
exit $?
