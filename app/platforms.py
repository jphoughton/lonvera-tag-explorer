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
COLORS = {"meta": "#1a5fb4", "google": "#d9822b"}
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


def windows(meta: q.DataMeta, args, numbers: str, platforms=PLATFORMS) -> tuple[dict, dict, dict]:
    """({platform: (data span, WindowSpec)}, {platform: why it is left out}, info). A platform whose data is not loaded is left out. 'Since launch' on the
    All views is the stretch every loaded platform has data for (a platform alone keeps its own). Stage / Active only / comparison are dropped."""
    source, basis = NUMBERS[numbers]
    a = MultiDict(args)
    for k in DROP:
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
    return {"grain": grain, "numbers": numbers, "labels": labels, "points": comb, "groups": groups, "window_info": info, "compare_info": None, "notes": notes}
