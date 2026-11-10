"""etl.meta_write with the HTTP layer faked: no network, no real token. Every ad and ad set is created paused, the source has no turn-on, remove or update calls, chunked
video path above 50 MB and refusal above 500 MB, backoff on rate limits, token scrubbed from errors, validate-only mode, feature opt-outs, missing token."""
import io
import json
import re
from pathlib import Path

import pytest

from etl import meta_write as mw

TOKEN = "EAAtesttokenvalue1234567890"
SRC = Path(mw.__file__).read_text()


class Resp:
    def __init__(self, j, code=200, headers=None):
        self.status_code, self._j, self.headers = code, j, headers or {}

    def json(self):
        return self._j


class Fake:
    """Queue of responses per (method, url-suffix); records every call."""

    def __init__(self, post=None, get=None):
        self.calls, self.post_q, self.get_q = [], list(post or []), list(get or [])

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {}), None))
        return self.get_q.pop(0) if self.get_q else Resp({})

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, dict(data or {}), files))
        return self.post_q.pop(0) if self.post_q else Resp({"id": "1"})


def T(fake, **kw):
    return mw.Transport(token=TOKEN, session=fake, sleep=lambda s: None, **kw)


def posts(fake):
    return [c for c in fake.calls if c[0] == "POST"]


def test_missing_token_error(monkeypatch):
    monkeypatch.delenv("META_LAUNCH_TOKEN", raising=False)
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)                  # the read token must not be used
    with pytest.raises(mw.MissingToken) as e:
        mw.Transport(session=Fake())
    assert "META_LAUNCH_TOKEN" in str(e.value) and TOKEN not in str(e.value)


def test_token_from_env_only(monkeypatch):
    monkeypatch.setenv("META_LAUNCH_TOKEN", TOKEN)
    assert mw.get_token() == TOKEN


def test_ad_is_paused_and_has_no_status_argument():
    f = Fake(post=[Resp({"id": "77"})])
    assert mw.create_paused_ad("123", "9", "5", "My ad", http=T(f)) == "77"
    (_, url, data, _), = posts(f)
    assert url.endswith("/act_123/ads") and data["status"] == "PAUSED" and json.loads(data["creative"]) == {"creative_id": "5"}
    import inspect
    assert "status" not in inspect.signature(mw.create_paused_ad).parameters


def test_clone_adset_paused_drops_subscribers_adds_explore():
    tpl = {"account_id": "123", "campaign_id": "C1", "billing_event": "IMPRESSIONS", "optimization_goal": "OFFSITE_CONVERSIONS", "bid_strategy": "COST_CAP",
           "promoted_object": {"pixel_id": "p"}, "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 1}],
           "targeting": {"subscriber_universe": {"x": 1}, "instagram_positions": ["stream", "explore_home"], "geo_locations": {"countries": ["US"]}}}
    f = Fake(get=[Resp(tpl)], post=[Resp({"id": "AS9"})])
    assert mw.clone_adset_paused("T1", "New set", 10000, 6800, http=T(f)) == "AS9"
    (_, url, data, _), = posts(f)
    assert url.endswith("/act_123/adsets") and data["status"] == "PAUSED" and data["daily_budget"] == "10000" and data["bid_amount"] == "6800"
    tg = json.loads(data["targeting"])
    assert "subscriber_universe" not in tg and tg["instagram_positions"] == ["stream", "explore_home", "explore"] and data["bid_strategy"] == "COST_CAP"
    assert "subscriber_universe" in tpl["targeting"]                # the template was not mutated


def test_validate_only_flag_on_every_write():
    f = Fake()
    h = T(f)
    spec = mw.build_creative_spec("n", "P", "I", "https://x.test", "image", ["b"], ["t"], image_hash="h")
    assert mw.create_creative("123", spec, validate_only=True, http=h) is None
    assert mw.create_paused_ad("123", "9", "5", "a", validate_only=True, http=h) is None
    f.get_q.append(Resp({"account_id": "123", "campaign_id": "C", "billing_event": "IMPRESSIONS", "optimization_goal": "X", "targeting": {}}))
    assert mw.clone_adset_paused("T", "n", 1, 1, validate_only=True, http=h) is None
    for _, _, data, _ in posts(f):
        assert json.loads(data["execution_options"]) == ["validate_only"]
    f2 = Fake()
    mw.create_paused_ad("123", "9", "5", "a", http=T(f2))
    assert "execution_options" not in posts(f2)[0][2]


def test_creative_spec_opts_out_every_feature_key():
    keys = ["video_auto_crop", "product_browsing", "show_summary", "video_auto_crop"]
    s = mw.build_creative_spec("n", "P1", "IG1", "https://shop.test/vb/sp", "video", ["a", "b", "c"], ["x", "y"], video_id="V", thumbnail_url="u", feature_keys=keys)
    assert s["degrees_of_freedom_spec"]["creative_features_spec"] == {k: {"enroll_status": "OPT_OUT"} for k in set(keys)}
    af = s["asset_feed_spec"]
    assert len(af["bodies"]) == 3 and len(af["titles"]) == 2 and af["call_to_action_types"] == ["SHOP_NOW"] and af["ad_formats"] == ["SINGLE_VIDEO"]
    assert s["object_story_spec"] == {"page_id": "P1", "instagram_actor_id": "IG1"} and "utm_content={{ad.name}}" in s["url_tags"]
    img = mw.build_creative_spec("n", "P1", None, "https://x", "image", ["a"], ["t"], image_hash="h")
    assert img["asset_feed_spec"]["call_to_action_types"] == ["LEARN_MORE"] and "instagram_actor_id" not in img["object_story_spec"]
    with pytest.raises(mw.MetaWriteError):
        mw.build_creative_spec("n", "P", None, "l", "image", ["a"] * 4, ["t"], image_hash="h")


def test_create_creative_json_encodes_nested_fields():
    f = Fake(post=[Resp({"id": "CR1"})])
    spec = mw.build_creative_spec("n", "P", "I", "https://x", "image", ["a"], ["t"], image_hash="h", feature_keys=["k"])
    assert mw.create_creative("act_123", spec, http=T(f)) == "CR1"
    data = posts(f)[0][2]
    assert isinstance(data["asset_feed_spec"], str) and json.loads(data["degrees_of_freedom_spec"])["creative_features_spec"]["k"]["enroll_status"] == "OPT_OUT"


def test_find_ad_by_name_exact_match_only():
    f = Fake(get=[Resp({"data": [{"id": "1", "name": "ad-1b", "effective_status": "PAUSED"}, {"id": "2", "name": "ad-1", "effective_status": "PAUSED"}]})])
    assert mw.find_ad_by_name("123", "ad-1", http=T(f)) == {"id": "2", "name": "ad-1", "effective_status": "PAUSED"}
    assert mw.find_ad_by_name("123", "nope", http=T(Fake(get=[Resp({"data": []})]))) is None
    assert not posts(f)


def test_single_post_video_up_to_50mb_and_image_hash(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 1000)
    f = Fake(post=[Resp({"id": "V1"})])
    assert mw.upload_video("123", str(p), 1000, "v.mp4", http=T(f)) == "V1"
    assert len(posts(f)) == 1 and "source" in posts(f)[0][3]
    f = Fake(post=[Resp({"images": {"a.png": {"hash": "HASH1"}}})])
    assert mw.upload_image("123", b"png", "a.png", http=T(f)) == "HASH1"


def test_chunked_video_above_50mb(monkeypatch):
    size = mw.SINGLE_POST_MAX + 10
    monkeypatch.setattr(mw, "CHUNK", 1)                              # unused by the server-driven offsets, here for clarity
    half = size // 2
    f = Fake(post=[Resp({"upload_session_id": "S", "video_id": "V9", "start_offset": "0", "end_offset": str(half)}),
                   Resp({"start_offset": str(half), "end_offset": str(size)}),
                   Resp({"start_offset": str(size), "end_offset": str(size)}),
                   Resp({"success": True})])

    class Big(io.RawIOBase):
        def __init__(self):
            self.pos = 0

        def seek(self, n, w=0):
            self.pos = n
            return n

        def read(self, n=-1):
            return b"\0" * 5                                          # content is irrelevant; sizes are driven by `size`

    assert mw.upload_video("123", Big(), size, "big.mp4", http=T(f)) == "V9"
    phases = [c[2].get("upload_phase") for c in posts(f)]
    assert phases == ["start", "transfer", "transfer", "finish"]


def test_video_over_500mb_refused():
    f = Fake()
    with pytest.raises(mw.FileTooBig):
        mw.upload_video("123", io.BytesIO(b""), mw.VIDEO_HARD_CAP + 1, "huge.mp4", http=T(f))
    assert not f.calls


def test_backoff_on_rate_limit_code_then_success():
    sleeps = []
    f = Fake(post=[Resp({"error": {"message": "Too many calls", "code": 17}}, 400), Resp({"error": {"message": "x", "code": 80004}}, 400), Resp({"id": "9"})])
    h = mw.Transport(token=TOKEN, session=f, sleep=sleeps.append)
    assert mw.create_paused_ad("123", "9", "5", "a", http=h) == "9"
    assert sleeps == [60, 120] and len(posts(f)) == 3


def test_post_not_retried_on_server_error():
    f = Fake(post=[Resp({"error": {"message": "boom", "code": 2}}, 500), Resp({"id": "9"})])
    with pytest.raises(mw.MetaWriteError):
        mw.create_paused_ad("123", "9", "5", "a", http=T(f))
    assert len(posts(f)) == 1                                        # a retry here could create a duplicate


def test_get_retried_on_server_error():
    f = Fake(get=[Resp({"error": {"message": "boom", "code": 2}}, 500), Resp({"data": []})])
    assert mw.find_ad_by_name("123", "a", http=T(f)) is None and len(f.calls) == 2


def test_token_scrubbed_from_raised_error():
    f = Fake(post=[Resp({"error": {"message": f"Invalid parameter access_token={TOKEN} and {TOKEN}", "error_user_msg": f"bad {TOKEN}", "code": 100}}, 400)])
    with pytest.raises(mw.MetaWriteError) as e:
        mw.create_paused_ad("123", "9", "5", "a", http=T(f))
    assert TOKEN not in str(e.value) and "***" in str(e.value)
    f = Fake(post=[Resp({"error": {"message": f"token {TOKEN}", "code": 190}}, 400)])
    with pytest.raises(mw.MetaWriteError) as e:
        mw.create_paused_ad("123", "9", "5", "a", http=T(f))
    assert TOKEN not in str(e.value) and "Replace META_LAUNCH_TOKEN" in str(e.value)


def test_network_exception_text_scrubbed():
    class Boom(Fake):
        def post(self, *a, **k):
            raise RuntimeError(f"connection reset url?access_token={TOKEN}")
    with pytest.raises(mw.MetaWriteError) as e:
        mw.Transport(token=TOKEN, session=Boom(), sleep=lambda s: None, max_retries=2).post_edge("act_1", "ads", "x", data={})
    assert TOKEN not in str(e.value)


def test_usage_header_exposed():
    f = Fake(get=[Resp({"data": []}, headers={"x-business-use-case-usage": json.dumps({"123": [{"call_count": 40, "total_cputime": 81, "total_time": 12}]})})])
    h = T(f)
    mw.find_ad_by_name("123", "a", http=h)
    assert mw.usage_pct(h) == 81.0 and mw.parse_usage({"x-app-usage": '{"call_count": 12}'}) == 12.0 and mw.parse_usage({}) == 0.0


def test_wait_video_ready_and_thumbnail():
    f = Fake(get=[Resp({"status": {"video_status": "processing"}}), Resp({"status": {"video_status": "ready"}})])
    assert mw.wait_video_ready("V", http=T(f), sleep=lambda s: None) is True
    f = Fake(get=[Resp({"status": {"video_status": "error"}})])
    with pytest.raises(mw.MetaWriteError):
        mw.wait_video_ready("V", http=T(f), sleep=lambda s: None)
    t = iter([0, 5, 2000])
    with pytest.raises(mw.MetaWriteError):
        mw.wait_video_ready("V", timeout=900, http=T(Fake()), sleep=lambda s: None, clock=lambda: next(t))
    f = Fake(get=[Resp({"data": [{"uri": "a", "is_preferred": False}, {"uri": "b", "is_preferred": True}]})])
    assert mw.video_thumbnail("V", http=T(f)) == "b"


def test_refuses_posts_to_existing_objects_or_other_edges():
    h = T(Fake())
    for acct, edge in (("12345", "ads"), ("act_1", "ads/999"), ("act_1", "campaigns"), ("act_1", "ads?status=x")):
        with pytest.raises(mw.MetaWriteError):
            h.post_edge(acct, edge, "x")


def test_source_has_no_turn_on_remove_or_update_calls():
    assert "ACTIVE" not in SRC
    assert not re.search(r"DELETE", SRC, re.I) and ".delete(" not in SRC and ".put(" not in SRC and ".patch(" not in SRC
    assert len(re.findall(r"\.post\(", SRC)) == 1                    # the single write helper
    assert len(re.findall(r"\.post_edge\(", SRC)) >= 6 and not re.search(r"post_edge\(\s*(?!acct|_acct)", SRC.replace("def post_edge(", ""))
    assert 'status": _PAUSED' in SRC and SRC.count("_PAUSED") >= 3
    assert not re.search(r"status.{0,12}(ACTIVE|ENABLED|LIVE)", SRC, re.I) and "META_ACCESS_TOKEN" not in SRC.split('"""', 2)[2]
