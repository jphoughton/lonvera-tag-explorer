"""Weekly creative priorities: the one-sheeter of market cluster / ICP / pain point / story lines to produce ads for next.

Every Communication Overview line (taxonomy_lines; the line a tagger chose, ad_tags tag_set 'linemap') gets a performance STATUS from rates (ROAS index to the product's own
average, last 30 days vs days 31-90 and lifetime): WORKING NOW, RISING, FADING / WAS WORKING, NOT WORKING, AVERAGE, TOO EARLY, UNTESTED / UNDER-TESTED. Sections per product:
  iterate  WORKING NOW / RISING lines that look under-iterated -> more concepts.
  faded    WAS WORKING, NOW ISN'T (leads the sheet): lines with a sustained strong stretch in the weekly history (3+ consecutive weeks above the product average with enough spend)
           that are now below average, collapsed or idle; with when it worked, how well, when and how it stopped, the former winning ads and hooks, a 'consistent with'
           diagnosis and a revive play. Lines that never worked and are NOT WORKING go to the Stop list.
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
HIT_SPEND = 1000.0                     # SCORING ONLY: the hit rate inside the score counts an ad as a hit at this spend (hits / mature tested ads); $10,000 left 0 statuses unchanged but moved 14 scores / 5 ranks on sparse counts, so scoring stays here
MIN_14D_SPEND = 100.0                  # last-14-day ROAS is shown only with at least this much spend in the 14 days (raw purchase value / spend, not shrunk)
COHORT_WINDOWS = {"d14": (0, 13), 0: (0, 29), 1: (30, 59), 2: (60, 89)}      # ads by launch age in days: last 14 days, last 30 days, 31-60, 61-90
COHORT_LABELS = ("Launched in the last 30 days", "Launched 31 to 60 days ago", "Launched 61 to 90 days ago")
TOP_SPEND = 10000.0                    # DISPLAY: what Josh calls a hit (2026-11-10): an ad that reached $10,000 spend; shown as $10K+ / launched ads
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

CONCEPTS_ITERATE = 8                   # new concepts to brief per product per week: iterate section
CONCEPTS_FADED = 6                     # ... was-working-now-isn't section (the lead section)
CONCEPTS_NEW = 6                       # ... and new section (together 20 per product, the weekly brief size)
TOP_FADED, TOP_STOP, TOP_COMPETITOR, TOP_PROPOSAL = 8, 5, 3, 5
W_PROP_ADJ, W_PROP_COMP = 0.5, 0.5     # proposed new clusters: score = 100 x (0.5 adjacent results + 0.5 competitor activity)
MAX_PER_FADED_ROW = 3                  # concepts go to the top rows of the faded list only (budget 6); the rest are listed with 0
TOP_NEW = 6                            # test rows per product (Make more has no row cap since 2026-11-10: every Working / Rising line is listed)
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
STRETCH_WEEKS = 3                      # a 'working' stretch = this many consecutive weeks each at or above the product's weekly ROAS ...
STRETCH_WEEK_MIN = 200.0               # ... with at least this line spend per week ...
STRETCH_MIN_SPEND = 2000.0             # ... and at least this spend over the stretch
STRETCH_MIN_INDEX = 1.10               # ... and the stretch's ROAS at least this many times the product's over the same weeks
FADED_THIN_SPEND = 5000.0              # peak stretch under this spend is flagged thin
COLLAPSE_IDX, COLLAPSE_RATIO = 0.90, 0.70   # COLLAPSED: now index under 0.90 and ROAS at most 0.70x the peak ROAS
WINNER_MIN_SPEND = 1000.0              # an ad is a (former) winner at this lifetime spend with ROAS at or above the product's
DECAY_RATIO = 0.80                     # a live winner whose last-30-day ROAS is under this share of its lifetime ROAS has decayed
TOP_WINNERS = 5
COMP_LONG_DAYS = 60                    # a competitor ad running this long counts as long-running
W_ADJ_C, W_CROSS_C, W_COMP_C, W_ORDER_C, W_STORY_C = 0.40, 0.15, 0.25, 0.12, 0.08   # new-line weights when a competitor file is loaded
ST_WORKING, ST_RISING, ST_FADING, ST_NOT, ST_AVG, ST_EARLY, ST_UNTESTED = ("WORKING NOW", "RISING", "FADING / WAS WORKING", "NOT WORKING", "AVERAGE", "TOO EARLY", "UNTESTED / UNDER-TESTED")
STATUS_USUAL = "RUNNING AS USUAL"                 # the label old snapshots (before 2026-11-10 Action / Status redesign) used for AVERAGE lines; read as Average
STATUSES = (ST_WORKING, ST_RISING, ST_FADING, ST_NOT, ST_AVG, ST_EARLY, ST_UNTESTED)

# ---- Action and Status vocabulary (2026-11-10). Every concept has exactly ONE Action (JSON field `section`, user-facing word "Action") and ONE Status.
# Stored snapshots keep their old values; every read goes through norm_snapshot / norm_section / norm_status / norm_key below (old -> new, idempotent).
ACTIONS = ("revive", "make_more", "test", "add", "stop", "hold")
ACTION_LABEL = {"revive": "Revive", "make_more": "Make more", "test": "Test", "add": "Add to sheet", "stop": "Stop", "hold": "Hold"}
SRC_COMPETITOR, SRC_DRAFT = "Competitor", "Our draft"
OLD_SECTION = {"faded": ("revive", None), "iterate": ("make_more", None), "new": ("test", None), "proposal": ("add", SRC_DRAFT), "competitor": ("add", SRC_COMPETITOR), "stop": ("stop", None), "rest": ("hold", None),
               "other": ("hold", None)}
OLD_FROM_NEW = {"revive": ["faded"], "make_more": ["iterate"], "test": ["new"], "add": ["proposal", "competitor"], "stop": ["stop"], "hold": ["rest", "other"]}
STATUS_LABELS = ("Working", "Rising", "Fading", "Not working", "Average", "Too early", "Untested")
STATUS_LABEL = {ST_WORKING: "Working", ST_RISING: "Rising", ST_FADING: "Fading", ST_NOT: "Not working", ST_AVG: "Average", ST_EARLY: "Too early", ST_UNTESTED: "Untested",
                STATUS_USUAL: "Average", "FADED": "Fading", "COLLAPSED": "Fading", "STOPPED": "Fading", "PROPOSED (draft)": "Untested", "NO LINE": "No line",
                **{k: k for k in STATUS_LABELS}}
REVIVE_KINDS = ("FADED", "COLLAPSED", "STOPPED")
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


def _pc(n, d, nd=0) -> str:
    return f"{100 * n / d:.{nd}f}%" if d else "n/a"


def pacific_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(PT).date()


# ----------------------------------------------------------------------------------------------------------------------- loading
FAM_SQL = """
SELECT m.family_key, m.tracker_row_id, p.slug AS product, g.line_id, g.confidence AS tag_conf, g.method AS tag_method, g.tagger,
       m.spend_launch, m.purchases_launch, m.revenue_launch, m.live,
       cs.first_day, cs.last_spend_day, COALESCE(cs.launched_before_data_start, false) AS before_start, COALESCE(cs.display, m.family_key) AS display,
       hk.hook, hk.confidence AS hook_conf, (m.launch_date IS NOT NULL) AS has_launch, COALESCE(m.launch_date::date, CASE WHEN cs.launched_before_data_start THEN NULL ELSE cs.first_day::date END) AS eff_launch
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
  COALESCE(sum(spend) FILTER (WHERE day > t.d - 14), 0) AS s14, COALESCE(sum(purchases) FILTER (WHERE day > t.d - 14), 0) AS p14, COALESCE(sum(revenue) FILTER (WHERE day > t.d - 14), 0) AS r14,
  COALESCE(sum(spend) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS s60, COALESCE(sum(purchases) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS p60,
  COALESCE(sum(revenue) FILTER (WHERE day <= t.d - 30 AND day > t.d - 90), 0) AS r60
FROM {table} f CROSS JOIN t GROUP BY f.family_key
"""

FUNNEL_SQL = """
SELECT p.slug, count(*) AS launched, count(*) FILTER (WHERE COALESCE(m.spend_launch, 0) < %s) AS under_tested,
       count(*) FILTER (WHERE COALESCE(m.spend_launch, 0) >= %s) AS tested, count(*) FILTER (WHERE COALESCE(m.spend_launch, 0) >= %s) AS top
FROM ad_metrics m JOIN products p ON p.id = m.product_id AND p.slug = ANY(%s)
WHERE m.is_first_row AND (m.launch_date IS NOT NULL OR m.spend_launch > 0)
GROUP BY p.slug
"""

COHORT_SQL = """
SELECT slug, count(*) AS launched, count(*) FILTER (WHERE sp > 0) AS spent, count(*) FILTER (WHERE sp < %s) AS under_tested, count(*) FILTER (WHERE sp >= %s) AS tested, count(*) FILTER (WHERE sp >= %s) AS top
FROM (SELECT p.slug, COALESCE(m.spend_launch, 0) AS sp, (%s::date - COALESCE(m.launch_date::date, CASE WHEN cs.launched_before_data_start THEN NULL ELSE cs.first_day::date END)) AS age
      FROM ad_metrics m JOIN products p ON p.id = m.product_id AND p.slug = ANY(%s) LEFT JOIN creative_stage_moves cs ON cs.family_key = m.family_key
      WHERE m.is_first_row AND (m.launch_date IS NOT NULL OR m.spend_launch > 0)) x
WHERE age BETWEEN %s AND %s GROUP BY slug
"""

WEEKLY_SQL = """
SELECT family_key, date_trunc('week', day)::date AS wk, sum(spend) AS spend, sum(revenue) AS revenue, sum(purchases) AS purchases FROM family_daily GROUP BY 1, 2
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
    for fk, wk, sp, rv, pu in conn.execute(WEEKLY_SQL).fetchall():
        weekly.setdefault(fk, {})[wk] = (_f(sp), _f(rv), _f(pu))
    funnel = {r[0]: {"launched": r[1], "under_tested": r[2], "tested": r[3], "top": r[4]} for r in conn.execute(FUNNEL_SQL, (JUDGED_SPEND, JUDGED_SPEND, TOP_SPEND, list(PRODUCTS))).fetchall()}
    cohorts: dict = {}
    for wkey, (lo, hi) in COHORT_WINDOWS.items():
        for r in conn.execute(COHORT_SQL, (JUDGED_SPEND, JUDGED_SPEND, TOP_SPEND, through, list(PRODUCTS), lo, hi)).fetchall():
            cohorts.setdefault(r[0], {})[wkey] = {"launched": r[1], "spent": r[2], "under_tested": r[3], "tested": r[4], "top": r[5]}
    return {"cohorts": cohorts, "through": through, "lines": lines, "families": fams, "win": win, "weekly": weekly, "funnel": funnel, "competitors": load_competitors(conn)}



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


PROPOSALS_PATH = __file__.rsplit("/", 1)[0] + "/creative_priorities_proposals.json"


def load_proposals() -> list[dict]:
    """Draft new market clusters / lines (Communication Overview shape, story 15 words or fewer) kept in etl/creative_priorities_proposals.json. Josh approves drafts by adding them to the sheet;
    a draft whose ICP already exists on the sheet for that product drops out by itself."""
    try:
        return json.load(open(PROPOSALS_PATH))
    except (OSError, ValueError):
        return []


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

def best_stretch(weekly_line: dict, weekly_prod: dict) -> dict | None:
    """The line's best sustained working stretch: maximal runs of consecutive weeks, each with line spend >= STRETCH_WEEK_MIN and weekly ROAS at or above the product's weekly ROAS,
    at least STRETCH_WEEKS long, spend >= STRETCH_MIN_SPEND and ROAS index (over the same weeks) >= STRETCH_MIN_INDEX. Of those, the run that earned the most revenue above the product
    average. Weeks are Mondays; the window ends on the Sunday of the last week."""
    weeks = sorted(weekly_prod)
    runs, cur = [], []
    for w in weeks:
        ls, lr = weekly_line.get(w, (0.0, 0.0, 0.0))[:2]
        ps, pr = weekly_prod[w][:2]
        ok = ls >= STRETCH_WEEK_MIN and ps > 0 and pr > 0 and (lr / ls) >= (pr / ps)
        if ok and (not cur or (w - cur[-1]).days == 7):
            cur.append(w)
        else:
            if len(cur) >= STRETCH_WEEKS:
                runs.append(cur)
            cur = [w] if ok else []
    if len(cur) >= STRETCH_WEEKS:
        runs.append(cur)
    best = None
    for run in runs:
        ls = sum(weekly_line[w][0] for w in run)
        lr = sum(weekly_line[w][1] for w in run)
        lp = sum((weekly_line[w][2] if len(weekly_line[w]) > 2 else 0.0) for w in run)
        ps = sum(weekly_prod[w][0] for w in run)
        pr = sum(weekly_prod[w][1] for w in run)
        if ls < STRETCH_MIN_SPEND or not ps or not pr:
            continue
        idx = (lr / ls) / (pr / ps)
        if idx < STRETCH_MIN_INDEX:
            continue
        excess = lr - (pr / ps) * ls
        if best is None or excess > best["excess"]:
            best = {"start": run[0].isoformat(), "end": (run[-1] + dt.timedelta(days=6)).isoformat(), "weeks": len(run), "spend": round(ls, 2), "revenue": round(lr, 2), "roas": round(lr / ls, 4),
                    "cpa": round(ls / lp, 2) if lp else None, "index": round(idx, 3), "excess": round(excess, 2), "product_roas": round(pr / ps, 4)}
    return best


def faded_kind(m: dict, stretch: dict | None) -> str | None:
    """STOPPED (no spend in the last 30 days), COLLAPSED (now index under 0.90 and ROAS at most 0.70x the peak) or FADED (now below the product average) for a line that had a working
    stretch; None when it has no stretch or is at or above the product average now."""
    if stretch is None:
        return None
    now = m["idx_30"] if (m["idx_30"] is not None and m["spend_30d"] >= DORMANT_SPEND) else None
    if now is None:
        return "STOPPED"
    if now >= NOW_GOOD:
        return None
    if now < COLLAPSE_IDX and m["roas_30d"] is not None and m["roas_30d"] <= COLLAPSE_RATIO * stretch["roas"]:
        return "COLLAPSED"
    return "FADED"


def former_winners(fams: list[dict], base_roas: float, weekly: dict, through: dt.date) -> tuple[list[dict], int, int]:
    """Ads of a line that reached WINNER_MIN_SPEND with lifetime ROAS at or above the product's and are no longer working: stopped (no spend in the last 3 days) or decayed (live, but
    last-30-day ROAS under DECAY_RATIO x its lifetime ROAS or almost no spend). -> (non-working winners by revenue above average, winners in all, winners still working)."""
    out, total, healthy = [], 0, 0
    for f in fams:
        sp = f["spend_launch"]
        if sp < WINNER_MIN_SPEND or not base_roas or f["revenue_launch"] / sp < base_roas:
            continue
        total += 1
        roas = f["revenue_launch"] / sp
        s30, r30 = _f(f["w"]["s30"]), _f(f["w"]["r30"])
        r30x = r30 / s30 if s30 >= DORMANT_SPEND else None
        if f["live3"] and r30x is not None and r30x >= DECAY_RATIO * roas:
            healthy += 1
            continue
        wk = weekly.get(f["family_key"]) or {}
        peak = max(wk.items(), key=lambda kv: kv[1][1] - base_roas * kv[1][0])[0] if wk else None
        out.append({"ad": f["display"], "ad_id": f["tracker_row_id"], "hook": f["hook"], "spend": round(sp), "roas": round(roas, 2), "kind": "decayed" if f["live3"] else "stopped",
                    "peak_week": peak.isoformat() if peak else None, "stopped": f["last_spend_day"].isoformat() if (f["last_spend_day"] and not f["live3"]) else None,
                    "roas_30d": round(r30x, 2) if r30x is not None else None, "excess": f["revenue_launch"] - base_roas * sp})
    out.sort(key=lambda w: -w["excess"])
    return out, total, healthy


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
    zero = {k: 0.0 for k in ("s_all", "p_all", "r_all", "s30", "p30", "r30", "sp30", "pp30", "rp30", "s7", "s60", "p60", "r60", "s14", "p14", "r14")}

    for f in fams:
        f["w"] = win_meta.get(f["family_key"], zero)
        f["wn"] = win_nc.get(f["family_key"], zero)
        for k in ("spend_launch", "purchases_launch", "revenue_launch"):
            f[k] = _f(f[k])
        f["judged"] = f["spend_launch"] >= JUDGED_SPEND
        age_ok = f["before_start"] or (f["first_day"] is not None and (through - f["first_day"]).days >= MATURE_DAYS)
        f["mature"] = bool(f["judged"] and age_ok)
        f["hit"] = f["spend_launch"] >= HIT_SPEND
        f["top"] = f["spend_launch"] >= TOP_SPEND
        f["launched"] = bool(f.get("has_launch") or f["spend_launch"] > 0)
        f["new30"] = bool(f["first_day"] is not None and not f["before_start"] and (through - f["first_day"]).days < 30 and f["spend_launch"] > 0)
        f["roas30"] = ratio(_f(f["w"]["r30"]), _f(f["w"]["s30"]))
        f["live3"] = bool(f["last_spend_day"] is not None and (through - f["last_spend_day"]).days < 3)

    # product baselines (all of the product's ads with spend, tagged or not)
    base: dict[str, dict] = {}
    for slug in PRODUCTS:
        pf = [f for f in fams if f["product"] == slug]
        b = {"launch": _blank(), "since": _blank(), "d30": _blank(), "d14": _blank(), "prior30": _blank(), "nc_since": _blank(), "nc_d30": _blank(), "then": _blank()}
        for f in pf:
            _acc(b["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
            w, wn = f["w"], f["wn"]
            _acc(b["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
            _acc(b["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
            _acc(b["d14"], _f(w.get("s14")), _f(w.get("p14")), _f(w.get("r14")))
            _acc(b["prior30"], _f(w["sp30"]), _f(w["pp30"]), _f(w["rp30"]))
            _acc(b["then"], _f(w.get("s60")), _f(w.get("p60")), _f(w.get("r60")))
            _acc(b["nc_since"], _f(wn["s_all"]), _f(wn["p_all"]), _f(wn["r_all"]))
            _acc(b["nc_d30"], _f(wn["s30"]), _f(wn["p30"]), _f(wn["r30"]))
        mature = [f for f in pf if f["mature"]]
        b["judged"] = len(mature)
        b["hits"] = sum(1 for f in mature if f["hit"])
        b["hit_rate"] = b["hits"] / b["judged"] if b["judged"] else 0.0
        # display funnel (all time): every ad with a Launch Date or any spend, how many never reached $100, how many were tested ($100+), how many reached $10K
        fn = (inp.get("funnel") or {}).get(slug) or {"launched": sum(1 for f in pf if f["launched"]), "under_tested": sum(1 for f in pf if f["launched"] and f["spend_launch"] < JUDGED_SPEND),
                                                      "tested": sum(1 for f in pf if f["judged"]), "top": sum(1 for f in pf if f["top"])}
        b["funnel"] = fn
        # last 30 days: ads that spent in the window, how many stayed under $100 of window spend, got $100+, got $10K+ inside the window
        ch = (inp.get("cohorts") or {}).get(slug)
        if ch is None:                                            # no SQL cohorts (tests, old inputs): derive from the families in hand
            ch = {}
            for win, (lo, hi) in COHORT_WINDOWS.items():
                lo_ = [f for f in pf if f.get("eff_launch") is not None and lo <= (through - f["eff_launch"]).days <= hi and f["launched"]]
                ch[win] = {"launched": len(lo_), "spent": sum(1 for f in lo_ if f["spend_launch"] > 0), "under_tested": sum(1 for f in lo_ if f["spend_launch"] < JUDGED_SPEND), "tested": sum(1 for f in lo_ if f["spend_launch"] >= JUDGED_SPEND), "top": sum(1 for f in lo_ if f["top"])}
        zero_c = {"launched": 0, "spent": 0, "under_tested": 0, "tested": 0, "top": 0}
        b["cohorts"] = [{"label": lab, "from_days": bk * 30, "to_days": bk * 30 + 29, **ch.get(bk, zero_c)} for bk, lab in enumerate(COHORT_LABELS)]
        b["cohort14"] = {"label": "Launched in the last 14 days", "from_days": 0, "to_days": 13, **ch.get("d14", zero_c)}
        b["live14"] = sum(1 for f in pf if _f(f["w"].get("s14")) > 0)
        b["new14"] = sum(1 for f in pf if f["first_day"] is not None and not f["before_start"] and (through - f["first_day"]).days < 14 and f["spend_launch"] > 0)
        b["no_line14"] = sum(_f(f["w"].get("s14")) for f in pf if f["line_id"] is None or f["line_id"] not in lines)
        s30s = [_f(f["w"]["s30"]) for f in pf if _f(f["w"]["s30"]) > 0]
        b["funnel30"] = {"active": len(s30s), "under_tested": sum(1 for v in s30s if v < JUDGED_SPEND), "tested": sum(1 for v in s30s if v >= JUDGED_SPEND), "top": sum(1 for v in s30s if v >= TOP_SPEND)}
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
        a = agg.setdefault(f["line_id"], {"fams": [], "launch": _blank(), "d30": _blank(), "d14": _blank(), "prior30": _blank(), "nc_since": _blank(), "nc_d30": _blank(), "s7": 0.0,
                                          "since": _blank(), "weak_spend": 0.0, "strong": _blank(), "then": _blank(), "newads": [0.0, 0.0], "oldads": [0.0, 0.0]})
        a["fams"].append(f)
        _acc(a["launch"], f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
        w, wn = f["w"], f["wn"]
        _acc(a["since"], _f(w["s_all"]), _f(w["p_all"]), _f(w["r_all"]))
        _acc(a["d30"], _f(w["s30"]), _f(w["p30"]), _f(w["r30"]))
        _acc(a["d14"], _f(w.get("s14")), _f(w.get("p14")), _f(w.get("r14")))
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
                     hit_idx=None, funnel=None, spend_14d=0.0, purchases_14d=0.0, value_14d=0.0, roas_14d=None, spend_then=0.0, roas_then=None, cpa_then=None, idx_then=None, cpa_30d=None, share_then=0.0, share_now=0.0, new_spend30=0.0, new_rev30=0.0, old_spend30=0.0, old_rev30=0.0, nc_roas=None, nc_idx=None, nc_purchases=0.0, nc_cpa=None, nc_roas_30d=None, spend_share=0.0, launch_share_30d=0.0, since_spend=0.0)
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
        s30w = [_f(f["w"]["s30"]) for f in fm if _f(f["w"]["s30"]) > 0]
        funnel_line = {"launched": sum(1 for f in fm if f["launched"]), "under_tested": sum(1 for f in fm if f["launched"] and not f["judged"]), "tested": sum(1 for f in fm if f["judged"]), "top": sum(1 for f in fm if f["top"]),
                       "launched_30d": sum(1 for f in fm if f["launched"] and f.get("eff_launch") is not None and 0 <= (through - f["eff_launch"]).days < 30), "top_launched_30d": sum(1 for f in fm if f["top"] and f.get("eff_launch") is not None and 0 <= (through - f["eff_launch"]).days < 30),
                       "active_30d": len(s30w), "under_tested_30d": sum(1 for v in s30w if v < JUDGED_SPEND), "tested_30d": sum(1 for v in s30w if v >= JUDGED_SPEND), "top_30d": sum(1 for v in s30w if v >= TOP_SPEND)}
        hr = hits / len(mature) if mature else None
        hit_idx = (shrunk_rate(hits, len(mature), b["hit_rate"]) / b["hit_rate"]) if b["hit_rate"] else None
        nsp, nrv = a["nc_since"]["spend"], a["nc_since"]["revenue"]
        nc_idx = (shrunk_roas(nrv, nsp, b["nc_roas"]) / b["nc_roas"]) if b["nc_roas"] else None
        m.update(ads=sum(1 for f in fm if f["spend_launch"] > 0), tagged_ads=len(fm), roas_strong=(ratio(a["strong"]["revenue"], a["strong"]["spend"]) if a["strong"]["spend"] >= 1000 else None), judged=sum(1 for f in fm if f["judged"]), hits=hits, mature=len(mature),
                 spend=sp, purchases=pu, revenue=rv, roas=roas, cpa=ratio(sp, pu, 2),
                 spend_30d=s30, purchases_30d=a["d30"]["purchases"], roas_30d=ratio(r30, s30),
                 spend_14d=a["d14"]["spend"], purchases_14d=a["d14"]["purchases"], value_14d=a["d14"]["revenue"], roas_14d=(ratio(a["d14"]["revenue"], a["d14"]["spend"]) if a["d14"]["spend"] >= MIN_14D_SPEND else None),
                 spend_prior30=sp30, roas_prior30=ratio(rp30, sp30), spend_7d=a["s7"],
                 launches_30d=sum(1 for f in fm if f["new30"]), live_ads=sum(1 for f in fm if f["live3"]),
                 winners_live=sum(1 for f in fm if f["live3"] and _f(f["w"]["s30"]) >= 500 and f["roas30"] is not None and b["roas_30d"] and f["roas30"] >= b["roas_30d"]),
                 weak_share=(a["weak_spend"] / sp) if sp else 0.0, idx_launch=idx_launch, idx_30=idx_30, hit_rate=hr, hit_idx=hit_idx, funnel=funnel_line,
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
        for wk, t_ in (weekly.get(f["family_key"]) or {}).items():
            sp, rv, pu = t_[0], t_[1], (t_[2] if len(t_) > 2 else 0.0)
            pw = prod_weekly.setdefault(f["product"], {})
            a_, b_, c_ = pw.get(wk, (0.0, 0.0, 0.0))
            pw[wk] = (a_ + sp, b_ + rv, c_ + pu)
            if f["line_id"] in lines:
                lw = line_weekly.setdefault(f["line_id"], {})
                a_, b_, c_ = lw.get(wk, (0.0, 0.0, 0.0))
                lw[wk] = (a_ + sp, b_ + rv, c_ + pu)
    comp = inp.get("competitors")
    comp_by_line, comp_unmatched = map_competitors(comp, lines)
    proposals = load_proposals()
    code_to_lid = {l_["line_code"]: lid_ for lid_, l_ in lines.items()}
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
    # ---- was working, now isn't: weekly history decides (lines with a sustained strong stretch that are now below average, collapsed or idle); never-worked NOT WORKING lines go to Stop
    faded: list[dict] = []
    for lid, l in lines.items():
        m = metrics[lid]
        if m["status"] in (ST_WORKING, ST_RISING, ST_UNTESTED):
            continue
        stt = best_stretch(line_weekly.get(lid, {}), prod_weekly.get(l["product"], {}))
        kind = faded_kind(m, stt)
        if kind is None and stt is None and m["status"] == ST_FADING:        # strong over its lifetime (history before the weekly data) and now idle or below average
            kind = "STOPPED" if m["spend_30d"] < DORMANT_SPEND else "FADED"
        if kind is None:
            continue
        b0 = base[l["product"]]
        wins, wtotal, whealthy = former_winners(fams_by_line.get(lid, []), b0["roas"], weekly, through)
        now_r = m["roas_30d"] if m["spend_30d"] >= DORMANT_SPEND else None
        peak_r = stt["roas"] if stt else (m["roas"] or 0.0)
        drop = clamp(1.0 - (now_r / peak_r)) if (now_r is not None and peak_r) else 1.0
        size = clamp(math.log10(max(stt["excess"] if stt else max((m["idx_launch"] or 1.0) - 1.0, 0.0) * m["spend"], 1000.0) / 1000.0) / math.log10(100))
        strength = clamp((((stt["index"] if stt else m["idx_launch"]) or 1.0) - 1.0) / 0.6)
        score = 100 * (0.45 * size + 0.30 * strength + 0.25 * min(1.0, drop / 0.6)) * (1.0 - WEAK_PENALTY * m["weak_share"])
        faded.append({"line": l, "m": m, "score": score, "kind": kind, "stretch": stt, "wins": wins, "wtotal": wtotal, "whealthy": whealthy, "drop": drop, "comp": {"size": size, "strength": strength, "drop": drop}})
    faded_ids = {f_["line"]["id"] for f_ in faded}
    for lid, l in lines.items():
        if metrics[lid]["status"] == ST_NOT and lid not in faded_ids:
            stop.append({"line": l, "m": metrics[lid], "score": metrics[lid]["spend"], "comp": {}})


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
                    "spend_14d": money(m["spend_14d"]), "purchases_14d": round(m["purchases_14d"]), "value_14d": money(m["value_14d"]), "roas_14d": None,
                    "launch_share_30d": 0.0, "spend_share": 0.0, "live_ads": m["live_ads"], "winners_live": 0, "nc_purchases": round(m["nc_purchases"])}
        return {"spend": money(m["spend"]), "purchases": round(m["purchases"]), "roas": m["roas"], "cpa": m["cpa"], "roas_index": round(m["idx_launch"], 3) if m["idx_launch"] is not None else None,
                "ads": m["ads"], "tagged_ads": m["tagged_ads"], "roas_strong_tags": m["roas_strong"], "judged": m["judged"], "hits": m["hits"], "mature": m["mature"], "hit_rate": round(m["hit_rate"], 4) if m["hit_rate"] is not None else None,
                "hit_rate_index": round(m["hit_idx"], 3) if m["hit_idx"] is not None else None,
                "spend_30d": money(m["spend_30d"]), "roas_30d": m["roas_30d"], "spend_prior30": money(m["spend_prior30"]), "roas_prior30": m["roas_prior30"],
                "spend_7d": money(m["spend_7d"]), "launches_30d": m["launches_30d"], "spend_14d": money(m["spend_14d"]), "purchases_14d": round(m["purchases_14d"]), "value_14d": money(m["value_14d"]), "roas_14d": m["roas_14d"], "launch_share_30d": round(m["launch_share_30d"], 4), "spend_share": round(m["spend_share"], 4),
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
        out.update({"funnel": m.get("funnel"), "roas_then": m["roas_then"], "cpa_then": m["cpa_then"], "spend_then": money(m["spend_then"]), "idx_then": round(m["idx_then"], 3) if m["idx_then"] is not None else None,
                    "idx_30": round(m["idx_30"], 3) if m["idx_30"] is not None else None, "cpa_30d": m["cpa_30d"], "share_then": round(m["share_then"], 4), "share_now": round(m["share_now"], 4)})
        return out

    def x(v):
        return "n/a" if v is None else f"{v:.2f}x"

    def usd(v):
        return "n/a" if v is None else f"${v:,.0f}"

    def then_now(m: dict) -> str:
        return (f"Then (days {THEN_FROM}-{THEN_TO}): ROAS {x(m['roas_then'])}, CPA {usd(m['cpa_then'])}, {pct(m['share_then'])} of product spend. "
                f"Now (30 days): ROAS {x(m['roas_30d'])}, CPA {usd(m['cpa_30d'])}, {pct(m['share_now'])} of product spend. Lifetime ROAS {x(m['roas'])}.")

    def faded_info(lid: int, m: dict, slug: str) -> dict | None:
        last = last_at_or_above(line_weekly.get(lid, {}), prod_weekly.get(slug, {}))
        chg = None
        if m["roas_then"] and m["roas_30d"] is not None and m["spend_30d"] >= DORMANT_SPEND:
            chg = round((m["roas_30d"] / m["roas_then"] - 1) * 100)
        return {"last_at_or_above": last, "roas_change_pct": chg, "idx_then": round(m["idx_then"], 3) if m["idx_then"] is not None else None,
                "idx_now": round(m["idx_30"], 3) if (m["idx_30"] is not None and m["spend_30d"] >= DORMANT_SPEND) else None}

    rows: list[dict] = []
    by_product = {sl: {"iterate": [], "faded": [], "stop": [], "new": []} for sl in PRODUCTS}
    for it in iterate:
        by_product[it["line"]["product"]]["iterate"].append(it)
    for it in faded:
        by_product[it["line"]["product"]]["faded"].append(it)
    for it in stop:
        by_product[it["line"]["product"]]["stop"].append(it)
    for nw in new_rows:
        by_product[nw["line"]["product"]]["new"].append(nw)

    def detail_extras(lid, l, m, slug) -> dict:
        """What every line row's panel shows besides the numbers: its winning ads (with the working stretch for the trend bars). Same data on every Action, Hold included."""
        wins, wtotal, whealthy = former_winners(fams_by_line.get(lid, []), base[slug]["roas"], weekly, through)
        return {"stretch": best_stretch(line_weekly.get(lid, {}), prod_weekly.get(slug, {})),
                "former_winners": [{k_: v_ for k_, v_ in w_.items() if k_ != "excess"} for w_ in wins[:TOP_WINNERS]],
                "winners_summary": {"winners": wtotal, "not_working": len(wins), "stopped": sum(1 for w_ in wins if w_["kind"] == "stopped"), "decayed": sum(1 for w_ in wins if w_["kind"] == "decayed"), "still_working": whealthy,
                                    "live_share": round(whealthy / wtotal, 3) if wtotal else None, "launches_in_peak": None, "launches_last_30d": m["launches_30d"]}}

    def common(section, rank, l, m, b, concepts, score, conf, fl, why, comp_dict, hooks, evidence=None, extras=None):
        cs = comp_sum.get(l["id"])
        return {**(extras or {}), "section": section, "product": l["product"], "rank": rank, "line_id": l["id"], "line_code": l["line_code"], "cluster": l["cluster"], "icp": l["icp"], "pain_point": l["pain_point"],
                "emotional_trigger": l["emotional_trigger"], "story": l["story"], "ingredients": l["ingredients"], "concepts": concepts, "score": round(score, 1), "confidence": conf, "flags": fl,
                "why": why, "status": STATUS_LABEL[m["status"]], "then_now": then_now(m), "competitor": ({k: v for k, v in cs.items()} if cs else None), "components": {k: round(v, 3) for k, v in comp_dict.items()},
                "metrics": metric_block(m, b), "hooks": hooks, "evidence": evidence}

    for slug in PRODUCTS:
        b = base[slug]
        its = sorted(by_product[slug]["iterate"], key=lambda r: (-r["score"], r["line"]["sort_order"], r["line"]["id"]))
        budget_rows = min(len(its), CONCEPTS_ITERATE)                 # the weekly concept budget goes to the best-scoring lines; every other Working / Rising line is listed with 0
        alloc = allocate([r["score"] for r in its[:budget_rows]], CONCEPTS_ITERATE, 1, MAX_PER_ITERATE_ROW) + [0] * (len(its) - budget_rows)
        for i, (it, n) in enumerate(zip(its, alloc), start=1):
            l, m = it["line"], it["m"]
            fl = risk_flags(m)
            if m["spend_30d"] < DORMANT_SPEND:
                fl.append("dormant (retired winner)")
            if m["spend_30d"] >= MIN_RECENT_SPEND and m["spend_prior30"] >= MIN_RECENT_SPEND and m["roas_prior30"] and m["roas_30d"] is not None and m["roas_30d"] / m["roas_prior30"] < SATURATED_RATIO:
                fl.append("fading vs prior 30 days")
            why = (f"ROAS {m['roas']:.2f}x vs {b['roas']:.2f}x for the product (index {m['idx_launch']:.2f}); {pct(m['hit_rate'])} of {m['mature']} mature ads reached ${HIT_SPEND:,.0f} vs {pct(b['hit_rate'])}; "
                   f"{pct(m['launch_share_30d'])} of the product's new ads in 30 days vs {pct(m['spend_share'])} of its spend.")
            rows.append(common("make_more", i, l, m, b, n, it["score"], conf_level(m, [z for z in fl if z in ("thin sample", "weak tags", "few ads") or z.startswith("Upstack")]), fl, why, it["comp"], hooks_for(l["id"]), extras=detail_extras(l["id"], l, m, slug)))
        fd = sorted(by_product[slug]["faded"], key=lambda r: (-r["score"], r["line"]["sort_order"], r["line"]["id"]))[:TOP_FADED]
        budget_rows = min(len(fd), CONCEPTS_FADED)
        alloc = allocate([max(r["score"], 1.0) for r in fd[:budget_rows]], CONCEPTS_FADED, 1, MAX_PER_FADED_ROW) + [0] * (len(fd) - budget_rows)
        for i, (it, n) in enumerate(zip(fd, alloc), start=1):
            l, m, stt, wins = it["line"], it["m"], it["stretch"], it["wins"]
            fi = faded_info(l["id"], m, slug)
            now_on = m["spend_30d"] >= DORMANT_SPEND
            peak_txt = ""
            if stt:
                peak_txt = (f"Weeks of {stt['start']} to {stt['end']} ({stt['weeks']} weeks): ROAS {x(stt['roas'])} vs {x(stt['product_roas'])} for the product over the same weeks (index {stt['index']:.2f}), "
                            f"CPA {usd(stt['cpa'])}, {usd(stt['spend'])} spend.")
            else:
                peak_txt = f"No sustained weekly stretch (the strong results predate the weekly data on 2026-05-06 or were spread thin); lifetime ROAS {x(m['roas'])} vs {x(b['roas'])} for the product on {usd(m['spend'])}."
            now_txt = (f"Last 30 days: ROAS {x(m['roas_30d'])} (index {m['idx_30']:.2f}), CPA {usd(m['cpa_30d'])}, {usd(m['spend_30d'])} spend" if now_on else "No spend in the last 30 days")
            if stt and now_on and m["roas_30d"] is not None and stt["roas"]:
                now_txt += f"; ROAS {round((m['roas_30d'] / stt['roas'] - 1) * 100):+d}% from the peak"
            last = fi["last_at_or_above"]
            if last:
                now_txt += f"; last at or above the product average in the {ROLL_WEEKS} weeks ending {last['window_end']} ({last['weeks_since']} week{'s' if last['weeks_since'] != 1 else ''} ago)"
            now_txt += "."
            launches_peak = None
            if stt:
                s0, e0 = dt.date.fromisoformat(stt["start"]), dt.date.fromisoformat(stt["end"])
                launches_peak = sum(1 for f_ in fams_by_line.get(l["id"], []) if f_["first_day"] and not f_["before_start"] and s0 <= f_["first_day"] <= e0)
                peak_rate = launches_peak * 30.0 / (stt["weeks"] * 7)
            else:
                peak_rate = None
            diag = []
            nw = len(wins)
            if not now_on:
                diag.append(f"No spend in the last 30 days ({nw} of {it['wtotal']} winning ads stopped or decayed): consistent with spend moved away or the ads switched off, not with a delivery decline")
            elif m["share_now"] < 0.5 * m["share_then"] and (m["idx_30"] or 0) >= COLLAPSE_IDX:
                diag.append(f"Share of the product's spend fell from {pct(m['share_then'])} to {pct(m['share_now'])} while the line still runs at index {m['idx_30']:.2f}: consistent with spend moved to other lines")
            new_roas = ratio(m["new_rev30"], m["new_spend30"]) if m["new_spend30"] >= 500 else None
            decayed_live = [w_ for w_ in wins if w_["kind"] == "decayed"]
            if now_on and m["launches_30d"] <= max(1, 0.5 * (peak_rate or 0)) and (decayed_live or m["old_spend30"] >= 500):
                diag.append(f"Few new ads ({m['launches_30d']} in the last 30 days" + (f" against about {peak_rate:.0f} per 30 days in the working stretch" if peak_rate else "") + f") and the ads still running are {len(decayed_live)} decayed winner(s) of {it['wtotal']}: consistent with the same ads fatiguing and no fresh iterations")
            if now_on and new_roas is not None and b["roas_30d"] and new_roas < NOW_BAD * b["roas_30d"]:
                diag.append(f"{m['launches_30d']} new ads launched in the last 30 days run at {x(new_roas)} vs {x(b['roas_30d'])} for the product: consistent with the whole line weakening even with fresh ads")
            if not diag:
                diag.append("No single pattern stands out in the numbers (new ads, ad fatigue and spend share all look ordinary): consistent with partial fatigue; refresh the former winners and watch the next two weeks")
            diagnosis = "; ".join(diag[:2]) + "."
            top = [w_ for w_ in wins if w_["hook"]][:2]
            weak_line = any("whole line weakening" in d_ for d_ in diag)
            play = (f"{n} concept{'s' if n != 1 else ''} this week: " if n else "Lower priority this week (no concepts allocated): ") + (
                ("rework the angle (new story or ICP framing) keeping the structure of the winning hooks " if weak_line else "remake the former winners' hooks with new scenes, openings and proof ") +
                (("(" + "; ".join(f"\"{w_['hook'][:90]}\" ({w_['ad']}" + (f", peak week of {w_['peak_week']}" if w_['peak_week'] else "") + ")" for w_ in top) + ")") if top else "(no hook text stored for the former winners; open them from the list)") +
                f"; test small before scaling against the product's {x(b['roas_30d'])} last-30-day ROAS.")
            fl = risk_flags(m)
            if stt is None:
                fl.append("no sustained weekly peak")
            elif stt["spend"] < FADED_THIN_SPEND:
                fl.append(f"thin: peak stretch only {usd(stt['spend'])}")
            if stt and stt["roas"] < 1.0:
                fl.append(f"peak ROAS {x(stt['roas'])} was above the product average but under 1.0x")
            r = common("revive", i, l, m, b, n, it["score"], conf_level(m, [z for z in fl if z in ("thin sample", "weak tags", "few ads") or z.startswith("Upstack")]), fl,
                       f"{it['kind']}: was working, now isn't. {peak_txt} {now_txt}", it["comp"],
                       [{"hook": w_["hook"], "ad": w_["ad"], "ad_id": w_["ad_id"], "spend": w_["spend"], "roas": w_["roas"], "line_code": l["line_code"], "borrowed": False} for w_ in wins if w_["hook"]][:3])
            r["revive_kind"] = it["kind"]                              # STOPPED / COLLAPSED / FADED; the status itself is the line's one of seven
            r["when_it_worked"] = peak_txt
            r["how_it_stopped"] = now_txt
            r["diagnosis"] = diagnosis
            r["revive_play"] = play
            r["stretch"] = it["stretch"]
            r["faded"] = fi
            r["former_winners"] = [{k_: v_ for k_, v_ in w_.items() if k_ != "excess"} for w_ in wins[:TOP_WINNERS]]
            r["winners_summary"] = {"winners": it["wtotal"], "not_working": nw, "stopped": sum(1 for w_ in wins if w_["kind"] == "stopped"), "decayed": len(decayed_live), "still_working": it["whealthy"],
                                    "live_share": round(it["whealthy"] / it["wtotal"], 3) if it["wtotal"] else None, "launches_in_peak": launches_peak, "launches_last_30d": m["launches_30d"]}
            rows.append(r)
        sp_ = sorted(by_product[slug]["stop"], key=lambda r: (-r["score"], r["line"]["id"]))[:TOP_STOP]
        for i, it in enumerate(sp_, start=1):
            l, m = it["line"], it["m"]
            rows.append(common("stop", i, l, m, b, 0, 0.0, "medium", risk_flags(m),
                               f"Spent {usd(m['spend'])} over {m['judged']} tested ads at {x(m['roas'])} against {x(b['roas'])} for the product (index {m['idx_launch']:.2f}); {('no spend in the last 30 days' if m['spend_30d'] < DORMANT_SPEND else 'last 30 days index ' + format(m['idx_30'], '.2f'))}. Consistent with a line that is weak over its history.",
                               {}, hooks_for(l["id"]), extras=detail_extras(l["id"], l, m, slug)))
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
                                "same": "ICP" if norm(adj_l["icp"]) == norm(l["icp"]) else "cluster", "status": STATUS_LABEL[metrics[adj_l["id"]]["status"]]} if adj_l else None),
                  "cross_product": ({"product": cr_l["product"], "line_code": cr_l["line_code"], "icp": cr_l["icp"], "cluster": cr_l["cluster"], "pain_point": cr_l["pain_point"],
                                     "spend": money(metrics[cr_l["id"]]["spend"]), "roas": metrics[cr_l["id"]]["roas"], "roas_index": round(metrics[cr_l["id"]]["idx_launch"], 3),
                                     "same": "pain point" if norm(cr_l["pain_point"]) == norm(l["pain_point"]) else "cluster", "status": STATUS_LABEL[metrics[cr_l["id"]]["status"]]} if cr_l else None)}
            parts = []
            if adj_l:
                parts.append(f"same {ev['adjacent']['same']} on {adj_l['line_code']} ({adj_l['pain_point']}) spent ${metrics[adj_l['id']]['spend']:,.0f} at {metrics[adj_l['id']]['roas']:.2f}x, index {metrics[adj_l['id']]['idx_launch']:.2f}, {STATUS_LABEL[metrics[adj_l['id']]['status']].lower()}")
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
            rows.append(common("test", i, l, m, b, n, nw["score"], "medium" if ((adj_l and cr_l) or (cs and (adj_l or cr_l))) else "low", fl, why, nw["comp"], hooks, ev))
        # proposed new market clusters (drafts for Josh's approval); evidence = adjacent results of ours + competitor activity, never invented numbers
        claimed: list[str] = []
        have_icps = {norm(x_["icp"]) for x_ in lines.values() if x_["product"] == slug}
        props = []
        for pr in proposals:
            if pr["product"] != slug or norm(pr["icp"]) in have_icps:
                continue
            claimed += [t.lower() for t in pr["match_terms"]]
            adj_items, adj_best = [], 0.0
            for code in pr["adjacent_lines"]:
                lid2 = code_to_lid.get(code)
                if lid2 is None:
                    continue
                m2 = metrics[lid2]
                e = comp_econ(m2) * comp_size(m2) * (1.0 - WEAK_PENALTY * m2["weak_share"]) * proven_factor(m2)
                adj_best = max(adj_best, e)
                adj_items.append({"line_code": code, "status": STATUS_LABEL[m2["status"]], "spend": money(m2["spend"]), "roas": m2["roas"], "roas_index": round(m2["idx_launch"], 3) if m2["idx_launch"] is not None else None})
            mrows = []
            if comp:
                for r in comp["rows"]:
                    if _slug(r["product"]) == _slug(slug) and any(t.lower() in " ".join((r["cluster"], r["icp"], r["pain_point"], r["hook"], r.get("new_angle_label", ""))).lower() for t in pr["match_terms"]):
                        mrows.append({**r, "brand": (r["brand"] or "").split(" (")[0].strip()})
            cs = comp_summary(mrows)
            props.append({"pr": pr, "score": 100 * (W_PROP_ADJ * adj_best + W_PROP_COMP * comp_score(cs)), "adj_items": adj_items, "cs": cs, "adj": adj_best})
        props.sort(key=lambda t: (-t["score"], t["pr"]["cluster"], t["pr"]["icp"]))
        for i, t in enumerate(props[:TOP_PROPOSAL], start=1):
            pr, cs = t["pr"], t["cs"]
            parts = ["Draft for Josh's approval (add to the Communication Overview if approved)."]
            if t["adj_items"]:
                parts.append("Adjacent results of ours: " + "; ".join(f"{a_['line_code']} {a_['status'].lower()}" + (f" at {a_['roas']:.2f}x on ${a_['spend']:,.0f}" if a_["roas"] is not None else "") for a_ in t["adj_items"]) + ".")
            if cs:
                parts.append("Competitors run it: " + cs["text"] + " (running a long time, not proven performance).")
            parts.append(pr["source"])
            rows.append({"section": "add", "source": SRC_DRAFT, "product": slug, "rank": i, "line_id": None, "line_code": "DRAFT", "cluster": pr["cluster"], "icp": pr["icp"], "pain_point": pr["pain_point"],
                         "emotional_trigger": None, "story": pr["story"], "ingredients": "", "concepts": 1, "score": round(t["score"], 1), "confidence": "medium" if (cs and t["adj"] > 0) else "low",
                         "flags": [pr["flag"], "DRAFT for approval", "no performance numbers: evidence is competitor activity and our adjacent results only"], "why": " ".join(parts), "status": STATUS_LABEL[ST_UNTESTED],
                         "then_now": "", "competitor": cs, "components": {"adjacent": round(t["adj"], 3), "competitors": round(comp_score(cs), 3)}, "metrics": {"spend": 0, "tagged_ads": 0, "ads": 0, "judged": 0},
                         "hooks": [{"hook": c_["hook"], "ad": c_["brand"], "ad_id": None, "spend": None, "roas": None, "line_code": c_["brand"], "borrowed": True} for c_ in (cs["hooks"] if cs else [])][:2],
                         "evidence": {"adjacent_lines": t["adj_items"]}, "cluster_description": pr["cluster_description"], "icp_description": pr["icp_description"], "pain_description": pr["pain_description"]})
        # competitor angles that are not on our sheet at all (those already drafted as a proposal above are not repeated)
        if comp:
            groups: dict[tuple, list] = {}
            for r in comp_unmatched:
                if r["product"] == slug and (r["cluster"] or r["icp"] or r["pain_point"]) and not any(t in " ".join((r["cluster"], r["icp"], r["pain_point"], r["hook"])).lower() for t in claimed):
                    groups.setdefault((_nm(r["cluster"]), _nm(r["icp"]), _nm(r["pain_point"])), []).append(r)
            ranked = sorted(groups.values(), key=lambda g_: (-comp_score(comp_summary(g_)), -len(g_), g_[0]["cluster"]))[:TOP_COMPETITOR]
            for i, g_ in enumerate(ranked, start=1):
                cs = comp_summary(g_)
                top = max(g_, key=lambda r: r["days"])
                rows.append({"section": "add", "source": SRC_COMPETITOR, "product": slug, "rank": i, "line_id": None, "line_code": "not on sheet", "cluster": g_[0]["cluster"] or "(not mapped)", "icp": g_[0]["icp"] or "(not mapped)",
                             "pain_point": g_[0]["pain_point"] or "(not mapped)", "emotional_trigger": None, "story": ("Competitor hook: " + top["hook"]) if top["hook"] else "", "ingredients": "", "concepts": 1,
                             "score": round(100 * comp_score(cs), 1), "confidence": "medium" if cs["long_running"] >= 2 and cs["competitors"] >= 2 else "low",
                             "flags": ["not on our Communication Overview", "competitor signal is running a long time, not proven performance"],
                             "why": "Competitors run this angle: " + cs["text"] + ". We have no line for it.", "status": STATUS_LABEL[ST_UNTESTED], "then_now": "", "competitor": cs, "components": {"competitors": round(comp_score(cs), 3)},
                             "metrics": {"spend": 0, "tagged_ads": 0, "ads": 0, "judged": 0}, "hooks": [], "evidence": None,
                             "cluster_description": "As mapped from the competitor ads; not a cluster on our sheet.", "icp_description": "As mapped from the competitor ads.", "pain_description": "As mapped from the competitor ads."})


    # ---- Everything else: every line that has no action row, plus one "ads not mapped to a line" row per product, so the rows add up to the product tiles (lightweight: numbers only)
    in_rows = {r["line_id"] for r in rows if r["line_id"] is not None}
    for slug in PRODUCTS:
        b = base[slug]
        rest = sorted([(lid, metrics[lid]) for lid in lines if lines[lid]["product"] == slug and lid not in in_rows and lid in metrics],
                      key=lambda t: (-t[1]["spend_14d"], -t[1]["spend"], lines[t[0]]["sort_order"], t[0]))
        for i, (lid, m) in enumerate(rest, start=1):
            l = lines[lid]
            if m["spend"] >= JUDGED_SPEND:
                why = f"No action this week. Spent {usd(m['spend'])} at {x(m['roas'])} against {x(b['roas'])} for the product; last 14 days {usd(m['spend_14d'])}."
            else:
                why = f"No action this week. Spent {usd(m['spend'])} so far."
            rows.append(common("hold", i, l, m, b, 0, 0.0, "low", risk_flags(m), why, {}, hooks_for(lid), extras=detail_extras(lid, l, m, slug)))
        nm = [f for f in fams if f["product"] == slug and (f["line_id"] is None or f["line_id"] not in lines)]
        la, d30_, d14_ = _blank(), _blank(), _blank()
        for f in nm:
            _acc(la, f["spend_launch"], f["purchases_launch"], f["revenue_launch"])
            _acc(d30_, _f(f["w"]["s30"]), _f(f["w"]["p30"]), _f(f["w"]["r30"]))
            _acc(d14_, _f(f["w"].get("s14")), _f(f["w"].get("p14")), _f(f["w"].get("r14")))
        mm = {"spend": money(la["spend"]), "purchases": round(la["purchases"]), "roas": ratio(la["revenue"], la["spend"]) if la["spend"] >= JUDGED_SPEND else None, "cpa": ratio(la["spend"], la["purchases"], 2),
              "spend_30d": money(d30_["spend"]), "purchases_30d": round(d30_["purchases"]), "roas_30d": ratio(d30_["revenue"], d30_["spend"]) if d30_["spend"] >= JUDGED_SPEND else None, "cpa_30d": ratio(d30_["spend"], d30_["purchases"], 2),
              "spend_14d": money(d14_["spend"]), "purchases_14d": round(d14_["purchases"]), "value_14d": money(d14_["revenue"]), "roas_14d": ratio(d14_["revenue"], d14_["spend"]) if d14_["spend"] >= MIN_14D_SPEND else None,
              "ads": len(nm), "live_ads": sum(1 for f in nm if f["live3"]), "launches_30d": sum(1 for f in nm if f["new30"])}
        if not nm:                                                    # on prod every dollar sits on a tagged ad family (0% unmapped); the row exists only when something is unmapped
            continue
        top_nm = sorted(nm, key=lambda f: -f["spend_launch"])[:15]
        rows.append({"section": "hold", "product": slug, "rank": 10 ** 6, "line_id": None, "line_code": "no line", "cluster": "Ads without a line yet", "icp": "Untagged ads", "pain_point": "Not a concept line", "unmapped_ads": [{"ad": f["display"], "spend": money(f["spend_launch"]), "spend_14d": money(_f(f["w"].get("s14"))), "tagger": f.get("tagger") or ""} for f in top_nm],
                     "emotional_trigger": "", "story": "Meta spend on ads that no line claims yet, kept here so the rows add up to the product tile.", "ingredients": "", "concepts": 0, "score": 0.0, "confidence": "low", "flags": [],
                     "why": f"{len(nm)} ads with spend carry no line; {usd(la['spend'])} all time.", "status": "No line", "then_now": "", "competitor": None, "components": {}, "metrics": mm, "hooks": [], "evidence": None,
                     "cluster_description": "Spend that no line tag claims.", "icp_description": "Ads without a tag or without a tracker row.", "pain_description": "Not a concept line; it exists so the rows add up to the product tile."})

    names = {l["product"]: l["product_name"] for l in inp["lines"]}
    for r in rows:
        r["product_name"] = names.get(r["product"], r["product"])
        if r["line_id"] is not None:
            r.update(describe(inp["lines"], lines[r["line_id"]]))
    baselines = {s: {"product_name": names.get(s, s), "roas": round(b["roas"], 4), "cpa": b["cpa"], "spend": money(b["launch"]["spend"]), "roas_30d": round(b["roas_30d"], 4), "roas_prior30": round(b["roas_prior30"], 4), "roas_then": round(b["roas_then"], 4),
                     "hit_rate": round(b["hit_rate"], 4), "judged": b["judged"], "hits": b["hits"], "hit_spend": TOP_SPEND, "tested_spend": JUDGED_SPEND,
                     "spend_14d": money(b["d14"]["spend"]), "roas_14d": ratio(b["d14"]["revenue"], b["d14"]["spend"]), "funnel": b["funnel"], "funnel_30d": b["funnel30"], "cohorts": b["cohorts"], "cohort_14d": b["cohort14"], "purchases_14d": round(b["d14"]["purchases"]), "value_14d": money(b["d14"]["revenue"]), "cpa_14d": ratio(b["d14"]["spend"], b["d14"]["purchases"], 2), "live_14d": b["live14"], "new_ads_14d": b["new14"], "spend_14d_no_line": money(b["no_line14"]), "spend_30d": money(b["d30"]["spend"]), "purchases_30d": round(b["d30"]["purchases"]), "cpa_30d": ratio(b["d30"]["spend"], b["d30"]["purchases"], 2),
                     "purchases": round(b["launch"]["purchases"]), "live_ads": sum(1 for f in fams if f["product"] == s and f["live3"]), "data_through": str(through), "new_ads_30d": b["new30"], "nc_roas": round(b["nc_roas"], 4)} for s, b in base.items()}
    tagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] in lines) for s in PRODUCTS}
    untagged_spend = {s: sum(f["spend_launch"] for f in fams if f["product"] == s and f["line_id"] not in lines) for s in PRODUCTS}
    auto_fams = sum(1 for f in fams if (f["tagger"] or "").startswith("auto-"))
    thresholds = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)}
    out_stats = {"data_through": through.isoformat(), "week_start": week.isoformat(), "status_counts": {sl: {STATUS_LABEL[st]: n for st, n in c.items()} for sl, c in status_counts.items()}, "status_counts_total": {STATUS_LABEL[st]: sum(c[st] for c in status_counts.values()) for st in STATUSES},
                 "iterate_candidates": len(iterate), "faded_candidates": len(faded), "faded_by_kind": {k_: sum(1 for f_ in faded if f_["kind"] == k_) for k_ in ("FADED", "COLLAPSED", "STOPPED")}, "stop_candidates": len(stop), "new_candidates": len(new_rows),
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
SECTION_ORDER = ACTIONS
SECTION_TITLES = {"revive": "Revive: beat the product average 3+ weeks in a row, now below it or off", "make_more": "Make more: working or rising", "test": "Test: on our sheet, 0 to 1 ads run",
                  "add": "Add to sheet: not on our sheet yet (competitor angles and our drafts)", "stop": "Stop: enough spend, consistently below the product average", "hold": "Hold: no action this week"}


def norm_section(sec) -> str:
    """Old section -> new Action (new values and anything unknown pass through)."""
    return OLD_SECTION[sec][0] if sec in OLD_SECTION else sec


def norm_status(st):
    return STATUS_LABEL.get(st, st)


def norm_key(key):
    """A row key product|section|cluster|icp|pain_point with an old section rewritten to the new Action (manual:<id> keys and new keys unchanged)."""
    if not isinstance(key, str) or key.startswith("manual:"):
        return key
    parts = key.split("|")
    if len(parts) >= 5 and parts[1] in OLD_SECTION:
        parts[1] = OLD_SECTION[parts[1]][0]
        return "|".join(parts)
    return key


def key_variants(key: str) -> list[str]:
    """The normalised key plus every old-section form of it (for deleting / looking up state stored under an old key)."""
    key = norm_key(key)
    if not isinstance(key, str) or key.startswith("manual:"):
        return [key]
    parts = key.split("|")
    if len(parts) < 5:
        return [key]
    return [key] + ["|".join([parts[0], o, *parts[2:]]) for o in OLD_FROM_NEW.get(parts[1], [])]


def norm_row(r: dict) -> dict:
    """One row in the new vocabulary: section = Action, `source` on Add rows, status = one of the seven labels (old Revive rows keep their old kind in `revive_kind`). Idempotent."""
    out = dict(r)
    old = r.get("section")
    if old in OLD_SECTION:
        out["section"], src = OLD_SECTION[old]
        if src and not out.get("source"):
            out["source"] = src
    st = r.get("status")
    if st in REVIVE_KINDS and not out.get("revive_kind"):
        out["revive_kind"] = st
    if st is not None:
        out["status"] = norm_status(st)
    return out


def norm_stats(stats):
    if not isinstance(stats, dict):
        return stats
    out = dict(stats)
    def fold(c):
        o = {}
        for k, v in (c or {}).items():
            o[norm_status(k)] = o.get(norm_status(k), 0) + v
        return o
    if "status_counts_total" in out:
        out["status_counts_total"] = fold(out["status_counts_total"])
    if isinstance(out.get("status_counts"), dict):
        out["status_counts"] = {k: fold(v) for k, v in out["status_counts"].items()}
    return out


def norm_snapshot(snap: dict) -> dict:
    """A snapshot / saved-view payload with rows, stats and filters in the new vocabulary. Never writes anything: stored snapshots keep their old values."""
    out = dict(snap)
    out["rows"] = [norm_row(r) for r in snap.get("rows", [])]
    if "stats" in out:
        out["stats"] = norm_stats(out["stats"])
    return out


def definitions(thr: dict | None = None) -> dict:
    """The rules printed on /priorities, with the real thresholds (from the snapshot's stored thresholds, else the constants in this module)."""
    g = {**{k: v for k, v in globals().items() if k.isupper()}, **(thr or {})}
    d0 = lambda v: f"${v:,.0f}"
    idx = lambda v: f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v)}"
    return {
        "actions": [
            {"key": "revive", "label": "Revive", "rule": f"Beat the product average for {g['STRETCH_WEEKS']} or more weeks in a row (at least {d0(g['STRETCH_WEEK_MIN'])} spent a week and {idx(g['STRETCH_MIN_INDEX'])}x the product's ROAS over the stretch), and is now below it or switched off."},
            {"key": "make_more", "label": "Make more", "rule": "Status is Working or Rising. Every one is listed, best score first; the top " + str(g["CONCEPTS_ITERATE"]) + " per product share this week's new concepts."},
            {"key": "test", "label": "Test", "rule": f"On our sheet, with 0 to {g['NEW_MAX_ADS']} ad run (under {d0(g['NEW_MAX_SPEND'])} spent). Up to {g['TOP_NEW']} per product, best evidence from similar lines and competitors first."},
            {"key": "add", "label": "Add to sheet", "rule": f"Not on our sheet yet. Source is Competitor (an angle competitors run that we have no line for, up to {g['TOP_COMPETITOR']} per product) or Our draft (a proposal, up to {g['TOP_PROPOSAL']} per product)."},
            {"key": "stop", "label": "Stop", "rule": f"Spent at least {d0(g['MIN_LINE_SPEND'])} and is consistently below the product average (status Not working), and never had a strong stretch. Up to {g['TOP_STOP']} per product."},
            {"key": "hold", "label": "Hold", "rule": "Everything else: average, too early to judge, or not picked for another action. No action this week."}],
        "statuses": [
            {"label": "Working", "rule": f"Beats the product average over the last 30 days and since launch (since launch at least {idx(g['INDEX_BAR'])}x the product's ROAS), on at least {d0(g['MIN_LINE_SPEND'])} spent, {g['MIN_LINE_PURCHASES']} purchases and {g['MIN_LINE_ADS']} ads that spent {d0(g['JUDGED_SPEND'])}."},
            {"label": "Rising", "rule": f"Above the product average now and at least {round((g['RISE_RATIO'] - 1) * 100)}% better than days {g['THEN_FROM']} to {g['THEN_TO']}."},
            {"label": "Fading", "rule": f"Was above the product average and now is below it, down {round((1 - g['FALL_RATIO']) * 100)}% or more from days {g['THEN_FROM']} to {g['THEN_TO']}, or has had no spend for 30 days."},
            {"label": "Not working", "rule": f"Enough spend to judge and below the product average: under {idx(g['NOT_WORK_LIFE'])}x since launch and under {idx(g['NOW_BAD'])}x in the last 30 days."},
            {"label": "Average", "rule": "Enough spend to judge and close to the product average."},
            {"label": "Too early", "rule": f"Under {d0(g['MIN_LINE_SPEND'])} spent, {g['MIN_LINE_PURCHASES']} purchases or {g['MIN_LINE_ADS']} ads that spent {d0(g['JUDGED_SPEND'])}, so it cannot be judged yet."},
            {"label": "Untested", "rule": f"Under {d0(g['NEW_MAX_SPEND'])} spent and at most {g['NEW_MAX_ADS']} ad."}]}
CSV_COLS = ["market_cluster", "icp", "pain_point", "story", "product", "section", "market_cluster_description", "icp_description", "pain_point_description", "status", "rank", "concepts", "week_start",
            "then_vs_now", "when_it_worked", "how_it_stopped", "diagnosis", "revive_play", "former_winner_ads", "what_others_are_running", "emotional_trigger", "ingredients", "line_code", "confidence", "flags", "score",
            "spend", "roas", "cpa", "roas_index", "ads", "hit_rate", "hit_rate_index", "spend_30d", "roas_30d", "spend_prior30", "roas_prior30", "launches_30d", "nc_roas", "nc_cpa",
            "hook_1", "hook_2", "hook_3", "why", "source", "notes",
            "ads_launched", "ads_under_100_spend", "ads_100_plus_spend", "ads_10k_plus_spend", "hit_rate_10k_of_launched", "hit_rate_10k_of_tested", "spend_14d", "roas_14d", "ads_launched_last_30d", "ads_10k_plus_of_launched_last_30d", "add_source"]


def _row_key(r: dict) -> str:
    """Same join as app.creative_priorities.row_key (kept here so etl does not import the app)."""
    if r.get("manual"):
        return str(r["key"])
    return "|".join(str(r.get(k)) for k in ("product", "section", "cluster", "icp", "pain_point"))


def _funnel_cells(fn: dict | None) -> list:
    if not fn:
        return [""] * 6
    la, te, tp = fn["launched"], fn["tested"], fn["top"]
    return [la, fn["under_tested"], te, tp, round(tp / la, 4) if la else "", round(tp / te, 4) if te else ""]


def to_csv(snapshot: dict, briefed: dict | None = None) -> str:
    """briefed (view exports only): {row key: {at, note}} appends the columns briefed (yes / blank), briefed_at and briefed_note after every other column."""
    snapshot = norm_snapshot(snapshot)                                  # old stored snapshots read in the new vocabulary
    briefed = {norm_key(k): v for k, v in briefed.items()} if briefed is not None else None
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLS + (["briefed", "briefed_at", "briefed_note"] if briefed is not None else []))
    for r in sorted(snapshot["rows"], key=lambda r: (PRODUCTS.index(r["product"]) if r["product"] in PRODUCTS else 99, SECTION_ORDER.index(r["section"]) if r["section"] in SECTION_ORDER else len(SECTION_ORDER), r["rank"])):
        m = r["metrics"]
        g = lambda k: m.get(k)
        hk = [h["hook"] + (f" [{h['line_code']}]" if h.get("borrowed") else "") for h in r["hooks"]] + ["", "", ""]
        comp = (r.get("competitor") or {}).get("text", "")
        row = ([r["cluster"], r["icp"], r["pain_point"], r["story"] or "", r["product_name"], r["section"], r["cluster_description"], r["icp_description"], r["pain_description"], r.get("status", ""), r["rank"],
                    r["concepts"], snapshot["week_start"], r.get("then_now", ""), r.get("when_it_worked", ""), r.get("how_it_stopped", ""), r.get("diagnosis", ""), r.get("revive_play", ""),
                    " | ".join(f"{w_['ad']} ({w_['kind']}, ${w_['spend']:,} at {w_['roas']}x, peak week {w_['peak_week']}" + (f", stopped {w_['stopped']}" if w_.get("stopped") else "") + ")" for w_ in r.get("former_winners", [])),
                    comp, r["emotional_trigger"] or "", r["ingredients"], r["line_code"], r["confidence"],
                    "; ".join(r["flags"]), r["score"], g("spend"), g("roas"), g("cpa"), g("roas_index"), g("ads"), g("hit_rate"), g("hit_rate_index"), g("spend_30d"), g("roas_30d"), g("spend_prior30"),
                    g("roas_prior30"), g("launches_30d"), g("nc_roas"), g("nc_cpa"), hk[0], hk[1], hk[2], r["why"], "manual" if r.get("manual") else "auto", r.get("notes", "")] + _funnel_cells(m.get("funnel")) + [g("spend_14d") if g("spend_14d") is not None else "", g("roas_14d") if g("roas_14d") is not None else "", *(((m.get("funnel") or {}).get("launched_30d", ""), (m.get("funnel") or {}).get("top_launched_30d", ""))), r.get("source", "") if r.get("section") == "add" else ""])
        formula = lambda c: "'" + c if isinstance(c, str) and c[:1] in ("=", "+", "-", "@", "\t") else c
        if r.get("manual"):          # typed by hand: a leading = + - @ would run as a formula when the CSV is opened in Sheets / Excel
            row = [formula(c) for c in row]
        if briefed is not None:
            b = briefed.get(_row_key(r)) or {}
            row += ["yes" if b else "", b.get("at", ""), formula(b.get("note", ""))]
        w.writerow(row)
    return buf.getvalue()


def to_markdown(snapshot: dict) -> str:
    snapshot = norm_snapshot(snapshot)
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
        fn, f3 = b.get("funnel"), b.get("funnel_30d")
        if fn:
            L.append(f"- {b['product_name']} average, ALL TIME: ROAS {b['roas']:.2f}x, CPA {cpa}; ads launched {fn['launched']:,}: under ${JUDGED_SPEND:,.0f} spend {fn['under_tested']:,} ({_pc(fn['under_tested'], fn['launched'])}), "
                     f"${JUDGED_SPEND:,.0f}+ {fn['tested']:,}, ${TOP_SPEND:,.0f}+ (hits) {fn['top']:,}; {fn['top']:,} of {fn['launched']:,} launched ads hit ${TOP_SPEND:,.0f}+ spend ({_pc(fn['top'], fn['launched'], 1)}); {fn['top']:,} of {fn['tested']:,} ads that spent ${JUDGED_SPEND:,.0f}+ ({_pc(fn['top'], fn['tested'], 1)}). "
                     f"LAST 30 DAYS: ROAS {b['roas_30d']:.2f}x, CPA {'n/a' if b.get('cpa_30d') is None else '${:.0f}'.format(b['cpa_30d'])}; {b['new_ads_30d']} new ads with spend; " + "; ".join(f"{c['label'].lower()}: {c['top']:,} of {c['launched']:,} already hit ${TOP_SPEND:,.0f}+ (under ${JUDGED_SPEND:,.0f}: {c['under_tested']:,}, ${JUDGED_SPEND:,.0f}+: {c['tested']:,})" for c in b.get('cohorts', [])))
        else:
            L.append(f"- {b['product_name']} average: ROAS {b['roas']:.2f}x all time, {b['roas_30d']:.2f}x last 30 days, CPA {cpa}, {b['new_ads_30d']} new ads in 30 days")
    for slug in PRODUCTS:
        pr = [r for r in rows if r["product"] == slug]
        if not pr:
            continue
        L += ["", f"## {pr[0]['product_name']}"]
        for sec in SECTION_ORDER[:-1]:                                  # the markdown brief is the action sections only (not "Everything else")
            sr = [r for r in pr if r["section"] == sec]
            if not sr:
                continue
            L += ["", f"### {SECTION_TITLES[sec]}", ""]
            for r in sr:
                m = r["metrics"]
                L.append(f"**{r['rank']}. {r['cluster']} → {r['icp']} → {r['pain_point']}**  [{r['status']}{', ' + r['source'] if r.get('source') else ''}]  ")
                L.append(f"Story: {r['story'] or ''}  ")
                L.append(f"_Cluster: {r['cluster_description']} ICP: {r['icp_description']} Pain point: {r['pain_description']}_  ")
                if r.get("then_now"):
                    L.append(f"{r['then_now']}  ")
                if r.get("competitor"):
                    L.append(f"What others are running: {r['competitor']['text']} (running a long time, not proven performance)  ")
                extra = f"{r['concepts']} concept(s)" if r["concepts"] else "no new concepts"
                if sec == "make_more":
                    extra += f" | hit rate {pct(m.get('hit_rate'))}"
                L.append(f"_{extra} | confidence {r['confidence']}{' | ' + '; '.join(r['flags']) if r['flags'] else ''}_  ")
                L.append(f"Why: {r['why']}  ")
                if r.get("when_it_worked"):
                    L.append(f"When it worked: {r['when_it_worked']}  ")
                    L.append(f"When and how it stopped: {r['how_it_stopped']}  ")
                    L.append(f"Diagnosis (consistent with): {r['diagnosis']}  ")
                    L.append(f"Revive play: {r['revive_play']}  ")
                    for w_ in r.get("former_winners", [])[:3]:
                        L.append(f"- Former winner: {w_['ad']} ({w_['kind']}), ${w_['spend']:,} at {w_['roas']}x, peak week {w_['peak_week']}" + (f", stopped {w_['stopped']}" if w_.get("stopped") else "") + (f": {w_['hook']}" if w_["hook"] else "") + "  ")
                if r["hooks"]:
                    L.append(("Hooks from the working period: " if sec == "revive" else "Hooks: ") + " / ".join(h["hook"] for h in r["hooks"]))
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
