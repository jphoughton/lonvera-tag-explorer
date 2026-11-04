"""Variables: every lever has the sheet's value as default and a source; editing one through the API is validated, audited and versioned; and each class of lever
moves the outputs it claims to (one test per class)."""
import datetime as dt

import pytest

from app.projection.pacing import feeds, levers as lv, month as M, periods, rollup, views
from app.projection.pacing.targets import TargetBook
from pacing_support import SNAPSHOT_DAY, SNAP
from etl import projection_pacing as ETL
from snapshot_pnl import ROW_MAP

BASE = "/api/projection/pacing"


@pytest.fixture(autouse=True)
def reset_scenario(pacing_db):
    import psycopg
    with psycopg.connect(pacing_db) as c:
        c.execute("UPDATE proj_scenarios SET params = '{}', version = 1 WHERE slug = 'current'")
        c.execute("DELETE FROM proj_audit")
        c.execute("DELETE FROM proj_scenario_history")
        c.execute("DELETE FROM proj_pacing_setting")
    yield


def book_with(pnl, cohorts, params):
    return TargetBook(pnl, None, cohorts, row_map=ROW_MAP, levers=lv.parse(params))


def month_rows(conn, pnl, cohorts, params, today=SNAPSHOT_DAY, month=dt.date(2026, 11, 1)):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), min(today, dt.date(2026, 11, 3)))
    return M.pacing_month(fr, month, today, book_with(pnl, cohorts, params), cohorts, params), fr


# ---------------------------------------------------------------------------------------------------------------- catalog of variables

def test_every_variable_has_the_sheets_value_a_source_and_an_owner():
    for s in lv.SPECS:
        assert s.source and s.label and s.path and s.owner in ("pacing", "drivers", "financing"), s.key
    d = {x["key"]: x for x in lv.describe({})}
    assert len(d) == len(lv.SPECS) == 17 and all(x["is_default"] for x in d.values())
    assert d["pace_good"]["default"] == 1.0 and d["pace_warn"]["default"] == 0.9 and d["last_days"]["default"] == 7 and d["data_lag_days"]["default"] == 1
    assert d["product_cost_pct"]["default"] == 0.1726 and d["fulfillment_per_order"]["default"] == 5.53 and d["payback_horizon"]["default"] == 36
    assert d["sales_tax_rate"]["default"] == 0.07 and d["below_ebitda_rows"]["default"] == [62, 94]
    assert d["product_cost_pct"]["owner"] == "financing" and d["sales_tax_rate"]["owner"] == "drivers"


def test_parse_ignores_malformed_stored_values():
    L = lv.parse({"pacing": {"last_days": "x", "rates_month": "nope", "cost_drivers": {"fulfillment": "weird", "inbound": "dtc_rev"}, "below_ebitda_rows": "no"}})
    assert L.last_days == 7 and L.rates_month is None and L.cost_drivers == {"inbound": "dtc_rev"} and L.below_ebitda_rows == (62, 94)


# ---------------------------------------------------------------------------------------------------------------- one test per class of lever

def test_status_thresholds_move_the_colors(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    by = {r["key"]: r for r in base["rows"]}
    assert by["amazon_rev"]["status"] == "bad" and by["advertising"]["status"] == "bad" and by["nc_roas"]["status"] == "warn"           # 88%, 111%, 90.3%
    p = {"pacing": {"pace_warn": 0.80, "cost_warn": 1.20, "pace_good": 0.88}}
    moved, _ = month_rows(conn, pnl, cohorts, p)
    by2 = {r["key"]: r for r in moved["rows"]}
    assert by2["amazon_rev"]["status"] == "good"                                                          # 88.3% now clears the on-pace bar of 88%
    assert by2["advertising"]["status"] == "warn"                                                         # 111% over plan is inside 120%
    assert by2["nc_roas"]["status"] == "good"
    assert by2["total_rev"]["pacing"] == by["total_rev"]["pacing"]                                        # thresholds never change a number


def test_early_month_notice_window(conn, pnl, cohorts):
    assert month_rows(conn, pnl, cohorts, {})[0]["early"] is True
    assert month_rows(conn, pnl, cohorts, {"pacing": {"early_days": 2}})[0]["early"] is False             # 2 days elapsed is not fewer than 2


def test_recent_average_window(conn, pnl, cohorts):
    base, fr = month_rows(conn, pnl, cohorts, {})
    three, _ = month_rows(conn, pnl, cohorts, {"pacing": {"last_days": 3}})
    last3 = sum(fr.days[dt.date(2026, 11, 2) - dt.timedelta(days=k)]["o_total_rev"] for k in range(3)) / 3
    assert {r["key"]: r for r in three["rows"]}["dtc_rev"]["last7"] == pytest.approx(last3)
    assert {r["key"]: r for r in base["rows"]}["dtc_rev"]["last7"] != pytest.approx(last3)


def test_days_elapsed_rule_follows_the_lag(conn, pnl, cohorts):
    zero, _ = month_rows(conn, pnl, cohorts, {"pacing": {"data_lag_days": 0}})
    assert zero["days_elapsed"] == 3 and zero["data_through"] == "2026-11-03" and zero["days_remaining"] == 27
    two, _ = month_rows(conn, pnl, cohorts, {"pacing": {"data_lag_days": 2}})
    assert two["days_elapsed"] == 1 and two["data_through"] == "2026-11-01"
    assert periods.default_month(dt.date(2026, 11, 2), lag=2) == dt.date(2026, 10, 1)                       # a two-day lag keeps showing October on Nov 2


def test_rates_month_reprices_the_daily_estimate(conn, pnl, cohorts):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 31), dt.date(2026, 11, 2))
    outs = {}
    for name, params in (("default", {}), ("june", {"pacing": {"rates_month": "2026-06"}})):
        L = lv.parse(params)
        b = book_with(pnl, cohorts, params)
        f2 = feeds.load_frame(conn, dt.date(2026, 10, 31), dt.date(2026, 11, 2))
        rollup.apply_costs(f2, b, lv.rates_month(L, SNAPSHOT_DAY))
        outs[name] = f2.days[dt.date(2026, 11, 2)]["e_cogs"]
    assert outs["default"] != pytest.approx(outs["june"])                                                  # June's actual rates differ from the November plan's


def test_below_ebitda_rows_set_the_ebitda_goal(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    moved, _ = month_rows(conn, pnl, cohorts, {"pacing": {"below_ebitda_rows": [66]}})
    b = {r["key"]: r for r in base["rows"]}
    m = {r["key"]: r for r in moved["rows"]}
    assert m["below_ebitda"]["goal"] == pytest.approx(1277.75, rel=1e-3) and b["below_ebitda"]["goal"] == 0
    assert m["ebitda"]["goal"] - b["ebitda"]["goal"] == pytest.approx(m["below_ebitda"]["goal"])
    assert m["noi"]["goal"] == b["noi"]["goal"]


def test_cost_line_driver_override_changes_the_cost_estimate(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    moved, _ = month_rows(conn, pnl, cohorts, {"pacing": {"cost_drivers": {"fulfillment": "total_rev"}}})
    b = {r["key"]: r for r in base["rows"]}
    m = {r["key"]: r for r in moved["rows"]}
    assert b["fulfillment"]["mtd"] == pytest.approx(8749.65, abs=1)
    total, dtc = b["total_rev"], b["dtc_rev"]
    assert m["fulfillment"]["mtd"] == pytest.approx(total["mtd"] * b["fulfillment"]["goal"] / total["goal"])      # now a share of total revenue
    assert m["fulfillment"]["goal"] == b["fulfillment"]["goal"]
    # and the daily estimate follows the same definition
    fr = feeds.load_frame(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    rollup.apply_costs(fr, book_with(pnl, cohorts, {"pacing": {"cost_drivers": {"fulfillment": "fixed"}}}), dt.date(2026, 11, 1))
    fr2 = feeds.load_frame(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    rollup.apply_costs(fr2, book_with(pnl, cohorts, {}), dt.date(2026, 11, 1))
    assert fr.days[dt.date(2026, 11, 2)]["e_fixed_opex"] == fr2.days[dt.date(2026, 11, 2)]["e_fixed_opex"]       # fulfillment is COGS: fixed opex unchanged
    assert fr.days[dt.date(2026, 11, 2)]["e_cogs"] != pytest.approx(fr2.days[dt.date(2026, 11, 2)]["e_cogs"])


def test_sales_tax_estimate_comes_from_the_scenarios_rate(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    ten, _ = month_rows(conn, pnl, cohorts, {"drivers": {"sales_tax_rate": 0.10}})
    b = {r["key"]: r for r in base["rows"]}
    t = {r["key"]: r for r in ten["rows"]}
    assert t["sales_tax"]["mtd"] == pytest.approx(-0.10 * t["dtc_rev"]["mtd"]) and b["sales_tax"]["mtd"] == pytest.approx(-0.07 * b["dtc_rev"]["mtd"], rel=1e-3)
    assert t["total_rev"]["mtd"] < b["total_rev"]["mtd"]                                                  # more tax estimated: less net revenue so far
    fr = feeds.load_frame(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    rollup.apply_costs(fr, book_with(pnl, cohorts, {"drivers": {"sales_tax_rate": 0.10}}), dt.date(2026, 11, 1))
    assert fr.days[dt.date(2026, 11, 2)]["x_tax_est"] == pytest.approx(0.10 * fr.days[dt.date(2026, 11, 2)]["o_total_rev"])


def test_payback_box_inputs_move_the_payback(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    pb = base["payback"]
    assert pb["product_cost_pct"] == 0.1726 and pb["fulfillment_per_order"] == 5.53 and pb["horizon_months"] == 36 and pb["months"]["mtd"] == 10
    costly, _ = month_rows(conn, pnl, cohorts, {"financing": {"payback": {"product_cost_pct": 0.4}}})
    assert costly["payback"]["first_order_margin"]["mtd"] < pb["first_order_margin"]["mtd"] and (costly["payback"]["months"]["mtd"] is None or costly["payback"]["months"]["mtd"] > 10)
    short, _ = month_rows(conn, pnl, cohorts, {"financing": {"payback": {"max_age": 6}}})
    assert short["payback"]["months"]["mtd"] is None and short["payback"]["months"]["mtd_label"] == "6+"
    fulfil, _ = month_rows(conn, pnl, cohorts, {"financing": {"payback": {"fulfillment": 25}}})
    assert fulfil["payback"]["first_order_margin"]["mtd"] == pytest.approx(pb["first_order_margin"]["mtd"] - 25 + 5.53)


def test_trend_window_defaults_follow_the_levers():
    thr = dt.date(2026, 11, 2)
    a, b = views.clamp_range("day", None, None, thr, lv.parse({"pacing": {"rollup_days": 10}}))
    assert (b - a).days == 9
    a, b = views.clamp_range("week", None, None, thr, lv.parse({"pacing": {"rollup_weeks": 4}}))
    assert a == periods.week_start(thr - dt.timedelta(days=27))


def test_feed_classification_rules_change_the_loaded_days(meta_sandbox):
    c = meta_sandbox
    c.execute("DELETE FROM ad_daily")
    c.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES ('k1','Main','Beef Liver Testing','testing')")
    c.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_name) VALUES ('ka','Main','x','x','k1','Liver set')")
    c.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, source, impressions) "
              "VALUES ('ka','2026-11-02','k','K','k1',100,1,50,5,'meta_api',100)")
    ETL.refresh(c, days=3, snapshot_dir=SNAP)
    row = c.execute("SELECT product, stage FROM proj_feed_meta_day WHERE source='app' AND campaign_id='k1'").fetchone()
    assert (row["product"], row["stage"]) == ("Beef Liver", "Testing")
    c.execute("INSERT INTO proj_pacing_setting (key, value) VALUES ('stage_keyword', '\"tester\"'), "
              "('product_rules', '{\"Boneflex\": \"liver\"}')")
    c.commit()
    ETL.refresh(c, days=3, snapshot_dir=SNAP)
    row = c.execute("SELECT product, stage FROM proj_feed_meta_day WHERE source='app' AND campaign_id='k1'").fetchone()
    assert (row["product"], row["stage"]) == ("Boneflex", "Scaling")                                      # the stored rules apply to the next load
    c.execute("DELETE FROM proj_pacing_setting")
    c.commit()


# ---------------------------------------------------------------------------------------------------------------- API

def test_levers_endpoint_lists_values_defaults_sources(api):
    j = api.get(f"{BASE}/levers?scenario=current").get_json()
    assert j["scenario"] == "current" and len(j["levers"]) == 17
    pw = next(x for x in j["levers"] if x["key"] == "pace_warn")
    assert pw["value"] == 0.9 and pw["is_default"] and pw["source"] and pw["owner"] == "pacing" and pw["path"] == "pacing.pace_warn"


def test_put_levers_is_validated_versioned_audited_and_resettable(api, pacing_db):
    import psycopg
    from psycopg.rows import dict_row
    r = api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"pace_warn": 0.8, "last_days": 5}})
    assert r.status_code == 200
    j = r.get_json()
    assert j["scenario"]["version"] == 2 and {x["key"]: x["value"] for x in j["levers"]}["last_days"] == 5
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        audit = c.execute("SELECT action, new FROM proj_audit WHERE scenario_slug = 'current'").fetchall()
        assert audit and audit[0]["action"] == "scenario.params" and audit[0]["new"]["params"]["pacing"]["pace_warn"] == 0.8
        assert c.execute("SELECT count(*) AS n FROM proj_scenario_history WHERE scenario_id = (SELECT id FROM proj_scenarios WHERE slug='current')").fetchone()["n"] >= 1
    # the month endpoint uses the stored lever
    mon = api.get(f"{BASE}/month").get_json()
    assert mon["rows"] and next(r for r in mon["rows"] if r["key"] == "amazon_rev")["status"] == "warn"          # 88% is above the new 80% watch line
    # null restores the sheet's value and removes the stored key
    back = api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"pace_warn": None}}).get_json()
    assert {x["key"]: x["is_default"] for x in back["levers"]}["pace_warn"] is True
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        assert "pace_warn" not in (c.execute("SELECT params FROM proj_scenarios WHERE slug='current'").fetchone()["params"].get("pacing") or {})


def test_put_levers_writes_inputs_owned_by_other_modules_to_their_namespace(api, pacing_db):
    import psycopg
    from psycopg.rows import dict_row
    api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"product_cost_pct": 0.2, "sales_tax_rate": 0.08, "payback_horizon": 24}})
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        p = c.execute("SELECT params FROM proj_scenarios WHERE slug='current'").fetchone()["params"]
    assert p["financing"]["payback"] == {"product_cost_pct": 0.2, "max_age": 24} and p["drivers"]["sales_tax_rate"] == 0.08


@pytest.mark.parametrize("body", [
    {"levers": {"nope": 1}}, {"levers": {"pace_warn": 5}}, {"levers": {"last_days": 2.5}}, {"levers": {"last_days": "x"}}, {"levers": {}}, {"levers": {"rates_month": "soon"}},
    {"levers": {"below_ebitda_rows": [1, 2]}}, {"levers": {"cost_drivers": {"fulfillment": "weird"}}}, {"levers": {"cost_drivers": {"zzz": "fixed"}}},
    {"levers": {"pace_warn": 1.0, "pace_good": 0.9}}, {"levers": {"pace_good": True}}, {"levers": {"product_cost_pct": 1.5}}])
def test_put_levers_rejects_bad_values(api, body):
    r = api.put(f"{BASE}/levers", json={"scenario": "current", **body})
    assert r.status_code == 400 and r.get_json()["error"]


def test_put_levers_needs_a_scenario_and_checks_the_version(api):
    assert api.put(f"{BASE}/levers", json={"levers": {"pace_warn": 0.8}}).status_code == 400
    assert api.put(f"{BASE}/levers", json={"scenario": "ghost", "levers": {"pace_warn": 0.8}}).status_code == 404
    assert api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"pace_warn": 0.8}, "expected_version": 99}).status_code == 409
    assert api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"pace_warn": 0.8}, "expected_version": "1"}).status_code == 400


def test_levers_need_the_apps_login(api_app):
    anon = api_app.test_client()
    assert anon.get(f"{BASE}/levers").status_code == 401 and anon.put(f"{BASE}/levers", json={}).status_code == 401
    assert anon.get(f"{BASE}/rules").status_code == 401


def test_rules_endpoint_lists_defaults_and_edits_are_validated_and_audited(api, pacing_db):
    import psycopg
    from psycopg.rows import dict_row
    j = api.get(f"{BASE}/rules").get_json()["rules"]
    assert [r["key"] for r in j] == ["stage_keyword", "product_rules", "product_level_rules"] and all(r["is_default"] and r["source"] for r in j)
    ok = api.put(f"{BASE}/rules", json={"rules": {"stage_keyword": "Pilot"}})
    assert ok.status_code == 200 and {r["key"]: r["value"] for r in ok.get_json()["rules"]}["stage_keyword"] == "pilot"
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        assert c.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'pacing.rule'").fetchone()["n"] == 1
    assert api.put(f"{BASE}/rules", json={"rules": {"product_rules": {"VitaBull": "(unclosed"}}}).status_code == 400
    assert api.put(f"{BASE}/rules", json={"rules": {"nope": "x"}}).status_code == 400
    assert api.put(f"{BASE}/rules", json={"rules": {"stage_keyword": ""}}).status_code == 400
    back = api.put(f"{BASE}/rules", json={"rules": {"stage_keyword": None}}).get_json()["rules"]
    assert next(r for r in back if r["key"] == "stage_keyword")["is_default"] is True


def test_payback_and_feed_age_bands_follow_their_levers(conn, pnl, cohorts):
    base, _ = month_rows(conn, pnl, cohorts, {})
    assert base["payback"]["months"]["diff"] == 5 and base["payback"]["months"]["status"] == "bad"          # 10 months against a goal of 5
    loose, _ = month_rows(conn, pnl, cohorts, {"pacing": {"payback_bad_months": 8}})
    assert loose["payback"]["months"]["status"] == "warn"
    fresh = [{"feed": "x", "label": "X", "data_through": "2026-10-27", "stale_after_days": 2}]
    assert views.freshness_chips(fresh, SNAPSHOT_DAY)[0]["status"] == "bad"                                   # 6 days behind yesterday: over the 5-day bar
    assert views.freshness_chips(fresh, SNAPSHOT_DAY, 1, 9)[0]["status"] == "warn"
