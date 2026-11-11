"""Every why-tree query, including the ones that need the optional views (Klaviyo, landing pages, Shopify orders, Meta breakdowns), runs against the REAL migrated analytics views
(migrations 230 / 231 / 240), so a column that a view does not have fails here instead of in production. The seeded-table tests in test_why.py cover the logic; this covers the names."""
import datetime as dt

from brain import why_stats as S
from brain import why_tree as WT
from why_seed import END, STEP, make_runner


def test_all_why_queries_run_on_the_real_views_with_every_optional_view_present(migrated_db):
    per = S.grain_periods(END, {"day"})
    kw = {"periods": ", ".join(f"('{l}', date '{s}', date '{e}')" for l, s, e, _ in per), "prod": "", "hstart": END - dt.timedelta(days=34), "end": END, "start": STEP, "cstart": STEP - dt.timedelta(days=14), "cend": STEP - dt.timedelta(days=1)}
    texts = [f["sql"] for f in WT.FAMILIES.values()] + [WT.group_sql(g) for g in WT.MIX_GROUPS]
    texts += [c["sql"] for c in WT.CHANGE_SQL.values()] + [WT.COVERAGE_SQL, WT.VIEWS_SQL, WT.LEAD_AD_SQL.replace("{keys}", "'x'"), WT.LP_FOR_ADS_SQL.replace("{keys}", "'x'")]
    texts += [WT.group_sql("family", keys=["x"]), WT.group_sql("pack", keys=["1 bottle (single)"]), WT.group_sql("country")]
    run = make_runner(migrated_db)
    named = 0
    for t in texts:
        sql = t.format_map(kw) if "{periods}" in t or "{hstart}" in t else t
        named += any(v in sql for v in ("analytics.klaviyo_", "analytics.landing_page", "analytics.shopify_order"))
        assert run(sql, 5)["columns"], sql[:200]
    assert named >= 5                                                    # the queries that name the Klaviyo / landing page / Shopify views were among them
