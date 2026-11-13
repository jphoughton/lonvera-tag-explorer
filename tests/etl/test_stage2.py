import datetime as dt

import pytest

from etl import db as db_mod, backfill_sheet, build_metrics, meta_api, meta_pull, resolve_families, sheets_io, tracker_sync, run_daily
from conftest import trow, write_snapshot

D = dt.date


def row(day, ad_id, name, camp="c1", cname="Boneflex Scaling", spend=10.0, pur=1.0, val=20.0, clicks=5.0, acct="Lonvera Main", adset="as1", asname="Adset"):
    return {"day": D.fromisoformat(day), "ad_id": ad_id, "ad_name": name, "campaign_id": camp, "campaign_name": cname, "adset_id": adset, "adset_name": asname,
            "spend": spend, "purchases": pur, "value": val, "clicks": clicks, "account": acct}


def seed(conn, tmp_path):
    daily = [("2026-05-06", "seed", 1, 0, 0, 0), ("2026-10-30", "Fam A", 7, 0, 0, 3), ("2026-10-31", "Fam A", 8, 1, 9, 4)]
    snap = write_snapshot(tmp_path, [trow("A", "Fam A", "Boneflex", launch="5/6/2026")], daily, windows=[("Fam A", "Fam A")])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    conn.commit()
    return snap


def test_aggregate_famday_and_load_chunk_replaces_whole_days(conn, tmp_path):
    seed(conn, tmp_path)
    rows = [row("2026-10-30", "111", "Fam A - Copy"), row("2026-10-30", "222", "Fam A", spend=5, pur=0, val=0),
            row("2026-10-30", "111", "Fam A - Copy", spend=1, pur=0, val=0),          # same ad/day twice -> summed
            row("2026-10-31", "333", "Fam A", camp="c2", cname="Fam TEST campaign", spend=9, pur=2, val=40)]
    agg = meta_pull.aggregate(rows)
    assert len(agg) == 3 and agg[("111", D(2026, 10, 30))]["spend"] == 11.0
    fd = meta_pull.famday(agg)
    assert fd[(D(2026, 10, 30), "fam a")][0] == 16.0
    meta_pull.load_chunk(conn, agg, D(2026, 10, 30), D(2026, 10, 31))
    # the family-grain 'sheet' rows for 10-30 / 10-31 are gone, replaced by ad-grain rows
    assert conn.execute("SELECT count(*) FROM ad_daily WHERE source='sheet' AND day >= '2026-10-30'").fetchone()[0] == 0
    assert dict(conn.execute("SELECT ad_id, sum(spend) FROM ad_daily WHERE source='meta_api' GROUP BY 1").fetchall()) == {"111": 11, "222": 5, "333": 9}
    assert dict(conn.execute("SELECT campaign_id, stage FROM meta_campaigns").fetchall()) == {"c1": "scaling", "c2": "testing"}
    assert conn.execute("SELECT count(*) FROM meta_ads").fetchone()[0] >= 3
    # the 05-06 sheet day is untouched
    assert conn.execute("SELECT count(*) FROM ad_daily WHERE day='2026-05-06' AND source='sheet'").fetchone()[0] == 1
    # re-running the same chunk is idempotent
    before = conn.execute("SELECT count(*), sum(spend) FROM ad_daily").fetchone()
    meta_pull.load_chunk(conn, agg, D(2026, 10, 30), D(2026, 10, 31))
    assert conn.execute("SELECT count(*), sum(spend) FROM ad_daily").fetchone() == before


def test_stage_totals_after_api_load(conn, tmp_path):
    seed(conn, tmp_path)
    agg = meta_pull.aggregate([row("2026-10-30", "111", "Fam A", spend=6), row("2026-10-31", "333", "Fam A", camp="c2", cname="Fam TEST", spend=9, pur=2, val=40)])
    meta_pull.load_chunk(conn, agg, D(2026, 10, 30), D(2026, 10, 31))
    build_metrics.rebuild(conn)
    st = {(r[0].isoformat(), r[1]): float(r[2]) for r in conn.execute("SELECT day, stage, spend FROM family_daily WHERE family_key='fam a'").fetchall()}
    assert st == {("2026-10-30", "scaling"): 6.0, ("2026-10-31", "testing"): 9.0}


class FakeResp:
    def __init__(self, code, payload):
        self.status_code, self._p = code, payload

    def json(self):
        return self._p


def test_graph_get_retries_rate_limit_then_succeeds(monkeypatch):
    import requests
    seq = [FakeResp(400, {"error": {"code": 80004, "message": "There have been too many calls to this ad-account"}}),
           FakeResp(500, {"error": {"code": 2, "message": "temporary"}}),
           FakeResp(200, {"data": [1]})]
    monkeypatch.setattr(requests, "get", lambda *a, **k: seq.pop(0))
    sleeps = []
    out = meta_api.graph_get("http://x", {"access_token": "SECRET-TOKEN-12345"}, "test", sleep=sleeps.append)
    assert out == {"data": [1]} and len(sleeps) == 2 and sleeps[0] >= 60


def test_graph_get_too_big_and_fatal_and_token_scrub(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(400, {"error": {"code": 1, "message": "Please reduce the amount of data you're asking for"}}))
    with pytest.raises(meta_api.TooBig):
        meta_api.graph_get("http://x", {}, "t")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(400, {"error": {"code": 100, "message": "bad param access_token=EAAB1234567890abcdef"}}))
    with pytest.raises(meta_api.GraphError) as e:
        meta_api.graph_get("http://x", {}, "t")
    assert "EAAB1234567890abcdef" not in str(e.value)


def test_pull_chunk_splits_when_too_big(monkeypatch):
    calls = []

    def fake_paged(url, params, what, pace=0):
        import json
        tr = json.loads(params["time_range"])
        calls.append((tr["since"], tr["until"]))
        if tr["since"] != tr["until"]:
            raise meta_api.TooBig("reduce")
        return [{"day": D.fromisoformat(tr["since"]), "ad_id": "1", "ad_name": "x", "campaign_id": "", "campaign_name": "", "adset_id": "", "adset_name": "",
                 "spend": 1.0, "purchases": 0, "value": 0, "clicks": 0}]
    monkeypatch.setattr(meta_api, "_paged", fake_paged)
    rows = meta_api.pull_chunk("tok", "act_1", "Acct", D(2026, 10, 1), D(2026, 10, 4))
    assert sorted(r["day"].isoformat() for r in rows) == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert all(r["account"] == "Acct" for r in rows)


def test_date_chunks():
    ch = meta_api.date_chunks(D(2026, 5, 6), D(2026, 11, 2))
    assert ch[0] == (D(2026, 5, 6), D(2026, 5, 19)) and ch[-1][1] == D(2026, 11, 2) and len(ch) == 13
    assert ch[1][0] == D(2026, 5, 20)


def test_resolve_families_new_and_retry(conn, tmp_path):
    trs = [trow("Bull-9", "Bull-9", "VitaBull"), trow("Lonvera-VitalBull-Video-1.1", "Lonvera-VitalBull-Video-1.1", "VitaBull")]
    daily = [("2026-05-06", "x", 1, 0, 0, 0), ("2026-10-30", "Bull-9", 5, 0, 0, 0), ("2026-10-30", "Bull-9_VID_new", 4, 0, 0, 0),
             ("2026-10-30", "Lonvera-VitalBull-Video-1.1-hook7", 3, 0, 0, 0), ("2026-10-30", "mystery ad", 2, 0, 0, 0)]
    snap = write_snapshot(tmp_path, trs, daily, windows=[])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    assert conn.execute("SELECT count(*) FROM meta_family_map WHERE tracker_family_key IS NOT NULL").fetchone()[0] == 0     # 'sheet' mode: no resolver
    st = resolve_families.resolve(conn)
    got = dict(conn.execute("SELECT meta_family_key, rule FROM meta_family_map WHERE tracker_family_key IS NOT NULL").fetchall())
    assert got == {"bull-9": "exact", "bull-9_vid_new": "relaunch", "lonvera-vitalbull-video-1.1-hook7": "prefix"}
    assert st["still_unmatched"] == 2                                                                                         # 'x' and 'mystery ad'
    assert resolve_families.resolve(conn)["newly_matched"] == 0                                                              # idempotent


def test_meta_pull_stores_impressions(conn, tmp_path):
    seed(conn, tmp_path)
    rows = [dict(row("2026-10-30", "111", "Fam A"), impressions=1500.0), dict(row("2026-10-30", "222", "Fam A"), impressions=500.0)]
    agg = meta_pull.aggregate(rows)
    meta_pull.load_chunk(conn, agg, D(2026, 10, 30), D(2026, 10, 30))
    assert float(conn.execute("SELECT sum(impressions) FROM ad_daily WHERE day='2026-10-30' AND source='meta_api'").fetchone()[0]) == 2000.0
    # sheet rows keep NULL impressions
    assert conn.execute("SELECT impressions FROM ad_daily WHERE source='sheet' LIMIT 1").fetchone()[0] is None


def test_insight_rows_carry_impressions():
    r = meta_api.rows_from([{"ad_id": 1, "ad_name": "x", "date_start": "2026-10-30", "spend": "3", "impressions": "250", "inline_link_clicks": "4",
                             "actions": [{"action_type": "omni_purchase", "value": "2"}], "action_values": [{"action_type": "omni_purchase", "value": "55.5"}]}])[0]
    assert r["impressions"] == 250.0 and r["clicks"] == 4.0 and r["purchases"] == 2.0 and r["value"] == 55.5
    assert "impressions" in meta_api.INSIGHT_FIELDS.split(",")


def test_resolve_rebuild_replaces_sheet_mapping_with_resolver(conn, tmp_path):
    trs = [trow("Bull-9", "Bull-9", "VitaBull"), trow("Other", "Other", "VitaBull")]
    daily = [("2026-05-06", "x", 1, 0, 0, 0), ("2026-10-30", "Bull-9", 5, 0, 0, 0), ("2026-10-30", "Bull-9_VID_new", 4, 0, 0, 0)]
    snap = write_snapshot(tmp_path, trs, daily, windows=[("Bull-9", "Other")])        # the sheet's Windows mapping is deliberately "wrong"
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    assert conn.execute("SELECT tracker_family_key FROM meta_family_map WHERE meta_family_key='bull-9'").fetchone()[0] == "other"
    resolve_families.resolve(conn, rebuild=True)
    got = dict(conn.execute("SELECT meta_family_key, rule FROM meta_family_map WHERE tracker_family_key IS NOT NULL").fetchall())
    assert got == {"bull-9": "exact", "bull-9_vid_new": "relaunch"}


def test_lifetime_load_replaces_everything_and_reconcile_flags(conn, tmp_path):
    from etl import lifetime_pull
    seed(conn, tmp_path)
    conn.execute("INSERT INTO meta_ad_lifetime (ad_id, meta_family_key, spend, source) VALUES ('old','fam a',5,'sheet')")
    rows = [dict(row("2026-10-30", "111", "Fam A - Copy", spend=60.0, pur=3, val=90.0, clicks=30.0), impressions=3000.0),
            dict(row("2026-10-30", "222", "Fam B", spend=40.0, pur=1, val=10.0, clicks=10.0), impressions=1000.0)]
    agg = lifetime_pull.aggregate(rows)
    lifetime_pull.load(conn, agg)
    got = {r[0]: (r[1], float(r[2]), r[3]) for r in conn.execute("SELECT ad_id, meta_family_key, spend, source FROM meta_ad_lifetime").fetchall()}
    assert got == {"111": ("fam a", 60.0, "meta_api"), "222": ("fam b", 40.0, "meta_api")}          # sheet-era row gone
    assert conn.execute("SELECT count(*) FROM meta_ads WHERE ad_id IN ('111','222')").fetchone()[0] == 2


def test_verify_api_sql_checks_pass_on_consistent_data(conn, tmp_path):
    from etl import verify_api
    snap = seed(conn, tmp_path)
    conn.execute("INSERT INTO meta_ad_lifetime (ad_id, meta_family_key, spend, purchases, conv_value, impressions, link_clicks, source, pulled_at) "
                 "VALUES ('1','fam a',100,5,200,1000,50,'meta_api',now())")
    conn.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key) VALUES ('1','Lonvera Main','Fam A','fam a') ON CONFLICT DO NOTHING")
    conn.commit()
    build_metrics.rebuild(conn)
    ck = verify_api.Checks()
    verify_api.check_consistency(conn, ck)
    assert not ck.failed, ck.rows


def test_funnel_actions_picked_like_purchases():
    from etl.resolver import pick_atc, pick_lpv
    assert pick_atc([{"action_type": "omni_add_to_cart", "value": "9"}, {"action_type": "add_to_cart", "value": "7"}]) == 9.0
    assert pick_atc([{"action_type": "add_to_cart", "value": "7"}, {"action_type": "offsite_conversion.fb_pixel_add_to_cart", "value": "5"}]) == 7.0
    assert pick_atc([{"action_type": "offsite_conversion.fb_pixel_add_to_cart", "value": "5"}]) == 5.0
    assert pick_atc(None) == 0.0 and pick_lpv([]) == 0.0
    assert pick_lpv([{"action_type": "landing_page_view", "value": "8"}, {"action_type": "omni_landing_page_view", "value": "10"}]) == 10.0
    assert pick_lpv([{"action_type": "landing_page_view", "value": "8"}]) == 8.0
    r = meta_api.rows_from([{"ad_id": 1, "ad_name": "x", "date_start": "2026-10-30", "spend": "3", "inline_link_clicks": "4",
                             "actions": [{"action_type": "link_click", "value": "4"}, {"action_type": "landing_page_view", "value": "3"},
                                         {"action_type": "omni_add_to_cart", "value": "2"}]}])[0]
    assert r["lpv"] == 3.0 and r["atc"] == 2.0 and r["clicks"] == 4.0


def test_load_chunk_stores_funnel_and_view_exposes_it(conn, tmp_path):
    seed(conn, tmp_path)
    rows = [dict(row("2026-10-30", "111", "Fam A"), lpv=8.0, atc=3.0), dict(row("2026-10-30", "111", "Fam A"), lpv=2.0, atc=1.0)]
    meta_pull.load_chunk(conn, meta_pull.aggregate(rows), D(2026, 10, 30), D(2026, 10, 30))
    assert conn.execute("SELECT landing_page_views, add_to_carts FROM ad_daily WHERE ad_id='111'").fetchone() == (10, 4)
    assert conn.execute("SELECT landing_page_views, add_to_carts FROM analytics.meta_ad_daily WHERE ad_id='111' AND day='2026-10-30'").fetchone() == (10, 4)
    # legacy family-grain sheet rows read NULL (not 0): never pulled
    assert conn.execute("SELECT landing_page_views FROM analytics.meta_ad_daily WHERE source='sheet' LIMIT 1").fetchone()[0] is None


def test_fill_funnel_updates_only_the_two_columns_and_resumes(conn, tmp_path, monkeypatch):
    seed(conn, tmp_path)
    meta_pull.load_chunk(conn, meta_pull.aggregate([row("2026-10-30", "111", "Fam A", spend=10, clicks=5), row("2026-10-31", "111", "Fam A", spend=20, clicks=7)]),
                         D(2026, 10, 30), D(2026, 10, 31))
    conn.execute("UPDATE ad_daily SET landing_page_views = NULL, add_to_carts = NULL")        # as history looks before the backfill
    conn.commit()
    before = conn.execute("SELECT ad_id, day, spend, purchases, conv_value, link_clicks, impressions FROM ad_daily WHERE source='meta_api' ORDER BY 1, 2").fetchall()
    calls = []

    def fake_chunk(tok, acct, an, since, until):
        calls.append((an, since, until))
        if an != "Lonvera Main":
            return []
        return [dict(row("2026-10-30", "111", "Fam A", spend=10.2, clicks=5.0), lpv=4.0, atc=2.0), dict(row("2026-10-31", "111", "Fam A", spend=20, clicks=7.0), lpv=6.0, atc=3.0),
                dict(row("2026-10-31", "999", "Other ad", spend=1, clicks=1.0), lpv=1.0, atc=1.0)]
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    monkeypatch.setattr(meta_api, "pull_chunk", fake_chunk)
    res = meta_pull.fill_funnel(conn, D(2026, 10, 30), D(2026, 10, 31), chunk_days=7, pause=0, sleep=lambda s: None)
    assert res["done"] == 1 and res["updated"] == 2 and res["pulled_ad_days_without_row"] == 1 and not res["failed"]
    assert res["per_chunk"][0]["spend_diff_pct"] != 0
    assert conn.execute("SELECT ad_id, day, landing_page_views, add_to_carts FROM ad_daily WHERE source='meta_api' ORDER BY 2").fetchall() == [("111", D(2026, 10, 30), 4, 2), ("111", D(2026, 10, 31), 6, 3)]
    after = conn.execute("SELECT ad_id, day, spend, purchases, conv_value, link_clicks, impressions FROM ad_daily WHERE source='meta_api' ORDER BY 1, 2").fetchall()
    assert after == before                                              # nothing but the two new columns changed
    n = len(calls)
    res2 = meta_pull.fill_funnel(conn, D(2026, 10, 30), D(2026, 10, 31), chunk_days=7, pause=0, sleep=lambda s: None)
    assert res2["skipped"] == 1 and len(calls) == n                     # resume: already filled, no Meta call


def test_fill_funnel_reports_failed_chunk_after_retries(conn, tmp_path, monkeypatch):
    seed(conn, tmp_path)
    meta_pull.load_chunk(conn, meta_pull.aggregate([row("2026-10-30", "111", "Fam A")]), D(2026, 10, 30), D(2026, 10, 30))
    conn.execute("UPDATE ad_daily SET landing_page_views = NULL, add_to_carts = NULL")
    conn.commit()
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")

    def boom(*a):
        raise meta_api.RateLimited("80004")
    monkeypatch.setattr(meta_api, "pull_chunk", boom)
    waits = []
    res = meta_pull.fill_funnel(conn, D(2026, 10, 30), D(2026, 10, 30), chunk_days=7, pause=0, attempts=2, sleep=waits.append)
    assert res["failed"] == ["2026-10-30..2026-10-30"] and waits == [120, 240]
    assert conn.execute("SELECT landing_page_views FROM ad_daily WHERE ad_id='111'").fetchone()[0] is None
