"""Mock of GET /api/amazon/* for tests/ui/mock_server.py.

Made-up Amazon rows run through the REAL payload builders in app/amazon.py over an in-memory source that has the same methods as the database one: the MAX / SUM
rule, the share recompute, the gaps and the alignment of ad days are production code; only the database is replaced.

Facts of the mock (the production spans, shifted): orders Oct 1 2025 .. Nov 9 2026; ads Jan 4 2026 .. Nov 9 2026 (three campaigns of Sponsored Products, one of each other type,
one campaign for all products); traffic Oct 1 2025 .. Nov 7 2026 (it lags two days); search weeks Sundays Jul 13 2025 .. Nov 1 2026 (69 weeks); settlement days Jan 2026 .. Nov 2026.
Keyword layer (Sponsored Products, loaded by hand): targeting Aug 10 .. Nov 10 2026, search terms Sep 9 .. Nov 10, lists snapshotted Nov 12 / 13; nine ad groups (one advertises two products, one has no
product ad), 42 keywords and targets, 24 customer search terms (nine of them in Search Query Performance), 2,600 negative keywords (more than the 2,000 shown at once).
Run with MOCK_AMAZON_EMPTY=1 for the "Amazon data has not loaded yet" state. `?fail=1` answers a 503.
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
from app import amazon as A, queries as q            # noqa: E402

D = dt.date
EMPTY = os.environ.get("MOCK_AMAZON_EMPTY") == "1"
ORD_FIRST, ORD_LAST = D(2025, 10, 1), D(2026, 11, 9)
ADS_FIRST, ADS_LAST = D(2026, 1, 4), D(2026, 11, 9)
TRF_FIRST, TRF_LAST = D(2025, 10, 1), D(2026, 11, 7)
FIRST_WEEK, LAST_WEEK = D(2025, 7, 13), D(2026, 11, 1)
FEE_FIRST, FEE_LAST = D(2026, 1, 3), D(2026, 11, 5)
ASINS = [("B0VB000001", "vita-bull", "Vita Bull 60 count", 24.0), ("B0BL000001", "beef-liver", "Beef Liver 90 count", 29.0), ("B0BL000002", "beef-liver", "Beef Liver 180 count", 49.0),
         ("B0BF000001", "boneflex", "Boneflex 120 count", 34.0), ("B0VE000001", "vita-belle", "Vital Belle 60 count", 27.0), ("B0XX000001", None, "Variety bundle", 80.0)]
CAMPAIGNS = [("100", "sp", "vita-bull", "SP | Vita Bull | exact"), ("101", "sp", "beef-liver", "SP | Beef Liver | exact"), ("102", "sp", "boneflex", "SP | Boneflex | auto"), ("103", "sb", None, "SB | Brand defense"),
             ("104", "sbv", "beef-liver", "SBV | Beef Liver video"), ("105", "sd", "vita-bull", "SD | Vita Bull retargeting"), ("106", "sp", None, "SP | All Products | broad")]
STATUS = {"102": "paused", "105": "paused"}


def _dates(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def _wave(d: dt.date, k: int) -> float:
    return 1.0 + 0.25 * (((d.toordinal() * (k + 3)) % 11) / 11.0 - 0.5)


ORDERS = []
for _d in _dates(ORD_FIRST, ORD_LAST):
    for _k, (_asin, _slug, _name, _price) in enumerate(ASINS):
        _o = max(1, round((5 - _k % 4) * _wave(_d, _k) * (1 + (_d - ORD_FIRST).days / 400)))
        ORDERS.append({"day": _d, "asin": _asin, "channel": "Amazon.com", "product_slug": _slug, "orders": _o, "units": _o + (1 if _k % 2 else 0), "revenue": round(_o * _price, 2),
                       "nc_orders": max(0, _o // 3), "nc_revenue": round(max(0, _o // 3) * _price, 2)})

ADS = []
for _d in _dates(ADS_FIRST, ADS_LAST):
    for _k, (_cid, _typ, _slug, _name) in enumerate(CAMPAIGNS):
        _sp = round((6 + 3 * (6 - _k)) * _wave(_d, _k + 20), 2)
        _clk = round(_sp / (0.9 + 0.1 * _k))
        _sales = round(_sp * (2.0 + 0.4 * (_k % 3)) * _wave(_d, _k + 40), 2)
        ADS.append({"day": _d, "campaign_id": _cid, "name": _name, "status": STATUS.get(_cid, "enabled"), "ad_type": _typ, "product_slug": _slug, "spend": _sp, "impressions": _clk * 90, "clicks": _clk,
                    "sales": _sales, "orders": max(0, round(_sales / 26))})
ADS_ASIN = [{"day": r["day"], "asin": ASINS[0][0] if r["product_slug"] == "vita-bull" else ASINS[1][0] if r["product_slug"] == "beef-liver" else ASINS[3][0], "product_slug": r["product_slug"], "spend": r["spend"],
             "impressions": r["impressions"], "clicks": r["clicks"], "sales": r["sales"], "orders": r["orders"]} for r in ADS if r["ad_type"] in ("sp", "sd") and r["product_slug"]]
TRAFFIC = [{"day": d, "sessions": round(900 * _wave(d, 60) * (1 + (d - TRF_FIRST).days / 500)), "page_views": round(1300 * _wave(d, 61))} for d in _dates(TRF_FIRST, TRF_LAST)]

# (term, branded, base volume, {product: (our share of clicks in percent, ASIN index)})
TERMS = [("beef liver capsules", False, 24000, {"beef-liver": (9.0, 1), "vita-bull": (0.6, 0)}), ("beef liver supplement", False, 15000, {"beef-liver": (6.5, 1)}),
         ("desiccated beef liver", False, 9000, {"beef-liver": (11.0, 2)}), ("lonvera beef liver", True, 3200, {"beef-liver": (52.0, 1)}), ("lonvera", True, 5200, {"beef-liver": (30.0, 1), "vita-bull": (24.0, 0), "boneflex": (20.0, 3)}),
         ("vital bull", True, 2600, {"vita-bull": (61.0, 0)}), ("testosterone support", False, 22000, {"vita-bull": (2.2, 0)}), ("bovine prostate supplement", False, 3100, {"vita-bull": (14.0, 0)}),
         ("men's health vitamins", False, 31000, {"vita-bull": (0.9, 0)}), ("joint support supplement", False, 41000, {"boneflex": (1.1, 3)}), ("glucosamine chondroitin", False, 38000, {"boneflex": (0.7, 3)}),
         ("bone marrow supplement", False, 2800, {"boneflex": (12.0, 3)}), ("bone flex", True, 1200, {"boneflex": (58.0, 3)}), ("grass fed liver pills", False, 5200, {"beef-liver": (4.0, 2)}),
         ("iron supplement for women", False, 52000, {"beef-liver": (0.5, 1), "vita-belle": (0.4, 4)}), ("menopause support", False, 33000, {"vita-belle": (0.8, 4)}),
         ("hair growth vitamins", False, 61000, {"beef-liver": (0.3, 1), "vita-belle": (0.2, 4)}), ("vital belle", True, 900, {"vita-belle": (64.0, 4)}),
         ("organ meat supplement", False, 7400, {"beef-liver": (8.0, 1), "vita-bull": (3.0, 0)}), ("liver support cleanse", False, 12500, {"beef-liver": (1.4, 1)})]
SQP = []
WEEKS = [FIRST_WEEK + dt.timedelta(days=7 * i) for i in range((LAST_WEEK - FIRST_WEEK).days // 7 + 1)]
for _i, _wk in enumerate(WEEKS):
    for _t, (_term, _br, _vol, _prods) in enumerate(TERMS):
        _v = round(_vol * (1 + 0.15 * (((_i * 7 + _t * 3) % 10) / 10 - 0.5)) * (1 + _i / 120))
        _mt = (_v * 25, round(_v * 0.55), round(_v * 0.09), round(_v * 0.05))
        for _p, (_share, _ai) in _prods.items():
            if (_t + _i) % 6 == 0 and _p != "beef-liver":                       # a term that drops out of the top terms of that product in some weeks: a gap
                continue
            _f = _share / 100 * (1 + 0.2 * (((_i + _t) % 5) / 5 - 0.4))
            SQP.append({"period_start": _wk, "query_key": _term, "search_query": _term.title() if not _br else _term, "asin": ASINS[_ai][0], "product_slug": _p, "is_branded": _br,
                        "search_query_volume": _v, "total_impressions": _mt[0], "asin_impressions": round(_mt[0] * _f), "total_clicks": _mt[1], "asin_clicks": round(_mt[1] * _f),
                        "total_cart_adds": _mt[2], "asin_cart_adds": round(_mt[2] * _f * 1.1), "total_purchases": _mt[3], "asin_purchases": round(_mt[3] * _f * 1.2)})
FEES = []
for _d in _dates(FEE_FIRST, FEE_LAST)[::4]:
    for _desc, _tt, _at, _amt in (("Principal", "Order", "ItemPrice", 3100.0), ("Commission", "Order", "ItemFees", -465.0), ("FBA per unit fulfillment fee", "Order", "ItemFees", -820.0),
                                  ("Cost of Advertising", "ServiceFee", "Other", -540.0), ("Storage Fee", "ServiceFee", "Other", -60.0), ("Promotion", "Order", "Promotion", -90.0)):
        FEES.append({"day": _d, "transaction_type": _tt, "amount_type": _at, "amount_description": _desc, "lines": 9, "amount": round(_amt * _wave(_d, len(_desc)), 2)})


# ---------------------------------------------------------------------------------------------------------------- keyword layer
KW_TG_FIRST, KW_LAST, KW_ST_FIRST = D(2026, 8, 10), D(2026, 11, 10), D(2026, 9, 9)
KW_SNAP, NEG_SNAP, KW_LOADED = D(2026, 11, 12), D(2026, 11, 13), D(2026, 11, 13)
# (ad group id, campaign id, campaign name, ad group name, product key)
AGS = [("g1", "c1", "Vita Bull | Exact", "Vita Bull exact", "vita-bull"), ("g2", "c1", "Vita Bull | Exact", "Vita Bull phrase", "vita-bull"), ("g3", "c2", "Beef Liver | Broad", "Beef Liver broad", "beef-liver"),
       ("g4", "c2", "Beef Liver | Broad", "Beef Liver exact", "beef-liver"), ("g5", "c3", "Boneflex | Auto", "Boneflex auto", "boneflex"), ("g6", "c4", "Vital Belle | Phrase", "Vital Belle phrase", "vita-belle"),
       ("g7", "c5", "All Products | Catch all", "Catch all keywords", "mixed"), ("g8", "c5", "All Products | Catch all", "Catch all targets", "mixed"), ("g9", "c6", "Old campaign", "Old ad group", "unmapped")]
KW_TEXT = {"vita-bull": ["testosterone support", "bovine prostate supplement", "vital bull", "men's health vitamins", "beef organs for men", "lonvera vital bull"],
           "beef-liver": ["beef liver capsules", "desiccated beef liver", "lonvera beef liver", "organ meat supplement", "grass fed liver pills", "liver support cleanse", "beef liver supplement"],
           "boneflex": ["joint support supplement", "bone marrow supplement", "glucosamine chondroitin", "bone flex"], "vita-belle": ["menopause support", "vital belle", "hair growth vitamins"],
           "mixed": ["lonvera", "organ supplements", "ancestral supplements", "natural vitamins"], "unmapped": ["old keyword", "legacy term"]}
KW_ITEMS = []                  # one per keyword or target: id, text, match, ad group, bid, state
_n = 0
for _ag, _cid, _cn, _agn, _pk in AGS:
    for _i, _txt in enumerate(KW_TEXT[_pk][:4 + (_pk == "beef-liver")]):
        _n += 1
        _m = ["EXACT", "PHRASE", "BROAD"][_i % 3]
        KW_ITEMS.append({"keyword_id": f"k{_n}", "text": _txt, "match_type": _m, "ad_group_id": _ag, "campaign_id": _cid, "campaign_name": _cn, "ad_group_name": _agn, "product": _pk,
                         "bid": round(1.2 + 0.35 * (_n % 9), 2), "state": "ENABLED" if _n % 7 else "PAUSED", "kind": "keyword"})
for _ag, _cid, _cn, _agn, _pk in (AGS[4], AGS[7]):
    for _j, _t in enumerate(["loose-match", "asin=\"B0VB000001\"", "substitutes"]):
        _n += 1
        KW_ITEMS.append({"keyword_id": f"t{_n}", "text": _t, "match_type": "TARGETING_EXPRESSION_PREDEFINED" if _j != 1 else "TARGETING_EXPRESSION", "ad_group_id": _ag, "campaign_id": _cid, "campaign_name": _cn,
                         "ad_group_name": _agn, "product": _pk, "bid": None if _j == 0 else 0.9, "state": "ENABLED", "kind": "target"})
KW_DAILY = []                  # (day, item, impressions, clicks, cost, purchases, sales); the last two keywords of the list never get an impression
for _k, _it in enumerate(KW_ITEMS[:-2]):
    for _d in _dates(KW_TG_FIRST, KW_LAST):
        _imp = round((400 + 90 * (_k % 7)) * _wave(_d, _k + 70))
        _clk = round(_imp * (0.004 + 0.002 * (_k % 5)))
        _cost = round(_clk * (0.8 + 0.25 * (_k % 6)), 2)
        _pur = round(_clk * (0.08 + 0.03 * (_k % 4)))
        KW_DAILY.append((_d, _it, _imp, _clk, _cost, _pur, round(_pur * 31.5, 2)))
# customer search terms: (term, share of the keyword list it is served on); nine of them are organic search terms of TERMS above
ST_TERMS = ["beef liver capsules", "desiccated beef liver", "lonvera beef liver", "organ meat supplement", "bovine prostate supplement", "bone marrow supplement", "vital bull", "bone flex", "vital belle",
            "beef liver pills for women", "liver supplement without taste", "best organ supplement", "b0vb000001", "desiccated  Beef Liver Pills", "cheap beef liver", "ox liver tablets", "joint pain pills",
            "beef bone marrow capsules", "men testosterone booster", "prostate health", "menopause hair loss", "energy supplement", "bull testicle supplement", "liver detox"]
ST_DAILY = []
for _t, _term in enumerate(ST_TERMS):
    for _c in range(1 + _t % 3):
        _it = KW_ITEMS[(_t * 5 + _c * 11) % (len(KW_ITEMS) - 2)]
        for _d in _dates(KW_ST_FIRST, KW_LAST):
            _imp = round((60 + 25 * (_t % 6)) * _wave(_d, _t + _c))
            _clk = max(0, round(_imp * 0.05))
            _cost = round(_clk * (1.1 + 0.2 * (_t % 5)), 2)
            _pur = round(_clk * 0.15)
            ST_DAILY.append((_d, _term, _it, _imp, _clk, _cost, _pur, round(_pur * 33.0, 2)))
NEGS = []
for _i in range(2600):
    _ag = AGS[_i % len(AGS)]
    NEGS.append({"keyword_id": f"n{_i}", "text": f"negative term {_i:04d}" if _i % 50 else f"collagen {_i}", "match_type": "NEGATIVE_EXACT" if _i % 9 == 0 else "NEGATIVE_PHRASE",
                 "state": "ARCHIVED" if _i % 130 == 0 else "ENABLED", "campaign_id": _ag[1], "ad_group_id": _ag[0], "campaign_name": _ag[2], "ad_group_name": _ag[3], "product": _ag[4]})


def _kw_sums_of(rows):
    return {"impressions": sum(r[2] for r in rows), "clicks": sum(r[3] for r in rows), "cost": round(sum(r[4] for r in rows), 2), "purchases": sum(r[5] for r in rows), "sales": round(sum(r[6] for r in rows), 2)}



def _sum(rows, keys):
    return {k: (sum(float(r[k]) for r in rows if r.get(k) is not None) if any(r.get(k) is not None for r in rows) else None) for k in keys}


def _group(rows, keyfn, keys, extra=None):
    out: dict = {}
    for r in rows:
        out.setdefault(keyfn(r), []).append(r)
    res = []
    for k, rs in out.items():
        row = {"key": k, **_sum(rs, keys)}
        if extra:
            row.update(extra(rs))
        res.append(row)
    return res


ORD_KEYS = ("orders", "units", "revenue", "nc_orders", "nc_revenue")
AD_KEYS = ("spend", "impressions", "clicks", "sales", "orders")


def _kw_inrange(f, t):
    return [r for r in KW_DAILY if f <= r[0] <= t]


class KwMixin:
    """the keyword layer's source methods: the same return shapes as app.amazon.DbSource"""

    def kw_info(self):
        if EMPTY:
            return {"st": None, "tg": None, "loaded": None, "kw_snapshot": None, "tg_snapshot": None, "neg_snapshot": None}
        return {"st": (KW_ST_FIRST, KW_LAST), "tg": (KW_TG_FIRST, KW_LAST), "loaded": KW_LOADED, "kw_snapshot": KW_SNAP, "tg_snapshot": KW_SNAP, "neg_snapshot": NEG_SNAP}

    def kw_report(self, f, t):
        out: dict = {}
        for r in _kw_inrange(f, t):
            out.setdefault(r[1]["keyword_id"], []).append(r)
        res = []
        for kid, rows in out.items():
            it = rows[0][1]
            res.append({"campaign_id": it["campaign_id"], "ad_group_id": it["ad_group_id"], "keyword_id": kid, "match_type": it["match_type"], "text": it["text"], "campaign_name": it["campaign_name"],
                        "ad_group_name": it["ad_group_name"], "product": it["product"], **_kw_sums_of(rows)})
        return res

    def kw_list(self):
        return [{**{k: v for k, v in it.items() if k != "kind"}, "kind": it["kind"], "text": it["text"], "keyword_id": it["keyword_id"]} for it in KW_ITEMS]

    def _st_rows(self, f, t, product, key=None):
        return [r for r in ST_DAILY if f <= r[0] <= t and (not product or r[2]["product"] == product) and (key is None or A.norm_query(r[1]) == key)]

    def adsearch_grouped(self, f, t, product):
        out: dict = {}
        for r in self._st_rows(f, t, product):
            out.setdefault(A.norm_query(r[1]), []).append(r)
        res = []
        for key, rows in out.items():
            sm = {"impressions": sum(r[3] for r in rows), "clicks": sum(r[4] for r in rows), "cost": round(sum(r[5] for r in rows), 2), "purchases": sum(r[6] for r in rows), "sales": round(sum(r[7] for r in rows), 2)}
            res.append({"key": key, "term": rows[0][1], "keywords": len({r[2]["keyword_id"] for r in rows}), "campaigns": len({r[2]["campaign_id"] for r in rows}), **sm})
        return res

    def adsearch_matches(self, f, t, key, product):
        out: dict = {}
        for r in self._st_rows(f, t, product, key):
            out.setdefault(r[2]["keyword_id"], []).append(r)
        res = []
        for kid, rows in out.items():
            it = rows[0][2]
            res.append({"campaign_id": it["campaign_id"], "ad_group_id": it["ad_group_id"], "keyword_id": kid, "match_type": it["match_type"], "text": it["text"], "term": rows[0][1],
                        "campaign_name": it["campaign_name"], "ad_group_name": it["ad_group_name"], "product": it["product"],
                        "impressions": sum(r[3] for r in rows), "clicks": sum(r[4] for r in rows), "cost": round(sum(r[5] for r in rows), 2), "purchases": sum(r[6] for r in rows), "sales": round(sum(r[7] for r in rows), 2)})
        return res

    def negatives(self, needle, match_type, state, product, limit):
        base = [r for r in NEGS if not needle or needle in (r["text"] + " " + r["campaign_name"] + " " + r["ad_group_name"]).lower()]
        kept = [r for r in base if (not match_type or r["match_type"] == match_type) and (not state or r["state"] == state) and (not product or r["product"] == product)]
        kept.sort(key=lambda r: (r["text"].lower(), r["keyword_id"]))
        count = lambda key: {k: sum(1 for r in base if r[key] == k) for k in {r[key] for r in base}}      # noqa: E731
        return {"snapshot_date": NEG_SNAP, "rows": kept[:limit], "matched": len(kept), "total": len(NEGS), "by_state": {k.lower(): v for k, v in count("state").items()},
                "by_match": count("match_type"), "by_product": count("product")}


class MemSource(KwMixin):
    def spans(self):
        if EMPTY:
            return dict.fromkeys(("orders", "ads", "ads_asin", "traffic", "fees", "sqp", "kw_st", "kw_tg", "kw_neg"))
        return {"kw_st": (KW_ST_FIRST, KW_LAST), "kw_tg": (KW_TG_FIRST, KW_LAST), "kw_neg": (NEG_SNAP, NEG_SNAP), "orders": (ORD_FIRST, ORD_LAST), "ads": (ADS_FIRST, ADS_LAST), "ads_asin": (ADS_FIRST, ADS_LAST), "traffic": (TRF_FIRST, TRF_LAST), "fees": (FEE_FIRST, FEE_LAST),
                "sqp": (FIRST_WEEK, LAST_WEEK + dt.timedelta(days=6))}

    def orders_by_day(self, f, t):
        return [{"day": k, **_sum(rs, ORD_KEYS)} for k, rs in _bucket(ORDERS, f, t, lambda r: r["day"]).items()]

    def orders_grouped(self, f, t, by):
        key = (lambda r: r["product_slug"]) if by == "product" else (lambda r: r["asin"])
        return _group([r for r in ORDERS if f <= r["day"] <= t], key, ORD_KEYS, lambda rs: {"product_slug": rs[0]["product_slug"]})

    def ads_by_day(self, f, t, campaign_id=None):
        rows = [r for r in ADS if (campaign_id is None or r["campaign_id"] == campaign_id)]
        return [{"day": k, **_sum(rs, AD_KEYS)} for k, rs in _bucket(rows, f, t, lambda r: r["day"]).items()]

    def ads_grouped(self, f, t, by):
        rows = [r for r in ADS if f <= r["day"] <= t]
        if by == "campaign":
            return _group(rows, lambda r: r["campaign_id"], AD_KEYS, lambda rs: {"name": rs[-1]["name"], "status": rs[-1]["status"], "ad_type": rs[0]["ad_type"], "product_slug": rs[0]["product_slug"],
                                                                                "last_day": max(r["day"] for r in rs)})
        return _group(rows, (lambda r: r["product_slug"]) if by == "product" else (lambda r: r["ad_type"]), AD_KEYS)

    def ads_asin_grouped(self, f, t):
        return _group([r for r in ADS_ASIN if f <= r["day"] <= t], lambda r: r["asin"], AD_KEYS, lambda rs: {"product_slug": rs[0]["product_slug"]})

    def traffic_by_day(self, f, t):
        return [r for r in TRAFFIC if f <= r["day"] <= t]

    def asin_names(self):
        return {a: {"name": n, "product_slug": s} for a, s, n, _ in ASINS}

    def weeks(self):
        return [] if EMPTY else [(w, w + dt.timedelta(days=6)) for w in WEEKS]

    def sqp_rows(self, first_start, last_start, product, branded, text, term):
        return [r for r in SQP if first_start <= r["period_start"] <= last_start and (not product or r["product_slug"] == product) and (branded is None or r["is_branded"] == branded)
                and (not text or text in r["query_key"]) and (not term or r["query_key"] == term)]

    def fees_grouped(self, f, t):
        rows = [r for r in FEES if f <= r["day"] <= t]
        out: dict = {}
        for r in rows:
            k = (r["day"].replace(day=1), r["transaction_type"], r["amount_type"], r["amount_description"])
            e = out.setdefault(k, {"month": k[0], "transaction_type": k[1], "amount_type": k[2], "amount_description": k[3], "lines": 0, "amount": 0.0})
            e["lines"] += r["lines"]
            e["amount"] += r["amount"]
        return list(out.values())


def _bucket(rows, f, t, keyfn):
    out: dict = {}
    for r in rows:
        if f <= r["day"] <= t:
            out.setdefault(keyfn(r), []).append(r)
    return out


def register(app, deps):
    Bad = deps["Bad"]
    src = MemSource()

    def reply(view, kind, build):
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        try:
            ameta = A.span_meta(src, kind)
            if ameta is None:
                return jsonify(A.empty_payload(view))
            w = A.parse(request.args, ameta)
            info = A.window_info(w, ameta)
            return jsonify({**build(src, ameta, w), "window": info, "window_info": info, "compare_info": None})
        except q.ApiError as e:
            raise Bad(e.message)

    arg = lambda n: (request.args.get(n) or "").strip() or None          # noqa: E731

    @app.get("/api/amazon/overview")
    def amz_overview():
        return reply("overview", "overview", lambda s, m, w: A.overview(s, m, w))

    @app.get("/api/amazon/trend")
    def amz_trend():
        return reply("trend", "overview", lambda s, m, w: A.trend(s, m, w, (arg("grain") or "day").lower()))

    @app.get("/api/amazon/campaigns")
    def amz_campaigns():
        return reply("campaigns", "ads", lambda s, m, w: A.campaigns(s, m, w, arg("ad_type"), arg("product"), arg("q")))

    @app.get("/api/amazon/campaign-trend")
    def amz_campaign_trend():
        return reply("campaign-trend", "ads", lambda s, m, w: A.campaign_trend(s, m, w, (arg("grain") or "day").lower(), arg("campaign_id")))

    @app.get("/api/amazon/products")
    def amz_products():
        return reply("products", "products", lambda s, m, w: A.products(s, m, w))

    @app.get("/api/amazon/search-terms")
    def amz_search_terms():
        return reply("search-terms", "sqp", lambda s, m, w: A.search_terms(s, m, w, arg("product"), arg("branded"), arg("q")))

    @app.get("/api/amazon/search-terms/history")
    def amz_term_history():
        return reply("search-term-history", "sqp", lambda s, m, w: A.term_history(s, m, arg("term"), arg("product")))

    @app.get("/api/amazon/keywords")
    def amz_keywords():
        return reply("keywords", "keywords", lambda s, m, w: A.keywords(s, m, w, arg("product"), arg("state"), arg("q"), arg("all") not in ("1", "true", "yes")))

    @app.get("/api/amazon/ad-search-terms")
    def amz_ad_search_terms():
        return reply("ad-search-terms", "adsearch", lambda s, m, w: A.ad_search_terms(s, m, w, arg("product"), arg("q")))

    @app.get("/api/amazon/ad-search-terms/matches")
    def amz_ad_search_matches():
        return reply("ad-search-term-matches", "adsearch", lambda s, m, w: A.ad_search_term_matches(s, m, w, arg("term"), arg("product")))

    @app.get("/api/amazon/negatives")
    def amz_negatives():
        return reply("negatives", "negatives", lambda s, m, w: A.negatives(s, m, arg("q"), arg("match"), arg("state"), arg("product"), int(arg("limit")) if (arg("limit") or "").isdigit() else None))

    @app.get("/api/amazon/fees")
    def amz_fees():
        return reply("fees", "fees", lambda s, m, w: A.fees(s, m, w))
