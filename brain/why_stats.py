"""Pure helpers for the `why` tool: comparison windows, grain periods, changepoints and shift-share. No database, no I/O."""
from __future__ import annotations

import calendar
import datetime as dt
import math
import statistics

D = dt.date


# ------------------------------------------------------------------ windows
def shift_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return D(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def compare_window(kind: str, s: dt.date, e: dt.date, cs: dt.date | None = None, ce: dt.date | None = None) -> tuple[dt.date, dt.date]:
    n = (e - s).days + 1
    if kind == "prior_period":
        ce = s - dt.timedelta(days=1)
        return ce - dt.timedelta(days=n - 1), ce
    if kind == "last_month":
        cs = shift_months(s, -1)
        return cs, cs + dt.timedelta(days=n - 1)
    if kind == "last_year":                                   # 364 days back keeps the weekday pattern
        cs = s - dt.timedelta(days=364)
        return cs, cs + dt.timedelta(days=n - 1)
    if kind == "custom":
        if not (cs and ce) or ce < cs:
            raise ValueError("custom comparison needs compare_start and compare_end (compare_end on or after compare_start)")
        return cs, ce
    raise ValueError(f"unknown compare {kind!r}; use prior_period, last_month, last_year or custom")


def grain_periods(end: dt.date, grains: set[str]) -> list[tuple[str, dt.date, dt.date, dict]]:
    """[(label, start, end, meta)] for the requested grains, Pacific days, Monday weeks.
    d:YYYY-MM-DD x35 | w:<monday> x13 complete weeks (+ the partial current week) | m:YYYY-MM full months x6 closed, mm:YYYY-MM the same day-of-month range as the month-to-date of `end`."""
    out: list[tuple[str, dt.date, dt.date, dict]] = []
    if "day" in grains:
        for i in range(34, -1, -1):
            d = end - dt.timedelta(days=i)
            out.append((f"d:{d}", d, d, {"grain": "day"}))
    if "week" in grains:
        last_sun = end if end.weekday() == 6 else end - dt.timedelta(days=end.weekday() + 1)
        for i in range(12, -1, -1):
            mon = last_sun - dt.timedelta(days=6 + 7 * i)
            out.append((f"w:{mon}", mon, mon + dt.timedelta(days=6), {"grain": "week"}))
        if end != last_sun:
            mon = last_sun + dt.timedelta(days=1)
            out.append((f"w:{mon}", mon, end, {"grain": "week", "partial": True}))
    if "month" in grains:
        first_cur = end.replace(day=1)
        month_closed_today = (end + dt.timedelta(days=1)).day == 1
        for i in range(6, -1, -1):
            ms = shift_months(first_cur, -i)
            full_end = D(ms.year, ms.month, calendar.monthrange(ms.year, ms.month)[1])
            cur = i == 0
            same_end = min(ms + dt.timedelta(days=end.day - 1), full_end)
            out.append((f"mm:{ms:%Y-%m}", ms, same_end, {"grain": "month", "same_days": True, "current": cur}))
            if not cur or month_closed_today:
                out.append((f"m:{ms:%Y-%m}", ms, full_end, {"grain": "month", "closed": True}))
    return out


# ------------------------------------------------------------------ robust stats
def median(xs):
    return statistics.median(xs)


def mad_spread(xs):
    m = median(xs)
    return 1.4826 * median([abs(x - m) for x in xs])


def _clean(series):
    return [(i, v) for i, (_, v) in enumerate(series) if v is not None and not (isinstance(v, float) and math.isnan(v))]


def out_flags(series, base_n: int, rel_floor: float, k: float = 3.0, min_base: int = 3):
    """For each point: None (no baseline), 0 (inside), +1 / -1 (above / below the trailing median by more than max(k * robust spread, rel_floor * median))."""
    flags: list[int | None] = []
    pts = [v for _, v in series]
    for i, v in enumerate(pts):
        base = [x for x in pts[max(0, i - base_n):i] if x is not None]
        if v is None or len(base) < min_base:
            flags.append(None)
            continue
        med = median(base)
        band = max(k * mad_spread(base), rel_floor * abs(med), 1e-12)
        flags.append(0 if abs(v - med) <= band else (1 if v > med else -1))
    return flags


def first_break(series, base_n: int, rel_floor: float, persist: int, k: float = 3.0):
    """Index of the first point that left its trailing baseline AND stayed out (same direction) for `persist` points, or is still out at the end of a series too short to show more. None if none."""
    flags = out_flags(series, base_n, rel_floor, k)
    n = len(flags)
    for i, f in enumerate(flags):
        if f in (1, -1):
            run = 0
            while i + run < n and flags[i + run] == f:
                run += 1
            if run >= persist:
                return i, f
    return None


def _sse(xs):
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs)


def step_fit(vals, min_seg: int):
    n = len(vals)
    if n < 2 * min_seg:
        return None
    sst = _sse(vals)
    best = None
    for k in range(min_seg, n - min_seg + 1):
        sse = _sse(vals[:k]) + _sse(vals[k:])
        if best is None or sse < best[0]:
            best = (sse, k)
    sse, k = best
    m1, m2 = sum(vals[:k]) / k, sum(vals[k:]) / (n - k)
    return {"k": k, "mean_before": m1, "mean_after": m2, "r2": (1 - sse / sst) if sst > 0 else 0.0}


def linear_fit(vals):
    n = len(vals)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(vals) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, vals))
    slope = sxy / sxx if sxx else 0.0
    sst = sum((y - my) ** 2 for y in vals)
    ssr = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, vals))
    return {"slope": slope, "r2": (1 - ssr / sst) if sst > 0 else 0.0, "mean": my}


def analyse_series(series, grain: str, rel_floor: float):
    """series = [(label, value)] chronological. Returns shape (flat | step | drift | spike | short), the break label, and a one-line summary.
    Break = the first point that left its trailing baseline (4 weeks of days, 4 weeks, 3 months) and stayed out; step fits a two-segment mean split."""
    base_n, persist, min_seg = {"day": (28, 3, 5), "week": (4, 2, 3), "month": (3, 1, 2)}[grain]
    pts = _clean(series)
    vals = [v for _, v in pts]
    res = {"shape": "short", "break": None, "break_direction": None, "step": None}
    if len(vals) < max(2 * min_seg, 6 if grain != "month" else 4):
        return res
    flags = out_flags(series, base_n, rel_floor)
    out_idx = [i for i, f in enumerate(flags) if f in (1, -1)]
    fb = first_break(series, base_n, rel_floor, persist)
    step = step_fit(vals, min_seg)
    lin = linear_fit(vals)
    mean = lin["mean"] or 1e-12
    drift_total = lin["slope"] * (len(vals) - 1) / abs(mean)
    res["step"] = None if not step else {**step, "split_label": series[pts[step["k"]][0]][0], "rel_change": (step["mean_after"] - step["mean_before"]) / abs(step["mean_before"]) if step["mean_before"] else None}
    step_ok = bool(step and step["r2"] >= 0.5 and step["r2"] >= lin["r2"] + 0.1 and step["mean_before"] and abs(step["mean_after"] / step["mean_before"] - 1) >= rel_floor)
    if fb is not None:
        res["break"] = series[fb[0]][0]
        res["break_direction"] = "up" if fb[1] > 0 else "down"
    if step_ok:
        res["shape"] = "step"
        if res["break"] is None:
            res["break"] = res["step"]["split_label"]
            res["break_direction"] = "up" if step["mean_after"] > step["mean_before"] else "down"
    elif lin["r2"] >= 0.4 and abs(drift_total) >= rel_floor:
        res["shape"] = "drift"
    elif out_idx and fb is None:
        res["shape"] = "spike"
        res["spike_labels"] = [series[i][0] for i in out_idx][:5]
    elif fb is not None:
        res["shape"] = "step"
    else:
        res["shape"] = "flat"
    return res


def weekday_like_for_like(day_vals, idx_range, weeks_back: int = 4):
    """Each day in idx_range (indexes into day_vals = [(num, den)] chronological) vs the same weekday in the `weeks_back` prior weeks (ratio of sums). Returns (cur_value, baseline_value, rel) or None."""
    cn = cd = bn = bd = 0.0
    used = 0
    for i in idx_range:
        n, d = day_vals[i]
        if n is None:
            continue
        ref = [(day_vals[i - 7 * w]) for w in range(1, weeks_back + 1) if i - 7 * w >= 0 and day_vals[i - 7 * w][0] is not None]
        if len(ref) < 2:
            continue
        cn += n
        cd += d or 0
        for rn, rd in ref:
            bn += rn / len(ref)
            bd += (rd or 0) / len(ref)
        used += 1
    if not used:
        return None
    cur = cn / cd if cd else (cn if all(day_vals[i][1] is None for i in idx_range) else None)
    base = bn / bd if bd else (bn if all(day_vals[i][1] is None for i in idx_range) else None)
    if cur is None or base in (None, 0):
        return None
    return {"cur": cur, "baseline": base, "rel": cur / base - 1, "days_used": used}


# ------------------------------------------------------------------ shift-share
def shift_share(groups: dict, num: str, den: str, scale: float = 1.0):
    """groups = {key: {"cur": {measures}, "cmp": {measures}}}. Rate r_i = scale * num_i / den_i, weight s_i = den_i / sum(den). Total rate R = sum s_i r_i exactly.
    Components (they sum to R_cur - R_cmp): rate_effect (same values both windows, their own rate change), mix_effect (continuing values: weight moved between them), entrants / exits (values present in one window only).
    Accounting, not cause."""
    tot = {w: sum((g[w].get(den) or 0) for g in groups.values()) for w in ("cur", "cmp")}
    totn = {w: sum((g[w].get(num) or 0) for g in groups.values()) for w in ("cur", "cmp")}
    if not tot["cur"] or not tot["cmp"]:
        return None
    R = {w: scale * totn[w] / tot[w] for w in ("cur", "cmp")}
    rows = {}
    comp = {"rate_effect": 0.0, "mix_effect": 0.0, "entrants": 0.0, "exits": 0.0}
    for k, g in groups.items():
        dc, dp = g["cur"].get(den) or 0, g["cmp"].get(den) or 0
        sc, sp = dc / tot["cur"], dp / tot["cmp"]
        rc = scale * (g["cur"].get(num) or 0) / dc if dc else None
        rp = scale * (g["cmp"].get(num) or 0) / dp if dp else None
        if dc and dp:
            rate, mix = sc * (rc - rp), (sc - sp) * (rp - R["cmp"])
            comp["rate_effect"] += rate
            comp["mix_effect"] += mix
            contrib, kind = rate + mix, "continuing"
        elif dc:
            contrib = sc * (rc - R["cmp"])
            comp["entrants"] += contrib
            kind = "new"
        elif dp:
            contrib = -sp * (rp - R["cmp"])
            comp["exits"] += contrib
            kind = "dropped"
        else:
            continue
        rows[k] = {"kind": kind, "share_cur": sc, "share_cmp": sp, "rate_cur": rc, "rate_cmp": rp, "contribution": contrib}
    delta = R["cur"] - R["cmp"]
    mix_total = comp["mix_effect"] + comp["entrants"] + comp["exits"]
    return {"cur": R["cur"], "cmp": R["cmp"], "delta": delta, "rate_effect": comp["rate_effect"], "mix_effect": mix_total,
            "mix_detail": {"continuing_reweighting": comp["mix_effect"], "new_values": comp["entrants"], "dropped_values": comp["exits"]},
            "sum_check": comp["rate_effect"] + mix_total - delta, "rows": rows}
