"""Build the Upstack source from the raw pulls (one transaction, caller commits):

  1. ad_daily_upstack  = Meta's spend / clicks / impressions per ad and day (copied from ad_daily for every pulled day, so a Meta restatement flows
                         through) + the Upstack orders and new customers credited to each ad.
  2. Mapping of Upstack's utm values (campaign NAME x ad NAME as clicked) to Meta ads:
       exact ad name inside the utm campaign  >  same ad family inside the campaign  >  exact ad name in any campaign  >  same family anywhere.
     The utm campaign may be a campaign name or a raw campaign id. When several Meta ads match (the same ad copied into several ad sets) the day's
     orders are split by each ad's Meta spend that day (else lifetime spend, else equally); the split always sums back exactly, so family, tag and
     product totals are exact and only the twin ads' own rows are an allocation.
     Rows with no match ('(not set)', truncated ids, deleted ads, campaigns Upstack could not split by ad) go to upstack_unmapped_daily and are in no
     ad, family or tag total; the tie-out reports them.
  3. The derived tables of both bases (nc, all) via build_metrics.rebuild_source.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal

from . import build_metrics, db
from .resolver import fam
from .upstack_api import NOT_IDENTIFIED

MEASURES = ("nc", "nc_rev", "orders", "rev")          # order of the allocated vector


def _split(total: float, weights: list[float]) -> list[float]:
    """total * w_i / sum(w), rounded to cents, remainder on the largest weight so the parts add up to round(total, 2)."""
    s = sum(weights)
    if s <= 0:
        weights, s = [1.0] * len(weights), float(len(weights))
    parts = [round(total * w / s, 2) for w in weights]
    big = max(range(len(parts)), key=lambda i: weights[i])
    parts[big] = round(round(total, 2) - (sum(parts) - parts[big]), 2)
    return parts


class Mapper:
    """utm (campaign, ad) -> candidate Meta ad ids. Built once from meta_campaigns / meta_ads."""

    def __init__(self, campaigns: list[tuple], ads: list[tuple]):
        # campaigns: (campaign_id, name); ads: (ad_id, ad_name, campaign_id, meta_family_key)
        self.camp_ids = {str(c) for c, _ in campaigns}
        self.camp_by_name: dict[str, set[str]] = collections.defaultdict(set)
        for cid, name in campaigns:
            self.camp_by_name[str(name).strip().lower()].add(str(cid))
        self.by_name_camp: dict[tuple, list[str]] = collections.defaultdict(list)
        self.by_fam_camp: dict[tuple, list[str]] = collections.defaultdict(list)
        self.by_name: dict[str, list[str]] = collections.defaultdict(list)
        self.by_fam: dict[str, list[str]] = collections.defaultdict(list)
        self.ad_info: dict[str, tuple] = {}
        for ad_id, name, cid, key in ads:
            if not name:
                continue
            n = str(name).strip().lower()
            k = (key or fam(name).lower())
            cid = str(cid or "")
            self.by_name_camp[(cid, n)].append(ad_id)
            self.by_fam_camp[(cid, k)].append(ad_id)
            self.by_name[n].append(ad_id)
            self.by_fam[k].append(ad_id)
            self.ad_info[ad_id] = (cid, k, fam(name))
        self.stats: collections.Counter = collections.Counter()

    def campaign_ids(self, utm_campaign: str) -> set[str]:
        s = str(utm_campaign).strip()
        ids = set(self.camp_by_name.get(s.lower(), ()))
        if not ids and s in self.camp_ids:                       # some UTMs carry the raw campaign id
            ids = {s}
        return ids

    def candidates(self, utm_campaign: str, utm_ad: str) -> tuple[list[str], str]:
        """-> (ad ids, rule). rule is one of exact | family | name_any | family_any | no_campaign | no_ad | not_identified."""
        ad = str(utm_ad).strip()
        if ad in ("", NOT_IDENTIFIED, "(not set)"):
            return [], "not_identified"
        cids = self.campaign_ids(utm_campaign)
        n, k = ad.lower(), fam(ad).lower()
        got = sorted({a for c in cids for a in self.by_name_camp.get((c, n), ())})
        if got:
            return got, "exact"
        got = sorted({a for c in cids for a in self.by_fam_camp.get((c, k), ())})
        if got:
            return got, "family"
        if n in self.by_name:
            return sorted(set(self.by_name[n])), "name_any"
        if k in self.by_fam:
            return sorted(set(self.by_fam[k])), "family_any"
        return [], ("no_ad" if cids else "no_campaign")


def allocate(raw_rows: list[tuple], mapper: Mapper, spend: dict, life: dict) -> tuple[dict, list[tuple]]:
    """raw_rows: (day, campaign, ad, nc, nc_rev, orders, rev). spend[(ad_id, day)], life[ad_id] = Meta spend weights.
    -> ({(ad_id, day): [nc, nc_rev, orders, rev]}, unmapped [(day, campaign, ad, reason, nc, nc_rev, orders, rev)])."""
    out: dict[tuple, list[float]] = {}
    unmapped: list[tuple] = []
    for day, camp, ad, nc, nc_rev, orders, rev in raw_rows:
        cands, rule = mapper.candidates(camp, ad)
        mapper.stats[rule] += 1
        mapper.stats[rule + ":orders"] += float(orders)
        if not cands:
            unmapped.append((day, camp, ad, rule, nc, nc_rev, orders, rev))
            continue
        w = [float(spend.get((a, day), 0)) for a in cands]
        if sum(w) <= 0:
            w = [float(life.get(a, 0)) for a in cands]
        vec = [float(nc), float(nc_rev), float(orders), float(rev)]
        parts = [_split(v, w) for v in vec]
        for i, a in enumerate(cands):
            cur = out.setdefault((a, day), [0.0, 0.0, 0.0, 0.0])
            for j in range(4):
                cur[j] = round(cur[j] + parts[j][i], 2)
    return out, unmapped


SQL_COPY_META = """
INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, link_clicks, impressions)
SELECT d.ad_id, d.day, coalesce(a.meta_family_key, d.meta_family_key), coalesce(a.family_name, d.meta_family), coalesce(d.campaign_id, ''),
       d.spend, d.link_clicks, d.impressions
FROM ad_daily d LEFT JOIN meta_ads a ON a.ad_id = d.ad_id
WHERE d.day IN (SELECT day FROM upstack_attr_days)
"""


def build(conn) -> dict:
    """Rebuild everything Upstack-derived from upstack_attr_* + ad_daily + meta_ads. Idempotent."""
    days = [r[0] for r in conn.execute("SELECT day FROM upstack_attr_days ORDER BY day").fetchall()]
    if not days:
        raise db.JobFailed("no Upstack days pulled yet")
    raw = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in conn.execute(
        "SELECT day, campaign, ad, nc, nc_rev, orders, rev FROM upstack_attr_daily WHERE day IN (SELECT day FROM upstack_attr_days)").fetchall()]
    mapper = Mapper(conn.execute("SELECT campaign_id, name FROM meta_campaigns").fetchall(),
                    conn.execute("SELECT ad_id, ad_name, campaign_id, meta_family_key FROM meta_ads").fetchall())
    spend = {(a, d): float(s) for a, d, s in conn.execute(
        "SELECT ad_id, day, spend FROM ad_daily WHERE day IN (SELECT day FROM upstack_attr_days) AND spend > 0").fetchall()}
    life = {a: float(s) for a, s in conn.execute("SELECT ad_id, spend FROM meta_ad_lifetime WHERE spend > 0").fetchall()}
    alloc, unmapped = allocate(raw, mapper, spend, life)

    conn.execute("TRUNCATE ad_daily_upstack")
    conn.execute(SQL_COPY_META)
    conn.execute("CREATE TEMP TABLE tmp_up_alloc (ad_id text, day date, nc numeric, nc_rev numeric, orders numeric, rev numeric) ON COMMIT DROP")
    db.bulk_copy(conn, "tmp_up_alloc", ["ad_id", "day", "nc", "nc_rev", "orders", "rev"], [(a, d, *v) for (a, d), v in alloc.items()])
    conn.execute("""
        INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, link_clicks, impressions,
                                      purchases, conv_value, nc_purchases, nc_conv_value)
        SELECT t.ad_id, t.day, coalesce(a.meta_family_key, ''), coalesce(a.family_name, a.meta_family_key, ''), coalesce(a.campaign_id, ''), 0, 0, 0,
               t.orders, t.rev, t.nc, t.nc_rev
        FROM tmp_up_alloc t JOIN meta_ads a ON a.ad_id = t.ad_id
        ON CONFLICT (ad_id, day) DO UPDATE SET purchases = EXCLUDED.purchases, conv_value = EXCLUDED.conv_value,
                                               nc_purchases = EXCLUDED.nc_purchases, nc_conv_value = EXCLUDED.nc_conv_value""")
    conn.execute("TRUNCATE upstack_unmapped_daily")
    db.bulk_copy(conn, "upstack_unmapped_daily", ["day", "campaign", "ad", "reason", "nc", "nc_rev", "orders", "rev"], _merge_unmapped(unmapped))

    derived = {b: build_metrics.rebuild_source(conn, b) for b in build_metrics.UPSTACK_BASES}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db.set_meta(conn, "upstack_through", days[-1].isoformat())
    db.set_meta(conn, "upstack_first_day", days[0].isoformat())
    db.set_meta(conn, "upstack_built_at", now)
    n_ad = conn.execute("SELECT count(*) FROM ad_daily_upstack").fetchone()[0]
    tot = conn.execute("SELECT coalesce(sum(spend),0), coalesce(sum(purchases),0), coalesce(sum(nc_purchases),0) FROM ad_daily_upstack").fetchone()
    un = conn.execute("SELECT coalesce(sum(orders),0), coalesce(sum(nc),0) FROM upstack_unmapped_daily").fetchone()
    return {"days": len(days), "first_day": days[0].isoformat(), "through": days[-1].isoformat(), "raw_rows": len(raw), "ad_daily_rows": n_ad,
            "mapped_orders": float(tot[1]), "mapped_nc": float(tot[2]), "unmapped_orders": float(un[0]), "unmapped_nc": float(un[1]),
            "match_rules": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(mapper.stats.items())}, "derived": derived}


def _merge_unmapped(rows: list[tuple]):
    """Same (day, campaign, ad) can only occur once in upstack_attr_daily, but keep the PK safe if it ever repeats."""
    agg: dict[tuple, list] = {}
    for day, camp, ad, reason, nc, nc_rev, orders, rev in rows:
        cur = agg.setdefault((day, camp, ad), [reason, Decimal(0), Decimal(0), Decimal(0), Decimal(0)])
        for j, v in enumerate((nc, nc_rev, orders, rev), start=1):
            cur[j] += Decimal(str(v))
    return [(d, c, a, v[0], v[1], v[2], v[3], v[4]) for (d, c, a), v in agg.items()]
