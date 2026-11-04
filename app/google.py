"""Google (Marketing metrics, channel Google): campaigns and campaign types from Upstack's two Google feeds (etl.upstack_google).

Numbers: `source=meta` (the default; here it means Google's own reporting, delivered through Upstack's ad-platform feed) or `source=upstack` with
`basis=nc|all` (Upstack's any-click 30-day attribution, utm_source "Google Ads"). Spend, clicks and impressions are Google's in both. Google has no
creatives, so there is no tag tree: rows are campaign types (Shopping / Search / Performance Max / Demand Gen / Other, read from the campaign name)
and campaigns. Same windows, comparison and metric shaping as the Meta pages (queries.side_fields / cmp_blocks)."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from . import queries as q

TYPES = ("Shopping", "Search", "Performance Max", "Demand Gen", "Other")
GOOGLE_LABELS = {"reported": {"purchases": "Purchases", "revenue": "Conversion value", "roas": "ROAS", "cpa": "CPA"}}
NOT_IN_FEED = "(utm campaign not in the Google feed)"


def google_meta(meta: q.DataMeta, source: str) -> q.DataMeta:
    """The Google data span for the chosen numbers: Google's own from the first channel-feed day, Upstack's from the first attribution day."""
    if meta.google_through is None or meta.google_first_day is None:
        raise q.ApiError(503, "Google data is not loaded yet")
    first = meta.google_first_day
    if source == "upstack":
        if meta.google_upstack_first_day is None:
            raise q.ApiError(503, "Upstack's Google attribution is not loaded yet")
        first = max(first, meta.google_upstack_first_day)
    return replace(meta, daily_start=first, data_through=meta.google_through, impressions_from=first)


def source_info(w: q.WindowSpec, gmeta: q.DataMeta) -> dict:
    if w.source != "upstack":
        return {"key": "meta", "basis": None, "label": "Google", "labels": GOOGLE_LABELS["reported"], "model": None, "channel": "google",
                "summary": "Google's own reporting, delivered through Upstack's ad-platform feed: purchases and conversion value are what Google counts "
                           "(returning customers included). Switch to Upstack to read the same campaigns on Upstack's attribution.",
                "note": None, "history_from": gmeta.daily_start.isoformat(), "through": gmeta.data_through.isoformat(), "built_at": gmeta.google_built_at}
    base = q.UPSTACK_LABELS[w.basis]
    what = "First orders only (new customers) and their revenue" if w.basis == "nc" else "Every order credited to the campaign (new and returning customers) and its revenue"
    return {"key": "upstack", "basis": w.basis, "label": "Upstack · " + ("new customers" if w.basis == "nc" else "all orders"), "labels": base, "channel": "google",
            "model": "Upstack any-click attribution, 30-day window, Google Ads source",
            "summary": (f"{what}, credited to the Google campaign clicked in the 30 days before the order (Upstack any-click attribution, 30-day window, Google Ads source). "
                        "Spend, clicks and impressions are Google's, unchanged. Revenue is Upstack's order total (tax included, before refunds)."),
            "note": (f"History starts {q._fmt_day(gmeta.daily_start, True)}, so 'since launch' means since then. An order can be credited to several campaigns "
                     f"(any-click), so rows overlap; utm campaigns that are no Google campaign sit in a '{NOT_IN_FEED}' row."),
            "history_from": gmeta.daily_start.isoformat(), "through": gmeta.data_through.isoformat(), "built_at": gmeta.google_built_at}


def window_info(w: q.WindowSpec, gmeta: q.DataMeta) -> dict:
    info = w.info(gmeta)
    info["source"] = source_info(w, gmeta)
    info["channel"] = "google"
    info["stage"], info["stages"], info["stage_note"] = "all", list(q.STAGES), ""
    if w.source == "upstack" and w.is_launch:
        info["label"] = f"Since launch · Upstack history from {q._fmt_day(gmeta.daily_start, True)}"
    elif w.is_launch:
        info["label"] = f"Since launch · Google history from {q._fmt_day(gmeta.daily_start, True)}"
    return info


def _cols(w: q.WindowSpec) -> tuple[str, str]:
    if w.source != "upstack":
        return "purchases", "purchases_value"
    return ("nc", "nc_rev") if w.basis == "nc" else ("orders", "rev")


def _agg_cols(pcol: str, vcol: str, with_cmp: bool) -> str:
    def sums(prefix: str, pred: str) -> str:
        f = f" FILTER (WHERE {pred})"
        return (f"COALESCE(sum(spend){f}, 0) AS {prefix}spend, COALESCE(sum({pcol}){f}, 0) AS {prefix}purch, COALESCE(sum({vcol}){f}, 0) AS {prefix}rev, "
                f"COALESCE(sum(impressions){f}, 0) AS {prefix}imp, COALESCE(sum(clicks){f}, 0) AS {prefix}clk")
    out = sums("", "day BETWEEN %(wf)s AND %(wt)s") + ", count(DISTINCT campaign_key) FILTER (WHERE day BETWEEN %(wf)s AND %(wt)s AND spend > 0) AS active"
    if with_cmp:
        out += ", " + sums("c_", "day BETWEEN %(cf)s AND %(ct)s")
    return out


def _side(r: dict, prefix: str = "") -> dict:
    sp, clk = r[prefix + "spend"], r[prefix + "clk"]
    # Google clicks are all clicks, shown as link clicks; impressions are known for every day, so every row carries traffic (clk_tr = clk, sp_tr = spend)
    return {"spend": sp, "purchases": r[prefix + "purch"], "revenue": r[prefix + "rev"], "imp": r[prefix + "imp"], "clk": clk, "clk_tr": clk, "sp_tr": sp,
            "purch_t": r[prefix + "purch"]}


def _m(r: dict, w: q.WindowSpec, cw: q.WindowSpec | None) -> dict:
    m = {**q.side_fields(_side(r), w.days), "active_campaigns": int(r["active"])}
    if cw is not None:
        m["cmp"], m["delta"] = q.cmp_blocks(_side(r), w, _side(r, "c_"))
    return m


def _windows(gmeta: q.DataMeta, w: q.WindowSpec, params: dict) -> q.WindowSpec | None:
    """wf/wt (A) and cf/ct (B) day ranges; returns the usable comparison window."""
    cw = w.cmp if (w.cmp is not None and not w.cmp.is_empty) else None
    if w.is_launch:
        params.update(wf=gmeta.daily_start, wt=gmeta.data_through)
    else:
        params.update(wf=w.date_from, wt=w.date_to)
    lo, hi = params["wf"], params["wt"]
    if cw is not None:
        params.update(cf=cw.date_from, ct=cw.date_to)
        lo, hi = min(lo, cw.date_from), max(hi, cw.date_to)
    params.update(lo=lo, hi=hi)
    return cw


def rollup(conn, gmeta: q.DataMeta, w: q.WindowSpec, group: str, ctype: str | None = None) -> dict:
    if group not in ("type", "campaign"):
        raise q.ApiError(400, "group must be type or campaign")
    if ctype is not None and ctype not in TYPES:
        raise q.ApiError(400, "type must be one of " + ", ".join(TYPES))
    pcol, vcol = _cols(w)
    params: dict = {}
    cw = _windows(gmeta, w, params)
    where = "day BETWEEN %(lo)s AND %(hi)s"
    if ctype is not None:
        where += " AND campaign_type = %(ctype)s"
        params["ctype"] = ctype
    keycols = "campaign_type" if group == "type" else "campaign_key, campaign_name, campaign_type, in_feed"
    sql = f"SELECT {keycols}, {_agg_cols(pcol, vcol, cw is not None)} FROM google_daily WHERE {where} GROUP BY {keycols}"
    raw = q.rows_of(conn, sql, params)
    status = {r["campaign_id"]: r for r in q.rows_of(conn, "SELECT campaign_id, status, bid_strategy FROM upstack_google_campaigns")} if group == "campaign" else {}
    rows = []
    for r in raw:
        m = _m(r, w, cw)
        if group == "type":
            rows.append({"key": r["campaign_type"], "label": r["campaign_type"], "campaign_type": r["campaign_type"], "m": m})
        else:
            st = status.get(r["campaign_key"]) or {}
            rows.append({"key": r["campaign_key"], "label": r["campaign_name"] if r["in_feed"] else NOT_IN_FEED, "utm_campaign": None if r["in_feed"] else r["campaign_name"],
                         "campaign_type": r["campaign_type"], "in_feed": r["in_feed"], "status": st.get("status"), "bid_strategy": st.get("bid_strategy"), "m": m})
    order = {t: i for i, t in enumerate(TYPES)}
    rows.sort(key=lambda x: (-(x["m"]["spend"] or 0), order.get(x["campaign_type"], 9), x["label"]))
    total_raw = q.row_of(conn, f"SELECT {_agg_cols(pcol, vcol, cw is not None)} FROM google_daily WHERE {where}", params)
    total = _m(total_raw, w, cw)
    q.apply_shares([r["m"] for r in rows] + [total], total)
    return {"group": group, "type": ctype, "rows": rows, "total": {"m": total}}


def trend(conn, gmeta: q.DataMeta, w: q.WindowSpec, grain: str, ctype: str | None, campaign: str | None) -> dict:
    if grain not in ("day", "week"):
        raise q.ApiError(400, "grain must be day or week")
    if ctype is not None and ctype not in TYPES:
        raise q.ApiError(400, "type must be one of " + ", ".join(TYPES))
    pcol, vcol = _cols(w)
    params: dict = {}
    cw = _windows(gmeta, w, params)
    where = "TRUE"
    if ctype is not None:
        where += " AND campaign_type = %(ctype)s"
        params["ctype"] = ctype
    if campaign is not None:
        where += " AND campaign_key = %(ck)s"
        params["ck"] = campaign

    def series(lo: dt.date, hi: dt.date) -> list[dict]:
        key = "day" if grain == "day" else "date_trunc('week', day)::date"
        res = q.rows_of(conn, f"""SELECT {key} AS t, sum(spend) AS spend, sum({pcol}) AS purch, sum({vcol}) AS rev, sum(impressions) AS imp, sum(clicks) AS clk
                                  FROM google_daily WHERE {where} AND day BETWEEN %(a)s AND %(b)s GROUP BY 1 ORDER BY 1""", {**params, "a": lo, "b": hi})
        by = {r["t"]: r for r in res}
        out, t = [], lo if grain == "day" else lo - dt.timedelta(days=lo.weekday())
        while t <= hi:
            r = by.get(t)
            sp, pu, rv, im, cl = (q._f(r[k]) if r else 0.0 for k in ("spend", "purch", "rev", "imp", "clk"))
            span_lo, span_hi = max(t, lo), min(t + dt.timedelta(days=0 if grain == "day" else 6), hi)
            out.append({"t": t.isoformat(), "partial": grain == "week" and ((span_hi - span_lo).days + 1) < 7, "spend": q.f2(sp), "purchases": q._num(pu), "revenue": q.f2(rv),
                        "roas": q.ratio(rv, sp, 4), "cpa": q.ratio(sp, pu, 2), "impressions": q._num(im), "link_clicks": q._num(cl),
                        "ctr": q.ratio(cl, im, 5), "cpc": q.ratio(sp, cl, 4), "cpm": q.ratio(sp * 1000, im, 4), "conv_rate": q.ratio(pu, cl, 5)})
            t += dt.timedelta(days=1 if grain == "day" else 7)
        return out
    out = {"grain": grain, "scope": {"type": ctype, "campaign": campaign}, "points": series(params["wf"], params["wt"])}
    if cw is not None:
        out["cmp_points"] = series(cw.date_from, cw.date_to)
    return out
