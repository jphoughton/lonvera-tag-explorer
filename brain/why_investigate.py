"""The investigation of `why`: noise gate by grain and hour, the full hypothesis ledger (one verdict each), an automatic edited-vs-unedited control for every material change event,
recursion from the surviving hypothesis down to a change someone made / an external event / quantified noise, the answer contract and the memory of earlier runs.

Deterministic: SELECTs through Why.q (same guard, same step budget plus EXTRA_STEPS), no model call. Statistics live in why_stats_inv.py (pure, unit tested).
Basis: unscoped questions use the business's actual orders (orders_daily) over Meta + Google spend; product-scoped ones use the ad views' Upstack new customers. Meta delivery
numbers are Meta-reported. Never mixed in one ratio.
"""
from __future__ import annotations

import datetime as dt
import os
import statistics
from zoneinfo import ZoneInfo

from . import why_stats_inv as X

EXTRA_STEPS = 24
EXTRA_SECONDS = 45.0
MAX_EVENTS = 4
MAX_DEPTH = 4
PT = ZoneInfo("America/Los_Angeles")
NC_METRICS = ("nc_roas", "nc_cpa", "nc_revenue", "nc_aov", "conversion")


def _lit(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _day(v) -> dt.date:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _pct(a, b):
    return None if not b else (a / b - 1) * 100


def _ts(v) -> dt.datetime:
    d = v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d.astimezone(PT) if d.tzinfo else d.replace(tzinfo=PT)


class Inv:
    def __init__(self, w, bdc=None):
        self.w, self.bdc = w, bdc or {}
        self.main_steps, self.main_max, self.main_time = w.steps, w.max_steps, w.time_budget
        w.max_steps = w.steps + EXTRA_STEPS                     # the investigation has its own budget; the user's max_steps governs the metric walk
        w.time_budget += EXTRA_SECONDS
        self.ledger: list[dict] = []
        self.untestable: list[str] = []
        self.skipped_queries: list[str] = []
        self.metric = w.metric if w.metric in X.METRICS else "nc_roas"
        self.metric_note = None if w.metric in X.METRICS else f"'{w.metric}' has no daily formula here; the noise gate and ledger use nc_roas"
        self.start, self.end, self.cs, self.ce = w.start, w.end, w.cstart, w.cend
        self.lo = min(self.cs, self.end - dt.timedelta(days=36))
        self.daily: dict = {}
        self.scoped = bool(w.product)

    # ---------------------------------------------------------------- helpers
    def q(self, sql: str, limit: int = 1000):
        try:
            return self.w.q(sql, limit)
        except Exception as e:                                                       # noqa: BLE001  budget, guard, missing view: the hypothesis becomes untestable, nothing else breaks
            self.skipped_queries.append(f"{type(e).__name__}: {str(e)[:100]}")
            return None

    def add(self, hid, hypothesis, verdict, evidence, missing=None, **extra):
        e = {"id": hid, "hypothesis": hypothesis, "verdict": verdict, "evidence": evidence}
        if missing:
            e["missing"] = missing
            self.untestable.append(f"{hid}: {missing}")
        e.update(extra)
        self.ledger.append(e)
        return e

    def per(self, alias="d"):
        return (f"(values ('cur', date '{self.start}', date '{self.end}'), ('cmp', date '{self.cs}', date '{self.ce}')) p(p, s, e)")

    # ---------------------------------------------------------------- data
    def sm(self, col: str) -> str:
        return f"sum(d.{col})" if col in self.cols else "0"

    def load_daily(self) -> bool:
        w = self.w
        pc = w.prod_clause
        self.cols = {r["column_name"] for r in (self.q("select column_name from information_schema.columns where table_schema = 'analytics' and table_name = 'meta_ad_daily'") or [])}
        ads = self.q(f"select d.day, sum(d.spend) spend, sum(d.impressions) imps, sum(d.link_clicks) clicks, {self.sm('landing_page_views')} lpv, {self.sm('add_to_carts')} atc, {self.sm('initiated_checkouts')} ic, "
                     f"sum(d.meta_purchases) mp, sum(d.upstack_new_customers) nc, sum(d.upstack_new_customer_revenue) ncr from analytics.meta_ad_daily d "
                     f"where d.day between date '{self.lo}' and date '{self.end}'{pc} group by d.day order by d.day")
        if not ads:
            return False
        self.ads = {_day(r["day"]): r for r in ads}
        for d, r in self.ads.items():
            self.daily[d] = {"spend": _f(r["spend"]), "imps": _f(r["imps"]), "clicks": _f(r["clicks"]), "nc": _f(r["nc"]), "nc_rev": _f(r["ncr"])}
        self.basis = "Upstack-attributed new customers on the ad views (product-scoped)"
        if not self.scoped and w.has("orders_daily"):
            od = self.q(f"select day, new_customer_revenue nc_rev, new_customers nc from analytics.orders_daily where day between date '{self.lo}' and date '{self.end}'")
            pl = self.q(f"select day, sum(spend) spend from analytics.platform_daily where day between date '{self.lo}' and date '{self.end}' group by day") if w.has("platform_daily") else None
            if od:
                self.basis = "actual orders (orders_daily) over Meta + Google spend; impressions and clicks Meta-reported"
                orders = {_day(r["day"]): r for r in od}
                spend = {_day(r["day"]): _f(r["spend"]) for r in (pl or [])}
                for d in list(self.daily):
                    if d in orders:
                        self.daily[d]["nc_rev"], self.daily[d]["nc"] = _f(orders[d]["nc_rev"]), _f(orders[d]["nc"])
                    if d in spend:
                        self.daily[d]["spend"] = spend[d]
        return True

    def window(self, p):
        lo, hi = (self.start, self.end) if p == "cur" else (self.cs, self.ce)
        return X.window_sum(self.daily, lo, hi)

    # ---------------------------------------------------------------- 1. noise gate
    def gate(self):
        self.gate_rows = X.noise_gate(self.daily, self.end, self.metric)
        real = [g for g, r in self.gate_rows.items() if r.get("verdict") == "real"]
        self.gate_real = bool(real)
        self.finest_real = min(real) if real else None
        cur, cmp_ = self.window("cur"), self.window("cmp")
        fn = X.METRICS[self.metric][0]
        vc, vb = fn(cur), fn(cmp_)
        self.vc, self.vb = vc, vb
        self.asked_change = _pct(vc, vb) if vc is not None and vb else None
        nxt = None
        for g in (7, 3, 1):
            r = self.gate_rows.get(g, {})
            if r.get("verdict") == "inside noise":
                nxt = X.next_day_needed(self.daily, self.end, self.metric, g, r)
                if nxt:
                    break
        self.next_needed = nxt
        ck = X.METRICS[self.metric][1]
        self.asked_band = None
        if ck and cur[ck] > 0 and cmp_[ck] > 0 and self.asked_change is not None:
            self.asked_band = 2 * (1 / cur[ck] + 1 / cmp_[ck]) ** 0.5 * 100
        hourly = None
        if self.w.has("orders_hourly") and not self.scoped and self.metric in ("nc_roas", "nc_revenue", "nc_cpa"):
            rows = self.q(f"select day, hour, new_customer_revenue nc_rev, new_customers nc from analytics.orders_hourly where day between date '{self.end - dt.timedelta(days=30)}' and date '{self.end}'")
            self.hourly_rows = [{**r, "day": _day(r["day"])} for r in (rows or [])]
            hourly = X.hourly_curve(self.hourly_rows, self.end)
        else:
            self.hourly_rows = []
        self.hourly = hourly

    def grain_sentence(self) -> str:
        parts = []
        for g in X.GRAINS:
            r = self.gate_rows.get(g, {})
            if "change_pct" in r:
                parts.append(f"{g}-day {r['change_pct']:+.1f}% vs same weekdays (band +/-{r['noise_band_pct']:.0f}%): {r['verdict']}")
        asked = ""
        if self.asked_change is not None:
            asked = f"asked comparison {self.asked_change:+.1f}%" + (f" (count band +/-{self.asked_band:.0f}%: {'real' if abs(self.asked_change) > self.asked_band else 'inside noise'})" if self.asked_band else "") + "; "
        return asked + ("; ".join(parts) or "not enough days loaded")

    # ---------------------------------------------------------------- 2. ledger
    def run_ledger(self):
        cur, cmp_ = self.window("cur"), self.window("cmp")
        real = self.gate_real
        self.h_measurement()
        self.h_spend(cur, cmp_, real)
        self.h_mix(real)
        self.h_auction(cur, cmp_, real)
        self.h_creative(real)
        self.h_funnel(real)
        self.h_order_side(real)
        self.h_contamination(cur, cmp_)
        self.h_breakdowns(real)
        self.h_email(real)
        self.h_inventory_feeds()

    def h_measurement(self):
        rows = [(d, r) for d, r in sorted(self.ads.items())]
        lag = {d: (_f(r["nc"]) / self.daily[d]["nc"]) if self.daily[d]["nc"] else None for d, r in rows}
        if self.scoped:
            self.add("H1", "Measurement / attribution lag", "ruled out", "product-scoped question: both numerator and denominator are the ad views, so lag cannot create a gap between them")
            return
        last2 = [lag[d] for d in (self.end, self.end - dt.timedelta(days=1)) if lag.get(d) is not None]
        early = [lag[d] for d in lag if self.end - dt.timedelta(days=14) <= d <= self.end - dt.timedelta(days=4) and lag[d] is not None]
        if not last2 or len(early) < 5:
            self.add("H1", "Measurement / attribution lag", "untestable", "not enough days to compare the ad-attributed share of actual new customers", missing="attributed vs actual new customers per day")
            return
        a, b = statistics.fmean(last2), statistics.fmean(early)
        if self.metric in NC_METRICS or self.metric in ("spend", "meta_spend"):
            self.add("H1", "Measurement / attribution lag", "ruled out",
                     f"the headline uses actual orders (orders_daily), which do not lag; ad-attributed share of actual new customers was {a:.0%} in the last 2 days vs {b:.0%} on {self.end - dt.timedelta(days=14)} to {self.end - dt.timedelta(days=4)} "
                     f"({'ad-attributed cuts under-count the last days; do not read them as a fall' if a < b * 0.85 else 'no sign of lag'})")
        else:
            self.add("H1", "Measurement / attribution lag", "consistent with" if a < b * 0.85 else "ruled out", f"ad-attributed share of actual new customers {a:.0%} last 2 days vs {b:.0%} earlier")

    def h_spend(self, cur, cmp_, real):
        ds = _pct(cur["spend"] / max(cur["days"], 1), cmp_["spend"] / max(cmp_["days"], 1))
        if ds is None:
            self.add("H2", "Spend level", "untestable", "no spend in the comparison window", missing="spend")
            return
        # spend-level effect on the ratio: compare to the same weekdays
        g1 = self.gate_rows.get(1, {})
        v = X.factor_verdict(real, None if self.asked_change is None else min(abs(ds) / max(abs(self.asked_change), 1e-9), 1.5), abs(ds) > 8)
        if self.metric not in ("nc_roas", "nc_cpa", "nc_revenue", "spend", "meta_spend"):
            v = "inside noise" if not real else ("consistent with" if abs(ds) > 15 else "ruled out")
        self.add("H2", "Spend level (daily average)", v, f"spend per day {ds:+.1f}% ({cur['spend'] / max(cur['days'], 1):,.0f} vs {cmp_['spend'] / max(cmp_['days'], 1):,.0f}); metric {self.vc if self.vc is None else round(self.vc, 3)} vs {None if self.vb is None else round(self.vb, 3)}")

    def h_mix(self, real):
        rows = self.q(f"select p.p, d.product_slug prod, d.stage, sum(d.spend) spend, sum(d.impressions) imps, sum(d.link_clicks) clicks, {self.sm('landing_page_views')} lpv, {self.sm('add_to_carts')} atc, "
                      f"{self.sm('initiated_checkouts')} ic, sum(d.upstack_new_customers) nc, sum(d.upstack_new_customer_revenue) ncr from analytics.meta_ad_daily d join {self.per()} on d.day between p.s and p.e "
                      f"{'where true' + self.w.prod_clause if self.scoped else ''} group by 1, 2, 3")
        self.seg = rows
        if not rows:
            self.add("H3", "Spend mix by product and stage", "untestable", "no ad rows", missing="meta_ad_daily")
            return
        for dim, name in (("prod", "product"), ("stage", "stage")):
            res = self._shift(rows, dim)
            if res is None:
                self.add("H3" + name[0], f"Spend mix by {name}", "untestable", "no ad-attributed new-customer revenue in a window", missing="attributed revenue")
                continue
            v = X.factor_verdict(real, res["mix_share"], abs(res["mix_effect_pts"]) > 0 and res["max_share_shift_pts"] >= 3)
            self.add("H3" + name[0], f"Spend mix by {name} (Upstack ad-attributed NC ROAS)", v,
                     f"mix effect {res['mix_effect_pts']:+.3f} ROAS points of a {res['total_pts']:+.3f} move ({res['mix_share']:.0%}); rate effect {res['rate_effect_pts']:+.3f}; biggest share shift {res['top']}", detail=res["rows"][:4])
            if dim == "prod":
                self.prod_shift = res

    @staticmethod
    def _shift(rows, dim):
        agg = {}
        for r in rows:
            k = r[dim] or "(none)"
            a = agg.setdefault(k, {"cur": [0.0, 0.0], "cmp": [0.0, 0.0]})
            a[r["p"]][0] += _f(r["spend"])
            a[r["p"]][1] += _f(r["ncr"])
        tc = sum(a["cur"][0] for a in agg.values())
        tb = sum(a["cmp"][0] for a in agg.values())
        if not tc or not tb:
            return None
        rc = sum(a["cur"][1] for a in agg.values()) / tc
        rb = sum(a["cmp"][1] for a in agg.values()) / tb
        out, mix, rate = [], 0.0, 0.0
        for k, a in agg.items():
            sc, sb = a["cur"][0] / tc, a["cmp"][0] / tb
            r_c = a["cur"][1] / a["cur"][0] if a["cur"][0] else None
            r_b = a["cmp"][1] / a["cmp"][0] if a["cmp"][0] else None
            rbm = r_b if r_b is not None else rb
            rcm = r_c if r_c is not None else rbm
            m = (sc - sb) * (rbm - rb)
            rt = sc * (rcm - rbm)
            mix += m
            rate += rt
            out.append({"key": k, "share_cmp": round(sb, 4), "share_cur": round(sc, 4), "roas_cmp": None if r_b is None else round(r_b, 3), "roas_cur": None if r_c is None else round(r_c, 3), "mix_pts": round(m, 4), "rate_pts": round(rt, 4)})
        total = rc - rb
        out.sort(key=lambda r: -(abs(r["mix_pts"]) + abs(r["rate_pts"])))
        top = max(out, key=lambda r: abs(r["share_cur"] - r["share_cmp"]))
        return {"mix_effect_pts": mix, "rate_effect_pts": rate, "total_pts": total, "mix_share": (mix / total) if total else 0.0, "rows": out, "max_share_shift_pts": abs(top["share_cur"] - top["share_cmp"]) * 100,
                "top": f"{top['key']} {top['share_cmp']:.0%} -> {top['share_cur']:.0%}"}

    def h_auction(self, cur, cmp_, real):
        cm, bm = X.METRICS["cpm"][0], X.METRICS["cpm"][0]
        res = []
        for name in ("cpm", "ctr", "cpc"):
            f = X.METRICS[name][0]
            a, b = f(cur), f(cmp_)
            res.append((name, a, b, _pct(a, b) if a is not None and b else None))
        gates = {n: X.noise_gate(self.daily, self.end, n) for n in ("cpm", "ctr", "cpc")}
        moved = [n for n in ("cpm", "ctr", "cpc") if any(r.get("verdict") == "real" for r in gates[n].values())]
        txt = "; ".join(f"{n} {a:.4g} vs {b:.4g} ({p:+.1f}%)" for n, a, b, p in res if a is not None and b and p is not None)
        if not txt:
            self.add("H4", "Auction: CPM, CTR, CPC", "untestable", "no impressions or clicks", missing="Meta delivery")
        elif moved:
            self.add("H4", "Auction: CPM, CTR, CPC", "consistent with" if real else "inside noise", f"{txt}; outside same-weekday noise: {', '.join(moved)} (Meta-reported)")
        else:
            self.add("H4", "Auction: CPM, CTR, CPC", "ruled out" if real else "inside noise", f"{txt}; none outside the same-weekday noise band at any grain (Meta-reported)")

    def h_creative(self, real):
        rows = self.q(f"select p.p, (f.first > p.e - 14) is_new, sum(d.spend) spend, sum(d.upstack_new_customers) nc, sum(d.upstack_new_customer_revenue) ncr from analytics.meta_ad_daily d "
                      f"join (select ad_id, min(first_day) over (partition by coalesce(family_key, ad_id)) first from analytics.meta_ads) f on f.ad_id = d.ad_id join {self.per()} on d.day between p.s and p.e "
                      f"{'where true' + self.w.prod_clause if self.scoped else ''} group by 1, 2") if self.w.has("meta_ads") else None
        if not rows:
            self.add("H5", "Creative: new vs old ad families, fatigue", "untestable", "ad family first-spend days unavailable", missing="meta_ads.first_day")
            return
        agg = {(r["p"], bool(r["is_new"])): r for r in rows}
        def share(p):
            t = sum(_f(r["spend"]) for (pp, _), r in agg.items() if pp == p)
            n = _f(agg.get((p, True), {}).get("spend"))
            return (n / t if t else None)
        def roas(p, new):
            r = agg.get((p, new))
            return _f(r["ncr"]) / _f(r["spend"]) if r and _f(r["spend"]) else None
        sc, sb = share("cur"), share("cmp")
        if sc is None or sb is None:
            self.add("H5", "Creative: new vs old ad families", "untestable", "no spend in a window", missing="spend")
            return
        gap = (roas("cur", False) or 0) - (roas("cur", True) or 0)
        eff = (sc - sb) * ((roas("cmp", True) or 0) - (roas("cmp", False) or 0))
        tot = None if self.vc is None or self.vb is None or self.metric != "nc_roas" else self.vc - self.vb
        v = X.factor_verdict(real, None if not tot else eff / tot, abs(sc - sb) >= 0.03)
        self.add("H5", "Creative: new ad families (first spend within 14 days) vs old", v,
                 f"new families' share of spend {sb:.0%} -> {sc:.0%}; Upstack ad-attributed NC ROAS new {roas('cur', True) and round(roas('cur', True), 3)} vs old {roas('cur', False) and round(roas('cur', False), 3)} in the window; "
                 f"share-shift effect {eff:+.3f} ROAS points{'' if tot is None else f' of {tot:+.3f}'}; old-family fatigue is not separable from the old/new split here")

    def h_funnel(self, real):
        rows = getattr(self, "seg", None)
        if not rows:
            self.add("H6", "Post-click funnel by product", "untestable", "no ad rows", missing="meta_ad_daily funnel columns")
            return
        steps = (("link click to landing page view", "clicks", "lpv"), ("landing page view to add to cart", "lpv", "atc"), ("add to cart to initiated checkout", "atc", "ic"), ("initiated checkout to new customer (Upstack)", "ic", "nc"))
        by = {}
        for r in rows:
            for key in (r["prod"] or "(none)", "(all)"):
                a = by.setdefault(key, {"cur": {}, "cmp": {}})[r["p"]]
                for k in ("clicks", "lpv", "atc", "ic", "nc"):
                    a[k] = a.get(k, 0.0) + _f(r[k])
        moved, flat = [], []
        for prod, pd in sorted(by.items(), key=lambda kv: -kv[1]["cur"].get("clicks", 0))[:4]:
            for label, a, b in steps:
                n1, k1, n0, k0 = pd["cur"].get(a, 0), pd["cur"].get(b, 0), pd["cmp"].get(a, 0), pd["cmp"].get(b, 0)
                if n1 < 200 or n0 < 200 or not k0 or not k1:
                    continue
                p1, p0 = k1 / n1, k0 / n0
                pp = (k1 + k0) / (n1 + n0)
                se = (pp * (1 - pp) * (1 / n1 + 1 / n0)) ** 0.5 if 0 < pp < 1 else 0
                z = (p1 - p0) / se if se else 0.0
                rec = f"{prod}: {label} {p0:.2%} -> {p1:.2%} ({_pct(p1, p0):+.1f}%, z {z:+.1f})"
                (moved if abs(z) >= 3 and abs(p1 / p0 - 1) >= 0.05 else flat).append(rec)
        if not moved and not flat:
            self.add("H6", "Post-click funnel by product", "untestable", "too few events for a step rate", missing="funnel counts")
        elif moved:
            self.add("H6", "Post-click funnel by product", "consistent with" if real else "inside noise", "steps beyond binomial noise (|z| >= 3 and >= 5% relative): " + "; ".join(moved[:5]), detail_flat=flat[:3])
        else:
            self.add("H6", "Post-click funnel by product", "ruled out" if real else "inside noise", f"no step rate moved beyond binomial noise (|z| < 3 or < 5% relative); e.g. {'; '.join(flat[:2])}")

    def h_order_side(self, real):
        w = self.w
        if not w.has("shopify_orders", "shopify_order_lines"):
            self.add("H7", "Order side: AOV by product mix, pack size, price point, discount", "untestable", "order lines not available", missing="shopify_order_lines")
            return
        rows = self.q(f"select p.p, l.product, count(distinct o.order_id) orders, sum(l.quantity) qty, sum(l.price * l.quantity) gross, sum(l.line_discount) disc from analytics.shopify_orders o "
                      f"join analytics.shopify_order_lines l on l.order_id = o.order_id join {self.per()} on o.order_date between p.s and p.e "
                      f"where o.is_new_customer and not o.is_rebill and not o.is_cancelled and not o.is_test and not o.is_trybe_gift and l.quantity > 0 group by grouping sets ((p.p, l.product), (p.p))")
        if not rows:
            self.add("H7", "Order side: AOV decomposition", "untestable", "no new-customer order lines in a window (order lines cover the last 120 days)", missing="shopify_order_lines for the window")
            return
        sides = {}
        for r in rows:
            s = sides.setdefault(r["p"], {"orders": 0, "products": {}})
            if r["product"] is None:
                s["orders"] = int(r["orders"])
            else:
                s["products"][r["product"]] = {"qty": _f(r["qty"]), "gross": _f(r["gross"]), "disc": _f(r["disc"])}
        if "cur" not in sides or "cmp" not in sides or not sides["cur"]["orders"] or not sides["cmp"]["orders"] or not sides["cur"]["products"] or not sides["cmp"]["products"]:
            self.add("H7", "Order side: AOV decomposition", "untestable", "order lines missing for one window", missing="shopify_order_lines for the window")
            return
        dec = X.decompose_aov(sides["cur"], sides["cmp"])
        self.aov = dec
        tot = dec["change"]
        eff = dec["effects"]
        dom = max(eff, key=lambda k: abs(eff[k]))
        share = abs(eff[dom]) / abs(tot) if tot else None
        rel = abs(tot) / dec["aov_cmp"] if dec["aov_cmp"] else 0
        orders = max(sides["cur"]["orders"], 1)
        aov_band = 2 * 0.5 / (orders ** 0.5)          # AOV per-order coefficient of variation ~0.5, 2 sigma
        beyond = rel > aov_band
        v = X.factor_verdict(real, share, beyond)
        if self.metric not in ("nc_roas", "nc_aov", "nc_revenue", "nc_cpa"):
            v = "ruled out" if real else "inside noise"
        self.add("H7", "Order side: AOV (new-customer product net per order)", v,
                 f"{dec['aov_cmp']:.2f} -> {dec['aov_cur']:.2f} ({tot:+.2f}, {rel:.1%}; 2-sigma band about {aov_band:.1%}): pack size {eff['pack_size']:+.2f}, product mix {eff['product_mix']:+.2f}, price point {eff['price_point']:+.2f}, "
                 f"discount {eff['discount']:+.2f}; effects add up to the change (residual {dec['residual']:.1e}); dominant: {dom}", detail=dec)

    def h_contamination(self, cur, cmp_):
        a, b = None, None
        rows = self.q(f"select p.p, sum(d.meta_purchases) mp, sum(d.upstack_new_customers) nc from analytics.meta_ad_daily d join {self.per()} on d.day between p.s and p.e{'' if not self.scoped else self.w.prod_clause.replace(' and ', ' where ', 1)} group by 1")
        w = self.w
        if rows and len(rows) == 2:
            r = {x["p"]: x for x in rows}
            a = _f(r["cur"]["mp"]) / _f(r["cur"]["nc"]) if _f(r["cur"]["nc"]) else None
            b = _f(r["cmp"]["mp"]) / _f(r["cmp"]["nc"]) if _f(r["cmp"]["nc"]) else None
        if a is None or b is None:
            self.add("H8", "New vs returning contamination (Meta purchases per Upstack new customer)", "untestable", "no ratio", missing="meta_purchases and upstack_new_customers")
        else:
            moved = abs(a / b - 1) > 0.10
            self.add("H8", "New vs returning contamination (Meta purchases per Upstack new customer)", "consistent with" if moved and self.gate_real else ("inside noise" if not self.gate_real else "ruled out"),
                     f"Meta-reported purchases per Upstack new customer {b:.2f} -> {a:.2f} ({(a / b - 1) * 100:+.1f}%); a rise means Meta credits more returning buyers; rebills are out of the headline (orders_daily new customers are first orders)")
        if w.has("shopify_orders") and not self.scoped:
            rows = self.q(f"select p.p, case when o.ship_country = 'US' then 'US' else 'non-US' end c, count(*) orders, sum(o.subtotal_price) rev from analytics.shopify_orders o join {self.per()} on o.order_date between p.s and p.e "
                          f"where o.is_new_customer and not o.is_rebill and not o.is_cancelled and not o.is_test and not o.is_trybe_gift group by 1, 2")
            if rows:
                d = {(r["p"], r["c"]): r for r in rows}
                def shr(p):
                    t = sum(_f(r["orders"]) for (pp, _), r in d.items() if pp == p)
                    n = _f(d.get((p, "non-US"), {}).get("orders"))
                    return n / t if t else None
                sc, sb = shr("cur"), shr("cmp")
                def aov(p, c):
                    r = d.get((p, c))
                    return _f(r["rev"]) / _f(r["orders"]) if r and _f(r["orders"]) else None
                if sc is None or sb is None:
                    self.add("H9", "Country mix", "untestable", "no orders in a window", missing="orders with ship country")
                else:
                    gap = (aov("cmp", "non-US") or 0) - (aov("cmp", "US") or 0)
                    eff = (sc - sb) * gap
                    v = X.factor_verdict(self.gate_real, None if not self.vc else abs(eff) / max(abs(aov("cmp", "US") or 1), 1), abs(sc - sb) >= 0.03)
                    self.add("H9", "Country mix (new-customer orders, US vs non-US)", v if v != "consistent with" or abs(sc - sb) >= 0.03 else "ruled out",
                             f"non-US share of new-customer orders {sb:.1%} -> {sc:.1%}; subtotal per order US {aov('cur', 'US') and round(aov('cur', 'US'), 2)} vs non-US {aov('cur', 'non-US') and round(aov('cur', 'non-US'), 2)}; effect on AOV {eff:+.2f}")
            else:
                self.add("H9", "Country mix", "untestable", "no order rows", missing="shopify_orders")
        else:
            self.add("H9", "Country mix", "untestable" if not w.has("shopify_orders") else "ruled out", "product-scoped: order rows carry no ad product" if w.has("shopify_orders") else "orders not available", missing=None if w.has("shopify_orders") else "shopify_orders")

    def h_breakdowns(self, real):
        b = self.bdc or {}
        if not b or b.get("applies") is False or b.get("error"):
            self.add("H10", "Meta breakdown segments (placement, device, age, gender, country)", "untestable", (b.get("reason") or b.get("error") or "breakdown step did not run"), missing="meta breakdown step")
            return
        movers = b.get("movers") or []
        n = b.get("beyond_noise_count") or 0
        head = b.get("headline") or ""
        if not real:
            self.add("H10", "Meta breakdown segments", "inside noise", "overall move is inside noise; segment shifts not read")
        elif n:
            self.add("H10", "Meta breakdown segments", "consistent with", f"{n} segment mover(s) beat their noise band (Meta-reported): " + "; ".join(f"{m.get('dimension')}={m.get('segment')}" for m in movers[:4]))
        else:
            self.add("H10", "Meta breakdown segments", "ruled out", "no segment beat its noise band: " + (head[:140] or "nothing beat noise"))

    def h_email(self, real):
        if not self.w.has("klaviyo_daily"):
            self.add("H11", "Email and SMS sends", "untestable", "klaviyo_daily not available", missing="klaviyo_daily")
            return
        rows = self.q(f"select p.p, sum(k.sends) sends, sum(k.revenue) rev from analytics.klaviyo_daily k join {self.per()} on k.day between p.s and p.e group by 1")
        if not rows or len(rows) < 2:
            self.add("H11", "Email and SMS sends", "untestable", "no Klaviyo rows in a window", missing="klaviyo_daily")
            return
        r = {x["p"]: x for x in rows}
        ds = _pct(_f(r["cur"]["sends"]), _f(r["cmp"]["sends"]))
        if self.metric in NC_METRICS or self.metric in ("spend", "meta_spend", "cpm", "cpc", "ctr"):
            self.add("H11", "Email and SMS sends", "ruled out", f"email / SMS attributes returning customers; this metric is first orders and paid delivery (sends {ds:+.1f}% vs the comparison)" if ds is not None else "email / SMS does not drive a first-order metric")
        else:
            self.add("H11", "Email and SMS sends", "consistent with" if ds is not None and abs(ds) > 15 else "ruled out", f"sends {ds if ds is None else round(ds, 1)}% vs comparison; revenue {_f(r['cur']['rev']):,.0f} vs {_f(r['cmp']['rev']):,.0f}")

    def h_inventory_feeds(self):
        if not self.w.has("change_timeline"):
            self.add("H12", "Inventory (stockouts)", "untestable", "change_timeline not available", missing="analytics.change_timeline")
            self.add("H13", "Tracking / feed breaks", "untestable", "change_timeline not available", missing="analytics.change_timeline")
            return
        evs = getattr(self, "events", [])
        lo = self.start - dt.timedelta(days=3)
        so = [e for e in evs if e["kind"] in ("stockout", "stock_near_zero")]
        self.add("H12", "Inventory (stockouts / near zero in the window and the 3 days before)", ("consistent with" if self.gate_real else "inside noise") if so else "ruled out",
                 ("; ".join(f"{e['object_names']} {e['kind']} on {e['event_day']}" for e in so[:4])) if so else "no stockout or near-zero stock event; product on-hand did not hit zero")
        fe = [e for e in evs if e["kind"] == "feed_run_failed" and str(e.get("object_names")) not in ("meta_breakdowns", "lifetime_pull")]    # breakdown / lifetime pulls do not feed the headline
        gaps = [d for d in (self.start + dt.timedelta(days=i) for i in range((self.end - self.start).days + 1)) if d not in self.daily or self.daily[d]["spend"] == 0]
        if fe or gaps:
            self.add("H13", "Tracking / feed breaks (failed ETL runs, days with no data)", "consistent with" if self.gate_real else "inside noise", f"{len(fe)} failed run(s): {'; '.join(e['object_names'] for e in fe[:3])}; days with no spend rows: {[str(g) for g in gaps[:3]]}")
        else:
            self.add("H13", "Tracking / feed breaks (failed ETL runs, days with no data)", "ruled out", "no failed ETL run and no empty day in the window")

    # ---------------------------------------------------------------- 3. change events and controls
    def load_events(self):
        if not self.w.has("change_timeline"):
            self.events = []
            return
        rows = self.q(f"select event_time, event_day, source, kind, scope, object_ids, object_names, product, old_value, new_value, magnitude, editor, objects_affected, prior_day_spend_share "
                      f"from analytics.change_timeline where event_day between date '{self.start - dt.timedelta(days=3)}' and date '{self.end}' order by event_time", 600)
        out = []
        for r in rows or []:
            r = dict(r)
            r["event_day"] = _day(r["event_day"])
            r["object_ids"] = [str(x) for x in (r.get("object_ids") or [])] if not isinstance(r.get("object_ids"), str) else [r["object_ids"]]
            out.append(r)
        self.events = out

    def material(self):
        evs = [e for e in self.events if e["source"] == "meta_activity_log" and e["scope"] in ("adset", "campaign") and e["object_ids"]]
        evs = [e for e in evs if (e.get("prior_day_spend_share") or 0) >= 0.03 or (e["objects_affected"] or 0) >= 5 or e["kind"].endswith(("budget", "bid_amount", "bid_strategy", "run_status"))]
        evs.sort(key=lambda e: -((e.get("prior_day_spend_share") or 0) * 100 + min(e["objects_affected"] or 0, 100) / 10))
        return evs[:MAX_EVENTS]

    def run_controls(self):
        self.controlled = []
        for e in self.material():
            ids = "array[" + ",".join(_lit(i) for i in e["object_ids"][:400]) + "]::text[]"
            lo = e["event_day"] - dt.timedelta(days=3)
            rows = self.q(f"select d.day, (d.adset_id = any({ids}) or d.campaign_id = any({ids})) aff, sum(d.spend) spend, sum(d.impressions) imps, sum(d.link_clicks) clicks, "
                          f"sum(d.upstack_new_customers) nc, sum(d.upstack_new_customer_revenue) nc_rev from analytics.meta_ad_daily d where d.day between date '{lo}' and date '{self.end}'"
                          f"{self.w.prod_clause} group by 1, 2")
            ctl = None
            if rows:
                ctl = X.control([{**r, "day": _day(r["day"])} for r in rows], e["event_day"], self.end)
            ver, word = ("untestable", "no ad rows for the control") if not ctl else X.control_verdict(ctl, e.get("prior_day_spend_share"))
            et = _ts(e["event_time"])
            split = X.event_hour_split(self.hourly_rows, et.date(), et.hour) if self.hourly_rows and e["event_day"] <= self.end and et.date() == self.end else None
            if split is None and self.w.has("orders_hourly") and not self.scoped and e["event_day"] >= self.end - dt.timedelta(days=5):
                rows = self.q(f"select day, hour, new_customer_revenue nc_rev from analytics.orders_hourly where day between date '{et.date() - dt.timedelta(days=30)}' and date '{et.date()}'")
                if rows:
                    split = X.event_hour_split([{**r, "day": _day(r["day"])} for r in rows], et.date(), et.hour)
            ent = {"event": f"{e['kind']} by {e.get('editor') or '?'} on {e['objects_affected']} {e['scope']}(s): {e.get('old_value')} -> {e.get('new_value')} at {et:%Y-%m-%d %H:%M} PT, {((e.get('prior_day_spend_share') or 0) * 100):.0f}% of prior-day Meta spend",
                   "verdict": ver, "control": word, "event_time": f"{et:%Y-%m-%d %H:%M} PT", "_ctl": ctl, "_ev": e, "hour_split": split}
            self.controlled.append(ent)
            summ = ""
            if ctl:
                g = ctl["groups"]
                summ = (f" | edited: CPM {g['affected']['change_pct']['cpm']}%, CPC {g['affected']['change_pct']['cpc']}%, conv {g['affected']['change_pct']['conv']}%, NC ROAS {g['affected']['change_pct']['nc_roas']}%, CPA {g['affected']['change_pct']['cpa']}%, spend {g['affected']['change_pct']['spend']}%"
                        f" | unedited: CPM {g['unaffected']['change_pct']['cpm']}%, CPC {g['unaffected']['change_pct']['cpc']}%, conv {g['unaffected']['change_pct']['conv']}%, NC ROAS {g['unaffected']['change_pct']['nc_roas']}%, CPA {g['unaffected']['change_pct']['cpa']}%, spend {g['unaffected']['change_pct']['spend']}%"
                        f" | before {ctl['pre_window'][0]}..{ctl['pre_window'][1]}, after {ctl['post_window'][0]}..{ctl['post_window'][1]}")
            self.add("E" + str(len(self.controlled)), "Change event: " + ent["event"], ver, word + summ, control=ctl, hour_split=split)
        coinc = [e for e in self.events if e["source"] != "meta_activity_log" and e["kind"] not in ("stockout", "stock_near_zero", "feed_run_failed")]
        seen = {}
        for e in coinc:
            seen.setdefault((e["source"], e["kind"]), []).append(e)
        for (src, kind), es in list(seen.items())[:8]:
            names = "; ".join(str(x.get("object_names"))[:60] for x in es[:2])
            ba = self.before_after(es[0]["event_day"])
            self.add("C" + str(len(self.ledger) + 1), f"Coincident {kind} ({src}, {len(es)} on {es[0]['event_day']}..{es[-1]['event_day']}): {names}", ba[0],
                     ba[1] + " No unaffected group exists for a site-wide or account-wide change, so this is before / after only: at best consistent with, never supported by control.", missing=None)

    def before_after(self, day):
        if day > self.end or not self.daily:
            return "untestable", "event after the window"
        n = (self.end - day).days + 1
        pre = X.window_sum(self.daily, day - dt.timedelta(days=n), day - dt.timedelta(days=1))
        post = X.window_sum(self.daily, day, self.end)
        f = X.METRICS[self.metric][0]
        a, b = f(post), f(pre)
        if a is None or not b or pre["days"] < n or post["days"] < 1:
            return "untestable", "not enough days before / after"
        ck = X.METRICS[self.metric][1]
        band = 2 * (1 / max(post[ck], 1) + 1 / max(pre[ck], 1)) ** 0.5 * 100 if ck else 10.0
        ch = _pct(a, b)
        return ("consistent with" if abs(ch) > band else "inside noise"), f"{self.metric} {b:.4g} ({n} days before) -> {a:.4g} (from the event day), {ch:+.1f}% vs a +/-{band:.0f}% count band."

    # ---------------------------------------------------------------- 4. recursion
    def recurse(self):
        chain = []
        gs = self.grain_sentence()
        chain.append(f"L0 the move: {self.metric} {None if self.vc is None else round(self.vc, 4)} vs {None if self.vb is None else round(self.vb, 4)} ({'n/a' if self.asked_change is None else f'{self.asked_change:+.1f}%'}) over the asked windows; {gs}.")
        cur, cmp_ = self.window("cur"), self.window("cmp")
        if cur["days"] and cmp_["days"]:
            sp = _pct(cur["spend"] / cur["days"], cmp_["spend"] / cmp_["days"])
            nr = _pct(cur["nc_rev"] / cur["days"], cmp_["nc_rev"] / cmp_["days"])
            chain.append(f"L1 factors per day: spend {sp:+.1f}%, new-customer revenue {nr:+.1f}% ({self.basis}).")
        stop = None
        surviving = [e for e in self.ledger if e["verdict"] in ("supported by control", "consistent with") and not e["id"].startswith(("E", "C"))]
        ev_ok = [c for c in self.controlled if c["verdict"] in ("supported by control", "consistent with")]
        ev_out = [c for c in self.controlled if c["verdict"] == "ruled out"]
        self.surviving_ids = [e["id"] for e in surviving]
        if surviving:
            chain.append("L2 hypotheses still standing: " + "; ".join(f"{e['id']} {e['hypothesis'].split(' (')[0]} ({e['verdict']})" for e in surviving[:5]) + ".")
        lead = None
        ps = getattr(self, "prod_shift", None)
        if ps and ps["rows"] and not self.scoped and any(e["id"] == "H3p" for e in surviving):
            lead = ps["rows"][0]["key"]
            chain.append(f"L3 which product carries the mix / rate move: {lead} (share {ps['rows'][0]['share_cmp']:.0%} -> {ps['rows'][0]['share_cur']:.0%}, ROAS {ps['rows'][0]['roas_cmp']} -> {ps['rows'][0]['roas_cur']}).")
        adsets = self.adset_drill(lead)
        if adsets:
            touch = []
            ids = {a["adset_id"]: a for a in adsets}
            for c in self.controlled:
                hit = [i for i in c["_ev"]["object_ids"] if i in ids]
                if hit:
                    touch.append((c, len(hit)))
            top = ", ".join(f"{a['adset_name'][:30]} (spend {a['d_spend']:+,.0f}/day)" for a in adsets[:3])
            chain.append(f"L3 ad sets with the largest daily-spend change{' in ' + lead if lead else ''}: {top}." + (f" {sum(n for _, n in touch)} of the top {len(adsets)} were touched by a change event." if touch else " None of them was touched by a logged change event."))
        for c in self.controlled[:3]:
            chain.append(f"L4 change someone made: {c['event']}. Control: {c['control']} [{c['verdict']}].")
            stop = "a change someone made (leaf)"
        if not self.controlled and not surviving and self.gate_real:
            stop = "no tested hypothesis survives"
        if not self.gate_real:
            stop = "quantified noise (leaf)"
        self.chain = chain
        self.depth_stop = stop or f"depth cap {MAX_DEPTH}"
        self.ev_ok, self.ev_out = ev_ok, ev_out

    def adset_drill(self, lead):
        if self.scoped and not lead:
            lead = self.w.product
        pc = f" and d.product_slug = {_lit(lead)}" if lead and not self.scoped else self.w.prod_clause
        rows = self.q(f"select d.adset_id, max(d.adset_name) adset_name, p.p, sum(d.spend) spend, sum(d.upstack_new_customer_revenue) ncr from analytics.meta_ad_daily d join {self.per()} on d.day between p.s and p.e "
                      f"where d.adset_id is not null{pc} group by 1, 3", 1000)
        if not rows:
            return []
        a = {}
        for r in rows:
            x = a.setdefault(r["adset_id"], {"adset_id": r["adset_id"], "adset_name": r["adset_name"] or "", "cur": 0.0, "cmp": 0.0})
            x[r["p"]] += _f(r["spend"])
        nc, nb = max((self.end - self.start).days + 1, 1), max((self.ce - self.cs).days + 1, 1)
        out = [{**x, "d_spend": x["cur"] / nc - x["cmp"] / nb} for x in a.values()]
        out.sort(key=lambda x: -abs(x["d_spend"]))
        return out[:6]

    # ---------------------------------------------------------------- 5. answer contract
    def compose(self, priors):
        ok = [c for c in self.controlled if c["verdict"] == "supported by control"]
        cons = [c for c in self.controlled if c["verdict"] == "consistent with"]
        out_ev = [c for c in self.controlled if c["verdict"] == "ruled out"]
        cur_s = f"{self.metric} {None if self.vc is None else round(self.vc, 3)} vs {None if self.vb is None else round(self.vb, 3)}"
        gs = self.grain_sentence()
        action = None
        if not self.gate_real:
            answer = f"Inside noise at every grain tested ({cur_s}; {gs}). No cause is claimed."
            if self.controlled:
                answer += " Logged changes in the window were still controlled: " + "; ".join(f"{c['event'].split(' at ')[0]}: {c['verdict']}" for c in self.controlled[:3]) + "."
            nxt = (self.next_needed or {}).get("reading")
            next_check = nxt or "wait one more day; re-run with the same windows"
        elif ok or cons:
            lead = (ok or cons)[0]
            word = "Supported by a control" if ok else "Consistent with"
            ev = lead["_ev"]
            answer = (f"{word}: {lead['event']}. Control: {lead['control']}. Real at the {self.finest_real}-day grain ({cur_s}; {gs}).")
            if ok:
                action = f"the edit was applied to {ev['objects_affected']} {ev['scope']}(s); if the edited group is the one that fell, reverse the edit on those objects only and watch the same control for 2 days"
            next_check = "another day of the same edited vs unedited comparison; confirm the difference does not shrink (a pre-trend that differs would void it)"
        else:
            lead = next((e for e in self.ledger if e["id"] in getattr(self, "surviving_ids", [])), None)
            if lead:
                answer = f"A real move at the {self.finest_real}-day grain ({cur_s}); the factor still standing is '{lead['hypothesis']}' ({lead['verdict']}): {lead['evidence'][:200]}. No logged change event passed a control."
                if out_ev:
                    answer += f" {len(out_ev)} logged change event(s) were controlled and ruled out (the unedited group moved the same way)."
            else:
                answer = f"A real move at the {self.finest_real}-day grain ({cur_s}) with no tested hypothesis standing; {len(out_ev)} change event(s) ruled out by their control. Unexplained."
            next_check = "the chain above names the leaf; the single test that would settle it is a like-for-like split of the lead factor by ad set for the next day"
        ruled = [f"{e['id']} {e['hypothesis'].split(' (')[0]}: {e['evidence'][:140]}" for e in self.ledger if e["verdict"] == "ruled out"]
        out = {"answer": answer, "confidence": ("supported by control" if ok else "consistent with" if (cons or (self.gate_real and getattr(self, 'surviving_ids', []))) else "inside noise" if not self.gate_real else "unexplained"),
               "chain": self.chain, "ruled_out": ruled, "untestable": self.untestable, "next_check": next_check, "depth_stopped_at": self.depth_stop,
               "noise_gate": {"basis": self.basis, "grains": self.gate_rows, "next_day_needed": self.next_needed, "hour_of_day": self.hourly, "note": self.metric_note},
               "ledger": [self._slim(e) for e in self.ledger], "priors_seen_before": priors}
        if action:
            out["action"] = action
        return out

    @staticmethod
    def _slim(e):
        o = {k: e[k] for k in ("id", "hypothesis", "verdict", "evidence") if k in e}
        if e.get("missing"):
            o["missing"] = e["missing"]
        if e.get("hour_split"):
            o["hour_split"] = e["hour_split"]
        if e.get("control"):
            o["control_groups"] = {n: {"before": {k: (None if v is None else round(v, 4)) for k, v in g["before"].items()}, "after": {k: (None if v is None else round(v, 4)) for k, v in g["after"].items()}, "change_pct": g["change_pct"]}
                                   for n, g in e["control"]["groups"].items()}
            o["pre_trend_pct"] = e["control"]["pre_trend_pct"]
        return o

    def priors(self):
        if not self.w.has("why_findings"):
            return []
        rows = self.q(f"select hypothesis, verdict, evidence, window_end, created_at from analytics.why_findings where metric = {_lit(self.w.metric)} and verdict in ('supported by control', 'consistent with', 'ruled out') "
                      f"and not (window_start = date '{self.start}' and window_end = date '{self.end}') order by created_at desc limit 6")
        return [{"seen_before": f"{r['hypothesis'][:90]}: {r['verdict']} (window ending {r['window_end']})", "note": "a lead, not proof"} for r in (rows or [])]


def persist(w, ledger: list[dict]) -> str:
    url = os.environ.get("WHY_FINDINGS_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return "not written: no write connection configured (WHY_FINDINGS_URL / DATABASE_URL)"
    try:
        import psycopg
        rows = [(w.metric, w.product, w.platform, w.start, w.end, w.cstart, w.cend, e["hypothesis"][:300], e["verdict"], str(e["evidence"])[:600]) for e in ledger]
        with psycopg.connect(url, connect_timeout=8) as c:
            with c.cursor() as cur:
                cur.executemany("insert into why_findings (metric, product, platform, window_start, window_end, compare_start, compare_end, hypothesis, verdict, evidence) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
            c.commit()
        return f"written: {len(rows)} findings"
    except Exception as e:                                                      # noqa: BLE001  memory must never take the answer down
        return f"not written: {type(e).__name__}"


def run_step(w, bdc=None) -> dict:
    inv = Inv(w, bdc)
    try:
        res = _run(w, inv)
        res["steps_used"] = w.steps - inv.main_steps
        if inv.skipped_queries:
            res["skipped_queries"] = inv.skipped_queries[:4]
        return res
    finally:
        w.queries_inv = w.steps - inv.main_steps
        w.steps, w.max_steps, w.time_budget = inv.main_steps, inv.main_max, inv.main_time


def _run(w, inv) -> dict:
    if w.platform == "google":
        return {"applies": False, "reason": "the investigation reads Meta ad views and orders; this question is scoped to Google"}
    if not w.has("meta_ad_daily"):
        return {"applies": False, "reason": "analytics.meta_ad_daily is not in the database"}
    if not inv.load_daily():
        return {"applies": False, "reason": "no ad rows in the lookback window"}
    inv.gate()
    inv.load_events()
    inv.run_ledger()
    inv.run_controls()
    inv.recurse()
    res = inv.compose(inv.priors())
    res["applies"] = True
    res["memory"] = persist(w, [e for e in inv.ledger])
    return res
