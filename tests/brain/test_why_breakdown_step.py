"""The breakdown step inside `why`: SQL against seeded analytics tables (breakdown rows with funnel columns, change ledger and activity log), the ranked movers, the coincident-change join, the
compact answer and the guard rails (no step for Google or repeat metrics, nothing beats noise on a quiet account)."""
import datetime as dt
import math

import psycopg
import pytest

from brain import why as W
from brain import why_breakdown_step as BS
from brain.knowledge import load
from brain_helpers import DB_NAME, PG_BASE, ROOT
from why_seed import END, STEP, Scenario, make_runner, seed

DB = DB_NAME + "_whybd"
URL = f"{PG_BASE}/{DB}"
KN = load(ROOT)
D = dt.date


@pytest.fixture(scope="module")
def db():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    return URL


def build(url, step: bool, ledger: bool = True):
    seed(url, Scenario(with_breakdown=True))
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from analytics.meta_breakdown_daily")
        rows = []
        d = D(2026, 9, 20)
        while d <= END:
            i = (d - D(2026, 9, 20)).days
            after = step and d >= STEP
            for dim, segs in (("placement", (("feed", 0.6, 0.020), ("reels", 0.4, 0.010))), ("publisher_platform", (("facebook", 0.7, 0.015), ("instagram", 0.3, 0.015)))):
                for seg, w, r in segs:
                    if after and dim == "placement":
                        w, r = (0.3, 0.020) if seg == "feed" else (0.7, 0.004)           # share moves to reels and reels' own click rate collapses
                    imps = 100_000 * w
                    n = 1 + 0.02 * math.sin(i * 2.3 + len(seg))
                    rows.append((d, "Lonvera Main", "camp1", "as_" + seg, "ad_" + seg, dim, seg, imps * 0.05, imps, imps * 0.8, imps * r * n, imps * r * 0.8 * n, imps * r * 0.02 * n, imps * r * 1.2 * n,
                                 imps * r * 0.1 * n, imps * r * 0.05 * n))
            d += dt.timedelta(days=1)
        c.cursor().executemany("insert into analytics.meta_breakdown_daily values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        if ledger:
            c.execute("create table analytics.meta_change_ledger (day date, prev_day date, adset_id text, adset_name text, campaign_id text, account text, change_type text, old_value text, new_value text, old_number numeric, new_number numeric)")
            c.execute("create table analytics.meta_change_log (event_time timestamptz, event_day date, account text, event_type text, translated_event_type text, object_level text, object_type text, object_id text, "
                      "object_name text, campaign_id text, is_system_event boolean, application_name text, old_value text, new_value text, old_amount numeric, new_amount numeric, old_roas_floor numeric, new_roas_floor numeric)")
            c.execute("insert into analytics.meta_change_ledger values (%s, %s, 'as_reels', 'Reels ad set', 'camp1', 'Lonvera Main', 'bid_amount', '60', '45', 60, 45)", (STEP, STEP - dt.timedelta(days=1)))
            c.execute("insert into analytics.meta_change_log values (now(), %s, 'Lonvera Main', 'update_ad_set_budget', 'x', 'adset', 'CAMPAIGN', 'as_reels', 'Reels ad set', 'camp1', false, 'app', '100', '300', 100, 300, null, null)", (STEP,))
            c.execute("insert into analytics.meta_change_log values (now(), %s, 'Lonvera Main', 'first_delivery_event', 'x', 'ad', 'ADGROUP', 'ad_reels', 'a', 'camp1', true, null, null, null, null, null, null, null)", (STEP,))


def go(url, **args):
    log = []
    a = {"start": str(STEP), "end": str(STEP), "compare": "custom", "compare_start": str(STEP - dt.timedelta(days=1)), "compare_end": str(STEP - dt.timedelta(days=1)), **args}
    return W.run_why(a, make_runner(url, log), KN), log


def test_a_real_step_in_one_dimension_is_found_ranked_and_joined_to_the_ledger(db):
    build(db, step=True)
    out, log = go(db, metric="ctr")
    bd = out["breakdown_changes"]
    assert bd["applies"] and "Meta-reported" in bd["basis"] and bd["beyond_noise_count"] >= 1
    assert bd["movers"][0]["verdict"] == "moved beyond noise" and bd["movers"][0]["dimension"] == "placement"
    top = next(m for m in bd["movers"] if m["metric"] != "spend_share")
    assert abs(top["mix_effect"] + top["within_segment_effect"] - top["contribution"]) < 1e-12
    ctr = bd["aggregate"]["ctr"]
    assert ctr["aggregate"]["verdict"] in ("moved beyond noise", "inside noise") and "->" in ctr["display"]     # (the synthetic dimensions do not share one total, so only the form is checked here)
    assert all(m["dimension"] != "publisher_platform" or m["verdict"] != "moved beyond noise" for m in bd["movers"])          # that dimension did not move
    cc = bd["coincident_changes"]["movers"]["placement:reels"]
    kinds = {c["type"] for c in cc["changes"]}
    assert {"bid_amount", "update_ad_set_budget"} <= kinds and "first_delivery_event" not in kinds
    assert "coincident with" in cc["reading"] and "caused" not in cc["reading"].replace("not shown to cause", "")
    assert bd["coincident_changes"]["account_wide"]["ledger:bid_amount"]["records"] == 1
    assert any("meta_breakdown_daily" in q for q in log)


def test_a_quiet_account_says_nothing_beat_noise(db):
    build(db, step=False)
    out, _ = go(db, metric="ctr")
    bd = out["breakdown_changes"]
    assert bd["beyond_noise_count"] == 0 and "No single breakdown segment moved beyond" in bd["headline"]


def test_funnel_metrics_wait_for_the_fill_and_say_so(db):
    build(db, step=True)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("update analytics.meta_breakdown_daily set add_to_carts = null, initiated_checkouts = null where day = %s", (STEP,))
    out, _ = go(db, metric="ctr")
    bd = out["breakdown_changes"]
    assert "link_to_atc" not in bd["aggregate"] and "cpm" in bd["aggregate"]
    assert any("not filled" in g for g in bd["gaps"])


def test_no_breakdown_step_for_google_or_repeat_metrics_or_a_tiny_budget(db):
    build(db, step=True)
    for metric in ("repeat_revenue", "klaviyo_flow_revenue"):
        out, _ = go(db, metric=metric)
        assert out["breakdown_changes"]["applies"] is False
    out, _ = go(db, metric="ctr", max_steps=12)
    assert "skipped" in out["breakdown_changes"] and out["steps_used"] <= out["max_steps"]


def test_without_the_ledger_views_the_step_still_answers_and_says_the_join_is_missing(db):
    build(db, step=True, ledger=False)
    out, _ = go(db, metric="ctr")
    bd = out["breakdown_changes"]
    assert bd["beyond_noise_count"] >= 1 and "coincident_changes" not in bd and any("ledger" in n for n in bd["notes"])


def test_compact_answer_carries_the_breakdown_section_and_the_instruction_to_report_it(db):
    build(db, step=True)
    out, _ = go(db, metric="ctr")
    c = W.compact(out)
    assert c["breakdown_changes"]["headline"] and c["breakdown_changes"]["movers"][0]["verdict"] == "moved beyond noise"
    assert "breakdown_changes" in c["how_to_answer"]
    assert c["breakdown_changes"]["coincident_changes"]["movers"]["placement:reels"]["changes"]


def test_a_bulk_bid_edit_on_many_ad_sets_is_listed_with_its_amounts(db):
    build(db, step=True)
    with psycopg.connect(db, autocommit=True) as c:
        for i in range(20):
            c.execute("insert into analytics.meta_change_log values (%s, %s, 'Lonvera Main', 'update_ad_set_bid_strategy', 'x', 'adset', 'CAMPAIGN', %s, 'n', 'camp1', false, 'app', 'COST_CAP', 'COST_CAP', 67.5, 80, null, null)",
                      (dt.datetime(2026, 10, 18, 19, 7, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=i), STEP, f"bulk{i}"))
    out, _ = go(db, metric="ctr")
    cc = out["breakdown_changes"]["coincident_changes"]
    b = [x for x in cc["bulk_edits"] if x["type"] == "update_ad_set_bid_strategy"]
    assert b and b[0]["objects"] == 20 and b[0]["average_old"] == "67.50" and b[0]["average_new"] == "80.00" and b[0]["first_event_pacific"] == "2026-10-18 12:07"
    assert "bulk edit" in cc["bulk_edits_reading"] and out["breakdown_changes"]["coincident_changes"]["account_wide"]["log:update_ad_set_bid_strategy"]["records"] == 20
    assert "bulk_edits" in W.compact(out)["breakdown_changes"]["coincident_changes"]
