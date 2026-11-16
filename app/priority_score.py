"""Creative priority score: one number per cut (0 to 100) that answers "should we make more creative for this cut now?". Spec, weights and backtest: docs/PRIORITY_SCORE.md.

PURE: no database, no clock, no randomness. Input = one signal dict per cut (built by app/priority_signals.py, or by hand in tests); output = the same cuts with a score, a tier, the
drivers that produced the score, a confidence and flags. Every cut is scored from ITS OWN rolled-up sums (never an average of child scores), so the function works the same at every
level (ICP, ICP + pain point, market cluster, cluster + ICP + pain point, with or without vendor / media).

Signal dict (one per cut). Money in dollars, NC = Upstack new customers (first orders, any-click 30 days), spend is Meta's. Missing numeric keys read as 0, missing optional keys as None.
  labels                 {dim: text}            the cut, e.g. {"icp": "Weekend Face", "pain_point": "Alcohol Face"}; used only for the tie-break and the output
  spend_30d, nc_revenue_30d, nc_purchases_30d     last 30 days ending the as-of day
  spend_prior30, nc_revenue_prior30               days 31 to 60 before the as-of day
  spend_7d, nc_revenue_7d                         last 7 days (shown, not scored)
  expected_nc_roas_30d   float | None           the NC ROAS this cut's PRODUCT MIX would get: sum over products of (cut spend in product x product NC ROAS) / cut spend, last 30 days;
                                                when the cut has no 30-day spend, the scope's blended NC ROAS. The economics prior.
  expected_nc_roas_prior30  float | None        the same for days 31 to 60 (momentum is read as an index so a market-wide dip does not look like the cut fading)
  blended_nc_roas_30d    float | None           NC ROAS of everything in scope, last 30 days (shown only)
  judged_ads, hit_ads    int                    hit rate (the app's SCORING definition, etl/creative_priorities.py): ads whose first spend day is 14 to 180 days before the as-of day
                                                (mature), that spent at least JUDGED_SPEND ($100) by the as-of day; a hit reached HIT_SPEND ($1,000) by the as-of day
  expected_hit_rate      float | None           the product-mix hit rate (same definition, all ads of the products in scope, weighted by the cut's judged ads per product)
  live_ads               int                    unique ads (tracker families) that spent on either of the last two loaded days
  launches_60d           int                    ad families whose first spend day is in the last 60 days
  days_since_launch      int | None             days from the newest ad family's first spend day to the as-of day (None = no ad ever spent)
  spend_share_30d        float                  cut spend / scope spend, last 30 days
  launch_share_60d       float                  cut launches / scope launches, last 60 days
  top_ad_share_30d       float | None           the biggest ad's share of the cut's 30-day spend
  old_spend_share_30d    float | None           share of the cut's 30-day spend on ads whose first spend day is more than 60 days back (incl. ads running before data starts)
  pipeline_open          int                    production tickets assigned .. ready (not launched, not dropped)
  oldest_ticket_days     int | None             age of the oldest open ticket (shown only)
  tagged_ads             int                    tracker families tagged to the cut, with or without spend (shown only)
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------------------------------- reused from etl/creative_priorities.py (same names, same values)
JUDGED_SPEND = 100.0          # an ad counts as tested once it spent this much
HIT_SPEND = 1000.0            # scoring hit: an ad that reached this spend
MATURE_DAYS = 14              # ads younger than this are left out of the hit rate
SHRINK_SPEND = 2500.0         # ROAS shrinks toward its prior with this many dollars of prior spend
SHRINK_ADS = 4.0              # hit rate shrinks toward its prior with this many prior ads
MIN_RECENT_SPEND = 1000.0     # a 30-day window's ROAS is trusted only above this spend
HIT_FLOOR, HIT_SPAN = 0.75, 1.25   # hit-rate component = clamp((hit-rate index - 0.75) / 1.25)

# ---------------------------------------------------------------------------------------------------- this score's own constants (docs/PRIORITY_SCORE.md)
DEFAULT_CONFIG = {
    "weights": {"economics": 0.10, "hit_rate": 0.40, "supply_gap": 0.25, "scale": 0.25, "momentum": 0.0},   # backtest 2026-11-15: momentum and past ROAS did not predict new-ad results (docs/PRIORITY_SCORE.md)
    "econ_floor": 0.80, "econ_span": 0.50,       # economics = clamp((shrunk NC ROAS index - 0.80) / 0.50): index 0.80 -> 0, 1.30 -> 1
    "scale_min": 1000.0, "scale_full": 50000.0,  # scale = log position of 30-day spend between $1,000 (0) and $50,000 (1)
    "evidence_spend": 2500.0,                    # supply gap counts in proportion to spend / (spend + this): a $300 cut cannot claim a supply gap
    "stale_days": 45,                            # supply gap: days since the last launch at which staleness is full
    "pipeline_cover": 0.5,                       # score x (1 - 0.5 x coverage); coverage = open tickets / max(3, launches in the last 60 days / 2)
    "pipeline_min_need": 3,
    "tiers": ((55, "Make more now"), (45, "Make more"), (35, "Keep steady"), (0, "Low priority")),   # set on the score spread of 2026-11-14 (top ICP 60, median tested ICP about 30)
    "low_conf_max_tier": "Keep steady",          # a low-confidence cut (under $1,000 in 30 days or under 3 tested ads) never reads Make more, whatever its score
    "untested_label": "Untested",
    "loser_spend": 10000.0, "loser_index": 0.85, "loser_cap": 44.0,   # guard: 30-day spend >= $10,000 and shrunk NC ROAS index < 0.85 -> score capped at 44 (never above Keep steady)
}

SIGNAL_TEXT = {"loser_cap": "Capped: losing money at scale", "economics": "New-customer return", "hit_rate": "Hit rate", "supply_gap": "Supply gap", "scale": "Scale", "momentum": "Momentum", "pipeline": "Already in the pipeline"}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _n(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _opt(x):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def shrunk_roas(revenue: float, spend: float, prior: float, k: float = SHRINK_SPEND) -> float:
    """ROAS pulled toward the prior by k dollars of pretend spend at the prior's ROAS (empirical-Bayes style): $300 of spend barely moves it, $25,000 mostly speaks for itself."""
    return (revenue + k * prior) / (spend + k)


def shrunk_rate(hits: float, n: float, prior: float, k: float = SHRINK_ADS) -> float:
    return (hits + k * prior) / (n + k)


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def components(s: dict, cfg: dict) -> dict:
    """Each component on 0..1 with the raw numbers behind it. Never raises on missing data."""
    spend30, rev30 = _n(s.get("spend_30d")), _n(s.get("nc_revenue_30d"))
    sp_p, rv_p = _n(s.get("spend_prior30")), _n(s.get("nc_revenue_prior30"))
    exp30 = _opt(s.get("expected_nc_roas_30d")) or _opt(s.get("blended_nc_roas_30d"))
    exp_p = _opt(s.get("expected_nc_roas_prior30")) or exp30

    # economics: shrunk NC ROAS as an index to what the cut's product mix gets anyway. No spend -> index 1.0 exactly (the prior), which maps to a middling 0.4.
    if exp30:
        idx = shrunk_roas(rev30, spend30, exp30) / exp30
        econ = clamp((idx - cfg["econ_floor"]) / cfg["econ_span"])
    else:
        idx, econ = None, 0.0

    # hit rate: shrunk toward the product-mix rate with SHRINK_ADS pretend ads
    judged, hits = _n(s.get("judged_ads")), _n(s.get("hit_ads"))
    exp_hr = _opt(s.get("expected_hit_rate"))
    if exp_hr:
        hidx = shrunk_rate(hits, judged, exp_hr) / exp_hr
        hit = clamp((hidx - HIT_FLOOR) / HIT_SPAN)
    else:
        hidx, hit = None, 0.0

    # supply gap: the cut carries spend but gets few new ads (launch share under spend share) or none lately. Weighted by evidence so tiny cuts cannot claim it.
    ss, ls = _n(s.get("spend_share_30d")), _n(s.get("launch_share_60d"))
    under = clamp(1.0 - ls / ss) if ss > 0 else 0.0
    dsl = s.get("days_since_launch")
    stale = clamp(_n(dsl) / cfg["stale_days"]) if dsl is not None else 1.0
    w_ev = spend30 / (spend30 + cfg["evidence_spend"])
    gap = w_ev * (0.6 * under + 0.4 * stale) if spend30 > 0 else 0.0

    # scale: how much the cut already carries (it can absorb new ads at that level)
    scale = clamp(math.log10(max(spend30, cfg["scale_min"]) / cfg["scale_min"]) / math.log10(cfg["scale_full"] / cfg["scale_min"]))

    # momentum: last 30 days vs the 30 before, each as an index to its own product-mix expectation, only when both windows have MIN_RECENT_SPEND; else neutral 0.5
    mom, mom_ratio = 0.5, None
    if spend30 >= MIN_RECENT_SPEND and sp_p >= MIN_RECENT_SPEND and exp30 and exp_p and rv_p > 0:
        mom_ratio = (rev30 / spend30 / exp30) / (rv_p / sp_p / exp_p)
        mom = clamp(0.5 + (mom_ratio - 1.0))

    # pipeline: tickets already open against the cut's own launch pace
    open_ = _n(s.get("pipeline_open"))
    need = max(float(cfg["pipeline_min_need"]), _n(s.get("launches_60d")) / 2.0)
    coverage = clamp(open_ / need)
    return {"economics": econ, "hit_rate": hit, "supply_gap": gap, "scale": scale, "momentum": mom, "coverage": coverage,
            "raw": {"idx": idx, "roas30": (rev30 / spend30) if spend30 else None, "exp30": exp30, "hidx": hidx, "hit_rate": (hits / judged) if judged else None, "exp_hr": exp_hr,
                    "judged": judged, "hits": hits, "under": under, "stale": stale, "dsl": dsl, "spend30": spend30, "mom_ratio": mom_ratio, "open": open_, "need": need,
                    "spend_share": ss, "launch_share": ls}}


def _text(sig: str, c: dict) -> str:
    r = c["raw"]
    if sig == "economics":
        if r["roas30"] is None:
            return "No spend in the last 30 days, so return reads as average (no evidence either way)"
        return f"New-customer ROAS {r['roas30']:.2f}x on {_usd(r['spend30'])} in 30 days vs {r['exp30']:.2f}x for its product mix (index {r['idx']:.2f} after shrinking)"
    if sig == "hit_rate":
        if not r["judged"]:
            return "No mature tested ads, so hit rate reads as average"
        return f"{r['hits']:.0f} of {r['judged']:.0f} tested ads reached {_usd(HIT_SPEND)} ({_pct(r['hit_rate'])}) vs {_pct(r['exp_hr'] or 0)} for its product mix"
    if sig == "supply_gap":
        last = "never launched" if r["dsl"] is None else f"last new ad {int(r['dsl'])} days ago"
        return f"Carries {_pct(r['spend_share'])} of spend but got {_pct(r['launch_share'])} of new ads in 60 days; {last}"
    if sig == "scale":
        return f"{_usd(r['spend30'])} spent in the last 30 days"
    if sig == "momentum":
        if r["mom_ratio"] is None:
            return "Not enough spend in both 30-day windows to read a trend"
        return f"Return vs its product mix is {r['mom_ratio']:.2f}x the prior 30 days"
    if sig == "pipeline":
        return f"{r['open']:.0f} concepts already in production against about {r['need']:.0f} needed"
    return ""


def confidence(s: dict) -> str:
    spend30, judged = _n(s.get("spend_30d")), _n(s.get("judged_ads"))
    if spend30 >= 5000 and judged >= 8:
        return "high"
    if spend30 >= 1000 and judged >= 3:
        return "medium"
    return "low"


def score_one(s: dict, config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    w = cfg["weights"]
    c = components(s, cfg)
    base = sum(w[k] * c[k] for k in w)                       # 0..1
    mult = 1.0 - cfg["pipeline_cover"] * c["coverage"]
    raw_score = 100.0 * base * mult
    r = c["raw"]
    capped = r["spend30"] >= cfg["loser_spend"] and r["idx"] is not None and r["idx"] < cfg["loser_index"] and raw_score > cfg["loser_cap"]
    score = round(cfg["loser_cap"] if capped else raw_score, 1)
    drivers = [{"signal": k, "label": SIGNAL_TEXT[k], "value": round(c[k], 3), "points": round(100.0 * w[k] * c[k], 1), "plain_text": _text(k, c)} for k in w if w[k] > 0]
    drivers.sort(key=lambda d: (-d["points"], d["signal"]))
    if c["coverage"] > 0:
        drivers.append({"signal": "pipeline", "label": SIGNAL_TEXT["pipeline"], "value": round(c["coverage"], 3), "points": round(-100.0 * base * (1.0 - mult), 1), "plain_text": _text("pipeline", c)})
    if capped:
        drivers.append({"signal": "loser_cap", "label": "Capped: losing money at scale", "value": round(r["idx"], 3), "points": round(score - raw_score, 1),
                        "plain_text": f"Capped at {cfg['loser_cap']:.0f}: {_usd(r['spend30'])} in 30 days at a new-customer return index of {r['idx']:.2f} (under {cfg['loser_index']:.2f})"})

    flags = []
    untested = r["spend30"] < JUDGED_SPEND and not r["judged"]
    if untested:
        flags.append("untested: no ads with enough spend or history, the score cannot judge this cut; treat it as a test, not a scale-up")
    if r["spend30"] >= 10000 and r["idx"] is not None and r["idx"] < 0.85:
        flags.append("big and below its product mix: more creative here has not been paying back")
    if r["spend30"] >= MIN_RECENT_SPEND and _n(s.get("top_ad_share_30d")) >= 0.6:
        flags.append(f"one ad carries {_pct(_n(s.get('top_ad_share_30d')))} of the cut's spend")
    if r["spend30"] < MIN_RECENT_SPEND and r["judged"] and (r["hidx"] or 0) > 1.2:
        flags.append("no spend now but new ads used to hit here: a revive candidate")
    if c["coverage"] >= 1:
        flags.append("pipeline already covers the cut's launch pace")
    if r["spend30"] >= MIN_RECENT_SPEND and _n(s.get("old_spend_share_30d")) >= 0.8 and _n(s.get("live_ads")) > 0:
        flags.append(f"{_pct(_n(s.get('old_spend_share_30d')))} of spend is on ads older than 60 days")

    tier = cfg["untested_label"] if untested else next(lab for floor, lab in cfg["tiers"] if score >= floor)
    conf = confidence(s)
    labels_ = [lab for _, lab in cfg["tiers"]]
    if conf == "low" and not untested and labels_.index(tier) < labels_.index(cfg["low_conf_max_tier"]):
        tier = cfg["low_conf_max_tier"]
        flags.append("low confidence: tier held at Keep steady until the cut has $1,000 in 30 days and 3 tested ads")
    shown = [d for d in drivers if d["signal"] not in ("pipeline", "loser_cap")][:3] + [d for d in drivers if d["signal"] in ("pipeline", "loser_cap")]
    why = "; ".join(d["plain_text"] for d in shown)
    return {"labels": dict(s.get("labels") or {}), "score": score, "tier": tier, "drivers": drivers, "why": why, "confidence": conf, "flags": flags,
            "components": {k: round(c[k], 3) for k in ("economics", "hit_rate", "supply_gap", "scale", "momentum", "coverage")}}


_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def score_cuts(rows: list[dict], config: dict | None = None) -> list[dict]:
    """Score every cut and sort: score high to low, then confidence (high first), then 30-day spend (high first), then labels A to Z. Each output row = {labels, score (0..100), tier,
    drivers: [{signal, label, value, points, plain_text}] (points add up to the score; a pipeline row carries the negative points), why (top 3 drivers in words), confidence, flags,
    components, signals (the input, unchanged)}. Untested cuts (under $100 in 30 days and no tested ads) get tier 'Untested' and sort after every tested cut."""
    out = []
    for s in rows:
        o = score_one(s, config)
        o["signals"] = s
        out.append(o)
    out.sort(key=lambda o: (o["tier"] == (config or DEFAULT_CONFIG).get("untested_label", "Untested"), -o["score"], _CONF_ORDER[o["confidence"]], -_n(o["signals"].get("spend_30d")),
                            tuple(str(v).lower() for v in o["labels"].values())))
    return out
