"""FYI only (v1.5): compares tagexp_dev (Vita Bull) with the frozen SHEET snapshot tabs rollups / overview_metrics / tag_breakout / tag_map plus headline totals.
The app is API-driven and no longer ties to the sheet, so this prints differences and exits 0 (use --strict for the old pass/fail behavior; that was only
valid when ad_metrics was built with the sheet's lifetime-cutoff rule + Windows mapping). QA lives in etl.verify_api.
Usage: python -m etl.verify_tieout --snapshot-dir D [--strict]"""
from __future__ import annotations

import argparse
import collections
import sys

from . import db, sheets_io
from .sheets_io import num

TOL = 0.011          # money tolerance (sheet totals are floats displayed to cents)

# headline numbers from CONTRACT.md rule 13
HEADLINE = {"rows": 1464, "unique_ads_sheet": 1434, "live": 141, "spend": 4761591.26, "purchases": 54960, "revenue": 4262522.08,
            "spend_7d": 17727.98, "purchases_7d": 168, "unmatched_7d": 3997.48, "unmatched_families_7d": 21}

SQL_GROUP = """
SELECT {sel},
       count(DISTINCT r.family_key) AS uniq_contract,
       count(DISTINCT CASE WHEN r.ad_family = '' THEN lower(trim(r.concept_id)) ELSE r.family_key END) AS uniq_sheet,
       count(*) FILTER (WHERE m.live) AS live, sum(m.spend_launch) AS spend, sum(m.purchases_launch) AS purchases, sum(m.revenue_launch) AS revenue,
       sum(m.spend_7d) AS spend_7d, sum(m.purchases_7d) AS purchases_7d
FROM tracker_rows r JOIN products p ON p.id = r.product_id AND p.slug = 'vita-bull'
JOIN ad_tags t ON t.tracker_row_id = r.id AND t.tag_set = 'linemap' AND t.is_current
JOIN ad_metrics m ON m.tracker_row_id = r.id
{group}
"""


class Report:
    def __init__(self):
        self.bad = 0
        self.notes = []
        self.checked = collections.Counter()

    def mism(self, where, what, sheet, mine):
        self.bad += 1
        print(f"  MISMATCH [{where}] {what}: sheet={sheet!r} db={mine!r}")

    def cmp(self, where, what, sheet, mine, tol=TOL):
        self.checked[where] += 1
        if abs(num(sheet) - float(mine or 0)) > tol:
            self.mism(where, what, sheet, float(mine or 0))


def group_db(conn, sel, group):
    cols = ["uniq_contract", "uniq_sheet", "live", "spend", "purchases", "revenue", "spend_7d", "purchases_7d"]
    out = {}
    for r in conn.execute(SQL_GROUP.format(sel=sel, group=group)).fetchall():
        nk = len(r) - len(cols)
        out[tuple(r[:nk])] = dict(zip(cols, [float(x or 0) for x in r[nk:]]))
    return out


def check_metrics(rep, where, key, sheet_vals, mine):
    """sheet_vals: [unique, live, spend, purchases, revenue, spend7d] from the sheet; mine: db group dict."""
    if mine is None:
        mine = {"uniq_sheet": 0, "uniq_contract": 0, "live": 0, "spend": 0, "purchases": 0, "revenue": 0, "spend_7d": 0, "purchases_7d": 0}
    u, lv, sp, pu, rv, s7 = sheet_vals
    tag = f"{where} {key}"
    rep.cmp(where, tag + " unique ads", u, mine["uniq_sheet"], tol=0.5)
    if abs(mine["uniq_contract"] - mine["uniq_sheet"]) > 0:
        rep.notes.append(f"{tag}: contract unique ads {int(mine['uniq_contract'])} vs sheet {int(mine['uniq_sheet'])} (blank-family row collides with a real family name)")
    rep.cmp(where, tag + " live", lv, mine["live"], tol=0.5)
    rep.cmp(where, tag + " spend", sp, mine["spend"])
    rep.cmp(where, tag + " purchases", pu, mine["purchases"], tol=0.5)
    rep.cmp(where, tag + " revenue", rv, mine["revenue"])
    rep.cmp(where, tag + " spend 7d", s7, mine["spend_7d"])


def verify_totals(conn, rep):
    r = conn.execute("""SELECT count(*), count(DISTINCT r.family_key), count(DISTINCT CASE WHEN r.ad_family='' THEN lower(trim(r.concept_id)) ELSE r.family_key END),
        count(*) FILTER (WHERE m.live), sum(m.spend_launch), sum(m.purchases_launch), sum(m.revenue_launch), sum(m.spend_7d), sum(m.purchases_7d)
        FROM tracker_rows r JOIN products p ON p.id=r.product_id AND p.slug='vita-bull' JOIN ad_metrics m ON m.tracker_row_id=r.id""").fetchone()
    u = conn.execute("""SELECT sum(spend_7d), count(*) FILTER (WHERE spend_7d > 0) FROM unmatched_meta u JOIN products p ON p.id=u.product_id AND p.slug='vita-bull'""").fetchone()
    print(f"headline: rows {r[0]}, unique ads (contract id: keys) {r[1]}, unique ads (sheet semantics) {r[2]}, live {r[3]}, spend {float(r[4]):,.2f}, "
          f"purchases {float(r[5]):,.0f}, revenue {float(r[6]):,.2f}, 7d spend {float(r[7]):,.2f} / {float(r[8]):,.0f} purchases, unmatched VB 7d {float(u[0]):,.2f} ({u[1]} families)")
    rep.cmp("headline", "rows", HEADLINE["rows"], r[0], 0)
    rep.cmp("headline", "unique ads (sheet semantics)", HEADLINE["unique_ads_sheet"], r[2], 0)
    if r[1] != r[2]:
        rep.notes.append(f"headline: contract-semantics unique ads = {r[1]} vs sheet {r[2]}: the blank-family row 'Lonvera-VitalBull-Static-7.1' "
                         f"has the same name as a real Ad Family, which the sheet's COUNTUNIQUE merges (CONTRACT's 'id:' prefix keeps them apart)")
    rep.cmp("headline", "live", HEADLINE["live"], r[3], 0)
    rep.cmp("headline", "spend", HEADLINE["spend"], r[4])
    rep.cmp("headline", "purchases", HEADLINE["purchases"], r[5], 0)
    rep.cmp("headline", "revenue", HEADLINE["revenue"], r[6])
    rep.cmp("headline", "spend 7d", HEADLINE["spend_7d"], r[7])
    rep.cmp("headline", "purchases 7d", HEADLINE["purchases_7d"], r[8], 0)
    rep.cmp("headline", "unmatched VB 7d spend", HEADLINE["unmatched_7d"], u[0])
    rep.cmp("headline", "unmatched VB families with 7d spend", HEADLINE["unmatched_families_7d"], u[1], 0)


def verify_rollups(conn, rep, rows):
    hdr = rows[2]
    starts = [i for i, h in enumerate(hdr) if h == "Market Cluster"]
    assert len(starts) == 3, starts
    specs = [("rollups/cluster", starts[0], 1, "r.family_key IS NOT NULL", "t.cluster"),
             ("rollups/icp", starts[1], 2, None, "t.cluster, t.icp"),
             ("rollups/pain", starts[2], 3, None, "t.cluster, t.icp, t.pain_point")]
    for where, c0, nl, _, sel in specs:
        mine = group_db(conn, sel, f"GROUP BY {sel}")
        seen = set()
        for r in rows[3:]:
            r = list(r) + [""] * 40
            labels = tuple(str(r[c0 + j]).strip() for j in range(nl))
            if not labels[0]:
                continue
            m0 = c0 + nl
            vals = [num(r[m0]), num(r[m0 + 1]), num(r[m0 + 2]), num(r[m0 + 3]), num(r[m0 + 4]), num(r[m0 + 7])]
            check_metrics(rep, where, labels, vals, mine.get(labels))
            seen.add(labels)
        for k, v in mine.items():
            if k not in seen and (v["spend"] > 0 or v["uniq_sheet"] > 0):
                rep.mism(where, f"{k} present in db but not on the sheet tab", None, v["uniq_sheet"])
        tot = rows[1]
        t0 = c0
        vals = [num(tot[t0 + nl]) if len(tot) > t0 + nl else 0]
        rep.cmp(where + "/total", "total spend", num(tot[c0 + nl + 2]) if len(tot) > c0 + nl + 2 else 0, sum(v["spend"] for v in mine.values()))
        rep.cmp(where + "/total", "total purchases", num(tot[c0 + nl + 3]), sum(v["purchases"] for v in mine.values()), 0.5)


def fill_down(rows, ncols):
    last = [""] * ncols
    out = []
    for r in rows:
        r = [str(c).strip() for c in r] + [""] * ncols
        cur = []
        for j in range(ncols):
            if r[j]:
                last[j] = r[j]
                for k in range(j + 1, ncols):
                    last[k] = last[k] if k != j else last[k]
            cur.append(last[j])
        out.append(cur)
    return out


def verify_overview_metrics(conn, rep, rows):
    mine = group_db(conn, "t.cluster, t.icp, t.pain_point", "GROUP BY 1, 2, 3")
    cluster = icp = None
    n = 0
    for r in rows[1:]:
        r = list(r) + [""] * 20
        if str(r[0]).strip() == "Total":
            break
        if str(r[1]).strip():
            cluster = str(r[1]).strip()
        if str(r[2]).strip():
            icp = str(r[2]).strip()
        pain = str(r[3]).strip()
        if not pain:
            continue
        n += 1
        vals = [num(r[9]), num(r[10]), num(r[11]), num(r[12]), num(r[13]), num(r[16])]
        check_metrics(rep, "overview_metrics", (cluster, icp, pain), vals, mine.get((cluster, icp, pain)))
    rep.checked["overview_metrics/pain-lines"] = n


def verify_tag_breakout(conn, rep, rows):
    mine = group_db(conn, "t.cluster, t.icp, t.pain_point", "GROUP BY 1, 2, 3")
    cluster = icp = None
    for r in rows[1:]:
        r = list(r) + [""] * 14
        if str(r[0]).strip() == "Total":
            break
        if str(r[0]).strip():
            cluster = str(r[0]).strip()
        if str(r[1]).strip():
            icp = str(r[1]).strip()
        pain = str(r[2]).strip()
        if not pain:
            continue
        vals = [num(r[4]), num(r[5]), num(r[6]), num(r[7]), num(r[8]), num(r[11])]
        check_metrics(rep, "tag_breakout", (cluster, icp, pain), vals, mine.get((cluster, icp, pain)))
        # trigger text, ROAS and CPA
        m = mine.get((cluster, icp, pain))
        if m and m["spend"] > 0 and r[9] != "":
            rep.cmp("tag_breakout", f"{pain} ROAS", r[9], m["revenue"] / m["spend"], 0.005)
        if m and m["purchases"] > 0 and r[10] != "":
            rep.cmp("tag_breakout", f"{pain} CPA", r[10], m["spend"] / m["purchases"], 0.01)
        if m and m["purchases_7d"] > 0 and r[12] != "":
            rep.cmp("tag_breakout", f"{pain} CPA 7d", r[12], m["spend_7d"] / m["purchases_7d"], 0.01)


def verify_tag_map(conn, rep, rows):
    """Per-row: match tag_map rows to tracker rows by (concept_id, ad_family) + ordinal (ordered by the sheet's Tracker Row), then compare."""
    by_key = collections.defaultdict(list)
    for rid, cid, af in conn.execute("""SELECT r.id, r.concept_id, r.ad_family FROM tracker_rows r JOIN products p ON p.id=r.product_id AND p.slug='vita-bull'
                                       ORDER BY r.sheet_row, r.id""").fetchall():
        by_key[(cid, af)].append(rid)
    data = [list(r) + [""] * 32 for r in rows[2:] if r and str(r[0]).strip() != ""]
    data.sort(key=lambda r: num(r[2]))
    cnt = collections.Counter()
    metrics = {r[0]: r for r in conn.execute("""SELECT tracker_row_id, spend_launch, purchases_launch, revenue_launch, live, spend_7d, purchases_7d FROM ad_metrics""").fetchall()}
    tags = {r[0]: r for r in conn.execute("""SELECT tracker_row_id, cluster, icp, pain_point, emotional_trigger, confidence, method, seen, why, runner_up
                                            FROM ad_tags WHERE tag_set='linemap' AND is_current""").fetchall()}
    n = 0
    for r in data:
        k = (str(r[0]), str(r[1]))
        i = cnt[k]
        cnt[k] += 1
        if i >= len(by_key.get(k, [])):
            rep.mism("tag_map", f"{k} ordinal {i}", "row on tab", "no tracker row")
            continue
        rid = by_key[k][i]
        m = metrics[rid]
        w = f"tag_map {k[0]}#{i + 1}"
        rep.cmp("tag_map", w + " spend", r[15], m[1])
        rep.cmp("tag_map", w + " purchases", r[18], m[2], 0.5)
        rep.cmp("tag_map", w + " revenue", r[19], m[3])
        rep.cmp("tag_map", w + " live", r[20], 1 if m[4] else 0, 0)
        rep.cmp("tag_map", w + " spend 7d", r[21], m[5])
        rep.cmp("tag_map", w + " purchases 7d", r[22], m[6], 0.5)
        t = tags.get(rid)
        if not t:
            rep.mism("tag_map", w, "tagged", "no current linemap tag")
        else:
            for name, sv, dv in (("cluster", r[5], t[1]), ("icp", r[6], t[2]), ("pain", r[7], t[3]), ("trigger", r[8], t[4]),
                                 ("confidence", r[9], t[5]), ("method", r[10], t[6]), ("seen", r[11], t[7]), ("why", r[12], t[8])):
                rep.checked["tag_map/tag-text"] += 1
                if str(sv).strip() != str(dv or "").strip():
                    rep.mism("tag_map", f"{w} {name}", sv, dv)
        n += 1
    rep.checked["tag_map/rows"] = n
    if n != 1464:
        rep.mism("tag_map", "row count", 1464, n)


def run_sections(conn, snapshot_dir) -> Report:
    rep = Report()
    rd = lambda t: sheets_io.read_tab(t, snapshot_dir)
    print("== headline totals =="); verify_totals(conn, rep)
    print("== rollups (3 tables) =="); verify_rollups(conn, rep, rd("rollups"))
    print("== overview_metrics =="); verify_overview_metrics(conn, rep, rd("overview_metrics"))
    print("== tag_breakout =="); verify_tag_breakout(conn, rep, rd("tag_breakout"))
    print("== tag_map (per row) =="); verify_tag_map(conn, rep, rd("tag_map"))
    print("\nchecked:", dict(rep.checked))
    for n in sorted(set(rep.notes))[:12]:
        print("  NOTE", n)
    return rep


def fyi(conn, snapshot_dir) -> int:
    """Print the sheet comparison; never fails. Returns the number of differing cells."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rep = run_sections(conn, snapshot_dir)
    lines = buf.getvalue().splitlines()
    mism = [l for l in lines if "MISMATCH" in l]
    print(f"sheet comparison (FYI): {rep.bad} differing cells out of {sum(rep.checked.values())} compared")
    for l in mism[:8]:
        print(l)
    if len(mism) > 8:
        print(f"  ... {len(mism) - 8} more")
    for l in lines:
        if l.startswith("headline:"):
            print(" ", l)
    return rep.bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    with db.JobRun("verify_tieout") as run, db.connect() as conn:
        rep = run_sections(conn, a.snapshot_dir)
        run.stats = {"mismatches": rep.bad, "checked": dict(rep.checked), "notes": sorted(set(rep.notes))[:20], "strict": a.strict}
        print(f"\nRESULT (FYI): {rep.bad} differences vs the sheet snapshot")
    sys.exit(1 if (rep.bad and a.strict) else 0)


if __name__ == "__main__":
    main()
