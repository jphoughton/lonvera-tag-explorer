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


def test_sheet_fallback_refreshes_trailing_days_only(conn, tmp_path):
    snap = seed(conn, tmp_path)
    conn.execute("UPDATE ad_daily SET spend = 999 WHERE day = '2026-10-31'")
    conn.commit()
    out = run_daily.refresh_from_sheet(conn, str(snap), days=2)
    assert out["from"] == "2026-10-30" and out["to"] == "2026-10-31" and out["rows"] == 2
    assert float(conn.execute("SELECT spend FROM ad_daily WHERE day='2026-10-31'").fetchone()[0]) == 8.0


def test_run_daily_sheet_source_end_to_end(conn, tmp_path):
    snap = seed(conn, tmp_path)
    res = run_daily.run(days=2, source="sheet", snapshot_dir=str(snap))
    assert res["tracker_sync"]["ok"] and res["meta"]["ok"] and res["resolve"]["ok"] and res["build_metrics"]["ok"]
    assert res["build_metrics"]["data_through"] == "2026-10-31"
    jobs = [r[0] for r in conn.execute("SELECT job FROM etl_runs WHERE status='ok' ORDER BY id").fetchall()]
    assert {"tracker_sync", "build_metrics", "run_daily"} <= set(jobs)


def test_run_daily_token_rejected_falls_back_but_fails_loudly(conn, tmp_path, monkeypatch):
    snap = seed(conn, tmp_path)

    def rejected(*a, **k):
        raise meta_api.TokenRejected("Meta rejected the access token (code 190)")
    monkeypatch.setattr(meta_pull, "pull_into_db", rejected)
    with pytest.raises(db_mod.JobFailed, match="meta token rejected"):
        run_daily.run(days=2, source="auto", snapshot_dir=str(snap))
    # data was still refreshed from the sheet and metrics rebuilt
    assert conn.execute("SELECT value FROM app_meta WHERE key='data_through'").fetchone()[0] == "2026-10-31"
    top = conn.execute("SELECT status, error FROM etl_runs WHERE job='run_daily' ORDER BY id DESC LIMIT 1").fetchone()
    assert top == ("failed", "meta token rejected")
    mp = conn.execute("SELECT status, error FROM etl_runs WHERE job='meta_pull' ORDER BY id DESC LIMIT 1").fetchone()
    assert mp == ("failed", "meta token rejected")


def test_run_daily_other_meta_failure_also_loud(conn, tmp_path, monkeypatch):
    snap = seed(conn, tmp_path)
    monkeypatch.setattr(meta_pull, "pull_into_db", lambda *a, **k: (_ for _ in ()).throw(meta_api.RateLimited("80004 gave up")))
    with pytest.raises(db_mod.JobFailed, match="meta pull failed"):
        run_daily.run(days=2, source="auto", snapshot_dir=str(snap))
    assert conn.execute("SELECT status FROM etl_runs WHERE job='run_daily' ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"
    with pytest.raises(db_mod.JobFailed):
        run_daily.run(days=2, source="meta", snapshot_dir=str(snap))


def test_token_error_codes_are_never_retried(monkeypatch):
    import requests
    for code in (190, 102, 463, 467):
        monkeypatch.setattr(requests, "get", lambda *a, _c=code, **k: FakeResp(400, {"error": {"code": _c, "message": "Invalid OAuth access token."}}))
        slept = []
        with pytest.raises(meta_api.TokenRejected):
            meta_api.graph_get("http://x", {"access_token": "EAAxxxxxxxxxxxxxxxxxxxxxxxxx"}, "t", sleep=slept.append)
        assert slept == []
