"""Pure statistics for the `why` investigation (no database, no I/O): count-based noise gate by grain, exact AOV decomposition, edited-vs-unedited control, verdict assignment, bulk grouping.

Everything takes plain lists / dicts so it is unit tested on synthetic data (tests/brain/test_why_investigation.py).
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

VERDICTS = ("supported by control", "consistent with", "ruled out", "inside noise", "untestable")
GRAINS = (1, 3, 7)
BASELINE_WEEKS = 4
K_SIGMA = 2.0


def _f(x) -> float:
    return float(x) if x is not None else 0.0


# ------------------------------------------------------------------ metric formulas on summed daily rows
# a daily row carries: nc_rev, nc, spend, imps, clicks, aov_n (orders) ; the count that drives noise is `count_key`
METRICS = {
    "nc_roas": (lambda s: s["nc_rev"] / s["spend"] if s["spend"] else None, "nc"),
    "nc_cpa": (lambda s: s["spend"] / s["nc"] if s["nc"] else None, "nc"),
    "nc_revenue": (lambda s: s["nc_rev"], "nc"),
    "nc_aov": (lambda s: s["nc_rev"] / s["nc"] if s["nc"] else None, "nc"),
    "spend": (lambda s: s["spend"], None),
    "meta_spend": (lambda s: s["spend"], None),
    "cpm": (lambda s: s["spend"] / s["imps"] * 1000 if s["imps"] else None, "clicks"),
    "ctr": (lambda s: s["clicks"] / s["imps"] if s["imps"] else None, "clicks"),
    "cpc": (lambda s: s["spend"] / s["clicks"] if s["clicks"] else None, "clicks"),
    "conversion": (lambda s: s["nc"] / s["clicks"] if s["clicks"] else None, "nc"),
}
KEYS = ("nc_rev", "nc", "spend", "imps", "clicks")


def window_sum(daily: dict, lo: dt.date, hi: dt.date) -> dict:
    out = {k: 0.0 for k in KEYS}
    out["days"] = 0
    d = lo
    while d <= hi:
        r = daily.get(d)
        if r is not None:
            out["days"] += 1
            for k in KEYS:
                out[k] += _f(r.get(k))
        d += dt.timedelta(days=1)
    return out


def noise_gate(daily: dict, end: dt.date, metric: str, grains=GRAINS, weeks: int = BASELINE_WEEKS) -> dict:
    """For each grain g (days): the g-day window ending `end` against the same-length windows ending 7, 14, 21, 28 days earlier (same weekday alignment).
    Noise = max(Poisson band on the driving count, empirical spread of the baseline windows) x K_SIGMA. Real = |relative change| beyond that band.
    Also returns what the NEXT day's value would have to be for the 7-day (or the smallest non-real) window to cross the band."""
    fn, ckey = METRICS[metric]
    out = {}
    for g in grains:
        cur = window_sum(daily, end - dt.timedelta(days=g - 1), end)
        base = [window_sum(daily, end - dt.timedelta(days=7 * k + g - 1), end - dt.timedelta(days=7 * k)) for k in range(1, weeks + 1)]
        base = [b for b in base if b["days"] == g]
        vc = fn(cur) if cur["days"] == g else None
        vbs = [fn(b) for b in base]
        vbs = [v for v in vbs if v is not None]
        if vc is None or len(vbs) < 2:
            out[g] = {"grain_days": g, "verdict": "untestable", "reason": "not enough days loaded for this grain"}
            continue
        vb = statistics.fmean(vbs)
        rel = (vc - vb) / vb if vb else None
        if rel is None:
            out[g] = {"grain_days": g, "verdict": "untestable", "reason": "baseline is zero"}
            continue
        emp = (statistics.stdev(vbs) / abs(vb)) if len(vbs) >= 3 and vb else 0.0
        pois = None
        if ckey:
            n_c = cur[ckey]
            n_b = statistics.fmean([b[ckey] for b in base])
            if n_c > 0 and n_b > 0:
                pois = math.sqrt(1 / n_c + 1 / (len(base) * n_b))
        band = K_SIGMA * max(emp, pois or 0.0)
        real = abs(rel) > band if band > 0 else abs(rel) > 0.05
        out[g] = {"grain_days": g, "value": round(vc, 4), "same_weekday_baseline": round(vb, 4), "change_pct": round(rel * 100, 1), "noise_band_pct": round(band * 100, 1),
                  "poisson_band_pct": None if pois is None else round(K_SIGMA * pois * 100, 1), "empirical_band_pct": round(K_SIGMA * emp * 100, 1),
                  "count_basis": ckey, "count_cur": None if not ckey else round(cur[ckey]), "verdict": "real" if real else "inside noise"}
    return out


def next_day_needed(daily: dict, end: dt.date, metric: str, grain: int, gate_row: dict) -> dict | None:
    """The value tomorrow's single day must show for the g-day window (ending tomorrow) to leave the noise band, assuming tomorrow's volume equals the window's daily mean."""
    if gate_row.get("verdict") not in ("real", "inside noise") or "same_weekday_baseline" not in gate_row:
        return None
    fn, _ = METRICS[metric]
    nxt = end + dt.timedelta(days=1)
    w6 = window_sum(daily, nxt - dt.timedelta(days=grain - 1), end)           # the g-1 days that stay in the window
    mean_day = {k: (w6[k] / w6["days"] if w6["days"] else 0.0) for k in KEYS}
    base = gate_row["same_weekday_baseline"]
    band = gate_row["noise_band_pct"] / 100
    target = base * (1 - band) if gate_row["change_pct"] < 0 else base * (1 + band)
    # solve fn(w6 + x) = target for ratio metrics by searching a scale on the numerator-side keys; simple bisection on a scalar multiplier of the mean day
    def val(mult):
        s = {k: w6[k] + mean_day[k] * mult for k in KEYS}
        return fn(s)
    lo, hi = 0.0, 20.0
    v0, v1 = val(lo), val(hi)
    if v0 is None or v1 is None:
        return None
    if (v0 - target) * (v1 - target) > 0:
        return {"note": "cannot cross the band with one more day of normal volume", "target_value": round(target, 4)}
    for _ in range(40):
        mid = (lo + hi) / 2
        vm = val(mid)
        if vm is None:
            return None
        if (v0 - target) * (vm - target) <= 0:
            hi = mid
        else:
            lo, v0 = mid, vm
    s = {k: mean_day[k] * ((lo + hi) / 2) for k in KEYS}
    day_val = fn(s)
    return {"target_window_value": round(target, 4), "tomorrow_single_day_value_needed": None if day_val is None else round(day_val, 4),
            "reading": f"for the {grain}-day window ending {nxt} to be outside the band, {nxt} alone would need about {None if day_val is None else round(day_val, 3)} at normal volume"}


# ------------------------------------------------------------------ hour of day
def hourly_curve(rows: list[dict], end: dt.date, weeks: int = BASELINE_WEEKS) -> dict | None:
    """rows: {day, hour, nc_rev, nc}. Cumulative new-customer revenue by hour on `end` against the mean of the same weekday over the prior `weeks` weeks."""
    by = {}
    for r in rows:
        by.setdefault(r["day"], {})[int(r["hour"])] = (_f(r.get("nc_rev")), _f(r.get("nc")))
    if end not in by:
        return None
    base_days = [end - dt.timedelta(days=7 * k) for k in range(1, weeks + 1) if (end - dt.timedelta(days=7 * k)) in by]
    if len(base_days) < 2:
        return None
    last = max(by[end])
    out, first_gap = [], None
    cum_c = cum_n = 0.0
    cb = {d: 0.0 for d in base_days}
    nb = {d: 0.0 for d in base_days}
    for h in range(0, last + 1):
        cum_c += by[end].get(h, (0, 0))[0]
        cum_n += by[end].get(h, (0, 0))[1]
        for d in base_days:
            cb[d] += by[d].get(h, (0, 0))[0]
            nb[d] += by[d].get(h, (0, 0))[1]
        mb = statistics.fmean(cb.values())
        mn = statistics.fmean(nb.values())
        ratio = cum_c / mb if mb else None
        band = K_SIGMA * math.sqrt(1 / cum_n + 1 / (len(base_days) * mn)) if cum_n > 0 and mn > 0 else None
        if first_gap is None and ratio is not None and band is not None and abs(ratio - 1) > band and mn >= 10:
            first_gap = h
        if h in (6, 9, 12, 15, 18, 21, last):
            out.append({"hour": h, "cum_nc_revenue": round(cum_c, 2), "baseline_cum": round(mb, 2), "ratio": None if ratio is None else round(ratio, 3), "band": None if band is None else round(band, 3)})
    return {"day": str(end), "through_hour": last, "checkpoints": out, "first_hour_outside_noise": first_gap, "baseline_days": [str(d) for d in base_days]}


def event_hour_split(rows: list[dict], event_day: dt.date, event_hour: int, weeks: int = BASELINE_WEEKS) -> dict | None:
    """New-customer revenue on the event day BEFORE vs AFTER the event hour, each against the mean of the same hours on the same weekday of the prior weeks. Account level only."""
    by = {}
    for r in rows:
        by.setdefault(r["day"], {})[int(r["hour"])] = _f(r.get("nc_rev"))
    if event_day not in by:
        return None
    base_days = [event_day - dt.timedelta(days=7 * k) for k in range(1, weeks + 1) if (event_day - dt.timedelta(days=7 * k)) in by]
    if len(base_days) < 2:
        return None
    last = max(by[event_day])
    def part(d, lo, hi):
        return sum(v for h, v in by[d].items() if lo <= h <= hi)
    pre_a, post_a = part(event_day, 0, event_hour - 1), part(event_day, event_hour, last)
    pre_b = statistics.fmean([part(d, 0, event_hour - 1) for d in base_days])
    post_b = statistics.fmean([part(d, event_hour, last) for d in base_days])
    if not pre_b or not post_b:
        return None
    r1, r2 = pre_a / pre_b, post_a / post_b
    return {"event_hour": event_hour, "before_ratio_vs_same_weekday": round(r1, 3), "after_ratio_vs_same_weekday": round(r2, 3), "after_over_before": round(r2 / r1, 3) if r1 else None,
            "basis": "account-level new-customer revenue by hour (orders_hourly), not by ad set"}


# ------------------------------------------------------------------ AOV decomposition (exact)
def decompose_aov(cur: dict, cmp_: dict) -> dict:
    """Each side: {"orders": N, "products": {name: {"qty": q, "gross": sum(price*qty), "disc": sum(line_discount)}}}. AOV = product net per order = U x (P - D), U units per order,
    P gross price per unit (= sum over products of unit share x product unit price), D discount per unit. The four effects add up to the AOV change exactly (midpoint split):
    pack size (U), product mix (shares in P), price point (unit prices within products), discount (D)."""
    def side(s):
        q = sum(p["qty"] for p in s["products"].values())
        g = sum(p["gross"] for p in s["products"].values())
        d = sum(p["disc"] for p in s["products"].values())
        n = s["orders"]
        return {"n": n, "q": q, "U": q / n, "P": g / q, "D": d / q, "aov": (g - d) / n}
    a, b = side(cmp_), side(cur)
    names = set(cmp_["products"]) | set(cur["products"])
    share_a, share_b, pa, pb = {}, {}, {}, {}
    for nme in names:
        pa_ = cmp_["products"].get(nme)
        pb_ = cur["products"].get(nme)
        share_a[nme] = pa_["qty"] / a["q"] if pa_ else 0.0
        share_b[nme] = pb_["qty"] / b["q"] if pb_ else 0.0
        up_a = pa_["gross"] / pa_["qty"] if pa_ and pa_["qty"] else None
        up_b = pb_["gross"] / pb_["qty"] if pb_ and pb_["qty"] else None
        pa[nme] = up_a if up_a is not None else up_b
        pb[nme] = up_b if up_b is not None else up_a
    mix = sum((share_b[n] - share_a[n]) * (pa[n] + pb[n]) / 2 for n in names)
    price_unit = sum((share_a[n] + share_b[n]) / 2 * (pb[n] - pa[n]) for n in names)      # effect on P of unit-price changes
    u_mid = (a["U"] + b["U"]) / 2
    x_mid = ((a["P"] - a["D"]) + (b["P"] - b["D"])) / 2
    e_pack = (b["U"] - a["U"]) * x_mid
    e_mix = u_mid * mix
    e_price = u_mid * price_unit
    e_disc = -u_mid * (b["D"] - a["D"])
    total = b["aov"] - a["aov"]
    return {"aov_cmp": round(a["aov"], 4), "aov_cur": round(b["aov"], 4), "change": round(total, 4), "units_per_order": [round(a["U"], 4), round(b["U"], 4)],
            "effects": {"pack_size": round(e_pack, 4), "product_mix": round(e_mix, 4), "price_point": round(e_price, 4), "discount": round(e_disc, 4)},
            "residual": total - (e_pack + e_mix + e_price + e_disc), "basis": "product net of discounts per new-customer order (shipping and tax excluded)"}


# ------------------------------------------------------------------ edited vs unedited control
def group_metrics(rows: list[dict]) -> dict:
    s = {k: sum(_f(r.get(k)) for r in rows) for k in ("spend", "imps", "clicks", "nc", "nc_rev")}
    return {"spend": s["spend"], "cpm": s["spend"] / s["imps"] * 1000 if s["imps"] else None, "cpc": s["spend"] / s["clicks"] if s["clicks"] else None,
            "conv": s["nc"] / s["clicks"] if s["clicks"] else None, "nc_roas": s["nc_rev"] / s["spend"] if s["spend"] else None, "cpa": s["spend"] / s["nc"] if s["nc"] else None,
            "nc": s["nc"], "days": len({r["day"] for r in rows})}


def _rel(a, b):
    return None if a is None or b in (None, 0) else a / b - 1


def control(rows: list[dict], event_day: dt.date, end: dt.date, pre_days: int = 3) -> dict:
    """rows: {day, aff (bool), spend, imps, clicks, nc, nc_rev} per group and day. Affected vs unaffected, before vs after. The pre-trend is the first vs last pre day of NC ROAS in each group."""
    pre_lo = event_day - dt.timedelta(days=pre_days)
    pre_hi = event_day - dt.timedelta(days=1)
    out = {"pre_window": [str(pre_lo), str(pre_hi)], "post_window": [str(event_day), str(end)], "groups": {}}
    daily = {}
    for grp in (True, False):
        name = "affected" if grp else "unaffected"
        g = [r for r in rows if bool(r["aff"]) == grp]
        pre = group_metrics([r for r in g if pre_lo <= r["day"] <= pre_hi])
        post = group_metrics([r for r in g if event_day <= r["day"] <= end])
        out["groups"][name] = {"before": pre, "after": post, "change_pct": {k: (None if _rel(post[k], pre[k]) is None else round(_rel(post[k], pre[k]) * 100, 1)) for k in ("cpm", "cpc", "conv", "nc_roas", "cpa", "spend")}}
        days = sorted({r["day"] for r in g if pre_lo <= r["day"] <= pre_hi})
        if len(days) >= 2:
            f = group_metrics([r for r in g if r["day"] == days[0]])["nc_roas"]
            l = group_metrics([r for r in g if r["day"] == days[-1]])["nc_roas"]
            daily[name] = _rel(l, f)
        else:
            daily[name] = None
    out["pre_trend_pct"] = {k: (None if v is None else round(v * 100, 1)) for k, v in daily.items()}
    ca, cu = out["groups"]["affected"]["change_pct"]["nc_roas"], out["groups"]["unaffected"]["change_pct"]["nc_roas"]
    out["nc_roas_diff_in_diff_pts"] = None if ca is None or cu is None else round(ca - cu, 1)
    return out


def control_verdict(ctl: dict, spend_share: float | None) -> tuple[str, str]:
    """(verdict, wording). Count noise on NC ROAS per group ~ 2/sqrt(nc) in each period; a group that moved less than that did not move."""
    a, u = ctl["groups"]["affected"], ctl["groups"]["unaffected"]
    ca, cu = a["change_pct"]["nc_roas"], u["change_pct"]["nc_roas"]
    if ca is None or cu is None or a["before"]["nc"] < 5 or a["after"]["nc"] < 5 or u["before"]["nc"] < 5:
        return "untestable", "too few new customers in the affected or the unaffected group (under 5 in a window) for a before / after read"
    def band(g):
        nb, na = max(g["before"]["nc"], 1), max(g["after"]["nc"], 1)
        return K_SIGMA * math.sqrt(1 / nb + 1 / na) * 100
    ba, bu = band(a), band(u)
    pt = ctl["pre_trend_pct"]
    pre_gap = None if pt["affected"] is None or pt["unaffected"] is None else abs(pt["affected"] - pt["unaffected"])
    did = ca - cu
    if abs(ca) <= ba and abs(did) <= ba:
        return "inside noise", f"the edited group's NC ROAS moved {ca:+.1f}% (noise band +/-{ba:.0f}%), inside noise"
    if abs(did) <= max(ba, bu) and abs(cu) > bu:
        return "ruled out", f"consistent with not the cause: the unedited group moved {cu:+.1f}% against {ca:+.1f}% for the edited group, the same way (difference {did:+.1f} points, inside the +/-{max(ba, bu):.0f}% band)"
    if abs(did) > max(ba, bu):
        if pre_gap is not None and pre_gap <= max(ba, bu) and (spend_share is None or spend_share >= 0.05):
            return "supported by control", f"edited group {ca:+.1f}% vs unedited {cu:+.1f}% (difference {did:+.1f} points, band +/-{max(ba, bu):.0f}%), parallel pre-trend (gap {pre_gap:.0f} points); not randomized"
        return "consistent with", f"edited group {ca:+.1f}% vs unedited {cu:+.1f}% (difference {did:+.1f} points) but the pre-trend differs (gap {'n/a' if pre_gap is None else f'{pre_gap:.0f}'} points) or the edit touched under 5% of spend, so selection is not excluded"
    return "inside noise", f"edited {ca:+.1f}% vs unedited {cu:+.1f}%: the difference ({did:+.1f} points) is inside the band"


# ------------------------------------------------------------------ bulk edit grouping (Python mirror of analytics.change_timeline, used on raw events and in tests)
def group_bulk(events: list[dict], gap_minutes: int = 90) -> list[dict]:
    """events: {event_time (datetime), event_type, actor, object_id, old_amount, new_amount, account}. Same event_type by the same actor in the same account within
    `gap_minutes` of the previous event of that kind => one row with the object count, the old and new range and the id list."""
    keyed = {}
    for e in sorted(events, key=lambda e: e["event_time"]):
        keyed.setdefault((e.get("account"), e["event_type"], e.get("actor")), []).append(e)
    out = []
    for (acct, et, actor), evs in keyed.items():
        cur = []
        for e in evs:
            if cur and (e["event_time"] - cur[-1]["event_time"]) > dt.timedelta(minutes=gap_minutes):
                out.append(_bulk_row(acct, et, actor, cur))
                cur = []
            cur.append(e)
        if cur:
            out.append(_bulk_row(acct, et, actor, cur))
    return sorted(out, key=lambda r: r["event_time"])


def _bulk_row(acct, et, actor, evs):
    ids = sorted({e["object_id"] for e in evs})
    olds = [_f(e["old_amount"]) for e in evs if e.get("old_amount") is not None]
    news = [_f(e["new_amount"]) for e in evs if e.get("new_amount") is not None]
    return {"account": acct, "event_type": et, "actor": actor, "event_time": evs[0]["event_time"], "last_time": evs[-1]["event_time"], "objects": len(ids), "object_ids": ids,
            "old_range": [min(olds), max(olds)] if olds else None, "new_range": [min(news), max(news)] if news else None}


# ------------------------------------------------------------------ verdict for factor hypotheses
def factor_verdict(gate_real: bool, share_of_move: float | None, beyond_noise: bool | None) -> str:
    """share_of_move: the factor's share of the aggregate move (0..1+). beyond_noise: whether the factor itself moved beyond its own noise band."""
    if not gate_real:
        return "inside noise"
    if beyond_noise is None or share_of_move is None:
        return "untestable"
    if not beyond_noise or abs(share_of_move) < 0.15:
        return "ruled out"
    return "consistent with"
