#!/usr/bin/env python3
"""Mock of the Tag Explorer API (docs/CONTRACT.md v1 + v1.1) for developing and testing the UI.

Serves app/static and canned-but-consistent JSON for every contract endpoint. Numbers are derived from one
ad-level daily table so every level sums to its parent, and the window / stage / from-to params really
change the figures. Login password: `test`.

    .venv/bin/python tests/ui/mock_server.py            # http://127.0.0.1:5099
"""
import io
import json
import math
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory

from mock_app import app  # noqa: F401
from mock_data import *  # noqa: F401,F403
import mock_performance  # noqa: F401,E402  (Overview, Google, Campaigns routes)
import mock_product  # noqa: F401,E402  (products, tag tree, ad page, trend, preview routes)


# ----------------------------------------------------------------------------------------------- routes
@app.errorhandler(Bad)
def _bad(e):
    return jsonify(error=str(e)), 400


@app.before_request
def guard():
    p = request.path
    if p.startswith("/api/priorities/vendor/"):
        pass                                   # a vendor's own link is login-exempt (the token is the credential), same as the real app
    elif (p.startswith("/api/") and p != "/api/login") or p.startswith("/media/"):
        if not AUTH_DISABLED and request.cookies.get("sid") != "ok":
            return jsonify(error="unauthorized"), 401
    g.active = request.args.get("active") == "1"
    g.cmp = None
    if p.startswith("/api/") and any(k in request.args for k in ("compare", "cfrom", "cto")):
        g.cmp = parse_cmp(request.args, parse_win(request.args))
    if p.startswith("/api/") and request.args.get("__delay"):
        import time
        time.sleep(float(request.args["__delay"]))


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    if body.get("username") not in USERS or body.get("password") != PASSWORD:
        return jsonify(error="invalid username or password"), 401
    r = Response(status=204)
    r.set_cookie("sid", "ok", httponly=True, samesite="Lax")
    r.set_cookie("user", body["username"], httponly=True, samesite="Lax")
    return r


@app.post("/api/logout")
def logout():
    r = Response(status=204)
    r.delete_cookie("sid")
    r.delete_cookie("user")
    return r


@app.get("/api/status")
def status():
    extra = dict(auth="disabled", user="guest") if AUTH_DISABLED else dict(user=request.cookies.get("user"))
    extra["stale"] = dict(is_stale=os.environ.get("MOCK_STALE") == "1", reason="the last Meta pull failed (token rejected)" if os.environ.get("MOCK_STALE") == "1" else None)
    extra["last_ok_at"] = "2026-11-02T22:12:00+00:00"
    extra["sources"] = dict(platforms=[dict(key="meta", name="Meta", available=True, first_day=DAILY_START.isoformat(), through=DATA_THROUGH.isoformat()),
                                       dict(key="google", name="Google", available=True, first_day=DAILY_START.isoformat(), through=DATA_THROUGH.isoformat())])
    return jsonify(**extra, data_through=DATA_THROUGH.isoformat(), daily_start=DAILY_START.isoformat(), lifetime_cutoff=CUTOFF.isoformat(),
                   last_refresh=dict(job="run_daily", finished_at="2026-11-03T06:12:00+00:00", status="ok"), products=len(PRODUCTS))


import mock_promotions  # noqa: E402  (Test to scale: /api/promotions, built on the real payload code in app/promotions.py)

mock_promotions.register(app, dict(parse_win=parse_win, Bad=Bad))

import mock_trends  # noqa: E402  (Trends: /api/trends, built on the real payload code in app/trends.py over made-up days)

mock_trends.register(app, dict(Bad=Bad))

import mock_inventory  # noqa: E402  (Inventory history: /api/inventory/history, built on the real payload code in app/inventory_history.py over made-up snapshot rows)

mock_inventory.register(app, dict(Bad=Bad))

import mock_daily  # noqa: E402  (Daily story: /api/daily-story, built with the real facts builder over a made-up panel)

mock_daily.register(app, dict(Bad=Bad))

import mock_email  # noqa: E402  (Email: /api/email/*, built on the real payload code in app/email_view.py over made-up Klaviyo days)

mock_email.register(app, dict(Bad=Bad))

import mock_lookup  # noqa: E402  (Tag lookup: /api/tag-lookup, built on the real matching code in app/lookup.py over a few made-up rows)

mock_lookup.register(app, dict(Bad=Bad))

import mock_priorities  # noqa: E402  (Creative priorities: /api/priorities from a trimmed real snapshot)

mock_priorities.register(app, dict(Bad=Bad))

import mock_production_vendor  # noqa: E402  (Production vendor page "My tasks": public vendor routes with status / comments, registered first so its tokens win)

mock_production_vendor.register(app, dict(Bad=Bad))

import mock_priorities_assign  # noqa: E402  (Production: /api/priorities/assignments, vendor-links, vendor/<token>, in memory)

mock_priorities_assign.register(app, dict(Bad=Bad))

import mock_production_board  # noqa: E402  (Production board: status / due / priority on assignments, /board, /move, comments, vendor-capacity; needs mock_priorities_assign's state)

mock_production_board.register(app, dict(Bad=Bad))

import mock_priorities_launch  # noqa: E402  (Production launch to Meta: /api/priorities/.../launch and the `launch` flag on the assignments list, in memory, off by default)

mock_priorities_launch.register(app, dict(Bad=Bad))

import mock_competitors  # noqa: E402  (Competitors: /api/competitors and /<brand>, two synthetic weeks)

mock_competitors.register(app, dict(Bad=Bad))


def shell(*_a, **_k):
    return send_from_directory(STATIC, "index.html")


for rule in ("/", "/login", "/google", "/campaigns", "/promotions", "/trends", "/daily", "/email", "/inventory/history", "/priorities", "/priorities/v/<tok>", "/priorities/production", "/competitors", "/priorities/vendor/<tok>", "/lookup", "/p/<slug>", "/ad/<rid>"):
    app.add_url_rule(rule, endpoint="shell" + rule, view_func=shell)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5099
    sys.path.insert(0, str(ROOT))
    from tests._servers import make_server          # keep-alive server: werkzeug's closes every connection, which exhausts the ephemeral ports in a parallel run
    make_server(os.environ.get("MOCK_HOST", "127.0.0.1"), port, app).serve_forever()
