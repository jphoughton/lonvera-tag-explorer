"""Pure arithmetic for the daily story (no database, no clock): ratios, medians, z-scores, the exact log decomposition of a ratio into its terms, and the exact
mix-versus-rate split of a ratio across segments. Everything here is deterministic and unit-tested (tests/etl/test_daily_story_math.py).

Rules: a ratio is always sum / sum, never an average of daily ratios; None is "not available" and is never treated as zero; every decomposition adds back to the
total move EXACTLY (to floating point), which the tests assert.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence


def ratio(n, d):
    """n / d, or None when either is missing or d is zero."""
    if n is None or d is None:
        return None
    try:
        return None if float(d) == 0 else float(n) / float(d)
    except (TypeError, ValueError):
        return None


def pct_change(x, base):
    """x / base - 1, None when either is missing or base is zero."""
    r = ratio(x, base)
    return None if r is None else r - 1.0


def median(xs: Iterable) -> float | None:
    v = [float(x) for x in xs if x is not None]
    return statistics.median(v) if v else None


def mean(xs: Iterable) -> float | None:
    v = [float(x) for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def stdev(xs: Iterable) -> float | None:
    """Sample standard deviation; None under 2 values."""
    v = [float(x) for x in xs if x is not None]
    return statistics.stdev(v) if len(v) >= 2 else None


MIN_NOISE_DAYS = 10          # fewer trailing days than this and a z-score is not offered


def zscore(x, baseline, history: Sequence) -> float | None:
    """(x - baseline) / std(history). None when x / baseline is missing, history has fewer than MIN_NOISE_DAYS values, or the std is zero."""
    if x is None or baseline is None:
        return None
    vals = [h for h in history if h is not None]
    if len(vals) < MIN_NOISE_DAYS:
        return None
    s = stdev(vals)
    if not s:
        return None
    return (float(x) - float(baseline)) / s


def flag(z, threshold: float = 2.0) -> str | None:
    """'high' / 'low' when |z| >= threshold, else None."""
    if z is None or abs(z) < threshold:
        return None
    return "high" if z > 0 else "low"


def log_decompose(factors: Sequence[tuple], eps: float = 0.0) -> dict | None:
    """Exact log decomposition of a metric M = prod(v_i ** sign_i) between a day and its baseline.

    factors: [(name, v_day, v_base, sign)], sign +1 (the metric rises with v) or -1 (it falls with v). Returns None when any value is missing or not positive
    (the identity needs logs). Otherwise {'total_log', 'pct_change', 'terms': [{'name', 'day', 'base', 'sign', 'log', 'pct_points', 'share'}]} where
    sum(log) == total_log == ln(M_day / M_base) and sum(pct_points) == pct_change (the total percentage move is split in proportion to each term's log)."""
    terms, total = [], 0.0
    for name, vd, vb, sign in factors:
        if vd is None or vb is None or vd <= eps or vb <= eps:
            return None
        lg = sign * (math.log(vd) - math.log(vb))
        total += lg
        terms.append({"name": name, "day": vd, "base": vb, "sign": sign, "log": lg})
    pct = math.exp(total) - 1.0
    for t in terms:
        t["pct_points"] = (pct * t["log"] / total) if total else 0.0
        t["share"] = (t["log"] / total) if total else None
    return {"total_log": total, "pct_change": pct, "terms": terms}


def mix_rate(day: dict, base: dict) -> dict | None:
    """Exact mix-versus-rate split of a spend-weighted ratio (value / spend) across segments.

    day / base: {segment: (spend, value)}. R = sum(value)/sum(spend) = sum_i w_i * r_i with w_i the segment's spend share and r_i = value_i / spend_i.
    With R_bar the mean of the two totals, w_bar_i / r_bar_i the segment's mean share / rate:
        mix_i  = (w_i - w0_i) * (r_bar_i - R_bar)    spend moved into (+) or out of (-) a segment that earns more than average
        rate_i = (r_i - r0_i) * w_bar_i              the segment earning more (+) or less (-) per dollar, weighted by its mean share
    sum_i (mix_i + rate_i) == R - R0 exactly. A segment with no spend on one side takes the other side's rate there (so it has no rate effect on that side).
    Returns None when either side has no spend."""
    sd = sum(v[0] for v in day.values())
    sb = sum(v[0] for v in base.values())
    if sd <= 0 or sb <= 0:
        return None
    vd = sum(v[1] for v in day.values())
    vb = sum(v[1] for v in base.values())
    R, R0 = vd / sd, vb / sb
    Rbar = (R + R0) / 2
    rows, mix_t, rate_t = [], 0.0, 0.0
    for k in sorted(set(day) | set(base)):
        s1, v1 = day.get(k, (0.0, 0.0))
        s0, v0 = base.get(k, (0.0, 0.0))
        w1, w0 = s1 / sd, s0 / sb
        r1 = v1 / s1 if s1 > 0 else None
        r0 = v0 / s0 if s0 > 0 else None
        if r1 is None:
            r1 = r0
        if r0 is None:
            r0 = r1
        if r1 is None:                      # no spend on either side
            continue
        rbar = (r1 + r0) / 2
        mix = (w1 - w0) * (rbar - Rbar)
        rate = (r1 - r0) * (w1 + w0) / 2
        mix_t += mix
        rate_t += rate
        rows.append({"key": k, "spend": s1, "base_spend": s0, "share": w1, "base_share": w0, "rate": r1 if s1 > 0 else None, "base_rate": r0 if s0 > 0 else None,
                     "mix": mix, "rate_effect": rate, "total": mix + rate})
    return {"value": R, "base": R0, "change": R - R0, "mix": mix_t, "rate": rate_t, "rows": rows}


def top_by_abs(rows: list[dict], key: str, n: int) -> list[dict]:
    return sorted(rows, key=lambda r: -abs(r[key]))[:n]


def bucket(x: float, edges: Sequence[float], labels: Sequence[str]) -> str:
    """labels[i] for the first edge x is below; the last label otherwise (len(labels) == len(edges) + 1)."""
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]
