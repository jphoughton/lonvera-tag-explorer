"""Weekly creative priorities: the one-sheeter of market cluster / ICP / pain point / story lines to produce ads for next.

Every Communication Overview line (taxonomy_lines; the line a tagger chose, ad_tags tag_set 'linemap') gets a performance STATUS from rates (ROAS index to the product's own
average, last 30 days vs days 31-90 and lifetime): WORKING NOW, RISING, FADING / WAS WORKING, NOT WORKING, AVERAGE, TOO EARLY, UNTESTED / UNDER-TESTED. Sections per product:
  iterate  WORKING NOW / RISING lines that look under-iterated -> more concepts.
  revive   FADING lines: strong earlier or lifetime, now below the product average; with the old winners' hooks and a 'consistent with' read of why.
  new      lines with no ads (or almost none) ranked by evidence that the demand exists: an adjacent winner (same ICP / cluster) on the same product, the same pain point /
           cluster working on another product, competitors running it a long time (optional file), Josh's cluster order and whether the line has a story.
  competitor  angles competitors run that are not on our sheet at all (optional competitor file).
  stop     NOT WORKING lines (enough spend, consistently weak): deprioritise.
Scoring is deterministic and documented in docs/CREATIVE_PRIORITIES.md; every threshold is a constant below. Numbers are Meta-reported (the app's primary basis); the
Upstack new-customer numbers ride along and only raise a flag. This is prioritisation, not a causal claim.

Stored one snapshot per week (week_start = the Sunday, Pacific) in creative_priorities_snapshots; re-running a week replaces it.
Usage: python -m etl.creative_priorities [--week YYYY-MM-DD] [--dry-run] [--if-sunday] [--out DIR]
  --if-sunday  do nothing unless today (Pacific) is a Sunday (the nightly etl calls it this way)
  --dry-run    compute and print / write files, store nothing (read-only database session)
  --out DIR    also write DIR/priorities_<week>.csv and .md
  --competitors-file FILE [--as-of YYYY-MM-DD]   with --dry-run: use this competitor CSV instead of the stored ads (the stored ads come from etl.competitor_ads, weekly)"""
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

CONCEPTS_ITERATE = 10                  # new concepts to brief per product per week: iterate section
CONCEPTS_REVIVE = 4                    # ... revive section
CONCEPTS_NEW = 6                       # ... and new section (together 20 per product, the weekly brief size)
TOP_REVIVE, TOP_STOP, TOP_COMPETITOR = 4, 5, 3
MAX_PER_REVIVE_ROW = 3
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
THEN_FROM, THEN_TO = 31, 90            # the 'then' window: days 31 to 90 before the last day of data
MIN_THEN_SPEND = 1000.0                # the then window is trusted from this spend
NOW_GOOD, NOW_BAD = 1.00, 0.90         # last-30-day shrunk ROAS index at/above = good now; under = bad now
RISE_RATIO, FALL_RATIO = 1.15, 0.85    # now / then at or above = rising; at or below = fading
THEN_GOOD = 1.05                       # then-window (or lifetime INDEX_BAR) index at/above = it was working
NOT_WORK_LIFE = 0.95                   # lifetime index under this and bad now = not working
ROLL_WEEKS, ROLL_MIN_SPEND = 4, 1000.0 # 'faded since': rolling 4-week windows with at least this line spend
COMP_LONG_DAYS = 60                    # a competitor ad running this long counts as long-running
W_ADJ_C, W_CROSS_C, W_COMP_C, W_ORDER_C, W_STORY_C = 0.40, 0.15, 0.25, 0.12, 0.08   # new-line weights when a competitor file is loaded
ST_WORKING, ST_RISING, ST_FADING, ST_NOT, ST_AVG, ST_EARLY, ST_UNTESTED = ("WORKING NOW", "RISING", "FADING / WAS WORKING", "NOT WORKING", "AVERAGE", "TOO EARLY", "UNTESTED / UNDER-TESTED")
STATUSES = (ST_WORKING, ST_RISING, ST_FADING, ST_NOT, ST_AVG, ST_EARLY, ST_UNTESTED)
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
  COALESCE(sum(spend) FILTER (WHERE day > t.d - 7), 0) AS s7,
  COALESCE(sum(spend) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS s60, COALESCE(sum(purchases) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS p60,
  COALESCE(sum(revenue) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS r60
FROM {table} f CROSS JOIN t GROUP BY f.family_key
"""

WEEKLY_SQL = """
SELECT family_key, date_trunc('week', day)::date AS wk, sum(spend) AS spend, sum(revenue) AS revenue FROM family_daily GROUP BY 1, 2
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
    weekly: dict = {}
    for fk, wk, sp, rv in conn.execute(WEEKLY_SQL).fetchall():
        weekly.setdefault(fk, {})[wk] = (_f(sp), _f(rv))
    return {"through": through, "lines": lines, "families": fams, "win": win, "weekly": weekly, "competitors": load_competitors(conn)}



# ----------------------------------------------------------------------------------------------------------------------- competitor input (optional)
COMP_ALIASES = {"brand": ("brand", "advertiser", "page", "page_name"), "days": ("days_running", "days", "running_days", "days_active", "days_live"),
                "variants": ("variant_count", "variant_count_in_loaded_set", "variants", "variant", "copies"), "hook": ("hook", "hook_opening_line", "headline", "ad_hook", "primary_text_hook"),
                "product": ("mapped_product", "mapped_lonvera_product", "product", "our_product"), "line_code": ("mapped_line", "mapped_line_code", "line_code", "line"),
                "cluster": ("mapped_cluster", "cluster", "market_cluster"), "icp": ("mapped_icp", "icp"), "pain_point": ("mapped_pain_point", "mapped_pain", "pain_point", "pain"),
                "new": ("new_flag", "new", "is_new"), "confidence": ("confidence", "mapping_confidence", "map_confidence"), "url": ("ad_url", "url", "ad_library_url", "link")}


def _key(h: str) -> str:
    return "_".join("".join(ch if ch.isalnum() else " " for ch in h.lower()).split())


def _num(x, default=0):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return default


def parse_competitor_csv(text: str) -> list[dict]:
    """Normalise a competitor ads CSV (header names matched by alias, case and punctuation ignored) into dict rows."""
    rd = csv.DictReader(io.StringIO(text))
    hdr = {_key(h): h for h in (rd.fieldnames or [])}
    col = {k: next((hdr[a] for a in al if a in hdr), None) for k, al in COMP_ALIASES.items()}
    if col["days"] is None:                                            # 'days_running_at_2026-11-09'
        col["days"] = next((h for k, h in hdr.items() if k.startswith("days_running")), None)
    out = []
    for r in rd:
        g = lambda k: (r.get(col[k]) or "").strip() if col[k] else ""
        if not g("brand") and not g("hook"):
            continue
        out.append({"brand": g("brand"), "days": int(_num(g("days"))), "variants": int(_num(g("variants"), 1)) or 1, "hook": g("hook"), "product": g("product"), "line_code": g("line_code"),
                    "cluster": g("cluster"), "icp": g("icp"), "pain_point": g("pain_point"), "new": g("new").lower() in ("1", "true", "yes", "y", "x", "new"), "confidence": g("confidence").lower(),
                    "url": g("url")})
    return out


COMPETITOR_VIEW = "competitor_concepts"
COMPETITOR_COLS = ("week_start", "brand", "concept_key", "first_ad_start_date", "days_running", "variant_count", "format", "hook", "primary_text_excerpt", "mapped_product", "line_code", "cluster", "icp",
                   "pain_point", "confidence", "is_new_angle", "new_angle_label", "as_of", "is_stale")


def load_competitors(conn) -> dict | None:
    """Competitor concepts from the read-only view `competitor_concepts` (written by the competitor job, not by this module) for its latest week, or None when the view is absent, lacks
    the contract columns or is empty: then there is no competitor signal and no competitor section. Never raises."""
    try:
        have = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s", (COMPETITOR_VIEW,)).fetchall()}
        if not set(COMPETITOR_COLS) <= have:
            return None
        cur = conn.execute(f"SELECT {', '.join(COMPETITOR_COLS)} FROM {COMPETITOR_VIEW} WHERE week_start = (SELECT max(week_start) FROM {COMPETITOR_VIEW})")
        data = [dict(zip(COMPETITOR_COLS, r)) for r in cur.fetchall()]
    except Exception:                                                   # noqa: BLE001  an optional input never breaks the snapshot
        conn.rollback()
        return None
    if not data:
        return None
    rows, stale = [], set()
    for d in data:
        if d["is_stale"]:
            stale.add(d["brand"])
        rows.append({"brand": d["brand"] or "", "days": int(d["days_running"] or 0), "variants": int(d["variant_count"] or 1), "hook": d["hook"] or "", "product": d["mapped_product"] or "",
                     "line_code": d["line_code"] or "", "cluster": d["cluster"] or "", "icp": d["icp"] or "", "pain_point": d["pain_point"] or "", "new": bool(d["is_new_angle"]),
                     "confidence": (d["confidence"] or "").lower(), "url": "", "new_angle_label": d["new_angle_label"] or "", "format": d["format"] or ""})
    as_of = max((d["as_of"] for d in data if d["as_of"]), default=None)
    return {"loaded_at": None, "as_of": (as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)) if as_of else data[0]["week_start"].isoformat(), "source": COMPETITOR_VIEW, "rows": rows,
            "stale_brands": sorted(stale)}


def competitors_from_file(path: str, as_of: dt.date | None = None) -> dict:
    import os
    if path.endswith(".json"):                                         # rows already normalised (e.g. dumped from etl.competitor_ads.rows_for_snapshot)
        d = json.load(open(path))
        return {"loaded_at": None, "as_of": d["as_of"], "source": os.path.basename(path), "rows": d["rows"], "stale_brands": d.get("stale_brands", [])}
    with open(path, newline="") as fh:
        rows = parse_competitor_csv(fh.read())
    return {"loaded_at": None, "as_of": (as_of or dt.date.fromtimestamp(os.path.getmtime(path))).isoformat(), "source": os.path.basename(path), "rows": rows}


def _slug(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum()).replace("vitalbull", "vitabull")


def _nm(s: str) -> str:
    return " ".join((s or "").lower().split())


def map_competitors(comp: dict | None, lines: dict) -> tuple[dict, list[dict]]:
    """-> ({line_id: [rows]}, [rows on no line of ours]). A row maps by (product, line code), else (product, ICP, pain point), else (product, cluster, pain point)."""
    by_line: dict[int, list] = {}
    unmatched: list[dict] = []
    if not comp:
        return by_line, unmatched
    prods = {_slug(p): p for p in PRODUCTS}
    names = {_slug(l["product_name"]): l["product"] for l in lines.values()}
    idx_code = {(l["product"], l["line_code"].lower()): lid for lid, l in lines.items()}
    idx_ip = {(l["product"], _nm(l["icp"]), _nm(l["pain_point"])): lid for lid, l in lines.items()}
    idx_cp = {}
    for lid, l in lines.items():
        idx_cp.setdefault((l["product"], _nm(l["cluster"]), _nm(l["pain_point"])), lid)
    for r in comp["rows"]:
        prod = prods.get(_slug(r["product"])) or names.get(_slug(r["product"]))
        if prod is None:
            continue
        lid = idx_code.get((prod, (r["line_code"] or "").lower())) or idx_ip.get((prod, _nm(r["icp"]), _nm(r["pain_point"]))) or idx_cp.get((prod, _nm(r["cluster"]), _nm(r["pain_point"])))
        r2 = {**r, "product": prod, "brand": (r["brand"] or "").split(" (")[0].strip()}      # 'OmegaXL (green-lipped mussel oil)' is the brand OmegaXL
        if lid is None:
            unmatched.append(r2)
        else:
            by_line.setdefault(lid, []).append(r2)
    return by_line, unmatched


def comp_summary(rows: list[dict]) -> dict | None:
    """Competitor signal of one line: brands, concepts, those running COMP_LONG_DAYS+ days. Running a long time is NOT proven performance."""
    if not rows:
        return None
    brands = sorted({r["brand"] for r in rows if r["brand"]})
    long_rows = [r for r in rows if r["days"] >= COMP_LONG_DAYS]
    long_brands = sorted({r["brand"] for r in long_rows if r["brand"]})
    top = sorted(rows, key=lambda r: (-r["days"], r["brand"]))[:2]
    nb, nc, nl = len(brands), len(rows), len(long_rows)
    text = f"{nb} competitor{'s' if nb != 1 else ''}, {nc} concept{'s' if nc != 1 else ''}, {nl} running {COMP_LONG_DAYS}+ days" + (f" ({', '.join(long_brands[:4])})" if long_brands else "")
    return {"competitors": nb, "concepts": nc, "long_running": nl, "brands": brands, "long_brands": long_brands, "text": text,
            "hooks": [{"brand": r["brand"], "hook": r["hook"], "days": r["days"], "url": r["url"]} for r in top if r["hook"]]}


def comp_score(c: dict | None) -> float:
    return 0.0 if not c else clamp(0.6 * min(1.0, c["long_running"] / 3) + 0.4 * min(1.0, c["competitors"] / 3))


# ----------------------------------------------------------------------------------------------------------------------- status
def classify(m: dict) -> str:
    """Performance status of a line from rates only: index = shrunk line ROAS / the product's own ROAS in the same window (see docs/CREATIVE_PRIORITIES.md)."""
    if m["spend"] < NEW_MAX_SPEND and m["judged"] <= NEW_MAX_ADS:
        return ST_UNTESTED
    if m["spend"] < MIN_LINE_SPEND or m["purchases"] < MIN_LINE_PURCHASES or m["judged"] < MIN_LINE_ADS:
        return ST_EARLY
    life = m["idx_launch"] or 0.0
    now = m["idx_30"] if (m["idx_30"] is not None and m["spend_30d"] >= DORMANT_SPEND) else None
    then = m["idx_then"] if (m["idx_then"] is not None and m["spend_then"] >= MIN_THEN_SPEND) else None
    if now is None:                                                   # no spend in the last 30 days: a retired line, judged on its history
        return ST_FADING if life >= INDEX_BAR else ST_NOT if life < NOT_WORK_LIFE else ST_AVG
    if now >= NOW_GOOD:
        if now >= INDEX_BAR and then is not None and now >= RISE_RATIO * then:
            return ST_RISING
        if life >= INDEX_BAR:
            return ST_WORKING
        if then is not None and then >= THEN_GOOD and now >= INDEX_BAR:
            return ST_WORKING                                         # good in both windows, only the lifetime average is dragged by an older weak stretch
        return ST_RISING if now >= INDEX_BAR else ST_AVG
    was_good = life >= INDEX_BAR or (then is not None and then >= THEN_GOOD)
    declined = now < NOW_BAD or (then is not None and now <= FALL_RATIO * then)
    if was_good and declined:
        return ST_FADING
    if now < NOW_BAD and life < NOT_WORK_LIFE:
        return ST_NOT
    return ST_AVG


def last_at_or_above(weekly_line: dict, weekly_prod: dict) -> dict | None:
    """The last rolling ROLL_WEEKS-week window in which the line's ROAS was at or above its product's (line spend in the window >= ROLL_MIN_SPEND)."""
    weeks = sorted(weekly_prod)
    best = None
    for i in range(ROLL_WEEKS - 1, len(weeks)):
        win = weeks[i - ROLL_WEEKS + 1: i + 1]
        ls = sum(weekly_line.get(w, (0.0, 0.0))[0] for w in win)
        if ls < ROLL_MIN_SPEND:
            continue
        lr = sum(weekly_line.get(w, (0.0, 0.0))[1] for w in win)
        ps = sum(weekly_prod[w][0] for w in win)
        pr = sum(weekly_prod[w][1] for w in win)
        if ps and pr and (lr / ls) >= (pr / ps):
            best = {"window_end": (win[-1] + dt.timedelta(days=6)).isoformat(), "weeks_since": len(weeks) - 1 - i}
    return best

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
    zero = {k: 0.0 for k in ("s_all", "p_all", "r_all", "s30", "p30", "r30", "sp30", "pp30", "rp30", "s7", "s60", "p60", "r60")}

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
        b = {"launch": _blank(), "since": _blank(), "d30": _blank(), "prior30": _blank(), "nc_since": _blank(), "nc_d30": _blank(), "then": _blank()}
        for f in pf:
            _acc(b["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
            w, wn = f["w"], f["wn"]
            _acc(b["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
            _acc(b["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
            _acc(b["prior30"], _f(w["sp30"]), _f(w["pp30"]), _f(w["rp30"]))
            _acc(b["then"], _f(w.get("s60")), _f(w.get("p60")), _f(w.get("r60")))
            _acc(b["nc_since"], _f(wn["s_all"]), _f(wn["p_all"]), _f(wn["r_all"]))
            _acc(b["nc_d30"], _f(wn["s30"]), _f(wn["p30"]), _f(wn["r30"]))
        mature = [f for f in pf if f["mature"]]
        b["judged"] = len(mature)
        b["hits"] = sum(1 for f in mature if f["hit"])
        b["hit_rate"] = b["hits"] / b["judged"] if b["judged"] else 0.0
        b["roas"] = ratio(b["launch"]["revenue"], b["launch"]["spend"]) or 0.0
        b["roas_30d"] = ratio(b["d30"]["revenue"], b["d30"]["spend"]) or 0.0
        b["roas_prior30"] = ratio(b["prior30"]["revenue"], b["prior30"]["spend"]) or 0.0
        b["roas_then"] = ratio(b["then"]["revenue"], b["then"]["spend"]) or 0.0
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
                                          "since": _blank(), "weak_spend": 0.0, "strong": _blank(), "then": _blank(), "newads": [0.0, 0.0], "oldads": [0.0, 0.0]})
        a["fams"].append(f)
        _acc(a["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
        w, wn = f["w"], f["wn"]
        _acc(a["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
        _acc(a["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
        _acc(a["prior30"], _f(w["sp30"]), _f(w["pp30"]), _f(w["rp30"]))
        _acc(a["nc_since"], _f(wn["s_all"]), _f(wn["p_all"]), _f(wn["r_all"]))
        _acc(a["nc_d30"], _f(wn["s30"]), _f(wn["p30"]), _f(wn["r30"]))
        a["s7"] += _f(w["s7"])
        _acc(a["then"], _f(w.get("s60")), _f(w.get("p60")), _f(w.get("r60")))
        tgt = a["newads"] if f["new30"] else a["oldads"]
        tgt[0] += _f(w["s30"])
        tgt[1] += _f(w["r30"])
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
                     hit_idx=None, spend_then=0.0, roas_then=None, cpa_then=None, idx_then=None, cpa_30d=None, share_then=0.0, share_now=0.0, new_spend30=0.0, new_rev30=0.0, old_spend30=0.0, old_rev30=0.0, nc_roas=None, nc_idx=None, nc_purchases=0.0, nc_cpa=None, nc_roas_30d=None, spend_share=0.0, launch_share_30d=0.0, since_spend=0.0)
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
                 launch_share_30d=(sum(1 for f in fm if f["new30"]) / b["new30"]) if b["new30"] else 0.0, since_spend=nsp,
                 spend_then=a["then"]["spend"], roas_then=ratio(a["then"]["revenue"], a["then"]["spend"]), cpa_then=ratio(a["then"]["spend"], a["then"]["purchases"], 2),
                 idx_then=(shrunk_roas(a["then"]["revenue"], a["then"]["spend"], b["roas_then"]) / b["roas_then"]) if b["roas_then"] else None,
                 cpa_30d=ratio(s30, a["d30"]["purchases"], 2), share_then=(a["then"]["spend"] / b["then"]["spend"]) if b["then"]["spend"] else 0.0,
                 share_now=(s30 / b["d30"]["spend"]) if b["d30"]["spend"] else 0.0,
                 new_spend30=a["newads"][0], new_rev30=a["newads"][1], old_spend30=a["oldads"][0], old_rev30=a["oldads"][1])
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

    # ------------------------------------------------------------------------------------------------ status, hooks index, weekly series, competitors
    for lid, m in metrics.items():
        m["status"] = classify(m)
    status_counts = {sl: {st: 0 for st in STATUSES} for sl in PRODUCTS}
    for lid, m in metrics.items():
        status_counts[m["product"]][m["status"]] += 1
    fams_by_line: dict[int, list] = {}
    for f in fams:
        if f["line_id"] in lines:
            fams_by_line.setdefault(f["line_id"], []).append(f)
    prod_weekly: dict[str, dict] = {}
    line_weekly: dict[int, dict] = {}
    weekly = inp.get("weekly") or {}
    for f in fams:
        for wk, (sp, rv) in (weekly.get(f["family_key"]) or {}).items():
            pw = prod_weekly.setdefault(f["product"], {})
            a_, b_ = pw.get(wk, (0.0, 0.0))
            pw[wk] = (a_ + sp, b_ + rv)
            if f["line_id"] in lines:
                lw = line_weekly.setdefault(f["line_id"], {})
                a_, b_ = lw.get(wk, (0.0, 0.0))
                lw[wk] = (a_ + sp, b_ + rv)
    comp = inp.get("competitors")
    comp_by_line, comp_unmatched = map_competitors(comp, lines)
    comp_sum = {lid: comp_summary(rows_) for lid, rows_ in comp_by_line.items()}

    def comp_component(lid: int) -> float:
        return comp_score(comp_sum.get(lid))

    def proven_factor(m) -> float:
        return 1.0 if m["status"] in (ST_WORKING, ST_RISING) else 0.6 if m["status"] == ST_FADING else 0.0

    def comp_econ(m):
        bl = blended(m)
        return clamp(((bl if bl is not None else 0) - ECON_FLOOR) / ECON_SPAN)

    # ------------------------------------------------------------------------------------------------ iterate / revive / stop
    iterate: list[dict] = []
    revive: list[dict] = []
    stop: list[dict] = []
    for lid, l in lines.items():
        m = metrics[lid]
        st = m["status"]
        if st in (ST_WORKING, ST_RISING):
            mom = 0.5
            if m["spend_30d"] >= MIN_RECENT_SPEND and m["spend_prior30"] >= MIN_RECENT_SPEND and m["roas_30d"] is not None and m["roas_prior30"]:
                mom = clamp(0.5 + (m["roas_30d"] / m["roas_prior30"] - 1.0))
            room = clamp(1.0 - (m["launch_share_30d"] / m["spend_share"])) if m["spend_share"] else 0.0
            c = {"econ": comp_econ(m), "hit": clamp(((m["hit_idx"] or 0) - HIT_FLOOR) / HIT_SPAN), "room": room, "momentum": mom, "size": comp_size(m)}
            score = 100 * (W_ECON * c["econ"] + W_HIT * c["hit"] + W_ROOM * c["room"] + W_MOM * c["momentum"] + W_SIZE * c["size"]) * (1.0 - WEAK_PENALTY * m["weak_share"])
            iterate.append({"line": l, "m": m, "score": score, "comp": c})
        elif st == ST_FADING:
            best = max(m["idx_launch"] or 0.0, m["idx_then"] if (m["idx_then"] is not None and m["spend_then"] >= MIN_THEN_SPEND) else 0.0)
            now = m["idx_30"] if (m["idx_30"] is not None and m["spend_30d"] >= DORMANT_SPEND) else None
            decline = 1.0 if now is None else clamp((1.0 - now / best) / 0.5) if best else 0.0
            c = {"was_strong": clamp((best - ECON_FLOOR) / ECON_SPAN), "decline": decline, "size": comp_size(m)}
            score = 100 * (0.4 * c["size"] + 0.4 * c["was_strong"] + 0.2 * c["decline"]) * (1.0 - WEAK_PENALTY * m["weak_share"])
            revive.append({"line": l, "m": m, "score": score, "comp": c})
        elif st == ST_NOT:
            stop.append({"line": l, "m": m, "score": m["spend"], "comp": {}})

    # ------------------------------------------------------------------------------------------------ new
    proven_lines = [(lid, metrics[lid]) for lid in lines if metrics[lid]["spend"] >= MIN_LINE_SPEND and (metrics[lid]["idx_launch"] or 0) >= INDEX_BAR and proven_factor(metrics[lid]) > 0]
    n_lines = {sl: max(1, sum(1 for l in lines.values() if l["product"] == sl)) for sl in PRODUCTS}
    order_rank = {sl: {l["id"]: i for i, l in enumerate(sorted((x_ for x_ in lines.values() if x_["product"] == sl), key=lambda x_: (x_["sort_order"], x_["id"])))} for sl in PRODUCTS}
    norm = lambda s_: " ".join((s_ or "").lower().split())
    w_adj, w_cross, w_comp, w_order, w_story = (W_ADJ_C, W_CROSS_C, W_COMP_C, W_ORDER_C, W_STORY_C) if comp else (W_ADJ, W_CROSS, 0.0, W_ORDER, W_STORY)
    new_rows: list[dict] = []
    for lid, l in lines.items():
        m = metrics[lid]
        if l["added_after_tagging"]:
            continue                                                  # reads 0 ads by construction (ads about it sit under sibling lines): not 'untested'
        if m["status"] != ST_UNTESTED or m["tagged_ads"] > NEW_MAX_TAGGED:
            continue
        adj, adj_src, cross, cross_src = 0.0, None, 0.0, None
        for plid, pm in proven_lines:
            pl = lines[plid]
            e = comp_econ(pm) * comp_size(pm) * (1.0 - WEAK_PENALTY * pm["weak_share"]) * proven_factor(pm)
            if pl["product"] == l["product"] and plid != lid:
                w = ADJ_ICP if norm(pl["icp"]) == norm(l["icp"]) else ADJ_CLUSTER if norm(pl["cluster"]) == norm(l["cluster"]) else 0.0
                if w * e > adj:
                    adj, adj_src = w * e, plid
            elif pl["product"] != l["product"]:
                w = CROSS_PAIN if norm(pl["pain_point"]) == norm(l["pain_point"]) else CROSS_CLUSTER if norm(pl["cluster"]) == norm(l["cluster"]) else 0.0
                if w * e > cross:
                    cross, cross_src = w * e, plid
        cc = comp_component(lid)
        order = 1.0 - order_rank[l["product"]][lid] / n_lines[l["product"]]
        story = 1.0 if (l["story"] or "").strip() else 0.0
        score = 100 * (w_adj * adj + w_cross * cross + w_comp * cc + w_order * order + w_story * story)
        new_rows.append({"line": l, "m": m, "score": score, "adj": adj, "adj_src": adj_src, "cross": cross, "cross_src": cross_src, "has_evidence": adj > 0 or cross > 0 or cc > 0,
                         "comp": {"adjacent": adj, "cross_product": cross, "competitors": cc, "cluster_order": order, "story": story}})


    # ------------------------------------------------------------------------------------------------ hooks

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

    def metric_block0(m: dict, b: dict) -> dict:
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

    def metric_block(m: dict, b: dict) -> dict:
        out = metric_block0(m, b)
        out.update({"roas_then": m["roas_then"], "cpa_then": m["cpa_then"], "spend_then": money(m["spend_then"]), "idx_then": round(m["idx_then"], 3) if m["idx_then"] is not None else None,
                    "idx_30": round(m["idx_30"], 3) if m["idx_30"] is not None else None, "cpa_30d": m["cpa_30d"], "share_then": round(m["share_then"], 4), "share_now": round(m["share_now"], 4)})
        return out

    def x(v):
        return "n/a" if v is None else f"{v:.2f}x"

    def usd(v):
        return "n/a" if v is None else f"${v:,.0f}"

    def then_now(m: dict) -> str:
        return (f"Then (days {THEN_FROM}-{THEN_TO}): ROAS {x(m['roas_then'])}, CPA {usd(m['cpa_then'])}, {pct(m['share_then'])} of product spend. "
                f"Now (30 days): ROAS {x(m['roas_30d'])}, CPA {usd(m['cpa_30d'])}, {pct(m['share_now'])} of product spend. Lifetime ROAS {x(m['roas'])}.")

    def approach(m: dict, b: dict) -> str:
        n_new = m["launches_30d"]
        if m["spend_30d"] < DORMANT_SPEND:
            return ("No spend in the last 30 days: consistent with a retired winner, not a decline in delivery. Remake the old winners' hooks with new scenes and proof, then test cheaply.")
        new_roas = ratio(m["new_rev30"], m["new_spend30"]) if m["new_spend30"] >= 500 else None
        old_roas = ratio(m["old_rev30"], m["old_spend30"]) if m["old_spend30"] >= 500 else None
        if n_new <= 1 or m["launch_share_30d"] < 0.5 * m["spend_share"]:
            return (f"Only {n_new} new ad{'s' if n_new != 1 else ''} in 30 days against {pct(m['spend_share'])} of the product's lifetime spend: consistent with creative fatigue and no fresh iterations. "
                    "Remake the old winners' hooks with new scenes, openings and proof.")
        if new_roas is not None and b["roas_30d"] and new_roas < NOW_BAD * b["roas_30d"] and (old_roas is None or old_roas < NOW_BAD * b["roas_30d"]):
            return (f"{n_new} new ads launched in 30 days and they also run below the product average ({x(new_roas)} vs {x(b['roas_30d'])}): consistent with the whole line weakening, not only old creative wearing out. "
                    "Rework the angle (new story or ICP framing) and test it small before scaling.")
        return (f"Mixed: new ads {x(new_roas)} and older ads {x(old_roas)} over the last 30 days against a product average of {x(b['roas_30d'])}: consistent with partial fatigue. "
                "Refresh the older winners' hooks and keep what the newer ads show works.")

    def faded_info(lid: int, m: dict, slug: str) -> dict | None:
        last = last_at_or_above(line_weekly.get(lid, {}), prod_weekly.get(slug, {}))
        chg = None
        if m["roas_then"] and m["roas_30d"] is not None and m["spend_30d"] >= DORMANT_SPEND:
            chg = round((m["roas_30d"] / m["roas_then"] - 1) * 100)
        return {"last_at_or_above": last, "roas_change_pct": chg, "idx_then": round(m["idx_then"], 3) if m["idx_then"] is not None else None,
                "idx_now": round(m["idx_30"], 3) if (m["idx_30"] is not None and m["spend_30d"] >= DORMANT_SPEND) else None}

    rows: list[dict] = []
    by_product = {sl: {"iterate": [], "revive": [], "stop": [], "new": []} for sl in PRODUCTS}
    for it in iterate:
        by_product[it["line"]["product"]]["iterate"].append(it)
    for it in revive:
        by_product[it["line"]["product"]]["revive"].append(it)
    for it in stop:
        by_product[it["line"]["product"]]["stop"].append(it)
    for nw in new_rows:
        by_product[nw["line"]["product"]]["new"].append(nw)

    def common(section, rank, l, m, b, concepts, score, conf, fl, why, comp_dict, hooks, evidence=None):
        cs = comp_sum.get(l["id"])
        return {"section": section, "product": l["product"], "rank": rank, "line_id": l["id"], "line_code": l["line_code"], "cluster": l["cluster"], "icp": l["icp"], "pain_point": l["pain_point"],
                "emotional_trigger": l["emotional_trigger"], "story": l["story"], "ingredients": l["ingredients"], "concepts": concepts, "score": round(score, 1), "confidence": conf, "flags": fl,
                "why": why, "status": m["status"], "then_now": then_now(m), "competitor": ({k: v for k, v in cs.items()} if cs else None), "components": {k: round(v, 3) for k, v in comp_dict.items()},
                "metrics": metric_block(m, b), "hooks": hooks, "evidence": evidence}

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
                   f"{pct(m['launch_share_30d'])} of the product's new ads in 30 days vs {pct(m['spend_share'])} of its spend.")
            rows.append(common("iterate", i, l, m, b, n, it["score"], conf_level(m, [z for z in fl if z in ("thin sample", "weak tags", "few ads") or z.startswith("Upstack")]), fl, why, it["comp"], hooks_for(l["id"])))
        rv = sorted(by_product[slug]["revive"], key=lambda r: (-r["score"], r["line"]["sort_order"], r["line"]["id"]))[:TOP_REVIVE]
        alloc = allocate([max(r["score"], 1.0) for r in rv], CONCEPTS_REVIVE, 1, MAX_PER_REVIVE_ROW)
        for i, (it, n) in enumerate(zip(rv, alloc), start=1):
            l, m = it["line"], it["m"]
            fi = faded_info(l["id"], m, slug)
            last = fi["last_at_or_above"]
            bits = []
            if fi["idx_then"] is not None and fi["idx_now"] is not None:
                bits.append(f"ROAS index to the product {fi['idx_then']:.2f} (days {THEN_FROM}-{THEN_TO}) to {fi['idx_now']:.2f} (last 30 days)" + (f", ROAS {fi['roas_change_pct']:+d}%" if fi["roas_change_pct"] is not None else ""))
            elif fi["idx_now"] is None:
                bits.append("no spend in the last 30 days")
            bits.append(f"lifetime ROAS {x(m['roas'])} vs {x(b['roas'])} for the product")
            if last:
                bits.append(f"last at or above the product average in the {ROLL_WEEKS} weeks ending {last['window_end']} ({last['weeks_since']} week{'s' if last['weeks_since'] != 1 else ''} ago)")
            fl = risk_flags(m)
            r = common("revive", i, l, m, b, n, it["score"], conf_level(m, [z for z in fl if z in ("thin sample", "weak tags", "few ads") or z.startswith("Upstack")]), fl,
                       "Was working, now " + ("idle" if fi["idx_now"] is None else "below the product average") + ": " + "; ".join(bits) + ".", it["comp"], hooks_for(l["id"]))
            r["faded"] = fi
            r["approach"] = approach(m, b)
            rows.append(r)
        sp_ = sorted(by_product[slug]["stop"], key=lambda r: (-r["score"], r["line"]["id"]))[:TOP_STOP]
        for i, it in enumerate(sp_, start=1):
            l, m = it["line"], it["m"]
            rows.append(common("stop", i, l, m, b, 0, 0.0, "medium", risk_flags(m),
                               f"Spent {usd(m['spend'])} over {m['judged']} tested ads at {x(m['roas'])} against {x(b['roas'])} for the product (index {m['idx_launch']:.2f}); {('no spend in the last 30 days' if m['spend_30d'] < DORMANT_SPEND else 'last 30 days index ' + format(m['idx_30'], '.2f'))}. Consistent with a line that is weak over its history.",
                               {}, []))
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
                                "same": "ICP" if norm(adj_l["icp"]) == norm(l["icp"]) else "cluster", "status": metrics[adj_l["id"]]["status"]} if adj_l else None),
                  "cross_product": ({"product": cr_l["product"], "line_code": cr_l["line_code"], "icp": cr_l["icp"], "cluster": cr_l["cluster"], "pain_point": cr_l["pain_point"],
                                     "spend": money(metrics[cr_l["id"]]["spend"]), "roas": metrics[cr_l["id"]]["roas"], "roas_index": round(metrics[cr_l["id"]]["idx_launch"], 3),
                                     "same": "pain point" if norm(cr_l["pain_point"]) == norm(l["pain_point"]) else "cluster", "status": metrics[cr_l["id"]]["status"]} if cr_l else None)}
            parts = []
            if adj_l:
                parts.append(f"same {ev['adjacent']['same']} on {adj_l['line_code']} ({adj_l['pain_point']}) spent ${metrics[adj_l['id']]['spend']:,.0f} at {metrics[adj_l['id']]['roas']:.2f}x, index {metrics[adj_l['id']]['idx_launch']:.2f}, {metrics[adj_l['id']]['status'].lower()}")
            if cr_l:
                parts.append(f"same {ev['cross_product']['same']} on {cr_l['product']} {cr_l['line_code']} spent ${metrics[cr_l['id']]['spend']:,.0f} at {metrics[cr_l['id']]['roas']:.2f}x, index {metrics[cr_l['id']]['idx_launch']:.2f}")
            cs = comp_sum.get(l["id"])
            if cs:
                parts.append("competitors run it: " + cs["text"] + " (running a long time, not proven performance)")
            why = ("; ".join(parts) + ".") if parts else "No performance evidence from adjacent, other-product or competitor lines; ranked on cluster order and available story only."
            fl = [] if nw["has_evidence"] else ["no performance evidence"]
            if m["tagged_ads"]:
                fl.append(f"{m['tagged_ads']} ad(s) already tagged, ${m['spend']:,.0f} spent")
            hooks = hooks_for(l["id"], borrowed_from=nw["adj_src"], limit=2) if nw["adj_src"] else []      # copy only from the same product's adjacent winner: another product's hooks do not transfer
            rows.append(common("new", i, l, m, b, n, nw["score"], "medium" if ((adj_l and cr_l) or (cs and (adj_l or cr_l))) else "low", fl, why, nw["comp"], hooks, ev))
        # competitor angles that are not on our sheet at all
        if comp:
            groups: dict[tuple, list] = {}
            for r in comp_unmatched:
                if r["product"] == slug and (r["cluster"] or r["icp"] or r["pain_point"]):
                    groups.setdefault((_nm(r["cluster"]), _nm(r["icp"]), _nm(r["pain_point"])), []).append(r)
            ranked = sorted(groups.values(), key=lambda g_: (-comp_score(comp_summary(g_)), -len(g_), g_[0]["cluster"]))[:TOP_COMPETITOR]
            for i, g_ in enumerate(ranked, start=1):
                cs = comp_summary(g_)
                top = max(g_, key=lambda r: r["days"])
                rows.append({"section": "competitor", "product": slug, "rank": i, "line_id": None, "line_code": "not on sheet", "cluster": g_[0]["cluster"] or "(not mapped)", "icp": g_[0]["icp"] or "(not mapped)",
                             "pain_point": g_[0]["pain_point"] or "(not mapped)", "emotional_trigger": None, "story": ("Competitor hook: " + top["hook"]) if top["hook"] else "", "ingredients": "", "concepts": 1,
                             "score": round(100 * comp_score(cs), 1), "confidence": "medium" if cs["long_running"] >= 2 and cs["competitors"] >= 2 else "low",
                             "flags": ["not on our Communication Overview", "competitor signal is running a long time, not proven performance"],
                             "why": "Competitors run this angle: " + cs["text"] + ". We have no line for it.", "status": ST_UNTESTED, "then_now": "", "competitor": cs, "components": {"competitors": round(comp_score(cs), 3)},
                             "metrics": {"spend": 0, "tagged_ads": 0, "ads": 0, "judged": 0}, "hooks": [], "evidence": None,
                             "cluster_description": "As mapped from the competitor ads; not a cluster on our sheet.", "icp_description": "As mapped from the competitor ads.", "pain_description": "As mapped from the competitor ads."})


    names = {l["product"]: l["product_name"] for l in inp["lines"]}
    for r in rows:
        r["product_name"] = names.get(r["product"], r["product"])
        if r["line_id"] is not None:
            r.update(describe(inp["lines"], lines[r["line_id"]]))
    baselines = {s: {"product_name": names.get(s, s), "roas": round(b["roas"], 4), "cpa": b["cpa"], "spend": money(b["launch"]["spend"]), "roas_30d": round(b["roas_30d"], 4), "roas_prior30": round(b["roas_prior30"], 4), "roas_then": round(b["roas_then"], 4),
                     "hit_rate": round(b["hit_rate"], 4), "judged": b["judged"], "hits": b["hits"], "new_ads_30d": b["new30"], "nc_roas": round(b["nc_roas"], 4)} for s, b in base.items()}
    tagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] in lines) for s in PRODUCTS}
    untagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] not in lines) for s in PRODUCTS}
    auto_fams = sum(1 for f in fams if (f["tagger"] or "").startswith("auto-"))
    thresholds = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)}
    out_stats = {"data_through": through.isoformat(), "week_start": week.isoformat(), "status_counts": status_counts, "status_counts_total": {st: sum(c[st] for c in status_counts.values()) for st in STATUSES},
                 "iterate_candidates": len(iterate), "revive_candidates": len(revive), "stop_candidates": len(stop), "new_candidates": len(new_rows),
                 "competitors": ({"as_of": comp["as_of"], "source": comp.get("source"), "rows": len(comp["rows"]), "matched_rows": sum(len(v) for v in comp_by_line.values()), "unmatched_rows": len(comp_unmatched),
                                  "brands": len({r["brand"] for r in comp["rows"]}), "stale_brands": comp.get("stale_brands", [])} if comp else None),
                 "tagged_spend": {s: round(v) for s, v in tagged_spend.items()}, "untagged_spend": {s: round(v) for s, v in untagged_spend.items()}, "auto_tagged_families": auto_fams,
                 "rows": len(rows)}
    res = {"week_start": week.isoformat(), "data_through": through.isoformat(), "basis": BASIS, "thresholds": thresholds, "baselines": baselines, "rows": rows, "stats": out_stats, "competitors_as_of": comp["as_of"] if comp else None, "legend": [{"tag": a, "meaning": b} for a, b in LEGEND]}
    if keep_lines:                                                    # every line with its numbers: for calibration and the sanity checks, never stored
        res["all_lines"] = [{"line_code": lines[i]["line_code"], "product": lines[i]["product"], "cluster": lines[i]["cluster"], "icp": lines[i]["icp"], "pain_point": lines[i]["pain_point"],
                             **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics[i].items() if k not in ("line_id", "product")}} for i in lines]
    return res


# ----------------------------------------------------------------------------------------------------------------------- output
SECTION_ORDER = ("iterate", "revive", "new", "competitor", "stop")
SECTION_TITLES = {"iterate": "Iterate on what is working (WORKING NOW / RISING)", "revive": "Revive or rework (FADING / WAS WORKING)", "new": "New and untested clusters",
                  "competitor": "Competitor angles we have not made (not on our sheet)", "stop": "Stop or deprioritise (NOT WORKING)"}
CSV_COLS = ["market_cluster", "icp", "pain_point", "story", "market_cluster_description", "icp_description", "pain_point_description", "status", "product", "section", "rank", "concepts", "week_start",
            "then_vs_now", "what_others_are_running", "suggested_approach", "emotional_trigger", "ingredients", "line_code", "confidence", "flags", "score",
            "spend", "roas", "cpa", "roas_index", "ads", "hit_rate", "hit_rate_index", "spend_30d", "roas_30d", "spend_prior30", "roas_prior30", "launches_30d", "nc_roas", "nc_cpa",
            "hook_1", "hook_2", "hook_3", "why"]


def to_csv(snapshot: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLS)
    for r in sorted(snapshot["rows"], key=lambda r: (PRODUCTS.index(r["product"]) if r["product"] in PRODUCTS else 99, SECTION_ORDER.index(r["section"]), r["rank"])):
        m = r["metrics"]
        g = lambda k: m.get(k)
        hk = [h["hook"] + (f" [{h['line_code']}]" if h.get("borrowed") else "") for h in r["hooks"]] + ["", "", ""]
        comp = (r.get("competitor") or {}).get("text", "")
        w.writerow([r["cluster"], r["icp"], r["pain_point"], r["story"] or "", r["cluster_description"], r["icp_description"], r["pain_description"], r.get("status", ""), r["product_name"], r["section"], r["rank"],
                    r["concepts"], snapshot["week_start"], r.get("then_now", ""), comp, r.get("approach", ""), r["emotional_trigger"] or "", r["ingredients"], r["line_code"], r["confidence"],
                    "; ".join(r["flags"]), r["score"], g("spend"), g("roas"), g("cpa"), g("roas_index"), g("ads"), g("hit_rate"), g("hit_rate_index"), g("spend_30d"), g("roas_30d"), g("spend_prior30"),
                    g("roas_prior30"), g("launches_30d"), g("nc_roas"), g("nc_cpa"), hk[0], hk[1], hk[2], r["why"]])
    return buf.getvalue()


def to_markdown(snapshot: dict) -> str:
    rows = snapshot["rows"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"
    L = [f"# Creative priorities, week of {snapshot['week_start']}", "",
         f"Data through {snapshot['data_through']}. Numbers: Meta-reported (spend, ROAS = purchase value / spend, CPA = spend / purchases); Upstack new-customer ROAS beside it. "
         f"Prioritisation, not a causal claim: 'consistent with' evidence only.", ""]
    ca = snapshot.get("competitors_as_of")
    L.append(f"Competitor signal: Meta Ad Library, as of {ca}. Running a long time is not proven performance." if ca else "Competitor signal: none loaded.")
    L += ["", "What the four tags are:", ""] + [f"- **{a}**: {b}" for a, b in LEGEND] + [""]
    tc = snapshot["stats"].get("status_counts_total")
    if tc:
        L += ["Lines by status (all products): " + ", ".join(f"{k} {v}" for k, v in tc.items()), ""]
    for slug, b in snapshot["baselines"].items():
        cpa = "n/a" if b["cpa"] is None else f"${b['cpa']:.0f}"
        L.append(f"- {b['product_name']} average: ROAS {b['roas']:.2f}x since launch, {b['roas_30d']:.2f}x last 30 days, CPA {cpa}, hit rate {b['hits']}/{b['judged']} = {b['hit_rate'] * 100:.0f}% (ads reaching ${HIT_SPEND:,.0f}), {b['new_ads_30d']} new ads in 30 days")
    for slug in PRODUCTS:
        pr = [r for r in rows if r["product"] == slug]
        if not pr:
            continue
        L += ["", f"## {pr[0]['product_name']}"]
        for sec in SECTION_ORDER:
            sr = [r for r in pr if r["section"] == sec]
            if not sr:
                continue
            L += ["", f"### {SECTION_TITLES[sec]}", ""]
            for r in sr:
                m = r["metrics"]
                L.append(f"**{r['rank']}. {r['cluster']} → {r['icp']} → {r['pain_point']}**  [{r['status']}]  ")
                L.append(f"Story: {r['story'] or ''}  ")
                L.append(f"_Cluster: {r['cluster_description']} ICP: {r['icp_description']} Pain point: {r['pain_description']}_  ")
                if r.get("then_now"):
                    L.append(f"{r['then_now']}  ")
                if r.get("competitor"):
                    L.append(f"What others are running: {r['competitor']['text']} (running a long time, not proven performance)  ")
                extra = f"{r['concepts']} concept(s)" if r["concepts"] else "no new concepts"
                if sec == "iterate":
                    extra += f" | hit rate {pct(m.get('hit_rate'))}"
                L.append(f"_{extra} | confidence {r['confidence']}{' | ' + '; '.join(r['flags']) if r['flags'] else ''}_  ")
                L.append(f"Why: {r['why']}  ")
                if r.get("approach"):
                    L.append(f"Suggested approach: {r['approach']}  ")
                if r["hooks"]:
                    L.append(("Old winners' hooks: " if sec == "revive" else "Hooks: ") + " / ".join(h["hook"] for h in r["hooks"]))
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
    ap.add_argument("--as-of", help="as-of date of --competitors-file (default: the file's modified date)")
    ap.add_argument("--competitors-file", metavar="FILE", help="with --dry-run: use this competitor CSV instead of the stored one")
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
            inp = load_inputs(conn)
            if a.competitors_file:
                inp["competitors"] = competitors_from_file(a.competitors_file, dt.date.fromisoformat(a.as_of) if a.as_of else None)
            snap = compute(inp, week, keep_lines=bool(a.out))
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
