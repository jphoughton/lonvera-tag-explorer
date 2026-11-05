"""Test to scale (/api/promotions, view creative_stage_moves): creatives moved from Testing to Scaling and the numbers around the move.

Own throwaway database (not the shared API fixture): a small hand-built account is loaded through the real ETL builders (etl.build_metrics.rebuild /
rebuild_source), so family_daily, the stage rule and the first-row attribution are the production ones, and every expected number below is written out
from the daily rows defined here (see DAYS / the CREATIVES table).
"""
from __future__ import annotations

import datetime as dt
import json

import psycopg
import pytest

from etl import build_metrics

from . import seed_fixture as S
from .conftest import DB_NAME, PG_BASE, PASSWORD, make_app
from .helpers import get_ok

D = dt.date
D0, THROUGH = D(2026, 5, 6), D(2026, 11, 2)
PROMO_DB = f"{DB_NAME}_promo"
PROMO_URL = f"{PG_BASE}/{PROMO_DB}"

CAMPAIGNS = {  # id -> name (the stage comes from the name: 'test' = testing)
    "c-vb-test": "VB_Test_Hooks", "c-bl-test": "BL_Test_Hooks",
    "c-vb-s1": "VB_Scaling_One", "c-vb-s2": "VB_Scaling_Two", "c-bl-s1": "BL_Scaling_One",
}


def rng(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


# creative -> (product, [(ad_id, campaign, adset, first day, last day, spend, purchases, revenue per day)])
CREATIVES: dict[str, tuple[str, list[tuple]]] = {
    # moved: tests Oct 10-14 (5 x 100 / 1 / 90), a testing copy still spends on Oct 21, scales Oct 20 - Nov 2 in two campaigns (14 x 200 / 3 / 270)
    "vb-mv-a": ("vita-bull", [("a-t1", "c-vb-test", "AS-test", D(2026, 10, 10), D(2026, 10, 14), 100, 1, 90),
                              ("a-t1", "c-vb-test", "AS-test", D(2026, 10, 21), D(2026, 10, 21), 20, 0, 0),
                              ("a-s1", "c-vb-s1", "AS-s1", D(2026, 10, 20), THROUGH, 150, 2, 200),
                              ("a-s2", "c-vb-s2", "AS-s2", D(2026, 10, 20), THROUGH, 50, 1, 70)]),
    # moved after 2 test days, 3 scaling days, then stopped (too early to call)
    "vb-mv-b": ("vita-bull", [("b-t", "c-vb-test", "AS-test", D(2026, 10, 12), D(2026, 10, 13), 60, 0, 0),
                              ("b-s", "c-vb-s1", "AS-s1", D(2026, 10, 15), D(2026, 10, 17), 50, 1, 40)]),
    # moved before the 30-day window (Sep 28)
    "bl-mv-old": ("beef-liver", [("o-t", "c-bl-test", "AS-bl-test", D(2026, 9, 20), D(2026, 9, 25), 80, 1, 70),
                                 ("o-s", "c-bl-s1", "AS-bl-s1", D(2026, 9, 28), D(2026, 10, 3), 100, 1, 100)]),
    # never moved: still testing / stopped testing
    "vb-nv-live": ("vita-bull", [("l-t", "c-vb-test", "AS-test", D(2026, 10, 25), THROUGH, 50, 0, 0)]),
    "bl-nv-stop": ("beef-liver", [("p-t", "c-bl-test", "AS-bl-test", D(2026, 10, 8), D(2026, 10, 10), 40, 0, 0)]),
    # scaling only: never tested
    "vb-sc-only": ("vita-bull", [("s-s", "c-vb-s1", "AS-s1", D(2026, 10, 12), THROUGH, 30, 0, 0)]),
    # testing and scaling start on the same day: not a move
    "bl-same-day": ("beef-liver", [("d-t", "c-bl-test", "AS-bl-test", D(2026, 10, 18), D(2026, 10, 18), 10, 0, 0),
                                   ("d-s", "c-bl-s1", "AS-bl-s1", D(2026, 10, 18), D(2026, 10, 19), 10, 0, 0)]),
    # scaling spend came first, testing later: not a move
    "vb-sc-first": ("vita-bull", [("f-s", "c-vb-s1", "AS-s1", D(2026, 10, 9), D(2026, 10, 9), 10, 0, 0),
                                  ("f-t", "c-vb-test", "AS-test", D(2026, 10, 12), D(2026, 10, 12), 10, 0, 0)]),
    # one beef liver creative moves alone on Oct 23
    "bl-mv-c": ("beef-liver", [("c-t", "c-bl-test", "AS-bl-test", D(2026, 10, 14), D(2026, 10, 16), 50, 1, 40),
                               ("c-s", "c-bl-s1", "AS-bl-s1", D(2026, 10, 23), THROUGH, 40, 1, 45)]),
    # was already running on the first day of data: its launch day is unknown (moves Oct 28)
    "vb-pre-data": ("vita-bull", [("r-t", "c-vb-test", "AS-test", D0, D(2026, 5, 8), 25, 0, 0),
                                  ("r-s", "c-vb-s1", "AS-s1", D(2026, 10, 28), THROUGH, 60, 1, 70)]),
    # tiny test (total $20), moves Oct 16
    "vb-mv-small": ("vita-bull", [("m-t", "c-vb-test", "AS-test", D(2026, 10, 14), D(2026, 10, 14), 20, 0, 0),
                                  ("m-s", "c-vb-s1", "AS-s1", D(2026, 10, 16), D(2026, 10, 17), 15, 0, 0)]),
}
for i in range(1, 46):      # a mass relaunch: 45 creatives tested Oct 13-14 and all entered scaling on Oct 22
    CREATIVES[f"vb-bulk-{i:02d}"] = ("vita-bull", [(f"bk{i}-t", "c-vb-test", "AS-test", D(2026, 10, 13), D(2026, 10, 14), 30, 0, 0),
                                                   (f"bk{i}-s", "c-vb-s1", "AS-s1", D(2026, 10, 22), D(2026, 10, 24), 20, 1, 25)])
BULK = [f"vb-bulk-{i:02d}" for i in range(1, 46)]
MOVED_ALL = ["vb-mv-a", "vb-mv-b", "bl-mv-old", "bl-mv-c", "vb-pre-data", "vb-mv-small"] + BULK
# Upstack twins of every daily row: all orders = 2x Meta's, new customers = 0.5x (spend stays Meta's)
UP_ALL, UP_NC = 2, 0.5


@pytest.fixture(scope="module")
def pdb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", PROMO_DB)
        conn = psycopg.connect(PROMO_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        pids = {slug: pid for pid, slug in conn.execute("SELECT id, slug FROM products").fetchall()}
        for cid, name in CAMPAIGNS.items():
            conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,'Lonvera Main',%s,'scaling')", (cid, name))
        adsets, ids, sheet = {}, {}, 0
        for fam, (prod, ads) in CREATIVES.items():
            sheet += 1
            tag = prod == "beef-liver"
            rid = conn.execute(
                """INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, launch_date, raw)
                   VALUES (%s,%s,%s,%s,%s,%s,'8 - Launched','VID',%s,'{}') RETURNING id""",
                (pids[prod], f"{fam}#1", f"C-{fam}", fam.upper(), fam, sheet, D(2026, 8, 1))).fetchone()[0]
            ids[fam] = rid
            if tag:
                conn.execute("INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point, tagger) VALUES (%s,'tracker','t',true,'Hair','BL1 Weekend Face','Hair Loss','test')", (rid,))
            conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,'exact')", (fam, fam.upper(), fam))
            for ad_id, camp, adset, d1, d2, sp, pu, rv in ads:
                if ad_id not in adsets:
                    adsets[ad_id] = adset
                    conn.execute(
                        """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name, created_date, status, family_name)
                           VALUES (%s,'Lonvera Main',%s,%s,%s,%s,%s,%s,'ACTIVE',%s)""", (ad_id, fam.upper(), fam, camp, "set:" + adset, adset, D(2026, 8, 1), fam.upper()))
                for d in rng(d1, d2):
                    conn.execute(
                        """INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'meta_api')""", (ad_id, d, fam, fam.upper(), camp, sp, pu, rv, sp // 10, sp * 20))
        # a sheet-backfilled style family: spend with no campaign = 'unclassified'
        conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, raw) VALUES (%s,'unc#1','C-unc','UNC','vb-unc',999,'{}')", (pids["vita-bull"],))
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('vb-unc','UNC','vb-unc','exact')")
        conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, source) VALUES ('fam:vb-unc',%s,'vb-unc','UNC','',5,0,0,0,'sheet')", (D(2026, 5, 7),))
        # one creative gets a thumbnail and a hook
        conn.execute("INSERT INTO creative_assets (family_key, kind, idx, mime, bytes, source) VALUES ('vb-mv-a','thumb',0,'image/png',%s,'test')", (S.PNG_1X1,))
        conn.execute("INSERT INTO ad_hooks (tracker_row_id, hook, version) VALUES (%s,'Stop guessing at your numbers','hooks-test')", (ids["vb-mv-a"],))
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('daily_start', %s)", (D0.isoformat(),))
        build_metrics.rebuild(conn, commit=False)
        # the Upstack source: the same ads and spend, orders / new customers scaled
        conn.execute(
            """INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, nc_purchases, nc_conv_value, link_clicks, impressions)
               SELECT ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases * %s, conv_value * %s, purchases * %s, conv_value * %s, link_clicks, impressions FROM ad_daily""",
            (UP_ALL, UP_ALL, UP_NC, UP_NC))
        for b in ("nc", "all"):
            build_metrics.rebuild_source(conn, b)
        conn.execute("SELECT refresh_creative_stage_moves()")                       # the Test to scale tables (migration 016) are precomputed
        for k, v in (("upstack_through", THROUGH.isoformat()), ("upstack_first_day", D0.isoformat()), ("upstack_built_at", "2026-11-03T00:00:00+00:00")):
            conn.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s)", (k, v))
        conn.execute("INSERT INTO etl_runs (job, started_at, finished_at, status, data_through) VALUES ('build_metrics', now() - interval '1 hour', now() - interval '59 minutes', 'ok', %s)", (THROUGH,))
        conn.commit()
    return {"ids": ids, "pids": pids}


@pytest.fixture(scope="module")
def papp(pdb):
    a = make_app(DATABASE_URL=PROMO_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def pc(papp):
    c = papp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


@pytest.fixture()
def pconn(pdb):
    with psycopg.connect(PROMO_URL, row_factory=psycopg.rows.dict_row) as c:
        yield c


def names(rows):
    return [r["family_key"] for r in rows]


def row(d, fam):
    return next(r for r in d["moved"] if r["family_key"] == fam)


def ns_row(d, fam):
    return next(r for r in d["not_scaled"]["rows"] if r["family_key"] == fam)


def get(pc, qs=""):
    return get_ok(pc, "/api/promotions" + ("?" + qs if qs else ""))


# ----------------------------------------------------------------------------------------------------------------- the SQL view

def test_view_classifies_every_path_and_measures_the_move(pconn):
    v = {r["family_key"]: r for r in pconn.execute("SELECT * FROM creative_stage_moves").fetchall()}
    assert v["vb-mv-a"]["path"] == "moved" and v["vb-mv-a"]["moved"] and v["vb-mv-a"]["began_in_testing"]
    assert (v["vb-mv-a"]["first_day"], v["vb-mv-a"]["testing_first_day"], v["vb-mv-a"]["scaling_first_day"]) == (D(2026, 10, 10), D(2026, 10, 10), D(2026, 10, 20))
    assert v["vb-mv-a"]["days_in_test"] == 10
    assert v["vb-nv-live"]["path"] == "testing_only" and v["vb-nv-live"]["days_in_test"] is None and v["vb-nv-live"]["live"] and not v["vb-nv-live"]["moved"]
    assert v["bl-nv-stop"]["path"] == "testing_only" and not v["bl-nv-stop"]["live"]
    assert v["vb-sc-only"]["path"] == "scaling_only" and not v["vb-sc-only"]["began_in_testing"] and float(v["vb-sc-only"]["test_spend"]) == 0
    assert v["bl-same-day"]["path"] == "same_day" and not v["bl-same-day"]["moved"]
    assert v["vb-sc-first"]["path"] == "scaling_first" and not v["vb-sc-first"]["moved"]
    assert v["vb-unc"]["path"] == "unclassified_only" and float(v["vb-unc"]["unclassified_spend"]) == 5
    assert sum(1 for r in v.values() if r["moved"]) == len(MOVED_ALL)


def test_view_numbers_split_at_the_first_scaling_day(pconn):
    a = pconn.execute("SELECT * FROM creative_stage_moves WHERE family_key = 'vb-mv-a'").fetchone()
    assert (float(a["test_spend"]), float(a["test_purchases"]), float(a["test_revenue"]), a["test_days"]) == (500.0, 5.0, 450.0, 5)
    assert float(a["test_after_spend"]) == 20.0                         # the testing copy that kept running after the move is not test evidence
    assert (float(a["scale_spend"]), float(a["scale_purchases"]), float(a["scale_revenue"]), a["scale_days"]) == (2800.0, 42.0, 3780.0, 14)
    assert float(a["last7_scale_spend"]) == 1400.0 and float(a["last7_spend"]) == 1400.0
    assert a["live"] and a["live_scaling"] and a["data_through"] == THROUGH and a["data_start"] == D0
    old = pconn.execute("SELECT * FROM creative_stage_moves WHERE family_key = 'bl-mv-old'").fetchone()
    assert old["moved"] and not old["live"] and not old["live_scaling"] and float(old["last7_spend"]) == 0


def test_view_flags_truncated_launch_and_bulk_days(pconn):
    p = pconn.execute("SELECT * FROM creative_stage_moves WHERE family_key = 'vb-pre-data'").fetchone()
    assert p["launched_before_data_start"] and p["moved"] and p["days_in_test"] == (D(2026, 10, 28) - D0).days
    assert not pconn.execute("SELECT launched_before_data_start FROM creative_stage_moves WHERE family_key = 'vb-mv-a'").fetchone()["launched_before_data_start"]
    b = pconn.execute("SELECT * FROM creative_stage_moves WHERE family_key = 'vb-bulk-07'").fetchone()
    assert b["moves_that_day"] == 45 and b["bulk_move_day"]
    assert pconn.execute("SELECT count(*) FROM creative_stage_moves WHERE bulk_move_day").fetchone()["count"] == 45
    one = pconn.execute("SELECT moves_that_day, bulk_move_day FROM creative_stage_moves WHERE family_key = 'bl-mv-c'").fetchone()
    assert (one["moves_that_day"], one["bulk_move_day"]) == (1, False)


def test_view_has_a_variant_per_attribution_basis(pconn):
    sp = {b: float(pconn.execute(f"SELECT scale_spend FROM creative_stage_moves{b} WHERE family_key = 'vb-mv-a'").fetchone()["scale_spend"]) for b in ("", "_upstack_nc", "_upstack_all")}
    assert sp == {"": 2800.0, "_upstack_nc": 2800.0, "_upstack_all": 2800.0}          # spend is Meta's in every basis
    got = {b: float(pconn.execute(f"SELECT scale_revenue FROM creative_stage_moves{b} WHERE family_key = 'vb-mv-a'").fetchone()["scale_revenue"]) for b in ("", "_upstack_nc", "_upstack_all")}
    assert got == {"": 3780.0, "_upstack_nc": 3780.0 * UP_NC, "_upstack_all": 3780.0 * UP_ALL}


# ----------------------------------------------------------------------------------------------------------------- the endpoint: window, moved table

def test_default_window_is_the_last_30_days_and_lists_the_moves(pc):
    d = get(pc)
    assert d["window_info"]["from"] == "2026-10-04" and d["window_info"]["to"] == "2026-11-02" and d["window_info"]["days"] == 30
    assert d["platform"] == "meta" and d["data_through"] == "2026-11-02" and d["window_info"]["source"]["key"] == "meta"
    assert sorted(names(d["moved"])) == sorted([f for f in MOVED_ALL if f != "bl-mv-old"])           # moved before Oct 4: not in the window
    assert names(d["moved"])[0] == "vb-mv-a"                                                       # biggest scaling spend first
    assert d["summary"]["moved"]["n"] == len(d["moved"]) == 50


def test_moved_row_carries_the_numbers_around_the_move(pc):
    r = row(get(pc), "vb-mv-a")
    assert (r["launched"], r["moved"], r["days_in_test"]) == ("2026-10-10", "2026-10-20", 10)
    assert r["test"] == {"spend": 500.0, "purchases": 5, "revenue": 450.0, "roas": 0.9, "cpa": 100.0, "link_clicks": 50, "days": 5}
    assert r["scale"]["spend"] == 2800.0 and r["scale"]["purchases"] == 42 and r["scale"]["revenue"] == 3780.0
    assert r["scale"]["roas"] == 1.35 and r["scale"]["cpa"] == pytest.approx(66.67, abs=0.01) and r["scale"]["days"] == 14
    assert r["test_after_spend"] == 20.0
    assert r["last7"]["spend"] == 1400.0 and r["last7"]["roas"] == 1.35
    assert r["live"] and r["live_scaling"] and not r["bulk"] and not r["launched_before_data_start"]
    assert r["held"] == {"state": "held", "ratio": 1.5}
    assert r["product"] == {"slug": "vita-bull", "name": "Vita Bull"} and r["cluster"] == "(untagged)"
    assert r["thumb_asset_id"] is not None and r["hook"] == "Stop guessing at your numbers" and r["display"] == "VB-MV-A"
    assert r["primary_id"] > 0 and r["ad_type"] == "VID"


def test_moved_row_lists_where_it_scaled_and_ties_to_the_ad_level_spend(pc, pconn):
    d = get(pc)
    p = row(d, "vb-mv-a")["placements"]
    assert (p["campaigns"], p["adsets"], p["ads"], p["spend"], p["more"]) == (2, 2, 2, 2800.0, 0)
    top = {x["campaign"]: x for x in p["top"]}
    assert top["VB_Scaling_One"]["spend"] == 2100.0 and top["VB_Scaling_One"]["adset"] == "AS-s1" and top["VB_Scaling_Two"]["spend"] == 700.0
    assert p["top"][0]["campaign"] == "VB_Scaling_One" and top["VB_Scaling_One"]["first_day"] == "2026-10-20" and top["VB_Scaling_One"]["roas"] == pytest.approx(200 / 150, abs=1e-4)
    # every moved creative: the scaling placements add up to ad_daily's scaling spend after the move, and to the creative's own scaling spend
    want = {r["fk"]: float(r["s"]) for r in pconn.execute(
        """SELECT m.tracker_family_key AS fk, sum(d.spend) AS s FROM ad_daily d JOIN meta_ads a ON a.ad_id = d.ad_id JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
           JOIN meta_campaigns c ON c.campaign_id = d.campaign_id AND c.stage = 'scaling' JOIN creative_stage_moves v ON v.family_key = m.tracker_family_key AND v.moved AND d.day >= v.scaling_first_day
           GROUP BY 1""").fetchall()}
    for r in d["moved"]:
        assert r["placements"]["spend"] == r["scale"]["spend"] == pytest.approx(want[r["family_key"]], abs=0.005), r["family_key"]
    assert sum(r["scale"]["spend"] for r in d["moved"]) == pytest.approx(sum(v for k, v in want.items() if k != "bl-mv-old"), abs=0.01)


def test_held_up_rule_and_the_too_early_cases(pc):
    d = get(pc)
    assert row(d, "vb-mv-b")["held"]["state"] == "too_early" and row(d, "vb-mv-b")["scale"]["days"] == 3       # $150 in 3 days: below the $200 floor
    assert row(d, "vb-bulk-01")["held"]["state"] == "too_early" and row(d, "vb-mv-small")["held"]["state"] == "too_early"        # $60 and $30
    c = row(d, "bl-mv-c")                                                       # tested at 0.80 ROAS, scales at 45 / 40 = 1.125 on $440 over 11 days
    assert c["held"] == {"state": "held", "ratio": round(1.125 / 0.8, 4)}
    assert row(d, "vb-pre-data")["held"] == {"state": "no_baseline", "ratio": None}                          # $360 scaled, but its testing earned nothing to compare with
    s = d["summary"]["held"]
    assert s == {"held": 2, "softer": 0, "fell": 0, "too_early": 47, "no_baseline": 1} and sum(s.values()) == len(d["moved"])


def test_creative_that_never_moved_is_not_in_the_moved_table(pc):
    d = get(pc)
    for fam in ("vb-nv-live", "bl-nv-stop", "vb-sc-only", "bl-same-day", "vb-sc-first", "bl-mv-old"):
        assert fam not in names(d["moved"]), fam


def test_truncated_launch_is_flagged_and_left_out_of_the_median(pc):
    d = get(pc)
    r = row(d, "vb-pre-data")
    assert r["launched_before_data_start"] and r["days_in_test"] == (D(2026, 10, 28) - D0).days
    assert d["summary"]["moved"]["launched_before_data_start"] == 1
    assert d["summary"]["median_days_n"] == len(d["moved"]) - 1


# ----------------------------------------------------------------------------------------------------------------- not scaled / populations / cohorts

def test_launched_not_scaled_list_says_still_testing_or_stopped(pc):
    d = get(pc)
    assert sorted(names(d["not_scaled"]["rows"])) == ["bl-nv-stop", "vb-nv-live"] and d["not_scaled"]["total"] == 2
    live, stop = ns_row(d, "vb-nv-live"), ns_row(d, "bl-nv-stop")
    assert live["state"] == "still_testing" and live["age_days"] == 9 and live["test"]["spend"] == 450.0 and live["test"]["days"] == 9
    assert stop["state"] == "stopped" and stop["last_spend_day"] == "2026-10-10" and stop["test"]["spend"] == 120.0 and stop["age_days"] == 26
    assert stop["test"]["roas"] == 0.0 and stop["test"]["cpa"] is None
    assert stop["product"]["slug"] == "beef-liver" and stop["cluster"] == "Hair" and stop["icp"] == "BL1 Weekend Face" and stop["pain"] == "Hair Loss"


def test_summary_counts_and_denominators(pc):
    s = get(pc)["summary"]
    assert s["launched"]["n"] == 54 and s["launched"]["began_in_testing"] == 51 and s["launched"]["no_test"] == 3
    assert s["launched"]["no_test_paths"] == {"scaling_only": 1, "same_day": 1, "scaling_first": 1, "unclassified_only": 0}
    assert s["still_testing"] == 1 and s["stopped"] == 1 and s["moved_from_launched"] == 49
    assert s["pct_reached_scaling"] == pytest.approx(49 / 51, abs=1e-4)                 # moved / creatives that began in testing, never / all launched
    assert s["moved"]["n"] == 50 and s["moved"]["bulk"] == 45 and s["launched"]["already_running_at_data_start"] == 0
    # testing and scaling ROAS are ratios of sums over the moved creatives, not an average of their ROAS
    d = get(pc)
    sp = sum(r["test"]["spend"] for r in d["moved"]); rv = sum(r["test"]["revenue"] for r in d["moved"])
    assert s["testing"]["spend"] == pytest.approx(sp, abs=0.01) and s["testing"]["roas"] == pytest.approx(rv / sp, abs=1e-4)
    sp2 = sum(r["scale"]["spend"] for r in d["moved"]); rv2 = sum(r["scale"]["revenue"] for r in d["moved"])
    assert s["scaling"]["spend"] == pytest.approx(sp2, abs=0.01) and s["scaling"]["roas"] == pytest.approx(rv2 / sp2, abs=1e-4)
    assert s["scaling_vs_testing_roas"] == pytest.approx((rv2 / sp2) / (rv / sp), abs=1e-3)
    assert s["scaling"]["roas"] != pytest.approx(sum(r["scale"]["roas"] or 0 for r in d["moved"]) / len(d["moved"]), abs=1e-3)
    # median days to scale: the 49 creatives that were launched in the data (not the one already running at the start)
    days = sorted(r["days_in_test"] for r in d["moved"] if not r["launched_before_data_start"])
    assert s["median_days_to_scale"] == days[len(days) // 2] and s["median_days_n"] == 49


def test_cohort_funnel_by_launch_week_adds_up_to_the_summary(pc):
    c = get(pc)["cohorts"]
    weeks = {r["week"]: r for r in c["rows"]}
    assert list(weeks) == ["2026-10-05", "2026-10-12", "2026-10-19"]                       # weeks (Mondays) in which something was launched
    assert sum(r["launched"] for r in c["rows"]) == c["total"]["launched"] == 51
    assert sum(r["moved"] for r in c["rows"]) == c["total"]["moved"] == 49
    assert c["total"]["pct_reached_scaling"] == pytest.approx(49 / 51, abs=1e-4) and c["total"]["median_days_to_scale"] == 9.0
    assert c["total"]["still_testing"] == 1 and c["total"]["stopped"] == 1 and c["total"]["no_test"] == 3
    w = weeks["2026-10-12"]                          # Oct 12-18: mv-b (12th), 45 bulk (13th), mv-c and mv-small (14th) began in testing; sc-only (12th) and same-day (18th) did not
    assert (w["launched"], w["moved"], w["pct_reached_scaling"], w["no_test"], w["median_days_to_scale"]) == (48, 48, 1.0, 2, 9.0)
    w = weeks["2026-10-05"]                          # Oct 5-11: mv-a (10th) moved after 10 days, bl-nv-stop (8th) stopped, sc-first (9th) never tested
    assert (w["launched"], w["moved"], w["stopped"], w["still_testing"], w["no_test"], w["median_days_to_scale"]) == (2, 1, 1, 0, 1, 10.0) and w["pct_reached_scaling"] == 0.5
    w = weeks["2026-10-19"]                          # vb-nv-live launched Oct 25, still testing: nothing of this week has had time to scale
    assert (w["launched"], w["moved"], w["still_testing"], w["pct_reached_scaling"]) == (1, 0, 1, 0.0) and w["median_days_to_scale"] is None
    assert not any(r["partial"] for r in c["rows"]) and get(pc, "from=2026-10-07&to=2026-10-20")["cohorts"]["rows"][0]["partial"] is True


# ----------------------------------------------------------------------------------------------------------------- bulk moves

def test_bulk_move_day_is_flagged_not_hidden_by_default(pc):
    d = get(pc)
    assert d["bulk_days"] == [{"day": "2026-10-22", "moves": 45}]
    r = row(d, "vb-bulk-01")
    assert r["bulk"] and r["moves_that_day"] == 45 and r["moved"] == "2026-10-22"
    assert sum(1 for x in d["moved"] if x["bulk"]) == 45 == d["summary"]["moved"]["bulk"]
    assert d["hidden_bulk"] == 0 and d["filters"]["hide_bulk"] is False and d["filters"]["bulk_min"] == 40
    assert "40 or more" in d["definition"]["bulk"]
    pts = {p["t"]: p for p in d["series"]["points"]}
    assert pts["2026-10-22"]["bulk_moves"] == 45 and pts["2026-10-22"]["moves"] == 0 and pts["2026-10-23"]["moves"] == 1


def test_hide_bulk_removes_them_from_every_number(pc):
    d = get(pc, "hide_bulk=1")
    assert sorted(names(d["moved"])) == ["bl-mv-c", "vb-mv-a", "vb-mv-b", "vb-mv-small", "vb-pre-data"]
    assert d["hidden_bulk"] == 45 and d["summary"]["moved"]["bulk"] == 0 and d["summary"]["moved"]["n"] == 5
    s = d["summary"]
    assert s["launched"]["began_in_testing"] == 6 and s["moved_from_launched"] == 4 and s["pct_reached_scaling"] == pytest.approx(4 / 6, abs=1e-4)
    assert sum(p["moves"] + p["bulk_moves"] for p in d["series"]["points"]) == 5
    assert d["cohorts"]["total"]["launched"] == 6 and any("bulk days" in n for n in d["notes"])
    assert get(pc, "hide_bulk=0")["summary"]["moved"]["n"] == 50


def test_bulk_threshold_is_a_parameter(pc):
    assert get(pc, "bulk_min=46")["summary"]["moved"]["bulk"] == 0 and get(pc, "bulk_min=46")["bulk_days"] == []
    assert get(pc, "bulk_min=45")["summary"]["moved"]["bulk"] == 45
    assert get(pc, "bulk_min=1&hide_bulk=1")["summary"]["moved"]["n"] == 0                                # every move day counts as bulk at 1
    assert "46 or more" in get(pc, "bulk_min=46")["definition"]["bulk"]


# ----------------------------------------------------------------------------------------------------------------- window edges

@pytest.mark.parametrize("qs,want", [
    ("from=2026-10-20&to=2026-10-20", ["vb-mv-a"]),
    ("from=2026-10-19&to=2026-10-19", []),
    ("from=2026-10-23&to=2026-10-23", ["bl-mv-c"]),
    ("from=2026-10-22&to=2026-10-22", BULK),
    ("from=2026-10-15&to=2026-10-16", ["vb-mv-b", "vb-mv-small"]),
    ("from=2026-09-28&to=2026-09-28", ["bl-mv-old"]),
    ("from=2026-10-28&to=2026-11-02", ["vb-pre-data"]),
    ("days=1", []),
    ("days=6", ["vb-pre-data"]),
    ("window=7d", ["vb-pre-data"]),
    ("window=mtd", []),
])
def test_moves_are_counted_by_their_first_scaling_day_inside_the_window(pc, qs, want):
    d = get(pc, qs)
    assert sorted(names(d["moved"])) == sorted(want), qs
    assert d["summary"]["moved"]["n"] == len(want)
    assert sum(p["moves"] + p["bulk_moves"] for p in d["series"]["points"]) == len(want)


def test_window_forms_agree_and_since_launch_means_every_day_held(pc):
    a, b, c = get(pc, "days=30"), get(pc, "window=30d"), get(pc)
    assert names(a["moved"]) == names(b["moved"]) == names(c["moved"])
    assert get(pc, "from=2026-10-04&to=2026-11-02")["summary"] == c["summary"]
    d = get(pc, "window=launch")
    assert len(d["moved"]) == 51 and "bl-mv-old" in names(d["moved"]) and d["window_info"]["from"] is None
    assert "All days held" in d["window_info"]["label"] and d["window_info"]["note"]
    assert d["series"]["grain"] == "week" and get(pc, "window=launch&grain=day")["series"]["grain"] == "day"
    assert row(d, "bl-mv-old")["live"] is False and row(d, "bl-mv-old")["days_in_test"] == 8 and row(d, "bl-mv-old")["held"]["state"] == "held"        # 0.88 ROAS tested, 1.0 scaled
    # the one moved before the window shows once the window reaches back to it
    assert "bl-mv-old" in names(get(pc, "days=40")["moved"]) and "bl-mv-old" not in names(get(pc, "days=30")["moved"])
    # the window clamps to the data span
    far = get(pc, "from=2026-04-01&to=2027-01-01")
    assert far["window_info"]["from"] == "2026-05-06" and far["window_info"]["to"] == "2026-11-02" and far["window_info"]["clamped"] is True and len(far["moved"]) == 51


def test_scaling_numbers_run_to_the_latest_day_not_to_the_window_end(pc):
    d = get(pc, "from=2026-10-20&to=2026-10-20")
    r = row(d, "vb-mv-a")
    assert r["scale"]["spend"] == 2800.0 and r["scale"]["days"] == 14                  # through Nov 2, not Oct 20
    assert any("latest day of data" in n for n in d["notes"])
    assert d["series"]["points"][0]["t"] == "2026-10-20" and d["series"]["points"][0]["scale_spend"] == 200.0 and len(d["series"]["points"]) == 1


def test_empty_results_are_empty_not_errors(pc):
    d = get(pc, "from=2026-10-19&to=2026-10-19")
    assert d["moved"] == [] and d["summary"]["moved"]["n"] == 0 and d["summary"]["testing"]["roas"] is None and d["summary"]["scaling"]["roas"] is None
    assert d["summary"]["median_days_to_scale"] is None and d["summary"]["scaling_vs_testing_roas"] is None
    d = get(pc, "from=2026-05-06&to=2026-05-06")                                       # first day of data: nothing launched in it counts, one creative was already running
    assert d["moved"] == [] and d["summary"]["launched"]["n"] == 0 and d["summary"]["launched"]["already_running_at_data_start"] >= 1
    assert d["summary"]["pct_reached_scaling"] is None and d["cohorts"]["rows"] == []


def test_bad_parameters_are_400s_and_unknown_product_404(pc):
    for qs in ("days=0", "days=367", "days=x", "window=forever", "from=2026-10-01", "hide_bulk=maybe", "bulk_min=0", "bulk_min=x", "min_test_spend=-1",
               "min_test_spend=abc", "grain=month", "source=bogus", "basis=bogus", "from=2027-02-01&to=2027-02-02"):
        r = pc.get("/api/promotions?" + qs)
        assert r.status_code == 400 and "error" in r.get_json(), qs
    assert pc.get("/api/promotions?product=nope").status_code == 404


def test_comparison_stage_and_active_are_ignored(pc):
    base = get(pc)
    for extra in ("compare=prev", "stage=testing", "active=1", "cfrom=2026-09-01&cto=2026-09-10"):
        d = get(pc, extra)
        assert d["summary"] == base["summary"] and d["compare_info"] is None, extra


# ----------------------------------------------------------------------------------------------------------------- filters

def test_min_test_spend_keeps_only_creatives_that_tested_properly(pc):
    d = get(pc, "min_test_spend=50")
    assert "vb-mv-small" not in names(d["moved"]) and len(d["moved"]) == 49
    s = d["summary"]
    assert s["launched"]["began_in_testing"] == 50 and s["moved_from_launched"] == 48 and s["launched"]["no_test"] == 3        # direct-to-scaling creatives do not test: unaffected
    assert names(d["not_scaled"]["rows"]) == ["vb-nv-live", "bl-nv-stop"] and ns_row(d, "vb-nv-live")["test"]["spend"] == 450.0           # tested for $450 and $120
    assert names(get(pc, "min_test_spend=200")["not_scaled"]["rows"]) == ["vb-nv-live"]
    assert sorted(names(get(pc, "min_test_spend=130")["moved"])) == ["bl-mv-c", "vb-mv-a"]          # test spend before the move: 500 and 150 (mv-b has 120, bulk 60, pre-data 75)
    assert get(pc, "min_test_spend=1000")["moved"] == [] and get(pc, "min_test_spend=1000")["not_scaled"]["rows"] == []
    assert get(pc, "min_test_spend=0")["summary"] == get(pc)["summary"]


def test_product_filter_and_chips(pc):
    bl = get(pc, "product=beef-liver")
    assert names(bl["moved"]) == ["bl-mv-c"] and names(bl["not_scaled"]["rows"]) == ["bl-nv-stop"]
    assert bl["summary"]["launched"]["began_in_testing"] == 2 and bl["summary"]["launched"]["no_test"] == 1 and bl["filters"]["product"] == "beef-liver"
    vb = get(pc, "product=vita-bull")
    assert len(vb["moved"]) == 49 and "bl-mv-c" not in names(vb["moved"])
    chips = {c["slug"]: c for c in get(pc, "product=beef-liver")["products"]}                         # the chips ignore the product filter
    assert chips["vita-bull"]["moved"] == 49 and chips["beef-liver"]["moved"] == 1 and chips["vita-bull"]["launched"] == 49 and chips["beef-liver"]["launched"] == 2
    assert [c["slug"] for c in get(pc)["products"]] == ["vita-bull", "beef-liver"]
    assert get(pc, "product=none")["moved"] == [] and get(pc, "product=vita-belle")["moved"] == []


def test_filters_compose(pc):
    d = get(pc, "product=vita-bull&hide_bulk=1&min_test_spend=100")
    assert names(d["moved"]) == ["vb-mv-a", "vb-mv-b"] and d["summary"]["moved"]["n"] == 2
    assert d["filters"] == {"product": "vita-bull", "hide_bulk": True, "bulk_min": 40, "min_test_spend": 100.0, "grain": "day"}


# ----------------------------------------------------------------------------------------------------------------- the three attribution bases

@pytest.mark.parametrize("qs,mult,labels", [
    ("", 1, {"purchases": "Purchases", "revenue": "Revenue", "roas": "ROAS", "cpa": "CPA"}),
    ("source=upstack&basis=all", UP_ALL, {"purchases": "Orders", "revenue": "Revenue", "roas": "ROAS", "cpa": "Cost per order"}),
    ("source=upstack&basis=nc", UP_NC, {"purchases": "New customers", "revenue": "NC revenue", "roas": "NC ROAS", "cpa": "NC CPA"}),
    ("source=upstack", UP_NC, {"purchases": "New customers", "revenue": "NC revenue", "roas": "NC ROAS", "cpa": "NC CPA"}),
])
def test_three_bases_change_purchases_and_revenue_but_never_spend(pc, qs, mult, labels):
    d = get(pc, qs)
    r = row(d, "vb-mv-a")
    assert r["test"]["spend"] == 500.0 and r["scale"]["spend"] == 2800.0 and r["last7"]["spend"] == 1400.0                  # Meta's spend in every basis
    assert r["test"]["purchases"] == pytest.approx(5 * mult) and r["test"]["revenue"] == pytest.approx(450 * mult)
    assert r["scale"]["purchases"] == pytest.approx(42 * mult) and r["scale"]["revenue"] == pytest.approx(3780 * mult)
    assert r["scale"]["roas"] == pytest.approx(1.35 * mult, abs=1e-3) and r["test"]["roas"] == pytest.approx(0.9 * mult, abs=1e-3)
    assert r["held"] == {"state": "held", "ratio": 1.5}                                                                      # a pure scaling of both: the ratio does not move
    assert d["labels"] == labels and d["window_info"]["source"]["labels"] == labels
    assert sorted(names(d["moved"])) == sorted(f for f in MOVED_ALL if f != "bl-mv-old")                                   # same moves in every basis
    assert d["summary"]["scaling"]["roas"] == pytest.approx(get(pc)["summary"]["scaling"]["roas"] * mult, abs=1e-3)
    assert row(d, "vb-mv-a")["placements"]["spend"] == 2800.0 and row(d, "vb-mv-a")["placements"]["top"][0]["revenue"] == pytest.approx(14 * 200 * mult)
    assert get(pc)["window_info"]["source"]["key"] == "meta"                                                                 # nothing leaks into the next request


def test_upstack_window_follows_the_upstack_span(pc, pconn):
    pconn.execute("UPDATE app_meta SET value = '2026-10-20' WHERE key = 'upstack_first_day'")
    pconn.commit()
    try:
        d = get(pc, "source=upstack&window=launch")
        assert d["window_info"]["daily_start"] == "2026-10-20"
        assert d["window_info"]["note"]
    finally:
        pconn.execute("UPDATE app_meta SET value = '2026-05-06' WHERE key = 'upstack_first_day'")
        pconn.commit()


# ----------------------------------------------------------------------------------------------------------------- chart series

def test_series_adds_up_to_the_table(pc):
    d = get(pc)
    pts = d["series"]["points"]
    assert d["series"]["grain"] == "day" and len(pts) == 30 and pts[0]["t"] == "2026-10-04" and pts[-1]["t"] == "2026-11-02"
    assert sum(p["moves"] + p["bulk_moves"] for p in pts) == 50
    assert sum(p["launched"] for p in pts) == d["summary"]["launched"]["began_in_testing"] == 51
    assert sum(p["scale_spend"] for p in pts) == pytest.approx(sum(r["scale"]["spend"] for r in d["moved"]), abs=0.01)         # the whole window is in view here
    by = {p["t"]: p for p in pts}
    assert by["2026-10-20"]["moves"] == 1 and by["2026-10-20"]["scale_spend"] == 200.0 and by["2026-10-23"]["moves"] == 1
    assert by["2026-10-22"]["scale_spend"] == 45 * 20 + 200 and by["2026-10-22"]["scale_roas"] == pytest.approx((45 * 25 + 270) / (45 * 20 + 200), abs=1e-4)
    assert by["2026-10-19"]["moves"] == 0 and by["2026-10-19"]["scale_spend"] == 0.0 and by["2026-10-19"]["scale_roas"] is None


def test_series_by_week_buckets_on_mondays_and_marks_partial_weeks(pc):
    d = get(pc, "grain=week")
    pts = d["series"]["points"]
    assert d["series"]["grain"] == "week" and [p["t"] for p in pts] == ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02"]
    assert [p["partial"] for p in pts] == [True, False, False, False, False, True]          # the window starts on a Sunday and ends on a Monday
    assert sum(p["moves"] + p["bulk_moves"] for p in pts) == 50
    wk = {p["t"]: p for p in pts}
    assert (wk["2026-10-19"]["moves"], wk["2026-10-19"]["bulk_moves"], wk["2026-10-12"]["moves"], wk["2026-10-26"]["moves"]) == (2, 45, 2, 1)
    assert wk["2026-10-12"]["launched"] == 48 and wk["2026-10-12"]["scale_spend"] == 180.0
    assert sum(p["scale_spend"] for p in pts) == pytest.approx(sum(r["scale"]["spend"] for r in d["moved"]), abs=0.01)


# ----------------------------------------------------------------------------------------------------------------- plumbing

def test_endpoint_needs_a_login_and_is_cacheable(papp, pc):
    assert papp.test_client().get("/api/promotions").status_code == 401
    from app import respcache
    assert respcache.CACHEABLE.match("/api/promotions") and not respcache.CACHEABLE.match("/api/promotions/x")
    assert pc.get("/promotions").status_code == 200                                    # the page shell is served at the route


def test_independent_check_of_the_moved_set_against_the_raw_daily_rows(pc, pconn):
    """The moved creatives of the last 30 days from ad_daily alone: first spend day per stage per family, no view, no family_daily."""
    got = pconn.execute("""
        WITH d AS (SELECT m.tracker_family_key AS fk, d.day, c.stage FROM ad_daily d JOIN meta_ads a ON a.ad_id = d.ad_id JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
                   JOIN meta_campaigns c ON c.campaign_id = d.campaign_id WHERE d.spend > 0),
        f AS (SELECT fk, min(day) FILTER (WHERE stage = 'testing') AS t, min(day) FILTER (WHERE stage = 'scaling') AS s FROM d GROUP BY fk)
        SELECT fk FROM f WHERE t < s AND s BETWEEN %s AND %s""", (THROUGH - dt.timedelta(days=29), THROUGH)).fetchall()
    assert sorted(r["fk"] for r in got) == sorted(names(get(pc)["moved"]))


def test_response_is_json_safe_and_free_of_sql_text(pc):
    raw = pc.get("/api/promotions?window=launch").get_data(as_text=True)
    json.loads(raw)
    assert "SELECT" not in raw and "Decimal" not in raw


# ----------------------------------------------------------------------------------------------------------------- precomputed tables (migration 016)
SFX = ("", "_upstack_nc", "_upstack_all")


def old_view_sql(sfx):
    """The migration-015 view logic (the select inside its template) for one basis: the reference the precomputed tables must equal."""
    import re
    from pathlib import Path
    mig = (Path(__file__).resolve().parents[2] / "migrations" / "015_creative_stage_moves.sql").read_text()
    tpl = re.search(r"\$tpl\$(.*?)\$tpl\$", mig, re.S).group(1)
    return tpl[tpl.index("WITH bounds"):].replace("%1$s", sfx)


def snapshot(conn):
    out = {}
    for sfx in SFX:
        out["m" + sfx] = [tuple(sorted(r.items())) for r in conn.execute(f"SELECT * FROM creative_stage_moves{sfx} ORDER BY family_key").fetchall()]
        out["p" + sfx] = [tuple(sorted(r.items())) for r in conn.execute(f"SELECT * FROM creative_scaling_placements{sfx} ORDER BY family_key, campaign_id, adset_id").fetchall()]
    return out


def test_tables_equal_the_old_view_logic_in_every_basis(pconn):
    for sfx in SFX:
        want = [dict(r) for r in pconn.execute(f"SELECT * FROM ({old_view_sql(sfx)}) v ORDER BY family_key").fetchall()]
        have = [dict(r) for r in pconn.execute(f"SELECT * FROM creative_stage_moves{sfx} ORDER BY family_key").fetchall()]
        assert len(want) == len(have) > 50 and want == have, sfx
        assert [c for c in want[0]] == [c for c in have[0]]                                   # same columns in the same order
    assert pconn.execute("SELECT count(*) FROM creative_stage_moves WHERE moved").fetchone()["count"] == len(MOVED_ALL)


def test_placements_add_up_to_scaling_spend_in_every_basis(pconn):
    for sfx in SFX:
        bad = pconn.execute(f"""SELECT m.family_key, m.scale_spend, p.s FROM creative_stage_moves{sfx} m
              LEFT JOIN (SELECT family_key, sum(spend) AS s FROM creative_scaling_placements{sfx} GROUP BY 1) p USING (family_key)
              WHERE m.moved AND abs(m.scale_spend - coalesce(p.s, 0)) > 0.005""").fetchall()
        assert bad == [], (sfx, bad[:3])
        assert pconn.execute(f"SELECT count(*) FROM creative_scaling_placements{sfx} p JOIN creative_stage_moves{sfx} m USING (family_key) WHERE NOT m.moved").fetchone()["count"] == 0


def test_refresh_is_idempotent_and_stamps_when_it_ran(pconn):
    before = snapshot(pconn)
    t0 = pconn.execute("SELECT value FROM app_meta WHERE key = 'stage_moves_built_at'").fetchone()["value"]
    for _ in range(2):
        pconn.execute("SELECT refresh_creative_stage_moves()")
        pconn.commit()
        assert snapshot(pconn) == before
    row = {r["key"]: r["value"] for r in pconn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'stage_moves_%'").fetchall()}
    assert row["stage_moves_built_at"] >= t0 and row["stage_moves_data_through"] == THROUGH.isoformat()


def test_nightly_module_refreshes_records_the_run_and_fails_loudly(pconn):
    from etl import db as edb, stage_moves
    import os
    os.environ["DATABASE_URL"] = PROMO_URL
    try:
        assert stage_moves.main() == 0
        r = pconn.execute("SELECT status, rows_written, data_through, stats FROM etl_runs WHERE job = 'stage_moves' ORDER BY id DESC LIMIT 1").fetchone()
        assert r["status"] == "ok" and r["rows_written"] > 50 and r["data_through"] == THROUGH and r["stats"]["creative_stage_moves"] == r["rows_written"]
        # a failure after the rebuild started is recorded as failed and rolls back: the previous rows stay
        n_before = pconn.execute("SELECT count(*) FROM creative_stage_moves").fetchone()["count"]
        orig = stage_moves.refresh

        def broken(conn):
            conn.execute("SELECT refresh_creative_stage_moves()")
            raise edb.JobFailed("boom")
        stage_moves.refresh = broken
        try:
            with pytest.raises(edb.JobFailed):
                stage_moves.main()
        finally:
            stage_moves.refresh = orig
        assert pconn.execute("SELECT status FROM etl_runs WHERE job = 'stage_moves' ORDER BY id DESC LIMIT 1").fetchone()["status"] == "failed"
        assert pconn.execute("SELECT count(*) FROM creative_stage_moves").fetchone()["count"] == n_before
    finally:
        os.environ.pop("DATABASE_URL", None)


def test_api_reports_when_the_tables_were_built_and_warns_when_they_are_old(pc, pconn):
    d = get(pc)
    assert d["stage_moves"]["data_through"] == THROUGH.isoformat() and d["stage_moves"]["built_at"].endswith("+00:00")
    assert not any("rebuilt" in n for n in d["notes"])
    pconn.execute("UPDATE app_meta SET value = '2026-10-30' WHERE key = 'stage_moves_data_through'")
    pconn.commit()
    try:
        d = get(pc)
        assert d["stage_moves"]["data_through"] == "2026-10-30" and any("older than the latest day of data" in n for n in d["notes"])
    finally:
        pconn.execute("UPDATE app_meta SET value = %s WHERE key = 'stage_moves_data_through'", (THROUGH.isoformat(),))
        pconn.commit()


def test_not_built_yet_is_a_clear_503(pc, pconn):
    saved = pconn.execute("SELECT value FROM app_meta WHERE key = 'stage_moves_built_at'").fetchone()["value"]
    pconn.execute("DELETE FROM app_meta WHERE key = 'stage_moves_built_at'")
    pconn.commit()
    try:
        r = pc.get("/api/promotions")
        assert r.status_code == 503 and "etl.stage_moves" in r.get_json()["error"]
    finally:
        pconn.execute("INSERT INTO app_meta (key, value) VALUES ('stage_moves_built_at', %s)", (saved,))
        pconn.commit()


def test_a_slow_query_answers_a_json_error_not_a_hang(pc, monkeypatch):
    from app import promotions as P

    def slow(conn, lo, hi):
        conn.execute("SELECT pg_sleep(3)")
        return []
    monkeypatch.setattr(P, "_fetch", slow)
    monkeypatch.setattr(P, "QUERY_TIMEOUT_MS", 150)
    import time
    t = time.time()
    r = pc.get("/api/promotions")
    assert r.status_code == 503 and r.get_json() == {"error": "query timed out"} and time.time() - t < 2.5
