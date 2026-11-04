"""Lonvera Tag Explorer: Flask app factory."""
from __future__ import annotations

import gzip
import logging
import os
from decimal import Decimal

import psycopg
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import api, auth, db, preview
from .config import load_config
from .queries import ApiError

log = logging.getLogger("tagexp")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# The HTML shell: scripts only from this origin (Chart.js is vendored), ad previews may be framed from Meta, images from here or data: URIs.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
       "frame-src https://*.facebook.com https://*.fbcdn.net https://*.fb.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>Lonvera Tag Explorer</title>
<body style="font:14px system-ui;padding:2rem"><h1>Lonvera Tag Explorer</h1><p>UI not built yet (app/static/index.html missing). The API is live under /api.</p></body>"""


class ApiJSONProvider(DefaultJSONProvider):
    """Decimal -> float, dates -> ISO (Flask's default would emit strings / HTTP dates)."""

    sort_keys = False
    ensure_ascii = False

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config.update(load_config(config))
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is required (set it in the environment; DEV_INSECURE_COOKIES=1 allows a dev default)")
    app.secret_key = app.config["SECRET_KEY"]
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.json = ApiJSONProvider(app)
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)       # one trusted hop (Railway edge)

    db.init_app(app)
    auth.init_auth(app)
    app.extensions["preview"] = preview.PreviewService(app.config.get("META_PREVIEW_TOKEN"))
    app.register_blueprint(api.bp)
    _register_pages(app)
    _register_errors(app)
    _register_headers(app)
    return app


def _register_pages(app: Flask) -> None:
    @app.get("/healthz")
    def healthz():
        ok = db.ping()
        return (jsonify(ok=True), 200) if ok else (jsonify(ok=False), 503)

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nDisallow: /", mimetype="text/plain")

    def shell(*_a, **_k):
        if request.path == "/login" and app.config.get("AUTH_DISABLED"):
            return redirect("/", code=302)             # no login while the app is open
        index = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index):
            resp = send_from_directory(STATIC_DIR, "index.html", max_age=0)
            resp.headers["Cache-Control"] = "no-cache"
        else:
            resp = Response(PLACEHOLDER, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = CSP
        return resp

    for rule in ("/", "/login", "/p/<slug>", "/ad/<id>"):
        app.add_url_rule(rule, endpoint="shell:" + rule, view_func=shell)


def _wants_json(path: str) -> bool:
    return path.startswith(("/api/", "/media/"))


def _register_errors(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def api_error(e: ApiError):
        return jsonify(error=e.message), e.status

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if _wants_json(request.path):
            return jsonify(error=(e.name or "error").lower().replace(" ", "_") if e.code != 404 else "not_found"), e.code
        return e

    @app.errorhandler(psycopg.DataError)
    def bad_value(_e):                      # e.g. a NUL byte or an out-of-range number in a query parameter
        return jsonify(error="invalid parameter value"), 400

    @app.errorhandler(psycopg.errors.QueryCanceled)
    def timeout(_e):
        return jsonify(error="query timed out"), 503

    @app.errorhandler(psycopg.OperationalError)
    def db_down(_e):
        log.exception("database unavailable")
        return jsonify(error="database unavailable"), 503

    @app.errorhandler(Exception)
    def boom(e: Exception):
        log.exception("unhandled error on %s", request.path)
        if _wants_json(request.path):
            return jsonify(error="internal_error"), 500
        return Response("Internal error", status=500, mimetype="text/plain")


def _register_headers(app: Flask) -> None:
    @app.after_request
    def headers(resp: Response):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.path.startswith("/static/") and resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = "no-cache"          # ES modules are not content-hashed: always revalidate (ETag / Last-Modified still apply)
        if not app.config.get("DEV_INSECURE_COOKIES") and request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return _maybe_gzip(resp)


def _maybe_gzip(resp: Response) -> Response:
    """The tree / families payloads are 1-2 MB of repetitive JSON; gzip them for clients that accept it."""
    if (resp.status_code != 200 or resp.mimetype != "application/json" or resp.direct_passthrough or "Content-Encoding" in resp.headers
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()):
        return resp
    body = resp.get_data()
    if len(body) < 2048:
        return resp
    resp.set_data(gzip.compress(body, compresslevel=5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers.add("Vary", "Accept-Encoding")
    return resp
