"""etl.meta_pages: page id parsing, bulk listing (paging, too-big halving), the incremental selection of ads with spend and no page row, rate-limit backoff, the loud
failure on unresolved spend, read-only (GET only), and the two analytics views (per-ad page, Lonvera vs other pages tying to total Meta spend)."""
import datetime as dt
import inspect

import pytest

from etl import db as db_mod, meta_api, meta_pages as mp

D = dt.date
TODAY = D(2026, 11, 18)
NOSLEEP = lambda s: None                                                                # noqa: E731


@pytest.fixture(autouse=True)
def _no_usage_leak():
    meta_api.LAST_USAGE.clear()
    yield
    meta_api.LAST_USAGE.clear()


def story(page, post="999"):
    return f"{page}_{post}"


# ------------------------------------------------------------------------------------------------------------------ parsing
def test_parse_page_from_effective_object_story_id():
    p = mp.parse_ad({"id": "1", "creative": {"id": "c1", "effective_object_story_id": story("161085663758792", "123"), "object_story_spec": {"page_id": "5"}}})
    assert (p["page_id"], p["source"], p["creative_id"], p["effective_object_story_id"]) == ("161085663758792", "effective_object_story_id", "c1", "161085663758792_123")


def test_parse_falls_back_to_object_story_id_then_spec_and_reads_instagram():
    p = mp.parse_ad({"id": "2", "creative": {"object_story_id": story("830267930177476"), "instagram_actor_id": "ig9"}})
    assert (p["page_id"], p["source"], p["instagram_user_id"]) == ("830267930177476", "object_story_id", "ig9")
    p = mp.parse_ad({"id": "3", "creative": {"object_story_spec": {"page_id": "777", "instagram_user_id": "ig1"}, "instagram_actor_id": "ig9"}})
    assert (p["page_id"], p["source"], p["instagram_user_id"], p["effective_object_story_id"]) == ("777", "object_story_spec", "ig1", None)


def test_instagram_only_creator_ads_are_not_unresolved():
    p = mp.parse_ad({"id": "9", "creative": {"object_story_spec": {"instagram_user_id": "1784"}}})
    assert (p["page_id"], p["source"], p["instagram_user_id"]) == (None, "instagram_only", "1784")


@pytest.mark.parametrize("ad", [{"id": "4"}, {"id": "5", "creative": {}}, {"id": "6", "creative": {"effective_object_story_id": "garbage"}},
                                {"id": "7", "creative": {"effective_object_story_id": "abc_123"}}, {"id": "8", "creative": {"object_story_spec": {"page_id": "x"}}}])
def test_parse_missing_or_malformed_creative_is_unresolved_not_an_error(ad):
    p = mp.parse_ad(ad)
    assert p["page_id"] is None and p["source"] == "unresolved" and p["ad_id"] == ad["id"]


# ------------------------------------------------------------------------------------------------------------------ listing
def test_list_ads_pages_by_cursor_and_halves_the_page_size_when_meta_says_too_big(monkeypatch):
    calls = []

    def fake(url, params, what, **kw):
        calls.append(dict(params))
        if len(calls) == 1:
            raise meta_api.TooBig("Please reduce the amount of data")
        if "after" not in params:
            return {"data": [{"id": "1"}, {"id": "2"}], "paging": {"cursors": {"after": "C1"}, "next": "x"}}
        return {"data": [{"id": "3"}], "paging": {"cursors": {"after": "C2"}}}
    monkeypatch.setattr(meta_api, "graph_get", fake)
    got = []
    n = mp.list_ads("tok", "act_1", "Lonvera Main", None, got.extend, sleep=NOSLEEP)
    assert n == 3 and [a["id"] for a in got] == ["1", "2", "3"]
    assert calls[0]["limit"] == 500 and calls[1]["limit"] == 250 and calls[2]["after"] == "C1" and calls[2]["limit"] == 250
    assert "creative{effective_object_story_id" in calls[0]["fields"] and "ARCHIVED" in calls[0]["effective_status"] and "DELETED" not in calls[0]["effective_status"]
    assert "filtering" not in calls[0]


def test_incremental_listing_filters_on_updated_time(monkeypatch):
    seen = []
    monkeypatch.setattr(meta_api, "graph_get", lambda url, params, what, **kw: (seen.append(params), {"data": []})[1])
    mp.list_ads("tok", "act_1", "Lonvera Main", dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc), lambda d: None, sleep=NOSLEEP)
    assert '"updated_time"' in seen[0]["filtering"] and '"GREATER_THAN"' in seen[0]["filtering"]


def test_listing_waits_for_the_regain_time_meta_reports_between_pages(monkeypatch):
    pages = [{"data": [{"id": "1"}], "paging": {"cursors": {"after": "C1"}, "next": "x"}}, {"data": [{"id": "2"}], "paging": {"cursors": {"after": "C2"}, "next": "x"}}, {"data": []}]

    def fake(url, params, what, **kw):
        j = pages.pop(0)
        meta_api.LAST_USAGE.clear()
        if len(pages) == 1:
            meta_api.LAST_USAGE.update(cpu=128.0, time=102.0, calls=1.0, regain_minutes=20.0)
        elif len(pages) == 0:
            meta_api.LAST_USAGE.update(cpu=95.0, time=10.0, calls=1.0, regain_minutes=0.0)
        return j
    monkeypatch.setattr(meta_api, "graph_get", fake)
    waits = []
    assert mp.list_ads("tok", "act_1", "Main", None, lambda d: None, sleep=waits.append) == 2
    assert waits == [mp.PACE, 20 * 60 + 5] and mp.usage_pause() == 25.0       # throttled: the whole regain time; hot but open: a short pause


def test_page_size_halves_when_a_page_used_the_whole_budget_and_grows_back_when_cheap():
    meta_api.LAST_USAGE.clear()
    assert mp.next_size(125) == 125                                                         # no reading: unchanged
    meta_api.LAST_USAGE.update(cpu=128.0, time=102.0, regain_minutes=29.0)
    assert mp.next_size(125) == 62 and mp.next_size(mp.MIN_PAGE_SIZE) == mp.MIN_PAGE_SIZE
    meta_api.LAST_USAGE.update(cpu=5.0, time=3.0, regain_minutes=0.0)
    assert mp.next_size(125) == 250 and mp.next_size(500) == 500
    meta_api.LAST_USAGE.update(cpu=50.0, time=10.0)
    assert mp.next_size(125) == 125
    meta_api.LAST_USAGE.clear()


def test_usage_header_is_read_without_ever_failing_a_call():
    meta_api.LAST_USAGE.clear()
    meta_api._read_usage({"x-business-use-case-usage": '{"1":[{"type":"ads_management","call_count":1,"total_cputime":128,"total_time":102,"estimated_time_to_regain_access":36}]}'})
    assert meta_api.LAST_USAGE["regain_minutes"] == 36.0 and meta_api.LAST_USAGE["cpu"] == 128.0
    meta_api._read_usage({"x-business-use-case-usage": "not json"})
    meta_api._read_usage({})


def test_rate_limit_80004_backs_off_exponentially_then_succeeds(monkeypatch):
    class R:
        def __init__(self, j, status=400):
            self.j, self.status_code = j, status

        def json(self):
            return self.j
    seq = [R({"error": {"message": "Calls to this api have exceeded the rate limit", "code": 80004}}), R({"error": {"message": "x", "code": 17}}), R({"data": []}, 200)]
    monkeypatch.setattr("requests.get", lambda *a, **k: seq.pop(0))
    waits = []
    out = meta_api.graph_get("https://x", {}, "t", sleep=waits.append)
    assert out == {"data": []} and waits == [60, 120]


# ------------------------------------------------------------------------------------------------------------------ database
@pytest.fixture
def clean(conn):
    conn.execute("TRUNCATE meta_ad_pages, ad_daily, meta_ads, meta_campaigns")
    conn.execute("DELETE FROM meta_pages WHERE page_id NOT IN ('161085663758792', '830267930177476')")
    conn.commit()
    return conn


def spend(conn, ad, day, amt):
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES (%s,%s,%s,%s,%s,'meta_api') ON CONFLICT (ad_id, day) DO UPDATE SET spend = EXCLUDED.spend",
                 (ad, day, f"fam-{ad}", f"fam-{ad}", amt))


def test_seed_pages_exist_and_lonvera_is_flagged(clean):
    rows = dict(clean.execute("SELECT page_id, is_lonvera FROM meta_pages").fetchall())
    assert rows["161085663758792"] is True and rows["830267930177476"] is False


def test_upsert_keeps_first_seen_never_blanks_a_known_page_and_adds_unseen_pages(clean):
    mp.upsert(clean, "Lonvera Main", [mp.parse_ad({"id": "10", "creative": {"effective_object_story_id": story("161085663758792")}})], D(2026, 11, 1))
    # a later listing that returns no creative must not erase the page; a swapped creative does replace it
    mp.upsert(clean, "Lonvera Main", [mp.parse_ad({"id": "10"})], D(2026, 11, 5))
    r = clean.execute("SELECT page_id, first_seen, last_seen, source FROM meta_ad_pages WHERE ad_id = '10'").fetchone()
    assert r == ("161085663758792", D(2026, 11, 1), D(2026, 11, 5), "effective_object_story_id")
    mp.upsert(clean, "Lonvera Main", [mp.parse_ad({"id": "10", "creative": {"effective_object_story_id": story("424242")}})], D(2026, 11, 6))
    assert clean.execute("SELECT page_id FROM meta_ad_pages WHERE ad_id = '10'").fetchone()[0] == "424242"
    assert clean.execute("SELECT name, is_lonvera FROM meta_pages WHERE page_id = '424242'").fetchone() == (None, False)


def test_missing_ids_are_ads_with_recent_spend_and_no_row_biggest_first_and_capped(clean):
    spend(clean, "a", TODAY - dt.timedelta(days=1), 50)
    spend(clean, "b", TODAY - dt.timedelta(days=2), 500)
    spend(clean, "c", TODAY - dt.timedelta(days=1), 70)
    spend(clean, "old", TODAY - dt.timedelta(days=40), 900)                       # outside the window
    spend(clean, "zero", TODAY - dt.timedelta(days=1), 0)
    mp.upsert(clean, "Lonvera Main", [mp.parse_ad({"id": "c", "creative": {"effective_object_story_id": story("1")}})], TODAY)
    assert mp.missing_ids(clean, TODAY) == ["b", "a"]
    assert mp.missing_ids(clean, TODAY, cap=1) == ["b"]


def fake_graph(monkeypatch, listing: dict, lookup: dict | None = None):
    """listing: {account: [graph ads]}; lookup: {ad_id: graph ad} answered to /?ids= calls. Records every call; fails the test on any non-GET."""
    calls = []

    def g(url, params, what, **kw):
        calls.append((url, dict(params), what))
        if url.endswith("/ads"):
            acct = url.split("/")[-2]
            return {"data": listing.get(acct, [])}
        if "ids" in params and "name" in params.get("fields", ""):
            return {i: {"id": i, "name": "Lonvera"} for i in params["ids"].split(",") if i == "161085663758792"}
        return {i: lookup[i] for i in params["ids"].split(",") if lookup and i in lookup}
    monkeypatch.setattr(meta_api, "graph_get", g)
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("Meta write call"))
    return calls


def test_run_lists_both_accounts_looks_up_the_missing_names_pages_and_ties_out(clean, monkeypatch):
    spend(clean, "m1", TODAY - dt.timedelta(days=1), 900)
    spend(clean, "b1", TODAY - dt.timedelta(days=1), 90)
    spend(clean, "late", TODAY - dt.timedelta(days=1), 10)                         # not in either listing: only the fallback lookup can resolve it
    clean.commit()
    calls = fake_graph(monkeypatch,
                       {"act_1671338033619223": [{"id": "m1", "creative": {"effective_object_story_id": story("161085663758792")}}],
                        "act_1543579384022317": [{"id": "b1", "creative": {"effective_object_story_id": story("830267930177476")}}]},
                       lookup={"late": {"id": "late", "creative": {"effective_object_story_id": story("161085663758792")}}})
    st = mp.run(today=TODAY, sleep=NOSLEEP)
    assert st["listed"] == {"Lonvera Main": 1, "Lonvera BackUp": 1} and st["looked_up"] == 1 and st["window_ads_no_page"] == 0
    assert dict(clean.execute("SELECT ad_id, page_id FROM meta_ad_pages").fetchall()) == {"m1": "161085663758792", "b1": "830267930177476", "late": "161085663758792"}
    assert clean.execute("SELECT name FROM meta_pages WHERE page_id = '161085663758792'").fetchone()[0] == "Lonvera"
    assert sum(1 for c in calls if c[2] == "ad page lookup") == 1                  # one batched call, never one per ad
    run = clean.execute("SELECT status, error FROM etl_runs WHERE job = 'meta_pages' ORDER BY id DESC LIMIT 1").fetchone()
    assert run[0] == "ok"


def test_run_fails_loudly_when_over_five_percent_of_recent_spend_has_no_page(clean, monkeypatch):
    spend(clean, "m1", TODAY - dt.timedelta(days=1), 900)
    spend(clean, "ghost", TODAY - dt.timedelta(days=1), 100)                       # 10% of spend, Meta returns nothing for it
    clean.commit()
    fake_graph(monkeypatch, {"act_1671338033619223": [{"id": "m1", "creative": {"effective_object_story_id": story("161085663758792")}}]})
    with pytest.raises(db_mod.JobFailed, match="no page identity"):
        mp.run(today=TODAY, sleep=NOSLEEP)
    assert clean.execute("SELECT status FROM etl_runs WHERE job = 'meta_pages' ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"


def test_a_small_unresolved_share_is_a_warning_on_an_ok_run(clean, monkeypatch):
    spend(clean, "m1", TODAY - dt.timedelta(days=1), 990)
    spend(clean, "ghost", TODAY - dt.timedelta(days=1), 10)
    clean.commit()
    fake_graph(monkeypatch, {"act_1671338033619223": [{"id": "m1", "creative": {"effective_object_story_id": story("161085663758792")}}]})
    st = mp.run(today=TODAY, sleep=NOSLEEP)
    status, err = clean.execute("SELECT status, error FROM etl_runs WHERE job = 'meta_pages' ORDER BY id DESC LIMIT 1").fetchone()
    assert status == "ok" and "1 ads with spend" in err and st["window_ads_no_page"] == 1


def test_token_rejection_and_listing_failure_fail_the_job(clean, monkeypatch):
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")

    def boom(url, params, what, **kw):
        raise meta_api.TokenRejected("rejected")
    monkeypatch.setattr(meta_api, "graph_get", boom)
    with pytest.raises(db_mod.JobFailed, match="meta token rejected"):
        mp.run(today=TODAY, sleep=NOSLEEP)

    def limited(url, params, what, **kw):
        raise meta_api.RateLimited("gave up after 7 retries token=SECRET")
    monkeypatch.setattr(meta_api, "graph_get", limited)
    with pytest.raises(db_mod.JobFailed, match="ad listing failed"):
        mp.run(today=TODAY, sleep=NOSLEEP)


def test_dry_run_writes_nothing(clean, monkeypatch):
    fake_graph(monkeypatch, {"act_1671338033619223": [{"id": "m1", "creative": {"effective_object_story_id": story("161085663758792")}}]})
    mp.run(dry=True, today=TODAY, sleep=NOSLEEP)
    assert clean.execute("SELECT count(*) FROM meta_ad_pages").fetchone()[0] == 0


def test_module_makes_no_write_calls_to_meta():
    src = inspect.getsource(mp)
    assert "requests.post" not in src and ".delete(" not in src and "POST" not in src


# ------------------------------------------------------------------------------------------------------------------ views
def test_views_split_spend_by_page_and_tie_to_total_ad_spend(clean):
    day = TODAY - dt.timedelta(days=1)
    clean.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES ('c1','Lonvera Main','VB Scaling','scaling')")
    for ad, amt in (("v1", 600), ("v2", 300), ("v3", 100)):
        clean.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id) VALUES (%s,'Lonvera Main',%s,%s,'c1')", (ad, f"n{ad}", f"fam-{ad}"))
        spend(clean, ad, day, amt)
    mp.upsert(clean, "Lonvera Main", [mp.parse_ad({"id": "v1", "creative": {"effective_object_story_id": story("161085663758792")}}),
                                      mp.parse_ad({"id": "v2", "creative": {"effective_object_story_id": story("830267930177476")}})], TODAY)
    clean.execute("UPDATE meta_pages SET name = 'Lonvera' WHERE page_id = '161085663758792'")
    clean.commit()
    ads = {r[0]: r for r in clean.execute("SELECT ad_id, page_id, is_lonvera_page, page_name, family_key FROM analytics.meta_ad_pages").fetchall()}
    assert ads["v1"][1:4] == ("161085663758792", True, "Lonvera") and ads["v2"][2] is False and ads["v1"][4] == "fam-v1" and "v3" not in ads
    by_page = {(r[0], r[1]): float(r[2]) for r in clean.execute("SELECT page_id, page_name, sum(spend) FROM analytics.meta_page_performance_daily GROUP BY 1, 2").fetchall()}
    assert by_page == {("161085663758792", "Lonvera"): 600.0, ("830267930177476", "Page 830267930177476"): 300.0, (None, "(unknown page)"): 100.0}
    total_ads = float(clean.execute("SELECT sum(spend) FROM analytics.meta_ad_daily").fetchone()[0])
    assert total_ads == sum(by_page.values()) == 1000.0
    cols = [r[0] for r in clean.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics' AND table_name = 'meta_page_performance_daily' ORDER BY ordinal_position").fetchall()]
    assert cols == ["day", "page_id", "page_name", "is_lonvera_page", "product_slug", "stage", "ads_with_spend", "spend", "impressions", "link_clicks", "meta_purchases", "meta_revenue"]
    cov = clean.execute("SELECT source, last_day, rows FROM analytics.data_coverage WHERE source = 'meta_ad_pages'").fetchone()
    assert cov[0] == "meta_ad_pages" and cov[1] == TODAY and cov[2] == 2


def test_spend_first_backfill_resolves_biggest_spenders_first_and_parks_deleted_ads(clean, monkeypatch):
    for ad, amt in (("big", 900), ("mid", 90), ("gone", 9)):
        spend(clean, ad, TODAY - dt.timedelta(days=3), amt)
    clean.commit()
    order = []

    def g(url, params, what, **kw):
        order.append(params["ids"])
        return {"big": {"id": "big", "creative": {"effective_object_story_id": story("161085663758792")}}, "mid": {"id": "mid", "creative": {"effective_object_story_id": story("830267930177476")}}}
    monkeypatch.setattr(meta_api, "graph_get", g)
    monkeypatch.setattr(meta_api, "get_token", lambda: "tok")
    st = mp.run(spend_since=D(2026, 5, 6), today=TODAY, sleep=NOSLEEP)
    assert st["looked_up"] == 2 and st["unresolved"] == 1 and order == ["big,mid,gone"]
    assert dict(clean.execute("SELECT ad_id, source FROM meta_ad_pages").fetchall()) == {"big": "effective_object_story_id", "mid": "effective_object_story_id", "gone": "unresolved"}
    assert mp.missing_ids(clean, TODAY, 200) == []


def test_instagram_only_ads_do_not_count_toward_the_unresolved_failure(clean, monkeypatch):
    spend(clean, "m1", TODAY - dt.timedelta(days=1), 500)
    spend(clean, "ig", TODAY - dt.timedelta(days=1), 500)
    clean.commit()
    fake_graph(monkeypatch, {"act_1671338033619223": [{"id": "m1", "creative": {"effective_object_story_id": story("161085663758792")}},
                                                     {"id": "ig", "creative": {"object_story_spec": {"instagram_user_id": "1784"}}}]})
    st = mp.run(today=TODAY, sleep=NOSLEEP)
    assert st["window_ads_no_page"] == 0 and st["window_spend_no_page"] == 0
