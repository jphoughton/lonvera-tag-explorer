"""Payloads for the pacing page: rollup grids (periods as columns, metrics as rows) and the freshness chips."""
from __future__ import annotations

from datetime import date, timedelta
from . import catalog, month as month_mod
from .feeds import Frame, load_frame
from .levers import Levers, rates_month
from .periods import data_through as dthru, month_start, week_start
from .rollup import PeriodResult, apply_costs, make_periods, project, rollup
from .targets import TargetBook

MAX_PERIODS = {"day": 400, "week": 110, "month": 60}
FIRST_MONTH = date(2025, 1, 1)

# Plan block (monthly grain): the headline measures per month, from the month pacing table (the same numbers as the page headline), against the P&L goal.
PLAN_ROWS = (
    # key, label, month-pacing row key, direction, fmt
    ("total_rev", "Revenue", "total_rev", "up", "money"),
    ("total_spend", "Ad spend", "advertising", "down", "money"),
    ("nc_rev", "New-customer revenue", "nc_rev", "up", "money"),
    ("ebitda", "EBITDA", "ebitda", "up", "money"),
    ("noi", "Net operating income", "noi", "up", "money"),
)


def clamp_range(grain: str, start: date | None, end: date | None, thr: date, L: Levers | None = None) -> tuple[date, date]:
    L = L or Levers()
    end = min(end or thr, thr)
    if start is None:
        if grain == "month":
            start = FIRST_MONTH
        else:
            start = end - timedelta(days=(L.rollup_days if grain == "day" else L.rollup_weeks * 7) - 1)
    if grain == "week":
        start = week_start(start)
    elif grain == "month":
        start = month_start(start)
    start = max(start, FIRST_MONTH - timedelta(days=2) if grain == "week" else FIRST_MONTH)
    n = len(make_periods(grain, start, end, thr))
    cap = MAX_PERIODS[grain]
    if n > cap:
        raise ValueError(f"at most {cap} {grain}s per request")
    return start, end


def plan_block(fr: Frame, results: list[PeriodResult], today: date, book: TargetBook) -> list[dict]:
    """Rows 'projected / plan / difference %' per headline measure and month. A running month projects at its pace; a closed month is its actual."""
    per_month = []
    for r in results:
        c = month_mod.month_context(fr, r.period.start, today, book)
        rows = {x["key"]: x for x in month_mod.build_rows(c)} if c.elapsed and r.values.get("biz_total_rev") else {}
        per_month.append(rows)
    out: list[dict] = []
    for key, label, src, direction, fmt in PLAN_ROWS:
        proj = [m[src]["projection"] if src in m else None for m in per_month]
        goal = [m[src]["goal"] if src in m else None for m in per_month]
        diff = [None if p is None or not g else p / g - 1 for p, g in zip(proj, goal)]
        band = "Projection against the plan"
        out.append({"key": f"plan_{key}_proj", "label": f"{label}, projected", "kind": "line", "fmt": fmt, "values": proj, "band": band, "sub": label,
                    "note": "Month to date / days with data x days in the month for a running month; the actual for a closed month"})
        out.append({"key": f"plan_{key}_goal", "label": f"{label}, plan", "kind": "line", "fmt": fmt, "values": goal, "band": band, "sub": label, "note": "P&L of the selected scenario"})
        out.append({"key": f"plan_{key}_diff", "label": f"{label}, difference from plan", "kind": "ratio", "fmt": "pct1", "values": diff, "band": band, "sub": label, "direction": direction})
    return out


def rollup_payload(conn, grain: str, start: date | None, end: date | None, today: date, book: TargetBook, view: str = "rollup",
                   bands: list[str] | None = None, delta: bool = False, frame: Frame | None = None) -> dict:
    L = book.levers
    thr = dthru(today, L.data_lag_days)
    start, end = clamp_range(grain, start, end, thr, L)
    periods = make_periods(grain, start, end, thr)
    first = periods[0].start if periods else start
    last = max((p.full_end for p in periods), default=end)
    fr = frame or load_frame(conn, max(first, FIRST_MONTH - timedelta(days=2)), min(last, thr))
    apply_costs(fr, book, rates_month(L, today))
    results = rollup(fr, grain, start, end, thr, view)
    mets = catalog.metrics_for(view, bands)
    rows: list[dict] = []
    if grain == "month" and view == "rollup" and (not bands or "projection against the plan" in {b.lower() for b in bands}):
        rows.append({"key": "band:plan", "label": "Projection against the plan", "kind": "header"})
        rows.extend(plan_block(fr, results, today, book))
    cur_band = cur_sub = None
    for m in mets:
        if m.band != cur_band:
            rows.append({"key": f"band:{m.band}", "label": m.band, "kind": "header"})
            cur_band, cur_sub = m.band, None
        if m.sub and m.sub != cur_sub and m.band == "Meta by product":
            rows.append({"key": f"sub:{m.band}:{m.sub}", "label": m.sub[:1].upper() + m.sub[1:], "kind": "subheader", "band": m.band})
        cur_sub = m.sub
        rows.append({"key": m.key, "label": m.short or m.label, "kind": m.kind if m.kind in ("subtotal", "ratio") else "line", "fmt": m.fmt, "note": m.note,
                     "band": m.band, "sub": m.sub, "values": [r.values.get(m.key) for r in results]})
    if delta:
        for row in rows:
            if row.get("values") and row["kind"] != "header":
                vs = row["values"]
                row["delta"] = [None] + [None if vs[i] is None or vs[i - 1] in (None, 0) else (vs[i] - vs[i - 1]) / abs(vs[i - 1]) for i in range(1, len(vs))]
    return {
        "grain": grain, "view": view, "from": start.isoformat(), "to": end.isoformat(), "as_of": today.isoformat(), "data_through": thr.isoformat(),
        "months": [r.period.start.isoformat() for r in results], "labels": [r.period.label for r in results], "keys": [r.period.key for r in results],
        "status": ["projected" if r.period.partial else "actual" for r in results], "days": [r.period.days for r in results],
        "days_in_period": [r.period.days_in_period for r in results],
        "rows": rows, "bands": (["Projection against the plan"] if grain == "month" and view == "rollup" else []) + catalog.bands_for(view),
        "captions": [caption(view, frame=fr, thr=thr)], "missing_goal_rows": book.missing_rows(),
    }


def caption(view: str, frame: Frame | None, thr: date) -> str:
    if view == "meta":
        return f"Meta campaign results through {thr.strftime('%b %-d')}. Purchases and value are Meta-reported."
    return f"Shopify, Amazon, Google and Meta through {thr.strftime('%b %-d')}. New-customer figures are actual orders, not Meta-attributed. Running periods are marked *."


def freshness_chips(fresh: list[dict], today: date, lag: int = 1, bad_after: int = 5) -> list[dict]:
    """Per-feed chips: how old the last day is against yesterday (the day every daily feed should reach)."""
    expected = dthru(today, lag)
    out = []
    for f in fresh:
        last = f.get("data_through")
        if isinstance(last, str):
            last = date.fromisoformat(last[:10])
        age = None if last is None else max(0, (expected - last).days)
        limit = f.get("stale_after_days", 2)
        status = "bad" if age is None or age > max(limit, bad_after) else "warn" if age > limit else "good"
        out.append({"feed": f["feed"], "label": f["label"], "data_through": last.isoformat() if last else None, "age_days": age, "status": status})
    return out
