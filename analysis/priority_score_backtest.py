"""Backtest of the creative priority score (app/priority_score.py, docs/PRIORITY_SCORE.md). Read-only.

Question: does a high score on day T predict that NEW ads launched in that cut in the next 28 days do well?
  as-of days T     every 7 days from 2026-07-06 (60 days of ad data behind it) while T + 56 <= the last loaded day
  features         app.priority_signals.build_signals on data up to T only (the same code the app runs), then app.priority_score.score_cuts; pipeline counts are 0 (ticket history
                   does not go back that far), so the pipeline factor is NOT tested here
  outcome cohort   ad families whose first spend day is in (T, T + 28]
  outcomes         hit rate = cohort ads that reached $1,000 spend by T + 56 / cohort ads that reached $100 (the app's scoring hit definition, read 28+ days after launch);
                   new-ad NC ROAS index = Upstack new-customer revenue / spend of the cohort over (T, T + 56], divided by what the same products got over the same days (all ads),
                   weighted by the cohort's own spend per product
  comparisons      Spearman rank correlation per as-of day (cuts with >= MIN_JUDGED judged cohort ads), averaged over days, and pooled top third vs bottom third,
                   for the score and for naive rankings: 30-day spend, raw 30-day NC ROAS, the shrunk economics index alone, the hit-rate index alone, and launches in the last 30 days
Tags are today's tags (they describe the creative, not its results, so they do not leak outcomes). Selection bias: outcomes exist only where the team chose to launch.

Usage: ~/lonvera-tag-explorer/.venv/bin/python analysis/priority_score_backtest.py [--url-file ~/lonvera-tag-explorer-data/analytics_reader_url.txt] [--group icp] [--weights econ,hit,gap,scale,mom]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import priority_score as ps, priority_signals as sig  # noqa: E402

LAUNCH_DAYS, EVAL_DAYS, STEP_DAYS = 28, 56, 7
FIRST_T = dt.date(2026, 7, 6)
MIN_JUDGED = 3
PERMS = 500
GROUPS = (["icp"], ["cluster"], ["pain_point"], ["cluster", "icp"], ["icp", "pain_point"], ["cluster", "icp", "pain_point"], ["icp", "media"])

FAM_SQL = """
SELECT DISTINCT ON (family_key) family_key, product_slug AS product, NULLIF(btrim(vendor), '') AS vendor,
       CASE upper(btrim(ad_type)) WHEN 'VID' THEN 'Video' WHEN 'IMG' THEN 'Image' END AS media,
       COALESCE(NULLIF(btrim(cluster), ''), NULLIF(btrim(tracker_cluster), '')) AS cluster,
       COALESCE(NULLIF(btrim(icp), ''), NULLIF(btrim(tracker_icp), '')) AS icp,
       COALESCE(NULLIF(btrim(pain_point), ''), NULLIF(btrim(tracker_pain), '')) AS pain_point
FROM analytics.creative_tracker ORDER BY family_key, is_first_row DESC, tracker_row_id
"""
DAILY_SQL = """
SELECT family_key, day, sum(spend) FILTER (WHERE basis = 'meta') AS spend,
       sum(purchases) FILTER (WHERE basis = 'upstack_new_customers') AS nc_purchases, sum(revenue) FILTER (WHERE basis = 'upstack_new_customers') AS nc_revenue
FROM analytics.creative_family_daily WHERE ads_scope = 'all_ads' AND basis IN ('meta', 'upstack_new_customers') GROUP BY 1, 2
"""


def load(url_file: str):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(open(os.path.expanduser(url_file)).read().strip(), row_factory=dict_row) as conn:
        fams = [dict(r) for r in conn.execute(FAM_SQL).fetchall()]
        daily = [dict(r) for r in conn.execute(DAILY_SQL).fetchall()]
    for r in daily:
        for k in ("spend", "nc_purchases", "nc_revenue"):
            r[k] = float(r[k]) if r[k] is not None else 0.0
    return fams, daily


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 4:
        return None
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else None


def outcomes(fams, daily, group, T, data_start):
    """per cut key: cohort judged / hits / spend / NC revenue / expected NC revenue over (T, T+56]."""
    end_launch, end_eval = T + dt.timedelta(days=LAUNCH_DAYS), T + dt.timedelta(days=EVAL_DAYS)
    fmap = {f["family_key"]: f for f in fams}
    first, todate, sp, rv = {}, {}, {}, {}
    prod_s, prod_r = {}, {}
    for r in daily:
        d, k = r["day"], r["family_key"]
        if r["spend"] > 0 and (k not in first or d < first[k]):
            first[k] = d
        if T < d <= end_eval:
            p = (fmap.get(k) or {}).get("product")
            prod_s[p] = prod_s.get(p, 0) + r["spend"]; prod_r[p] = prod_r.get(p, 0) + r["nc_revenue"]
    for r in daily:
        k, d = r["family_key"], r["day"]
        if k in first and T < first[k] <= end_launch and first[k] > data_start and d <= end_eval:
            todate[k] = todate.get(k, 0) + r["spend"]
            if d > T:
                sp[k] = sp.get(k, 0) + r["spend"]; rv[k] = rv.get(k, 0) + r["nc_revenue"]
    out = {}
    for k, s in todate.items():
        f = fmap.get(k)
        if not f:
            continue
        key = tuple(sig.pc._norm(f.get(d)) for d in group)
        o = out.setdefault(key, {"launched": 0, "judged": 0, "hits": 0, "spend": 0.0, "rev": 0.0, "exp_rev": 0.0})
        o["launched"] += 1
        if s >= ps.JUDGED_SPEND:
            o["judged"] += 1
            o["hits"] += s >= ps.HIT_SPEND
        p = f.get("product")
        base = prod_r.get(p, 0) / prod_s[p] if prod_s.get(p) else 0
        o["spend"] += sp.get(k, 0); o["rev"] += rv.get(k, 0); o["exp_rev"] += sp.get(k, 0) * base
    return out


def run(fams, daily, weights=None, groups=GROUPS, verbose=True):
    data_start = min(r["day"] for r in daily)
    last = max(r["day"] for r in daily)
    cfg = {**ps.DEFAULT_CONFIG, "weights": weights} if weights else None
    Ts = []
    T = FIRST_T
    while T + dt.timedelta(days=EVAL_DAYS) <= last:
        Ts.append(T)
        T += dt.timedelta(days=STEP_DAYS)
    rankers = ("score", "spend_30d", "nc_roas_30d", "econ_index", "hit_index", "launches_30d")
    summary = {}
    for group in groups:
        per = {r: {"hit": [], "roas": []} for r in rankers}
        pooled = {r: {"top": [0, 0, 0.0, 0.0], "bot": [0, 0, 0.0, 0.0]} for r in rankers}
        n_cuts = []
        days_pts = []
        for T in Ts:
            rows = sig.build_signals(fams, daily, [], group, T, data_start=data_start)
            scored = ps.score_cuts(rows, cfg)
            oc = outcomes(fams, daily, group, T, data_start)
            pts = []
            for s in scored:
                k = tuple(s["signals"]["key"])
                o = oc.get(k)
                if not o or o["judged"] < MIN_JUDGED or s["tier"] == "Untested":
                    continue
                g = s["signals"]
                hit_out = o["hits"] / o["judged"]
                roas_idx = o["rev"] / o["exp_rev"] if o["exp_rev"] else None
                feats = {"score": s["score"], "spend_30d": g["spend_30d"], "nc_roas_30d": g["nc_roas_30d"] or 0.0, "econ_index": s["components"]["economics"],
                         "hit_index": s["components"]["hit_rate"], "launches_30d": g["launches_30d"]}
                pts.append((feats, hit_out, roas_idx, o))
            n_cuts.append(len(pts))
            days_pts.append(pts)
            for r in rankers:
                xs = [p[0][r] for p in pts]
                c = spearman(xs, [p[1] for p in pts])
                if c is not None:
                    per[r]["hit"].append(c)
                pr = [(p[0][r], p[2]) for p in pts if p[2] is not None]
                c = spearman([a for a, _ in pr], [b for _, b in pr])
                if c is not None:
                    per[r]["roas"].append(c)
                srt = sorted(pts, key=lambda p: -p[0][r])
                third = len(srt) // 3
                if third:
                    for name, part in (("top", srt[:third]), ("bot", srt[-third:])):
                        acc = pooled[r][name]
                        for p in part:
                            acc[0] += p[3]["hits"]; acc[1] += p[3]["judged"]; acc[2] += p[3]["rev"]; acc[3] += p[3]["exp_rev"]
        # permutation null for the score: shuffle the score within each as-of day, PERMS times; share of shuffles whose mean rho reaches the observed one
        import random
        rnd = random.Random(7)
        null_h, null_r = [], []
        for _ in range(PERMS):
            hs, rs = [], []
            for pts in days_pts:
                xs = [p[0]["score"] for p in pts]
                rnd.shuffle(xs)
                c = spearman(xs, [p[1] for p in pts])
                if c is not None:
                    hs.append(c)
                pr = [(x, p[2]) for x, p in zip(xs, pts) if p[2] is not None]
                c = spearman([a for a, _ in pr], [b for _, b in pr])
                if c is not None:
                    rs.append(c)
            null_h.append(sum(hs) / len(hs) if hs else 0.0)
            null_r.append(sum(rs) / len(rs) if rs else 0.0)
        res = {}
        for r in rankers:
            h, ro = per[r]["hit"], per[r]["roas"]
            t, b = pooled[r]["top"], pooled[r]["bot"]
            res[r] = {"rho_hit": sum(h) / len(h) if h else None, "rho_roas": sum(ro) / len(ro) if ro else None, "days": len(h),
                      "top_hit": t[0] / t[1] if t[1] else None, "bot_hit": b[0] / b[1] if b[1] else None, "top_n": t[1], "bot_n": b[1],
                      "top_roas_idx": t[2] / t[3] if t[3] else None, "bot_roas_idx": b[2] / b[3] if b[3] else None}
        oh, orr = res["score"]["rho_hit"] or 0.0, res["score"]["rho_roas"] or 0.0
        res["score"]["p_hit"] = sum(1 for v in null_h if v >= oh) / PERMS
        res["score"]["p_roas"] = sum(1 for v in null_r if v >= orr) / PERMS
        summary[",".join(group)] = {"as_of_days": len(Ts), "first": Ts[0].isoformat() if Ts else None, "last": Ts[-1].isoformat() if Ts else None,
                                    "cuts_per_day": (sum(n_cuts) / len(n_cuts)) if n_cuts else 0, "rankers": res}
        if verbose:
            print(f"\n== group {','.join(group)}: {len(Ts)} as-of days {Ts[0]}..{Ts[-1]}, {sum(n_cuts) / max(1, len(n_cuts)):.1f} cuts per day with >= {MIN_JUDGED} judged new ads")
            print(f"{'ranker':<14}{'rho hit':>9}{'rho roas':>10}{'top hit':>9}{'bot hit':>9}{'top n':>7}{'bot n':>7}{'top roasI':>10}{'bot roasI':>10}")
            for r in rankers:
                x = res[r]
                f = lambda v, p=3: "-" if v is None else f"{v:.{p}f}"
                print(f"{r:<14}{f(x['rho_hit']):>9}{f(x['rho_roas']):>10}{f(x['top_hit']):>9}{f(x['bot_hit']):>9}{x['top_n']:>7}{x['bot_n']:>7}{f(x['top_roas_idx']):>10}{f(x['bot_roas_idx']):>10}")
            print(f"score permutation p (one-sided, {PERMS} shuffles within day): hit {res['score']['p_hit']:.3f}, roas {res['score']['p_roas']:.3f}")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-file", default="~/lonvera-tag-explorer-data/analytics_reader_url.txt")
    ap.add_argument("--group", help="one group, comma list (default: all of GROUPS)")
    ap.add_argument("--weights", help="economics,hit_rate,supply_gap,scale,momentum")
    a = ap.parse_args(argv)
    fams, daily = load(a.url_file)
    w = None
    if a.weights:
        v = [float(x) for x in a.weights.split(",")]
        w = dict(zip(("economics", "hit_rate", "supply_gap", "scale", "momentum"), v))
    groups = [a.group.split(",")] if a.group else GROUPS
    print(f"loaded {len(fams)} families, {len(daily)} family-days ({min(r['day'] for r in daily)} .. {max(r['day'] for r in daily)})")
    run(fams, daily, w, groups)


if __name__ == "__main__":
    main()
