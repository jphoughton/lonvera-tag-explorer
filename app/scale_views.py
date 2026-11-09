"""Test to scale, the two simple views (/promotions, tabs "What should be scaled" and "Performance in scaling"). Meta creatives only, plain ranked tables.

Both read the PRECOMPUTED tables of the page (creative_stage_moves*, migration 016) plus the per-product scaling averages in scaling_benchmarks (migration 193), all
rebuilt nightly by etl.stage_moves. One indexed query on a few thousand rows each; nothing is aggregated on request. Ratios are always ratio of sums.

VIEW 1  should_scale: creatives in TESTING that have not moved to scaling and look good enough to move (a screen, not a verdict).
  Candidates: path 'testing_only' (spend in a Testing campaign, none ever in a Scaling campaign) that spent on one of the last 3 days of data.
  Floors (editable): testing spend >= min_test_spend (default $500), purchases >= min_purchases (default 5), both in the chosen Numbers basis.
  Return bar (editable as a percentage, default 100%): the creative's testing ROAS is at or above its PRODUCT's scaling ROAS of the last 30 days, or its testing
  CPA is at or below that product's scaling CPA (CPA bar = scaling CPA / bar fraction). A product with no scaling spend in 30 days has no bar: nothing in it qualifies.
  Ranking: extra revenue the creative earned above what the product's scaling average would have earned on the same spend (revenue - bar ROAS x spend), biggest first.
  Confidence chip: more than 50 purchases high, 20 to 50 medium, under 20 low. Nearest misses (floors met, bar missed) come back for the empty state.

VIEW 2  in_scaling: creatives that moved (first scaling day inside the window, the page's window) and how they do since the move.
  Held-up logic is the page's own (app.promotions.held_up): scaling ROAS since the move divided by testing ROAS before it, and the same 'too early' floor ($200 and 3 days
  in scaling). 'vs testing' words: better from 110%, about the same from 90% (the page's held-up line), worse below. Status: too early (the page's floor), stopped (no
  scaling spend in the last 7 days), fading (last 7 days ROAS more than 10% below the since-move ROAS), else holding.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import promotions as promo, queries as q

SV_MIN_SPEND = 500.0                 # View 1 defaults, all editable in the UI
SV_MIN_PURCHASES = 5
SV_BAR_PCT = 100
SV_RECENT_DAYS = 3                   # a testing creative must have spent on one of the last 3 days of data (not editable)
NEAR = 0.90                          # testing ROAS within 10% below the bar = 'near'
CONF_HIGH, CONF_MID = 50, 20         # purchases: more than 50 high, 20 to 50 medium, below 20 low
NEAR_MISSES = 5
BETTER = 1.10                        # View 2: scaling ROAS / testing ROAS from 110% = better, from HELD_OK (90%) about the same, below worse
FADE = 0.90                          # View 2: last 7 days ROAS below 90% of the since-move ROAS = fading
VIEWS = ("should_scale", "in_scaling")

RULE_TEXT = ("Creatives that are in testing, have not moved to scaling, spent in the last {recent} days, and have at least ${spend} of testing spend and {purchases} {pw}, "
             "and {bar}. Ranked by the extra revenue each earned over what its product's scaling average would have earned on the same spend, biggest first.")


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


# ---------------------------------------------------------------------------------------------------------------- parameters

def parse_view(args) -> str:
    v = (args.get("view") or "should_scale").strip().lower()
    if v not in VIEWS:
        raise q.ApiError(400, "view must be should_scale or in_scaling")
    return v


def parse_should_scale_options(args) -> dict:
    spend_raw, pur_raw, bar_raw = args.get("min_test_spend"), args.get("min_purchases"), args.get("bar_pct")
    return {"min_spend": promo._money(args, "min_test_spend") if spend_raw not in (None, "") else SV_MIN_SPEND,
            "min_purchases": q.parse_uint(pur_raw, 0, 9999, "min_purchases must be an integer from 0 to 9999") if pur_raw not in (None, "") else SV_MIN_PURCHASES,
            "bar_pct": q.parse_uint(bar_raw, 1, 1000, "bar_pct must be an integer from 1 to 1000") if bar_raw not in (None, "") else SV_BAR_PCT}


def parse_in_scaling_options(args) -> dict:
    bulk_raw = args.get("bulk_min")
    return {"hide_bulk": promo._flag(args, "hide_bulk"),
            "bulk_min": q.parse_uint(bulk_raw if bulk_raw not in (None, "") else str(promo.BULK_MIN_DEFAULT), 1, 9999, "bulk_min must be an integer from 1 to 9999")}


# ---------------------------------------------------------------------------------------------------------------- shared shaping

def _blk(spend: float, purchases: float, revenue: float) -> dict:
    return {"spend": q.f2(spend), "purchases": q.num(purchases), "revenue": q.f2(revenue), "roas": q.ratio(revenue, spend, 4), "cpa": q.ratio(spend, purchases, 2)}


def _ident(r: dict) -> dict:
    return {"family_key": r["family_key"], "display": r["display"] or r["family_key"], "primary_id": r["tracker_row_id"],
            "product": {"slug": r["product_slug"], "name": r["product_name"]}, "ad_type": r["ad_type"], "launched": r["first_day"],
            "launched_before_data_start": bool(r["launched_before_data_start"])}


def _product_chips(rows: list[dict], order: dict) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["product_slug"], {"slug": r["product_slug"], "name": r["product_name"], "n": 0})["n"] += 1
    return sorted(seen.values(), key=lambda c: (order.get(c["slug"], (10**6, 0)), c["slug"]))


def confidence(purchases: float) -> str:
    return "high" if purchases > CONF_HIGH else "medium" if purchases >= CONF_MID else "low"


# ---------------------------------------------------------------------------------------------------------------- view 1

def _bar(b: dict | None, frac: float) -> dict | None:
    """One product's scaling average and the bar derived from it; None when there is nothing to compare with."""
    if not b or _f(b["spend"]) <= 0 or _f(b["revenue"]) <= 0:
        return None
    sp, pu, rv = _f(b["spend"]), _f(b["purchases"]), _f(b["revenue"])
    roas = rv / sp
    cpa = sp / pu if pu > 0 else None
    return {"spend": q.f2(sp), "purchases": q.num(pu), "revenue": q.f2(rv), "creatives": int(b["creatives"]), "roas": round(roas, 4), "cpa": round(cpa, 2) if cpa else None,
            "bar_roas": round(roas * frac, 4), "bar_cpa": round(cpa / frac, 2) if cpa else None, "day_from": b["day_from"], "day_to": b["day_to"],
            "_roas": roas, "_cpa": cpa}               # (unrounded, for the comparisons; not sent)


def _candidate(r: dict, bar: dict, frac: float) -> dict:
    sp, pu, rv = r["test_spend"], r["test_purchases"], r["test_revenue"]
    roas = rv / sp if sp else 0.0
    cpa = sp / pu if pu else None
    bar_roas = bar["_roas"] * frac
    ratio = roas / bar_roas if bar_roas else None
    cpa_bar = bar["_cpa"] / frac if bar["_cpa"] else None
    by_roas = roas >= bar_roas
    by_cpa = cpa is not None and cpa_bar is not None and cpa <= cpa_bar
    word = "above" if (by_roas or by_cpa) else "near" if ratio is not None and ratio >= NEAR else "below"
    return {"by_roas": by_roas, "by_cpa": by_cpa, "word": word, "ratio": round(ratio, 4) if ratio is not None else None, "excess": rv - bar_roas * sp}


def _should_row(r: dict, c: dict, through: dt.date, bar: dict) -> dict:
    return {**_ident(r), "days_in_testing": (through - r["first_day"]).days + 1, "last_spend_day": r["last_spend_day"],
            "test": _blk(r["test_spend"], r["test_purchases"], r["test_revenue"]), "last7": _blk(r["last7_spend"], r["last7_purchases"], r["last7_revenue"]),
            "vs_scaling": {"word": c["word"], "ratio": c["ratio"], "beats": "both" if c["by_roas"] and c["by_cpa"] else "roas" if c["by_roas"] else "cpa" if c["by_cpa"] else None,
                           "bar_roas": bar["bar_roas"], "bar_cpa": bar["bar_cpa"]},
            "confidence": {"level": confidence(r["test_purchases"]), "purchases": q.num(r["test_purchases"])},
            "extra_revenue": q.f2(c["excess"])}


def build_should_scale(rows: list[dict], bench: list[dict], *, through: dt.date, opts: dict, product_slug: str | None, product_order: dict, product_names: dict,
                       info: dict, built: dict | None = None) -> dict:
    """View 1 payload. rows: creative_stage_moves rows (normalize_row'd); only path 'testing_only' that spent in the last 3 days are candidates. Pure: no database."""
    frac = opts["bar_pct"] / 100.0
    live = [r for r in rows if r["path"] == "testing_only" and r["last_spend_day"] is not None and (through - r["last_spend_day"]).days < SV_RECENT_DAYS]
    chips = _product_chips(live, product_order)
    cand = [r for r in live if product_slug is None or r["product_slug"] == product_slug]
    bench_by = {b["product_slug"]: b for b in bench}
    bars = {slug: _bar(bench_by.get(slug), frac) for slug in set(r["product_slug"] for r in cand) | ({product_slug} if product_slug else set(bench_by))}
    floors = [r for r in cand if r["test_spend"] >= opts["min_spend"] and r["test_purchases"] >= opts["min_purchases"]]
    no_bar = [r for r in floors if bars.get(r["product_slug"]) is None]
    scored = [(r, _candidate(r, bars[r["product_slug"]], frac)) for r in floors if bars.get(r["product_slug"]) is not None]
    wins = sorted(((r, c) for r, c in scored if c["by_roas"] or c["by_cpa"]), key=lambda rc: (-rc[1]["excess"], -rc[0]["test_spend"], rc[0]["display"] or "", rc[0]["family_key"]))
    misses = sorted(((r, c) for r, c in scored if not (c["by_roas"] or c["by_cpa"])), key=lambda rc: (-(rc[1]["ratio"] or 0), -rc[0]["test_spend"], rc[0]["family_key"]))[:NEAR_MISSES]
    out_rows = [_should_row(r, c, through, bars[r["product_slug"]]) for r, c in wins]
    by_conf = {k: sum(1 for x in out_rows if x["confidence"]["level"] == k) for k in ("high", "medium", "low")}
    shown_bars = [{"slug": slug, "name": product_names.get(slug, slug), **{k: v for k, v in b.items() if not k.startswith("_")}} for slug in sorted(bars, key=lambda s: (product_order.get(s, (10**6, 0)), s))
                  if (b := bars[slug]) is not None and (product_slug is None or slug == product_slug)]
    notes = []
    for slug in sorted({r["product_slug"] for r in cand if bars.get(r["product_slug"]) is None}, key=lambda s: (product_order.get(s, (10**6, 0)), s)):
        notes.append(f"{product_names.get(slug, slug)} has no scaling spend in the last 30 days, so there is nothing to compare its testing creatives with and none is listed.")
    if built and built.get("data_through") and built["data_through"] < through.isoformat():
        notes.append(f"The Test to scale tables were last rebuilt {built['built_at'][:16].replace('T', ' ')} UTC and run through {built['data_through']}, older than the latest day of data ({through.isoformat()}).")
    return {
        "view": "should_scale", "platform": "meta", "scope": "tracker", "window_info": info, "data_through": through.isoformat(), "stage_moves": built,
        "labels": info["source"]["labels"], "numbers": info["source"]["label"],
        "filters": {"product": product_slug, "min_test_spend": opts["min_spend"], "min_purchases": opts["min_purchases"], "bar_pct": opts["bar_pct"]},
        "defaults": {"min_test_spend": SV_MIN_SPEND, "min_purchases": SV_MIN_PURCHASES, "bar_pct": SV_BAR_PCT, "recent_days": SV_RECENT_DAYS},
        "definition": {"rule": RULE_TEXT.format(recent=SV_RECENT_DAYS, spend=f"{opts['min_spend']:,.0f}", purchases=opts["min_purchases"], pw="purchases" if opts["min_purchases"] != 1 else "purchase",
                                                bar="return at least as much as the same product's scaling average over the last 30 days (ROAS at or above it, or CPA at or below it)" if opts["bar_pct"] == 100
                                                else f"reach {opts['bar_pct']}% of the same product's scaling average over the last 30 days (ROAS at or above it, or CPA at or below the average divided by {frac:g})"),
                       "screen": "This is a screen, not a verdict: testing numbers are noisy under about 50 purchases. The chip says how much evidence each row has.",
                       "confidence": {"high": f"more than {CONF_HIGH} purchases", "medium": f"{CONF_MID} to {CONF_HIGH} purchases", "low": f"under {CONF_MID} purchases"}},
        "products": chips, "bars": shown_bars,
        "summary": {"testing_live": len(cand), "meets_floors": len(floors), "qualify": len(out_rows), "no_bar": len(no_bar), "confidence": by_conf,
                    "by_product": {s: sum(1 for x in out_rows if x["product"]["slug"] == s) for s in sorted({x["product"]["slug"] for x in out_rows})}},
        "rows": out_rows,
        "near_misses": [_should_row(r, c, through, bars[r["product_slug"]]) for r, c in misses],
        "notes": notes,
    }


# ---------------------------------------------------------------------------------------------------------------- view 2

def _vs_testing(held: dict) -> dict:
    st, ratio = held["state"], held["ratio"]
    if st in ("too_early", "no_baseline"):
        return {"word": "too early" if st == "too_early" else "no baseline", "ratio": None}
    return {"word": "better" if ratio >= BETTER else "about the same" if ratio >= promo.HELD_OK else "worse", "ratio": ratio}


def _status(r: dict, held: dict) -> str:
    if held["state"] == "too_early":
        return "too early"
    if r["last7_scale_spend"] <= 0:
        return "stopped"
    since = r["scale_revenue"] / r["scale_spend"] if r["scale_spend"] else 0.0
    last7 = r["last7_scale_revenue"] / r["last7_scale_spend"]
    return "holding" if last7 >= since * FADE else "fading"


def _scaling_row(r: dict, through: dt.date, bulk_min: int) -> dict:
    held = promo.held_up(r)
    return {**_ident(r), "moved": r["scaling_first_day"], "days_since_move": (through - r["scaling_first_day"]).days, "days_in_test": r["days_in_test"],
            "bulk": r["moves_that_day"] >= bulk_min, "moves_that_day": r["moves_that_day"], "live_scaling": bool(r["live_scaling"]),
            "test": _blk(r["test_spend"], r["test_purchases"], r["test_revenue"]), "scale": {**_blk(r["scale_spend"], r["scale_purchases"], r["scale_revenue"]), "days": r["scale_days"]},
            "last7": _blk(r["last7_scale_spend"], r["last7_scale_purchases"], r["last7_scale_revenue"]),
            "vs_testing": _vs_testing(held), "status": _status(r, held)}


def _totals(rows: list[dict]) -> dict:
    s = lambda f: sum(f(r) for r in rows)        # noqa: E731
    sp, pu, rv = s(lambda r: r["scale_spend"]), s(lambda r: r["scale_purchases"]), s(lambda r: r["scale_revenue"])
    l7s, l7p, l7r = s(lambda r: r["last7_scale_spend"]), s(lambda r: r["last7_scale_purchases"]), s(lambda r: r["last7_scale_revenue"])
    return {"creatives": len(rows), "scale": _blk(sp, pu, rv), "last7": _blk(l7s, l7p, l7r)}


def build_in_scaling(all_rows: list[dict], *, lo: dt.date, hi: dt.date, through: dt.date, opts: dict, product_slug: str | None, product_order: dict, info: dict,
                     built: dict | None = None) -> dict:
    """View 2 payload from creative_stage_moves rows (normalize_row'd). Pure: no database."""
    bulk_min = opts["bulk_min"]
    in_win = [r for r in all_rows if r["path"] == "moved" and r["scaling_first_day"] is not None and lo <= r["scaling_first_day"] <= hi]
    for r in in_win:
        r["bulk"] = r["moves_that_day"] >= bulk_min
    bulk_days = sorted({(r["scaling_first_day"], r["moves_that_day"]) for r in in_win if r["bulk"]})
    hidden = sum(1 for r in in_win if r["bulk"]) if opts["hide_bulk"] else 0
    kept = [r for r in in_win if not (opts["hide_bulk"] and r["bulk"])]
    chips = _product_chips(kept, product_order)
    rows = sorted((r for r in kept if product_slug is None or r["product_slug"] == product_slug), key=lambda r: (-r["scale_spend"], r["display"] or "", r["family_key"]))
    out = [_scaling_row(r, through, bulk_min) for r in rows]
    by_p: dict[str, list[dict]] = {}
    for r in rows:
        by_p.setdefault(r["product_slug"], []).append(r)
    totals = [{"slug": s, "name": by_p[s][0]["product_name"], **_totals(by_p[s])} for s in sorted(by_p, key=lambda s: (product_order.get(s, (10**6, 0)), s))]
    notes = []
    if hi < through:
        notes.append(f"Scaling numbers run to {q._fmt_day(through, True)}, the latest day of data, not to the end of the window.")
    if opts["hide_bulk"] and hidden:
        notes.append(f"{hidden} creatives that moved on bulk days are left out of every number on this page.")
    if built and built.get("data_through") and built["data_through"] < through.isoformat():
        notes.append(f"The Test to scale tables were last rebuilt {built['built_at'][:16].replace('T', ' ')} UTC and run through {built['data_through']}, older than the latest day of data ({through.isoformat()}).")
    return {
        "view": "in_scaling", "platform": "meta", "scope": "tracker", "window_info": info, "data_through": through.isoformat(), "stage_moves": built,
        "labels": info["source"]["labels"], "numbers": info["source"]["label"],
        "filters": {"product": product_slug, "hide_bulk": opts["hide_bulk"], "bulk_min": bulk_min},
        "definition": {"held": promo.HELD_RULE, "bulk": promo.BULK_NOTE.format(n=bulk_min),
                       "vs_testing": f"Scaling ROAS since the move divided by testing ROAS before it: better from {BETTER:.0%}, about the same from {promo.HELD_OK:.0%}, worse below.",
                       "status": (f"Holding: the last 7 days' ROAS is within {1 - FADE:.0%} of, or above, the ROAS since the move. Fading: more than {1 - FADE:.0%} below it. Stopped: no scaling spend in "
                                  f"the last 7 days. Too early: under ${promo.HELD_MIN_SPEND:,.0f} spent or fewer than {promo.HELD_MIN_DAYS} days in scaling (the page's held-up floor)."),
                       "thresholds": {"better": BETTER, "same": promo.HELD_OK, "fade": FADE, "min_spend": promo.HELD_MIN_SPEND, "min_days": promo.HELD_MIN_DAYS}},
        "products": chips,
        "summary": {"n": len(out), "bulk": sum(1 for r in rows if r["bulk"]), "status": {k: sum(1 for x in out if x["status"] == k) for k in ("holding", "fading", "stopped", "too early")}},
        "bulk_days": [{"day": d, "moves": n} for d, n in bulk_days], "hidden_bulk": hidden,
        "rows": out, "totals": totals, "total_all": _totals(rows) if rows else None,
        "notes": notes,
    }


# ---------------------------------------------------------------------------------------------------------------- the endpoint

def _fetch_testing(conn) -> list[dict]:
    """Candidates of view 1: testing-only creatives that spent in the last SV_RECENT_DAYS days of data (the builder checks the day again)."""
    return [promo.normalize_row(r) for r in q.rows_of(conn, """
        SELECT * FROM creative_stage_moves WHERE path = 'testing_only' AND last_spend_day > data_through - %(n)s""", {"n": SV_RECENT_DAYS})]


def _fetch_moved(conn, lo: dt.date, hi: dt.date) -> list[dict]:
    return [promo.normalize_row(r) for r in q.rows_of(conn, """
        SELECT * FROM creative_stage_moves WHERE path = 'moved' AND scaling_first_day BETWEEN %(lo)s AND %(hi)s""", {"lo": lo, "hi": hi})]


def _fetch_bench(conn) -> list[dict]:
    sfx = q._SRC_SFX.get()
    return q.rows_of(conn, "SELECT product_slug, day_from, day_to, creatives, spend, purchases, revenue FROM scaling_benchmarks WHERE basis = %(b)s", {"b": sfx.lstrip("_") if sfx else "meta"})


def scale_views(conn, meta: q.DataMeta, args, product_slug: str | None = None) -> dict:
    """The payload of one view (args: view=should_scale|in_scaling, product, Numbers source / basis; view 1: min_test_spend, min_purchases, bar_pct; view 2: window / from / to /
    days, hide_bulk, bulk_min)."""
    view = parse_view(args)
    w = promo.parse_window(args, meta)
    lo, hi = w.span(meta)
    opts = parse_should_scale_options(args) if view == "should_scale" else parse_in_scaling_options(args)
    conn.execute(f"SET LOCAL statement_timeout = {promo.QUERY_TIMEOUT_MS}")
    built = promo.built_info(conn)
    if built is None:
        raise q.ApiError(503, "Test to scale data is not built yet: run python -m etl.stage_moves")
    if product_slug is not None and product_slug.lower() in (q.NO_PRODUCT_SLUG, "unassigned"):
        product_slug = q.NO_PRODUCT_SLUG
    elif product_slug is not None:
        product_slug = q.get_product(conn, product_slug)["slug"]
    prods = q.rows_of(conn, "SELECT id, slug, name, sort_order FROM products WHERE active")
    order = {p["slug"]: (p["sort_order"], p["id"]) for p in prods}
    info = promo._info(w, meta)
    if view == "in_scaling":
        return build_in_scaling(_fetch_moved(conn, lo, hi), lo=lo, hi=hi, through=meta.data_through, opts=opts, product_slug=product_slug, product_order=order, info=info, built=built)
    names = {p["slug"]: p["name"] for p in prods} | {q.NO_PRODUCT_SLUG: q.NO_PRODUCT}
    return build_should_scale(_fetch_testing(conn), _fetch_bench(conn), through=meta.data_through, opts=opts, product_slug=product_slug, product_order=order, product_names=names,
                              info=info, built=built)
