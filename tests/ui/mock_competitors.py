"""Mock of /api/competitors and /api/competitors/<brand> for tests/ui/mock_server.py: two synthetic weeks, built by the pure functions of app/competitors.py (same shapes as the real routes).
?week=, ?format=video|image honoured. /__test/competitors/chatter?on=1 switches the Chatter section on."""
from __future__ import annotations

import datetime as dt

from flask import jsonify, request

W1, W2 = "2026-11-09", "2026-11-16"


def register(app, deps):
    from app import competitors as c

    st = {"chatter": False}
    brands = [{"brand": "Acme", "product_type": "beef organ capsules", "search_phrases": ["acme"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None},
              {"brand": "Bolt & Co", "product_type": "joint supplement", "search_phrases": ["bolt"], "active": True, "last_ok_at": dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc), "last_error": "blocked"},
              {"brand": "Cedar", "product_type": "liver", "search_phrases": ["cedar"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None}]

    def ad(week, lid, brand, days, fmt, url="https://www.acme.com/p1?x=1", var=1, carried=None, cls=None):
        d = dict(library_id=lid, brand=brand, start_date=dt.date.fromisoformat(week) - dt.timedelta(days=days), days_running=days, variant_count=var, format=fmt, hook=f"{brand} hook {lid}", headline=None,
                 landing_url=url, concept_key=f"{brand}:{lid}", carried_from=carried, cluster=None, pain_point=None, mapped_product=None, is_new_angle=False, new_angle_label=None)
        d.update(cls or {})
        return d

    new_angle = dict(cluster="Joints", pain_point="Knee pain", mapped_product="boneflex", is_new_angle=True, new_angle_label="Marrow for joints")
    ads = {
        W1: [ad(W1, "a1", "Acme", 200, "video"), ad(W1, "a2", "Acme", 5, "image"), ad(W1, "a3", "Acme", 3, "video"), ad(W1, "b1", "Bolt & Co", 30, "image", url="bolt.com/x"), ad(W1, "c1", "Cedar", 40, "video", url="cedar.com")],
        W2: [ad(W2, "a1", "Acme", 207, "video"), ad(W2, "a4", "Acme", 1, "video", url="acme.com/new", cls=new_angle), ad(W2, "a5", "Acme", 1, "image", url="acme.com/new"),
             ad(W2, "b1", "Bolt & Co", 37, "image", url="bolt.com/x", carried=W1), ad(W2, "c1", "Cedar", 47, "video", url="cedar.com")],
    }

    def resolve():
        weeks = sorted(ads, reverse=True)
        wk = request.args.get("week") or weeks[0]
        if wk not in weeks:
            raise deps["Bad"]("no such week")
        fmt = request.args.get("format") or None
        if fmt not in (None, "video", "image"):
            raise deps["Bad"]("format must be video or image")
        older = [w for w in weeks if w < wk]
        prior = older[0] if older else None
        pick = lambda w: [a for a in ads[w] if fmt is None or a["format"] == fmt] if w else None
        return weeks, wk, prior, fmt, pick

    @app.get("/api/competitors")
    def competitors_mock():
        weeks, wk, prior, fmt, pick = resolve()
        out = c.payload_from(weeks, wk, prior, fmt, brands, pick(wk), pick(prior))
        if st["chatter"]:
            out["chatter"] = [{"source": "reddit", "brand": "Acme", "title": "Is Acme worth it?", "url": "https://www.reddit.com/r/x/1", "subreddit": "r/x", "score": 40, "num_comments": 7, "posted_at": None, "summary": None}]
        return jsonify(out)

    @app.get("/api/competitors/<path:brand>")
    def competitor_detail_mock(brand):
        weeks, wk, prior, fmt, pick = resolve()
        b = next((x for x in brands if x["brand"] == brand), None)
        if b is None:
            return jsonify(error="Unknown competitor brand."), 404
        cur = [a for a in pick(wk) if a["brand"] == brand]
        prev = [a for a in pick(prior) if a["brand"] == brand] if prior else None
        return jsonify(c.detail_from(b, wk, prior, fmt, cur, prev))

    @app.post("/__test/competitors/chatter")
    def competitors_chatter():
        st["chatter"] = request.args.get("on") == "1"
        return jsonify(ok=True)
