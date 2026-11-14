"""Mock of our own ad learning tags: GET /api/products/<slug>/learning-tags (canned numbers, same shape as app/own_tags.cuts) and the `learning` block of the ad page.
POST /__test/own_tags?mode=on|nc_off|off switches it (default off: the learning table does not exist yet, so the Tag Explorer pages are exactly as before)."""
from flask import jsonify, request

from mock_app import app
from mock_data import FAMS

STATE = {"mode": "off"}


def _side(spend, purchases, revenue, imp, clk):
    sp_cplc = round(spend / clk, 4)
    return {"spend": spend, "purchases": purchases, "revenue": revenue, "roas": round(revenue / spend, 4), "cpa": round(spend / purchases, 2), "ctr": round(clk / imp, 5), "cpm": round(spend * 1000 / imp, 4),
            "cplc": sp_cplc, "conv_rate": round(purchases / clk, 5), "aov": round(revenue / purchases, 2), "link_clicks": clk, "impressions": imp}


def _row(value, ads, spend, total, meta, nc):
    return {"value": value, "unique_ads": ads, "spend": spend, "spend_share": round(spend / total, 6), "meta": _side(spend, *meta) if meta else None, "nc": _side(spend, *nc) if nc else None}


TOTAL = 20000.0
# value -> (ad families, spend, (Meta purchases, revenue, impressions, link clicks), (NC purchases, revenue, impressions, link clicks))
HOOK = [("Question", 6, 9000.0, (180, 9450.0, 700000, 11000), (90, 7200.0, 700000, 11000)), ("Statement", 4, 6000.0, (110, 6000.0, 480000, 7000), (52, 3900.0, 480000, 7000)),
        ("Story", 3, 3500.0, (60, 3300.0, 260000, 3600), (25, 1750.0, 260000, 3600)), ("Offer", 1, 1500.0, (20, 900.0, 90000, 1100), (8, 450.0, 90000, 1100))]
DEVICE = [("Talking head", 8, 11000.0, (210, 11000.0, 840000, 12500), (100, 7900.0, 840000, 12500)), ("Voiceover", 4, 5000.0, (90, 4650.0, 360000, 5200), (40, 2900.0, 360000, 5200)),
          ("Split screen", 2, 2500.0, (40, 2000.0, 160000, 2200), (18, 1300.0, 160000, 2200)), ("Text on screen", 3, 1500.0, (30, 1000.0, 70000, 900), (12, 700.0, 70000, 900))]
OFFER = [("Discount", 7, 12000.0, (230, 12000.0, 900000, 13500), (110, 8800.0, 900000, 13500)), ("Bundle", 3, 4500.0, (80, 4400.0, 330000, 4800), (35, 2700.0, 330000, 4800))]


def payload(slug, nc=True):
    if slug != "vita-bull":
        return {"available": False}
    def cut(key, title, spec):
        rows = [_row(v, a, s, TOTAL, m, n if nc else None) for v, a, s, m, n in spec]
        tagged = sum(a for _, a, *_ in spec)
        return {"key": key, "title": title, "families": 40, "tagged": tagged, "note": f"{tagged} of 40 ad families tagged", "rows": rows}
    return {"available": True, "has_nc": nc, "window_info": {"label": "Oct 4 – Nov 2 · 30 days"}, "product": {"slug": slug, "name": "Vita Bull"}, "scope": "tracker", "filters": {},
            "cuts": [cut("hook_type", "Hook type", HOOK), cut("device", "Device", DEVICE), cut("offer_type", "Offer type", OFFER)],
            "total": {"meta": _side(TOTAL, 280, 13000.0, 1500000, 20000), "nc": _side(TOTAL, 120, 9400.0, 1500000, 20000) if nc else None},
            "note": "NC ROAS is Upstack new customer revenue divided by spend (return on ad spend). Meta ROAS is Meta's own reporting. Each ad family counts once, on its first tracker row, under its tag."}


def learning_for(row, fam):
    """The `learning` block of one mock ad: the first Vita Bull family gets every part, the second only a chip, the rest none."""
    if STATE["mode"] == "off" or row["product"] != "vita-bull":
        return None
    first = [f for f in FAMS if f["product"] == "vita-bull"][:3]
    keys = [f["key"] for f in first]
    if fam["key"] == keys[0]:
        return {"chips": [{"key": "hook_type", "label": "Hook type", "value": "Question"}, {"key": "device", "label": "Device", "value": "Talking head"},
                          {"key": "offer_type", "label": "Offer type", "value": "Discount"}, {"key": "cta", "label": "CTA", "value": "Shop now"}],
                "hook_quote": "Your liver is not a filter, it is a vitamin warehouse", "why_it_works": "Names the pain in the first second, then lets a customer prove it.",
                "seen_on": "A woman holds a bottle in a bright kitchen.", "said_on": "Try it for 30 days.", "transcript": "Learning transcript: your liver is a vitamin warehouse, try it for thirty days.",
                "method": "vision_transcript", "confidence": "high", "version": "own-v1"}
    if fam["key"] == keys[1]:
        return {"chips": [{"key": "hook_type", "label": "Hook type", "value": "Story"}], "method": "text", "confidence": "low", "version": "own-v1"}
    return None


@app.get("/api/products/<slug>/learning-tags")
def learning_tags_mock(slug):
    if STATE["mode"] == "off":
        return jsonify({"available": False})
    return jsonify(payload(slug, nc=STATE["mode"] != "nc_off"))


@app.post("/__test/own_tags")
def own_tags_toggle():
    STATE["mode"] = request.args.get("mode") if request.args.get("mode") in ("on", "nc_off") else "off"
    return jsonify(ok=True)


@app.get("/__test/own_tags/ids")
def own_tags_ids():
    """Tracker row ids of a Vita Bull ad with every learning part, one with only a chip, and one with none."""
    fams = [f for f in FAMS if f["product"] == "vita-bull"][:3]
    return jsonify(full=fams[0]["rows"][0]["id"], chip=fams[1]["rows"][0]["id"], none=fams[2]["rows"][0]["id"])
