"""The data dictionary cannot rot, and the `analytics` views mean what the dictionary says.

  * every table, view and column in the public and analytics schemas is described in docs/schema/descriptions.py (or excluded with a reason), and every
    description names something that exists (negative tests prove the guard fires: a new table, a new column, a stale description);
  * docs/DATA_SCHEMA.md and docs/schema.json equal what scripts/gen_schema_doc.py generates from the migrated schema;
  * the COMMENT ON text in the database (migrations 013 / 190 / 191) equals the descriptions;
  * every example query in the dictionary runs;
  * the analytics views reconcile with the tables behind them (spend, Upstack credit, roll-ups, Total NC ROAS, product rule, tracker tags).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path

import psycopg
import pytest

from . import seed_fixture as S

ROOT = Path(__file__).resolve().parents[2]
PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_API_DB", "tagexp_apitest") + "_schema"
DB_URL = f"{PG_BASE}/{DB_NAME}"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_schema_doc", ROOT / "scripts" / "gen_schema_doc.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_schema_doc"] = mod
    spec.loader.exec_module(mod)
    return mod


gen = _load_gen()
D = gen.load_descriptions()


# ---------------------------------------------------------------------------------------------------------------- a database with data in every source
EXTRA = """
-- Upstack Meta attribution pulled for 2026-10-30 .. 2026-11-02 only
INSERT INTO upstack_attr_days (day) SELECT d::date FROM generate_series('2026-10-30'::date, '2026-11-02'::date, interval '1 day') d;
INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, link_clicks, impressions, purchases, conv_value, nc_purchases, nc_conv_value)
SELECT d.ad_id, d.day, d.meta_family_key, d.meta_family, d.campaign_id, d.spend, d.link_clicks, d.impressions,
       round(d.purchases) + 1, round(d.conv_value * 1.1, 2), round(d.purchases / 2), round(d.conv_value / 2, 2)
FROM ad_daily d WHERE d.day BETWEEN '2026-10-30' AND '2026-11-02' AND d.source = 'meta_api';
-- any-click credit on a day the ad had no ad_daily row (spend 0)
INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, nc_purchases, nc_conv_value)
SELECT a.ad_id, '2026-10-31', a.meta_family_key, coalesce(a.family_name, a.meta_family_key), coalesce(a.campaign_id, ''), 0, 3, 210.50, 2, 120.25
FROM meta_ads a WHERE NOT EXISTS (SELECT 1 FROM ad_daily d WHERE d.ad_id = a.ad_id AND d.day = '2026-10-31') ORDER BY a.ad_id LIMIT 1;

INSERT INTO upstack_google_campaigns (campaign_id, name, status, bid_strategy, campaign_type) VALUES
  ('G1', 'Standard Shopping | All', 'ENABLED', 'MAXIMIZE_CONVERSION_VALUE', 'Shopping'), ('G2', 'Brand Search', 'ENABLED', 'TARGET_ROAS', 'Search');
INSERT INTO upstack_google_days (day, campaigns, nc, orders) SELECT d::date, 2, 4, 6 FROM generate_series('2026-10-30'::date, '2026-11-02'::date, interval '1 day') d;
-- google_daily also holds Google's own numbers for days before Upstack's attribution was pulled (nc / orders there are 0 by construction, set non-zero to prove the NULL mask)
INSERT INTO google_daily (day, campaign_key, campaign_name, campaign_type, in_feed, spend, impressions, clicks, purchases, purchases_value, nc, nc_rev, orders, rev)
SELECT d::date, 'G1', 'Standard Shopping | All', 'Shopping', true, 100, 5000, 200, 4, 280, 2, 140, 3, 210 FROM generate_series('2026-10-27'::date, '2026-11-02'::date, interval '1 day') d
UNION ALL SELECT d::date, 'G2', 'Brand Search', 'Search', true, 40, 900, 120, 2, 140, 1, 70, 2, 140 FROM generate_series('2026-10-27'::date, '2026-11-02'::date, interval '1 day') d
UNION ALL SELECT '2026-11-01'::date, 'name:(not set)', '(not set)', 'Other', false, 0, 0, 0, 0, 0, 1, 66, 1, 66;

INSERT INTO upstack_orders_daily (day, nc_rev, rc_rev, nc_orders, total_rev, orders)
SELECT d::date, 1000 + 10 * extract(day FROM d), 2500, 20, 3500 + 10 * extract(day FROM d), 70 FROM generate_series('2026-10-25'::date, '2026-11-02'::date, interval '1 day') d;

INSERT INTO proj_feed_meta_reach (grain, period_key, period_start, period_end, impressions, reach, unique_link_clicks, link_clicks, unique_outbound_clicks) VALUES
  ('day', '2026-11-02', '2026-11-02', '2026-11-02', 100000, 40000, 900, 1100, 850), ('day', '2026-11-03', '2026-11-03', '2026-11-03', 50000, 0, 0, 0, 0),
  ('week', '2026-W44', '2026-10-26', '2026-11-01', 700000, 150000, 6000, 7500, 5800), ('month', '2026-10', '2026-10-01', '2026-10-31', 3000000, 500000, 25000, 31000, 24000);
INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value) VALUES
  ('2024-12-31', 'H1', 'sheet', 'Old Bull Campaign', 'VitaBull', 'Scaling', 500, 40000, 600, 7, 350), ('2025-01-01', 'H1', 'sheet', 'Old Bull Campaign', 'VitaBull', 'Scaling', 450, 38000, 550, 6, 300),
  ('2026-11-01', 'C100', 'app', 'VB_Scaling_US', 'VitaBull', 'Scaling', 200, 10000, 200, 5, 250), ('2026-11-01', 'C100', 'sheet', 'VB_Scaling_US', 'VitaBull', 'Scaling', 999, 99999, 999, 99, 999);
INSERT INTO etl_runs (job, status, finished_at, data_through, rows_written, stats, error) VALUES ('build_metrics', 'failed', now(), '2026-11-02', 5, '{"x": 1}', repeat('e', 800));
"""


@pytest.fixture(scope="module")
def sdb():
    """Own database (not the shared API fixture DB): migrated, seeded like the API fixture, plus rows in every Upstack / Google / orders / feed table."""
    try:
        S.recreate_db(f"{PG_BASE}/postgres", DB_NAME)
        with psycopg.connect(DB_URL, autocommit=True) as c:
            S.apply_migrations(c)
        with psycopg.connect(DB_URL) as c:
            S.seed(c)
            c.execute(EXTRA)
            c.commit()
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    conn = psycopg.connect(DB_URL, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    yield conn
    conn.close()


def q(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()


# ---------------------------------------------------------------------------------------------------------------- completeness (the guard)
def test_dictionary_is_complete(sdb):
    probs = gen.problems(gen.introspect(sdb), D)
    assert not probs, "the data dictionary is incomplete or stale (edit docs/schema/descriptions.py):\n" + "\n".join(probs[:40])


def test_every_relation_and_column_has_a_description_independent_check(sdb):
    """The same guarantee without the generator's own helpers: straight from information_schema."""
    desc_cols = {k: gen.resolve_cols(k, D.REL) for k in D.REL}
    rows = q(sdb, """SELECT t.table_schema, t.table_name, c.column_name FROM information_schema.tables t
                     JOIN information_schema.columns c ON c.table_schema = t.table_schema AND c.table_name = t.table_name
                     WHERE t.table_schema IN ('public', 'analytics') AND t.table_type IN ('BASE TABLE', 'VIEW')""")
    missing_rel, missing_col = set(), []
    for sch, rel, col in rows:
        key = f"{sch}.{rel}"
        if key in D.EXCLUDED:
            continue
        if key not in D.REL:
            missing_rel.add(key)
        elif not desc_cols[key].get(col):
            missing_col.append(f"{key}.{col}")
    assert not missing_rel, f"tables / views without a description: {sorted(missing_rel)}"
    assert not missing_col, f"columns without a description: {missing_col[:30]}"


def test_guard_fires_on_a_new_table_a_new_column_and_a_stale_description(sdb):
    c = psycopg.connect(DB_URL)                         # a writable connection; everything below is rolled back
    try:
        c.execute("CREATE TABLE public.zz_new_table (a int)")
        c.execute("ALTER TABLE public.ad_daily ADD COLUMN zz_new_col int")
        c.execute("CREATE VIEW analytics.zz_new_view AS SELECT 1 AS x")
        probs = gen.problems(gen.introspect(c), D)
        assert any(p.startswith("public.zz_new_table:") for p in probs)
        assert any(p.startswith("public.ad_daily.zz_new_col:") for p in probs)
        assert any(p.startswith("analytics.zz_new_view:") for p in probs)
        c.rollback()
        # a description of a column that is gone (meta_family: no view reads it, so Postgres lets it go; for a column a view reads, the DROP itself is refused)
        c.execute("ALTER TABLE public.ad_daily DROP COLUMN meta_family")
        probs = gen.problems(gen.introspect(c), D)
        assert any(p.startswith("public.ad_daily.meta_family:") and "stale" in p for p in probs)
    finally:
        c.rollback()
        c.close()


def test_guard_fires_on_missing_text_and_missing_examples(sdb):
    live = gen.introspect(sdb)

    class Patched:                                      # a copy of the descriptions with holes
        GROUPS, EXCLUDED = D.GROUPS, D.EXCLUDED
        REL = {k: dict(v) for k, v in D.REL.items()}
    Patched.REL["analytics.orders_daily"]["cols"] = {k: v for k, v in D.REL["analytics.orders_daily"]["cols"].items() if k != "day"}
    Patched.REL["analytics.orders_daily"]["examples"] = D.REL["analytics.orders_daily"]["examples"][:1]
    Patched.REL["public.ad_daily"]["grain"] = ""
    probs = gen.problems(live, Patched)
    assert "analytics.orders_daily.day: column has no description" in probs
    assert any("analytics.orders_daily: an analytics view needs at least 2 example queries" == p for p in probs)
    assert "public.ad_daily: missing required field 'grain'" in probs


def test_analytics_contains_only_views_and_none_of_the_excluded_names(sdb):
    rel = q(sdb, "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'analytics' AND c.relkind NOT IN ('i', 'c', 't', 'I')")
    assert rel and all(k == "v" for _, k in rel), rel
    names = {n for n, _ in rel}
    assert {"meta_ad_daily", "meta_adset_daily", "meta_campaign_daily", "meta_ads", "google_campaign_daily", "platform_daily", "orders_daily", "creative_tracker", "etl_runs"} <= names
    forbidden = ("feed_oauth", "schema_migrations", "session", "login", "auth", "password", "oauth", "token")
    for n in names:
        assert not any(f in n for f in forbidden), n
    # no analytics column leaks a secret-looking name
    cols = q(sdb, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'analytics'")
    for t, c in cols:
        assert not any(f in c for f in ("password", "secret", "token", "oauth", "api_key")), (t, c)
    # nothing projection-finance is reachable from a view: every referenced public relation is a marketing one
    deps = q(sdb, """SELECT DISTINCT cl.relname FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid JOIN pg_class v ON v.oid = r.ev_class JOIN pg_namespace vn ON vn.oid = v.relnamespace
                     JOIN pg_class cl ON cl.oid = d.refobjid JOIN pg_namespace cn ON cn.oid = cl.relnamespace
                     WHERE vn.nspname = 'analytics' AND cn.nspname = 'public' AND cl.relkind IN ('r', 'v')""")
    used = {r[0] for r in deps}
    assert "feed_oauth" not in used
    allowed_proj = {"proj_feed_meta_reach", "proj_feed_meta_day"}
    assert {u for u in used if u.startswith("proj_")} <= allowed_proj, used


# ---------------------------------------------------------------------------------------------------------------- generated files and comments
def test_generated_docs_are_up_to_date(sdb):
    md, js, _ = gen.generate(sdb)
    assert (ROOT / "docs" / "DATA_SCHEMA.md").read_text() == md, "docs/DATA_SCHEMA.md is stale: python scripts/gen_schema_doc.py --write"
    assert (ROOT / "docs" / "schema.json").read_text() == js, "docs/schema.json is stale: python scripts/gen_schema_doc.py --write"


def test_database_comments_equal_descriptions(sdb):
    """psql \\d+, DBeaver and introspecting agents see exactly the dictionary. A description change after migrations 013 / 190 / 191 shipped needs a NEW
    migration: python scripts/gen_schema_doc.py --comments all --out migrations/NNN_schema_comments_<what>.sql"""
    model = gen.build_model(gen.introspect(sdb), D)
    want = gen.expected_comments(model, D)
    have = {}
    for sch, rel, oid in q(sdb, "SELECT n.nspname, c.relname, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('public', 'analytics') AND c.relkind IN ('r', 'v', 'p')"):
        have[(f"{sch}.{rel}", None)] = one(sdb, "SELECT obj_description(%s::oid, 'pg_class')", (oid,))[0]
        for attnum, col in q(sdb, "SELECT attnum, attname FROM pg_attribute WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped", (oid,)):
            have[(f"{sch}.{rel}", col)] = one(sdb, "SELECT col_description(%s::oid, %s)", (oid, attnum))[0]
    have[("analytics", None)] = one(sdb, "SELECT obj_description('analytics'::regnamespace)")[0]
    bad = [k for k, v in want.items() if have.get(k) != v]
    assert not bad, f"{len(bad)} comment(s) differ from docs/schema/descriptions.py, first: {bad[:5]}"


def test_example_queries_run(sdb):
    n = 0
    for key, spec in D.REL.items():
        for title, sql in spec.get("examples") or []:
            try:
                q(sdb, sql)
            except Exception as e:                      # noqa: BLE001
                pytest.fail(f"example '{title}' of {key} does not run: {e}\n{sql}")
            n += 1
    assert n >= 25


# ---------------------------------------------------------------------------------------------------------------- the views mean what they say
def test_meta_ad_daily_reconciles_with_ad_daily_and_upstack(sdb):
    assert one(sdb, "SELECT count(*) FROM (SELECT day, ad_id FROM analytics.meta_ad_daily GROUP BY 1, 2 HAVING count(*) > 1) x")[0] == 0     # grain (day, ad_id)
    # spend, clicks, impressions, purchases, revenue: identical to ad_daily on every day, nothing double counted by the Upstack copy
    a = q(sdb, "SELECT day, sum(spend), sum(link_clicks), sum(impressions), sum(purchases), sum(conv_value) FROM ad_daily GROUP BY 1 ORDER BY 1")
    b = q(sdb, "SELECT day, sum(spend), sum(link_clicks), sum(impressions), sum(meta_purchases), sum(meta_revenue) FROM analytics.meta_ad_daily GROUP BY 1 ORDER BY 1")
    assert a == b
    # Upstack side: identical to ad_daily_upstack, NULL (not 0) on days it was not pulled
    up = one(sdb, "SELECT sum(nc_purchases), sum(nc_conv_value), sum(purchases), sum(conv_value) FROM ad_daily_upstack")
    mv = one(sdb, "SELECT sum(upstack_new_customers), sum(upstack_new_customer_revenue), sum(upstack_orders), sum(upstack_order_revenue) FROM analytics.meta_ad_daily")
    assert up == mv
    assert one(sdb, "SELECT count(*) FROM analytics.meta_ad_daily WHERE day < '2026-10-30' AND upstack_orders IS NOT NULL")[0] == 0
    assert one(sdb, "SELECT count(*) FROM analytics.meta_ad_daily WHERE day < '2026-10-30'")[0] > 0
    assert one(sdb, "SELECT count(*) FROM analytics.meta_ad_daily WHERE day >= '2026-10-30' AND upstack_orders IS NULL")[0] == 0
    # the credit-only ad-day: spend 0, Meta side 0, source NULL, Upstack side filled
    r = one(sdb, "SELECT spend, impressions, meta_purchases, upstack_orders, upstack_new_customers, source FROM analytics.meta_ad_daily WHERE day = '2026-10-31' AND source IS NULL")
    assert r is not None and r[0] == 0 and r[1] == 0 and r[2] == 0 and r[3] == 3 and r[4] == 2
    # legacy family-grain rows survive with NULL campaign / ad set instead of '' (and no ad name)
    r = one(sdb, "SELECT count(*), count(campaign_id), count(adset_id), count(ad_name) FROM analytics.meta_ad_daily WHERE source = 'sheet'")
    assert r[0] > 0 and r[1:] == (0, 0, 0)


def test_adset_and_campaign_rollups_equal_the_ad_level_sums(sdb):
    cols = "spend, impressions, link_clicks, meta_purchases, meta_revenue, upstack_new_customers, upstack_new_customer_revenue, upstack_orders, upstack_order_revenue"
    total = ", ".join(f"sum({c})" for c in cols.split(", "))
    ad = one(sdb, f"SELECT {total} FROM analytics.meta_ad_daily")
    assert one(sdb, f"SELECT {total} FROM analytics.meta_adset_daily") == ad
    assert one(sdb, f"SELECT {total} FROM analytics.meta_campaign_daily") == ad
    # per day too, and the rollup carries the right names
    assert q(sdb, f"SELECT day, {total} FROM analytics.meta_campaign_daily GROUP BY 1 ORDER BY 1") == q(sdb, f"SELECT day, {total} FROM analytics.meta_ad_daily GROUP BY 1 ORDER BY 1")
    r = one(sdb, "SELECT campaign_name, stage, account FROM analytics.meta_campaign_daily WHERE campaign_id = 'C200' LIMIT 1")
    assert r == ("VB_Testing_Cambium", "testing", "Lonvera Main")
    r = one(sdb, "SELECT adset_name, campaign_id FROM analytics.meta_adset_daily WHERE adset_id = 'Sa1' LIMIT 1")
    assert r == ("VB scale set", "C100")
    # grain
    assert one(sdb, "SELECT count(*) FROM (SELECT day, adset_id FROM analytics.meta_adset_daily GROUP BY 1, 2 HAVING count(*) > 1) x")[0] == 0
    assert one(sdb, "SELECT count(*) FROM (SELECT day, campaign_id FROM analytics.meta_campaign_daily GROUP BY 1, 2 HAVING count(*) > 1) x")[0] == 0
    # ads_with_spend counts distinct ads with spend that day
    r = one(sdb, "SELECT ads_with_spend FROM analytics.meta_campaign_daily WHERE campaign_id = 'C100' AND day = '2026-11-02'")
    assert r[0] == one(sdb, "SELECT count(DISTINCT ad_id) FROM ad_daily WHERE campaign_id = 'C100' AND day = '2026-11-02' AND spend > 0")[0]


def test_meta_ads_product_follows_the_products_table_rule(sdb):
    from app import queries as aq
    rule = dict(q(sdb, f"WITH {aq._pfa('all')} SELECT a.ad_id, pr.slug FROM meta_ads a LEFT JOIN pfa ON pfa.meta_family_key = a.meta_family_key LEFT JOIN products pr ON pr.id = pfa.product_id"))
    got = dict(q(sdb, "SELECT ad_id, product_slug FROM analytics.meta_ads"))
    assert got == rule and any(v for v in got.values())
    assert one(sdb, "SELECT count(*) FROM analytics.meta_ads")[0] == one(sdb, "SELECT count(*) FROM meta_ads")[0]                  # one row per ad
    # lifetime totals and stage come through
    r = one(sdb, "SELECT lifetime_spend, stage, is_active FROM analytics.meta_ads WHERE ad_id = 'a1'")
    life = one(sdb, "SELECT spend FROM meta_ad_lifetime WHERE ad_id = 'a1'")
    assert r[0] == (life[0] if life else None) and r[1] in ("testing", "scaling") and r[2] is True


def test_google_campaign_daily_masks_upstack_before_it_was_pulled(sdb):
    assert one(sdb, "SELECT sum(spend), sum(google_purchases) FROM analytics.google_campaign_daily") == one(sdb, "SELECT sum(spend), sum(purchases) FROM google_daily")
    assert one(sdb, "SELECT count(*) FROM analytics.google_campaign_daily WHERE day < '2026-10-30' AND upstack_orders IS NOT NULL")[0] == 0
    assert one(sdb, "SELECT count(*) FROM analytics.google_campaign_daily WHERE day >= '2026-10-30' AND upstack_orders IS NULL")[0] == 0
    r = one(sdb, "SELECT campaign_status, bid_strategy FROM analytics.google_campaign_daily WHERE campaign_key = 'G2' LIMIT 1")
    assert r == ("ENABLED", "TARGET_ROAS")
    r = one(sdb, "SELECT in_feed, campaign_status, upstack_new_customers FROM analytics.google_campaign_daily WHERE campaign_key LIKE 'name:%'")
    assert r == (False, None, 1)


def test_platform_daily_spend_adds_up_and_total_nc_roas_is_actual_revenue_over_spend(sdb):
    assert one(sdb, "SELECT count(*) FROM (SELECT day, platform FROM analytics.platform_daily GROUP BY 1, 2 HAVING count(*) > 1) x")[0] == 0
    assert set(r[0] for r in q(sdb, "SELECT DISTINCT platform FROM analytics.platform_daily")) == {"meta", "google"}
    assert q(sdb, "SELECT day, spend FROM analytics.platform_daily WHERE platform = 'meta' ORDER BY 1") == q(sdb, "SELECT day, sum(spend) FROM ad_daily GROUP BY 1 ORDER BY 1")
    assert q(sdb, "SELECT day, spend FROM analytics.platform_daily WHERE platform = 'google' ORDER BY 1") == q(sdb, "SELECT day, sum(spend) FROM google_daily GROUP BY 1 ORDER BY 1")
    assert one(sdb, "SELECT sum(clicks) FROM analytics.platform_daily WHERE platform = 'meta'")[0] == one(sdb, "SELECT sum(link_clicks) FROM ad_daily")[0]
    assert one(sdb, "SELECT sum(clicks) FROM analytics.platform_daily WHERE platform = 'google'")[0] == one(sdb, "SELECT sum(clicks) FROM google_daily")[0]
    # the documented Total NC ROAS query (days both platforms and the orders feed cover), recomputed by hand
    ex = next(sql for t, sql in D.REL["analytics.platform_daily"]["examples"] if t.startswith("Total NC ROAS for a window"))
    days, spend, rev, roas = one(sdb, ex)
    both = q(sdb, """SELECT m.day, m.spend + g.spend, o.nc_rev FROM (SELECT day, sum(spend) spend FROM ad_daily GROUP BY 1) m
                     JOIN (SELECT day, sum(spend) spend FROM google_daily GROUP BY 1) g USING (day) JOIN upstack_orders_daily o USING (day)
                     WHERE m.day > (SELECT max(day) FROM ad_daily) - 30""")
    assert days == len(both) > 0
    assert round(float(spend)) == round(float(sum(b[1] for b in both)))
    assert float(roas) == pytest.approx(float(sum(b[2] for b in both)) / float(sum(b[1] for b in both)), abs=1e-4)


def test_orders_daily_and_etl_runs(sdb):
    assert q(sdb, "SELECT day, new_customer_revenue, new_customers, returning_customer_revenue, total_revenue, orders FROM analytics.orders_daily ORDER BY 1") == \
        q(sdb, "SELECT day, nc_rev, nc_orders, rc_rev, total_rev, orders FROM upstack_orders_daily ORDER BY 1")
    r = one(sdb, "SELECT length(error), status, stats FROM analytics.etl_runs WHERE job = 'build_metrics' AND status = 'failed'")
    assert r[0] == 500 and r[1] == "failed" and r[2] == {"x": 1}


def test_meta_reach_history_and_coverage(sdb):
    r = q(sdb, "SELECT period_key, frequency FROM analytics.meta_reach ORDER BY grain, period_key")
    d = dict(r)
    assert float(d["2026-11-02"]) == 2.5 and d["2026-11-03"] is None and float(d["2026-W44"]) == pytest.approx(4.6667, abs=1e-4)
    h = q(sdb, "SELECT day, source, spend FROM analytics.meta_campaign_daily_history ORDER BY day, source")
    assert [x[0].isoformat() for x in h] == ["2024-12-31", "2025-01-01", "2026-11-01"]
    assert [x[1] for x in h] == ["sheet", "sheet", "app"]                      # the sheet row of an app day is dropped
    cov = {r[0]: r for r in q(sdb, "SELECT source, first_day, last_day, days, rows FROM analytics.data_coverage")}
    assert cov["meta_ad_daily"][1:3] == (dt.date(2026, 5, 6), dt.date(2026, 11, 2))
    assert cov["upstack_meta_attribution"][1:4] == (dt.date(2026, 10, 30), dt.date(2026, 11, 2), 4)
    assert cov["orders_daily"][1:3] == (dt.date(2026, 10, 25), dt.date(2026, 11, 2))
    assert cov["google_campaign_daily"][2] == dt.date(2026, 11, 2)
    assert set(cov) == {"meta_ad_daily", "upstack_meta_attribution", "google_campaign_daily", "upstack_google_attribution", "orders_daily", "meta_reach (day grain)",
                        "meta_campaign_daily_history"}


def test_creative_tracker_view(sdb):
    alive = one(sdb, "SELECT count(*) FROM tracker_rows WHERE deleted_at IS NULL")[0]
    assert one(sdb, "SELECT count(*), count(DISTINCT tracker_row_id) FROM analytics.creative_tracker") == (alive, alive)
    assert one(sdb, "SELECT sum(spend_launch), sum(purchases_launch), count(*) FILTER (WHERE is_first_row) FROM analytics.creative_tracker") == \
        one(sdb, "SELECT sum(m.spend_launch), sum(m.purchases_launch), count(*) FILTER (WHERE m.is_first_row) FROM ad_metrics m JOIN tracker_rows t ON t.id = m.tracker_row_id WHERE t.deleted_at IS NULL")
    # the tag shown is the current tag of the PRODUCT's tag set
    want = q(sdb, """SELECT t.id, g.tag_set, g.cluster, g.icp, g.pain_point FROM tracker_rows t JOIN products p ON p.id = t.product_id
                     LEFT JOIN ad_tags g ON g.tracker_row_id = t.id AND g.is_current AND g.tag_set = p.tag_set WHERE t.deleted_at IS NULL ORDER BY 1""")
    got = q(sdb, "SELECT tracker_row_id, tag_set, cluster, icp, pain_point FROM analytics.creative_tracker ORDER BY 1")
    assert got == want and any(r[1] == "linemap" for r in got) and any(r[1] == "tracker" for r in got)
    # secrets / free-form blobs are not exposed
    cols = {r[0] for r in q(sdb, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = 'creative_tracker'")}
    assert "raw" not in cols and "deleted_at" not in cols


def test_example_queries_cover_the_four_asked_for_cases():
    titles = " | ".join(t for k, s in D.REL.items() if k.startswith("analytics.") for t, _ in s.get("examples", []))
    assert "Ad level: 30-day spend" in titles                                  # ad-level 30-day spend / ROAS
    assert "Daily spend and Meta ROAS of one campaign's ad sets" in titles      # ad set roll-up by day
    assert "Campaigns, last 7 days" in titles and "Daily spend, testing vs scaling" in titles   # campaign roll-up
    assert "Total spend and Total NC ROAS by day" in titles                     # Meta vs Google total spend / NC ROAS by day
    assert "Vita Bull since-launch spend" in titles                            # creative family with tags
