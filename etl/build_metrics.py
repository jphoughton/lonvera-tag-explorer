"""Rebuild derived tables in ONE transaction: meta_campaigns.stage, family_daily, ad_metrics, unmatched_meta, app_meta.data_through.
Implements CONTRACT.md "Metric rules" exactly. Usage: python -m etl.build_metrics"""
from __future__ import annotations

import datetime as dt

from . import db

SQL_STAGE = """UPDATE meta_campaigns SET stage = CASE WHEN lower(name) LIKE '%test%' THEN 'testing' ELSE 'scaling' END
               WHERE stage IS DISTINCT FROM CASE WHEN lower(name) LIKE '%test%' THEN 'testing' ELSE 'scaling' END"""

SQL_FAMILY_DAILY = """
INSERT INTO family_daily (family_key, day, stage, spend, purchases, revenue, link_clicks)
SELECT m.tracker_family_key, d.day,
       CASE WHEN d.campaign_id = '' THEN 'unclassified' ELSE coalesce(c.stage, 'unclassified') END,
       sum(d.spend), sum(d.purchases), sum(d.conv_value), sum(d.link_clicks)
FROM ad_daily d
JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key AND m.tracker_family_key IS NOT NULL
LEFT JOIN meta_campaigns c ON c.campaign_id = d.campaign_id
GROUP BY 1, 2, 3
"""

# first row of a (product, family) = smallest sheet_row (tie: smallest id). Only first rows carry metrics.
SQL_AD_METRICS = """
INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, launch_date,
   spend_launch, purchases_launch, revenue_launch, spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d, live)
WITH ranked AS (
  SELECT t.id, t.product_id, t.family_key, t.launch_date,
         (row_number() OVER (PARTITION BY t.product_id, t.family_key ORDER BY t.sheet_row NULLS LAST, t.id) = 1) AS is_first
  FROM tracker_rows t
), post AS (          -- mapped daily after the lifetime cutoff + fixed windows, per tracker family
  SELECT family_key,
         sum(spend) FILTER (WHERE day > %(cut)s) AS sp_post, sum(purchases) FILTER (WHERE day > %(cut)s) AS pu_post, sum(revenue) FILTER (WHERE day > %(cut)s) AS rv_post,
         sum(spend) FILTER (WHERE day >= %(d7)s) AS sp7, sum(purchases) FILTER (WHERE day >= %(d7)s) AS pu7, sum(revenue) FILTER (WHERE day >= %(d7)s) AS rv7,
         sum(spend) FILTER (WHERE day >= %(d30)s) AS sp30, sum(purchases) FILTER (WHERE day >= %(d30)s) AS pu30, sum(revenue) FILTER (WHERE day >= %(d30)s) AS rv30,
         sum(spend) FILTER (WHERE day >= %(d1)s) AS sp_live
  FROM family_daily GROUP BY family_key
)
SELECT r.id, r.product_id, r.family_key, r.is_first, r.launch_date,
  CASE WHEN r.is_first AND r.launch_date IS NOT NULL THEN round(CASE WHEN r.launch_date >= %(d0)s THEN coalesce(fl.sp, 0) ELSE coalesce(lt.spend, 0) + coalesce(p.sp_post, 0) END, 2) ELSE 0 END,
  CASE WHEN r.is_first AND r.launch_date IS NOT NULL THEN round(CASE WHEN r.launch_date >= %(d0)s THEN coalesce(fl.pu, 0) ELSE coalesce(lt.purchases, 0) + coalesce(p.pu_post, 0) END, 0) ELSE 0 END,
  CASE WHEN r.is_first AND r.launch_date IS NOT NULL THEN round(CASE WHEN r.launch_date >= %(d0)s THEN coalesce(fl.rv, 0) ELSE coalesce(lt.conv_value, 0) + coalesce(p.rv_post, 0) END, 2) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(p.sp7, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(p.pu7, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(p.rv7, 0), 2) ELSE 0 END,
  CASE WHEN r.is_first THEN round(coalesce(p.sp30, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(p.pu30, 0), 2) ELSE 0 END, CASE WHEN r.is_first THEN round(coalesce(p.rv30, 0), 2) ELSE 0 END,
  (r.is_first AND coalesce(p.sp_live, 0) > 0)
FROM ranked r
LEFT JOIN post p ON p.family_key = r.family_key
LEFT JOIN ad_lifetime lt ON lt.family_key = r.family_key
LEFT JOIN LATERAL (
  SELECT sum(spend) sp, sum(purchases) pu, sum(revenue) rv FROM family_daily f
  WHERE r.is_first AND r.launch_date IS NOT NULL AND r.launch_date >= %(d0)s AND f.family_key = r.family_key AND f.day >= r.launch_date
) fl ON true
"""

SQL_UNMATCHED = """
INSERT INTO unmatched_meta (meta_family_key, meta_family, product_id, spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d,
                            spend_all, purchases_all, revenue_all, live)
SELECT g.k, g.name,
       coalesce((SELECT p.id FROM products p WHERE p.meta_regex IS NOT NULL AND g.name ~* p.meta_regex ORDER BY p.meta_priority LIMIT 1),
                (SELECT id FROM products WHERE slug = 'unassigned')),
       g.sp7, g.pu7, g.rv7, g.sp30, g.pu30, g.rv30, g.spa, g.pua, g.rva, g.sp_live > 0
FROM (
  SELECT d.meta_family_key AS k, (array_agg(d.meta_family ORDER BY d.day DESC))[1] AS name,
         coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d7)s), 0) AS sp7, coalesce(sum(d.purchases) FILTER (WHERE d.day >= %(d7)s), 0) AS pu7, coalesce(sum(d.conv_value) FILTER (WHERE d.day >= %(d7)s), 0) AS rv7,
         coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d30)s), 0) AS sp30, coalesce(sum(d.purchases) FILTER (WHERE d.day >= %(d30)s), 0) AS pu30, coalesce(sum(d.conv_value) FILTER (WHERE d.day >= %(d30)s), 0) AS rv30,
         sum(d.spend) AS spa, sum(d.purchases) AS pua, sum(d.conv_value) AS rva,
         coalesce(sum(d.spend) FILTER (WHERE d.day >= %(d1)s), 0) AS sp_live
  FROM ad_daily d
  LEFT JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key
  WHERE m.tracker_family_key IS NULL
  GROUP BY d.meta_family_key
) g
"""


def rebuild(conn, commit: bool = True) -> dict:
    """Rebuild everything derived. Returns summary stats (incl. per-product totals). commit=False leaves the txn open (used for what-if runs)."""
    stats: dict = {}
    dthru = conn.execute("SELECT max(day) FROM ad_daily").fetchone()[0]
    if dthru is None:
        raise RuntimeError("ad_daily is empty; run backfill_sheet first")
    d0 = dt.date.fromisoformat(db.get_meta(conn, "daily_start") or conn.execute("SELECT min(day) FROM ad_daily").fetchone()[0].isoformat())
    cut = dt.date.fromisoformat(db.get_meta(conn, "lifetime_cutoff", "2026-09-17"))
    P = {"d0": d0, "cut": cut, "d7": dthru - dt.timedelta(days=6), "d30": dthru - dt.timedelta(days=29), "d1": dthru - dt.timedelta(days=1)}
    conn.execute(SQL_STAGE)
    conn.execute("TRUNCATE family_daily")
    conn.execute(SQL_FAMILY_DAILY)
    conn.execute("TRUNCATE ad_metrics")
    conn.execute(SQL_AD_METRICS, P)
    conn.execute("TRUNCATE unmatched_meta")
    conn.execute(SQL_UNMATCHED, P)
    db.set_meta(conn, "data_through", dthru.isoformat())
    db.set_meta(conn, "metrics_built_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    db.set_meta(conn, "daily_start", d0.isoformat(), only_if_missing=True)
    stats["data_through"] = dthru.isoformat()
    stats["family_daily_rows"] = conn.execute("SELECT count(*) FROM family_daily").fetchone()[0]
    stats["ad_metrics_rows"] = conn.execute("SELECT count(*) FROM ad_metrics").fetchone()[0]
    stats["unmatched_families"] = conn.execute("SELECT count(*) FROM unmatched_meta").fetchone()[0]
    stats["by_product"] = {r[0]: {"rows": r[1], "unique_ads": r[2], "live": r[3], "spend_launch": float(r[4]), "purchases_launch": float(r[5]),
                                  "revenue_launch": float(r[6]), "spend_7d": float(r[7]), "purchases_7d": float(r[8])}
                           for r in conn.execute("""SELECT p.slug, count(*), count(DISTINCT m.family_key), count(*) FILTER (WHERE m.live), sum(m.spend_launch),
                                  sum(m.purchases_launch), sum(m.revenue_launch), sum(m.spend_7d), sum(m.purchases_7d)
                                  FROM ad_metrics m JOIN products p ON p.id = m.product_id GROUP BY p.slug ORDER BY p.slug""").fetchall()}
    stats["unmatched_by_product"] = {r[0]: {"families": r[1], "spend_7d": float(r[2]), "spend_all": float(r[3])}
                                     for r in conn.execute("""SELECT p.slug, count(*), sum(u.spend_7d), sum(u.spend_all) FROM unmatched_meta u
                                          JOIN products p ON p.id = u.product_id GROUP BY p.slug ORDER BY p.slug""").fetchall()}
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


if __name__ == "__main__":
    main()
