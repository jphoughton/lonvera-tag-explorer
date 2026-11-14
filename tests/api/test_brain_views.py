"""Migration 230: every subject area of the database is readable through the `analytics` schema, with a domain, a catalog and a metric glossary.

  * every analytics view has a domain from DOMAINS and a description; analytics.catalog lists exactly the views, with the domains of descriptions.py;
  * the metric glossary has the contract shape (name, label, formula, source, default_basis, other_bases, traps, domain), schema.json carries it under "metrics" and
    analytics.metric_glossary holds the same rows; every `source` names a real view and column;
  * the views mean what they say: flattened priorities rows, flattened scenario levers, the cohort curve inputs (compared with the engine's own live_curve),
    the three bases of the creative roll-ups, the production stage, the first / last day of the catalog;
  * what migration 230 leaves out stays out (the reader-role test checks the privileges; here the views carry no person or secret column).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import psycopg
import pytest

from . import seed_fixture as S
from .test_schema_docs import D, gen

ROOT = Path(__file__).resolve().parents[2]
PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_API_DB", "tagexp_apitest") + "_brainviews"          # not "_brain": tests/brain holds a session database of that name, and a worker running both would drop it
DB_URL = f"{PG_BASE}/{DB_NAME}"

ROWS = [
    {"product": "vita-bull", "product_name": "Vital Bull", "section": "iterate", "status": "WORKING", "rank": 1, "cluster": "Energy", "icp": "Dale", "pain_point": "Low energy",
     "emotional_trigger": "Shame", "story": "s", "concepts": 3, "score": 71.5, "confidence": "high", "flags": ["thin sample", "few ads"], "why": "because", "line_code": "VB1",
     "metrics": {"spend": 12000.5, "purchases": 150, "roas": 1.1, "cpa": 80.0, "roas_index": 1.2, "ads": 9, "judged": 8, "hits": 2, "mature": 6, "hit_rate": 0.3333, "hit_rate_index": 1.4,
                 "spend_30d": 3000, "roas_30d": 1.0, "spend_14d": 1500, "roas_14d": None, "nc_roas": 0.7, "nc_cpa": 110, "nc_purchases": 100, "live_ads": 4,
                 "funnel": {"launched": 40, "under_tested": 20, "tested": 20, "top": 4}}},
    {"product": "boneflex", "product_name": "Boneflex", "section": "new", "rank": 2, "cluster": "Joints", "icp": "Eileen", "pain_point": "Floor", "manual": True, "key": "manual:3", "metrics": {}},
]
THRESHOLDS = {"HIT_SPEND": 1000.0, "TOP_SPEND": 10000.0, "JUDGED_SPEND": 100.0, "MATURE_DAYS": 14}
BASELINES = {"vita-bull": {"roas": 0.9, "cpa": 85.5, "hit_rate": 0.12, "label": "not a number", "funnel": {"launched": 40}}}

# six cohorts: cum dollars per customer by age, with a zero cell and a gap on purpose (a blank / zero cell is "not observed")
COH = [("2026-01-01", 1000, 50.0, [60, 66, 70, 73, 75, 0, 78, 80]),
       ("2026-02-01", 1200, 52.0, [62, 69, 73, 77, 80, 83]),
       ("2026-03-01", 1500, 48.0, [58, 63, 67, 70, 72]),
       ("2026-04-01", 2000, 55.0, [66, 74, 79, 83]),
       ("2026-05-01", 2500, 51.0, [61, 66, 70]),
       ("2026-06-01", 3000, 53.0, [64, 71])]

EXTRA = f"""
INSERT INTO creative_priorities_snapshots (week_start, data_through, basis, thresholds, baselines, rows, stats)
VALUES ('2026-11-08', '2026-11-07', 'meta', '{json.dumps(THRESHOLDS)}', '{json.dumps(BASELINES)}', '{json.dumps(ROWS)}', '{{}}');
INSERT INTO proj_scenarios (slug, name, base_slug, params) VALUES
  ('what-if', 'What if', 'current', '{json.dumps({"drivers": {"sales_tax_rate": 0.0}, "financing": {"payback": {"aov": 73, "cac": 90}}, "scalar": 5})}'),
  ('old', 'Old', NULL, '{{"x": {{"y": 1}}}}');
UPDATE proj_scenarios SET archived_at = now() WHERE slug = 'old';
INSERT INTO proj_series (scenario_id, line_key, month, value, kind) SELECT id, 'financing.principal_paid', '2026-12-01', 1000, 'input' FROM proj_scenarios WHERE slug IN ('what-if', 'old');
INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, brief_url, created_by, updated_by) VALUES
  ('line:1', 'text:a', 'vita-bull', 'Madre', '2026-11-09', 'https://example.com/brief', 'someone', 'someone');
INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, brief_url, creative_url, ready_at, created_by) VALUES
  ('line:2', 'text:b', 'boneflex', 'Victoria', '2026-11-09', 'https://example.com/brief', 'https://example.com/creative', now(), 'someone');
INSERT INTO proj_feed_runs (feed, source, rows_loaded, first_day, last_day) VALUES ('orders', 'sheet:Orders - History', 10, '2026-10-01', '2026-11-02');
INSERT INTO proj_feed_config (feed, mode, changed_by) VALUES ('orders', 'shadow', 'someone');
INSERT INTO proj_feed_orders (day, nc_rev, rc_rev, nc_orders, total_rev, total_orders, tax, refunds) VALUES ('2026-11-01', 1000, 2500, 20, 3500, 70, 200, 100);
INSERT INTO proj_feed_platform (day, platform, spend, purchases, purchase_value) VALUES ('2026-11-01', 'Meta (Facebook/Instagram)', 900, 40, 1500), ('2026-11-01', 'Google Ads', 100, 5, 300);
INSERT INTO proj_feed_meta_product (day, product, source, spend, link_clicks) VALUES ('2026-11-01', 'VitaBull', 'app', 100, 50), ('2026-11-01', 'VitaBull', 'sheet', 999, 999), ('2026-10-01', 'VitaBull', 'sheet', 80, 40);
INSERT INTO proj_curve_goal (snapshot_date, age, value, snapshot_id) VALUES ('2026-09-29', 1, 0.2, 1), ('2026-10-30', 1, 0.25, 2), ('2026-10-30', 2, 0.1, 2);
INSERT INTO proj_po (po_number, product, vendor, warehouse, units, land_date, status, link) VALUES ('PO1', 'bull', 'china', 'revamp', 1000, '2026-12-01', 'planned', 'https://example.com/secret-ish');
INSERT INTO alert_state (check_id, first_seen, last_seen, alert_count, fingerprint, last_message) VALUES ('c1', now(), now(), 1, 'fp', 'something broke');
"""


@pytest.fixture(scope="module")
def bdb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", DB_NAME)
        with psycopg.connect(DB_URL, autocommit=True) as c:
            S.apply_migrations(c)
        with psycopg.connect(DB_URL) as c:
            S.seed(c)
            c.execute(EXTRA)
            for m, n, fo, cum in COH:
                c.execute("INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer, source) VALUES (%s, %s, %s, 't')", (m, n, fo))
                for age, v in enumerate(cum, start=1):
                    c.execute("INSERT INTO proj_cohort_cum (cohort_month, age, cum_per_customer) VALUES (%s, %s, %s)", (m, age, v))
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


# ---------------------------------------------------------------------------------------------------------------- domains, catalog, glossary
def test_every_analytics_view_has_a_domain_and_a_description():
    views = {k: v for k, v in D.REL.items() if k.startswith("analytics.")}
    assert len(views) >= 80
    domains = {k for k, _ in D.DOMAINS}
    assert domains == {"marketing", "attribution", "orders", "creative", "tags", "priorities", "production", "projection", "inventory", "cohorts", "pacing", "competitors", "pipeline", "email", "landing_pages", "story", "amazon"}
    for k, v in views.items():
        assert v.get("domain") in domains, k
        assert len(str(v.get("purpose") or "").strip()) > 20, k
    assert {v["domain"] for v in views.values()} == domains                      # every domain has at least one view (email and landing_pages only through migration 231)
    assert {v["domain"] for k, v in views.items() if k not in D.DEFERRED} == domains - {"email", "landing_pages"}
    assert views["analytics.meta_ad_daily"]["domain"] == "marketing" and views["analytics.priorities_concepts"]["domain"] == "priorities"


def test_catalog_lists_every_view_with_the_domains_of_the_descriptions(bdb):
    live = {r[0] for r in q(bdb, "SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")}
    cat = {r[0]: r for r in q(bdb, "SELECT view_name, domain, description, grain, first_day, last_day, row_estimate FROM analytics.catalog")}
    documented = {k.split(".", 1)[1] for k in D.REL if k.startswith("analytics.")}
    assert set(cat) == live and live <= documented and documented - live <= {n.split(".", 1)[1] for n in D.DEFERRED}
    for name, r in cat.items():
        spec = D.REL[f"analytics.{name}"]
        assert r[1] == spec["domain"] and r[2] == spec["purpose"].strip() and r[3] == spec["grain"].strip(), name
    # first / last day come from the base table; a view with no day axis has none
    assert cat["orders_daily_detail"][4] == dt.date(2026, 11, 1) and cat["orders_daily_detail"][5] == dt.date(2026, 11, 1)
    assert cat["meta_ad_daily"][4] == one(bdb, "SELECT min(day) FROM ad_daily")[0] and cat["meta_ad_daily"][5] == one(bdb, "SELECT max(day) FROM ad_daily")[0]
    assert cat["tag_taxonomy_lines"][4] is None and cat["tag_products"][5] is None
    assert cat["cohorts_monthly"][4] == dt.date(2026, 1, 1) and cat["cohorts_monthly"][5] == dt.date(2026, 6, 1)
    assert all(r[6] is None or r[6] >= 0 for r in cat.values())
    assert one(bdb, "SELECT count(*) FROM analytics.catalog WHERE domain = 'creative'")[0] >= 8


def test_metric_glossary_contract_shape():
    names = [m["name"] for m in D.METRICS]
    assert len(names) == len(set(names)) >= 20
    for m in D.METRICS:
        assert set(m) == {"name", "label", "formula", "source", "default_basis", "other_bases", "traps", "domain"}, m["name"]
        assert all(isinstance(m[k], str) and m[k].strip() for k in ("name", "label", "formula", "source", "default_basis")), m["name"]
        assert isinstance(m["other_bases"], list) and m["other_bases"] and all(isinstance(x, str) for x in m["other_bases"]), m["name"]
        assert isinstance(m["traps"], list) and m["traps"] and all(isinstance(x, str) for x in m["traps"]), m["name"]
        assert m["domain"] in {k for k, _ in D.DOMAINS}, m["name"]
        assert all(p.strip().startswith("analytics.") for p in m["source"].split(";")), m["name"]
    # the metrics the task asks for are there
    want = {"spend", "meta_purchases", "meta_roas", "upstack_nc_roas", "upstack_all_orders_roas", "nc_revenue", "total_nc_roas", "nc_cpa", "conversion_rate_uoc", "cpm", "frequency", "hit_rate",
            "test_to_scale_rate", "repeat_revenue", "retention_curve", "payback", "gross_vs_net_revenue", "sales_tax_deduction"}
    assert want <= set(names)
    # the two hit-rate readings and the CSV split are spelled out
    hr = next(m for m in D.METRICS if m["name"] == "hit_rate")
    assert "10,000" in hr["default_basis"] and any("1,000" in b for b in hr["other_bases"]) and any("CSV" in t for t in hr["traps"])
    # default basis of the Upstack ROAS is new customers, with the never-compare trap
    nc = next(m for m in D.METRICS if m["name"] == "upstack_nc_roas")
    assert "new customers" in nc["default_basis"].lower() and any("Never compare Upstack new customers with Meta" in t for t in nc["traps"])


def test_schema_json_and_sql_carry_the_metrics(bdb):
    js = json.loads((ROOT / "docs" / "schema.json").read_text())
    assert [m["name"] for m in js["metrics"]] == [m["name"] for m in D.METRICS]
    for m, want in zip(js["metrics"], D.METRICS):
        assert m == {k: want[k] for k in D.METRIC_FIELDS} and list(m) == list(D.METRIC_FIELDS)
    md = (ROOT / "docs" / "DATA_SCHEMA.md").read_text()
    assert "\n## Metrics\n" in md and "### total_nc_roas" in md and "**Default basis:**" in md
    got = {r[0]: r for r in q(bdb, "SELECT name, label, formula, source, default_basis, other_bases, traps, domain FROM analytics.metric_glossary")}
    assert set(got) == {m["name"] for m in D.METRICS}
    for m in D.METRICS:
        assert list(got[m["name"]][1:]) == [m["label"], m["formula"], m["source"], m["default_basis"], m["other_bases"], m["traps"], m["domain"]]
    assert "domain" in js["relations"][0] and {r["domain"] for r in js["relations"] if r["schema"] == "analytics"} == {k for k, _ in D.DOMAINS}


def test_metric_sources_name_real_views_and_columns(bdb):
    probs = gen.metrics_problems(gen.introspect(bdb), D)
    assert not probs, probs
    # the check fires: a bad column, a bad view, a missing field
    class Bad:
        DOMAINS, METRIC_FIELDS = D.DOMAINS, D.METRIC_FIELDS
        METRICS = [dict(D.METRICS[0], source="analytics.orders_daily.nope"), dict(D.METRICS[1], source="analytics.no_such_view.x"), {k: v for k, v in D.METRICS[2].items() if k != "traps"}]
    live = gen.introspect(bdb)
    out = gen.metrics_problems(live, Bad)
    assert any("no column 'nope'" in p for p in out) and any("does not exist" in p for p in out) and any("fields must be exactly" in p for p in out)


def test_generator_catalog_sql_is_what_the_migration_holds():
    text = (ROOT / "migrations" / "230_analytics_brain_views.sql").read_text()
    assert gen.catalog_sql(D) in (ROOT / "migrations" / "262_change_timeline_catalog.sql").read_text()      # the latest migration that re-issues the catalog / glossary (230 holds the first version)
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analytics_reader" in text
    assert text.count("CREATE OR REPLACE VIEW analytics.") >= 69


# ---------------------------------------------------------------------------------------------------------------- the views mean what they say
def test_priorities_concepts_flatten_the_snapshot(bdb):
    r = one(bdb, "SELECT product_slug, section, status, rank, line_code, concepts, score, flags, is_manual, spend, roas, roas_14d, hit_rate, ads_launched, ads_10k_plus_spend, nc_roas "
                 "FROM analytics.priorities_concepts WHERE week_start = '2026-11-08' AND NOT is_manual")
    assert r[:8] == ("vita-bull", "iterate", "WORKING", 1, "VB1", 3, 71.5, "thin sample; few ads")
    assert r[8] is False and float(r[9]) == 12000.5 and float(r[10]) == 1.1 and r[11] is None and float(r[12]) == 0.3333
    assert r[13] == 40 and r[14] == 4 and float(r[15]) == 0.7
    m = one(bdb, "SELECT product_slug, is_manual, spend, ads_launched, line_code, status FROM analytics.priorities_concepts WHERE is_manual")
    assert m == ("boneflex", True, None, None, None, None)
    assert one(bdb, "SELECT count(*) FROM analytics.priorities_concepts")[0] == 2 == one(bdb, "SELECT row_count FROM analytics.priorities_snapshots")[0]
    assert dict(q(bdb, "SELECT name, value FROM analytics.priorities_thresholds")) == {k: str(v) for k, v in THRESHOLDS.items()}
    base = {r[0]: float(r[1]) for r in q(bdb, "SELECT metric, value FROM analytics.priorities_baselines WHERE product_slug = 'vita-bull'")}
    assert base == {"roas": 0.9, "cpa": 85.5, "hit_rate": 0.12}                     # numeric entries only: the label and the nested funnel are left out


def test_projection_levers_flatten_scenarios_and_hide_archived_ones(bdb):
    lv = {(r[0], r[1], r[2]): r[3] for r in q(bdb, "SELECT scenario_slug, module, lever, value FROM analytics.projection_levers")}
    assert lv[("what-if", "drivers", "sales_tax_rate")] == 0.0 and lv[("what-if", "financing", "payback")] == {"aov": 73, "cac": 90}
    assert ("what-if", "scalar", "x") not in lv and not [k for k in lv if k[0] == "old"]            # a non-object module is skipped; an archived scenario is not shown
    assert lv[("current", "financing", "debt")]["rate"] == 0.15                                      # the plan of record's seeded levers
    sc = {r[0]: r for r in q(bdb, "SELECT slug, is_default, archived_at FROM analytics.projection_scenarios")}
    assert sc["current"][1] is True and sc["old"][2] is not None and "params" not in {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'projection_scenarios'")}
    series = {r[0] for r in q(bdb, "SELECT DISTINCT scenario_slug FROM analytics.projection_series")}
    assert "what-if" in series and "old" not in series                                               # an archived scenario's cells are not shown


def test_cohort_curve_inputs_reproduce_the_engines_live_curve(bdb):
    from app.projection.cohorts import engine as E
    cohorts = []
    for m, n, fo, cum in COH:
        cohorts.append(E.Cohort(month=dt.date.fromisoformat(m), new_customers=float(n), first_order=fo, cum=tuple(float(v) for v in cum) + (None,) * (36 - len(cum))))
    for n_last in (3, 6):
        want = E.live_curve(cohorts, E.Levers(n_cohorts=n_last))
        got = dict(q(bdb, "SELECT age, sum(new_customers * increment_ratio) / NULLIF(sum(new_customers), 0) FROM analytics.cohorts_curve_cumulative WHERE usable_rank <= %s AND age < 28 GROUP BY 1", (n_last,)))
        for p in want[:27]:
            if p.source == "data":
                assert float(got[p.age]) == pytest.approx(p.value, abs=1e-9), (n_last, p.age)
            else:
                assert p.age not in got or got[p.age] is None
    # the zero cell (cohort 2026-01 age 6) is not usable and the age-7 increment after it is unknown; the first-order base is used at age 1
    z = one(bdb, "SELECT increment, increment_ratio, usable_rank FROM analytics.cohorts_curve_cumulative WHERE cohort_month = '2026-01-01' AND age = 6")
    assert z == (None, None, None)
    a1 = one(bdb, "SELECT increment, round(increment_ratio, 4) FROM analytics.cohorts_curve_cumulative WHERE cohort_month = '2026-06-01' AND age = 1")
    assert float(a1[0]) == pytest.approx(64 - 53) and float(a1[1]) == pytest.approx(round((64 - 53) / 53, 4))
    assert one(bdb, "SELECT usable_rank FROM analytics.cohorts_curve_cumulative WHERE cohort_month = '2026-06-01' AND age = 1")[0] == 1
    assert one(bdb, "SELECT first_order_revenue FROM analytics.cohorts_monthly WHERE cohort_month = '2026-06-01'")[0] == 3000 * 53


def test_goal_curve_marks_the_newest_snapshot_active(bdb):
    got = q(bdb, "SELECT snapshot_id, age, is_active FROM analytics.cohorts_curve_goal ORDER BY snapshot_id, age")
    assert got == [(1, 1, False), (2, 1, True), (2, 2, True)]
    assert "saved_by" not in {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'cohorts_curve_goal'")}


def test_creative_rollups_are_three_separate_bases(bdb):
    for view, table in (("creative_stage_moves", "creative_stage_moves"), ("creative_scaling_placements", "creative_scaling_placements"), ("creative_unmatched_meta", "unmatched_meta")):
        n = {b: one(bdb, f"SELECT count(*) FROM analytics.{view} WHERE basis = %s", (b,))[0] for b in ("meta", "upstack_new_customers", "upstack_all_orders")}
        total = sum(one(bdb, f"SELECT count(*) FROM public.{t}")[0] for t in (table, table + "_upstack_nc", table + "_upstack_all"))
        assert sum(n.values()) == one(bdb, f"SELECT count(*) FROM analytics.{view}")[0] == total, (view, n)
        assert n["meta"] == one(bdb, f"SELECT count(*) FROM public.{table}")[0]
    # the daily roll-up: six slices, each equal to its table; the planner sees one table when basis and scope are given
    for scope, tbl in (("all_ads", "family_daily"), ("active_ads", "family_daily_active")):
        for basis, suf in (("meta", ""), ("upstack_new_customers", "_upstack_nc"), ("upstack_all_orders", "_upstack_all")):
            a = one(bdb, "SELECT count(*), coalesce(sum(spend), 0), coalesce(sum(revenue), 0) FROM analytics.creative_family_daily WHERE basis = %s AND ads_scope = %s", (basis, scope))
            b = one(bdb, f"SELECT count(*), coalesce(sum(spend), 0), coalesce(sum(revenue), 0) FROM public.{tbl}{suf}")
            assert a == b, (basis, scope)
    assert one(bdb, "SELECT count(*) FROM analytics.creative_family_daily WHERE basis = 'meta' AND ads_scope = 'all_ads' AND product_slug IS NOT NULL")[0] > 0
    m = one(bdb, "SELECT count(*), count(*) FILTER (WHERE is_first_row) FROM analytics.creative_family_metrics WHERE basis = 'meta'")
    assert m == one(bdb, "SELECT count(*), count(*) FILTER (WHERE is_first_row) FROM public.ad_metrics")
    assert one(bdb, "SELECT count(*) FROM analytics.creative_family_metrics WHERE concept_id IS NOT NULL AND basis = 'meta'")[0] > 0


def test_tags_hooks_and_taxonomy_views(bdb):
    assert one(bdb, "SELECT count(*) FILTER (WHERE is_current) FROM analytics.tracker_tags") == one(bdb, "SELECT count(*) FROM public.ad_tags g JOIN public.tracker_rows t ON t.id = g.tracker_row_id WHERE g.is_current AND t.deleted_at IS NULL")
    assert one(bdb, "SELECT count(*) FROM analytics.tracker_tags WHERE line_code IS NOT NULL")[0] > 0
    assert one(bdb, "SELECT count(*) FROM analytics.tag_taxonomy_lines")[0] == one(bdb, "SELECT count(*) FROM public.taxonomy_lines")[0]
    assert one(bdb, "SELECT count(*) FROM analytics.tag_line_ingredients")[0] == one(bdb, "SELECT count(*) FROM public.line_ingredients")[0]
    assert one(bdb, "SELECT count(*) FROM analytics.tracker_hooks")[0] == one(bdb, "SELECT count(*) FROM public.ad_hooks h JOIN public.tracker_rows t ON t.id = h.tracker_row_id WHERE t.deleted_at IS NULL")[0]
    cols = {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name IN ('tracker_tags', 'tracker_hooks')")}
    assert "extra" not in cols


def test_feed_views_rename_and_apply_the_app_rule(bdb):
    assert one(bdb, "SELECT new_customer_revenue, returning_customer_revenue, new_customers, total_revenue, orders, tax, refunds FROM analytics.orders_daily_detail") == (1000, 2500, 20, 3500, 70, 200, 100)
    assert dict(q(bdb, "SELECT platform, spend FROM analytics.platform_daily_history")) == {"meta": 900, "google": 100}
    assert {r[0] for r in q(bdb, "SELECT platform_label FROM analytics.platform_daily_history")} == {"Meta (Facebook/Instagram)", "Google Ads"}
    # the app reads 'app' rows when a day has any, else the sheet rows: no day is counted twice
    assert q(bdb, "SELECT day, source, spend FROM analytics.meta_product_daily ORDER BY day") == [(dt.date(2026, 10, 1), "sheet", 80), (dt.date(2026, 11, 1), "app", 100)]
    f = one(bdb, "SELECT feed, mode, last_day FROM analytics.pipeline_feeds")
    assert f == ("orders", "shadow", dt.date(2026, 11, 2))
    assert "changed_by" not in {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'pipeline_feeds'")}
    assert one(bdb, "SELECT check_id, last_message FROM analytics.pipeline_alerts") == ("c1", "something broke")


def test_production_stage_and_person_columns(bdb):
    got = {r[0]: r[1] for r in q(bdb, "SELECT vendor, stage_from_assignment FROM analytics.production_assignments")}
    assert got == {"Madre": "brief_sent", "Victoria": "ready"}
    cols = {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'production_assignments'")}
    assert not ({"created_by", "updated_by"} & cols)


def test_purchase_orders_leave_out_the_link(bdb):
    cols = {c[0] for c in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'inventory_purchase_orders'")}
    assert "link" not in cols and {"po_id", "units", "land_date", "unit_cost"} <= cols
    assert one(bdb, "SELECT units FROM analytics.inventory_purchase_orders WHERE po_number = 'PO1'")[0] == 1000


def test_no_analytics_column_names_a_person_or_a_secret(bdb):
    bad_words = {"password", "secret", "token", "oauth", "actor", "session", "ip", "hash"}               # whole words of the snake_case name ('extractor' and 'sessions' are fine)
    cols = q(bdb, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'analytics'")
    leaks = [(t, c) for t, c in cols if (set(c.split("_")) & bad_words or c.endswith("_by") or "api_key" in c) and c != "content_hash"]
    assert not leaks, leaks
    # no view's definition reads the secret / audit / launch tables
    dep = q(bdb, """SELECT DISTINCT cl.relname FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid JOIN pg_class v ON v.oid = r.ev_class JOIN pg_namespace vn ON vn.oid = v.relnamespace
                    JOIN pg_class cl ON cl.oid = d.refobjid JOIN pg_namespace cn ON cn.oid = cl.relnamespace WHERE vn.nspname = 'analytics' AND cn.nspname = 'public'""")
    used = {r[0] for r in dep}
    assert not [u for u in used if u.startswith(("creative_launch_", "proj_feedx_")) or u in {"api_keys", "feed_oauth", "creative_priorities_views", "creative_priorities_vendor_links", "proj_audit"}], used


def test_every_new_view_runs_fast_enough_on_the_fixture(bdb):
    import time
    t0 = time.time()
    for key in D.BRAIN_VIEWS:
        q(bdb, f"SELECT count(*) FROM {key}")
    assert time.time() - t0 < 20


def test_comments_of_re_described_relations_are_rewritten_by_210(bdb):
    c = one(bdb, "SELECT obj_description('public.proj_scenarios'::regclass, 'pg_class')")[0]
    assert "analytics projection_* / inventory_* / cohorts_* views" in c and "not exposed in analytics" not in c
    assert "analytics.orders_by_product_daily" in one(bdb, "SELECT obj_description('analytics.orders_daily'::regclass, 'pg_class')")[0]
    assert "catalog" in one(bdb, "SELECT obj_description('analytics'::regnamespace)")[0]


# ---------------------------------------------------------------------------------------------------------------- migration 231: views over the Klaviyo, landing page and Shopify order tables
M231 = ROOT / "migrations" / "231_analytics_deferred_views.sql"


def test_231_creates_every_view_for_real_and_the_columns_match_the_source_tables(bdb):
    names = sorted(n.split(".", 1)[1] for n in D.DEFERRED)
    assert len(names) == 13
    live = {r[0] for r in q(bdb, "SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")}
    assert set(names) <= live                                            # the sources are all on build/v1 now: 231 ran for real on first apply
    for n in names:
        v = D.DEFERRED_VIEWS[n]
        src = {r[0] for r in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s", (n,))}
        mine = [r[0] for r in q(bdb, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = %s ORDER BY ordinal_position", (n,))]
        assert set(mine) == set(v["cols"]) and set(mine) | set(v["left_out"]) == src, (n, src ^ (set(mine) | set(v["left_out"])))     # nothing new in the source goes unreviewed


def test_231_views_carry_no_personal_columns_the_source_comments_and_are_safe_to_rerun(bdb):
    names = sorted(n.split(".", 1)[1] for n in D.DEFERRED)
    cols = {}
    for t, c in q(bdb, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = ANY(%s)", (names,)):
        cols.setdefault(t, set()).add(c)
    assert not ({"customer_id", "tags"} & cols["shopify_orders"]) and {"order_id", "order_date", "total_price", "discount_codes", "is_new_customer", "ship_country", "ship_region"} <= cols["shopify_orders"]
    assert {"sku", "quantity", "price"} <= cols["shopify_order_lines"]
    assert not any("raw" in c.split("_") or c in ("raw_url", "blocks", "images") for t in cols for c in cols[t])
    pii = {"email", "name", "phone", "address", "ip", "first_name", "last_name", "customer_email"}
    assert not (pii & cols["shopify_orders"]) and not (pii & cols["shopify_order_lines"])
    for n in names:
        v = D.DEFERRED_VIEWS[n]
        assert one(bdb, "SELECT obj_description(%s::regclass, 'pg_class')", (f"analytics.{n}",))[0] == v["comment"], n
        for c, text in v["cols"].items():
            got = one(bdb, "SELECT col_description(%s::regclass, a.attnum) FROM pg_attribute a WHERE a.attrelid = %s::regclass AND a.attname = %s", (f"analytics.{n}", f"analytics.{n}", c))[0]
            assert got == text, (n, c)
    cat = dict(q(bdb, "SELECT view_name, domain FROM analytics.catalog WHERE domain IN ('email', 'landing_pages') OR view_name LIKE 'shopify%'"))
    assert cat == {n: D.DEFERRED_VIEWS[n]["domain"] for n in names}
    c = psycopg.connect(DB_URL, autocommit=True)
    try:
        c.execute(M231.read_text())                                      # a second run changes nothing
        c.execute(M231.read_text())
        assert one(c, "SELECT count(*) FROM analytics.catalog")[0] == one(c, "SELECT count(*) FROM information_schema.views WHERE table_schema = 'analytics'")[0]
    finally:
        c.close()


def test_231_views_are_documented(bdb):
    probs = [p for p in gen.problems(gen.introspect(bdb), D) if p.startswith("analytics.")]
    assert not probs, probs


def test_catalog_picks_up_later_views_by_itself(bdb):
    c = psycopg.connect(DB_URL, autocommit=True)
    try:
        c.execute("CREATE VIEW analytics.zz_later_view AS SELECT 1 AS x")
        c.execute("COMMENT ON VIEW analytics.zz_later_view IS 'A later view. Grain: one row. Source: nothing.'")
        got = dict(q(c, "SELECT view_name, domain FROM analytics.catalog WHERE view_name = 'zz_later_view'"))
        assert got == {"zz_later_view": "unassigned"}
        assert one(c, "SELECT description, first_day FROM analytics.catalog WHERE view_name = 'zz_later_view'") == ("A later view.", None)
        assert one(c, "SELECT domain FROM analytics.catalog WHERE view_name = 'orders_daily'")[0] == "orders"
    finally:
        c.execute("DROP VIEW IF EXISTS analytics.zz_later_view")
        c.close()


def test_231_skips_a_source_whose_shape_changed():
    """On a throwaway database: rename a column of a source table and re-run 231: that view is skipped (not an error) and the others stay."""
    name = DB_NAME + "_shape"
    try:
        S.recreate_db(f"{PG_BASE}/postgres", name)
        with psycopg.connect(f"{PG_BASE}/{name}", autocommit=True) as c:
            S.apply_migrations(c)
            c.execute("DROP VIEW analytics.klaviyo_flows")
            c.execute("ALTER TABLE klaviyo_flows RENAME COLUMN trigger_type TO trigger_kind")
            c.execute(M231.read_text())
            assert one(c, "SELECT to_regclass('analytics.klaviyo_flows') IS NULL")[0]
            assert one(c, "SELECT to_regclass('analytics.klaviyo_daily') IS NOT NULL")[0]
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")


# ---------------------------------------------------------------------------------------------------------------- migration 254: analytics.ad_transcripts
def test_ad_transcripts_view_is_in_the_catalog_readable_and_joined_to_the_tracker(bdb):
    live = q(bdb, "SELECT t.concept_id, t.ad_family, t.family_key, t.status, t.ad_type, t.vendor, p.slug FROM public.tracker_rows t JOIN public.products p ON p.id = t.product_id "
                  "WHERE t.deleted_at IS NULL ORDER BY t.id LIMIT 1")[0]
    gone = one(bdb, "SELECT concept_id FROM public.tracker_rows WHERE deleted_at IS NOT NULL LIMIT 1")[0]
    with psycopg.connect(DB_URL, autocommit=True) as w:
        w.execute("DELETE FROM creative_ad_transcripts")
        w.execute("INSERT INTO creative_ad_transcripts (concept_id, label, transcript, language, duration_s, engine, model) VALUES (%s, 'Original', %s, 'en', 31.5, 'faster-whisper', 'base.en')",
                  (live[0], "  If your joints ache every morning, try this.  "))
        w.execute("INSERT INTO creative_ad_transcripts (concept_id, label, transcript) VALUES (%s, 'Hook 2', '[no speech]')", (gone,))
        w.execute("INSERT INTO creative_ad_transcripts (concept_id, label, transcript) VALUES ('not-on-the-tracker', 'Original', 'one two three')")
    rows = {r[0]: r for r in q(bdb, "SELECT concept_id, ad_family, family_key, product_slug, vendor, tracker_status, ad_type, word_count, has_speech, language, duration_s FROM analytics.ad_transcripts")}
    assert len(rows) == 3 == one(bdb, "SELECT count(*) FROM analytics.ad_transcripts")[0]                  # one row per ad, even when a concept_id repeats on the tracker
    assert rows[live[0]] == (live[0], live[1], live[2], live[6], live[5], live[3], live[4], 8, True, "en", 31.5)      # joined to the live tracker row; words counted after trimming
    assert rows[gone][3:7] == (None, None, None, None) and rows[gone][7:9] == (0, False)               # soft-deleted tracker row: the transcript stays, tracker columns are NULL, [no speech] = 0 words
    assert rows["not-on-the-tracker"][7:9] == (3, True) and rows["not-on-the-tracker"][3] is None
    cat = one(bdb, "SELECT domain, grain, row_estimate FROM analytics.catalog WHERE view_name = 'ad_transcripts'")
    assert cat[0] == "creative" and cat[1] == "one row per ad (concept_id)"
    assert "ad_transcripts" in {r[0] for r in q(bdb, "SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")}
    assert D.REL["analytics.ad_transcripts"]["domain"] == "creative"
    with psycopg.connect(DB_URL, autocommit=True) as w:                                                    # a repeated concept_id on the tracker must not repeat the transcript
        w.execute("DELETE FROM creative_ad_transcripts WHERE concept_id IN (%s, 'not-on-the-tracker')", (gone,))
        w.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, raw) "
                  "SELECT product_id, row_key || '#dup', concept_id, ad_family, family_key, sheet_row + 5000, status, ad_type, '{}'::jsonb FROM tracker_rows WHERE concept_id = %s AND deleted_at IS NULL LIMIT 1", (live[0],))
    assert one(bdb, "SELECT count(*) FROM analytics.ad_transcripts WHERE concept_id = %s", (live[0],))[0] == 1
