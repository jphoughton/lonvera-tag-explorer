"""Mock of the Theme previews API for tests/ui/mock_server.py: /api/theme/* in memory, no Shopify, no database. Off by default (the real default).
Switch with POST /__test/theme/config (JSON: enabled, can_edit); POST /__test/theme/reset restores it; GET /__test/theme/calls lists the saves the page sent."""
from __future__ import annotations

from flask import jsonify, request

S: dict = {}


def reset():
    S.clear()
    S.update(cfg={"enabled": False, "can_edit": True}, previews=[], calls=[], files={"sections/header.liquid": "<header>Hello</header>", "assets/theme.css": "body { margin: 0; }"})


reset()


def discovery():
    c = S["cfg"]
    return {"enabled": c["enabled"], "can_edit": c["enabled"] and c["can_edit"]}


def register(app, deps):
    def gate():
        if not S["cfg"]["enabled"]:
            return jsonify(error="not found"), 404
        if not S["cfg"]["can_edit"]:
            return jsonify(error="Your login is not allowed to edit themes."), 403
        return None

    @app.get("/api/theme/status")
    def theme_mock_status():
        return jsonify(discovery()) if S["cfg"]["enabled"] else (jsonify(error="not found"), 404)

    @app.get("/api/theme/themes")
    def theme_mock_themes():
        g = gate()
        return g or jsonify(previews=S["previews"], live={"id": "1", "name": "Live theme", "role": "MAIN"})

    @app.post("/api/theme/previews")
    def theme_mock_new():
        g = gate()
        if g:
            return g
        n = len(S["previews"]) + 100
        p = {"id": str(n), "name": "preview-guest-20261118-" + request.get_json()["name"].lower().replace(" ", "-"), "created_by": "guest", "created_at": "2026-11-18T10:00:00+00:00",
             "preview_url": f"https://example-store.myshopify.com/?preview_theme_id={n}"}
        S["previews"].insert(0, p)
        return jsonify(p), 201

    @app.get("/api/theme/previews/<pid>/files")
    def theme_mock_files(pid):
        g = gate()
        if g:
            return g
        key = request.args.get("key")
        if key is None:
            return jsonify(files=[{"key": k, "size": len(v)} for k, v in sorted(S["files"].items())])
        return jsonify(key=key, content=S["files"][key]) if key in S["files"] else (jsonify(error="File not found."), 404)

    @app.put("/api/theme/previews/<pid>/files")
    def theme_mock_put(pid):
        g = gate()
        if g:
            return g
        b = request.get_json()
        S["files"][b["key"]] = b["content"]
        S["calls"].append({"id": pid, "key": b["key"], "content": b["content"]})
        return jsonify(ok=True, key=b["key"], size=len(b["content"]))

    @app.post("/__test/theme/config")
    def theme_mock_cfg():
        S["cfg"].update(request.get_json() or {})
        return jsonify(ok=True)

    @app.post("/__test/theme/reset")
    def theme_mock_reset():
        reset()
        return jsonify(ok=True)

    @app.get("/__test/theme/calls")
    def theme_mock_calls():
        return jsonify(S["calls"])
