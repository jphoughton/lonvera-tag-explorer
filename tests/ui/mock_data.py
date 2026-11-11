"""Data, windows and metric helpers of the Marketing metrics mock API (split out of mock_server.py; no routes live here).

One ad-level daily table (ROWS) feeds every level, so a product sums to its tags and the platforms sum to their campaigns."""
import io
import json
import math
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"

DAILY_START = date(2026, 5, 6)
DATA_THROUGH = date(2026, 11, 2)
CUTOFF = date(2026, 9, 17)
DAYS = [DAILY_START + timedelta(i) for i in range((DATA_THROUGH - DAILY_START).days + 1)]
N = len(DAYS)
IMP_START = 45
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PASSWORD = "test"
USERS = {"JoshHoughton", "Lazer"}
AUTH_DISABLED = os.environ.get("MOCK_AUTH") == "disabled"  # MOCK_AUTH=disabled: no login, every /api and /media call works without a cookie


# ----------------------------------------------------------------------------------------------- data
VB_LINES = [  # code, cluster, icp, pain, trigger
    ("L01", "Low T & Libido", "Number Optimizer", "Low Energy", "Shame"),
    ("L02", "Low T & Libido", "Number Optimizer", "Low Drive", "Frustration"),
    ("L03", "Low T & Libido", "Frank the Skeptic", "Distrust of TRT", "Distrust"),
    ("L04", "Low T & Libido", "Frank the Skeptic", "Brain Fog", "Fear"),
    ("L05", "Energy & Performance", "Weekend Lifter", "Plateau", "Pride"),
    ("L06", "Energy & Performance", "Weekend Lifter", "Recovery", "Frustration"),
    ("L07", "Energy & Performance", "Busy Provider Dad", "Afternoon Crash", "Shame"),
    ("L08", "Energy & Performance", "Busy Provider Dad", "Sleep", "Fear"),
    ("L09", "Prostate & Aging", "Prostate Worrier", "Night Bathroom Trips", "Fear"),
    ("L10", "Prostate & Aging", "Prostate Worrier", "Surgery Dodger", "Fear"),
    ("L11", "Prostate & Aging", "Won't Slow Down", "Longevity", "Desire"),
]
CLUSTER_COLORS = {"Low T & Libido": "#c0392b", "Energy & Performance": "#2e86c1", "Prostate & Aging": "#7d3c98"}
TRACKER_TAGS = {
    "beef-liver": [
        ("Hair Thinning", "Denise Thinning Hair", "Hair Loss"), ("Hair Thinning", "Denise Thinning Hair", "Ferritin Low"),
        ("Iron & B12", "Carla Low Ferritin", "Fatigue"), ("Alcohol & Liver", "Gary Drinker", "Alcohol Face"),
        ("Alcohol & Liver", "Gary Drinker", "Liver Health"), ("(none)", "(none)", "(none)"),
    ],
    "boneflex": [
        ("Joint Pain", "Eileen Floor-Proof", "Knee Pain"), ("Joint Pain", "Eileen Floor-Proof", "Morning Stiffness"),
        ("Joint Pain", "Ron Weekend Warrior", "Recovery"), ("Mobility", "Carol Daily NSAID", "NSAID Worry"),
    ],
}
PRODUCTS = [
    dict(slug="vita-bull", name="Vita Bull", tag_set="linemap", n=40, prefix=["Bull", "Brief2-2-Bull", "Lonvera-VitalBull-Static", "cedFat", "breakdown"]),
    dict(slug="beef-liver", name="Beef Liver", tag_set="tracker", n=16, prefix=["Liver", "BeefLiver-Static", "Hair"]),
    dict(slug="boneflex", name="Boneflex", tag_set="tracker", n=12, prefix=["BoneFlex", "Boneflex-Static"]),
]
STATUSES = ["Launched"] * 7 + ["Approved - Ready To Launch", "In Production", "Briefing"]
INGREDIENTS = [("Zinc", "Low testosterone signalling", "Libido, recovery"), ("Cholesterol", "Hormone substrate gap", "T production"), ("Vitamin B12", "Low energy", "Focus")]
XSS = "<img src=x onerror=window.__xss=1>Bull <b>bold</b> & <script>window.__xss=2</script>"


def cur_ads():
    """ads in play for this request: every ad, or only ACTIVE ones with ?active=1"""
    if getattr(g, "active", False):
        return [a for a in ADS if a["status"] == "ACTIVE"]
    return ADS


def stage_of(name):
    return "testing" if "test" in name.lower() else "scaling"


def build():
    rng = random.Random(7)
    campaigns = {}
    for p in PRODUCTS:
        s = p["slug"]
        for i, nm in enumerate([f"{p['name'].replace(' ', '')}_US_Scaling_CC_{i}" for i in range(1, 4)] + [f"{p['name'].replace(' ', '')}_WW_Test_Hooks_{i}" for i in range(1, 4)]):
            cid = f"1202{(sum(map(ord, s)) * 100003 + i * 7919) % 10**9:09d}"
            campaigns[cid] = dict(campaign_id=cid, product=s, name=nm, account=rng.choice(["Main", "BackUp"]), stage=stage_of(nm))
    camp_by_prod = {p["slug"]: [c for c in campaigns.values() if c["product"] == p["slug"]] for p in PRODUCTS}

    rows, fams, ads = [], [], []
    rid = 1000
    asset = 0

    def traffic_for(rng, daily):
        """per day (impressions or None, link clicks): impressions are unknown before IMP_START (API pull range)"""
        cpc = rng.uniform(1.4, 3.6)
        ctr = rng.uniform(0.007, 0.02)
        out = []
        for i, d in enumerate(daily):
            clk = int(round(d[0] / cpc * rng.uniform(0.85, 1.15))) if d[0] > 0 else 0
            imp = int(round(clk / ctr * rng.uniform(0.9, 1.1))) if (i >= IMP_START and d[0] > 0) else (0 if i >= IMP_START else None)
            out.append((imp, clk))
        return out, (round(1 / cpc, 6), ctr)

    def daily_for(rng, launch, live, base):
        """list of (spend, purchases, revenue) per day"""
        li = max(0, (launch - DAILY_START).days) if launch else 0
        end = N - 1 if live else min(N - 1, li + rng.randint(10, 120))
        cpa = rng.uniform(62, 135)
        aov = rng.uniform(58, 98)
        out = []
        for i in range(N):
            if i < li or i > end:
                out.append((0.0, 0, 0.0))
                continue
            age = i - li
            shape = 0.5 + min(age, 20) / 20 - max(0, (age - 80) / 200)
            sp = max(0.0, base * shape * rng.uniform(0.55, 1.4))
            pu = int(round(sp / cpa * rng.uniform(0.6, 1.4)))
            out.append((round(sp, 2), pu, round(pu * aov * rng.uniform(0.9, 1.1), 2)))
        return out

    for p in PRODUCTS:
        s = p["slug"]
        lines = VB_LINES if p["tag_set"] == "linemap" else None
        ttags = TRACKER_TAGS.get(s)
        for k in range(p["n"]):
            pre = rng.choice(p["prefix"])
            name = f"{pre}-{rng.randint(100, 999)}-Hook{rng.randint(1, 4)}" if pre in ("Bull", "Liver", "BoneFlex") else f"{pre}-{rng.randint(2, 40)}.{rng.randint(1, 9)}"
            if k == 0:
                name = "Brief2-2-Bull-398" if s == "vita-bull" else name
            if k == 1 and s == "vita-bull":
                name = "Bull-526-Hook2"
            if k == 2 and s == "vita-bull":
                name = "XSS-<b>test</b>-family"
            key = name.lower()
            if any(f["key"] == key for f in fams):
                name = name + f"-{k}"
                key = name.lower()
            status = STATUSES[k % len(STATUSES)] if k > 5 else "Launched"
            launched = status == "Launched"
            launch = date(2026, 3, 1) + timedelta(days=rng.randint(0, 235)) if launched else None
            if launched and k < 6:
                launch = DAILY_START + timedelta(days=rng.randint(20, 150))
            live = launched and rng.random() < 0.38
            fam = dict(key=key, product=s, display=name, status=status, ad_type=rng.choice(["VID", "IMG"]), launch=launch, live=live,
                       thumb=None if rng.random() < 0.12 else f"a{asset + 1}", rows=[], confidence=None)
            asset += 1
            nrows = 2 if (k % 9 == 4 and launched) else 1
            picks = []
            for r_i in range(nrows):
                if lines:
                    if k in (38, 39):
                        ln = None
                    else:
                        ln = rng.choice(lines)
                        while r_i == 1 and ln[0] == picks[0][0]:
                            ln = rng.choice(lines)
                    picks.append(ln)
                else:
                    picks.append(rng.choice(ttags))
            for r_i, pk in enumerate(picks):
                rid += 1
                conf = rng.choices(["high", "medium", "low"], [0.2, 0.35, 0.45])[0]
                method = rng.choice(["image", "video: thumbnail + 4 frames + audio transcript", "text only", "thumbnail: no audio"])
                row = dict(id=rid, fam=key, product=s, ordinal=r_i, concept_id="0510" if (s == "vita-bull" and k == 5 and r_i == 0) else (name if r_i == 0 else f"{name} - Copy {r_i + 1}"),
                           sheet_row=1500 + rid, conf=conf, method=method)
                if lines:
                    row["line"] = pk
                    row["cluster"], row["icp"], row["pain"] = (pk[1], pk[2], pk[3]) if pk else ("(untagged)",) * 3
                else:
                    row["line"] = None
                    row["cluster"], row["icp"], row["pain"] = pk
                rows.append(row)
                fam["rows"].append(row)
            fam["confidence"] = min((r["conf"] for r in fam["rows"]), key={"low": 0, "medium": 1, "high": 2}.get)
            fams.append(fam)

            # Meta ads (only launched families spend)
            if launched:
                n_ads = rng.randint(1, 4)
                base = rng.choice([8, 20, 60, 140, 300]) * (1.8 if live else 1)
                for a in range(n_ads):
                    cmp = rng.choice(camp_by_prod[s])
                    ad = dict(ad_id=f"2385{rng.randint(10**10, 10**11):d}", fam=key, product=s, name=name if a == 0 else f"{name} - Copy {a + 1}",
                              campaign_id=cmp["campaign_id"], adset=rng.choice(["US_Scaling_PastWinners", "WW_GEN_Test_Hooks", "US_Broad_1DC"]),
                              status=rng.choice(["ACTIVE", "ACTIVE", "PAUSED"]), created=(launch + timedelta(days=a * 3)).isoformat(),
                              daily=daily_for(rng, launch + timedelta(days=a * 3), live and a < 2, base / n_ads * rng.uniform(0.6, 1.5)),
                              life=(0, 0, 0), detail_cut=0)
                    if launch < DAILY_START:
                        ls = rng.uniform(2000, 40000) / n_ads
                        ad["life"] = (round(ls, 2), int(ls / 90), round(ls * rng.uniform(0.7, 1.1), 2))
                    ad["tr"], (clk_per_dollar, ad_ctr) = traffic_for(rng, ad["daily"])
                    lclk = int(ad["life"][0] * clk_per_dollar)
                    ad["life_tr"] = (int(lclk / ad_ctr), lclk, ad["life"][0])
                    ads.append(ad)
        # fixed zero-spend family for the "with spend only" test
        # unmatched Meta families (no tracker row)
        for u in range(6):
            nm = f"Unmatched-{p['name'].replace(' ', '')}-{u + 1}"
            cmp = rng.choice(camp_by_prod[s])
            live = u < 2
            ud = daily_for(rng, date(2026, 8, 20), live, rng.choice([5, 15, 40]))
            utr, _ = traffic_for(rng, ud)
            ads.append(dict(ad_id=f"2386{rng.randint(10**10, 10**11):d}", fam=None, product=s, meta_family=nm, name=nm, campaign_id=cmp["campaign_id"],
                            adset="US_Broad_1DC", status="ACTIVE", created="2026-09-01",
                            daily=ud, tr=utr, life=(0, 0, 0), life_tr=(0, 0, 0), detail_cut=0))
    # first live vita-bull family has ad-level detail only for the last 60 days
    fam_by_key = {f["key"]: f for f in fams}
    f = sorted((x for x in fams if x["product"] == "vita-bull" and x["live"] and x["launch"] >= DAILY_START + timedelta(days=10)), key=lambda x: x["launch"])[0]
    for ad in ads:
        if ad["fam"] == f["key"]:
            ad["detail_cut"] = N - 60
    f["has_remainder"] = True
    # copy / content per row
    for r in rows:
        r["emotion"] = random.Random(r["id"]).choice(["Shame", "Fear", "Frustration", "Distrust", "Pride"])
        r["ingredient"] = random.Random(r["id"] + 1).choice(INGREDIENTS)[0]
    return campaigns, rows, fams, ads, fam_by_key


CAMPAIGNS, ROWS, FAMS, ADS, FAM_BY_KEY = build()
ROW_BY_ID = {r["id"]: r for r in ROWS}

# hooks (ad_hooks): two thirds of the tracker rows have one; the others show the empty cell / no hook card. Own deterministic choice, no shared RNG.
HOOKS = [  # hook, spoken, on_screen, source, confidence, note
    ("Your doctor will not tell you this about testosterone", "Your doctor will not tell you this about testosterone", None, "spoken", "high", None),
    ("STOP guessing your zinc levels after 40", None, "STOP guessing your zinc levels after 40", "on_screen", "medium", "no audio, text overlay only"),
    ("I lifted for twenty years before I learned this about recovery and why my numbers never moved at all", "I lifted for twenty years before I learned this", "RECOVERY MYTH", "both", "high", None),
    ("Organ supplements: the one thing nobody checks", None, None, "copy", "low", "image unreadable, hook taken from the primary text"),
]


def hook_of(row):
    """the hook block of a tracker row (the ad-detail shape) or None"""
    if FAM_BY_KEY[row["fam"]]["display"].startswith("XSS"):
        return dict(hook=XSS, spoken=None, on_screen=None, hook_source="copy", confidence="low", note=None, version="hooks-v1-2026-11-05")
    if row["id"] % 3 == 0:
        return None
    hook, spoken, on_screen, src, conf, note = HOOKS[row["id"] % len(HOOKS)]
    return dict(hook=hook, spoken=spoken, on_screen=on_screen, hook_source=src, confidence=conf, note=note, version="hooks-v1-2026-11-05")


def hook_fields(row):
    """the three flat fields a /families row carries for its primary tracker row"""
    k = hook_of(row)
    return dict(hook=k["hook"] if k else None, hook_source=k["hook_source"] if k else None, hook_confidence=k["confidence"] if k else None)


ASSETS = {f["thumb"] for f in FAMS if f["thumb"]}


# ----------------------------------------------------------------------------------------------- windows
class Bad(Exception):
    pass


def lab(d):
    return f"{MON[d.month - 1]} {d.day}"


STAGE_NOTE = "Testing / Scaling follow the campaign name; days without campaign data count as Unclassified and only appear when Unclassified is included."


def active_info(info, stage=None):
    """stage_note when the stage filter is not both Testing and Scaling; active=1: what the numbers mean and how many ads are active"""
    st = stage if stage is not None else info.get("_stage")
    info = dict(info)
    info.pop("_stage", None)
    if st is not None and not {"testing", "scaling"} <= set(st):
        info["stage_note"] = STAGE_NOTE
    if not getattr(g, "active", False):
        return info
    from flask import request as _rq
    slug = (_rq.view_args or {}).get("slug")
    n = sum(1 for a in ADS if a["status"] == "ACTIVE" and (slug is None or a["product"] == slug))
    note = "Active ads only: creatives with at least one ACTIVE ad; numbers are the sums over their active ads (current status applied to the whole window; status as of Nov 2, 10:12 PM)."
    info["active_ads"] = n
    info["note"] = (info["note"] + " " + note) if info.get("note") else note
    return info


def build_window(fd, td, kind, stage):
    """clamp [fd, td] to the daily span and build the window dict + window_info"""
    cf, ct = max(fd, DAILY_START), min(td, DATA_THROUGH)
    if cf > ct:
        cf = ct = DATA_THROUGH if fd > DATA_THROUGH else DAILY_START
    clamped = (cf, ct) != (fd, td)
    n = (ct - cf).days + 1
    i0_ = (cf - DAILY_START).days
    notes = []
    if clamped:
        notes.append(f"Daily data runs {DAILY_START.isoformat()} to {DATA_THROUGH.isoformat()}; range clamped.")
    if i0_ < IMP_START:
        notes.append(f"Impressions are available from {lab(DAYS[IMP_START])}; CTR, CPM and CPC cover those days only.")
    info = dict(kind=kind, label=f"{lab(cf)} · 1 day" if n == 1 else f"{lab(cf)} – {lab(ct)} · {n} days",
                **{"from": cf.isoformat(), "to": ct.isoformat()}, days=n, clamped=clamped,
                note=" ".join(notes) or None,
                daily_start=DAILY_START.isoformat(), data_through=DATA_THROUGH.isoformat())
    i0, i1 = (cf - DAILY_START).days, (ct - DAILY_START).days
    return dict(info=active_info(info, stage), i0=i0, i1=i1, stage=stage, key=(i0, i1, stage, getattr(g, "active", False)), launch=False)


def trailing(w, stage):
    if w == "mtd":
        return build_window(DATA_THROUGH.replace(day=1), DATA_THROUGH, "mtd", stage)
    m = re.fullmatch(r"(\d+)d", w or "")
    if not m or int(m.group(1)) not in (1, 7, 14, 30, 60, 90, 180):
        raise Bad("bad window")
    n = int(m.group(1))
    return build_window(DATA_THROUGH - timedelta(days=n - 1), DATA_THROUGH, "trailing", stage)


ALL_STAGES = ("testing", "scaling", "unclassified")


def parse_stage(args):
    """stage = comma-separated include list (omitted / 'all' = all three); empty or unknown -> 400"""
    raw = args.get("stage")
    if raw is None or raw == "all":
        return frozenset(ALL_STAGES)
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    if not parts:
        raise Bad("at least one stage must be included")
    if any(x not in ALL_STAGES for x in parts):
        raise Bad("bad stage")
    return frozenset(parts)


def parse_win(args):
    stage = parse_stage(args)
    f, t = args.get("from"), args.get("to")
    if f or t:
        if not (f and t):
            raise Bad("from and to are both required")
        try:
            fd, td = date.fromisoformat(f), date.fromisoformat(t)
        except ValueError:
            raise Bad("bad date")
        if fd > td:
            raise Bad("from is after to")
        if td < DAILY_START or fd > DATA_THROUGH:   # wholly outside the held data: the real API answers 400 too
            raise Bad(f"no daily data in that range (data covers {lab(DAILY_START)}, {DAILY_START.year} – {lab(DATA_THROUGH)}, {DATA_THROUGH.year})")
        return build_window(fd, td, "custom", stage)
    w = args.get("window", "launch")
    if w == "launch":
        info = dict(kind="launch", label="Since launch", **{"from": None, "to": None}, days=None, clamped=False,
                    note="With a stage filter only days with a campaign (classified days) count." if len(stage) < 3 else None,
                    daily_start=DAILY_START.isoformat(), data_through=DATA_THROUGH.isoformat())
        return dict(info=active_info(info, stage), i0=None, i1=None, stage=stage, key=("launch", stage, getattr(g, "active", False)), launch=True)
    return trailing(w, stage)


def parse_cmp(args, win):
    """window B from compare=prev|Nd|mtd or cfrom/cto; None when comparison is off"""
    c, cf, ct = args.get("compare"), args.get("cfrom"), args.get("cto")
    if not (c or cf or ct):
        return None
    if win["launch"]:
        raise Bad("compare needs a date window")
    stage = win["stage"]
    if cf or ct:
        if not (cf and ct):
            raise Bad("cfrom and cto are both required")
        try:
            fd, td = date.fromisoformat(cf), date.fromisoformat(ct)
        except ValueError:
            raise Bad("bad date")
        if fd > td:
            raise Bad("cfrom is after cto")
        return build_window(fd, td, "custom", stage)
    if c == "prev":
        n = win["info"]["days"]
        a0 = date.fromisoformat(win["info"]["from"])
        return build_window(a0 - timedelta(days=n), a0 - timedelta(days=1), "prev", stage)
    return trailing(c, stage)


def cinfo():
    return g.cmp["info"] if getattr(g, "cmp", None) else None


# ----------------------------------------------------------------------------------------------- metrics
def sums(daily, i0, i1):
    sp = pu = rv = 0.0
    for i in range(max(0, i0), min(N - 1, i1) + 1):
        d = daily[i]
        sp += d[0]; pu += d[1]; rv += d[2]
    return sp, pu, rv


def ad_stage(ad):
    return CAMPAIGNS[ad["campaign_id"]]["stage"]


def launch_idx(launch):
    return (launch - DAILY_START).days if launch else None


IMP_START = 45  # impressions exist only from this daily index on (Jun 20): earlier days are "unknown"
UNC_DAYS = 30  # the first 30 daily rows come from the sheet backfill: no campaign, so "unclassified"


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def day_split(daily, lo, hi, stg):
    out = {"testing": (0, 0, 0), "scaling": (0, 0, 0), "unclassified": (0, 0, 0)}
    if hi < lo:
        return out
    u_hi = min(hi, UNC_DAYS - 1)
    if lo <= u_hi:
        out["unclassified"] = sums(daily, lo, u_hi)
    c_lo = max(lo, UNC_DAYS)
    if c_lo <= hi:
        out[stg] = sums(daily, c_lo, hi)
    return out


def ad_metrics(ad, win, launch, cut=True):
    """(spend, purchases, revenue) for one ad in the window, by stage: dict stage -> tuple"""
    stg = ad_stage(ad)
    daily = ad["daily"]
    start = ad["detail_cut"] if cut else 0
    if win["launch"]:
        if launch is None and ad["fam"] is not None:
            return {"testing": (0, 0, 0), "scaling": (0, 0, 0), "unclassified": (0, 0, 0)}
        if launch is None or launch >= DAILY_START:
            li = 0 if launch is None else (launch - DAILY_START).days
            return day_split(daily, max(li, start), N - 1, stg)
        ci = (CUTOFF - DAILY_START).days
        out = day_split(daily, max(ci + 1, start), N - 1, stg)
        out["unclassified"] = _add(out["unclassified"], ad["life"])
        return out
    return day_split(daily, max(win["i0"], start), win["i1"], stg)


def ad_traffic(ad, win, launch, cut=True):
    """(impressions, link clicks, clicks on days with impressions, spend on days with impressions) mirroring ad_metrics' day selection"""
    tr, sp = ad["tr"], ad["daily"]
    start = ad["detail_cut"] if cut else 0
    life = False
    if win["launch"]:
        if launch is None and ad["fam"] is not None:
            return (0, 0, 0, 0)
        if launch is None or launch >= DAILY_START:
            li = 0 if launch is None else (launch - DAILY_START).days
            lo, hi = max(li, start), N - 1
        else:
            lo, hi, life = max((CUTOFF - DAILY_START).days + 1, start), N - 1, True
    else:
        lo, hi = max(win["i0"], start), win["i1"]
    stg = ad_stage(ad)
    imp = clk = clk_tr = sp_tr = 0
    for i in range(max(lo, 0), min(hi, N - 1) + 1):
        if ("unclassified" if i < UNC_DAYS else stg) not in win["stage"]:
            continue
        im, ck = tr[i]
        clk += ck
        if im is not None:
            imp += im; clk_tr += ck; sp_tr += sp[i][0]
    if life and "unclassified" in win["stage"]:
        li_imp, li_clk, li_sp = ad["life_tr"]
        imp += li_imp; clk += li_clk; clk_tr += li_clk; sp_tr += li_sp
    return imp, clk, clk_tr, sp_tr


_FT = {}


def fam_traffic(fam_key, win):
    ck = (fam_key, win["key"])
    if ck not in _FT:
        t = [0, 0, 0, 0]
        for ad in cur_ads():
            if ad["fam"] == fam_key:
                for j, v in enumerate(ad_traffic(ad, win, FAM_BY_KEY[fam_key]["launch"], cut=False)):
                    t[j] += v
        _FT[ck] = tuple(t)
    return _FT[ck]


TRAFFIC_KEYS = ("impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")


def traffic_fields(sp, pu, imp, clk, clk_tr, sp_tr):
    return dict(impressions=int(imp) if imp else None, link_clicks=int(clk),
                ctr=round(clk_tr / imp, 6) if imp else None, cpc=round(sp / clk, 4) if clk else None,
                cpm=round(sp_tr / imp * 1000, 4) if imp else None, conv_rate=round(pu / clk, 6) if clk else None)


def metrics_of(split, win):
    """apply the stage include list to a by-stage split -> spend, purchases, revenue"""
    keys = [k for k in ALL_STAGES if k in win["stage"]]
    sp = sum(split[k][0] for k in keys); pu = sum(split[k][1] for k in keys); rv = sum(split[k][2] for k in keys)
    return sp, pu, rv


def _roas(s, r):
    return round(r / s, 4) if s > 0 else None


def _cpa(s, p):
    return round(s / p, 2) if p > 0 else None


_FM = {}


def fam_split(fam_key, win, ads_filter=None):
    """by-stage (spend, purchases, revenue) for a tracker family over its ads"""
    ck = (fam_key, win["key"][:2] if not win["launch"] else "launch", getattr(g, "active", False))
    if ck in _FM:
        return _FM[ck]
    fam = FAM_BY_KEY[fam_key]
    tot = {"testing": [0, 0, 0], "scaling": [0, 0, 0], "unclassified": [0, 0, 0]}
    last7 = [0, 0, 0]
    live = False
    for ad in cur_ads():
        if ad["fam"] != fam_key:
            continue
        for st, v in ad_metrics(ad, win, fam["launch"], cut=False).items():
            for j in range(3):
                tot[st][j] += v[j]
        a7 = sums(ad["daily"], N - 7, N - 1)
        for j in range(3):
            last7[j] += a7[j]
        if ad["daily"][N - 1][0] > 0 or ad["daily"][N - 2][0] > 0:
            live = True
    _FM[ck] = (tot, last7, live)
    return _FM[ck]


KEYS5 = ("spend", "purchases", "revenue", "roas", "cpa")


def per_day(obj, win):
    n = win["info"]["days"] if win and not win.get("launch") else None
    return {f"{k}_per_day": (round(obj[k] / n, 4) if n else None) for k in ("spend", "purchases", "revenue")}


def delta_block(a, b, wa, wb):
    da, db = wa["info"]["days"], wb["info"]["days"]
    d = {}
    for k in KEYS5:
        av, bv = a.get(k), b.get(k)
        d[k] = None if av is None or bv is None else round(av - bv, 4)
        d[k + "_pct"] = None if av is None or not bv else round((av - bv) / bv, 4)
    for k in ("spend", "purchases", "revenue"):
        ap, bp = a[k] / da, b[k] / db
        d[k + "_per_day_pct"] = None if not bp else round((ap - bp) / bp, 4)
    for k in TRAFFIC_KEYS:
        av, bv = a.get(k), b.get(k)
        d[k] = None if av is None or bv is None else round(av - bv, 6)
        d[k + "_pct"] = None if av is None or not bv else round((av - bv) / bv, 4)
    d["ctr_pp"] = None if d["ctr"] is None else round(d["ctr"] * 100, 4)
    d["conv_rate_pp"] = None if d["conv_rate"] is None else round(d["conv_rate"] * 100, 4)
    return d


def cmp_block(b, wb):
    out = {k: b.get(k) for k in KEYS5 + ("active_ads",) + TRAFFIC_KEYS if k in b}
    out.update(per_day(b, wb))
    return out


def decorate(obj, objb, win):
    """add per-day fields (always) and cmp/delta (when comparing) to a metrics object or flat row"""
    obj.update(per_day(obj, win))
    if getattr(g, "cmp", None) and objb is not None:
        obj["cmp"] = cmp_block(objb, g.cmp)
        obj["delta"] = delta_block(obj, objb, win, g.cmp)
    return obj


def shares(objs, tot):
    """spend_share of each metrics dict / flat row vs the response total (and for B + delta in pp when comparing)"""
    ts = tot["spend"]
    tb = tot["cmp"]["spend"] if tot.get("cmp") else None
    for o in objs:
        o["spend_share"] = round(o["spend"] / ts, 6) if ts else None
        if o.get("cmp"):
            o["cmp"]["spend_share"] = round(o["cmp"]["spend"] / tb, 6) if tb else None
            a, b = o["spend_share"], o["cmp"]["spend_share"]
            o["delta"]["spend_share_pp"] = None if a is None or b is None else round((a - b) * 100, 4)
    tot["spend_share"] = 1.0
    if tot.get("cmp"):
        tot["cmp"]["spend_share"] = 1.0
        tot["delta"]["spend_share_pp"] = 0.0
    return objs


def make_m(items, win, unique_ads=None):
    m = _make_m(items, win, unique_ads)
    mb = _make_m(items, g.cmp, unique_ads) if getattr(g, "cmp", None) else None
    return decorate(m, mb, win)


def _make_m(items, win, unique_ads=None):
    """items: [(fam, counted_here)] -> metrics object"""
    m_sp = m_pu = m_rv = 0.0
    sp7 = pu7 = 0.0
    live = active = 0
    split_tot = {"testing": [0.0, 0, 0.0], "scaling": [0.0, 0, 0.0], "unclassified": [0.0, 0, 0.0]}
    for fam, counted in items:
        if not counted:
            continue
        split, l7, lv = fam_split(fam["key"], win)
        sp, pu, rv = metrics_of(split, win)
        m_sp += sp; m_pu += pu; m_rv += rv
        sp7 += l7[0]; pu7 += l7[1]
        live += 1 if lv else 0
        active += 1 if sp > 0 else 0
        for k in split_tot:
            for j in range(3):
                split_tot[k][j] += split[k][j]
    m_sp, m_rv = round(m_sp, 2), round(m_rv, 2)
    t_imp = t_clk = t_clk_tr = t_sp_tr = 0
    for fam, counted in items:
        if counted:
            ft = fam_traffic(fam["key"], win)
            t_imp += ft[0]; t_clk += ft[1]; t_clk_tr += ft[2]; t_sp_tr += ft[3]
    traffic = traffic_fields(m_sp, m_pu, t_imp, t_clk, t_clk_tr, t_sp_tr)
    cls = split_tot["testing"][0] + split_tot["scaling"][0]
    stage_obj = {k: dict(spend=round(v[0], 2), purchases=int(round(v[1])), revenue=round(v[2], 2), roas=_roas(v[0], v[2]), cpa=_cpa(v[0], v[1])) for k, v in split_tot.items() if k != "unclassified"}
    stage_obj["unclassified"] = dict(spend=round(split_tot["unclassified"][0], 2), purchases=int(round(split_tot["unclassified"][1])), revenue=round(split_tot["unclassified"][2], 2))
    return dict(unique_ads=unique_ads if unique_ads is not None else len({f["key"] for f, _ in items}), live_now=live, spend=m_sp, purchases=int(round(m_pu)), revenue=m_rv,
                roas=_roas(m_sp, m_rv), cpa=_cpa(m_sp, m_pu), spend_7d=round(sp7, 2), cpa_7d=_cpa(sp7, pu7), active_ads=active,
                testing_pct=round(split_tot["testing"][0] / cls, 4) if cls > 0 else None, stage=stage_obj, **traffic)


def prod_items(slug):
    """every (fam, row, counted_here) of a product"""
    out = []
    live = {a["fam"] for a in cur_ads()} if getattr(g, "active", False) else None
    for f in FAMS:
        if f["product"] != slug:
            continue
        if live is not None and f["key"] not in live:
            continue  # active only: a creative needs at least one active ad
        for r in f["rows"]:
            out.append((f, r, r["ordinal"] == 0))
    return out


def group_of(prod, row):
    return (row["cluster"], row["icp"], row["pain"])


def filt(items, args):
    c, i, p = args.get("cluster"), args.get("icp"), args.get("pain")
    return [(f, r, ch) for f, r, ch in items
            if (c is None or r["cluster"] == c) and (i is None or r["icp"] == i) and (p is None or r["pain"] == p)]


def items_for_m(entries):
    """entries (fam,row,counted) -> items for make_m (unique families; counted if any counted row here)"""
    seen = {}
    for f, r, ch in entries:
        seen[f["key"]] = seen.get(f["key"], False) or ch
    return [(FAM_BY_KEY[k], v) for k, v in seen.items()]


def prod_meta(slug):
    return next(p for p in PRODUCTS if p["slug"] == slug)


def product_json(p):
    tl = "Line-mapped tags" if p["tag_set"] == "linemap" else "Tracker tags — not line-mapped yet"
    return dict(slug=p["slug"], name=p["name"], tag_set=p["tag_set"], tag_label=tl)


def unmatched_rows(slug, win):
    by = {}
    for ad in cur_ads():
        if ad["product"] != slug or ad["fam"] is not None:
            continue
        e = by.setdefault(ad["meta_family"], dict(meta_family=ad["meta_family"], spend=0.0, purchases=0, revenue=0.0, spend_7d=0.0, live=False, _b=[0.0, 0, 0.0], _t=[0, 0, 0, 0], _tb=[0, 0, 0, 0]))
        tw = win if not win["launch"] else dict(win, launch=False, i0=0, i1=N - 1, key=("all", win["stage"]))
        for j, v in enumerate(ad_traffic(ad, tw, None)):
            e["_t"][j] += v
        if getattr(g, "cmp", None):
            for j, v in enumerate(ad_traffic(ad, g.cmp, None)):
                e["_tb"][j] += v
        if win["launch"] or win["i0"] is None:
            sp, pu, rv = sums(ad["daily"], 0, N - 1)
        else:
            sp, pu, rv = sums(ad["daily"], win["i0"], win["i1"])
        if getattr(g, "cmp", None):
            bs = sums(ad["daily"], g.cmp["i0"], g.cmp["i1"])
            for j in range(3):
                e["_b"][j] += bs[j]
        e["spend"] += sp; e["purchases"] += pu; e["revenue"] += rv
        e["spend_7d"] += sums(ad["daily"], N - 7, N - 1)[0]
        e["live"] = e["live"] or ad["daily"][N - 1][0] > 0 or ad["daily"][N - 2][0] > 0
    rows = []
    for e in by.values():
        e["spend"] = round(e["spend"], 2); e["revenue"] = round(e["revenue"], 2); e["spend_7d"] = round(e["spend_7d"], 2)
        e["purchases"] = int(round(e["purchases"]))
        e["roas"] = _roas(e["spend"], e["revenue"]); e["cpa"] = _cpa(e["spend"], e["purchases"])
        b = e.pop("_b")
        t, tb_ = e.pop("_t"), e.pop("_tb")
        e.update(traffic_fields(e["spend"], e["purchases"], *t))
        eb = dict(spend=round(b[0], 2), purchases=int(round(b[1])), revenue=round(b[2], 2), roas=_roas(b[0], b[2]), cpa=_cpa(b[0], b[1]), **traffic_fields(b[0], b[1], *tb_))
        decorate(e, eb if getattr(g, "cmp", None) else None, win if not win["launch"] else None)
        rows.append(e)
    rows.sort(key=lambda r: -r["spend"])
    return rows



def unmatched_total_m(win):
    """the 'Not on the tracker' row of the landing page: every product's unmatched Meta spend, as one metrics object"""
    rows = []
    for pr in PRODUCTS:
        rows += unmatched_rows(pr["slug"], win)
    tot = total_of(rows, win)
    m = {k: v for k, v in tot.items() if k != "spend_7d"}
    m["unique_ads"] = len(rows)
    m["live_now"] = sum(1 for r in rows if r["live"])
    stage = {"testing": [0.0, 0, 0.0], "scaling": [0.0, 0, 0.0], "unclassified": [0.0, 0, 0.0]}
    for ad in cur_ads():
        if ad["fam"] is None:
            for st, v in ad_metrics(ad, win, None).items():
                for j in range(3):
                    stage[st][j] += v[j]
    m["stage"] = {k: dict(spend=round(v[0], 2), purchases=int(round(v[1])), revenue=round(v[2], 2)) for k, v in stage.items()}
    cls = stage["testing"][0] + stage["scaling"][0]
    m["testing_pct"] = round(stage["testing"][0] / cls, 4) if cls > 0 else None
    return m


def _comb(a, b, ka, kb=None):
    return (a.get(ka) or 0) + ((b.get(kb or ka)) or 0)


def merge_m(a, b, win):
    """a + b as one metrics object (ratios recomputed from the sums); also for the compare block"""
    def one(x, y, win_days=None):
        sp, pu, rv = _comb(x, y, "spend"), _comb(x, y, "purchases"), _comb(x, y, "revenue")
        imp = (x.get("impressions") or 0) + (y.get("impressions") or 0)
        clk = (x.get("link_clicks") or 0) + (y.get("link_clicks") or 0)
        ctr_n = (x.get("ctr") or 0) * (x.get("impressions") or 0) + (y.get("ctr") or 0) * (y.get("impressions") or 0)
        cpm_n = (x.get("cpm") or 0) * (x.get("impressions") or 0) + (y.get("cpm") or 0) * (y.get("impressions") or 0)
        return dict(spend=round(sp, 2), purchases=int(pu), revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu), impressions=int(imp) if imp else None,
                    link_clicks=int(clk), ctr=round(ctr_n / imp, 6) if imp else None, cpc=round(sp / clk, 4) if clk else None,
                    cpm=round(cpm_n / imp, 4) if imp else None, conv_rate=round(pu / clk, 6) if clk else None)
    out = one(a, b)
    out["unique_ads"] = (a.get("unique_ads") or 0) + (b.get("unique_ads") or 0)
    out["live_now"] = (a.get("live_now") or 0) + (b.get("live_now") or 0)
    out["stage"] = {k: dict(spend=round(((a.get("stage") or {}).get(k) or {}).get("spend", 0) + ((b.get("stage") or {}).get(k) or {}).get("spend", 0), 2)) for k in ("testing", "scaling", "unclassified")}
    t, sc = out["stage"]["testing"]["spend"], out["stage"]["scaling"]["spend"]
    out["testing_pct"] = round(t / (t + sc), 4) if t + sc > 0 else None
    outb = one(a["cmp"], b["cmp"]) if a.get("cmp") and b.get("cmp") else None
    return decorate(out, outb, win if not win["launch"] else None)


LEVEL_NAMES = ("cluster", "icp", "pain")


def rollup_rows(slug, level, flt, win):
    """one row per label at `level` under the filters flt (cluster/icp/pain); returns (rows, filtered entries)"""
    p = prod_meta(slug)
    ents = filt(prod_items(slug), flt)
    idx = LEVEL_NAMES.index(level)
    if level == "icp" and not flt.get("cluster"):
        raise Bad("icp requires cluster")
    if level == "pain" and not (flt.get("cluster") and flt.get("icp")):
        raise Bad("pain requires cluster and icp")
    groups = {}
    for f, r, ch in ents:
        groups.setdefault(group_of(p, r)[idx], []).append((f, r, ch))
    order = {}
    if p["tag_set"] == "linemap":
        for n, ln in enumerate(VB_LINES):
            order.setdefault((ln[1], ln[2], ln[3])[idx], n)
    rows = []
    for label, es in groups.items():
        m = make_m(items_for_m(es), win)
        if level == "pain":
            child = len({f["key"] for f, _, _ in es})
        else:
            child = len({group_of(p, r)[idx + 1] for _, r, _ in es})
        rows.append(dict(label=label, cluster=es[0][1]["cluster"], icp=es[0][1]["icp"] if idx >= 1 else None, pain=es[0][1]["pain"] if idx >= 2 else None,
                         color=CLUSTER_COLORS.get(es[0][1]["cluster"]) if p["tag_set"] == "linemap" else None,
                         line_code=es[0][1]["line"][0] if level == "pain" and es[0][1]["line"] else None,
                         trigger=es[0][1]["line"][4] if level == "pain" and es[0][1]["line"] else None, child_count=child, m=m,
                         _fams=[f["key"] for f, _, ch in es if ch]))
    if p["tag_set"] == "linemap":
        rows.sort(key=lambda r: (r["label"] == "(untagged)", order.get(r["label"], 99)))
    else:
        rows.sort(key=lambda r: (r["label"] in ("(untagged)", "(none)"), -r["m"]["spend"]))
    return rows, ents


def ad_counts(fam_key):
    ads_f = [a for a in cur_ads() if a["fam"] == fam_key]
    return len(ads_f), sum(1 for a in ads_f if a["daily"][N - 1][0] > 0 or a["daily"][N - 2][0] > 0)


def family_rows(slug, flt, win):
    p = prod_meta(slug)
    ents = filt(prod_items(slug), flt)
    groups = {}
    for f, r, ch in ents:
        groups.setdefault((f["key"], group_of(p, r)), []).append((f, r, ch))
    rows = []
    for (key, grp), es in groups.items():
        f = FAM_BY_KEY[key]
        counted = any(ch for _, _, ch in es)
        m = make_m([(f, counted)], win, unique_ads=1)
        rows.append(dict(family_key=key, display=f["display"], ad_ids=[r["id"] for _, r, _ in es], primary_id=es[0][1]["id"], group=grp,
                         concept_ids=[r["concept_id"] for _, r, _ in es], n_rows=len(f["rows"]), ad_type=f["ad_type"], status=f["status"],
                         launch_date=f["launch"].isoformat() if f["launch"] else None, thumb_asset_id=f["thumb"],
                         confidence=min((r["conf"] for _, r, _ in es), key={"low": 0, "medium": 1, "high": 2}.get) if p["tag_set"] == "linemap" else None,
                         method=es[0][1]["method"] if p["tag_set"] == "linemap" else None, counted_here=counted, m=m,
                         ads_count=ad_counts(key)[0], ads_live=ad_counts(key)[1], _fams=[key] if counted else [], **hook_fields(es[0][1])))
    rows.sort(key=lambda r: -r["m"]["spend"])
    return rows, ents


G_CAMPS = [("Standard Shopping | All Products | US | tROAS 190% | Branded | Hot", "Shopping", "ENABLED"), ("Standard Shopping | Bestseller Products | US", "Shopping", "PAUSED"),
           ("Brand Search | US | Hot | tROAS 160%", "Search", "ENABLED"), ("PMax | Beef Liver Products | Broad | US", "Performance Max", "ENABLED"),
           ("DG15 | Vital Bull | Video 9x16 | US", "Demand Gen", "PAUSED"), ("Prospecting | Top 3 Products | US | Cold", "Other", "PAUSED")]


def g_day(ci, i):
    rnd = random.Random(ci * 7919 + i)
    sp = 30 + ci * 22 + rnd.random() * 40
    clk = int(sp * (1.6 + rnd.random()))
    return dict(spend=sp, imp=clk * 35, clk=clk, rp=sp / (28 + 4 * ci) * (0.8 + rnd.random() * 0.4), rv=sp * (1.1 + 0.15 * ci), up=sp / (45 + 3 * ci), uv=sp * (0.6 + 0.1 * ci))


def g_sum(ci, i0, i1, src):
    t = dict(spend=0.0, imp=0, clk=0, p=0.0, v=0.0)
    for i in range(i0, i1 + 1):
        d = g_day(ci, i)
        t["spend"] += d["spend"]; t["imp"] += d["imp"]; t["clk"] += d["clk"]
        t["p"] += d["up"] if src == "upstack" else d["rp"]
        t["v"] += d["uv"] if src == "upstack" else d["rv"]
    return t


def g_m(t, days, total_spend):
    sp, pu, rv, clk, imp = t["spend"], t["p"], t["v"], t["clk"], t["imp"]
    return dict(spend=round(sp, 2), purchases=round(pu, 2), revenue=round(rv, 2), roas=round(rv / sp, 4) if sp else None, cpa=round(sp / pu, 2) if pu else None,
                impressions=imp, link_clicks=clk, ctr=round(clk / imp, 5) if imp else None, cpc=round(sp / clk, 4) if clk else None, cpm=round(sp * 1000 / imp, 4) if imp else None,
                conv_rate=round(pu / clk, 5) if clk else None, spend_share=round(sp / total_spend, 4) if total_spend else None, active_campaigns=1,
                spend_per_day=round(sp / days, 2), purchases_per_day=round(pu / days, 2), revenue_per_day=round(rv / days, 2))


def g_info(win, src, basis):
    info = dict(win["info"], channel="google", stage="all", stages=["testing", "scaling", "unclassified"], stage_note="")
    if src == "upstack":
        info["source"] = dict(key="upstack", basis=basis, label="Upstack · " + ("new customers" if basis == "nc" else "all orders"),
                              labels=dict(purchases="New customers" if basis == "nc" else "Orders", revenue="NC revenue" if basis == "nc" else "Revenue", roas="NC ROAS" if basis == "nc" else "ROAS",
                                          cpa="NC CPA" if basis == "nc" else "Cost per order"),
                              model="Upstack any-click attribution, 30-day window, Google Ads source", summary="Mock: Upstack's attribution of Google campaigns.", note=None)
    else:
        info["source"] = dict(key="meta", basis=None, label="Google", labels=dict(purchases="Purchases", revenue="Conversion value", roas="ROAS", cpa="CPA"), model=None,
                              summary="Mock: Google's own reporting through Upstack.", note=None)
    return info


def g_rows(group, args, win):
    src = "upstack" if args.get("source") == "upstack" else "meta"
    basis = args.get("basis", "nc")
    i0, i1 = (0, N - 1) if win.get("launch") else (win["i0"], win["i1"])
    days = i1 - i0 + 1
    want = args.get("type")
    items = [(ci, c) for ci, c in enumerate(G_CAMPS) if not want or c[1] == want]
    sums = {ci: g_sum(ci, i0, i1, src) for ci, _ in items}
    tot_t = dict(spend=sum(t["spend"] for t in sums.values()), imp=sum(t["imp"] for t in sums.values()), clk=sum(t["clk"] for t in sums.values()),
                 p=sum(t["p"] for t in sums.values()), v=sum(t["v"] for t in sums.values()))
    rows = []
    if group == "type":
        for typ in dict.fromkeys(c[1] for _, c in items):
            t = dict(spend=0.0, imp=0, clk=0, p=0.0, v=0.0)
            for ci, c in items:
                if c[1] == typ:
                    for k in t:
                        t[k] += sums[ci][k]
            rows.append(dict(key=typ, label=typ, campaign_type=typ, m=g_m(t, days, tot_t["spend"])))
    else:
        for ci, c in items:
            rows.append(dict(key="g%d" % ci, label=c[0], utm_campaign=None, campaign_type=c[1], in_feed=True, status=c[2], bid_strategy="X", m=g_m(sums[ci], days, tot_t["spend"])))
    rows.sort(key=lambda r: -r["m"]["spend"])
    return rows, g_m(tot_t, days, tot_t["spend"]), g_info(win, src, basis)


def ad_row(ad, win, launch):
    r = _ad_row(ad, win, launch)
    rb = _ad_row(ad, g.cmp, launch) if getattr(g, "cmp", None) else None
    return decorate(r, rb, win if not win["launch"] else None)


def _ad_row(ad, win, launch):
    split = ad_metrics(ad, win, launch)
    sp, pu, rv = metrics_of(split, win)
    cmp = CAMPAIGNS[ad["campaign_id"]]
    fam = FAM_BY_KEY.get(ad["fam"]) if ad["fam"] else None
    first = fam["rows"][0] if fam else None
    sp7, pu7, _ = sums(ad["daily"], N - 7, N - 1)
    tf = traffic_fields(sp, pu, *ad_traffic(ad, win, launch))
    days = 0
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    for i in range(lo, hi + 1):
        if ad["daily"][i][0] > 0:
            days += 1
    return dict(ad_id=ad["ad_id"], ad_name=ad["name"], family_key=ad["fam"] or ("meta:" + ad["meta_family"].lower()), display=fam["display"] if fam else ad["meta_family"],
                primary_id=first["id"] if first else None, thumb_asset_id=fam["thumb"] if fam else None, campaign_id=cmp["campaign_id"], campaign_name=cmp["name"],
                stage=cmp["stage"], adset_name=ad["adset"], account=cmp["account"], status=ad["status"], created_date=ad["created"],
                tag=dict(cluster=first["cluster"], icp=first["icp"], pain=first["pain"]) if first else dict(cluster=None, icp=None, pain=None),
                spend=round(sp, 2), purchases=int(round(pu)), revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu), spend_7d=round(sp7, 2),
                live=ad["daily"][N - 1][0] > 0 or ad["daily"][N - 2][0] > 0, days_active=days, links=ad_links(ad), **tf)


def sum_cmp(tot, rows, win):
    """tot: flat total dict (A already set). add per-day, and cmp/delta from the rows' cmp blocks (recomputed from sums)"""
    tb = None
    if getattr(g, "cmp", None) and rows and all("cmp" in r for r in rows):
        sp = sum(r["cmp"]["spend"] for r in rows); pu = sum(r["cmp"]["purchases"] for r in rows); rv = sum(r["cmp"]["revenue"] for r in rows)
        tb = dict(spend=round(sp, 2), purchases=pu, revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu), **flat_traffic([r["cmp"] for r in rows], sp, pu))
    elif getattr(g, "cmp", None):
        tb = dict(spend=0, purchases=0, revenue=0, roas=None, cpa=None, **traffic_fields(0, 0, 0, 0, 0, 0))
    decorate(tot, tb, win if not win["launch"] else None)
    return tot


def flat_traffic(rows, sp, pu):
    imp = sum(r.get("impressions") or 0 for r in rows)
    clk = sum(r.get("link_clicks") or 0 for r in rows)
    clk_tr = sum((r.get("ctr") or 0) * (r.get("impressions") or 0) for r in rows)
    sp_tr = sum((r.get("cpm") or 0) * (r.get("impressions") or 0) / 1000 for r in rows)
    return traffic_fields(sp, pu, imp, clk, clk_tr, sp_tr)


def total_of(rows, win):
    sp = sum(r["spend"] for r in rows); pu = sum(r["purchases"] for r in rows); rv = sum(r["revenue"] for r in rows)
    tot = dict(spend=round(sp, 2), purchases=pu, revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu), spend_7d=round(sum(r["spend_7d"] for r in rows), 2),
               **flat_traffic(rows, sp, pu))
    return sum_cmp(tot, rows, win)


def ads_for(slug, args, win):
    p = prod_meta(slug)
    c, i, pn, fk, cid = (args.get(k) for k in ("cluster", "icp", "pain", "family_key", "campaign_id"))
    fam_filter = None
    if c or i or pn:
        ents = filt(prod_items(slug), args)
        fam_filter = {f["key"] for f, r, ch in ents if ch}
    rows = []
    remainders = []
    for ad in cur_ads():
        if ad["product"] != slug:
            continue
        if fam_filter is not None and ad["fam"] not in fam_filter:
            continue
        if fk and (ad["fam"] or ("meta:" + ad["meta_family"].lower())) != fk:
            continue
        if cid and ad["campaign_id"] != cid:
            continue
        fam = FAM_BY_KEY.get(ad["fam"]) if ad["fam"] else None
        row = ad_row(ad, win, fam["launch"] if fam else None)
        if len(win["stage"]) < 3 and not row["spend"] and not (row.get("cmp") or {}).get("spend"):
            continue
        rows.append(row)
    # remainder row for the family with partial ad-level detail
    for f in FAMS:
        if f.get("has_remainder") and f["product"] == slug and (fk in (None, f["key"])) and (fam_filter is None or f["key"] in fam_filter) and not cid:
            tot = fam_split(f["key"], win)[0]
            sp, pu, rv = metrics_of(tot, win)
            have_sp = sum(r["spend"] for r in rows if r["family_key"] == f["key"])
            have_pu = sum(r["purchases"] for r in rows if r["family_key"] == f["key"])
            have_rv = sum(r["revenue"] for r in rows if r["family_key"] == f["key"])
            if sp - have_sp > 0.5:
                rem_b = None
                if getattr(g, "cmp", None):
                    bsp, bpu, brv = metrics_of(fam_split(f["key"], g.cmp)[0], g.cmp)
                    hb = [sum(r["cmp"][k] for r in rows if r["family_key"] == f["key"]) for k in ("spend", "purchases", "revenue")]
                    rem_b = dict(spend=round(bsp - hb[0], 2), purchases=int(round(bpu - hb[1])), revenue=round(brv - hb[2], 2), roas=_roas(bsp - hb[0], brv - hb[2]), cpa=_cpa(bsp - hb[0], bpu - hb[1]))
                rem = dict(ad_id=None, ad_name="(days without ad-level detail)", family_key=f["key"], display=f["display"], primary_id=f["rows"][0]["id"], thumb_asset_id=None,
                           campaign_id=None, campaign_name=None, stage=None, adset_name=None, account=None, status=None, created_date=None, tag=None,
                           spend=round(sp - have_sp, 2), purchases=int(round(pu - have_pu)), revenue=round(rv - have_rv, 2), roas=_roas(sp - have_sp, rv - have_rv),
                           cpa=_cpa(sp - have_sp, pu - have_pu), spend_7d=0, live=False, days_active=0)
                remainders.append(decorate(rem, rem_b, win if not win["launch"] else None))
    rows = rows + remainders
    rows.sort(key=lambda r: -(r["spend"] or 0))
    return rows


def campaign_payload(win, slugs):
    """(rows, by_stage, total) of the campaigns of the given products (None = every product); one product per mock campaign"""
    out = {}
    for ad in cur_ads():
        if slugs is not None and ad["product"] not in slugs:
            continue
        fam = FAM_BY_KEY.get(ad["fam"]) if ad["fam"] else None
        r = ad_row(ad, win, fam["launch"] if fam else None)
        c = CAMPAIGNS[ad["campaign_id"]]
        e = out.setdefault(c["campaign_id"], dict(campaign_id=c["campaign_id"], name=c["name"], account=c["account"], stage=c["stage"], product_slug=ad["product"], ads=0, fams=set(), spend=0.0, purchases=0, revenue=0.0, spend_7d=0.0, live=False, last_day=None))
        e["ads"] += 1; e["fams"].add(ad["fam"] or ad["meta_family"]); e.setdefault("_ads", []).append(ad)
        e["spend"] += r["spend"]; e["purchases"] += r["purchases"]; e["revenue"] += r["revenue"]; e["spend_7d"] += r["spend_7d"]
        e.setdefault("_rows", []).append(r)
        if "cmp" in r:
            b = e.setdefault("_b", [0.0, 0, 0.0])
            b[0] += r["cmp"]["spend"]; b[1] += r["cmp"]["purchases"]; b[2] += r["cmp"]["revenue"]
        e["live"] = e["live"] or r["live"]
        last = max((i for i in range(N) if ad["daily"][i][0] > 0), default=None)
        if last is not None:
            ld = DAYS[last].isoformat()
            e["last_day"] = ld if e["last_day"] is None or ld > e["last_day"] else e["last_day"]
    rows = []
    for e in out.values():
        if len(win["stage"]) < 3 and not e["spend"] and not (e.get("_b") and e["_b"][0]):
            continue
        e["families"] = len(e.pop("fams"))
        e["spend"] = round(e["spend"], 2); e["revenue"] = round(e["revenue"], 2); e["spend_7d"] = round(e["spend_7d"], 2)
        e["roas"] = _roas(e["spend"], e["revenue"]); e["cpa"] = _cpa(e["spend"], e["purchases"])
        b = e.pop("_b", None)
        crow = e.pop("_rows", [])
        camp_ads = e.pop("_ads", [])
        spec = spark_request()
        if spec:
            e["spark"] = spark_for(camp_ads, win, spec)
        e.update(flat_traffic(crow, e["spend"], e["purchases"]))
        eb = dict(spend=round(b[0], 2), purchases=int(round(b[1])), revenue=round(b[2], 2), roas=_roas(b[0], b[2]), cpa=_cpa(b[0], b[1]),
                  **flat_traffic([r["cmp"] for r in crow], b[0], b[1])) if b else None
        decorate(e, eb, win if not win["launch"] else None)
        rows.append(e)
    rows.sort(key=lambda r: -r["spend"])
    by_stage = []
    for st in ("testing", "scaling"):
        rs = [r for r in rows if r["stage"] == st]
        sp = sum(r["spend"] for r in rs); pu = sum(r["purchases"] for r in rs); rv = sum(r["revenue"] for r in rs)
        card = dict(stage=st, campaigns=len(rs), ads=sum(r["ads"] for r in rs), spend=round(sp, 2), purchases=pu, revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu), spend_7d=round(sum(r["spend_7d"] for r in rs), 2))
        by_stage.append(sum_cmp(card, rs, win))
    tot = total_of(rows, win)
    tot["ads"] = sum(r["ads"] for r in rows)
    shares(rows, tot)
    return rows, by_stage, tot


def meta_rows(win, slug):
    """platform-level Meta campaign rows (one product per mock campaign; the biggest is shown as Mixed 70 / 30 with a second product when unfiltered)"""
    names = {p["slug"]: p["name"] for p in PRODUCTS}
    if slug and slug != "none":
        prod_meta(slug)
    only = {slug} if slug and slug != "none" else (set() if slug == "none" else None)
    rows, _by_stage, tot = campaign_payload(win, only)
    for r in rows:
        s_ = r.pop("product_slug")
        r.update(product=names[s_], product_slug=s_, products=[dict(slug=s_, name=names[s_], spend=r["spend"], share=1.0)])
    if not slug and rows:
        r0 = rows[0]
        first = r0["product_slug"]
        other = next(p for p in PRODUCTS if p["slug"] != first)
        r0.update(product="Mixed", product_slug=None, products=[dict(slug=first, name=names[first], spend=round(r0["spend"] * 0.7, 2), share=0.7),
                                                               dict(slug=other["slug"], name=other["name"], spend=round(r0["spend"] * 0.3, 2), share=0.3)])
    return rows, tot


def product_chips(win):
    chips = []
    for p in PRODUCTS:
        prs, _b, pt = campaign_payload(win, {p["slug"]})
        chips.append(dict(slug=p["slug"], name=p["name"], campaigns=len(prs), spend=pt["spend"]))
    return chips


def platform_series(win, platform, numbers, ads_filter=None, gtype=None):
    """per-day 8-tuples of a platform on the chosen numbers (mock); Meta from the mock ads, Google from g_day"""
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    if platform == "meta":
        arr = sum_series(ads_filter if ads_filter is not None else cur_ads(), ALL_STAGES)
        k = dict(own=(1.0, 1.0), nc=(0.7, 0.72), all=(1.2, 1.1))[numbers]
        return [(a[0], a[1] * k[0], a[2] * k[1], a[3], a[4], a[5], a[6], a[7]) for a in arr], lo, hi
    out = []
    for i in range(N):
        sp = pu = rv = imp = clk = 0.0
        for ci in range(len(G_CAMPS)):
            if gtype and G_CAMPS[ci][1] != gtype:
                continue
            d = g_day(ci, i)
            sp += d["spend"]; imp += d["imp"]; clk += d["clk"]
            pu += d["rp"] if numbers == "own" else d["up"]
            rv += d["rv"] if numbers == "own" else d["uv"]
        out.append((sp, pu, rv, imp, clk, clk, sp, 1))
    return out, lo, hi


def numbers_arg(args):
    n = args.get("numbers")
    if n:
        return n
    return "own" if args.get("source") != "upstack" else ("all" if args.get("basis") == "all" else "nc")


# ----------------------------------------------------------------------------------------------- Total spend / Total NC ROAS (All views)
ORDERS_FROM = 3                       # the mock orders feed starts three days after the first day: earlier days read blank, never zero
NO_ROAS_FILTER = ("Total NC ROAS is shown for all platforms and all products only: Upstack's orders have no product or platform dimension, so new-customer revenue "
                  "cannot be split to a filtered view. Total spend below still follows the filter.")


def day_spend_table(only, slug, typ):
    """per-day spend of each platform in view (all N days), the platforms' filters as on the All campaigns table"""
    out = {}
    for key in ("meta", "google"):
        if only and only != key or (key == "google" and slug) or (key == "meta" and typ):
            continue
        ads_f = [a for a in cur_ads() if a["product"] == slug] if (key == "meta" and slug) else None
        arr, _lo, _hi = platform_series({"launch": True}, key, "own", ads_f, typ)
        out[key] = [round(a[0], 2) for a in arr]            # cents per day, as the day points the chart draws
    return out


_ORDERS = []


def orders_nc(i):
    """actual new-customer revenue of mock day i: a little over half of that day's two-platform spend (all ads, whatever the request filters), varying by day;
    None before the feed starts"""
    if i < ORDERS_FROM:
        return None
    if not _ORDERS:
        marr = sum_series(ADS, ALL_STAGES)
        garr, _lo, _hi = platform_series({"launch": True}, "google", "own")
        _ORDERS.extend(None if k < ORDERS_FROM else round((marr[k][0] + garr[k][0]) * (0.55 + 0.1 * math.sin(k)) + 120.0, 2) for k in range(N))
    return _ORDERS[i]


def _bucket(d, grain):
    return d if grain == "day" else d - timedelta(days=d.weekday()) if grain == "week" else d.replace(day=1)


def total_block(win, grain, labels, only, slug, typ):
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    sp = day_spend_table(only, slug, typ)
    full = not only and not slug and not typ
    by = {}
    for i in range(lo, hi + 1):
        b = by.setdefault(_bucket(DAYS[i], grain), dict(spend=0.0, meta=0.0, google=0.0, rev=0.0, nsp=0.0, days=0, n=0))
        m, gg = sp.get("meta", [0.0] * N)[i], sp.get("google", [0.0] * N)[i]
        b["spend"] += m + gg; b["meta"] += m; b["google"] += gg; b["n"] += 1
        o = orders_nc(i)
        if full and o is not None:
            b["rev"] += o; b["nsp"] += m + gg; b["days"] += 1
    size = lambda k: 1 if grain == "day" else 7 if grain == "week" else __import__("calendar").monthrange(k.year, k.month)[1]
    pts = []
    for lab in labels:
        k = date.fromisoformat(lab)
        b = by.get(k)
        if not b:
            pts.append(None)
            continue
        pts.append(dict(t=lab, partial=b["n"] < size(k), spend=round(b["spend"], 2), meta_spend=round(b["meta"], 2), google_spend=round(b["google"], 2),
                        nc_rev=round(b["rev"], 2) if b["days"] else None, nc_spend=round(b["nsp"], 2) if b["days"] else None,
                        nc_roas=round(b["rev"] / b["nsp"], 4) if b["days"] and b["nsp"] else None, nc_days=b["days"]))
    return dict(available=full, reason=None if full else NO_ROAS_FILTER, points=pts, orders_first=DAYS[ORDERS_FROM].isoformat())


def kpi_block(lo, hi, only, slug, typ):
    sp = day_spend_table(only, slug, typ)
    full = not only and not slug and not typ
    meta_sp = round(sum(sp.get("meta", [0.0] * N)[lo:hi + 1]), 2)
    google_sp = round(sum(sp.get("google", [0.0] * N)[lo:hi + 1]), 2)
    total = round(meta_sp + google_sp, 2)
    D = [i for i in range(lo, hi + 1) if full and orders_nc(i) is not None]
    nc = dict(available=bool(D) and full, reason=None if full else NO_ROAS_FILTER)
    if D and full:
        rev = round(sum(orders_nc(i) for i in D), 2)
        nsp = round(sum(sp["meta"][i] + sp["google"][i] for i in D), 2)
        am, ag = round(rev * 0.62, 2), round(rev * 0.18, 2)
        nc.update(rev=rev, spend=nsp, roas=round(rev / nsp, 4), **{"from": DAYS[D[0]].isoformat(), "to": DAYS[D[-1]].isoformat()}, days=len(D), orders=len(D) * 7,
                  attributed=dict(meta=am, google=ag, sum=round(am + ag, 2), gap=round(am + ag - rev, 2), gap_pct=round((am + ag - rev) / rev, 4)))
        if len(D) < hi - lo + 1:
            nc["partial"] = f"Days with Meta, Google and order data: {len(D)} of {hi - lo + 1} in this window."
    return dict(spend=total, meta_spend=meta_sp, google_spend=google_sp, meta_share=round(meta_sp / total, 4) if total else None,
                google_share=round(google_sp / total, 4) if total else None, window=dict(**{"from": DAYS[lo].isoformat(), "to": DAYS[hi].isoformat()}, days=hi - lo + 1), nc=nc)


def reliability(tag_row):
    method = tag_row["method"].lower()
    c = tag_row["conf"]
    notes = []
    if "text only" in method:
        notes.append("Tagged from copy only (no image)")
    elif "thumbnail" in method and "no audio" in method:
        notes.append("Tagged from a thumbnail only")
    notes.append({"low": "Low confidence — blind re-tag agreed ~52%", "high": "High confidence — blind re-tag agreed ~93%", "medium": "Medium confidence"}[c])
    notes.append("Blind re-tag agreement overall: line 79%, ICP 84%, cluster 88%")
    return ". ".join(notes)



# ----------------------------------------------------------------------------------------------- trend (v1.6)
TREND_METRICS = ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")
ZERO8 = (0, 0, 0, 0, 0, 0, 0, 0)
_AS = {}
TREND_CALLS = {"n": 0}


def ad_series(ad, stages):
    """per day 8-tuple (spend, purchases, revenue, impressions, clicks, clicks_on_known_days, spend_on_known_days, impressions_known)"""
    ck = (ad["ad_id"], stages)
    if ck not in _AS:
        out = []
        stg = ad_stage(ad)
        for i in range(N):
            if ("unclassified" if i < UNC_DAYS else stg) not in stages:
                out.append(ZERO8)
                continue
            sp, pu, rv = ad["daily"][i]
            im, cl = ad["tr"][i]
            known = im is not None
            out.append((sp, pu, rv, im or 0, cl, cl if known else 0, sp if known else 0, 1 if known else 0))
        _AS[ck] = out
    return _AS[ck]


def sum_series(ads_list, stages):
    tot = [list(ZERO8) for _ in range(N)]
    for ad in ads_list:
        for i, t in enumerate(ad_series(ad, stages)):
            row = tot[i]
            for j in range(8):
                row[j] += t[j]
    return [tuple(r) for r in tot]


def point_from(t, s, partial=False):
    sp, pu, rv, im, ck, ck_tr, sp_tr, known = s
    pt = dict(t=t, spend=round(sp, 2), purchases=int(pu), revenue=round(rv, 2), roas=round(rv / sp, 4) if sp else None, cpa=round(sp / pu, 2) if pu else None,
              impressions=int(im) if known else None, link_clicks=int(ck), ctr=round(ck_tr / im, 6) if im else None, cpc=round(sp / ck, 4) if ck else None,
              cpm=round(sp_tr / im * 1000, 4) if im else None, conv_rate=round(pu / ck, 6) if ck else None)
    if partial:
        pt["partial"] = True
    return pt


def series_points(arr, lo, hi, grain):
    if grain == "day":
        return [point_from(DAYS[i].isoformat(), arr[i]) for i in range(lo, hi + 1)]
    if grain == "month":
        import calendar
        mb = {}
        for i in range(lo, hi + 1):
            k = DAYS[i].replace(day=1)
            b = mb.setdefault(k, [list(ZERO8), 0])
            for j in range(8):
                b[0][j] += arr[i][j]
            b[1] += 1
        return [point_from(k.isoformat(), tuple(b[0]), partial=b[1] < calendar.monthrange(k.year, k.month)[1]) for k, b in sorted(mb.items())]
    buckets = {}
    for i in range(lo, hi + 1):
        mon = DAYS[i] - timedelta(days=DAYS[i].weekday())
        b = buckets.setdefault(mon, [list(ZERO8), 0])
        for j in range(8):
            b[0][j] += arr[i][j]
        b[1] += 1
    return [point_from(m.isoformat(), tuple(b[0]), partial=b[1] < 7) for m, b in sorted(buckets.items())]


def align_points(pts, n):
    pts = list(pts)[:n]
    return pts + [None] * (n - len(pts))


def scope_ads(slug, scope):
    ads_in = [a for a in cur_ads() if a["product"] == slug]
    flt = {k: scope[k] for k in ("cluster", "icp", "pain") if scope.get(k)}
    if flt:
        fams = {f["key"] for f, r, ch in filt(prod_items(slug), flt) if ch}
        ads_in = [a for a in ads_in if a["fam"] in fams]
    if scope.get("family_key"):
        ads_in = [a for a in ads_in if (a["fam"] or "meta:" + a["meta_family"].lower()) == scope["family_key"]]
    if scope.get("campaign_id"):
        ads_in = [a for a in ads_in if a["campaign_id"] == scope["campaign_id"]]
    return ads_in


def spark_request():
    m = request.args.get("spark")
    if not m:
        return None
    if m not in TREND_METRICS:
        raise Bad("bad spark metric")
    try:
        n = int(request.args.get("spark_days", 30))
    except ValueError:
        raise Bad("bad spark_days")
    return m, max(1, min(n, 60))


def spark_for(ads_list, win, spec):
    m, n = spec
    end = N - 1 if win["launch"] else win["i1"]
    arr = sum_series(ads_list, win["stage"])
    out = []
    for i in range(end - n + 1, end + 1):
        if i < 0:
            out.append(0)
            continue
        v = point_from("", arr[i])[m]
        out.append(v if v is not None else None)
    return out


def fams_ads(fam_keys):
    keys = set(fam_keys)
    return [a for a in cur_ads() if a["fam"] in keys]


def attach_sparks(rows, win, ads_of):
    spec = spark_request()
    for r in rows:
        if spec:
            r["spark"] = spark_for(ads_of(r), win, spec)
    return rows



def ad_links(ad):
    n = int("".join(c for c in ad["ad_id"] if c.isdigit())[-9:] or 0)
    fam = FAM_BY_KEY.get(ad["fam"]) if ad.get("fam") else None
    story = "javascript:alert(1)" if (fam and fam["display"].startswith("XSS")) else f"https://www.facebook.com/lonvera/posts/{n}"
    return dict(story_url=story, ads_manager_url=f"https://adsmanager.facebook.com/adsmanager/manage/ads?act=12345678&selected_ad_ids={ad['ad_id']}",
                ad_id=ad["ad_id"], account=CAMPAIGNS[ad["campaign_id"]]["account"])


PREVIEW_MODE = os.environ.get("MOCK_PREVIEW", "on")  # on | off


def preview_json(name):
    fmt = request.args.get("format", "mobile_feed")
    if PREVIEW_MODE == "off":
        return jsonify(enabled=False, src=None, format=fmt)
    from urllib.parse import quote
    return jsonify(enabled=True, src=f"/mock/preview?name={quote(name)}&format={fmt}", format=fmt)
