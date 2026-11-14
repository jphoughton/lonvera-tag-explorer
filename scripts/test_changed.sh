#!/usr/bin/env bash
# Changed-only tests: exactly `scripts/test.sh --fast`. Runs the tests the diff against origin/build/v1 (plus uncommitted and untracked files) can affect, in
# parallel, and prints which rule picked which tests. Falls back to the FULL suite for a migration, conftest, hub module, harness file or anything it cannot place
# (scripts/select_tests.py). Extra args go to pytest; --base REF changes the comparison point. Set TEST_PG=persistent for the long-lived test Postgres.
exec "$(dirname "${BASH_SOURCE[0]}")/test.sh" --fast "$@"
