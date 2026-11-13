"""Mock of GET /api/daily-story for tests/ui/mock_server.py: the REAL facts builder (etl.daily_story.compute_facts) and the REAL template over the made-up panel of
tests/daily_story_fixture.py (70 days to 2026-11-11), a hand-made scoreboard. `?fail=1` answers 503."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from etl import daily_story as DS, daily_story_narrative as N, daily_story_registry as REG   # noqa: E402
from tests.daily_story_fixture import D1, make_panel                                         # noqa: E402

_CACHE: dict = {}


def _payload(day: dt.date):
    if "p" not in _CACHE:
        _CACHE["p"] = make_panel()
    p = _CACHE["p"]
    f = DS._clean(DS.compute_facts(p, day))
    s = N.template(f)
    days = [{"day": (D1 - dt.timedelta(days=i)).isoformat(), "coverage": "partial"} for i in range(0, 60)]
    sb = [{"change_type": "troas_share_up", "magnitude_bucket": "(any size)", "metric": "nc_roas", "n": 9, "rises": 6, "falls": 1, "rise_rate": 6 / 9, "fall_rate": 1 / 9, "base_rise_rate": 0.35,
           "base_fall_rate": 0.2, "rise_lift": 6 / 9 - 0.35, "fall_lift": 1 / 9 - 0.2, "median_delta": 0.08, "base_days": 60, "label": "correlation, not cause"}]
    return {"available": True, "day": day.isoformat(), "first_day": days[-1]["day"], "last_day": D1.isoformat(), "days": days, "coverage": f["readiness"]["coverage"], "version": "v1",
            "computed_at": "2026-11-12T13:40:00+00:00", "story": {"headline": s["headline"], "story": s["story"], "confidence": s["confidence"], "cannot_see": s["cannot_see"], "model": None, "source": "template", "note": "",
                                                                    "written_at": "2026-11-12T13:40:00+00:00"},
            "facts": f, "scoreboard": sb, "registry": [{"metric_key": r["key"], "label": r["label"], "unit": r["unit"], "basis": r["basis"]} for r in REG.REGISTRY]}


def register(app, deps):
    Bad = deps["Bad"]

    @app.get("/api/daily-story")
    def daily_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        v = request.args.get("day")
        try:
            day = dt.date.fromisoformat(v) if v else D1
        except ValueError:
            raise Bad("day must be a date like 2026-11-11")
        if not (D1 - dt.timedelta(days=59) <= day <= D1):
            return jsonify(error=f"no daily story for {day.isoformat()}"), 404
        return jsonify(_payload(day))
