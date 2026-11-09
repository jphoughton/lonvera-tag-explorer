"""Mock of GET /api/promotions (Test to scale) for tests/ui/mock_server.py.

A deterministic set of creatives in the shape of the SQL view creative_stage_moves (migration 015) is run through the REAL payload builder
(app.promotions.assemble), so the response has exactly the production shape and rules (populations, bulk days, held-up calls, cohorts, series); only
the database is replaced by in-memory lookups. Window / product / hide_bulk / bulk_min / min_test_spend / grain / source / basis really change the answer.
"""
from __future__ import annotations

import copy
import decimal
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from flask import Response, jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import promotions as P, queries as q, scale_views as SV            # noqa: E402

D0, THROUGH = date(2026, 5, 6), date(2026, 11, 2)
IDX_THROUGH = (THROUGH - D0).days
BULK_DAY = D0 + timedelta(days=169)                       # Oct 22: 45 creatives enter scaling the same day
PRODUCTS = [("vita-bull", "Vita Bull", 1), ("beef-liver", "Beef Liver", 2), ("boneflex", "Boneflex", 3)]
TAGS = {"vita-bull": ("Low T & Libido", "Number Optimizer", "Low Energy"), "beef-liver": ("Hair Thinning", "Denise Thinning Hair", "Hair Loss"),
        "boneflex": ("Joint Pain", "Eileen Floor-Proof", "Knee Pain")}
CAMPAIGNS = {"vita-bull": ["VitaBull_US_Scaling_CC_1", "VitaBull_WW_Scaling_CC_2"], "beef-liver": ["BeefLiver_US_Scaling_CC_1", "BeefLiver_US_Scaling_CC_2"],
             "boneflex": ["Boneflex_US_Scaling_CC_1", "Boneflex_WW_Scaling_CC_3"]}
UP_MULT = {"all": 1.7, "nc": 0.55}                        # Upstack twins of the purchases / revenue (spend stays Meta's)


def _json_default(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()                      # the real app's JSON provider writes dates as ISO strings; Flask's default would write HTTP dates
    raise TypeError(type(o))


def _day(i: int) -> date:
    return D0 + timedelta(days=i)


def _build() -> list[dict]:
    R = random.Random(11)
    rows: list[dict] = []
    nid = [5000]

    def base(prod, name, path, first, **kw):
        nid[0] += 1
        slug, pname, _ = next(p for p in PRODUCTS if p[0] == prod)
        cl, icp, pain = TAGS[prod]
        r = dict(family_key=name.lower(), tracker_row_id=nid[0], product_id=1, product_slug=slug, product_name=pname, concept_id=name, display=name,
                 ad_type=R.choice(["VID", "VID", "IMG"]), tracker_status="8 - Launched", tracker_launch_date=None, cluster=cl, icp=icp, pain=pain,
                 data_start=D0, data_through=THROUGH, path=path, began_in_testing=path in ("moved", "testing_only"), moved=path == "moved",
                 first_day=first, testing_first_day=None, scaling_first_day=None, last_spend_day=first, scaling_last_day=None, days_in_test=None,
                 launched_before_data_start=first == D0, test_spend=0.0, test_purchases=0.0, test_revenue=0.0, test_link_clicks=0.0, test_days=0,
                 test_after_spend=0.0, scale_spend=0.0, scale_purchases=0.0, scale_revenue=0.0, scale_link_clicks=0.0, scale_days=0, unclassified_spend=0.0,
                 last7_spend=0.0, last7_purchases=0.0, last7_revenue=0.0, last7_scale_spend=0.0, last7_scale_purchases=0.0, last7_scale_revenue=0.0,
                 live=False, live_scaling=False, moves_that_day=0, bulk_move_day=False)
        r.update(kw)
        rows.append(r)
        return r

    def tested(r, days, spend, roas):
        r["testing_first_day"] = r["first_day"]
        r["test_days"] = days
        r["test_spend"] = round(spend, 2)
        r["test_revenue"] = round(spend * roas, 2)
        r["test_purchases"] = float(round(spend * roas / 70))
        r["test_link_clicks"] = float(round(spend / 2.4))

    def scaled(r, move_idx, spend, roas):
        r["scaling_first_day"] = _day(move_idx)
        r["days_in_test"] = (r["scaling_first_day"] - r["testing_first_day"]).days
        n = max(1, IDX_THROUGH - move_idx + 1)
        r["scale_days"] = n
        r["scale_spend"] = round(spend, 2)
        r["scale_revenue"] = round(spend * roas, 2)
        r["scale_purchases"] = float(round(spend * roas / 70))
        r["scale_link_clicks"] = float(round(spend / 2.4))
        r["live_scaling"] = r["live"] = R.random() < 0.6
        r["last_spend_day"] = THROUGH if r["live"] else _day(min(IDX_THROUGH, move_idx + R.randint(2, 20)))
        r["scaling_last_day"] = r["last_spend_day"]
        k = min(7, n)
        share = k / n if r["live"] else 0.0
        r["last7_scale_spend"] = r["last7_spend"] = round(spend * share * 0.9, 2)
        r["last7_scale_revenue"] = r["last7_revenue"] = round(spend * share * 0.9 * roas, 2)
        r["last7_scale_purchases"] = r["last7_purchases"] = float(round(r["last7_scale_revenue"] / 70))

    names = {"vita-bull": ["Bull-{}-Hook{}", "Lonvera-VitalBull-Static-{}.{}"], "beef-liver": ["Liver-{}-Hook{}", "Lonvera-BeefLiver-Static-{}.{}"],
             "boneflex": ["BoneFlex-{}-Hook{}", "Lonvera-BoneFlex-Static-{}.{}"]}

    def nm(prod):
        t = R.choice(names[prod])
        return t.format(R.randint(100, 899) if "Hook" in t else R.randint(2, 12), R.randint(1, 4) if "Hook" in t else R.randint(1, 9))

    def prod():
        return R.choice([p[0] for p in PRODUCTS])

    for _ in range(28):                                            # ordinary moves, spread over the summer and autumn
        pr = prod()
        li = R.randint(100, 168)
        gap = R.randint(2, 25)
        mi = min(IDX_THROUGH - 2, li + gap)
        r = base(pr, nm(pr), "moved", _day(li))
        tr = R.uniform(0.5, 1.4)
        tested(r, max(2, gap - 1), R.uniform(80, 1500), tr)
        scaled(r, mi, R.uniform(60, 6000), tr * R.uniform(0.5, 1.7))
    for i in range(45):                                            # the mass relaunch
        pr = prod()
        r = base(pr, f"Relaunch-{pr[:2].upper()}-{i + 1:02d}", "moved", _day(160 + i % 4))
        tr = R.uniform(0.6, 1.2)
        tested(r, 3, R.uniform(70, 400), tr)
        scaled(r, 169, R.uniform(20, 300), tr * R.uniform(0.6, 1.3))
    r = base("vita-bull", "Bull-110-Hook1", "moved", D0)           # already running on the first day of data
    tested(r, 3, 210.0, 0.9)
    scaled(r, 175, 800.0, 1.0)
    for i in range(14):                                            # began in testing, never scaled: six still spending
        pr = prod()
        li = R.randint(140, 178)
        r = base(pr, nm(pr) + "-t", "testing_only", _day(li))
        live = i < 6
        tested(r, max(1, (IDX_THROUGH - li) if live else R.randint(1, 6)), R.uniform(30, 900), R.uniform(0.3, 1.2))
        r["live"] = live
        r["last_spend_day"] = THROUGH if live else _day(min(IDX_THROUGH - 2, li + R.randint(1, 6)))
        r["last7_spend"] = round(r["test_spend"] * 0.5, 2) if live else 0.0
        r["last7_revenue"] = round(r["last7_spend"] * 0.7, 2)
    for i in range(5):                                             # launched straight into scaling
        pr = prod()
        r = base(pr, nm(pr) + "-s", "scaling_only", _day(R.randint(150, 175)))
        r["scaling_first_day"] = r["first_day"]
        r["scale_spend"] = round(R.uniform(100, 2000), 2)
        r["scale_days"] = 10
    for r in rows:
        if r["path"] == "moved":
            r["moves_that_day"] = sum(1 for x in rows if x["path"] == "moved" and x["scaling_first_day"] == r["scaling_first_day"])
            r["bulk_move_day"] = r["moves_that_day"] >= 40
    return rows


ROWS = _build()
BY_KEY = {r["family_key"]: r for r in ROWS}


MULT_KEYS = ("test_purchases", "test_revenue", "scale_purchases", "scale_revenue", "last7_purchases", "last7_revenue", "last7_scale_purchases", "last7_scale_revenue")
# the product scaling averages of the last 30 days (spend, purchases, revenue; Meta basis): Vita Bull 0.80x / $60, Beef Liver 0.80x / $60, Boneflex none
SV_BENCH = {"vita-bull": (20000.0, 333.0, 16000.0), "beef-liver": (15000.0, 250.0, 12000.0)}


def _sv_testing() -> list[dict]:
    """Testing-only creatives for the 'What should be scaled' view (deterministic): (name, product, first day index, spend, purchases, revenue, days since last spend)."""
    spec = [("Bull-901-Hook1", "vita-bull", 150, 1500, 60, 2100, 0), ("Bull-902-Hook2", "vita-bull", 165, 800, 30, 1000, 0), ("Liver-903-Hook1", "beef-liver", 170, 600, 8, 750, 0),
            ("Liver-904-Hook3", "beef-liver", 168, 900, 12, 700, 0), ("Bull-905-Hook1", "vita-bull", 176, 120, 6, 300, 0), ("Boneflex-906-Hook1", "boneflex", 172, 700, 9, 1500, 0),
            ("Bull-907-Hook2", "vita-bull", 160, 2500, 20, 1500, 0), ("Liver-908-Dead", "beef-liver", 140, 1200, 40, 3000, 5)]
    tmpl = next(r for r in ROWS if r["path"] == "testing_only")
    out = []
    for i, (name, prod, li, sp, pu, rv, dead) in enumerate(spec):
        slug, pname, _ = next(p for p in PRODUCTS if p[0] == prod)
        r = copy.deepcopy(tmpl)
        last = THROUGH - timedelta(days=dead)
        r.update(family_key=name.lower(), tracker_row_id=9000 + i, product_slug=slug, product_name=pname, display=name, concept_id=name, path="testing_only", moved=False,
                 first_day=_day(li), testing_first_day=_day(li), scaling_first_day=None, last_spend_day=last, live=dead == 0, launched_before_data_start=False,
                 test_spend=float(sp), test_purchases=float(pu), test_revenue=float(rv), test_days=10,
                 last7_spend=0.0 if dead else round(sp * 0.4, 2), last7_purchases=0.0 if dead else round(pu * 0.4, 2), last7_revenue=0.0 if dead else round(rv * 0.4, 2),
                 scale_spend=0.0, scale_purchases=0.0, scale_revenue=0.0, scale_days=0)
        out.append(r)
    return out


SV_TESTING = _sv_testing()


def _sv_kill_extras() -> list[dict]:
    """Extra testing creatives for the 'What should be killed' view only: (name, product, spend, purchases, revenue, days since last spend). Kill line = 2 x $60 = $120."""
    spec = [("Bull-911-Zero", "vita-bull", 400, 0, 0, 0), ("Bull-912-Poor", "vita-bull", 900, 5, 270, 0), ("Liver-913-Early", "beef-liver", 90, 0, 0, 0),
            ("Liver-914-Dead", "beef-liver", 700, 0, 0, 5), ("Liver-915-Medium", "beef-liver", 130, 0, 0, 0), ("Boneflex-916-NoBar", "boneflex", 500, 0, 0, 0),
            ("Liver-917-Fine", "beef-liver", 800, 10, 560, 0), ("Bull-918-Soft", "vita-bull", 1000, 10, 600, 0)]
    tmpl = next(r for r in ROWS if r["path"] == "testing_only")
    out = []
    for i, (name, prod, sp, pu, rv, dead) in enumerate(spec):
        slug, pname, _ = next(p for p in PRODUCTS if p[0] == prod)
        r = copy.deepcopy(tmpl)
        r.update(family_key=name.lower(), tracker_row_id=9100 + i, product_slug=slug, product_name=pname, display=name, concept_id=name, path="testing_only", moved=False,
                 first_day=_day(172), testing_first_day=_day(172), scaling_first_day=None, last_spend_day=THROUGH - timedelta(days=dead), live=dead == 0, launched_before_data_start=False,
                 test_spend=float(sp), test_purchases=float(pu), test_revenue=float(rv), test_days=10,
                 last7_spend=0.0 if dead else round(sp * 0.4, 2), last7_purchases=0.0 if dead else round(pu * 0.4, 2), last7_revenue=0.0 if dead else round(rv * 0.4, 2),
                 scale_spend=0.0, scale_purchases=0.0, scale_revenue=0.0, scale_days=0)
        out.append(r)
    return out


SV_KILL_EXTRAS = _sv_kill_extras()


class MemSide:
    """thumbnails, hooks, ad sets and the daily scaling spend, made up deterministically from the rows"""

    def thumbs(self, keys):
        return {k: 7000 + int(BY_KEY[k]["tracker_row_id"]) for k in keys if int(BY_KEY[k]["tracker_row_id"]) % 4}

    def hooks(self, ids):
        return {i: {"hook": f"Hook line for creative {i}: what nobody tells you about energy after 40"} for i in ids if i % 3 != 0}

    def placements(self, keys):
        out = {}
        for k in keys:
            r = BY_KEY[k]
            camps = CAMPAIGNS[r["product_slug"]]
            n = 1 + int(r["tracker_row_id"]) % 3
            share = [0.6, 0.3, 0.1][:n]
            tot = sum(share)
            top = []
            for j in range(n):
                sp = round(r["scale_spend"] * share[j] / tot, 2)
                rv = round(r["scale_revenue"] * share[j] / tot, 2)
                top.append({"campaign_id": f"c{j}", "campaign": camps[j % 2], "adset_id": f"a{j}", "adset": f"US_Broad_{j + 1}", "ads": 1 + j,
                            **P._block(sp, round(rv / 70), rv), "first_day": r["scaling_first_day"], "last_day": THROUGH})
            out[k] = {"campaigns": len({t["campaign"] for t in top}), "adsets": n, "ads": sum(t["ads"] for t in top), "spend": q.f2(r["scale_spend"]), "top": top, "more": 0}
        return out

    def scale_by_day(self, keys, lo, hi):
        out = {}
        for k in keys:
            r = BY_KEY[k]
            n = max(1, r["scale_days"])
            for i in range(n):
                d = r["scaling_first_day"] + timedelta(days=i)
                if lo <= d <= hi and d <= THROUGH:
                    e = out.setdefault(d, {"spend": 0.0, "purchases": 0.0, "revenue": 0.0})
                    e["spend"] += r["scale_spend"] / n
                    e["purchases"] += r["scale_purchases"] / n
                    e["revenue"] += r["scale_revenue"] / n
        return out


def register(app, deps):
    """deps: parse_win (the mock's window parser) and Bad (its 400 exception)."""
    parse_win, Bad = deps["parse_win"], deps["Bad"]

    def window_of(args):
        """(lo, hi, info) of the request's window with the Numbers source filled in, like the real endpoint."""
        a = {k: v for k, v in args.items() if k not in P.DROP_ARGS}
        days = a.pop("days", None)
        if days is not None:
            if not days.isdigit() or not 1 <= int(days) <= 366:
                raise Bad("days must be an integer from 1 to 366")
            a.pop("window", None)
            a["from"], a["to"] = (THROUGH - timedelta(days=int(days) - 1)).isoformat(), THROUGH.isoformat()
        elif not (a.get("window") or a.get("from") or a.get("to")):
            a["window"] = "30d"
        win = parse_win(a)
        info = dict(win["info"])
        info.setdefault("source", None)
        src = "upstack" if args.get("source") == "upstack" else "meta"
        basis = args.get("basis", "nc") if src == "upstack" else None
        if src == "upstack":
            info["source"] = dict(key="upstack", basis=basis, label="Upstack · " + ("new customers" if basis == "nc" else "all orders"), labels=q.UPSTACK_LABELS[basis],
                                  model=q.UPSTACK_MODEL, summary="Mock: Upstack's attribution of the same ads.", note=None, history_from=D0.isoformat(), through=THROUGH.isoformat())
        else:
            info["source"] = dict(key="meta", basis=None, label="Meta", labels=q.META_LABELS, model=None, summary="Mock: Meta's own reporting.", note=None)
        if win["launch"]:
            lo, hi = D0, THROUGH
            info["label"] = f"All days held · {q._fmt_day(D0, True)} – {q._fmt_day(THROUGH, True)}"
        else:
            lo, hi = date.fromisoformat(info["from"]), date.fromisoformat(info["to"])
        return lo, hi, info, src, basis

    @app.get("/api/promotions")
    def promotions_mock():
        args = request.args
        lo, hi, info, src, basis = window_of(args)
        try:
            opts = P.parse_options(args, lo, hi)
        except q.ApiError as e:
            return jsonify(error=e.message), e.status
        slug = args.get("product")
        if slug and slug not in [p[0] for p in PRODUCTS] + ["none"]:
            return jsonify(error="product not found"), 404
        mult = UP_MULT[basis] if src == "upstack" else 1.0
        rows = copy.deepcopy(ROWS)
        for r in rows:
            for k in ("test_purchases", "test_revenue", "scale_purchases", "scale_revenue", "last7_purchases", "last7_revenue", "last7_scale_purchases", "last7_scale_revenue"):
                r[k] = round(r[k] * mult, 2)
        order = {p[0]: (p[2], p[2]) for p in PRODUCTS}
        out = P.assemble([P.normalize_row(r) for r in rows], lo=lo, hi=hi, through=THROUGH, info=info, opts=opts, product_slug=slug or None, product_order=order, side=MemSide(),
                         built={"built_at": "2026-11-03T13:41:00+00:00", "data_through": THROUGH.isoformat()})
        return Response(json.dumps(out, default=_json_default), mimetype="application/json")


    @app.get("/api/promotions/scale")
    def promotions_scale_mock():
        args = request.args
        try:
            view = SV.parse_view(args)
            opts = {"should_scale": SV.parse_should_scale_options, "should_kill": SV.parse_should_kill_options, "in_scaling": SV.parse_in_scaling_options}[view](args)
        except q.ApiError as e:
            return jsonify(error=e.message), e.status
        lo, hi, info, src, basis = window_of(args)
        slug = args.get("product")
        if slug and slug not in [p[0] for p in PRODUCTS] + ["none"]:
            return jsonify(error="product not found"), 404
        mult = UP_MULT[basis] if src == "upstack" else 1.0
        built = {"built_at": "2026-11-03T13:41:00+00:00", "data_through": THROUGH.isoformat()}
        order = {p[0]: (p[2], p[2]) for p in PRODUCTS}
        scale = lambda rows: [dict(r, **{k: round(r[k] * mult, 2) for k in MULT_KEYS}) for r in rows]          # noqa: E731
        if view == "in_scaling":
            out = SV.build_in_scaling([P.normalize_row(r) for r in scale(copy.deepcopy(ROWS))], lo=lo, hi=hi, through=THROUGH, opts=opts, product_slug=slug or None,
                                      product_order=order, info=info, built=built)
        else:
            bench = [dict(product_slug=k, day_from=THROUGH - timedelta(days=29), day_to=THROUGH, creatives=12, spend=v[0], purchases=round(v[1] * mult, 2), revenue=round(v[2] * mult, 2))
                     for k, v in SV_BENCH.items()]
            if view == "should_kill":
                out = SV.build_should_kill([P.normalize_row(r) for r in scale(copy.deepcopy(SV_TESTING + SV_KILL_EXTRAS))], bench, through=THROUGH, opts=opts, product_slug=slug or None,
                                           product_order=order, product_names={p[0]: p[1] for p in PRODUCTS} | {"none": q.NO_PRODUCT}, info=info, built=built)
                return Response(json.dumps(out, default=_json_default), mimetype="application/json")
            out = SV.build_should_scale([P.normalize_row(r) for r in scale(copy.deepcopy(SV_TESTING))], bench, through=THROUGH, opts=opts, product_slug=slug or None,
                                        product_order=order, product_names={p[0]: p[1] for p in PRODUCTS} | {"none": q.NO_PRODUCT}, info=info, built=built)
        return Response(json.dumps(out, default=_json_default), mimetype="application/json")
