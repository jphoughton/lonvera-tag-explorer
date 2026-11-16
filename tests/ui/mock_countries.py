"""Mock of GET /api/meta/countries for tests/ui/mock_server.py.

Made-up daily rows run through the REAL payload builder in app/countries.py over an in-memory source with the same methods as the database one, so the window cut, the
ratios of sums, the blanks and the Total row are production code; only the database is replaced.

Facts of the mock: the Meta country breakdown covers Oct 12 .. Nov 2 2026 (the mock's last loaded day), new customers by country cover Sep 1 .. Nov 2 2026. Six countries have
spend (United States, United Kingdom, Australia, Canada, Ireland, Germany); Spain and Unknown have new customers but no spend; every day is the same shape, scaled per country.
Run with MOCK_COUNTRIES_EMPTY=1 for the "Country data has not loaded yet" state. `?fail=1` answers a 503.
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
from app import countries as C, queries as q            # noqa: E402

D = dt.date
EMPTY = os.environ.get("MOCK_COUNTRIES_EMPTY") == "1"
SPEND_FIRST, NC_FIRST, LAST = D(2026, 10, 12), D(2026, 9, 1), D(2026, 11, 2)
# country: (spend per day, impressions per day, link clicks per day, Meta purchase value per day, new customers per day, NC revenue per day)
DAILY = {"US": (10000.0, 400000, 6000, 8000.0, 100, 6800.0), "GB": (1800.0, 60000, 900, 1100.0, 17, 1100.0), "AU": (1600.0, 50000, 700, 1000.0, 12, 1000.0),
         "CA": (1000.0, 35000, 500, 700.0, 9, 680.0), "IE": (400.0, 14000, 200, 240.0, 3.5, 240.0), "DE": (50.0, 2000, 20, 0.0, 0, 0.0),
         "ES": (None, None, None, None, 1, 60.0), "unknown": (None, None, None, None, 1, 55.0)}


def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


class MemSource:
    def spans(self):
        if EMPTY:
            return {"spend": None, "nc": None}
        return {"spend": (SPEND_FIRST, LAST), "nc": (NC_FIRST, LAST)}

    def spend(self, lo, hi):
        n = len([d for d in _days(max(lo, SPEND_FIRST), min(hi, LAST))])
        return [{"country": c.upper(), "spend": v[0] * n, "impressions": v[1] * n, "link_clicks": v[2] * n, "purchase_value": v[3] * n} for c, v in DAILY.items() if v[0] is not None and n]

    def nc(self, lo, hi):
        n = len([d for d in _days(max(lo, NC_FIRST), min(hi, LAST))])
        return [{"country": c.upper(), "new_customers": int(round(v[4] * n)), "revenue": v[5] * n} for c, v in DAILY.items() if n and (v[4] or v[5])]


def register(app, deps):
    Bad = deps["Bad"]
    src = MemSource()

    @app.get("/api/meta/countries")
    def mock_countries():
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        try:
            meta = q.DataMeta(daily_start=D(2026, 5, 6), data_through=LAST, impressions_from=D(2026, 5, 6))
            w = q.parse_window({k: request.args[k] for k in ("window", "from", "to") if k in request.args}, meta)
            return jsonify(C.build(src, w, meta))
        except q.ApiError as e:
            raise Bad(e.message)
