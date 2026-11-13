"""What changed inside the Meta breakdowns: the pure arithmetic of the `why` breakdown step (no database, no clock).

For a target window against a comparison window, one breakdown dimension at a time (placement, device, platform, age, gender, country ...), an aggregate ratio

    R = sum(numerator) / sum(denominator)            e.g. CPM = spend / impressions * 1000, link click -> add to cart = add to carts / link clicks

is the weighted mean of its segments' own ratios r_i with weights w_i = denominator_i / sum(denominator). Its change splits EXACTLY (no residual) into

    mix effect_i  = (w1_i - w0_i) * ((r0_i + r1_i) / 2 - (R0 + R1) / 2)     share of the denominator moving between segments, valued against the average level
    rate effect_i = (r1_i - r0_i) * (w0_i + w1_i) / 2                      the segment's own ratio moving, at its average weight

because w1 r1 - w0 r0 = (w1 - w0) (r0 + r1) / 2 + (r1 - r0) (w0 + w1) / 2 for every segment and the weights of each window sum to 1 (so subtracting a constant from the mix
term changes nothing). A segment missing from one window (new, or gone) takes the other window's ratio on the empty side, so its whole contribution is mix.

A mover is only reported as "moved beyond noise" when the target window's own ratio sits outside the segment's normal range against BOTH controls (the same weekdays of the
four prior weeks, median, or for a window of 7+ days the adjacent windows of the same length; the seven days before the window, pooled) on the side the move went; the normal range is two standard deviations of the segment's own daily ratio over
the 28 days before the window, divided by sqrt(days in the window). Rates only, ratios from sums (never an average of daily ratios); the standard deviation is the one place a
daily ratio is used, and only to size the noise. Nothing here says "caused": the words are "moved beyond noise" / "inside noise" / "not enough history".
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

MIN_SPEND_SHARE = 0.03          # a segment below this share of spend (mean of the two windows) is not material
MIN_MOVE_SHARE = 0.10           # ... and its contribution must be at least this share of the aggregate move, or
ABS_FLOOR_RATE = 0.01           # ... at least one percentage point of the metric (ratio metrics), or
ABS_FLOOR_REL = 0.02            # ... 2% of the comparison level for a dollar metric
Z_BEYOND = 2.0                  # standard deviations of the window-level noise
NOISE_DAYS = 28                 # history for the segment's own daily noise
MIN_NOISE_DAYS = 14             # fewer valid days than this = not enough history
WEEKS = 4                       # same-weekday controls


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    num: str
    den: str
    scale: float
    unit: str                    # "usd" | "rate"
    basis: str


METRICS: tuple[Metric, ...] = (
    Metric("cpm", "CPM (cost per 1,000 impressions)", "spend", "impressions", 1000.0, "usd", "spend / impressions"),
    Metric("ctr", "link click rate (link clicks / impressions)", "link_clicks", "impressions", 1.0, "rate", "link clicks / impressions"),
    Metric("cost_per_link_click", "cost per link click", "spend", "link_clicks", 1.0, "usd", "spend / link clicks"),
    Metric("link_to_atc", "link click to add to cart", "atc", "link_clicks", 1.0, "rate", "adds to cart / link clicks"),
    Metric("atc_to_checkout", "add to cart to initiated checkout", "ic", "atc", 1.0, "rate", "initiated checkouts / adds to cart"),
    Metric("checkout_to_purchase", "initiated checkout to purchase", "purchases", "ic", 1.0, "rate", "purchases / initiated checkouts"),
    Metric("atc_to_purchase", "add to cart to purchase", "purchases", "atc", 1.0, "rate", "purchases / adds to cart"),
    Metric("cost_per_purchase", "cost per purchase (Meta-reported)", "spend", "purchases", 1.0, "usd", "spend / Meta-reported purchases"),
)
SPEND_SHARE = Metric("spend_share", "share of spend", "seg_spend", "total_spend", 1.0, "rate", "segment spend / total spend")
FUNNEL_KEYS = ("atc", "ic")
BY_ID = {m.id: m for m in METRICS + (SPEND_SHARE,)}


# ------------------------------------------------------------------------------------------------------------------ the exact split
def shift_share(segs: dict[str, tuple[float, float, float, float]], scale: float = 1.0) -> dict | None:
    """segs: segment -> (num0, den0, num1, den1). Returns the aggregate ratios and, per segment, weights, ratios, mix effect and rate effect (all multiplied by `scale`), such that
    sum(mix) + sum(rate) == (R1 - R0) * scale to rounding. None when a window has no denominator at all."""
    d0, d1 = sum(v[1] for v in segs.values()), sum(v[3] for v in segs.values())
    if d0 <= 0 or d1 <= 0:
        return None
    n0, n1 = sum(v[0] for v in segs.values()), sum(v[2] for v in segs.values())
    r0_all, r1_all = n0 / d0, n1 / d1
    rbar = (r0_all + r1_all) / 2
    rows = {}
    for k, (a0, b0, a1, b1) in segs.items():
        w0, w1 = b0 / d0, b1 / d1
        r0 = a0 / b0 if b0 > 0 else None
        r1 = a1 / b1 if b1 > 0 else None
        if r0 is None and r1 is None:
            continue
        new, gone = r0 is None, r1 is None
        if r0 is None:
            r0 = r1
        if r1 is None:
            r1 = r0
        mix = (w1 - w0) * ((r0 + r1) / 2 - rbar) * scale
        rate = (r1 - r0) * (w0 + w1) / 2 * scale
        rows[k] = {"w0": w0, "w1": w1, "r0": r0 * scale, "r1": r1 * scale, "mix": mix, "rate": rate, "contribution": mix + rate, "new": new, "gone": gone}
    return {"R0": r0_all * scale, "R1": r1_all * scale, "delta": (r1_all - r0_all) * scale, "segments": rows,
            "mix_total": sum(r["mix"] for r in rows.values()), "rate_total": sum(r["rate"] for r in rows.values())}


# ------------------------------------------------------------------------------------------------------------------ controls and noise
def _pool(num: dict, den: dict, d0: dt.date, d1: dt.date, bad: frozenset | set = frozenset(), avail: frozenset | set | None = None) -> tuple[float, float] | None:
    """Sum of numerator and denominator over [d0, d1]; None when any day of the span is a missing-funnel day or (when `avail` is given) a day the breakdown was not loaded on: a ratio built
    on a partial window would compare unlike things."""
    n = d = 0.0
    day = d0
    while day <= d1:
        if day in bad or (avail is not None and day not in avail):
            return None
        n += num.get(day, 0.0)
        d += den.get(day, 0.0)
        day += dt.timedelta(days=1)
    return n, d


def _ratio(p: tuple[float, float] | None, scale: float) -> float | None:
    return None if p is None or p[1] <= 0 else p[0] / p[1] * scale


def controls(num: dict[dt.date, float], den: dict[dt.date, float], start: dt.date, end: dt.date, scale: float = 1.0, bad=frozenset(), avail=None) -> dict:
    """The window's own ratio and its two controls plus the noise. Weekday control: the median over the prior four windows of the same length shifted back by 7, 14, 21, 28 days (a
    one-day window: the same weekday of the prior four weeks; at least 3 complete windows needed). A window of 7 days or more already contains every weekday, so its prior windows are
    the adjacent ones (shifted by its own length; at least 2 complete windows needed). Prior-7 control: the pooled ratio of the seven days before the window. Noise: the standard
    deviation of the daily ratio over the 28 days before the window. Windows that include a day the breakdown was not loaded on (`avail`) or a missing-funnel day are skipped."""
    nd = (end - start).days + 1
    cur = _ratio(_pool(num, den, start, end, bad, avail), scale)
    step, need = (7, 3) if nd < 7 else (nd, 2)
    wk = [_ratio(_pool(num, den, start - dt.timedelta(days=step * k), end - dt.timedelta(days=step * k), bad, avail), scale) for k in range(1, WEEKS + 1)]
    wk = [x for x in wk if x is not None]
    wd = statistics.median(wk) if len(wk) >= need else None
    p7 = _ratio(_pool(num, den, start - dt.timedelta(days=7), start - dt.timedelta(days=1), bad, avail), scale)
    daily = []
    day = start - dt.timedelta(days=NOISE_DAYS)
    while day < start:
        if day not in bad and (avail is None or day in avail) and den.get(day, 0.0) > 0:
            daily.append(num.get(day, 0.0) / den[day] * scale)
        day += dt.timedelta(days=1)
    sd = statistics.stdev(daily) if len(daily) >= MIN_NOISE_DAYS else None
    noise = None if sd is None else sd / math.sqrt(nd)
    return {"cur": cur, "weekday_median": wd, "weekday_windows": len(wk), "prior7": p7, "daily_sd": sd, "noise": noise, "noise_days": len(daily), "days": nd}


def verdict(cur: float | None, cmp_: float | None, c: dict, z: float = Z_BEYOND) -> tuple[str, dict]:
    """'moved beyond noise' when `cur` is outside noise x z of BOTH controls, on the side the move versus `cmp_` went; 'inside noise' otherwise; 'not enough history' when a control or the
    noise could not be built. Returns the verdict and the z scores."""
    wd, p7, nz = c.get("weekday_median"), c.get("prior7"), c.get("noise")
    if cur is None or cmp_ is None or wd is None or p7 is None or nz is None:
        return "not enough history", {}
    direction = cur - cmp_
    if direction == 0:
        return "inside noise", {"z_weekday": 0.0, "z_prior7": 0.0}
    floor = max(nz, 1e-12)
    zs = {"z_weekday": (cur - wd) / floor, "z_prior7": (cur - p7) / floor}
    sign = 1 if direction > 0 else -1
    beyond = all(sign * v >= z for v in zs.values())
    return ("moved beyond noise" if beyond else "inside noise"), {k: round(v, 2) for k, v in zs.items()}


# ------------------------------------------------------------------------------------------------------------------ one dimension
def _daily(rows: list[dict]) -> tuple[dict[str, dict[dt.date, dict]], set[dt.date]]:
    """rows (day, seg, spend, impressions, link_clicks, atc, ic, purchases, funnel_ok) -> segment -> day -> values; the days on which any row has an unfilled funnel."""
    segs: dict[str, dict[dt.date, dict]] = {}
    bad: set[dt.date] = set()
    for r in rows:
        v = {k: float(r.get(k) or 0) for k in ("spend", "impressions", "link_clicks", "purchases")}
        ok = r.get("funnel_ok", True)
        v["atc"], v["ic"] = (float(r.get("atc") or 0), float(r.get("ic") or 0)) if ok else (0.0, 0.0)
        if not ok:
            bad.add(r["day"])
        segs.setdefault(str(r["seg"]), {})[r["day"]] = v
    return segs, bad


def _series(segs: dict, seg_keys, key: str) -> dict[dt.date, float]:
    out: dict[dt.date, float] = {}
    for k in seg_keys:
        for d, v in segs[k].items():
            out[d] = out.get(d, 0.0) + v[key]
    return out


def _fmt(m: Metric, v: float | None) -> str | None:
    if v is None:
        return None
    return f"${v:,.2f}" if m.unit == "usd" else f"{v * 100:.2f}%"


def analyze_dimension(dim: str, rows: list[dict], start: dt.date, end: dt.date, cstart: dt.date, cend: dt.date, metrics=METRICS) -> dict:
    """The whole breakdown step for one dimension: aggregate move, exact mix / within-segment split, material segments, controls and verdicts for every metric, plus the spend-share shifts."""
    segs, bad = _daily(rows)
    keys = list(segs)
    spend = {k: _series(segs, [k], "spend") for k in keys}
    total_spend = _series(segs, keys, "spend")
    avail = frozenset(d for d, v in total_spend.items() if v > 0)
    out: dict = {"dimension": dim, "segments": len(keys), "gaps": [], "metrics": {}, "tests": 0}

    def window_sum(k, key, a, b):
        return sum(v[key] for d, v in segs[k].items() if a <= d <= b)

    def window_bad(a, b):
        return sorted(d for d in bad if a <= d <= b)

    share_cur = {k: sum(v for d, v in spend[k].items() if start <= d <= end) for k in keys}
    share_cmp = {k: sum(v for d, v in spend[k].items() if cstart <= d <= cend) for k in keys}
    tc, tm = sum(share_cur.values()), sum(share_cmp.values())
    if tc <= 0 or tm <= 0:
        out["gaps"].append(f"{dim}: no spend in the {'target' if tc <= 0 else 'comparison'} window (breakdown not loaded for those days)")
        return out
    avg_share = {k: (share_cur[k] / tc + share_cmp[k] / tm) / 2 for k in keys}

    # spend share: the weights themselves
    out["spend_share"] = _spend_share_block(dim, segs, keys, spend, total_spend, start, end, cstart, cend, avg_share, tc, tm, out)

    for m in metrics:
        funnel_bad = window_bad(start, end) + window_bad(cstart, cend) if (m.num in FUNNEL_KEYS or m.den in FUNNEL_KEYS) else []
        if funnel_bad:
            out["gaps"].append(f"{dim} / {m.id}: add to cart / checkout not filled on {len(funnel_bad)} day(s) of the windows (first {funnel_bad[0]}); metric skipped")
            continue
        seg_in = {k: (window_sum(k, m.num, cstart, cend), window_sum(k, m.den, cstart, cend), window_sum(k, m.num, start, end), window_sum(k, m.den, start, end)) for k in keys}
        ss = shift_share(seg_in, m.scale)
        if ss is None:
            out["gaps"].append(f"{dim} / {m.id}: no {m.den} in one of the windows")
            continue
        badset = frozenset(bad) if (m.num in FUNNEL_KEYS or m.den in FUNNEL_KEYS) else frozenset()
        num_all, den_all = _series(segs, keys, m.num), _series(segs, keys, m.den)
        agg_c = controls(num_all, den_all, start, end, m.scale, badset, avail)
        agg_v, agg_z = verdict(ss["R1"], ss["R0"], agg_c)
        block = {"label": m.label, "basis": m.basis, "unit": m.unit, "cmp": ss["R0"], "cur": ss["R1"], "delta": ss["delta"], "rel": (ss["delta"] / ss["R0"]) if ss["R0"] else None,
                 "display": f"{_fmt(m, ss['R0'])} -> {_fmt(m, ss['R1'])}",
                 "aggregate": {"weekday_median": agg_c["weekday_median"], "prior7": agg_c["prior7"], "daily_sd": agg_c["daily_sd"], "noise": agg_c["noise"], "verdict": agg_v, **agg_z},
                 "mix_total": ss["mix_total"], "within_segment_total": ss["rate_total"], "exact_check": ss["mix_total"] + ss["rate_total"] - ss["delta"], "movers": []}
        denom_move = max(abs(ss["delta"]), 0.01 * abs(ss["R0"]), 1e-12)          # a flat aggregate must not make every wiggle "100% of the move"
        floor = ABS_FLOOR_RATE if m.unit == "rate" else ABS_FLOOR_REL * abs(ss["R0"])
        sign = 1 if ss["delta"] >= 0 else -1
        same_dir_spend = 0.0
        for k, r in ss["segments"].items():
            if avg_share[k] < MIN_SPEND_SHARE:
                continue
            if r["rate"] * sign > 0:
                same_dir_spend += avg_share[k]
            c = controls(_series(segs, [k], m.num), _series(segs, [k], m.den), start, end, m.scale, badset, avail)
            out["tests"] += 1
            v, z = verdict(r["r1"], r["r0"], c)
            material = abs(r["contribution"]) >= MIN_MOVE_SHARE * denom_move or abs(r["contribution"]) >= floor
            if not material:
                continue
            block["movers"].append({"segment": k, "spend_share_cmp": share_cmp[k] / tm, "spend_share_cur": share_cur[k] / tc, "rate_cmp": r["r0"], "rate_cur": r["r1"],
                                    "rate_display": f"{_fmt(m, r['r0'])} -> {_fmt(m, r['r1'])}", "weight_cmp": r["w0"], "weight_cur": r["w1"], "mix_effect": r["mix"], "within_segment_effect": r["rate"],
                                    "contribution": r["contribution"], "share_of_aggregate_move": r["contribution"] / denom_move, "spend_share_avg": avg_share[k],
                                    "concentration": (r["contribution"] / denom_move) / avg_share[k], "new_or_gone": "new" if r["new"] else ("gone" if r["gone"] else None),
                                    "weekday_median": c["weekday_median"], "prior7": c["prior7"], "daily_sd": c["daily_sd"], "noise_2sd": None if c["noise"] is None else 2 * c["noise"],
                                    "normal_range": None if c["weekday_median"] is None or c["noise"] is None else [c["weekday_median"] - 2 * c["noise"], c["weekday_median"] + 2 * c["noise"]],
                                    "verdict": v, **z, "metric": m.id, "dimension": dim})
        block["movers"].sort(key=lambda x: -abs(x["contribution"]))
        block["breadth"] = {"share_of_spend_moving_with_the_aggregate": same_dir_spend, "text": f"segments carrying {same_dir_spend * 100:.0f}% of spend moved in the aggregate's direction within segment"}
        out["metrics"][m.id] = block
    return out


def _spend_share_block(dim, segs, keys, spend, total_spend, start, end, cstart, cend, avg_share, tc, tm, out) -> list[dict]:
    res = []
    for k in keys:
        if avg_share[k] < MIN_SPEND_SHARE:
            continue
        s_cur, s_cmp = sum(v for d, v in spend[k].items() if start <= d <= end) / tc, sum(v for d, v in spend[k].items() if cstart <= d <= cend) / tm
        c = controls(spend[k], total_spend, start, end, 1.0, frozenset(), frozenset(d for d, v in total_spend.items() if v > 0))
        out["tests"] += 1
        v, z = verdict(c["cur"], s_cmp, c)
        res.append({"segment": k, "share_cmp": s_cmp, "share_cur": s_cur, "delta_pp": (s_cur - s_cmp) * 100, "weekday_median": c["weekday_median"], "prior7": c["prior7"], "daily_sd": c["daily_sd"],
                    "noise_2sd": None if c["noise"] is None else 2 * c["noise"], "verdict": v, **z, "metric": "spend_share", "dimension": dim})
    res.sort(key=lambda x: -abs(x["delta_pp"]))
    return [x for x in res if abs(x["delta_pp"]) >= 1.0][:6]


# ------------------------------------------------------------------------------------------------------------------ across dimensions
def _excess(x: dict) -> float:
    """How far a segment's share of the aggregate move runs ahead of its own share of spend (a move that every segment shares equally has excess 0 for all of them)."""
    if x.get("share_of_aggregate_move") is None:
        return abs(x["delta_pp"]) / 10          # spend-share shifts: 10 points of share rank like a segment carrying 100% of a move on 0% of spend
    return abs(x["share_of_aggregate_move"] - x["spend_share_avg"])


def rank_movers(per_dim: list[dict]) -> list[dict]:
    """Every material segment move of every dimension and metric, beyond-noise first, then by its share of the aggregate move; spend-share shifts ride along with their own verdicts."""
    allm = []
    for d in per_dim:
        for mb in d.get("metrics", {}).values():
            allm.extend(mb["movers"])
        allm.extend({**s, "share_of_aggregate_move": None, "contribution": s["delta_pp"] / 100} for s in d.get("spend_share", []))
    order = {"moved beyond noise": 0, "inside noise": 1, "not enough history": 2}
    allm.sort(key=lambda x: (order.get(x["verdict"], 3), -_excess(x)))
    return allm


def describe_mover(x: dict) -> str:
    m = BY_ID[x["metric"]]
    if x["metric"] == "spend_share":
        what = f"{x['dimension']} '{x['segment']}' spend share {x['share_cmp'] * 100:.1f}% -> {x['share_cur'] * 100:.1f}% ({x['delta_pp']:+.1f} points)"
    else:
        sh = x.get("share_of_aggregate_move")
        what = (f"{x['dimension']} '{x['segment']}' {m.label} {x['rate_display']}, contribution {x['contribution'] * (100 if m.unit == 'rate' else 1):+.3f}"
                f"{' points' if m.unit == 'rate' else ' dollars'} = {sh * 100:.0f}% of the aggregate move on {x['spend_share_avg'] * 100:.0f}% of spend (mix {x['mix_effect'] * (100 if m.unit == 'rate' else 1):+.3f}, within-segment "
                f"{x['within_segment_effect'] * (100 if m.unit == 'rate' else 1):+.3f})")
    band = ""
    if x.get("noise_2sd") is not None and x["metric"] != "spend_share":
        band = f"; normal range {_fmt(m, x['normal_range'][0])} to {_fmt(m, x['normal_range'][1])}"
    elif x.get("noise_2sd") is not None:
        band = f"; normal +/-{x['noise_2sd'] * 100:.1f} points"
    return f"{what}{band}: {x['verdict']}"


def headline(per_dim: list[dict], ranked: list[dict], window: str, n_cmp: int | None = None) -> str:
    beyond = [x for x in ranked if x["verdict"] == "moved beyond noise"]
    tests = sum(d.get("tests", 0) for d in per_dim)
    if not per_dim or all(not d.get("metrics") for d in per_dim):
        return f"Breakdowns could not be compared for {window}: no loaded days in one of the windows."
    if not beyond:
        agg = [(d["dimension"], mid, mb) for d in per_dim for mid, mb in d.get("metrics", {}).items() if mb["aggregate"]["verdict"] == "moved beyond noise"]
        tail = ""
        if agg:
            seen = {}
            for dn, mid, mb in agg:
                seen.setdefault(mid, mb)
            tail = " The account-level " + ", ".join(f"{mb['label']} ({mb['display']})" for mb in seen.values()) + " did move beyond noise, but spread across segments rather than concentrated in one."
        return (f"No single breakdown segment moved beyond its normal day-to-day noise against both controls in {window} ({tests} segment-level comparisons across "
                f"{len(per_dim)} dimensions).{tail}")
    top = beyond[:3]
    conc = [x for x in beyond if x.get("concentration") is not None and x["concentration"] >= 1.5 and x["spend_share_avg"] <= 0.5]
    agg = {}
    for d in per_dim:
        for mid, mb in d.get("metrics", {}).items():
            if mb["aggregate"]["verdict"] == "moved beyond noise":
                agg.setdefault(mid, mb)
    shape = ("Concentrated: " + " | ".join(describe_mover(x) for x in conc[:3])) if conc else ("None is concentrated (every beyond-noise segment carries its move in proportion to its spend): the move looks "
                                                                                           "broad-based across segments rather than located in one")
    return (f"{len(beyond)} segment-level move(s) beat their own noise against both controls in {window} (of {tests} comparisons; about {tests * 0.0455:.0f} would pass one 2-sigma test by chance alone). "
            + (("Account-level moves beyond noise: " + ", ".join(f"{mb['label']} {mb['display']}" for mb in agg.values()) + ". ") if agg else "")
            + shape + ". Largest by contribution: " + " | ".join(describe_mover(x) for x in top) + ". Consistent with a change in or around those segments, not proof of cause.")
