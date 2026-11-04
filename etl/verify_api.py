"""QA against the Meta Graph API (v1.5): the database must agree with FRESH, independent Graph pulls — not with the Google Sheet.
Invariants:
  1 per account and day, SUM(ad_daily.spend) = Graph account-level daily spend (<= $1 or 0.1% for days older than 3; newer days are reported only)
  2 per ad, lifetime spend >= SUM(ad_daily.spend) (the gap is spend before the daily range)
  3 account lifetime spend (Graph) = SUM of per-ad lifetime within 0.5%
  4 trailing-7-day Graph ad-level pull vs the DB, whole range and a random ad sample
  5 stage totals by day vs a Graph campaign-level pull (testing/scaling by campaign name)
  6 structure: Vita Bull tracker rows / unique ads / current line-map tags / taxonomy lines
  7 tree = rollup = families = ads sums at each level (SQL consistency, also vs an independent lifetime sum)
Also prints Vita Bull traffic (impressions, link clicks, CTR, CPC) for last 7d / 30d and since launch.
Optional `--vs-sheet --snapshot-dir D` prints the old sheet comparison as an FYI (never fails).
Usage: python -m etl.verify_api [--days 30] [--sample 40] [--seed 1] [--skip-api] [--vs-sheet --snapshot-dir D]"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import random
import sys

from . import db, meta_api, meta_pull
from .resolver import classify_stage


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail, info=False):
        status = "INFO" if info else ("PASS" if ok else "FAIL")
        self.rows.append((status, name, detail))
        print(f"[{status}] {name}: {detail}", flush=True)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]


def tol_ok(diff, base, abs_tol=1.0, pct=0.001):
    return abs(diff) <= max(abs_tol, abs(base) * pct)


def check_account_days(conn, tok, ck: Checks, since, until):
    cutoff_old = until - dt.timedelta(days=3)
    db_days = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for acct, day, sp, im, cl in conn.execute("""SELECT coalesce(a.account, '?'), d.day, sum(d.spend), sum(d.impressions), sum(d.link_clicks)
                                                 FROM ad_daily d LEFT JOIN meta_ads a ON a.ad_id = d.ad_id WHERE d.day BETWEEN %s AND %s GROUP BY 1, 2""", (since, until)).fetchall():
        db_days[(acct, day)] = [float(sp), float(im or 0), float(cl or 0)]
    bad, old_checked, new_diffs, worst = [], 0, [], (0.0, None)
    for acct_id, an in meta_api.ACCOUNTS:
        g = meta_api.account_daily(tok, acct_id, since, until)
        for day in sorted(set(g) | {d for (a, d) in db_days if a == an}):
            gs = g.get(day, {"spend": 0.0, "impressions": 0.0, "clicks": 0.0})
            mine = db_days.get((an, day), [0.0, 0.0, 0.0])
            diff = mine[0] - gs["spend"]
            if abs(diff) > abs(worst[0]):
                worst = (diff, (an, day))
            if day <= cutoff_old:
                old_checked += 1
                if not tol_ok(diff, gs["spend"]):
                    bad.append((an, day.isoformat(), round(gs["spend"], 2), round(mine[0], 2)))
            elif abs(diff) > 0.01:
                new_diffs.append((an, day.isoformat(), round(diff, 2)))
    unknown = sum(v[0] for (a, d), v in db_days.items() if a == "?")
    ck.add("1 account-day spend vs Graph", not bad and unknown < 1,
           f"{old_checked} account-days older than 3d checked, {len(bad)} outside tolerance {bad[:3]}; worst diff {worst[0]:+.2f} at {worst[1]}; "
           f"spend not attributable to an account ${unknown:,.2f}; last-3-day diffs (Meta restatement window) {new_diffs}")


def check_lifetime(conn, tok, ck: Checks, until):
    viol = conn.execute("""SELECT count(*), coalesce(max(d.s - l.spend), 0) FROM (SELECT ad_id, sum(spend) s FROM ad_daily WHERE ad_id NOT LIKE 'fam:%' GROUP BY 1) d
                           JOIN meta_ad_lifetime l ON l.ad_id = d.ad_id AND l.source = 'meta_api' WHERE d.s > l.spend + 0.5""").fetchone()
    no_life = conn.execute("""SELECT count(*), coalesce(sum(s), 0) FROM (SELECT ad_id, sum(spend) s FROM ad_daily WHERE ad_id NOT LIKE 'fam:%' GROUP BY 1) d
                              WHERE NOT EXISTS (SELECT 1 FROM meta_ad_lifetime l WHERE l.ad_id = d.ad_id AND l.source = 'meta_api')""").fetchone()
    ck.add("2 per-ad lifetime >= daily sum", viol[0] == 0 and float(no_life[1]) < 1,
           f"{viol[0]} ads with daily spend above lifetime (max excess ${float(viol[1]):.2f}); ads with daily rows but no lifetime row: {no_life[0]} (${float(no_life[1]):,.2f})")
    db_tot = {a: float(s or 0) for a, s in conn.execute("""SELECT coalesce(a.account, '?'), sum(l.spend) FROM meta_ad_lifetime l LEFT JOIN meta_ads a ON a.ad_id = l.ad_id
                                                         WHERE l.source = 'meta_api' GROUP BY 1""").fetchall()}
    parts, ok = [], True
    for acct_id, an in meta_api.ACCOUNTS:
        g = meta_api.account_lifetime(tok, acct_id, meta_api.earliest_allowed(), until)
        mine = db_tot.get(an, 0.0)
        off = abs(mine - g["spend"]) / g["spend"] if g["spend"] else 0.0
        ok &= off <= 0.005
        parts.append(f"{an}: Graph ${g['spend']:,.2f} vs sum of ads ${mine:,.2f} ({off:.3%})")
    ck.add("3 account lifetime vs sum of per-ad lifetime (<=0.5%)", ok, "; ".join(parts))


def check_trailing7(conn, tok, ck: Checks, until, sample, seed):
    since = until - dt.timedelta(days=6)
    rows = []
    for acct_id, an in meta_api.ACCOUNTS:
        rows += meta_api.pull_chunk(tok, acct_id, an, since, until)
    agg = meta_pull.aggregate(rows)
    mine = {(a, d): [float(x or 0) for x in (sp, pu, rv, im, cl)] for a, d, sp, pu, rv, im, cl in conn.execute(
        "SELECT ad_id, day, spend, purchases, conv_value, impressions, link_clicks FROM ad_daily WHERE day BETWEEN %s AND %s", (since, until)).fetchall()}
    old = until - dt.timedelta(days=3)
    keys = set(agg) | set(mine)
    bad_old, bad_new = [], 0
    for k in keys:
        g = agg.get(k)
        gv = [g["spend"], g["purchases"], g["value"], g["impressions"], g["clicks"]] if g else [0.0] * 5
        mv = mine.get(k, [0.0] * 5)
        off = abs(gv[0] - mv[0]) > 0.05 or abs(gv[1] - mv[1]) > 0.5 or abs(gv[2] - mv[2]) > 1.0 or abs(gv[3] - mv[3]) > max(2, 0.005 * gv[3]) or abs(gv[4] - mv[4]) > 1
        if off:
            if k[1] <= old:
                bad_old.append((k[0], k[1].isoformat(), [round(x, 2) for x in gv[:2]], [round(x, 2) for x in mv[:2]]))
            else:
                bad_new += 1
    ids = sorted({k[0] for k in agg})
    random.Random(seed).shuffle(ids)
    samp = set(ids[:sample])
    bad_s = [b for b in bad_old if b[0] in samp]
    tot_g, tot_m = sum(v["spend"] for v in agg.values()), sum(v[0] for k, v in mine.items())
    ck.add("4 trailing-7d Graph pull vs DB", not bad_old,
           f"{len(agg)} ad-days from Graph, {len(mine)} in DB; spend Graph ${tot_g:,.2f} vs DB ${tot_m:,.2f}; {len(bad_old)} ad-days older than 3d differ {bad_old[:3]}; "
           f"random sample of {len(samp)} ads: {len(bad_s)} differ; last-3-day ad-days that moved (restatement) {bad_new}")


def check_stage(conn, tok, ck: Checks, until, days):
    since = until - dt.timedelta(days=days - 1)
    g = collections.defaultdict(float)
    for acct_id, an in meta_api.ACCOUNTS:
        for r in meta_api.campaign_daily(tok, acct_id, an, since, until):
            g[(r["day"], classify_stage(r["name"]))] += r["spend"]
    mine = {(d, st): float(s) for d, st, s in conn.execute(
        """SELECT d.day, CASE WHEN lower(c.name) LIKE '%%test%%' THEN 'testing' ELSE 'scaling' END, sum(d.spend) FROM ad_daily d
           JOIN meta_campaigns c ON c.campaign_id = d.campaign_id WHERE d.day BETWEEN %s AND %s GROUP BY 1, 2""", (since, until)).fetchall()}
    old = until - dt.timedelta(days=3)
    bad = []
    for k in set(g) | set(mine):
        if k[0] <= old and not tol_ok(mine.get(k, 0) - g.get(k, 0), g.get(k, 0), abs_tol=5.0, pct=0.01):
            bad.append((k[0].isoformat(), k[1], round(g.get(k, 0), 2), round(mine.get(k, 0), 2)))
    ck.add("5 stage totals vs Graph campaign-level", not bad,
           f"{len(set(k[0] for k in g))} days, testing ${sum(v for (d, s), v in g.items() if s == 'testing'):,.0f} / scaling ${sum(v for (d, s), v in g.items() if s == 'scaling'):,.0f} (Graph) "
           f"vs ${sum(v for (d, s), v in mine.items() if s == 'testing'):,.0f} / ${sum(v for (d, s), v in mine.items() if s == 'scaling'):,.0f} (DB); {len(bad)} day-stages off by more than 1% / $5: {bad[:3]}")


def check_structure(conn, ck: Checks):
    rows = conn.execute("SELECT count(*) FROM tracker_rows t JOIN products p ON p.id=t.product_id WHERE p.slug='vita-bull'").fetchone()[0]
    uniq = conn.execute("""SELECT count(DISTINCT CASE WHEN ad_family = '' THEN lower(trim(concept_id)) ELSE family_key END), count(DISTINCT family_key)
                           FROM tracker_rows t JOIN products p ON p.id=t.product_id WHERE p.slug='vita-bull'""").fetchone()
    tags = conn.execute("""SELECT count(*), count(DISTINCT tracker_row_id), count(*) FILTER (WHERE line_id IS NULL), count(DISTINCT line_id)
                           FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id JOIN products p ON p.id=t.product_id
                           WHERE p.slug='vita-bull' AND a.tag_set='linemap' AND a.is_current""").fetchone()
    lines = conn.execute("SELECT count(*) FROM taxonomy_lines t JOIN products p ON p.id=t.product_id WHERE p.slug='vita-bull'").fetchone()[0]
    ck.add("6 Vita Bull structure", rows == 1464 and uniq[0] == 1434 and tags[0] == 1464 and tags[1] == 1464 and tags[2] == 0 and lines == 37,
           f"tracker rows {rows} (expect 1,464), unique ads {uniq[0]} by name (expect 1,434; {uniq[1]} with the contract's id: keys), current line-map tags {tags[0]} on {tags[1]} rows, "
           f"{tags[2]} without a line, {tags[3]} distinct lines used, taxonomy lines {lines} (expect 37)")


def check_consistency(conn, ck: Checks):
    prod = {r[0]: [float(x or 0) for x in r[1:]] for r in conn.execute(
        "SELECT p.slug, sum(spend_launch), sum(purchases_launch), sum(revenue_launch), sum(impressions_launch), sum(clicks_launch), sum(spend_7d) "
        "FROM ad_metrics m JOIN products p ON p.id=m.product_id GROUP BY 1").fetchall()}
    # independent: family-level lifetime from meta_ad_lifetime via map, summed over each product's distinct families
    ind = {r[0]: [float(x or 0) for x in r[1:]] for r in conn.execute("""
        WITH life AS (SELECT m.tracker_family_key fk, sum(l.spend) sp, sum(l.purchases) pu, sum(l.conv_value) rv, sum(l.impressions) im, sum(l.link_clicks) cl
                      FROM meta_ad_lifetime l JOIN meta_ads a ON a.ad_id = l.ad_id JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
                      WHERE l.source='meta_api' AND m.tracker_family_key IS NOT NULL GROUP BY 1),
        fams AS (SELECT DISTINCT p.slug, m.family_key FROM ad_metrics m JOIN products p ON p.id=m.product_id WHERE m.is_first_row)
        SELECT f.slug, sum(round(l.sp, 2)), sum(round(l.pu, 0)), sum(round(l.rv, 2)), sum(l.im), sum(l.cl) FROM fams f JOIN life l ON l.fk = f.family_key GROUP BY 1""").fetchall()}
    bad = []
    for slug, v in prod.items():
        i = ind.get(slug, [0] * 5)
        if abs(v[0] - i[0]) > 0.5 or abs(v[1] - i[1]) > 0.5 or abs(v[2] - i[2]) > 0.5 or abs(v[3] - i[3]) > 1 or abs(v[4] - i[4]) > 1:
            bad.append((slug, [round(x, 2) for x in v[:5]], [round(x, 2) for x in i[:5]]))
    ck.add("7a ad_metrics = independent lifetime sum per product", not bad, f"{len(prod)} products; mismatches {bad[:3]}")
    tag_bad = []
    for ts in ("linemap", "tracker"):
        for slug, sp, tagged_sp, n_rows, n_tagged in conn.execute("""
            SELECT p.slug, sum(m.spend_launch), sum(m.spend_launch) FILTER (WHERE a.id IS NOT NULL), count(*), count(a.id)
            FROM ad_metrics m JOIN products p ON p.id=m.product_id
            LEFT JOIN ad_tags a ON a.tracker_row_id = m.tracker_row_id AND a.tag_set=%s AND a.is_current
            WHERE p.tag_set = %s GROUP BY 1""", (ts, ts)).fetchall():
            if abs(float(sp or 0) - float(tagged_sp or 0)) > 0.01 or n_rows != n_tagged:
                tag_bad.append((slug, ts, float(sp or 0), float(tagged_sp or 0), n_rows, n_tagged))
    fam_bad = conn.execute("""SELECT count(*) FROM (SELECT p.slug, sum(m.spend_7d) a FROM ad_metrics m JOIN products p ON p.id=m.product_id GROUP BY 1) x""").fetchone()[0]
    w7 = {r[0]: float(r[1] or 0) for r in conn.execute("""SELECT p.slug, sum(f.spend) FROM (SELECT DISTINCT product_id, family_key FROM tracker_rows) t JOIN products p ON p.id=t.product_id
        JOIN family_daily f ON f.family_key=t.family_key AND f.day >= (SELECT value::date FROM app_meta WHERE key='data_through') - 6 GROUP BY 1""").fetchall()}
    d7_bad = [(s, round(prod[s][5], 2), round(w7.get(s, 0), 2)) for s in prod if abs(prod[s][5] - w7.get(s, 0)) > 0.05]
    ck.add("7b tag groups cover every row/spend of their product; 7d sums = family_daily", not tag_bad and not d7_bad,
           f"tag coverage problems {tag_bad[:3]}; 7d mismatches {d7_bad[:3]} ({fam_bad} products)")


def check_launch_totals(conn, ck: Checks):
    r = conn.execute("""SELECT (SELECT coalesce(sum(spend_launch),0) FROM ad_metrics), (SELECT coalesce(sum(spend_launch),0) FROM unmatched_meta),
                               (SELECT coalesce(sum(spend),0) FROM meta_ad_lifetime WHERE source='meta_api')""").fetchone()
    tracked, unmatched, life = (float(x) for x in r)
    ck.add("7c launch totals: tracker families + unmatched = API lifetime", abs(tracked + unmatched - life) < 1.0,
           f"tracker families ${tracked:,.2f} + unmatched ${unmatched:,.2f} = ${tracked + unmatched:,.2f} vs lifetime ${life:,.2f}")
    twice = conn.execute("SELECT count(*) FROM (SELECT family_key FROM ad_metrics WHERE is_first_row GROUP BY 1 HAVING count(*) > 1) x").fetchone()[0]
    deleted = conn.execute("SELECT count(*) FROM ad_metrics m JOIN tracker_rows t ON t.id = m.tracker_row_id WHERE t.deleted_at IS NOT NULL").fetchone()[0]
    ck.add("7d one first row per family across all products; deleted tracker rows excluded", twice == 0 and deleted == 0, f"families with 2+ first rows {twice}; deleted rows with metrics {deleted}")
    ind = float(conn.execute("""SELECT coalesce(sum(round(x.sp, 2)), 0) FROM (SELECT m.tracker_family_key fk, sum(l.spend) sp FROM meta_ad_lifetime l
                                JOIN meta_ads a ON a.ad_id = l.ad_id AND a.status = 'ACTIVE' JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
                                WHERE l.source = 'meta_api' AND m.tracker_family_key IN (SELECT family_key FROM tracker_rows WHERE deleted_at IS NULL) GROUP BY 1) x""").fetchone()[0])
    act = float(conn.execute("SELECT coalesce(sum(spend_launch_active), 0) FROM ad_metrics").fetchone()[0])
    fda = float(conn.execute("SELECT coalesce(sum(spend), 0) FROM family_daily_active").fetchone()[0])
    daily_act = float(conn.execute("""SELECT coalesce(sum(d.spend), 0) FROM ad_daily d JOIN meta_ads a ON a.ad_id = d.ad_id AND a.status = 'ACTIVE'
                                      JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key WHERE m.tracker_family_key IS NOT NULL""").fetchone()[0])
    ck.add("7e active-only tables = ACTIVE ads only", abs(act - ind) < 1.0 and abs(fda - daily_act) < 1.0,
           f"ad_metrics.spend_launch_active ${act:,.2f} vs independent ${ind:,.2f}; family_daily_active ${fda:,.2f} vs ACTIVE ad_daily ${daily_act:,.2f}")


def check_status(conn, tok, ck: Checks, sample, seed):
    thr = conn.execute("SELECT value::date FROM app_meta WHERE key='data_through'").fetchone()[0]
    ids = [r[0] for r in conn.execute("SELECT DISTINCT ad_id FROM ad_daily WHERE day >= %s AND ad_id NOT LIKE 'fam:%%'", (thr - dt.timedelta(days=59),)).fetchall()]
    random.Random(seed).shuffle(ids)
    ids = ids[:max(sample, 1)]
    got = meta_api.fetch_ad_meta(tok, ids, strict=True)
    mine = dict(conn.execute("SELECT ad_id, status FROM meta_ads WHERE ad_id = ANY(%s)", (ids,)).fetchall())
    diff = [(i, mine.get(i), v["status"]) for i, v in got.items() if "error" not in v and v["status"] != mine.get(i)]
    ck.add("8 ad status vs Graph (random delivering ads)", len(diff) <= max(1, len(ids) * 0.03), f"{len(ids)} sampled, {len(diff)} differ {diff[:3]}")


def vb_traffic(conn, ck: Checks):
    thr = conn.execute("SELECT value::date FROM app_meta WHERE key='data_through'").fetchone()[0]
    q = """SELECT sum(f.spend), sum(f.purchases), sum(f.revenue), sum(f.link_clicks), sum(f.impressions), sum(f.clicks_tr), sum(f.spend_tr)
           FROM (SELECT DISTINCT t.family_key FROM tracker_rows t JOIN products p ON p.id=t.product_id AND p.slug='vita-bull' AND t.family_key NOT LIKE 'id:%%') x
           JOIN family_daily f ON f.family_key = x.family_key WHERE f.day >= %s"""
    out = []
    for label, n in (("last 7d", 7), ("last 30d", 30)):
        r = [float(x or 0) for x in conn.execute(q, (thr - dt.timedelta(days=n - 1),)).fetchone()]
        ctr = f"{r[5] / r[4] * 100:.2f}%" if r[4] else "n/a"
        cpc = f"${r[6] / r[5]:.2f}" if r[5] else "n/a"
        out.append(f"{label}: spend ${r[0]:,.0f}, impressions {r[4]:,.0f}, link clicks {r[3]:,.0f}, CTR {ctr}, CPC {cpc}, purchases {r[1]:,.0f}")
    r = [float(x or 0) for x in conn.execute("""SELECT sum(spend_launch), sum(impressions_launch), sum(clicks_launch), sum(purchases_launch), sum(revenue_launch)
                                               FROM ad_metrics m JOIN products p ON p.id=m.product_id AND p.slug='vita-bull'""").fetchone()]
    out.append(f"since launch (API lifetime): spend ${r[0]:,.2f}, impressions {r[1]:,.0f}, link clicks {r[2]:,.0f}, "
               f"CTR {r[2] / r[1] * 100 if r[1] else 0:.2f}%, CPC ${r[0] / r[2] if r[2] else 0:.2f}, purchases {r[3]:,.0f}, revenue ${r[4]:,.2f} "
               f"(FYI the sheet showed $4,761,591 / 54,960 / $4,262,522 using its 2026-09-17 lifetime snapshot)")
    ck.add("Vita Bull traffic and since-launch totals", True, "\n      " + "\n      ".join(out), info=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="window for the campaign-level stage check")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--skip-api", action="store_true", help="only the SQL-side checks (no Graph calls)")
    ap.add_argument("--vs-sheet", action="store_true", help="print the old sheet comparison as an FYI (never fails)")
    ap.add_argument("--snapshot-dir")
    a = ap.parse_args()
    ck = Checks()
    with db.JobRun("verify_api") as run, db.connect() as conn:
        thr = dt.date.fromisoformat(db.get_meta(conn, "data_through"))
        d0 = dt.date.fromisoformat(db.get_meta(conn, "daily_start"))
        db.log(f"data_through {thr}, daily_start {d0}")
        if not a.skip_api:
            tok = meta_api.get_token()
            check_account_days(conn, tok, ck, d0, thr)
            check_lifetime(conn, tok, ck, thr)
            check_trailing7(conn, tok, ck, thr, a.sample, a.seed)
            check_status(conn, tok, ck, 60, a.seed)
            check_stage(conn, tok, ck, thr, a.days)
        check_structure(conn, ck)
        check_consistency(conn, ck)
        check_launch_totals(conn, ck)
        vb_traffic(conn, ck)
        if a.vs_sheet and a.snapshot_dir:
            from . import verify_tieout
            print("\n--- FYI: comparison with the sheet snapshot (informational only; the app is API-driven) ---")
            verify_tieout.fyi(conn, a.snapshot_dir)
        run.stats = {"checks": [{"status": s, "name": n, "detail": d[:400]} for s, n, d in ck.rows]}
    print(f"\nRESULT: {len(ck.failed)} failed of {sum(1 for r in ck.rows if r[0] != 'INFO')} checks")
    sys.exit(1 if ck.failed else 0)


if __name__ == "__main__":
    main()
