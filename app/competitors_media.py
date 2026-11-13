"""Competitor ad thumbnails: GET /api/competitors/media/<library_id> returns the stored JPEG of one watched competitor ad (table competitor_ad_media, migration 246, written only by
etl.competitor_ads). Read-only, login required like every other /api route (not in auth.PUBLIC_API_RE), nothing is fetched from Meta here."""
from __future__ import annotations

import re

from flask import Blueprint, Response, jsonify

from . import db

bp = Blueprint("competitors_media", __name__)

_DIGITS = re.compile(r"[0-9]{1,32}")


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
