"""Google campaigns from Upstack (Marketing metrics, channel Google). Two feeds, both campaign grain:
  * channel feed  (query-channel-details, Google rows): spend, impressions, clicks, and the platform's own purchases / conversion value
  * attribution   (query-attribution, utm_source "Google Ads", any-click, 30-day window): Upstack new customers / orders / revenue by utm campaign NAME
Upstack exposes no campaign type for Google, so the type is read from the campaign name (campaign_type()). READ phase (Upstack only), then ONE write
transaction: replace the pulled days in the raw tables, rebuild google_daily. An Upstack failure leaves the last good data in place.
Called from etl.upstack_daily (nightly: the same trailing window as the Meta side; backfills pass --google-since)."""
from __future__ import annotations

import collections
import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, upstack_api as api

GOOGLE_INTEGRATION = "googleAdsIntegration"
SOURCE_NAME = "Google Ads"
GOOGLE_MEASURES = ("channel.spend", "channel.purchases", "channel.purchases_value", "channel.clicks", "channel.impressions")
CHANNEL_CHUNK_DAYS = 90
ATTRIBUTION_FROM = dt.date(2025, 5, 13)          # first day Upstack has any attribution (probed 2026-11-04); the channel feed goes back further
TYPES = ("Shopping", "Search", "Performance Max", "Demand Gen", "Other")

_RX_TYPES = [
    (re.compile(r"^\s*pmax\b", re.I), "Performance Max"),
    (re.compile(r"^\s*(dg\d*\b|il\s*\|\s*demand\s*gen)", re.I), "Demand Gen"),
    (re.compile(r"^\s*(standard\s+shopping|ss\s*\|)", re.I), "Shopping"),
    (re.compile(r"^\s*(brand\s+search|prospecting\s+search|competitors\s+search|il\s*\|\s*search)", re.I), "Search"),
]


def campaign_type(name: str) -> str:
    """Upstack gives no Google campaign type; this account's names start with it ('Standard Shopping | ...', 'PMax | ...', 'Brand Search | ...',
    'DG15 | ...', 'IL | Demand Gen | ...'). A name that says none of it ('Prospecting | Top 3 Products | ...') is Other, never guessed."""
    for rx, t in _RX_TYPES:
        if rx.match(name or ""):
            return t
    return "Other"


def channel_google(results: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """query-channel-details results (campaign dimension) -> ([(id, name, status, bid_strategy, type)], [(day, id, spend, impressions, clicks, purchases, value)])
    for Google campaigns only; days with no activity are dropped."""
    camps, daily = [], []
    for r in results:
        bd = r.get("basicDetails") or {}
        if bd.get("resourceSource") != GOOGLE_INTEGRATION:
            continue
        cid, name = str(r.get("campaign") or ""), r.get("campaignName") or bd.get("resourceName") or ""
        if not cid:
            continue
        camps.append((cid, name, bd.get("status"), bd.get("bidStrategy"), campaign_type(name)))
        for p in (r.get("data") or {}).get("primary") or []:
            sp, pu, pv, cl, im = (api.num(p.get(m)) for m in GOOGLE_MEASURES)
            if any((sp, pu, pv, cl, im)):
                daily.append((dt.date.fromisoformat(str(p["dt"])[:10]), cid, sp, im, cl, pu, pv))
    return camps, daily


def pull_channel(client: api.Client, since: dt.date, until: dt.date) -> tuple[list[tuple], list[tuple]]:
    camps: dict[str, tuple] = {}
    daily: list[tuple] = []
    d = since
    while d <= until:
        e = min(d + dt.timedelta(days=CHANNEL_CHUNK_DAYS - 1), until)
        c, rows = channel_google(client.channel_campaigns(d, e, GOOGLE_MEASURES))
        camps.update({x[0]: x for x in c})
        daily += rows
        d = e + dt.timedelta(days=1)
    return list(camps.values()), daily


def pull_attribution(client: api.Client, days: list[dt.date], workers: int = 4) -> dict[dt.date, list[list]]:
    """{day: [[utm campaign, nc, nc_rev, orders, rev]]} (one call per day)."""
    def one(d: dt.date):
        return d, api.campaign_rows(client.attribution(d, "utm_campaign", source=SOURCE_NAME))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return dict(ex.map(one, days))


def check_days(attr: dict[dt.date, list[list]], daily: list[tuple]) -> None:
    """An empty Upstack answer is not a quiet day: refuse a day with real Google spend (> $200) and no attributed Google orders at all."""
    spend: dict[dt.date, float] = collections.defaultdict(float)
    for d, _cid, sp, *_ in daily:
        spend[d] += sp
    for d, rows in attr.items():
        if not rows and spend.get(d, 0) > 200 and d >= ATTRIBUTION_FROM:
            raise db.JobFailed(f"upstack returned no Google-attributed orders for {d} (Google spend {spend[d]:,.0f})")


def write(conn, camps: list[tuple], daily: list[tuple], attr: dict[dt.date, list[list]], since: dt.date, until: dt.date) -> None:
    for cid, name, status, bid, typ in camps:
        conn.execute("""INSERT INTO upstack_google_campaigns (campaign_id, name, status, bid_strategy, campaign_type) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (campaign_id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status, bid_strategy = EXCLUDED.bid_strategy,
                        campaign_type = EXCLUDED.campaign_type, updated_at = now()""", (cid, name, status, bid, typ))
    conn.execute("DELETE FROM upstack_google_channel_daily WHERE day BETWEEN %s AND %s", (since, until))
    agg: dict[tuple, list[float]] = {}
    for d, cid, *vals in daily:
        cur = agg.setdefault((d, cid), [0.0] * 5)
        for i, v in enumerate(vals):
            cur[i] += v
    db.bulk_copy(conn, "upstack_google_channel_daily", ["day", "campaign_id", "spend", "impressions", "clicks", "purchases", "purchases_value"],
                 [(d, cid, *[round(x, 2) for x in v]) for (d, cid), v in agg.items()])
    days = sorted(attr)
    if days:
        conn.execute("DELETE FROM upstack_google_attr_daily WHERE day = ANY(%s)", (days,))
        rows: dict[tuple, list[float]] = {}
        for d in days:
            for camp, nc, nc_rev, orders, rev in attr[d]:
                cur = rows.setdefault((d, camp), [0.0] * 4)
                for i, v in enumerate((nc, nc_rev, orders, rev)):
                    cur[i] += v
        db.bulk_copy(conn, "upstack_google_attr_daily", ["day", "campaign", "nc", "nc_rev", "orders", "rev"], [(d, c, *[round(x, 2) for x in v]) for (d, c), v in rows.items()])
        for d in days:
            conn.execute("""INSERT INTO upstack_google_days (day, pulled_at, campaigns, nc, orders) VALUES (%s, now(), %s, %s, %s)
                            ON CONFLICT (day) DO UPDATE SET pulled_at = now(), campaigns = EXCLUDED.campaigns, nc = EXCLUDED.nc, orders = EXCLUDED.orders""",
                         (d, len(attr[d]), round(sum(r[1] for r in attr[d]), 2), round(sum(r[3] for r in attr[d]), 2)))


def build(conn) -> dict:
    """google_daily = channel feed rows + attribution rows, per day and campaign. Attribution is keyed by campaign NAME; when two campaign ids share a
    name its numbers go to the one with the most spend. A utm campaign that is no Google campaign keeps its own row (in_feed false)."""
    camps = {cid: (name, typ) for cid, name, typ in conn.execute("SELECT campaign_id, name, campaign_type FROM upstack_google_campaigns").fetchall()}
    spend_by_id = {cid: float(s) for cid, s in conn.execute("SELECT campaign_id, sum(spend) FROM upstack_google_channel_daily GROUP BY 1").fetchall()}
    id_of: dict[str, str] = {}
    for cid, (name, _t) in sorted(camps.items(), key=lambda kv: spend_by_id.get(kv[0], 0.0)):
        id_of[name.strip().lower()] = cid                                   # ascending spend, so the biggest campaign of a shared name wins
    pulled = [r[0] for r in conn.execute("SELECT day FROM upstack_google_days ORDER BY day").fetchall()]
    rows: dict[tuple, list] = {}

    def row(day, key, name, typ, in_feed):
        return rows.setdefault((day, key), [name, typ, in_feed, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for day, cid, sp, im, cl, pu, pv in conn.execute("SELECT day, campaign_id, spend, impressions, clicks, purchases, purchases_value FROM upstack_google_channel_daily").fetchall():
        name, typ = camps.get(cid, (cid, "Other"))
        r = row(day, cid, name, typ, True)
        r[3:8] = [float(sp), float(im), float(cl), float(pu), float(pv)]
    unmapped = collections.Counter()
    for day, camp, nc, nc_rev, orders, rev in conn.execute("SELECT day, campaign, nc, nc_rev, orders, rev FROM upstack_google_attr_daily WHERE day IN (SELECT day FROM upstack_google_days)").fetchall():
        cid = id_of.get(camp.strip().lower())
        if cid is not None:
            r = row(day, cid, camps[cid][0], camps[cid][1], True)
        else:
            r = row(day, "name:" + camp, camp, campaign_type(camp), False)
            unmapped["orders"] += float(orders)
            unmapped["nc"] += float(nc)
        r[8:12] = [r[8] + float(nc), r[9] + float(nc_rev), r[10] + float(orders), r[11] + float(rev)]
    conn.execute("TRUNCATE google_daily")
    db.bulk_copy(conn, "google_daily", ["day", "campaign_key", "campaign_name", "campaign_type", "in_feed", "spend", "impressions", "clicks", "purchases", "purchases_value",
                                        "nc", "nc_rev", "orders", "rev"],
                 [(d, k, v[0], v[1], v[2], *[round(x, 2) for x in v[3:]]) for (d, k), v in rows.items()])
    ch = conn.execute("SELECT min(day), max(day) FROM upstack_google_channel_daily").fetchone()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if ch and ch[0] and pulled:
        db.set_meta(conn, "google_first_day", ch[0].isoformat())           # Google-reported numbers: from the first channel-feed day
        db.set_meta(conn, "google_through", min(ch[1], pulled[-1]).isoformat())
        db.set_meta(conn, "google_upstack_first_day", pulled[0].isoformat())  # Upstack-attributed numbers: from the first pulled attribution day
        db.set_meta(conn, "google_built_at", now)
    tot = conn.execute("SELECT coalesce(sum(spend),0), coalesce(sum(purchases),0), coalesce(sum(orders),0), coalesce(sum(nc),0), count(*) FROM google_daily").fetchone()
    return {"campaigns": len(camps), "rows": int(tot[4]), "first_day": ch[0].isoformat() if ch and ch[0] else None, "through": min(ch[1], pulled[-1]).isoformat() if ch and ch[1] and pulled else None,
            "upstack_first_day": pulled[0].isoformat() if pulled else None, "spend": float(tot[0]), "reported_purchases": float(tot[1]), "orders": float(tot[2]), "nc": float(tot[3]),
            "unmapped_orders": float(unmapped["orders"]), "unmapped_nc": float(unmapped["nc"])}


def run(conn, client: api.Client, since: dt.date, until: dt.date, workers: int = 4, dry_run: bool = False, skip_done: bool = False,
        channel_since: dt.date | None = None) -> dict:
    """Pull [since, until] attribution (>= ATTRIBUTION_FROM) and the channel feed from channel_since (default: since); write and rebuild unless dry_run."""
    t0 = time.time()
    a_since = max(since, ATTRIBUTION_FROM)
    done = {r[0] for r in conn.execute("SELECT day FROM upstack_google_days").fetchall()} if skip_done else set()
    days = [d for d in api_days(a_since, until) if d not in done]
    attr = pull_attribution(client, days, workers) if days else {}
    c_since = channel_since or since
    camps, daily = pull_channel(client, c_since, until)
    check_days(attr, daily)
    stats = {"attribution_days": len(days), "channel_campaigns": len(camps), "channel_rows": len(daily), "channel_since": c_since.isoformat(), "seconds": round(time.time() - t0)}
    if dry_run:
        return stats
    write(conn, camps, daily, attr, c_since, until)
    stats["build"] = build(conn)
    return stats


def api_days(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)
