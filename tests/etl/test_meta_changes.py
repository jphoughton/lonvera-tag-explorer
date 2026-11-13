"""etl.meta_changes: activity-log parsing (budgets in dollars, ROAS floor as a ratio), dedupe, backfill chunks, the ad set snapshot, the ledger view that diffs consecutive days,
read-only (GET only) and no token in any error text."""
import datetime as dt
import json

import pytest

from etl import db as db_mod, meta_api, meta_changes as mc

D = dt.date
ACCTS = [("act_1", "Lonvera Main"), ("act_2", "Lonvera BackUp")]


def ev(et, otype, oid, name, extra, when="2026-11-10T20:30:00+0000", actor="Rodolfo"):
    return {"event_type": et, "object_type": otype, "object_id": oid, "object_name": name, "extra_data": json.dumps(extra) if not isinstance(extra, str) else extra,
            "event_time": when, "actor_id": "9", "actor_name": actor, "translated_event_type": et.replace("_", " ")}


BUDGET = ev("update_ad_set_budget", "CAMPAIGN", "as1", "Boneflex Testing", {"old_value": {"type": "payment_amount", "currency": "USD", "old_value": 50000, "additional_type": "status_string", "additional_value": ""},
                                                                             "new_value": {"type": "payment_amount", "currency": "USD", "new_value": 60000}, "type": "composite_data"})
BID = ev("update_ad_set_bid_strategy", "CAMPAIGN", "as2", "VB Scaling", {"old_value": "COST_CAP", "new_value": "COST_CAP", "additional_value": {"old_value": 6800, "new_value": 8000, "currency": "USD", "type": "payment_amount"},
                                                                       "adset_minimum_return_on_ad_spend": {"old_value": 9000, "new_value": 10500}, "type": "bid_type"})
STATUS = ev("update_ad_set_run_status", "CAMPAIGN", "as3", "BL Scaling", {"old_value": "Active", "new_value": "Inactive", "run_status": {"old_value": 1, "new_value": 7}, "type": "run_status", "campaign_id": 77})
CBUDGET = ev("update_campaign_budget", "CAMPAIGN_GROUP", "c9", "BL CBO", {"old_value": {"type": "payment_amount", "old_value": 987600}, "new_value": {"type": "payment_amount", "new_value": 1323400}})
TARGET = ev("update_ad_set_target_spec", "CAMPAIGN", "as1", "Boneflex Testing", {"old_value": [{"content": "Location:", "children": ["United States"]}, {"content": "Age:", "children": ["18 - 65+"]}], "new_value": "plain text"})
IMG = ev("add_images", "ACCOUNT", "1671338033619223", "Lonvera Main", {"image_url": "https://scontent.example/x.jpg?sig=abc"})


def test_parse_event_budget_bid_floor_status_and_levels():
    b = mc.parse_event("Lonvera Main", BUDGET)
    assert (b["object_level"], b["old_amount"], b["new_amount"], b["old_value"], b["new_value"]) == ("adset", 500.0, 600.0, None, None)
    assert b["event_day"] == D(2026, 11, 10) and b["raw"]["type"] == "composite_data"
    g = mc.parse_event("Lonvera Main", BID)
    assert (g["old_value"], g["new_value"], g["old_amount"], g["new_amount"], g["old_roas_floor"], g["new_roas_floor"]) == ("COST_CAP", "COST_CAP", 68.0, 80.0, 0.9, 1.05)
    s = mc.parse_event("Lonvera Main", STATUS)
    assert (s["old_value"], s["new_value"], s["campaign_id"], s["old_amount"]) == ("Active", "Inactive", "77", None)
    c = mc.parse_event("Lonvera Main", CBUDGET)
    assert (c["object_level"], c["campaign_id"], c["old_amount"], c["new_amount"]) == ("campaign", "c9", 9876.0, 13234.0)
    t = mc.parse_event("Lonvera Main", TARGET)
    assert t["old_value"] == "Location: United States | Age: 18 - 65+" and t["new_value"] == "plain text"
    i = mc.parse_event("Lonvera Main", IMG)
    assert i["raw"] == {} and i["object_level"] == "account"
    # an odd extra_data never raises and stays in raw
    o = mc.parse_event("Lonvera Main", ev("something_new", "WEIRD", "1", "x", "not json"))
    assert o["old_value"] is None and o["raw"] == {"_unparsed": "not json"} and o["object_level"] == "weird"
    # midnight Pacific: 2026-11-11T07:30Z is Nov 10 23:30 PT
    assert mc.parse_event("Lonvera Main", ev("x", "CAMPAIGN", "1", "n", {}, when="2026-11-11T07:30:00+0000"))["event_day"] == D(2026, 11, 10)


def test_key_is_stable_and_account_specific():
    a, b = mc.parse_event("Lonvera Main", BUDGET), mc.parse_event("Lonvera Main", dict(BUDGET))
    assert a["event_key"] == b["event_key"] != mc.parse_event("Lonvera BackUp", BUDGET)["event_key"]


@pytest.fixture
def clean(conn):
    conn.execute("TRUNCATE meta_change_log, meta_adset_state_daily")
    conn.commit()
    return conn


def test_load_log_dedupes_and_overlap_rerun_adds_nothing(clean):
    rows = [mc.parse_event("Lonvera Main", e) for e in (BUDGET, BID, STATUS)]
    assert mc.load_log(clean, rows) == 3
    assert mc.load_log(clean, rows + [mc.parse_event("Lonvera Main", CBUDGET)]) == 1
    assert clean.execute("SELECT count(*) FROM meta_change_log").fetchone()[0] == 4
    assert clean.execute("SELECT old_amount, new_amount FROM analytics.meta_change_log WHERE event_type='update_ad_set_budget'").fetchone() == (500, 600)


def test_run_log_chunks_a_backfill_pulls_both_accounts_and_is_get_only(clean, monkeypatch):
    seen = []

    def fake_get(url, params, what, **kw):
        seen.append((url, dict(params or {}), what))
        if "act_2" in url:
            return {"data": []}
        if params and params.get("since") == "2026-11-01":
            return {"data": [BUDGET], "paging": {"next": "https://graph.example/next"}}
        if url == "https://graph.example/next":
            return {"data": [BID]}
        return {"data": []}
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    res = mc.run_log(clean, "tok", D(2026, 11, 1), D(2026, 11, 14), chunk_days=7, accounts=ACCTS)
    assert res["chunks"] == 2 and res["pulled"] == 2 and res["inserted"] == 2 and res["by_account"]["Lonvera Main"] == 2
    first = [p for u, p, w in seen if u.endswith("/act_1/activities")]
    assert [(p["since"], p["until"]) for p in first] == [("2026-11-01", "2026-11-08"), ("2026-11-08", "2026-11-15")]     # Meta's until is exclusive: one day added
    assert all(p["fields"].startswith("event_type") and p["limit"] == 500 for p in first)
    assert mc.run_log(clean, "tok", D(2026, 11, 1), D(2026, 11, 14), accounts=ACCTS)["inserted"] == 0                      # resumable / idempotent


def test_dry_run_writes_nothing(clean, monkeypatch):
    monkeypatch.setattr(meta_api, "graph_get", lambda *a, **k: {"data": [BUDGET]})
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    res = mc.run_log(clean, "tok", D(2026, 11, 10), D(2026, 11, 10), dry_run=True, accounts=ACCTS)
    assert res["pulled"] == 2 and res["inserted"] == 0
    assert clean.execute("SELECT count(*) FROM meta_change_log").fetchone()[0] == 0


def adset(i, **kw):
    a = {"id": i, "name": "AS " + i, "campaign_id": "c1", "status": "ACTIVE", "effective_status": "ACTIVE", "daily_budget": "50000", "bid_strategy": "COST_CAP", "bid_amount": "6800",
         "bid_constraints": {"roas_average_floor": 9000}, "optimization_goal": "VALUE", "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 1}],
         "created_time": "2026-10-01T10:00:00-0700", "updated_time": "2026-11-01T10:00:00-0700"}
    a.update(kw)
    return a


def snap(conn, monkeypatch, day, sets_main, sets_backup=()):
    monkeypatch.setattr(mc, "pull_adsets", lambda tok, acct, an, pace=0.3: list(sets_main if acct == "act_1" else sets_backup))
    return mc.run_snapshot(conn, "tok", day, accounts=ACCTS)


def ledger(conn):
    return sorted((str(r[0]), r[1], r[2], r[3], r[4], None if r[5] is None else float(r[5])) for r in conn.execute("SELECT day, adset_id, change_type, old_value, new_value, new_number FROM analytics.meta_change_ledger"))


def test_snapshot_units_archived_pruning_and_same_day_rerun(clean, monkeypatch):
    old_arch = adset("dead", status="ARCHIVED", effective_status="ARCHIVED", updated_time="2026-09-01T10:00:00-0700")
    new_arch = adset("fresh", status="ARCHIVED", effective_status="ARCHIVED", updated_time="2026-11-11T10:00:00-0700")
    res = snap(clean, monkeypatch, D(2026, 11, 12), [adset("a1"), old_arch, new_arch], [adset("b1", bid_strategy=None, bid_amount=None, bid_constraints=None)])
    assert res["adsets"] == 3 and res["by_account"] == {"Lonvera Main": 2, "Lonvera BackUp": 1}
    r = clean.execute("SELECT daily_budget, bid_amount, roas_average_floor, attribution_spec FROM meta_adset_state_daily WHERE adset_id='a1'").fetchone()
    assert (float(r[0]), float(r[1]), float(r[2])) == (500.0, 68.0, 0.9) and r[3][0]["window_days"] == 1
    assert clean.execute("SELECT bid_amount, roas_average_floor FROM meta_adset_state_daily WHERE adset_id='b1'").fetchone() == (None, None)
    snap(clean, monkeypatch, D(2026, 11, 12), [adset("a1", daily_budget="70000")])                        # same day again: replaced, not duplicated
    assert clean.execute("SELECT count(*), max(daily_budget) FROM meta_adset_state_daily WHERE adset_id='a1'").fetchone() == (1, 700)


def test_ledger_diffs_consecutive_days_into_change_events(clean, monkeypatch):
    snap(clean, monkeypatch, D(2026, 11, 10), [adset("a"), adset("b"), adset("c"), adset("d")])
    assert ledger(clean) == []                                                    # the first snapshot is the baseline: no events, nothing is 'new'
    snap(clean, monkeypatch, D(2026, 11, 11), [
        adset("a", daily_budget="60000", bid_amount="8000", bid_constraints={"roas_average_floor": 10500}),
        adset("b", status="PAUSED", effective_status="PAUSED"),
        adset("c", bid_strategy="LOWEST_COST_WITHOUT_CAP", bid_amount=None),
        adset("d", effective_status="CAMPAIGN_PAUSED"),
        adset("e", created_time="2026-11-11T09:00:00-0800", effective_status="IN_PROCESS"),                    # created inside the gap: new
        adset("old", created_time="2026-06-01T09:00:00-0700", status="PAUSED", effective_status="PAUSED"),     # appears for the first time but is old: not new
    ])
    got = ledger(clean)
    assert ("2026-11-11", "a", "daily_budget", "500.00", "600.00", 600) in got
    assert ("2026-11-11", "a", "bid_amount", "68.00", "80.00", 80) in got
    assert ("2026-11-11", "a", "roas_floor", "0.9000", "1.0500", 1.05) in got
    assert ("2026-11-11", "b", "paused", "ACTIVE", "PAUSED", None) in got and ("2026-11-11", "b", "effective_status", "ACTIVE", "PAUSED", None) in got
    assert ("2026-11-11", "c", "bid_strategy", "COST_CAP", "LOWEST_COST_WITHOUT_CAP", None) in got and ("2026-11-11", "c", "bid_amount", "68.00", None, None) in got
    assert ("2026-11-11", "d", "effective_status", "ACTIVE", "CAMPAIGN_PAUSED", None) in got and not any(g[1] == "d" and g[2] == "paused" for g in got)
    assert ("2026-11-11", "e", "new_adset", None, "IN_PROCESS", None) in got
    assert not any(g[1] == "old" for g in got)
    snap(clean, monkeypatch, D(2026, 11, 12), [adset("a", daily_budget="60000", bid_amount="8000", bid_constraints={"roas_average_floor": 10500}), adset("b", status="ACTIVE", effective_status="ACTIVE")])
    later = [g for g in ledger(clean) if g[0] == "2026-11-12"]
    assert later == [("2026-11-12", "b", "activated", "PAUSED", "ACTIVE", None), ("2026-11-12", "b", "effective_status", "PAUSED", "ACTIVE", None)]     # a unchanged: nothing; c, d, e not in the snapshot: nothing
    # the ledger carries prev_day, so a missed night shows as a longer gap
    snap(clean, monkeypatch, D(2026, 11, 15), [adset("b", status="ACTIVE", effective_status="ACTIVE", daily_budget="90000")])
    assert clean.execute("SELECT prev_day, new_number FROM analytics.meta_change_ledger WHERE day='2026-11-15' AND change_type='daily_budget'").fetchone() == (D(2026, 11, 12), 900)


def test_main_runs_snapshot_even_when_the_log_fails_then_fails_the_job_loudly(clean, monkeypatch):
    def boom(*a, **k):
        raise meta_api.GraphError("activities: HTTP 400 code 200: (#200) permission missing EAAB" + "x" * 50)
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    monkeypatch.setattr(meta_api, "ACCOUNTS", ACCTS)
    monkeypatch.setattr(mc, "pull_activities", boom)
    monkeypatch.setattr(mc, "pull_adsets", lambda tok, acct, an, pace=0.3: [adset("a")] if acct == "act_1" else [])
    with pytest.raises(db_mod.JobFailed) as e:
        mc.main(["--since", "2026-11-01", "--until", "2026-11-03"])                                              # older than today - 2: data is missing, so the job fails
    assert "activity log" in str(e.value) and "EAAB" not in db_mod.scrub(e.value)
    assert clean.execute("SELECT count(*) FROM meta_adset_state_daily").fetchone()[0] == 1                       # the snapshot still landed
    r = clean.execute("SELECT status, error FROM etl_runs WHERE job='meta_changes' ORDER BY id DESC LIMIT 1").fetchone()
    assert r[0] == "failed" and "EAAB" not in r[1]


def test_module_makes_no_write_calls_to_meta():
    src = open(mc.__file__).read()
    assert "requests.post" not in src and "requests.delete" not in src and "method=\"POST\"" not in src


def test_a_window_meta_calls_too_big_is_split_down_to_days(monkeypatch):
    calls = []

    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": [ev("create_ad", "ADGROUP", "x2", "ad", {})]}
        calls.append((params["since"], params["until"], params["limit"]))
        if (dt.date.fromisoformat(params["until"]) - dt.date.fromisoformat(params["since"])).days > 1:
            raise meta_api.TooBig("An unknown error occurred")
        if params["limit"] == 500 and params["since"] == "2026-05-08":                       # one day that only works with smaller pages
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", params["since"], "ad", {})], "paging": {"next": "https://graph.example/n"}} if params["limit"] == 100 and params["since"] == "2026-05-08" else {"data": [ev("create_ad", "ADGROUP", params["since"], "ad", {})]}
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    out = mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 5, 6), D(2026, 5, 9))
    days = sorted({c[0] for c in calls if (dt.date.fromisoformat(c[1]) - dt.date.fromisoformat(c[0])).days == 1})
    assert days == ["2026-05-06", "2026-05-07", "2026-05-08", "2026-05-09"]
    assert ("2026-05-08", "2026-05-09", 100) in calls and len(out) == 5                     # 4 days + the second page of the day that needed smaller pages


def test_a_single_day_that_never_works_raises(monkeypatch):
    monkeypatch.setattr(meta_api, "graph_get", lambda *a, **k: (_ for _ in ()).throw(meta_api.TooBig("An unknown error occurred")))
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    with pytest.raises(meta_api.TooBig):
        mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 5, 6), D(2026, 5, 6))


# ---- resilience: transient errors, split, per-day independence, self-heal
def _days(params):
    return (dt.date.fromisoformat(params["until"]) - dt.date.fromisoformat(params["since"])).days


def test_one_unknown_error_is_retried_and_the_window_loads(monkeypatch):
    state = {"n": 0}

    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": []}
        state["n"] += 1
        if state["n"] == 1:
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", "r1", "ad", {})]}
    sleeps = []
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: sleeps.append(s))
    fails = []
    out = mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 11), D(2026, 11, 13), failures=fails)
    assert len(out) == 1 and fails == [] and state["n"] == 2 and any(s >= 3 for s in sleeps)               # one backoff sleep happened, no split needed


def test_backoff_is_exponential_with_jitter(monkeypatch):
    monkeypatch.setattr(mc.random, "random", lambda: 0.0)
    assert [round(mc._backoff(i), 2) for i in range(3)] == [3.75, 7.5, 15.0]
    monkeypatch.setattr(mc.random, "random", lambda: 1.0)
    assert round(mc._backoff(0), 2) == 6.25 and mc._backoff(10) <= 150


def test_a_week_that_always_fails_but_single_days_work_is_split_and_loads_every_day(monkeypatch):
    calls = []

    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": []}
        calls.append((params["since"], _days(params)))
        if _days(params) > 1:
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", params["since"], "ad", {})]}
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    fails = []
    out = mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 7), D(2026, 11, 13), failures=fails)
    assert fails == [] and len(out) == 7
    assert sum(1 for c in calls if c[0] == "2026-11-07" and c[1] > 1) >= 2                                   # the week was retried before it was split


def test_a_day_that_never_loads_is_recorded_and_the_other_days_still_load(monkeypatch):
    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": []}
        if params["since"] == "2026-11-09":
            raise meta_api.TooBig("An unknown error occurred")
        if _days(params) > 1:
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", params["since"], "ad", {})]}
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    fails = []
    out = mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 7), D(2026, 11, 11), failures=fails)
    assert len(out) == 4 and [(f[0], f[1]) for f in fails] == [(D(2026, 11, 9), D(2026, 11, 9))]
    with pytest.raises(meta_api.TooBig):                                                                       # without a failures list the old contract holds
        mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 9), D(2026, 11, 9))


def test_rate_limit_is_not_split_and_a_token_rejection_propagates(monkeypatch):
    seen = []

    def rl(url, params, what, **kw):
        seen.append(params and params["since"])
        raise meta_api.RateLimited("too many calls")
    monkeypatch.setattr(meta_api, "graph_get", rl)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    fails = []
    assert mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 7), D(2026, 11, 13), failures=fails) == [] and len(fails) == 1 and len(seen) == 1
    monkeypatch.setattr(meta_api, "graph_get", lambda *a, **k: (_ for _ in ()).throw(meta_api.TokenRejected("x")))
    with pytest.raises(meta_api.TokenRejected):
        mc.pull_activities("tok", "act_1", "Lonvera Main", D(2026, 11, 7), D(2026, 11, 13), failures=[])


def test_one_account_failing_does_not_lose_the_other_or_the_snapshot(clean, monkeypatch):
    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": []}
        if "act_1/" in url:
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", "b" + params["since"], "ad", {})]}
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    monkeypatch.setattr(meta_api, "ACCOUNTS", ACCTS)
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    monkeypatch.setattr(mc, "pull_adsets", lambda tok, acct, an, pace=0.3: [adset("a")] if acct == "act_1" else [adset("b")])
    today = dt.datetime.now(mc.PT).date()
    assert mc.main(["--since", (today - dt.timedelta(days=1)).isoformat()]) == 0                               # failures are only within the last 2 days: the next run re-pulls them
    r = clean.execute("SELECT status, error FROM etl_runs WHERE job='meta_changes' ORDER BY id DESC LIMIT 1").fetchone()
    assert r[0] == "ok" and "next run re-pulls" in r[1]                                                        # recorded in etl_runs.error
    assert clean.execute("SELECT count(*) FROM meta_change_log WHERE account=%s", (ACCTS[1][1],)).fetchone()[0] >= 1
    assert clean.execute("SELECT count(*) FROM meta_adset_state_daily WHERE day=%s", (today,)).fetchone()[0] == 2


def test_missing_old_days_fail_the_job_and_the_next_night_heals_them(clean, monkeypatch):
    today = dt.datetime.now(mc.PT).date()
    old = today - dt.timedelta(days=5)
    bad = {"on": True}

    def fake_get(url, params, what, **kw):
        if params is None:
            return {"data": []}
        if bad["on"] and params["since"] == old.isoformat():
            raise meta_api.TooBig("An unknown error occurred")
        return {"data": [ev("create_ad", "ADGROUP", "d" + params["since"], "ad", {}, when=params["since"] + "T20:30:00+0000")]}
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    monkeypatch.setattr(meta_api, "ACCOUNTS", ACCTS[:1])
    monkeypatch.setattr(meta_api, "graph_get", fake_get)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    monkeypatch.setattr(mc, "pull_adsets", lambda tok, acct, an, pace=0.3: [adset("a")])
    with pytest.raises(db_mod.JobFailed) as e1:
        mc.main(["--since", old.isoformat(), "--until", old.isoformat(), "--no-snapshot"])
    assert "older than 2 days" in str(e1.value)
    bad["on"] = False
    assert mc.main(["--days", "3", "--no-snapshot"]) == 0                                                      # nightly run: overlap + heal of every empty day in the last 7
    n = clean.execute("SELECT count(*) FROM meta_change_log WHERE event_day=%s", (old,)).fetchone()[0]
    assert n >= 1
