"""etl.meta_breakdowns: catalog, requests, paging, upsert, unsupported sets and level fallback, rate limits, async fallback, newest-first resumable backfill.
Mocked HTTP only: `requests.get` / `requests.post` are replaced, and a guard fails any test that reaches the real network."""
import datetime as dt
import json
import os
import re
import socket
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from etl import db, meta_api, meta_breakdowns as mb

D = dt.date
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/tagexp_test")
ACCT = meta_api.ACCOUNTS[0]
ONE = [ACCT]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network call attempted in a test")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


@pytest.fixture
def bd(conn):
    conn.execute("TRUNCATE meta_breakdown_daily, meta_breakdown_coverage, meta_breakdown_progress")
    conn.commit()
    return conn


# ----------------------------------------------------------------------------------------------- a fake Graph API
ADS = [("a1", "s1", "c1"), ("a2", "s1", "c1"), ("a3", "s2", "c2")]
VALUES = {"age": ["18-24", "25-34"], "gender": ["female", "male"], "country": ["US", "GB"], "region": ["California", "Texas"]}
PAGE = 2


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


def err(code, msg, status=400):
    return Resp({"error": {"code": code, "message": msg}}, status)


class Fake:
    """handler(params, acct) -> None (serve default rows) | Resp. `calls` records (method, path, params)."""

    def __init__(self, handler=None, page=PAGE):
        self.handler, self.calls, self.page = handler, [], page
        self.async_rows: dict[str, list] = {}

    def install(self, monkeypatch):
        monkeypatch.setattr(requests, "get", self.get)
        monkeypatch.setattr(requests, "post", self.post)
        return self

    # rows ---------------------------------------------------------------------------------------
    def rows(self, p):
        tr = json.loads(p["time_range"])
        since, until = D.fromisoformat(tr["since"]), D.fromisoformat(tr["until"])
        level = p["level"]
        out = []
        d = since
        while d <= until:
            ents = ([{"ad_id": a, "adset_id": s, "campaign_id": c} for a, s, c in ADS] if level == "ad" else
                    [{"campaign_id": c} for c in ("c1", "c2")] if level == "campaign" else [{}])
            filt = json.loads(p["filtering"]) if p.get("filtering") else []
            for e in ents:
                for f in filt:
                    if f["field"] == "ad.id" and e.get("ad_id") not in f["value"]:
                        e = None
                        break
                if e is None:
                    continue
                if "action_breakdowns" in p:
                    abs_ = p["action_breakdowns"].split(",")[1:]
                    acts, vals = [{"action_type": "link_click", "value": "9"}], []
                    for i, v in enumerate(("iphone", "desktop")):
                        acts.append({"action_type": "omni_purchase", **{b: v for b in abs_}, "value": str(i + 1)})
                        vals.append({"action_type": "omni_purchase", **{b: v for b in abs_}, "value": str(10 * (i + 1))})
                    out.append({**e, "date_start": d.isoformat(), "actions": acts, "action_values": vals})
                    continue
                names = [n for n in p.get("breakdowns", "").split(",") if n]
                combos = [{}]
                for n in names:
                    combos = [{**c, n: v} for c in combos for v in VALUES.get(n, ["x", "y"])]
                for c in combos:
                    out.append({**e, **c, "date_start": d.isoformat(), "spend": "10.00", "impressions": "1000", "reach": "900", "clicks": "30", "inline_link_clicks": "20",
                                "outbound_clicks": [{"action_type": "outbound_click", "value": "15"}], "unique_outbound_clicks": [{"action_type": "outbound_click", "value": "12"}],
                                "actions": [{"action_type": "omni_purchase", "value": "2"}, {"action_type": "omni_add_to_cart", "value": "7"},
                                            {"action_type": "add_to_cart", "value": "99"}, {"action_type": "omni_initiated_checkout", "value": "4"}],
                                "action_values": [{"action_type": "omni_purchase", "value": "100"}]})
            d += dt.timedelta(days=1)
        fields = set(p["fields"].split(","))
        return [{k: v for k, v in r.items() if k not in mb.BASE_FIELDS or k in fields} for r in out]

    def serve(self, url, params, method):
        path = urlparse(url).path
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if params:
            self.cur = dict(params)
            p = dict(params)
        else:                                                                       # a next-page URL: same query as the request it continues, plus ?page=
            p = {**getattr(self, "cur", {}), **q}
        self.calls.append((method, path, {k: v for k, v in p.items() if k != "access_token"}))
        acct = path.split("/")[-2] if path.endswith("/insights") else None
        if self.handler:
            r = self.handler(p, acct)
            if r is not None:
                return r
        if "report_run_id" in path or re.search(r"/\d+$", path):                 # async status poll
            return Resp({"async_status": "Job Completed"})
        if path.endswith("/insights") and "/9999/" in path:                       # async result pages
            return Resp({"data": self.async_rows.get("rows", [])})
        rows = self.rows(p)
        page = int(q.get("page", 0))
        chunk = rows[page * self.page:(page + 1) * self.page]
        body = {"data": chunk}
        if (page + 1) * self.page < len(rows):
            body["paging"] = {"next": f"https://graph.facebook.com/v23.0{path}?page={page + 1}&access_token=T"}
        return Resp(body)

    def get(self, url, params=None, timeout=None):
        return self.serve(url, params, "GET")

    def post(self, url, data=None, timeout=None):
        r = self.serve(url, data, "POST")
        return r if r.status_code != 200 or "error" in r.body else Resp({"report_run_id": "9999"})


def client(sleeps=None, **k):
    sleeps = [] if sleeps is None else sleeps
    return mb.Client("TOKEN", pace=0, sleep=sleeps.append, **k), sleeps


def count(conn, sql="SELECT count(*) FROM meta_breakdown_daily"):
    return conn.execute(sql).fetchone()[0]


def run(conn, windows, sets, c=None, **kw):
    c = c or client()[0]
    kw.setdefault("accounts", ONE)
    return mb.run(conn, windows, sets, client=c, pause=lambda s: None, **kw)


# ----------------------------------------------------------------------------------------------- catalog and requests
def test_catalog_has_every_set_the_brief_names():
    names = set(mb.BY_NAME)
    for n in ("age", "gender", "age,gender", "country", "region", "dma", "impression_device", "device_platform", "publisher_platform", "platform_position",
              "publisher_platform,platform_position,impression_device", "product_id", "hourly_stats_aggregated_by_advertiser_time_zone",
              "hourly_stats_aggregated_by_audience_time_zone", "frequency_value", "user_segment_key", "place_page_id", "landing_destination", "is_conversion_id_modeled",
              "image_asset", "video_asset", "title_asset", "body_asset", "description_asset", "call_to_action_asset", "link_url_asset", "ad_format_asset",
              "action_device", "action_destination", "action_reaction", "action_video_sound", "action_video_type", "action_carousel_card_id", "action_carousel_card_name",
              "conversion_destination", "mmm", "skan_campaign_id"):
        assert n in names, n


def test_every_entry_builds_a_valid_ad_level_request():
    for e in mb.CATALOG:
        p = mb.build_params(e, "ad", "TOK", D(2026, 11, 1), D(2026, 11, 7))
        assert p["level"] == "ad" and p["time_increment"] == 1 and p["limit"] == mb.LIMIT
        assert json.loads(p["time_range"]) == {"since": "2026-11-01", "until": "2026-11-07"}
        for f in ("ad_id", "adset_id", "campaign_id", "date_start"):
            assert f in p["fields"].split(","), (e.name, f)
        if e.action:
            assert "breakdowns" not in p and p["action_breakdowns"] == "action_type," + ",".join(e.breakdowns)
            assert "spend" not in p["fields"].split(",")
        else:
            assert p["breakdowns"] == ",".join(e.breakdowns) and "action_breakdowns" not in p
            assert "reach" in p["fields"].split(",") and "unique_outbound_clicks" in p["fields"].split(",")
        assert len(e.breakdowns) <= mb.MAX_DIMS


def test_every_set_is_pulled_at_ad_level_and_cheap_sets_run_first_heavy_last():
    order = [e.name for e in mb.select_sets(None)]
    assert len(order) == len(mb.CATALOG)
    cheap = ["user_segment_key", "age", "gender", "age,gender", "country", "publisher_platform", "platform_position", "impression_device", "device_platform"]
    heavy = ["region", "dma", "hourly_stats_aggregated_by_advertiser_time_zone", "hourly_stats_aggregated_by_audience_time_zone"]
    assert max(order.index(n) for n in cheap) < min(order.index(n) for n in heavy)
    assert max(order.index(n) for n in cheap) < len(order) // 3 and min(order.index(n) for n in heavy) > len(order) - 15
    assert all(mb.BY_NAME[n].chunk_days == 1 for n in heavy)
    assert mb.fields_for(mb.BY_NAME["region"], "campaign").startswith("campaign_id,spend")          # lower levels only on a refusal


def test_dims_key_is_stable_and_escaped():
    assert mb.dims_key({"gender": "female", "age": "25-34"}) == mb.dims_key({"age": "25-34", "gender": "female"}) == "age=25-34|gender=female"
    assert mb.dims_key({"a": "x|y", "b": "p=q"}) != mb.dims_key({"a": "x", "b": "y|b=p=q"})
    assert mb.dims_key({"title_asset": {"text": "Hi", "id": "1"}}) == mb.dims_key({"title_asset": {"id": "1", "text": "Hi"}})
    assert mb.dims_key({"a": None}) == "a="


def test_parse_sets_arg():
    assert mb.parse_sets_arg("age;age+gender; region") == ["age", "age,gender", "region"]
    assert mb.parse_sets_arg(None) is None
    with pytest.raises(SystemExit):
        mb.select_sets(["nope"])


def test_module_never_touches_meta_write():
    src = open(mb.__file__).read()
    assert not re.search(r"^\s*(from|import)\s+.*meta_write", src, re.M) and "from . import" in src and "meta_write" not in re.findall(r"from \. import ([^\n]+)", src)[0]


# ----------------------------------------------------------------------------------------------- pulling, paging, upsert
def test_pull_pages_and_stores_every_row_with_ids_and_basics(bd, monkeypatch):
    fake = Fake().install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 2))], ["age,gender"])
    assert st["sets_ok"] == 1 and st["levels"] == {"age,gender": "ad"}
    n = 2 * 3 * 4                                                               # days x ads x (2 ages x 2 genders)
    assert count(bd) == n and st["rows"] == n
    assert len([c for c in fake.calls if c[0] == "GET"]) > 2                    # paged (2 rows per page)
    r = bd.execute("SELECT level, entity_id, campaign_id, adset_id, dim1, dim2, dim3, spend, impressions, reach, clicks, inline_link_clicks, outbound_clicks, "
                   "unique_outbound_clicks, purchases, purchase_value, add_to_cart, initiate_checkout FROM meta_breakdown_daily WHERE entity_id='a3' AND dims_key='age=18-24|gender=female' AND day='2026-11-01'").fetchone()
    assert r == ("ad", "a3", "c2", "s2", "18-24", "female", None, 10, 1000, 900, 30, 20, 15, 12, 2, 100, 7, 4)       # omni add to cart wins over the plain one
    assert all(c[0] == "GET" for c in fake.calls)
    assert all("access_token" not in c[2] for c in fake.calls)


def test_upsert_is_idempotent_and_refreshes_values(bd, monkeypatch):
    Fake().install(monkeypatch)
    w = [(D(2026, 11, 1), D(2026, 11, 3))]
    run(bd, w, ["age"])
    n1 = count(bd)
    t1 = bd.execute("SELECT min(pulled_at) FROM meta_breakdown_daily").fetchone()[0]
    bd.execute("UPDATE meta_breakdown_daily SET spend = 0")
    bd.commit()
    run(bd, w, ["age"])
    assert count(bd) == n1 and bd.execute("SELECT min(spend) FROM meta_breakdown_daily").fetchone()[0] == 10
    assert bd.execute("SELECT min(pulled_at) FROM meta_breakdown_daily").fetchone()[0] > t1
    assert mb.dedupe([("x",) * 22, ("x",) * 22])[1] == 1


def test_action_set_stores_purchases_only(bd, monkeypatch):
    Fake().install(monkeypatch)
    run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["action_device"])
    rows = bd.execute("SELECT dim1, spend, impressions, reach, purchases, purchase_value FROM meta_breakdown_daily WHERE entity_id='a1' ORDER BY dim1").fetchall()
    assert rows == [("desktop", None, None, None, 2, 20), ("iphone", None, None, None, 1, 10)]


def test_partitions_are_created_for_later_months(bd):
    assert mb.ensure_partitions(bd, D(2029, 2, 15), D(2029, 4, 1)) == 3
    bd.execute("INSERT INTO meta_breakdown_daily (day, account_id, level, entity_id, breakdown_set, dims_key) VALUES ('2029-03-05','act_1','ad','a','age','age=1')")
    assert bd.execute("SELECT tableoid::regclass::text FROM meta_breakdown_daily").fetchone()[0] == "meta_breakdown_daily_2029_03"
    bd.execute("INSERT INTO meta_breakdown_daily (day, account_id, level, entity_id, breakdown_set, dims_key) VALUES ('2020-01-01','act_1','ad','a','age','age=2')")
    assert bd.execute("SELECT tableoid::regclass::text FROM meta_breakdown_daily WHERE day='2020-01-01'").fetchone()[0] == "meta_breakdown_daily_default"
    bd.rollback()


# ----------------------------------------------------------------------------------------------- refusals and fallbacks
def refuse(levels_refused, names=None):
    def h(p, acct):
        if p.get("level") in levels_refused and (names is None or p.get("breakdowns") in names):
            return err(100, "(#100) Invalid parameter: breakdown combination is not valid for this level")
    return h


def test_unsupported_set_is_recorded_and_skipped_others_continue(bd, monkeypatch):
    Fake(refuse(set(LV := ("ad", "campaign", "account")), {"gender"})).install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age", "gender", "country"])
    assert st["sets_ok"] == 2 and st["sets_unsupported"] == 1 and st["errors"]["gender"] == "unsupported at every level"
    cov = {(r[0], r[1]): r[2] for r in bd.execute("SELECT breakdown_set, level, status FROM meta_breakdown_coverage")}
    assert cov[("gender", "ad")] == cov[("gender", "campaign")] == cov[("gender", "account")] == "unsupported"
    assert cov[("age", "ad")] == cov[("country", "ad")] == "ok"
    assert "token" not in bd.execute("SELECT last_error FROM meta_breakdown_coverage WHERE breakdown_set='gender' AND level='ad'").fetchone()[0].lower()


def test_ad_level_refusal_falls_back_to_campaign_and_records_the_reason(bd, monkeypatch):
    Fake(refuse({"ad"}, {"region"})).install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 2))], ["region"])
    assert st["levels"] == {"region": "campaign"} and st["sets_ok"] == 1
    rows = bd.execute("SELECT level, entity_id, campaign_id, adset_id FROM meta_breakdown_daily").fetchall()
    assert rows and {r[0] for r in rows} == {"campaign"} and all(r[3] is None and r[2] == r[1] for r in rows)
    cov = {r[1]: (r[2], r[5], r[6]) for r in bd.execute("SELECT breakdown_set, level, status, first_day, last_day, last_error, rows FROM meta_breakdown_coverage")}
    assert cov["ad"][0] == "unsupported" and "100" in cov["ad"][1] and cov["campaign"][0] == "ok" and cov["campaign"][2] == len(rows)


def test_account_level_fallback_and_recently_unsupported_is_not_asked_again(bd, monkeypatch):
    f = Fake(refuse({"ad", "campaign"}, {"dma"})).install(monkeypatch)
    run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["dma"])
    assert bd.execute("SELECT DISTINCT level FROM meta_breakdown_daily").fetchall() == [("account",)]
    f.calls.clear()
    run(bd, [(D(2026, 11, 2), D(2026, 11, 2))], ["dma"])                            # named with sets: asked again at every level
    assert {c[2]["level"] for c in f.calls} >= {"ad", "campaign", "account"}
    recent = mb.recently_unsupported(bd)
    assert ("dma", "ad") in recent and ("dma", "campaign") in recent and ("dma", "account") not in recent


def test_field_ladder_retries_without_reach_before_giving_up(bd, monkeypatch):
    def h(p, acct):
        if "reach" in p["fields"].split(","):
            return err(100, "(#100) reach is not supported with this breakdown")
    f = Fake(h).install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["frequency_value"])
    assert st["sets_ok"] == 1 and count(bd, "SELECT count(*) FROM meta_breakdown_daily WHERE reach IS NULL") == count(bd)
    assert st["levels"]["frequency_value"] == "ad"


def test_token_rejection_stops_the_run(bd, monkeypatch):
    Fake(lambda p, a: err(190, "token expired", 401)).install(monkeypatch)
    with pytest.raises(meta_api.TokenRejected):
        run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age"])


def test_unexpected_error_is_recorded_not_fatal(bd, monkeypatch):
    Fake(lambda p, a: err(2635, "something else", 400) if p.get("breakdowns") == "gender" else None).install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["gender", "age"])
    assert st["sets_error"] == 1 and st["sets_ok"] == 1
    assert bd.execute("SELECT status FROM meta_breakdown_coverage WHERE breakdown_set='gender' AND level='ad'").fetchone()[0] == "error"


# ----------------------------------------------------------------------------------------------- rate limits, size
def test_rate_limit_backs_off_then_succeeds(bd, monkeypatch):
    n = {"k": 0}

    def h(p, acct):
        if n["k"] < 2:
            n["k"] += 1
            return err(80004, "There have been too many calls to this ad-account", 400)
    Fake(h).install(monkeypatch)
    c, sleeps = client()
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age"], c=c)
    assert st["sets_ok"] == 1 and 60 in sleeps and 120 in sleeps
    assert [s for s in sleeps if s > 1] == [60, 120]


def test_rate_limit_that_outlasts_retries_sleeps_and_continues(bd, monkeypatch):
    n = {"k": 0}

    def h(p, acct):
        if n["k"] < 3:
            n["k"] += 1
            return err(80004, "too many calls")
    Fake(h).install(monkeypatch)
    c, sleeps = client(max_retries=0)                                              # the client gives up at once, the run waits RATE_WAIT and tries again
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age"], c=c, max_rate_waits=5)
    assert st["sets_ok"] == 1 and st["rate_waits"] == 3 and sleeps.count(mb.RATE_WAIT) == 3


def test_rate_limit_gives_up_after_max_waits(bd, monkeypatch):
    Fake(lambda p, a: err(80004, "too many calls")).install(monkeypatch)
    c, _ = client(max_retries=0)
    with pytest.raises(meta_api.RateLimited):
        run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age"], c=c, max_rate_waits=2)


def test_too_big_window_is_halved(bd, monkeypatch):
    def h(p, acct):
        tr = json.loads(p["time_range"])
        if tr["since"] != tr["until"]:
            return err(1, "Please reduce the amount of data you're asking for")
    f = Fake(h).install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 4))], ["age"])
    assert st["sets_ok"] == 1 and count(bd, "SELECT count(DISTINCT day) FROM meta_breakdown_daily") == 4 and st["async_sets"] == []
    assert {c[0] for c in f.calls} == {"GET"}


def test_one_day_too_big_uses_the_async_report_only_then(bd, monkeypatch):
    day = {"since": "2026-11-01", "until": "2026-11-01"}
    f = Fake()
    f.async_rows["rows"] = f.rows({"level": "ad", "breakdowns": "region", "time_range": json.dumps(day), "fields": ",".join(mb.BASE_FIELDS + ["ad_id", "adset_id", "campaign_id"])})

    def handler(p, acct):
        if p.get("breakdowns") == "region" and p.get("level") == "ad" and not f.posted:        # plain GETs of this set are too big until a report run exists
            return err(1, "Please reduce the amount of data you're asking for")
    f.handler, f.posted = handler, False
    real_post = f.post

    def post(url, data=None, timeout=None):
        f.posted = True
        return real_post(url, data, timeout)
    f.install(monkeypatch)
    monkeypatch.setattr(requests, "post", post)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["region"])
    assert st["async_sets"] == ["region"] and st["sets_ok"] == 1
    posts = [c for c in f.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][1].endswith("/insights") and count(bd) == len(f.async_rows["rows"]) > 0


# ----------------------------------------------------------------------------------------------- the backfill
def test_windows_run_newest_first_and_the_last_one_is_short():
    w = mb.windows_newest_first(D(2026, 1, 1), D(2026, 3, 15), 30)
    assert w[0] == (D(2026, 2, 14), D(2026, 3, 15)) and w[1] == (D(2026, 1, 15), D(2026, 2, 13)) and w[2] == (D(2026, 1, 1), D(2026, 1, 14))
    assert len(mb.windows_newest_first(D(2025, 11, 11), D(2026, 11, 10), 30)) == 13           # 365 days
    assert mb.DEFAULT_MAX_DAYS == 365


def test_backfill_commits_each_window_newest_first_and_updates_coverage(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    w = mb.windows_newest_first(D(2026, 10, 1), D(2026, 11, 3), 15)                          # (10-20..11-03) (10-05..10-19) (10-01..10-04)
    seen = []

    def pause(s):
        seen.append(bd.execute("SELECT min(day), max(day) FROM meta_breakdown_daily").fetchone())
    mb.run(bd, w, ["age"], client=client()[0], accounts=ONE, pause=pause, backfill=True)
    firsts = [c[2]["time_range"] for c in f.calls if c[1].endswith("/insights") and "page" not in c[2]]
    starts = [json.loads(t)["since"] for t in firsts]
    win = [0 if x >= "2026-10-20" else 1 if x >= "2026-10-05" else 2 for x in starts]
    assert win == sorted(win) and set(win) == {0, 1, 2}                                         # every request of window 1, then window 2, then window 3
    assert seen[0][0] >= D(2026, 10, 20) and seen[-1][0] == D(2026, 10, 1)                       # committed and visible after each window, moving back
    assert bd.execute("SELECT first_day, last_day FROM meta_breakdown_coverage WHERE breakdown_set='age' AND level='ad'").fetchone() == (D(2026, 10, 1), D(2026, 11, 3))
    assert count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set NOT LIKE '\\_\\_chunk%'") == 3


def test_resume_skips_finished_windows_and_finishes_the_rest(bd, monkeypatch):
    w = mb.windows_newest_first(D(2026, 10, 1), D(2026, 11, 3), 15)
    state = {"fail": True}

    def h(p, acct):
        tr = json.loads(p["time_range"])
        if state["fail"] and D.fromisoformat(tr["since"]) < D(2026, 10, 20):
            return err(80004, "too many calls")
    f = Fake(h).install(monkeypatch)
    c, _ = client(max_retries=0)
    with pytest.raises(meta_api.RateLimited):
        mb.run(bd, w, ["age", "gender"], client=c, accounts=ONE, pause=lambda s: None, backfill=True, max_rate_waits=0)
    assert bd.execute("SELECT min(day) FROM meta_breakdown_daily").fetchone()[0] >= D(2026, 10, 20)           # window 1 is committed and visible
    assert bd.execute("SELECT first_day FROM meta_breakdown_coverage WHERE breakdown_set='age'").fetchone()[0] >= D(2026, 10, 20)
    assert count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set NOT LIKE '\\_\\_chunk%'") == 2                                      # age and gender of window 1
    state["fail"] = False
    f.calls.clear()
    st = mb.run(bd, w, ["age", "gender"], client=client()[0], accounts=ONE, pause=lambda s: None, backfill=True)
    assert st["windows_done"] == 3 and count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set NOT LIKE '\\_\\_chunk%'") == 6
    assert bd.execute("SELECT min(day), max(day) FROM meta_breakdown_daily").fetchone() == (D(2026, 10, 1), D(2026, 11, 3))
    requested = {json.loads(c[2]["time_range"])["since"] for c in f.calls if "time_range" in c[2]}
    assert "2026-10-20" not in requested and "2026-11-03" not in requested                                     # window 1 was not pulled again


def test_restart_ignores_progress(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    w = [(D(2026, 11, 1), D(2026, 11, 2))]
    mb.run(bd, w, ["age"], client=client()[0], accounts=ONE, pause=lambda s: None, backfill=True)
    n = len(f.calls)
    st = mb.run(bd, w, ["age"], client=client()[0], accounts=ONE, pause=lambda s: None, backfill=True)
    assert len(f.calls) == n and st["windows_done"] == 1                                        # finished window: no request at all
    mb.run(bd, w, ["age"], client=client()[0], accounts=ONE, pause=lambda s: None, backfill=True, restart=True)
    assert len(f.calls) > n


def test_retention_limit_stops_the_set_and_is_recorded_not_fatal(bd, monkeypatch):
    def h(p, acct):
        if D.fromisoformat(json.loads(p["time_range"])["until"]) < D(2026, 10, 2):
            return err(100, "(#100) The start date is beyond the retention period for unique metrics")
    Fake(h).install(monkeypatch)
    w = mb.windows_newest_first(D(2026, 9, 1), D(2026, 10, 31), 30)
    st = mb.run(bd, w, ["age", "gender"], client=client()[0], accounts=ONE, pause=lambda s: None, backfill=True)
    assert st["sets_ok"] == 2 and st["windows_done"] == len(w)
    r = bd.execute("SELECT status, first_day, last_error FROM meta_breakdown_coverage WHERE breakdown_set='age' AND level='ad'").fetchone()
    assert r[0] == "ok" and r[1] == D(2026, 10, 2) and "retention" in r[2] and "2026-10-02" in r[2]


def test_budget_stops_before_the_next_set(bd, monkeypatch):
    Fake().install(monkeypatch)
    n = {"k": 0}
    stopper = mb.Stopper(1, None, clock=lambda: 0 if n["k"] < 1 else 10_000)                  # one minute budget; the clock jumps after the first set
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age", "gender", "country"], client=client()[0], accounts=ONE, stopper=stopper,
                pause=lambda s: n.__setitem__("k", n["k"] + 1))
    assert st["stopped_early"] and "time budget" in st["stop_reason"] and st["sets_ok"] == 1 and count(bd) > 0


def test_job_is_recorded_and_main_runs_nightly_window(bd, monkeypatch):
    Fake().install(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setattr(mb.meta_api, "get_token", lambda: "TOKEN")
    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    assert mb.main(["--sets", "age", "--days", "2", "--until", "2026-11-05"]) == 0
    row = bd.execute("SELECT status, rows_written, data_through, stats->>'sets_ok' FROM etl_runs WHERE job='meta_breakdowns'").fetchone()
    assert row == ("ok", count(bd), D(2026, 11, 5), "1") and count(bd, "SELECT count(*) FROM meta_breakdown_progress") == 0       # nightly mode does not write progress


def test_probe_reports_shape_and_row_estimate_at_ad_level(monkeypatch):
    Fake(refuse({"ad"}, {"dma"})).install(monkeypatch)
    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    c, _ = client(max_retries=0)
    out = []
    res = mb.probe(c, ACCT[0], ACCT[1], D(2026, 11, 8), [mb.BY_NAME[n] for n in ("age", "dma", "action_device")], sample_ads=2, out=out.append)
    by = {r["set"]: r for r in res}
    assert by["age"]["accepted"] and by["age"]["level"] == "ad" and by["age"]["rows_per_day_est"] == 3 * 2 and by["age"]["dims_sample"] == {"age": "18-24"}
    assert by["dma"]["level"] == "campaign" and "ad" in by["dma"]["refused_levels"]
    assert by["action_device"]["rows_per_day_est"] == 6
    assert mb.backfill_estimate(1000, 365) == {"rows": 365000, "calls": 730 + 53}


# ----------------------------------------------------------------------------------------------- quiet hours, budget, lock
UTC = dt.timezone.utc


def test_quiet_window_parsing_and_membership():
    assert mb.parse_quiet("12:30-15:30") == (750, 930) and mb.parse_quiet("off") is None and mb.parse_quiet("") is None and mb.parse_quiet(None) is None
    q = mb.parse_quiet("12:30-15:30")
    assert mb.in_quiet(q, dt.datetime(2026, 11, 9, 12, 30, tzinfo=UTC)) and mb.in_quiet(q, dt.datetime(2026, 11, 9, 15, 29, tzinfo=UTC))
    assert not mb.in_quiet(q, dt.datetime(2026, 11, 9, 12, 29, tzinfo=UTC)) and not mb.in_quiet(q, dt.datetime(2026, 11, 9, 15, 30, tzinfo=UTC))
    assert mb.in_quiet(q, dt.datetime(2026, 11, 9, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8))))                  # 05:00 PST is 13:00 UTC: the nightly pull
    wrap = mb.parse_quiet("22:00-02:00")
    assert mb.in_quiet(wrap, dt.datetime(2026, 11, 9, 23, 0, tzinfo=UTC)) and mb.in_quiet(wrap, dt.datetime(2026, 11, 9, 1, 0, tzinfo=UTC)) and not mb.in_quiet(wrap, dt.datetime(2026, 11, 9, 3, 0, tzinfo=UTC))
    with pytest.raises(SystemExit):
        mb.parse_quiet("noon")


def test_no_request_starts_in_the_quiet_hours(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    stopper = mb.Stopper(None, (750, 930), now=lambda: dt.datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age"], client=client()[0], accounts=ONE, stopper=stopper, pause=lambda s: None)
    assert st["stopped_early"] and "quiet" in st["stop_reason"] and f.calls == [] and count(bd) == 0


def test_quiet_hours_beginning_mid_run_stop_cleanly_commit_and_resume(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    w = mb.windows_newest_first(D(2026, 10, 1), D(2026, 11, 3), 15)
    clock = {"t": dt.datetime(2026, 11, 9, 10, 0, tzinfo=UTC)}
    stopper = mb.Stopper(None, (750, 930), now=lambda: clock["t"])
    st = mb.run(bd, w, ["age", "gender"], client=client()[0], accounts=ONE, stopper=stopper, backfill=True,
                pause=lambda s: clock.__setitem__("t", dt.datetime(2026, 11, 9, 12, 31, tzinfo=UTC)) if f.calls else None)         # quiet begins after the first set
    assert st["stopped_early"] and "quiet" in st["stop_reason"]
    n_calls = len(f.calls)
    assert bd.execute("SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set NOT LIKE '\\_\\_chunk%'").fetchone()[0] == 1                       # the first set of window 1 is recorded
    assert bd.execute("SELECT first_day, status FROM meta_breakdown_coverage WHERE breakdown_set='age'").fetchone()[0] >= D(2026, 10, 20)
    st2 = mb.run(bd, w, ["age", "gender"], client=client()[0], accounts=ONE, stopper=mb.Stopper(None, None), backfill=True, pause=lambda s: None)
    assert not st2["stopped_early"] and st2["windows_done"] == 3 and len(f.calls) > n_calls
    assert bd.execute("SELECT min(day), max(day) FROM meta_breakdown_daily").fetchone() == (D(2026, 10, 1), D(2026, 11, 3))


def test_main_auto_in_quiet_hours_exits_zero_and_starts_nothing(bd, monkeypatch, capsys):
    f = Fake().install(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setattr(mb.meta_api, "get_token", lambda: "TOKEN")
    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    now = dt.datetime.now(UTC)
    spec = f"{(now - dt.timedelta(hours=1)):%H:%M}-{(now + dt.timedelta(hours=1)):%H:%M}"
    monkeypatch.setenv("META_BREAKDOWNS_QUIET_UTC", spec)
    assert mb.main(["--auto"]) == 0
    assert f.calls == [] and "quiet hours" in capsys.readouterr().out
    assert count(bd, "SELECT count(*) FROM etl_runs WHERE job='meta_breakdowns'") == 0
    monkeypatch.setenv("META_BREAKDOWNS_QUIET_UTC", "off")                                    # the override string 'off' (or a window that is not now) lets it run
    assert mb.main(["--auto", "--sets", "age", "--max-days", "2", "--max-minutes", "5"]) == 0 and f.calls


def test_second_run_exits_zero_when_the_advisory_lock_is_held(bd, monkeypatch, capsys):
    import psycopg
    f = Fake().install(monkeypatch)
    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    monkeypatch.setattr(mb.meta_api, "get_token", lambda: "TOKEN")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("META_BREAKDOWNS_QUIET_UTC", "off")
    with psycopg.connect(TEST_URL, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (mb.LOCK_KEY,))
        assert mb.main(["--auto"]) == 0
        out = [line for line in capsys.readouterr().out.splitlines() if "lock" in line]
        assert len(out) == 1 and f.calls == [] and count(bd, "SELECT count(*) FROM etl_runs WHERE job='meta_breakdowns'") == 0
        other.execute("SELECT pg_advisory_unlock(%s)", (mb.LOCK_KEY,))
    assert mb.main(["--auto", "--sets", "age", "--max-days", "1"]) == 0 and f.calls             # free again: it runs


def test_wall_clock_budget_ends_a_run_with_exit_zero(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("META_BREAKDOWNS_QUIET_UTC", "off")
    monkeypatch.setattr(mb.meta_api, "get_token", lambda: "TOKEN")
    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    ticks = iter(range(0, 10**6, 4000))                                                       # each clock read jumps 4000 s: the 1 minute budget is gone after one check
    monkeypatch.setattr(mb.time, "monotonic", lambda: next(ticks))
    assert mb.main(["--auto", "--max-minutes", "1", "--max-days", "40"]) == 0
    r = bd.execute("SELECT status, stats->>'stopped_early' FROM etl_runs WHERE job='meta_breakdowns'").fetchone()
    assert r == ("ok", "true")


# ----------------------------------------------------------------------------------------------- --auto: anchor, catch-up, finish
def auto(conn, today, stopper=None, **kw):
    kw.setdefault("names", ["age"])
    return mb.auto(conn, stopper or mb.Stopper(None, None), max_days=kw.pop("max_days", 40), window_days=15, client=client()[0], accounts=ONE, today=today, pause=lambda s: None, **kw)


def test_auto_stores_the_anchor_resumes_without_arguments_catches_up_then_finishes(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    n = {"k": 0}
    stop_after_first = mb.Stopper(1, None, clock=lambda: 0 if n["k"] < 1 else 10_000)
    st = mb.auto(bd, stop_after_first, ["age"], max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 10), pause=lambda s: n.__setitem__("k", n["k"] + 1))
    assert st["stopped_early"] and not st["backfill_done"]
    assert mb.get_anchor(bd) == (D(2026, 10, 1), D(2026, 11, 9), 40)                      # stored on first start
    assert bd.execute("SELECT min(day) FROM meta_breakdown_daily").fetchone()[0] >= D(2026, 10, 26)       # window 1 (newest 15 days) is in
    f.calls.clear()
    st = auto(bd, D(2026, 11, 12), max_days=999)                                          # two nights later, no arguments needed: the stored anchor wins over max_days
    first = json.loads([c for c in f.calls if "time_range" in c[2]][0][2]["time_range"])
    assert first["since"] == "2026-11-05" and first["until"] == "2026-11-11"              # catch-up first (trailing 7 up to yesterday), before going further back; request chunks of 7 days
    assert max(json.loads(c[2]["time_range"])["until"] for c in f.calls if "time_range" in c[2]) == "2026-11-11"
    assert st["backfill_done"] and mb.get_anchor(bd) == (D(2026, 10, 1), D(2026, 11, 9), 40)
    assert bd.execute("SELECT min(day), max(day) FROM meta_breakdown_daily").fetchone() == (D(2026, 10, 1), D(2026, 11, 11))
    assert bd.execute("SELECT window_end FROM meta_breakdown_progress WHERE breakdown_set='__caught_up__'").fetchone()[0] == D(2026, 11, 11)
    assert bd.execute("SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set='__backfill_done__'").fetchone()[0] == 1


def test_auto_after_the_backfill_pulls_only_the_new_days_and_the_trailing_seven_once_a_day(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    auto(bd, D(2026, 11, 10))                                                              # whole backfill in one run
    assert bd.execute("SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set='__backfill_done__'").fetchone()[0] == 1
    f.calls.clear()
    st = auto(bd, D(2026, 11, 11))                                                         # next day: yesterday = 11-10, trailing 7 = 11-04..11-10
    days = [json.loads(c[2]["time_range"]) for c in f.calls if "time_range" in c[2]]
    assert min(d["since"] for d in days) == "2026-11-04" and max(d["until"] for d in days) == "2026-11-10" and "backfill" not in st
    f.calls.clear()
    auto(bd, D(2026, 11, 11))                                                              # a second run the same day: nothing to do
    assert f.calls == []
    auto(bd, D(2026, 11, 20))                                                              # service was down: everything since the last catch-up, not just 7 days
    days = [json.loads(c[2]["time_range"]) for c in f.calls if "time_range" in c[2]]
    assert min(d["since"] for d in days) == "2026-11-11" and max(d["until"] for d in days) == "2026-11-19"


def test_auto_never_starts_older_windows_before_the_gap_to_yesterday_is_current(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    n = {"k": 0}
    mb.auto(bd, mb.Stopper(1, None, clock=lambda: 0 if n["k"] < 1 else 10_000), ["age"], max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 10),
            pause=lambda s: n.__setitem__("k", n["k"] + 1))
    f.calls.clear()
    stopper = mb.Stopper(1, None, clock=lambda: 0 if n["k"] < 3 else 10_000)              # stop right after the catch-up set
    n["k"] = 2
    st = mb.auto(bd, stopper, ["age"], max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 12), pause=lambda s: n.__setitem__("k", n["k"] + 1))
    days = {json.loads(c[2]["time_range"])["since"] for c in f.calls if "time_range" in c[2]}
    assert st["stopped_early"] and "catch_up" in st and days <= {"2026-11-05", "2026-11-10", "2026-11-06", "2026-11-11"}


def test_restart_resets_the_anchor_and_progress(bd, monkeypatch):
    Fake().install(monkeypatch)
    auto(bd, D(2026, 11, 10))
    assert mb.get_anchor(bd) is not None and count(bd, "SELECT count(*) FROM meta_breakdown_progress") > 0
    mb.reset_progress(bd)
    assert mb.get_anchor(bd) is None and count(bd, "SELECT count(*) FROM meta_breakdown_progress") == 0
    auto(bd, D(2026, 11, 20), max_days=20)
    assert mb.get_anchor(bd) == (D(2026, 10, 31), D(2026, 11, 19), 20)


def test_stored_anchor_is_one_row_per_kind(bd, monkeypatch):
    Fake().install(monkeypatch)
    auto(bd, D(2026, 11, 10))
    auto(bd, D(2026, 11, 11))
    auto(bd, D(2026, 11, 12))
    kinds = dict(bd.execute("SELECT breakdown_set, count(*) FROM meta_breakdown_progress WHERE level='-' GROUP BY 1").fetchall())
    assert kinds == {"__anchor__": 1, "__backfill_done__": 1, "__caught_up__": 1}


# ----------------------------------------------------------------------------------------------- long values and per-set failure (migration 241)
def _long_rows(values, day=D(2026, 11, 1)):
    e = mb.BY_NAME["body_asset"]
    raw = [{"ad_id": "a1", "adset_id": "s1", "campaign_id": "c1", "date_start": day.isoformat(), "body_asset": {"id": "1", "text": v}, "spend": "1.00",
            "impressions": "10"} for v in values]
    return mb.normalise(e, "ad", ACCT, raw)


def test_value_over_5000_characters_upserts_and_is_idempotent(bd):
    rows = _long_rows(["Long ad copy. " * 400])
    assert len(rows[0][10]) > 5000
    mb.upsert(bd, rows)
    bd.commit()
    mb.upsert(bd, rows)
    bd.commit()
    assert count(bd) == 1
    assert bd.execute("SELECT length(dims_key) FROM meta_breakdown_daily").fetchone()[0] > 5000


def test_long_values_sharing_a_long_prefix_stay_distinct(bd):
    pre = "same long opening " * 300
    rows = _long_rows([pre + "ending A", pre + "ending B"])
    mb.upsert(bd, rows)
    bd.commit()
    assert count(bd) == 2
    mb.upsert(bd, rows)
    bd.commit()
    assert count(bd) == 2


def test_a_set_that_raises_does_not_stop_later_sets_and_is_recorded_errored(bd, monkeypatch):
    Fake().install(monkeypatch)
    real = mb.upsert

    def flaky(conn, rows):
        if rows and rows[0][6] == "gender":
            raise RuntimeError("index row size 3104 exceeds btree version 4 maximum 2704")
        return real(conn, rows)
    monkeypatch.setattr(mb, "upsert", flaky)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["gender", "age"])
    assert st["sets_error"] == 1 and st["sets_ok"] == 1 and st["error_pieces"] == 1
    r = bd.execute("SELECT status, last_error FROM meta_breakdown_coverage WHERE breakdown_set='gender' AND level='ad'").fetchone()
    assert r[0] == "error" and "RuntimeError" in r[1]
    assert bd.execute("SELECT status FROM meta_breakdown_coverage WHERE breakdown_set='age' AND level='ad'").fetchone()[0] == "ok"
    assert count(bd, "SELECT count(*) FROM meta_breakdown_daily WHERE breakdown_set='age'") > 0


def test_failed_set_keeps_the_backfill_open_so_a_later_run_retries_it(bd, monkeypatch):
    Fake().install(monkeypatch)
    real = mb.upsert
    broken = {"on": True}

    def flaky(conn, rows):
        if broken["on"] and rows and rows[0][6] == "gender":
            raise RuntimeError("boom")
        return real(conn, rows)
    monkeypatch.setattr(mb, "upsert", flaky)
    today = D(2026, 11, 12)
    out = auto(bd, today, names=["gender", "age"], max_days=2)
    assert not out["backfill_done"]
    broken["on"] = False
    out = auto(bd, today, names=["gender", "age"], max_days=2)
    assert out["backfill_done"]
    assert count(bd, "SELECT count(*) FROM meta_breakdown_daily WHERE breakdown_set='gender'") > 0


# ----------------------------------------------------------------------------------------------- pacing: resumable catch-up, time share, dead sets, per-set budget, heavy sets last
def calls_by_set(f):
    """(breakdowns, since) of every plain insights GET, in order."""
    return [(c[2].get("breakdowns", c[2].get("action_breakdowns")), json.loads(c[2]["time_range"])["since"]) for c in f.calls if c[0] == "GET" and "time_range" in c[2]]


def stopper_after(k_sets, n):
    return mb.Stopper(1, None, clock=lambda: 0 if n["k"] < k_sets else 10_000)


def test_catch_up_resumes_and_skips_sets_already_refreshed_today(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    w = [(D(2026, 11, 5), D(2026, 11, 11))]
    n = {"k": 0}
    st = mb.run(bd, w, ["age", "gender", "country"], client=client()[0], accounts=ONE, catchup_day=D(2026, 11, 11), stopper=stopper_after(1, n), pause=lambda s: n.__setitem__("k", n["k"] + 1))
    assert st["stopped_early"] and {b for b, _ in calls_by_set(f)} == {"age"}               # stopped after the first set
    f.calls.clear()
    run(bd, w, ["age", "gender", "country"], catchup_day=D(2026, 11, 11))                    # same day again: age is skipped, the rest is pulled
    assert {b for b, _ in calls_by_set(f)} == {"gender", "country"}
    f.calls.clear()
    run(bd, w, ["age", "gender", "country"], catchup_day=D(2026, 11, 11))                    # and a third time: nothing at all
    assert calls_by_set(f) == []
    run(bd, w, ["age", "gender", "country"], catchup_day=D(2026, 11, 12))                    # next day: everything again
    assert {b for b, _ in calls_by_set(f)} == {"age", "gender", "country"}
    assert count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set LIKE '\\_\\_catchup\\_\\_%' AND level NOT LIKE '%:2026-11-12'") > 0
    mb.clear_old_catchup_stamps(bd, D(2026, 11, 12))
    assert count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set LIKE '\\_\\_catchup\\_\\_%' AND level NOT LIKE '%:2026-11-12'") == 0


def test_auto_catch_up_that_did_not_finish_resumes_in_the_next_run(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    auto(bd, D(2026, 11, 10), names=["age", "gender"])                                       # whole backfill
    n = {"k": 0}
    st = mb.auto(bd, stopper_after(1, n), ["age", "gender"], max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 12), pause=lambda s: n.__setitem__("k", n["k"] + 1))
    assert st["stopped_early"] and mb._special(bd, mb.CAUGHT_SET) is None      # not marked caught up
    f.calls.clear()
    st = auto(bd, D(2026, 11, 12), names=["age", "gender"])
    assert {b for b, _ in calls_by_set(f)} == {"gender"} and mb._special(bd, mb.CAUGHT_SET)[1] == D(2026, 11, 11)


def test_backfill_gets_time_when_the_catch_up_is_unfinished(bd, monkeypatch, capsys):
    f = Fake().install(monkeypatch)
    sets = ["age", "gender", "country", "device_platform", "impression_device", "publisher_platform", "frequency_value", "product_id"]
    t0 = {"t": 0}
    mb.auto(bd, mb.Stopper(100, None, clock=lambda: t0["t"]), sets, max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 10),
            pause=lambda s: t0.__setitem__("t", t0["t"] + 3000))                              # day one: stops after the first set of the backfill (50 min each)
    assert mb.get_anchor(bd) is not None
    t = {"t": 0}
    clock = lambda: t["t"]                                                                   # noqa: E731
    f.calls.clear()
    adv0 = mb.backfill_rows(bd)
    st = mb.auto(bd, mb.Stopper(100, None, clock=clock), sets, max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 12),
                 pause=lambda s: t.__setitem__("t", t["t"] + 600))                           # 10 minutes per set: the catch-up share is 40 of 100 minutes
    assert st["catch_up"]["stopped_early"] and "catch-up time share" in st["catch_up"]["stop_reason"] and mb._special(bd, mb.CAUGHT_SET) is None
    assert st["catch_up"]["sets_pulled"] == 5 and st["backfill"]["rows"] > 0 and mb.backfill_rows(bd) > adv0       # the backfill ran in the same run and advanced
    out = capsys.readouterr().out
    assert re.search(r"run start: phase=catch-up window=2026-11-05\.\.2026-11-11 sets_done=0 sets_remaining=8", out)
    assert re.search(r"run start: phase=backfill/light-newest-window window=\S+ sets_done=\d+ sets_remaining=\d+", out)
    assert re.search(r"run end: phase=backfill/\S+ window=\S+ sets_done=\d+ sets_remaining=\d+ backfill_advanced=yes", out)


def test_dead_sets_are_skipped_until_the_reprobe_day(bd, monkeypatch):
    f = Fake(refuse({"ad", "campaign", "account"}, {"gender"})).install(monkeypatch)
    ents = [mb.BY_NAME["gender"], mb.BY_NAME["age"]]
    mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None)
    f.calls.clear()
    mb.run(bd, [(D(2026, 11, 2), D(2026, 11, 2))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None)
    assert {b for b, _ in calls_by_set(f)} == {"age"}                                        # gender: no request at any of the three levels
    bd.execute("UPDATE meta_breakdown_coverage SET checked_at = now() - interval '8 days' WHERE breakdown_set = 'gender'")
    bd.commit()
    f.calls.clear()
    mb.run(bd, [(D(2026, 11, 3), D(2026, 11, 3))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None)
    assert {c[2]["level"] for c in f.calls if c[2].get("breakdowns") == "gender"} == {"ad", "campaign", "account"}      # asked again after the re-probe period
    assert mb.RECHECK_UNSUPPORTED_DAYS == 7


def shape_less(p, acct):
    if p.get("breakdowns") == "gender":
        return Resp({"data": [{"ad_id": "a1", "adset_id": "s1", "campaign_id": "c1", "date_start": "2026-11-01", "spend": "1", "impressions": "1"}]})


def test_a_set_whose_rows_never_carry_the_keys_is_quarantined_and_does_not_hold_the_backfill_open(bd, monkeypatch):
    f = Fake(shape_less).install(monkeypatch)
    ents = [mb.BY_NAME["gender"], mb.BY_NAME["age"]]
    kw = dict(client=None, accounts=ONE, entries=ents, pause=lambda s: None)
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, today=D(2026, 11, 12), **{**kw, "client": client()[0]})
    assert st["sets_error"] == 1 and st.get("error_pieces", 0) == 0                           # deterministic: not a retry-later error
    assert mb.quarantined_sets(bd, D(2026, 11, 12)) == {"gender": "shape"}
    f.calls.clear()
    mb.run(bd, [(D(2026, 11, 2), D(2026, 11, 2))], None, today=D(2026, 11, 13), **{**kw, "client": client()[0]})
    assert {b for b, _ in calls_by_set(f)} == {"age"}
    f.calls.clear()
    mb.run(bd, [(D(2026, 11, 3), D(2026, 11, 3))], None, today=D(2026, 11, 12) + dt.timedelta(days=mb.QUARANTINE_DAYS + 1), **{**kw, "client": client()[0]})
    assert {b for b, _ in calls_by_set(f)} == {"gender", "age"}                              # re-probed after the quarantine


def unavailable(p, acct):
    if p.get("breakdowns") == "age":
        return err(2, "Service temporarily unavailable")


def test_a_failing_set_is_bounded_per_run_then_quarantined_after_three_failing_runs(bd, monkeypatch):
    f = Fake(unavailable).install(monkeypatch)
    ents = [mb.BY_NAME["age"]]
    c, sleeps = client()
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=c, accounts=[ACCT, meta_api.ACCOUNTS[1]], entries=ents, pause=lambda s: None, today=D(2026, 11, 12))
    assert sum(sleeps) <= mb.TRANSIENT_BUDGET_S and len([x for x in f.calls if x[0] == "GET"]) == 4        # three retries (15+30+45 s), then the set is an error for this run, both accounts
    assert st["sets_error"] == 1 and "retry budget" in st["errors"]["age"] and mb.quarantined_sets(bd, D(2026, 11, 12)) == {}
    for _ in range(mb.FAIL_LIMIT - 1):
        mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 12))
    assert mb.quarantined_sets(bd, D(2026, 11, 12)) == {"age": "failing"}
    f.calls.clear()
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 13))
    assert f.calls == [] and st["quarantined_failing"] == 1 and st["sets_skipped_quarantined"] == 1


def test_a_clean_pull_clears_the_failing_count(bd, monkeypatch):
    f = Fake(unavailable).install(monkeypatch)
    ents = [mb.BY_NAME["age"]]
    mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 12))
    assert count(bd, "SELECT rows FROM meta_breakdown_progress WHERE breakdown_set = '__fails__:age'") == 1
    f.handler = None
    mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 12))
    assert count(bd, "SELECT count(*) FROM meta_breakdown_progress WHERE breakdown_set = '__fails__:age'") == 0


def test_a_set_too_big_for_one_day_goes_straight_to_async_afterwards_and_that_is_remembered(bd, monkeypatch):
    f = Fake()

    def handler(p, acct):
        if p.get("breakdowns") == "age" and p.get("level") == "ad" and not f.posted:
            return err(1, "Please reduce the amount of data you're asking for")
    f.handler, f.posted = handler, False
    real_post = f.post

    def post(url, data=None, timeout=None):
        f.posted = True
        return real_post(url, data, timeout)
    f.install(monkeypatch)
    monkeypatch.setattr(requests, "post", post)
    ents = [mb.BY_NAME["age"]]
    mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 2))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 12))
    assert mb.async_first_sets(bd) == {"age"}
    plain_before = [c for c in f.calls if c[0] == "GET" and "/act_" in c[1]]
    assert len(plain_before) == 2                                                           # whole span, then day one; day two goes straight to async
    f.calls.clear()
    mb.run(bd, [(D(2026, 11, 3), D(2026, 11, 4))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, today=D(2026, 11, 13))      # a later run: loaded from the database
    assert [c for c in f.calls if c[0] == "GET" and "/act_" in c[1]] == [] and len([c for c in f.calls if c[0] == "POST"]) == 1


def test_backfill_stages_put_the_four_heavy_sets_last_with_one_probe_on_the_newest_window():
    ws = mb.windows_newest_first(D(2026, 1, 1), D(2026, 4, 1), 30)
    stages = mb.backfill_stages(mb.select_sets(None), ws)
    assert [s[0] for s in stages] == ["light-newest-window", "heavy-probe", "light", "heavy"]
    assert stages[0][1] == stages[1][1] == ws[:1] and stages[2][1] == ws[1:] and stages[3][1] == ws
    assert {e.name for e in stages[1][2]} == set(mb.HEAVY_LAST) == {e.name for e in stages[3][2]}
    assert not set(mb.HEAVY_LAST) & {e.name for e in stages[0][2] + stages[2][2]} and len(stages[0][2]) + len(stages[1][2]) == len(mb.CATALOG)
    assert [e.tier for e in stages[0][2]] == sorted(e.tier for e in stages[0][2])             # cheap first inside a stage


def test_auto_pulls_light_sets_to_full_depth_before_heavy_ones_after_a_single_probe_window(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    sets = ["age", "gender", "region"]
    out = auto(bd, D(2026, 11, 10), names=sets, max_days=40)
    assert out["backfill_done"]
    order = []
    ws = mb.windows_newest_first(D(2026, 10, 1), D(2026, 11, 9), 15)
    for b, since in calls_by_set(f):
        wi = next(i for i, (a, z) in enumerate(ws) if a <= D.fromisoformat(since) <= z)
        if not order or order[-1] != (b, wi):
            order.append((b, wi))
    assert order == [("age", 0), ("gender", 0), ("region", 0), ("age", 1), ("gender", 1), ("age", 2), ("gender", 2), ("region", 1), ("region", 2)]
    assert mb._special(bd, mb.PROBE_SET) is not None


def test_heavy_probe_is_attempted_once_and_does_not_hold_the_light_backfill(bd, monkeypatch):
    f = Fake().install(monkeypatch)
    sets = ["age", "region"]
    t = {"t": 0}
    st = mb.auto(bd, mb.Stopper(100, None, clock=lambda: t["t"]), sets, max_days=40, window_days=15, client=client()[0], accounts=ONE, today=D(2026, 11, 10),
                 pause=lambda s: t.__setitem__("t", t["t"] + 1500))          # 25 minutes per set pull: the probe (25% = 25 minutes) is cut off after the first heavy window piece
    probe_done = mb._special(bd, mb.PROBE_SET) is not None
    assert probe_done and "backfill" in st
    f.calls.clear()
    t["t"] = 0
    auto(bd, D(2026, 11, 10), names=sets, max_days=40)
    assert mb._special(bd, mb.DONE_SET) is not None                                          # the heavy stage resumed the probe's leftovers and went on to the full depth
    assert bd.execute("SELECT min(day) FROM meta_breakdown_daily WHERE breakdown_set = 'region'").fetchone()[0] == D(2026, 10, 1)


def test_per_set_time_numbers_are_kept_in_the_run_stats(bd, monkeypatch):
    Fake().install(monkeypatch)
    st = run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["age", "gender"])
    assert set(st["per_set"]) == {"age", "gender"} and st["per_set"]["age"]["rows"] > 0 and st["sets_pulled"] == 2


def test_retry_budget_is_refilled_by_every_request_that_gets_through(bd, monkeypatch):
    n = {"k": 0}

    def flaky(p, acct):
        n["k"] += 1
        if n["k"] % 3 == 0:
            return err(2, "Service temporarily unavailable")
    Fake(flaky).install(monkeypatch)
    c, sleeps = client()
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=c, accounts=ONE, entries=[mb.BY_NAME["age"]], pause=lambda s: None, today=D(2026, 11, 12))
    assert st["sets_ok"] == 1 and sum(sleeps) > mb.TRANSIENT_BUDGET_S                 # many sporadic errors over a long pull: more than the budget in total, none of it consecutive


def test_committed_request_windows_of_a_failed_window_are_not_pulled_again(bd, monkeypatch):
    f = Fake()
    f.handler = lambda p, acct: err(2, "Service temporarily unavailable") if json.loads(p["time_range"])["since"] == "2026-11-08" and f.fail else None
    f.fail = True
    f.install(monkeypatch)
    ents = [mb.BY_NAME["age"]]
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, backfill=True, today=D(2026, 11, 12))
    assert st["sets_error"] == 1 and st["rows"] > 0
    f.fail = False
    f.calls.clear()
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=client()[0], accounts=ONE, entries=ents, pause=lambda s: None, backfill=True, today=D(2026, 11, 12))
    assert {since for _, since in calls_by_set(f)} == {"2026-11-08"} and st["sets_ok"] == 1
    assert mb.window_done(bd, "age", "ad", ACCT[0], D(2026, 11, 1), D(2026, 11, 14))


def test_a_window_that_keeps_failing_after_the_set_has_worked_is_split_in_halves(bd, monkeypatch):
    def h(p, acct):
        tr = json.loads(p["time_range"])
        span = (D.fromisoformat(tr["until"]) - D.fromisoformat(tr["since"])).days
        if tr["since"] >= "2026-11-08" and span >= 3:
            return err(2, "Service temporarily unavailable")
    f = Fake(h).install(monkeypatch)
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=client()[0], accounts=ONE, entries=[mb.BY_NAME["age"]], pause=lambda s: None, today=D(2026, 11, 12))
    assert st["sets_ok"] == 1 and count(bd, "SELECT count(DISTINCT day) FROM meta_breakdown_daily") == 14


def test_a_set_that_delivered_nothing_fails_fast_instead_of_splitting(bd, monkeypatch):
    f = Fake(lambda p, acct: err(2, "Service temporarily unavailable")).install(monkeypatch)
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=client()[0], accounts=ONE, entries=[mb.BY_NAME["age"]], pause=lambda s: None, today=D(2026, 11, 12))
    assert st["sets_error"] == 1 and len([c for c in f.calls if c[0] == "GET"]) == 4


def test_splitting_after_progress_is_capped(bd, monkeypatch):
    n = {"ok": 0}

    def h(p, acct):
        tr = json.loads(p["time_range"])
        if tr["since"] == "2026-11-01" and n["ok"] == 0:
            n["ok"] = 1                                                                      # the first request gets through ...
            return None
        return err(2, "Service temporarily unavailable")                                   # ... everything after it fails
    f = Fake(h).install(monkeypatch)
    st = mb.run(bd, [(D(2026, 11, 1), D(2026, 11, 14))], None, client=client()[0], accounts=ONE, entries=[mb.BY_NAME["age"]], pause=lambda s: None, today=D(2026, 11, 12))
    assert st["sets_error"] == 1 and len([c for c in f.calls if c[0] == "GET"]) <= 2 + 4 * 4 + 4


# ----------------------------------------------------------------------------------------------- funnel columns and the fill-only history pass (migration 254)
def test_action_sets_carry_no_funnel_values(bd, monkeypatch):
    Fake().install(monkeypatch)
    run(bd, [(D(2026, 11, 1), D(2026, 11, 1))], ["action_device"])
    assert bd.execute("SELECT count(*), count(add_to_cart), count(initiate_checkout) FROM meta_breakdown_daily WHERE breakdown_set = 'action_device'").fetchone()[0] > 0
    assert bd.execute("SELECT count(add_to_cart) + count(initiate_checkout) FROM meta_breakdown_daily WHERE breakdown_set = 'action_device'").fetchone()[0] == 0


def test_repull_never_blanks_a_filled_funnel_value(bd):
    e = mb.BY_NAME["age"]
    raw = [{"ad_id": "a1", "adset_id": "s1", "campaign_id": "c1", "date_start": "2026-11-01", "age": "18-24", "spend": "5", "impressions": "100",
            "actions": [{"action_type": "omni_add_to_cart", "value": "3"}]}]
    mb.upsert(bd, mb.normalise(e, "ad", ACCT[0], raw))
    nofunnel = mb.normalise(e, "ad", ACCT[0], [{**raw[0], "actions": []}])
    assert nofunnel[0][-2] == 0                                  # Meta returned no add to cart this time: 0, a real value
    bd.execute("UPDATE meta_breakdown_daily SET add_to_cart = NULL")
    mb.upsert(bd, nofunnel[:0] or [tuple(list(nofunnel[0][:-2]) + [None, None])])
    assert bd.execute("SELECT add_to_cart FROM meta_breakdown_daily").fetchone()[0] is None       # a NULL re-pull does not invent a value


def test_fill_funnel_updates_only_the_two_columns_of_existing_rows(bd, monkeypatch):
    Fake().install(monkeypatch)
    w = (D(2026, 11, 1), D(2026, 11, 3))
    run(bd, [w], ["age", "gender"])
    n = count(bd)
    bd.execute("UPDATE meta_breakdown_daily SET add_to_cart = NULL, initiate_checkout = NULL")
    bd.execute("UPDATE meta_breakdown_daily SET spend = 123, purchases = 77 WHERE breakdown_set = 'age'")        # values a fill must leave alone (Meta restates; the fill never writes them)
    bd.execute("DELETE FROM meta_breakdown_daily WHERE breakdown_set = 'gender' AND day = '2026-11-03'")           # a pulled row with no stored counterpart is not inserted
    bd.commit()
    before = count(bd)
    out = mb.fill_funnel(bd, w[0], w[1], ["age", "gender"], pause=lambda s: None, client=client()[0], accounts=ONE)
    assert count(bd) == before < n
    assert bd.execute("SELECT count(*) FROM meta_breakdown_daily WHERE add_to_cart IS NULL").fetchone()[0] == 0
    assert bd.execute("SELECT min(add_to_cart), max(add_to_cart), min(initiate_checkout) FROM meta_breakdown_daily").fetchone() == (7, 7, 4)
    assert bd.execute("SELECT min(spend), max(spend), min(purchases) FROM meta_breakdown_daily WHERE breakdown_set = 'age'").fetchone() == (123, 123, 77)
    assert out["per_set"]["age"]["left"] == 0 and out["per_set"]["age"]["filled"] > 0 and not out["errors"]


def test_fill_funnel_is_resumable_and_a_finished_window_costs_no_request(bd, monkeypatch):
    fake = Fake().install(monkeypatch)
    w = (D(2026, 11, 1), D(2026, 11, 3))
    run(bd, [w], ["age"])
    bd.execute("UPDATE meta_breakdown_daily SET add_to_cart = NULL")
    bd.commit()
    kw = dict(pause=lambda s: None, client=client()[0], accounts=ONE)
    mb.fill_funnel(bd, w[0], w[1], ["age"], **kw)
    calls = len(fake.calls)
    out = mb.fill_funnel(bd, w[0], w[1], ["age"], **kw)
    assert len(fake.calls) == calls and out["windows_skipped"] == 1 and out["windows_filled"] == 0
