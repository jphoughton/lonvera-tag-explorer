"""scripts/deploy/run_breakdowns.sh (the dedicated `breakdowns` cron service) and its separation from the main nightly script."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "run_breakdowns.sh"


@pytest.fixture()
def call(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    exe = bin_dir / "python"
    exe.write_text('#!/bin/sh\necho "$*" >> "$STUB_LOG"\ncase "$2" in\n  etl.migrate) exit "${MIGRATE_RC:-0}";;\n  *) exit "${BD_RC:-0}";;\nesac\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def run(*args, **env):
        e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log), **{k: str(v) for k, v in env.items()}}
        p = subprocess.run(["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, env=e, cwd=str(tmp_path))
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return p, calls
    return run


def test_migrate_then_auto_mode(call):
    p, calls = call()
    assert p.returncode == 0 and calls == ["-m etl.migrate", "-m etl.meta_breakdowns --auto"]


def test_extra_arguments_are_passed_through(call):
    _, calls = call("--max-minutes", "60")
    assert calls[-1] == "-m etl.meta_breakdowns --auto --max-minutes 60"


def test_failed_migration_stops_and_fails(call):
    p, calls = call(MIGRATE_RC=3)
    assert p.returncode == 3 and calls == ["-m etl.migrate"]


def test_the_pull_exit_code_is_the_services_exit_code(call):
    assert call(BD_RC=1)[0].returncode == 1


def test_the_main_nightly_script_no_longer_runs_breakdowns():
    assert "meta_breakdowns" not in (ROOT / "scripts" / "deploy" / "run_etl.sh").read_text()
