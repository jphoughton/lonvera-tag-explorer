-- 261: analytics.change_timeline (one row per change event across Meta, pages, Klaviyo, ads, prices/offers, stock, feeds; bulk Meta edits grouped) and
--      public.why_findings + analytics.why_findings (what each `why` run concluded per hypothesis, read back as priors, never as proof).
CREATE TABLE IF NOT EXISTS why_findings (
  id            bigserial   PRIMARY KEY,
  created_at    timestamptz NOT NULL DEFAULT now(),
  metric        text        NOT NULL,
  product       text,
  platform      text,
  window_start  date        NOT NULL,
  window_end    date        NOT NULL,
  compare_start date,
  compare_end   date,
  hypothesis    text        NOT NULL,
  verdict       text        NOT NULL,
  evidence      text
);
CREATE INDEX IF NOT EXISTS why_findings_metric ON why_findings (metric, window_end);

CREATE OR REPLACE VIEW analytics.why_findings AS
SELECT f.created_at, f.metric, f.product, f.platform, f.window_start, f.window_end, f.compare_start, f.compare_end, f.hypothesis, f.verdict, f.evidence
FROM public.why_findings f;

CREATE OR REPLACE VIEW analytics.change_timeline AS
WITH raw AS (
  SELECT c.*, COALESCE(NULLIF(c.actor_name, ''), NULLIF(c.application_name, ''), c.actor_id) AS actor
  FROM public.meta_change_log c
  WHERE c.actor_id IS NOT NULL AND c.actor_id <> '0'
    AND c.event_type ~ '^(update|create)_(ad_set|campaign|ad)_'
), gapped AS (
  SELECT r.*, CASE WHEN lag(r.event_time) OVER w IS NULL OR r.event_time - lag(r.event_time) OVER w > interval '90 minutes' THEN 1 ELSE 0 END AS brk
  FROM raw r WINDOW w AS (PARTITION BY r.account, r.event_type, r.actor ORDER BY r.event_time)
), grp AS (
  SELECT g.*, sum(g.brk) OVER (PARTITION BY g.account, g.event_type, g.actor ORDER BY g.event_time ROWS UNBOUNDED PRECEDING) AS gid FROM gapped g
), bulk AS (
  SELECT account, event_type, actor, gid, min(event_time) AS event_time, max(event_time) AS last_time, min(object_level) AS object_level,
         array_agg(DISTINCT object_id) AS ids, count(DISTINCT object_id)::int AS n, (array_agg(object_name ORDER BY event_time))[1] AS first_name,
         min(old_amount) AS omin, max(old_amount) AS omax, min(new_amount) AS nmin, max(new_amount) AS nmax, avg(old_amount) AS oavg, avg(new_amount) AS navg,
         (array_agg(old_value ORDER BY event_time))[1] AS oval, (array_agg(new_value ORDER BY event_time))[1] AS nval
  FROM grp GROUP BY account, event_type, actor, gid
), meta AS (
  SELECT b.event_time, (b.event_time AT TIME ZONE 'America/Los_Angeles')::date AS event_day, 'meta_activity_log'::text AS source, b.event_type AS kind,
         b.object_level AS scope, b.ids AS object_ids,
         CASE WHEN b.n > 1 THEN b.n || ' ' || b.object_level || 's (first: ' || COALESCE(b.first_name, '?') || ')' ELSE b.first_name END AS object_names,
         NULL::text AS product,
         CASE WHEN b.omin IS NULL THEN b.oval WHEN b.omin = b.omax THEN b.omin::text ELSE b.omin || ' to ' || b.omax END AS old_value,
         CASE WHEN b.nmin IS NULL THEN b.nval WHEN b.nmin = b.nmax THEN b.nmin::text ELSE b.nmin || ' to ' || b.nmax END AS new_value,
         CASE WHEN b.oavg > 0 THEN round((b.navg / b.oavg - 1)::numeric, 4) END AS magnitude,
         b.actor AS editor, b.n AS objects_affected, b.last_time AS last_event_time
  FROM bulk b
), pg AS (
  SELECT v.fetched_at AS event_time, (v.fetched_at AT TIME ZONE 'America/Los_Angeles')::date AS event_day, 'landing_page'::text AS source,
         CASE WHEN v.prices::text IS DISTINCT FROM v.pprices THEN 'page_price_change' ELSE 'page_copy_change' END AS kind, 'page'::text AS scope,
         ARRAY[v.landing_page_id::text] AS object_ids, p.url AS object_names, NULL::text AS product,
         v.pprices AS old_value, v.prices::text AS new_value, NULL::numeric AS magnitude, 'site'::text AS editor, 1 AS objects_affected, v.fetched_at AS last_event_time
  FROM (SELECT pv.*, lag(pv.prices::text) OVER w AS pprices, lag(pv.content_hash) OVER w AS phash FROM public.landing_page_versions pv WINDOW w AS (PARTITION BY pv.landing_page_id ORDER BY pv.fetched_at)) v
  JOIN public.landing_pages p ON p.id = v.landing_page_id
  WHERE v.phash IS NOT NULL AND v.content_hash IS DISTINCT FROM v.phash
), kl AS (
  SELECT f.created_at, (f.created_at AT TIME ZONE 'America/Los_Angeles')::date, 'klaviyo', 'flow_created', 'flow', ARRAY[f.id::text], f.name, NULL::text, NULL::text, f.status, NULL::numeric, 'klaviyo', 1, f.created_at
  FROM public.klaviyo_flows f WHERE f.created_at IS NOT NULL
  UNION ALL
  SELECT f.updated_at, (f.updated_at AT TIME ZONE 'America/Los_Angeles')::date, 'klaviyo', 'flow_updated', 'flow', ARRAY[f.id::text], f.name, NULL, NULL, f.status, NULL, 'klaviyo', 1, f.updated_at
  FROM public.klaviyo_flows f WHERE f.updated_at IS NOT NULL AND f.updated_at > f.created_at + interval '5 minutes'
  UNION ALL
  SELECT k.send_time, (k.send_time AT TIME ZONE 'America/Los_Angeles')::date, 'klaviyo', 'campaign_sent', 'campaign', ARRAY[k.id::text], k.name, NULL, NULL, k.channel, NULL, 'klaviyo', 1, k.send_time
  FROM public.klaviyo_campaigns k WHERE k.send_time IS NOT NULL
), fam AS (
  SELECT (a.first_day::timestamp AT TIME ZONE 'America/Los_Angeles') AS event_time, a.first_day AS event_day, 'meta_ads'::text AS source, 'new_ad_families'::text AS kind, 'ad'::text AS scope,
         array_agg(DISTINCT a.family_key) AS object_ids, count(DISTINCT a.family_key) || ' new ad families (' || a.product_slug || ')' AS object_names, a.product_slug AS product,
         NULL::text AS old_value, NULL::text AS new_value, NULL::numeric AS magnitude, 'ads team'::text AS editor, count(DISTINCT a.family_key)::int AS objects_affected,
         (a.first_day::timestamp AT TIME ZONE 'America/Los_Angeles') AS last_event_time
  FROM (SELECT family_key, product_slug, min(first_day) AS first_day FROM analytics.meta_ads WHERE family_key IS NOT NULL GROUP BY 1, 2) a
  WHERE a.first_day >= (SELECT min(day) FROM analytics.meta_ad_daily) + 14
  GROUP BY a.first_day, a.product_slug
), codes AS (
  SELECT o.order_date, trim(c.code) AS code FROM public.shopify_orders o, LATERAL unnest(string_to_array(o.discount_codes, ',')) AS c(code)
  WHERE o.discount_codes IS NOT NULL AND NOT o.is_test AND NOT o.is_trybe_gift
), disc AS (
  SELECT (d.day::timestamp AT TIME ZONE 'America/Los_Angeles') AS event_time, d.day AS event_day, 'shopify_orders'::text AS source, 'discount_code_first_seen'::text AS kind, 'account'::text AS scope,
         ARRAY[d.code] AS object_ids, d.code AS object_names, NULL::text AS product, NULL::text AS old_value, d.orders::text AS new_value, NULL::numeric AS magnitude,
         'derived'::text AS editor, d.orders::int AS objects_affected, (d.day::timestamp AT TIME ZONE 'America/Los_Angeles') AS last_event_time
  FROM (SELECT x.code, x.order_date AS day, count(*) AS orders, rank() OVER (PARTITION BY x.code ORDER BY x.order_date) AS rk FROM codes x WHERE x.code <> '' GROUP BY x.code, x.order_date) d
  WHERE d.rk = 1 AND d.orders >= 20 AND d.day >= (SELECT min(order_date) FROM public.shopify_orders) + 14
), pp AS (
  SELECT (q.day::timestamp AT TIME ZONE 'America/Los_Angeles') AS event_time, q.day AS event_day, 'shopify_order_lines'::text AS source, 'price_point_first_seen'::text AS kind, 'product'::text AS scope,
         ARRAY[q.unit::text] AS object_ids, q.product || ' at ' || q.unit AS object_names, q.product AS product, NULL::text AS old_value, q.unit::text AS new_value, NULL::numeric AS magnitude,
         'derived'::text AS editor, q.lines::int AS objects_affected, (q.day::timestamp AT TIME ZONE 'America/Los_Angeles') AS last_event_time
  FROM (SELECT z.product, z.unit, z.order_date AS day, count(*) AS lines, rank() OVER (PARTITION BY z.product, z.unit ORDER BY z.order_date) AS rk
        FROM (SELECT l.product, round(l.price)::int AS unit, o.order_date FROM public.shopify_order_lines l JOIN public.shopify_orders o USING (order_id)
              WHERE l.price > 0 AND l.quantity = 1 AND NOT o.is_test AND NOT o.is_trybe_gift) z GROUP BY z.product, z.unit, z.order_date) q
  WHERE q.rk = 1 AND q.lines >= 15 AND q.day >= (SELECT min(order_date) FROM public.shopify_orders) + 14
), stock AS (
  SELECT (s.as_of::timestamp AT TIME ZONE 'America/Los_Angeles') AS event_time, s.as_of AS event_day, 'inventory'::text AS source,
         CASE WHEN s.on_hand <= 0 THEN 'stockout' ELSE 'stock_near_zero' END AS kind, 'product'::text AS scope, ARRAY[s.location || '/' || s.product] AS object_ids,
         s.location || ' ' || s.product AS object_names, s.product AS product, s.prev::text AS old_value, s.on_hand::text AS new_value, NULL::numeric AS magnitude,
         'derived'::text AS editor, 1 AS objects_affected, (s.as_of::timestamp AT TIME ZONE 'America/Los_Angeles') AS last_event_time
  FROM (SELECT i.*, lag(i.on_hand) OVER (PARTITION BY i.location, i.product ORDER BY i.as_of) AS prev, max(i.on_hand) OVER (PARTITION BY i.location, i.product ORDER BY i.as_of ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING) AS mx FROM analytics.inventory_stock_daily i) s
  WHERE (s.on_hand <= 0 AND COALESCE(s.prev, 1) > 0) OR (s.on_hand > 0 AND s.mx > 0 AND s.on_hand < 0.05 * s.mx AND s.prev >= 0.05 * s.mx)
), feed AS (
  SELECT r.started_at, (r.started_at AT TIME ZONE 'America/Los_Angeles')::date, 'etl_runs', 'feed_run_failed', 'job', ARRAY[r.job], r.job, NULL::text, NULL::text, left(COALESCE(r.error, r.status), 120), NULL::numeric, 'pipeline', 1, r.started_at
  FROM public.etl_runs r WHERE r.status NOT IN ('ok', 'success', 'succeeded', 'running', 'skipped')
), allev AS (
  SELECT * FROM meta UNION ALL SELECT * FROM pg UNION ALL SELECT * FROM kl UNION ALL SELECT * FROM fam UNION ALL SELECT * FROM disc UNION ALL SELECT * FROM pp UNION ALL SELECT * FROM stock UNION ALL SELECT * FROM feed
)
SELECT e.event_time, e.event_day, e.source, e.kind, e.scope, e.object_ids, e.object_names, e.product, e.old_value, e.new_value, e.magnitude, e.editor, e.objects_affected, e.last_event_time, sh.share AS prior_day_spend_share
FROM allev e
LEFT JOIN LATERAL (
  SELECT (sum(d.spend) FILTER (WHERE d.adset_id = ANY (e.object_ids) OR d.campaign_id = ANY (e.object_ids)) / NULLIF(sum(d.spend), 0))::numeric(8,4) AS share
  FROM analytics.meta_ad_daily d WHERE e.source = 'meta_activity_log' AND d.day = e.event_day - 1
) sh ON true;
