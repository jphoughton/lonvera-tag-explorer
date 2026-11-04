"""Synthetic dataset for the API tests + an independent pure-Python oracle of the contract's metric rules.

The oracle works from the RAW fixture (tracker rows, ad_daily, mapping, campaigns, lifetime) and also builds the derived tables
(family_daily, ad_metrics, unmatched_meta) the way etl.build_metrics is specified to. The tests then compare the API's answers to
oracle answers computed with plain loops, so the SQL in app/queries.py is checked against an independent implementation.
"""
from __future__ import annotations

import datetime as dt
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

D = dt.date
D0 = D(2026, 5, 6)
CUT = D(2026, 9, 17)
THROUGH = D(2026, 11, 2)
Z = Decimal(0)
STAGES = ("testing", "scaling", "unclassified")


def dec(x) -> Decimal:
    return Decimal(str(x))


def fkey(ad_family: str, concept: str) -> str:
    f = (ad_family or "").strip().lower()
    return f if f else "id:" + (concept or "").strip().lower()


def rnd0(x: Decimal) -> Decimal:
    return x.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def rnd2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------------------------------------------------------------
# raw fixture definition
# ---------------------------------------------------------------------------------------------------------------------------------

TAXONOMY = [  # vita-bull Communication Overview (sort order = list order)
    dict(code="L01", cluster="Alpha Cluster", icp="ICP One", pain="Pain A", trig="Fear", story="Story A", color="#aa3333",
         ingredients=[("Zinc", "Zinc depletion", "Supports testosterone"), ("Cholesterol", "Missing substrate", "Supports hormones")]),
    dict(code="L02", cluster="Alpha Cluster", icp="ICP One", pain="Pain B", trig="Shame", story="Story B", color="#aa3333", ingredients=[]),
    dict(code="L03", cluster="Alpha Cluster", icp="ICP Two", pain="Pain C", trig="Relief", story="Story C", color="#aa3333", ingredients=[]),
    dict(code="L04", cluster="Beta Cluster", icp="ICP Three", pain="Pain D", trig="Desire", story="Story D", color="#3355aa", ingredients=[]),
    dict(code="L05", cluster="Beta Cluster", icp="ICP Three", pain="Pain E", trig="Pride", story="Story E", color="#3355aa", ingredients=[]),  # no ads
]

# tag spec: ('linemap', line_code, confidence, method, extra) | ('tracker', cluster, icp, pain) | None (untagged)
TRACKER = [
    # ---- vita-bull (linemap) ----
    dict(k="A1", product="vita-bull", concept="VB-A1", family="VB-Fam-A", launch=D(2026, 8, 1), status="8 - Launched", ad_type="VID",
         tag=("linemap", "L01", "high", "image", dict(seen="Bottle on counter", why="spoken point is zinc", runner_up="ICP Two - Pain C (Relief)",
                                                        copy_only="ICP Two - Pain C (Relief)"))),
    dict(k="A2", product="vita-bull", concept="VB-A2", family=" vb-fam-a ", launch=D(2026, 8, 1), status="8 - Launched", ad_type="VID",
         tag=("linemap", "L02", "low", "text only", {})),
    dict(k="B1", product="vita-bull", concept="VB-B1", family="VB-Fam-B", launch=D(2026, 4, 1), status="8 - Launched", ad_type="IMG",
         tag=("linemap", "L01", "medium", "video: thumbnail + no audio", {})),
    dict(k="Z510a", product="vita-bull", concept="0510", family="", launch=None, status="3 - Briefing", ad_type="IMG",
         tag=("linemap", "L03", "high", "image", {})),
    dict(k="Z510b", product="vita-bull", concept="510", family="", launch=D(2026, 9, 1), status="8 - Launched", ad_type="IMG",
         tag=("linemap", "L03", "medium", "image", {})),
    dict(k="C1", product="vita-bull", concept="VB-C1", family="VB-Fam-C", launch=None, status="8 - Launched", ad_type="VID",
         tag=("linemap", "L04", "high", "image + audio transcript", {})),
    dict(k="D1", product="vita-bull", concept="VB-D1", family="VB-Fam-D", launch=D(2026, 10, 20), status="8 - Launched", ad_type="VID",
         tag=("linemap", "L04", "medium", "image", {})),
    dict(k="E1", product="vita-bull", concept="VB-E1", family="VB-Fam-E", launch=D(2026, 9, 20), status="8 - Launched", ad_type="IMG",
         tag=None),
    dict(k="F1", product="vita-bull", concept="VB-F1", family="VB-Fam-F", launch=D(2026, 7, 1), status="8 - Launched", ad_type="VID",
         tag=("linemap", "L03", "low", "video: thumbnail + 4 frames + no audio", {})),
    # ---- beef-liver (tracker tags) ----
    dict(k="BL1", product="beef-liver", concept="BL-1", family="BL-Fam-1", launch=D(2026, 9, 25), status="8 - Launched", ad_type="VID",
         tag=("tracker", "Hair", "BL1 Weekend Face", "Hair Loss")),
    dict(k="BL2", product="beef-liver", concept="BL-2", family="BL-Fam-2", launch=D(2026, 8, 10), status="8 - Launched", ad_type="IMG",
         tag=("tracker", "(none)", "(none)", "(none)")),
    dict(k="BL3", product="beef-liver", concept="BL-3", family="BL-Fam-3", launch=None, status="8 - Launched", ad_type="IMG",
         tag=("tracker", "Hair", "BL1 Weekend Face", "Hair Loss")),
    dict(k="BL4", product="beef-liver", concept="BL-4", family="", launch=None, status="2 - Up Next For Briefing", ad_type="IMG",
         tag=("tracker", "Energy", "BL2 The Stubborn Gut", "Low Energy")),
    # ---- boneflex ----
    dict(k="BF1", product="boneflex", concept="BF-1", family="BF-Fam-1", launch=D(2026, 10, 1), status="8 - Launched", ad_type="IMG",
         tag=("tracker", "Joints", "BF1 Won't Sit It Out", "Joint Pain")),
]

CAMPAIGNS = [  # id, account, name
    ("C100", "Main", "VB_Scaling_US"), ("C200", "Main", "VB_Testing_Cambium"),
    ("C300", "Main", "BL_Scaling"), ("C400", "Main", "BL test campaign"),
]
# note campaign "C999" is deliberately NOT in meta_campaigns (-> unclassified)

# meta ads: ad_id -> (meta family display, campaign, adset, status, created)
ADS = {
    "a1": ("VB-Fam-A", "C100", "VB scale set", "ACTIVE", D(2026, 9, 28)),
    "a2": ("VB-Fam-A - Copy 2", "C200", "VB test set", "ACTIVE", D(2026, 10, 9)),
    "a3": ("VB-Fam-A_VID_relaunch", "C100", "VB scale set", "PAUSED", D(2026, 10, 14)),
    "b1": ("VB-Fam-B", "C100", "VB scale set", "ACTIVE", D(2026, 3, 30)),
    "b2": ("VB-Fam-B - Copy 2", "C200", "VB test set", "PAUSED", D(2026, 3, 30)),
    "c1": ("VB-Fam-C", "C100", "VB scale set", "ACTIVE", D(2026, 10, 19)),
    "d1": ("VB-Fam-D", "C200", "VB test set", "ACTIVE", D(2026, 10, 19)),
    "d2": ("VB-Fam-D - Copy 2", "C100", "VB scale set", "ACTIVE", D(2026, 10, 24)),
    "e1": ("VB-Fam-E", "C100", "VB scale set", "ACTIVE", D(2026, 9, 19)),
    "f1": ("VB-Fam-F", "C999", "VB scale set", "PAUSED", D(2026, 9, 29)),
    "bl1a": ("BL-Fam-1", "C300", "BL scale set", "ACTIVE", D(2026, 9, 24)),
    "bl1b": ("BL-Fam-1 - Copy 2", "C400", "BL test set", "ACTIVE", D(2026, 10, 9)),
    "bl3a": ("BL-Fam-3", "C300", "BL scale set", "ACTIVE", D(2026, 10, 27)),
    "u1": ("Brief2-2-Bull-398", "C100", "VB scale set", "ACTIVE", D(2026, 10, 19)),
    "u2": ("Weird-Bone-1", "C300", "BL scale set", "ACTIVE", D(2026, 10, 24)),
    "u3": ("Orphan-Zzz", "C100", "VB scale set", "ACTIVE", D(2026, 10, 29)),
    "u4": ("Vital Flow Test 1", "C200", "VB test set", "ACTIVE", D(2026, 10, 29)),
}


def meta_family_key(display: str) -> str:
    return re.sub(r" - copy \d+$", "", display.strip().lower())


# ad-level day ranges (inclusive; the series generator skips ~15% of days; "last-day-offset" lets an ad end before data_through)
AD_RANGES = {
    "a1": (D(2026, 10, 1), D(2026, 11, 2)), "a2": (D(2026, 10, 10), D(2026, 11, 2)), "a3": (D(2026, 10, 15), D(2026, 11, 2)),
    "b1": (D(2026, 5, 6), D(2026, 11, 2)), "b2": (D(2026, 5, 6), D(2026, 10, 15)),
    "c1": (D(2026, 10, 20), D(2026, 11, 2)),
    "d1": (D(2026, 10, 20), D(2026, 11, 2)), "d2": (D(2026, 10, 25), D(2026, 11, 2)),
    "e1": (D(2026, 9, 20), D(2026, 11, 1)),
    "f1": (D(2026, 9, 30), D(2026, 10, 20)),
    "bl1a": (D(2026, 9, 25), D(2026, 11, 2)), "bl1b": (D(2026, 10, 10), D(2026, 10, 25)), "bl3a": (D(2026, 10, 28), D(2026, 11, 2)),
    "u1": (D(2026, 10, 20), D(2026, 11, 2)), "u2": (D(2026, 10, 25), D(2026, 11, 2)), "u3": (D(2026, 10, 30), D(2026, 11, 2)),
    "u4": (D(2026, 10, 30), D(2026, 11, 1)),
}
# family-grain sheet rows: meta family display -> (start, end)
FAM_RANGES = {"VB-Fam-A": (D(2026, 8, 1), D(2026, 9, 30)), "VB-Fam-F": (D(2026, 7, 1), D(2026, 9, 29)), "BL-Fam-2": (D(2026, 8, 10), D(2026, 9, 30))}

# meta family key -> (tracker family key | None, rule)
MAPPING = {
    "vb-fam-a": ("vb-fam-a", "sheet-windows"), "vb-fam-a_vid_relaunch": ("vb-fam-a", "relaunch"), "vb-fam-b": ("vb-fam-b", "sheet-windows"),
    "vb-fam-c": ("vb-fam-c", "sheet-windows"), "vb-fam-d": ("vb-fam-d", "sheet-windows"), "vb-fam-e": ("vb-fam-e", "sheet-windows"),
    "vb-fam-f": ("vb-fam-f", "sheet-windows"), "bl-fam-1": ("bl-fam-1", "sheet-windows"), "bl-fam-2": ("bl-fam-2", "sheet-windows"),
    "bl-fam-3": ("bl-fam-3", "sheet-windows"),
    "brief2-2-bull-398": (None, None), "weird-bone-1": (None, None), "orphan-zzz": (None, None), "vital flow test 1": (None, None),
}
META_REGEX = {"boneflex": ("bone", 10), "beef-liver": ("liver", 20), "vita-bull": ("bull", 30), "vita-belle": ("belle", 40),
              "vital-flow": (r"vital[-_ ]?flow", 50)}

# per-ad lifetime (Meta Ads - Lifetime tab) for the pre-D0 family; ad_lifetime has extra history no ad row accounts for
META_AD_LIFETIME = {"b1": (dec("1000.50"), dec(20), dec("900.00")), "b2": (dec("500.25"), dec(9), dec("400.00"))}
LIFETIME_EXTRA = {"vb-fam-b": (dec("120.00"), dec(3), dec("100.00"))}


@dataclass
class Fx:
    product_ids: dict = field(default_factory=dict)          # slug -> id
    tracker: list = field(default_factory=list)              # dicts with id, product, family_key, sheet_row, ...
    ad_daily: list = field(default_factory=list)             # dicts: ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, source
    camp_stage: dict = field(default_factory=dict)           # campaign id -> stage
    lifetime: dict = field(default_factory=dict)             # tracker family key -> (spend, purch, rev)
    # derived (oracle-built)
    family_daily: dict = field(default_factory=dict)         # (family_key, day, stage) -> [spend, purch, rev, clicks]
    ad_metrics: dict = field(default_factory=dict)           # tracker row id -> dict
    unmatched: dict = field(default_factory=dict)            # meta_family_key -> dict


def stage_of_campaign(name: str) -> str:
    return "testing" if "test" in name.lower() else "scaling"


def _series(key: str, start: dt.date, end: dt.date):
    rnd = random.Random(key)
    d = start
    while d <= end:
        if rnd.random() > 0.15 or d >= THROUGH - dt.timedelta(days=1) and key in ("d1", "d2", "a1", "e1", "c1"):
            spend = round(rnd.uniform(5, 60), 2)
            purch = rnd.choice([0, 1, 1, 2, 3, 2.5])
            rev = round(purch * rnd.uniform(25, 60), 2)
            clicks = rnd.randint(5, 80)
            yield d, dec(spend), dec(purch), dec(rev), clicks
        d += dt.timedelta(days=1)


def build_fixture(product_ids: dict) -> Fx:
    fx = Fx(product_ids=dict(product_ids))
    # tracker rows
    seen = defaultdict(int)
    for i, t in enumerate(TRACKER):
        r = dict(t)
        r["sheet_row"] = i + 2
        r["family_key"] = fkey(t["family"], t["concept"])
        seen[(t["concept"], t["family"])] += 1
        r["row_key"] = f"{t['concept']}#{seen[(t['concept'], t['family'])]}"
        r["id"] = 1000 + i
        r["product_id"] = product_ids[t["product"]]
        fx.tracker.append(r)
    fx.camp_stage = {cid: stage_of_campaign(name) for cid, _a, name in CAMPAIGNS}
    # ad-level rows
    for ad_id, (rng_s, rng_e) in AD_RANGES.items():
        disp, camp, *_ = ADS[ad_id]
        for d, sp, pu, rv, cl in _series(ad_id, rng_s, rng_e):
            fx.ad_daily.append(dict(ad_id=ad_id, day=d, meta_family_key=meta_family_key(disp), meta_family=disp, campaign_id=camp,
                                    spend=sp, purchases=pu, conv_value=rv, link_clicks=cl, source="meta_api"))
    # family-grain sheet rows
    for disp, (s, e) in FAM_RANGES.items():
        for d, sp, pu, rv, cl in _series("fam:" + disp, s, e):
            k = meta_family_key(disp)
            fx.ad_daily.append(dict(ad_id="fam:" + k, day=d, meta_family_key=k, meta_family=disp, campaign_id="",
                                    spend=sp, purchases=pu, conv_value=rv, link_clicks=cl, source="sheet"))
    # lifetime (family sums = per-ad sums + extra)
    for ad_id, (sp, pu, rv) in META_AD_LIFETIME.items():
        k = meta_family_key(ADS[ad_id][0])
        tfam = MAPPING[k][0]
        cur = fx.lifetime.get(tfam, (Z, Z, Z))
        fx.lifetime[tfam] = (cur[0] + sp, cur[1] + pu, cur[2] + rv)
    for tfam, (sp, pu, rv) in LIFETIME_EXTRA.items():
        cur = fx.lifetime.get(tfam, (Z, Z, Z))
        fx.lifetime[tfam] = (cur[0] + sp, cur[1] + pu, cur[2] + rv)
    build_derived(fx)
    return fx


# ---------------------------------------------------------------------------------------------------------------------------------
# oracle: derived tables
# ---------------------------------------------------------------------------------------------------------------------------------


def stage_of_row(fx: Fx, campaign_id: str) -> str:
    if campaign_id == "":
        return "unclassified"
    return fx.camp_stage.get(campaign_id, "unclassified")


def build_derived(fx: Fx) -> None:
    fd = fx.family_daily
    for r in fx.ad_daily:
        tfam = MAPPING.get(r["meta_family_key"], (None, None))[0]
        if tfam is None:
            continue
        key = (tfam, r["day"], stage_of_row(fx, r["campaign_id"]))
        cur = fd.setdefault(key, [Z, Z, Z, 0])
        cur[0] += r["spend"]
        cur[1] += r["purchases"]
        cur[2] += r["conv_value"]
        cur[3] += r["link_clicks"]
    # first rows
    firsts = {}
    for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
        firsts.setdefault((r["product_id"], r["family_key"]), r["id"])
    for r in fx.tracker:
        is_first = firsts[(r["product_id"], r["family_key"])] == r["id"]
        row = dict(tracker_row_id=r["id"], product_id=r["product_id"], family_key=r["family_key"], is_first_row=is_first,
                   launch_date=r["launch"])
        if is_first:
            f = r["family_key"]
            lm = launch_metrics(fx, f, r["launch"], "all")
            row.update(spend_launch=lm[0], purchases_launch=lm[1], revenue_launch=lm[2])
            for tag, n in (("7d", 7), ("30d", 30)):
                s, p, v = window_sums(fx, f, THROUGH - dt.timedelta(days=n - 1), THROUGH, STAGES)
                row.update({f"spend_{tag}": s, f"purchases_{tag}": p, f"revenue_{tag}": v})
            row["live"] = window_sums(fx, f, THROUGH - dt.timedelta(days=1), THROUGH, STAGES)[0] > 0
        else:
            row.update(spend_launch=Z, purchases_launch=Z, revenue_launch=Z, spend_7d=Z, purchases_7d=Z, revenue_7d=Z,
                       spend_30d=Z, purchases_30d=Z, revenue_30d=Z, live=False)
        fx.ad_metrics[r["id"]] = row
    # unmatched
    by = defaultdict(list)
    for r in fx.ad_daily:
        if MAPPING.get(r["meta_family_key"], (None, None))[0] is None:
            by[r["meta_family_key"]].append(r)
    for k, rows in by.items():
        name = rows[-1]["meta_family"]
        pid = None
        for slug, (rx, prio) in sorted(META_REGEX.items(), key=lambda kv: kv[1][1]):
            if re.search(rx, name, re.I):
                pid = fx.product_ids[slug]
                break
        pid = pid or fx.product_ids["unassigned"]

        def sums(d_from, d_to=THROUGH):
            sel = [x for x in rows if d_from <= x["day"] <= d_to]
            return (sum((x["spend"] for x in sel), Z), sum((x["purchases"] for x in sel), Z), sum((x["conv_value"] for x in sel), Z))
        s7, s30, sa = sums(THROUGH - dt.timedelta(days=6)), sums(THROUGH - dt.timedelta(days=29)), sums(D(2000, 1, 1))
        fx.unmatched[k] = dict(meta_family_key=k, meta_family=name, product_id=pid,
                               spend_7d=s7[0], purchases_7d=s7[1], revenue_7d=s7[2], spend_30d=s30[0], purchases_30d=s30[1],
                               revenue_30d=s30[2], spend_all=sa[0], purchases_all=sa[1], revenue_all=sa[2],
                               live=sums(THROUGH - dt.timedelta(days=1))[0] > 0)


def window_sums(fx: Fx, tfam: str, d_from: dt.date, d_to: dt.date, stages) -> tuple[Decimal, Decimal, Decimal]:
    s = p = v = Z
    for (f, day, st), (sp, pu, rv, _cl) in fx.family_daily.items():
        if f == tfam and d_from <= day <= d_to and st in stages:
            s, p, v = s + sp, p + pu, v + rv
    return s, p, v


def stages_of(stage) -> tuple:
    """'all' | 'testing' | ('testing', 'scaling') ... -> tuple of included stages."""
    if stage == "all":
        return STAGES
    return (stage,) if isinstance(stage, str) else tuple(stage)


def launch_start(launch: dt.date | None) -> dt.date | None:
    if launch is None:
        return None
    return launch if launch >= D0 else CUT + dt.timedelta(days=1)


def launch_metrics(fx: Fx, tfam: str, launch: dt.date | None, stage: str) -> tuple[Decimal, Decimal, Decimal]:
    """Contract rule 3 (+ stage filter: classified days only, no lifetime). Per family: money 2dp, purchases integer."""
    if launch is None or tfam.startswith("id:"):
        return Z, Z, Z
    stages = stages_of(stage)
    s, p, v = window_sums(fx, tfam, launch_start(launch), THROUGH, stages)
    if "unclassified" in stages and launch < D0:       # lifetime history carries no stage -> it is 'unclassified'
        # (all three stages together reproduce the contract's since-launch total)
        ls, lp, lv = fx.lifetime.get(tfam, (Z, Z, Z))
        s, p, v = s + ls, p + lp, v + lv
    return rnd2(s), rnd0(p), rnd2(v)


def stage_buckets(fx: Fx, tfam: str, launch: dt.date | None, window) -> dict:
    """testing/scaling/unclassified buckets for the selected window (launch or (from, to))."""
    out = {}
    if window == "launch":
        if launch is None or tfam.startswith("id:"):
            return {st: (Z, Z, Z) for st in STAGES}
        d_from, d_to = launch_start(launch), THROUGH
    else:
        d_from, d_to = window
    for st in STAGES:
        out[st] = window_sums(fx, tfam, d_from, d_to, (st,))
    if window == "launch" and launch < D0:
        ls, lp, lv = fx.lifetime.get("%s" % tfam, (Z, Z, Z))
        u = out["unclassified"]
        out["unclassified"] = (u[0] + ls, u[1] + lp, u[2] + lv)
    return out


def family_metrics(fx: Fx, tfam: str, launch: dt.date | None, window, stage: str) -> dict:
    """What the first tracker row of family `tfam` carries for the selected window + stage."""
    stages = stages_of(stage)
    if tfam.startswith("id:"):
        z = (Z, Z, Z)
        return dict(spend=Z, purchases=Z, revenue=Z, buckets={st: z for st in STAGES}, s7=Z, p7=Z, live=False)
    if window == "launch":
        s, p, v = launch_metrics(fx, tfam, launch, stage)
    else:
        s, p, v = window_sums(fx, tfam, window[0], window[1], stages)
    s7, p7, _ = window_sums(fx, tfam, THROUGH - dt.timedelta(days=6), THROUGH, STAGES)
    live = window_sums(fx, tfam, THROUGH - dt.timedelta(days=1), THROUGH, STAGES)[0] > 0
    return dict(spend=s, purchases=p, revenue=v, buckets=stage_buckets(fx, tfam, launch, window), s7=s7, p7=p7, live=live)


def tag_labels(row: dict) -> tuple[str, str, str]:
    """(cluster, icp, pain) group of a tracker row under its product's tag set."""
    t = row["tag"]
    if t is None:
        return ("(untagged)",) * 3
    if t[0] == "linemap":
        ln = next(x for x in TAXONOMY if x["code"] == t[1])
        return ln["cluster"], ln["icp"], ln["pain"]
    return t[1], t[2], t[3]


def aggregate(fx: Fx, rows: list[dict], window, stage: str) -> dict:
    """Oracle for one group of tracker rows -> the numbers behind `m`."""
    out = dict(unique=len({r["family_key"] for r in rows}), live=0, active=0, spend=Z, purchases=Z, revenue=Z, s7=Z, p7=Z,
               t=[Z, Z, Z], s=[Z, Z, Z], u=[Z, Z, Z])
    for r in rows:
        am = fx.ad_metrics[r["id"]]
        if not am["is_first_row"] or r["family_key"].startswith("id:"):
            continue
        fm = family_metrics(fx, r["family_key"], r["launch"], window, stage)
        out["spend"] += fm["spend"]
        out["purchases"] += fm["purchases"]
        out["revenue"] += fm["revenue"]
        out["s7"] += fm["s7"]
        out["p7"] += fm["p7"]
        out["live"] += 1 if fm["live"] else 0
        out["active"] += 1 if fm["spend"] > 0 else 0
        for key, st in (("t", "testing"), ("s", "scaling"), ("u", "unclassified")):
            for i in range(3):
                out[key][i] += fm["buckets"][st][i]
    return out


# ---------------------------------------------------------------------------------------------------------------------------------
# oracle: ad-level (Meta ad grain) rows
# ---------------------------------------------------------------------------------------------------------------------------------


def ad_oracle(fx: Fx, fams: dict, window, stage: str, campaign: str | None = None, unmatched_pid: int | None = None):
    """Per-Meta-ad sums for a selection. fams: tracker family key -> launch date of its first tracker row.
    window: 'launch' (per-ad launch rule) or (from, to). unmatched_pid: also include Meta families with no tracker row assigned to that product.
    Returns ({(ad_id, family): [spend, purchases, revenue]}, remainder [spend, purchases, revenue]) - the remainder holds family-grain
    ('fam:') days and Lifetime history no ad row accounts for."""
    stages = stages_of(stage)
    ads: dict = defaultdict(lambda: [Z, Z, Z])
    rem = [Z, Z, Z]

    def add(target, r):
        target[0] += r["spend"]
        target[1] += r["purchases"]
        target[2] += r["conv_value"]

    for r in fx.ad_daily:
        tfam = MAPPING[r["meta_family_key"]][0]
        if tfam is not None and tfam in fams:
            if window == "launch":
                ls = launch_start(fams[tfam])
                if ls is None or not (ls <= r["day"] <= THROUGH):
                    continue
            elif not (window[0] <= r["day"] <= window[1]):
                continue
            key_fam = tfam
        elif tfam is None and unmatched_pid is not None and fx.unmatched[r["meta_family_key"]]["product_id"] == unmatched_pid:
            if window != "launch" and not (window[0] <= r["day"] <= window[1]):
                continue
            key_fam = r["meta_family_key"]
        else:
            continue
        if stage_of_row(fx, r["campaign_id"]) not in stages:
            continue
        if campaign is not None and r["campaign_id"] != campaign:
            continue
        add(rem if r["ad_id"].startswith("fam:") else ads[(r["ad_id"], key_fam)], r)
    if window == "launch" and "unclassified" in stages:
        for tfam, launch in fams.items():
            if launch is None or launch >= D0:
                continue
            per_ad = [Z, Z, Z]
            for ad_id, (sp, pu, rv) in META_AD_LIFETIME.items():
                if MAPPING[meta_family_key(ADS[ad_id][0])][0] != tfam:
                    continue
                if campaign is not None and ADS[ad_id][1] != campaign:
                    continue
                for i, v in enumerate((sp, pu, rv)):
                    per_ad[i] += v
                    ads[(ad_id, tfam)][i] += v
            if campaign is None:
                lt = fx.lifetime.get(tfam, (Z, Z, Z))
                for i in range(3):
                    rem[i] += lt[i] - per_ad[i]
    return {k: v for k, v in ads.items() if any(v)}, rem
