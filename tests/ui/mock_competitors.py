"""Mock of /api/competitors, /api/competitors/<brand> and /api/competitors/media/<id> for tests/ui/mock_server.py: two synthetic weeks, built by the pure functions of app/competitors.py (same shapes as the real routes).
?week=, ?format=video|image honoured. /__test/competitors/chatter?on=1 switches the Chatter section on; /__test/competitors/media?on=0 removes every thumbnail (default on: a few ads have one, like production after the first media run)."""
from __future__ import annotations

import datetime as dt
import struct
import zlib

from flask import Response

from flask import jsonify, request

W1, W2 = "2026-11-09", "2026-11-16"


def png(seed: int, w=160, h=200) -> bytes:
    """A small generated gradient PNG (stands in for the JPEG thumbnail the real route serves)."""
    r0, g0, b0 = (seed * 53) % 200 + 30, (seed * 97) % 200 + 30, (seed * 29) % 200 + 30
    rows = b"".join(b"\x00" + b"".join(bytes(((r0 + x // 2 + y // 3) % 256, (g0 + y // 2) % 256, (b0 + x // 3) % 256)) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def register(app, deps):
    from app import competitors as c

    st = {"chatter": False, "media": True}
    brands = [{"brand": "Acme", "product_type": "beef organ capsules", "search_phrases": ["acme"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None},
              {"brand": "Bolt & Co", "product_type": "joint supplement", "search_phrases": ["bolt"], "active": True, "last_ok_at": dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc), "last_error": "blocked"},
              {"brand": "Cedar", "product_type": "liver", "search_phrases": ["cedar"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None}]
    for nm, pt in (("Dune Labs", "hair supplement"), ("Ember Health", "joint chews"), ("Fig & Root", "ancestral nutrition"), ("Gild", "collagen powder")):
        brands.append({"brand": nm, "product_type": pt, "search_phrases": [nm.lower()], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None})

    def ad(week, lid, brand, days, fmt, url="https://www.acme.com/p1?x=1", var=1, carried=None, cls=None, hook=None, text=None, head=None, thumb=False):
        d = dict(library_id=lid, brand=brand, start_date=dt.date.fromisoformat(week) - dt.timedelta(days=days), days_running=days, variant_count=var, format=fmt, hook=hook or f"{brand} hook {lid}", headline=head,
                 primary_text=text or (hook or f"{brand} hook {lid}") + "\n\nSee why thousands switched. Free shipping on your first order.", base_thumb=thumb,
                 landing_url=url, concept_key=f"{brand}:{lid}", carried_from=carried, cluster=None, pain_point=None, mapped_product=None, is_new_angle=False, new_angle_label=None, has_thumb=False)
        d.update(cls or {})
        return d

    new_angle = dict(cluster="Joints", pain_point="Knee pain", mapped_product="boneflex", is_new_angle=True, new_angle_label="Marrow for joints")
    hair = dict(cluster="Hair thinning", pain_point="Shedding after GLP-1", mapped_product="beef-liver", is_new_angle=False)
    iron = dict(cluster="Iron and B12", pain_point="Low ferritin", mapped_product="beef-liver", is_new_angle=False)
    energy = dict(cluster="Energy", pain_point="Afternoon crash", mapped_product="vita-bull", is_new_angle=False)
    gut = dict(cluster="Gut health", pain_point="Bloating after meals", mapped_product="none", is_new_angle=True, new_angle_label="Gut reset")
    extra1 = [ad(W1, "d1", "Dune Labs", 310, "video", "dune.co/hair", hook="I stopped the shedding in 90 days and my stylist noticed first", thumb=True, cls=hair),
              ad(W1, "d2", "Dune Labs", 120, "image", "dune.co/hair", hook="Your hair is a ferritin problem", thumb=True, cls=iron),
              ad(W1, "e1", "Ember Health", 90, "video", "ember.com/chews", hook="Knees that do not announce the stairs", cls=new_angle),
              ad(W1, "e2", "Ember Health", 60, "image", "ember.com/chews", hook="Joint chews that taste like candy", cls=new_angle),
              ad(W1, "e3", "Ember Health", 45, "image", "ember.com/chews", hook="Two chews a day", cls=new_angle),
              ad(W1, "f1", "Fig & Root", 150, "video", "figroot.com", hook="What our great-grandparents ate for breakfast", thumb=True, cls=energy),
              ad(W1, "f2", "Fig & Root", 25, "image", "figroot.com/gut", hook="Bloated after every meal? Try this", cls=gut),
              ad(W1, "g1", "Gild", 75, "image", "gild.com/c", hook="Collagen is not a magic powder. Here is what it does.", thumb=True, cls=hair)]
    extra2 = [ad(W2, "d1", "Dune Labs", 317, "video", "dune.co/hair", hook="I stopped the shedding in 90 days and my stylist noticed first", thumb=True, cls=hair),
              ad(W2, "d2", "Dune Labs", 127, "image", "dune.co/hair", hook="Your hair is a ferritin problem", thumb=True, cls=iron),
              ad(W2, "e1", "Ember Health", 97, "video", "ember.com/chews", hook="Knees that do not announce the stairs", cls=new_angle),
              ad(W2, "e2", "Ember Health", 67, "image", "ember.com/chews", hook="Joint chews that taste like candy", cls=new_angle),
              ad(W2, "e4", "Ember Health", 2, "video", "ember.com/new", hook="Test: marrow vs glucosamine, 30 days", cls=new_angle),
              ad(W2, "e5", "Ember Health", 2, "image", "ember.com/new", hook="The joint aisle is lying to you", cls=new_angle),
              ad(W2, "e6", "Ember Health", 1, "image", "ember.com/new", hook="Founder story: why we dropped sugar", cls=new_angle),
              ad(W2, "e7", "Ember Health", 1, "video", "ember.com/new", hook="Unboxing the new flavor", cls=new_angle),
              ad(W2, "f1", "Fig & Root", 157, "video", "figroot.com", hook="What our great-grandparents ate for breakfast", thumb=True, cls=energy),
              ad(W2, "f2", "Fig & Root", 32, "image", "figroot.com/gut", hook="Bloated after every meal? Try this", cls=gut),
              ad(W2, "g1", "Gild", 82, "image", "gild.com/c", hook="Collagen is not a magic powder. Here is what it does.", thumb=True, cls=hair)]
    ads = {
        W1: [ad(W1, "a1", "Acme", 200, "video", hook="Your liver is not a filter, it is a vitamin warehouse", head="Beef liver, finally in a capsule", thumb=True), ad(W1, "a2", "Acme", 5, "image"), ad(W1, "a3", "Acme", 3, "video"), ad(W1, "b1", "Bolt & Co", 30, "image", url="bolt.com/x"), ad(W1, "c1", "Cedar", 40, "video", url="cedar.com")] + extra1,
        W2: [ad(W2, "a1", "Acme", 207, "video", hook="Your liver is not a filter, it is a vitamin warehouse", head="Beef liver, finally in a capsule", thumb=True), ad(W2, "a4", "Acme", 1, "video", url="acme.com/new", cls=new_angle, hook="Your knees are asking for marrow", thumb=True), ad(W2, "a5", "Acme", 1, "image", url="acme.com/new"),
             ad(W2, "b1", "Bolt & Co", 37, "image", url="bolt.com/x", carried=W1), ad(W2, "c1", "Cedar", 47, "video", url="cedar.com", hook="Three eggs and a steak is not a supplement plan", thumb=True, cls=energy)] + extra2,
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
        for lst in ads.values():
            for a in lst:
                a["has_thumb"] = bool(a["base_thumb"]) and st["media"]
        pick = lambda w: [a for a in ads[w] if fmt is None or a["format"] == fmt] if w else None
        return weeks, wk, prior, fmt, pick

    @app.get("/api/competitors")
    def competitors_mock():
        weeks, wk, prior, fmt, pick = resolve()
        out = c.payload_from(weeks, wk, prior, fmt, brands, pick(wk), pick(prior))
        if st["chatter"]:
            out["chatter"] = [{"source": "reddit", "brand": "Acme", "title": "Is Acme worth it?", "url": "https://www.reddit.com/r/x/1", "subreddit": "r/x", "score": 40, "num_comments": 7, "posted_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat(), "summary": None},
                              {"source": "news", "brand": "Dune Labs", "title": "Dune Labs raises a round for its hair line", "url": "https://news.example.com/dune", "subreddit": None, "score": None, "num_comments": None, "posted_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=6)).isoformat(), "summary": None}]
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

    @app.get("/api/competitors/media/<path:lid>")
    def competitors_media_mock(lid):
        known = {a["library_id"] for lst in ads.values() for a in lst if a["base_thumb"]}
        if not st["media"] or lid not in known:
            return jsonify(error="No image."), 404
        return Response(png(sum(map(ord, lid))), mimetype="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/__test/competitors/media")
    def competitors_media_toggle():
        st["media"] = request.args.get("on") != "0"
        return jsonify(ok=True)

    @app.post("/__test/competitors/chatter")
    def competitors_chatter():
        st["chatter"] = request.args.get("on") == "1"
        return jsonify(ok=True)
