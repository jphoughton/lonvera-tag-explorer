"""Rebuild derived tables in ONE transaction: meta_campaigns.stage, family_daily (+traffic sums), family_daily_active, ad_metrics (all ads and the *_active mirrors),
unmatched_meta, app_meta.data_through. Implements CONTRACT.md "Metric rules" as amended by v1.4/v1.5/v1.9 and the QA fixes:
  * Meta family keys follow the CURRENT ad name (meta_ads.meta_family_key); a renamed ad's whole history moves with it (daily AND lifetime).
  * FIRST ROW is chosen per family_key across ALL products: the tracker row whose product matches the family NAME (products.meta_regex), else a real product
    over 'unassigned', else the lowest sheet_row. Every other row of the family carries zero metrics, so a family is counted once in total.
  * deleted tracker rows (deleted_at) are ignored; families whose tracker rows are all deleted count as unmatched.
  * unmatched_meta includes lifetime-only families with their API lifetime (spend_launch ...), product guessed from family name -> campaign name -> ad set name.
Usage: python -m etl.build_metrics"""
from __future__ import annotations

import datetime as dt

from . import db

SQL_STAGE = """UPDATE meta_campaigns SET stage = CASE WHEN lower(name) LIKE '%test%' THEN 'testing' ELSE 'scaling' END
               WHERE stage IS DISTINCT FROM CASE WHEN lower(name) LIKE '%test%' THEN 'testing' ELSE 'scaling' END"""

# daily / lifetime rows follow the ad's current name (meta_ads), so history stays attached to a renamed ad
SQL_SYNC_KEYS = [
    """UPDATE ad_daily d SET meta_family_key = a.meta_family_key, meta_family = coalesce(a.family_name, d.meta_family)
       FROM meta_ads a WHERE a.ad_id = d.ad_id AND a.meta_family_key IS NOT NULL AND d.meta_family_key IS DISTINCT FROM a.meta_family_key""",
    """UPDATE meta_ad_lifetime l SET meta_family_key = a.meta_family_key
       FROM meta_ads a WHERE a.ad_id = l.ad_id AND a.meta_family_key IS NOT NULL AND l.meta_family_key IS DISTINCT FROM a.meta_family_key""",
]

SQL_FAMILY_DAILY = """
INSERT INTO {table} (family_key, day, stage, spend, purchases, revenue, link_clicks, impressions, clicks_tr, spend_tr)
SELECT m.tracker_family_key, d.day,
       CASE WHEN d.campaign_id = '' THEN 'unclassified' ELSE coalesce(c.stage, 'unclassified') END,
       sum(d.spend), sum(d.purchases), sum(d.conv_value), sum(d.link_clicks),
       -- traffic base = rows that carry impressions (API rows). CTR = clicks_tr / impressions, CPC = spend_tr / clicks_tr, CPM = spend_tr / impressions * 1000.
       coalesce(sum(d.impressions), 0),
       coalesce(sum(d.link_clicks) FILTER (WHERE d.impressions IS NOT NULL), 0),
       coalesce(sum(d.spend) FILTER (WHERE d.impressions IS NOT NULL), 0)
FROM ad_daily d
{ads_join} meta_ads a ON a.ad_id = d.ad_id {active_cond}
JOIN meta_family_map m ON m.meta_family_key = coalesce(a.meta_family_key, d.meta_family_key) AND m.tracker_family_key IS NOT NULL
LEFT JOIN meta_campaigns c ON c.campaign_id = d.campaign_id
GROUP BY 1, 2, 3
"""
SQL_FAMILY_DAILY_ALL = SQL_FAMILY_DAILY.format(table="family_daily", ads_join="LEFT JOIN", active_cond="")
SQL_FAMILY_DAILY_ACTIVE = SQL_FAMILY_DAILY.format(table="family_daily_active", ads_join="JOIN", active_cond="AND a.status = 'ACTIVE'")

# first row of a family across ALL products (see module doc); only first rows carry metrics
SQL_RANKED = """
CREATE TEMP TABLE tmp_ranked AS
SELECT t.id, t.product_id, t.family_key, t.launch_date,
       (row_number() OVER (PARTITION BY t.family_key ORDER BY
           (p.meta_regex IS NOT NULL AND t.family_key ~* p.meta_regex) DESC,
           CASE WHEN p.meta_regex IS NOT NULL AND t.family_key ~* p.meta_regex THEN p.meta_priority ELSE 100000 END,
           (p.slug <> 'unassigned') DESC,
           t.sheet_row NULLS LAST, t.id) = 1) AS is_first
FROM tracker_rows t JOIN products p ON p.id = t.product_id
WHERE t.deleted_at IS NULL
"""

# SINCE LAUNCH = the Meta API's all-time ad-level totals (meta_ad_lifetime, source='meta_api') summed over the family's Meta ads through meta_family_map.
SQL_AD_METRICS = """
INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, launch_date,
   spend_launch, purchases_launch, revenue_launch, impressions_launch, clicks_launch,
   spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d, live,
   ads_active_count, spend_launch_active, purchases_launch_active, revenue_launch_active, impressions_launch_active, clicks_launch_active, live_active)
WITH life AS (
  SELECT m.tracker_family_key AS family_key, sum(l.spend) AS sp, sum(l.purchases) AS pu, sum(l.conv_value) AS rv, sum(l.impressions) AS im, sum(l.link_clicks) AS cl
  FROM meta_ad_lifetime l JOIN meta_family_map m ON m.meta_family_key = l.meta_family_key
  WHERE l.source = 'meta_api' AND m.tracker_family_key IS NOT NULL GROUP BY 1
), life_act AS (
  SELECT m.tracker_family_key AS family_key, sum(l.spend) AS sp, sum(l.purchases) AS pu, sum(l.conv_value) AS rv, sum(l.impressions) AS im, sum(l.link_clicks) AS cl
  FROM meta_ad_lifetime l JOIN meta_ads a ON a.ad_id = l.ad_id AND a.status = 'ACTIVE' JOIN meta_family_map m ON m.meta_family_key = l.meta_family_key
  WHERE l.source = 'meta_api' AND m.tracker_family_key IS NOT NULL GROUP BY 1
), nact AS (
  SELECT m.tracker_family_key AS family_key, count(*) AS n
  FROM meta_ads a JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
  WHERE a.status = 'ACTIVE' AND m.tracker_family_key IS NOT NULL GROUP BY 1
), win AS (
  SELECT family_key,
         sum(spend) FILTER (WHERE day >= %(d7)s) AS sp7, sum(purchases) FILTER (WHERE day >= %(d7)s) AS pu7, sum(revenue) FILTER (WHERE day >= %(d7)s) AS rv7,
         sum(spend) FILTER (WHERE day >= %(d30)s) AS sp30, sum(purchases) FILTER (WHERE day >= %(d30)s) AS pu30, sum(revenue) FILTER (WHERE day >= %(d30)s) AS rv30,
         sum(spend) FILTER (WHERE day >= %(d1)s) AS sp_live
  FROM family_daily GROUP BY family_key
), win_act AS (
  SELECT family_key, sum(spend) FILTER (WHERE day >= %(d1)s) AS sp_live FROM family_daily_active GROUP BY family_key
)
SELECT r.id, r.product_id, r.family_key, r.is_first, r.launch_date,
  CASE WHEN r.is_first THEN round(coalesce(l.sp, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(l.pu, 0), 0) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(l.rv, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN coalesce(l.im, 0) ELSE 0 END, CASE WHEN r.is_first THEN coalesce(l.cl, 0) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(w.sp7, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(w.pu7, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(w.rv7, 0), 2) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(w.sp30, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(w.pu30, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(w.rv30, 0), 2) ELSE 0 END,
  (r.is_first AND coalesce(w.sp_live, 0) > 0),
  coalesce(n.n, 0),
  CASE WHEN r.is_first THEN round(coalesce(la.sp, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(la.pu, 0), 0) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(la.rv, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN coalesce(la.im, 0) ELSE 0 END, CASE WHEN r.is_first THEN coalesce(la.cl, 0) ELSE 0 END,
  (r.is_first AND coalesce(wa.sp_live, 0) > 0)
FROM tmp_ranked r
LEFT JOIN life l ON l.family_key = r.family_key
LEFT JOIN life_act la ON la.family_key = r.family_key
LEFT JOIN nact n ON n.family_key = r.family_key
LEFT JOIN win w ON w.family_key = r.family_key
LEFT JOIN win_act wa ON wa.family_key = r.family_key
"""

# Meta families with no (live) tracker family. Includes families that exist only in meta_ad_lifetime (no daily rows). Product guess: family name, else the
# campaign of the family's biggest ad, else its ad set name (same meta_regex rules); product_source says which. Display name: highest lifetime spend, tie lexicographic.
SQL_UNMATCHED = """
INSERT INTO unmatched_meta (meta_family_key, meta_family, product_id, product_source, spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d,
                            spend_all, purchases_all, revenue_all, live, spend_launch, purchases_launch, revenue_launch, impressions_launch, clicks_launch)
WITH live_fams AS (SELECT DISTINCT family_key FROM tracker_rows WHERE deleted_at IS NULL),
unm AS (
  SELECT DISTINCT a.meta_family_key AS k FROM meta_ads a
  LEFT JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key AND m.tracker_family_key IN (SELECT family_key FROM live_fams)
  WHERE m.meta_family_key IS NULL AND a.meta_family_key IS NOT NULL
), daily AS (
  SELECT a.meta_family_key AS k,
         coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d7)s), 0) sp7, coalesce(sum(d.purchases) FILTER (WHERE d.day >= %(d7)s), 0) pu7, coalesce(sum(d.conv_value) FILTER (WHERE d.day >= %(d7)s), 0) rv7,
         coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d30)s), 0) sp30, coalesce(sum(d.purchases) FILTER (WHERE d.day >= %(d30)s), 0) pu30, coalesce(sum(d.conv_value) FILTER (WHERE d.day >= %(d30)s), 0) rv30,
         sum(d.spend) spa, sum(d.purchases) pua, sum(d.conv_value) rva, coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d1)s), 0) sp_live
  FROM ad_daily d JOIN meta_ads a ON a.ad_id = d.ad_id GROUP BY 1
), life AS (
  SELECT a.meta_family_key AS k, sum(l.spend) sp, sum(l.purchases) pu, sum(l.conv_value) rv, sum(l.impressions) im, sum(l.link_clicks) cl
  FROM meta_ad_lifetime l JOIN meta_ads a ON a.ad_id = l.ad_id WHERE l.source = 'meta_api' GROUP BY 1
), disp AS (
  SELECT DISTINCT ON (k) k, name FROM (
    SELECT a.meta_family_key AS k, coalesce(a.family_name, a.meta_family_key) AS name, sum(coalesce(l.spend, 0)) AS sp
    FROM meta_ads a LEFT JOIN meta_ad_lifetime l ON l.ad_id = a.ad_id WHERE a.meta_family_key IS NOT NULL GROUP BY 1, 2) x
  ORDER BY k, sp DESC, name COLLATE "C" ASC
), dom AS (
  SELECT DISTINCT ON (a.meta_family_key) a.meta_family_key AS k, c.name AS cname, a.adset_name AS aname
  FROM meta_ads a LEFT JOIN meta_ad_lifetime l ON l.ad_id = a.ad_id LEFT JOIN meta_campaigns c ON c.campaign_id = a.campaign_id
  WHERE a.meta_family_key IS NOT NULL ORDER BY a.meta_family_key, coalesce(l.spend, 0) DESC, a.ad_id
), guess AS (
  SELECT u.k, disp.name,
         product_for(disp.name) AS p_name, product_for(dom.cname) AS p_camp, product_for(dom.aname) AS p_adset
  FROM unm u JOIN disp ON disp.k = u.k LEFT JOIN dom ON dom.k = u.k
)
SELECT g.k, g.name,
       coalesce(g.p_name, g.p_camp, g.p_adset, (SELECT id FROM products WHERE slug = 'unassigned')),
       CASE WHEN g.p_name IS NOT NULL THEN 'name' WHEN g.p_camp IS NOT NULL THEN 'campaign' WHEN g.p_adset IS NOT NULL THEN 'adset' ELSE 'none' END,
       coalesce(d.sp7, 0), coalesce(d.pu7, 0), coalesce(d.rv7, 0), coalesce(d.sp30, 0), coalesce(d.pu30, 0), coalesce(d.rv30, 0),
       coalesce(d.spa, 0), coalesce(d.pua, 0), coalesce(d.rva, 0), coalesce(d.sp_live, 0) > 0,
       round(coalesce(l.sp, 0), 2), round(coalesce(l.pu, 0), 2), round(coalesce(l.rv, 0), 2), coalesce(l.im, 0), coalesce(l.cl, 0)
FROM guess g LEFT JOIN daily d ON d.k = g.k LEFT JOIN life l ON l.k = g.k
WHERE d.k IS NOT NULL OR l.k IS NOT NULL
"""


def rebuild(conn, commit: bool = True) -> dict:
    """Rebuild everything derived. Returns summary stats (incl. per-product totals). commit=False leaves the txn open (used for what-if runs)."""
    stats: dict = {}
    dthru = conn.execute("SELECT max(day) FROM ad_daily").fetchone()[0]
    if dthru is None:
        raise RuntimeError("ad_daily is empty; run the Meta pull first")
    d0 = dt.date.fromisoformat(db.get_meta(conn, "daily_start") or conn.execute("SELECT min(day) FROM ad_daily").fetchone()[0].isoformat())
    P = {"d7": dthru - dt.timedelta(days=6), "d30": dthru - dt.timedelta(days=29), "d1": dthru - dt.timedelta(days=1)}
    conn.execute(SQL_STAGE)
    for q in SQL_SYNC_KEYS:
        conn.execute(q)
    conn.execute("TRUNCATE family_daily")
    conn.execute(SQL_FAMILY_DAILY_ALL)
    conn.execute("TRUNCATE family_daily_active")
    conn.execute(SQL_FAMILY_DAILY_ACTIVE)
    conn.execute("DROP TABLE IF EXISTS tmp_ranked")
    conn.execute(SQL_RANKED)
    conn.execute("TRUNCATE ad_metrics")
    conn.execute(SQL_AD_METRICS, P)
    conn.execute("DROP TABLE tmp_ranked")
    conn.execute("TRUNCATE unmatched_meta")
    conn.execute(SQL_UNMATCHED, P)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db.set_meta(conn, "data_through", dthru.isoformat())
    db.set_meta(conn, "metrics_built_at", now)
    db.set_meta(conn, "daily_start", d0.isoformat(), only_if_missing=True)
    db.set_meta(conn, "tz_note", "Each Meta ad account is bucketed by its OWN day: Lonvera Main = America/Los_Angeles, Lonvera BackUp = America/New_York. "
                                 "Days are Graph date_start values and are not re-based to one timezone; created_date is the Pacific date of created_time.")
    lp = conn.execute("SELECT max(pulled_at), count(*) FILTER (WHERE source='meta_api'), count(*) FILTER (WHERE source<>'meta_api') FROM meta_ad_lifetime").fetchone()
    if lp[0]:
        db.set_meta(conn, "lifetime_pulled_at", lp[0].isoformat(timespec="seconds"))
    stats["lifetime_ads_api"] = lp[1]
    stats["data_through"] = dthru.isoformat()
    stats["family_daily_rows"] = conn.execute("SELECT count(*) FROM family_daily").fetchone()[0]
    stats["family_daily_active_rows"] = conn.execute("SELECT count(*) FROM family_daily_active").fetchone()[0]
    stats["ad_metrics_rows"] = conn.execute("SELECT count(*) FROM ad_metrics").fetchone()[0]
    stats["active_ads"] = conn.execute("SELECT count(*) FROM meta_ads WHERE status = 'ACTIVE'").fetchone()[0]
    stats["unmatched_families"] = conn.execute("SELECT count(*) FROM unmatched_meta").fetchone()[0]
    stats["by_product"] = {r[0]: {"rows": r[1], "unique_ads": r[2], "live": r[3], "spend_launch": float(r[4]), "purchases_launch": float(r[5]),
                                  "revenue_launch": float(r[6]), "spend_7d": float(r[7]), "purchases_7d": float(r[8]),
                                  "impressions_launch": float(r[9]), "clicks_launch": float(r[10]), "spend_launch_active": float(r[11])}
                           for r in conn.execute("""SELECT p.slug, count(*), count(DISTINCT m.family_key), count(*) FILTER (WHERE m.live), sum(m.spend_launch),
                                  sum(m.purchases_launch), sum(m.revenue_launch), sum(m.spend_7d), sum(m.purchases_7d), sum(m.impressions_launch), sum(m.clicks_launch),
                                  sum(m.spend_launch_active) FROM ad_metrics m JOIN products p ON p.id = m.product_id GROUP BY p.slug ORDER BY p.slug""").fetchall()}
    stats["unmatched_by_product"] = {r[0]: {"families": r[1], "spend_7d": float(r[2]), "spend_all": float(r[3]), "spend_launch": float(r[4])}
                                     for r in conn.execute("""SELECT p.slug, count(*), sum(u.spend_7d), sum(u.spend_all), sum(u.spend_launch) FROM unmatched_meta u
                                          JOIN products p ON p.id = u.product_id GROUP BY p.slug ORDER BY p.slug""").fetchall()}
    stats["unmatched_by_source"] = {r[0]: {"families": r[1], "spend_launch": float(r[2])} for r in conn.execute(
        "SELECT product_source, count(*), sum(spend_launch) FROM unmatched_meta GROUP BY 1 ORDER BY 1").fetchall()}
    stats["launch_total"] = {"ad_metrics": float(conn.execute("SELECT coalesce(sum(spend_launch),0) FROM ad_metrics").fetchone()[0]),
                             "unmatched": float(conn.execute("SELECT coalesce(sum(spend_launch),0) FROM unmatched_meta").fetchone()[0]),
                             "api_lifetime": float(conn.execute("SELECT coalesce(sum(spend),0) FROM meta_ad_lifetime WHERE source='meta_api'").fetchone()[0])}
    if commit:
        conn.commit()
    return stats


def main():
    with db.JobRun("build_metrics") as run, db.connect() as conn:
        stats = rebuild(conn)
        run.stats = stats
        run.data_through = dt.date.fromisoformat(stats["data_through"])
        run.rows_written = stats["ad_metrics_rows"] + stats["family_daily_rows"]
        for p, s in stats["by_product"].items():
            db.log(f"  {p}: {s}")
        db.log(f"  unmatched: {stats['unmatched_by_product']}")
        db.log(f"  launch total: {stats['launch_total']}")


if __name__ == "__main__":
    main()
