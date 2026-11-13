"""Competitor ad media. GET /api/competitors/media/<library_id> returns the stored JPEG of one watched competitor ad (table competitor_ad_media, migration 246); GET /api/competitors/video/<library_id>
returns its saved playable copy (table competitor_ad_video, migration 255) with HTTP Range support so seeking and iOS Safari playback work. Both are written only by etl.competitor_ads, read-only here,
login required like every other /api route (not in auth.PUBLIC_API_RE), and nothing is fetched from Meta in this process."""
from __future__ import annotations

import re

from flask import Blueprint, Response, jsonify, request

from . import db

bp = Blueprint("competitors_media", __name__)

_DIGITS = re.compile(r"[0-9]{1,32}")
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@bp.get("/api/competitors/media/<library_id>")
def competitor_media(library_id: str):
    if not _DIGITS.fullmatch(library_id):
        return jsonify(error="not found"), 404
    row = db.get_conn().execute("SELECT content_type, bytes FROM competitor_ad_media WHERE library_id = %s", (library_id,)).fetchone()
    if row is None:
        return jsonify(error="not found"), 404
    resp = Response(bytes(row["bytes"]), mimetype=row["content_type"])
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


def _parse_range(header: str, size: int):
    """('full', None) without a usable Range header; ('range', (start, end)) inclusive; ('bad', None) for 416. Only a single range is served (a multi-range header reads as the whole file)."""
    if not header:
        return "full", None
    m = _RANGE.fullmatch(header.strip())
    if not m:
        return ("bad", None) if header.strip().lower().startswith("bytes=") and "," not in header else ("full", None)
    a, b = m.groups()
    if a == "" and b == "":
        return "bad", None
    if a == "":                                              # suffix: the last b bytes
        n = int(b)
        if n == 0:
            return "bad", None
        return "range", (max(0, size - n), size - 1)
    start = int(a)
    end = min(int(b), size - 1) if b != "" else size - 1
    if start >= size or end < start:
        return "bad", None
    return "range", (start, end)


@bp.get("/api/competitors/video/<library_id>")
def competitor_video(library_id: str):
    if not _DIGITS.fullmatch(library_id):
        return jsonify(error="not found"), 404
    row = db.get_conn().execute("SELECT content_type, bytes FROM competitor_ad_video WHERE library_id = %s", (library_id,)).fetchone()
    if row is None:
        return jsonify(error="not found"), 404
    data = bytes(row["bytes"])
    size = len(data)
    kind, rng = _parse_range(request.headers.get("Range", ""), size)
    if kind == "bad":
        resp = Response(status=416)
        resp.headers["Content-Range"] = f"bytes */{size}"
    elif kind == "range":
        start, end = rng
        resp = Response(data[start:end + 1], status=206, mimetype=row["content_type"])
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        resp = Response(data, mimetype=row["content_type"])
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(size if kind == "full" else 0 if kind == "bad" else rng[1] - rng[0] + 1)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp
