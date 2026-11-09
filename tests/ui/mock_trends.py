"""Mock of GET /api/trends for tests/ui/mock_server.py.

Made-up days (2025-01-01 .. 2026-11-04, "today" = 2026-11-05 Pacific) with a weekly rhythm, a 10-day Meta hole in March 2025, Upstack attribution only from
2025-05-13 (Google) and 2026-05-06 (Meta) and a reach feed of non-additive day / week / month rows, run through the REAL payload builder
(app.trends.build_periods / assemble): periods, like-for-like bases, dashes and changes are the production code; only the database is replaced.
`?as_of=YYYY-MM-DD` moves "today" (the UI tests use it to open on a Monday, the 1st of a month, ...), `?fail=1` answers a 503 for the error-box test.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import queries as q, trends as T            # noqa: E402

D = dt.date
FIRST, LAST = D(2025, 1, 1), D(2026, 11, 4)
TODAY = D(2026, 11, 5)
HOLE = (D(2025, 3, 4), D(2025, 3, 13))                  # Meta has no rows on these days (the real sheet history has this hole)
META_UP_FIRST, G_UP_FIRST = D(2026, 5, 6), D(2025, 5, 13)
TRAFFIC_FIRST = D(2025, 5, 8)                            # the traffic feed (new visitors) starts here


def _build():
    R = random.Random(5)
    RV = random.Random(77)                                   # its own stream: the golden file pins the numbers drawn from R
    fr, meta_days, prod_days = {}, set(), set()
    reach_day = {}
    for d in T.daterange(FIRST, LAST):
        wk = 1.0 + 0.12 * math.sin(d.weekday() / 7 * 2 * math.pi) + 0.35 * (d.year == 2026 and d.month >= 5)
        ms = round(4500 * wk * R.uniform(0.85, 1.15), 2)
        gs = round(380 * wk * R.uniform(0.8, 1.2), 2)
        mi = ms * R.uniform(95, 130)
        mc = mi * R.uniform(0.009, 0.014)
        row = dict(ms=ms, mi=mi, mc=mc, mp_own=ms / R.uniform(75, 110), mr_own=ms * R.uniform(0.62, 0.8), gs=gs, gi=gs * R.uniform(60, 80), gc=gs / R.uniform(1.0, 1.8),
                   gp_own=gs / R.uniform(40, 60), gr_own=gs * R.uniform(1.1, 1.8))
        nc_o = round(ms / R.uniform(95, 125))
        row.update(onc_ord=nc_o, onc_rev=nc_o * R.uniform(62, 72), orc=ms * R.uniform(2.0, 2.6), ts=ms * R.uniform(0.18, 0.4))
        row["ss"] = ms - row["ts"]
        row["tnc"], row["snc"] = round(nc_o * 0.3), round(nc_o * 0.8)
        row["otot"] = row["onc_rev"] + row["orc"]
        if d >= TRAFFIC_FIRST:
            row["nv"] = round(nc_o * RV.uniform(22, 30))
        if d >= G_UP_FIRST:
            row.update(gp_nc=row["gp_own"] * 0.6, gr_nc=row["gr_own"] * 0.55, gp_all=row["gp_own"] * 1.7, gr_all=row["gr_own"] * 1.6)
        if d >= META_UP_FIRST:
            row.update(mp_nc=row["mp_own"] * 0.55, mr_nc=row["mr_own"] * 0.55, mp_all=row["mp_own"] * 1.6, mr_all=row["mr_own"] * 1.5)
        shares = (0.34, 0.4, 0.22)
        for (slug, _n), sh in zip(T.PRODUCTS, shares):
            row[f"{slug}_spend"] = ms * sh * R.uniform(0.9, 1.1)
            row[f"{slug}_ncrev"] = row["onc_rev"] * sh * R.uniform(0.9, 1.1)
            row[f"{slug}_ncord"] = round(nc_o * sh)
        if not (HOLE[0] <= d <= HOLE[1]):
            meta_days.add(d)
            prod_days.add(d)
        fr[d] = row
        reach_day[d] = (mi, mi / R.uniform(1.6, 2.6), mc * 0.8)

    def grain_rows(grain):
        rows, d = {}, FIRST
        spans = {}
        for d in T.daterange(FIRST, LAST):
            a, b = T.span_of(grain, d)
            spans[a] = (a, b)
        for a, (a_, b) in spans.items():
            end = min(b, TODAY)                      # the feed's current period runs through today (partial)
            days = [x for x in T.daterange(a, end) if x in reach_day]
            if not days:
                continue
            impr = sum(reach_day[x][0] for x in days)
            reach = sum(reach_day[x][1] for x in days) * (0.62 if grain == "week" else 0.4 if grain == "month" else 1.0)
            rows[a] = {"end": end, "impr": impr, "reach": reach, "uoc": sum(reach_day[x][2] for x in days)}
        return rows
    return fr, meta_days, prod_days, {g: grain_rows(g) for g in T.INTERVALS}


FR, META_DAYS, PROD_DAYS, REACH = _build()


def _cov():
    return {"meta": T.Cov(FIRST, LAST, META_DAYS), "meta_up": T.Cov(META_UP_FIRST, LAST), "google": T.Cov(FIRST, LAST), "google_up": T.Cov(G_UP_FIRST, LAST),
            "orders": T.Cov(FIRST, LAST), "prod_spend": T.Cov(FIRST, LAST, PROD_DAYS), "prod_nc": T.Cov(FIRST, LAST), "anyclick": T.Cov(D(2025, 5, 8), LAST), "traffic": T.Cov(TRAFFIC_FIRST, LAST), "reach": T.Cov(FIRST, LAST)}


def register(app, deps):
    Bad = deps["Bad"]

    @app.get("/api/trends")
    def trends_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        try:
            opt = T.parse_args(request.args)
        except q.ApiError as e:
            raise Bad(e.message)
        today = opt["as_of"] or TODAY
        cov = _cov()
        through = min(LAST, today - dt.timedelta(days=1))
        for c in cov.values():
            c.last = min(c.last, through)
        periods = T.build_periods(opt["interval"], today, through, opt["periods"])
        return jsonify(T.assemble(opt, today, cov, through, periods, FR, REACH[opt["interval"]]))

    @app.get("/api/trends/plan")
    def trends_plan_mock():
        return jsonify(plan_mock(request.args))


def plan_mock(args) -> dict:
    """GET /api/trends/plan for the mock: made-up projected / target numbers per month, shaped exactly like app.trends.plan_payload (the real numbers come from the pacing module)."""
    if args.get("fail") == "1":
        return {"available": False, "reason": "The plan figures could not be loaded (mock failure).", "months": [], "values": {}}
    frm = D.fromisoformat(args["from"]).replace(day=1)
    to = D.fromisoformat(args["to"]).replace(day=1)
    months, d = [], frm
    while d <= to:
        months.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    R = random.Random(11)
    vals = {k: [] for k in ("total_rev", "total_spend", "nc_rev", "ebitda", "noi")}
    out = {}
    for m in months:
        S = T.sums(FR, m, T.month_end(m))
        base = {"total_rev": S["otot"] * 1.4, "total_spend": (S["ms"] + S["gs"]) * 1.1, "nc_rev": S["onc_rev"], "ebitda": S["otot"] * 0.12, "noi": S["otot"] * 0.1}
        for k, v in base.items():
            goal = v * R.uniform(0.9, 1.1)
            if not v:                                                       # a month before the data begins has no plan figure
                for suf in ("proj", "goal", "diff"):
                    out.setdefault(f"plan_{k}_{suf}", []).append(None)
                continue
            out.setdefault(f"plan_{k}_proj", []).append(round(v, 4))
            out.setdefault(f"plan_{k}_goal", []).append(round(goal, 4))
            out.setdefault(f"plan_{k}_diff", []).append(round(v / goal - 1, 6))
    for k in ("ebitda", "noi"):
        out[f"plan_{k}_gap"] = [None if p is None else round(p - g, 4) for p, g in zip(out[f"plan_{k}_proj"], out[f"plan_{k}_goal"])]
    return {"available": True, "reason": None, "months": [m.isoformat() for m in months], "values": out, "data_through": LAST.isoformat(),
            "caption": "Same figures as the Pacing page (mock)."}
