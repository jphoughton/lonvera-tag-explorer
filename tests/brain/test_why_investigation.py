"""The investigation inside `why`: noise gate by grain, AOV decomposition exactness, edited-vs-unedited control, verdict assignment, bulk-edit grouping (Python mirror and the SQL view
analytics.change_timeline on a migrated database), and the answer contract on a seeded scenario."""
import datetime as dt
import math
import random

import psycopg
import pytest

from brain import why as W
from brain import why_stats_inv as X
from brain.knowledge import load
from brain_helpers import DB_NAME, PG_BASE, ROOT
from why_seed import Scenario, make_runner, seed

D = dt.date
END = D(2026, 11, 12)
KN = load(ROOT)


def series(days=40, base_nc=300, base_spend=10000.0, aov=62.0, drop_last=0.0, seed_=1):
    rnd = random.Random(seed_)
    out = {}
    for i in range(days):
        d = END - dt.timedelta(days=days - 1 - i)
        nc = rnd.gauss(base_nc, math.sqrt(base_nc))
        f = 1 - (drop_last if d == END else 0)
        out[d] = {"nc": nc * f, "nc_rev": nc * f * aov, "spend": base_spend, "imps": 500000, "clicks": 9000}
    return out


def test_noise_gate_flat_series_is_inside_noise_and_a_collapse_is_real():
    flat = X.noise_gate(series(), END, "nc_roas")
    assert all(flat[g]["verdict"] == "inside noise" for g in (1, 3, 7)), flat
    bad = X.noise_gate(series(drop_last=0.45), END, "nc_roas")
    assert bad[1]["verdict"] == "real" and bad[1]["change_pct"] < -35
    assert abs(bad[7]["change_pct"]) < abs(bad[1]["change_pct"]) / 3      # one bad day dilutes in a 7-day window
    assert bad[1]["poisson_band_pct"] > 5 and bad[1]["count_basis"] == "nc"


def test_next_day_needed_names_the_value_that_confirms():
    daily = series(drop_last=0.08)
    row = X.noise_gate(daily, END, "nc_roas")[3]
    need = X.next_day_needed(daily, END, "nc_roas", 3, row)
    assert need and ("tomorrow_single_day_value_needed" in need or "note" in need)


def test_hourly_curve_flags_the_hour_the_day_falls_behind():
    rows = []
    for k in range(0, 29, 7):
        d = END - dt.timedelta(days=k)
        for h in range(24):
            v = 40.0 if h < 12 else (40.0 if k else 15.0)       # today stops earning from noon
            rows.append({"day": d, "hour": h, "nc_rev": v * 10, "nc": v / 6})
    cur = X.hourly_curve(rows, END)
    assert cur["first_hour_outside_noise"] is not None and cur["first_hour_outside_noise"] >= 12
    sp = X.event_hour_split(rows, END, 12)
    assert sp["before_ratio_vs_same_weekday"] == 1.0 and sp["after_ratio_vs_same_weekday"] < 0.5


def test_aov_decomposition_adds_up_exactly():
    cmp_ = {"orders": 1000, "products": {"A": {"qty": 1500.0, "gross": 1500 * 49.99, "disc": 1500 * 5.0}, "B": {"qty": 900.0, "gross": 900 * 39.99, "disc": 900 * 4.0}}}
    cur = {"orders": 900, "products": {"A": {"qty": 1000.0, "gross": 1000 * 54.99, "disc": 1000 * 8.0}, "B": {"qty": 1100.0, "gross": 1100 * 39.99, "disc": 1100 * 8.0}, "C": {"qty": 50.0, "gross": 50 * 20.0, "disc": 0.0}}}
    r = X.decompose_aov(cur, cmp_)
    assert abs(sum(r["effects"].values()) - r["change"]) < 5e-4 and abs(r["residual"]) < 1e-9
    only_price = X.decompose_aov({"orders": 1000, "products": {"A": {"qty": 1500.0, "gross": 1500 * 54.99, "disc": 7500.0}}}, {"orders": 1000, "products": {"A": {"qty": 1500.0, "gross": 1500 * 49.99, "disc": 7500.0}}})
    assert only_price["effects"]["price_point"] == pytest.approx(5.0 * 1.5, abs=1e-6) and only_price["effects"]["product_mix"] == 0 and only_price["effects"]["discount"] == 0


def rows_for(aff_pre, aff_post, un_pre, un_post, event=D(2026, 11, 11)):
    out = []
    for i, d in enumerate([event - dt.timedelta(days=k) for k in (3, 2, 1)] + [event, event + dt.timedelta(days=1)]):
        post = d >= event
        for aff, (pre_r, post_r) in ((True, (aff_pre, aff_post)), (False, (un_pre, un_post))):
            r = post_r if post else pre_r
            spend = 4000.0
            out.append({"day": d, "aff": aff, "spend": spend, "imps": 100000, "clicks": 2000, "nc": spend * r / 62, "nc_rev": spend * r})
    return out


def test_control_supports_only_when_the_unedited_group_stayed_put_and_pretrend_is_parallel():
    ctl = X.control(rows_for(0.8, 0.5, 0.8, 0.8), D(2026, 11, 11), D(2026, 11, 12))
    v, word = X.control_verdict(ctl, 0.5)
    assert v == "supported by control" and "parallel pre-trend" in word and ctl["nc_roas_diff_in_diff_pts"] < -30


def test_control_rules_out_when_the_unedited_group_moved_the_same_way():
    ctl = X.control(rows_for(0.8, 0.5, 0.8, 0.52), D(2026, 11, 11), D(2026, 11, 12))
    v, word = X.control_verdict(ctl, 0.5)
    assert v == "ruled out" and "consistent with not the cause" in word


def test_control_inside_noise_and_untestable():
    ctl = X.control(rows_for(0.8, 0.79, 0.8, 0.8), D(2026, 11, 11), D(2026, 11, 12))
    assert X.control_verdict(ctl, 0.5)[0] == "inside noise"
    tiny = X.control([{**r, "nc": 1.0, "nc_rev": 60.0} for r in rows_for(0.8, 0.5, 0.8, 0.8)], D(2026, 11, 11), D(2026, 11, 12))
    assert X.control_verdict(tiny, 0.5)[0] == "untestable"


def test_factor_verdict_assignment():
    assert X.factor_verdict(False, 0.9, True) == "inside noise"
    assert X.factor_verdict(True, 0.6, True) == "consistent with"
    assert X.factor_verdict(True, 0.05, True) == "ruled out"
    assert X.factor_verdict(True, 0.6, False) == "ruled out"
    assert X.factor_verdict(True, None, None) == "untestable"
    assert set(X.VERDICTS) == {"supported by control", "consistent with", "ruled out", "inside noise", "untestable"}


def test_group_bulk_merges_within_90_minutes_by_actor_and_type():
    t0 = dt.datetime(2026, 11, 11, 10, 11, tzinfo=dt.timezone.utc)
    ev = [{"event_time": t0 + dt.timedelta(minutes=i * 4), "event_type": "update_ad_set_bid_amount", "actor": "Rodolfo", "object_id": f"as{i}", "old_amount": 45 + i % 24, "new_amount": 80, "account": "Main"} for i in range(89)]
    ev += [{"event_time": t0 + dt.timedelta(days=1, hours=2), "event_type": "update_ad_set_bid_amount", "actor": "Rodolfo", "object_id": f"as{i}", "old_amount": 80, "new_amount": 68, "account": "Main"} for i in range(91)]
    ev += [{"event_time": t0 + dt.timedelta(minutes=30), "event_type": "update_ad_set_bid_amount", "actor": "Someone else", "object_id": "x1", "old_amount": 10, "new_amount": 12, "account": "Main"}]
    rows = X.group_bulk(ev)
    assert [r["objects"] for r in rows] == [89, 1, 91]
    assert rows[0]["old_range"] == [45.0, 68.0] and rows[0]["new_range"] == [80.0, 80.0] and rows[2]["new_range"] == [68.0, 68.0]


@pytest.fixture(scope="module")
def migrated():
    from etl.migrate import migrate
    name = DB_NAME + "_whyinv_sql"
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    with psycopg.connect(f"{PG_BASE}/{name}", autocommit=True) as conn:
        migrate(conn)
    return f"{PG_BASE}/{name}"


def test_change_timeline_view_groups_bulk_edits_and_exposes_the_actor(migrated):
    with psycopg.connect(migrated, autocommit=True) as c:
        c.execute("""insert into meta_change_log(event_key, account, event_time, event_day, event_type, object_level, object_id, object_name, actor_id, actor_name, old_amount, new_amount, raw)
            select 'k' || i, 'Main', timestamptz '2026-11-11 10:11-08' + (i || ' minutes')::interval, date '2026-11-11', 'update_ad_set_bid_amount', 'adset', 'as' || i, 'Set ' || i, '77', 'Rodolfo', 45 + i % 20, 80, '{}'
            from generate_series(0, 88) i""")
        c.execute("""insert into meta_change_log(event_key, account, event_time, event_day, event_type, object_level, object_id, object_name, actor_id, actor_name, old_amount, new_amount, raw)
            select 'z' || i, 'Main', timestamptz '2026-11-12 12:11-08' + (i || ' minutes')::interval, date '2026-11-12', 'update_ad_set_bid_amount', 'adset', 'as' || i, 'Set ' || i, '77', 'Rodolfo', 80, 68, '{}'
            from generate_series(0, 90) i""")
        c.execute("insert into meta_change_log(event_key, account, event_time, event_day, event_type, object_level, object_id, object_name, actor_id, old_amount, new_amount, raw) "
                  "values ('sys', 'Main', '2026-11-11 13:00-08', '2026-11-11', 'update_ad_set_bid_amount', 'adset', 'as1', 'Set 1', '0', 80, 68, '{}')")
        rows = c.execute("select event_day, kind, editor, objects_affected, old_value, new_value, array_length(object_ids, 1) from analytics.change_timeline where source = 'meta_activity_log' order by event_time").fetchall()
        cols = {r[0] for r in c.execute("select column_name from information_schema.columns where table_schema = 'analytics' and table_name = 'change_timeline'")}
    assert [(r[3], r[6]) for r in rows] == [(89, 89), (91, 91)]                      # the system event is excluded, each bulk edit is one row
    assert rows[0][2] == "Rodolfo" and rows[0][4] == "45.00 to 64.00" and rows[0][5] == "80.00" and rows[1][5] == "68.00"
    assert {"event_time", "event_day", "source", "kind", "scope", "object_ids", "object_names", "old_value", "new_value", "magnitude", "editor", "objects_affected", "prior_day_spend_share"} <= cols


@pytest.fixture(scope="module")
def whydb():
    name = DB_NAME + "_whyinv"
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    return f"{PG_BASE}/{name}"


def test_answer_contract_and_a_verdict_for_every_hypothesis(whydb):
    seed(whydb, Scenario(x_mode="new"))
    with psycopg.connect(whydb, autocommit=True) as c:
        ids = [r[0] for r in c.execute("select distinct adset_id from analytics.meta_ad_daily where adset_id is not null limit 20").fetchall()]
        c.execute("create table analytics.change_timeline (event_time timestamptz, event_day date, source text, kind text, scope text, object_ids text[], object_names text, product text, old_value text, new_value text, "
                  "magnitude numeric, editor text, objects_affected int, last_event_time timestamptz, prior_day_spend_share numeric)")
        c.execute("insert into analytics.change_timeline values ('2026-10-29 10:11-07', '2026-10-29', 'meta_activity_log', 'update_ad_set_bid_amount', 'adset', %s, '10 adsets', null, '45.00 to 68.00', '80.00', 0.4, 'Rodolfo', 10, "
                  "'2026-10-29 11:07-07', 0.5)", (ids[:10],))
    out = W.run_why({"start": "2026-10-29", "end": "2026-10-31", "compare": "prior_period"}, make_runner(whydb, []), KN)
    inv = out["investigation"]
    assert inv["applies"] and inv["answer"] and inv["chain"] and "next_check" in inv and "ruled_out" in inv and "untestable" in inv
    ids_ = [e["id"] for e in inv["ledger"]]
    for need in ("H1", "H2", "H3p", "H4", "H5", "H6", "H7", "H8", "H10", "H11", "H12", "H13", "E1"):
        assert need in ids_, need
    assert all(e["verdict"] in X.VERDICTS for e in inv["ledger"])
    assert all(e["evidence"] for e in inv["ledger"])
    assert any("update_ad_set_bid_amount" in c for c in inv["chain"])
    assert set(inv["noise_gate"]["grains"]) == {1, 3, 7}
    c = W.compact(out)
    assert list(c)[0] == "answer" and "chain" in c and c["ledger"] and "how_to_answer_first" in c
    assert out["steps_used"] <= 40


# ---------------------------------------------------------------- six added hypotheses (pure statistics)
def _seg(p, key, spend, ncr, nc, clicks=1000):
    return {"p": p, "key": key, "spend": spend, "ncr": ncr, "nc": nc, "clicks": clicks}


def test_shift_share_is_exact_for_every_metric_and_names_the_mover():
    rows = [_seg("cmp", "A", 1760, 3500, 150, 3000), _seg("cmp", "B", 1000, 500, 40, 2000), _seg("cmp", "C", 3240, 3000, 200, 6000),
            _seg("cur", "A", 690, 1380, 60, 1200), _seg("cur", "B", 3000, 1500, 120, 6000), _seg("cur", "C", 2310, 1900, 140, 4300), _seg("cur", "D", 500, 100, 8, 700)]   # D is new, A shrinks
    for m in ("nc_roas", "cost_per_nc", "conversion"):
        r = X.shift_share(rows, m)
        assert abs(r["residual"]) < 1e-12, (m, r["residual"])
        assert abs(r["mix_total"] + r["rate_total"] - r["total"]) < 1e-12
    r = X.shift_share(rows, "nc_roas")
    assert any(mv["key"] == "A" and mv["mix_material"] for mv in r["movers"])           # A's spend share 17.6% -> 6.9%
    assert all(mv["key"] != "D" or not mv["rate_material"] for mv in r["movers"])        # 8 new customers: below the count floor


def test_shift_share_with_a_flat_mix_and_flat_rates_has_no_movers_and_empty_input_is_none():
    rows = [_seg("cmp", "A", 1000, 900, 50), _seg("cmp", "B", 1000, 800, 50), _seg("cur", "A", 1000, 905, 50), _seg("cur", "B", 1000, 795, 50)]
    assert X.shift_share(rows, "nc_roas")["movers"] == []
    assert X.shift_share([], "nc_roas") is None and X.shift_share([_seg("cur", "A", 5, 5, 1)], "nc_roas") is None


def test_cap_exposure_shares_levels_and_binding():
    cur = {"spend": 1000, "capped_spend": 800, "spend_times_cap": 800 * 80, "floor_spend": 100, "spend_times_floor": 100 * 0.9, "meta_cpp": 78.0, "meta_roas": 0.8}
    cmp_ = {"spend": 1000, "capped_spend": 300, "spend_times_cap": 300 * 95, "floor_spend": 100, "spend_times_floor": 100 * 0.9, "meta_cpp": 60.0, "meta_roas": 1.0}
    r = X.cap_exposure(cur, cmp_)
    assert round(r["capped_share_shift_pts"]) == 50 and round(r["cap_level_change_pct"], 1) == round((80 / 95 - 1) * 100, 1) and r["moved"]
    assert r["cur"]["avg_cap"] == 80 and "binding" in r["binding_cur"]["cost_cap"] and r["binding_cmp"]["cost_cap"].startswith("not binding")
    assert X.cap_exposure({"spend": 0}, cmp_) is None
    flat = X.cap_exposure(cmp_, dict(cmp_))
    assert not flat["moved"]


def test_intraday_shape_flags_back_loaded_day_from_the_right_hour_and_needs_baselines():
    def day(f):                       # f(hour) amount
        return {h: f(h) for h in range(24)}
    base = [day(lambda h: 10.0) for _ in range(4)]
    tgt = day(lambda h: 10.0 if h < 14 else 25.0)               # same morning, heavier after 14:00 -> back-loaded vs front
    r = X.intraday_shape(tgt, base)
    assert r["first_outside"]["direction"] == "back-loaded"
    assert X.intraday_shape(dict(base[0]), base)["first_outside"] is None                      # a normal day is inside the band at every hour
    slow = day(lambda h: 4.0 if h < 18 else 40.0)
    s = X.intraday_shape(slow, [day(lambda h: 10.0 + (i % 2)) for i in range(4)])
    assert s["first_outside"]["direction"] == "back-loaded" and s["first_outside"]["hour"] < 18
    c18 = next(c for c in s["checkpoints"] if c["hour"] == 18)
    assert c18["target_share"] < c18["baseline_share"]
    assert X.intraday_shape(tgt, base[:1]) is None and X.intraday_shape({}, base) is None


def test_calendar_flags_and_history():
    D = dt.date
    assert "holiday: thanksgiving" in X.calendar_flags(D(2026, 11, 26)) and "black friday" in X.calendar_flags(D(2026, 11, 27)) and "cyber monday" in X.calendar_flags(D(2026, 11, 30))
    assert "BFCM ramp window" in X.calendar_flags(D(2026, 11, 20)) and "BFCM ramp window" not in X.calendar_flags(D(2026, 12, 3))
    assert "payday (last business day)" in X.calendar_flags(D(2026, 10, 30)) and "month start" in X.calendar_flags(D(2026, 11, 2))
    assert "payday (1st / 15th)" in X.calendar_flags(D(2026, 5, 15)) and "payday (1st / 15th)" in X.calendar_flags(D(2026, 11, 13))          # Nov 15 2026 is a Sunday
    daily = {}
    d = D(2025, 1, 1)
    while d <= D(2026, 11, 20):
        daily[d] = {"nc_rev": 100.0 * (1.5 if X.calendar_flags(d) and "BFCM ramp window" in X.calendar_flags(d) else 1.0), "nc": 2.0, "spend": 100.0, "imps": 0, "clicks": 0}
        d += dt.timedelta(days=1)
    res = X.calendar_check(daily, "nc_revenue", (D(2025, 11, 17), D(2025, 11, 23)), (D(2025, 11, 3), D(2025, 11, 9)))
    assert "BFCM ramp window" in res["differing"] and res["move_now"] > 0.4
    res2 = X.calendar_check(daily, "nc_revenue", (D(2026, 11, 16), D(2026, 11, 20)), (D(2026, 11, 2), D(2026, 11, 6)))
    assert res2["same_sign"]["last year"] is True and res2["differing"]["BFCM ramp window"]["cur_days"] == 5
    same = X.calendar_check(daily, "nc_revenue", (D(2026, 3, 24), D(2026, 3, 27)), (D(2026, 3, 17), D(2026, 3, 20)))
    assert same["differing"] == {}


def test_two_prop_z():
    p1, p0, z = X.two_prop_z(30, 1000, 10, 1000)
    assert (p1, p0) == (0.03, 0.01) and z > 3 and X.two_prop_z(1, 0, 1, 5) is None


def _extend(url):
    """Add the views the six new hypotheses read, with a known story: one persona loses its spend share, caps tighten, orders move to another source."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("alter table analytics.creative_tracker add column product_slug text")
        keys = [r[0] for r in c.execute("select family_key from analytics.creative_tracker order by family_key limit 2").fetchall()]
        c.execute("update analytics.creative_tracker set icp = 'Thin', product_slug = 'vita-bull' where family_key = %s", (keys[0],))
        c.execute("update analytics.creative_tracker set icp = 'Beginner', product_slug = 'vita-bull' where family_key = %s", (keys[1],))
        c.execute("create table analytics.creative_family_daily (basis text, ads_scope text, family_key text, product_slug text, day date, stage text, spend numeric, purchases numeric, revenue numeric, link_clicks numeric, impressions numeric)")
        c.execute("create table analytics.meta_bids_daily (day date, spend numeric, capped_spend numeric, spend_times_cap numeric, floor_spend numeric, spend_times_floor numeric, loaded_at timestamptz)")
        c.execute("create table analytics.orders_hourly (day date, hour int, new_customer_revenue numeric, new_customers numeric, loaded_at timestamptz)")
        c.execute("create table analytics.pacing_live_today (pulled_at timestamptz, day date, pacific_time time, meta_spend numeric, google_spend numeric, total_spend numeric, new_customer_revenue numeric, new_customers int)")
        c.execute("create table analytics.attribution_unmapped_daily (day date, campaign text, ad text, reason text, new_customers numeric, new_customer_revenue numeric, orders numeric, order_revenue numeric)")
        c.execute("create table analytics.attribution_source_daily (day date, ad_source text, new_customers numeric, new_customer_revenue numeric, orders numeric, order_revenue numeric, loaded_at timestamptz)")
        for col, typ in (("source_name", "text"), ("refunded_amount", "numeric"), ("is_rebill", "boolean")):
            c.execute(f"alter table analytics.shopify_orders add column {col} {typ}")
        c.execute("update analytics.shopify_orders set refunded_amount = 0, is_rebill = false, source_name = case when order_date < date '2026-10-25' then 'web' else '322223865857' end")
        cur_days = [D(2026, 10, 25) + dt.timedelta(days=i) for i in range(7)]
        for d in (D(2026, 10, 18) + dt.timedelta(days=i) for i in range(14)):
            late = d >= D(2026, 10, 25)
            sp = (690 / 7, 3000 / 7) if late else (1760 / 7, 1000 / 7)
            rv = (1380 / 7, 1500 / 7) if late else (3500 / 7, 500 / 7)
            for k, s_, r_ in zip(keys, sp, rv):
                c.execute("insert into analytics.creative_family_daily values ('upstack_new_customers', 'all_ads', %s, 'vita-bull', %s, 'scaling', %s, %s, %s, %s, %s)", (k, d, s_, r_ / 60, r_, s_ * 2, s_ * 40))
            c.execute("insert into analytics.meta_bids_daily values (%s, 1000, %s, %s, 100, 90, now())", (d, 800 if late else 300, (800 * 80) if late else (300 * 95)))
            c.execute("insert into analytics.attribution_unmapped_daily values (%s, 'c', 'a', 'no_ad', %s, 0, 0, 0)", (d, 30 if late else 5))
            c.execute("insert into analytics.attribution_source_daily values (%s, 'Meta Ads', %s, 0, 0, 0, now()), (%s, 'Google Ads', %s, 0, 0, 0, now())", (d, 200, d, 20 if late else 5))
        # hourly: same weekday baseline front-loaded, the target day (10-31) back-loaded
        for k in range(0, 29, 7):
            d = D(2026, 10, 31) - dt.timedelta(days=k)
            for h in range(24):
                v = 5.0 if (k == 0 and h < 18) else (30.0 if k == 0 else 12.0)
                c.execute("insert into analytics.orders_hourly values (%s, %s, %s, %s, now())", (d, h, v * 60, v))
    return keys


def test_new_hypotheses_run_on_seeded_views_and_report_the_story(whydb):
    seed(whydb, Scenario(x_mode="new", with_shopify=True))
    _extend(whydb)
    out = W.run_why({"start": "2026-10-25", "end": "2026-10-31", "compare": "custom", "compare_start": "2026-10-18", "compare_end": "2026-10-24"}, make_runner(whydb, []), KN)
    inv = out["investigation"]
    led = {e["id"]: e for e in inv["ledger"]}
    for need in ("H1b", "H4b", "H14", "H16", "H17", "H18"):
        assert need in led and led[need]["verdict"] in X.VERDICTS and led[need]["evidence"], need
    assert "Thin" in led["H14"]["evidence"] and "share" in led["H14"]["evidence"] and "tags are today's" in led["H14"]["evidence"]
    assert led["H4b"]["verdict"] != "untestable" and "30%" in led["H4b"]["evidence"] and "80%" in led["H4b"]["evidence"]
    assert led["H1b"]["verdict"] != "untestable" and "no Meta ad" in led["H1b"]["evidence"]
    assert led["H16"]["verdict"] != "untestable" and ("back-loaded" in led["H16"]["evidence"] or "inside the band" in led["H16"]["evidence"])
    assert led["H17"]["verdict"] != "untestable" and "source_name" in led["H17"]["evidence"] and "untestable-meaning" in led["H17"]["evidence"]
    assert "payday" in led["H18"]["evidence"] and led["H18"]["verdict"] in ("consistent with", "ruled out", "inside noise", "untestable")
    assert "consistent with a calendar effect" in led["H18"]["evidence"] or led["H18"]["verdict"] != "consistent with"
    assert out["steps_used"] <= 60
    cmp_ = W.compact(out)
    assert {"H1b", "H4b", "H14", "H16", "H17", "H18"} <= {e["id"] for e in cmp_["ledger"]}
    assert all(len(e["evidence"]) <= 260 for e in cmp_["ledger"])
    assert "hook type" in " ".join(inv["untestable"])


def test_new_hypotheses_are_untestable_with_the_missing_data_named_when_views_are_absent(whydb):
    seed(whydb, Scenario(x_mode="new"))
    out = W.run_why({"start": "2026-10-29", "end": "2026-10-31", "compare": "prior_period"}, make_runner(whydb, []), KN)
    led = {e["id"]: e for e in out["investigation"]["ledger"]}
    for need, miss in (("H1b", "attribution"), ("H4b", "meta_bids_daily"), ("H14", "creative_family_daily"), ("H16", "orders_hourly"), ("H17", "shopify_orders")):
        assert led[need]["verdict"] == "untestable" and miss in led[need].get("missing", ""), (need, led[need])
    assert led["H18"]["verdict"] in X.VERDICTS
