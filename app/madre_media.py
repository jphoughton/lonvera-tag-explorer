"""Ad video for the Madre review side panel: resolve one tracker family to a video file in Google Drive, cache it on disk, serve it with Range support.

Lookup order (the client only ever sends a family key; it never supplies a URL or a Drive id):
  1. the family's tracker asset link when it is a Google Drive file (or a Drive folder holding a video named like the family);
  2. the shared Drive folder 'Launched Ads' (id LAUNCHED_ADS_FOLDER, subfolders by product, each with Videos / Images): a video whose file name is the family key
     (or starts with it, for copies).
Credentials: the same GOOGLE_SA_JSON / GOOGLE_SA_FILE service account the launch worker uses (etl/drive_io.py, drive.readonly only). Nothing is written to Drive.
Cache: one file per family under MADRE_MEDIA_CACHE (default: the system temp dir), at most MAX_FILE_BYTES per file and MAX_CACHE_BYTES in total (oldest evicted first).
A miss (no file, private file, too big) is remembered for MISS_TTL seconds so a panel that cannot play does not hammer Drive.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("tagexp.madre_media")

LAUNCHED_ADS_FOLDER = "1ccRQbskaoDnVwDqt26ELfpW3cFepvRWG"
MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_CACHE_BYTES = 1024 * 1024 * 1024
MISS_TTL = 15 * 60
FAMILY_RE = re.compile(r"^[A-Za-z0-9._ \-()+,&#'@]{1,200}$")

_lock = threading.Lock()
_fam_locks: dict[str, threading.Lock] = {}
_misses: dict[str, float] = {}
_video_folders: list[str] | None = None


def cache_dir() -> Path:
    d = Path(os.environ.get("MADRE_MEDIA_CACHE") or Path(tempfile.gettempdir()) / "madre_media")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(family: str) -> Path:
    return cache_dir() / (hashlib.sha1(family.lower().encode()).hexdigest()[:24] + ".mp4")


def _norm(s: str) -> str:
    return re.sub(r"\.(mp4|mov|m4v|webm)$", "", (s or "").strip().lower())


def _session():
    from etl import drive_io
    return drive_io, drive_io._default_session()


def _list(drive_io, s, q: str, n: int = 50) -> list[dict]:
    j = drive_io._get_json(s, "files", {"q": q, "fields": "files(id,name,mimeType,size,parents)", "pageSize": n, "includeItemsFromAllDrives": "true", "corpora": "allDrives"}, "the media folder")
    return j.get("files") or []


def _launched_video_folders(drive_io, s) -> list[str]:
    global _video_folders
    if _video_folders is None:
        ids = []
        for prod in _list(drive_io, s, f"'{LAUNCHED_ADS_FOLDER}' in parents and mimeType = '{drive_io.FOLDER_MIME}' and trashed = false", 20):
            for sub in _list(drive_io, s, f"'{prod['id']}' in parents and mimeType = '{drive_io.FOLDER_MIME}' and trashed = false", 20):
                if sub["name"].strip().lower() == "videos":
                    ids.append(sub["id"])
        _video_folders = ids
    return _video_folders


def _pick(files: list[dict], family: str) -> dict | None:
    """Best video among candidate files: exact name first, then names that start with the family (copies), smallest copy number first."""
    fam = _norm(family)
    vids = [f for f in files if (f.get("mimeType") or "").startswith("video/") and int(f.get("size") or 0) > 0]
    exact = [f for f in vids if _norm(f["name"]) == fam]
    if exact:
        return exact[0]
    pre = sorted([f for f in vids if _norm(f["name"]).startswith(fam)], key=lambda f: len(f["name"]))
    return pre[0] if pre else None


def resolve(family: str, asset_url: str | None) -> dict | None:
    """{id, name, size} of the Drive video for this family, or None."""
    drive_io, s = _session()
    if asset_url:
        try:
            kind, fid = drive_io.parse_link(asset_url)
        except drive_io.DriveError:
            kind = fid = None
        if kind:
            try:
                meta = drive_io._get_json(s, f"files/{fid}", {"fields": "id,name,mimeType,size"}, "the asset link")
                if meta.get("mimeType") == drive_io.FOLDER_MIME:
                    hit = _pick(_list(drive_io, s, f"'{fid}' in parents and trashed = false"), family)
                    if hit:
                        return hit
                elif (meta.get("mimeType") or "").startswith("video/"):
                    return meta
            except drive_io.DriveError:
                pass
    tok = re.sub(r"[^A-Za-z0-9]+", " ", family).strip().split(" ")[0:3]
    term = " ".join(tok).replace("'", "")
    if not term:
        return None
    folders = set(_launched_video_folders(drive_io, s))
    cands = [f for f in _list(drive_io, s, f"name contains '{term}' and mimeType contains 'video/' and trashed = false", 100) if set(f.get("parents") or ()) & folders]
    return _pick(cands, family)


def _evict(keep: Path) -> None:
    files = sorted((p for p in cache_dir().glob("*.mp4") if p != keep), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files) + (keep.stat().st_size if keep.exists() else 0)
    for p in files:
        if total <= MAX_CACHE_BYTES:
            break
        total -= p.stat().st_size
        try:
            p.unlink()
        except OSError:
            pass


def get_video(family: str, asset_url: str | None) -> Path | None:
    """Local path of the cached video for this family (downloaded on first use), or None when there is none to play."""
    if not FAMILY_RE.match(family or ""):
        return None
    path = _path_for(family)
    if path.exists() and path.stat().st_size > 0:
        os.utime(path, None)
        return path
    with _lock:
        if _misses.get(family.lower(), 0) > time.time():
            return None
        lk = _fam_locks.setdefault(family.lower(), threading.Lock())
    with lk:
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            hit = resolve(family, asset_url)
            if not hit or int(hit.get("size") or 0) > MAX_FILE_BYTES:
                raise LookupError("no video")
            drive_io, s = _session()
            tmp = path.with_suffix(".part")
            n = drive_io.download(hit["id"], tmp, s)
            if n <= 0 or n > MAX_FILE_BYTES:
                tmp.unlink(missing_ok=True)
                raise LookupError("bad size")
            os.replace(tmp, path)
            _evict(path)
            return path
        except Exception as e:                      # noqa: BLE001  a miss is not an error for the page: it falls back to the poster
            if not isinstance(e, LookupError):
                log.warning("madre media: %s for %r", type(e).__name__, family)
            with _lock:
                _misses[family.lower()] = time.time() + MISS_TTL
            return None
