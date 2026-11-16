"""Mock of GET /api/no-attribution/products for tests/ui/mock_server.py.

The REAL payload code in app/no_attribution.py runs over an in-memory stand-in for the database connection that answers its three queries from made-up daily rows:
spend feed Jan 1 .. Nov 9 2026 (VitaBull, Beef Liver, Boneflex, Other), new-customer feed Jan 1 .. Nov 7 2026 (the last two days have no orders yet), total new-customer revenue
that is 0.4% above the products' sum. So the cut to common days, the sums, the ratios and the tie-out are production code; only the database is replaced.
Run with MOCK_NOATTR_EMPTY=1 for the "feeds have not loaded yet" state. `?fail=1` answers a 503.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import no_attribution as N, queries as q            # noqa: E402

D = dt.date
EMPTY = os.environ.get("MOCK_NOATTR_EMPTY") == "1"
FIRST, SP_LAST, NC_LAST = D(2026, 1, 1), D(2026, 11, 9), D(2026, 11, 7)
NAMES = ["VitaBull", "Beef Liver", "Boneflex", "Other"]


def days(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def spend(d, i):  return 300 * (3 - i if i < 3 else 0.3) + d.day * 5 + 40 * i
def clicks(d, i): return 400 * (3 - i if i < 3 else 0.2) + d.day
def rev(d, i):    return 260 * (3 - i if i < 3 else 0.2) + d.weekday() * 11
def orders(d, i): return 3 * (3 - i if i < 3 else 0.2) + d.weekday() % 3


class _Res:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def execute(self, sql, params=None):
        a, b = (params["a"], params["b"]) if isinstance(params, dict) else params
        span = [d for d in days(FIRST, SP_LAST) if a <= d <= b]
        if "proj_feed_meta_product" in sql:
            return _Res([{"product": NAMES[i], "spend": sum(spend(d, i) for d in span), "link_clicks": sum(clicks(d, i) for d in span)} for i in range(4)])
        if "proj_feed_nc_product" in sql:
            nc = [d for d in span if d <= NC_LAST]
            return _Res([{"product": NAMES[i], "nc_rev": sum(rev(d, i) for d in nc), "nc_orders": sum(orders(d, i) for d in nc)} for i in range(4)])
        nc = [d for d in span if d <= NC_LAST]
        return _Res([{"s": sum(rev(d, i) for d in nc for i in range(4)) * 1.004, "n": len(nc)}])


def register(app, deps):
    Bad = deps["Bad"]

    @app.get("/api/no-attribution/products")
    def noattr_products():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        if EMPTY:
            return jsonify(N.empty_payload())
        ameta = q.DataMeta(daily_start=FIRST, data_through=min(SP_LAST, NC_LAST), impressions_from=FIRST)
        try:
            w = N.parse(request.args, ameta)
        except q.ApiError as e:
            raise Bad(e.message)
        info = N.window_info(w, ameta)
        return jsonify({**N.products(FakeConn(), ameta, w), "window": info, "window_info": info, "compare_info": None})
