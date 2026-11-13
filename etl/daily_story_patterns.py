"""Pattern memory for the daily story: after each day, record the change types that showed that day and what the next 1-3 days did against their same-weekday
baselines, so the scoreboard (view daily_story_pattern_scoreboard) can say how often a pattern has preceded a rise or fall. CORRELATION, NOT CAUSE: several changes
usually land together, days overlap, and the outcome windows share days.

One row per (day, change_type) with the largest event's magnitude bucket, plus one `_all_days` row per day (the base rate every pattern is compared with).
Outcomes are the mean of (value / same-weekday median baseline - 1) over days D+1..D+3 that exist (out_days says how many); d1 is D+1 alone.
"""
from __future__ import annotations

import datetime as dt

from psycopg.rows import tuple_row

from . import daily_story_math as M

OUTCOME_METRICS = ("nc_roas", "cpm", "ctr", "conv", "aov")
ALL_DAYS = "_all_days"


def _delta(facts: dict | None, key: str):
    if not facts or not facts.get("metrics") or key not in facts["metrics"]:
        return None
    return facts["metrics"][key].get("pct_vs_baseline")


def build_rows(facts_by_day: dict[dt.date, dict], days: list[dt.date] | None = None) -> list[dict]:
    """Pattern rows for the given days (default: every day with facts), from the stored facts only."""
    out = []
    for d in (days or sorted(facts_by_day)):
        f = facts_by_day.get(d)
        if not f or not f.get("readiness", {}).get("ready") or "metrics" not in f:
            continue
        nxt = [facts_by_day.get(d + dt.timedelta(days=k)) for k in (1, 2, 3)]
        outc = {}
        for k in OUTCOME_METRICS:
            vals = [_delta(n, k) for n in nxt]
            avail = [v for v in vals if v is not None]
            outc[f"out_{k}"] = M.mean(avail)
            outc[f"d1_{k}"] = vals[0]
        n_days = len([n for n in nxt if n and n.get("metrics")])
        base = {"out_days": n_days, **outc}
        by_type: dict[str, dict] = {}
        for e in f.get("events") or []:
            cur = by_type.get(e["type"])
            if cur is None or abs(e.get("magnitude") or 0) > abs(cur.get("magnitude") or 0):
                by_type[e["type"]] = e
        for typ, e in by_type.items():
            out.append({"day": d, "change_type": typ, "magnitude_bucket": e["bucket"], "subject": (e.get("subject") or "")[:200], "magnitude": e.get("magnitude"), **base})
        out.append({"day": d, "change_type": ALL_DAYS, "magnitude_bucket": "all", "subject": "", "magnitude": None, **base})
    return out


COLS = ("day", "change_type", "magnitude_bucket", "subject", "magnitude", "out_days") + tuple(f"out_{k}" for k in OUTCOME_METRICS) + tuple(f"d1_{k}" for k in OUTCOME_METRICS)


def _write(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = (f"INSERT INTO daily_story_patterns ({', '.join(COLS)}, updated_at) VALUES ({', '.join(['%s'] * len(COLS))}, now()) ON CONFLICT (day, change_type) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c not in ("day", "change_type")) + ", updated_at = now()")
    with conn.cursor() as cur:
        cur.executemany(sql, [tuple(r.get(c) for c in COLS) for r in rows])
    return len(rows)


def _load_facts(conn, since: dt.date, until: dt.date) -> dict[dt.date, dict]:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT day, facts FROM daily_story_facts WHERE day BETWEEN %s AND %s", (since, until))
        return {r[0]: r[1] for r in cur.fetchall()}


def rebuild(conn) -> int:
    """Recompute every pattern row from the stored facts (backfill)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT min(day), max(day) FROM daily_story_facts")
        lo, hi = cur.fetchone()
    if lo is None:
        return 0
    facts = _load_facts(conn, lo, hi)
    conn.execute("DELETE FROM daily_story_patterns")
    return _write(conn, build_rows(facts))


def update(conn, d: dt.date) -> int:
    """After day d's facts exist: write d's own rows and refresh the previous 3 days (their outcome windows just got a day longer)."""
    facts = _load_facts(conn, d - dt.timedelta(days=4), d)
    return _write(conn, build_rows(facts, [x for x in sorted(facts) if x >= d - dt.timedelta(days=3)]))
