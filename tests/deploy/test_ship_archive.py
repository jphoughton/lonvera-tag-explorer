"""scripts/deploy/ship.sh must ship docs/ (the hosted /mcp brain reads docs/schema.json and docs/app_map.json at runtime) and must refuse, before any upload, an archive without them.

Runs `ship.sh --dry-run` (no railway, no network needed) against throwaway git repos holding a copy of the script, plus once against this checkout's HEAD.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHIP = REPO / "scripts" / "deploy" / "ship.sh"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


def _mini_repo(tmp_path, files):
    repo = tmp_path / "repo"
    (repo / "scripts" / "deploy").mkdir(parents=True)
    shutil.copy(SHIP, repo / "scripts" / "deploy" / "ship.sh")
    (repo / "Dockerfile").write_text("FROM scratch\n")
    for rel in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "t.py").write_text("x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "update-ref", "refs/remotes/origin/build/v1", "HEAD")
    return repo


def _dry_run(repo, tmp_path):
    env = dict(os.environ, SHIP_STATE=str(tmp_path / "shipped.json"))
    return subprocess.run(["bash", str(repo / "scripts" / "deploy" / "ship.sh"), "--dry-run", "--services", "web"], cwd=repo, env=env, capture_output=True, text=True)


def test_dry_run_passes_guard_when_docs_present(tmp_path):
    repo = _mini_repo(tmp_path, ["docs/schema.json", "docs/app_map.json", "docs/x.md"])
    r = _dry_run(repo, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "archive guard ok" in r.stdout
    assert "3 files under docs/" in r.stdout


def test_dry_run_fails_before_upload_when_app_map_missing(tmp_path):
    repo = _mini_repo(tmp_path, ["docs/schema.json", "docs/x.md"])
    r = _dry_run(repo, tmp_path)
    assert r.returncode != 0
    assert "lacks docs/app_map.json" in r.stderr
    assert "would run these" not in r.stdout


def test_dry_run_fails_when_schema_missing(tmp_path):
    repo = _mini_repo(tmp_path, ["docs/app_map.json"])
    r = _dry_run(repo, tmp_path)
    assert r.returncode != 0
    assert "lacks docs/schema.json" in r.stderr


def test_this_checkout_archive_has_brain_docs(tmp_path):
    """The real tree: both files are committed and ship.sh does not exclude docs."""
    env = dict(os.environ, SHIP_STATE=str(tmp_path / "shipped.json"))
    r = subprocess.run(["bash", str(SHIP), "--dry-run", "--services", "web", "--ref", "HEAD", "--force"], cwd=REPO, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "archive guard ok" in r.stdout
    assert "(exclude)docs" not in SHIP.read_text()
    ignore = (REPO / ".dockerignore").read_text().split()
    assert "docs" not in ignore and "docs/" not in ignore
