#!/usr/bin/env python3
"""Mock of the Tag Explorer API (docs/CONTRACT.md v1 + v1.1) for developing and testing the UI.

Serves app/static and canned-but-consistent JSON for every contract endpoint. Numbers are derived from one
ad-level daily table so every level sums to its parent, and the window / stage / from-to params really
change the figures. Login password: `test`.

    .venv/bin/python tests/ui/mock_server.py            # http://127.0.0.1:5099
"""
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

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


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


# ----------------------------------------------------------------------------------------------- routes
@app.errorhandler(Bad)
def _bad(e):
    return jsonify(error=str(e)), 400


@app.before_request
def guard():
    p = request.path
    if (p.startswith("/api/") and p != "/api/login") or p.startswith("/media/"):
        if not AUTH_DISABLED and request.cookies.get("sid") != "ok":
            return jsonify(error="unauthorized"), 401
    g.active = request.args.get("active") == "1"
    g.cmp = None
    if p.startswith("/api/") and any(k in request.args for k in ("compare", "cfrom", "cto")):
        g.cmp = parse_cmp(request.args, parse_win(request.args))
    if p.startswith("/api/") and request.args.get("__delay"):
        import time
        time.sleep(float(request.args["__delay"]))


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    if body.get("username") not in USERS or body.get("password") != PASSWORD:
        return jsonify(error="invalid username or password"), 401
    r = Response(status=204)
    r.set_cookie("sid", "ok", httponly=True, samesite="Lax")
    r.set_cookie("user", body["username"], httponly=True, samesite="Lax")
    return r


@app.post("/api/logout")
def logout():
    r = Response(status=204)
    r.delete_cookie("sid")
    r.delete_cookie("user")
    return r


@app.get("/api/status")
def status():
    extra = dict(auth="disabled", user="guest") if AUTH_DISABLED else dict(user=request.cookies.get("user"))
    extra["stale"] = dict(is_stale=os.environ.get("MOCK_STALE") == "1", reason="the last Meta pull failed (token rejected)" if os.environ.get("MOCK_STALE") == "1" else None)
    extra["last_ok_at"] = "2026-11-02T22:12:00+00:00"
    extra["sources"] = dict(platforms=[dict(key="meta", name="Meta", available=True, first_day=DAILY_START.isoformat(), through=DATA_THROUGH.isoformat()),
                                       dict(key="google", name="Google", available=True, first_day=DAILY_START.isoformat(), through=DATA_THROUGH.isoformat())])
    return jsonify(**extra, data_through=DATA_THROUGH.isoformat(), daily_start=DAILY_START.isoformat(), lifetime_cutoff=CUTOFF.isoformat(),
                   last_refresh=dict(job="run_daily", finished_at="2026-11-03T06:12:00+00:00", status="ok"), products=len(PRODUCTS))



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


@app.get("/api/products")
def products():
    win = parse_win(request.args)
    out = []
    for p in PRODUCTS:
        ents = prod_items(p["slug"])
        d = product_json(p)
        d["rows"] = len(ents)
        d["m"] = make_m(items_for_m(ents), win)
        out.append(d)
    tot = dict(spend=sum(d["m"]["spend"] for d in out))
    if g.cmp:
        tot["cmp"] = dict(spend=sum(d["m"]["cmp"]["spend"] for d in out))
        tot["delta"] = {}
    shares([d["m"] for d in out], tot)
    if request.args.get("envelope"):
        all_ents = [e for pr in PRODUCTS for e in prod_items(pr["slug"])]
        tm = make_m(items_for_m(all_ents), win)
        um = unmatched_total_m(win)
        ta = merge_m(tm, um, win)
        # % of spend on the landing is relative to the Meta account total (tracker products + not on the tracker)
        for obj in [d["m"] for d in out] + [tm, um]:
            obj["spend_share"] = round(obj["spend"] / ta["spend"], 6) if ta["spend"] else None
            if obj.get("cmp"):
                obj["cmp"]["spend_share"] = round(obj["cmp"]["spend"] / ta["cmp"]["spend"], 6) if ta["cmp"]["spend"] else None
                obj["delta"]["spend_share_pp"] = None if obj["spend_share"] is None or obj["cmp"]["spend_share"] is None else round((obj["spend_share"] - obj["cmp"]["spend_share"]) * 100, 4)
        ta["spend_share"] = 1.0
        if ta.get("cmp"):
            ta["cmp"]["spend_share"] = 1.0
            ta["delta"]["spend_share_pp"] = 0.0
        return jsonify(window_info=win["info"], compare_info=cinfo(), rows=out, total=dict(m=tm, rows=sum(d["rows"] for d in out)),
                       unmatched=dict(m=um), total_all=dict(m=ta))
    return jsonify(out)


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


@app.get("/api/products/<slug>/rollup")
def rollup(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    level = request.args.get("level", "cluster")
    if level not in LEVEL_NAMES:
        raise Bad("bad level")
    flt = {k: request.args.get(k) for k in ("cluster", "icp") if request.args.get(k)}
    rows, ents = rollup_rows(slug, level, flt, win)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([r["m"] for r in rows], total["m"])
    attach_sparks(rows, win, lambda r: fams_ads(r["_fams"]))
    for r in rows:
        r.pop("_fams", None)
    saved, g.cmp = g.cmp, None
    um = unmatched_rows(slug, parse_win({}))
    g.cmp = saved
    um7 = sum(u["spend_7d"] for u in um)
    return jsonify(product=product_json(p), level=level, filters=dict(cluster=request.args.get("cluster"), icp=request.args.get("icp")),
                   window=win["info"], window_info=win["info"], compare_info=cinfo(), rows=rows, total=total,
                   unmatched=dict(spend_7d=round(um7, 2), spend_30d=round(um7 * 4.1, 2), live_families=sum(1 for u in um if u["live"]), families=len(um)))


@app.get("/api/products/<slug>/families")
def families(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    flt = {k: request.args.get(k) for k in ("cluster", "icp", "pain") if request.args.get(k)}
    rows, ents = family_rows(slug, flt, win)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([r["m"] for r in rows], total["m"])
    attach_sparks(rows, win, lambda r: fams_ads(r["_fams"]))
    for r in rows:
        r.pop("group", None)
        r.pop("_fams", None)
    return jsonify(product=product_json(p), filters={k: request.args.get(k) for k in ("cluster", "icp", "pain")}, window=win["info"], window_info=win["info"], compare_info=cinfo(),
                   rows=rows, total=total)


@app.get("/api/products/<slug>/tree")
def tree(slug):
    win = parse_win(request.args)
    p = prod_meta(slug)
    root = request.args.get("root", "cluster")
    levels = {"cluster": 0, "icp": 1, "pain": 2, "family": 3}
    if root not in levels:
        raise Bad("bad root")
    r0 = levels[root]
    try:
        depth = int(request.args.get("depth", 0))
    except ValueError:
        raise Bad("bad depth")
    if not 0 <= depth <= 3:
        raise Bad("bad depth")
    base = {k: request.args.get(k) for k in ("cluster", "icp", "pain") if request.args.get(k)}
    need = ("cluster", "icp", "pain")[:r0]
    if any(k not in base for k in need):
        raise Bad(f"root={root} needs " + "+".join(need))
    out = []
    spark_spec = spark_request()

    def key_of(level, flt, label=None, fam=None):
        parts = [flt.get("cluster"), flt.get("icp"), flt.get("pain")][:level]
        if level < 3 and label is not None:
            parts.append(label)
        if level == 3:
            parts.append(fam)
        return "|".join(parts)

    def emit(level, flt, parent_key):
        if level < 3:
            rows, _ = rollup_rows(slug, LEVEL_NAMES[level], flt, win)
        else:
            rows, _ = family_rows(slug, flt, win)
        for row in rows:
            if level < 3:
                label = row["label"]
                node = dict(key=key_of(level, flt, label), parent_key=parent_key, level=level, label=label, cluster=row["cluster"] if level >= 0 else None,
                            icp=row["icp"], pain=row["pain"], family_key=None, primary_id=None, color=row["color"], line_code=row["line_code"], trigger=row["trigger"],
                            confidence=None, ad_type=None, status=None, thumb_asset_id=None, has_children=True, child_count=row["child_count"], counted_here=True, m=row["m"])
                child_flt = {**flt, LEVEL_NAMES[level]: label}
            else:
                grp = row["group"]
                node = dict(key=key_of(3, dict(cluster=grp[0], icp=grp[1], pain=grp[2]), fam=row["family_key"]), parent_key=parent_key, level=3, label=row["display"],
                            cluster=grp[0], icp=grp[1], pain=grp[2], family_key=row["family_key"], primary_id=row["primary_id"], color=None, line_code=None, trigger=None,
                            confidence=row["confidence"], ad_type=row["ad_type"], status=row["status"], thumb_asset_id=row["thumb_asset_id"], has_children=True,
                            launch_date=row["launch_date"], ads_count=row["ads_count"], ads_live=row["ads_live"],
                            child_count=sum(1 for a in cur_ads() if a["fam"] == row["family_key"]), counted_here=row["counted_here"], m=row["m"])
            if spark_spec:
                node["spark"] = spark_for(fams_ads(row["_fams"]), win, spark_spec)
            out.append(node)
            if level - r0 < depth and level < 3:
                emit(level + 1, child_flt, node["key"])

    parent0 = key_of(r0 - 1, base) if r0 > 0 else None
    emit(r0, base, parent0)
    ents = filt(prod_items(slug), base)
    total = dict(m=make_m(items_for_m(ents), win))
    shares([n["m"] for n in out], total["m"])
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=out, total=total)


@app.get("/api/products/<slug>/coverage")
def coverage(slug):
    """Synthetic coverage for the UI tests: the real per-line numbers of the mock, with L02 forced to 'no ads', L03 to '$0 spend' and L04 to 'added after tagging'
    so every section of the Not tested panel has a row."""
    win = parse_win(request.args)
    p = prod_meta(slug)
    if p["tag_set"] != "linemap":
        return jsonify(available=False, reason="Tracker tags, no line list.", summary=None, rows=[], keys={}, window_info=win["info"], compare_info=None)
    force = {"L02": "no_ads", "L03": "no_spend", "L04": "added"}
    keys, rows, cnt = {}, [], {"total": 0, "no_ads": 0, "no_spend": 0, "added_after_tagging": 0, "tested": 0}
    for code, cluster, icp, pain, trig in VB_LINES:
        st = {"no_ads": "no_ads", "no_spend": "no_spend", "added": "no_ads"}.get(force.get(code), "tested")
        added = force.get(code) == "added"
        keys["|".join([cluster, icp, pain])] = {"state": st, "added_after_tagging": added}
        cnt["total"] += 1
        cnt["added_after_tagging" if added else st] += 1
        if st != "tested":
            rows.append(dict(level=2, key="|".join([cluster, icp, pain]), cluster=cluster, icp=icp, pain=pain, label=pain, line_code=code, trigger=trig, color=None,
                             state=st, added_after_tagging=added, unique_ads=0 if st == "no_ads" else 2, spend=0.0))
    summ = dict(cnt, not_tested=cnt["no_ads"] + cnt["no_spend"] - 0)
    icps = dict(total=1, tested=1, no_ads=0, no_spend=0, added_after_tagging=0, not_tested=0)
    return jsonify(available=True, summary=dict(pain_points=summ, icps=icps), rows=rows, keys=keys, window_info=win["info"], compare_info=None)


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


@app.get("/api/google/rollup")
def google_rollup():
    win = parse_win(request.args)
    group = request.args.get("group", "type")
    if group not in ("type", "campaign"):
        raise Bad("bad group")
    rows, total, info = g_rows(group, request.args, win)
    return jsonify(channel="google", group=group, type=request.args.get("type"), window=info, window_info=info, compare_info=None, rows=rows, total=dict(m=total))


@app.get("/api/google/trend")
def google_trend():
    win = parse_win(request.args)
    src = "upstack" if request.args.get("source") == "upstack" else "meta"
    i0, i1 = (0, N - 1) if win.get("launch") else (win["i0"], win["i1"])
    pts = []
    for i in range(i0, i1 + 1):
        sp = sum(g_day(ci, i)["spend"] for ci in range(len(G_CAMPS)))
        pu = sum(g_day(ci, i)["up" if src == "upstack" else "rp"] for ci in range(len(G_CAMPS)))
        rv = sum(g_day(ci, i)["uv" if src == "upstack" else "rv"] for ci in range(len(G_CAMPS)))
        pts.append(dict(t=DAYS[i].isoformat(), partial=False, spend=round(sp, 2), purchases=round(pu, 2), revenue=round(rv, 2), roas=round(rv / sp, 4), cpa=round(sp / pu, 2),
                        impressions=int(sp * 60), link_clicks=int(sp * 1.8), ctr=0.03, cpc=1.2, cpm=40.0, conv_rate=0.02))
    return jsonify(channel="google", window_info=g_info(win, src, request.args.get("basis", "nc")), compare_info=None, grain=request.args.get("grain", "day"), scope={}, points=pts)


@app.get("/api/products/<slug>/unmatched")
def unmatched(slug):
    win = parse_win(request.args)
    rows = unmatched_rows(slug, win)
    tot = dict(spend=round(sum(r["spend"] for r in rows), 2), purchases=sum(r["purchases"] for r in rows), revenue=round(sum(r["revenue"] for r in rows), 2),
               spend_7d=round(sum(r["spend_7d"] for r in rows), 2), families=len(rows))
    tot["roas"] = _roas(tot["spend"], tot["revenue"]); tot["cpa"] = _cpa(tot["spend"], tot["purchases"])
    tot.update(flat_traffic(rows, tot["spend"], tot["purchases"]))
    sum_cmp(tot, rows, win)
    shares(rows, tot)
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=rows, total=tot)


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


@app.get("/api/products/<slug>/ads")
def ads(slug):
    win = parse_win(request.args)
    prod_meta(slug)
    rows = ads_for(slug, request.args, win)
    spec = spark_request()
    if spec:
        by_id = {a["ad_id"]: a for a in cur_ads()}
        for r in rows:
            r["spark"] = spark_for([by_id[r["ad_id"]]], win, spec) if r["ad_id"] in by_id else None
    tot = total_of(rows, win)
    shares(rows, tot)
    return jsonify(window_info=win["info"], compare_info=cinfo(), rows=rows, total=tot)


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


@app.get("/api/products/<slug>/campaigns")
def campaigns(slug):
    win = parse_win(request.args)
    prod_meta(slug)
    rows, by_stage, tot = campaign_payload(win, {slug})
    return jsonify(window_info=win["info"], compare_info=cinfo(), by_stage=by_stage, rows=rows, total=tot)


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


@app.get("/api/campaigns")
def platform_campaigns():
    """Platform level (Meta): every campaign across the products. The mock has one product per campaign, so the biggest campaign is shown as Mixed (70 / 30 with
    a second product) to exercise that cell; the API tests cover the real split."""
    win = parse_win(request.args)
    slug = request.args.get("product")
    rows, tot = meta_rows(win, slug)
    names = {p["slug"]: p["name"] for p in PRODUCTS}
    brief = dict(slug=slug, name=names.get(slug, "No product")) if slug else None
    return jsonify(platform="meta", product=brief, scope="tracker+unmatched", window_info=win["info"], compare_info=cinfo(), products=product_chips(win), rows=rows, total=tot)


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


@app.get("/api/campaigns/trend")
def platform_campaigns_trend():
    win = parse_win(request.args)
    grain = request.args.get("grain", "day")
    split = request.args.get("split", "none")
    if grain not in ("day", "week", "month") or split not in ("none", "stage", "product"):
        raise Bad("bad grain or split")
    slug, cid = request.args.get("product"), request.args.get("campaign_id")
    ads_in = [a for a in cur_ads() if (not slug or a["product"] == slug) and (not cid or a["campaign_id"] == cid)]
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    out = dict(platform="meta", window_info=win["info"], compare_info=cinfo(), grain=grain, scope=dict(product=slug, campaign_id=cid), points=series_points(sum_series(ads_in, win["stage"]), lo, hi, grain))
    if getattr(g, "cmp", None):
        out["cmp_points"] = align_points(series_points(sum_series(ads_in, win["stage"]), g.cmp["i0"], g.cmp["i1"], grain), len(out["points"]))
    if split == "stage":
        out["groups"] = [dict(key=st, label=st.capitalize(), color=None, points=series_points(sum_series(ads_in, frozenset({st})), lo, hi, grain)) for st in ALL_STAGES if st in win["stage"]]
    elif split == "product":
        out["groups"] = [dict(key=p["slug"], label=p["name"], color=None, points=series_points(sum_series([a for a in ads_in if a["product"] == p["slug"]], win["stage"]), lo, hi, grain)) for p in PRODUCTS]
    return jsonify(out)


@app.get("/api/platforms/trend")
def platforms_trend():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    grain = request.args.get("grain", "day")
    if grain not in ("day", "week", "month"):
        raise Bad("bad grain")
    numbers = numbers_arg(request.args)
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    groups = []
    for key, name, color in (("meta", "Meta", "#1a5fb4"), ("google", "Google", "#d9822b")):
        if only and only != key or (key == "google" and slug) or (key == "meta" and typ):
            continue
        ads_f = [a for a in cur_ads() if a["product"] == slug] if (key == "meta" and slug) else None
        arr, lo, hi = platform_series(win, key, numbers, ads_f, typ)
        groups.append(dict(key=key, label=name, color=color, points=series_points(arr, lo, hi, grain)))
    labels = sorted({pt["t"] for gr in groups for pt in gr["points"]})
    comb = []
    for t in labels:
        col = [pt for gr in groups for pt in gr["points"] if pt["t"] == t]
        comb.append(dict(t=t, spend=round(sum(c["spend"] for c in col), 2), impressions=sum(c["impressions"] or 0 for c in col), link_clicks=sum(c["link_clicks"] or 0 for c in col)))
    notes = ["Upstack attribution is drawn only where Upstack has it: Meta from May 6, 2026, Google from May 13, 2025."] if numbers != "own" else []
    total = total_block(win, grain, labels, only, slug, typ)
    return jsonify(window_info=win["info"], compare_info=None, grain=grain, numbers=numbers, labels=labels, points=comb, groups=groups, total=total, notes=notes)


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


@app.get("/api/platforms/kpis")
def platforms_kpis():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    if only not in (None, "meta", "google"):
        raise Bad("only must be meta or google")
    if slug and typ:
        raise Bad("product and type cannot both be set")
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    view = only or ("meta" if slug else "google" if typ else None)
    if view:                                           # one platform's own figures on the Numbers basis: the subtotal of the All campaigns table for the same arguments
        numbers = numbers_arg(request.args)
        res = campaigns_all_platforms().get_json()
        m = res["subtotals"][view]["m"]
        spend = round(m["spend"], 2)
        scope = next(pr["name"] for pr in PRODUCTS if pr["slug"] == slug) if slug else typ
        other = "google" if view == "meta" else "meta"
        total = dict(view=view, name=view.capitalize() + (f" · {scope}" if scope else ""), platform=view.capitalize(), numbers=numbers, basis=res["basis"][view],
                     labels=dict(zip(("revenue", "roas"), dict(own=("Revenue", "ROAS"), nc=("NC revenue", "NC ROAS"), all=("Revenue", "ROAS"))[numbers])),
                     spend=spend, **{f"{view}_spend": spend, f"{other}_spend": 0.0, f"{view}_share": 1.0, f"{other}_share": 0.0},
                     window=dict(**{"from": DAYS[lo].isoformat(), "to": DAYS[hi].isoformat()}, days=hi - lo + 1),
                     m={k: m.get(k) for k in ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks")})
        return jsonify(view=view, filters=dict(only=only, product=slug, type=typ), total=total, compare=None, delta=None, numbers=numbers, notes=[], platform_windows={}, window_info=win["info"])
    out = dict(view="all", filters=dict(only=only, product=slug, type=typ), total=kpi_block(lo, hi, only, slug, typ), compare=None, delta=None,
               orders=dict(first=DAYS[ORDERS_FROM].isoformat(), last=DAYS[-1].isoformat()), platform_windows={}, notes=[], window_info=win["info"])
    if getattr(g, "cmp", None):
        b = kpi_block(g.cmp["i0"], g.cmp["i1"], only, slug, typ)
        out["compare"] = b
        out["compare_info"] = g.cmp["info"]
        ta, tb = out["total"], b
        pct = lambda x, y: round((x - y) / y, 4) if x is not None and y else None
        out["delta"] = dict(spend_pct=pct(ta["spend"], tb["spend"]), nc_rev_pct=pct(ta["nc"].get("rev"), tb["nc"].get("rev")), nc_roas_pct=pct(ta["nc"].get("roas"), tb["nc"].get("roas")))
    return jsonify(out)


@app.get("/api/campaigns/all")
def campaigns_all_platforms():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    numbers = numbers_arg(request.args)
    only, slug, typ = request.args.get("only"), request.args.get("product"), request.args.get("type")
    if slug and typ:
        raise Bad("product and type cannot both be set")
    basis_label = {("meta", "own"): "Meta's own reporting", ("google", "own"): "Google's own reporting", ("meta", "nc"): "Upstack · new customers", ("google", "nc"): "Upstack · new customers",
                   ("meta", "all"): "Upstack · all orders", ("google", "all"): "Upstack · all orders"}
    rows, subtotals = [], {}
    if (only in (None, "meta")) and not typ:
        mrows, mtot = meta_rows(win, slug)
        k = dict(own=(1.0, 1.0), nc=(0.7, 0.72), all=(1.2, 1.1))[numbers]
        for r in mrows:
            m = {kk: vv for kk, vv in r.items() if kk not in ("products", "spark")}
            m["purchases"] = round(m["purchases"] * k[0]); m["revenue"] = round(m["revenue"] * k[1], 2)
            m["roas"] = _roas(m["spend"], m["revenue"]); m["cpa"] = _cpa(m["spend"], m["purchases"])
            rows.append(dict(key="meta:" + r["campaign_id"], platform="meta", platform_name="Meta", name=r["name"], campaign_id=r["campaign_id"], group=r["product"], group_kind="product",
                             product_slug=r["product_slug"], products=r["products"], account=r["account"], stage=r["stage"], ads=r["ads"], families=r["families"], live=r["live"],
                             last_day=r["last_day"], status=None, basis=basis_label[("meta", numbers)], spark=r.get("spark"), m=m))
        mt = {kk: vv for kk, vv in mtot.items() if kk != "spark"}
        mt["purchases"] = round(mt["purchases"] * k[0]); mt["revenue"] = round(mt["revenue"] * k[1], 2); mt["roas"] = _roas(mt["spend"], mt["revenue"]); mt["cpa"] = _cpa(mt["spend"], mt["purchases"])
        subtotals["meta"] = dict(campaigns=len(mrows), ads=mt.get("ads"), m=mt)
    if (only in (None, "google")) and not slug:
        grows, gtot, _info = g_rows("campaign", dict(source="upstack" if numbers != "own" else "meta", basis="all" if numbers == "all" else "nc", **({"type": typ} if typ else {})), win)
        for r in grows:
            rows.append(dict(key="google:" + r["key"], platform="google", platform_name="Google", name=r["label"], campaign_id=r["key"], group=r["campaign_type"], group_kind="type", product_slug=None,
                             products=None, account=None, stage=None, ads=None, families=None, live=None, last_day=None, status=r["status"], basis=basis_label[("google", numbers)], spark=None, m=r["m"]))
        subtotals["google"] = dict(campaigns=len(grows), ads=None, m=gtot)
    rows.sort(key=lambda r: -r["m"]["spend"])
    tot_sp = sum(s_["m"]["spend"] for s_ in subtotals.values())
    for r in rows:
        r["m"]["spend_share"] = round(r["m"]["spend"] / tot_sp, 4) if tot_sp else None
    for s_ in subtotals.values():
        s_["m"]["spend_share"] = round(s_["m"]["spend"] / tot_sp, 4) if tot_sp else None
    imp = sum(s_["m"].get("impressions") or 0 for s_ in subtotals.values()); clk = sum(s_["m"].get("link_clicks") or 0 for s_ in subtotals.values())
    total = dict(spend=round(tot_sp, 2), impressions=imp, link_clicks=clk, campaigns=sum(s_["campaigns"] for s_ in subtotals.values()), spend_share=1.0 if tot_sp else None,
                 ctr=round(clk / imp, 5) if imp else None, cpc=round(tot_sp / clk, 4) if clk else None, additive=len(subtotals) == 1)
    gtypes = g_rows("type", dict(source="meta"), win)[0]
    plat_win = {k: {"from": win["info"].get("from"), "to": win["info"].get("to"), "days": win["info"].get("days"), "label": win["info"].get("label"), "note": None, "clamped": False} for k in subtotals}
    return jsonify(numbers=numbers, platform=only, window_info=win["info"], platform_windows=plat_win, rows=rows, subtotals=subtotals, total=total, notes=[], basis={k: basis_label[(k, numbers)] for k in subtotals},
                   platforms=[dict(key="meta", name="Meta", spend=round(sum(c["spend"] for c in product_chips(win)), 2)), dict(key="google", name="Google", spend=round(sum(r["m"]["spend"] for r in gtypes), 2))],
                   products=product_chips(win), types=[dict(key=r["key"], name=r["label"], spend=r["m"]["spend"]) for r in gtypes])


@app.get("/api/platforms/overview")
def platforms_overview():
    win = parse_win({k: v for k, v in request.args.items() if k in ("window", "from", "to")})
    _rows, _bs, tot = campaign_payload(win, None)
    imp, clk = tot.get("impressions"), tot.get("link_clicks")

    def meta_m(pu, rv):
        sp = tot["spend"]
        return dict(spend=sp, purchases=pu, revenue=round(rv, 2), roas=round(rv / sp, 4) if sp else None, cpa=round(sp / pu, 2) if pu else None, impressions=imp, link_clicks=clk,
                    ctr=round(clk / imp, 5) if imp and clk else None, cpc=round(sp / clk, 4) if clk else None)
    meta_rows = [dict(basis="reported", label="Meta's own reporting", labels=dict(purchases="Purchases"), m=meta_m(tot["purchases"], tot["revenue"])),
                 dict(basis="nc", label="Upstack · new customers", labels=dict(purchases="New customers"), m=meta_m(round(tot["purchases"] * 0.7), tot["revenue"] * 0.72)),
                 dict(basis="all", label="Upstack · all orders", labels=dict(purchases="Orders"), m=meta_m(round(tot["purchases"] * 1.2), tot["revenue"] * 1.1))]
    for r in meta_rows:
        r["window"] = dict(win["info"], clamped=False)
    g_rows_out = []
    for basis, src, label in (("reported", "meta", "Google's own reporting"), ("nc", "upstack", "Upstack · new customers"), ("all", "upstack", "Upstack · all orders")):
        _r, total, info = g_rows("type", dict(source=src, basis="all" if basis == "all" else "nc"), win)
        g_rows_out.append(dict(basis=basis, label=label, labels=info["source"]["labels"], m=total, window=dict(win["info"], clamped=False)))
    return jsonify(window_info=win["info"], compare_info=None, platforms=[dict(key="meta", name="Meta", rows=meta_rows, note=None), dict(key="google", name="Google", rows=g_rows_out, note=None)])


@app.get("/api/products/<slug>/stage-daily")
def stage_daily(slug):
    prod_meta(slug)
    if any(k in request.args for k in ("window", "from", "to")):
        win = parse_win({k: v for k, v in request.args.items() if k != "stage"})
        lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    else:
        days = int(request.args.get("days", 90))
        lo, hi = max(0, N - days), N - 1
    out = []
    for i in range(lo, hi + 1):
        d = dict(day=DAYS[i].isoformat(), testing=0.0, scaling=0.0, unclassified=0.0)
        for ad in cur_ads():
            if ad["product"] == slug:
                d["unclassified" if i < UNC_DAYS else ad_stage(ad)] += ad["daily"][i][0]
        d = {k: (round(v, 2) if k != "day" else v) for k, v in d.items()}
        out.append(d)
    return jsonify(out)


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


@app.get("/api/ads/<int:rid>")
def ad_detail(rid):
    row = ROW_BY_ID.get(rid)
    if not row:
        return jsonify(error="not found"), 404
    win = parse_win(request.args)
    fam = FAM_BY_KEY[row["fam"]]
    p = prod_meta(row["product"])
    linemap = p["tag_set"] == "linemap"
    ln = row["line"]
    tag = None
    line = None
    if linemap and ln:
        tag = dict(tag_set="linemap", version="linemap-v2-2026-11-03", line_code=ln[0], cluster=ln[1], icp=ln[2], pain_point=ln[3], emotional_trigger=ln[4],
                   confidence=row["conf"], method=row["method"], seen="A man at a kitchen table holding the bottle; text overlay about testosterone.",
                   why=f"Hook speaks to {ln[3].lower()} for {ln[2]}.", runner_up="L07 · Busy Provider Dad / Afternoon Crash",
                   copy_only_line=dict(line_code="L02", label="Low Drive") if row["conf"] != "high" else None, reliability_note=reliability(row))
        line = dict(line_code=ln[0], story=f"{ln[2]} feels {ln[3].lower()} and wants it fixed without a prescription.", emotional_trigger=ln[4],
                    ingredients=[dict(ingredient=i, attacks=a, supports=s) for i, a, s in INGREDIENTS[:2]])
    is_video = fam["ad_type"] == "VID"
    xss = fam["display"].startswith("XSS")
    content = dict(primary_text=[XSS if xss else f"Tired of {ln[3].lower() if ln else 'feeling run down'}?\nHere is what 3 studies say about {row['ingredient']}.", "Second primary text variant, shorter."],
                   headlines=["Built for men over 40", "The 90-day guarantee"], description="Free shipping on 3+ bottles.", meta_body="Meta body text as running.",
                   meta_title="Vita Bull", meta_desc="Beef organ complex", transcript=("So here's the thing about testosterone... " * 30) if is_video else None, is_video=is_video)
    nxt = [r for r in fam["rows"] if r["id"] != rid]
    L = parse_win({}); D7 = parse_win({"window": "7d"}); D30 = parse_win({"window": "30d"})

    def fm(w):
        sp, pu, rv = metrics_of(fam_split(fam["key"], w)[0], w)
        return dict(spend=round(sp, 2), purchases=int(round(pu)), revenue=round(rv, 2), roas=_roas(sp, rv), cpa=_cpa(sp, pu))
    live = fam_split(fam["key"], L)[2]
    cmp_fam = {}
    if getattr(g, "cmp", None):
        fa, fb = fm(win), fm(g.cmp)
        cmp_fam = dict(compare=fb, delta=delta_block(fa, fb, win, g.cmp))
    series = [0, 0, 0]
    daily = []
    fam_ads = [a for a in cur_ads() if a["fam"] == fam["key"]]
    for i in range(N):
        sp = sum(a["daily"][i][0] for a in fam_ads); pu = sum(a["daily"][i][1] for a in fam_ads); rv = sum(a["daily"][i][2] for a in fam_ads)
        known = [a["tr"][i][0] for a in fam_ads if a["tr"][i][0] is not None]
        daily.append(dict(day=DAYS[i].isoformat(), spend=round(sp, 2), purchases=pu, revenue=round(rv, 2), link_clicks=sum(a["tr"][i][1] for a in fam_ads),
                          impressions=sum(known) if known else None))
    meta_ads = [ad_row(a, win, fam["launch"]) for a in fam_ads]
    spec = spark_request()
    if spec:
        for r, a in zip(meta_ads, fam_ads):
            r["spark"] = spark_for([a], win, spec)
    shares(meta_ads, total_of(meta_ads, win))
    camps = {}
    for r in meta_ads:
        e = camps.setdefault(r["campaign_id"], dict(campaign_id=r["campaign_id"], name=r["campaign_name"], stage=r["stage"], spend=0.0, purchases=0, revenue=0.0))
        e["spend"] += r["spend"]; e["purchases"] += r["purchases"]; e["revenue"] += r["revenue"]
    asset_ids = [fam["thumb"]] if fam["thumb"] else []
    frames = [f"f{rid}{k}" for k in range(4)] if is_video else []
    links = ad_links(fam_ads[0]) if fam_ads else dict(story_url=None, ads_manager_url=None, ad_id=None, account=None)
    return jsonify(id=rid, links=links, product=dict(slug=p["slug"], name=p["name"]), concept_id=row["concept_id"], ad_family=fam["display"], family_key=fam["key"], sheet_row=row["sheet_row"],
                   status=fam["status"], ad_type=fam["ad_type"], style="UGC" if is_video else "Static", vendor="Madre", launch_date=fam["launch"].isoformat() if fam["launch"] else None,
                   concept_name="Doctor says no", description="Founder explains the study.", hypothesis="Skeptics respond to cited studies.", meta_ad_name=fam["display"],
                   story_url="https://example.com/story", asset_url="javascript:alert(1)" if xss else "https://drive.example.com/asset",
                   tag=tag, hook=hook_of(row), tracker_tag=dict(cluster=row["cluster"], icp=row["icp"], pain_point=row["pain"], emotion=row["emotion"], ingredient=row["ingredient"]),
                   line=line, content=content, assets=dict(thumb=fam["thumb"], frames=frames),
                   siblings=[dict(id=r["id"], concept_id=r["concept_id"], line_code=r["line"][0] if r["line"] else None, cluster=r["cluster"], icp=r["icp"], pain_point=r["pain"], status=fam["status"]) for r in nxt],
                   metrics=dict(counted_on_this_row=row["ordinal"] == 0, family=dict(launch=fm(L), d7=fm(D7), d30=fm(D30), window=fm(win), live=live, **cmp_fam)),
                   window_info=win["info"], compare_info=cinfo(), daily=daily, meta_ads=meta_ads,
                   campaigns=[dict(e, spend=round(e["spend"], 2), revenue=round(e["revenue"], 2)) for e in camps.values()])



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


@app.get("/api/products/<slug>/trend")
def trend(slug):
    TREND_CALLS["n"] += 1
    win = parse_win(request.args)
    prod_meta(slug)
    grain = request.args.get("grain", "day")
    split = request.args.get("split", "none")
    if grain not in ("day", "week", "month"):
        raise Bad("bad grain")
    if split not in ("none", "stage", "children"):
        raise Bad("bad split")
    scope = {k: request.args.get(k) for k in ("cluster", "icp", "pain", "family_key", "campaign_id")}
    ads_in = scope_ads(slug, scope)
    lo, hi = (0, N - 1) if win["launch"] else (win["i0"], win["i1"])
    info = dict(win["info"])
    if win["launch"]:
        info["note"] = " ".join(x for x in (info.get("note"), f"Trend covers {lab(DAYS[0])} to {lab(DAYS[-1])} (daily data).") if x)
    arr = sum_series(ads_in, win["stage"])
    points = series_points(arr, lo, hi, grain)
    out = dict(window_info=info, compare_info=cinfo(), grain=grain, scope={k: v for k, v in scope.items()}, points=points)
    if g.cmp:
        b = series_points(sum_series(ads_in, win["stage"]), g.cmp["i0"], g.cmp["i1"], grain)
        out["cmp_points"] = align_points(b, len(points))
    if split == "stage":
        out["by_stage"] = {st: series_points(sum_series(ads_in, frozenset({st})), lo, hi, grain) for st in ALL_STAGES if st in win["stage"]}
    if split == "children":
        flt = {k: scope[k] for k in ("cluster", "icp", "pain") if scope.get(k)}
        groups = []
        if scope.get("family_key") or scope.get("campaign_id") or scope.get("pain"):
            if scope.get("pain") and not (scope.get("family_key") or scope.get("campaign_id")):
                fam_rows, _ = family_rows(slug, flt, parse_win({}))
                for fr in fam_rows:
                    groups.append((fr["family_key"], fr["display"], None, fams_ads([fr["family_key"]]) if fr["counted_here"] else []))
            else:
                for a in ads_in:
                    groups.append((a["ad_id"], a["name"], None, [a]))
        else:
            level = "cluster" if not scope.get("cluster") else ("icp" if not scope.get("icp") else "pain")
            rows, _ = rollup_rows(slug, level, flt, parse_win({}))
            for r in rows:
                fams = [f["key"] for f, rr, ch in filt(prod_items(slug), {**flt, level: r["label"]}) if ch]
                groups.append((r["label"], r["label"], r["color"], fams_ads(fams)))
        scored = []
        for key, label, color, ads_g in groups:
            a_ = sum_series(ads_g, win["stage"])
            scored.append((sum(a_[i][0] for i in range(lo, hi + 1)), key, label, color, ads_g))
        scored.sort(key=lambda x: -x[0])
        top, rest = scored[:8], scored[8:]
        kids = [dict(key=k, label=lb, color=c, points=series_points(sum_series(ag, win["stage"]), lo, hi, grain)) for _, k, lb, c, ag in top]
        if rest:
            other_ads = [a for _, _, _, _, ag in rest for a in ag]
            kids.append(dict(key="__other__", label="Other", color=None, points=series_points(sum_series(other_ads, win["stage"]), lo, hi, grain)))
        out["children"] = kids
    return jsonify(out)


@app.get("/api/_test/trend_calls")
def trend_calls():
    return jsonify(TREND_CALLS)



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


@app.get("/api/ads/<int:rid>/preview")
def ad_preview(rid):
    row = ROW_BY_ID.get(rid)
    if not row:
        return jsonify(error="not found"), 404
    return preview_json(FAM_BY_KEY[row["fam"]]["display"])


@app.get("/api/meta-ads/<ad_id>/preview")
def meta_ad_preview(ad_id):
    ad = next((a for a in cur_ads() if a["ad_id"] == ad_id), None)
    if not ad:
        return jsonify(error="not found"), 404
    return preview_json(ad["name"])


@app.get("/mock/preview")
def mock_preview_page():
    from html import escape
    name, fmt = request.args.get("name", ""), request.args.get("format", "")
    return Response(f"<!doctype html><meta charset=utf-8><body style='font:14px sans-serif;margin:0;padding:16px;background:#fafafa'><div id='mock-ad' style='border:1px solid #ccc;border-radius:8px;padding:12px;background:#fff'><b>{escape(name)}</b><div>Mock Meta preview ({escape(fmt)})</div></div></body>", mimetype="text/html")


@app.get("/media/<aid>")
def media(aid):
    from PIL import Image, ImageDraw
    h = sum(ord(c) * (i + 3) for i, c in enumerate(aid))
    img = Image.new("RGB", (160, 160), ((h * 37) % 200 + 30, (h * 91) % 200 + 30, (h * 53) % 200 + 30))
    ImageDraw.Draw(img).text((10, 10), aid, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = Response(buf.getvalue(), mimetype="image/png")
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r


import mock_promotions  # noqa: E402  (Test to scale: /api/promotions, built on the real payload code in app/promotions.py)

mock_promotions.register(app, dict(parse_win=parse_win, Bad=Bad))

import mock_trends  # noqa: E402  (Trends: /api/trends, built on the real payload code in app/trends.py over made-up days)

mock_trends.register(app, dict(Bad=Bad))

import mock_lookup  # noqa: E402  (Tag lookup: /api/tag-lookup, built on the real matching code in app/lookup.py over a few made-up rows)

mock_lookup.register(app, dict(Bad=Bad))


def shell(*_a, **_k):
    return send_from_directory(STATIC, "index.html")


for rule in ("/", "/login", "/google", "/campaigns", "/promotions", "/trends", "/lookup", "/p/<slug>", "/ad/<rid>"):
    app.add_url_rule(rule, endpoint="shell" + rule, view_func=shell)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5099
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
