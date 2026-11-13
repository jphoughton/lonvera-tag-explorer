"""Daily story: every morning, one very concise explanation of why yesterday's performance was what it was, built from every metric in the database, plus the pattern memory.

  python -m etl.daily_story                      yesterday (Pacific), only when the sources are loaded (else a 'data not ready' row naming the source); NON-FATAL nightly step
  python -m etl.daily_story --day 2026-11-11     one past day (recomputes its facts and story)
  python -m etl.daily_story --backfill [--since 2026-05-06] [--llm-days N]   facts + template story for every day since the Meta ad-level start, then the pattern memory
  python -m etl.daily_story --dry-run --day D    print the facts summary and story, write nothing

Tables: daily_story_facts (day, facts jsonb), daily_story (day, headline, story, confidence, cannot_see, model), daily_story_patterns, daily_story_metrics (the registry).
See docs/DAILY_STORY.md for the definitions, the tests and the limits. `deliver(story)` is the (empty) hook where Slack or email will go.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys

from . import daily_story_events as EV, daily_story_facts as F, daily_story_math as M, daily_story_narrative as N, daily_story_registry as REG, db
from .daily_story_panel import BREAKDOWN_START, SHOPIFY_START, START, Panel, load

PT_OFFSET_NOTE = "Pacific days"
CORE_SOURCES = (("meta_ad_daily", "Meta ad data"), ("orders_daily", "Upstack orders"), ("google_campaign_daily", "Google spend"), ("upstack_meta_attribution", "Upstack Meta attribution"))
STATIC_CANNOT_SEE = [
    "checkout and payment events (initiated-checkout counts only when the column exists; no payment-step or abandoned-checkout data)",
    "promotions, offers and price changes",
    "site changes other than landing page content hashes (history from 2026-11-11)",
    "budgets, bids and caps by ad set (only the account-level capped vs ROAS-floor spend share)",
    "placement, device, age and country with Upstack attribution (placement is Meta-reported, from 2026-10-12)",
    "repeat, subscription and rebill behavior beyond order totals",
    "auction and competitor effects",
]


def pacific_yesterday(now: dt.datetime | None = None) -> dt.date:
    import zoneinfo
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(zoneinfo.ZoneInfo("America/Los_Angeles")).date() - dt.timedelta(days=1)


def deliver(story: dict) -> None:
    """Delivery hook (Slack, email): the channel is not chosen yet, so this does nothing."""
    return None


# ------------------------------------------------------------------------------------------------------------------------------------ readiness

def readiness(panel: Panel, d: dt.date) -> dict:
    a = panel.acct.get(d, {})
    missing = []
    have = {"meta_ad_daily": a.get("has_meta"), "orders_daily": a.get("has_orders"), "google_campaign_daily": a.get("has_google")}
    for src, label in CORE_SOURCES:
        cov = panel.coverage.get(src)
        ok = have.get(src) if src in have else bool(cov and cov["last_day"] and cov["last_day"] >= d)
        if not ok:
            missing.append(label)
    opt_missing, opt_absent = [], []
    kmin = min(panel.klaviyo) if panel.klaviyo else None
    for name, present, starts in (("Klaviyo", d in panel.klaviyo, kmin), ("Amazon", d in panel.amazon, START), ("Shopify order mix", d in panel.shop, SHOPIFY_START),
                                  ("placement breakdown", d in panel.place, BREAKDOWN_START), ("initiated checkouts", panel.has_ic, None)):
        if present:
            continue
        (opt_absent if (starts is not None and d < starts) or (starts is None and not panel.has_ic) else opt_missing).append(name)
    return {"ready": not missing, "missing_core": missing, "optional_missing": opt_missing, "optional_not_yet_available": opt_absent,
            "coverage": "not_ready" if missing else ("full" if not opt_missing else "partial")}


def cannot_see(panel: Panel, d: dt.date, rd: dict, live: dict | None) -> list[str]:
    out = list(STATIC_CANNOT_SEE)
    if panel.feed_name is None:
        out.insert(0, "the Meta change log (budget, bid and cap edits are not loaded; changes are inferred from first-seen ad sets and families and from spend shifts)")
    if not panel.has_ic:
        out.append("Meta initiated-checkout counts (column not present in meta_ad_daily yet)")
    for n in rd["optional_missing"]:
        out.append(f"{n} for this day (not loaded)")
    if live and live.get("tagged_spend_share") is not None and live["tagged_spend_share"] < 0.9:
        out.append(f"creative tags for {1 - live['tagged_spend_share']:.0%} of spend (untagged families)")
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ facts

def compute_facts(panel: Panel, d: dt.date, memo: F.Memo | None = None) -> dict:
    memo = memo or F.Memo(panel)
    rd = readiness(panel, d)
    base = {"version": F.VERSION, "day": d.isoformat(), "weekday": F.WEEKDAYS[d.weekday()], "readiness": rd}
    if not rd["ready"]:
        base.update({"cannot_see": [f"{m} not loaded for {d.isoformat()}" for m in rd["missing_core"]]})
        return base
    bd = F.baseline_days(panel, d)
    metrics = F.metric_table(panel, memo, d)
    dec = F.decomposition(panel, d)
    cuts = {c: F.cut_table(panel, d, c) for c in F.CUTS}
    movers = {"families": F.family_movers(panel, d)}
    ct = cuts.get("cluster")
    if ct:
        movers["clusters"] = [{"cluster": r["key"], "share": r["share"], "base_share": r["base_share"], "attr_roas": r["day"]["attr_roas"] if r["day"] else None, "base_attr_roas": r["base"]["attr_roas"],
                               "effect": r["effect"]} for r in sorted([x for x in ct["rows"] if x["effect"]], key=lambda x: -abs(x["effect"]["total"]))[:4]]
    live = F.live_summary(panel, d, memo)
    ledger = EV.ledger(panel, memo, d)
    own = [{k: e[k] for k in ("type", "bucket", "subject", "magnitude")} for e in EV.events_for_day(panel, memo, d)]
    nc = metrics["nc_roas"]
    anomalies = sorted([{"key": k, "label": m["label"], "value": m["value"], "baseline_median": m["baseline_median"], "z": m["z"], "flag": m["flag"]} for k, m in metrics.items() if m["flag"]],
                       key=lambda x: -abs(x["z"]))[:12]
    ctl_vals = [{"day": b.isoformat(), "nc_roas": memo.day(b).get("nc_roas")} for b in bd]
    facts = {**base, "windows": {"day": d.isoformat(), "baseline_same_weekday": [b.isoformat() for b in bd], "trailing_7d": [x.isoformat() for x in sorted(F.trailing_days(panel, d, 7))],
                               "noise": "sample standard deviation of the previous 28 days of the same daily metric"},
             "metrics": metrics, "decomposition": dec, "anomalies": anomalies, "cuts": cuts, "movers": movers, "creative_live": live, "changes": ledger, "events": own,
             "control": {"weekday": F.WEEKDAYS[d.weekday()], "same_weekday_days": ctl_vals, "median": nc["baseline_median"], "day": nc["value"], "pct_vs_median": nc["pct_vs_baseline"], "z": nc["z"],
                         "note": "a same-weekday control removes the weekday pattern only; it does not hold the changes in the ledger constant"},
             "sections": {"placement": F.placement_section(panel, d), "google": F.google_section(panel, memo, d), "klaviyo": F.klaviyo_section(panel, memo, d), "amazon": F.amazon_section(panel, d)}}
    facts["cannot_see"] = cannot_see(panel, d, rd, live)
    facts["basis_notes"] = ["Headline NC ROAS = actual new-customer revenue / (Meta + Google spend); gross of tax and refunds; the last 3 to 7 loaded days still move",
                            "Ad-attributed NC ROAS (cuts) is Upstack any-click 30-day attribution of new customers to Meta ads; it is a cut for comparing ads, never the day's headline",
                            "Klaviyo revenue is Klaviyo-attributed and Amazon is a separate channel: neither is added to NC revenue"]
    return _clean(facts)


def _clean(o):
    """JSON-safe: no NaN / inf, dates as text."""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items() if k != "ref"}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    return o


# ------------------------------------------------------------------------------------------------------------------------------------ storage

def store_facts(conn, d: dt.date, facts: dict) -> None:
    conn.execute("""INSERT INTO daily_story_facts (day, facts, version, coverage, created_at) VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (day) DO UPDATE SET facts = EXCLUDED.facts, version = EXCLUDED.version, coverage = EXCLUDED.coverage, created_at = now()""",
                 (d, db.jsonb(facts), facts["version"], facts["readiness"]["coverage"]))


def store_story(conn, d: dt.date, s: dict) -> None:
    conn.execute("""INSERT INTO daily_story (day, headline, story, confidence, cannot_see, model, source, note, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
                    ON CONFLICT (day) DO UPDATE SET headline=EXCLUDED.headline, story=EXCLUDED.story, confidence=EXCLUDED.confidence, cannot_see=EXCLUDED.cannot_see,
                      model=EXCLUDED.model, source=EXCLUDED.source, note=EXCLUDED.note, created_at=now()""",
                 (d, s["headline"], s["story"], db.jsonb(s.get("confidence") or {}), db.jsonb(s.get("cannot_see") or []), s.get("model"), s.get("source") or "template", s.get("note") or ""))


def summarise(facts: dict) -> str:
    if not facts["readiness"]["ready"]:
        return f"{facts['day']}: not ready ({', '.join(facts['readiness']['missing_core'])})"
    m = facts["metrics"]["nc_roas"]
    return f"{facts['day']} {facts['weekday'][:3]} NC ROAS {m['value']:.3f} vs {m['baseline_median'] if m['baseline_median'] is None else round(m['baseline_median'], 3)} z={m['z'] if m['z'] is None else round(m['z'], 2)} coverage={facts['readiness']['coverage']} changes={len(facts['changes'])}"


# ------------------------------------------------------------------------------------------------------------------------------------ run

def run_day(conn, d: dt.date, *, use_llm: bool = True, dry_run: bool = False, panel: Panel | None = None) -> dict:
    """Facts + story for one day. The panel is loaded for d - 40 days .. d unless given (backfill passes its own)."""
    own = panel is None
    panel = panel or load(conn, max(START, d - dt.timedelta(days=40)), d)
    facts = compute_facts(panel, d)
    if use_llm:
        story = N.generate(facts)
    else:
        story = {**N.template(facts), "model": None, "source": "template", "note": ""}
    if not dry_run:
        REG.upsert(conn)
        store_facts(conn, d, facts)
        store_story(conn, d, story)
        conn.commit()
    return {"facts": facts, "story": story}


def run_nightly(conn, target: dt.date | None = None) -> dict:
    from . import daily_story_patterns as P
    d = target or pacific_yesterday()
    out = run_day(conn, d)
    P.update(conn, d)
    conn.commit()
    deliver(out["story"])
    return {"day": d.isoformat(), "coverage": out["facts"]["readiness"]["coverage"], "source": out["story"]["source"], "note": out["story"].get("note", "")}


def backfill(conn, since: dt.date, until: dt.date, llm_days: list[dt.date] | None = None, log=db.log) -> dict:
    from . import daily_story_patterns as P
    panel = load(conn, since, until)
    memo = F.Memo(panel)
    REG.upsert(conn)
    llm_set = set(llm_days or [])
    cov = {"full": 0, "partial": 0, "not_ready": 0}
    d = since
    while d <= until:
        facts = compute_facts(panel, d, memo)
        story = N.generate(facts) if d in llm_set else {**N.template(facts), "model": None, "source": "template", "note": "backfill"}
        store_facts(conn, d, facts)
        store_story(conn, d, story)
        cov[facts["readiness"]["coverage"]] += 1
        d += dt.timedelta(days=1)
    conn.commit()
    npat = P.rebuild(conn)
    conn.commit()
    log(f"daily_story backfill {since}..{until}: {cov}, {npat} pattern rows")
    return {"coverage": cov, "pattern_rows": npat}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--since", default=START.isoformat())
    ap.add_argument("--until")
    ap.add_argument("--llm-days", default="", help="comma-separated days that get the model-written story during a backfill")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        with db.JobRun("daily_story") as r, db.connect() as conn:
            if a.backfill:
                until = dt.date.fromisoformat(a.until) if a.until else pacific_yesterday()
                days = [dt.date.fromisoformat(x) for x in a.llm_days.split(",") if x.strip()]
                r.stats = backfill(conn, dt.date.fromisoformat(a.since), until, days)
            elif a.dry_run:
                d = dt.date.fromisoformat(a.day) if a.day else pacific_yesterday()
                out = run_day(conn, d, use_llm=not a.no_llm, dry_run=True)
                print(summarise(out["facts"]))
                print(json.dumps(out["story"], indent=1))
            else:
                d = dt.date.fromisoformat(a.day) if a.day else None
                r.stats = run_nightly(conn, d)
                db.log("daily_story ok:", r.stats)
    except Exception as e:                                                    # noqa: BLE001
        db.log("daily_story FAILED:", db.scrub(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
