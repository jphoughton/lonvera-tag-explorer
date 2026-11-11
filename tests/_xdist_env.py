"""pytest plugin (loaded with `-p tests._xdist_env` by scripts/test.sh): makes the suite safe to run under pytest-xdist (scripts/test.sh).

Serial runs (plain `pytest tests`) are untouched: nothing here does anything unless PYTEST_XDIST_WORKER is set.

Under xdist every worker (gw0, gw1, ...) gets its own throwaway Postgres databases and its own mock-server ports, by suffixing the names
that the fixtures already read from the environment (they all have defaults, listed below) before any test module is imported.
"""
from __future__ import annotations

import os

_WORKER = os.environ.get("PYTEST_XDIST_WORKER")          # "gw0", "gw1", ... ; unset in serial runs and in the xdist controller

# scripts/test.sh sets TEST_STRICT=1: it expects Postgres and Chromium, so a fixture that skips for "not available" is a FAILURE there (a saturated or
# broken machine must never show up as a green run full of skips). A plain `pytest tests` keeps the friendly skip.
STRICT = os.environ.get("TEST_STRICT") == "1"
ENVIRONMENT_SKIPS = ("Postgres not available", "Chromium not available", "could not prepare the page database", "could not start a scratch Postgres", "sheet snapshot not available")

# env var -> default database name used by the fixture that reads it (kept in step with the conftest files)
_DB_VARS = {
    "TEST_API_DB": "tagexp_apitest",
    "TEST_PROJ_DRIVERS_DB": "tagexp_proj_drivers_test",
    "TEST_PROJ_FINANCING_DB": "tagexp_proj_financing",
    "TEST_PROJ_CORE_DB": "tagexp_proj_core",
    "TEST_PROJ_SUPPLY_DB": "tagexp_proj_inventory_supply_test",
    "TEST_PROJ_MODELER_DB": "tagexp_proj_modeler_test",
    "TEST_PROJ_INVENTORY_DEMAND_DB": "tagexp_proj_inventory_demand_test",
    "TEST_PROJ_PACING_DB": "tagexp_proj_pacing_t",
    "TEST_PROJ_COHORTS_DB": "tagexp_proj_cohorts_test",
    "TEST_PROJ_COHORTS_PAGE_DB": "tagexp_proj_cohorts_page",
    "TEST_PROJ_FEEDS_DB": "tagexp_proj_feeds",
    "TEST_PROJ_FEEDS_WIRED_DB": "tagexp_proj_feeds_wired",
    "TEST_PROJ_VARIABLES_DB": "tagexp_proj_variables_test",
    "TEST_PROJ_PNL_DB": "tagexp_proj_pnl_test",
}

if _WORKER:
    _suffix = f"_{os.getppid()}_{_WORKER}"           # parent pid = the xdist controller: a second run (or another session) never shares a name
    os.environ["TEST_WORKER_SUFFIX"] = _suffix
    for _var, _default in _DB_VARS.items():
        os.environ[_var] = os.environ.get(_var, _default) + _suffix
    os.environ["TEST_DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", os.environ.get("TEST_PG_BASE", "postgresql://localhost") + "/tagexp_test") + _suffix      # etl tests


# ---- database housekeeping for a worker: the etl tests expect their database to exist already, and every worker removes its own at the end ----
def _admin_url() -> str:
    return os.environ.get("TEST_PG_BASE", "postgresql://localhost") + "/postgres"


def pytest_configure(config):
    if not _WORKER:
        return
    import psycopg
    name = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[1]
    if _WORKER == "gw0":
        _reap_dead_runs(_admin_url())
    try:
        with psycopg.connect(_admin_url(), autocommit=True) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                c.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
        if STRICT:
            raise       # scripts/test.sh expects Postgres: say so now instead of letting every fixture skip its tests
        # otherwise no local Postgres: the fixtures skip their tests themselves


def _reap_dead_runs(admin_url: str) -> None:
    """drop the databases of parallel runs that are gone (killed or crashed): their names end in _<controller pid>_gw<N> and that pid no longer exists"""
    import re

    import psycopg
    try:
        with psycopg.connect(admin_url, autocommit=True) as c:
            names = [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname ~ '_[0-9]+_gw[0-9]+$'").fetchall()]
            for name in names:
                pid = int(re.search(r"_([0-9]+)_gw[0-9]+$", name).group(1))
                try:
                    os.kill(pid, 0)
                    continue                                            # that run is still alive (this one, or another session's)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    continue
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except psycopg.Error:
        pass


def pytest_unconfigure(config):
    if not _WORKER:
        return
    import psycopg
    try:
        with psycopg.connect(_admin_url(), autocommit=True) as c:
            for (name,) in c.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (f"%{_suffix}%",)).fetchall():
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except psycopg.OperationalError:
        pass


# ---- connections: Postgres allows 100 in total on this machine, shared with every other session ----
import gc  # noqa: E402

import pytest  # noqa: E402


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if STRICT and rep.skipped and any(m in str(rep.longrepr) for m in ENVIRONMENT_SKIPS):
        reason = str(rep.longrepr[-1]) if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
        rep.outcome = "failed"
        rep.longrepr = f"TEST_STRICT: this test was skipped because the environment is missing something scripts/test.sh requires: {reason}"



@pytest.fixture(scope="module", autouse=True)
def _release_connections_after_module():
    yield
    if _WORKER:
        try:
            from app.projection import rwdb
            rwdb.close_all()
        except Exception:
            pass
        gc.collect()                  # connections held only by finished fixtures' objects close here instead of at the end of the worker


# ---- scheduling: whole files stay together (their module-scoped databases and mock servers hold state), except the few huge files that are split in
# contiguous chunks so that one worker does not carry the whole run. (pytest-xdist 3.8's own `loadgroup` does not work with pytest 9.2: it sets the
# private Item._nodeid, which pytest 9 no longer reads - so this is a LoadFileScheduling with a finer scope.) ----
SPLIT_FILES = {
    "tests/ui/test_ui.py": 3,
    "tests/projection/variables/test_variables_access.py": 3,
    "tests/projection/modeler/test_modeler_page.py": 2,
}


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_make_scheduler(config, log):
    if config.getvalue("dist") != "loadfile":
        return None
    from xdist.scheduler import LoadFileScheduling

    class SplitFileScheduling(LoadFileScheduling):
        _chunk_of: dict | None = None

        def _split_scope(self, nodeid: str) -> str:
            path = nodeid.split("::", 1)[0]
            k = SPLIT_FILES.get(path, 1)
            if k == 1 or not self.collection:
                return path
            if self._chunk_of is None:
                per_file: dict[str, list] = {}
                for nid in self.collection:
                    per_file.setdefault(nid.split("::", 1)[0], []).append(nid)
                self._chunk_of = {nid: f"{f}#{i * SPLIT_FILES.get(f, 1) // len(ids)}" for f, ids in per_file.items() for i, nid in enumerate(ids)}
            return self._chunk_of.get(nid := nodeid, path)

    return SplitFileScheduling(config, log)


# ---- shared resources: Postgres allows 100 connections for every worker AND every other session's test run, and the macOS ephemeral port range is shared too. A refused
# connect is a "busy machine", not a failed test: it is retried with a short back-off for up to 10 minutes (and says so on stderr every 30 s), then it fails loudly. ----
BUSY_ERRORS = ("too many clients", "Can't assign requested address")
BUSY_WAIT_MAX = 600


def _retry_when_the_machine_is_busy():
    import sys
    import time

    import psycopg

    orig = psycopg.Connection.connect.__func__

    def connect(cls, *args, **kwargs):
        delay, waited, told = 0.2, 0.0, 0.0
        while True:
            try:
                return orig(cls, *args, **kwargs)
            except psycopg.OperationalError as e:
                msg = str(e)
                if not any(b in msg for b in BUSY_ERRORS) or waited > BUSY_WAIT_MAX:
                    raise
                if waited - told >= 30:
                    told = waited
                    print(f"[{_WORKER}] waiting for a free Postgres connection / local port ({waited:.0f}s): {msg.splitlines()[0][:120]}", file=sys.stderr, flush=True)
                time.sleep(delay)
                waited += delay
                delay = min(delay * 1.5, 2.0)

    psycopg.Connection.connect = classmethod(connect)


if _WORKER:
    _retry_when_the_machine_is_busy()

