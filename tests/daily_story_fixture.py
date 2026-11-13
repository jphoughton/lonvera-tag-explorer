"""A made-up Panel for the daily story tests and the UI mock: 70 days (2026-09-03 .. 2026-11-11) of family x campaign rows, orders, Google, Klaviyo and Amazon with a weekly rhythm,
a tROAS 7-day-click campaign that ramps up on 2026-11-10, a new ad set on 2026-11-09 and a large SMS send on 2026-11-11. Deterministic (fixed random seed)."""
from __future__ import annotations

import datetime as dt
import math
import random

from etl.daily_story_panel import Panel, structure_of

D0, D1 = dt.date(2026, 9, 3), dt.date(2026, 11, 11)
FAMS = {
    "fam-a": dict(name="Liver listicle", product_slug="beef-liver", cluster="Ancestral Nutrition", icp="Primal Living", pain_point="Low Energy", emotional_trigger="Desire", line_code="B50", format="Video: UGC Talking Head", media="Video", vendor="Madre", hook="The number one deficiency in the world equals copper."),
    "fam-b": dict(name="Bull static", product_slug="vita-bull", cluster="Energy & Multivitamin", icp="The Wired-Tired", pain_point="Low Energy", emotional_trigger="Frustration", line_code="B34", format="Image: Photo-Hero Benefit Ad", media="Image", vendor="Victoria", hook=None),
    "fam-c": dict(name="Bone flex demo", product_slug="boneflex", cluster="Joint Supplements", icp="Weekend Warrior", pain_point="Joint Pain", emotional_trigger="Fear", line_code="BF11", format="Video: Voiceover B-Roll Video", media="Video", vendor="Other", hook="Stop guessing about your knees."),
    "fam-d": dict(name="New explainer", product_slug="beef-liver", cluster="Hair Thinning", icp="Thinning and Drained", pain_point="Hair Loss", emotional_trigger="Shame", line_code="B12", format="Video: AI UGC", media="Video", vendor="Madre", hook="Ferritin first."),
}
FAM_FIRST = {"fam-a": D0 - dt.timedelta(days=60), "fam-b": D0 - dt.timedelta(days=20), "fam-c": D0 - dt.timedelta(days=5), "fam-d": dt.date(2026, 11, 9)}


def make_panel(until: dt.date = D1, with_ic: bool = False) -> Panel:
    R = random.Random(11)
    p = Panel()
    p.has_ic = with_ic
    p.fam = {k: {**v, "fk": k, "concept_id": k.upper()} for k, v in FAMS.items()}
    p.fam_first = dict(FAM_FIRST)
    p.fam_created = {k: v for k, v in FAM_FIRST.items()}
    p.coverage = {s: {"source": s, "first_day": D0, "last_day": until} for s in ("meta_ad_daily", "orders_daily", "google_campaign_daily", "upstack_meta_attribution")}
    d = D0
    while d <= until:
        wk = 1.0 + 0.15 * math.sin(d.weekday() / 7 * 2 * math.pi)
        big = d >= dt.date(2026, 11, 10)
        camps = [("BeefLiver_WW_Scaling_CostCaps_CBO_1DC", "scaling", 0.45 if not big else 0.2), ("VitaBull_WW_Scaling_tROAS_CBO_7DC", "scaling", 0.05 if not big else 0.45), ("All_WW_Testing_CostCaps_ABO_1DC", "testing", 0.2)]
        rows = []
        for fk in ("fam-a", "fam-b", "fam-c", "fam-d"):
            if fk == "fam-d" and d < dt.date(2026, 11, 9):
                continue
            for cname, stage, w in camps:
                spend = round(3000 * wk * w * R.uniform(0.8, 1.2) * (0.3 if fk == "fam-d" else 1.0) / 2, 2)
                imp = spend * R.uniform(16, 19) * 1000 / 1000 * 1000 / 1000 * 1000 / 1000
                imp = spend / 0.055 * 1000 / 1000
                clicks = imp * 0.026
                nc = round(clicks * 0.02 * R.uniform(0.7, 1.3))
                ic = clicks * 0.1 if with_ic else None
                rows.append({"day": d, "fk": fk, "product": FAMS[fk]["product_slug"], "campaign_name": cname, "stage": stage, "spend": spend, "impressions": imp, "clicks": clicks, "lpv": clicks * 0.84,
                             "atc": clicks * 0.84 * 0.11, "ic": ic, "nc": float(nc), "nc_rev": nc * R.uniform(66, 80), "mp": float(nc) * 1.1, "mrev": nc * 90.0, "n_ads": 2.0,
                             "structure": structure_of(cname, stage)})
        p.rows[d] = rows
        a = {k: sum(r[k] for r in rows) for k in ("spend", "impressions", "clicks", "lpv", "atc", "nc", "nc_rev", "mp", "mrev", "n_ads")}
        a["ic"] = sum(r["ic"] for r in rows) if with_ic else None
        a["has_meta"] = True
        a["has_orders"] = a["has_google"] = True
        a["acct_nc"] = round(a["nc"] * 1.35)
        a["acct_nc_rev"] = a["acct_nc"] * R.uniform(66, 80)
        a["returning_rev"] = 18000 * wk
        a["orders"] = a["acct_nc"] + 300
        gs = 500 * wk
        a.update({"g_spend": gs, "g_clicks": gs / 3.5, "g_impr": gs * 200, "g_gp": gs / 80, "g_grev": gs * 1.1, "g_nc": gs / 100, "g_nc_rev": gs / 100 * 70,
                  "bid_spend": a["spend"], "capped_spend": a["spend"] * (0.6 if not big else 0.55), "floor_spend": a["spend"] * 0.3})
        p.acct[d] = a
        p.adsets[d] = [{"adset_id": f"as{i}", "name": f"adset {i}", "campaign": camps[0][0], "stage": "scaling", "spend": a["spend"] / 6} for i in range(5)]
        if d >= dt.date(2026, 11, 9):
            p.adsets[d].append({"adset_id": "as-new", "name": "new adset", "campaign": camps[1][0], "stage": "scaling", "spend": a["spend"] * 0.1})
        p.klaviyo[d] = {("campaign", "email"): dict(sends=12000.0, delivered=11900.0, opens=6000.0, clicks=120.0, unsubs=40.0, revenue=1200.0, orders=20.0),
                        ("flow", "email"): dict(sends=1500.0, delivered=1490.0, opens=700.0, clicks=60.0, unsubs=5.0, revenue=1900.0, orders=30.0),
                        ("campaign", "sms"): dict(sends=(4500.0 if d == dt.date(2026, 11, 11) else 0.0), delivered=0.0, opens=0.0, clicks=50.0, unsubs=3.0, revenue=300.0, orders=5.0),
                        ("flow", "sms"): dict(sends=40.0, delivered=40.0, opens=0.0, clicks=2.0, unsubs=0.0, revenue=50.0, orders=1.0)}
        p.klaviyo_top[d] = [{"source": "campaign", "channel": "email", "name": "Weekly send", "sends": 12000.0, "delivered": 11900.0, "revenue": 1200.0, "orders": 20.0}]
        p.google_type[d] = {"Shopping": dict(spend=gs * 0.6, clicks=gs / 3.5 * 0.6, impressions=1.0, gp=1.0, grev=gs * 0.7, nc=2.0, nc_rev=140.0), "Search": dict(spend=gs * 0.4, clicks=gs / 3.5 * 0.4, impressions=1.0, gp=1.0, grev=gs * 0.4, nc=1.0, nc_rev=70.0)}
        p.amazon[d] = {"rev": 5000 * wk, "nc_rev": 1800.0, "ad_spend": 800 * wk, "orders": 110.0, "units": 119.0, "nc_orders": 30.0, "attr_sales": 1500.0, "clicks": 200.0, "impressions": 40000.0}
        p.traffic[d] = 3000.0
        d += dt.timedelta(days=1)
    for k, v in FAM_FIRST.items():
        pass
    p.adset_first = {f"as{i}": D0 - dt.timedelta(days=30) for i in range(5)}
    p.adset_first["as-new"] = dt.date(2026, 11, 9)
    return p
