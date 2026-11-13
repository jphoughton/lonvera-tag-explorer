"""Nightly Postgres backup: pg_dump -Fc -> Google Drive (Shared Drive "Lonvera DB Backups", folder lonvera-tag-explorer) -> re-download -> restore into a scratch
Postgres -> compare row counts with the live database -> prune old dumps. See docs/BACKUP.md.

Order matters and is the whole safety story:
  1. dump production (read-only: pg_dump takes one consistent snapshot, writes nothing),
  2. upload to Drive, check Drive's own size + md5, download the file again and check it is byte-identical,
  3. restore the DOWNLOADED copy into a throwaway database on a scratch Postgres started inside this container (never on the production server),
  4. compare row counts of the key tables with the live database (tolerance for rows written while the dump ran), drop the scratch database,
  5. only if every step passed: delete dumps older than --keep-days, always keeping the newest 3 good files.
Any failed step fails the run (etl_runs job 'backup', status failed, exit 1) and nothing is pruned. A dump that fails verification is renamed UNVERIFIED-... so it is
never counted as a good backup.

Usage:  python -m etl.backup [--dry-run] [--keep-days 7] [--no-verify] [--no-record]
  --dry-run    dump (+ verify unless --no-verify) only: no upload, no prune, no etl_runs row; prints what would be pruned when Drive is reachable
  --no-record  do the whole run but write no etl_runs row (local proofs against production)
Env: DATABASE_URL (the database to back up), GOOGLE_SA_JSON (service account), BACKUP_DRIVE_ID (the Shared Drive id, or any folder id, that holds the
     lonvera-tag-explorer folder), optional BACKUP_PG_BIN (directory with pg_dump / pg_restore / initdb / pg_ctl of the SAME major version as the server or newer), BACKUP_TMPDIR.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from etl import db

FOLDER_NAME = "lonvera-tag-explorer"
PREFIX = "lonvera-tag-explorer_"
SUFFIX = ".dump"
DEFAULT_KEEP_DAYS = 7
KEEP_MIN = 3                                   # the newest this-many good dumps are never pruned, whatever their age
# Row counts compared between the live database and the restored dump. Tables missing on the live side are skipped (older schema); missing on the restored side = failure.
KEY_TABLES = ["ad_metrics", "ad_tags", "etl_runs", "ad_daily", "ad_daily_upstack", "meta_ads", "tracker_rows", "creative_assets", "app_meta",
              "family_daily", "upstack_attr_daily", "meta_ad_lifetime", "creative_stage_moves", "family_content", "meta_family_map", "shopify_orders", "shopify_order_lines"]
COUNT_TOLERANCE = 0.02                         # relative slack beyond the live before/after window (the ETL replaces trailing windows)
COUNT_SLACK_ROWS = 5
PG_BIN_CANDIDATES = ["/usr/lib/postgresql/18/bin", "/opt/homebrew/opt/postgresql@18/bin", "/usr/lib/postgresql/17/bin", "/opt/homebrew/opt/postgresql@17/bin"]
_TS = re.compile(re.escape(PREFIX) + r"(\d{8}T\d{6})Z" + re.escape(SUFFIX) + r"$")


# ---------------------------------------------------------------- naming / retention (pure)
def file_name(now: dt.datetime) -> str:
    return f"{PREFIX}{now:%Y%m%dT%H%M%S}Z{SUFFIX}"


def parse_ts(name: str) -> dt.datetime | None:
    m = _TS.match(name)
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc) if m else None


def select_monthly(files: list[dict]) -> list[dict]:
    """The earliest good dump of each calendar month (UTC, from the file name) is that month's permanent snapshot. UNVERIFIED-* and foreign names never match."""
    first: dict[tuple[int, int], tuple[dt.datetime, dict]] = {}
    for f in files:
        ts = parse_ts(f["name"])
        if ts and ((ts.year, ts.month) not in first or ts < first[(ts.year, ts.month)][0]):
            first[(ts.year, ts.month)] = (ts, f)
    return [f for _, f in sorted(first.values(), key=lambda x: x[0])]


def select_prune(files: list[dict], now: dt.datetime, keep_days: int, keep_min: int = KEEP_MIN) -> tuple[list[dict], list[dict]]:
    """files: [{id, name, ...}]. Only names that match our pattern are ever considered (UNVERIFIED-*, other files and folders are never touched).
    Returns (keep, delete): the newest `keep_min` are always kept, and the earliest good dump of every month (select_monthly) is kept forever; of the rest, only those at least keep_days old are deleted."""
    good = sorted(((parse_ts(f["name"]), f) for f in files if parse_ts(f["name"])), key=lambda x: x[0], reverse=True)
    cutoff = now - dt.timedelta(days=keep_days)
    monthly = {f["name"] for f in select_monthly(files)}
    keep, delete = [], []
    for i, (ts, f) in enumerate(good):
        (keep if i < keep_min or ts > cutoff or f["name"] in monthly else delete).append(f)
    return keep, delete


def select_unverified(files: list[dict], now: dt.datetime, keep_days: int) -> list[dict]:
    """UNVERIFIED-<name> copies (a dump that failed the restore check) older than keep_days: junk copies of sensitive data, removed with the normal prune."""
    cutoff = now - dt.timedelta(days=keep_days)
    return [f for f in files if f["name"].startswith("UNVERIFIED-") and (parse_ts(f["name"][len("UNVERIFIED-"):]) or now) <= cutoff]


def compare_counts(before: dict, after: dict, restored: dict, tol: float = COUNT_TOLERANCE, slack: int = COUNT_SLACK_ROWS) -> list[str]:
    """Problems (empty = match). The dump is one snapshot taken between the two live counts, so the restored count must sit inside [min-slack, max+slack] of them."""
    bad = []
    for t, b in before.items():
        if b is None:
            continue                                   # table not on the live side
        r = restored.get(t)
        if r is None:
            bad.append(f"{t}: missing in the restored database")
            continue
        a = after.get(t, b)
        lo, hi = min(b, a), max(b, a)
        if r < lo - max(slack, lo * tol) or r > hi + max(slack, hi * tol):
            bad.append(f"{t}: restored {r} vs live {b}..{a}")
    return bad


# ---------------------------------------------------------------- subprocess / postgres helpers
def _pg_env(url: str) -> dict:
    """libpq env from a postgres:// URL, so the password never appears in a command line."""
    u = urlparse(url)
    env = {"PGHOST": u.hostname or "", "PGPORT": str(u.port or 5432), "PGUSER": unquote(u.username or ""), "PGPASSWORD": unquote(u.password or ""),
           "PGDATABASE": (u.path or "/").lstrip("/")}
    for kv in (u.query or "").split("&"):
        if kv.startswith("sslmode="):
            env["PGSSLMODE"] = kv.split("=", 1)[1]
    return {k: v for k, v in env.items() if v}


def _run(cmd: list[str], env: dict | None = None, timeout: int = 3 * 3600) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    for k in [k for k in full if k.startswith("PG") and k not in ("PGTZ",)]:
        del full[k]                                    # never inherit a stray PG* variable that could point a tool at the wrong database
    full.update(env or {})
    return subprocess.run(cmd, env=full, capture_output=True, text=True, timeout=timeout)


def _tool(name: str) -> str:
    for d in ([os.environ["BACKUP_PG_BIN"]] if os.environ.get("BACKUP_PG_BIN") else []) + PG_BIN_CANDIDATES:
        p = Path(d) / name
        if p.exists():
            return str(p)
    w = shutil.which(name)
    if w:
        return w
    raise db.JobFailed(f"{name} not found (set BACKUP_PG_BIN)")


def _major(version_text: str) -> int:
    m = re.search(r"(\d+)\.\d+", version_text)
    return int(m.group(1)) if m else 0


def _fail_text(cp: subprocess.CompletedProcess, what: str) -> str:
    err = db.scrub((cp.stderr or cp.stdout or "").strip()[-600:])
    return f"{what} failed (exit {cp.returncode}): {err}"


def md5_of(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(url: str, tables=KEY_TABLES) -> dict:
    """Exact row counts; None for a table that does not exist."""
    out = {}
    with db.connect(url, autocommit=True) as c:
        have = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        for t in tables:
            out[t] = c.execute(f'SELECT count(*) FROM public."{t}"').fetchone()[0] if t in have else None
    return out


def table_set(url: str) -> set:
    with db.connect(url, autocommit=True) as c:
        return {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}


class ScratchPostgres:
    """A throwaway Postgres server inside this container (initdb in a temp dir, TCP on 127.0.0.1 and a free port, trust auth, no outside access).
    The production server is never involved in the restore test."""

    def __init__(self, base_dir: str | None = None):
        self.base = tempfile.mkdtemp(prefix="bkpg_", dir=base_dir)
        self.port = None

    def __enter__(self):
        try:
            return self._start()
        except BaseException:
            shutil.rmtree(self.base, ignore_errors=True)         # a failed start must not leave a data directory behind
            raise

    def _start(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        data = self.data = os.path.join(self.base, "data")
        loc = {"LC_ALL": os.environ.get("BACKUP_LC_ALL", "en_US.UTF-8")}     # macOS: the postmaster refuses to start without a valid locale
        self.loc = loc
        cp = _run([_tool("initdb"), "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-sync"], env=loc)
        if cp.returncode:
            raise db.JobFailed(_fail_text(cp, "initdb"))
        opts = f"-p {self.port} -c listen_addresses=127.0.0.1 -c unix_socket_directories= -c fsync=off -c full_page_writes=off -c synchronous_commit=off -c max_wal_size=2GB"
        cp = _run([_tool("pg_ctl"), "-D", data, "-l", os.path.join(self.base, "pg.log"), "-w", "-t", "120", "-o", opts, "start"], env=loc)
        if cp.returncode:
            raise db.JobFailed(_fail_text(cp, "scratch postgres start"))
        return self

    def url(self, dbname="postgres") -> str:
        return f"postgresql://postgres@127.0.0.1:{self.port}/{dbname}"

    def __exit__(self, *a):
        try:
            _run([_tool("pg_ctl"), "-D", self.data, "-m", "immediate", "-w", "stop"], env=self.loc)
        except Exception:
            pass
        shutil.rmtree(self.base, ignore_errors=True)
        return False


# ---------------------------------------------------------------- Drive
class Drive:
    """The few Drive v3 calls the backup needs, all with supportsAllDrives. `svc` is a googleapiclient Resource (tests pass a fake with the same shape)."""

    def __init__(self, svc):
        self.svc = svc

    @classmethod
    def from_env(cls):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        raw = os.environ.get("GOOGLE_SA_JSON")
        if not raw:
            raise db.JobFailed("GOOGLE_SA_JSON is not set")
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/drive"])
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def ensure_folder(self, parent_id: str, name: str = FOLDER_NAME) -> str:
        q = f"'{parent_id}' in parents and name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        r = self.svc.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives").execute()
        if r.get("files"):
            return r["files"][0]["id"]
        f = self.svc.files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}, fields="id", supportsAllDrives=True).execute()
        return f["id"]

    def upload(self, path: str, name: str, folder_id: str, chunk_mb: int = 16) -> dict:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(path, mimetype="application/octet-stream", resumable=True, chunksize=chunk_mb * 1024 * 1024)
        req = self.svc.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id,name,size,md5Checksum", supportsAllDrives=True)
        resp = None
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
        return resp

    def download(self, file_id: str, dest: str, chunk_mb: int = 16) -> None:
        from googleapiclient.http import MediaIoBaseDownload
        with open(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True), chunksize=chunk_mb * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=5)

    def list_files(self, folder_id: str) -> list[dict]:
        out, tok = [], None
        while True:
            r = self.svc.files().list(q=f"'{folder_id}' in parents and trashed=false", fields="nextPageToken,files(id,name,size,createdTime)", pageSize=200, pageToken=tok,
                                      supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives").execute()
            out += r.get("files", [])
            tok = r.get("nextPageToken")
            if not tok:
                return out

    def rename(self, file_id: str, name: str) -> None:
        self.svc.files().update(fileId=file_id, body={"name": name}, supportsAllDrives=True).execute()

    def delete(self, file_id: str) -> None:
        """Permanent delete; a Content manager on a Shared Drive may only TRASH (Drive answers the permanent delete with 404 / 403), so fall back to the trash.
        Trashed files leave the folder listing at once and Drive purges them after 30 days."""
        try:
            self.svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        except Exception as e:
            if not any(c in str(e) for c in ("403", "404")):
                raise
            try:
                self.svc.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()
            except Exception as e2:
                if "404" not in str(e2):
                    raise                                           # genuinely gone already: nothing to delete


# ---------------------------------------------------------------- the run
VERIFY_DB_PREFIX = "backup_verify_"           # scratch database the dump is restored into (a module constant so a test can give its run a name of its own)


def _dump(url: str, path: str, runner) -> None:
    cp = runner([_tool("pg_dump"), "-Fc", "--no-owner", "--no-acl", "-f", path], env=_pg_env(url))
    if cp.returncode or not os.path.exists(path) or os.path.getsize(path) == 0:
        raise db.JobFailed(_fail_text(cp, "pg_dump"))


def _verify_restore(dump_path: str, live_url: str, before: dict, after: dict, stats: dict, scratch_factory, runner, today: dt.date, tmp_base) -> None:
    dbname = f"{VERIFY_DB_PREFIX}{today:%Y%m%d}"
    t0 = time.time()
    with scratch_factory(tmp_base) as sp:
        with db.connect(sp.url("postgres"), autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            c.execute(f'CREATE DATABASE "{dbname}"')
        cp = runner([_tool("pg_restore"), "--no-owner", "--no-acl", "-j", "2", "-d", sp.url(dbname)])
        if cp.returncode:
            raise db.JobFailed(_fail_text(cp, "pg_restore into scratch"))
        restored = table_counts(sp.url(dbname))
        problems = compare_counts(before, after, restored)
        live_tables, rest_tables = table_set(live_url), table_set(sp.url(dbname))
        if live_tables - rest_tables:
            problems.append("tables missing after restore: " + ", ".join(sorted(live_tables - rest_tables))[:200])
        stats["tables_verified"] = {t: {"live": before[t], "live_after": after.get(t), "restored": restored.get(t)} for t in before if before[t] is not None}
        stats["tables_in_schema"] = len(live_tables)
        with db.connect(sp.url("postgres"), autocommit=True) as c:
            c.execute(f'DROP DATABASE "{dbname}"')
    stats["verify_seconds"] = round(time.time() - t0, 1)
    if problems:
        raise db.JobFailed("restore verification failed: " + "; ".join(problems)[:600])


class _NoRecord:
    def __init__(self, job, url=None): self.stats, self.rows_written, self.data_through, self.run_id = {}, None, None, None
    def __enter__(self): return self
    def __exit__(self, *a): return False


def run(*, dry_run: bool = False, keep_days: int = DEFAULT_KEEP_DAYS, verify: bool = True, record: bool = True, drive: Drive | None = None, drive_parent: str | None = None,
        db_url: str | None = None, now: dt.datetime | None = None, scratch_factory=ScratchPostgres, runner=_run, tmp_base: str | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    url = db.database_url(db_url)
    tmp_base = tmp_base or os.environ.get("BACKUP_TMPDIR") or None
    work = tempfile.mkdtemp(prefix="bkup_", dir=tmp_base)
    try:
        Job = db.JobRun if (record and not dry_run) else _NoRecord
        with Job("backup", url) as job:
            st = job.stats
            job.data_through = now.date()
            name = file_name(now)
            st.update(file=name, dry_run=dry_run, keep_days=keep_days, verified=False)
            # 0. versions: pg_restore / pg_dump must be at least the server's major version
            with db.connect(url, autocommit=True) as c:
                server_major = int(c.execute("SHOW server_version_num").fetchone()[0]) // 10000
            dump_major = _major(runner([_tool("pg_dump"), "--version"]).stdout)
            st.update(server_major=server_major, pg_dump_major=dump_major)
            if dump_major < server_major:
                raise db.JobFailed(f"pg_dump {dump_major} is older than the server ({server_major}); install postgresql-client-{server_major} / set BACKUP_PG_BIN")
            # 1. dump
            before = table_counts(url)
            path = os.path.join(work, name)
            t0 = time.time()
            _dump(url, path, runner)
            st["dump_seconds"] = round(time.time() - t0, 1)
            after = table_counts(url)
            st["size_bytes"] = os.path.getsize(path)
            local_md5 = md5_of(path)
            restore_src, file_id, folder_id = path, None, None
            # 2. upload + re-read
            if not dry_run:
                drive = drive or Drive.from_env()
                parent = drive_parent or os.environ.get("BACKUP_DRIVE_ID")
                if not parent:
                    raise db.JobFailed("BACKUP_DRIVE_ID is not set (the Shared Drive 'Lonvera DB Backups' id)")
                folder_id = drive.ensure_folder(parent)
                t0 = time.time()
                up = drive.upload(path, name, folder_id)
                file_id = up["id"]
                st.update(drive_file_id=file_id, upload_seconds=round(time.time() - t0, 1))
                if int(up.get("size", -1)) != st["size_bytes"] or up.get("md5Checksum") != local_md5:
                    raise db.JobFailed(f"Drive reports size {up.get('size')} md5 {up.get('md5Checksum')} for the upload; local is {st['size_bytes']} {local_md5}")
                back = os.path.join(work, "downloaded.dump")
                drive.download(file_id, back)
                if md5_of(back) != local_md5:
                    raise db.JobFailed("the file downloaded from Drive differs from the dump that was uploaded")
                os.remove(path)
                restore_src = back
                st["drive_roundtrip_ok"] = True
            # 3-4. restore + compare
            if verify:
                try:
                    _verify_restore(restore_src, url, before, after, st, scratch_factory, runner_with_file(runner, restore_src), now.date(), work)
                except Exception:
                    if file_id:
                        try:
                            drive.rename(file_id, "UNVERIFIED-" + name)
                        except Exception as e:
                            db.log("[backup] could not rename the unverified dump:", e)
                    raise
                st["verified"] = True
            else:
                st["verify_skipped"] = True
            # 5. prune (only reached when everything above passed)
            if not dry_run:
                listing = drive.list_files(folder_id)
                keep, delete = select_prune(listing, now, keep_days)
                delete = delete + select_unverified(listing, now, keep_days)
                for f in delete:
                    drive.delete(f["id"])
                st.update(pruned=[f["name"] for f in delete], kept=len(keep), monthly_kept=[f["name"] for f in select_monthly(listing)])
            elif drive is not None and (drive_parent or os.environ.get("BACKUP_DRIVE_ID")):
                st["would_prune"] = [f["name"] for f in select_prune(drive.list_files(drive.ensure_folder(drive_parent or os.environ["BACKUP_DRIVE_ID"])), now, keep_days)[1]]
    finally:
        shutil.rmtree(work, ignore_errors=True)                   # the dump holds sensitive business data: never leave a copy behind
    return st


def runner_with_file(runner, dump_path):
    """pg_restore needs the dump file as its last argument: wrap the runner so _verify_restore does not have to know the path."""
    def r(cmd, env=None, **kw):
        if cmd and os.path.basename(cmd[0]) == "pg_restore":
            cmd = cmd + [dump_path]
        return runner(cmd, env=env, **kw)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="dump (+ verify) only: no upload, no prune, no etl_runs row")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    ap.add_argument("--no-verify", action="store_true", help="skip the restore check (not for the nightly run)")
    ap.add_argument("--no-record", action="store_true", help="write no etl_runs row")
    a = ap.parse_args(argv)
    try:
        st = run(dry_run=a.dry_run, keep_days=a.keep_days, verify=not a.no_verify, record=not a.no_record)
    except db.JobFailed as e:
        db.log("[backup] FAILED:", e)
        sys.exit(1)
    except Exception as e:
        db.log("[backup] FAILED:", repr(e)[:400])
        sys.exit(1)
    for t, v in (st.get("tables_verified") or {}).items():
        db.log(f"[backup]   {t}: live {v['live']} -> {v['live_after']}, restored {v['restored']}")
    db.log("[backup] done", json.dumps({k: v for k, v in st.items() if k != "tables_verified"}, default=str)[:600])


if __name__ == "__main__":
    main()
