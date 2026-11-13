"""The breakdown step of `why`: runs brain/why_breakdowns.py over analytics.meta_breakdown_daily for the same windows `why` was asked about, then lists the change-ledger entries that
coincide with each material mover. Used by Why.finalize; everything here is read-only SELECTs through Why.q (same guard, same step budget).

Basis: Meta-reported (the breakdown rows carry no Upstack new-customer attribution): spend, impressions, link clicks, adds to cart, initiated checkouts and purchases that count
returning customers. Say so whenever a number from this step is quoted next to an Upstack new-customer figure.
"""
from __future__ import annotations

import datetime as dt
import time

from . import why_breakdowns as B

DIMENSIONS = ("publisher_platform", "placement", "impression_device", "device_platform", "age", "gender", "country")
MIN_STEPS_TO_RUN = 20
EXTRA_STEPS = 14                  # the step may use this many queries on top of the main walk's budget
EXTRA_SECONDS = 30.0
SKIP_METRIC_PREFIXES = ("repeat_", "klaviyo_", "nc_campaign_email", "google_", "amazon_", "renewals", "bids", "upsell", "checkout_funnel")
LOG_TYPES = ("update_ad_set_bid_strategy", "update_ad_set_budget", "update_ad_set_run_status", "update_ad_set_target_spec", "create_ad_set", "update_ad_set_optimization_goal",
             "update_campaign_budget", "update_campaign_run_status", "create_ad", "update_ad_run_status", "update_ad_creative")
PRIORITY = {t: i for i, t in enumerate(("bid_amount", "bid_strategy", "update_ad_set_bid_strategy", "daily_budget", "lifetime_budget", "update_ad_set_budget", "update_campaign_budget", "roas_floor",
                                         "paused", "activated", "archived", "update_ad_set_run_status", "update_campaign_run_status", "update_ad_set_target_spec", "update_ad_set_optimization_goal",
                                         "optimization_goal", "attribution_spec", "new_adset", "create_ad_set"))}
MAX_LEDGER_MOVERS = 8
BULK_OBJECTS = 15
SEGMENTS_PER_MOVER = 6


def applies(w) -> tuple[bool, str]:
    if w.platform == "google":
        return False, "the breakdown step reads Meta; this question is scoped to Google"
    if w.metric.startswith(SKIP_METRIC_PREFIXES) or w.metric == "repeat_revenue":
        return False, f"'{w.metric}' is not driven by Meta delivery (no breakdown step)"
    if not w.has("meta_breakdown_daily"):
        return False, "analytics.meta_breakdown_daily is not in the database"
    return True, ""


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def dim_sql(dim: str, lo: dt.date, end: dt.date, product: str | None, nseg: int) -> str:
    prod = f"and ad_id in (select ad_id from analytics.meta_ads where product_slug = {_lit(product)}) " if product else ""
    return (f"with r as (select day, value, sum(spend) spend, sum(impressions) impressions, sum(link_clicks) link_clicks, sum(add_to_carts) atc, sum(initiated_checkouts) ic, "
            f"sum(purchases) purchases, (count(add_to_carts) = count(*) and count(initiated_checkouts) = count(*)) funnel_ok "
            f"from analytics.meta_breakdown_daily where breakdown = {_lit(dim)} and day between date '{lo}' and date '{end}' and spend is not null {prod}"
            f"group by day, value), rk as (select value, rank() over (order by sum(spend) desc) rk from r group by value) "
            f"select r.day, case when rk.rk <= {nseg} then r.value else '(other)' end seg, sum(r.spend) spend, sum(r.impressions) impressions, sum(r.link_clicks) link_clicks, "
            f"sum(r.atc) atc, sum(r.ic) ic, sum(r.purchases) purchases, bool_and(r.funnel_ok) funnel_ok from r join rk on rk.value = r.value group by 1, 2 order by 1, 2")


def _asd(v) -> dt.date:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


def run_step(w) -> dict:
    """The `breakdown_changes` block of a why answer (or {"applies": False, "reason": ...})."""
    ok, why_not = applies(w)
    if not ok:
        return {"applies": False, "reason": why_not}
    from .why import BudgetExceeded            # local import: why.py imports this module
    if w.max_steps < MIN_STEPS_TO_RUN or w.budget_hit:
        return {"applies": True, "skipped": f"the main walk's query budget (max_steps {w.max_steps}) leaves no room: re-run with max_steps >= {MIN_STEPS_TO_RUN} (default 40) to get the breakdown step"}
    w.max_steps += EXTRA_STEPS                  # the step has its own allowance; steps_used and max_steps in the answer both include it
    w.time_budget = max(w.time_budget, (time.time() - w.t0) + EXTRA_SECONDS)
    try:
        return _run(w, BudgetExceeded)
    except BudgetExceeded:
        return {"applies": True, "error": "query budget ran out inside the breakdown step; re-run with a larger max_steps"}


def _run(w, BudgetExceeded) -> dict:
    lo = min(w.cstart, w.start - dt.timedelta(days=B.NOISE_DAYS))
    span = (w.end - lo).days + 1
    out: dict = {"applies": True, "basis": "Meta-reported (from analytics.meta_breakdown_daily): spend, impressions, link clicks, adds to cart, initiated checkouts and purchases; purchases count returning "
                 "customers and breakdown rows carry no Upstack new-customer attribution, so none of these ratios is an NC ROAS or NC CPA.",
                 "window": {"start": str(w.start), "end": str(w.end)}, "comparison": {"start": str(w.cstart), "end": str(w.cend)},
                 "method": ("Per dimension, each ratio (CPM, link click rate, cost per link click, link click to add to cart, add to cart to checkout, checkout to purchase, add to cart to purchase, cost "
                            "per purchase) is split exactly into mix (share of the denominator moving between segments) and within-segment rate shift; segments under 3% of spend are not reported; "
                            "a segment is material at 10% of the aggregate move or 1 point of the metric. Controls: median of the same weekdays of the prior 4 weeks and the pooled prior 7 days; noise = "
                            "2 standard deviations of the segment's own daily ratio over 28 days, divided by sqrt(days in the window). 'moved beyond noise' needs both controls."),
                 "dimensions": {}, "gaps": [], "notes": []}
    if span > 180:
        out["notes"].append(f"window spans {span} days: dimensions skipped (row limit)")
        return out
    nseg = max(5, min(12, 950 // span - 1))
    per_dim, last_loaded = [], None
    for dim in DIMENSIONS:
        try:
            rows = w.q(dim_sql(dim, lo, w.end, w.product, nseg), 1000)
        except BudgetExceeded:
            out["notes"].append(f"query budget ran out before {dim}")
            break
        if not rows:
            out["gaps"].append(f"{dim}: no rows between {lo} and {w.end}")
            continue
        for r in rows:
            r["day"] = _asd(r["day"])
        days = sorted({r["day"] for r in rows if r.get("spend")})
        last_loaded = max(days) if days and (last_loaded is None or max(days) > last_loaded) else last_loaded
        res = B.analyze_dimension(dim, rows, w.start, w.end, w.cstart, w.cend)
        res["first_day"], res["last_day"] = str(days[0]) if days else None, str(days[-1]) if days else None
        res["funnel_days_filled"] = len({r["day"] for r in rows if r.get("funnel_ok")})
        res["days_loaded"] = len(days)
        per_dim.append(res)
        out["gaps"].extend(res.get("gaps", []))
    if last_loaded is not None and last_loaded < w.end:
        out["notes"].append(f"breakdowns are loaded through {last_loaded}; the window ends {w.end}")
    ranked = B.rank_movers(per_dim)
    out["tests_run"] = sum(d.get("tests", 0) for d in per_dim)
    out["chance_flags_expected_one_control"] = round(out["tests_run"] * 0.0455, 1)
    out["headline"] = B.headline(per_dim, ranked, f"{w.start}..{w.end} vs {w.cstart}..{w.cend}")
    agg = {}
    for d in per_dim:
        for mid, mb in d.get("metrics", {}).items():
            a = agg.setdefault(mid, {"label": mb["label"], "basis": mb["basis"], "cmp": mb["cmp"], "cur": mb["cur"], "display": mb["display"], "aggregate": mb["aggregate"]})
            a.setdefault("by_dimension", {})[d["dimension"]] = {"mix_total": mb["mix_total"], "within_segment_total": mb["within_segment_total"], "breadth": mb["breadth"]["text"]}
    out["aggregate"] = agg
    out["movers"] = [dict(x, text=B.describe_mover(x)) for x in ranked[:14]]
    out["beyond_noise_count"] = sum(1 for x in ranked if x["verdict"] == "moved beyond noise")
    out["dimensions"] = {d["dimension"]: {"segments": d["segments"], "first_day": d.get("first_day"), "last_day": d.get("last_day"), "days_loaded": d.get("days_loaded"),
                                          "funnel_days_filled": d.get("funnel_days_filled"), "spend_share": d.get("spend_share", [])} for d in per_dim}
    if not w.has("meta_change_ledger", "meta_change_log", "meta_ads"):
        out["notes"].append("the change ledger views (analytics.meta_change_ledger / meta_change_log) are not in the database: no coincident-change join")
        return out
    try:
        out["coincident_changes"] = _ledger(w, ranked[:MAX_LEDGER_MOVERS], BudgetExceeded)
    except BudgetExceeded:
        out["notes"].append("query budget ran out before the change ledger join")
    return out


def _ledger(w, movers: list[dict], BudgetExceeded) -> dict:
    """Ad sets carrying each mover's spend, and the change-ledger / activity-log entries on them (and on their campaigns) within one day of the window. 'Coincident with', never cause."""
    lo, hi = w.start - dt.timedelta(days=1), w.end + dt.timedelta(days=1)
    seen, picks = set(), []
    for x in movers:
        k = (x["dimension"], x["segment"])
        if k in seen or x["segment"] == "(other)":
            continue
        seen.add(k)
        picks.append(k)
    res: dict = {"window_plus_minus_one_day": [str(lo), str(hi)], "movers": {}, "account_wide": {}, "other_dated_changes": []}
    vals = ", ".join(f"({_lit(d)}, {_lit(s)})" for d, s in picks)
    prod = f"and b.ad_id in (select ad_id from analytics.meta_ads where product_slug = {_lit(w.product)}) " if w.product else ""
    carriers: dict[tuple, list[dict]] = {}
    if picks:
        sql = (f"select breakdown, value, adset_id, campaign_id, cur_spend, cmp_spend from (select b.breakdown, b.value, b.adset_id, b.campaign_id, "
               f"sum(case when b.day between date '{w.start}' and date '{w.end}' then b.spend end) cur_spend, sum(case when b.day between date '{w.cstart}' and date '{w.cend}' then b.spend end) cmp_spend, "
               f"row_number() over (partition by b.breakdown, b.value order by sum(b.spend) desc) rn from analytics.meta_breakdown_daily b "
               f"where (b.breakdown, b.value) in (values {vals}) and b.adset_id is not null and b.day between date '{min(w.start, w.cstart)}' and date '{max(w.end, w.cend)}' {prod}"
               f"group by 1, 2, 3, 4) t where rn <= {SEGMENTS_PER_MOVER}")
        for r in w.q(sql, 1000):
            carriers.setdefault((r["breakdown"], r["value"]), []).append(r)
    adsets = sorted({r["adset_id"] for v in carriers.values() for r in v})
    camps = sorted({r["campaign_id"] for v in carriers.values() for r in v if r.get("campaign_id")})
    changes: list[dict] = []
    if adsets:
        a_in = ", ".join(_lit(a) for a in adsets)
        c_in = ", ".join(_lit(c) for c in camps) or "'-'"
        types = ", ".join(_lit(t) for t in LOG_TYPES)
        sql = (f"select 'ledger' src, l.day::date d, l.adset_id obj, l.campaign_id camp, l.change_type typ, count(*) n, min(l.old_value) old_value, max(l.new_value) new_value "
               f"from analytics.meta_change_ledger l where l.day between date '{lo}' and date '{hi}' and l.adset_id in ({a_in}) group by 1, 2, 3, 4, 5 "
               f"union all select 'log', c.event_day, coalesce(a.adset_id, c.object_id), c.campaign_id, c.event_type, count(*), coalesce(min(c.old_amount)::text, min(c.old_value)), coalesce(max(c.new_amount)::text, max(c.new_value)) "
               f"from analytics.meta_change_log c left join analytics.meta_ads a on a.ad_id = c.object_id and c.object_level = 'ad' "
               f"where c.event_day between date '{lo}' and date '{hi}' and not c.is_system_event and c.event_type in ({types}) "
               f"and (c.object_id in ({a_in}) or c.object_id in ({c_in}) or a.adset_id in ({a_in})) group by 1, 2, 3, 4, 5")
        changes = w.q(sql, 1000)
    by_adset: dict[str, list[dict]] = {}
    for c in changes:
        by_adset.setdefault(str(c["obj"]), []).append(c)
        if c.get("camp"):
            by_adset.setdefault("camp:" + str(c["camp"]), []).append(c)
    for (dim, seg), rows in carriers.items():
        tot_cur = sum(float(r["cur_spend"] or 0) for r in rows) or 1.0
        entry = {"ad_sets_carrying_the_segment": [{"adset_id": r["adset_id"], "campaign_id": r["campaign_id"], "spend_in_window": round(float(r["cur_spend"] or 0), 2),
                                                    "spend_in_comparison": round(float(r["cmp_spend"] or 0), 2)} for r in rows[:3]], "changes": []}
        got, seen_c = [], set()
        for r in rows:
            for c in by_adset.get(str(r["adset_id"]), []) + by_adset.get("camp:" + str(r["campaign_id"]), []):
                key = (c["src"], str(c["d"]), c["obj"], c["typ"])
                if key in seen_c:
                    continue
                seen_c.add(key)
                got.append({"date": str(c["d"]), "source": "ad set snapshot diff" if c["src"] == "ledger" else "activity log", "type": c["typ"], "object": c["obj"], "events": int(c["n"]),
                            "old": c.get("old_value"), "new": c.get("new_value")})
        got.sort(key=lambda e: (PRIORITY.get(e["type"], 9), e["date"], e["type"]))        # structure changes (bid, budget, status, targeting) before ad-level churn
        entry["changes"] = got[:8]
        entry["changes_total"] = len(got)
        kinds: dict[str, int] = {}
        for e in got:
            kinds[e["type"]] = kinds.get(e["type"], 0) + e["events"]
        entry["changes_by_type"] = kinds
        entry["reading"] = (f"{len(got)} change record(s) on the ad sets or campaigns carrying {dim} '{seg}' spend within one day of the window ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:5])}): "
                            "coincident with, not shown to cause, the move" if got else f"no change records on the top ad sets carrying {dim} '{seg}' within one day of the window")
        res["movers"][f"{dim}:{seg}"] = entry
    # account-wide counts in the same days, so a quiet segment is read against a busy or quiet account; a day on which one kind of edit touched many ad sets is a bulk edit and is listed with its amounts
    try:
        sql = (f"select 'ledger' src, day d, change_type typ, count(*) n, count(distinct adset_id) adsets, null::text old_amt, null::text new_amt, null::text t0, null::text t1 "
               f"from analytics.meta_change_ledger where day between date '{lo}' and date '{hi}' group by 1, 2, 3 "
               f"union all select 'log', event_day, event_type, count(*), count(distinct object_id), round(avg(old_amount), 2)::text, round(avg(new_amount), 2)::text, to_char(min(event_time) at time zone 'America/Los_Angeles', 'YYYY-MM-DD HH24:MI'), to_char(max(event_time) at time zone 'America/Los_Angeles', 'YYYY-MM-DD HH24:MI') "
               f"from analytics.meta_change_log where event_day between date '{lo}' and date '{hi}' and not is_system_event and event_type in ({', '.join(_lit(t) for t in LOG_TYPES)}) group by 1, 2, 3 "
               f"order by 1, 2, 4 desc")
        recs = w.q(sql, 500)
        tot: dict[str, dict] = {}
        bulk = []
        for r in recs:
            t = tot.setdefault(f"{r['src']}:{r['typ']}", {"records": 0, "objects_max_in_a_day": 0})
            t["records"] += int(r["n"])
            t["objects_max_in_a_day"] = max(t["objects_max_in_a_day"], int(r["adsets"]))
            if int(r["adsets"]) >= BULK_OBJECTS and r["typ"] not in ("create_ad", "update_ad_run_status"):
                bulk.append({"date": str(r["d"]), "source": "ad set snapshot diff" if r["src"] == "ledger" else "activity log", "type": r["typ"], "objects": int(r["adsets"]), "records": int(r["n"]),
                             "average_old": r["old_amt"], "average_new": r["new_amt"], "first_event_pacific": r["t0"], "last_event_pacific": r["t1"]})
        res["account_wide"] = dict(sorted(tot.items(), key=lambda kv: -kv[1]["records"]))
        res["bulk_edits"] = bulk[:8]
        if bulk:
            res["bulk_edits_reading"] = (f"{len(bulk)} day-and-type combination(s) in the window touched {BULK_OBJECTS}+ ad sets at once: a bulk edit changes delivery of every segment together, so look here before reading "
                                         "a single segment's move; coincident with, not shown to cause")
    except BudgetExceeded:
        pass
    try:
        ev = [e for e in w.events() if e.get("date") and lo <= e["date"] <= hi and e["type"] in ("landing_page_version_changed", "klaviyo_flow_created", "klaviyo_flow_updated", "feed_or_tracking_break",
                                                                                                  "ad_family_launched", "adset_launched")]
        ev.sort(key=lambda e: -(e.get("spend_in_window") or e.get("spend_cur") or 0))
        res["other_dated_changes"] = [{k: (str(v) if isinstance(v, dt.date) else v) for k, v in e.items() if k in ("type", "date", "label", "url", "product", "spend_in_window", "spend_cur")} for e in ev[:8]]
    except BudgetExceeded:
        pass
    res["reading"] = ("Change records are listed as 'coincident with' each mover (same ad sets or campaigns, within one day of the window); a budget, bid, status or targeting edit near a move is a lead to test "
                      "with a control, not a cause.")
    return res


def compact_block(bd: dict, top: int = 6) -> dict:
    """The agent-sized `breakdown_changes`: headline, the aggregate ratios with their own verdicts, the top movers as sentences with the numbers, the coincident change records and the gaps."""
    if not bd.get("applies", True) or bd.get("error") or bd.get("skipped"):
        return {k: bd.get(k) for k in ("applies", "reason", "error", "skipped") if bd.get(k)}
    keep = ("segment", "dimension", "metric", "verdict", "rate_cmp", "rate_cur", "share_of_aggregate_move", "mix_effect", "within_segment_effect", "normal_range", "daily_sd", "weekday_median", "prior7",
            "share_cmp", "share_cur", "delta_pp", "noise_2sd", "spend_share_avg", "concentration", "text")
    ch = bd.get("coincident_changes") or {}
    out = {"basis": bd.get("basis"), "headline": bd.get("headline"), "window": bd.get("window"), "comparison": bd.get("comparison"),
           "aggregate": {k: {"label": v["label"], "move": v["display"], "verdict": (v.get("aggregate") or {}).get("verdict")} for k, v in (bd.get("aggregate") or {}).items()},
           "movers": [{k: x[k] for k in keep if k in x and x[k] is not None} for x in (bd.get("movers") or [])[:top]], "beyond_noise_count": bd.get("beyond_noise_count"),
           "tests_run": bd.get("tests_run"), "coincident_changes": {"reading": ch.get("reading"), "bulk_edits": ch.get("bulk_edits"), "bulk_edits_reading": ch.get("bulk_edits_reading"), "account_wide": ch.get("account_wide"),
                                                                    "movers": {k: {"carrying_ad_sets": [a["adset_id"] for a in v["ad_sets_carrying_the_segment"]], "reading": v["reading"], "changes_by_type": v.get("changes_by_type"), "changes": v["changes"][:4]}
                                                                               for k, v in (ch.get("movers") or {}).items()},
                                                                    "other_dated_changes": (ch.get("other_dated_changes") or [])[:4]},
           "gaps": (bd.get("gaps") or [])[:6], "notes": bd.get("notes")}
    return {k: v for k, v in out.items() if v not in (None, [], {})}
