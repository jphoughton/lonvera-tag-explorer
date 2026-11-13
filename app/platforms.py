"""Platform = All (Marketing metrics): the overview, the one table of every campaign of every platform, and their trend.

Platforms are Meta (ad_daily, the Tags data) and Google (Upstack's Google feeds, app/google.py). Both are read through the same numbers choice:
  own   the platform's own reporting (Meta's, Google's)
  nc    Upstack any-click attribution, new customers
  all   Upstack any-click attribution, all orders
Spend, impressions and clicks are the platform's in all three. Purchases and revenue are never added across platforms: both platforms credit the
orders they touched (an order can be credited to a Meta ad and a Google campaign), so only per-platform subtotals are shown.

The windows of the two platforms differ (Meta's daily data starts 2026-05-06, Google's channel feed 2024-03-25, Upstack attribution 2025-05-13 for Google and
2026-05-06 for Meta), so on the All views 'since launch' means the days every platform has data, never each platform's own history."""
from __future__ import annotations

import datetime as dt

from werkzeug.datastructures import MultiDict

from . import google as gg, queries as q

PLATFORMS = ("meta", "google")
NAMES = {"meta": "Meta", "google": "Google"}
NUMBERS = {"own": ("meta", "nc"), "nc": ("upstack", "nc"), "all": ("upstack", "all")}
DROP = ("compare", "cfrom", "cto", "perday", "stage", "active")        # Stage and Active only are Meta-only; comparison is not offered on the All views
COLORS = {"meta": "#1a5fb4", "google": "#E8573A"}
BASIS_LABEL = {("meta", "own"): "Meta's own reporting", ("google", "own"): "Google's own reporting", ("meta", "nc"): "Upstack · new customers",
               ("google", "nc"): "Upstack · new customers", ("meta", "all"): "Upstack · all orders", ("google", "all"): "Upstack · all orders"}
ADDITIVE_ALL = ("spend", "impressions", "link_clicks")                  # the only metrics that may be added across platforms


def numbers_of(args) -> str:
    """own | nc | all from the page's source / basis (Numbers from Meta | Upstack and Count), or an explicit numbers=own|nc|all."""
    n = (args.get("numbers") or "").strip().lower()
    if n:
        if n not in NUMBERS:
            raise q.ApiError(400, "numbers must be own, nc or all")
        return n
    source, basis = q.parse_source(args)
    return "own" if source == "meta" else basis


def _span(meta: q.DataMeta, platform: str, source: str) -> q.DataMeta:
    return q.meta_for_source(meta, source) if platform == "meta" else gg.google_meta(meta, source)


def windows(meta: q.DataMeta, args, numbers: str, platforms=PLATFORMS, keep_compare: bool = False) -> tuple[dict, dict, dict]:
    """({platform: (data span, WindowSpec)}, {platform: why it is left out}, info). A platform whose data is not loaded is left out. 'Since launch' on the
    All views is the stretch every loaded platform has data for (a platform alone keeps its own). Stage / Active only / comparison are dropped."""
    source, basis = NUMBERS[numbers]
    a = MultiDict(args)
    for k in DROP:
        if keep_compare and k in ("compare", "cfrom", "cto", "perday"):
            continue
        a.pop(k, None)
    a["source"], a["basis"] = source, basis
    spans, skipped = {}, {}
    for pf in platforms:
        try:
            spans[pf] = _span(meta, pf, source)
        except q.ApiError as e:
            if e.status != 503:
                raise
            skipped[pf] = e.message
    wins = {pf: q.parse_window(a, m) for pf, m in spans.items()}
    note = None
    if len(wins) > 1 and all(w.is_launch for w in wins.values()):
        lo, hi = max(m.daily_start for m in spans.values()), min(m.data_through for m in spans.values())
        if lo <= hi:
            b = MultiDict(a)
            b.pop("window", None)
            b["from"], b["to"] = lo.isoformat(), hi.isoformat()
            wins = {pf: q.parse_window(b, m) for pf, m in spans.items()}
            note = (f"Since launch here means the days every platform has data: {q._fmt_day(lo, True)} – {q._fmt_day(hi, True)}"
                    + (" (Upstack attribution starts later than the platforms' own reporting)." if source == "upstack" else "."))
    return {pf: (spans[pf], wins[pf]) for pf in spans}, skipped, {"note": note}


def head_info(wins: dict, note: str | None) -> dict:
    """window_info of the page header: the first loaded platform's window, with the common-span note."""
    pf = next(iter(wins))
    m, w = wins[pf]
    info = w.info(m)
    if note:
        info["note"] = note + (f" {info['note']}" if info.get("note") else "")
    return info


def window_dict(info: dict) -> dict:
    return {"from": info.get("from"), "to": info.get("to"), "days": info.get("days"), "label": info.get("label"), "note": info.get("note"), "clamped": bool(info.get("clamped"))}


# ---------------------------------------------------------------------------------------------------------------- the one table of every campaign

def _share(rows: list[dict], total_spend) -> None:
    for r in rows:
        sp = r["m"].get("spend") or 0
        r["m"]["spend_share"] = round(sp / total_spend, 4) if total_spend else None


def all_campaigns(conn, meta: q.DataMeta, args, numbers: str, *, platform: str | None = None, product_slug: str | None = None,
                  ctype: str | None = None, spark: tuple[str, int] | None = None) -> dict:
    """Every Meta and Google campaign in one list: rows carry platform, product (Meta) or campaign type (Google) and the basis they are counted on. product_slug
    keeps only that product's Meta campaigns (and drops Google); ctype keeps only Google campaigns of that type (and drops Meta). Totals add spend, impressions
    and clicks only; per-platform subtotals carry the full metrics (valid within a platform)."""
    if platform not in (None, "meta", "google"):
        raise q.ApiError(400, "platform must be meta or google")
    if product_slug is not None and ctype is not None:
        raise q.ApiError(400, "product and type cannot both be set")
    want = [pf for pf in PLATFORMS if (platform in (None, pf)) and not (pf == "google" and product_slug is not None) and not (pf == "meta" and ctype is not None)]
    source, basis = NUMBERS[numbers]
    wins, skipped, extra = windows(meta, args, numbers, [pf for pf in PLATFORMS if platform in (None, pf)])
    rows: list[dict] = []
    subtotals: dict[str, dict] = {}
    plat_info: dict[str, dict] = {}
    notes = [f"{NAMES[pf]}: {why}" for pf, why in skipped.items()]
    for pf in want:
        if pf not in wins:
            continue
        span, w = wins[pf]
        plat_info[pf] = window_dict(w.info(span) if pf == "meta" else gg.window_info(w, span))
        label = BASIS_LABEL[(pf, numbers)]
        if pf == "meta":
            q.set_source(source, basis)
            product = "all"
            if product_slug is not None:
                product = (None if product_slug == q.NO_PRODUCT_SLUG else q.get_product(conn, product_slug))
            res = q.campaigns_all(conn, span, w, product, spark=spark)
            for r in res["rows"]:
                rows.append({"key": "meta:" + r["campaign_id"], "platform": "meta", "platform_name": "Meta", "name": r["name"], "campaign_id": r["campaign_id"],
                             "group": r["product"], "group_kind": "product", "product_slug": r["product_slug"], "products": r["products"], "account": r["account"],
                             "stage": r["stage"], "ads": r["ads"], "families": r["families"], "live": r["live"], "last_day": r["last_day"], "status": None,
                             "basis": label, "spark": r.get("spark"), "m": {k: v for k, v in r.items() if k not in ("products", "spark")}})
            subtotals["meta"] = {"campaigns": res["total"]["campaigns"], "ads": res["total"]["ads"], "m": {k: v for k, v in res["total"].items() if k != "spark"}}
        else:
            res = gg.rollup(conn, span, w, "campaign", ctype)
            for r in res["rows"]:
                rows.append({"key": "google:" + r["key"], "platform": "google", "platform_name": "Google", "name": r["label"], "campaign_id": r["key"],
                             "group": r["campaign_type"], "group_kind": "type", "product_slug": None, "products": None, "account": None, "stage": None, "ads": None,
                             "families": None, "live": None, "last_day": None, "status": r.get("status"), "in_feed": r.get("in_feed"), "basis": label, "spark": None,
                             "m": r["m"]})
            subtotals["google"] = {"campaigns": sum(1 for r in res["rows"] if (r["m"].get("spend") or 0) > 0), "ads": None, "m": res["total"]["m"]}
    rows.sort(key=lambda r: (-(r["m"].get("spend") or 0), r["platform"], r["name"]))
    total_spend = sum((subtotals[pf]["m"].get("spend") or 0) for pf in subtotals)
    _share(rows, total_spend)
    for pf in subtotals:
        sp = subtotals[pf]["m"].get("spend") or 0
        subtotals[pf]["m"]["spend_share"] = round(sp / total_spend, 4) if total_spend else None
    imp = [subtotals[pf]["m"].get("impressions") for pf in subtotals]
    clk = sum((subtotals[pf]["m"].get("link_clicks") or 0) for pf in subtotals)
    total = {"spend": round(total_spend, 2), "impressions": sum(i for i in imp if i is not None) if any(i is not None for i in imp) else None, "link_clicks": clk,
             "campaigns": sum(s["campaigns"] for s in subtotals.values()), "spend_share": 1.0 if total_spend else None}
    total["ctr"] = round(clk / total["impressions"], 5) if total["impressions"] else None
    total["cpc"] = round(total_spend / clk, 4) if clk else None
    total["additive"] = len(subtotals) == 1                              # one platform in view: its totals are the totals
    info = head_info(wins, extra["note"]) if wins else {}
    return {"numbers": numbers, "platform": platform, "window_info": info, "platform_windows": plat_info, "rows": rows, "subtotals": subtotals, "total": total,
            "notes": notes, "basis": {pf: BASIS_LABEL[(pf, numbers)] for pf in subtotals}}


def filter_chips(conn, meta: q.DataMeta, args, numbers: str) -> dict:
    """The Platform / Product / Campaign type filters of the All campaigns view: spend per platform, per Meta product and per Google type in the window (before
    any filter)."""
    wins, _skipped, _x = windows(meta, args, numbers)
    out: dict = {"platforms": [], "products": [], "types": []}
    source, basis = NUMBERS[numbers]
    if "meta" in wins:
        span, w = wins["meta"]
        q.set_source(source, basis)
        out["products"] = q.campaign_products(conn, span, w)
        out["platforms"].append({"key": "meta", "name": "Meta", "spend": round(sum(p["spend"] for p in out["products"]), 2)})
    if "google" in wins:
        span, w = wins["google"]
        g = gg.rollup(conn, span, w, "type")
        out["types"] = [{"key": r["key"], "name": r["label"], "spend": r["m"]["spend"]} for r in g["rows"]]
        out["platforms"].append({"key": "google", "name": "Google", "spend": g["total"]["m"]["spend"]})
    return out


# ---------------------------------------------------------------------------------------------------------------- trend

def _align(series: dict[str, list[dict]]) -> tuple[list[str], dict[str, list[dict | None]]]:
    """One x axis for platforms whose windows differ: the union of their bucket dates, a platform without a bucket gets null there."""
    labels = sorted({pt["t"] for pts in series.values() for pt in pts})
    out = {}
    for pf, pts in series.items():
        by = {pt["t"]: pt for pt in pts}
        out[pf] = [by.get(t) for t in labels]
    return labels, out


def trend(conn, meta: q.DataMeta, args, numbers: str, *, grain: str = "day", platform: str | None = None, product_slug: str | None = None,
          ctype: str | None = None) -> dict:
    """Trend of the All views: one point list per platform on the chosen numbers, over one x axis, plus the combined additive series. Same filters as the All
    campaigns table (platform, Meta product, Google type), so the chart adds up to the table. Platforms are never added except for spend, impressions and clicks."""
    if platform not in (None, "meta", "google"):
        raise q.ApiError(400, "platform must be meta or google")
    if product_slug is not None and ctype is not None:
        raise q.ApiError(400, "product and type cannot both be set")
    want = [pf for pf in PLATFORMS if (platform in (None, pf)) and not (pf == "google" and product_slug is not None) and not (pf == "meta" and ctype is not None)]
    source, basis = NUMBERS[numbers]
    wins, skipped, extra = windows(meta, args, numbers, [pf for pf in PLATFORMS if platform in (None, pf)])
    series: dict[str, list[dict]] = {}
    spans: dict[str, dict] = {}
    notes = [f"{NAMES[pf]}: {why}" for pf, why in skipped.items()]
    for pf in want:
        if pf not in wins:
            continue
        span, w = wins[pf]
        if pf == "meta":
            q.set_source(source, basis)
            product = "all"
            if product_slug is not None:
                product = (None if product_slug == q.NO_PRODUCT_SLUG else q.get_product(conn, product_slug))
            series[pf] = q.campaigns_trend(conn, span, w, product, grain=grain)["points"]
        else:
            series[pf] = gg.trend(conn, span, w, grain, ctype, None)["points"]
        info = w.info(span) if pf == "meta" else gg.window_info(w, span)
        spans[pf] = {"from": info.get("from"), "to": info.get("to"), "history": span.daily_start}
    labels, aligned = _align(series)
    groups = [{"key": pf, "label": NAMES[pf], "color": COLORS[pf], "points": aligned[pf], "from": spans[pf]["from"], "to": spans[pf]["to"],
               "history_from": spans[pf]["history"].isoformat()} for pf in series]
    # combined additive series (spend, impressions, clicks); everything else is null on it
    comb = []
    for i, t in enumerate(labels):
        col = [aligned[pf][i] for pf in series if aligned[pf][i]]
        pt: dict = {"t": t, "partial": any(c.get("partial") for c in col)}
        for k in ADDITIVE_ALL:
            vals = [c[k] for c in col if c.get(k) is not None]
            pt[k] = (round(sum(vals), 2) if k == "spend" else sum(vals)) if vals else None
        comb.append(pt)
    if source == "upstack" and groups:
        notes.append("Upstack attribution is drawn only where Upstack has it: "
                     + ", ".join(f"{g['label']} from {q._fmt_day(dt.date.fromisoformat(g['history_from']), True)}" for g in groups) + ".")
    info = head_info(wins, extra["note"]) if wins else {}
    tbl = day_table(conn, meta, args, platform=platform, product_slug=product_slug, ctype=ctype)
    part = {pt["t"]: bool(pt.get("partial")) for pt in comb}
    total = total_series(tbl, grain, labels, part)
    return {"grain": grain, "numbers": numbers, "labels": labels, "points": comb, "groups": groups, "total": total, "window_info": info, "compare_info": None, "notes": notes}


# ---------------------------------------------------------------------------------------------------------------- totals: Total spend and Total NC ROAS
#
# Meta and Google are the only spend platforms, so Total spend = Meta spend + Google spend (each platform's own spend, the same in every basis).
# Total NC ROAS = the business's ACTUAL new-customer revenue / total spend. The numerator is Upstack's order-level new-customer revenue per Pacific day
# (upstack_orders_daily, the same source and buckets as the pacing 'NC Rev'), counted once. It is NOT the sum of Meta-attributed and Google-attributed
# NC revenue: both platforms credit the same orders and some orders are credited to neither. That attributed sum is shown next to it with the gap.
# The ratio is a ratio of sums over the days both platforms and the orders feed cover; a day without data is blank, never zero.

NO_ROAS_FILTER = ("Total NC ROAS is shown for all platforms and all products only: Upstack's orders have no product or platform dimension, so new-customer revenue "
                  "cannot be split to a filtered view. Total spend below still follows the filter.")
NO_ROAS_ORDERS = "New-customer order data (Upstack orders) is not loaded yet, so Total NC ROAS is not available."
NO_ROAS_PLATFORM = "Total NC ROAS needs both Meta and Google data in the window."


def orders_coverage(conn) -> tuple[dt.date | None, dt.date | None]:
    r = q.row_of(conn, "SELECT min(day) AS a, max(day) AS b FROM upstack_orders_daily")
    return (r["a"], r["b"]) if r and r["a"] else (None, None)


def orders_by_day(conn, lo: dt.date, hi: dt.date) -> dict[dt.date, dict]:
    return {r["day"]: {"nc_rev": float(r["nc_rev"]), "nc_orders": float(r["nc_orders"]), "total_rev": float(r["total_rev"])}
            for r in q.rows_of(conn, "SELECT day, nc_rev, nc_orders, total_rev FROM upstack_orders_daily WHERE day BETWEEN %(a)s AND %(b)s", {"a": lo, "b": hi})}


def _range(span: q.DataMeta, w: q.WindowSpec) -> tuple[dt.date, dt.date]:
    return (span.daily_start, span.data_through) if w.is_launch else (w.date_from, w.date_to)


def _day_points(conn, span, w, pf: str, product, ctype) -> list[dict]:
    if pf == "meta":
        return q.campaigns_trend(conn, span, w, product, grain="day")["points"]
    return gg.trend(conn, span, w, "day", ctype, None)["points"]


def _product_arg(conn, slug):
    if slug is None:
        return "all"
    return None if slug == q.NO_PRODUCT_SLUG else q.get_product(conn, slug)


def day_table(conn, meta: q.DataMeta, args, *, platform=None, product_slug=None, ctype=None) -> dict:
    """Per Pacific day: Meta spend, Google spend (None where the platform's window does not cover the day), actual NC revenue (None where the orders feed has no
    day) and, on the Upstack new-customer basis, what each platform attributes. `full` = both platforms, no product / type filter: the only view where a total
    NC ROAS means something."""
    want = [pf for pf in PLATFORMS if (platform in (None, pf)) and not (pf == "google" and product_slug is not None) and not (pf == "meta" and ctype is not None)]
    wins, skipped, extra = windows(meta, args, "own", [pf for pf in PLATFORMS if platform in (None, pf)])
    spend: dict[str, dict[dt.date, float]] = {}
    rng: dict[str, tuple] = {}
    for pf in want:
        if pf not in wins:
            continue
        span, w = wins[pf]
        q.set_source("meta", "nc")
        product = _product_arg(conn, product_slug) if pf == "meta" else None
        spend[pf] = {dt.date.fromisoformat(pt["t"]): pt["spend"] for pt in _day_points(conn, span, w, pf, product, ctype)}
        rng[pf] = _range(span, w)
    full = platform is None and product_slug is None and ctype is None and set(spend) == set(PLATFORMS)
    days = sorted({d for m in spend.values() for d in m})
    first, last = orders_coverage(conn)
    orders = orders_by_day(conn, days[0], days[-1]) if days and first else {}
    # what each platform attributes on Upstack's new-customer basis (for the gap line)
    attributed: dict[str, dict[dt.date, float]] = {}
    if full and orders:
        wins_nc, _sk, _x = windows(meta, args, "nc")
        for pf, (span, w) in wins_nc.items():
            q.set_source("upstack", "nc")
            product = "all" if pf == "meta" else None
            attributed[pf] = {dt.date.fromisoformat(pt["t"]): pt["revenue"] for pt in _day_points(conn, span, w, pf, product, None)}
    q.set_source("meta")
    reason = None
    if not full:
        reason = NO_ROAS_FILTER if (platform or product_slug or ctype) else NO_ROAS_PLATFORM
    elif not orders:
        reason = NO_ROAS_ORDERS
    rows = []
    for d in days:
        m, g = spend.get("meta", {}).get(d), spend.get("google", {}).get(d)
        o = orders.get(d)
        defined = full and m is not None and g is not None and o is not None
        rows.append({"day": d, "meta": m, "google": g, "spend": (m or 0.0) + (g or 0.0), "nc_rev": o["nc_rev"] if o else None, "nc_orders": o["nc_orders"] if o else None,
                     "defined": defined, "attr_meta": attributed.get("meta", {}).get(d), "attr_google": attributed.get("google", {}).get(d)})
    return {"rows": rows, "full": full, "reason": reason, "ranges": rng, "orders_first": first, "orders_last": last, "skipped": skipped, "extra": extra, "wins": wins}


def _roas(rev: float, spend: float):
    return round(rev / spend, 4) if spend else None


def kpi_of(t: dict) -> dict:
    """The strip for one window from its day table: totals follow the table (each platform over its own window); NC ROAS is over the days all three feeds cover."""
    rows = t["rows"]
    meta_sp = round(sum(r["meta"] or 0.0 for r in rows), 2)
    google_sp = round(sum(r["google"] or 0.0 for r in rows), 2)
    total = round(meta_sp + google_sp, 2)
    out = {"spend": total, "meta_spend": meta_sp, "google_spend": google_sp,
           "meta_share": round(meta_sp / total, 4) if total else None, "google_share": round(google_sp / total, 4) if total else None,
           "window": {"from": rows[0]["day"].isoformat(), "to": rows[-1]["day"].isoformat(), "days": len(rows)} if rows else None}
    D = [r for r in rows if r["defined"]]
    nc: dict = {"available": bool(D) and t["reason"] is None, "reason": t["reason"]}
    if D and t["reason"] is None:
        rev = round(sum(r["nc_rev"] for r in D), 2)
        sp = round(sum(r["spend"] for r in D), 2)
        attr_m = round(sum(r["attr_meta"] or 0.0 for r in D), 2) if any(r["attr_meta"] is not None for r in D) else None
        attr_g = round(sum(r["attr_google"] or 0.0 for r in D), 2) if any(r["attr_google"] is not None for r in D) else None
        have = attr_m is not None and attr_g is not None
        attr_sum = round(attr_m + attr_g, 2) if have else None
        nc.update({"rev": rev, "spend": sp, "roas": _roas(rev, sp), "from": D[0]["day"].isoformat(), "to": D[-1]["day"].isoformat(), "days": len(D),
                   "orders": round(sum(r["nc_orders"] or 0 for r in D)),
                   "attributed": {"meta": attr_m, "google": attr_g, "sum": attr_sum, "gap": round(attr_sum - rev, 2) if have else None,
                                  "gap_pct": round((attr_sum - rev) / rev, 4) if have and rev else None}})
        if len(D) < len(rows):
            nc["partial"] = f"Days with Meta, Google and order data: {len(D)} of {len(rows)} in this window."
    elif t["reason"] is None:
        nc["reason"] = "No day in this window has Meta, Google and order data together."
    out["nc"] = nc
    return out


ROAS_LABELS = {"own": ("Revenue", "ROAS"), "nc": ("NC revenue", "NC ROAS"), "all": ("Revenue", "ROAS")}      # as the tables label them on each basis


def platform_view_of(platform, product_slug, ctype) -> str | None:
    """The platform whose own figures the strip shows: the Platform filter, or the platform a Product (Meta) / Campaign type (Google) filter belongs to."""
    return platform or ("meta" if product_slug is not None else "google" if ctype is not None else None)


def platform_total(conn, meta: q.DataMeta, args, numbers: str, view: str, *, platform, product_slug, ctype) -> dict:
    """One platform's own figures for the strip: the subtotal of the All campaigns table for the same arguments (so spend, revenue and ROAS tie to it by construction),
    on the Numbers basis in force (own reporting, Upstack new customers, Upstack all orders). No blended total, no overlap with the other platform."""
    res = all_campaigns(conn, meta, args, numbers, platform=platform, product_slug=product_slug, ctype=ctype)
    sub = res["subtotals"].get(view)
    m = (sub or {}).get("m") or {}
    spend = round(m.get("spend") or 0.0, 2)
    wins, _sk, _x = windows(meta, args, numbers, [pf for pf in PLATFORMS if platform in (None, pf)])
    w = {}
    if view in wins:                                   # the days the figures cover (a since-launch window names its first and last day)
        lo, hi = _range(*wins[view])
        w = {"from": lo.isoformat(), "to": hi.isoformat(), "days": (hi - lo).days + 1}
    rev_l, roas_l = ROAS_LABELS[numbers]
    other = "google" if view == "meta" else "meta"
    scope = (q.NO_PRODUCT if product_slug == q.NO_PRODUCT_SLUG else q.get_product(conn, product_slug)["name"]) if product_slug is not None else ctype
    return {"view": view, "name": NAMES[view] + (f" · {scope}" if scope else ""), "platform": NAMES[view], "numbers": numbers, "basis": BASIS_LABEL[(view, numbers)], "labels": {"revenue": rev_l, "roas": roas_l},
            "spend": spend, f"{view}_spend": spend, f"{other}_spend": 0.0, f"{view}_share": 1.0 if spend else None, f"{other}_share": 0.0 if spend else None,
            "window": {"from": w.get("from"), "to": w.get("to"), "days": w.get("days")} if w else None,
            "m": {k: m.get(k) for k in ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks")} if sub else None,
            "note": None if sub else f"{NAMES[view]} has no data loaded for this window.", "notes": res["notes"]}


def kpis(conn, meta: q.DataMeta, args, *, numbers: str = "own", platform=None, product_slug=None, ctype=None) -> dict:
    """Totals strip above the All charts: window A and, when Compare is on (a date window), window B. Unfiltered it is the blended strip (Total spend, actual NC
    revenue, Total NC ROAS). With a Platform filter (or a Product / Campaign type filter, which belong to one platform) it is THAT platform's own figures on the
    Numbers basis in force, laid out the same: only the numbers and the labels change."""
    view = platform_view_of(platform, product_slug, ctype)
    if view:
        return _platform_kpis(conn, meta, args, numbers, view, platform=platform, product_slug=product_slug, ctype=ctype)
    a = day_table(conn, meta, args, platform=platform, product_slug=product_slug, ctype=ctype)
    out = {"view": "all", "filters": {"only": platform, "product": product_slug, "type": ctype}, "total": kpi_of(a), "compare": None, "delta": None,
           "orders": {"first": a["orders_first"].isoformat() if a["orders_first"] else None, "last": a["orders_last"].isoformat() if a["orders_last"] else None},
           "platform_windows": {k: {"from": v[0].isoformat(), "to": v[1].isoformat()} for k, v in a["ranges"].items()},
           "notes": [f"{NAMES[k]}: {why}" for k, why in a["skipped"].items()]}
    out["window_info"] = head_info(a["wins"], a["extra"]["note"]) if a["wins"] else {}
    cmp_on = any(args.get(k) for k in ("compare", "cfrom", "cto")) and a["wins"]
    if cmp_on:
        try:
            full_wins, _sk, _x = windows(meta, args, "own", keep_compare=True)
            span0, w0 = next(iter(full_wins.values()))
            cw = w0.cmp
        except q.ApiError:
            cw = None
        if cw is not None and not cw.is_empty:
            b_args = MultiDict(args)
            for k in ("compare", "cfrom", "cto", "perday", "window"):
                b_args.pop(k, None)
            b_args["from"], b_args["to"] = cw.date_from.isoformat(), cw.date_to.isoformat()
            try:
                b = day_table(conn, meta, b_args, platform=platform, product_slug=product_slug, ctype=ctype)
            except q.ApiError as e:
                if e.status != 400:
                    raise
                out["notes"].append("The comparison window starts before a platform's data does, so there is nothing to compare to.")
                return out
            out["compare"] = kpi_of(b)
            out["compare_info"] = {"from": cw.date_from.isoformat(), "to": cw.date_to.isoformat(), "days": (cw.date_to - cw.date_from).days + 1}
            ta, tb = out["total"], out["compare"]
            pct = lambda x, y: round((x - y) / y, 4) if x is not None and y else None
            out["delta"] = {"spend_pct": pct(ta["spend"], tb["spend"]), "nc_rev_pct": pct(ta["nc"].get("rev"), tb["nc"].get("rev")), "nc_roas_pct": pct(ta["nc"].get("roas"), tb["nc"].get("roas"))}
    return out


def _compare_window(meta: q.DataMeta, args, platforms):
    """window B of the page's own window (None when Compare is off, a launch window, or B is empty), as the first loaded platform reads it"""
    if not any(args.get(k) for k in ("compare", "cfrom", "cto")):
        return None
    try:
        full_wins, _sk, _x = windows(meta, args, "own", platforms, keep_compare=True)
        if not full_wins:
            return None
        cw = next(iter(full_wins.values()))[1].cmp
    except q.ApiError:
        return None
    return cw if cw is not None and not cw.is_empty else None


def _b_args(args, cw) -> MultiDict:
    b = MultiDict(args)
    for k in ("compare", "cfrom", "cto", "perday", "window"):
        b.pop(k, None)
    b["from"], b["to"] = cw.date_from.isoformat(), cw.date_to.isoformat()
    return b


def _platform_kpis(conn, meta, args, numbers, view, *, platform, product_slug, ctype) -> dict:
    kw = dict(platform=platform, product_slug=product_slug, ctype=ctype)
    a = platform_total(conn, meta, args, numbers, view, **kw)
    out = {"view": view, "filters": {"only": platform, "product": product_slug, "type": ctype}, "total": a, "compare": None, "delta": None, "numbers": numbers,
           "notes": list(a.pop("notes")), "platform_windows": {}}
    wins, _sk, extra = windows(meta, args, numbers, [pf for pf in PLATFORMS if platform in (None, pf)])
    out["window_info"] = head_info(wins, extra["note"]) if wins else {}
    out["platform_windows"] = {k: {"from": (w.date_from if not w.is_launch else span.daily_start).isoformat(), "to": (w.date_to if not w.is_launch else span.data_through).isoformat()} for k, (span, w) in wins.items()}
    cw = _compare_window(meta, args, [pf for pf in PLATFORMS if platform in (None, pf)])
    if cw is not None:
        try:
            b = platform_total(conn, meta, _b_args(args, cw), numbers, view, **kw)
        except q.ApiError as e:
            if e.status != 400:
                raise
            out["notes"].append("The comparison window starts before the platform's data does, so there is nothing to compare to.")
            return out
        b.pop("notes", None)
        out["compare"] = b
        out["compare_info"] = {"from": cw.date_from.isoformat(), "to": cw.date_to.isoformat(), "days": (cw.date_to - cw.date_from).days + 1}
        pct = lambda x, y: round((x - y) / y, 4) if x is not None and y else None
        ma, mb = a.get("m") or {}, b.get("m") or {}
        out["delta"] = {"spend_pct": pct(a["spend"], b["spend"]), "revenue_pct": pct(ma.get("revenue"), mb.get("revenue")), "roas_pct": pct(ma.get("roas"), mb.get("roas"))}
    return out


def _bucket_key(d: dt.date, grain: str) -> dt.date:
    return d if grain == "day" else d - dt.timedelta(days=d.weekday()) if grain == "week" else d.replace(day=1)


def total_series(t: dict, grain: str, labels: list[str], partial: dict[str, bool]) -> dict:
    """The Total series of the chart on the chart's own x axis: per bucket the platforms' summed spend, and NC ROAS as a ratio of sums over the days all feeds
    cover (never a mean of daily ratios). Buckets without such a day are null."""
    by: dict[dt.date, dict] = {}
    for r in t["rows"]:
        b = by.setdefault(_bucket_key(r["day"], grain), {"spend": 0.0, "meta": 0.0, "google": 0.0, "nc_rev": 0.0, "nc_spend": 0.0, "days": 0})
        b["spend"] += r["spend"]
        b["meta"] += r["meta"] or 0.0
        b["google"] += r["google"] or 0.0
        if r["defined"]:
            b["nc_rev"] += r["nc_rev"]
            b["nc_spend"] += r["spend"]
            b["days"] += 1
    pts = []
    for lab in labels:
        b = by.get(dt.date.fromisoformat(lab))
        if not b:
            pts.append(None)
            continue
        pts.append({"t": lab, "partial": bool(partial.get(lab)), "spend": round(b["spend"], 2), "meta_spend": round(b["meta"], 2), "google_spend": round(b["google"], 2),
                    "nc_rev": round(b["nc_rev"], 2) if b["days"] else None, "nc_spend": round(b["nc_spend"], 2) if b["days"] else None,
                    "nc_roas": _roas(b["nc_rev"], b["nc_spend"]) if b["days"] else None, "nc_days": b["days"]})
    return {"available": t["full"] and t["reason"] is None, "reason": t["reason"], "points": pts,
            "orders_first": t["orders_first"].isoformat() if t["orders_first"] else None}
