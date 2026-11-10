"""Read-only Google Drive access for the ad launch worker: turn a pasted Drive link into a list of image and video files, and stream one file to disk. Uses the
service account (same GOOGLE_SA_JSON / GOOGLE_SA_FILE credentials as etl/sheets_io.py) with the drive.readonly scope only, and sets supportsAllDrives on every call.
It never uploads, moves, renames, shares or removes anything, and it never prints the credentials. When Drive says the file is missing or private it returns a plain
message that names the service account email (read from the credentials) so the buyer knows who to share the file or folder with."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

API = "https://www.googleapis.com/drive/v3"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
LOCAL_KEY = Path.home() / ".gcloud-keys" / "shopify-data-451316-d67b76e78c82.json"
FOLDER_MIME = "application/vnd.google-apps.folder"
IMAGE_MAX = 30 * 1024 * 1024
VIDEO_MAX = 500 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff"}
FIELDS = "id,name,mimeType,size"
_ID = r"([A-Za-z0-9_-]{10,})"


class DriveError(Exception):
    """A problem the buyer can act on; the text is plain English."""


def _info() -> dict:
    raw = os.environ.get("GOOGLE_SA_JSON")
    if raw:
        return json.loads(raw)
    key = Path(os.environ.get("GOOGLE_SA_FILE") or LOCAL_KEY)
    if not key.exists():
        raise DriveError("No Google credentials: set GOOGLE_SA_JSON or GOOGLE_SA_FILE.")
    return json.loads(key.read_text())


def service_account_email() -> str:
    """The email to share Drive files and folders with. Read from the credentials, never hard-coded."""
    return str(_info().get("client_email") or "the service account")


_session = None


def _default_session():
    global _session
    if _session is None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        _session = AuthorizedSession(service_account.Credentials.from_service_account_info(_info(), scopes=SCOPES))
    return _session


def parse_link(url: str) -> tuple[str, str]:
    """('file' | 'folder', id) for the common Drive link shapes. Anything else raises DriveError."""
    u = urlparse((url or "").strip())
    if u.scheme in ("http", "https") and u.netloc.lower() in ("drive.google.com", "docs.google.com"):
        m = re.search(r"/folders/" + _ID, u.path)
        if m:
            return "folder", m.group(1)
        m = re.search(r"/file/(?:u/\d+/)?d/" + _ID, u.path)
        if m:
            return "file", m.group(1)
        if u.path.rstrip("/") in ("/open", "/uc"):
            ids = parse_qs(u.query).get("id")
            if ids and re.fullmatch(_ID, ids[0]):
                return "file", ids[0]
    raise DriveError("That is not a Google Drive link. Paste a link to one file, or to a folder, from drive.google.com.")


def _fail(r, what: str) -> DriveError:
    if r.status_code in (403, 404):
        return DriveError(f"Drive says {what} is missing or private. Share it with {service_account_email()} (Viewer is enough) and try again.")
    if r.status_code == 429:
        return DriveError("Google Drive is busy right now. Try again in a minute.")
    return DriveError(f"Google Drive returned an error (HTTP {r.status_code}) while reading {what}. Try again in a minute.")


def _get_json(session, path: str, params: dict, what: str) -> dict:
    r = session.get(f"{API}/{path}", params={**params, "supportsAllDrives": "true"}, timeout=120)
    if r.status_code != 200:
        raise _fail(r, what)
    return r.json()


def _classify(f: dict) -> tuple[dict | None, str | None]:
    """(entry, None) when usable, else (None, reason)."""
    mime, size = f.get("mimeType", ""), int(f.get("size") or 0)
    if mime in IMAGE_TYPES:
        if size > IMAGE_MAX:
            return None, f"image is over 30 MB ({size // (1024 * 1024)} MB)"
        kind = "image"
    elif mime.startswith("video/"):
        if size > VIDEO_MAX:
            return None, f"video is over 500 MB ({size // (1024 * 1024)} MB)"
        kind = "video"
    else:
        return None, "not an image or video"
    return {"id": f["id"], "name": f["name"], "mime": mime, "size": size, "kind": kind}, None


def list_media(url: str, max_files: int = 10, session=None) -> dict:
    """{'files': [{id, name, mime, size, kind}], 'skipped': [{name, reason}]}. A file link gives that one file. A folder gives its direct children only (no subfolders),
    sorted by name, images and videos only; everything else, oversize files and files past `max_files` are listed as skipped with the reason."""
    s = session or _default_session()
    kind, fid = parse_link(url)
    meta = _get_json(s, f"files/{fid}", {"fields": FIELDS}, "that link")
    if kind == "file" and meta.get("mimeType") != FOLDER_MIME:
        entry, why = _classify(meta)
        return {"files": [entry] if entry else [], "skipped": [] if entry else [{"name": meta.get("name", fid), "reason": why}]}
    children, token = [], None
    while True:
        p = {"q": f"'{fid}' in parents and trashed = false", "fields": f"nextPageToken,files({FIELDS})", "pageSize": 1000, "includeItemsFromAllDrives": "true"}
        if token:
            p["pageToken"] = token
        j = _get_json(s, "files", p, "that folder")
        children += j.get("files") or []
        token = j.get("nextPageToken")
        if not token:
            break
    files, skipped = [], []
    for f in sorted(children, key=lambda x: x.get("name", "").lower()):
        entry, why = _classify(f)
        if not entry:
            skipped.append({"name": f.get("name", f["id"]), "reason": why})
        elif len(files) >= max_files:
            skipped.append({"name": entry["name"], "reason": f"over the limit of {max_files} files per launch"})
        else:
            files.append(entry)
    return {"files": files, "skipped": skipped}


def download(file_id: str, dest_path, session=None, chunk: int = 1024 * 1024) -> int:
    """Stream one file to dest_path. Returns the number of bytes written."""
    s = session or _default_session()
    r = s.get(f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}, stream=True, timeout=300)
    if r.status_code != 200:
        raise _fail(r, "that file")
    n = 0
    with open(dest_path, "wb") as out:
        for part in r.iter_content(chunk_size=chunk):
            if part:
                out.write(part)
                n += len(part)
    return n
