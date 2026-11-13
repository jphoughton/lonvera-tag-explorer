"""Meta breakdowns read API (GET only): what the nightly pull holds (coverage) and the rows of one breakdown set aggregated over a date range.
Numbers are Meta-reported. Reach and unique outbound clicks are stored per row but are never summed here (they are not additive across days or ads)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from . import queries as q

LEVELS = ("ad", "campaign", "account")
MAX_ROWS = 1000
SUMS = ("spend", "impressions", "clicks", "inline_link_clicks", "outbound_clicks", "purchases", "purchase_value")


def _f(v) -> float | None:
    return None if v is None else (float(v) if isinstance(v, Decimal) else v)


def _ratio(a, b, scale=1.0, nd=4):
    return round(a / b * scale, nd) if a is not None and b else None


def _day(args, name: str) -> dt.date | None:
    v = (args.get(name) or "").strip()
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise q.ApiError(400, f"{name} must be a date like 2026-11-09") from None


def normalise_set(v: str) -> str:
    """'age,gender' | 'age+gender' | 'age gender' (a plus in a URL arrives as a space) -> 'age,gender'."""
    return ",".join(x for x in v.replace("+", ",").replace(" ", ",").split(",") if x)


def coverage(conn) -> dict:
    rows = conn.execute("SELECT breakdown_set, level, status, first_day, last_day, last_error, rows, checked_at FROM meta_breakdown_coverage ORDER BY breakdown_set, level").fetchall()
    prog = conn.execute("SELECT count(*) AS n, max(done_at) AS last FROM meta_breakdown_progress WHERE level IN ('ad', 'campaign', 'account')").fetchone()
    out = [{"breakdown_set": r["breakdown_set"], "level": r["level"], "status": r["status"], "first_day": r["first_day"].isoformat() if r["first_day"] else None,
            "last_day": r["last_day"].isoformat() if r["last_day"] else None, "rows": int(r["rows"]), "last_error": r["last_error"],
            "checked_at": r["checked_at"].isoformat()} for r in rows]
    return {"rows": out, "count": len(out), "with_data": sum(1 for r in out if r["rows"] > 0), "backfill_windows_recorded": int(prog["n"]),
            "note": "Meta-reported numbers. status unsupported = Meta refused that set at that level; a set refused at ad level is held at campaign, then account level."}


def breakdown(conn, args) -> dict:
    raw = (args.get("set") or "").strip()
    if not raw:
        raise q.ApiError(400, "set is required, for example set=age or set=age,gender (see /api/meta/breakdowns/coverage)")
    bset = normalise_set(raw)
    level = (args.get("level") or "").strip().lower() or None
    if level and level not in LEVELS:
        raise q.ApiError(400, f"level must be one of {', '.join(LEVELS)}")
    have = conn.execute("SELECT level, min(first_day) AS f, max(last_day) AS l FROM meta_breakdown_coverage WHERE breakdown_set = %s AND rows > 0 GROUP BY level", (bset,)).fetchall()
    if not have:
        raise q.ApiError(404, f"no rows held for breakdown set {bset!r} (see /api/meta/breakdowns/coverage)")
    if level is None:
        level = next(lv for lv in LEVELS if any(h["level"] == lv for h in have))        # the finest level that has data
    held = next((h for h in have if h["level"] == level), None)
    if held is None:
        raise q.ApiError(404, f"no rows held for {bset!r} at level {level}")
    end = _day(args, "end") or held["l"]
    start = _day(args, "start") or max(held["f"], end - dt.timedelta(days=6))
    if start > end:
        raise q.ApiError(400, "start must not be after end")
    params: dict = {"bset": bset, "level": level, "start": start, "end": end}
    where = ["d.breakdown_set = %(bset)s", "d.level = %(level)s", "d.day BETWEEN %(start)s AND %(end)s"]
    cte, product = "", None
    cid = (args.get("campaign_id") or "").strip()
    if cid:
        if level == "account":
            raise q.ApiError(400, "campaign_id cannot filter account-level rows")
        where.append("d.campaign_id = %(cid)s")
        params["cid"] = cid
    slug = (args.get("product") or "").strip()
    if slug:
        if level == "account":
            raise q.ApiError(400, "product cannot filter account-level rows")
        product = q.get_product(conn, slug)
        params["prod"] = product["id"]
        cte = "WITH " + q._pfa(product["id"]) + ", pa AS (SELECT ad_id, campaign_id FROM meta_ads WHERE meta_family_key IN (SELECT meta_family_key FROM pfa)) "
        where.append("d.entity_id IN (SELECT ad_id FROM pa)" if level == "ad" else "d.campaign_id IN (SELECT campaign_id FROM pa WHERE campaign_id IS NOT NULL)")
    names = bset.split(",")
    sql = (cte + "SELECT d.dim1, d.dim2, d.dim3, " + ", ".join(f"sum(d.{c}) AS {c}" for c in SUMS) + ", count(*) AS source_rows FROM meta_breakdown_daily d WHERE "
           + " AND ".join(where) + " GROUP BY d.dim1, d.dim2, d.dim3 ORDER BY sum(d.spend) DESC NULLS LAST, sum(d.purchases) DESC NULLS LAST, d.dim1, d.dim2, d.dim3 LIMIT %(lim)s")
    params["lim"] = MAX_ROWS + 1
    res = conn.execute(sql, params).fetchall()
    truncated = len(res) > MAX_ROWS
    rows = []
    for r in res[:MAX_ROWS]:
        sp, im, cl, lc, pu, pv = (_f(r[k]) for k in ("spend", "impressions", "clicks", "inline_link_clicks", "purchases", "purchase_value"))
        rows.append({"dims": dict(zip(names, (r["dim1"], r["dim2"], r["dim3"]))), "spend": sp, "impressions": im, "clicks": cl, "link_clicks": lc,
                     "outbound_clicks": _f(r["outbound_clicks"]), "purchases": pu, "revenue": pv,
                     "roas": _ratio(pv, sp, nd=3), "cost_per_purchase": _ratio(sp, pu, nd=2), "cost_per_1000_impressions": _ratio(sp, im, 1000, 2),
                     "click_rate": _ratio(cl, im), "link_click_rate": _ratio(lc, im), "source_rows": int(r["source_rows"])})
    return {"set": bset, "level": level, "start": start.isoformat(), "end": end.isoformat(), "product": product["slug"] if product else None, "campaign_id": cid or None,
            "dimensions": names, "rows": rows, "truncated": truncated, "held": {"first_day": held["f"].isoformat(), "last_day": held["l"].isoformat()},
            "note": "Meta-reported purchases and revenue (they count returning customers). Reach and frequency are not returned: they are not additive across days or ads. "
                    "Action breakdown sets split purchases and revenue only (spend and impressions are blank). Sum within one set only, never across sets."
                    + (" A campaign that runs several products is counted whole under each product it contains." if product and level == "campaign" else "")}
