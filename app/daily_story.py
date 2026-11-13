"""Daily story (/daily, /api/daily-story): the stored story and facts of one day, the days that have one, and the pattern scoreboard. Read-only: everything is
computed nightly by etl/daily_story.py and stored in daily_story, daily_story_facts and daily_story_patterns (docs/DAILY_STORY.md).

Answers 200 with available=false and the reason when the tables are empty or the migration has not run, so the page can say so instead of failing.
"""
from __future__ import annotations

import datetime as dt

from . import queries as q

SCOREBOARD_LIMIT = 24


def _date(args) -> dt.date | None:
    v = args.get("day")
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise q.ApiError(400, "day must be a date like 2026-11-11") from None


def scoreboard(conn, limit: int = SCOREBOARD_LIMIT) -> list[dict]:
    """Strongest patterns first: by the larger of |rise_lift| and |fall_lift|, then n. Only rows with n of 6 or more exist in the view."""
    rows = conn.execute("""SELECT change_type, magnitude_bucket, metric, n, rises, falls, rise_rate, fall_rate, base_rise_rate, base_fall_rate, rise_lift, fall_lift, median_delta, base_days, label
                           FROM daily_story_pattern_scoreboard WHERE magnitude_bucket = '(any size)' AND metric IN ('nc_roas', 'cpm', 'ctr', 'conv', 'aov')
                           ORDER BY greatest(abs(rise_lift), abs(fall_lift)) DESC, n DESC LIMIT %s""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def build(conn, args) -> dict:
    day = _date(args)
    have = conn.execute("SELECT to_regclass('public.daily_story_facts') AS t").fetchone()["t"]
    if not have:
        return {"available": False, "reason": "The daily story tables do not exist yet (migration 248)."}
    span = conn.execute("SELECT min(day) AS first_day, max(day) AS last_day, count(*) AS n FROM daily_story_facts").fetchone()
    if not span["n"]:
        return {"available": False, "reason": "No daily story has been computed yet (python -m etl.daily_story)."}
    picked = day or span["last_day"]
    f = conn.execute("SELECT day, facts, version, coverage, created_at FROM daily_story_facts WHERE day = %s", (picked,)).fetchone()
    if f is None:
        raise q.ApiError(404, f"no daily story for {picked.isoformat()} (days held: {span['first_day'].isoformat()} to {span['last_day'].isoformat()})")
    s = conn.execute("SELECT headline, story, confidence, cannot_see, model, source, note, created_at FROM daily_story WHERE day = %s", (picked,)).fetchone()
    days = [{"day": r["day"].isoformat(), "coverage": r["coverage"]} for r in conn.execute("SELECT day, coverage FROM daily_story_facts ORDER BY day DESC LIMIT 400").fetchall()]
    reg = [dict(r) for r in conn.execute("SELECT metric_key, label, unit, basis FROM daily_story_metrics ORDER BY sort_order").fetchall()]
    return {"available": True, "day": picked.isoformat(), "first_day": span["first_day"].isoformat(), "last_day": span["last_day"].isoformat(), "days": days, "coverage": f["coverage"], "version": f["version"],
            "computed_at": f["created_at"].isoformat(), "story": None if s is None else {"headline": s["headline"], "story": s["story"], "confidence": s["confidence"], "cannot_see": s["cannot_see"], "model": s["model"],
                                                                                           "source": s["source"], "note": s["note"], "written_at": s["created_at"].isoformat()},
            "facts": f["facts"], "scoreboard": scoreboard(conn), "registry": reg}
