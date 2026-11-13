"""(Video: ads with base_video have a saved copy, a tiny webm served with HTTP Range at /api/competitors/video/<id>; /__test/competitors/video?on=0 hides them all.)
Mock of /api/competitors, /api/competitors/<brand> and /api/competitors/media/<id> for tests/ui/mock_server.py: two synthetic weeks, built by the pure functions of app/competitors.py (same shapes as the real routes).
?week=, ?format=video|image honoured. /__test/competitors/tags?mode=on|empty|off switches the model-read tags (default off: no tags table) and /__test/competitors/lp?on=1 the tagged landing pages (default off); /__test/competitors/chatter?on=1 switches the Chatter section on; /__test/competitors/media?on=0 removes every thumbnail (default on: a few ads have one, like production after the first media run)."""
from __future__ import annotations

import datetime as dt
import struct
import zlib
from pathlib import Path

from flask import Response, send_file

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

    st = {"chatter": False, "media": True, "video": True, "tags": "off", "lp": False}
    tiny = Path(__file__).with_name("competitor_tiny.webm")
    brands = [{"brand": "Acme", "product_type": "beef organ capsules", "search_phrases": ["acme"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None},
              {"brand": "Bolt & Co", "product_type": "joint supplement", "search_phrases": ["bolt"], "active": True, "last_ok_at": dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc), "last_error": "blocked"},
              {"brand": "Cedar", "product_type": "liver", "search_phrases": ["cedar"], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None}]
    for nm, pt in (("Dune Labs", "hair supplement"), ("Ember Health", "joint chews"), ("Fig & Root", "ancestral nutrition"), ("Gild", "collagen powder")):
        brands.append({"brand": nm, "product_type": pt, "search_phrases": [nm.lower()], "active": True, "last_ok_at": dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc), "last_error": None})

    def ad(week, lid, brand, days, fmt, url="https://www.acme.com/p1?x=1", var=1, carried=None, cls=None, hook=None, text=None, head=None, thumb=False, video=False):
        d = dict(library_id=lid, brand=brand, start_date=dt.date.fromisoformat(week) - dt.timedelta(days=days), days_running=days, variant_count=var, format=fmt, hook=hook or f"{brand} hook {lid}", headline=head,
                 primary_text=text or (hook or f"{brand} hook {lid}") + "\n\nSee why thousands switched. Free shipping on your first order.", base_thumb=thumb, base_video=video,
                 landing_url=url, concept_key=f"{brand}:{lid}", carried_from=carried, cluster=None, pain_point=None, mapped_product=None, is_new_angle=False, new_angle_label=None, has_thumb=False, has_video=False)
        d.update(cls or {})
        return d

    new_angle = dict(cluster="Joints", pain_point="Knee pain", mapped_product="boneflex", is_new_angle=True, new_angle_label="Marrow for joints")
    hair = dict(cluster="Hair thinning", pain_point="Shedding after GLP-1", mapped_product="beef-liver", is_new_angle=False)
    iron = dict(cluster="Iron and B12", pain_point="Low ferritin", mapped_product="beef-liver", is_new_angle=False)
    energy = dict(cluster="Energy", pain_point="Afternoon crash", mapped_product="vita-bull", is_new_angle=False)
    gut = dict(cluster="Gut health", pain_point="Bloating after meals", mapped_product="none", is_new_angle=True, new_angle_label="Gut reset")
    extra1 = [ad(W1, "d1", "Dune Labs", 310, "video", "dune.co/hair", hook="I stopped the shedding in 90 days and my stylist noticed first", thumb=True, video=True, cls=hair),
              ad(W1, "d2", "Dune Labs", 120, "image", "dune.co/hair", hook="Your hair is a ferritin problem", thumb=True, cls=iron),
              ad(W1, "e1", "Ember Health", 90, "video", "ember.com/chews", hook="Knees that do not announce the stairs", cls=new_angle),
              ad(W1, "e2", "Ember Health", 60, "image", "ember.com/chews", hook="Joint chews that taste like candy", cls=new_angle),
              ad(W1, "e3", "Ember Health", 45, "image", "ember.com/chews", hook="Two chews a day", cls=new_angle),
              ad(W1, "f1", "Fig & Root", 150, "video", "figroot.com", hook="What our great-grandparents ate for breakfast", thumb=True, cls=energy),
              ad(W1, "f2", "Fig & Root", 25, "image", "figroot.com/gut", hook="Bloated after every meal? Try this", cls=gut),
              ad(W1, "g1", "Gild", 75, "image", "gild.com/c", hook="Collagen is not a magic powder. Here is what it does.", thumb=True, cls=hair)]
    extra2 = [ad(W2, "d1", "Dune Labs", 317, "video", "dune.co/hair", hook="I stopped the shedding in 90 days and my stylist noticed first", thumb=True, video=True, cls=hair),
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


    # ---- learning layer fixtures: tags for every W2 ad (7 of 16 are Testimonial, so the share chart highlights it against Lonvera's 9%), transcripts, landing pages
    T = {  # lid: (style, hook_type, device, offer_type, proof, emotion)
        "a1": ("Testimonial", "Question", "Talking head", "Discount", "Reviews", "Relief"), "a4": ("Testimonial", "Statement", "Voiceover", "Discount", "Doctor quote", "Fear"),
        "a5": ("Studio", "Offer", "Text on screen", "Bundle", "Reviews", "Desire"), "b1": ("Testimonial", "Story", "Talking head", None, "Reviews", "Relief"),
        "c1": ("Testimonial", "Question", "Voiceover", "Discount", "Reviews", "Frustration"), "d1": ("Testimonial", "Story", "Talking head", "Bundle", "Reviews", "Relief"),
        "d2": ("UGC demo", "Question", "Split screen", "Discount", "Before and after", "Desire"), "e1": ("UGC demo", "Question", "Split screen", "Bundle", "Before and after", "Frustration"),
        "e2": ("Studio", "Statement", "Text on screen", "Discount", "Reviews", "Comparison"), "e4": ("Testimonial", "Story", "Talking head", "Bundle", "Reviews", "Relief"),
        "e5": ("Studio", "Offer", "Text on screen", None, "Reviews", "Desire"), "e6": ("UGC demo", "Story", "Talking head", None, "Founder", "Pride"),
        "e7": ("UGC demo", "Question", "Split screen", None, "Reviews", "Frustration"), "f1": ("Testimonial", "Story", "Voiceover", None, "Reviews", "Relief"),
        "f2": ("Studio", "Statement", "Text on screen", None, "Reviews", "Comparison"), "g1": ("UGC demo", "Statement", "Split screen", "Discount", "Before and after", "Desire"),
    }
    WHY = {"a1": "Names the reader's pain in the first second, then lets a customer prove it.", "d1": "A before-and-after story from a real customer carries the claim."}
    SAID = {"a1": "Your liver is not a filter, it is a vitamin warehouse.", "d1": "I stopped the shedding in 90 days."}
    TRANSCRIPT = {"a1": "Your liver is not a filter, it is a vitamin warehouse. Beef liver has more of the nutrients you are missing than any pill. Try it for 30 days.", "d1": "I stopped the shedding in 90 days and my stylist noticed first. Here is what I changed."}
    AD_LP = {"a1": "lpA1", "a4": "lpA1", "a5": "lpA2", "d1": "lpD1", "d2": "lpD1", "e1": "lpE1", "e2": "lpE1"}
    LPS = [
        dict(lp_id="lpA1", brand="Acme", final_url="https://acme.com/join", ad_count=3, page_type="Advertorial", key_theme="Your joints are starving for marrow, not more glucosamine", hero_headline="Why your knees ache on the stairs",
             selling_points=["Grass-fed bone marrow", "No fillers or binders", "Made in the USA", "Third party tested", "60 day guarantee", "Extra point six"], offer_summary="Buy 2 get 1 free", price_text="$49 a bottle", guarantee="60-day money back",
             proof_elements=["Reviews", "Doctor quote"], urgency_devices=["Countdown timer"], objections=["Does it work for me", "Is it safe with my medication"], cta="Start my order", why_it_works="Opens with a story the reader already lives, then offers a risk-free trial.",
             cluster="Joints", pain_point="Knee pain", changed_from_prior=True, change_note="New hero headline and a countdown timer", has_media=True),
        dict(lp_id="lpA2", brand="Acme", final_url="https://acme.com/shop", ad_count=1, page_type="Product page", key_theme="One simple bottle, one simple promise", hero_headline="Beef liver, finally in a capsule",
             selling_points=["Liver and heart in one", "No taste"], offer_summary=None, price_text="$39", guarantee="30 day guarantee", proof_elements=["Reviews"], urgency_devices=[], changed_from_prior=False, has_media=False),
        dict(lp_id="lpD1", brand="Dune Labs", final_url="https://dune.co/hair", ad_count=2, page_type="Quiz funnel", key_theme="Find your hair type, then the fix", hero_headline="Take the 60 second hair quiz", selling_points=["Personal plan", "Dermatologist built"],
             guarantee="90 days", proof_elements=["Reviews", "Before and after"], urgency_devices=["Low stock"], changed_from_prior=False, has_media=False),
        dict(lp_id="lpE1", brand="Ember Health", final_url="https://ember.com/chews", ad_count=2, page_type="Product page", key_theme="Joint chews that taste like candy", hero_headline="Chews, not pills", selling_points=["Tastes like candy", "No sugar"],
             guarantee="30 day guarantee", proof_elements=["Reviews"], urgency_devices=["Countdown timer", "Low stock"], changed_from_prior=True, change_note="Price dropped to $29", has_media=False),
    ]
    LONVERA = {"through": "2026-11-15", "style": {"testimonial": ("Testimonial", 0.09), "ugc demo": ("UGC demo", 0.5), "studio": ("Studio", 0.41)},
               "proof_mechanism": {"reviews": ("Reviews", 0.7), "doctor quote": ("Doctor quote", 0.3)}, "emotion": {"relief": ("Relief", 0.2), "fear": ("Fear", 0.3), "desire": ("Desire", 0.5)}}

    def apply_tags():
        for lst in ads.values():
            for a in lst:
                t = T.get(a["library_id"]) if st["tags"] == "on" else None
                keys = ("style", "hook_type", "device", "offer_type", "proof_mechanism", "emotion")
                for k in keys + ("why_it_works", "seen_on", "said_on", "hook_text", "cluster", "pain_point", "is_new_angle", "new_angle_label"):
                    a.pop("tg_" + k, None)
                a["tg_present"] = bool(t)
                if t:
                    a.update({"tg_" + k: v for k, v in zip(keys, t)})
                    a["tg_why_it_works"], a["tg_said_on"] = WHY.get(a["library_id"]), SAID.get(a["library_id"])
                    if a["library_id"] == "a1":
                        a["tg_seen_on"], a["tg_hook_text"] = "A woman holds a bottle in a bright kitchen", "Your liver is not a filter"
                a["tr_transcript"] = TRANSCRIPT.get(a["library_id"]) if st["tags"] == "on" else None
                lp = AD_LP.get(a["library_id"]) if st["lp"] else None
                a["lp_id"] = lp
                a["lp_page_type"] = next((x["page_type"] for x in LPS if x["lp_id"] == lp), None)
                a["lp_key_theme"] = next((x["key_theme"] for x in LPS if x["lp_id"] == lp), None)

    def ctx_for(wk, brand=None):
        mode = st["tags"]
        ctx = {"tags_table": mode != "off", "filters": c.learn.filters_from(request.args), "lp_linked": st["lp"]}
        if mode != "off":
            ctx["week_tagged"] = sum(1 for a in ads[wk] if a["library_id"] in T) if mode == "on" else 0
            if mode == "on" and not brand:
                ctx["lonvera"] = LONVERA
        ctx["lp_rows"] = [dict(x, ad_count=x["ad_count"]) for x in LPS if st["lp"] and wk == W2 and (brand is None or x["brand"] == brand)]
        return ctx

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
                a["has_video"] = bool(a["base_video"]) and st["video"]
        apply_tags()
        pick = lambda w: [a for a in ads[w] if fmt is None or a["format"] == fmt] if w else None
        return weeks, wk, prior, fmt, pick

    @app.get("/api/competitors")
    def competitors_mock():
        weeks, wk, prior, fmt, pick = resolve()
        out = c.payload_from(weeks, wk, prior, fmt, brands, pick(wk), pick(prior), ctx_for(wk))
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
        return jsonify(c.detail_from(b, wk, prior, fmt, cur, prev, ctx_for(wk, brand)))

    @app.get("/api/competitors/media/<path:lid>")
    def competitors_media_mock(lid):
        known = {a["library_id"] for lst in ads.values() for a in lst if a["base_thumb"]}
        if not st["media"] or lid not in known:
            return jsonify(error="No image."), 404
        return Response(png(sum(map(ord, lid))), mimetype="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/competitors/video/<path:lid>")
    def competitors_video_mock(lid):
        known = {a["library_id"] for lst in ads.values() for a in lst if a["base_video"]}
        if not st["video"] or lid not in known:
            return jsonify(error="not found"), 404
        return send_file(tiny, mimetype="video/webm", conditional=True, max_age=0)

    @app.post("/__test/competitors/video")
    def competitors_video_toggle():
        st["video"] = request.args.get("on") != "0"
        return jsonify(ok=True)

    @app.post("/__test/competitors/media")
    def competitors_media_toggle():
        st["media"] = request.args.get("on") != "0"
        return jsonify(ok=True)

    @app.post("/__test/competitors/chatter")
    def competitors_chatter():
        st["chatter"] = request.args.get("on") == "1"
        return jsonify(ok=True)

    @app.post("/__test/competitors/tags")
    def competitors_tags_toggle():
        st["tags"] = request.args.get("mode") if request.args.get("mode") in ("on", "empty") else "off"
        return jsonify(ok=True)

    @app.post("/__test/competitors/lp")
    def competitors_lp_toggle():
        st["lp"] = request.args.get("on") == "1"
        return jsonify(ok=True)

    @app.get("/api/competitors/lp-media/<path:lid>")
    def competitors_lp_media_mock(lid):
        if not st["lp"] or lid != "lpA1":
            return jsonify(error="No image."), 404
        return Response(png(7, 200, 150), mimetype="image/png", headers={"Cache-Control": "private, max-age=86400"})
