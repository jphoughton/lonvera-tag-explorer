"""Test to scale (/promotions): creatives that moved from TESTING to SCALING, and what happened around the move. Meta only (Google has no creative grain).

A MOVE = a creative (tracker family) had spend in a TESTING campaign and its first spend in a SCALING campaign came on a later day. Meta cannot move an ad between
campaigns; the buyer duplicates the creative into a scaling campaign, so the move is read off the daily spend. The per-creative facts come from the SQL view
creative_stage_moves (migration 015; one variant per Meta | Upstack source, swapped per request by queries.source_sql), the ad sets a creative scaled in from
ad_daily x meta_ads x meta_campaigns. Everything here is a ratio of sums over the same rows the page lists, never an average of ratios.

Populations (all within the window, after the Product / Hide bulk / Min test spend filters):
  moved       creatives whose first scaling day is in the window (event view: the table, the chart, the testing-vs-scaling numbers)
  launched    creatives whose first spend day (any stage) is in the window, not already running on the first day of data (cohort view)
    testers     launched creatives that began in testing (moved or never moved): the base of '% reached scaling'
    no test     launched straight into scaling (or scaling first / same day / stage unknown): not testers, never in a test-to-scale rate
"""
from __future__ import annotations

import collections
import datetime as dt
import re
import statistics
from typing import Any

from werkzeug.datastructures import MultiDict

from . import queries as q

DEFAULT_WINDOW = "30d"
BULK_MIN_DEFAULT = 40                       # creatives entering scaling on one day: this many or more = a mass relaunch, not a test winner
DROP_ARGS = ("compare", "cfrom", "cto", "perday", "stage", "active")    # the page is about stages; comparison and Active only do not apply
NOT_SCALED_LIMIT = 1000
QUERY_TIMEOUT_MS = 8000                     # statement_timeout for this endpoint (the app default is 20 s, the gateway's limit)
PLACEMENTS_SHOWN = 8                        # ad sets listed per creative (the counts cover all of them)
HELD_MIN_SPEND = 200.0                      # scaling spend and days since the move below which 'held up?' says too early
HELD_MIN_DAYS = 3
HELD_OK, HELD_SOFT = 0.90, 0.70             # scaling ROAS / testing ROAS: held up from 0.90, softer from 0.70, below that fell
HELD_RULE = (f"Scaling ROAS since the move divided by testing ROAS before it: held up at {HELD_OK:.0%} or more, softer from {HELD_SOFT:.0%}, fell below. "
             f"'Too early' until the creative has spent ${HELD_MIN_SPEND:,.0f} and run {HELD_MIN_DAYS} days in scaling.")
MOVE_DEFINITION = ("A creative moved when it spent in a Testing campaign and its first spend in a Scaling campaign came on a later day. Meta cannot move an ad between "
                   "campaigns: the buyer duplicates the creative into a scaling campaign, so the move is read from the daily spend (a campaign is Testing when its name "
                   "contains 'test').")
BULK_NOTE = ("A day when {n} or more creatives entered scaling is a bulk move (a mass relaunch into new scaling campaigns), not a set of test winners.")
NO_TEST_PATHS = ("scaling_only", "same_day", "scaling_first", "unclassified_only")

_RX_MONEY = re.compile(r"[0-9]{1,7}(\.[0-9]{1,2})?")
_FALSE, _TRUE = ("", "0", "false", "no", "off"), ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------------------------------------------- parameters

def _flag(args, name: str) -> bool:
    raw = args.get(name)
    if raw is None:
        return False
    v = str(raw).strip().lower()
    if v in _FALSE:
        return False
    if v in _TRUE:
        return True
    raise q.ApiError(400, f"{name} must be 1 or 0")


def _money(args, name: str) -> float:
    raw = args.get(name)
    if raw is None or str(raw).strip() == "":
        return 0.0
    if not _RX_MONEY.fullmatch(str(raw).strip()):
        raise q.ApiError(400, f"{name} must be a number of dollars, 0 or more")
    return float(raw)


def parse_window(args, meta: q.DataMeta) -> q.WindowSpec:
    """The page's window: days=<n> (the last n days of data), or window=launch|1d|..|180d|mtd, or from/to; none of them = the last 30 days. Stage, Active only and
    comparison are ignored. Source / basis ride along inside the WindowSpec."""
    a = MultiDict(args)
    for k in DROP_ARGS:
        a.pop(k, None)
    days = a.pop("days", None)
    if days is not None and str(days).strip() != "":
        n = q.parse_uint(str(days).strip(), 1, 366, "days must be an integer from 1 to 366")
        a.pop("window", None)
        a["to"] = meta.data_through.isoformat()
        a["from"] = max(meta.daily_start, meta.data_through - dt.timedelta(days=n - 1)).isoformat()
    elif not any((a.get(k) or "").strip() for k in ("window", "from", "to")):
        a["window"] = DEFAULT_WINDOW
    return q.parse_window(a, meta)


def _info(w: q.WindowSpec, meta: q.DataMeta) -> dict:
    """window_info; Since launch here means every day held (the data starts on a fixed day, there is no per-creative launch to anchor to)."""
    info = w.info(meta)
    if w.is_launch:
        info["label"] = f"All days held · {q._fmt_day(meta.daily_start, True)} – {q._fmt_day(meta.data_through, True)}"
        base = "Since launch does not apply here; showing every day held."
        info["note"] = base + (f" {info['note']}" if info["note"] else "")
    return info


# ---------------------------------------------------------------------------------------------------------------- small helpers

def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


def _block(spend: float, purchases: float, revenue: float, clicks: float | None = None, days: int | None = None) -> dict:
    out = {"spend": q.f2(spend), "purchases": q.num(purchases), "revenue": q.f2(revenue), "roas": q.ratio(revenue, spend, 4), "cpa": q.ratio(spend, purchases, 2)}
    if clicks is not None:
        out["link_clicks"] = q.num(clicks)
    if days is not None:
        out["days"] = int(days)
    return out


def held_up(r: dict) -> dict:
    """The 'held up?' call on one moved creative: scaling ROAS since the move against testing ROAS before it (see HELD_RULE)."""
    sp, rv = r["scale_spend"], r["scale_revenue"]
    if sp < HELD_MIN_SPEND or r["scale_days"] < HELD_MIN_DAYS:
        return {"state": "too_early", "ratio": None}
    if r["test_spend"] <= 0 or r["test_revenue"] <= 0:
        return {"state": "no_baseline", "ratio": None}
    ratio = (rv / sp) / (r["test_revenue"] / r["test_spend"])
    return {"state": "held" if ratio >= HELD_OK else "softer" if ratio >= HELD_SOFT else "fell", "ratio": round(ratio, 4)}


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _median(vals: list[float]) -> float | None:
    return round(float(statistics.median(vals)), 1) if vals else None


# ---------------------------------------------------------------------------------------------------------------- data

_NUM = ("test_spend", "test_purchases", "test_revenue", "test_link_clicks", "test_after_spend", "scale_spend", "scale_purchases", "scale_revenue",
        "scale_link_clicks", "unclassified_spend", "last7_spend", "last7_purchases", "last7_revenue", "last7_scale_spend", "last7_scale_purchases",
        "last7_scale_revenue")


def normalize_row(r: dict) -> dict:
    """A creative_stage_moves row with plain floats / ints and the 'no product' mapping (shared with the UI tests' mock server)."""
    for k in _NUM:
        r[k] = _f(r[k])
    r["test_days"], r["scale_days"], r["moves_that_day"] = int(r["test_days"]), int(r["scale_days"]), int(r["moves_that_day"])
    r["product_slug"] = r["product_slug"] if r["product_slug"] and r["product_slug"] != "unassigned" else q.NO_PRODUCT_SLUG
    r["product_name"] = r["product_name"] if r["product_slug"] != q.NO_PRODUCT_SLUG else q.NO_PRODUCT
    return r


def _fetch(conn, lo: dt.date, hi: dt.date) -> list[dict]:
    """Creatives that moved in [lo, hi] or first spent in it: one row each, numbers as floats."""
    return [normalize_row(r) for r in q.rows_of(conn, """
        SELECT * FROM creative_stage_moves
        WHERE (path = 'moved' AND scaling_first_day BETWEEN %(lo)s AND %(hi)s) OR (first_day BETWEEN %(lo)s AND %(hi)s)""", {"lo": lo, "hi": hi})]


def _thumbs(conn, family_keys: list[str]) -> dict[str, int]:
    if not family_keys:
        return {}
    return {r["family_key"]: r["id"] for r in q.rows_of(
        conn, "SELECT DISTINCT ON (family_key) family_key, id FROM creative_assets WHERE kind = 'thumb' AND family_key = ANY(%s) ORDER BY family_key, idx", (family_keys,))}


def _placements(conn, family_keys: list[str]) -> dict[str, dict]:
    """Where each moved creative scales: its SCALING-campaign ad sets since the move, read from the precomputed creative_scaling_placements (migration 016,
    built from ad_daily x meta_ads x meta_campaigns mapped to the creative the way family_daily is built), so the spend adds up to the creative's scaling spend."""
    if not family_keys:
        return {}
    got = q.rows_of(conn, """
        SELECT family_key AS fk, campaign_id, campaign, adset_id, adset, ads, spend, purchases, revenue, first_day, last_day
        FROM creative_scaling_placements WHERE family_key = ANY(%(fks)s)""", {"fks": family_keys})
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for r in got:
        by[r["fk"]].append(r)
    out = {}
    for fk, lst in by.items():
        lst.sort(key=lambda r: (-_f(r["spend"]), r["campaign"], r["adset"]))
        out[fk] = {"campaigns": len({r["campaign_id"] for r in lst}), "adsets": len(lst), "ads": sum(int(r["ads"]) for r in lst),
                   "spend": q.f2(sum(_f(r["spend"]) for r in lst)),
                   "top": [{"campaign_id": r["campaign_id"], "campaign": r["campaign"], "adset_id": r["adset_id"], "adset": r["adset"], "ads": int(r["ads"]),
                            **_block(_f(r["spend"]), _f(r["purchases"]), _f(r["revenue"])), "first_day": r["first_day"], "last_day": r["last_day"]}
                           for r in lst[:PLACEMENTS_SHOWN]],
                   "more": max(0, len(lst) - PLACEMENTS_SHOWN)}
    return out


def built_info(conn) -> dict | None:
    """When the precomputed tables were last rebuilt and the last day of data they cover (None = never built)."""
    kv = {r["key"]: r["value"] for r in q.rows_of(conn, "SELECT key, value FROM app_meta WHERE key IN ('stage_moves_built_at', 'stage_moves_data_through')")}
    if not kv.get("stage_moves_built_at"):
        return None
    return {"built_at": kv["stage_moves_built_at"], "data_through": kv.get("stage_moves_data_through") or None}


def _scale_by_day(conn, family_keys: list[str], lo: dt.date, hi: dt.date) -> dict[dt.date, dict]:
    if not family_keys:
        return {}
    return {r["day"]: r for r in q.rows_of(conn, """
        SELECT day, sum(spend) AS spend, sum(purchases) AS purchases, sum(revenue) AS revenue FROM family_daily
        WHERE stage = 'scaling' AND family_key = ANY(%(fks)s) AND day BETWEEN %(lo)s AND %(hi)s GROUP BY day""", {"fks": family_keys, "lo": lo, "hi": hi})}


# ---------------------------------------------------------------------------------------------------------------- shaping

def _identity(r: dict, thumbs: dict, hooks: dict) -> dict:
    hk = hooks.get(r["tracker_row_id"])
    return {"family_key": r["family_key"], "display": r["display"] or r["family_key"], "primary_id": r["tracker_row_id"], "thumb_asset_id": thumbs.get(r["family_key"]),
            "product": {"slug": r["product_slug"], "name": r["product_name"]}, "ad_type": r["ad_type"], "status": r["tracker_status"],
            "cluster": r["cluster"], "icp": r["icp"], "pain": r["pain"], "hook": hk["hook"] if hk else None, "launched": r["first_day"],
            "launched_before_data_start": bool(r["launched_before_data_start"]), "live": bool(r["live"])}


def _moved_row(r: dict, ident: dict, place: dict | None, bulk_min: int) -> dict:
    held = held_up(r)
    return {**ident, "moved": r["scaling_first_day"], "days_in_test": r["days_in_test"],
            "bulk": r["moves_that_day"] >= bulk_min, "moves_that_day": r["moves_that_day"],
            "test": _block(r["test_spend"], r["test_purchases"], r["test_revenue"], r["test_link_clicks"], r["test_days"]),
            "test_after_spend": q.f2(r["test_after_spend"]),
            "scale": _block(r["scale_spend"], r["scale_purchases"], r["scale_revenue"], r["scale_link_clicks"], r["scale_days"]),
            "last7": _block(r["last7_scale_spend"], r["last7_scale_purchases"], r["last7_scale_revenue"]),
            "last7_spend_all_stages": q.f2(r["last7_spend"]),
            "live_scaling": bool(r["live_scaling"]), "held": held,
            "placements": place or {"campaigns": 0, "adsets": 0, "ads": 0, "spend": 0.0, "top": [], "more": 0}}


def _not_scaled_row(r: dict, ident: dict, through: dt.date) -> dict:
    return {**ident, "state": "still_testing" if r["live"] else "stopped", "age_days": (through - r["first_day"]).days + 1, "last_spend_day": r["last_spend_day"],
            "test": _block(r["test_spend"], r["test_purchases"], r["test_revenue"], r["test_link_clicks"], r["test_days"]),
            "last7": _block(r["last7_spend"], r["last7_purchases"], r["last7_revenue"])}


def _series(moved: list[dict], testers: list[dict], scale_days: dict, lo: dt.date, hi: dt.date, grain: str, bulk_min: int) -> list[dict]:
    key = (lambda d: d) if grain == "day" else _monday
    buckets: dict[dt.date, dict] = {}
    d = key(lo)
    step = dt.timedelta(days=1 if grain == "day" else 7)
    while d <= hi:
        buckets[d] = {"t": d, "moves": 0, "bulk_moves": 0, "launched": 0, "scale_spend": 0.0, "scale_purchases": 0.0, "scale_revenue": 0.0}
        d += step
    for r in moved:
        b = buckets.get(key(r["scaling_first_day"]))
        if b:
            b["bulk_moves" if r["moves_that_day"] >= bulk_min else "moves"] += 1
    for r in testers:
        b = buckets.get(key(r["first_day"]))
        if b:
            b["launched"] += 1
    for day, v in scale_days.items():
        b = buckets.get(key(day))
        if b:
            b["scale_spend"] += _f(v["spend"]); b["scale_purchases"] += _f(v["purchases"]); b["scale_revenue"] += _f(v["revenue"])
    out = []
    for b in buckets.values():
        end = b["t"] + (step - dt.timedelta(days=1))
        out.append({"t": b["t"], "moves": b["moves"], "bulk_moves": b["bulk_moves"], "launched": b["launched"], **{k: q.f2(b[k]) for k in ("scale_spend", "scale_revenue")},
                    "scale_purchases": q.num(b["scale_purchases"]), "scale_roas": q.ratio(b["scale_revenue"], b["scale_spend"], 4),
                    "partial": grain == "week" and (b["t"] < lo or end > hi)})
    return out


def _cohorts(testers: list[dict], no_test: list[dict], lo: dt.date, hi: dt.date) -> dict:
    """The funnel by launch week: creatives that began in testing that week, how many reached scaling since, how long it took."""
    weeks: dict[dt.date, dict] = {}
    for r in testers:
        w = weeks.setdefault(_monday(r["first_day"]), {"launched": 0, "moved": 0, "still_testing": 0, "stopped": 0, "days": [], "no_test": 0})
        w["launched"] += 1
        if r["moved"]:
            w["moved"] += 1
            w["days"].append(r["days_in_test"])
        else:
            w["still_testing" if r["live"] else "stopped"] += 1
    for r in no_test:
        weeks.setdefault(_monday(r["first_day"]), {"launched": 0, "moved": 0, "still_testing": 0, "stopped": 0, "days": [], "no_test": 0})["no_test"] += 1
    rows = []
    for wk in sorted(weeks):
        w = weeks[wk]
        rows.append({"week": wk, "launched": w["launched"], "moved": w["moved"], "pct_reached_scaling": q.ratio(w["moved"], w["launched"], 4),
                     "median_days_to_scale": _median(w["days"]), "still_testing": w["still_testing"], "stopped": w["stopped"], "no_test": w["no_test"],
                     "partial": wk < lo or wk + dt.timedelta(days=6) > hi})
    n, m = sum(w["launched"] for w in weeks.values()), sum(w["moved"] for w in weeks.values())
    alld = [d for w in weeks.values() for d in w["days"]]
    return {"rows": rows, "total": {"launched": n, "moved": m, "pct_reached_scaling": q.ratio(m, n, 4), "median_days_to_scale": _median(alld),
                                    "still_testing": sum(w["still_testing"] for w in weeks.values()), "stopped": sum(w["stopped"] for w in weeks.values()),
                                    "no_test": sum(w["no_test"] for w in weeks.values())}}


# ---------------------------------------------------------------------------------------------------------------- the endpoint

class DbSide:
    """The lookups around the rows (thumbnails, hooks, where each creative scaled, the daily scaling spend): from the database. The UI tests' mock server
    passes an in-memory twin with the same four methods."""

    def __init__(self, conn):
        self.conn = conn

    def thumbs(self, family_keys: list[str]) -> dict[str, int]:
        return _thumbs(self.conn, family_keys)

    def hooks(self, row_ids: list[int]) -> dict[int, dict]:
        return q.hooks_for(self.conn, row_ids)

    def placements(self, family_keys: list[str]) -> dict[str, dict]:
        return _placements(self.conn, family_keys)

    def scale_by_day(self, family_keys: list[str], lo: dt.date, hi: dt.date) -> dict[dt.date, dict]:
        return _scale_by_day(self.conn, family_keys, lo, hi)


def promotions(conn, meta: q.DataMeta, args, product_slug: str | None = None) -> dict:
    """Everything the Test to scale page shows for one window (see the module doc). args: window / from / to / days, product, hide_bulk, bulk_min,
    min_test_spend, grain, and the page's Numbers (source / basis)."""
    w = parse_window(args, meta)
    lo, hi = w.span(meta)
    opts = parse_options(args, lo, hi)
    conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")           # a bad query answers a clear JSON error (503 'query timed out'), not a 20 s gateway hang
    built = built_info(conn)
    if built is None:
        raise q.ApiError(503, "Test to scale data is not built yet: run python -m etl.stage_moves")
    product = None
    if product_slug is not None and product_slug.lower() in (q.NO_PRODUCT_SLUG, "unassigned"):
        product_slug = q.NO_PRODUCT_SLUG                                  # creatives of no active product
    elif product_slug is not None:
        product = q.get_product(conn, product_slug)                    # 404 for an unknown product
        product_slug = product["slug"]
    order = {p["slug"]: (p["sort_order"], p["id"]) for p in q.rows_of(conn, "SELECT id, slug, sort_order FROM products WHERE active")}
    return assemble(_fetch(conn, lo, hi), lo=lo, hi=hi, through=meta.data_through, info=_info(w, meta), opts=opts, product_slug=product_slug,
                    product_order=order, side=DbSide(conn), built=built)


def parse_options(args, lo: dt.date, hi: dt.date) -> dict:
    bulk_raw = args.get("bulk_min")
    grain = (args.get("grain") or "auto").strip().lower()
    if grain not in ("auto", "day", "week"):
        raise q.ApiError(400, "grain must be auto, day or week")
    return {"hide_bulk": _flag(args, "hide_bulk"),
            "bulk_min": q.parse_uint(bulk_raw if bulk_raw not in (None, "") else str(BULK_MIN_DEFAULT), 1, 9999, "bulk_min must be an integer from 1 to 9999"),
            "min_test": _money(args, "min_test_spend"),
            "grain": ("day" if (hi - lo).days + 1 <= 45 else "week") if grain == "auto" else grain}


def assemble(all_rows: list[dict], *, lo: dt.date, hi: dt.date, through: dt.date, info: dict, opts: dict, product_slug: str | None, product_order: dict,
             side, built: dict | None = None) -> dict:
    """The page's payload from the creatives' rows (normalize_row'd). Pure: no database. product_slug: None = every product, 'none' = creatives of no
    product, else the slug."""
    hide_bulk, bulk_min, min_test, grain = opts["hide_bulk"], opts["bulk_min"], opts["min_test"], opts["grain"]
    in_win = lambda d: d is not None and lo <= d <= hi            # noqa: E731
    for r in all_rows:
        r["bulk"] = r["path"] == "moved" and r["moves_that_day"] >= bulk_min
    bulk_days = sorted({(r["scaling_first_day"], r["moves_that_day"]) for r in all_rows if r["bulk"] and in_win(r["scaling_first_day"])})
    hidden_bulk = sum(1 for r in all_rows if r["bulk"] and in_win(r["scaling_first_day"])) if hide_bulk else 0
    rows = [r for r in all_rows if not (hide_bulk and r["bulk"])]
    rows = [r for r in rows if not (r["began_in_testing"] and r["test_spend"] < min_test)]           # Min test spend: testers only (a creative that never tested has no test spend)

    def by_filter(rs):
        return rs if product_slug is None else [r for r in rs if r["product_slug"] == product_slug]

    moved_all = [r for r in rows if r["path"] == "moved" and in_win(r["scaling_first_day"])]
    launched_all = [r for r in rows if in_win(r["first_day"]) and not r["launched_before_data_start"]]
    chips: dict[str, dict] = {}
    for r in moved_all:
        chips.setdefault(r["product_slug"], {"slug": r["product_slug"], "name": r["product_name"], "moved": 0, "launched": 0})["moved"] += 1
    for r in launched_all:
        if r["began_in_testing"]:
            chips.setdefault(r["product_slug"], {"slug": r["product_slug"], "name": r["product_name"], "moved": 0, "launched": 0})["launched"] += 1
    product_chips = sorted(chips.values(), key=lambda c: (product_order.get(c["slug"], (10**6, 0)), c["slug"]))

    moved = sorted(by_filter(moved_all), key=lambda r: (-r["scale_spend"], r["display"] or "", r["family_key"]))
    launched = by_filter(launched_all)
    testers = [r for r in launched if r["began_in_testing"]]
    no_test = [r for r in launched if not r["began_in_testing"]]
    not_scaled = sorted((r for r in testers if not r["moved"]), key=lambda r: (-r["test_spend"], r["display"] or "", r["family_key"]))
    cohort_moved = [r for r in testers if r["moved"]]

    shown_ns = not_scaled[:NOT_SCALED_LIMIT]
    thumbs = side.thumbs([r["family_key"] for r in moved] + [r["family_key"] for r in shown_ns])
    hooks = side.hooks([r["tracker_row_id"] for r in moved + shown_ns if r["tracker_row_id"] is not None])
    place = side.placements([r["family_key"] for r in moved])
    scale_days = side.scale_by_day([r["family_key"] for r in moved], lo, hi)

    moved_rows = [_moved_row(r, _identity(r, thumbs, hooks), place.get(r["family_key"]), bulk_min) for r in moved]
    ns_rows = [_not_scaled_row(r, _identity(r, thumbs, hooks), through) for r in shown_ns]

    t_sp, t_pu, t_rv = (sum(r[k] for r in moved) for k in ("test_spend", "test_purchases", "test_revenue"))
    s_sp, s_pu, s_rv = (sum(r[k] for r in moved) for k in ("scale_spend", "scale_purchases", "scale_revenue"))
    days_ok = [r["days_in_test"] for r in moved if not r["launched_before_data_start"]]
    held_counts = collections.Counter(m["held"]["state"] for m in moved_rows)
    t_roas, s_roas = q.ratio(t_rv, t_sp, 4), q.ratio(s_rv, s_sp, 4)
    summary = {
        "launched": {"n": len(launched), "began_in_testing": len(testers), "no_test": len(no_test),
                     "no_test_paths": {p: sum(1 for r in no_test if r["path"] == p) for p in NO_TEST_PATHS},
                     "already_running_at_data_start": sum(1 for r in by_filter(rows) if in_win(r["first_day"]) and r["launched_before_data_start"])},
        "still_testing": sum(1 for r in testers if not r["moved"] and r["live"]),
        "stopped": sum(1 for r in testers if not r["moved"] and not r["live"]),
        "moved_from_launched": len(cohort_moved),
        "pct_reached_scaling": q.ratio(len(cohort_moved), len(testers), 4),
        "moved": {"n": len(moved), "bulk": sum(1 for r in moved if r["bulk"]), "launched_before_data_start": sum(1 for r in moved if r["launched_before_data_start"])},
        "median_days_to_scale": _median(days_ok), "median_days_n": len(days_ok),
        "testing": _block(t_sp, t_pu, t_rv), "scaling": _block(s_sp, s_pu, s_rv),
        "scaling_vs_testing_roas": q.ratio(s_roas, t_roas, 4) if s_roas is not None and t_roas else None,
        "scaling_spend_last7": q.f2(sum(r["last7_scale_spend"] for r in moved)),
        "live_scaling": sum(1 for r in moved if r["live_scaling"]),
        "held": {k: held_counts.get(k, 0) for k in ("held", "softer", "fell", "too_early", "no_baseline")},
    }
    notes = []
    if hi < through:
        notes.append(f"Scaling numbers run to {q._fmt_day(through, True)}, the latest day of data, not to the end of the window; the chart stops at the window's end.")
    if hide_bulk and hidden_bulk:
        notes.append(f"{hidden_bulk} creatives that moved on bulk days are left out of every number on this page.")
    if built and built.get("data_through") and built["data_through"] < through.isoformat():
        notes.append(f"The Test to scale tables were last rebuilt {built['built_at'][:16].replace('T', ' ')} UTC and run through {built['data_through']}, older than the latest day of data ({through.isoformat()}).")
    return {
        "stage_moves": built, "platform": "meta", "scope": "tracker", "window_info": info, "compare_info": None, "data_through": through.isoformat(),
        "labels": info["source"]["labels"], "numbers": info["source"]["label"],
        "filters": {"product": product_slug, "hide_bulk": hide_bulk, "bulk_min": bulk_min, "min_test_spend": min_test, "grain": grain},
        "definition": {"move": MOVE_DEFINITION, "bulk": BULK_NOTE.format(n=bulk_min), "held": HELD_RULE,
                       "held_thresholds": {"min_spend": HELD_MIN_SPEND, "min_days": HELD_MIN_DAYS, "held": HELD_OK, "softer": HELD_SOFT}},
        "products": product_chips,
        "summary": summary,
        "bulk_days": [{"day": d, "moves": n} for d, n in bulk_days], "hidden_bulk": hidden_bulk,
        "series": {"grain": grain, "points": _series(moved, testers, scale_days, lo, hi, grain, bulk_min)},
        "moved": moved_rows,
        "not_scaled": {"rows": ns_rows, "total": len(not_scaled), "shown": len(ns_rows)},
        "cohorts": _cohorts(testers, no_test, lo, hi),
        "notes": notes,
    }
