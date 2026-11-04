"""Projection: scenario-driven model of the Lonvera business (replaces the Lonvera Projections sheet), mounted into the Tag Explorer app.

`register(app)` is called once from create_app. It adds the core API, mounts every discovered module blueprint, installs the write guard rails
(Origin / rate limit / JSON only / size cap / noindex) and serves the SPA shell at /projection and /projection/<module>.
Auth is the app's own: nothing here logs anyone in or out.
"""
from __future__ import annotations

import os

from flask import Flask, Response, send_from_directory

from . import api, registry

PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>Projection</title>
<body style="font:14px system-ui;padding:2rem"><h1>Projection</h1><p>UI not built yet (app/static/projection.html missing). The API is live under /api/projection.</p></body>"""


def register(app: Flask) -> None:
    api.install_guards(app)
    app.register_blueprint(api.bp)
    registry.register_modules(app)
    _register_pages(app)


def _register_pages(app: Flask) -> None:
    def shell(*_a, **_k):
        folder = app.static_folder
        if folder and os.path.isfile(os.path.join(folder, "projection.html")):
            resp = send_from_directory(folder, "projection.html", max_age=0)
            resp.headers["Cache-Control"] = "no-cache"
            return resp
        return Response(PLACEHOLDER, mimetype="text/html")

    for rule in ("/projection", "/projection/<module>"):
        app.add_url_rule(rule, endpoint="projection:shell:" + rule, view_func=shell)
