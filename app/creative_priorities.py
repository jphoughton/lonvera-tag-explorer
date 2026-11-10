"""Creative priorities (/priorities, /api/priorities): the weekly one-sheeter written by etl.creative_priorities (table creative_priorities_snapshots, migration 195).
Reads one stored snapshot; nothing is computed on request. See docs/CREATIVE_PRIORITIES.md."""
from __future__ import annotations

import datetime as dt

from . import queries as q

NOT_BUILT = "No creative priorities snapshot exists yet. It is written on Sundays by the nightly etl; run python -m etl.creative_priorities to write the current week."


def _week(raw: str | None) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        raise q.ApiError(400, "week must be a date, YYYY-MM-DD") from None
    return d


def weeks(conn) -> list[dict]:
    cur = conn.execute("SELECT week_start, built_at, data_through, jsonb_array_length(rows) AS n FROM creative_priorities_snapshots ORDER BY week_start DESC LIMIT 104")
    return [{"week_start": r["week_start"].isoformat(), "built_at": r["built_at"].isoformat(), "data_through": r["data_through"].isoformat(), "rows": r["n"]} for r in cur.fetchall()]


def snapshot(conn, raw_week: str | None) -> dict:
    """The snapshot of the given week (any day: the Sunday on or before it is used), else the latest."""
    wk = _week(raw_week)
    if wk is not None:
        wk = wk - dt.timedelta(days=(wk.weekday() + 1) % 7)
        row = conn.execute("SELECT week_start, built_at, data_through, basis, thresholds, baselines, rows, stats FROM creative_priorities_snapshots WHERE week_start = %s", (wk,)).fetchone()
        if row is None:
            raise q.ApiError(404, f"No creative priorities snapshot for the week of {wk.isoformat()}.")
    else:
        row = conn.execute("SELECT week_start, built_at, data_through, basis, thresholds, baselines, rows, stats FROM creative_priorities_snapshots ORDER BY week_start DESC LIMIT 1").fetchone()
        if row is None:
            raise q.ApiError(404, NOT_BUILT)
    return {"week_start": row["week_start"].isoformat(), "built_at": row["built_at"].isoformat(), "data_through": row["data_through"].isoformat(), "basis": row["basis"],
            "thresholds": row["thresholds"], "baselines": row["baselines"], "rows": row["rows"], "stats": row["stats"]}


def payload(conn, raw_week: str | None) -> dict:
    from etl import creative_priorities as cp
    snap = snapshot(conn, raw_week)
    comp = (snap.get("stats") or {}).get("competitors")
    return {**snap, "competitors_as_of": comp["as_of"] if comp else None, "competitor_stale": (comp or {}).get("stale_brands", []), "weeks": weeks(conn), "legend": [{"tag": a, "meaning": b} for a, b in cp.LEGEND]}


def csv_text(conn, raw_week: str | None) -> tuple[str, str]:
    from etl import creative_priorities as cp
    snap = snapshot(conn, raw_week)
    return snap["week_start"], cp.to_csv(snap)
