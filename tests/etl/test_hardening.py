"""QA-round-1 fixes: cross-product first row, renamed ads, merge-suffix names, unmatched lifetime/product guess, active mirrors, tracker hardening,
atomic run_daily (no sheet fallback), status refresh, Pacific created dates, unfiltered pulls."""
import datetime as dt

import pytest

from etl import build_metrics, db as db_mod, lifetime_pull, meta_api, meta_pull, names, resolve_families, run_daily, sheets_io, status_refresh, tracker_sync
from etl.resolver import fam
from conftest import TRACKER_HDR, trow, write_snapshot

D = dt.date


def put_ad(conn, ad_id, name, camp="c1", status=None, adset="as1", acct="Lonvera Main"):
    f = fam(name)
    conn.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, family_name, campaign_id, adset_name, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                 "ON CONFLICT (ad_id) DO UPDATE SET ad_name=EXCLUDED.ad_name, meta_family_key=EXCLUDED.meta_family_key, family_name=EXCLUDED.family_name, "
                 "campaign_id=EXCLUDED.campaign_id, adset_name=EXCLUDED.adset_name, status=EXCLUDED.status", (ad_id, acct, name, f.lower(), f, camp, adset, status))


def put_daily(conn, ad_id, day, key, spend, purchases=0, impressions=100, clicks=5, camp="c1", value=0):
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'meta_api')", (ad_id, day, key, key, camp, spend, purchases, value, clicks, impressions))


def put_life(conn, ad_id, key, spend, purchases=0, value=0, impressions=1000, clicks=50):
    conn.execute("INSERT INTO meta_ad_lifetime (ad_id, meta_family_key, spend, purchases, conv_value, impressions, link_clicks, source, pulled_at) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,'meta_api',now())", (ad_id, key, spend, purchases, value, impressions, clicks))


def base(conn, tmp_path, trs):
    snap = write_snapshot(tmp_path, trs, [("2026-05-06", "seed", 1, 0, 0, 0)], windows=[])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))
    conn.execute("DELETE FROM ad_daily")
    conn.execute("INSERT INTO meta_campaigns (campaign_id, name, stage) VALUES ('c1','Scaling x','scaling')")
    conn.commit()
    return snap


def test_family_under_two_products_is_counted_once_and_name_picks_the_winner(conn, tmp_path):
    trs = [trow("A1", "boneflex-96", "", launch="1/1/2026"),                 # unassigned row first in the sheet
           trow("A2", "boneflex-96", "Boneflex"),
           trow("B1", "lonvera-beefliver-static-3.2", "Boneflex"),           # product says Boneflex but the NAME says beef liver
           trow("B2", "lonvera-beefliver-static-3.2", "BeefLiver"),
           trow("C1", "plainname", "VitaBull"), trow("C2", "plainname", "BeefLiver")]   # no signal in the name: lowest sheet_row wins
    base(conn, tmp_path, trs)
    for i, k in enumerate(("boneflex-96", "lonvera-beefliver-static-3.2", "plainname"), start=1):
        put_ad(conn, str(i), k); put_life(conn, str(i), k, 100.0 * i)
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,'exact')", (k, k, k))
    put_daily(conn, "1", "2026-11-02", "boneflex-96", 1.0)
    conn.commit()
    build_metrics.rebuild(conn)
    win = {r[0]: r[1] for r in conn.execute("""SELECT t.concept_id, p.slug FROM ad_metrics m JOIN tracker_rows t ON t.id=m.tracker_row_id JOIN products p ON p.id=m.product_id
                                             WHERE m.is_first_row""").fetchall()}
    assert win == {"A2": "boneflex", "B2": "beef-liver", "C1": "vita-bull"}      # real product beats unassigned; name match beats sheet order; else lowest sheet_row
    assert float(conn.execute("SELECT sum(spend_launch) FROM ad_metrics").fetchone()[0]) == 600.0       # each family once, not once per product
    zero = conn.execute("SELECT count(*) FROM ad_metrics WHERE NOT is_first_row AND (spend_launch <> 0 OR spend_7d <> 0 OR live)").fetchone()[0]
    assert zero == 0


def test_renamed_ad_history_follows_the_new_name(conn, tmp_path):
    base(conn, tmp_path, [trow("N", "new name", "VitaBull"), trow("O", "old name", "VitaBull")])
    for k in ("new name", "old name"):
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,'exact')", (k, k, k))
    put_ad(conn, "1", "old name")
    put_daily(conn, "1", "2026-10-20", "old name", 10.0); put_life(conn, "1", "old name", 50.0)
    conn.commit()
    build_metrics.rebuild(conn)
    assert float(conn.execute("SELECT spend FROM family_daily WHERE family_key='old name'").fetchone()[0]) == 10.0
    put_ad(conn, "1", "New Name - Copy")                       # the ad is renamed in Meta; meta_ads is refreshed, ad_daily / lifetime still carry the old key
    conn.commit()
    build_metrics.rebuild(conn)
    assert conn.execute("SELECT count(*) FROM family_daily WHERE family_key='old name'").fetchone()[0] == 0
    assert float(conn.execute("SELECT spend FROM family_daily WHERE family_key='new name'").fetchone()[0]) == 10.0
    assert conn.execute("SELECT meta_family_key FROM ad_daily WHERE ad_id='1'").fetchone()[0] == "new name"
    m = {r[0]: float(r[1]) for r in conn.execute("SELECT t.concept_id, m.spend_launch FROM ad_metrics m JOIN tracker_rows t ON t.id=m.tracker_row_id").fetchall()}
    assert m == {"N": 50.0, "O": 0.0}                           # lifetime follows too


def test_merge_suffix_names_map_to_the_base_family():
    assert fam("bull-526-hook2 - copy | vb-merge-2026-10-22") == "bull-526-hook2"
    assert fam("whiteWomenTest - Copy 2 | VB-MERGE-2026-10-22") == "whiteWomenTest"
    assert fam("Foo | bl-merge-2026-10-22 - Copy") == "Foo"
    assert fam("a | b") == "a | b"
    from etl.resolver import Resolver
    r = Resolver([{"family": "bull-526-hook2", "concept": "bull-526-hook2", "adname": ""}])
    assert r.resolve_rule("Bull-526-Hook2 - Copy | vb-merge-2026-10-22") == ("bull-526-hook2", "exact")


def test_unmatched_includes_lifetime_only_families_product_guess_and_deterministic_name(conn, tmp_path):
    base(conn, tmp_path, [trow("T", "tfam", "Boneflex")])
    conn.execute("INSERT INTO meta_campaigns (campaign_id, name, stage) VALUES ('cb','Boneflex - USA - CC','scaling'),('cn','Nothing here','scaling')")
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('tfam','tfam','tfam','exact')")
    put_ad(conn, "1", "tfam"); put_life(conn, "1", "tfam", 100.0)
    put_ad(conn, "2", "newmech_postid - Copy - DONE", camp="cb"); put_life(conn, "2", "newmech_postid - copy - done", 69.0)    # no signal in the name -> campaign says Boneflex
    put_ad(conn, "3", "mystery", camp="cn", adset="Beef Liver adset"); put_life(conn, "3", "mystery", 5.0)                       # ad set says liver
    put_ad(conn, "4", "nothing", camp="cn"); put_life(conn, "4", "nothing", 1.0)
    put_ad(conn, "5", "Dual Case"); put_ad(conn, "6", "dual case"); put_life(conn, "5", "dual case", 10.0); put_life(conn, "6", "dual case", 10.0)   # tie -> lexicographic
    put_ad(conn, "7", "Dual Case Daily"); put_daily(conn, "7", "2026-10-31", "dual case daily", 3.0)
    conn.commit()
    build_metrics.rebuild(conn)
    rows = {r[0]: r[1:] for r in conn.execute("""SELECT lower(u.meta_family), p.slug, u.product_source, u.spend_launch, u.spend_all FROM unmatched_meta u
                                                JOIN products p ON p.id=u.product_id""").fetchall()}
    assert rows["newmech_postid - copy - done".replace(" - copy", "")][:3] == ("boneflex", "campaign", 69.0) if "newmech_postid - done" in rows else True
    got = {k.split(" - ")[0]: v for k, v in rows.items()}
    assert got["newmech_postid"][:3] == ("boneflex", "campaign", 69.0)
    assert got["mystery"][:3] == ("beef-liver", "adset", 5.0)
    assert got["nothing"][:3] == ("unassigned", "none", 1.0)
    assert got["dual case"][3] == 0 and float(got["dual case"][2]) == 20.0                    # lifetime-only family, no daily rows
    assert conn.execute("SELECT meta_family FROM unmatched_meta WHERE meta_family_key='dual case'").fetchone()[0] == "Dual Case"       # 'Dual Case' < 'dual case'
    tot = build_metrics.rebuild(conn)["launch_total"]
    assert abs(tot["ad_metrics"] + tot["unmatched"] - tot["api_lifetime"]) < 0.01            # the panel's launch total equals lifetime


def test_active_tables_and_mirror_columns(conn, tmp_path):
    base(conn, tmp_path, [trow("A", "fa", "VitaBull"), trow("A2", "fa", "VitaBull")])
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('fa','fa','fa','exact')")
    put_ad(conn, "1", "fa", status="ACTIVE"); put_ad(conn, "2", "fa - Copy", status="PAUSED"); put_ad(conn, "3", "fa - Copy 2", status="ACTIVE")
    for ad, sp in (("1", 10.0), ("2", 100.0), ("3", 5.0)):
        put_life(conn, ad, "fa", sp * 10)
    put_daily(conn, "1", "2026-11-02", "fa", 10.0, purchases=1, value=30); put_daily(conn, "2", "2026-11-02", "fa", 100.0); put_daily(conn, "3", "2026-11-01", "fa", 5.0)
    conn.commit()
    build_metrics.rebuild(conn)
    fd = {r[0].isoformat(): float(r[1]) for r in conn.execute("SELECT day, sum(spend) FROM family_daily_active GROUP BY 1").fetchall()}
    assert fd == {"2026-11-02": 10.0, "2026-11-01": 5.0}                                      # only the ACTIVE ads
    r = conn.execute("""SELECT t.concept_id, m.ads_active_count, m.spend_launch_active, m.spend_launch, m.live_active, m.live FROM ad_metrics m
                        JOIN tracker_rows t ON t.id=m.tracker_row_id ORDER BY t.sheet_row""").fetchall()
    assert [(a, b, float(c), float(d), e, f) for a, b, c, d, e, f in r] == [("A", 2, 150.0, 1150.0, True, True), ("A2", 2, 0.0, 0.0, False, False)]


def test_tracker_sync_refuses_missing_header_and_suspicious_blank_family_jump(conn, tmp_path):
    snap = base(conn, tmp_path, [trow(f"C{i}", f"fam{i}", "VitaBull") for i in range(10)])
    good = sheets_io.read_tab("creative_tracker", snap)
    before = conn.execute("SELECT count(*), count(*) FILTER (WHERE family_key LIKE 'id:%%') FROM tracker_rows").fetchone()
    bad = [list(r) for r in good]
    bad[0][bad[0].index("Ad Family")] = "Ad Fam"                                           # a renamed header must not blank every family
    with pytest.raises(tracker_sync.TrackerRefused, match="Ad Family"):
        tracker_sync.sync(conn, bad)
    jump = [list(r) for r in good]
    ix = jump[0].index("Ad Family")
    for r in jump[1:6]:
        r[ix] = ""                                                                          # 0 blank -> 5 blank
    conn.execute("UPDATE app_meta SET value='1' WHERE key='tracker_blank_family_count'")       # previous sync had 1 blank
    conn.commit()
    with pytest.raises(tracker_sync.TrackerRefused, match="jumped"):
        tracker_sync.sync(conn, jump)
    assert conn.execute("SELECT count(*), count(*) FILTER (WHERE family_key LIKE 'id:%%') FROM tracker_rows").fetchone() == before       # nothing was written
    tracker_sync.sync(conn, jump, force=True)
    assert conn.execute("SELECT count(*) FILTER (WHERE family_key LIKE 'id:%%') FROM tracker_rows").fetchone()[0] == 5


def test_tracker_rows_missing_are_soft_deleted_and_come_back(conn, tmp_path):
    snap = base(conn, tmp_path, [trow("K1", "k1", "VitaBull"), trow("K2", "k2", "VitaBull"), trow("K3", "k3", "VitaBull")])
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('k2','k2','k2','exact')")
    put_ad(conn, "1", "k2"); put_life(conn, "1", "k2", 40.0); put_daily(conn, "1", "2026-11-02", "k2", 4.0)
    conn.commit()
    full = sheets_io.read_tab("creative_tracker", snap)
    short = [full[0]] + [r for r in full[1:] if r[2] != "K2"]
    s = tracker_sync.sync(conn, short, force=True)
    assert s["deleted_now"] == 1 and conn.execute("SELECT deleted_at IS NOT NULL FROM tracker_rows WHERE concept_id='K2'").fetchone()[0]
    build_metrics.rebuild(conn)
    assert conn.execute("SELECT count(*) FROM ad_metrics m JOIN tracker_rows t ON t.id=m.tracker_row_id WHERE t.concept_id='K2'").fetchone()[0] == 0
    um = conn.execute("SELECT spend_launch FROM unmatched_meta WHERE meta_family_key='k2'").fetchone()           # its Meta spend is unmatched now, not lost
    assert um and float(um[0]) == 40.0
    s = tracker_sync.sync(conn, full, force=True)
    assert s["resurrected"] == 1 and conn.execute("SELECT deleted_at IS NULL FROM tracker_rows WHERE concept_id='K2'").fetchone()[0]


def test_duplicate_group_identity_survives_a_row_inserted_above(conn, tmp_path):
    def rows(*specs):                      # specs: (concept_name,) for rows sharing concept + family
        out = []
        for nm in specs:
            r = trow("DUP", "dupfam", "VitaBull"); r[TRACKER_HDR.index("Concept Name")] = nm
            out.append(r)
        return out
    snap = write_snapshot(tmp_path, rows("first", "second", "third"), [("2026-05-06", "x", 1, 0, 0, 0)], windows=[])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap), force=True)
    keys = {r[0]: r[1] for r in conn.execute("SELECT concept_name, row_key FROM tracker_rows").fetchall()}
    snap2 = write_snapshot(tmp_path, rows("brand new", "first", "second", "third"), [("2026-05-06", "x", 1, 0, 0, 0)], windows=[])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap2), force=True)
    now = {r[0]: r[1] for r in conn.execute("SELECT concept_name, row_key FROM tracker_rows WHERE deleted_at IS NULL").fetchall()}
    assert all(now[k] == v for k, v in keys.items())                       # first/second/third keep their keys (and therefore their tags)
    assert now["brand new"] not in keys.values()


def test_created_date_is_the_pacific_date():
    at, d = meta_api.parse_created("2026-10-12T02:30:00+0000")             # 7:30pm PDT on the 11th
    assert d == D(2026, 10, 11) and at.isoformat().startswith("2026-10-12T02:30")
    assert meta_api.parse_created("2026-11-03T18:00:00+0000")[1] == D(2026, 11, 3)
    assert meta_api.parse_created(None) == (None, None)


def test_status_refresh_updates_status_names_and_created(conn, tmp_path, monkeypatch):
    base(conn, tmp_path, [trow("S", "s", "VitaBull")])
    put_ad(conn, "1", "s", status="ADSET_PAUSED"); put_ad(conn, "2", "old", status="ARCHIVED"); put_ad(conn, "3", "t", status="ACTIVE")
    put_daily(conn, "1", "2026-11-02", "s", 5.0)
    conn.commit()
    seen = {}

    def fake(tok, ids, batch=50, pace=0, strict=False):
        seen["ids"], seen["strict"] = sorted(ids), strict
        return {"1": {"status": "ACTIVE", "name": "s", "created_at": dt.datetime(2026, 10, 12, 2, 30, tzinfo=dt.timezone.utc), "created": D(2026, 10, 11)},
                "3": {"status": "PAUSED", "name": "t renamed - Copy", "created_at": None, "created": None}}
    monkeypatch.setattr(meta_api, "fetch_ad_meta", fake)
    rows, st = status_refresh.fetch_updates(conn, "tok")
    status_refresh.apply_updates(conn, rows)
    # ARCHIVED ad without recent delivery is NOT asked for (but ones with no created_at are, so ask-set = recent + non-terminal + missing created)
    assert seen["strict"] is True and "3" in seen["ids"] and "1" in seen["ids"]
    assert st["status_changed"] == 2 and st["renamed"] == 1
    r = {a: (b, c, d) for a, b, c, d in conn.execute("SELECT ad_id, status, family_name, created_date FROM meta_ads WHERE ad_id IN ('1','3')").fetchall()}
    assert r["1"] == ("ACTIVE", "s", D(2026, 10, 11)) and r["3"][0] == "PAUSED" and r["3"][1] == "t renamed"
    assert conn.execute("SELECT value FROM app_meta WHERE key='status_refreshed_at'").fetchone()


def test_daily_pull_has_no_row_filter(monkeypatch):
    seen = {}

    def fake(url, params, what, pace=0):
        seen.update(params)
        return []
    monkeypatch.setattr(meta_api, "_paged", fake)
    meta_api.pull_chunk("tok", "act_1", "A", D(2026, 10, 1), D(2026, 10, 2))
    assert "filtering" not in seen and "impressions" in seen["fields"]


# ---------------- atomic run_daily ----------------
def stub_graph(monkeypatch, rows=None, token_error=False, life=None):
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")

    def run_pull(conn, since, until, dry_run=False, replace=True, snapshot_dir=None, workers=2, tok=None, atomic=False, load=True):
        if token_error:
            raise meta_api.TokenRejected("Meta rejected the access token (code 190)")
        agg = meta_pull.aggregate(rows or [])
        return {"chunks": 1, "chunks_loaded": 1, "chunks_failed": [], "rows_inserted": 0, "rows_deleted": 0, "recon": [], "_pending": [(agg, since, until)]}, {}
    monkeypatch.setattr(meta_pull, "run_pull", run_pull)
    monkeypatch.setattr(lifetime_pull, "pull", lambda tok, until=None: (lifetime_pull.aggregate(life or []), [{"account": "x", "ok": True}]))
    monkeypatch.setattr(status_refresh, "fetch_updates", lambda conn, tok=None, created_limit=5000, extra_ids=(): ([], {"asked": 0, "status_changed": 0, "renamed": 0, "created_changed": 0}))


def apirow(day, ad_id, name, **kw):
    r = {"day": D.fromisoformat(day), "ad_id": ad_id, "ad_name": name, "campaign_id": "c1", "campaign_name": "Scaling x", "adset_id": "as1", "adset_name": "A",
         "spend": 10.0, "purchases": 1.0, "value": 30.0, "clicks": 5.0, "impressions": 100.0, "account": "Lonvera Main"}
    r.update(kw)
    return r


def test_run_daily_success_sets_last_ok_at_and_status(conn, tmp_path, monkeypatch):
    snap = base(conn, tmp_path, [trow("R", "rfam", "VitaBull")])
    stub_graph(monkeypatch, rows=[apirow("2026-11-01", "1", "rfam"), apirow("2026-11-02", "1", "rfam")], life=[apirow("2026-11-02", "1", "rfam", spend=500.0)])
    res = run_daily.run(days=2, snapshot_dir=str(snap), until=D(2026, 11, 2))
    assert res["build_metrics"]["ok"] and res["build_metrics"]["data_through"] == "2026-11-02"
    am = dict(conn.execute("SELECT key, value FROM app_meta").fetchall())
    assert am["refresh_status"] == "ok" and am["last_error"] == "" and am["last_ok_at"] and am["data_through"] == "2026-11-02"
    assert float(conn.execute("SELECT sum(spend_launch) FROM ad_metrics").fetchone()[0]) == 500.0


def test_run_daily_token_rejected_fails_loudly_and_leaves_data_untouched(conn, tmp_path, monkeypatch):
    snap = base(conn, tmp_path, [trow("R", "rfam", "VitaBull")])
    put_ad(conn, "1", "rfam"); put_daily(conn, "1", "2026-11-02", "rfam", 77.0)
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('rfam','rfam','rfam','exact')")
    conn.commit()
    build_metrics.rebuild(conn)
    before = (conn.execute("SELECT count(*), sum(spend) FROM ad_daily").fetchone(), conn.execute("SELECT count(*), sum(spend) FROM family_daily").fetchone())
    stub_graph(monkeypatch, token_error=True)
    with pytest.raises(db_mod.JobFailed, match="meta token rejected"):
        run_daily.run(days=2, snapshot_dir=str(snap), until=D(2026, 11, 2))
    assert (conn.execute("SELECT count(*), sum(spend) FROM ad_daily").fetchone(), conn.execute("SELECT count(*), sum(spend) FROM family_daily").fetchone()) == before
    assert conn.execute("SELECT count(*) FROM ad_daily WHERE source='sheet'").fetchone()[0] == 0           # no sheet fallback ever writes family-grain rows
    top = conn.execute("SELECT status, error FROM etl_runs WHERE job='run_daily' ORDER BY id DESC LIMIT 1").fetchone()
    assert top == ("failed", "meta token rejected")
    am = dict(conn.execute("SELECT key, value FROM app_meta").fetchall())
    assert am["refresh_status"] == "failed" and am["last_error"] == "meta token rejected" and "last_ok_at" not in am


def test_run_daily_tracker_refusal_still_refreshes_numbers_but_fails_the_run(conn, tmp_path, monkeypatch):
    snap = base(conn, tmp_path, [trow("R", "rfam", "VitaBull")])
    full = sheets_io.read_tab("creative_tracker", snap)
    full[0][full[0].index("Ad Family")] = "Family"                                                     # tracker header broken
    monkeypatch.setattr(sheets_io, "read_tab", lambda stem, snapshot_dir=None: full)
    stub_graph(monkeypatch, rows=[apirow("2026-11-02", "1", "rfam")], life=[apirow("2026-11-02", "1", "rfam", spend=9.0)])
    with pytest.raises(db_mod.JobFailed, match="Ad Family"):
        run_daily.run(days=1, snapshot_dir=str(snap), until=D(2026, 11, 2))
    assert conn.execute("SELECT value FROM app_meta WHERE key='data_through'").fetchone()[0] == "2026-11-02"      # Meta numbers were still refreshed
    assert conn.execute("SELECT value FROM app_meta WHERE key='refresh_status'").fetchone()[0] == "failed"
    assert conn.execute("SELECT count(*) FROM tracker_rows WHERE deleted_at IS NOT NULL").fetchone()[0] == 0   # and the broken tracker deleted nothing


def test_blank_concept_id_rows_are_skipped_and_existing_ones_soft_deleted(conn, tmp_path):
    snap = base(conn, tmp_path, [trow("P1", "p1", "VitaBull"), trow("", "", "Boneflex", status="3 - Briefing"), trow("P2", "p2", "VitaBull")])
    st = tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap), force=True)
    assert st["skipped_blank_concept"] == 1 and st["rows_in_sheet"] == 2
    assert conn.execute("SELECT count(*) FROM tracker_rows WHERE concept_id = ''").fetchone()[0] == 0
    # a placeholder stored by an older sync is soft-deleted on the next one
    conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row) "
                 "SELECT id, '#1', '', '', 'id:', 99 FROM products WHERE slug='unassigned'")
    conn.commit()
    st = tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap), force=True)
    assert st["deleted_now"] == 1 and conn.execute("SELECT deleted_at IS NOT NULL FROM tracker_rows WHERE row_key='#1'").fetchone()[0]


def test_status_refresh_also_fetches_ads_new_in_this_run(conn, tmp_path, monkeypatch):
    base(conn, tmp_path, [trow("S", "s", "VitaBull")])
    put_ad(conn, "1", "s", status="ACTIVE")
    conn.execute("UPDATE meta_ads SET created_at = now() WHERE ad_id = '1'")
    put_daily(conn, "1", "2026-11-02", "s", 5.0)
    conn.commit()
    seen = {}
    monkeypatch.setattr(meta_api, "fetch_ad_meta", lambda tok, ids, batch=50, pace=0, strict=False: seen.setdefault("ids", sorted(ids)) and {})
    status_refresh.fetch_updates(conn, "tok", extra_ids=["1", "999"])
    assert "999" in seen["ids"]                      # an ad the pull is about to insert is refreshed in the same run


# ---------------- a failing lifetime pull must not block the daily rows ----------------
def test_run_daily_lifetime_failure_still_writes_daily_rows_and_fails_the_run(conn, tmp_path, monkeypatch):
    snap = base(conn, tmp_path, [trow("R", "rfam", "VitaBull")])
    put_ad(conn, "1", "rfam"); put_life(conn, "1", "rfam", 123.0)
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('rfam','rfam','rfam','exact')")
    conn.commit()
    stub_graph(monkeypatch, rows=[apirow("2026-11-01", "1", "rfam"), apirow("2026-11-02", "1", "rfam", spend=42.0)])

    def boom(tok, until=None):
        raise meta_api.TooBig("Please reduce the amount of data you're asking for")
    monkeypatch.setattr(lifetime_pull, "pull", boom)
    with pytest.raises(db_mod.JobFailed, match="lifetime pull failed"):
        run_daily.run(days=2, snapshot_dir=str(snap), until=D(2026, 11, 2))
    # the daily Meta rows landed
    assert float(conn.execute("SELECT sum(spend) FROM ad_daily WHERE day = '2026-11-02'").fetchone()[0]) == 42.0
    assert conn.execute("SELECT value FROM app_meta WHERE key='data_through'").fetchone()[0] == "2026-11-02"
    # the previous lifetime table is untouched
    assert float(conn.execute("SELECT sum(spend) FROM meta_ad_lifetime").fetchone()[0]) == 123.0
    # the failure still surfaces: a failed lifetime_pull job run, a failed run_daily, refresh_status failed with the reason
    assert conn.execute("SELECT status FROM etl_runs WHERE job='lifetime_pull' ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM etl_runs WHERE job='build_metrics' ORDER BY id DESC LIMIT 1").fetchone()[0] == "ok"
    assert conn.execute("SELECT status FROM etl_runs WHERE job='run_daily' ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"
    am = dict(conn.execute("SELECT key, value FROM app_meta").fetchall())
    assert am["refresh_status"] == "failed" and "lifetime pull failed" in am["last_error"] and "last_ok_at" not in am


def test_pull_lifetime_retries_too_big_from_the_async_report(monkeypatch):
    monkeypatch.setattr(meta_api, "LIFETIME_RETRY_SLEEP", 0)
    calls = {"paged": 0, "async": 0}

    def paged(*a, **k):
        calls["paged"] += 1
        raise meta_api.TooBig("reduce")

    def async_report(tok, acct, an, extra):
        calls["async"] += 1
        if calls["async"] < 3:
            raise meta_api.TooBig("reduce")                       # twice too big, then it works
        return [{"day": D(2026, 11, 2), "ad_id": "1", "ad_name": "x", "campaign_id": "", "campaign_name": "", "adset_id": "", "adset_name": "",
                 "spend": 1.0, "purchases": 0, "value": 0, "clicks": 0}]
    monkeypatch.setattr(meta_api, "_paged", paged)
    monkeypatch.setattr(meta_api, "_async_report", async_report)
    rows = meta_api.pull_lifetime("tok", "act_1", "Acct", D(2025, 1, 1), D(2026, 11, 2))
    assert calls["async"] == 3 and len(rows) == 1 and rows[0]["account"] == "Acct"


def test_pull_lifetime_gives_up_after_three_too_big_attempts_as_a_graph_error(monkeypatch):
    monkeypatch.setattr(meta_api, "LIFETIME_RETRY_SLEEP", 0)
    n = {"async": 0}

    def async_report(*a, **k):
        n["async"] += 1
        raise meta_api.TooBig("reduce")
    monkeypatch.setattr(meta_api, "_paged", lambda *a, **k: (_ for _ in ()).throw(meta_api.TooBig("reduce")))
    monkeypatch.setattr(meta_api, "_async_report", async_report)
    with pytest.raises(meta_api.GraphError, match="too big after 3 attempts"):
        meta_api.pull_lifetime("tok", "act_1", "Acct", D(2025, 1, 1), D(2026, 11, 2))
    assert n["async"] == 3
