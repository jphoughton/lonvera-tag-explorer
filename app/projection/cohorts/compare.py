"""Retention-curve choices side by side: which as-of months can be built, and what each choice does to repeat revenue and the P&L.

Nothing here writes. A comparison runs the whole engine in memory once per option with the cohorts levers overridden on a copy of the scenario.
It is not cached: one run is about 25 ms, and its inputs (cohort uploads, QuickBooks months, spend feeds) change without bumping the scenario
version, so a cache keyed on the version would go stale.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from .. import engine
from ..interfaces import Scenario
from . import engine as eng
from . import store
from .model import CohortResultX

DEFAULT_ASOF = date(2026, 8, 1)             # the month Josh named; the page falls back to the month before the latest when it is not computable
DEFAULT_YEAR = 2027
REVENUE_KEYS = ("total_revenue", "total_company_revenue")


class CompareError(ValueError):
    """A request the comparison cannot answer (message goes to the user)."""


def effective_levers(scn: Scenario, **override: Any) -> eng.Levers:
    lv = eng.parse_levers((scn.params or {}).get("cohorts"), strict=False)
    return replace(lv, **override) if override else lv


def with_n(lv: eng.Levers, n: int | None) -> eng.Levers:
    """The levers with n cohorts averaged (and the minimum per age kept at or under it)."""
    return lv if not n else replace(lv, n_cohorts=n, min_cohorts_per_age=min(lv.min_cohorts_per_age, n))


def snapshot_list(cohorts: list[eng.Cohort], scn: Scenario, today: date, n: int | None = None) -> list[dict]:
    return eng.asof_snapshots(cohorts, with_n(effective_levers(scn), n), today)


def computable_months(snaps: list[dict]) -> list[dict]:
    return [s for s in snaps if s["computable"]]


def asof_problem(cohorts: list[eng.Cohort], lv: eng.Levers, today: date) -> str | None:
    """None when the as-of month in `lv` can be built, else a plain sentence saying which months can."""
    if lv.curve_source != "asof":
        return None
    ok = computable_months(eng.asof_snapshots(cohorts, lv, today))
    if not ok:
        return "No month has enough cohort history to build an as-of curve yet."
    months = [s["month"] for s in ok]
    if lv.asof_month in months:
        return None
    return (f"The as-of curve with {lv.n_cohorts} cohorts needs at least {lv.n_cohorts} cohorts at each of the first {eng.MIN_COMPUTABLE_AGES} months of age: "
            f"choose a month from {eng.month_text(months[0])} to {eng.month_text(months[-1])}.")


def default_asof(snaps: list[dict]) -> date | None:
    ok = [s["month"] for s in computable_months(snaps)]
    if DEFAULT_ASOF in ok:
        return DEFAULT_ASOF
    earlier = [m for m in ok if m < ok[-1]] if ok else []
    return earlier[-1] if earlier else (ok[-1] if ok else None)


def options(cohorts: list[eng.Cohort], scn: Scenario, today: date, goal_snapshot: date | None, n: int | None = None) -> dict:
    """What the picker needs: the saved choice, the sources, the n choices and every month with whether and how its curve reads."""
    saved = effective_levers(scn)
    n = n or saved.n_cohorts
    snaps = snapshot_list(cohorts, scn, today, n)
    lv = with_n(saved, n)
    return {
        "saved": {**saved.to_dict(), "label": eng.curve_label(saved, goal_snapshot)},
        "sources": [{"key": "live", "label": "Current"}, {"key": "goal", "label": "Goal"}, {"key": "asof", "label": "As of month"}],
        "n_choices": list(eng.N_COHORT_CHOICES), "n": n,
        "asof_months": [{"month": s["month"].isoformat(), "label": eng.month_text(s["month"]), "computable": s["computable"], "is_latest": s["is_latest"],
                         "fewest_cohorts": s["fewest_cohorts"], "lifetime": s["lifetime"], "twelve": s["twelve"]} for s in snaps],
        "default_asof": (default_asof(snaps).isoformat() if default_asof(snaps) else None),
        "latest_month": snaps[-1]["month"].isoformat() if snaps else None,
        "goal_snapshot": goal_snapshot.isoformat() if goal_snapshot else None,
        "needs": f"At least {lv.n_cohorts} cohorts at each of the first {eng.MIN_COMPUTABLE_AGES} months of age",
    }


# ---------------------------------------------------------------------------------------------------------------- comparison

def _variant(scn: Scenario, overrides: dict) -> Scenario:
    params = dict(scn.params or {})
    coh = {k: v for k, v in dict(params.get("cohorts") or {}).items() if k != "curve"}      # the earlier name would be shadowed by curve_source anyway
    params["cohorts"] = {**coh, **overrides}
    return replace(scn, params=params)


def _fy(pnl: Any, year: int) -> dict:
    fy = (getattr(pnl, "fy", None) or {}).get(year) or {}
    lines = getattr(pnl, "lines", None) or {}

    def pick(*keys: str) -> float | None:
        for k in keys:
            if k in fy and fy[k] is not None:
                return float(fy[k])
        for k in keys:
            if k in lines:
                return float(sum(v for m, v in lines[k].items() if m.year == year))
        return None

    ev = getattr(pnl, "ev", None) or {}
    return {"year": year, "loi": float(ev["loi"]) if isinstance(ev.get("loi"), (int, float)) else None, "repeat_rev": pick("repeat_rev"), "total_revenue": pick(*REVENUE_KEYS), "noi": pick("noi", "net_operating_income"),
            "ev": {k: float(v) for k, v in ev.items() if isinstance(k, str) and k.endswith("x") and isinstance(v, (int, float))}}


def _run(conn, scn: Scenario, today: date, overrides: dict, key: str, label: str, short: str) -> dict:
    res = engine.run(_variant(scn, overrides), conn, today=today)
    c = res.cohorts
    if not isinstance(c, CohortResultX):
        raise CompareError(res.errors.get("cohorts") or "the cohorts module did not run")
    pnl = res.results.get("pnl")                                   # None when the P&L module is degraded: the figures then read blank
    year = int(((getattr(pnl, "ev", None) or {}).get("basis_year")) or DEFAULT_YEAR)
    fy = _fy(pnl, year)
    if fy["repeat_rev"] is None:
        fy["repeat_rev"] = float(sum(v for m, v in c.repeat_rev.items() if m.year == year))
    return {"key": key, "label": label, "short": short, "curve": c.curve_used, "lifetime": c.lifetime_multiple, "twelve": c.twelve_month_multiple,
            "fy": fy, "warnings": c.warnings, "overrides": overrides}


def _delta(opt: dict, base: dict) -> dict:
    d: dict[str, Any] = {"lifetime": opt["lifetime"] - base["lifetime"], "twelve": opt["twelve"] - base["twelve"]}
    for k in ("repeat_rev", "total_revenue", "noi"):
        a, b = opt["fy"].get(k), base["fy"].get(k)
        d[k] = None if a is None or b is None else a - b
    d["ev"] = {m: v - base["fy"]["ev"][m] for m, v in opt["fy"]["ev"].items() if m in base["fy"]["ev"]}
    return d


def _matches_saved(saved: eng.Levers, key: str, n: int, asof: date | None) -> bool:
    if key == "current":
        return saved.curve_source == "live" and saved.n_cohorts == n
    if key == "goal":
        return saved.curve_source == "goal"
    return saved.curve_source == "asof" and saved.asof_month == asof and saved.n_cohorts == n


def compare(conn, scn: Scenario, today: date, asof: date, n: int, asof2: date | None = None) -> dict:
    """Current, Goal and As-of (and an optional second As-of) run through the full engine, each with its curve, multiples and the P&L year's repeat
    revenue, revenue, net operating income and implied value, plus the change against Current."""
    cohorts = store.load_cohorts(conn)
    goal, goal_snap = store.load_goal(conn)
    saved = effective_levers(scn)
    lv_n = with_n(saved, n)
    months = [s["month"] for s in computable_months(eng.asof_snapshots(cohorts, lv_n, today))]
    for m in [asof] + ([asof2] if asof2 else []):
        if m not in months:
            raise CompareError(asof_problem(cohorts, replace(lv_n, curve_source="asof", asof_month=m), today) or "that month cannot be built")
    base_min = min(saved.min_cohorts_per_age, n)
    n_over = {"n_cohorts": n, "min_cohorts_per_age": base_min}
    specs = [("current", f"Current, last {n} cohorts", "Current", {**n_over, "curve_source": "live"}),
             ("goal", f"Goal, saved {eng.month_text(goal_snap)}" if goal_snap else "Goal", "Goal", {"curve_source": "goal"}),
             ("asof", f"As of {eng.month_text(asof)}, last {n} cohorts", f"As of {eng.month_text(asof)}",
              {**n_over, "curve_source": "asof", "asof_month": asof.isoformat()})]
    if asof2:
        specs.append(("asof2", f"As of {eng.month_text(asof2)}, last {n} cohorts", f"As of {eng.month_text(asof2)}",
                      {**n_over, "curve_source": "asof", "asof_month": asof2.isoformat()}))
    opts = [_run(conn, scn, today, ov, key, label, short) for key, label, short, ov in specs]
    base = opts[0]
    for o, (key, _l, _s, _ov) in zip(opts, specs):
        o["vs_current"] = _delta(o, base)
        o["active"] = _matches_saved(saved, "asof" if key == "asof2" else key, n, asof2 if key == "asof2" else asof)
    return {"scenario": scn.slug, "version": scn.version, "year": base["fy"]["year"], "n": n, "ages": list(range(1, eng.CURVE_AGES + 1)),
            "saved_label": eng.curve_label(saved, goal_snap), "options": opts,
            "multiples": sorted({m for o in opts for m in o["fy"]["ev"]}, key=lambda t: float(t[:-1])), "loi": base["fy"]["loi"]}
