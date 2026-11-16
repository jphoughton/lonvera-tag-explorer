"""Madre review (/madre-review): a weekly deck for one creative vendor (default Madre), Meta-reported, built from the analytics views.

Basis: analytics.meta_ad_daily (spend, impressions, link clicks, add to carts, initiated checkouts, Meta purchases and purchase value). Every ratio is a ratio of sums.
Vendor of an ad: meta_ads.tracker_family_key -> creative_tracker.family_key (first tracker row of the family). No tracker match or a blank vendor = Untagged, never folded
into the vendor. Stage: testing when the campaign name contains 'test' (any case), else scaling. Product: meta_ad_daily.product_slug.

The server decides the slide set and order and writes the copy from the numbers; the browser (js/pages/madre_review.js) only draws them.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from . import queries as q

PRODUCTS = (("vita-bull", "Vita Bull"), ("beef-liver", "Beef Liver"), ("boneflex", "Boneflex"))
PRODUCT_NAME = dict(PRODUCTS)
DEFAULT_VENDOR = "Madre"

# thresholds (Josh's calls); all in one place
WIN_MIN_SPEND, WIN_MIN_PURCHASES, WIN_CPA_UNDER = 300.0, 3, 0.10            # winner: $300+ spend, 3+ purchases, CPA 10%+ under the product average
LOSE_MIN_SPEND, LOSE_CPA_OVER = 300.0, 0.40                                  # loser: $300+ spend and CPA 40%+ over the product average, or no purchases
SCALE_MIN_SPEND, SCALE_MAX_OPEN = 300.0, 2                                   # pocket flag Scale: $300+ spend, CPA 10%+ under the product, 2 or fewer open
PILE_MIN_READY, PILE_MAX_SPEND = 8, 600.0                                    # Too much in pipeline: 8+ ready to launch, under $600 spend
PULL_MIN_SPEND, PULL_CPA_OVER, PULL_MIN_OPEN = 300.0, 0.30, 5                # Pull back: $300+ spend, CPA 30%+ over the product, 5+ open
POCKET_MIN_SPEND, POCKET_MIN_OPEN = 100.0, 3                                 # pockets listed: $100+ spend or 3+ open concepts
POCKET_ROWS = 8
SUGGEST_MIN_SPEND, SUGGEST_PER_PRODUCT = 150.0, 4
WINNER_TILES, LOSER_TILES = 12, 9
LAUNCHED_DAYS = 14
OPEN_STATUSES = range(2, 8)                                                  # tracker status 2 to 7 = open (7 = Ready to launch)
SUMS = ("spend", "impressions", "link_clicks", "add_to_carts", "initiated_checkouts", "purchases", "purchase_value")
TABLE_KEYS = ("spend", "purchases", "cpa", "roas", "ctr", "cpc", "cvr", "cpm", "aov", "atc_rate", "atc_to_purchase")


# ---------------------------------------------------------------------------------------------------------------- helpers

def _f(v) -> float:
    return float(v) if v is not None else 0.0


def metrics(s: dict) -> dict:
    """Sums -> the shown metrics (ratios of sums; None when the denominator is zero)."""
    d = lambda a, b: (a / b) if b else None
    sp, im, lc, atc, pu, pv = (_f(s.get(k)) for k in ("spend", "impressions", "link_clicks", "add_to_carts", "purchases", "purchase_value"))
    out = {k: _f(s.get(k)) for k in SUMS}
    out.update(cpa=d(sp, pu), roas=d(pv, sp), ctr=d(lc, im), cpc=d(sp, lc), cpm=d(sp * 1000, im), cvr=d(pu, lc), aov=d(pv, pu),
               atc_rate=d(atc, lc), atc_to_purchase=d(pu, atc))
    return out


def _zero() -> dict:
    return {k: 0.0 for k in SUMS}


def _add(a: dict, b: dict) -> dict:
    for k in SUMS:
        a[k] += b.get(k, 0.0)
    return a


def _sub(a: dict, b: dict) -> dict:
    return {k: a[k] - b.get(k, 0.0) for k in SUMS}


def monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def span_label(a: dt.date, b: dt.date) -> str:
    if a.month == b.month:
        return f"{a.strftime('%b')} {a.day}–{b.day}"
    return f"{a.strftime('%b')} {a.day} – {b.strftime('%b')} {b.day}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def concept_key(cid: str) -> str:
    """Concepts collapse '-hookN' variants and ' - Copy N' copies."""
    s = (cid or "").strip().lower()
    s = re.sub(r"\s*-\s*copy\s*\d+\s*$", "", s)
    s = re.sub(r"-hook\d+", "", s)
    return s.strip()


def status_num(st: str | None) -> int | None:
    m = re.match(r"\s*(\d+)", st or "")
    return int(m.group(1)) if m else None


def money(v, d: int = 0) -> str:
    return "n/a" if v is None else f"${v:,.{d}f}"


def short_name(fk: str) -> str:
    return re.sub(r"^lonvera-(vitalbull|vitabull|beefliver|boneflex)-(video-|static-)?", lambda m: {"vitalbull": "VB ", "vitabull": "VB ", "beefliver": "BL ", "boneflex": "BF "}[m.group(1)], fk or "")


# ---------------------------------------------------------------------------------------------------------------- data

def last_loaded_day(conn) -> dt.date | None:
    r = q.row_of(conn, "SELECT last_day FROM analytics.data_coverage WHERE source = 'meta_ad_daily'")
    if r and r.get("last_day"):
        return r["last_day"]
    r = q.row_of(conn, "SELECT max(day) AS d FROM analytics.meta_ad_daily")
    return r["d"] if r else None


def latest_week(last_day: dt.date) -> dt.date:
    """Monday of the latest complete Monday-Sunday week (its Sunday is on or before the last loaded day)."""
    m = monday_of(last_day)
    return m if last_day >= m + dt.timedelta(days=6) else m - dt.timedelta(days=7)


def week_list(conn, newest: dt.date, n: int = 26) -> list[dict]:
    first = q.row_of(conn, "SELECT min(day) AS d FROM analytics.meta_ad_daily")
    lo = (monday_of(first["d"]) + dt.timedelta(days=7)) if first and first["d"] else newest      # the first partial week is skipped
    out, m = [], newest
    while m >= lo and len(out) < n:
        out.append({"start": m.isoformat(), "label": span_label(m, m + dt.timedelta(days=6))})
        m -= dt.timedelta(days=7)
    return out


DAILY_SQL = """
WITH tv AS (SELECT DISTINCT ON (family_key) family_key, NULLIF(btrim(vendor), '') AS vendor
            FROM analytics.creative_tracker WHERE is_first_row ORDER BY family_key, product_slug)
SELECT (d.day >= %(cs)s) AS is_cur, d.product_slug AS product, COALESCE(a.tracker_family_key, a.family_key) AS fk,
       (a.tracker_family_key IS NOT NULL) AS matched, (position('test' in lower(COALESCE(d.campaign_name, ''))) > 0) AS testing, tv.vendor,
       sum(d.spend) AS spend, sum(d.impressions) AS impressions, sum(d.link_clicks) AS link_clicks, sum(d.add_to_carts) AS add_to_carts,
       sum(d.initiated_checkouts) AS initiated_checkouts, sum(d.meta_purchases) AS purchases, sum(d.meta_revenue) AS purchase_value
FROM analytics.meta_ad_daily d
JOIN analytics.meta_ads a ON a.ad_id = d.ad_id
LEFT JOIN tv ON tv.family_key = a.tracker_family_key
WHERE d.day BETWEEN %(ps)s AND %(ce)s
GROUP BY 1, 2, 3, 4, 5, 6
"""

LIFE_SQL = """
SELECT COALESCE(a.tracker_family_key, a.family_key) AS fk, sum(d.spend) AS spend, sum(d.meta_purchases) AS purchases, min(d.day) FILTER (WHERE d.spend > 0) AS first_day
FROM analytics.meta_ad_daily d JOIN analytics.meta_ads a ON a.ad_id = d.ad_id
WHERE COALESCE(a.tracker_family_key, a.family_key) = ANY(%(fks)s) AND d.day <= %(ce)s
GROUP BY 1
"""

TRACKER_SQL = """
SELECT product_slug AS product, concept_id, family_key, status, ad_type, vendor, launch_date, asset_url, hook,
       COALESCE(cluster, tracker_cluster) AS cluster, COALESCE(icp, tracker_icp) AS icp, COALESCE(pain_point, tracker_pain) AS pain_point,
       emotional_trigger, tracker_emotion, is_first_row, spend_launch, purchases_launch
FROM analytics.creative_tracker WHERE product_slug = ANY(%(prods)s)
"""


def _is_vendor(vendor: str | None, want: str) -> bool:
    return bool(vendor) and vendor.strip().lower() == want.strip().lower()


def _group(vendor: str | None, want: str) -> str:
    return "untagged" if not vendor else ("vendor" if _is_vendor(vendor, want) else "other")


def _posters(conn, fks: list[str]) -> dict[str, int]:
    """Thumbnail asset id per tracker family (public.creative_assets; skipped when the connection cannot read it)."""
    try:
        rows = conn.execute("SELECT family_key, min(id) AS id FROM creative_assets WHERE kind = 'thumb' AND family_key = ANY(%s) GROUP BY 1", (fks,)).fetchall()
        return {(r["family_key"] if isinstance(r, dict) else r[0]): (r["id"] if isinstance(r, dict) else r[1]) for r in rows}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def build(conn, args, vendor: str | None = None) -> dict:
    vendor = (vendor or args.get("vendor") or DEFAULT_VENDOR).strip()[:40] or DEFAULT_VENDOR
    last = last_loaded_day(conn)
    if last is None:
        return {"empty": True, "vendor": vendor, "slides": [], "message": "No Meta ad data is loaded yet."}
    newest = latest_week(last)
    wk = args.get("week")
    if wk:
        try:
            cs = monday_of(dt.date.fromisoformat(str(wk)[:10]))
        except ValueError:
            raise q.ApiError(400, "week must be a date like 2026-11-09")
        if cs > newest:
            cs = newest
    else:
        cs = newest
    ce, ps, pe = cs + dt.timedelta(days=6), cs - dt.timedelta(days=7), cs - dt.timedelta(days=1)
    cur_label, prev_label = span_label(cs, ce), span_label(ps, pe)
    weeks = week_list(conn, newest)

    # ---- sums per (window, product, group, stage), per ad family
    sums: dict[tuple, dict] = {}                    # (is_cur, product, group, stage) -> sums
    acct: dict[bool, dict] = {True: _zero(), False: _zero()}
    by_vendor: dict[tuple, dict] = {}               # (is_cur, vendor name) -> sums
    fam_cur: dict[tuple, dict] = {}                 # (product, fk) -> {sums, testing_spend}
    for r in conn.execute(DAILY_SQL, {"cs": cs, "ps": ps, "ce": ce}).fetchall():
        s = {k: _f(r[k]) for k in SUMS}
        cur = bool(r["is_cur"])
        _add(acct[cur], s)
        prod, stage = r["product"], ("testing" if r["testing"] else "scaling")
        grp = _group(r["vendor"], vendor)
        _add(sums.setdefault((cur, prod, grp, stage), _zero()), s)
        _add(by_vendor.setdefault((cur, r["vendor"] or "Untagged"), _zero()), s)
        if cur and grp == "vendor":
            f = fam_cur.setdefault((prod, r["fk"]), {"s": _zero(), "testing": 0.0})
            _add(f["s"], s)
            if r["testing"]:
                f["testing"] += s["spend"]

    def pick(cur: bool, prods=None, groups=("vendor", "other", "untagged"), stages=("testing", "scaling")) -> dict:
        out = _zero()
        for (c, p, g, st), s in sums.items():
            if c == cur and (prods is None or p in prods) and g in groups and st in stages:
                _add(out, s)
        return metrics(out)

    # ---- tracker rows
    trk = conn.execute(TRACKER_SQL, {"prods": [p for p, _ in PRODUCTS]}).fetchall()
    first = {}                                      # (product, family_key) -> first row
    for r in trk:
        if r["is_first_row"]:
            first[(r["product"], r["family_key"])] = r
    life = {}
    fks = sorted({fk for (_p, fk) in fam_cur})
    if fks:
        for r in conn.execute(LIFE_SQL, {"fks": fks, "ce": ce}).fetchall():
            life[r["fk"]] = r
    posters = _posters(conn, fks)

    bench = {p: pick(True, {p})["cpa"] for p, _ in PRODUCTS}

    # ---- ads of the vendor this week
    ads: dict[str, dict] = {}
    ad_rows: list[dict] = []
    for (prod, fk), f in fam_cur.items():
        if prod not in PRODUCT_NAME or f["s"]["spend"] <= 0:
            continue
        m = metrics(f["s"])
        tr = first.get((prod, fk)) or {}
        lf = life.get(fk) or {}
        lsp, lpu = _f(lf.get("spend")), _f(lf.get("purchases"))
        key = slug(fk)[:60] + "-" + slug(prod)[:2]
        a = {"key": key, "family": fk, "product": prod, "product_name": PRODUCT_NAME[prod], "stage": "Testing" if f["testing"] > f["s"]["spend"] / 2 else "Scaling",
             "hook": tr.get("hook") or "", "tags": [t for t in (tr.get("cluster"), tr.get("icp"), tr.get("pain_point"), tr.get("emotional_trigger") or tr.get("tracker_emotion")) if t],
             "asset": tr.get("asset_url") or "", "first_day": lf.get("first_day").isoformat() if lf.get("first_day") else None,
             "poster": ("/media/%d" % posters[fk]) if fk in posters else None, "video": "/api/madre-review/video?family=" + fk,
             "m": m, "life": {"spend": lsp, "purchases": lpu, "cpa": (lsp / lpu) if lpu else None}, "bench": bench.get(prod), "name": short_name(fk)}
        ads[key] = a
        ad_rows.append(a)

    # ---- pipeline of the vendor (open concepts) and any-vendor launched families per pocket
    vrows = [r for r in trk if _is_vendor(r["vendor"], vendor)]
    pocket_of = lambda r: (r["product"], r["cluster"] or "Untagged", r["icp"] or "Untagged", r["pain_point"] or "Untagged")
    open_c: dict[tuple, set] = {}
    ready_c: dict[tuple, set] = {}
    launched_c: dict[tuple, set] = {}
    tickets_by_concept: dict[tuple, dict] = {}
    for r in vrows:
        n = status_num(r["status"])
        pk = pocket_of(r)
        ck = (r["product"], concept_key(r["concept_id"]))
        if n in OPEN_STATUSES:
            open_c.setdefault(pk, set()).add(ck)
            if n == 7 and (r["asset_url"] or "").strip():
                ready_c.setdefault(pk, set()).add(ck)
            t = tickets_by_concept.get(ck)
            if t is None or n > t["n"]:
                tickets_by_concept[ck] = {"n": n, "row": r, "variants": (t["variants"] if t else 0) + 1, "asset": (t or {}).get("asset") or ""}
            else:
                t["variants"] += 1
            if (r["asset_url"] or "").strip() and not tickets_by_concept[ck]["asset"]:
                tickets_by_concept[ck]["asset"] = r["asset_url"]
        elif n in (8, 9) and r["launch_date"] and ce - dt.timedelta(days=LAUNCHED_DAYS - 1) <= r["launch_date"] <= ce:
            launched_c.setdefault(pk, set()).add(ck)

    told: dict[tuple, list] = {}                    # (product, cluster, pain) -> first rows with spend, any vendor
    for r in first.values():
        if _f(r["spend_launch"]) > 0:
            told.setdefault((r["product"], r["cluster"] or "Untagged", r["pain_point"] or "Untagged"), []).append(r)

    tickets: dict[str, dict] = {}
    board: dict[str, dict[int, list]] = {p: {} for p, _ in PRODUCTS}
    for (prod, ckey), t in tickets_by_concept.items():
        r = t["row"]
        k = "t-" + slug(ckey)[:50] + "-" + slug(prod)[:2]
        tl = told.get((prod, r["cluster"] or "Untagged", r["pain_point"] or "Untagged")) or []
        top = sorted(tl, key=lambda x: -_f(x["spend_launch"]))[:3]
        tsp = sum(_f(x["spend_launch"]) for x in tl)
        tpu = sum(_f(x["purchases_launch"]) for x in tl)
        tickets[k] = {"key": k, "id": r["concept_id"], "product": prod, "product_name": PRODUCT_NAME[prod], "status": r["status"], "status_n": t["n"], "vendor": vendor,
                      "cluster": r["cluster"], "icp": r["icp"], "pain": r["pain_point"], "format": {"VID": "Video", "IMG": "Image"}.get(r["ad_type"] or "", "n/a"),
                      "variants": t["variants"], "asset": t["asset"],
                      "told": ({"n": len(tl), "spend": tsp, "cpa": (tsp / tpu) if tpu else None,
                                "top": [{"family": x["family_key"], "spend": _f(x["spend_launch"]), "cpa": (_f(x["spend_launch"]) / _f(x["purchases_launch"])) if _f(x["purchases_launch"]) else None,
                                         "vendor": x["vendor"]} for x in top]} if tl else None)}
        board[prod].setdefault(t["n"], []).append(k)

    # ---- pockets (vendor spend per product x cluster x ICP x pain point, with pipeline supply)
    pk_sums: dict[tuple, dict] = {}
    for a in ad_rows:
        tr = first.get((a["product"], a["family"])) or {}
        pk = (a["product"], tr.get("cluster") or "Untagged", tr.get("icp") or "Untagged", tr.get("pain_point") or "Untagged")
        _add(pk_sums.setdefault(pk, _zero()), {k: a["m"][k] for k in SUMS})
    pockets: dict[str, list[dict]] = {p: [] for p, _ in PRODUCTS}
    for pk in set(pk_sums) | set(open_c) | set(ready_c) | set(launched_c):
        prod = pk[0]
        if prod not in PRODUCT_NAME:
            continue
        m = metrics(pk_sums.get(pk, _zero()))
        row = {"product": prod, "cluster": pk[1], "icp": pk[2], "pain": pk[3], "m": m, "open": len(open_c.get(pk, ())), "ready": len(ready_c.get(pk, ())),
               "launched": len(launched_c.get(pk, ()))}
        row["flag"] = pocket_flag(row, bench.get(prod))
        pockets[prod].append(row)
    for p in pockets:
        pockets[p].sort(key=lambda r: -r["m"]["spend"])

    # ---- slides
    pm = {(g): pick(True, groups=(g,)) for g in ("vendor", "other", "untagged")}
    pp = {g: pick(False, groups=(g,)) for g in ("vendor", "other", "untagged")}
    acc_c, acc_p = metrics(acct[True]), metrics(acct[False])
    slides: list[dict] = []
    sections = [{"id": "overview", "label": "Overview"}] + [{"id": p, "label": n} for p, n in PRODUCTS] + [{"id": "notes", "label": "Notes"}]

    def table(cols, keys=TABLE_KEYS) -> dict:
        return {"keys": list(keys), "cols": cols}

    def col(group, window, m, prev=None):
        return {"group": group, "window": window, "m": m, "p": prev}

    vm, vp = pm["vendor"], pp["vendor"]
    slides.append({"id": "cover", "section": "overview", "kind": "cover", "title": f"{vendor} · Week of {cur_label}",
                   "big": [["Spend", vm["spend"], "money"], ["Purchases", vm["purchases"], "num"], ["CPA", vm["cpa"], "money2"], ["Account CPA", acc_c["cpa"], "money2"]],
                   "foot": f"Meta-reported. {vendor} = ads tagged {vendor} on the Creative Tracker."})

    mt, ot = pick(True, groups=("vendor",), stages=("testing",)), pick(True, groups=("other",), stages=("testing",))
    ms, os_ = pick(True, groups=("vendor",), stages=("scaling",)), pick(True, groups=("other",), stages=("scaling",))
    scaling_ok = _le(ms["cpa"], os_["cpa"], 1.05)
    testing_ok = _le(mt["cpa"], ot["cpa"], 1.15)
    title = {(True, True): "Scaling and testing both hold.", (True, False): "Scaling holds. Testing is too expensive.",
             (False, True): "Testing holds. Scaling is too expensive.", (False, False): "Scaling and testing are both too expensive."}[(scaling_ok, testing_ok)]
    best = _best_line(sums, vendor)
    ready_total = sum(len(v) for v in ready_c.values())
    big_pocket = max((r for r in sum(pockets.values(), []) if r["m"]["spend"] > 0), key=lambda r: r["m"]["spend"], default=None)
    cards = [{"label": "Testing", "value": money(mt["cpa"]), "text": f"{vendor} test CPA vs {money(ot['cpa'])} for other vendors. {money(mt['spend'])} in tests, {mt['purchases']:,.0f} purchases."},
             ({"label": "Best line", "value": money(best["cpa"]), "text": f"{PRODUCT_NAME[best['product']]} {vendor} {best['stage']}: {best['roas']:.2f}x ROAS on {money(best['spend'])}."} if best
              else {"label": "Best line", "value": "n/a", "text": "No line with $1,000 spend."}),
             {"label": "Pipeline", "value": f"{ready_total} ready to launch", "text": (f"Biggest pocket: {big_pocket['icp']} / {big_pocket['pain']} at {money(big_pocket['m']['spend'])} with {big_pocket['open']} open." if big_pocket else "")}]
    slides.append({"id": "week", "section": "overview", "kind": "week", "kicker": "The week", "title": title, "cards": cards,
                   "bullets": [f"{vendor} CPA {money(vp['cpa'], 2)} to {money(vm['cpa'], 2)}. Account {money(acc_p['cpa'], 2)} to {money(acc_c['cpa'], 2)}.",
                               f"{vendor} scaling {money(ms['cpa'], 2)}. Testing {money(mt['cpa'], 2)}."]})
    oth_c, oth_p = pm["other"], pp["other"]
    slides.append({"id": "scorecard", "section": "overview", "kind": "table", "kicker": "Scorecard", "title": f"{vendor} vs account",
                   "table": table([col(g, w, m, p_) for g, (mc, mp) in (( vendor, (vm, vp)), ("Account", (acc_c, acc_p)), ("Other vendors", (oth_c, oth_p)), ("Untagged", (pm["untagged"], pp["untagged"]))) for w, m, p_ in ((prev_label, mp, None), (cur_label, mc, mp))]),
                   "foot": "Green and red = 5%+ move vs the week before. Untagged = no Creative Tracker match.",
                   "vendors": _vendor_rows(by_vendor)})

    for prod, pname in PRODUCTS:
        slides += _product_slides(prod, pname, vendor, cur_label, prev_label, pick, bench, ad_rows, pockets[prod], board[prod], tickets, ce)

    tg = acc_c["spend"] and sum(m_["spend"] for (c_, _v), m_ in by_vendor.items() if c_ and _v == "Untagged") / acc_c["spend"]
    slides.append({"id": "notes", "section": "notes", "kind": "notes", "kicker": "Notes", "title": "How this is built", "bullets": [
        f"Meta-reported, from the Lonvera database through {last.strftime('%b')} {last.day}. The last 3 days still move. Ratios are ratios of sums.",
        f"{vendor} = vendor on the Creative Tracker row of the ad family. Untagged ({(tg or 0) * 100:.1f}% of account spend) has no tracker match.",
        "Testing = campaign name contains test. Everything else is scaling.",
        "Pockets use the line-mapped tags (market cluster, ICP, pain point)."]})

    return {"vendor": vendor, "week": {"start": cs.isoformat(), "end": ce.isoformat(), "label": cur_label}, "prev": {"start": ps.isoformat(), "end": pe.isoformat(), "label": prev_label},
            "weeks": weeks, "default_week": newest.isoformat(), "last_loaded_day": last.isoformat(),
            "freshness": f"Loaded through {last.strftime('%b')} {last.day}. The last 3 days still move.", "bench": bench, "sections": sections, "slides": slides, "ads": ads, "tickets": tickets,
            "thresholds": {"win_min_spend": WIN_MIN_SPEND, "win_min_purchases": WIN_MIN_PURCHASES, "win_cpa_under": WIN_CPA_UNDER, "lose_min_spend": LOSE_MIN_SPEND, "lose_cpa_over": LOSE_CPA_OVER},
            "scorecard": {"account": {"cur": acc_c, "prev": acc_p}, "vendor": {"cur": vm, "prev": vp}}}


def _le(a, b, mult: float) -> bool:
    if a is None or b is None:
        return True
    return a <= b * mult


def _best_line(sums: dict, vendor: str) -> dict | None:
    best = None
    for prod, _n in PRODUCTS:
        for stage in ("testing", "scaling"):
            s = _zero()
            for (c, p, g, st), v in sums.items():
                if c and p == prod and g == "vendor" and st == stage:
                    _add(s, v)
            m = metrics(s)
            if m["spend"] >= 1000 and m["cpa"] is not None and (best is None or m["cpa"] < best["cpa"]):
                best = {"product": prod, "stage": stage, **m}
    return best


def _vendor_rows(by_vendor: dict) -> list[dict]:
    names = sorted({v for (c, v) in by_vendor if c}, key=lambda n: -by_vendor[(True, n)]["spend"])
    return [{"vendor": n, "m": metrics(by_vendor[(True, n)])} for n in names]


def pocket_flag(r: dict, bench) -> str:
    """'scale' | 'pile' | 'pull' | '' (Josh's rules; thresholds above)."""
    sp, cpa = r["m"]["spend"], r["m"]["cpa"]
    if bench:
        if sp >= SCALE_MIN_SPEND and cpa is not None and cpa <= bench * (1 - WIN_CPA_UNDER) and r["open"] <= SCALE_MAX_OPEN:
            return "scale"
        if r["ready"] >= PILE_MIN_READY and sp < PILE_MAX_SPEND:
            return "pile"
        if sp >= PULL_MIN_SPEND and (cpa is None or cpa >= bench * (1 + PULL_CPA_OVER)) and r["open"] >= PULL_MIN_OPEN:
            return "pull"
    elif r["ready"] >= PILE_MIN_READY and sp < PILE_MAX_SPEND:
        return "pile"
    return ""


FLAG_LABEL = {"scale": "Scale", "pile": "Too much in pipeline", "pull": "Pull back", "": ""}


def _product_slides(prod, pname, vendor, cur_label, prev_label, pick, bench, ad_rows, pockets, board, tickets, ce) -> list[dict]:
    P = {prod}
    b = bench.get(prod)
    pv = lambda cur, g=("vendor",), st=("testing", "scaling"): pick(cur, P, g, st)
    vm, vp = pv(True), pv(False)
    allm, allp = pick(True, P), pick(False, P)
    oth, othp = pv(True, ("other",)), pv(False, ("other",))
    unt, untp = pv(True, ("untagged",)), pv(False, ("untagged",))
    mine = sorted([a for a in ad_rows if a["product"] == prod], key=lambda a: -a["m"]["spend"])
    sec = prod
    out: list[dict] = []
    out.append({"id": "cover", "section": sec, "kind": "cover", "product": prod, "kicker": f"{vendor} · {pname}", "title": pname,
                "big": [[f"{vendor} spend", vm["spend"], "money"], ["Purchases", vm["purchases"], "num"], [f"{vendor} CPA", vm["cpa"], "money2"], ["Product avg CPA", b, "money2"]],
                "foot": f"{cur_label}. Product avg = all ads on {pname}, any vendor."})
    out.append({"id": "scorecard", "section": sec, "kind": "table", "kicker": f"{pname} · Scorecard", "title": f"{vendor} vs {pname} avg",
                "table": {"keys": list(TABLE_KEYS), "cols": [{"group": g, "window": w, "m": m, "p": p_} for g, mc, mp in ((vendor, vm, vp), (f"{pname} avg", allm, allp), ("Other vendors", oth, othp), ("Untagged", unt, untp))
                                                              for w, m, p_ in ((prev_label, mp, None), (cur_label, mc, mp))]},
                "foot": "Product avg = all vendors. Green and red = 5%+ move vs the week before."})
    stg = [(f"{vendor} testing", pv(True, ("vendor",), ("testing",)), pv(False, ("vendor",), ("testing",))), ("Other testing", pv(True, ("other",), ("testing",)), pv(False, ("other",), ("testing",))),
           (f"{vendor} scaling", pv(True, ("vendor",), ("scaling",)), pv(False, ("vendor",), ("scaling",))), ("Other scaling", pv(True, ("other",), ("scaling",)), pv(False, ("other",), ("scaling",)))]
    out.append({"id": "stage", "section": sec, "kind": "table", "kicker": f"{pname} · Stage", "title": "Testing vs scaling",
                "table": {"keys": list(TABLE_KEYS), "cols": [{"group": g, "window": cur_label, "m": m, "p": p_} for g, m, p_ in stg]},
                "foot": f"Testing = campaign name has test. Other = other vendors, no Untagged. Colors vs {prev_label}."})
    winners = [a for a in mine if b and a["m"]["spend"] >= WIN_MIN_SPEND and a["m"]["purchases"] >= WIN_MIN_PURCHASES and a["m"]["cpa"] is not None and a["m"]["cpa"] <= b * (1 - WIN_CPA_UNDER)]
    winners.sort(key=lambda a: a["m"]["cpa"])
    losers = [a for a in mine if b and a["m"]["spend"] >= LOSE_MIN_SPEND and (a["m"]["purchases"] == 0 or (a["m"]["cpa"] is not None and a["m"]["cpa"] >= b * (1 + LOSE_CPA_OVER)))]
    losers.sort(key=lambda a: -a["m"]["spend"])
    out.append({"id": "winners", "section": sec, "kind": "ads", "kicker": f"{pname} · Winners", "title": f"{len(winners)} winners: CPA {int(WIN_CPA_UNDER * 100)}%+ under {pname} avg",
                "ads": [a["key"] for a in winners[:WINNER_TILES]], "total": len(winners), "empty": "No ads cleared the bar this week.",
                "foot": f"${WIN_MIN_SPEND:,.0f}+ spend, {WIN_MIN_PURCHASES}+ purchases, CPA at least {int(WIN_CPA_UNDER * 100)}% under the product's avg CPA across all vendors ({money(b)}). Click an ad to play it."})
    out.append({"id": "losers", "section": sec, "kind": "ads", "kicker": f"{pname} · Losers", "title": f"{len(losers)} losers: CPA {int(LOSE_CPA_OVER * 100)}%+ over {pname} avg or no sales",
                "ads": [a["key"] for a in losers[:LOSER_TILES]], "total": len(losers), "empty": "No ads missed the bar this week.",
                "foot": f"${LOSE_MIN_SPEND:,.0f}+ spend and CPA at least {int(LOSE_CPA_OVER * 100)}% over the product's avg CPA across all vendors ({money(b)}), or no purchases."})
    shown = [r for r in pockets if r["m"]["spend"] >= POCKET_MIN_SPEND or r["open"] >= POCKET_MIN_OPEN][:POCKET_ROWS]
    out.append({"id": "pockets", "section": sec, "kind": "pockets", "kicker": f"{pname} · Pockets", "title": "Where spend went vs what's in the pipeline",
                "rows": [{**r, "flag_label": FLAG_LABEL[r["flag"]]} for r in shown], "window": cur_label,
                "foot": f"Open = concepts in status 2–7. Ready = ready to launch with an asset. Scale: ${SCALE_MIN_SPEND:,.0f}+ spend, CPA {int(WIN_CPA_UNDER * 100)}%+ under product, {SCALE_MAX_OPEN} or fewer open. "
                        f"Too much in pipeline: {PILE_MIN_READY}+ ready, under ${PILE_MAX_SPEND:,.0f} spend. Pull back: CPA {int(PULL_CPA_OVER * 100)}%+ over product, {PULL_MIN_OPEN}+ open."})
    cols = [{"status_n": n, "label": _status_label(tickets, keys), "tickets": keys} for n, keys in sorted(board.items())]
    out.append({"id": "pipeline", "section": sec, "kind": "board", "kicker": f"{pname} · Pipeline", "title": "Open concepts", "columns": cols, "empty": f"No open {vendor} concepts on {pname}.",
                "foot": "Concepts collapse hooks and copies. Click one to open it."})
    sugg = suggestions(prod, pname, vendor, pockets, b, cur_label)
    out.append({"id": "next", "section": sec, "kind": "suggest", "kicker": f"{pname} · Next pockets", "title": "Brief more here", "cards": sugg, "empty": "No pocket clears the bar this week.",
                "foot": "Pockets flagged Scale, plus pockets with CPA under the product avg and no open concept."})
    out.append({"id": "asks", "section": sec, "kind": "asks", "kicker": f"{pname} · Asks", "title": f"Asks for {vendor} this week", "items": asks(prod, pname, vendor, stg, losers, pockets, b)})
    return out


def _status_label(tickets: dict, keys: list[str]) -> str:
    return tickets[keys[0]]["status"] if keys else ""


def suggestions(prod, pname, vendor, pockets, bench, cur_label) -> list[dict]:
    if not bench:
        return []
    picks, seen = [], set()
    for r in sorted(pockets, key=lambda r: (r["m"]["cpa"] if r["m"]["cpa"] is not None else 1e9)):
        sp, cpa = r["m"]["spend"], r["m"]["cpa"]
        good = cpa is not None and cpa <= bench * (1 - WIN_CPA_UNDER)
        if r["flag"] == "scale" or (good and sp >= SUGGEST_MIN_SPEND and r["open"] == 0):
            if (r["cluster"], r["icp"], r["pain"]) in seen:
                continue
            seen.add((r["cluster"], r["icp"], r["pain"]))
            picks.append(r)
    cards = []
    for r in picks[:SUGGEST_PER_PRODUCT]:
        m = r["m"]
        why = (f"CPA {money(m['cpa'], 2)} vs {pname} avg {money(bench, 2)}, {m['roas']:.2f}x ROAS on {money(m['spend'])}. "
               f"{r['open']} open, {r['ready']} ready to launch.")
        cards.append({"product": prod, "product_name": pname, "cluster": r["cluster"], "icp": r["icp"], "pain": r["pain"], "title": f"{r['icp']}: {r['pain']}", "why": why,
                      "idea": {"product": prod, "cluster": r["cluster"], "icp": r["icp"], "pain_point": r["pain"], "media_type": "video",
                               "note": f"{vendor} review {cur_label}: {why}"}})
    return cards


def asks(prod, pname, vendor, stg, losers, pockets, bench) -> list[list[dict]]:
    """Imperative bullets, each a list of segments: {t: text} or {ad: key, label}."""
    t = lambda s: {"t": s}
    items: list[list[dict]] = []
    mt, ot = stg[0][1], stg[1][1]
    test_losers = [a for a in losers if a["stage"] == "Testing"][:2]
    if mt["cpa"] is not None and ot["cpa"] is not None and mt["cpa"] > ot["cpa"] * 1.15 and mt["spend"] >= 300:
        seg = [t(f"Cut test CPA. {vendor} tests ran {money(mt['cpa'])} vs {money(ot['cpa'])} for other vendors.")]
        if test_losers:
            seg.append(t(" Pause "))
            for i, a in enumerate(test_losers):
                seg += ([t(" and ")] if i else []) + [{"ad": a["key"], "label": a["name"]}]
            seg.append(t("."))
        items.append(seg)
    elif test_losers:
        seg = [t("Pause ")]
        for i, a in enumerate(test_losers):
            seg += ([t(" and ")] if i else []) + [{"ad": a["key"], "label": a["name"]}]
        items.append(seg + [t(".")])
    for r in [r for r in pockets if r["flag"] == "pile"][:2]:
        items.append([t(f"Launch the {r['ready']} ready {r['icp']} ads before briefing more {r['pain']}.")])
    for r in [r for r in pockets if r["flag"] == "pull"][:1]:
        items.append([t(f"Brief less on {r['icp']} / {r['pain']}. CPA {money(r['m']['cpa'])} vs {money(bench)} avg, {r['open']} open.")])
    for r in [r for r in pockets if r["flag"] == "scale"][:2]:
        items.append([t(f"Brief into {r['icp']} / {r['pain']}. CPA {money(r['m']['cpa'])}, {r['open']} open.")])
    if not items:
        items.append([t("Nothing to change this week.")])
    return items


def payload(conn, args) -> dict:
    return build(conn, args)
