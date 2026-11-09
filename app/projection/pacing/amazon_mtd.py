"""Amazon month-to-date revenue, completed.

Amazon revenue reaches the feed with a lag, so the days after the last reported day make month-to-date (MTD) read low. Josh's rule: take the trailing
7-day average of the days that ARE reported and project the missing days at that rate; the projection is thrown away and recomputed the moment a day's
actual arrives (nothing here is stored as a fact: the nightly snapshot table is only a cache of this function's output, see `resolve`).

`compute` is pure (a dict of day -> revenue in, a dict out). `resolve` reads the nightly snapshot when it is current and otherwise computes live from
proj_feed_amazon, and says which it did. Backtest on 21 closed months (docs/AMAZON_MTD.md): no alternative rule beat the plain trailing 7-day average by a
material margin, so it is the only rule; `window` is the one knob.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import replace
from zoneinfo import ZoneInfo

from .periods import days_in_month, month_end, month_start

PACIFIC = ZoneInfo("America/Los_Angeles")
METHOD = "trailing-7-available/v1"
WINDOW = 7                    # days of AVAILABLE (reported, revenue above zero) data in the rate
MAX_STALE_DAYS = 5            # more missing days than this and the feed is called stale: nothing is projected
PARTIAL_RATIO = 0.5           # a newest day below this share of the days before it is treated as possibly partial
SERIES_BACK = 45              # days of history read for the rate
SNAPSHOT_TOLERANCE_DAYS = 1   # a snapshot written up to this many Pacific days ago may be used, if its data cutoff still matches


def pacific_today(now_utc: dt.datetime) -> dt.date:
    """The Pacific calendar date at an instant (daylight saving included: 2026-11-02 07:30 UTC is still Nov 1 in Los Angeles)."""
    return now_utc.astimezone(PACIFIC).date()


def _day(d: dt.date) -> str:
    return f"{d:%b} {d.day}"


def _money0(x: float) -> str:
    return f"${x:,.0f}"


def compute(series: dict[dt.date, float], today: dt.date, data_through: dt.date, month: dt.date | None = None, window: int = WINDOW,
            max_stale_days: int = MAX_STALE_DAYS, partial_ratio: float = PARTIAL_RATIO) -> dict:
    """Complete one month's Amazon MTD.

    series        day -> Amazon revenue, every row the feed holds (rows of zero revenue count as not reported)
    today         the Pacific date now
    data_through  the last day that should be complete (yesterday unless the data lag lever says otherwise)
    month         the month to complete (any date in it); default = the month holding data_through
    """
    month = month_start(month or data_through)
    mend = month_end(month)
    thr = min(data_through, mend)                                   # last day of the month that should have data
    elapsed = (thr - month).days + 1 if thr >= month else 0
    pos = sorted(d for d, v in series.items() if v and v > 0 and d <= thr)
    last_actual = pos[-1] if pos else None
    in_month = [d for d in pos if d >= month]
    actual_mtd = round(sum(float(series[d]) for d in in_month), 2)
    out = {"method": METHOD, "month": month.isoformat(), "today": today.isoformat(), "data_through": data_through.isoformat(),
           "days_in_month": days_in_month(month), "days_elapsed": elapsed,
           "last_actual_day": last_actual.isoformat() if last_actual and last_actual >= month else None,
           "last_reported_day": last_actual.isoformat() if last_actual else None,
           "days_actual": (last_actual - month).days + 1 if last_actual and last_actual >= month else 0,
           "days_projected": 0, "daily_rate": None, "rate_days": 0, "rate_source": None, "projected_days": [], "partial_topup": 0.0,
           "actual_mtd": actual_mtd, "projected_amount": 0.0, "projected_mtd": actual_mtd, "gap_days": [], "flags": [], "lag_days": None,
           "status": "complete", "note": ""}
    flags = out["flags"]
    if elapsed == 0:
        out["status"] = "no_days"
        out["note"] = "Amazon: this month has no complete days yet."
        return out
    if last_actual is None:
        flags.append("no_data")
        out["status"] = "no_rate"
        out["note"] = "Amazon: the feed holds no revenue yet, so nothing is projected."
        return out
    out["gap_days"] = [d.isoformat() for d in _days(month, min(last_actual, thr)) if not (series.get(d) and series[d] > 0)]
    if out["gap_days"]:
        flags.append("gap_days_inside_month")
    lag = (thr - last_actual).days
    out["lag_days"] = lag

    # the rate: the last `window` available days; a newest day far below the rest may be partial and is left out of the rate
    prior = [d for d in pos if d < last_actual][-window:]
    newest = float(series[last_actual])
    partial = False
    if len(prior) >= 3:
        prior_avg = sum(float(series[d]) for d in prior) / len(prior)
        partial = newest < partial_ratio * prior_avg
    chosen = prior if partial else pos[-window:]
    rate = sum(float(series[d]) for d in chosen) / len(chosen)
    out["daily_rate"], out["rate_days"] = round(rate, 2), len(chosen)
    if len(chosen) < window:
        flags.append("short_window")
    if (chosen[-1] - chosen[0]).days + 1 > len(chosen):
        flags.append("window_has_gaps")
    out["rate_source"] = "trailing_7_available" if last_actual >= month else "previous_month_last_7"
    if partial:
        flags.append("newest_day_partial")
        if last_actual >= month:                                    # a partial day of the previous month is not this month's to top up
            out["partial_topup"] = round(max(0.0, rate - newest), 2)

    if lag > max_stale_days:
        flags.append("feed_stale")
        out["status"] = "stale"
        out["partial_topup"] = 0.0
        out["note"] = (f"Amazon: feed stale, last reported day {_day(last_actual)} ({lag} days behind); nothing projected. "
                       f"Month to date {_money0(actual_mtd)} is actuals only.")
        return out
    missing = [d for d in _days(max(month, last_actual + dt.timedelta(days=1)), thr)]
    out["projected_days"] = [{"day": d.isoformat(), "revenue": round(rate, 2)} for d in missing]
    out["days_projected"] = len(missing)
    out["projected_amount"] = round(rate * len(missing) + out["partial_topup"], 2)
    out["projected_mtd"] = round(actual_mtd + out["projected_amount"], 2)
    if not missing and not out["partial_topup"]:
        out["note"] = f"Amazon: {out['days_actual']} days reported through {_day(last_actual)}; nothing to project."
        return out
    out["status"] = "projected"
    src = "trailing 7-day average" if last_actual >= month else "the previous month's last 7 days"
    parts = []
    if missing:
        parts.append(f"{len(missing)} {'day' if len(missing) == 1 else 'days'} projected at {_money0(rate)} a day ({src})")
    if partial and out["partial_topup"]:
        parts.append(f"{_day(last_actual)} looks partial ({_money0(newest)} reported), topped up by {_money0(out['partial_topup'])}")
    if last_actual >= month:
        head = f"Amazon: {out['days_actual']} {'day' if out['days_actual'] == 1 else 'days'} reported through {_day(last_actual)}"
    else:
        head = f"Amazon: no days reported yet this month (last reported {_day(last_actual)})"
    out["note"] = head + ", " + "; ".join(parts) + f". Replaced by actuals as they arrive. Actuals only: {_money0(actual_mtd)}."
    return out


def _days(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def apply_to_frame(fr, res: dict):
    """A copy of the pacing Frame whose Amazon revenue holds the projected days (and the partial-day top-up). Other Amazon measures are untouched."""
    if not res.get("projected_days") and not res.get("partial_topup"):
        return fr
    days = dict(fr.days)
    for p in res["projected_days"]:
        d = dt.date.fromisoformat(p["day"])
        if d in days:
            days[d] = defaultdict(float, days[d])
            days[d]["a_revenue"] = p["revenue"]
    if res.get("partial_topup") and res.get("last_reported_day"):
        d = dt.date.fromisoformat(res["last_reported_day"])
        if d in days:
            days[d] = defaultdict(float, days[d])
            days[d]["a_revenue"] = days[d]["a_revenue"] + res["partial_topup"]
    return replace(fr, days=days)


# ---------------------------------------------------------------------------------------------------------------- database

def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [c.name for c in cur.description]
    return [dict(r) if isinstance(r, dict) else dict(zip(names, r)) for r in cur.fetchall()]


def load_series(conn, data_through: dt.date, back: int = SERIES_BACK) -> dict[dt.date, float]:
    rows = _rows(conn, "SELECT day, revenue FROM proj_feed_amazon WHERE day BETWEEN %s AND %s", (data_through - dt.timedelta(days=back), data_through))
    return {r["day"]: float(r["revenue"] or 0) for r in rows}


def feed_loaded_at(conn) -> dt.datetime | None:
    try:
        r = _rows(conn, "SELECT max(loaded_at) AS t FROM proj_feed_amazon")
    except Exception:                                                       # noqa: BLE001
        conn.rollback()
        return None
    return r[0]["t"] if r else None


SNAPSHOT_COLS = ("snapshot_day", "computed_at", "month", "data_through", "last_actual_day", "days_actual", "days_projected", "daily_rate", "actual_mtd",
                 "projected_mtd", "status", "method", "flags", "projected_days", "partial_topup", "lag_days", "rate_source", "note")


def save_snapshot(conn, res: dict, today: dt.date, now_utc: dt.datetime | None = None) -> None:
    """One row per (Pacific day, month); a re-run the same day replaces it. Caller commits."""
    from psycopg.types.json import Jsonb
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    conn.execute(
        "INSERT INTO proj_amazon_mtd_snapshots (snapshot_day, month, computed_at, data_through, last_actual_day, days_actual, days_projected, daily_rate, actual_mtd, "
        "projected_mtd, status, method, flags, projected_days, partial_topup, lag_days, rate_source, note) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (snapshot_day, month) DO UPDATE SET computed_at = EXCLUDED.computed_at, data_through = EXCLUDED.data_through, "
        "last_actual_day = EXCLUDED.last_actual_day, days_actual = EXCLUDED.days_actual, days_projected = EXCLUDED.days_projected, daily_rate = EXCLUDED.daily_rate, "
        "actual_mtd = EXCLUDED.actual_mtd, projected_mtd = EXCLUDED.projected_mtd, status = EXCLUDED.status, method = EXCLUDED.method, flags = EXCLUDED.flags, "
        "projected_days = EXCLUDED.projected_days, partial_topup = EXCLUDED.partial_topup, lag_days = EXCLUDED.lag_days, rate_source = EXCLUDED.rate_source, note = EXCLUDED.note",
        (today, dt.date.fromisoformat(res["month"]), now, dt.date.fromisoformat(res["data_through"]),
         dt.date.fromisoformat(res["last_actual_day"]) if res["last_actual_day"] else None, res["days_actual"], res["days_projected"], res["daily_rate"],
         res["actual_mtd"], res["projected_mtd"], res["status"], res["method"], res["flags"], Jsonb(res["projected_days"]), res["partial_topup"], res["lag_days"],
         res["rate_source"], res["note"]))


def _from_row(r: dict, today: dt.date) -> dict:
    iso = lambda d: d.isoformat() if d else None                            # noqa: E731
    f = lambda x: None if x is None else float(x)                           # noqa: E731
    days_in = days_in_month(r["month"])
    return {"method": r["method"], "month": iso(r["month"]), "today": today.isoformat(), "data_through": iso(r["data_through"]), "days_in_month": days_in,
            "days_elapsed": (min(r["data_through"], month_end(r["month"])) - r["month"]).days + 1 if r["data_through"] >= r["month"] else 0,
            "last_actual_day": iso(r["last_actual_day"]), "last_reported_day": iso(r["last_actual_day"]), "days_actual": r["days_actual"],
            "days_projected": r["days_projected"], "daily_rate": f(r["daily_rate"]), "rate_days": None, "rate_source": r["rate_source"],
            "projected_days": r["projected_days"] or [], "partial_topup": f(r["partial_topup"]) or 0.0, "actual_mtd": f(r["actual_mtd"]),
            "projected_amount": round(f(r["projected_mtd"]) - f(r["actual_mtd"]), 2), "projected_mtd": f(r["projected_mtd"]), "gap_days": [],
            "flags": list(r["flags"] or []), "lag_days": r["lag_days"], "status": r["status"], "note": r["note"]}


def read_snapshot(conn, month: dt.date, today: dt.date, data_through: dt.date) -> tuple[dict | None, str | None]:
    """(result, None) from the newest snapshot of `month` when it is current; otherwise (None, plain reason).

    Current = written within SNAPSHOT_TOLERANCE_DAYS Pacific days, computed by this method, with the same data cutoff as now, and not older than the last
    load of the Amazon feed (so a feed that reloaded after the snapshot is never hidden behind it)."""
    try:
        rows = _rows(conn, "SELECT * FROM proj_amazon_mtd_snapshots WHERE month = %s AND snapshot_day <= %s ORDER BY snapshot_day DESC LIMIT 1", (month, today))
    except Exception:                                                       # noqa: BLE001  (migration 194 not applied yet)
        conn.rollback()
        return None, "snapshot table not available"
    if not rows:
        return None, "no snapshot for this month"
    r = rows[0]
    if r["snapshot_day"] < today - dt.timedelta(days=SNAPSHOT_TOLERANCE_DAYS):
        return None, f"latest snapshot is from {r['snapshot_day']:%b} {r['snapshot_day'].day}"
    if r["method"] != METHOD:
        return None, "snapshot was made by a different method"
    if r["data_through"] != data_through:
        return None, f"snapshot cutoff {r['data_through']:%b} {r['data_through'].day} differs from today's {data_through:%b} {data_through.day}"
    loaded = feed_loaded_at(conn)
    if loaded is not None and r["computed_at"] < loaded:
        return None, "the Amazon feed reloaded after the snapshot"
    return _from_row(r, today), None


def resolve(conn, month: dt.date, today: dt.date, data_through: dt.date, live: bool = False) -> dict:
    """The completed Amazon MTD for `month`: the nightly snapshot when it is current, else computed now. Adds `source` ('snapshot' | 'computed live') and
    `live_reason` (why it was computed live). Read-only."""
    month = month_start(month)
    reason = "forced" if live else None
    if not live:
        snap, reason = read_snapshot(conn, month, today, data_through)
        if snap is not None:
            snap["source"], snap["live_reason"] = "snapshot", None
            return snap
    res = compute(load_series(conn, data_through), today, data_through, month)
    res["source"], res["live_reason"] = "computed live", reason
    return res


def compute_live(conn, month: dt.date, today: dt.date, data_through: dt.date) -> dict:
    return compute(load_series(conn, data_through), today, data_through, month)


def snapshot_month(today: dt.date, data_through: dt.date) -> dt.date:
    """The month the page shows by default: the one holding the last complete day."""
    return month_start(data_through)
