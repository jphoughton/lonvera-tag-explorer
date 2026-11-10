"""Weekly creative priorities: the one-sheeter of market cluster / ICP / pain point / story lines to produce ads for next.

Two sections per product (Vita Bull, Beef Liver, Boneflex), both on the Communication Overview line (taxonomy_lines; the line a tagger chose, ad_tags tag_set 'linemap'):
  iterate  lines whose ads already work (economics at or above the product's own average on enough spend) and that look under-iterated -> more concepts.
  new      lines with no ads (or almost none) ranked by evidence that the demand exists: an adjacent winner (same ICP / cluster) on the same product, or the same
           pain point / cluster working on another product, plus Josh's cluster order and whether the line has a story.
Scoring is deterministic and documented in docs/CREATIVE_PRIORITIES.md; every threshold is a constant below. Numbers are Meta-reported (the app's primary basis); the
Upstack new-customer numbers ride along and only raise a flag. This is prioritisation, not a causal claim.

Stored one snapshot per week (week_start = the Sunday, Pacific) in creative_priorities_snapshots; re-running a week replaces it.
Usage: python -m etl.creative_priorities [--week YYYY-MM-DD] [--dry-run] [--if-sunday] [--out DIR]
  --if-sunday  do nothing unless today (Pacific) is a Sunday (the nightly etl calls it this way)
  --dry-run    compute and print / write files, store nothing (read-only database session)
  --out DIR    also write DIR/priorities_<week>.csv and .md"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import sys
from zoneinfo import ZoneInfo

from . import db

# ----------------------------------------------------------------------------------------------------------------------- thresholds (documented in docs/CREATIVE_PRIORITIES.md)
PRODUCTS = ("vita-bull", "beef-liver", "boneflex")
BASIS = "meta"                         # scored on Meta-reported numbers; Upstack new customers is shown and flags

MIN_LINE_SPEND = 5000.0                # iterate: dollars spent on the line since launch before it can be judged
MIN_LINE_PURCHASES = 40                # ... and Meta-reported purchases
MIN_LINE_ADS = 4                       # ... and ads that reached JUDGED_SPEND
JUDGED_SPEND = 100.0                   # an ad counts as tested once it spent this much
HIT_SPEND = 1000.0                     # an ad is a hit once it reached this much spend (hit rate = hits / ads that were judged)
MATURE_DAYS = 14                       # ads younger than this are left out of the hit-rate denominator (no time to reach $1,000)
SHRINK_SPEND = 2500.0                  # ROAS shrinks toward the product average with this many dollars of prior spend
SHRINK_ADS = 4.0                       # hit rate shrinks toward the product average with this many prior ads
INDEX_BAR = 1.05                       # iterate: shrunk since-launch ROAS index (line / product) must be at least this (clearly above the product's own average)
FADE_BAR = 0.90                        # iterate: shrunk last-30-day ROAS index must be at least this when the line spent DORMANT_SPEND or more in 30 days
MIN_RECENT_SPEND = 1000.0              # spend in a 30-day window before that window's ROAS is trusted
DORMANT_SPEND = 250.0                  # under this in the last 30 days = dormant (a retired winner)
SATURATED_RATIO = 0.75                 # last-30-day ROAS under this share of the prior 30 days (both windows >= MIN_RECENT_SPEND) = fading flag
W_ECON, W_HIT, W_ROOM, W_MOM, W_SIZE = 0.35, 0.20, 0.20, 0.15, 0.10   # iterate score weights (sum 1)
ECON_FLOOR, ECON_SPAN = 0.95, 0.45     # economics component = clamp((blended index - 0.95) / 0.45)
HIT_FLOOR, HIT_SPAN = 0.75, 1.25       # hit-rate component = clamp((hit-rate index - 0.75) / 1.25)
SIZE_FULL = 25000.0                    # spend at which the size component reaches 1 (log scale from MIN_LINE_SPEND)

CONCEPTS_ITERATE = 14                  # new concepts to brief per product per week: iterate section
CONCEPTS_NEW = 6                       # ... and new section (together 20 per product, the weekly brief size)
TOP_ITERATE, TOP_NEW = 8, 6            # rows per product per section
MAX_PER_ITERATE_ROW, MAX_PER_NEW_ROW = 5, 2

NEW_MAX_ADS = 1                        # new: at most this many judged ads on the line ...
NEW_MAX_SPEND = 1000.0                 # ... and under this spend since launch
NEW_MAX_TAGGED = 3                     # ... and at most this many tagged ads in the tracker even with no spend (more = already in the pipeline)
NEW_MIN_FILL = 3                       # fill a product's new list to this many with no-evidence lines (flagged), never more
W_ADJ, W_CROSS, W_ORDER, W_STORY = 0.50, 0.20, 0.20, 0.10
ADJ_ICP, ADJ_CLUSTER = 1.0, 0.7        # weight of an adjacent winner on the same ICP / only the same cluster
CROSS_PAIN, CROSS_CLUSTER = 1.0, 0.6   # weight of the same pain point / same cluster name working on another product

WEAK_PENALTY = 0.35                    # iterate score x (1 - this x share of the line's spend on weak tags)
WEAK_SHARE = 0.35                      # share of a line's spend carried by low-confidence / copy-only / auto tags that makes the tags weak
THIN_SPEND, THIN_PURCHASES = 10000.0, 100
NC_DISAGREE = 0.85                     # Upstack new-customer ROAS index under this while Meta says at/above average = flag
NC_MIN_SPEND = 3000.0
HOOKS_PER_ROW = 3
HOOK_MIN_SPEND = 1000.0

LEGEND = [
    ("Market Cluster", "The broad audience or topic market the ad speaks to, for example Hair Thinning."),
    ("ICP", "The specific person (ideal customer profile) inside that market, for example a woman whose ferritin is normal yet she keeps shedding."),
    ("Pain Point", "The problem that person feels and wants solved."),
    ("Story", "The scene or narrative the ad tells, 15 words or fewer, written for that person and that pain."),
]

PT = ZoneInfo("America/Los_Angeles")


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ratio(a: float, b: float, nd: int = 4):
    return round(a / b, nd) if b else None


def week_start(today: dt.date) -> dt.date:
    """The Sunday on or before today."""
    return today - dt.timedelta(days=(today.weekday() + 1) % 7)


def pacific_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(PT).date()


# ----------------------------------------------------------------------------------------------------------------------- loading
FAM_SQL = """
SELECT m.family_key, m.tracker_row_id, p.slug AS product, g.line_id, g.confidence AS tag_conf, g.method AS tag_method, g.tagger,
       m.spend_launch, m.purchases_launch, m.revenue_launch, m.live,
       cs.first_day, cs.last_spend_day, COALESCE(cs.launched_before_data_start, false) AS before_start, COALESCE(cs.display, m.family_key) AS display,
       hk.hook, hk.confidence AS hook_conf
FROM ad_metrics m
JOIN products p ON p.id = m.product_id AND p.slug = ANY(%s)
LEFT JOIN ad_tags g ON g.tracker_row_id = m.tracker_row_id AND g.tag_set = 'linemap' AND g.is_current
LEFT JOIN creative_stage_moves cs ON cs.family_key = m.family_key
LEFT JOIN LATERAL (SELECT h.hook, h.confidence FROM tracker_rows tr JOIN ad_hooks h ON h.tracker_row_id = tr.id
                   WHERE tr.family_key = m.family_key AND tr.deleted_at IS NULL ORDER BY (tr.id = m.tracker_row_id) DESC, tr.sheet_row LIMIT 1) hk ON true
WHERE m.is_first_row AND (m.spend_launch > 0 OR g.line_id IS NOT NULL)
"""

WIN_SQL = """
WITH t AS (SELECT max(day) AS d FROM {table})
SELECT f.family_key,
  sum(spend) AS s_all, sum(purchases) AS p_all, sum(revenue) AS r_all,
  COALESCE(sum(spend) FILTER (WHERE day > t.d - 30), 0) AS s30, COALESCE(sum(purchases) FILTER (WHERE day > t.d - 30), 0) AS p30, COALESCE(sum(revenue) FILTER (WHERE day > t.d - 30), 0) AS r30,
  COALESCE(sum(spend) FILTER (WHERE day <= t.d - 30 AND day > t.d - 60), 0) AS sp30, COALESCE(sum(purchases) FILTER (WHERE day <= t.d - 30 AND day > t.d - 60), 0) AS pp30,
  COALESCE(sum(revenue) FILTER (WHERE day <= t.d - 30 AND day > t.d - 60), 0) AS rp30,
  COALESCE(sum(spend) FILTER (WHERE day > t.d - 7), 0) AS s7
FROM {table} f CROSS JOIN t GROUP BY f.family_key
"""

LINE_SQL = """
SELECT l.id, p.slug AS product, p.name AS product_name, l.line_code, l.cluster, l.icp, l.pain_point, l.emotional_trigger, l.story, l.sort_order, l.added_after_tagging,
       COALESCE((SELECT string_agg(i.ingredient, ' + ' ORDER BY i.sort_order) FROM line_ingredients i WHERE i.line_id = l.id), '') AS ingredients,
       COALESCE((SELECT i.attacks FROM line_ingredients i WHERE i.line_id = l.id ORDER BY i.sort_order LIMIT 1), '') AS attacks
FROM taxonomy_lines l JOIN products p ON p.id = l.product_id AND p.slug = ANY(%s)
ORDER BY p.slug, l.sort_order, l.id
"""


def load_inputs(conn) -> dict:
    """Everything the scoring needs, as plain dicts. Read-only."""
    through = conn.execute("SELECT max(day) FROM family_daily").fetchone()[0]
    if through is None:
        raise db.JobFailed("creative_priorities: family_daily is empty")
    cols = [d.name for d in conn.execute(LINE_SQL, (list(PRODUCTS),)).description]
    lines = [dict(zip(cols, r)) for r in conn.execute(LINE_SQL, (list(PRODUCTS),)).fetchall()]
    cur = conn.execute(FAM_SQL, (list(PRODUCTS),))
    fcols = [d.name for d in cur.description]
    fams = [dict(zip(fcols, r)) for r in cur.fetchall()]
    win = {}
    for key, table in (("meta", "family_daily"), ("nc", "family_daily_upstack_nc")):
        cur = conn.execute(WIN_SQL.format(table=table))
        wc = [d.name for d in cur.description]
        win[key] = {r[0]: dict(zip(wc, r)) for r in cur.fetchall()}
    return {"through": through, "lines": lines, "families": fams, "win": win}


# ----------------------------------------------------------------------------------------------------------------------- computation
def _blank():
    return {"spend": 0.0, "purchases": 0.0, "revenue": 0.0}


def _acc(d: dict, spend: float, purchases: float, revenue: float) -> None:
    d["spend"] += spend
    d["purchases"] += purchases
    d["revenue"] += revenue


def shrunk_roas(rev: float, spend: float, base: float, k: float = SHRINK_SPEND) -> float | None:
    """ROAS pulled toward the product average by k dollars of prior spend: small samples cannot look better or worse than the product by luck alone."""
    return (rev + k * base) / (spend + k) if base else None


def shrunk_rate(hits: float, n: float, base: float, k: float = SHRINK_ADS) -> float:
    return (hits + k * base) / (n + k)


def _weak_tag(f: dict) -> bool:
    m = (f["tag_method"] or "").lower()
    return f["tag_conf"] == "low" or m.startswith("text only") or m.startswith("no picture") or (f["tagger"] or "").startswith("auto-")


def allocate(weights: list[float], budget: int, lo: int, hi: int) -> list[int]:
    """Whole concepts per row: largest remainder on the weights, each row between lo and hi, total at most budget (fewer rows than budget/hi leaves the rest unspent)."""
    n = len(weights)
    if n == 0:
        return []
    out = [lo] * n
    left = budget - lo * n
    if left <= 0:
        return out
    tot = sum(w for w in weights) or 1.0
    want = [w / tot * budget for w in weights]
    # start from the floor of each row's proportional share, then hand out the remainder by largest fractional part
    base = [min(hi, max(lo, int(x))) for x in want]
    left = budget - sum(base)
    order = sorted(range(n), key=lambda i: (-(want[i] - int(want[i])), -weights[i], i))
    while left > 0 and any(b < hi for b in base):
        progressed = False
        for i in order:
            if left <= 0:
                break
            if base[i] < hi:
                base[i] += 1
                left -= 1
                progressed = True
        if not progressed:
            break
    while left < 0:                                   # lo floors overshot the budget: take from the lowest-weight rows that are above lo
        for i in sorted(range(n), key=lambda i: (weights[i], -i)):
            if left >= 0:
                break
            if base[i] > lo:
                base[i] -= 1
                left += 1
        else:
            break
    return base


def _join(items, k=4):
    items = list(dict.fromkeys(items))
    return ", ".join(items[:k]) + (f" and {len(items) - k} more" if len(items) > k else "")


def describe(all_lines: list[dict], l: dict) -> dict:
    """One plain sentence each for the line's cluster, ICP and pain point, built only from the Communication Overview text (no invented facts)."""
    same_cluster = [x for x in all_lines if x["product"] == l["product"] and x["cluster"] == l["cluster"]]
    same_icp = [x for x in same_cluster if x["icp"] == l["icp"]]
    pains = [x["pain_point"] for x in same_icp if x["pain_point"] != l["pain_point"]]
    cluster = (f"The {l['cluster']} market for {l['product_name']}: {len(set(x['icp'] for x in same_cluster))} ICP(s) in the Communication Overview ({_join(x['icp'] for x in same_cluster)}).")
    icp = (f"The specific person in this market: {l['icp']}" + (f", who also appears with {_join(pains, 3)}." if pains else ", with this as their only pain point on the sheet."))
    pain = f"The problem they feel: {l['pain_point']}."
    if l.get("emotional_trigger"):
        pain += f" The emotion behind it: {l['emotional_trigger']}."
    if l.get("attacks"):
        pain += f" Ingredient angle ({l['ingredients']}): {l['attacks'].rstrip('.')}."
    elif l.get("ingredients"):
        pain += f" Ingredient angle: {l['ingredients']}."
    return {"cluster_description": cluster, "icp_description": icp, "pain_description": pain}


def compute(inp: dict, week: dt.date, keep_lines: bool = False) -> dict:
    through: dt.date = inp["through"]
    lines = {l["id"]: l for l in inp["lines"]}
    fams = inp["families"]
    win_meta, win_nc = inp["win"]["meta"], inp["win"]["nc"]
    zero = {k: 0.0 for k in ("s_all", "p_all", "r_all", "s30", "p30", "r30", "sp30", "pp30", "rp30", "s7")}

    for f in fams:
        f["w"] = win_meta.get(f["family_key"], zero)
        f["wn"] = win_nc.get(f["family_key"], zero)
        for k in ("spend_launch", "purchases_launch", "revenue_launch"):
            f[k] = _f(f[k])
        f["judged"] = f["spend_launch"] >= JUDGED_SPEND
        age_ok = f["before_start"] or (f["first_day"] is not None and (through - f["first_day"]).days >= MATURE_DAYS)
        f["mature"] = bool(f["judged"] and age_ok)
        f["hit"] = f["spend_launch"] >= HIT_SPEND
        f["new30"] = bool(f["first_day"] is not None and not f["before_start"] and (through - f["first_day"]).days < 30 and f["spend_launch"] > 0)
        f["roas30"] = ratio(_f(f["w"]["r30"]), _f(f["w"]["s30"]))
        f["live3"] = bool(f["last_spend_day"] is not None and (through - f["last_spend_day"]).days < 3)

    # product baselines (all of the product's ads with spend, tagged or not)
    base: dict[str, dict] = {}
    for slug in PRODUCTS:
        pf = [f for f in fams if f["product"] == slug]
        b = {"launch": _blank(), "since": _blank(), "d30": _blank(), "prior30": _blank(), "nc_since": _blank(), "nc_d30": _blank()}
        for f in pf:
            _acc(b["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
            w, wn = f["w"], f["wn"]
            _acc(b["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
            _acc(b["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
            _acc(b["prior30"], _f(w["sp30"]), _f(w["pp30"]), _f(w["rp30"]))
            _acc(b["nc_since"], _f(wn["s_all"]), _f(wn["p_all"]), _f(wn["r_all"]))
            _acc(b["nc_d30"], _f(wn["s30"]), _f(wn["p30"]), _f(wn["r30"]))
        mature = [f for f in pf if f["mature"]]
        b["judged"] = len(mature)
        b["hits"] = sum(1 for f in mature if f["hit"])
        b["hit_rate"] = b["hits"] / b["judged"] if b["judged"] else 0.0
        b["roas"] = ratio(b["launch"]["revenue"], b["launch"]["spend"]) or 0.0
        b["roas_30d"] = ratio(b["d30"]["revenue"], b["d30"]["spend"]) or 0.0
        b["roas_prior30"] = ratio(b["prior30"]["revenue"], b["prior30"]["spend"]) or 0.0
        b["cpa"] = ratio(b["launch"]["spend"], b["launch"]["purchases"], 2)
        b["nc_roas"] = ratio(b["nc_since"]["revenue"], b["nc_since"]["spend"]) or 0.0
        b["new30"] = sum(1 for f in pf if f["new30"])
        b["spend_launch"] = b["launch"]["spend"]
        base[slug] = b

    # per-line aggregation
    agg: dict[int, dict] = {}
    for f in fams:
        if f["line_id"] is None or f["line_id"] not in lines:
            continue
        a = agg.setdefault(f["line_id"], {"fams": [], "launch": _blank(), "d30": _blank(), "prior30": _blank(), "nc_since": _blank(), "nc_d30": _blank(), "s7": 0.0,
                                          "since": _blank(), "weak_spend": 0.0, "strong": _blank()})
        a["fams"].append(f)
        _acc(a["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
        w, wn = f["w"], f["wn"]
        _acc(a["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
        _acc(a["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
        _acc(a["prior30"], _f(w["sp30"]), _f(w["pp30"]), _f(w["rp30"]))
        _acc(a["nc_since"], _f(wn["s_all"]), _f(wn["p_all"]), _f(wn["r_all"]))
        _acc(a["nc_d30"], _f(wn["s30"]), _f(wn["p30"]), _f(wn["r30"]))
        a["s7"] += _f(w["s7"])
        if _weak_tag(f):
            a["weak_spend"] += f["spend_launch"]
        else:
            _acc(a["strong"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])

    stats: dict[str, dict] = {}
    metrics: dict[int, dict] = {}
    for lid, l in lines.items():
        b = base[l["product"]]
        a = agg.get(lid)
        m = {"line_id": lid, "product": l["product"]}
        if a is None:
            m.update(ads=0, tagged_ads=0, roas_strong=None, judged=0, hits=0, mature=0, spend=0.0, purchases=0.0, revenue=0.0, roas=None, cpa=None, spend_30d=0.0, purchases_30d=0.0, roas_30d=None,
                     spend_prior30=0.0, roas_prior30=None, spend_7d=0.0, launches_30d=0, live_ads=0, winners_live=0, weak_share=0.0, idx_launch=None, idx_30=None, hit_rate=None,
                     hit_idx=None, nc_roas=None, nc_idx=None, nc_purchases=0.0, nc_cpa=None, nc_roas_30d=None, spend_share=0.0, launch_share_30d=0.0, since_spend=0.0)
            metrics[lid] = m
            continue
        sp, pu, rv = a["launch"]["spend"], a["launch"]["purchases"], a["launch"]["revenue"]
        fm = a["fams"]
        mature = [f for f in fm if f["mature"]]
        hits = sum(1 for f in mature if f["hit"])
        s30, r30 = a["d30"]["spend"], a["d30"]["revenue"]
        sp30, rp30 = a["prior30"]["spend"], a["prior30"]["revenue"]
        roas = ratio(rv, sp)
        idx_launch = (shrunk_roas(rv, sp, b["roas"]) / b["roas"]) if b["roas"] else None
        idx_30 = (shrunk_roas(r30, s30, b["roas_30d"]) / b["roas_30d"]) if b["roas_30d"] else None
        hr = hits / len(mature) if mature else None
        hit_idx = (shrunk_rate(hits, len(mature), b["hit_rate"]) / b["hit_rate"]) if b["hit_rate"] else None
        nsp, nrv = a["nc_since"]["spend"], a["nc_since"]["revenue"]
        nc_idx = (shrunk_roas(nrv, nsp, b["nc_roas"]) / b["nc_roas"]) if b["nc_roas"] else None
        m.update(ads=sum(1 for f in fm if f["spend_launch"] > 0), tagged_ads=len(fm), roas_strong=(ratio(a["strong"]["revenue"], a["strong"]["spend"]) if a["strong"]["spend"] >= 1000 else None), judged=sum(1 for f in fm if f["judged"]), hits=hits, mature=len(mature),
                 spend=sp, purchases=pu, revenue=rv, roas=roas, cpa=ratio(sp, pu, 2),
                 spend_30d=s30, purchases_30d=a["d30"]["purchases"], roas_30d=ratio(r30, s30),
                 spend_prior30=sp30, roas_prior30=ratio(rp30, sp30), spend_7d=a["s7"],
                 launches_30d=sum(1 for f in fm if f["new30"]), live_ads=sum(1 for f in fm if f["live3"]),
                 winners_live=sum(1 for f in fm if f["live3"] and _f(f["w"]["s30"]) >= 500 and f["roas30"] is not None and b["roas_30d"] and f["roas30"] >= b["roas_30d"]),
                 weak_share=(a["weak_spend"] / sp) if sp else 0.0, idx_launch=idx_launch, idx_30=idx_30, hit_rate=hr, hit_idx=hit_idx,
                 nc_roas=ratio(nrv, nsp), nc_idx=nc_idx, nc_purchases=a["nc_since"]["purchases"], nc_cpa=ratio(nsp, a["nc_since"]["purchases"], 2),
                 nc_roas_30d=ratio(a["nc_d30"]["revenue"], a["nc_d30"]["spend"]), spend_share=(sp / b["spend_launch"]) if b["spend_launch"] else 0.0,
                 launch_share_30d=(sum(1 for f in fm if f["new30"]) / b["new30"]) if b["new30"] else 0.0, since_spend=nsp)
        metrics[lid] = m

    def blended(m) -> float | None:
        if m["idx_launch"] is None:
            return None
        i30 = m["idx_30"] if (m["idx_30"] is not None and m["spend_30d"] >= MIN_RECENT_SPEND) else m["idx_launch"]
        return 0.5 * m["idx_launch"] + 0.5 * i30

    def comp_econ(m):
        bl = blended(m)
        return clamp(((bl if bl is not None else 0) - ECON_FLOOR) / ECON_SPAN)

    def comp_size(m):
        return clamp(math.log10(max(m["spend"], MIN_LINE_SPEND) / MIN_LINE_SPEND) / math.log10(SIZE_FULL / MIN_LINE_SPEND))

    def proven(m) -> bool:
        return m["spend"] >= MIN_LINE_SPEND and (m["idx_launch"] or 0) >= INDEX_BAR

    # ------------------------------------------------------------------------------------------------ iterate
    iterate: list[dict] = []
    skipped = {"thin": 0, "below_average": 0, "fading": 0}
    for lid, l in lines.items():
        m = metrics[lid]
        if m["spend"] < MIN_LINE_SPEND or m["purchases"] < MIN_LINE_PURCHASES or m["judged"] < MIN_LINE_ADS:
            if m["spend"] > 0:
                skipped["thin"] += 1
            continue
        if (m["idx_launch"] or 0) < INDEX_BAR:
            skipped["below_average"] += 1
            continue
        if m["spend_30d"] >= DORMANT_SPEND and (m["idx_30"] or 0) < FADE_BAR:
            skipped["fading"] += 1
            continue
        mom = 0.5
        if m["spend_30d"] >= MIN_RECENT_SPEND and m["spend_prior30"] >= MIN_RECENT_SPEND and m["roas_30d"] is not None and m["roas_prior30"]:
            mom = clamp(0.5 + (m["roas_30d"] / m["roas_prior30"] - 1.0))
        room = clamp(1.0 - (m["launch_share_30d"] / m["spend_share"])) if m["spend_share"] else 0.0
        comp = {"econ": comp_econ(m), "hit": clamp(((m["hit_idx"] or 0) - HIT_FLOOR) / HIT_SPAN), "room": room, "momentum": mom, "size": comp_size(m)}
        score = 100 * (W_ECON * comp["econ"] + W_HIT * comp["hit"] + W_ROOM * comp["room"] + W_MOM * comp["momentum"] + W_SIZE * comp["size"]) * (1.0 - WEAK_PENALTY * m["weak_share"])
        iterate.append({"line": l, "m": m, "score": score, "comp": comp})

    # ------------------------------------------------------------------------------------------------ new
    proven_lines = [(lid, metrics[lid]) for lid in lines if proven(metrics[lid])]
    n_lines = {s: max(1, sum(1 for l in lines.values() if l["product"] == s)) for s in PRODUCTS}
    order_rank = {s: {l["id"]: i for i, l in enumerate(sorted((x for x in lines.values() if x["product"] == s), key=lambda x: (x["sort_order"], x["id"])))} for s in PRODUCTS}
    norm = lambda s: " ".join((s or "").lower().split())
    new_rows: list[dict] = []
    for lid, l in lines.items():
        m = metrics[lid]
        if l["added_after_tagging"]:
            continue                                                  # reads 0 ads by construction (ads about it sit under sibling lines): not 'untested'
        if m["judged"] > NEW_MAX_ADS or m["spend"] >= NEW_MAX_SPEND or m["tagged_ads"] > NEW_MAX_TAGGED:
            continue
        adj, adj_src, cross, cross_src = 0.0, None, 0.0, None
        for plid, pm in proven_lines:
            pl = lines[plid]
            e = comp_econ(pm) * comp_size(pm) * (1.0 - WEAK_PENALTY * pm["weak_share"])
            if pl["product"] == l["product"] and plid != lid:
                w = ADJ_ICP if norm(pl["icp"]) == norm(l["icp"]) else ADJ_CLUSTER if norm(pl["cluster"]) == norm(l["cluster"]) else 0.0
                if w * e > adj:
                    adj, adj_src = w * e, plid
            elif pl["product"] != l["product"]:
                w = CROSS_PAIN if norm(pl["pain_point"]) == norm(l["pain_point"]) else CROSS_CLUSTER if norm(pl["cluster"]) == norm(l["cluster"]) else 0.0
                if w * e > cross:
                    cross, cross_src = w * e, plid
        order = 1.0 - order_rank[l["product"]][lid] / n_lines[l["product"]]
        story = 1.0 if (l["story"] or "").strip() else 0.0
        score = 100 * (W_ADJ * adj + W_CROSS * cross + W_ORDER * order + W_STORY * story)
        new_rows.append({"line": l, "m": m, "score": score, "adj": adj, "adj_src": adj_src, "cross": cross, "cross_src": cross_src, "has_evidence": adj > 0 or cross > 0,
                         "comp": {"adjacent": adj, "cross_product": cross, "cluster_order": order, "story": story}})

    # ------------------------------------------------------------------------------------------------ hooks
    fams_by_line: dict[int, list] = {}
    for f in fams:
        if f["line_id"] in lines:
            fams_by_line.setdefault(f["line_id"], []).append(f)

    def hooks_for(lid: int, borrowed_from: int | None = None, limit: int = HOOKS_PER_ROW) -> list[dict]:
        src = borrowed_from if borrowed_from is not None else lid
        b = base[lines[src]["product"]]
        cands = []
        for f in fams_by_line.get(src, []):
            if not f["hook"] or f["spend_launch"] < HOOK_MIN_SPEND:
                continue
            r = ratio(f["revenue_launch"], f["spend_launch"]) or 0.0
            if r < b["roas"]:
                continue
            cands.append((f["revenue_launch"] - b["roas"] * f["spend_launch"], f, r))
        cands.sort(key=lambda t: (-t[0], t[1]["family_key"]))
        out, seen = [], set()
        for ex, f, r in cands:
            key = "".join(ch for ch in f["hook"].lower() if ch.isalnum())[:45]       # 'Have you seen...' and 'Have you seen' are one hook
            if key in seen:
                continue
            seen.add(key)
            out.append({"hook": f["hook"], "ad": f["display"], "ad_id": f["tracker_row_id"], "spend": round(f["spend_launch"]), "roas": round(r, 2),
                        "line_code": lines[src]["line_code"], "borrowed": borrowed_from is not None})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------------------------------------ rows
    def money(x):
        return round(x, 2)

    def metric_block(m: dict, b: dict) -> dict:
        if m["spend"] < JUDGED_SPEND:                                 # under $100 there is nothing to read: no ratios, so an untested line never shows a ROAS
            blank = {k: None for k in ("roas", "cpa", "roas_index", "hit_rate", "hit_rate_index", "roas_30d", "roas_prior30", "nc_roas", "nc_cpa", "nc_roas_index", "nc_roas_30d", "roas_strong_tags")}
            return {**blank, "spend": money(m["spend"]), "purchases": round(m["purchases"]), "ads": m["ads"], "tagged_ads": m["tagged_ads"], "judged": m["judged"], "hits": 0, "mature": 0,
                    "spend_30d": money(m["spend_30d"]), "spend_prior30": money(m["spend_prior30"]), "spend_7d": money(m["spend_7d"]), "launches_30d": m["launches_30d"],
                    "launch_share_30d": 0.0, "spend_share": 0.0, "live_ads": m["live_ads"], "winners_live": 0, "nc_purchases": round(m["nc_purchases"])}
        return {"spend": money(m["spend"]), "purchases": round(m["purchases"]), "roas": m["roas"], "cpa": m["cpa"], "roas_index": round(m["idx_launch"], 3) if m["idx_launch"] is not None else None,
                "ads": m["ads"], "tagged_ads": m["tagged_ads"], "roas_strong_tags": m["roas_strong"], "judged": m["judged"], "hits": m["hits"], "mature": m["mature"], "hit_rate": round(m["hit_rate"], 4) if m["hit_rate"] is not None else None,
                "hit_rate_index": round(m["hit_idx"], 3) if m["hit_idx"] is not None else None,
                "spend_30d": money(m["spend_30d"]), "roas_30d": m["roas_30d"], "spend_prior30": money(m["spend_prior30"]), "roas_prior30": m["roas_prior30"],
                "spend_7d": money(m["spend_7d"]), "launches_30d": m["launches_30d"], "launch_share_30d": round(m["launch_share_30d"], 4), "spend_share": round(m["spend_share"], 4),
                "live_ads": m["live_ads"], "winners_live": m["winners_live"],
                "nc_roas": m["nc_roas"], "nc_cpa": m["nc_cpa"], "nc_purchases": round(m["nc_purchases"]), "nc_roas_index": round(m["nc_idx"], 3) if m["nc_idx"] is not None else None,
                "nc_roas_30d": m["nc_roas_30d"]}

    def risk_flags(m: dict, extra_dormant=True) -> list[str]:
        fl = []
        if m["spend"] < THIN_SPEND or m["purchases"] < THIN_PURCHASES:
            fl.append("thin sample")
        if m["weak_share"] > WEAK_SHARE:
            fl.append("weak tags")
        if m["nc_idx"] is not None and m["since_spend"] >= NC_MIN_SPEND and m["nc_idx"] < NC_DISAGREE:
            fl.append("Upstack new-customer ROAS below product average")
        if m["judged"] < 2 * MIN_LINE_ADS:
            fl.append("few ads")
        return fl

    def conf_level(m: dict, fl: list[str]) -> str:
        n = len(fl)
        if n >= 2:
            return "low"
        return "high" if n == 0 and m["spend"] >= SIZE_FULL else "medium"

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.0f}%"

    rows: list[dict] = []
    by_product = {s: {"iterate": [], "new": []} for s in PRODUCTS}
    for it in iterate:
        by_product[it["line"]["product"]]["iterate"].append(it)
    for nw in new_rows:
        by_product[nw["line"]["product"]]["new"].append(nw)

    for slug in PRODUCTS:
        b = base[slug]
        its = sorted(by_product[slug]["iterate"], key=lambda r: (-r["score"], r["line"]["sort_order"], r["line"]["id"]))[:TOP_ITERATE]
        alloc = allocate([r["score"] for r in its], CONCEPTS_ITERATE, 1, MAX_PER_ITERATE_ROW)
        for i, (it, n) in enumerate(zip(its, alloc), start=1):
            l, m = it["line"], it["m"]
            fl = risk_flags(m)
            if m["spend_30d"] < DORMANT_SPEND:
                fl.append("dormant (retired winner)")
            if m["spend_30d"] >= MIN_RECENT_SPEND and m["spend_prior30"] >= MIN_RECENT_SPEND and m["roas_prior30"] and m["roas_30d"] is not None and m["roas_30d"] / m["roas_prior30"] < SATURATED_RATIO:
                fl.append("fading vs prior 30 days")
            why = (f"ROAS {m['roas']:.2f}x vs {b['roas']:.2f}x for the product (index {m['idx_launch']:.2f}); {pct(m['hit_rate'])} of {m['mature']} mature ads reached ${HIT_SPEND:,.0f} vs {pct(b['hit_rate'])}; "
                   f"{pct(m['launch_share_30d'])} of the product's new ads in 30 days vs {pct(m['spend_share'])} of its spend; "
                   f"last 30 days {'n/a' if m['roas_30d'] is None else format(m['roas_30d'], '.2f') + 'x'} on ${m['spend_30d']:,.0f} vs prior 30 {'n/a' if m['roas_prior30'] is None else format(m['roas_prior30'], '.2f') + 'x'}.")
            rows.append({"section": "iterate", "product": slug, "rank": i, "line_id": l["id"], "line_code": l["line_code"], "cluster": l["cluster"], "icp": l["icp"], "pain_point": l["pain_point"],
                         "emotional_trigger": l["emotional_trigger"], "story": l["story"], "ingredients": l["ingredients"], "concepts": n, "score": round(it["score"], 1),
                         "confidence": conf_level(m, [x for x in fl if x in ("thin sample", "weak tags", "few ads") or x.startswith("Upstack")]), "flags": fl, "why": why,
                         "components": {k: round(v, 3) for k, v in it["comp"].items()}, "metrics": metric_block(m, b), "hooks": hooks_for(l["id"]), "evidence": None})
        nws = sorted(by_product[slug]["new"], key=lambda r: (not r["has_evidence"], -r["score"], r["line"]["sort_order"], r["line"]["id"]))
        chosen = [r for r in nws if r["has_evidence"]][:TOP_NEW]
        if len(chosen) < NEW_MIN_FILL:
            chosen += [r for r in nws if not r["has_evidence"]][:NEW_MIN_FILL - len(chosen)]
        chosen.sort(key=lambda r: (not r["has_evidence"], -r["score"], r["line"]["sort_order"], r["line"]["id"]))
        alloc = allocate([max(r["score"], 1.0) for r in chosen], CONCEPTS_NEW, 1, MAX_PER_NEW_ROW)
        for i, (nw, n) in enumerate(zip(chosen, alloc), start=1):
            l, m = nw["line"], nw["m"]
            adj_l = lines[nw["adj_src"]] if nw["adj_src"] else None
            cr_l = lines[nw["cross_src"]] if nw["cross_src"] else None
            ev = {"adjacent": ({"product": adj_l["product"], "line_code": adj_l["line_code"], "icp": adj_l["icp"], "cluster": adj_l["cluster"], "pain_point": adj_l["pain_point"],
                                "spend": money(metrics[adj_l["id"]]["spend"]), "roas": metrics[adj_l["id"]]["roas"], "roas_index": round(metrics[adj_l["id"]]["idx_launch"], 3),
                                "same": "ICP" if norm(adj_l["icp"]) == norm(l["icp"]) else "cluster"} if adj_l else None),
                  "cross_product": ({"product": cr_l["product"], "line_code": cr_l["line_code"], "icp": cr_l["icp"], "cluster": cr_l["cluster"], "pain_point": cr_l["pain_point"],
                                     "spend": money(metrics[cr_l["id"]]["spend"]), "roas": metrics[cr_l["id"]]["roas"], "roas_index": round(metrics[cr_l["id"]]["idx_launch"], 3),
                                     "same": "pain point" if norm(cr_l["pain_point"]) == norm(l["pain_point"]) else "cluster"} if cr_l else None)}
            parts = []
            if adj_l:
                parts.append(f"same {ev['adjacent']['same']} on {adj_l['line_code']} ({adj_l['pain_point']}) spent ${metrics[adj_l['id']]['spend']:,.0f} at {metrics[adj_l['id']]['roas']:.2f}x, index {metrics[adj_l['id']]['idx_launch']:.2f}")
            if cr_l:
                parts.append(f"same {ev['cross_product']['same']} on {cr_l['product']} {cr_l['line_code']} spent ${metrics[cr_l['id']]['spend']:,.0f} at {metrics[cr_l['id']]['roas']:.2f}x, index {metrics[cr_l['id']]['idx_launch']:.2f}")
            why = ("; ".join(parts) + ".") if parts else "No performance evidence from adjacent or other-product lines; ranked on cluster order and available story only."
            fl = [] if nw["has_evidence"] else ["no performance evidence"]
            if m["tagged_ads"]:
                fl.append(f"{m['tagged_ads']} ad(s) already tagged, ${m['spend']:,.0f} spent")
            hooks = hooks_for(l["id"], borrowed_from=nw["adj_src"], limit=2) if nw["adj_src"] else []      # copy only from the same product's adjacent winner: another product's hooks do not transfer
            rows.append({"section": "new", "product": slug, "rank": i, "line_id": l["id"], "line_code": l["line_code"], "cluster": l["cluster"], "icp": l["icp"], "pain_point": l["pain_point"],
                         "emotional_trigger": l["emotional_trigger"], "story": l["story"], "ingredients": l["ingredients"], "concepts": n, "score": round(nw["score"], 1),
                         "confidence": "medium" if (adj_l and cr_l) else "low", "flags": fl, "why": why, "components": {k: round(v, 3) for k, v in nw["comp"].items()},
                         "metrics": metric_block(m, b), "hooks": hooks, "evidence": ev})

    names = {l["product"]: l["product_name"] for l in inp["lines"]}
    for r in rows:
        r["product_name"] = names.get(r["product"], r["product"])
        r.update(describe(inp["lines"], lines[r["line_id"]]))
    baselines = {s: {"product_name": names.get(s, s), "roas": round(b["roas"], 4), "cpa": b["cpa"], "spend": money(b["launch"]["spend"]), "roas_30d": round(b["roas_30d"], 4), "roas_prior30": round(b["roas_prior30"], 4),
                     "hit_rate": round(b["hit_rate"], 4), "judged": b["judged"], "hits": b["hits"], "new_ads_30d": b["new30"], "nc_roas": round(b["nc_roas"], 4)} for s, b in base.items()}
    tagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] in lines) for s in PRODUCTS}
    untagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] not in lines) for s in PRODUCTS}
    auto_fams = sum(1 for f in fams if (f["tagger"] or "").startswith("auto-"))
    thresholds = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)}
    out_stats = {"data_through": through.isoformat(), "week_start": week.isoformat(), "iterate_candidates": len(iterate), "iterate_skipped": skipped, "new_candidates": len(new_rows),
                 "tagged_spend": {s: round(v) for s, v in tagged_spend.items()}, "untagged_spend": {s: round(v) for s, v in untagged_spend.items()}, "auto_tagged_families": auto_fams,
                 "rows": len(rows)}
    res = {"week_start": week.isoformat(), "data_through": through.isoformat(), "basis": BASIS, "thresholds": thresholds, "baselines": baselines, "rows": rows, "stats": out_stats, "legend": [{"tag": a, "meaning": b} for a, b in LEGEND]}
    if keep_lines:                                                    # every line with its numbers: for calibration and the sanity checks, never stored
        res["all_lines"] = [{"line_code": lines[i]["line_code"], "product": lines[i]["product"], "cluster": lines[i]["cluster"], "icp": lines[i]["icp"], "pain_point": lines[i]["pain_point"],
                             **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics[i].items() if k not in ("line_id", "product")}} for i in lines]
    return res


# ----------------------------------------------------------------------------------------------------------------------- output
CSV_COLS = ["market_cluster", "icp", "pain_point", "story", "market_cluster_description", "icp_description", "pain_point_description", "product", "section", "rank", "concepts", "week_start", "emotional_trigger", "ingredients", "line_code", "confidence", "flags", "score",
            "spend", "roas", "cpa", "roas_index", "ads", "hit_rate", "hit_rate_index", "spend_30d", "roas_30d", "spend_prior30", "roas_prior30", "launches_30d", "nc_roas", "nc_cpa",
            "hook_1", "hook_2", "hook_3", "why"]


def to_csv(snapshot: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLS)
    for r in sorted(snapshot["rows"], key=lambda r: (PRODUCTS.index(r["product"]) if r["product"] in PRODUCTS else 99, r["section"] != "iterate", r["rank"])):
        m = r["metrics"]
        hk = [h["hook"] + (f" [{h['line_code']}]" if h.get("borrowed") else "") for h in r["hooks"]] + ["", "", ""]
        w.writerow([r["cluster"], r["icp"], r["pain_point"], r["story"] or "", r["cluster_description"], r["icp_description"], r["pain_description"], r["product_name"], r["section"], r["rank"], r["concepts"], snapshot["week_start"], r["emotional_trigger"] or "",
                    r["ingredients"], r["line_code"], r["confidence"], "; ".join(r["flags"]), r["score"], m["spend"], m["roas"], m["cpa"], m["roas_index"], m["ads"], m["hit_rate"], m["hit_rate_index"],
                    m["spend_30d"], m["roas_30d"], m["spend_prior30"], m["roas_prior30"], m["launches_30d"], m["nc_roas"], m["nc_cpa"], hk[0], hk[1], hk[2], r["why"]])
    return buf.getvalue()


def to_markdown(snapshot: dict) -> str:
    rows = snapshot["rows"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"
    L = [f"# Creative priorities, week of {snapshot['week_start']}", "",
         f"Data through {snapshot['data_through']}. Numbers: Meta-reported (spend, ROAS = purchase value / spend, CPA = spend / purchases); Upstack new-customer ROAS beside it. "
         f"Prioritisation, not a causal claim: 'consistent with' evidence only.", ""]
    L += ["What the four tags are:", ""] + [f"- **{a}**: {b}" for a, b in LEGEND] + [""]
    for slug, b in snapshot["baselines"].items():
        cpa = "n/a" if b["cpa"] is None else f"${b['cpa']:.0f}"
        L.append(f"- {b['product_name']} average: ROAS {b['roas']:.2f}x since launch, {b['roas_30d']:.2f}x last 30 days, CPA {cpa}, hit rate {b['hits']}/{b['judged']} = {b['hit_rate'] * 100:.0f}% (ads reaching ${HIT_SPEND:,.0f}), {b['new_ads_30d']} new ads in 30 days")
    for slug in PRODUCTS:
        pr = [r for r in rows if r["product"] == slug]
        if not pr:
            continue
        L += ["", f"## {pr[0]['product_name']}"]
        for sec, title in (("iterate", "Iterate on what is working"), ("new", "New to produce")):
            L += ["", f"### {title}", ""]
            for r in [r for r in pr if r["section"] == sec]:
                m = r["metrics"]
                L.append(f"**{r['rank']}. {r['cluster']} → {r['icp']} → {r['pain_point']}**  ")
                L.append(f"Story: {r['story'] or ''}  ")
                L.append(f"_Cluster: {r['cluster_description']} ICP: {r['icp_description']} Pain point: {r['pain_description']}_  ")
                nums = (f"{r['concepts']} concept(s) | ${m['spend']:,.0f} spend, ROAS {m['roas']:.2f}, CPA ${m['cpa']:.0f}, {m['judged']} ads, hit rate {pct(m['hit_rate'])}, "
                        f"30d ROAS {'n/a' if m['roas_30d'] is None else format(m['roas_30d'], '.2f')} vs prior {'n/a' if m['roas_prior30'] is None else format(m['roas_prior30'], '.2f')}" if sec == "iterate" else f"{r['concepts']} concept(s)")
                L.append(f"_{nums} | confidence {r['confidence']}{' | ' + '; '.join(r['flags']) if r['flags'] else ''}_  ")
                L.append(f"Why: {r['why']}  ")
                if r["hooks"]:
                    L.append("Hooks: " + " / ".join(h["hook"] for h in r["hooks"]))
                L.append("")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------------------------------------------------- storage
def store(conn, snapshot: dict) -> None:
    conn.execute(
        """INSERT INTO creative_priorities_snapshots (week_start, data_through, basis, thresholds, baselines, rows, stats, built_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (week_start) DO UPDATE SET data_through = EXCLUDED.data_through, basis = EXCLUDED.basis, thresholds = EXCLUDED.thresholds, baselines = EXCLUDED.baselines,
             rows = EXCLUDED.rows, stats = EXCLUDED.stats, built_at = now()""",
        (snapshot["week_start"], snapshot["data_through"], snapshot["basis"], db.jsonb(snapshot["thresholds"]), db.jsonb(snapshot["baselines"]), db.jsonb(snapshot["rows"]), db.jsonb(snapshot["stats"])))


def run(conn, week: dt.date, write: bool) -> dict:
    snap = compute(load_inputs(conn), week)
    if write:
        store(conn, snap)
        conn.commit()
    return snap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="week start (a Sunday), YYYY-MM-DD; default = the current Pacific week")
    ap.add_argument("--dry-run", action="store_true", help="compute only, store nothing")
    ap.add_argument("--if-sunday", action="store_true", help="exit quietly unless today (Pacific) is a Sunday")
    ap.add_argument("--out", help="directory for priorities_<week>.csv and .md")
    a = ap.parse_args(argv)
    today = pacific_today()
    if a.if_sunday and today.weekday() != 6:
        db.log(f"[creative_priorities] {today} is not a Sunday (Pacific): nothing to do")
        return 0
    week = dt.date.fromisoformat(a.week) if a.week else week_start(today)
    if week.weekday() != 6:
        sys.exit(f"--week must be a Sunday, got {week} ({week.strftime('%A')})")
    if a.dry_run:
        with db.connect() as conn:
            conn.read_only = True
            snap = compute(load_inputs(conn), week, keep_lines=bool(a.out))
    else:
        with db.JobRun("creative_priorities") as job, db.connect() as conn:
            snap = run(conn, week, True)
            job.stats = snap["stats"]
            job.rows_written = len(snap["rows"])
            job.data_through = dt.date.fromisoformat(snap["data_through"])
    db.log(f"[creative_priorities] week {week}: {len(snap['rows'])} rows {'(dry run, nothing stored)' if a.dry_run else 'stored'}")
    if a.out:
        import os
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, f"priorities_{week}.csv"), "w") as fh:
            fh.write(to_csv(snap))
        with open(os.path.join(a.out, f"priorities_{week}.md"), "w") as fh:
            fh.write(to_markdown(snap))
        with open(os.path.join(a.out, f"priorities_{week}.json"), "w") as fh:
            json.dump(snap, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                                # noqa: BLE001
        db.log("creative_priorities FAILED:", db.scrub(e))
        sys.exit(1)
