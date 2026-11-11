"""Each tool against a fixture repo (schema.json, docs, questions) with no database; and again with today's schema.json only (no glossary, no questions)."""
import json

import pytest

from brain import tools as T


@pytest.fixture()
def brain(fake_root):
    return T.Brain(root=fake_root)


@pytest.fixture()
def bare(bare_root):
    return T.Brain(root=bare_root)


def test_the_registry_has_every_tool_with_a_short_description():
    names = [t.name for t in T.TOOLS]
    assert names == ["catalog", "describe", "search", "metric", "freshness", "sql", "past_questions", "app_map"]
    for t in T.TOOLS:
        assert 20 < len(t.description) <= 220 and t.schema["type"] == "object" and t.schema["additionalProperties"] is False


def test_catalog_groups_by_domain_and_lists_only_what_sql_can_read(brain):
    c = brain.catalog()
    assert c["source"] == "docs/schema.json" and c["glossary_loaded"] is True
    doms = {d["domain"]: [r["name"] for r in d["relations"]] for d in c["domains"]}
    assert doms == {"ads": ["analytics.meta_ad_daily"], "orders": ["analytics.orders_daily"]}
    r = c["domains"][0]["relations"][0]
    assert r["grain"] == "ad x day" and r["coverage"] == "2026-05-06 onward" and r["description"] == "One row per Meta ad per day with spend and Meta-reported purchases."
    assert "public.ad_daily" in json.dumps(brain.catalog("all"))


def test_catalog_works_without_domains_or_glossary(bare):
    c = bare.catalog()
    assert c["glossary_loaded"] is False and c["glossary"] == "glossary not loaded yet"
    assert [d["domain"] for d in c["domains"]] == ["analytics (not yet assigned a domain)"]


def test_describe(brain):
    d = brain.describe("meta_ad_daily")
    assert d["name"] == "analytics.meta_ad_daily" and d["queryable_by_agents"] is True
    assert [c["name"] for c in d["columns"]] == ["day", "spend", "meta_revenue"] and d["columns"][1]["type"] == "numeric"
    assert d["examples"][0]["sql"].startswith("select day") and d["caveats"] == ["Meta-reported counts returning customers."]
    assert brain.describe("analytics.orders_daily")["examples"][0]["sql"] == "select * from analytics.orders_daily limit 10"      # generated when none is documented
    assert brain.describe("public.ad_daily")["queryable_by_agents"] is False
    with pytest.raises(T.ToolError, match="Did you mean: meta_ad_daily"):
        brain.describe("meta_ad_dayly")
    with pytest.raises(T.ToolError, match="catalog"):
        brain.describe("zzz")


def test_search_finds_views_columns_docs_metrics_and_questions(brain):
    kinds = lambda q: {h["kind"] for h in brain.search(q)["hits"]}            # noqa: E731
    assert "column" in kinds("meta-reported purchase revenue gross")
    assert "view" in kinds("actual new customer revenue per day")
    assert "metric" in kinds("ratio of sums average of daily ratios")
    assert "question" in kinds("repeat revenue billing reminder")
    docs = [h for h in brain.search("hit rate threshold")["hits"] if h["kind"] == "doc"]
    assert docs and docs[0]["path"] == "docs/GUIDE.md" and docs[0]["ref"] == "docs/GUIDE.md#The five rules" and docs[0]["line"] == 5 and "five percent" in docs[0]["snippet"]
    assert brain.search("zebra")["hits"] == []                                 # DATA_SCHEMA.md is not indexed twice
    q = [h for h in brain.search("boneflex hit rate")["hits"] if h["kind"] == "question"][0]
    assert q["path"] == "docs/questions/lonvera_items.jsonl" and q["date"] == "2026-10-01" and q["status"] == "open"
    assert brain.search("")["hits"] == []


def test_metric_is_fuzzy_and_lists_every_roas_variant(brain):
    m = brain.metric("roas")
    assert m["loaded"] is True and [x["name"] for x in m["matches"]] == ["meta_roas", "nc_roas"] or {x["name"] for x in m["matches"]} == {"meta_roas", "nc_roas"}
    assert all("traps" in x for x in m["matches"])
    assert brain.metric("NC ROAS")["matches"][0]["name"] == "nc_roas"
    assert brain.metric("cost per acquisition")["matches"][0]["name"] == "cpa"
    assert brain.metric("cpaa")["matches"][0]["name"] == "cpa"                  # typo
    none = brain.metric("zzzz")
    assert none["matches"] == [] and "meta_roas" in none["available"]
    with pytest.raises(T.ToolError):
        brain.metric("  ")


def test_metric_without_a_glossary_says_so_and_falls_back(bare):
    m = bare.metric("roas")
    assert m["loaded"] is False and "glossary not loaded yet" in m["note"] and isinstance(m["related"], list)
    assert "glossary not loaded yet" in bare.search("roas")["note"]


def test_past_questions_returns_date_verbatim_answer_status(brain):
    r = brain.past_questions("repeat revenue fall")
    assert r["loaded"] is True
    m = r["matches"][0]
    assert (m["id"], m["date"], m["status"]) == (7, "2026-10-23", "answered") and m["verbatim"].startswith("Why did repeat") and "Billing reminder" in m["answer"]
    alias = brain.past_questions("boneflex hit rate")["matches"][0]               # other field names are understood
    assert alias["date"] == "2026-10-01" and alias["answer"] == "Five percent." and alias["status"] == "open"


def test_past_questions_when_the_folder_is_absent(bare):
    r = bare.past_questions("anything")
    assert r["loaded"] is False and r["matches"] == []


def test_app_map(brain, bare):
    assert brain.app_map()["services"] == [{"name": "web"}] and brain.app_map("routes") == {"routes": [{"rule": "/mcp"}]}
    with pytest.raises(T.ToolError, match="unknown section"):
        brain.app_map("nope")
    with pytest.raises(T.ToolError, match="gen_app_map"):
        bare.app_map()


def test_sql_freshness_and_live_catalog_need_a_database(brain):
    for fn in (lambda: brain.sql("select 1"), brain.freshness):
        with pytest.raises(T.ToolError, match="SQL is not configured"):
            fn()
    assert brain.catalog()["source"] == "docs/schema.json" and "warning" not in brain.catalog()      # offline is the normal fallback, not a warning


def test_call_validates_arguments(brain):
    with pytest.raises(T.ToolError, match="unknown tool"):
        T.call(brain, "drop_everything", {})
    with pytest.raises(T.ToolError, match="required"):
        T.call(brain, "describe", {})
    with pytest.raises(T.ToolError, match="unknown argument"):
        T.call(brain, "catalog", {"evil": 1})
    with pytest.raises(T.ToolError, match="must be text"):
        T.call(brain, "search", {"text": 5})
    with pytest.raises(T.ToolError, match="whole number"):
        T.call(brain, "search", {"text": "x", "limit": "5"})
    assert T.row_count(T.call(brain, "search", {"text": "roas"})) >= 1


def test_the_real_repo_loads_and_its_docs_are_searchable():
    b = T.Brain()
    assert b.catalog()["domains"] and b.describe("meta_ad_daily")["columns"]
    assert any(h["kind"] == "doc" for h in b.search("api key authorization bearer")["hits"])
    assert b.app_map("services")["services"]
