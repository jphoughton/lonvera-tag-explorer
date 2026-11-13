"""Mock of GET /api/inventory/history for tests/ui/mock_server.py.

Made-up snapshot rows in a plain list, run through the REAL payload builder (app.inventory_history.build / parse): the estimate marks, the gaps, the per-location first
day and the ordering are production code; only the database is replaced. Facts: Revamp has every day Nov 3..11 (real); Amazon is an estimate on every day except Nov 8,
which is missing; the international warehouse starts Nov 5 with a typed count (real), the days after are estimates. `?fail=1` answers a 503.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import inventory_history as IH, queries as q            # noqa: E402

D = dt.date
NEWEST = D(2026, 11, 11)


def rows():
    out = []
    for i in range(9):
        d = D(2026, 11, 3 + i)
        out += [{"location": "revamp", "product": "bull", "as_of": d, "on_hand": 1000 - 10 * i, "allocated": 20, "source": "warehance"},
                {"location": "revamp", "product": "liver", "as_of": d, "on_hand": 500, "allocated": 5, "source": "warehance"}]
        if d != D(2026, 11, 8):
            out.append({"location": "amazon", "product": "bull", "as_of": d, "on_hand": 300 - 3 * i, "allocated": 0, "source": "estimate_from_count"})
        if d >= D(2026, 11, 5):
            out.append({"location": "intl", "product": "bull", "as_of": d, "on_hand": 4000 - 100 * (i - 2), "allocated": 9 if d == D(2026, 11, 5) else 0,
                        "source": "typed_count" if d == D(2026, 11, 5) else "estimate_from_count"})
    return out


def register(app, deps):
    Bad = deps["Bad"]

    @app.get("/api/inventory/history")
    def inventory_mock():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        try:
            picked, days = IH.parse(request.args)
        except q.ApiError as e:
            raise Bad(e.message)
        picked = picked or NEWEST
        allrows = rows()
        firsts = {}
        for r in allrows:
            firsts[r["location"]] = min(firsts.get(r["location"], r["as_of"]), r["as_of"])
        start = picked - dt.timedelta(days=days - 1)
        return jsonify(IH.build([r for r in allrows if start <= r["as_of"] <= picked], firsts, picked, days, NEWEST))
