"""JSON payloads for the cohorts page: monthly grid, curves, cohort heatmap, summary. Built from an engine result; no database access here.

Grid payloads use the shell's vocabulary (kind: calc | subtotal | total | header | ratio, fmt: usd0 | pct | num | x), see QUESTIONS_cohorts.md.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..interfaces import MONTHS, add_months, status_for
from . import engine as eng
from .model import CohortResultX

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_label(d: date | None, long: bool = False) -> str:
    if d is None:
        return "none"
    return f"{MONTH_ABBR[d.month - 1]} {d.year}" if not long else f"{d:%B %Y}"


def _row(key: str, label: str, values: list, kind: str = "calc", fmt: str = "usd0", **extra: Any) -> dict:
    return {"key": key, "label": label, "kind": kind, "fmt": fmt, "values": values, "input": False, **extra}


def _series(s: dict, missing: float | None = 0.0) -> list:
    return [s.get(m, missing) for m in MONTHS]


def curve_name(levers: dict) -> str:
    c = levers.get("curve", "live")
    if c == "live":
        return f"Live (last {levers.get('n_cohorts', 3)} cohorts per age)"
    if c == "goal":
        return "Goal"
    return f"blend of Goal ({float(c.split(':')[1]):.0%}) and Live"


def actual_through_for(res_actual: date | None, c: CohortResultX) -> date | None:
    """Months up to here are backed by actual cohort data (the page's asterisk marks the rest). Falls back to the engine's frontier when no
    cohort cell is loaded."""
    return c.observed_through or res_actual


def table_payload(c: CohortResultX, actual_through: date | None) -> dict:
    """Repeat revenue by month, split by the cohort's acquisition year, next to the actual repeat revenue the cohorts earned."""
    status = status_for(actual_through)
    rows: list[dict] = [_row("h_repeat", "Repeat revenue by cohort year", [None] * len(MONTHS), kind="header", fmt="text")]
    for y in sorted({m.year for m in MONTHS} | {co.month.year for co in c.cohort_table}):
        rows.append(_row(f"repeat_{y}", f"{y} cohorts", _series(c.repeat_by_year.get(y, {}))))
    rows.append(_row("repeat_rev", "Repeat revenue (modeled)", _series(c.repeat_rev), kind="total"))
    actual_vals = _series(c.repeat_rev_actual, None)
    rows.append(_row("repeat_actual", "Repeat revenue (actual, from cohort exports)", actual_vals))
    ratio = [(c.repeat_rev.get(m, 0.0) / a - 1.0) if a else None for m, a in zip(MONTHS, actual_vals)]
    rows.append(_row("repeat_gap", "Modeled vs actual", ratio, kind="ratio", fmt="pct",
                     note="Modeled months apply today's Live curve to every cohort, so months from older cohorts can read below what those cohorts earned."))
    rows.append(_row("h_cohort", "Revenue from cohorts", [None] * len(MONTHS), kind="header", fmt="text"))
    rows.append(_row("first_order_rev", "First-order revenue", _series(c.first_order_rev)))
    total_cohort = [c.first_order_rev.get(m, 0.0) + c.repeat_rev.get(m, 0.0) for m in MONTHS]
    rows.append(_row("cohort_total", "Total revenue from cohorts", total_cohort, kind="total"))
    rows.append(_row("repeat_share", "Repeat share of cohort revenue", [(c.repeat_rev.get(m, 0.0) / t) if t else None for m, t in zip(MONTHS, total_cohort)],
                     kind="ratio", fmt="pct"))
    rows.append(_row("new_customers", "New customers", _series(c.new_customers, None), fmt="num"))
    through = month_label(c.data_through)
    caption = f"Source: cohort exports through {through}; repeat = cohort first-order revenue x {curve_name(c.levers)} retention at that age."
    return {"months": [m.isoformat() for m in MONTHS], "status": status, "rows": rows, "captions": [caption]}


def curves_payload(c: CohortResultX) -> dict:
    ages = list(range(1, eng.CURVE_AGES + 1))

    def cum(v: list[float]) -> list[float]:
        out, s = [], 0.0
        for x in v:
            s += x
            out.append(s)
        return out

    pts = c.curve_points
    return {
        "ages": ages, "live": c.curve_live, "goal": c.curve_goal, "used": c.curve_used,
        "cumulative": {"live": cum(c.curve_live), "goal": cum(c.curve_goal), "used": cum(c.curve_used)},
        "source": [p.source for p in pts], "cohorts_used": [[d.isoformat() for d in p.cohorts_used] for p in pts],
        "n_used": [p.n_used for p in pts],
        "summary": {"lifetime_used": c.lifetime_multiple, "lifetime_live": c.lifetime_live, "lifetime_goal": c.lifetime_goal,
                    "twelve_used": c.twelve_month_multiple, "twelve_live": c.twelve_live, "twelve_goal": c.twelve_goal,
                    "live_vs_goal": (c.lifetime_live / c.lifetime_goal - 1.0) if c.lifetime_goal else None,
                    "twelve_live_vs_goal": (c.twelve_live / c.twelve_goal - 1.0) if c.twelve_goal else None,
                    "curve": c.levers.get("curve", "live")},
        "levers": c.levers, "goal_snapshot": c.goal_snapshot.isoformat() if c.goal_snapshot else None, "warnings": c.warnings,
        "caption": f"Source: cohort exports through {month_label(c.data_through)}; Goal curve snapshot {month_label(c.goal_snapshot) if c.goal_snapshot else 'not loaded'}.",
    }


def cohorts_payload(c: CohortResultX, cohorts: list[eng.Cohort]) -> dict:
    """Heatmap data: cumulative $ per customer by age for every cohort, with the cells that feed the Live curve flagged."""
    feeds: dict[date, list[int]] = {}
    for p in c.curve_points:
        if p.source == "data":
            for d in p.cohorts_used:
                feeds.setdefault(d, []).append(p.age)
    info = {r["month"]: r for r in c.cohort_rows}
    out = []
    for co in cohorts:
        r = info.get(co.month.isoformat(), {})
        out.append({
            "month": co.month.isoformat(), "new_customers": co.new_customers, "first_order": co.first_order,
            "first_order_rev": r.get("first_order_rev"), "repeat_90d": co.repeat_90d, "cac": co.cac,
            "cum": [co.cum_at(a) for a in range(1, eng.COHORT_AGES + 1)],
            "ratio": [co.increment_ratio(a) for a in range(1, eng.COHORT_AGES + 1)],
            "feeds_curve": sorted(feeds.get(co.month, [])),
            "last_age": co.last_observed_age(), "repeat_actual_total": r.get("repeat_actual_total"),
        })
    return {"ages": eng.COHORT_AGES, "cohorts": out, "tail_from_age": c.levers.get("tail_from_age"), "n_cohorts": c.levers.get("n_cohorts"),
            "caption": f"Source: cohort exports through {month_label(c.data_through)}; cumulative dollars per customer, first order included."}


def summary_payload(c: CohortResultX, actual_through: date | None, fresh: dict | None, today: date) -> dict:
    closed = eng.last_closed_month(today)
    next12 = [add_months(closed, i) for i in range(1, 13)]
    f12 = sum(c.repeat_rev.get(m, 0.0) for m in next12)
    # modeled vs actual over the last three closed months that have actual cohort data
    done = sorted(m for m in c.repeat_rev_actual if m <= closed)[-3:]
    mod, act = sum(c.repeat_rev.get(m, 0.0) for m in done), sum(c.repeat_rev_actual.get(m, 0.0) for m in done)
    loaded = len(c.cohort_rows)
    return {
        "lifetime_multiple": c.lifetime_multiple, "twelve_month_multiple": c.twelve_month_multiple,
        "lifetime_live": c.lifetime_live, "lifetime_goal": c.lifetime_goal,
        "live_vs_goal": (c.lifetime_live / c.lifetime_goal - 1.0) if c.lifetime_goal else None,
        "curve": c.levers.get("curve", "live"), "curve_name": curve_name(c.levers),
        "cohorts_loaded": loaded, "first_cohort": c.cohort_rows[0]["month"] if loaded else None,
        "last_cohort": c.cohort_rows[-1]["month"] if loaded else None,
        "data_through": c.data_through.isoformat() if c.data_through else None,
        "forward_12m_repeat": f12, "forward_12m_from": next12[0].isoformat(), "forward_12m_to": next12[-1].isoformat(),
        "backtest": {"months": [m.isoformat() for m in done], "modeled": mod, "actual": act, "gap": (mod / act - 1.0) if act else None},
        "freshness": fresh, "freshness_line": freshness_line(fresh), "actual_through": actual_through.isoformat() if actual_through else None,
        "warnings": c.warnings,
    }


def _d(v):
    return date.fromisoformat(v) if isinstance(v, str) else v


def freshness_line(fresh: dict | None) -> str:
    """'Cohorts loaded through Oct 2026; Nov closes Dec 1.' or, when the last closed month is not loaded,
    'Cohorts loaded through Sep 2026; Oct 2026 is closed and not loaded yet.'"""
    if not fresh or not fresh.get("data_through_month"):
        return "No cohorts are loaded."
    through, nxt = _d(fresh["data_through_month"]), _d(fresh["next_close"])
    if fresh.get("behind"):
        return f"Cohorts loaded through {month_label(through)}; {month_label(add_months(through, 1))} is closed and not loaded yet."
    open_month = add_months(nxt, -1)
    return f"Cohorts loaded through {month_label(through)}; {MONTH_ABBR[open_month.month - 1]} closes {MONTH_ABBR[nxt.month - 1]} {nxt.day}."
