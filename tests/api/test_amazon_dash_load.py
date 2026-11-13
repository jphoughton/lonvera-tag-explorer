"""etl/amazon_dash_load.py against small files shaped exactly like the real Data Dash pulls (FORMAT.md in ~/lonvera-tag-explorer-data/amazon_dash):
reports are JSON arrays with integer ids and camelCase fields; entity lists are objects with one list key (page_N.json) and string ids, in a folder named snapshot_YYYY-MM-DD."""
import datetime as dt
import json
from pathlib import Path

import psycopg
import pytest

from etl import amazon_dash_load as L

from .conftest import DB_URL

D = dt.date
KW_TABLES = list(L.TABLES)
ST = [  # date, campaign, ad group, keyword id (ints, as the reports send them), keyword, match, search term, imp, clicks, cost, purchases, sales
    ("2026-11-09", 101, 201, 301, "beef liver capsules", "EXACT", "Beef  Liver Capsules", 100, 10, 12.5, 2, 60.0),
    ("2026-11-09", 101, 201, 301, "beef liver capsules", "EXACT", "liver pills", 40, 4, 4.0, 1, 20),
    ("2026-11-10", 101, 201, 301, "beef liver capsules", "EXACT", "Beef  Liver Capsules", 120, 12, 15.0, 3, 90.0),
    ("2026-11-10", 102, 202, 302, "loose-match", "TARGETING_EXPRESSION_PREDEFINED", "b0gl4vvrvr", 30, 3, 3.0, 0, 0.0),
]


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _make(root: Path, dup=False):
    rows = [{"date": d, "campaignId": c, "campaignName": f"Camp {c}", "adGroupId": g, "adGroupName": f"AG {g}", "keywordId": k, "keyword": kw, "matchType": m, "searchTerm": t,
             "impressions": i, "clicks": cl, "cost": co, "purchases7d": p, "sales7d": s} for d, c, g, k, kw, m, t, i, cl, co, p, s in ST]
    if dup:
        rows.append(dict(rows[0], impressions=999))                                  # the same natural key twice: the later row wins
    _write(root / "sp_search_term" / "2026-11-09_2026-11-10" / "sp_search_term.json", rows)
    tg = [{"date": d, "campaignId": c, "campaignName": f"Camp {c}", "adGroupId": g, "adGroupName": f"AG {g}", "keywordId": k, "keyword": kw, "matchType": m, "targeting": kw,
           "impressions": i * 10, "clicks": cl, "cost": co, "purchases7d": p, "sales7d": s} for d, c, g, k, kw, m, t, i, cl, co, p, s in ST[:1] + ST[2:]]
    tg.append({"date": "2026-11-10", "campaignId": 101, "campaignName": "Camp 101", "adGroupId": 201, "adGroupName": "AG 201", "keywordId": 303, "keyword": "idle", "matchType": "BROAD",      # no 'targeting' key
               "impressions": 7, "clicks": 0, "cost": 0, "purchases7d": 0, "sales7d": 0})
    _write(root / "sp_targeting" / "2026-11-09_2026-11-10" / "sp_targeting.json", tg)
    _write(root / "sp_keyword_entities" / "snapshot_2026-11-12" / "page_0.json", {"keywords": [
        {"adGroupId": "201", "bid": 1.5, "campaignId": "101", "keywordId": "301", "keywordText": "beef liver capsules", "matchType": "EXACT", "state": "ENABLED"}], "nextToken": "x", "totalResults": 2})
    _write(root / "sp_keyword_entities" / "snapshot_2026-11-12" / "page_1.json", {"keywords": [
        {"adGroupId": "201", "bid": 0.4, "campaignId": "101", "keywordId": "303", "keywordText": "idle", "matchType": "BROAD", "state": "PAUSED"}], "totalResults": 2})
    _write(root / "sp_target_entities" / "snapshot_2026-11-12" / "page_0.json", {"targetingClauses": [
        {"adGroupId": "202", "campaignId": "102", "expression": [{"type": "QUERY_HIGH_REL_MATCHES"}], "expressionType": "AUTO", "resolvedExpression": [{"type": "QUERY_HIGH_REL_MATCHES"}], "state": "ENABLED", "targetId": "302"},
        {"adGroupId": "202", "bid": 2.0, "campaignId": "102", "expression": [{"type": "ASIN_SAME_AS", "value": "b07tyrty26"}], "expressionType": "MANUAL", "resolvedExpression": [{"type": "ASIN_SAME_AS", "value": "b07tyrty26"}],
         "state": "PAUSED", "targetId": "304"}], "totalResults": 2})
    _write(root / "sp_negative_keyword_entities" / "snapshot_2026-11-13" / "page_0.json", {"negativeKeywords": [
        {"adGroupId": "201", "campaignId": "101", "keywordId": "401", "keywordText": "free", "matchType": "NEGATIVE_PHRASE", "state": "ENABLED"}], "nextToken": "y", "totalResults": 2})
    _write(root / "sp_negative_keyword_entities" / "snapshot_2026-11-13" / "page_1.json", {"negativeKeywords": [
        {"adGroupId": "202", "campaignId": "102", "keywordId": "402", "keywordText": "gnc", "matchType": "NEGATIVE_EXACT", "state": "ARCHIVED"}], "totalResults": 2})
    _write(root / "sp_product_ad_entities" / "snapshot_2026-11-13" / "page_0.json", {"productAds": [
        {"adGroupId": "201", "adId": "501", "asin": "A1", "campaignId": "101", "sku": "LV-BLC180", "state": "ENABLED"},
        {"adGroupId": "202", "adId": "502", "asin": "A2", "campaignId": "102", "sku": "LV-VBULL60", "state": "ENABLED"},
        {"adGroupId": "202", "adId": "503", "asin": "A3", "campaignId": "102", "sku": "LV-BFLEX", "state": "PAUSED"}]})
    _write(root / "sp_campaign_entities" / "snapshot_2026-11-13" / "page_0.json", {"campaigns": [
        {"budget": {"budget": 40.0, "budgetType": "DAILY"}, "campaignId": "101", "dynamicBidding": {"placementBidding": [{"percentage": 100, "placement": "PLACEMENT_TOP"}, {"percentage": 25, "placement": "PLACEMENT_REST_OF_SEARCH"}],
         "strategy": "LEGACY_FOR_SALES"}, "name": "Camp 101", "portfolioId": "9", "startDate": "2026-01-14", "state": "ENABLED", "targetingType": "MANUAL"},
        {"budget": {"budget": 10.0, "budgetType": "DAILY"}, "campaignId": "102", "name": "Camp 102", "startDate": "2026-02-01", "state": "PAUSED", "targetingType": "AUTO"}]})


@pytest.fixture()
def conn(fx):
    with psycopg.connect(DB_URL) as c:
        for t in KW_TABLES + ["amazon_ads_campaign_daily", "amazon_asin_products"]:
            c.execute(f"TRUNCATE {t}")
        c.commit()
        yield c
        for t in KW_TABLES + ["amazon_ads_campaign_daily", "amazon_asin_products"]:
            c.execute(f"TRUNCATE {t}")
        c.commit()


def counts(c):
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in KW_TABLES}


def test_loads_every_table_with_text_ids_and_prints_the_counts(conn, tmp_path):
    _make(tmp_path)
    out = []
    res = L.run(tmp_path, conn, out=out.append)
    assert counts(conn) == {"amazon_ads_search_term_daily": 4, "amazon_ads_targeting_daily": 4, "amazon_ads_keywords_snapshot": 2, "amazon_ads_targets_snapshot": 2,
                            "amazon_ads_negative_keywords_snapshot": 2, "amazon_ads_campaigns_snapshot": 2, "amazon_ads_product_ads": 3}      # pages are concatenated, the report ints become text
    text = "\n".join(out)
    assert "amazon_ads_search_term_daily" in text and "2026-11-09 .. 2026-11-10" in text and "reconciliation search-term cost" in text
    assert res["amazon_ads_search_term_daily"]["first"] == D(2026, 11, 9) and res["amazon_ads_keywords_snapshot"]["first"] == D(2026, 11, 12)
    r = conn.execute("SELECT campaign_id, ad_group_id, keyword_id, search_term, search_term_key, cost, sales_7d FROM amazon_ads_search_term_daily WHERE day = '2026-11-09' ORDER BY search_term").fetchall()
    assert r[0] == ("101", "201", "301", "Beef  Liver Capsules", "beef liver capsules", 12.5, 60.0) and r[1][3] == "liver pills"                 # ints in the report, text in the table; key normalised
    assert conn.execute("SELECT keyword FROM amazon_ads_targeting_daily WHERE keyword_id = '303'").fetchone()[0] == "idle"
    assert conn.execute("SELECT targeting FROM amazon_ads_targeting_daily WHERE keyword_id = '303'").fetchone()[0] == "idle"                      # no 'targeting' field: the keyword text
    k = conn.execute("SELECT snapshot_date, bid, state FROM amazon_ads_keywords_snapshot WHERE keyword_id = '303'").fetchone()
    assert (k[0], float(k[1]), k[2]) == (D(2026, 11, 12), 0.4, "PAUSED")
    t = {r[0]: r[1:] for r in conn.execute("SELECT target_id, bid, expression_type, expression_text FROM amazon_ads_targets_snapshot")}
    assert t == {"302": (None, "AUTO", "QUERY_HIGH_REL_MATCHES"), "304": (2.0, "MANUAL", "ASIN_SAME_AS b07tyrty26")}
    c = conn.execute("SELECT placement_top_pct, placement_product_page_pct, placement_rest_of_search_pct, bidding_strategy, start_date, budget FROM amazon_ads_campaigns_snapshot WHERE campaign_id = '101'").fetchone()
    assert (c[0], c[1], c[2], c[3], c[4], c[5]) == (100, None, 25, "LEGACY_FOR_SALES", D(2026, 1, 14), 40)


def test_loading_twice_changes_nothing_and_a_duplicate_key_collapses(conn, tmp_path):
    _make(tmp_path, dup=True)
    L.run(tmp_path, conn, out=lambda *_: None)
    first = counts(conn)
    imp = conn.execute("SELECT impressions FROM amazon_ads_search_term_daily WHERE day = '2026-11-09' AND search_term = 'Beef  Liver Capsules'").fetchone()[0]
    assert imp == 999 and first["amazon_ads_search_term_daily"] == 4                      # five rows read, four keys: the later duplicate won
    before = conn.execute("SELECT min(loaded_at) FROM amazon_ads_targeting_daily").fetchone()[0]
    L.run(tmp_path, conn, out=lambda *_: None)
    assert counts(conn) == first
    assert conn.execute("SELECT min(loaded_at) FROM amazon_ads_targeting_daily").fetchone()[0] >= before


def test_a_restated_row_replaces_the_stored_one(conn, tmp_path):
    _make(tmp_path)
    L.run(tmp_path, conn, out=lambda *_: None)
    p = tmp_path / "sp_search_term" / "2026-11-09_2026-11-10" / "sp_search_term.json"
    data = json.loads(p.read_text())
    data[0]["cost"] = 99.0
    p.write_text(json.dumps(data))
    L.run(tmp_path, conn, out=lambda *_: None)
    assert counts(conn)["amazon_ads_search_term_daily"] == 4
    assert float(conn.execute("SELECT cost FROM amazon_ads_search_term_daily WHERE day = '2026-11-09' AND search_term = 'Beef  Liver Capsules'").fetchone()[0]) == 99.0


def test_dry_run_writes_nothing(conn, tmp_path):
    _make(tmp_path)
    out = []
    res = L.run(tmp_path, conn, dry_run=True, out=out.append)
    assert sum(counts(conn).values()) == 0 and out[0].startswith("DRY RUN") and res["amazon_ads_negative_keywords_snapshot"]["rows"] == 2


def test_a_second_snapshot_adds_history_and_product_ads_keep_the_latest(conn, tmp_path):
    _make(tmp_path)
    _write(tmp_path / "sp_keyword_entities" / "snapshot_2026-11-20" / "page_0.json", {"keywords": [
        {"adGroupId": "201", "bid": 2.25, "campaignId": "101", "keywordId": "301", "keywordText": "beef liver capsules", "matchType": "EXACT", "state": "ENABLED"}]})
    _write(tmp_path / "sp_product_ad_entities" / "snapshot_2026-11-20" / "page_0.json", {"productAds": [
        {"adGroupId": "201", "adId": "501", "asin": "A9", "campaignId": "101", "sku": "LV-BLC180", "state": "PAUSED"}]})
    L.run(tmp_path, conn, out=lambda *_: None)
    bids = conn.execute("SELECT snapshot_date, bid FROM amazon_ads_keywords_snapshot WHERE keyword_id = '301' ORDER BY 1").fetchall()
    assert [(d, float(b)) for d, b in bids] == [(D(2026, 11, 12), 1.5), (D(2026, 11, 20), 2.25)]                         # bid history accumulates
    assert conn.execute("SELECT asin, state, snapshot_date FROM amazon_ads_product_ads WHERE ad_id = '501'").fetchone() == ("A9", "PAUSED", D(2026, 11, 20))
    assert counts(conn)["amazon_ads_product_ads"] == 3


def test_missing_datasets_are_just_empty(conn, tmp_path):
    res = L.run(tmp_path, conn, out=lambda *_: None)
    assert all(v["rows"] == 0 and v["first"] is None for k, v in res.items() if k != "reconciliation") and sum(counts(conn).values()) == 0


def test_reconciliation_against_the_campaign_report_on_the_days_both_hold(conn, tmp_path):
    _make(tmp_path)
    for day, spend, ver in ((D(2026, 11, 9), 16.5, "v3"), (D(2026, 11, 10), 18.0, "v3"), (D(2026, 11, 11), 500.0, "v3")):       # Nov 11 is not in the reports: not compared
        conn.execute("INSERT INTO amazon_ads_campaign_daily (day, marketplace, campaign_id, ad_group_id, ad_type, api_version, spend) VALUES (%s, 'US', 'c', '', 'sp', %s, %s)", (day, ver, spend))
    conn.execute("INSERT INTO amazon_ads_campaign_daily (day, marketplace, campaign_id, ad_group_id, ad_type, api_version, spend) VALUES ('2026-11-09', 'US', 'sb', '', 'sb', 'v3', 77)")   # not Sponsored Products
    conn.commit()
    out = []
    res = L.run(tmp_path, conn, out=out.append)
    rec = res["reconciliation"][("amazon_ads_search_term_daily", "all")]
    # search-term cost: Nov 9 = 12.5 + 4.0, Nov 10 = 15.0 + 3.0; campaign spend 16.5 and 18.0 on the same two days
    assert rec["days"] == 2 and rec["report_cost"] == 34.5 and rec["campaign_spend"] == 34.5 and rec["diff"] == 0.0 and rec["diff_pct"] == 0.0
    assert (rec["first"], rec["last"]) == (D(2026, 11, 9), D(2026, 11, 10))
    tg = res["reconciliation"][("amazon_ads_targeting_daily", "all")]          # targeting holds 12.5 + 15.0 + 3.0 + 0
    assert tg["report_cost"] == 30.5 and tg["campaign_spend"] == 34.5 and tg["diff"] == -4.0 and tg["diff_pct"] == pytest.approx(-11.594, abs=1e-3)
    assert res["reconciliation"][("search_term", "targeting")]["days"] == 2
    assert any("difference -4.00" in line for line in out)


def test_reconcile_with_no_common_day():
    assert L.reconcile({D(2026, 1, 1): 5.0}, {D(2026, 1, 2): 5.0})["days"] == 0 and L.reconcile({}, {})["diff_pct"] is None


def test_normalisation_matches_the_search_query_key_rule():
    assert L.norm_query("  Beef  LIVER\tCapsules ") == "beef liver capsules" and L.norm_query(None) == ""


def test_main_reads_a_folder_and_refuses_a_missing_one(fx, tmp_path, capsys):
    with pytest.raises(SystemExit):
        L.main(["--dir", str(tmp_path / "nope")])
