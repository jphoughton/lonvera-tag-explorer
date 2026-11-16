"""Per-cut signals for the creative priority score (app/priority_score.py; spec docs/PRIORITY_SCORE.md). Read-only.

signals(conn, group, frm, to, filters) reads three things and hands them to build_signals (pure, also used by analysis/priority_score_backtest.py so the backtest runs the same code):
  families   one row per tracker family (first tracker row), tagged exactly like the By cut view: priorities_cuts.CUT_CTE (Communication Overview line tag, else the tracker's own
             columns), with product, vendor and media (Image / Video from the tracker Type)
  daily      family_daily (Meta spend) joined to family_daily_upstack_nc (Upstack new customers and their revenue) per tracker family per day, summed over stages
  tickets    the production assignments (priorities_assign.list_assignments), open = assigned .. ready, not dropped
group is any ordered subset of cluster / icp / pain_point / vendor / media (product is a filter, never a scoring level). Scoring windows end on `to` (the as-of day; default the last
loaded day): last 7, last 30 and days 31 to 60. frm..to only adds window_* totals for display. Live = spent on `to` or the day before (the By cut view's two loaded days).
"""
from __future__ import annotations

import datetime as dt

import psycopg

from . import priorities_assign as pa, priorities_cuts as pc
from .priority_score import HIT_SPEND, JUDGED_SPEND, MATURE_DAYS

SCORE_DIMS = ("cluster", "icp", "pain_point", "vendor", "media")
OPEN_STATUSES = ("assigned", "brief_sent", "in_production", "in_review", "ready")
HIT_COHORT_DAYS = 180          # hit rate reads ads whose first spend day is 14..180 days before the as-of day
OLD_AD_DAYS = 60               # an ad is "old" once its first spend day is more than this far back

FAMILIES_SQL = pc.CUT_CTE + """
SELECT t.family_key, t.product, t.vendor, t.media, t.cluster, t.icp, t.pain_point FROM tagged t
"""
DAILY_SQL = """
WITH m AS (SELECT family_key, day, sum(spend) AS spend FROM family_daily WHERE day <= %(to)s GROUP BY 1, 2),
     n AS (SELECT family_key, day, sum(purchases) AS nc_purchases, sum(revenue) AS nc_revenue FROM family_daily_upstack_nc WHERE day <= %(to)s GROUP BY 1, 2)
SELECT COALESCE(m.family_key, n.family_key) AS family_key, COALESCE(m.day, n.day) AS day, COALESCE(m.spend, 0) AS spend, n.nc_purchases, n.nc_revenue
FROM m FULL JOIN n ON n.family_key = m.family_key AND n.day = m.day
"""


def parse_score_group(raw) -> list[str]:
    """group for scoring: the By cut parser, minus product (product can filter but is not a level the score ranks)."""
    g = pc.parse_group(raw if isinstance(raw, str) else ",".join(raw or []))
    g = [d for d in g if d != "product"]
    if not g:
        raise pc.q.ApiError(400, "the priority score needs at least one of: " + ", ".join(SCORE_DIMS) + " (product is a filter, not a scoring level)")
    return g


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _ticket_media(r: dict):
    return {"video": "Video", "image": "Image"}.get(r.get("media_type")) or ("Video" if r.get("format_code") else None)


def _keep(row: dict, filters: dict) -> bool:
    """filters: {"products": [slug], "cluster": [normalised], "icp": [...], "pain_point": [...], "vendor": [...], "media": [...]} ('' = untagged). Missing / empty = no filter."""
    prods = filters.get("products")
    if prods and row.get("product") not in prods:
        return False
    for d in SCORE_DIMS:
        vals = filters.get(d)
        if vals and pc._norm(row.get(d)) not in vals:
            return False
    return True


def _date(x):
    if x is None or isinstance(x, dt.date) and not isinstance(x, dt.datetime):
        return x
    if isinstance(x, dt.datetime):
        return x.date()
    return dt.date.fromisoformat(str(x)[:10])


def build_signals(families: list[dict], daily: list[dict], tickets: list[dict], group: list[str], as_of: dt.date, frm: dt.date | None = None, data_start: dt.date | None = None,
                  filters: dict | None = None) -> list[dict]:
    """Pure: the per-cut signal dicts that priority_score.score_cuts reads (schema in its docstring), plus labels, window_* and diagnostics. Days after as_of are ignored, so the
    backtest can replay any past day. data_start = the first day of daily data (ads spending on it are treated as launched before the data starts: old, outside the hit cohort)."""
    filters = filters or {}
    frm = frm or as_of - dt.timedelta(days=29)
    d30, d60, d7 = as_of - dt.timedelta(days=29), as_of - dt.timedelta(days=59), as_of - dt.timedelta(days=6)
    fams = {f["family_key"]: f for f in families if _keep(f, filters)}
    if data_start is None:
        data_start = min((_date(r["day"]) for r in daily), default=as_of)

    # per family: window sums, spend to date, first spend day
    acc: dict[str, dict] = {}
    for r in daily:
        k = r["family_key"]
        if k not in fams:
            continue
        day = _date(r["day"])
        if day > as_of:
            continue
        a = acc.setdefault(k, {"s30": 0.0, "r30": 0.0, "p30": 0.0, "sp": 0.0, "rp": 0.0, "s7": 0.0, "r7": 0.0, "todate": 0.0, "first": None, "live": False, "sw": 0.0, "rw": 0.0, "pw": 0.0})
        sp, rv, pu = _f(r.get("spend")), _f(r.get("nc_revenue")), _f(r.get("nc_purchases"))
        a["todate"] += sp
        if sp > 0 and (a["first"] is None or day < a["first"]):
            a["first"] = day
        if sp > 0 and day >= as_of - dt.timedelta(days=1):
            a["live"] = True
        if day >= d30:
            a["s30"] += sp; a["r30"] += rv; a["p30"] += pu
        elif day >= d60:
            a["sp"] += sp; a["rp"] += rv
        if day >= d7:
            a["s7"] += sp; a["r7"] += rv
        if day >= frm:
            a["sw"] += sp; a["rw"] += rv; a["pw"] += pu

    def mature(a) -> bool:
        return a["first"] is not None and a["first"] > data_start and MATURE_DAYS <= (as_of - a["first"]).days <= HIT_COHORT_DAYS and a["todate"] >= JUDGED_SPEND

    # product baselines inside the scope (every ad of the product, tagged or not)
    prod: dict[str, dict] = {}
    for k, a in acc.items():
        p = prod.setdefault(fams[k].get("product"), {"s30": 0.0, "r30": 0.0, "sp": 0.0, "rp": 0.0, "judged": 0, "hits": 0})
        p["s30"] += a["s30"]; p["r30"] += a["r30"]; p["sp"] += a["sp"]; p["rp"] += a["rp"]
        if mature(a):
            p["judged"] += 1
            p["hits"] += a["todate"] >= HIT_SPEND
    for p in prod.values():
        p["roas30"] = p["r30"] / p["s30"] if p["s30"] else None
        p["roas_p"] = p["rp"] / p["sp"] if p["sp"] else None
        p["hr"] = p["hits"] / p["judged"] if p["judged"] else None
    tot_s30 = sum(p["s30"] for p in prod.values())
    tot_r30 = sum(p["r30"] for p in prod.values())
    tot_sp, tot_rp = sum(p["sp"] for p in prod.values()), sum(p["rp"] for p in prod.values())
    tot_j, tot_h = sum(p["judged"] for p in prod.values()), sum(p["hits"] for p in prod.values())
    blended30 = tot_r30 / tot_s30 if tot_s30 else None
    blended_p = tot_rp / tot_sp if tot_sp else None
    blended_hr = tot_h / tot_j if tot_j else None
    launched60 = [k for k, a in acc.items() if a["first"] is not None and a["first"] > data_start and (as_of - a["first"]).days < 60]
    tot_launch60 = len(launched60)

    cuts: dict[tuple, dict] = {}

    def slot(row: dict) -> dict:
        key = tuple(pc._norm(row.get(d)) for d in group)
        if key not in cuts:
            cuts[key] = {"labels": {d: pc._label(d, row.get(d)) for d in group}, "fams": [], "tickets": [], "tagged": 0}
        return cuts[key]

    for k, f in fams.items():
        c = slot(f)
        c["tagged"] += 1
        if k in acc:
            c["fams"].append(k)
    for t in tickets:
        row = {**t, "media": _ticket_media(t) if ("media_type" in t or "format_code" in t) else t.get("media")}
        if not _keep(row, filters):
            continue
        st = pc.status_of(t)
        if t.get("stage") == "dropped" or t.get("dropped_at") or st not in OPEN_STATUSES:
            continue
        slot(row)["tickets"].append(t)

    out = []
    for key, c in cuts.items():
        fs = [acc[k] for k in c["fams"]]
        prods_of = [fams[k].get("product") for k in c["fams"]]
        s30 = sum(a["s30"] for a in fs); r30 = sum(a["r30"] for a in fs); p30 = sum(a["p30"] for a in fs)
        sp = sum(a["sp"] for a in fs); rp = sum(a["rp"] for a in fs)
        # product-mix expectations (weights = the cut's own spend / judged ads per product)
        exp30 = sum(a["s30"] * (prod[p]["roas30"] or 0) for a, p in zip(fs, prods_of)) / s30 if s30 else blended30
        exp_p = sum(a["sp"] * (prod[p]["roas_p"] or 0) for a, p in zip(fs, prods_of)) / sp if sp else blended_p
        judged = [(a, p) for a, p in zip(fs, prods_of) if mature(a)]
        hits = sum(1 for a, _ in judged if a["todate"] >= HIT_SPEND)
        exp_hr = (sum(prod[p]["hr"] or 0 for _, p in judged) / len(judged)) if judged else blended_hr
        firsts = [a["first"] for a in fs if a["first"] is not None]
        new60 = sum(1 for a in fs if a["first"] is not None and a["first"] > data_start and (as_of - a["first"]).days < 60)
        new30 = sum(1 for a in fs if a["first"] is not None and a["first"] > data_start and (as_of - a["first"]).days < 30)
        old30 = sum(a["s30"] for a in fs if a["first"] is not None and (a["first"] <= data_start or (as_of - a["first"]).days > OLD_AD_DAYS))
        ages = sorted(max(0, (as_of - _date(t.get("created_at"))).days) for t in c["tickets"] if t.get("created_at"))
        out.append({
            "labels": c["labels"], "key": list(key),
            "spend_30d": round(s30, 2), "nc_revenue_30d": round(r30, 2), "nc_purchases_30d": round(p30, 2), "spend_prior30": round(sp, 2), "nc_revenue_prior30": round(rp, 2),
            "spend_7d": round(sum(a["s7"] for a in fs), 2), "nc_revenue_7d": round(sum(a["r7"] for a in fs), 2),
            "nc_roas_7d": (sum(a["r7"] for a in fs) / sum(a["s7"] for a in fs)) if sum(a["s7"] for a in fs) else None,
            "nc_roas_30d": r30 / s30 if s30 else None, "nc_roas_prior30": rp / sp if sp else None, "nc_cpa_30d": s30 / p30 if p30 else None,
            "expected_nc_roas_30d": exp30, "expected_nc_roas_prior30": exp_p, "blended_nc_roas_30d": blended30,
            "judged_ads": len(judged), "hit_ads": hits, "expected_hit_rate": exp_hr,
            "live_ads": sum(1 for a in fs if a["live"]), "ads_spending_30d": sum(1 for a in fs if a["s30"] > 0),
            "launches_30d": new30, "launches_60d": new60,
            "days_since_launch": (as_of - max(firsts)).days if firsts else None,
            "last_launch_day": max(firsts).isoformat() if firsts else None,
            "spend_share_30d": s30 / tot_s30 if tot_s30 else 0.0, "launch_share_60d": new60 / tot_launch60 if tot_launch60 else 0.0,
            "top_ad_share_30d": (max(a["s30"] for a in fs) / s30) if s30 else None,
            "old_spend_share_30d": (old30 / s30) if s30 else None,
            "pipeline_open": len(c["tickets"]), "oldest_ticket_days": ages[-1] if ages else None, "median_ticket_days": ages[len(ages) // 2] if ages else None,
            "tagged_ads": c["tagged"],
            "window_spend": round(sum(a["sw"] for a in fs), 2), "window_nc_revenue": round(sum(a["rw"] for a in fs), 2),
            "window_nc_roas": (sum(a["rw"] for a in fs) / sum(a["sw"] for a in fs)) if sum(a["sw"] for a in fs) else None,
        })
    out.sort(key=lambda r: (-r["spend_30d"], tuple(r["labels"].values())))
    return out


def signals(conn, group, frm: dt.date | None = None, to: dt.date | None = None, filters: dict | None = None, week=None, vendor=None) -> dict:
    """{group, as_of, from, rows: [signal dict], warning}. group = list or comma string of cluster / icp / pain_point / vendor / media (product refused as a level).
    filters as in _keep (normalised values). week / vendor narrow the pipeline side only, like the By cut view. Nothing is written."""
    group = parse_score_group(group)
    filters = {k: v for k, v in (filters or {}).items() if v}
    warning = None
    try:
        last = conn.execute("SELECT max(day) AS d FROM family_daily").fetchone()["d"]
        to = to or last or dt.date.today() - dt.timedelta(days=1)
        frm = frm or to - dt.timedelta(days=29)
        families = [dict(r) for r in conn.execute(FAMILIES_SQL).fetchall()]
        lo = to - dt.timedelta(days=HIT_COHORT_DAYS + 60)
        data_start = conn.execute("SELECT min(day) AS d FROM family_daily").fetchone()["d"]
        daily = [dict(r) for r in conn.execute(DAILY_SQL.replace("WHERE day <= %(to)s", "WHERE day <= %(to)s AND day >= %(lo)s"), {"to": to, "lo": lo}).fetchall()]
        # first spend day must be the real first day even when it falls before lo: add one synthetic zero-revenue marker per family (spend counted only for to-date, outside every window)
        firsts = conn.execute("SELECT family_key, min(day) AS first, sum(spend) FILTER (WHERE day < %s) AS before FROM family_daily WHERE spend > 0 AND day <= %s GROUP BY 1", (lo, to)).fetchall()
        for r in firsts:
            if r["first"] < lo:
                daily.append({"family_key": r["family_key"], "day": r["first"], "spend": r["before"] or 0, "nc_purchases": 0, "nc_revenue": 0})
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        families, daily, data_start, warning = [], [], None, "Ad spend could not be read here (a table is missing), so only the pipeline counts are used."
        to = to or dt.date.today() - dt.timedelta(days=1)
        frm = frm or to - dt.timedelta(days=29)
    tickets = pa.list_assignments(conn, week=week, vendor=vendor, include_closed=False)["rows"]
    rows = build_signals(families, daily, tickets, group, to, frm, data_start, filters)
    return {"group": group, "as_of": to.isoformat(), "from": frm.isoformat(), "rows": rows, "warning": warning,
            "basis": "Spend is Meta's; new-customer (NC) revenue and purchases are Upstack first orders (any-click, 30 day). Hit rate: ads 14 to 180 days old that spent $100+, hit = $1,000+ spend."}
