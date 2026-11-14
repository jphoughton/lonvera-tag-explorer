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
        rows = c.execute("select event_day, kind, actor, objects_affected, old_value, new_value, array_length(object_ids, 1) from analytics.change_timeline where source = 'meta_activity_log' order by event_time").fetchall()
        cols = {r[0] for r in c.execute("select column_name from information_schema.columns where table_schema = 'analytics' and table_name = 'change_timeline'")}
    assert [(r[3], r[6]) for r in rows] == [(89, 89), (91, 91)]                      # the system event is excluded, each bulk edit is one row
    assert rows[0][2] == "Rodolfo" and rows[0][4] == "45.00 to 64.00" and rows[0][5] == "80.00" and rows[1][5] == "68.00"
    assert {"event_time", "event_day", "source", "kind", "scope", "object_ids", "object_names", "old_value", "new_value", "magnitude", "actor", "objects_affected", "prior_day_spend_share"} <= cols


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
                  "magnitude numeric, actor text, objects_affected int, last_event_time timestamptz, prior_day_spend_share numeric)")
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
