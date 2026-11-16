"""Mock of GET /api/madre-review for tests/ui/mock_server.py: a small deck in the production payload shape (slide kinds cover, week, table, ads, pockets, board, suggest, asks,
notes; two product sections), one ad whose video the mock serves and one that 404s, and a tiny valid MP4-less stand-in file for the video route."""
from __future__ import annotations

from flask import Response, jsonify

M = lambda **k: {"spend": 1000.0, "purchases": 10.0, "cpa": 100.0, "roas": 0.8, "ctr": 0.02, "cpc": 2.0, "cvr": 0.02, "cpm": 50.0, "aov": 75.0, "atc_rate": 0.1, "atc_to_purchase": 0.2, **k}
COLS = [{"group": g, "window": w, "m": M(cpa=c), "p": (M(cpa=100.0) if w == "Nov 9–15" else None)} for g, c in (("Madre", 130.0), ("Account", 100.0)) for w in ("Nov 2–8", "Nov 9–15")]
AD = lambda k, name, video: {"key": k, "family": name, "product": "vita-bull", "product_name": "Vita Bull", "stage": "Testing", "hook": "Hook line", "tags": ["Cluster A", "ICP A", "Pain A"],
                             "asset": "https://drive.google.com/file/d/abc/view", "first_day": "2026-10-01", "poster": None, "video": video, "m": M(cpa=60.0), "life": {"spend": 5000.0, "purchases": 50.0, "cpa": 100.0},
                             "bench": 100.0, "name": name}
TICKET = {"key": "t-one", "id": "Lonvera-VitalBull-A1", "product": "vita-bull", "product_name": "Vita Bull", "status": "7 - Approved - Ready To Launch", "status_n": 7, "vendor": "Madre",
          "cluster": "Cluster A", "icp": "ICP A", "pain": "Pain A", "format": "Video", "variants": 2, "asset": "https://drive.google.com/file/d/abc/view",
          "told": {"n": 3, "spend": 9000.0, "cpa": 90.0, "top": [{"family": "fam-x", "spend": 5000.0, "cpa": 80.0, "vendor": "Other"}]}}
SLIDES = [
    {"id": "cover", "section": "overview", "kind": "cover", "title": "Madre · Week of Nov 9–15", "big": [["Spend", 22395.0, "money"], ["Purchases", 207.0, "num"], ["CPA", 108.19, "money2"], ["Account CPA", 104.26, "money2"]], "foot": "Meta-reported."},
    {"id": "scorecard", "section": "overview", "kind": "table", "kicker": "Scorecard", "title": "Madre vs account", "table": {"keys": ["spend", "purchases", "cpa", "roas"], "cols": COLS}, "foot": "x"},
    {"id": "cover", "section": "vita-bull", "kind": "cover", "kicker": "Madre · Vita Bull", "title": "Vita Bull", "big": [["Madre spend", 9000.0, "money"], ["Purchases", 80.0, "num"], ["Madre CPA", 112.0, "money2"], ["Product avg CPA", 103.0, "money2"]], "foot": "Nov 9–15."},
    {"id": "winners", "section": "vita-bull", "kind": "ads", "kicker": "Vita Bull · Winners", "title": "2 winners: CPA 10%+ under Vita Bull avg", "ads": ["a-play", "a-gone"], "total": 2, "empty": "none", "foot": "x"},
    {"id": "pipeline", "section": "vita-bull", "kind": "board", "kicker": "Vita Bull · Pipeline", "title": "Open concepts", "columns": [{"status_n": 7, "label": "7 - Approved - Ready To Launch", "tickets": ["t-one"]}], "empty": "none", "foot": "x"},
    {"id": "next", "section": "vita-bull", "kind": "suggest", "kicker": "Vita Bull · Next pockets", "title": "Brief more here", "foot": "x",
     "cards": [{"product": "vita-bull", "product_name": "Vita Bull", "cluster": "Cluster A", "icp": "ICP A", "pain": "Pain A", "title": "ICP A: Pain A", "why": "CPA $60.00 vs avg $100.00.",
                "idea": {"product": "vita-bull", "cluster": "Cluster A", "icp": "ICP A", "pain_point": "Pain A", "media_type": "video", "note": "n"}}]},
    {"id": "asks", "section": "vita-bull", "kind": "asks", "kicker": "Vita Bull · Asks", "title": "Asks for Madre this week", "items": [[{"t": "Pause "}, {"ad": "a-gone", "label": "VB 7.9.3 h2"}, {"t": "."}]]},
    {"id": "notes", "section": "notes", "kind": "notes", "kicker": "Notes", "title": "How this is built", "bullets": ["one", "two"]},
]
PAYLOAD = {"vendor": "Madre", "week": {"start": "2026-11-09", "end": "2026-11-15", "label": "Nov 9–15"}, "prev": {"label": "Nov 2–8"}, "default_week": "2026-11-09", "last_loaded_day": "2026-11-15",
           "freshness": "Loaded through Nov 15. The last 3 days still move.", "weeks": [{"start": "2026-11-09", "label": "Nov 9–15"}, {"start": "2026-11-02", "label": "Nov 2–8"}],
           "sections": [{"id": s, "label": l} for s, l in (("overview", "Overview"), ("vita-bull", "Vita Bull"), ("beef-liver", "Beef Liver"), ("boneflex", "Boneflex"), ("notes", "Notes"))],
           "slides": SLIDES, "ads": {"a-play": AD("a-play", "bull-527-hook3", "/api/madre-review/video?family=ok"), "a-gone": AD("a-gone", "vb-7.9.3-hook2", "/api/madre-review/video?family=gone")},
           "tickets": {"t-one": TICKET}, "bench": {}, "thresholds": {}, "scorecard": {}}
TINY = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def register(app, deps):
    @app.get("/api/madre-review")
    def mock_madre_review():
        return jsonify(PAYLOAD)

    @app.get("/api/madre-review/video")
    def mock_madre_video():
        from flask import request
        if request.args.get("family") == "ok":
            return Response(TINY, mimetype="video/mp4")
        resp = jsonify(error="no video for this ad")
        resp.status_code = 404
        return resp
