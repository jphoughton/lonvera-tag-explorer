"""scripts/deploy/run_tagging.sh (the daily `tagging` cron service: transcripts every day, competitor / own ad tagging on Sundays) and its Dockerfile flavor."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "run_tagging.sh"


@pytest.fixture()
def call(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    py = bin_dir / "python"
    py.write_text('#!/bin/sh\necho "$*" >> "$STUB_LOG"\ncase "$2" in\n  etl.transcribe_ads) exit "${TR_RC:-0}";;\n  etl.migrate) exit "${MIGRATE_RC:-0}";;\n  etl.competitor_tags) exit "${CT_RC:-0}";;\n  etl.own_ad_tags) exit "${OWN_RC:-0}";;\n  faster_whisper) exit "${WHISPER_RC:-0}";;\n  *) case "$1" in -c) exit "${WHISPER_RC:-0}";; esac; exit 0;;\nesac\n')
    for name in ("yt-dlp", "ffmpeg", "claude"):
        e = bin_dir / name
        e.write_text("#!/bin/sh\nexit 0\n")
        e.chmod(e.stat().st_mode | stat.S_IEXEC)
    py.chmod(py.stat().st_mode | stat.S_IEXEC)

    def run(*args, path_only=False, **env):
        base = f"{bin_dir}:/usr/bin:/bin" if path_only else f"{bin_dir}:{os.environ['PATH']}"
        e = {**os.environ, "PATH": base, "STUB_LOG": str(log), **{k: str(v) for k, v in env.items()}}
        p = subprocess.run(["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, env=e, cwd=str(tmp_path))
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return p, calls, bin_dir
    return run


def test_migrate_then_transcripts_then_both_tagging_steps_on_a_tag_day(call):
    p, calls, _ = call(TAGGING_FORCE=1)
    assert p.returncode == 0
    assert calls[0] == "-m etl.migrate"
    assert calls[-3:] == ["-m etl.transcribe_ads", "-m etl.competitor_tags --limit 80", "-m etl.own_ad_tags --no-import --limit 150"]   # transcripts first: the tagger reads them


def test_other_days_only_transcribe(call):
    p, calls, bin_dir = call()
    day = subprocess.run(["date", "-u", "+%u"], capture_output=True, text=True).stdout.strip()
    if day != "7":                                                     # on a Sunday the script runs the tagging steps whatever TAGGING_FORCE says
        assert p.returncode == 0 and calls[-1] == "-m etl.transcribe_ads" and not any("competitor_tags" in c or "own_ad_tags" in c for c in calls)


def test_a_non_tag_day_does_not_need_yt_dlp_or_claude(call):
    day = subprocess.run(["date", "-u", "+%u"], capture_output=True, text=True).stdout.strip()
    if day != "7":
        p, calls, bin_dir = call()
        (bin_dir / "yt-dlp").unlink()
        (bin_dir / "claude").unlink()
        p, calls, _ = call(path_only=True)
        assert p.returncode == 0 and calls[-1] == "-m etl.transcribe_ads"


def test_extra_args_go_to_every_module(call):
    _, calls, _ = call("--dry-run", TAGGING_FORCE=1)
    assert calls[-3:] == ["-m etl.transcribe_ads --dry-run", "-m etl.competitor_tags --limit 80 --dry-run", "-m etl.own_ad_tags --no-import --limit 150 --dry-run"]


def test_failure_of_first_step_still_runs_second_and_exits_nonzero(call):
    p, calls, _ = call(CT_RC=3, TAGGING_FORCE=1)
    assert p.returncode == 3 and calls[-1].startswith("-m etl.own_ad_tags")
    assert "etl.competitor_tags failed" in p.stderr


def test_second_step_failure_exits_nonzero(call):
    p, _, _ = call(OWN_RC=5, TAGGING_FORCE=1)
    assert p.returncode == 5 and "etl.own_ad_tags failed" in p.stderr


def test_transcribe_failure_still_runs_the_tagging_steps_and_exits_nonzero(call):
    p, calls, _ = call(TR_RC=4, TAGGING_FORCE=1)
    assert p.returncode == 4 and calls[-1].startswith("-m etl.own_ad_tags") and "etl.transcribe_ads failed" in p.stderr


def test_missing_tool_fails_preflight_and_runs_nothing(call, tmp_path):
    p, calls, bin_dir = call(WHISPER_RC=1)
    assert p.returncode == 97 and "preflight failed" in p.stderr
    assert not any("transcribe_ads" in c or "competitor_tags" in c or "own_ad_tags" in c for c in calls)
    (bin_dir / "ffmpeg").unlink()
    p, calls, _ = call(path_only=True)
    assert p.returncode == 97 and "ffmpeg missing" in p.stderr


def test_local_login_switches_are_unset(call):
    assert "unset OWN_TAGS_LOCAL_CLAUDE_LOGIN LP_LOCAL_CLAUDE_LOGIN" in SCRIPT.read_text()


def test_dockerfile_has_isolated_tagging_flavor():
    d = (ROOT / "Dockerfile").read_text()
    assert "FROM heavy AS tagging" in d and "requirements-whisper.txt" in d and "HF_HUB_OFFLINE=1" in d
    jobs = d[d.index("FROM heavy AS jobs"):d.index("# ---- tagging")]
    assert "whisper" not in jobs and "yt-dlp" not in jobs          # the nightly image stays slim


def test_mac_routine_is_gone():
    assert not (ROOT / "scripts" / "mac").exists()
