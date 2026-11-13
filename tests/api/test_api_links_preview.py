"""'See the ad': links, ads_count / ads_live, ad previews (feature flag + mocked Graph), CSP."""
import datetime as dt
import logging

import pytest

from app import config as cfg

from . import fixture_data as F
from .conftest import PASSWORD
from .helpers import get_ok

THROUGH = F.THROUGH
MAIN, BACKUP = "1671338033619223", "1543579384022317"
TOKEN = "EAAG-super-secret-preview-token-123"


def am_url(act, ad):
    return f"https://adsmanager.facebook.com/adsmanager/manage/ads?act={act}&selected_ad_ids={ad}"


# ---------------------------------------------------------------------------------------------------------------- oracle
def mapped_ads(fx):
    """{family_key: [ad ids]} from the raw fixture (an ad belongs to the family its Meta family name maps to)."""
    out = {}
    for ad_id, (disp, *_rest) in F.ADS.items():
        tfam = F.MAPPING[F.meta_family_key(disp)][0]
        if tfam:
            out.setdefault(tfam, []).append(ad_id)
    return out


def representative(fx, fk):
    """latest last_day, then lifetime spend, then ad id"""
    best = None
    for ad in mapped_ads(fx).get(fk, []):
        days = [r["day"] for r in fx.ad_daily if r["ad_id"] == ad]
        last = max(days) if days else dt.date.min
        lt = fx.ad_lifetime.get(ad, {}).get("spend", F.Z)
        key = (last, lt, [-ord(c) for c in ad])
        if best is None or key > best[0]:
            best = (key, ad)
    return best[1] if best else None


def ad_account(ad):
    return F.AD_ACCOUNT.get(ad, "Lonvera Main")


# ---------------------------------------------------------------------------------------------------------------- links
def test_ads_manager_url_mapping():
    assert cfg.ads_manager_url("Lonvera Main", "123") == am_url(MAIN, "123")
    assert cfg.ads_manager_url("Lonvera BackUp", "9") == am_url(BACKUP, "9")
    assert cfg.ads_manager_url("lonvera main", "9") == am_url(MAIN, "9") and cfg.ads_manager_url("  Lonvera Main ", "9") == am_url(MAIN, "9")
    assert cfg.ads_manager_url("act_555", "9") == am_url("555", "9") and cfg.ads_manager_url("555", "9") == am_url("555", "9")
    for bad in (None, "", "Some Other Account", "Main"):
        assert cfg.ads_manager_url(bad, "9") is None
    assert cfg.ads_manager_url("Lonvera Main", None) is None and cfg.ads_manager_url("Lonvera Main", "") is None


def test_ad_detail_links(client, fx):
    d = get_ok(client, "/api/ads/1000")
    ad = representative(fx, "vb-fam-a")
    assert d["links"] == {"story_url": "https://facebook.example/post/1", "ad_id": ad, "account": ad_account(ad),
                          "ads_manager_url": am_url(BACKUP if ad_account(ad) == "Lonvera BackUp" else MAIN, ad)}
    assert d["story_url"] == d["links"]["story_url"]
    b = get_ok(client, "/api/ads/1002")["links"]                                                # vb-fam-b: b1 runs to the end, b2 stopped Oct 15
    assert b["ad_id"] == "b1" and b["ads_manager_url"] == am_url(MAIN, "b1") and b["story_url"] is None
    blank = get_ok(client, "/api/ads/1003")["links"]                                            # concept with no ad family
    assert blank == {"story_url": None, "ads_manager_url": None, "ad_id": None, "account": None}
    nobody = get_ok(client, "/api/ads/1005")["links"]                                           # C1 has delivery but no ads in meta lifetime
    assert nobody["ad_id"] == "c1" and nobody["ads_manager_url"] == am_url(MAIN, "c1")


def test_the_representative_ad_follows_last_day_then_lifetime_spend(client, fx):
    t = get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3")
    checked = 0
    for r in t["rows"]:
        if r["level"] != 3 or r["family_key"].startswith("id:"):
            continue
        ad = representative(fx, r["family_key"])
        want = am_url(MAIN if ad_account(ad) == "Lonvera Main" else BACKUP, ad) if ad else None
        assert r["ads_manager_url"] == want, r["family_key"]
        checked += 1
    assert checked >= 5


def test_ads_rows_carry_ads_manager_and_story_urls(client, fx):
    d = get_ok(client, "/api/products/vita-bull/ads?window=30d")
    seen_backup = False
    for r in d["rows"]:
        if r["ad_id"] is None:
            assert r["ads_manager_url"] is None and r["story_url"] is None
            continue
        acct = ad_account(r["ad_id"]) if r["ad_id"] in F.ADS else None
        assert r["ads_manager_url"] == (am_url(BACKUP if acct == "Lonvera BackUp" else MAIN, r["ad_id"]) if acct else None), r["ad_id"]
        seen_backup |= acct == "Lonvera BackUp"
        assert r["story_url"] == ("https://facebook.example/post/1" if r["family_key"] == "vb-fam-a" else None), r["ad_id"]
    assert seen_backup
    for key in ("ads_manager_url", "story_url"):                      # the keys are always there
        assert all(key in r for r in get_ok(client, "/api/products/beef-liver/ads?window=30d")["rows"])


def test_tree_non_family_rows_have_null_links(client):
    t = get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3")
    assert all(r["ads_manager_url"] is None and r["story_url"] is None for r in t["rows"] if r["level"] < 3)
    fam = next(r for r in t["rows"] if r["family_key"] == "vb-fam-a")
    assert fam["story_url"] == "https://facebook.example/post/1" and fam["ads_manager_url"].startswith("https://adsmanager.facebook.com/")


# ---------------------------------------------------------------------------------------------------------------- creative vs ads
def expected_counts(fx, fk, window, stages):
    ads = {r["ad_id"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] == fk and not r["ad_id"].startswith("fam:") and r["spend"] > 0}
    live = {r["ad_id"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] == fk and not r["ad_id"].startswith("fam:") and r["spend"] > 0
            and r["day"] >= THROUGH - dt.timedelta(days=1)}
    if window == "launch":
        if "unclassified" in stages:
            n = set(mapped_ads(fx).get(fk, [])) | {a for a, lt in fx.ad_lifetime.items() if lt["tfam"] == fk}
        else:
            n = {r["ad_id"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] == fk and not r["ad_id"].startswith("fam:") and r["spend"] > 0
                 and F.stage_of_row(fx, r["campaign_id"]) in stages}
    else:
        n = {r["ad_id"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] == fk and not r["ad_id"].startswith("fam:") and r["spend"] > 0
             and window[0] <= r["day"] <= window[1] and F.stage_of_row(fx, r["campaign_id"]) in stages}
    return len(n), len(live)


@pytest.mark.parametrize("q_,win", [("", "launch"), ("window=30d", (THROUGH - dt.timedelta(days=29), THROUGH)), ("window=7d", (THROUGH - dt.timedelta(days=6), THROUGH)),
                                    ("from=2026-10-01&to=2026-10-12", (dt.date(2026, 10, 1), dt.date(2026, 10, 12)))])
@pytest.mark.parametrize("st,stages", [("", F.STAGES), ("&stage=testing", ("testing",)), ("&stage=scaling,unclassified", ("scaling", "unclassified")),
                                       ("&stage=testing,scaling", ("testing", "scaling"))])
def test_ads_count_and_live_on_families_and_tree(client, fx, q_, win, st, stages):
    fams = get_ok(client, "/api/products/vita-bull/families?" + q_ + st)["rows"]
    tree = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3&" + q_ + st)["rows"] if r["level"] == 3}
    for r in fams:
        want = expected_counts(fx, r["family_key"], win, stages) if r["counted_here"] and not r["family_key"].startswith("id:") else (0, 0)
        assert (r["ads_count"], r["ads_live"]) == want, (r["family_key"], q_, st)
    for r in tree.values():
        if r["counted_here"]:
            assert (r["ads_count"], r["ads_live"]) == expected_counts(fx, r["family_key"], win, stages) or r["family_key"].startswith("id:")
    assert all(0 <= r["ads_live"] <= r["ads_count"] or win == "launch" or r["ads_live"] >= 0 for r in fams)


def test_ad_detail_has_a_creative_object(client, fx):
    d = get_ok(client, "/api/ads/1000?window=30d")
    want = expected_counts(fx, "vb-fam-a", (THROUGH - dt.timedelta(days=29), THROUGH), F.STAGES)
    assert d["creative"] == {"family_key": "vb-fam-a", "rows": 2, "ads_count": want[0], "ads_live": want[1], "active_ads": 2}
    assert (d["ads_count"], d["ads_live"]) == want
    assert get_ok(client, "/api/ads/1001?window=30d")["creative"]["ads_count"] == want[0]          # same creative from its second tracker row
    assert get_ok(client, "/api/ads/1003")["creative"]["ads_count"] == 0                            # blank family has no ads
    assert get_ok(client, "/api/ads/1000")["creative"]["ads_count"] == 3                             # since launch: all three Meta ads


# ---------------------------------------------------------------------------------------------------------------- previews
class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


def good_body(src="https://www.facebook.com/ads/api/preview_iframe.php?d=abc&t=xyz"):
    return {"data": [{"body": f'<iframe src="{src.replace("&", "&amp;")}" width="320" height="540" scrolling="yes" style="border:none"></iframe>'}]}


@pytest.fixture()
def pv(app_factory):
    """an app with the preview flag ON and a fake Graph; returns (client, calls, service)"""
    a = app_factory(META_PREVIEW_TOKEN=TOKEN)
    c = a.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    svc = a.extensions["preview"]
    calls = []

    def fake(url, params=None, headers=None, timeout=None, allow_redirects=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout, "redirects": allow_redirects})
        return svc._next(url) if hasattr(svc, "_next") else FakeResp(200, good_body())
    svc.http_get = fake
    return c, calls, svc


def test_preview_is_off_without_the_token(client, app_factory):
    for u in ("/api/meta-ads/a1/preview", "/api/meta-ads/not-an-ad/preview", "/api/ads/1000/preview", "/api/ads/99999/preview", "/api/meta-ads/a1/preview?format=bogus"):
        r = client.get(u)
        assert r.status_code == 200 and r.get_json() == {"enabled": False}, u
    off = app_factory(META_PREVIEW_TOKEN="   ").test_client()                      # blank token = off
    off.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD})
    assert off.get("/api/meta-ads/a1/preview").get_json() == {"enabled": False}


def test_preview_success_and_graph_call_shape(pv):
    c, calls, _ = pv
    r = c.get("/api/meta-ads/a1/preview")
    d = r.get_json()
    assert r.status_code == 200 and d["enabled"] is True and d["ad_id"] == "a1" and d["format"] == "mobile_feed"
    assert d["src"] == "https://www.facebook.com/ads/api/preview_iframe.php?d=abc&t=xyz" and d["fetched_at"].endswith("+00:00")
    assert set(d) == {"enabled", "ad_id", "format", "src", "fetched_at"}
    call = calls[0]
    assert call["url"] == "https://graph.facebook.com/v23.0/a1/previews" and call["params"] == {"ad_format": "MOBILE_FEED_STANDARD"}
    assert call["timeout"] == 8 and call["redirects"] is False
    assert call["headers"] == {"Authorization": f"Bearer {TOKEN}"} and TOKEN not in str(call["url"]) + str(call["params"])      # token only in a header
    assert TOKEN not in r.get_data(as_text=True) and TOKEN not in str(dict(r.headers))


@pytest.mark.parametrize("fmt,graph", [("mobile_feed", "MOBILE_FEED_STANDARD"), ("desktop_feed", "DESKTOP_FEED_STANDARD"), ("reels", "INSTAGRAM_REELS"),
                                       ("stories", "INSTAGRAM_STORY"), ("MOBILE_FEED_STANDARD", None), ("Reels", "INSTAGRAM_REELS")])
def test_preview_formats(pv, fmt, graph):
    c, calls, _ = pv
    r = c.get(f"/api/meta-ads/a1/preview?format={fmt}")
    if graph is None:
        assert r.status_code == 400 and not calls
        return
    assert r.status_code == 200 and calls[0]["params"] == {"ad_format": graph} and r.get_json()["format"] == fmt.lower()
    assert c.get("/api/meta-ads/a1/preview").get_json()["format"] == "mobile_feed"             # default


def test_preview_by_tracker_row_uses_the_representative_ad(pv, fx):
    c, calls, _ = pv
    r = c.get("/api/ads/1000/preview?format=desktop_feed")
    assert r.status_code == 200 and r.get_json()["ad_id"] == representative(fx, "vb-fam-a") and r.get_json()["format"] == "desktop_feed"
    assert calls[0]["url"].endswith(f"/{representative(fx, 'vb-fam-a')}/previews")
    assert c.get("/api/ads/99999/preview").status_code == 404                                  # unknown tracker row
    assert c.get("/api/ads/1003/preview").status_code == 404 and len(calls) == 1               # blank family: no Meta ad, Graph not called


def test_unknown_ad_ids_are_404_and_never_reach_graph(pv):
    c, calls, _ = pv
    for bad in ("nope", "9999999999", "a1;DROP", "a1%00", "http%3A%2F%2Fevil.example%2F", "..%2F..%2Fme", "a" * 80):
        assert c.get(f"/api/meta-ads/{bad}/preview").status_code in (404, 400), bad
    assert not calls
    assert c.get("/api/meta-ads/a1/preview?url=https://evil.example/&src=https://evil.example/").get_json()["src"].startswith("https://www.facebook.com/")   # client URLs are ignored


@pytest.mark.parametrize("src", [
    "https://evilfacebook.com/x", "https://facebook.com.evil.example/x", "https://www.facebook.com.evil.example/x", "http://www.facebook.com/x",
    "//www.facebook.com/x", "javascript:alert(1)", "data:text/html,hi", "https://user:pw@www.facebook.com/x", "https://www.facebook.com:8443/x",
    "https://fbcdn.net.evil.example/x", "https://notfbcdn.net/x", "https://evil.example/?u=https://www.facebook.com/", "ftp://www.facebook.com/x", "",
    "https://www.facebook.com\\@evil.example/x", "https://127.0.0.1/x"])
def test_preview_rejects_bad_iframe_hosts(pv, src):
    c, _, svc = pv
    svc.http_get = lambda *a, **k: FakeResp(200, {"data": [{"body": f'<iframe src="{src}"></iframe>'}]})
    r = c.get("/api/meta-ads/a1/preview")
    assert r.status_code == 200 and r.get_json() == {"enabled": True, "error": "preview unavailable"}


@pytest.mark.parametrize("src", ["https://www.facebook.com/x", "https://facebook.com/x", "https://business.facebook.com/ads/x?a=1&b=2", "https://scontent-lax3-1.xx.fbcdn.net/v/x",
                                 "https://fbcdn.net/x", "https://WWW.FACEBOOK.COM/x"])
def test_preview_accepts_facebook_and_fbcdn_hosts(pv, src):
    c, _, svc = pv
    svc.http_get = lambda *a, **k: FakeResp(200, {"data": [{"body": f'<iframe src="{src}"></iframe>'}]})
    d = c.get("/api/meta-ads/a1/preview").get_json()
    assert d["enabled"] is True and d["src"] == src and "error" not in d


@pytest.mark.parametrize("payload", [
    {"data": []}, {"data": [{}]}, {"data": [{"body": "no iframe here"}]}, {"data": [{"body": "<iframe></iframe>"}]}, {"data": [{"body": 5}]}, {"error": {"message": "Invalid OAuth"}},
    ["x"], None, {"data": [{"body": "<div><iframe src='https://evil.example/x'></iframe></div><iframe src='https://www.facebook.com/ok'></iframe>"}]},
    {"data": [{"body": '<script>alert(1)</script><iframe src=https://www.facebook.com.evil.example/x>'}]}])
def test_preview_unusable_graph_payloads(pv, payload):
    c, _, svc = pv
    svc.http_get = lambda *a, **k: FakeResp(200, payload)
    assert c.get("/api/meta-ads/a1/preview").get_json() == {"enabled": True, "error": "preview unavailable"}   # first iframe wins and it is evil


@pytest.mark.parametrize("failure", ["http500", "http400", "timeout", "bad json", "connection", "token in message"])
def test_meta_errors_give_a_fixed_message_and_no_leaks(pv, failure, caplog):
    c, _, svc = pv

    def boom(*a, **k):
        if failure == "http500":
            return FakeResp(500, {"error": {"message": f"Internal error for token {TOKEN}", "code": 2}})
        if failure == "http400":
            return FakeResp(400, {"error": {"message": f"Invalid OAuth access token {TOKEN}", "code": 190}})
        if failure == "timeout":
            import requests
            raise requests.exceptions.ReadTimeout(f"HTTPSConnectionPool timed out Authorization: Bearer {TOKEN}")
        if failure == "bad json":
            return FakeResp(200, ValueError(f"not json {TOKEN}"))
        if failure == "connection":
            raise ConnectionError(f"cannot reach graph.facebook.com {TOKEN}")
        raise RuntimeError(f"token={TOKEN}")
    svc.http_get = boom
    with caplog.at_level(logging.DEBUG):
        r = c.get("/api/meta-ads/a1/preview")
    assert r.status_code == 200 and r.get_json() == {"enabled": True, "error": "preview unavailable"}
    assert TOKEN not in r.get_data(as_text=True) + str(dict(r.headers)) + caplog.text
    assert "Invalid OAuth" not in r.get_data(as_text=True) and "Internal error" not in r.get_data(as_text=True)


def test_preview_cache_six_hours_and_short_error_cache(pv):
    c, calls, svc = pv
    clock = [1_000_000.0]
    svc.clock = lambda: clock[0]
    c.get("/api/meta-ads/a1/preview")
    c.get("/api/meta-ads/a1/preview")
    assert len(calls) == 1                                                     # second request served from the cache
    c.get("/api/meta-ads/a1/preview?format=reels")
    c.get("/api/meta-ads/a2/preview")
    assert len(calls) == 3                                                     # cache key = (ad id, format)
    clock[0] += 6 * 3600 - 5
    c.get("/api/meta-ads/a1/preview")
    assert len(calls) == 3
    clock[0] += 10
    c.get("/api/meta-ads/a1/preview")
    assert len(calls) == 4                                                     # expired after 6 h
    svc.http_get = lambda *a, **k: (calls.append("err"), FakeResp(500, {}))[1]
    c.get("/api/meta-ads/b1/preview")
    c.get("/api/meta-ads/b1/preview")
    assert calls.count("err") == 1                                             # an error is remembered for a minute, not hammered
    clock[0] += 61
    c.get("/api/meta-ads/b1/preview")
    assert calls.count("err") == 2


def test_preview_rate_limits_per_ip_and_globally(pv):
    c, calls, svc = pv
    for i in range(20):
        assert c.get("/api/meta-ads/a1/preview").status_code == 200, i
    r = c.get("/api/meta-ads/a1/preview")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0 and r.get_json()["error"] == "too many preview requests"
    assert TOKEN not in r.get_data(as_text=True)
    other = c.get("/api/meta-ads/a1/preview", environ_overrides={"REMOTE_ADDR": "10.1.1.1"})
    assert other.status_code == 200                                            # another IP still fine
    # the global budget: 120 per minute over all IPs
    n_ok, code = 0, 200
    for i in range(130):
        code = c.get(f"/api/meta-ads/a{1 + i % 2}/preview", environ_overrides={"REMOTE_ADDR": f"10.2.{i // 250}.{i % 250}"}).status_code
        n_ok += code == 200
        if code == 429:
            break
    assert code == 429 and n_ok <= 120 - 21 + 1
    assert len(calls) <= 2                                                     # and everything was served from the cache anyway


def test_preview_needs_a_login_unless_auth_is_off(app_factory):
    a = app_factory(META_PREVIEW_TOKEN=TOKEN)
    anon = a.test_client()
    assert anon.get("/api/meta-ads/a1/preview").status_code == 401 and anon.get("/api/ads/1000/preview").status_code == 401
    assert anon.get("/api/meta-ads/a1/preview").get_json() == {"error": "unauthorized"}
    open_app = app_factory(META_PREVIEW_TOKEN=TOKEN, AUTH_DISABLED=True)
    open_app.extensions["preview"].http_get = lambda *a_, **k: FakeResp(200, good_body())
    assert open_app.test_client().get("/api/meta-ads/a1/preview").get_json()["enabled"] is True


def test_token_never_appears_anywhere(pv, caplog):
    c, _, _ = pv
    with caplog.at_level(logging.DEBUG):
        blobs = []
        for u in ("/api/status", "/api/meta-ads/a1/preview", "/api/ads/1000/preview", "/api/meta-ads/zzz/preview", "/api/ads/1000", "/", "/healthz",
                  "/api/meta-ads/a1/preview?format=bogus"):
            r = c.get(u)
            blobs.append(r.get_data(as_text=True) + str(dict(r.headers)))
    assert TOKEN not in "".join(blobs) + caplog.text


# ---------------------------------------------------------------------------------------------------------------- CSP
def test_csp_on_the_html_shell(anon):
    for path in ("/", "/login", "/p/vita-bull", "/ad/1000"):
        csp = anon.get(path).headers["Content-Security-Policy"]
        parts = {p.split()[0]: p.split()[1:] for p in csp.split(";") if p.strip()}
        assert parts["frame-src"] == ["https://*.facebook.com", "https://*.fbcdn.net", "https://*.fb.com", "https://drive.google.com"], path
        assert parts["img-src"] == ["'self'", "data:"] and parts["script-src"] == ["'self'"], path       # scripts: this origin only (Chart.js is vendored)
        assert parts["default-src"] == ["'self'"] and parts["object-src"] == ["'none'"] and "'unsafe-inline'" not in parts["script-src"]
        assert parts["frame-ancestors"] == ["'none'"] and parts["connect-src"] == ["'self'"], path
    assert "unsafe-eval" not in anon.get("/").headers["Content-Security-Policy"]


def test_the_shell_has_no_inline_script_the_csp_would_block(anon):
    html = anon.get("/").get_data(as_text=True)
    import re
    for m in re.finditer(r"<script\b([^>]*)>", html):
        assert "src=" in m.group(1), m.group(0)                          # every script is external
    assert not re.search(r"\son\w+\s*=", html)                           # no inline event handlers
