"""Mock of GET /api/tag-lookup for tests/ui/mock_server.py.

A handful of made-up tracker rows, tags, hooks and platform ads in plain lists (a MemoryStore with the same methods as app.lookup.DbStore), run through the REAL
matching and payload code (app.lookup.lookup): the rule that matched, the comparison markers, the suggestions and the no-match answer are production code;
only the database is replaced. `?fail=1` answers a 503 for the error-box test, `?__delay=` is handled by the mock server's own hook.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import lookup as L, queries as q            # noqa: E402

PRODUCTS = {"beef-liver": "Beef Liver", "boneflex": "Boneflex", "vita-bull": "Vita Bull"}
LINES = {
    "B07": dict(cluster="Hair Thinning", icp="BL3 Thinning and Drained", pain="Nutrient Deficiency (Iron / B12 / Vitamin A)", trig="Frustration",
                ing=[("Heme Iron", "Low ferritin starves the follicle", "Hair growth"), ("Vitamin B12", "Fatigue", "Energy")]),
    "B45": dict(cluster="Alcohol & Liver Support", icp="Beer Drinker", pain="Liver Health / Detox", trig="Fear", ing=[("Choline", "Fatty liver", "Liver function")]),
    "BF11": dict(cluster="Glucosamine & Chondroitin", icp="Build-My-Stack", pain="Joint Pain & Mobility Loss", trig="Desire", ing=[("Glucosamine", "Cartilage wear", "Joints")]),
}
# id, product, concept, ad family, status, tracker (cluster, icp, pain, emotion, ingredient), line, confidence, method, hook
ROWS = [
    (101, "beef-liver", "Liver-208 - Copy 3", "Liver-208", "8 - Launched", (None, "BL2 The Stubborn Gut", "Weight Gain / Stubborn Belly Fat", "Fear", None), "B45", "medium", "video: 4 frames + Whisper transcript", "Your liver can regenerate itself."),
    (102, "beef-liver", "Liver-208 - Copy 4", "Liver-208", "8 - Launched", (None, "BL1 Weekend Face", "Liver Health / Detox", "Fear", "Choline"), "B07", "low", "image", None),
    (103, "beef-liver", "Victoria-BeefLiver-Static-9.5", "Lonvera-BeefLiver-Static-9.5", "8 - Launched", (None, "BL3 Thinning and Drained", "Nutrient Deficiency (Iron / B12 / Vitamin A)", "Frustration", "Heme Iron"), "B07", "high", "image", "Always exhausted? You might need ferritin."),
    (104, "boneflex", "Lonvera-Boneflex-Video-2.1", "Lonvera-Boneflex-Video-2.1", "8 - Launched", (None, "BF1 Won't Sit It Out", "Joint Pain", "Desire", None), "BF11", "low", "text only", None),
    (105, "vita-bull", "Bull-777-Hook1", "Bull-777-Hook1", "5 - Briefed", (None, "VB1 Not Just Getting Older", "Low Energy", "Frustration", None), None, None, None, None),
]
ADS = [
    ("5100000001", "Liver-208-Copy3_VID_WeightGain_Madre", "liver-208-copy3_vid_weightgain_madre", "liver-208"),
    ("5100000002", "S-Lonvera-BeefLiver-Static-9.5_IMG_Offer", "s-lonvera-beefliver-static-9.5_img_offer", None),
    ("5100000003", "Lonvera-Boneflex-Video-2.1", "lonvera-boneflex-video-2.1", "lonvera-boneflex-video-2.1"),
    ("5100000004", "Bull-526-Hook2 - Copy", "bull-526-hook2", None),
    ("5100000005", "Liver-209-Hook1", "liver-209-hook1", None),
]
_UNUSED = {"liver-208": (3000.0, 30, 2400.0, 204850.0, 2790, 167570.0), "lonvera-boneflex-video-2.1": (300.0, 0, 0.0, 450.0, 3, 380.0), "lonvera-beefliver-static-9.5": (75.0, 1, 40.0, 75.0, 1, 40.0)}


def _row(r):
    rid, prod, concept, fam, status, tt, *_ = r
    return {"id": rid, "product_id": 1, "concept_id": concept, "ad_family": fam, "family_key": fam.lower(), "sheet_row": rid, "status": status, "ad_type": "VID", "vendor": "Madre",
            "launch_date": dt.date(2026, 10, 2), "tracker_cluster": tt[0], "tracker_icp": tt[1], "tracker_pain": tt[2], "tracker_emotion": tt[3], "tracker_ingredient": tt[4],
            "slug": prod, "product_name": PRODUCTS[prod]}


class MemoryStore:
    def __init__(self):
        self.rows = [_row(r) for r in ROWS]
        self.ads = [{"ad_id": i, "ad_name": n, "meta_family_key": k, "account": "Lonvera Main", "adset_name": "Scaling set", "status": "ACTIVE", "last_day": dt.date(2026, 11, 3)} for i, n, k, _ in ADS]
        self.fam = {k: t for _, _, k, t in ADS if t}

    def ads_by_id(self, ad_id):
        return [a for a in self.ads if a["ad_id"] == ad_id]

    def ads_by_name(self, name):
        return [a for a in self.ads if a["ad_name"] == name]

    def ads_by_family_keys(self, keys):
        return [a for a in self.ads if a["meta_family_key"] in keys]

    def tracker_family_of(self, mks):
        return {k: self.fam[k] for k in mks if k in self.fam}

    def trackers_by_family(self, fks):
        return [r for r in self.rows if r["family_key"] in fks]

    def trackers_by_concept(self, keys):
        return [r for r in self.rows if r["concept_id"].lower() in keys]

    def details(self, rows):
        by_id = {r[0]: r for r in ROWS}
        tags, ing, hooks = {}, {}, {}
        for r in rows:
            src = by_id[r["id"]]
            code = src[6]
            if code:
                ln = LINES[code]
                tags[r["id"]] = {"version": "linemap-v2-mock", "line_id": code, "confidence": src[7], "method": src[8], "seen": f"A close-up of {src[2]}.", "why": f"The copy and the picture both point at {ln['pain']}.",
                                 "runner_up": "B45 Beer Drinker", "line_code": code, "story": None, "cluster": ln["cluster"], "icp": ln["icp"], "pain_point": ln["pain"], "emotional_trigger": ln["trig"]}
                ing[code] = [{"ingredient": a, "attacks": b, "supports": c} for a, b, c in ln["ing"]]
            if src[9]:
                pass
        return {"tags": tags, "ingredients": ing}


def register(app, deps):
    Bad = deps["Bad"]

    @app.get("/api/tag-lookup")
    def tag_lookup_mock():
        if request.args.get("fail") == "1" or request.args.get("q") == "FAIL ME":
            return jsonify(error="query timed out"), 503
        try:
            return jsonify({**L.lookup(MemoryStore(), request.args.get("q"))})
        except q.ApiError as e:
            raise Bad(e.message)
