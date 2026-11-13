"""Daily story facts: for a target day D, every metric against its same-weekday baseline, the exact decomposition of the headline, mix-versus-rate by segment,
the live creative mix, top movers, the change ledger and the per-source sections (Meta, Google, Klaviyo, Amazon, orders). Pure Python over a Panel
(etl/daily_story_panel.py); no database and no clock in here, so every number is reproducible and testable.

Definitions (Josh's rules, docs/AGENT_DATA_GUIDE.md):
  headline NC ROAS = orders_daily.new_customer_revenue / (Meta + Google spend) of the same day; the ad-attributed NC ROAS (sum of upstack_new_customer_revenue over Meta
  ads) is only a cut for comparing ads / products / structures, never the day's headline; Meta-reported ROAS is a side data point.
  Baseline = the same weekday in the previous four weeks (fewer when history is short; the count is recorded). The metric table shows the median of those days and the
  pooled value (sum / sum); the exact decomposition uses the pooled baseline so its terms add back to the move to floating point.
  Noise = sample standard deviation of the previous 28 days of the same daily metric; z = (D - baseline median) / noise, flagged at |z| >= 2.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import daily_story_math as M
from .daily_story_panel import (BREAKDOWN_START, PRODUCT_LABEL, SHOPIFY_START, START, Panel, age_bucket)
from .daily_story_registry import BY_KEY

VERSION = "v1"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ZFLAG = 2.0
UNTAGGED = "(untagged)"

CUTS = ("product", "stage", "structure", "cluster", "icp", "pain_point", "emotional_trigger", "line", "media", "format", "vendor", "age")
CUT_LABEL = {"product": "Product", "stage": "Stage", "structure": "Campaign structure", "cluster": "Market cluster", "icp": "Ideal customer profile (ICP)",
             "pain_point": "Pain point", "emotional_trigger": "Emotional trigger", "line": "Communication line", "media": "Video vs image", "format": "Format and style",
             "vendor": "Vendor", "age": "Concept age (days since first spend)"}

# ---------------------------------------------------------------------------------------------------------------------------- windows

def baseline_days(panel: Panel, d: dt.date, n: int = 4) -> list[dt.date]:
    out = []
    for k in range(1, n + 1):
        b = d - dt.timedelta(days=7 * k)
        if b in panel.acct and panel.acct[b]["has_meta"]:
            out.append(b)
    return out


def trailing_days(panel: Panel, d: dt.date, n: int) -> list[dt.date]:
    return [d - dt.timedelta(days=k) for k in range(1, n + 1) if (d - dt.timedelta(days=k)) in panel.acct and panel.acct[d - dt.timedelta(days=k)]["has_meta"]]


# ---------------------------------------------------------------------------------------------------------------------------- account sums

ADD = ("spend", "impressions", "clicks", "lpv", "atc", "ic", "nc", "nc_rev", "mp", "mrev", "n_ads", "returning_rev", "orders", "acct_nc_rev", "acct_nc",
       "g_spend", "g_clicks", "g_impr", "g_gp", "g_grev", "g_nc", "g_nc_rev", "capped_spend", "floor_spend", "bid_spend")


def acct_sums(panel: Panel, days: list[dt.date]) -> dict:
    """Additive account sums over days. A field is None when any of the days lacks its source (never a partial sum)."""
    out: dict = {}
    for k in ADD:
        vals = [panel.acct[d].get(k) for d in days if d in panel.acct]
        out[k] = None if (not vals or any(v is None for v in vals)) else sum(vals)
    out["n_days"] = len(days)
    return out


def derive(s: dict) -> dict:
    """Every ratio metric from additive sums (sum / sum)."""
    r = M.ratio
    meta, g = s.get("spend"), s.get("g_spend")
    total = None if meta is None or g is None else meta + g
    m = {
        "spend_meta": meta, "spend_google": g, "spend_total": total, "nc_rev": s.get("acct_nc_rev"), "nc": s.get("acct_nc"), "returning_rev": s.get("returning_rev"),
        "orders": s.get("orders"), "impressions": s.get("impressions"), "link_clicks": s.get("clicks"), "lpv": s.get("lpv"), "atc": s.get("atc"), "ic": s.get("ic"),
        "nc_roas": r(s.get("acct_nc_rev"), total), "aov": r(s.get("acct_nc_rev"), s.get("acct_nc")), "cpnc": r(total, s.get("acct_nc")),
        "cpm": None if not s.get("impressions") else meta * 1000 / s["impressions"], "ctr": r(s.get("clicks"), s.get("impressions")), "cplc": r(meta, s.get("clicks")),
        "conv": r(s.get("acct_nc"), s.get("clicks")), "c2lpv": r(s.get("lpv"), s.get("clicks")), "lpv2atc": r(s.get("atc"), s.get("lpv")),
        "atc2ic": r(s.get("ic"), s.get("atc")), "ic2nc": r(s.get("acct_nc"), s.get("ic")), "atc2nc": r(s.get("acct_nc"), s.get("atc")),
        "meta_roas": r(s.get("mrev"), meta), "ad_roas": r(s.get("nc_rev"), meta),
        "capped_share": r(s.get("capped_spend"), s.get("bid_spend")), "floor_share": r(s.get("floor_spend"), s.get("bid_spend")),
        "google_clicks": s.get("g_clicks"), "google_nc": s.get("g_nc"), "google_nc_rev": s.get("g_nc_rev"),
    }
    return m


# metrics that are ratios of sums (pooled over a window) versus values that are averaged over a window
POOLED = {"nc_roas", "aov", "cpnc", "cpm", "ctr", "cplc", "conv", "c2lpv", "lpv2atc", "atc2ic", "ic2nc", "atc2nc", "meta_roas", "ad_roas", "capped_share", "floor_share"}


def _structure_shares(rows: list[dict]) -> dict:
    sc = [r for r in rows if (r["stage"] or "") == "scaling"]
    tot = sum(r["spend"] for r in sc)
    allspend = sum(r["spend"] for r in rows)
    return {
        "troas_scaling_share": M.ratio(sum(r["spend"] for r in sc if "troas" in (r["campaign_name"] or "").lower()), tot),
        "d7_scaling_share": M.ratio(sum(r["spend"] for r in sc if "7dc" in (r["campaign_name"] or "").lower()), tot),
        "testing_share": M.ratio(sum(r["spend"] for r in rows if (r["stage"] or "") == "testing"), allspend),
    }


def day_metrics(panel: Panel, d: dt.date) -> dict:
    """Every registry metric for one day (None = not available that day)."""
    m = derive(acct_sums(panel, [d]))
    rows = panel.rows.get(d, [])
    m.update(_structure_shares(rows))
    fams = {r["fk"] for r in rows if r["spend"] > 0}
    m["families_live"] = float(len(fams)) if rows else None
    m["families_launched"] = float(len([f for f in fams if panel.fam_first.get(f) == d and d > START])) if rows else None
    m["new_adsets"] = float(len([a for a in {x["adset_id"] for x in panel.adsets.get(d, [])} if panel.adset_first.get(a) == d and d > START])) if rows else None
    tot = sum(r["spend"] for r in rows)
    newsp = sum(r["spend"] for r in rows if (panel.age_days(r["fk"], d) is not None and panel.age_days(r["fk"], d) <= 7 and panel.fam_first.get(r["fk"], START) > START))
    m["new_ad_share"] = M.ratio(newsp, tot)
    m["stage_moves"] = float(panel.stage_moves.get(d, 0)) if rows else None
    # placement
    pl = panel.place.get(d)
    if pl:
        ptot = sum(v["spend"] for v in pl.values())
        m["fb_share"] = M.ratio(pl.get("facebook", {}).get("spend", 0.0), ptot)
        m["ig_share"] = M.ratio(pl.get("instagram", {}).get("spend", 0.0), ptot)
    else:
        m["fb_share"] = m["ig_share"] = None
    # orders mix
    sh = panel.shop.get(d)
    if sh and sh.get("n"):
        n = sh["n"]
        m.update({"pack_1": sh["p1"] / n, "pack_2": sh["p2"] / n, "pack_3": sh["p3"] / n, "pack_4p": sh["p4"] / n, "discount_share": sh["coded"] / n,
                  "refund_share": M.ratio(sh["refunded"], sh["rev"]), "rebill_orders": sh.get("rebills")})
    else:
        m.update({k: None for k in ("pack_1", "pack_2", "pack_3", "pack_4p", "discount_share", "refund_share", "rebill_orders")})
    # klaviyo
    kv = panel.klaviyo.get(d)
    if kv is not None:
        def s_(f, src=None, ch=None):
            return sum(v[f] for (so, c), v in kv.items() if (src is None or so == src) and (ch is None or c == ch))
        m.update({"email_sends": s_("sends"), "campaign_sends": s_("sends", "campaign"), "flow_sends": s_("sends", "flow"), "klaviyo_rev": s_("revenue"),
                  "campaign_rev": s_("revenue", "campaign"), "flow_rev": s_("revenue", "flow")})
    else:
        m.update({k: None for k in ("email_sends", "campaign_sends", "flow_sends", "klaviyo_rev", "campaign_rev", "flow_rev")})
    am = panel.amazon.get(d)
    m["amazon_rev"] = am["rev"] if am else None
    m["amazon_nc_rev"] = am["nc_rev"] if am else None
    m["amazon_ad_spend"] = am["ad_spend"] if am else None
    m["new_visitors"] = panel.traffic.get(d)
    m["lp_changes"] = float(len(panel.lp_changes.get(d, []))) if d >= dt.date(2026, 11, 11) else None
    return m


class Memo:
    """Per-panel cache of day_metrics (a backfill asks for each day many times)."""
    def __init__(self, panel: Panel):
        self.panel = panel
        self._c: dict[dt.date, dict] = {}

    def day(self, d: dt.date) -> dict:
        if d not in self._c:
            self._c[d] = day_metrics(self.panel, d)
        return self._c[d]


def metric_table(panel: Panel, memo: Memo, d: dt.date) -> dict:
    """Per metric: value, same-weekday baseline (median and pooled), trailing 7 days, noise (std of the previous 28 days), z, flag."""
    bd = baseline_days(panel, d)
    t7 = trailing_days(panel, d, 7)
    t28 = trailing_days(panel, d, 28)
    day = memo.day(d)
    pooled_b = derive(acct_sums(panel, bd)) if bd else {}
    pooled_t7 = derive(acct_sums(panel, t7)) if t7 else {}
    out = {}
    for key, v in day.items():
        base_vals = [memo.day(b).get(key) for b in bd]
        med = M.median(base_vals)
        hist = [memo.day(x).get(key) for x in t28]
        z = M.zscore(v, med, hist)
        t7v = pooled_t7.get(key) if key in POOLED else M.mean([memo.day(x).get(key) for x in t7])
        pb = pooled_b.get(key) if key in POOLED else M.mean(base_vals)
        reg = BY_KEY.get(key, {})
        out[key] = {"label": reg.get("label", key), "unit": reg.get("unit", "count"), "value": v, "baseline_median": med, "baseline_pooled": pb, "trailing_7d": t7v,
                    "noise_std_28d": M.stdev(hist) if len([h for h in hist if h is not None]) >= M.MIN_NOISE_DAYS else None,
                    "pct_vs_baseline": M.pct_change(v, med), "z": z, "flag": M.flag(z, ZFLAG), "n_baseline": len([x for x in base_vals if x is not None])}
    return out


# ---------------------------------------------------------------------------------------------------------------------------- decomposition

def decomposition(panel: Panel, d: dt.date) -> dict | None:
    """NC ROAS = AOV x (meta spend / total spend) x (1000 / CPM) x CTR x (link click -> landing page view) x (view -> add to cart) x (add to cart -> new customer)
    [x split in two when initiated checkouts exist]. Exact in logs against the pooled same-weekday baseline."""
    bd = baseline_days(panel, d)
    if not bd:
        return None
    sd, sb = acct_sums(panel, [d]), acct_sums(panel, bd)
    md, mb = derive(sd), derive(sb)
    if md["nc_roas"] is None or mb["nc_roas"] is None:
        return None
    has_ic = bool(sd.get("ic")) and bool(sb.get("ic"))
    def f(name, label, key, sign, fn=None):
        vd, vb = (fn(md), fn(mb)) if fn else (md[key], mb[key])
        return (name, vd, vb, sign, label)
    inv_ratio = lambda x: M.ratio(x["spend_meta"], x["spend_total"])          # meta share of total spend (the metric rises with it)
    cpm_inv = lambda x: None if not x["cpm"] else 1000.0 / x["cpm"]
    fs = [f("aov", "New-customer average order value", "aov", +1),
          f("spend_mix", "Meta share of total spend (Google spend dilutes)", None, +1, inv_ratio),
          f("cpm", "Cost per 1,000 impressions (higher cost lowers return)", None, +1, cpm_inv),
          f("ctr", "Link click-through rate", "ctr", +1),
          f("c2lpv", "Link click to landing page view", "c2lpv", +1),
          f("lpv2atc", "Landing page view to add to cart", "lpv2atc", +1)]
    if has_ic:
        fs += [f("atc2ic", "Add to cart to initiated checkout", "atc2ic", +1), f("ic2nc", "Initiated checkout to new customer", "ic2nc", +1)]
    else:
        fs += [f("atc2nc", "Add to cart to new customer", "atc2nc", +1)]
    res = M.log_decompose([(n, vd, vb, s) for n, vd, vb, s, _ in fs])
    if res is None:
        return None
    labels = {n: lab for n, _, _, _, lab in fs}
    for t in res["terms"]:
        t["label"] = labels[t["name"]]
        if t["name"] == "cpm":                      # show the CPM itself, not its inverse
            t["day"], t["base"] = md["cpm"], mb["cpm"]
        elif t["name"] == "spend_mix":
            t["day"], t["base"] = md["spend_meta"] / md["spend_total"], mb["spend_meta"] / mb["spend_total"]
    return {"metric": "nc_roas", "day": md["nc_roas"], "base": mb["nc_roas"], "baseline_days": [b.isoformat() for b in bd], "has_initiated_checkout": has_ic,
            "basis": "headline NC ROAS: orders_daily new-customer revenue / (Meta + Google spend); baseline = pooled same weekday", **res,
            "check_sum_pct_points": sum(t["pct_points"] for t in res["terms"])}


# ---------------------------------------------------------------------------------------------------------------------------- segment cuts

def _fam(panel: Panel, fk: str) -> dict:
    return panel.fam.get(fk) or {}


def key_of(panel: Panel, cut: str, r: dict) -> str:
    f = _fam(panel, r["fk"])
    if cut == "product":
        return PRODUCT_LABEL.get(r["product"], r["product"] or UNTAGGED)
    if cut == "stage":
        return (r["stage"] or "unclassified").capitalize()
    if cut == "structure":
        return r["structure"]
    if cut == "age":
        return age_bucket(panel.age_days(r["fk"], r["day"]))
    v = f.get({"line": "line_code"}.get(cut, cut))
    return v if v else UNTAGGED


def seg_sums(rows: list[dict]) -> dict:
    s = {k: 0.0 for k in ("spend", "impressions", "clicks", "lpv", "atc", "nc", "nc_rev", "mp", "mrev", "n_ads")}
    ic_any = False
    ic = 0.0
    fams = set()
    for r in rows:
        for k in s:
            s[k] += r[k]
        if r.get("ic") is not None:
            ic_any, ic = True, ic + r["ic"]
        if r["spend"] > 0:
            fams.add(r["fk"])
    s["ic"] = ic if ic_any else None
    s["fams"] = len(fams)
    return s


def seg_metrics(s: dict) -> dict:
    r = M.ratio
    return {"spend": s["spend"], "families": s["fams"], "ads": s["n_ads"], "attr_roas": r(s["nc_rev"], s["spend"]), "cpm": None if not s["impressions"] else s["spend"] * 1000 / s["impressions"],
            "ctr": r(s["clicks"], s["impressions"]), "cplc": r(s["spend"], s["clicks"]), "conv": r(s["nc"], s["clicks"]), "aov": r(s["nc_rev"], s["nc"]), "cpnc": r(s["spend"], s["nc"]),
            "c2lpv": r(s["lpv"], s["clicks"]), "lpv2atc": r(s["atc"], s["lpv"]), "atc2ic": r(s["ic"], s["atc"]), "atc2nc": r(s["nc"], s["atc"]), "nc": s["nc"], "nc_rev": s["nc_rev"],
            "meta_roas": r(s["mrev"], s["spend"])}


def _group(panel: Panel, cut: str, rows: list[dict]) -> dict[str, list[dict]]:
    g = defaultdict(list)
    for r in rows:
        g[key_of(panel, cut, r)].append(r)
    return g


def cut_table(panel: Panel, d: dt.date, cut: str, limit: int = 14) -> dict | None:
    rows_d = panel.rows.get(d, [])
    bd = baseline_days(panel, d)
    t7 = trailing_days(panel, d, 7)
    if not rows_d or not bd:
        return None
    rows_b = [r for b in bd for r in panel.rows.get(b, [])]
    rows_t = [r for t in t7 for r in panel.rows.get(t, [])]
    gd, gb, gt = _group(panel, cut, rows_d), _group(panel, cut, rows_b), _group(panel, cut, rows_t)
    tot_d, tot_b, tot_t = sum(r["spend"] for r in rows_d), sum(r["spend"] for r in rows_b), sum(r["spend"] for r in rows_t)
    sd_ = {k: seg_sums(v) for k, v in gd.items()}
    sb_ = {k: seg_sums(v) for k, v in gb.items()}
    st_ = {k: seg_sums(v) for k, v in gt.items()}
    mr = M.mix_rate({k: (v["spend"], v["nc_rev"]) for k, v in sd_.items()}, {k: (v["spend"], v["nc_rev"]) for k, v in sb_.items()})
    eff = {e["key"]: e for e in (mr["rows"] if mr else [])}
    rows = []
    for k in sorted(set(sd_) | set(sb_), key=lambda x: -max(sd_.get(x, {"spend": 0})["spend"], sb_.get(x, {"spend": 0})["spend"] / max(len(bd), 1))):
        a, b, t = sd_.get(k), sb_.get(k), st_.get(k)
        row = {"key": k, "day": seg_metrics(a) if a else None, "share": (a["spend"] / tot_d) if a and tot_d else 0.0, "base_share": (b["spend"] / tot_b) if b and tot_b else 0.0,
               "t7_share": (t["spend"] / tot_t) if t and tot_t else 0.0}
        bm = seg_metrics(b) if b else {}
        row["base"] = {"spend_per_day": (b["spend"] / len(bd)) if b else 0.0, **{x: bm.get(x) for x in ("attr_roas", "cpm", "ctr", "cplc", "conv", "aov", "c2lpv", "lpv2atc", "atc2nc")}}
        row["t7"] = {"attr_roas": seg_metrics(t)["attr_roas"] if t else None}
        e = eff.get(k)
        row["effect"] = None if not e else {"mix": e["mix"], "rate": e["rate_effect"], "total": e["total"]}
        rows.append(row)
    rows_out = rows[:limit]
    rest = rows[limit:]
    return {"cut": cut, "label": CUT_LABEL[cut], "n_segments": len(rows), "rows": rows_out, "other_rows": len(rest), "other_share": sum(r["share"] for r in rest),
            "mix_rate": None if not mr else {"day": mr["value"], "base": mr["base"], "change": mr["change"], "mix": mr["mix"], "rate": mr["rate"]}}


def family_movers(panel: Panel, d: dt.date, n: int = 6) -> dict | None:
    """Ads (families) contributing most to the move of the ad-attributed NC ROAS against the pooled same-weekday baseline (a cut, not the headline)."""
    rows_d = panel.rows.get(d, [])
    bd = baseline_days(panel, d)
    if not rows_d or not bd:
        return None
    sd, sb = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for r in rows_d:
        sd[r["fk"]][0] += r["spend"]; sd[r["fk"]][1] += r["nc_rev"]
    for b in bd:
        for r in panel.rows.get(b, []):
            sb[r["fk"]][0] += r["spend"]; sb[r["fk"]][1] += r["nc_rev"]
    mr = M.mix_rate({k: tuple(v) for k, v in sd.items()}, {k: tuple(v) for k, v in sb.items()})
    if not mr:
        return None
    out = []
    for e in M.top_by_abs(mr["rows"], "total", n):
        f = _fam(panel, e["key"])
        fk = e["key"]
        age = panel.age_days(fk, d)
        out.append({"family": fk, "name": f.get("name") or fk, "product": PRODUCT_LABEL.get(f.get("product_slug") or "", f.get("product_slug") or ""), "cluster": f.get("cluster") or UNTAGGED,
                    "icp": f.get("icp") or UNTAGGED, "format": f.get("format") or "Unknown", "vendor": f.get("vendor") or UNTAGGED, "hook": (f.get("hook") or "")[:160] or None,
                    "age_days": age, "spend": e["spend"], "base_spend_per_day": e["base_spend"] / len(bd), "share": e["share"], "base_share": e["base_share"],
                    "attr_roas": e["rate"], "base_attr_roas": e["base_rate"], "mix": e["mix"], "rate": e["rate_effect"], "total": e["total"]})
    return {"change": mr["change"], "day": mr["value"], "base": mr["base"], "rows": out}


def live_summary(panel: Panel, d: dt.date, memo: Memo) -> dict | None:
    rows = panel.rows.get(d, [])
    if not rows:
        return None
    fams = {r["fk"] for r in rows if r["spend"] > 0}
    tagged = {f for f in fams if _fam(panel, f).get("cluster")}
    tot = sum(r["spend"] for r in rows)
    tag_sp = sum(r["spend"] for r in rows if r["fk"] in tagged)
    bd = baseline_days(panel, d)
    base_fams = [len({r["fk"] for r in panel.rows.get(b, []) if r["spend"] > 0}) for b in bd]
    return {"families_live": len(fams), "ads_live": int(sum(r["n_ads"] for r in rows)), "baseline_families_median": M.median(base_fams),
            "tagged_spend_share": M.ratio(tag_sp, tot), "families_untagged": len(fams - tagged),
            "note": "families = distinct tracker family key (copies of a creative count once); tags come from the Creative Tracker first row of each family"}


# ---------------------------------------------------------------------------------------------------------------------------- per-source sections

def klaviyo_section(panel: Panel, memo: Memo, d: dt.date) -> dict:
    kv = panel.klaviyo.get(d)
    last = max(panel.klaviyo) if panel.klaviyo else None
    if kv is None:
        return {"available": False, "loaded_through": last.isoformat() if last else None, "note": "No Klaviyo rows for this day in analytics.klaviyo_daily."}
    bd = baseline_days(panel, d)
    cells = []
    for src in ("campaign", "flow"):
        for ch in ("email", "sms"):
            v = kv.get((src, ch), {})
            row = {"source": src, "channel": ch, "day": {f: v.get(f) for f in ("sends", "delivered", "opens", "clicks", "unsubs", "orders", "revenue")}}
            row["base"] = {f: M.median([(panel.klaviyo.get(b, {}).get((src, ch), {}) or {}).get(f) if b in panel.klaviyo else None for b in bd]) for f in ("sends", "delivered", "opens", "clicks", "unsubs", "orders", "revenue")}
            cells.append(row)
    tot = {f: sum((c["day"][f] or 0) for c in cells) for f in ("sends", "delivered", "opens", "clicks", "unsubs", "orders", "revenue")}
    tb = {f: M.median([sum((panel.klaviyo[b].get((s, c), {}) or {}).get(f, 0) for s in ("campaign", "flow") for c in ("email", "sms")) if b in panel.klaviyo else None for b in bd]) for f in tot}
    return {"available": True, "basis": "Klaviyo-attributed orders and revenue ('Placed Order - Upstackified'); not additive with Upstack new-customer revenue or ad revenue",
            "cells": cells, "total": tot, "total_base": tb, "biggest_sends": panel.klaviyo_top.get(d, [])[:5], "loaded_through": last.isoformat() if last else None,
            "not_in_database": ["Klaviyo opens and clicks are per-day counts (not unique people)", "flow SMS unsubscribes are not exposed by Klaviyo"]}


def google_section(panel: Panel, memo: Memo, d: dt.date) -> dict:
    a = panel.acct.get(d, {})
    if not a.get("has_google"):
        return {"available": False, "note": "No Google rows for this day."}
    bd = baseline_days(panel, d)
    def b(k): return M.median([panel.acct[x].get(k) for x in bd])
    cpc = M.ratio(a["g_spend"], a["g_clicks"])
    types = []
    for ct, v in sorted(panel.google_type.get(d, {}).items(), key=lambda kv: -kv[1]["spend"]):
        bsp = M.median([(panel.google_type.get(x, {}).get(ct) or {}).get("spend", 0.0) for x in bd])
        types.append({"type": ct, "spend": v["spend"], "base_spend": bsp, "clicks": v["clicks"], "cpc": M.ratio(v["spend"], v["clicks"]), "google_purchases": v["gp"], "google_revenue": v["grev"],
                      "google_roas": M.ratio(v["grev"], v["spend"]), "nc": v["nc"], "nc_rev": v["nc_rev"], "nc_roas": M.ratio(v["nc_rev"], v["spend"])})
    return {"available": True, "basis": "Google spend is in the headline NC ROAS denominator. Google-reported purchases and revenue are side data; Upstack new-customer columns are any-click attributed",
            "spend": a["g_spend"], "base_spend": b("g_spend"), "clicks": a["g_clicks"], "base_clicks": b("g_clicks"), "cpc": cpc, "base_cpc": M.ratio(b("g_spend"), b("g_clicks")),
            "google_purchases": a["g_gp"], "google_revenue": a["g_grev"], "google_roas": M.ratio(a["g_grev"], a["g_spend"]), "upstack_nc": a["g_nc"], "upstack_nc_rev": a["g_nc_rev"],
            "upstack_nc_roas": M.ratio(a["g_nc_rev"], a["g_spend"]), "share_of_total_spend": M.ratio(a["g_spend"], (a["spend"] or 0) + a["g_spend"]), "by_type": types}


def amazon_section(panel: Panel, d: dt.date) -> dict:
    last = max(panel.amazon) if panel.amazon else None
    am = panel.amazon.get(d)
    if am is None:
        return {"available": False, "loaded_through": last.isoformat() if last else None, "lag_days": (d - last).days if last else None,
                "note": "analytics.amazon_daily has no row for this day yet" + (f" (loaded through {last.isoformat()})" if last else "")}
    bd = baseline_days(panel, d)
    def b(k): return M.median([panel.amazon[x][k] for x in bd if x in panel.amazon])
    return {"available": True, "loaded_through": last.isoformat(), "lag_days": (d - last).days, "basis": "Amazon (ExpandFi feed). Never added to DTC NC ROAS",
            "revenue": am["rev"], "base_revenue": b("rev"), "units": am["units"], "orders": am["orders"], "nc_revenue": am["nc_rev"], "ad_spend": am["ad_spend"], "base_ad_spend": b("ad_spend"),
            "attributed_sales": am["attr_sales"], "acos": M.ratio(am["ad_spend"], am["attr_sales"]), "tacos": M.ratio(am["ad_spend"], am["rev"]), "clicks": am["clicks"], "impressions": am["impressions"],
            "note": "ACOS = ad spend / ad-attributed sales; TACoS = ad spend / total Amazon revenue; the feed lags about two days, so the newest days can be partial"}


def placement_section(panel: Panel, d: dt.date) -> dict:
    pl = panel.place.get(d)
    if not pl:
        return {"available": False, "note": f"Placement breakdowns start {BREAKDOWN_START.isoformat()} (analytics.meta_breakdown_daily)."}
    bd = [b for b in baseline_days(panel, d) if b in panel.place]
    rows = []
    for k in ("facebook", "instagram"):
        v = pl.get(k)
        if not v:
            continue
        pooled = {f: sum((panel.place[b].get(k) or {}).get(f, 0.0) for b in bd) for f in ("spend", "impressions", "clicks", "mp", "mrev")}
        rows.append({"placement": k, "spend": v["spend"], "share": M.ratio(v["spend"], sum(x["spend"] for x in pl.values())), "cpm": M.ratio(v["spend"] * 1000, v["impressions"]),
                     "ctr": M.ratio(v["clicks"], v["impressions"]), "cplc": M.ratio(v["spend"], v["clicks"]), "meta_roas": M.ratio(v["mrev"], v["spend"]),
                     "base_cpm": M.ratio(pooled["spend"] * 1000, pooled["impressions"]), "base_ctr": M.ratio(pooled["clicks"], pooled["impressions"]), "base_cplc": M.ratio(pooled["spend"], pooled["clicks"]),
                     "base_share": M.ratio(pooled["spend"], sum(sum((panel.place[b].get(x) or {}).get("spend", 0.0) for x in panel.place[b]) for b in bd))})
    return {"available": True, "basis": "Meta-reported (no Upstack attribution by placement); publisher_platform", "rows": rows, "n_baseline_days": len(bd)}
