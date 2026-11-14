"""Six more hypotheses for the `why` investigation (mixed into Inv): H14 persona / tag mix, H4b cap and floor exposure, H16 intraday shape, H17 order mix, H1b attribution leak, H18 calendar.

Each adds exactly one ledger entry with one verdict. Rates not counts, ratios from sums, one basis per ratio (stated in the evidence). Statistics are in why_stats_inv.py (pure, unit tested).
Evidence strings stay short (material movers only); the full tables ride in `detail`, which the compact view leaves out.
"""
from __future__ import annotations

import datetime as dt

from . import why_stats_inv as X

TAG_DIMS = (("icp", "persona (ICP)"), ("pain_point", "pain point"), ("style", "style"), ("ad_type", "type"))
NC_ONLY = ("nc_roas", "nc_cpa", "nc_revenue", "nc_aov", "conversion")


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _day(v) -> dt.date:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


def _lit(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


class MoreHypotheses:
    # ---------------------------------------------------------------- H14 persona / tag mix
    def h_persona(self, real):
        hid, name = "H14", "Persona / tag mix (persona, pain point, style, type; Upstack new customers on the ad views)"
        if not self.w.has("creative_family_daily", "creative_tracker"):
            self.add(hid, name, "untestable", "tag views not available", missing="creative_family_daily / creative_tracker")
            return
        pc = self.w.prod_clause.replace("d.", "f.") if self.scoped else ""
        rows = self.q("select p.p, coalesce(f.product_slug, t.product_slug) prod, t.icp, t.pain_point, t.style, t.ad_type, sum(f.spend) spend, sum(f.purchases) nc, sum(f.revenue) ncr, sum(f.link_clicks) clicks "
                      f"from analytics.creative_family_daily f join {self.per()} on f.day between p.s and p.e "
                      "left join (select family_key, product_slug, icp, pain_point, style, ad_type from analytics.creative_tracker where is_first_row) t on t.family_key = f.family_key "
                      f"where f.basis = 'upstack_new_customers' and f.ads_scope = 'all_ads'{pc} group by 1, 2, 3, 4, 5, 6", 3000)
        if not rows:
            self.add(hid, name, "untestable", "no family-level rows in a window", missing="creative_family_daily (basis upstack_new_customers)")
            return
        self.untestable.append("H14 hook type: the tracker stores hook text only, there is no hook-type column")
        pm = {"nc_roas": "nc_roas", "nc_revenue": "nc_roas", "nc_cpa": "cost_per_nc", "conversion": "conversion"}.get(self.metric, "nc_roas")
        last_loaded = max(self.ads) if getattr(self, "ads", None) else self.end
        maturing = (last_loaded - self.end).days < 7
        out, all_dims = [], {}
        for col, label in TAG_DIMS:
            seg = [{"p": r["p"], "key": f"{r['prod'] or '?'}: {r[col] or '(untagged)'}", "spend": r["spend"], "nc": r["nc"], "ncr": r["ncr"], "clicks": r["clicks"]} for r in rows]
            res = X.shift_share(seg, pm)
            if res is None:
                continue
            if maturing:
                for m in res["rows"]:
                    m["rate_material"] = False
            res["movers"] = [m for m in res["rows"] if m["mix_material"] or m["rate_material"]]
            all_dims[label] = res
        if not all_dims:
            self.add(hid, name, "untestable", "no spend in a window", missing="spend by tag")
            return
        first = next(iter(all_dims.values()))
        tot = first["total"]
        unt = {}
        for r in rows:
            if r["icp"] is None:
                unt[r["p"]] = unt.get(r["p"], 0.0) + _f(r["spend"])
        tcur = sum(_f(r["spend"]) for r in rows if r["p"] == "cur") or 1.0
        tcmp = sum(_f(r["spend"]) for r in rows if r["p"] == "cmp") or 1.0
        best = max(all_dims.items(), key=lambda kv: abs(kv[1]["mix_total"]) / max(abs(kv[1]["total"]), 1e-12))
        mix_share = abs(best[1]["mix_total"]) / abs(tot) if tot else None
        movers = []
        for label, res in all_dims.items():
            for m in res["movers"]:
                movers.append((abs(m["mix_part"]) + abs(m["rate_part"]), label, m))
        movers.sort(key=lambda x: -x[0])
        fmt = (lambda v: "n/a" if v is None else (f"{v:.2f}" if pm != "cost_per_nc" else f"{v:.0f}"))
        lines = []
        for _, label, m in movers[:4]:
            bit = f"{label} {m['key']}: spend/weight share {m['share_cmp']:.1%} -> {m['share_cur']:.1%}, mix {m['mix_part']:+.3f}"
            if m["rate_material"]:
                bit += f", own rate {fmt(m['rate_cmp'])} -> {fmt(m['rate_cur'])} ({m['rate_change_pct']:+.0f}% vs band +/-{m['rate_band_pct']:.0f}%), rate {m['rate_part']:+.3f}"
            lines.append(bit)
        beyond = bool(movers)
        v = X.factor_verdict(real, mix_share if beyond else 0.0, beyond)
        label_pm = {"nc_roas": "NC ROAS", "cost_per_nc": "cost per new customer", "conversion": "conversion (new customers per link click)"}[pm]
        head = (f"{label_pm} on the ad-view Upstack basis {first['value_cmp']:.3g} -> {first['value_cur']:.3g} ({tot:+.3f}); biggest mix share {best[0]} {mix_share:.0%} of it" if mix_share is not None else f"{label_pm} total {tot:+.3f}")
        caveat = ("tags are today's, applied to past days; " + ("window ends within 7 days of the last loaded day, so Upstack attribution is still maturing: only share shifts are read, rates are not" if maturing else "")).rstrip("; ")
        ev = f"{head}. " + ("Material movers: " + " | ".join(lines) if lines else "no tag value moved beyond its band") + f". Untagged spend {unt.get('cmp', 0) / tcmp:.0%} -> {unt.get('cur', 0) / tcur:.0%}. Caveat: {caveat}."
        drill = self._persona_changes(movers[:2])
        if drill:
            ev += " Changes touching the top mover's ad sets: " + drill
        self.add(hid, name, v, ev, detail={lab: {"total": r["total"], "mix_total": r["mix_total"], "rate_total": r["rate_total"], "residual": r["residual"], "rows": r["rows"][:6]} for lab, r in all_dims.items()})

    def _persona_changes(self, top):
        if not top or not getattr(self, "events", None) or not self.w.has("meta_ads"):
            return ""
        out = []
        by_id = {}
        for e in self.events:
            for i in e["object_ids"]:
                by_id.setdefault(i, []).append(e)
        for _, label, m in top[:1]:
            col = {l: c for c, l in TAG_DIMS}[label]
            prod, _, val = m["key"].partition(": ")
            if val == "(untagged)":
                continue
            pcl = f" and d.product_slug = {_lit(prod)}" if prod and prod != "?" else ""
            rows = self.q("select d.adset_id, max(d.adset_name) nm, p.p, sum(d.spend) spend from analytics.meta_ad_daily d join analytics.meta_ads a on a.ad_id = d.ad_id "
                          f"join (select family_key, {col} v from analytics.creative_tracker where is_first_row) t on t.family_key = a.tracker_family_key join {self.per()} on d.day between p.s and p.e "
                          f"where t.v = {_lit(val)} and d.adset_id is not null{pcl} group by 1, 3", 2000)
            if not rows:
                continue
            nc, nb = max((self.end - self.start).days + 1, 1), max((self.ce - self.cs).days + 1, 1)
            a = {}
            for r in rows:
                x = a.setdefault(r["adset_id"], {"nm": r["nm"] or "", "cur": 0.0, "cmp": 0.0})
                x[r["p"]] += _f(r["spend"])
            ranked = sorted(a.items(), key=lambda kv: -abs(kv[1]["cur"] / nc - kv[1]["cmp"] / nb))[:3]
            parts = []
            for i, x in ranked:
                evs = by_id.get(i, [])
                kinds = ", ".join(sorted({f"{e['kind']} {e['event_day']}" for e in evs})[:2]) or "no logged change"
                parts.append(f"{x['nm'][:34]} (spend {x['cur'] / nc - x['cmp'] / nb:+,.0f}/day; {kinds})")
            out.append(f"{val}: " + "; ".join(parts))
        return " ".join(out)

    # ---------------------------------------------------------------- H4b cap and floor exposure
    def h_caps(self, cur, cmp_, real):
        hid, name = "H4b", "Cap and floor exposure (cost cap, bid cap, ROAS floor; Meta-reported delivery)"
        if not self.w.has("meta_bids_daily"):
            self.add(hid, name, "untestable", "bid settings not available", missing="analytics.meta_bids_daily")
            return
        b = self.q(f"select p.p, sum(b.spend) spend, sum(b.capped_spend) capped_spend, sum(b.spend_times_cap) spend_times_cap, sum(b.floor_spend) floor_spend, sum(b.spend_times_floor) spend_times_floor, count(*) days "
                   f"from analytics.meta_bids_daily b join {self.per()} on b.day between p.s and p.e group by 1")
        d = {r["p"]: r for r in (b or [])}
        if len(d) < 2:
            self.add(hid, name, "untestable", "meta_bids_daily has no rows in a window (it starts 2026-04-28)", missing="meta_bids_daily for the comparison window")
            return
        m = self.q(f"select p.p, sum(d.spend) spend, sum(d.meta_purchases) mp, sum(d.meta_revenue) mr from analytics.meta_ad_daily d join {self.per()} on d.day between p.s and p.e"
                   f"{'' if not self.scoped else self.w.prod_clause.replace(' and ', ' where ', 1)} group by 1")
        mm = {r["p"]: r for r in (m or [])}
        for k in ("cur", "cmp"):
            r = mm.get(k)
            if r and _f(r["mp"]) > 0 and _f(r["spend"]) > 0:
                d[k] = {**d[k], "meta_cpp": _f(r["spend"]) / _f(r["mp"]), "meta_roas": _f(r["mr"]) / _f(r["spend"])}
        res = X.cap_exposure(d["cur"], d["cmp"])
        if not res:
            self.add(hid, name, "untestable", "no spend in a window", missing="meta_bids_daily spend")
            return
        a, c = res["cur"], res["cmp"]
        def f2(v, t="{:.2f}"):
            return "n/a" if v is None else t.format(v)
        state = self._adset_state_note()
        ev = (f"share of spend under a cost cap {c['capped_share']:.0%} -> {a['capped_share']:.0%}, average cap {f2(c['avg_cap'])} -> {f2(a['avg_cap'])} ({f2(res['cap_level_change_pct'], '{:+.0f}%')}); "
              f"Meta-reported cost per purchase {f2(c['cpp'])} -> {f2(a['cpp'])}, delivery vs cap {f2(c['cpp_over_cap'])} -> {f2(a['cpp_over_cap'])} ({a['cpp_over_cap'] and res['binding_cur']['cost_cap'] or 'n/a'}); "
              f"ROAS floor on {c['floor_share']:.0%} -> {a['floor_share']:.0%} of spend at average {f2(c['avg_floor'])} -> {f2(a['avg_floor'])}, Meta-reported return {f2(c['roas'])} -> {f2(a['roas'])} ({res['binding_cur']['roas_floor'] or 'n/a'}). "
              f"Account-level bids" + (" (product-scoped question: bids are not stored per product)" if self.scoped else "") + f"; {state}")
        v = X.factor_verdict(real, 1.0 if res["moved"] else 0.0, res["moved"]) if self.metric in NC_ONLY + ("cpm", "cpc", "spend", "meta_spend") else ("inside noise" if not real else "ruled out")
        self.add(hid, name, v, ev, detail=res)

    def _adset_state_note(self):
        if not self.w.has("meta_adset_state_daily"):
            return "ad-set state view absent: the activity-log change events (E1..) carry the edits"
        r = self.q("select min(day) mn from analytics.meta_adset_state_daily")
        mn = _day(r[0]["mn"]) if r and r[0]["mn"] else None
        if mn is None or mn > self.cs:
            return f"ad-set state snapshots start {mn}, after the comparison window, so edits come from the activity log (E1..)"
        return "ad-set state snapshots cover both windows"

    # ---------------------------------------------------------------- H1b attribution leak
    def h_attribution_leak(self, real):
        hid, name = "H1b", "Attribution leak: orders not tied to a Meta ad, non-Meta source share (sub-test of H1)"
        if self.scoped:
            self.add(hid, name, "untestable", "unmapped orders and last-click sources carry no ad product", missing="product on unmapped / source rows")
            return
        if not self.w.has("attribution_unmapped_daily", "attribution_source_daily"):
            self.add(hid, name, "untestable", "attribution views not available", missing="attribution_unmapped_daily / attribution_source_daily")
            return
        un = self.q(f"select p.p, sum(u.new_customers) nc from analytics.attribution_unmapped_daily u join {self.per()} on u.day between p.s and p.e group by 1")
        so = self.q(f"select p.p, s.ad_source src, sum(s.new_customers) nc from analytics.attribution_source_daily s join {self.per()} on s.day between p.s and p.e group by 1, 2")
        u = {r["p"]: _f(r["nc"]) for r in (un or [])}
        nc = {"cur": self.window("cur")["nc"], "cmp": self.window("cmp")["nc"]}
        parts, moved = [], False
        z1 = X.two_prop_z(u.get("cur", 0), nc["cur"], u.get("cmp", 0), nc["cmp"]) if un and nc["cur"] and nc["cmp"] else None
        if z1:
            p1, p0, z = z1
            mv = abs(z) >= 3 and abs(p1 - p0) >= 0.01
            moved |= mv
            parts.append(f"new customers tied to no Meta ad {p0:.1%} -> {p1:.1%} of actual new customers (orders_daily; z {z:+.1f}, {'beyond' if mv else 'inside'} binomial noise)")
        tot = {"cur": 0.0, "cmp": 0.0}
        meta = {"cur": 0.0, "cmp": 0.0}
        for r in so or []:
            tot[r["p"]] += _f(r["nc"])
            if str(r["src"]).lower().startswith("meta"):
                meta[r["p"]] += _f(r["nc"])
        z2 = X.two_prop_z(tot["cur"] - meta["cur"], tot["cur"], tot["cmp"] - meta["cmp"], tot["cmp"]) if tot["cur"] and tot["cmp"] else None
        if z2:
            p1, p0, z = z2
            mv = abs(z) >= 3 and abs(p1 - p0) >= 0.01
            moved |= mv
            parts.append(f"non-Meta share of last-click new customers {p0:.1%} -> {p1:.1%} (z {z:+.1f}, {'beyond' if mv else 'inside'} noise; last-click basis, separate from the any-click ad views)")
        if not parts:
            self.add(hid, name, "untestable", "no unmapped or source rows in a window", missing="attribution rows for both windows")
            return
        note = " Unmapped orders are inside orders_daily, so a rise leaves the actual-orders headline untouched and lowers ad-level ROAS only."
        v = "inside noise" if not real else ("consistent with" if moved else "ruled out")
        self.add(hid, name, v, "; ".join(parts) + "." + note)

    # ---------------------------------------------------------------- H16 intraday
    def h_intraday(self, real):
        hid, name = "H16", "Intraday shape: front- or back-loaded day (new-customer revenue and spend by hour vs the same weekday)"
        if self.scoped or self.metric not in NC_ONLY + ("spend", "meta_spend"):
            self.add(hid, name, "untestable", "hourly data is account-level new-customer revenue and spend; this question is product-scoped or not a revenue / spend metric", missing="hourly by product")
            return
        if not self.w.has("orders_hourly"):
            self.add(hid, name, "untestable", "orders_hourly not available", missing="analytics.orders_hourly")
            return
        by = {}
        for r in self.hourly_rows:
            by.setdefault(r["day"], {})[int(r["hour"])] = _f(r["nc_rev"])
        day = self.end
        base = [by[day - dt.timedelta(days=7 * k)] for k in range(1, 5) if (day - dt.timedelta(days=7 * k)) in by]
        if day not in by or len(base) < 2:
            self.add(hid, name, "untestable", f"orders_hourly has no rows for {day} or fewer than 2 of the same weekday in the prior 4 weeks", missing="orders_hourly for the target day and same weekdays")
            return
        complete = dt.datetime.now(X_PT).date() > day
        rev = X.intraday_shape(by[day], base, complete=complete) if complete else None
        spend = None
        snaps = self.q(f"select distinct on (day, hr) day, hr, total_spend from (select day, extract(hour from pacific_time)::int hr, pulled_at, total_spend from analytics.pacing_live_today "
                       f"where day between date '{day - dt.timedelta(days=28)}' and date '{day}') s order by day, hr, pulled_at desc", 1000) if self.w.has("pacing_live_today") else None
        if snaps:
            cum = {}
            for r in snaps:
                cum.setdefault(_day(r["day"]), {})[int(r["hr"])] = _f(r["total_spend"])
            def inc(d):
                run, out = 0.0, {}
                for h in range(0, 24):
                    if h in d:
                        out[h] = max(d[h] - run, 0.0)
                        run = max(d[h], run)
                return out
            sb = [inc(cum[day - dt.timedelta(days=7 * k)]) for k in range(1, 5) if (day - dt.timedelta(days=7 * k)) in cum]
            if day in cum and len(sb) >= 2 and complete:
                spend = X.intraday_shape(inc(cum[day]), sb)
        parts, flag = [], False
        for lab, r in (("new-customer revenue", rev), ("spend", spend)):
            if not r:
                continue
            fo = r["first_outside"]
            cp18 = next((c for c in r["checkpoints"] if c["hour"] == 18), None)
            seg = f"{lab}: " + (f"{cp18['target_share']:.0%} of the day's total in by 6 pm vs {cp18['baseline_share']:.0%} on the prior {r['baseline_days']} same weekdays (band +/-{cp18['band']:.0%})" if cp18 else "no 6 pm checkpoint")
            if fo:
                flag |= True
                seg += f"; {fo['direction']}, first outside the band at {fo['hour']:02d}:00"
            else:
                seg += "; inside the band at every hour"
            parts.append(seg)
        if not complete:
            hc = self.hourly
            parts.append(f"{day} is still running (the latest day can also be restated); cumulative new-customer revenue vs the same weekday first leaves the band at hour {hc.get('first_hour_outside_noise') if hc else 'n/a'}")
            flag = bool(hc and hc.get("first_hour_outside_noise") is not None)
        if not parts:
            self.add(hid, name, "untestable", "not enough hourly or spend snapshots for the target day and same weekdays", missing="pacing_live_today hourly snapshots / orders_hourly")
            return
        fh = (rev or spend or {}).get("first_outside")
        line = ""
        if flag and fh:
            near = [e for e in self.events if e["event_day"] == day and abs(self._hour_of(e) - fh["hour"]) <= 1]
            line = " Time-stamped changes within an hour of it: " + ("; ".join(f"{e['kind']} {self._hour_of(e):02d}:00 ({str(e.get('object_names'))[:40]})" for e in near[:3]) if near else "none logged") + "."
        v = "inside noise" if not real else ("consistent with" if flag else "ruled out")
        self.add(hid, name, v, "; ".join(parts) + "." + line + f" Tested on {day} (the window's last day); hourly revenue is Pacific time, spend is the latest Upstack snapshot per hour (trails the platforms by 30-60 minutes).")

    @staticmethod
    def _hour_of(e):
        t = e["event_time"]
        t = t if isinstance(t, dt.datetime) else dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return (t.astimezone(X_PT) if t.tzinfo else t).hour

    # ---------------------------------------------------------------- H17 order mix
    def h_order_mix(self, real):
        hid, name = "H17", "Order mix of new-customer orders (source, refunds, cancellations, rebill contamination; pack size and price point are H7)"
        if self.scoped:
            self.add(hid, name, "untestable", "order rows carry no ad product", missing="product on shopify_orders")
            return
        if not self.w.has("shopify_orders"):
            self.add(hid, name, "untestable", "shopify_orders not available", missing="analytics.shopify_orders")
            return
        rows = self.q("select p.p, o.source_name src, count(*) orders, count(*) filter (where o.is_cancelled) canc, count(*) filter (where o.is_rebill) rebill, "
                      "count(*) filter (where not o.is_cancelled and not o.is_rebill) kept, sum(o.total_price) filter (where not o.is_cancelled and not o.is_rebill) rev, "
                      "sum(o.refunded_amount) filter (where not o.is_cancelled and not o.is_rebill) refunded, count(*) filter (where not o.is_cancelled and not o.is_rebill and o.refunded_amount > 0) refunded_orders "
                      f"from analytics.shopify_orders o join {self.per()} on o.order_date between p.s and p.e where o.is_new_customer and not o.is_test and not o.is_trybe_gift group by 1, 2", 200)
        if not rows:
            self.add(hid, name, "untestable", "no new-customer orders in a window", missing="shopify_orders for the window")
            return
        agg = {"cur": {}, "cmp": {}}
        for r in rows:
            for k in ("orders", "canc", "rebill", "kept", "refunded_orders"):
                agg[r["p"]][k] = agg[r["p"]].get(k, 0.0) + _f(r[k])
            for k in ("rev", "refunded"):
                agg[r["p"]][k] = agg[r["p"]].get(k, 0.0) + _f(r[k])
        a, b = agg["cur"], agg["cmp"]
        if not a.get("orders") or not b.get("orders") or not a.get("kept") or not b.get("kept"):
            self.add(hid, name, "untestable", "no orders in one window", missing="shopify_orders for both windows")
            return
        tests = []
        for lab, k1, n1, k0, n0 in (("cancellation rate", a["canc"], a["orders"], b["canc"], b["orders"]), ("rebill-flagged share", a["rebill"], a["orders"], b["rebill"], b["orders"]),
                                     ("orders with a refund", a["refunded_orders"], a["kept"], b["refunded_orders"], b["kept"])):
            r = X.two_prop_z(k1, n1, k0, n0)
            if r:
                p1, p0, z = r
                tests.append((lab, p0, p1, z, abs(z) >= 3 and abs(p1 - p0) >= 0.01))
        ss = X.shift_share([{"p": r["p"], "key": r["src"] or "(none)", "rev": r["rev"], "kept": r["kept"], "nc": r["kept"]} for r in rows], "aov", min_count=30, share_pts=5.0)
        moved = [t for t in tests if t[4]] ; srcm = sorted([m for m in (ss["rows"] if ss else []) if abs(m["share_cur"] - m["share_cmp"]) * 100 >= 5 and (m["nc_cur"] + m["nc_cmp"]) >= 30], key=lambda m: -abs(m["share_cur"] - m["share_cmp"]))
        parts = [f"{lab} {p0:.1%} -> {p1:.1%} (z {z:+.1f})" for lab, p0, p1, z, mv in tests if mv]
        parts += [f"source_name {m['key']} share {m['share_cmp']:.1%} -> {m['share_cur']:.1%}" + (f", order value {m['rate_cmp']:.0f} -> {m['rate_cur']:.0f}" if m["rate_cmp"] and m["rate_cur"] else "") + f", AOV effect of the shift {m['mix_part']:+.2f}" for m in srcm[:3]]
        ref_amt = f"refunded amount {b['refunded'] / b['rev']:.1%} -> {a['refunded'] / a['rev']:.1%} of total_price (young orders refund later, so the latest days understate)" if a.get("rev") and b.get("rev") else ""
        ev = ("moved: " + "; ".join(parts) if parts else "no source share, refund, cancellation or rebill rate moved beyond its band") + ". " + ref_amt
        if srcm:
            ev += (" source_name values are app ids; the data says which is 'web' (Shopify online store), 'tiktok' and 'shopify_draft_order' but not what the numeric ids are, so their meaning is untestable-meaning.")
        v = "inside noise" if not real else ("consistent with" if (moved or srcm) else "ruled out")
        self.add(hid, name, v, ev, detail={"tests": [{"test": t[0], "cmp": t[1], "cur": t[2], "z": t[3]} for t in tests], "aov_by_source": ss and ss["rows"][:5]})

    # ---------------------------------------------------------------- H18 calendar
    def h_calendar(self, real):
        hid, name = "H18", "Calendar position (holidays, month start, paydays, BFCM ramp) and whether it moved the same way last month and last year"
        if self.scoped or self.metric not in ("nc_roas", "nc_cpa", "nc_revenue", "nc_aov", "spend", "meta_spend"):
            cc = X.calendar_counts(self.start, self.end)
            self.add(hid, name, "untestable", "history of the same calendar position is account-level new-customer revenue and spend only; flagged days in the target window: " + (", ".join(cc["flags"]) or "none"),
                     missing="account-level history for this metric / product")
            return
        lo = dt.date(2025, 1, 1)
        od = self.q(f"select day, new_customer_revenue nc_rev, new_customers nc from analytics.orders_daily where day between date '{lo}' and date '{self.end}'", 1000) if self.w.has("orders_daily") else None
        pl = self.q(f"select day, sum(spend) spend from analytics.platform_daily where day between date '{lo}' and date '{self.end}' group by day", 1000) if self.w.has("platform_daily") else None
        if not od:
            self.add(hid, name, "untestable", "orders_daily history missing", missing="orders_daily since 2025-01-01")
            return
        sp = {_day(r["day"]): _f(r["spend"]) for r in (pl or [])}
        daily = {_day(r["day"]): {"nc_rev": _f(r["nc_rev"]), "nc": _f(r["nc"]), "spend": sp.get(_day(r["day"]), 0.0), "imps": 0.0, "clicks": 0.0} for r in od}
        res = X.calendar_check(daily, self.metric, (self.start, self.end), (self.cs, self.ce))
        diff = res["differing"]
        flags = "; ".join(f"{k} ({v['cmp_days']} -> {v['cur_days']} days)" for k, v in diff.items())
        def sg(k):
            m, s = res["move_before"].get(k), res["same_sign"].get(k)
            return f"{k}: {'no data' if m is None else f'{m * 100:+.0f}%'}" + ("" if s is None else (" same direction" if s else " opposite direction"))
        hist = "same calendar position, " + "; ".join(sg(k) for k in ("last month", "last year")) + (f" (now {res['move_now'] * 100:+.0f}%)" if res["move_now"] is not None else "")
        if not diff:
            self.add(hid, name, "ruled out" if real else "inside noise", "the target and comparison windows hold the same calendar days (no holiday, month start, payday or BFCM day in one and not the other); " + hist, detail=res)
            return
        sames = [s for s in res["same_sign"].values() if s is not None]
        if not sames:
            self.add(hid, name, "untestable", f"calendar days differ: {flags}; history for the same calendar position is not loaded ({hist})", missing="orders_daily for last month / last year windows", detail=res)
            return
        if not real:
            self.add(hid, name, "inside noise", f"calendar days differ ({flags}) but the move is inside noise; {hist}", detail=res)
            return
        v = "consistent with" if any(sames) else "ruled out"
        txt = ("consistent with a calendar effect: " if v == "consistent with" else "no support for a calendar effect, the same position moved the other way both times: ") + f"calendar days differ: {flags}; {hist}. Two prior windows of one position, not a control."
        self.add(hid, name, v, txt, detail=res)


from zoneinfo import ZoneInfo  # noqa: E402

X_PT = ZoneInfo("America/Los_Angeles")
