"""Asset sync: every media file linked from a Production ticket is copied once into the Drive folder "Lonvera - Assets" (<Product>/<Images|Videos>).

    python -m etl.asset_sync [--dry-run] [--max-seconds 600] [--max-copies 300]

What counts as a ticket asset: the links on creative_priorities_assignments.creative_url and creative_priorities_assignments.variations[].url (the only two places an asset enters a ticket: the
Production board, the vendor page, the partner API and the launch flow all write these two fields; the app has no file upload). The brief link is a document, not an asset, and is not synced.
Link forms: Drive file (/file/d/ID/view|preview, open?id=, uc?id=) and Drive folder (every image and video inside, subfolders included) are copied server side (files.copy). Dropbox (dl=1) and direct
file URLs are downloaded; YouTube, Vimeo, Loom, Frame.io and Facebook post / video links go through yt-dlp; a Meta ad preview iframe is read for its videoURI (or image). Google Docs / Slides / Sheets
and Figma links hold no file and are logged as skipped. Downloads are capped at 150 MB (logged as skipped above that); Drive copies have no cap. Anything that cannot be resolved is stored with a reason.
Dedupe: the md5 of every file under Lonvera - Assets is cached (asset_sync_known_md5, refreshed every 6 hours) and a file whose md5 is there is recorded as duplicate and not copied.
Safety: this module only ADDS files (copy / upload / create a missing folder). The Drive client below has no delete, move, rename or share call. Every state change is in asset_sync_links / asset_sync_files.
A failure is logged and never fails the parent job (scripts/deploy/run_live.sh)."""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

from . import db

ROOT_ID = os.environ.get("ASSET_SYNC_ROOT_ID") or "1z43s9o-2RUwy6NNuKbHmK66cMWrCJt9A"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER = "application/vnd.google-apps.folder"
CAP_BYTES = 150 * 1024 * 1024
INDEX_TTL_S = 6 * 3600
FOLDER_RECHECK_S = 6 * 3600
OLD_FOLDER_RECHECK_S = 7 * 86400
UNRESOLVABLE_RECHECK_S = 7 * 86400
FAILED_MAX_FAST = 5
LOCK_KEY = 727_275_001
PRODUCT_FOLDERS = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-belle": "Vital Belle", "vital-belle": "Vital Belle", "vital-flow": "Vital Flow", "vita-flow": "Vital Flow"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
YT_TYPES = {"youtube", "vimeo", "loom", "frameio", "facebook_post"}
SKIP_TYPES = {"google_doc": "Google Doc, Slides or Sheet: holds no media file to copy", "figma": "Figma design link: no media file behind it without a Figma export"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; lonvera-asset-sync)"}


class LinkError(Exception):
    """A problem with one link. permanent=True: nothing to retry soon (unresolvable); False: transient (failed, retried)."""

    def __init__(self, msg: str, permanent: bool = False):
        super().__init__(msg)
        self.permanent = permanent


class TooBig(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ links
def extract_urls(text) -> list[str]:
    """Every http(s) link in a text, including the src of an <iframe>/<embed>, deduplicated in order."""
    out: list[str] = []
    for m in re.findall(r"https?://[^\s\"'<>]+", html.unescape(str(text or ""))):
        m = m.rstrip(".,;)")
        if m not in out:
            out.append(m)
    return out


def classify(url: str) -> str:
    p = urlsplit(url)
    host, path = (p.hostname or "").lower(), p.path
    if host == "docs.google.com" and re.match(r"/(document|presentation|spreadsheets|forms)/", path):
        return "google_doc"
    if host in ("drive.google.com", "docs.google.com", "drive.usercontent.google.com"):
        if "/folders/" in path:
            return "drive_folder"
        return "drive_file"
    if host.endswith("figma.com"):
        return "figma"
    if host.endswith("frame.io") or host == "f.io":
        return "frameio"
    if host.endswith("loom.com"):
        return "loom"
    if host.endswith("vimeo.com"):
        return "vimeo"
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("dropbox.com") or host.endswith("dropboxusercontent.com"):
        return "dropbox"
    if host.endswith("facebook.com") or host.endswith("fb.watch") or host.endswith("fb.com") or host == "fb.me":
        return "facebook_preview" if ("preview_iframe" in path or "/ads/api/preview" in path or "preview" in parse_qs(p.query)) else "facebook_post"
    if Path(path).suffix.lower() in VIDEO_EXT | IMAGE_EXT:
        return "direct"
    return "other"


def ticket_links(conn) -> list[dict]:
    """Every (ticket, link) pair: creative_url and variations[].url of every ticket, one row per distinct link."""
    out, seen = [], set()
    for r in conn.execute("SELECT id, product, concept_name, creative_url, variations, updated_at FROM creative_priorities_assignments ORDER BY id").fetchall():
        cands = [("creative_url", r["creative_url"])] + [(f"variation:{v.get('label', '')}", v.get("url")) for v in (r["variations"] or []) if isinstance(v, dict)]
        for field, text in cands:
            for u in extract_urls(text):
                if (r["id"], u) not in seen:
                    seen.add((r["id"], u))
                    out.append({"assignment_id": r["id"], "product": r["product"], "concept": r["concept_name"] or "", "url": u, "field": field, "updated_at": r["updated_at"]})
    return out


def media_kind(mime: str, name: str = "") -> str | None:
    mime = (mime or "").lower()
    ext = Path(name).suffix.lower()
    if mime.startswith("image/") or (not mime.startswith("video/") and ext in IMAGE_EXT):
        return "Images"
    if mime.startswith("video/") or ext in VIDEO_EXT:
        return "Videos"
    return None


# ------------------------------------------------------------------------------------------------------------------------------ Drive (add-only)
class Drive:
    """Minimal Drive v3 client over the service account. ADD-ONLY: list, get, copy, create folder, upload, download. There is deliberately no delete / update / move / share method."""

    def __init__(self, session=None):
        self._s = session

    @property
    def s(self):
        if self._s is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
            from .drive_io import _info
            self._s = AuthorizedSession(service_account.Credentials.from_service_account_info(_info(), scopes=SCOPES))
        return self._s

    def _json(self, method: str, path: str, what: str, **kw):
        for i in range(4):
            r = self.s.request(method, f"{API}/{path}", timeout=120, **kw)
            if r.status_code in (429, 500, 502, 503) and i < 3:
                time.sleep(2 ** i)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 404):
                raise LinkError(f"Drive says {what} is missing or private; share it with the service account ({_sa_email()}) as Viewer", permanent=True)
            raise LinkError(f"Drive HTTP {r.status_code} on {what}")
        raise LinkError(f"Drive busy on {what}")

    def get(self, fid: str) -> dict:
        return self._json("GET", f"files/{fid}", "the file", params={"fields": "id,name,mimeType,md5Checksum,size,shortcutDetails", "supportsAllDrives": "true"})

    def children(self, fid: str, fields: str = "id,name,mimeType,md5Checksum,size") -> list[dict]:
        out, token = [], None
        while True:
            p = {"q": f"'{fid}' in parents and trashed = false", "fields": f"nextPageToken,files({fields})", "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                p["pageToken"] = token
            j = self._json("GET", "files", "the folder", params=p)
            out += j.get("files") or []
            token = j.get("nextPageToken")
            if not token:
                return out

    def find_folder(self, parent: str, name: str) -> str | None:
        for f in self.children(parent, "id,name,mimeType"):
            if f["mimeType"] == FOLDER and f["name"].strip().lower() == name.lower():
                return f["id"]
        return None

    def create_folder(self, parent: str, name: str) -> str:
        return self._json("POST", "files", "the destination", params={"supportsAllDrives": "true", "fields": "id"}, json={"name": name, "mimeType": FOLDER, "parents": [parent]})["id"]

    def copy(self, fid: str, parent: str, name: str, description: str) -> str:
        return self._json("POST", f"files/{fid}/copy", "the file", params={"supportsAllDrives": "true", "fields": "id"}, json={"name": name, "parents": [parent], "description": description})["id"]

    def download(self, fid: str, dest: str) -> None:
        r = self.s.get(f"{API}/files/{fid}", params={"alt": "media", "supportsAllDrives": "true"}, stream=True, timeout=300)
        if r.status_code != 200:
            raise LinkError(f"Drive would not let the file be downloaded (HTTP {r.status_code})", permanent=r.status_code in (403, 404))
        with open(dest, "wb") as out:
            for part in r.iter_content(1 << 20):
                out.write(part)

    def upload(self, path: str, name: str, parent: str, mime: str, description: str) -> str:
        size = os.path.getsize(path)
        r = self.s.post(UPLOAD, params={"uploadType": "resumable", "supportsAllDrives": "true", "fields": "id"}, timeout=120,
                        headers={"X-Upload-Content-Type": mime or "application/octet-stream", "X-Upload-Content-Length": str(size)},
                        json={"name": name, "parents": [parent], "description": description})
        if r.status_code != 200 or not r.headers.get("Location"):
            raise LinkError(f"Drive refused the upload (HTTP {r.status_code})")
        loc, step = r.headers["Location"], 8 * 1024 * 1024
        with open(path, "rb") as f:
            pos = 0
            while True:
                chunk = f.read(step)
                end = pos + len(chunk)
                rng = f"bytes {pos}-{end - 1}/{size}" if size else "bytes */0"
                rr = self.s.put(loc, data=chunk, headers={"Content-Range": rng}, timeout=300)
                if rr.status_code in (200, 201):
                    return rr.json()["id"]
                if rr.status_code != 308:
                    raise LinkError(f"Drive upload failed (HTTP {rr.status_code})")
                pos = end


def _sa_email() -> str:
    try:
        from .drive_io import service_account_email
        return service_account_email()
    except Exception:                                                                 # noqa: BLE001
        return "the service account"


def walk_md5(drive, root: str):
    """Yield (md5, id, name) for every file under root, folders recursively."""
    todo = [root]
    while todo:
        fid = todo.pop()
        for f in drive.children(fid, "id,name,mimeType,md5Checksum"):
            if f["mimeType"] == FOLDER:
                todo.append(f["id"])
            elif f.get("md5Checksum"):
                yield f["md5Checksum"], f["id"], f.get("name", "")


# ------------------------------------------------------------------------------------------------------------------------------ downloads
def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def _name_from(resp, url: str, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return unquote(m.group(1)).strip()
    n = unquote(Path(urlsplit(url).path).name)
    return n if Path(n).suffix else fallback


def http_download(url: str, dest: str, cap: int = CAP_BYTES, get=None) -> dict:
    """Stream a public https/http file to dest with the size cap, following up to 5 redirects (each host checked as public). Returns {name, mime, size, md5}.
    Raises TooBig over the cap, LinkError when the answer is a page instead of a file."""
    import requests
    get = get or (lambda u, **kw: requests.get(u, stream=True, timeout=60, allow_redirects=False, headers=UA, **kw))
    cur = url
    for _ in range(6):
        p = urlsplit(cur)
        if p.scheme not in ("http", "https") or not p.hostname or not _public(p.hostname):
            raise LinkError("link does not point to a public website", permanent=True)
        r = get(cur)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            cur = requests.compat.urljoin(cur, r.headers["Location"])
            continue
        if r.status_code in (401, 403, 404, 410):
            raise LinkError(f"link answered HTTP {r.status_code} (private or gone)", permanent=True)
        if r.status_code != 200:
            raise LinkError(f"link answered HTTP {r.status_code}")
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        name = _name_from(r, cur, "download")
        if not (ctype.startswith(("image/", "video/")) or (ctype in ("application/octet-stream", "binary/octet-stream") and media_kind("", name))):
            raise LinkError(f"link is a web page, not a file ({ctype or 'unknown type'})", permanent=True)
        clen = int(r.headers.get("Content-Length") or 0)
        if clen > cap:
            raise TooBig(f"{clen // (1024 * 1024)} MB is over the {cap // (1024 * 1024)} MB cap")
        h, n = hashlib.md5(), 0
        with open(dest, "wb") as out:
            for part in r.iter_content(1 << 20):
                n += len(part)
                if n > cap:
                    raise TooBig(f"over the {cap // (1024 * 1024)} MB cap")
                h.update(part)
                out.write(part)
        if not media_kind(ctype, name) and ctype.startswith(("image/", "video/")):
            name += mimetypes.guess_extension(ctype) or ""
        return {"name": name, "mime": ctype if ctype.startswith(("image/", "video/")) else (mimetypes.guess_type(name)[0] or ""), "size": n, "md5": h.hexdigest()}
    raise LinkError("too many redirects")


def dropbox_direct(url: str) -> str:
    p = urlsplit(url)
    if re.match(r"/(scl/fo|sh)/", p.path):
        raise LinkError("Dropbox folder link: Dropbox serves a folder only as a zip, not as files", permanent=True)
    q = {k: v for k, v in parse_qs(p.query).items() if k != "dl"}
    qs = "&".join(f"{k}={v[0]}" for k, v in q.items())
    return urlunsplit((p.scheme, p.netloc, p.path, (qs + "&" if qs else "") + "dl=1", ""))


def ytdlp_download(url: str, tmpdir: str, cap: int = CAP_BYTES, run=None) -> str:
    """yt-dlp on one link; returns the downloaded file path. Over-cap, unsupported, private or missing videos raise LinkError (permanent) / TooBig."""
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, timeout=900))
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--no-part", "--socket-timeout", "30", "--max-filesize", str(cap), "-f", f"bv*[height<=1080][filesize<{cap}]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", os.path.join(tmpdir, "yt_%(id)s.%(ext)s"), url]
    try:
        r = run(cmd)
    except subprocess.TimeoutExpired:
        raise LinkError("yt-dlp timed out") from None
    files = sorted(p for p in Path(tmpdir).glob("yt_*") if p.suffix not in (".part", ".ytdl"))
    err = ((r.stderr or b"").decode("utf-8", "replace") + (r.stdout or b"").decode("utf-8", "replace")).strip()
    if not files:
        if re.search(r"larger than max-filesize|File is larger", err, re.I):
            raise TooBig(f"video is over the {cap // (1024 * 1024)} MB cap")
        last = (err.splitlines() or ["no output"])[-1][-200:]
        perm = bool(re.search(r"unsupported url|private|login|sign in|log in|not found|404|403|unavailable|removed|no video|forbidden|requires", err, re.I))
        raise LinkError("yt-dlp could not get a file: " + last, permanent=perm)
    return str(files[0])


def fb_preview_download(url: str, dest: str, get=None, dl=None) -> dict:
    """A Meta ad preview iframe page: read its videoURI (HD, then SD); no video -> the largest scontent image. Then a plain download."""
    import requests
    p = urlsplit(url)
    if not (p.hostname or "").lower().endswith(("facebook.com", "fb.com")):
        raise LinkError("not a Facebook preview link", permanent=True)
    get = get or (lambda u: requests.get(u, timeout=90, headers=UA))
    r = get(url)
    if r.status_code != 200:
        raise LinkError(f"preview answered HTTP {r.status_code}", permanent=r.status_code in (400, 401, 403, 404))
    h = r.text
    m = re.search(r'"videoURIHD":"([^"]+)"', h) or re.search(r'"videoURISD":"([^"]+)"', h)
    if m:
        src = m.group(1).replace("\\/", "/")
    else:
        imgs = re.findall(r'(?:src|content)="(https://[^"]*scontent[^"]+)"', h)
        if not imgs:
            raise LinkError("the preview holds no video or image address (it may have expired)", permanent=True)
        src = html.unescape(max(imgs, key=len))
    return (dl or http_download)(src, dest)


# ------------------------------------------------------------------------------------------------------------------------------ the run
class Ctx:
    def __init__(self, conn, drive, *, dl=None, ytdlp=None, fbp=None, now=None, log=db.log, max_seconds=600, max_copies=300, dry_run=False, tmp=None):
        self.conn, self.drive, self.log = conn, drive, log
        self.dl, self.ytdlp, self.fbp = dl or http_download, ytdlp or ytdlp_download, fbp or fb_preview_download
        self.now = now or time.time
        self.deadline = self.now() + max_seconds
        self.max_copies, self.dry_run = max_copies, dry_run
        self.tmp = tmp
        self.known: set[str] = set()
        self.index_ready = False
        self.folders: dict[tuple, str] = {}
        self.stats = {"links_seen": 0, "links_checked": 0, "files_found": 0, "copied": 0, "duplicate": 0, "skipped": 0, "failed": 0, "unresolvable_links": 0, "by_type": {}}

    def out_of_budget(self) -> bool:
        return self.now() >= self.deadline or self.stats["copied"] >= self.max_copies

    def bt(self, typ: str) -> dict:
        return self.stats["by_type"].setdefault(typ, {"found": 0, "copied": 0, "duplicate": 0, "unresolvable": 0})


def load_index(cx: Ctx) -> None:
    """The md5 set of Lonvera - Assets: from the cache table, re-listed from Drive when older than 6 hours."""
    if cx.index_ready:
        return
    conn = cx.conn
    r0 = conn.execute("SELECT value FROM app_meta WHERE key = %s", ("asset_sync_index_at",)).fetchone()
    at = r0["value"] if r0 else None
    fresh = False
    try:
        fresh = bool(at) and time.time() - float(at) < INDEX_TTL_S and conn.execute("SELECT 1 FROM asset_sync_known_md5 LIMIT 1").fetchone() is not None
    except ValueError:
        pass
    if not fresh:
        t = time.time()
        rows = {}
        for md5, fid, name in walk_md5(cx.drive, ROOT_ID):
            rows.setdefault(md5, (md5, fid, name[:300]))
        conn.execute("TRUNCATE asset_sync_known_md5")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO asset_sync_known_md5 (md5, drive_file_id, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", list(rows.values()))
        conn.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ("asset_sync_index_at", str(time.time())))
        conn.commit()
        cx.log(f"  asset_sync: md5 index rebuilt from Drive: {len(rows)} distinct files in {time.time() - t:.0f}s")
    cx.known = {r["md5"] for r in conn.execute("SELECT md5 FROM asset_sync_known_md5").fetchall()}
    cx.index_ready = True


def dest_folder(cx: Ctx, product: str, kind: str) -> tuple[str, str]:
    """(folder id, path) of <Product>/<Images|Videos>, from the product slug; Other when unknown. A missing folder is created (adding only)."""
    pname = PRODUCT_FOLDERS.get((product or "").strip().lower(), "Other")
    key = (pname, kind)
    if key not in cx.folders:
        pid = cx.folders.get((pname,)) or cx.drive.find_folder(ROOT_ID, pname) or cx.drive.create_folder(ROOT_ID, pname)
        cx.folders[(pname,)] = pid
        cx.folders[key] = cx.drive.find_folder(pid, kind) or cx.drive.create_folder(pid, kind)
    return cx.folders[key], f"{pname}/{kind}"


def _file_row(cx, link, source_key, name, mime, size, md5, status, dest_id="", path="", reason="", failed=False):
    cx.conn.execute(
        """INSERT INTO asset_sync_files (assignment_id, url, source_key, source_name, mime, size_bytes, md5, status, dest_file_id, dest_path, reason, attempts, last_attempt_at, synced_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), CASE WHEN %s = 'synced' THEN now() END)
           ON CONFLICT (assignment_id, source_key) DO UPDATE SET source_name = EXCLUDED.source_name, mime = EXCLUDED.mime, size_bytes = EXCLUDED.size_bytes, md5 = EXCLUDED.md5, status = EXCLUDED.status,
             dest_file_id = EXCLUDED.dest_file_id, dest_path = EXCLUDED.dest_path, reason = EXCLUDED.reason, last_attempt_at = now(),
             attempts = CASE WHEN EXCLUDED.status = 'failed' THEN asset_sync_files.attempts + 1 ELSE 0 END, synced_at = EXCLUDED.synced_at""",
        (link["assignment_id"], link["url"], source_key, name[:500], mime or "", size, md5, status, dest_id, path, reason[:500], 1 if failed else 0, status))


def _record_known(cx, md5, fid, name):
    if md5:
        cx.known.add(md5)
        cx.conn.execute("INSERT INTO asset_sync_known_md5 (md5, drive_file_id, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (md5, fid, name[:300]))


def _desc(link) -> str:
    return f"Ticket {link['assignment_id']} {link['concept']}".strip()[:200] + f". Synced from {link['url']}"[:700]


def place_file(cx: Ctx, link: dict, key: str, name: str, mime: str, size, md5: str | None, *, drive_id: str | None = None, local: str | None = None, typ: str) -> str:
    """Put one file in Lonvera - Assets unless its md5 is already there. Returns 'synced' | 'duplicate' | 'skipped' | 'failed'. Records the file row."""
    bt = cx.bt(typ)
    kind = media_kind(mime, name)
    if kind is None:
        _file_row(cx, link, key, name, mime, size, md5, "skipped", reason="not an image or video")
        cx.stats["skipped"] += 1
        return "skipped"
    if md5 and md5 in cx.known:
        row = cx.conn.execute("SELECT drive_file_id FROM asset_sync_known_md5 WHERE md5 = %s", (md5,)).fetchone()
        _file_row(cx, link, key, name, mime, size, md5, "duplicate", dest_id=(row or {}).get("drive_file_id", ""), reason="same md5 already in Lonvera - Assets")
        cx.stats["duplicate"] += 1
        bt["duplicate"] += 1
        return "duplicate"
    if cx.dry_run:
        cx.stats["copied"] += 1
        return "synced"
    folder, path = dest_folder(cx, link["product"], kind)
    try:
        if drive_id:
            try:
                new = cx.drive.copy(drive_id, folder, name, _desc(link))
            except LinkError:
                tmp = os.path.join(cx.tmp, "dl_" + drive_id)                                  # copy refused (copying disabled for viewers etc.): download + upload
                cx.drive.download(drive_id, tmp)
                try:
                    h = hashlib.md5(Path(tmp).read_bytes()).hexdigest() if os.path.getsize(tmp) < CAP_BYTES * 4 else md5
                    new = cx.drive.upload(tmp, name, folder, mime, _desc(link))
                    md5 = md5 or h
                finally:
                    os.path.exists(tmp) and os.remove(tmp)
        else:
            new = cx.drive.upload(local, name, folder, mime, _desc(link))
    except LinkError as e:
        _file_row(cx, link, key, name, mime, size, md5, "failed", reason=str(e), failed=True)
        cx.stats["failed"] += 1
        return "failed"
    _record_known(cx, md5, new, name)
    _file_row(cx, link, key, name, mime, size, md5, "synced", dest_id=new, path=path)
    cx.stats["copied"] += 1
    bt["copied"] += 1
    return "synced"


def _terminal(cx, link, key) -> bool:
    r = cx.conn.execute("SELECT status FROM asset_sync_files WHERE assignment_id = %s AND source_key = %s", (link["assignment_id"], key)).fetchone()
    return bool(r) and r["status"] in ("synced", "duplicate", "skipped")


def drive_candidates(cx: Ctx, url: str, typ: str, max_files: int = 1000) -> list[dict]:
    from .drive_io import DriveError, parse_link
    try:
        _kind, fid = parse_link(url)
    except DriveError as e:
        m = re.search(r"[-\w]{25,}", urlsplit(url).path + "?" + urlsplit(url).query)
        if not m:
            raise LinkError(str(e), permanent=True) from None
        fid = m.group(0)
    meta = cx.drive.get(fid)
    if meta.get("mimeType") == "application/vnd.google-apps.shortcut":
        t = (meta.get("shortcutDetails") or {}).get("targetId")
        if not t:
            raise LinkError("Drive shortcut with no target", permanent=True)
        meta = cx.drive.get(t)
    files, todo, depth = [], [(meta, 0)], 0
    while todo:
        f, d = todo.pop(0)
        if f["mimeType"] == FOLDER:
            if d >= 4:
                continue
            todo += [(c, d + 1) for c in sorted(cx.drive.children(f["id"]), key=lambda x: x.get("name", "").lower())]
        elif f["mimeType"].startswith("application/vnd.google-apps."):
            continue
        else:
            files.append(f)
        if len(files) > max_files:
            break
    return files


def process_link(cx: Ctx, link: dict) -> tuple[str, str, int, int, int]:
    """Look at one link. Returns (status, reason, found, copied, duplicate). Never raises for a link-level problem."""
    typ = link["link_type"]
    url = link["url"]
    found = copied = dup = 0
    cx.stats["links_checked"] += 1
    bt = cx.bt(typ)
    try:
        if typ in SKIP_TYPES:
            return "skipped", SKIP_TYPES[typ], 0, 0, 0
        if typ in ("drive_file", "drive_folder"):
            files = drive_candidates(cx, url, typ)
            found = len(files)
            if not files:
                return "unresolvable", "no image or video files behind this Drive link", 0, 0, 0
            for f in files:
                if _terminal(cx, link, f["id"]):
                    continue
                if cx.out_of_budget():
                    return "failed", "run budget reached; the rest is taken next run", found, copied, dup
                if int(f.get("size") or 0) == 0 and media_kind(f.get("mimeType", ""), f.get("name", "")) is None:
                    _file_row(cx, link, f["id"], f.get("name", ""), f.get("mimeType", ""), None, None, "skipped", reason="not an image or video")
                    cx.stats["skipped"] += 1
                    continue
                load_index(cx)
                res = place_file(cx, link, f["id"], f.get("name", f["id"]), f.get("mimeType", ""), int(f.get("size") or 0), f.get("md5Checksum"), drive_id=f["id"], typ=typ)
                copied += res == "synced"
                dup += res == "duplicate"
            bt["found"] += found
            cx.stats["files_found"] += found
            failed = cx.conn.execute("SELECT count(*) AS n FROM asset_sync_files WHERE assignment_id = %s AND url = %s AND status = 'failed'", (link["assignment_id"], url)).fetchone()["n"]
            return ("failed", f"{failed} file(s) failed; retried next run", found, copied, dup) if failed else ("done", "", found, copied, dup)
        # everything else is one downloaded file
        if _terminal(cx, link, url):
            return "done", "", 1, 0, 0
        if cx.out_of_budget():
            return "failed", "run budget reached; taken next run", 0, 0, 0
        tmpdir = tempfile.mkdtemp(dir=cx.tmp)
        try:
            dest = os.path.join(tmpdir, "file.bin")
            try:
                if typ == "facebook_preview":
                    info = cx.fbp(url, dest)
                elif typ in YT_TYPES:
                    path = cx.ytdlp(url, tmpdir)
                    data = Path(path).read_bytes() if os.path.getsize(path) < 64 * 1024 * 1024 else None
                    h = hashlib.md5(data).hexdigest() if data is not None else _md5_file(path)
                    dest = path
                    info = {"name": Path(path).name.split("_", 1)[-1] if Path(path).name.startswith("yt_") else Path(path).name, "mime": mimetypes.guess_type(path)[0] or "video/mp4", "size": os.path.getsize(path), "md5": h}
                else:
                    info = cx.dl(dropbox_direct(url) if typ == "dropbox" else url, dest)
            except TooBig as e:
                _file_row(cx, link, url, url[-120:], "", None, None, "skipped", reason=str(e))
                cx.stats["skipped"] += 1
                return "skipped", f"over the 150 MB cap: {e}", 1, 0, 0
            found = 1
            bt["found"] += 1
            cx.stats["files_found"] += 1
            load_index(cx)
            res = place_file(cx, link, url, info["name"], info["mime"], info["size"], info["md5"], local=dest, typ=typ)
            if res == "failed":
                return "failed", "upload failed; retried next run", found, 0, 0
            return "done", "", found, int(res == "synced"), int(res == "duplicate")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except LinkError as e:
        return ("unresolvable" if e.permanent else "failed"), str(e)[:500], found, copied, dup
    except Exception as e:                                                                # noqa: BLE001
        return "failed", db.scrub(f"{type(e).__name__}: {e}")[:500], found, copied, dup


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _next_check(status: str, typ: str, attempts: int, link: dict) -> str | None:
    """SQL interval text for next_check_at, or None for never."""
    if status == "skipped":
        return None
    if status == "done":
        if typ == "drive_folder":
            recent = link["updated_at"] is not None and (time.time() - link["updated_at"].timestamp()) < 30 * 86400
            return f"{FOLDER_RECHECK_S if recent else OLD_FOLDER_RECHECK_S} seconds"
        return None
    if status == "unresolvable":
        return f"{UNRESOLVABLE_RECHECK_S} seconds"
    return "0 seconds" if attempts < FAILED_MAX_FAST else "86400 seconds"


def run(conn, drive=None, dry_run: bool = False, **kw) -> dict:
    drive = drive or Drive()
    tmp = tempfile.mkdtemp(prefix="asset_sync_")
    cx = Ctx(conn, drive, dry_run=dry_run, tmp=tmp, **kw)
    try:
        links = ticket_links(conn)
        cx.stats["links_seen"] = len(links)
        by = {(l["assignment_id"], l["url"]): l for l in links}
        for l in links:
            l["link_type"] = classify(l["url"])
            if not dry_run:
                conn.execute("INSERT INTO asset_sync_links (assignment_id, url, link_type, field) VALUES (%s,%s,%s,%s) ON CONFLICT (assignment_id, url) DO UPDATE SET link_type = EXCLUDED.link_type",
                             (l["assignment_id"], l["url"], l["link_type"], l["field"]))
        conn.commit()
        due = conn.execute("SELECT assignment_id, url, attempts FROM asset_sync_links WHERE next_check_at IS NOT NULL AND next_check_at <= now() ORDER BY next_check_at, assignment_id").fetchall() if not dry_run else \
            [{"assignment_id": l["assignment_id"], "url": l["url"], "attempts": 0} for l in links]
        for d in due:
            l = by.get((d["assignment_id"], d["url"]))
            if l is None:
                continue
            if cx.out_of_budget():
                cx.log("  asset_sync: run budget reached, the rest waits for the next run")
                break
            status, reason, found, copied, dup = process_link(cx, l)
            attempts = d["attempts"] + 1 if status == "failed" else 0
            if status == "unresolvable":
                cx.stats["unresolvable_links"] += 1
                cx.bt(l["link_type"])["unresolvable"] += 1
            if not dry_run:
                nxt = _next_check(status, l["link_type"], attempts, l)
                conn.execute("""UPDATE asset_sync_links SET status = %s, reason = %s, files_found = %s, files_copied = files_copied + %s, files_duplicate = files_duplicate + %s, attempts = %s,
                                last_checked_at = now(), next_check_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() + (%s::text)::interval END WHERE assignment_id = %s AND url = %s""",
                             (status, reason, found, copied, dup, attempts, nxt, nxt, d["assignment_id"], d["url"]))
            conn.commit()
            if not dry_run:
                _autotag(cx, d["assignment_id"])
            if status in ("failed", "unresolvable"):
                cx.log(f"  asset_sync: ticket {l['assignment_id']} {l['link_type']} {status}: {reason}")
        if not dry_run:
            _autotag(cx, None)                                                            # new tickets: the Image / video tag from what the ticket itself says
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return cx.stats


def _autotag(cx, aid) -> None:
    """Set the ticket's Image / video tag from the files just copied (or, with aid None, from every untagged ticket's own signals) while it is still empty. Never overwrites a tag, never fails the run."""
    try:
        from . import media_type
        n = media_type.autotag(cx.conn, aid)
        cx.conn.commit()
        cx.stats["media_tagged"] = cx.stats.get("media_tagged", 0) + n
    except Exception as e:                                                                # noqa: BLE001
        cx.conn.rollback()
        cx.log(f"  asset_sync: media tag skipped: {db.scrub(e)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="work out what would be copied; copy and write nothing (the md5 index is still read)")
    ap.add_argument("--max-seconds", type=int, default=600)
    ap.add_argument("--max-copies", type=int, default=300)
    a = ap.parse_args()
    with db.JobRun("asset_sync") as job, psycopg.connect(db.database_url(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
            db.log("  asset_sync: another run is in progress, nothing to do")
            return
        st = run(conn, dry_run=a.dry_run, max_seconds=a.max_seconds, max_copies=a.max_copies)
        job.stats = st
        job.rows_written = st["copied"]
        db.log(f"  {json.dumps(st, default=str)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                                # noqa: BLE001
        db.log("asset_sync FAILED:", db.scrub(e))
        sys.exit(1)
