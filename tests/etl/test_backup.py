"""etl.backup: retention, count comparison, and the run flow (dump -> Drive -> re-download -> restore -> compare -> prune) with a fake Drive and a fake pg_dump /
pg_restore. No network, no real pg_dump. The live database is the test database; the 'scratch server' fake points at the same local server and a throwaway
database name, so CREATE / DROP DATABASE are exercised for real."""
import datetime as dt
import hashlib
import os
import subprocess

import psycopg
import pytest

from etl import backup, db as db_mod

NOW = dt.datetime(2026, 11, 10, 8, 30, tzinfo=dt.timezone.utc)


def nm(days_ago, hour=8):
    t = (NOW - dt.timedelta(days=days_ago)).replace(hour=hour, minute=30, second=0)
    return backup.file_name(t)


def files(*names):
    return [{"id": f"id-{n}", "name": n} for n in names]


# ------------------------------------------------------------------ retention (pure)
def test_prune_keeps_seven_days_of_dailies():
    fl = files(*[nm(k) for k in range(12)])
    keep, delete = backup.select_prune(fl, NOW, 7)
    assert len(keep) == 7 and {f["name"] for f in keep} == {nm(k) for k in range(7)}
    assert {f["name"] for f in delete} == {nm(k) for k in range(7, 12)}


def test_prune_never_goes_below_three_whatever_the_age():
    fl = files(*[nm(k) for k in (30, 40, 50, 60, 70)])
    keep, delete = backup.select_prune(fl, NOW, 7)
    assert [f["name"] for f in keep] == [nm(30), nm(40), nm(50)] and len(delete) == 2


def test_prune_ignores_foreign_files_and_unverified_dumps():
    fl = files(nm(1), nm(20), "UNVERIFIED-" + nm(30), "notes.txt", "lonvera-tag-explorer_bad.dump", nm(25))
    keep, delete = backup.select_prune(fl, NOW, 7, keep_min=1)
    touched = {f["name"] for f in keep + delete}
    assert touched == {nm(1), nm(20), nm(25)} and {f["name"] for f in delete} == {nm(20), nm(25)}


def test_unverified_copies_expire_with_the_prune():
    fl = files(nm(1), "UNVERIFIED-" + nm(2), "UNVERIFIED-" + nm(9), "UNVERIFIED-weird")
    assert [f["name"] for f in backup.select_unverified(fl, NOW, 7)] == ["UNVERIFIED-" + nm(9)]


def test_compare_counts_window_and_missing():
    before = {"a": 1000, "b": 10, "c": None, "d": 5}
    after = {"a": 1010, "b": 10, "c": None, "d": 5}
    assert backup.compare_counts(before, after, {"a": 1005, "b": 10, "d": 5}) == []
    bad = backup.compare_counts(before, after, {"a": 900, "b": 10})
    assert any(x.startswith("a:") for x in bad) and any("d: missing" in x for x in bad)
    assert backup.compare_counts({"x": 100000}, {"x": 100000}, {"x": 101500}) == []          # inside 2%
    assert backup.compare_counts({"x": 100000}, {"x": 100000}, {"x": 110000})                # outside


# ------------------------------------------------------------------ fakes
class FakeDrive:
    def __init__(self, existing=(), fail_upload=False, corrupt_download=False, bad_md5=False):
        self.files = {n: {"id": f"id-{n}", "name": n, "data": b"old"} for n in existing}
        self.fail_upload, self.corrupt_download, self.bad_md5 = fail_upload, corrupt_download, bad_md5
        self.deleted, self.renamed = [], []

    def ensure_folder(self, parent, name=backup.FOLDER_NAME):
        return "folder-1"

    def upload(self, path, name, folder_id, chunk_mb=16):
        if self.fail_upload:
            raise RuntimeError("storageQuotaExceeded")
        data = open(path, "rb").read()
        self.files[name] = {"id": f"id-{name}", "name": name, "data": data}
        return {"id": f"id-{name}", "name": name, "size": str(len(data)), "md5Checksum": "0" * 32 if self.bad_md5 else hashlib.md5(data).hexdigest()}

    def download(self, file_id, dest, chunk_mb=16):
        f = next(x for x in self.files.values() if x["id"] == file_id)
        open(dest, "wb").write(f["data"] + (b"x" if self.corrupt_download else b""))

    def list_files(self, folder_id):
        return [{"id": f["id"], "name": f["name"]} for f in self.files.values()]

    def rename(self, file_id, name):
        f = next(k for k, v in self.files.items() if v["id"] == file_id)
        self.files[name] = self.files.pop(f)
        self.files[name]["name"] = name
        self.renamed.append(name)

    def delete(self, file_id):
        k = next(k for k, v in self.files.items() if v["id"] == file_id)
        self.deleted.append(self.files.pop(k)["name"])


class FakeScratch:
    """Pretends to be the scratch server: same local server as the test database, throwaway database names."""

    def __init__(self, base_dir=None): self.base = base_dir
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def url(self, dbname="postgres"):
        return os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname


def make_runner(restore_rc=0, dump_rc=0, dump_major="18.6"):
    calls = []

    def runner(cmd, env=None, **kw):
        calls.append(cmd)
        tool = os.path.basename(cmd[0])
        if tool == "pg_dump" and "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, f"pg_dump (PostgreSQL) {dump_major}\n", "")
        if tool == "pg_dump":
            if dump_rc == 0:
                open(cmd[cmd.index("-f") + 1], "wb").write(b"PGDMP" + os.urandom(2048))
            return subprocess.CompletedProcess(cmd, dump_rc, "", "pg_dump: boom" if dump_rc else "")
        if tool == "pg_restore":
            return subprocess.CompletedProcess(cmd, restore_rc, "", "pg_restore: boom" if restore_rc else "")
        raise AssertionError(cmd)
    runner.calls = calls
    return runner


@pytest.fixture
def env(conn, dburl, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", dburl)
    monkeypatch.setattr(backup, "_tool", lambda n: "/x/" + n)
    live = {"ad_metrics": 10, "ad_tags": 5, "etl_runs": 3, "app_meta": 0}
    state = {"restored": dict(live)}

    def counts(url, tables=backup.KEY_TABLES):
        if "backup_verify" in url:
            return state["restored"]
        return {t: live.get(t) for t in tables if t in live}
    monkeypatch.setattr(backup, "table_counts", counts)
    monkeypatch.setattr(backup, "table_set", lambda url: {"ad_metrics", "ad_tags"})
    conn.commit()
    return state


def run(drive, runner=None, **kw):
    return backup.run(drive=drive, drive_parent="parent", now=NOW, scratch_factory=FakeScratch, runner=runner or make_runner(), **kw)


def last_run(conn):
    conn.rollback()
    return conn.execute("SELECT status, error, stats FROM etl_runs WHERE job='backup' ORDER BY id DESC LIMIT 1").fetchone()


# ------------------------------------------------------------------ run flow
def test_success_uploads_verifies_prunes_and_records(conn, env):
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)])
    st = run(d)
    assert backup.file_name(NOW) in d.files and st["verified"] and st["drive_roundtrip_ok"]
    assert set(d.deleted) == {nm(k) for k in range(7, 12)} and st["kept"] == 7                  # 7 days of dailies incl. today's: today + 1..6 = 7 files
    status, err, stats = last_run(conn)
    assert status == "ok" and err is None and stats["file"] == backup.file_name(NOW) and stats["size_bytes"] > 2000
    assert stats["tables_verified"]["ad_metrics"] == {"live": 10, "live_after": 10, "restored": 10} and "verify_seconds" in stats and stats["pruned"]


def test_upload_failure_prevents_prune_and_fails_the_run(conn, env):
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)], fail_upload=True)
    with pytest.raises(RuntimeError):
        run(d)
    assert d.deleted == [] and len(d.files) == 11
    status, err, _ = last_run(conn)
    assert status == "failed" and "storageQuotaExceeded" in err


def test_drive_md5_mismatch_fails_before_prune(conn, env):
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)], bad_md5=True)
    with pytest.raises(db_mod.JobFailed):
        run(d)
    assert d.deleted == [] and last_run(conn)[0] == "failed"


def test_corrupt_download_fails(conn, env):
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)], corrupt_download=True)
    with pytest.raises(db_mod.JobFailed, match="differs"):
        run(d)
    assert d.deleted == []


def test_verify_failure_fails_run_marks_dump_unverified_and_keeps_old_backups(conn, env):
    env["restored"]["ad_metrics"] = 3                                                              # restored table lost rows
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)])
    with pytest.raises(db_mod.JobFailed, match="ad_metrics"):
        run(d)
    assert d.deleted == [] and ("UNVERIFIED-" + backup.file_name(NOW)) in d.files and backup.file_name(NOW) not in d.files
    status, err, _ = last_run(conn)
    assert status == "failed" and "restore verification failed" in err


def test_restore_command_failure_fails(conn, env):
    d = FakeDrive()
    with pytest.raises(db_mod.JobFailed, match="pg_restore"):
        run(d, runner=make_runner(restore_rc=1))
    assert d.deleted == []


def test_dump_failure_never_touches_drive(conn, env):
    d = FakeDrive(existing=[nm(1)])
    with pytest.raises(db_mod.JobFailed, match="pg_dump"):
        run(d, runner=make_runner(dump_rc=1))
    assert list(d.files) == [nm(1)] and last_run(conn)[0] == "failed"


def test_old_pg_dump_is_refused(conn, env):
    with pytest.raises(db_mod.JobFailed, match="older than the server"):
        run(FakeDrive(), runner=make_runner(dump_major="15.4"))


def test_dry_run_dumps_and_verifies_but_writes_nothing(conn, env):
    n_before = conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0]
    d = FakeDrive(existing=[nm(k) for k in range(1, 12)])
    st = run(d, dry_run=True)
    conn.rollback()
    assert st["verified"] and len(d.files) == 11 and d.deleted == []
    assert conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0] == n_before
    assert set(st["would_prune"]) == {nm(k) for k in range(7, 12)}


def test_no_verify_skips_restore_and_no_local_copy_is_left(conn, env, tmp_path):
    d = FakeDrive()
    r = make_runner()
    st = backup.run(drive=d, drive_parent="p", now=NOW, runner=r, verify=False, tmp_base=str(tmp_path))
    assert st["verify_skipped"] and not any(os.path.basename(c[0]) == "pg_restore" for c in r.calls)
    assert os.listdir(tmp_path) == []                                                              # the dump (sensitive) is always removed


def test_local_copy_removed_on_failure_too(conn, env, tmp_path):
    with pytest.raises(db_mod.JobFailed):
        backup.run(drive=FakeDrive(bad_md5=True), drive_parent="p", now=NOW, runner=make_runner(), scratch_factory=FakeScratch, tmp_base=str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_missing_drive_id_fails_loudly(conn, env, monkeypatch):
    monkeypatch.delenv("BACKUP_DRIVE_ID", raising=False)
    with pytest.raises(db_mod.JobFailed, match="BACKUP_DRIVE_ID"):
        backup.run(drive=FakeDrive(), now=NOW, runner=make_runner(), scratch_factory=FakeScratch)


def test_pg_env_keeps_password_out_of_argv():
    e = backup._pg_env("postgresql://u:p%40ss@h.example:5432/railway")
    assert e["PGPASSWORD"] == "p@ss" and e["PGHOST"] == "h.example" and e["PGDATABASE"] == "railway"
