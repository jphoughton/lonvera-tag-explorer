"""Change events for the daily story: what changed on a given day, detected from the data (and from the Meta change feed when it exists).

Each event is dated to the day it first showed, carries a type and a magnitude bucket (the keys of the pattern memory), and a `ref` that lets the ledger work out the
share of the target day's Meta spend it touches. Detection thresholds are deliberately plain: a share moving 10 points or more against the previous 7 days, a spend
level 25% off the previous 7 days, a send volume 1.5x its previous 28-day median. Events are facts about the data, not causes.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import daily_story_math as M
from .daily_story_facts import UNTAGGED, Memo, _fam, key_of, trailing_days
from .daily_story_panel import PRODUCT_LABEL, START, Panel

PP_EDGES, PP_LABELS = (20, 40), ("10-20 points", "20-40 points", "40+ points")


def pp_bucket(x: float) -> str:
    return M.bucket(abs(x) * 100, PP_EDGES, PP_LABELS)


def count_bucket(n: int, edges, labels) -> str:
    return M.bucket(n, edges, labels)


def _ev(typ, bucket, subject=None, detail="", magnitude=None, ref=None, label=None):
    return {"type": typ, "bucket": bucket, "subject": subject, "detail": detail, "magnitude": magnitude, "ref": ref, "label": label or typ}


def share_map(panel: Panel, days: list[dt.date], cut: str) -> dict[str, float]:
    sp = defaultdict(float)
    for d in days:
        for r in panel.rows.get(d, []):
            sp[key_of(panel, cut, r)] += r["spend"]
    tot = sum(sp.values())
    return {k: v / tot for k, v in sp.items()} if tot else {}


def _pooled_share(panel, days, fn):
    num = den = 0.0
    for d in days:
        for r in panel.rows.get(d, []):
            den += r["spend"]
            if fn(r):
                num += r["spend"]
    return num / den if den else None


def events_for_day(panel: Panel, memo: Memo, d: dt.date) -> list[dict]:
    """All events that showed on day d (needs the previous days in the panel; returns [] when d has no Meta data)."""
    a = panel.acct.get(d)
    if not a or not a["has_meta"]:
        return []
    ev: list[dict] = []
    prior = trailing_days(panel, d, 7)
    enough = len(prior) >= 3
    rows = panel.rows.get(d, [])
    tot = a["spend"]
    have_feed = panel.feed_name is not None and d >= min(panel.feed_events, default=dt.date.max)

    # ---- spend level
    if enough:
        pm = M.mean([panel.acct[x]["spend"] for x in prior])
        if pm and tot / pm >= 1.25:
            ev.append(_ev("budget_up", "up 25-50%" if tot / pm < 1.5 else "up 50%+", detail=f"Meta spend {tot / pm - 1:+.0%} vs the previous 7 days", magnitude=tot / pm - 1, label="Meta spend up"))
        elif pm and tot / pm <= 0.75:
            ev.append(_ev("budget_down", "down 25-50%" if tot / pm > 0.5 else "down 50%+", detail=f"Meta spend {tot / pm - 1:+.0%} vs the previous 7 days", magnitude=tot / pm - 1, label="Meta spend down"))
    # ---- bids
    cs = M.ratio(a.get("capped_spend"), a.get("bid_spend"))
    pcs = M.ratio(sum(panel.acct[x]["capped_spend"] for x in prior if panel.acct[x].get("capped_spend") is not None), sum(panel.acct[x]["bid_spend"] for x in prior if panel.acct[x].get("bid_spend") is not None)) if enough else None
    if cs is not None and pcs is not None and abs(cs - pcs) >= 0.10:
        ev.append(_ev("capped_share_up" if cs > pcs else "capped_share_down", pp_bucket(cs - pcs), detail=f"capped share of Meta spend {cs:.0%} vs {pcs:.0%} over the previous 7 days", magnitude=cs - pcs, label="Share of spend on capped ad sets"))
    if cs is not None and cs >= 0.90:
        ev.append(_ev("capped_share_high", "90-95%" if cs < 0.95 else "95%+", detail=f"capped share of Meta spend {cs:.0%}", magnitude=cs, label="Capped share over 90%"))
    # ---- structure shares
    for typ, label, fn, scaling_only in (("troas_share", "tROAS share of scaling spend", lambda r: "troas" in (r["campaign_name"] or "").lower(), True),
                                          ("d7_share", "7-day-click share of scaling spend", lambda r: "7dc" in (r["campaign_name"] or "").lower(), True),
                                          ("testing_share", "Testing share of Meta spend", lambda r: (r["stage"] or "") == "testing", False)):
        if not enough:
            break
        sel = (lambda r: (r["stage"] or "") == "scaling") if scaling_only else (lambda r: True)
        def share(days):
            num = den = 0.0
            for x in days:
                for r in panel.rows.get(x, []):
                    if sel(r):
                        den += r["spend"]
                        if fn(r):
                            num += r["spend"]
            return num / den if den else None
        s1, s0 = share([d]), share(prior)
        if s1 is not None and s0 is not None and abs(s1 - s0) >= 0.10:
            ev.append(_ev(f"{typ}_{'up' if s1 > s0 else 'down'}", pp_bucket(s1 - s0), detail=f"{label} {s1:.0%} vs {s0:.0%} over the previous 7 days", magnitude=s1 - s0, label=label))
    # ---- ad sets and campaigns (derived only when no change feed exists)
    if not have_feed:
        new_as = {x["adset_id"] for x in panel.adsets.get(d, []) if panel.adset_first.get(x["adset_id"]) == d and d > START}
        if new_as:
            ev.append(_ev("new_adsets", count_bucket(len(new_as), (3, 10), ("1-2 ad sets", "3-9 ad sets", "10+ ad sets")), detail=f"{len(new_as)} ad sets spent for the first time", magnitude=len(new_as), ref={"adsets": sorted(new_as)}, label="New ad sets"))
        by_c = defaultdict(float)
        for r in rows:
            by_c[r["campaign_name"]] += r["spend"]
        prev_c = defaultdict(float)
        for x in prior:
            for r in panel.rows.get(x, []):
                prev_c[r["campaign_name"]] += r["spend"]
        if enough:
            for c, sp in by_c.items():
                if sp >= 50 and prev_c.get(c, 0.0) == 0.0:
                    ev.append(_ev("campaign_launch", "campaign restarted or new", subject=c, detail=f"{c}: first spend in 7+ days (${sp:,.0f})", magnitude=sp, ref={"campaign": c}, label="Campaign launched"))
            yest = d - dt.timedelta(days=1)
            ysp = defaultdict(float)
            for r in panel.rows.get(yest, []):
                ysp[r["campaign_name"]] += r["spend"]
            for c, sp in ysp.items():
                if sp >= 100 and by_c.get(c, 0.0) == 0.0:
                    ev.append(_ev("campaign_pause", "campaign stopped spending", subject=c, detail=f"{c}: spent ${sp:,.0f} yesterday, nothing today", magnitude=sp, ref={"campaign_yesterday": c}, label="Campaign stopped"))
    else:
        for e in panel.feed_events.get(d, []):
            n = e.get("count") or 1
            ev.append(_ev(f"feed_{e['type']}", count_bucket(n, (3, 10), ("1-2 objects", "3-9 objects", "10+ objects")), subject=e.get("subject"),
                          detail=f"{n} x {e['type'].replace('_', ' ')}" + (f": {e['detail']}" if e.get("detail") else ""), magnitude=n,
                          ref={"adsets": e["adset_ids"]} if e.get("adset_ids") else None, label=f"Meta change: {e['type'].replace('_', ' ')}"))
    # ---- creative supply
    fams = {r["fk"] for r in rows if r["spend"] > 0}
    launched = {f for f in fams if panel.fam_first.get(f) == d and d > START}
    if len(launched) >= 5:
        ev.append(_ev("families_launched", count_bucket(len(launched), (10, 30), ("5-9 families", "10-29 families", "30+ families")), detail=f"{len(launched)} ad families spent for the first time", magnitude=len(launched), ref={"families": sorted(launched)}, label="Ad families launched"))
    yest = d - dt.timedelta(days=1)
    if yest in panel.rows:
        ysp = defaultdict(float)
        for r in panel.rows[yest]:
            ysp[r["fk"]] += r["spend"]
        stopped = {f for f, sp in ysp.items() if sp >= 50 and f not in fams}
        if len(stopped) >= 3:
            ev.append(_ev("ads_stopped", count_bucket(len(stopped), (10, 30), ("3-9 families", "10-29 families", "30+ families")), detail=f"{len(stopped)} ad families spent $50+ yesterday and nothing today (${sum(ysp[f] for f in stopped):,.0f} yesterday)", magnitude=len(stopped), label="Ad families stopped spending"))
    if enough:
        for cut, label in (("cluster", "market cluster"), ("icp", "ICP")):
            s1, s0 = share_map(panel, [d], cut), share_map(panel, prior, cut)
            for k in set(s1) | set(s0):
                if k == UNTAGGED:
                    continue
                if s1.get(k, 0) >= 0.02 and s0.get(k, 0) == 0:
                    ev.append(_ev(f"{cut}_entered", f"entered the live mix (2%+ of spend)", subject=k, detail=f"{label} {k}: {s1[k]:.0%} of spend, none in the previous 7 days", magnitude=s1[k], ref={cut: k}, label=f"{label.capitalize()} entered the live mix"))
                elif s0.get(k, 0) >= 0.02 and s1.get(k, 0) == 0:
                    ev.append(_ev(f"{cut}_left", f"left the live mix (was 2%+ of spend)", subject=k, detail=f"{label} {k}: no spend, was {s0[k]:.0%} over the previous 7 days", magnitude=s0[k], label=f"{label.capitalize()} left the live mix"))
        sc1, sc0 = share_map(panel, [d], "cluster"), share_map(panel, prior, "cluster")
        mv = [(sc1.get(k, 0) - sc0.get(k, 0), k) for k in set(sc1) | set(sc0) if k != UNTAGGED]
        if mv:
            dlt, k = max(mv, key=lambda x: abs(x[0]))
            if abs(dlt) >= 0.10:
                ev.append(_ev("cluster_share_up" if dlt > 0 else "cluster_share_down", pp_bucket(dlt), subject=k, detail=f"{k}: {sc1.get(k, 0):.0%} of spend vs {sc0.get(k, 0):.0%} over the previous 7 days", magnitude=dlt, ref={"cluster": k}, label="Market cluster share of spend"))
        n1 = memo.day(d).get("new_ad_share")
        n0 = M.mean([memo.day(x).get("new_ad_share") for x in prior])
        if n1 is not None and n0 is not None and abs(n1 - n0) >= 0.10:
            ev.append(_ev("new_ad_share_up" if n1 > n0 else "new_ad_share_down", pp_bucket(n1 - n0), detail=f"share of spend on ads aged 7 days or less {n1:.0%} vs {n0:.0%}", magnitude=n1 - n0, label="New-ad share of spend"))
        v1, v0 = share_map(panel, [d], "media").get("Video"), share_map(panel, prior, "media").get("Video")
        if v1 is not None and v0 is not None and abs(v1 - v0) >= 0.10:
            ev.append(_ev("format_share_up" if v1 > v0 else "format_share_down", pp_bucket(v1 - v0), subject="Video", detail=f"video share of spend {v1:.0%} vs {v0:.0%}", magnitude=v1 - v0, label="Video share of spend"))
        top = max(rows, key=lambda r: r["spend"]) if rows else None
        if top and tot and top["spend"] / tot >= 0.10 and yest in panel.rows and panel.rows[yest]:
            ytop = max(panel.rows[yest], key=lambda r: r["spend"])
            if ytop["fk"] != top["fk"]:
                h = _fam(panel, top["fk"]).get("hook")
                ev.append(_ev("top_hook_changed", "new top ad (10%+ of spend)", subject=(h or _fam(panel, top["fk"]).get("name") or top["fk"])[:120], detail=f"top ad by spend changed; new top is {top['spend'] / tot:.0%} of spend", magnitude=top["spend"] / tot, ref={"families": [top["fk"]]}, label="Top ad changed"))
    mv_n = panel.stage_moves.get(d, 0)
    if mv_n >= 5:
        ev.append(_ev("moves_to_scaling", "5-19 families" if mv_n < 20 else "20+ families", detail=f"{mv_n} ad families reached scaling spend that day", magnitude=mv_n, label="Ads moved to scaling"))
    if panel.lp_changes.get(d):
        ev.append(_ev("new_lp", "page content changed", detail="; ".join((x.get("title") or str(x["landing_page_id"])) for x in panel.lp_changes[d])[:200], magnitude=len(panel.lp_changes[d]), label="Landing page changed"))

    # ---- Klaviyo
    kv = panel.klaviyo.get(d)
    prior28 = [x for x in (d - dt.timedelta(days=k) for k in range(1, 29)) if x in panel.klaviyo]
    if kv is not None and len(prior28) >= 10:
        def tot_(day, src, ch):
            return (panel.klaviyo.get(day, {}).get((src, ch)) or {}).get("sends", 0.0)
        for src, ch, typ, floor in (("campaign", "email", "big_email_send", 5000.0), ("campaign", "sms", "sms_blast", 1000.0)):
            v = tot_(d, src, ch)
            med = M.median([tot_(x, src, ch) for x in prior28])
            if med is not None and v >= floor and v >= 1.5 * max(med, 1.0):
                top = next((x for x in panel.klaviyo_top.get(d, []) if x["source"] == src and x["channel"] == ch), None)
                ev.append(_ev(typ, "1.5-2x the usual" if v < 2 * max(med, 1) else "2x or more", subject=top["name"] if top else None, detail=f"{ch} campaign sends {v:,.0f} vs a {med:,.0f} median", magnitude=v / max(med, 1.0), label="Large email campaign send" if ch == "email" else "Large text campaign send"))
        fv = sum(tot_(d, "flow", c) for c in ("email", "sms"))
        fm = M.median([sum(tot_(x, "flow", c) for c in ("email", "sms")) for x in prior28])
        if fm and abs(fv - fm) >= 500 and (fv >= 1.5 * fm or fv <= fm / 1.5):
            ev.append(_ev("flow_volume_up" if fv > fm else "flow_volume_down", "1.5x or more" if fv > fm else "a third or more below", detail=f"flow sends {fv:,.0f} vs a {fm:,.0f} median", magnitude=fv / fm - 1, label="Flow send volume"))
    # ---- Google and Amazon spend shifts
    if enough:
        gp = M.mean([panel.acct[x]["g_spend"] for x in prior if panel.acct[x].get("g_spend") is not None])
        g1 = a.get("g_spend")
        if gp and g1 is not None and abs(g1 - gp) >= 100 and (g1 >= 1.3 * gp or g1 <= 0.7 * gp):
            ev.append(_ev("google_spend_up" if g1 > gp else "google_spend_down", "30-60%" if abs(g1 / gp - 1) < 0.6 else "60%+", detail=f"Google spend ${g1:,.0f} vs ${gp:,.0f} per day over the previous 7 days", magnitude=g1 / gp - 1, label="Google spend"))
        if d in panel.amazon:
            ap = M.mean([panel.amazon[x]["ad_spend"] for x in prior if x in panel.amazon])
            a1 = panel.amazon[d]["ad_spend"]
            if ap and abs(a1 - ap) >= 100 and (a1 >= 1.3 * ap or a1 <= 0.7 * ap):
                ev.append(_ev("amazon_spend_up" if a1 > ap else "amazon_spend_down", "30-60%" if abs(a1 / ap - 1) < 0.6 else "60%+", detail=f"Amazon ad spend ${a1:,.0f} vs ${ap:,.0f} per day over the previous 7 days", magnitude=a1 / ap - 1, label="Amazon ad spend"))
    return ev


def ledger(panel: Panel, memo: Memo, D: dt.date, days_back: int = 7) -> list[dict]:
    """Events for D-days_back..D, each with its first day and the share of D's Meta spend it touches (None when the change is not tied to spend)."""
    rowsD = panel.rows.get(D, [])
    totD = sum(r["spend"] for r in rowsD)
    out = []
    for k in range(days_back, -1, -1):
        d = D - dt.timedelta(days=k)
        for e in events_for_day(panel, memo, d):
            ref, share = e.get("ref") or {}, None
            if totD:
                if "adsets" in ref:
                    ids = set(ref["adsets"])
                    share = sum(x["spend"] for x in panel.adsets.get(D, []) if x["adset_id"] in ids) / totD
                elif "campaign" in ref:
                    share = sum(r["spend"] for r in rowsD if r["campaign_name"] == ref["campaign"]) / totD
                elif "families" in ref:
                    fs = set(ref["families"])
                    share = sum(r["spend"] for r in rowsD if r["fk"] in fs) / totD
                elif "cluster" in ref:
                    share = sum(r["spend"] for r in rowsD if key_of(panel, "cluster", r) == ref["cluster"]) / totD
                elif "icp" in ref:
                    share = sum(r["spend"] for r in rowsD if key_of(panel, "icp", r) == ref["icp"]) / totD
            out.append({"first_day": d.isoformat(), "days_before_D": k, "type": e["type"], "label": e["label"], "bucket": e["bucket"], "subject": e["subject"], "detail": e["detail"],
                        "share_of_D_meta_spend": share, "source": "Meta change feed" if e["type"].startswith("feed_") else "derived from the daily tables"})
    return out
