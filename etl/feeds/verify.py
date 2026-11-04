"""Verification of the direct feeds against the sheet-fed tables: per feed and per day, with a tolerance per column, plus a "consecutive clean days" count.

    python -m etl.feeds verify [--feed a,b] [--days 30] [--need 7] [--live-sheet]

`compare_rows` is pure (rows in `feed.cols` order on both sides) and is shared with `dryrun`, which compares a fresh API pull with the live sheet tab.
Days at the end of the window (`settle_days`) are compared at 3x tolerance and can only be `soft`, never `fail`: the sources restate recent days.
A day the sheet side has not reached yet is `sheet_stale` (the desktop writer is behind: that is the problem being fixed, not a direct-feed failure).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from psycopg import sql

from .common import NOTE_LIMIT, get_mode, pacific_today, table_exists
from .feed import Feed, Metric

SOFT_FACTOR = 3.0


@dataclass
class DayResult:
    day: dt.date
    status: str = "ok"                       # ok | fail | soft | sheet_stale
    n_keys: int = 0
    n_bad: int = 0
    worst_col: str | None = None
    worst_abs: float = 0.0
    worst_rel: float = 0.0
    detail: str = ""

    def note(self, msg: str) -> None:
        if len(self.detail) < NOTE_LIMIT - 60:
            self.detail = (self.detail + "; " if self.detail else "") + msg


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _within(m: Metric, a: float, b: float, factor: float = 1.0) -> tuple[bool, float, float]:
    d = abs(a - b)
    rel = d / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0.0
    return (d <= m.abs_tol * factor or rel <= m.rel_tol * factor), d, rel


def compare_rows(feed: Feed, a_rows: Sequence[Sequence[Any]], b_rows: Sequence[Sequence[Any]], today: dt.date,
                 a_through: dt.date | None = None, b_through: dt.date | None = None) -> dict[dt.date, DayResult]:
    """a = the direct pull, b = the sheet-fed side. A key present on one side only counts as a difference unless its values are all zero.
    Rows with `skip_compare` true (periods still open) are left out. `b_through`: the sheet side's newest day; later days are `sheet_stale`."""
    idx = {c: feed.cols.index(c) for c in feed.cols}
    mi = [(m, idx[m.col]) for m in feed.metrics]

    def index(rows):
        out = {}
        for r in rows:
            if feed.skip_compare and feed.skip_compare(tuple(r), today):
                continue
            out[feed.key_of(r)] = tuple(r)
        return out

    A, B = index(a_rows), index(b_rows)
    soft_from = today - dt.timedelta(days=feed.settle_days) if feed.settle_days else today + dt.timedelta(days=1)
    res: dict[dt.date, DayResult] = {}
    for key in sorted(set(A) | set(B)):
        ra, rb = A.get(key), B.get(key)
        day = feed.day_of(ra if ra is not None else rb)
        dr = res.setdefault(day, DayResult(day))
        if b_through is not None and day > b_through and rb is None:
            dr.status = "sheet_stale" if dr.status == "ok" else dr.status
            continue
        dr.n_keys += 1
        soft = day >= soft_from
        factor = SOFT_FACTOR if soft else 1.0
        bad = False
        if ra is None or rb is None:
            present = ra if ra is not None else rb
            if any(_f(present[i]) for _m, i in mi):
                bad = True
                dr.note(f"{'+'.join(str(k) for k in key)}: only in {'direct' if ra is not None else 'sheet'}")
        else:
            for m, i in mi:
                ok, d, rel = _within(m, _f(ra[i]), _f(rb[i]), factor)
                if not ok:
                    bad = True
                    if d > dr.worst_abs:
                        dr.worst_col, dr.worst_abs, dr.worst_rel = m.col, d, rel
                    dr.note(f"{'+'.join(str(k) for k in key)} {m.col}: direct {_f(ra[i]):,.2f} vs sheet {_f(rb[i]):,.2f}")
        if bad:
            dr.n_bad += 1
    for dr in res.values():
        if dr.status == "sheet_stale":
            continue
        if dr.n_bad:
            dr.status = "soft" if dr.day >= soft_from else "fail"
        else:
            dr.status = "ok"
    return res


def summarize(results: dict[dt.date, DayResult]) -> dict[str, int]:
    out = {"checked": 0, "failed": 0, "soft": 0, "stale": 0, "ok": 0}
    for r in results.values():
        if r.status == "sheet_stale":
            out["stale"] += 1
            continue
        out["checked"] += 1
        out[{"fail": "failed", "soft": "soft", "ok": "ok"}[r.status]] += 1
    return out


# ---------------------------------------------------------------------------------------------------------------- database side

def _select(conn, feed: Feed, table: str, since: dt.date, until: dt.date) -> list[tuple]:
    where = [sql.SQL("{} BETWEEN %s AND %s").format(sql.Identifier(feed.day_col))]
    if feed.scope_where:
        where.append(sql.SQL(feed.scope_where))
    q = sql.SQL("SELECT {} FROM {} WHERE ").format(sql.SQL(", ").join(map(sql.Identifier, feed.cols)), sql.Identifier(table)) + sql.SQL(" AND ").join(where)
    return [tuple(r) for r in conn.execute(q, (since, until)).fetchall()]


def _max_day(conn, feed: Feed, table: str) -> dt.date | None:
    where = [sql.SQL(feed.scope_where)] if feed.scope_where else []
    q = sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(feed.day_col), sql.Identifier(table))
    if where:
        q += sql.SQL(" WHERE ") + where[0]
    return conn.execute(q).fetchone()[0]


def _min_day(conn, feed: Feed, table: str) -> dt.date | None:
    q = sql.SQL("SELECT min({}) FROM {}").format(sql.Identifier(feed.day_col), sql.Identifier(table))
    if feed.scope_where:
        q += sql.SQL(" WHERE ") + sql.SQL(feed.scope_where)
    return conn.execute(q).fetchone()[0]


def schema_drift(conn, feed: Feed) -> list[str]:
    """Columns (name and type) of the shadow table that differ from the main table; empty when they agree or the main table does not exist."""
    if not table_exists(conn, feed.main):
        return []
    q = "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s"
    a = {r[0]: r[1] for r in conn.execute(q, (feed.shadow,)).fetchall()}
    b = {r[0]: r[1] for r in conn.execute(q, (feed.main,)).fetchall()}
    out = []
    for c in sorted(set(a) | set(b)):
        if c not in a:
            out.append(f"{c}: missing in {feed.shadow}")
        elif c not in b:
            out.append(f"{c}: missing in {feed.main}")
        elif a[c] != b[c]:
            out.append(f"{c}: {a[c]} vs {b[c]}")
    return out


def verify_feed(conn, feed: Feed, days: int = 30, today: dt.date | None = None, sheet_rows: list[tuple] | None = None, record: bool = True) -> dict:
    """Shadow table vs the main table (or `sheet_rows` parsed from the live tab). Records per-day results and one verify run."""
    today = today or pacific_today()
    drift = schema_drift(conn, feed)
    s_min = _min_day(conn, feed, feed.shadow)
    s_max = _max_day(conn, feed, feed.shadow)
    if s_min is None:
        return {"feed": feed.key, "status": "no shadow data yet", "checked": 0, "failed": 0, "soft": 0, "stale": 0, "ok": 0, "schema": drift}
    since = max(today - dt.timedelta(days=days), s_min)
    until = today
    a = _select(conn, feed, feed.shadow, since, until)
    if sheet_rows is not None:
        b = [r for r in sheet_rows if since <= feed.day_of(r) <= until]
        b_through = max((feed.day_of(r) for r in sheet_rows), default=dt.date.min)
    else:
        b = _select(conn, feed, feed.main, since, until)
        b_through = _max_day(conn, feed, feed.main) or dt.date.min
    res = compare_rows(feed, a, b, today, a_through=s_max, b_through=b_through)
    summ = summarize(res)
    if record:
        with conn.cursor() as cur:
            for dr in res.values():
                cur.execute(
                    "INSERT INTO proj_feed_verify (feed, day, status, n_keys, n_bad, worst_col, worst_abs, worst_rel, detail, checked_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (feed, day) DO UPDATE SET status = excluded.status, "
                    "n_keys = excluded.n_keys, n_bad = excluded.n_bad, worst_col = excluded.worst_col, worst_abs = excluded.worst_abs, "
                    "worst_rel = excluded.worst_rel, detail = excluded.detail, checked_at = now()",
                    (feed.key, dr.day, dr.status, dr.n_keys, dr.n_bad, dr.worst_col, round(dr.worst_abs, 4), round(dr.worst_rel, 6), dr.detail[:NOTE_LIMIT] or None))
            cur.execute(
                "INSERT INTO proj_feed_verify_runs (feed, window_from, window_to, days_checked, days_failed, days_soft, days_stale, schema_ok, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (feed.key, since, until, summ["checked"], summ["failed"], summ["soft"], summ["stale"], not drift, ("; ".join(drift))[:NOTE_LIMIT] or None))
    failing = sorted((d for d, r in res.items() if r.status == "fail"))
    return {"feed": feed.key, "status": "ok" if not summ["failed"] and not drift else "fail", "window": (since, until), "schema": drift,
            "failing_days": failing, "results": res, **summ}


def clean_streak(conn, feed_key: str, today: dt.date | None = None) -> int:
    """Consecutive Pacific dates, ending today (or yesterday when today has not been verified yet), on which the day's last verify run found
    no failed day and no schema drift, and the day's last pull attempt succeeded."""
    today = today or pacific_today()
    runs = conn.execute(
        "SELECT DISTINCT ON (d) d, days_checked, days_failed, schema_ok FROM (SELECT (at AT TIME ZONE 'America/Los_Angeles')::date AS d, id, days_checked, "
        "days_failed, schema_ok FROM proj_feed_verify_runs WHERE feed = %s) x ORDER BY d, id DESC", (feed_key,)).fetchall()
    att = conn.execute(
        "SELECT DISTINCT ON (d) d, status FROM (SELECT (finished_at AT TIME ZONE 'America/Los_Angeles')::date AS d, id, status FROM proj_feed_attempts "
        "WHERE feed = %s AND mode IN ('shadow', 'direct')) x ORDER BY d, id DESC", (feed_key,)).fetchall()
    vr = {r[0]: r for r in runs}
    at = {r[0]: r[1] for r in att}

    def clean(d: dt.date) -> bool:
        r = vr.get(d)
        return bool(r and r[1] > 0 and r[2] == 0 and r[3] and at.get(d) == "ok")
    d = today if clean(today) else today - dt.timedelta(days=1)
    n = 0
    while clean(d):
        n += 1
        d -= dt.timedelta(days=1)
    return n


def run_verify(conn, feeds: Iterable[Feed], days: int = 30, need: int = 7, live_sheet: bool = False, today: dt.date | None = None, out=print) -> int:
    """Verify each feed that is in shadow mode. Returns the exit code: 1 when any feed has a failed day or schema drift."""
    code = 0
    for feed in feeds:
        if feed.native:
            out(f"{feed.key}: native feed (no sheet twin to compare with)")
            continue
        mode = get_mode(conn, feed.key)
        if mode == "direct":
            out(f"{feed.key}: mode is direct (the shadow comparison no longer applies)")
            continue
        sheet_rows = None
        if live_sheet:
            from etl import projection_pacing
            sheet_rows = feed.parse_sheet(projection_pacing.read_grid(feed.sheet_tab))
        r = verify_feed(conn, feed, days, today, sheet_rows)
        conn.commit()
        if r["status"] == "no shadow data yet":
            out(f"{feed.key}: no shadow data yet (mode {mode})")
            continue
        streak = clean_streak(conn, feed.key, today)
        flag = "ok" if r["status"] == "ok" else "FAIL"
        out(f"{feed.key}: {flag}  days checked {r['checked']}, ok {r['ok']}, failed {r['failed']}, soft {r['soft']}, sheet behind {r['stale']}; "
            f"consecutive clean days {streak} (need {need})")
        for d in r.get("failing_days", [])[:5]:
            dr = r["results"][d]
            out(f"    {d.isoformat()}  {dr.n_bad}/{dr.n_keys} keys differ; worst {dr.worst_col} abs {dr.worst_abs:,.2f} rel {dr.worst_rel:.2%}  {dr.detail[:140]}")
        for line in r["schema"][:5]:
            out(f"    schema drift: {line}")
        if r["status"] != "ok":
            code = 1
    return code
