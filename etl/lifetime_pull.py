"""All-time ad-level insights from the Meta Graph API (both accounts) -> meta_ad_lifetime (source='meta_api') + meta_ads / meta_campaigns upserts.
This is the ONLY source of "since launch" numbers: build_metrics sums these rows per tracker family (no cutoff rule, no launch-date gate).
Replace semantics: the table is replaced as a whole, and only when both accounts pulled cleanly and each account's per-ad spend sum reconciles with
the account-level lifetime spend within 0.5% (otherwise nothing is written and the job fails loudly).
Usage: python -m etl.lifetime_pull [--dry-run] [--no-ad-meta]"""
from __future__ import annotations

import argparse
import datetime as dt

from . import db, meta_api
from .meta_pull import default_until
from .resolver import classify_stage, fam

TOL = 0.005


def aggregate(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        a = out.get(r["ad_id"])
        if a is None:
            f = fam(r["ad_name"])
            out[r["ad_id"]] = a = {"ad_id": r["ad_id"], "ad_name": r["ad_name"], "famkey": f.lower(), "campaign_id": r["campaign_id"],
                                   "campaign_name": r["campaign_name"], "adset_id": r["adset_id"], "adset_name": r["adset_name"],
                                   "account": r["account"], "spend": 0.0, "purchases": 0.0, "value": 0.0, "impressions": 0.0, "clicks": 0.0}
        a["spend"] += r["spend"]; a["purchases"] += r["purchases"]; a["value"] += r["value"]
        a["impressions"] += r.get("impressions", 0.0); a["clicks"] += r["clicks"]
    return out


def pull(tok=None, until: dt.date | None = None) -> tuple[dict, list[dict]]:
    """-> ({ad_id: totals}, recon rows). The window is [earliest allowed start, until] with until = the last complete day (data_through), NOT date_preset=maximum,
    so since-launch is never ahead of the daily series. Raises GraphError / TokenRejected on API failure."""
    tok = tok or meta_api.get_token()
    until = until or default_until()
    since = meta_api.earliest_allowed()
    rows, recon = [], []
    for acct, an in meta_api.ACCOUNTS:
        r = meta_api.pull_lifetime(tok, acct, an, since, until)
        tot = meta_api.account_lifetime(tok, acct, since, until)
        ads_spend = sum(x["spend"] for x in r)
        off = abs(ads_spend - tot["spend"]) / tot["spend"] if tot["spend"] else 0.0
        recon.append({"account": an, "ads": len({x["ad_id"] for x in r}), "ads_spend": round(ads_spend, 2), "account_spend": round(tot["spend"], 2),
                      "pct_off": round(off * 100, 4), "ads_impressions": round(sum(x.get("impressions", 0) for x in r)), "account_impressions": round(tot["impressions"]),
                      "ads_clicks": round(sum(x["clicks"] for x in r)), "account_clicks": round(tot["clicks"]),
                      "ok": off <= TOL or abs(ads_spend - tot["spend"]) <= 5})
        db.log(f"  {an}: {recon[-1]['ads']} ads, per-ad spend ${ads_spend:,.2f} vs account lifetime ${tot['spend']:,.2f} ({off:.3%})")
        rows += r
    return aggregate(rows), recon


def load(conn, agg: dict[str, dict]) -> dict:
    """Replace meta_ad_lifetime; upsert meta_ads / meta_campaigns. One transaction (caller commits)."""
    camps = {a["campaign_id"]: (a["campaign_id"], a["account"], a["campaign_name"], classify_stage(a["campaign_name"]))
             for a in agg.values() if a["campaign_id"]}
    db.execmany(conn, """INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,%s,%s,%s)
                         ON CONFLICT (campaign_id) DO UPDATE SET account=EXCLUDED.account, name=EXCLUDED.name, stage=EXCLUDED.stage""", camps.values())
    db.execmany(conn, """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, family_name, campaign_id, adset_id, adset_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET account=EXCLUDED.account, ad_name=EXCLUDED.ad_name, meta_family_key=EXCLUDED.meta_family_key, family_name=EXCLUDED.family_name,
                         campaign_id=coalesce(EXCLUDED.campaign_id, meta_ads.campaign_id), adset_id=coalesce(EXCLUDED.adset_id, meta_ads.adset_id),
                         adset_name=coalesce(EXCLUDED.adset_name, meta_ads.adset_name)""",
                [(a["ad_id"], a["account"], a["ad_name"], a["famkey"], fam(a["ad_name"]), a["campaign_id"] or None, a["adset_id"] or None, a["adset_name"] or None)
                 for a in agg.values()])
    deleted = conn.execute("DELETE FROM meta_ad_lifetime").rowcount
    now = dt.datetime.now(dt.timezone.utc)
    n = db.bulk_copy(conn, "meta_ad_lifetime",
                     ["ad_id", "meta_family_key", "tracker_family_key", "spend", "purchases", "conv_value", "impressions", "link_clicks", "source", "pulled_at"],
                     [(a["ad_id"], a["famkey"], None, round(a["spend"], 2), round(a["purchases"], 2), round(a["value"], 2), round(a["impressions"]), round(a["clicks"]),
                       "meta_api", now) for a in agg.values()])
    return {"deleted": deleted, "inserted": n, "campaigns": len(camps)}


def check_recon(recon: list[dict]) -> None:
    bad = [r for r in recon if not r["ok"]]
    if bad:
        raise db.JobFailed(f"lifetime spend does not reconcile with account totals (>0.5%): {bad}")


def run(conn, tok=None, dry_run=False, ad_meta=True, until: dt.date | None = None) -> dict:
    tok = tok or meta_api.get_token()
    agg, recon = pull(tok, until)
    stats = {"window": [meta_api.earliest_allowed().isoformat(), (until or default_until()).isoformat()], "ads": len(agg), "recon": recon, "spend": round(sum(a["spend"] for a in agg.values()), 2),
             "impressions": round(sum(a["impressions"] for a in agg.values())), "clicks": round(sum(a["clicks"] for a in agg.values())),
             "purchases": round(sum(a["purchases"] for a in agg.values())), "revenue": round(sum(a["value"] for a in agg.values()), 2)}
    bad = [r for r in recon if not r["ok"]]
    if bad:
        raise db.JobFailed(f"lifetime spend does not reconcile with account totals (>0.5%): {bad}")
    if dry_run:
        return stats
    stats.update(load(conn, agg))
    conn.commit()
    if ad_meta:
        from .meta_pull import fill_ad_meta
        try:
            stats["ad_meta_filled"] = fill_ad_meta(conn, tok, limit=1500)
            conn.commit()
        except Exception as e:                                              # noqa: BLE001 — best effort
            stats["ad_meta_error"] = db.scrub(e)[:160]
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ad-meta", action="store_true")
    ap.add_argument("--until", help="last complete day (default: yesterday, Pacific)")
    a = ap.parse_args()
    until = dt.date.fromisoformat(a.until) if a.until else None
    if a.dry_run:
        with db.connect() as conn:
            st = run(conn, dry_run=True, until=until)
        db.log(f"DRY RUN: {st}")
        return
    with db.JobRun("lifetime_pull") as r, db.connect() as conn:
        st = run(conn, ad_meta=not a.no_ad_meta, until=until)
        r.stats = st
        r.rows_written = st["inserted"]
        db.log(f"  {st}")


if __name__ == "__main__":
    main()
