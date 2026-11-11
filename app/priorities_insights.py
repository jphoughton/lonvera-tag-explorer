"""Read-only drill-down behind every block of the /priorities row panel: GET /api/priorities/lines/<line_id>/insights?block=<name>&window=<7|30|90|all>[&ad_id=<tracker row id>].

Meta numbers from tables the app already has (ad_metrics, ad_tags linemap, family_daily, ad_hooks, creative_stage_moves); no sheet, Meta or Google calls, no writes. Returns the top ads on the line
for the window (name, spend, ROAS, purchases, launch date, hook), the weekly trend, and the best hooks. With ad_id it returns that one ad (hook, numbers, weekly trend) if it sits on the line."""
from __future__ import annotations

from . import queries as q

BLOCKS = ("then_now", "timeline", "hooks", "winners", "performance", "testing", "live", "ad")
WINDOWS = {"7": 7, "30": 30, "90": 90, "all": None}
TOP_ADS = 10
MAX_WEEKS = 52

ADS_SQL = """
WITH t AS (SELECT max(day) AS d FROM family_daily)
SELECT m.tracker_row_id AS ad_id, m.family_key, COALESCE(cs.display, m.family_key) AS name,
       COALESCE(m.launch_date::date, CASE WHEN cs.launched_before_data_start THEN NULL ELSE cs.first_day::date END) AS launch_date,
       COALESCE(sum(f.spend), 0) AS spend, COALESCE(sum(f.purchases), 0) AS purchases, COALESCE(sum(f.revenue), 0) AS revenue,
       max(f.day) AS last_day, hk.hook
FROM ad_metrics m
JOIN ad_tags g ON g.tracker_row_id = m.tracker_row_id AND g.tag_set = 'linemap' AND g.is_current AND g.line_id = %(line)s
CROSS JOIN t
LEFT JOIN creative_stage_moves cs ON cs.family_key = m.family_key
LEFT JOIN family_daily f ON f.family_key = m.family_key AND (%(days)s::int IS NULL OR f.day > t.d - %(days)s::int)
LEFT JOIN LATERAL (SELECT h.hook FROM tracker_rows tr JOIN ad_hooks h ON h.tracker_row_id = tr.id
                   WHERE tr.family_key = m.family_key AND tr.deleted_at IS NULL ORDER BY (tr.id = m.tracker_row_id) DESC, tr.sheet_row LIMIT 1) hk ON true
WHERE m.is_first_row AND (%(ad)s::bigint IS NULL OR m.tracker_row_id = %(ad)s::bigint)
GROUP BY m.tracker_row_id, m.family_key, cs.display, m.launch_date, cs.launched_before_data_start, cs.first_day, hk.hook
HAVING COALESCE(sum(f.spend), 0) > 0
"""

WEEKLY_SQL = """
WITH t AS (SELECT max(day) AS d FROM family_daily)
SELECT date_trunc('week', f.day)::date AS wk, sum(f.spend) AS spend, sum(f.revenue) AS revenue, sum(f.purchases) AS purchases
FROM family_daily f CROSS JOIN t
WHERE f.family_key = ANY(%(fams)s) AND (%(days)s::int IS NULL OR f.day > t.d - %(days)s::int)
GROUP BY 1 ORDER BY 1
"""

LINE_SQL = "SELECT l.id, l.line_code, l.cluster, l.icp, l.pain_point, p.name AS product_name FROM taxonomy_lines l JOIN products p ON p.id = l.product_id WHERE l.id = %s"


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _ad(r) -> dict:
    sp, rv = _f(r["spend"]), _f(r["revenue"])
    return {"ad_id": r["ad_id"], "name": r["name"], "spend": round(sp), "roas": round(rv / sp, 2) if sp >= 100 else None, "purchases": round(_f(r["purchases"])),
            "launch_date": r["launch_date"].isoformat() if r["launch_date"] else None, "last_spend_day": r["last_day"].isoformat() if r["last_day"] else None, "hook": r["hook"]}


def insights(conn, line_id: int, block: str, window: str, ad_id: int | None = None) -> dict:
    if block not in BLOCKS:
        raise q.ApiError(400, "block must be one of: " + ", ".join(BLOCKS))
    if window not in WINDOWS:
        raise q.ApiError(400, "window must be 7, 30, 90 or all")
    line = conn.execute(LINE_SQL, (line_id,)).fetchone()
    if line is None:
        raise q.ApiError(404, "No such line.")
    days = WINDOWS[window]
    rows = conn.execute(ADS_SQL, {"line": line_id, "days": days, "ad": ad_id}).fetchall()
    if ad_id is not None and not rows:
        raise q.ApiError(404, "That ad has no spend in this window on this line.")
    if block == "testing":
        rows = sorted(rows, key=lambda r: (r["launch_date"] is None, -(r["launch_date"].toordinal() if r["launch_date"] else 0), -_f(r["spend"])))     # newest launches first
    else:
        rows = sorted(rows, key=lambda r: (-_f(r["spend"]), r["family_key"]))
    ads = [_ad(r) for r in rows]
    hooks, seen = [], set()
    for a in sorted(ads, key=lambda a: -a["spend"]):
        if not a["hook"]:
            continue
        key = "".join(ch for ch in a["hook"].lower() if ch.isalnum())[:45]
        if key in seen:
            continue
        seen.add(key)
        hooks.append({"hook": a["hook"], "ad_id": a["ad_id"], "ad": a["name"], "spend": a["spend"], "roas": a["roas"]})
    fams = [r["family_key"] for r in rows]
    weekly = []
    if fams:
        for w in conn.execute(WEEKLY_SQL, {"fams": fams, "days": days}).fetchall()[-MAX_WEEKS:]:
            sp = _f(w["spend"])
            weekly.append({"week": w["wk"].isoformat(), "spend": round(sp), "roas": round(_f(w["revenue"]) / sp, 2) if sp >= 100 else None, "purchases": round(_f(w["purchases"]))})
    return {"line_id": line_id, "line_code": line["line_code"], "line": f"{line['cluster']}, {line['icp']}, {line['pain_point']}", "block": block, "window": window,
            "ads_total": len(ads), "ads": ads[:TOP_ADS] if ad_id is None else ads, "hooks": hooks[:TOP_ADS], "weekly": weekly,
            "total": {"spend": round(sum(a["spend"] for a in ads)), "purchases": round(sum(_f(r["purchases"]) for r in rows)),
                      "roas": (round(sum(_f(r["revenue"]) for r in rows) / sum(_f(r["spend"]) for r in rows), 2) if sum(_f(r["spend"]) for r in rows) >= 100 else None)}}
