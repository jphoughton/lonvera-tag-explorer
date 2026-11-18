"""Mock of GET /api/meta/pages for tests/ui/mock_server.py: made-up page rows through the REAL payload builder in app/meta_pages.py (window, ratios of sums, groups, Total).

Facts of the mock: Lonvera page ($60,000 spend, 600,000 purchases-value-style round numbers below), one other handle named 'Other handle', and an unknown page; the mock's last loaded day is
Nov 2 2026. Every row is the same shape each day, scaled by the number of days in the window. MOCK_PAGES_EMPTY=1 -> 'Page identity has not loaded yet'."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import meta_pages as M, queries as q            # noqa: E402

D = dt.date
EMPTY = os.environ.get("MOCK_PAGES_EMPTY") == "1"
LAST = D(2026, 11, 2)
# page_id: (name, is_lonvera, ads, spend/day, impressions/day, link clicks/day, purchases/day, purchase value/day, upstack spend/day, new customers/day, NC revenue/day)
DAILY = {"161085663758792": ("Lonvera", True, 40, 2000.0, 100000, 1500, 30, 1800.0, 2000.0, 20, 1200.0),
         "830267930177476": ("Other handle", False, 10, 500.0, 25000, 300, 5, 300.0, 500.0, 3, 180.0),
         None: ("(unknown page)", False, 3, 50.0, 2000, 20, 0, 0.0, 50.0, 0, 0.0)}


class MemSource:
    def pages(self, lo, hi, product, stages):
        n = (min(hi, LAST) - lo).days + 1
        if n <= 0:
            return []
        scale = 0.5 if product else 1.0
        return [{"page_id": pid, "page_name": v[0], "is_lonvera": v[1], "ads": v[2], "spend": v[3] * n * scale, "impressions": v[4] * n * scale, "link_clicks": v[5] * n * scale,
                 "purchases": v[6] * n * scale, "purchase_value": v[7] * n * scale, "up_spend": v[8] * n * scale, "nc": v[9] * n * scale, "nc_revenue": v[10] * n * scale} for pid, v in DAILY.items()]

    def products(self, lo, hi):
        return [{"slug": "vita-bull", "name": "Vita Bull", "spend": 1.0}, {"slug": "beef-liver", "name": "Beef Liver", "spend": 1.0}]

    def coverage(self):
        return {"ads": 0, "with_page": 0, "first_seen": None, "last_seen": None} if EMPTY else {"ads": 53, "with_page": 50, "first_seen": D(2026, 11, 18), "last_seen": D(2026, 11, 18)}


def register(app, deps):
    Bad = deps["Bad"]
    src = MemSource()

    @app.get("/api/meta/pages")
    def mock_pages():
        try:
            meta = q.DataMeta(daily_start=D(2026, 5, 6), data_through=LAST, impressions_from=D(2026, 5, 6))
            w = q.parse_window({k: request.args[k] for k in ("window", "from", "to", "stage") if k in request.args}, meta)
            return jsonify(M.build(src, w, meta, request.args.get("product")))
        except q.ApiError as e:
            raise Bad(e.message)
