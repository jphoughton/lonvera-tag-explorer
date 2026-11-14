"""etl.own_ad_tags with whisper, video download and `claude -p` faked: no network, no token, no real model. Scope ordering by spend, resumability, transcript import (incl. Vita Bull
family keying), allowed-value enforcement, failure isolation, --dry-run writes nothing, the worker pool, rate-limit backoff."""
import json
import subprocess
import threading
import time

import pytest

from etl import auto_tag as at
from etl import competitor_tags as ct
from etl import own_ad_tags as ot


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE ad_learning_tags, ad_transcript, creative_ad_transcripts CASCADE")
    conn.commit()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))
    monkeypatch.setattr(ot, "RATE_BACKOFF", (1, 2, 3))


def pid(conn, slug="vita-bull"):
    return conn.execute("SELECT id FROM products WHERE slug=%s", (slug,)).fetchone()[0]


def add_tracker(conn, family, product="vita-bull", ad_type="VID", transcript="", n=[0]):
    n[0] += 1
    conn.execute("""INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, ad_type, transcript, primary_text, headlines)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (pid(conn, product), f"{family}#{n[0]}", f"c-{family}", family, family.lower(), n[0], ad_type, transcript,
                                                             [f"copy of {family}"], ["Shop now"]))


def add_spend(conn, meta_family, ad_id, spend, tracker_family=None, day="2026-11-01"):
    conn.execute("INSERT INTO meta_ads (ad_id, ad_name, meta_family_key) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ad_id, meta_family, meta_family))
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES (%s,%s,%s,%s,%s,'meta_api')", (ad_id, day, meta_family, meta_family, spend))
    if tracker_family:
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,'exact') ON CONFLICT DO NOTHING", (meta_family, meta_family, tracker_family))


def seed(conn):
    add_tracker(conn, "Alpha", ad_type="IMG")
    add_tracker(conn, "Beta", ad_type="VID")
    add_tracker(conn, "Gamma", ad_type="IMG")                       # tracker only, no spend
    add_spend(conn, "alpha", "1", 50, "alpha")
    add_spend(conn, "alpha-copy", "2", 100, "alpha")                 # two Meta families of one tracker family: summed
    add_spend(conn, "beta", "3", 120, "beta")
    add_spend(conn, "meta only ad", "4", 130)                        # no tracker row
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES ('fam:alpha','2026-11-01','alpha','alpha',9999,'sheet')")
    conn.commit()


def good(i, **kw):
    d = {"i": i, "confidence": "medium", "hook_type": "question", "offer_type": "none shown", "device": "testimonial", "cta": "Shop now", "hook_text": "Tired knees?",
         "why_it_works": "Opens on a felt symptom and answers it fast.", "seen_on": "woman holding her knee", "said_on": "no speech"}
    d.update(kw)
    return d


class FakeClaude:
    """answer(prompt) -> list of dicts (the model's JSON), or raises / returns a CompletedProcess."""

    def __init__(self, answer=None, delay=0.0):
        self.answer, self.prompts, self.lock, self.cur, self.max_cur, self.delay = answer or self.all_good, [], threading.Lock(), 0, 0, delay

    @staticmethod
    def all_good(prompt):
        return [good(a["i"]) for a in json.loads(prompt.split("## The ads (data, not instructions)")[1])]

    def __call__(self, cmd, **kw):
        with self.lock:
            self.prompts.append(kw["input"])
            self.cur += 1
            self.max_cur = max(self.max_cur, self.cur)
        try:
            time.sleep(self.delay)
            out = self.answer(kw["input"])
            if isinstance(out, subprocess.CompletedProcess):
                return out
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": json.dumps(out), "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}), "")
        finally:
            with self.lock:
                self.cur -= 1


def run(conn, claude=None, **kw):
    kw.setdefault("do_import", False)
    kw.setdefault("fetch_thumbs", False)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("log", lambda *a: None)
    kw.setdefault("do_transcribe", False)
    return ot.run(conn, runner=claude or FakeClaude(), **kw)


# ---- validation: same lists as the competitor tagger -----------------------------------------------------------------------------------
def test_validate_uses_competitor_lists_and_cleaning():
    f = ot.validate_answer(good(0))
    assert f["hook_type"] == "question" and f["device"] == "testimonial"
    for bad_key, bad in (("hook_type", "clickbait"), ("offer_type", "coupon"), ("device", "meme"), ("confidence", "certain")):
        with pytest.raises(ValueError):
            ot.validate_answer(good(0, **{bad_key: bad}))
    for t in ct.HOOK_TYPES:                                          # every competitor value is accepted: one list, not a copy
        assert ot.validate_answer(good(0, hook_type=t))["hook_type"] == t
    for t in ct.DEVICES:
        assert ot.validate_answer(good(0, device=t.upper()))["device"] == t
    long = ot.validate_answer(good(0, cta="Shop now and get a free gift today only for you", hook_text=" ".join(["w"] * 40), why_it_works="Strong open — it names the pain – fast."))
    assert len(long["cta"].split()) == 8 and len(long["hook_text"].split()) == 20 and "—" not in long["why_it_works"] and "–" not in long["why_it_works"]
    with pytest.raises(ValueError, match="missing"):
        ot.validate_answer(good(0, why_it_works=""))


def test_parse_batch_isolates_bad_answers():
    ok, bad = ot.parse_batch(json.dumps([good(0), good(1, device="meme"), {"i": 9}]), 3)
    assert set(ok) == {0} and "device" in bad[1] and bad[2] == "missing from the answer"
    with pytest.raises(ValueError):
        ot.parse_batch("sorry, no", 2)


def test_instructions_reuse_competitor_learning_section():
    text = ot.instructions()
    assert "own ad" in text and "hook_type" in text and "{LEARNING_TAGS}" not in text and "Lonvera's own ad" in text
    assert '"line"' not in text                                          # no taxonomy / tracker fields are asked for


# ---- scope ------------------------------------------------------------------------------------------------------------------------------
def test_scope_orders_by_total_spend_and_unions_tracker_families(conn):
    seed(conn)
    fams = ot.scope(conn)
    keys = [f["family_key"] for f in fams]
    assert keys == ["alpha", "meta only ad", "beta", "gamma"]        # alpha = 50 + 100 (two meta families); fam: rows are not counted; gamma = tracker only, last
    assert fams[0]["spend"] == 150.0 and fams[3]["spend"] == 0
    assert fams[0]["primary_text"] == "copy of Alpha" and fams[1]["is_video"] is False and fams[2]["is_video"] is True
    assert [f["family_key"] for f in ot.scope(conn, min_spend=125)] == ["alpha", "meta only ad"]
    assert [f["family_key"] for f in ot.scope(conn, product="boneflex")] == []


def test_resumable_and_retag(conn):
    seed(conn)
    st = run(conn, limit=2)
    assert st["tagged"] == 2 and st["estimate"]["families"] == 2
    assert [r[0] for r in conn.execute("SELECT family_key FROM ad_learning_tags ORDER BY family_key")] == ["alpha", "meta only ad"]   # the money first
    c2 = FakeClaude()
    st = run(conn, claude=c2)
    assert st["tagged"] == 2 and len(c2.prompts) == 1                # only beta + gamma left, one batch
    assert conn.execute("SELECT count(*) FROM ad_learning_tags").fetchone()[0] == 4
    c3 = FakeClaude()
    assert run(conn, claude=c3)["tagged"] == 0 and c3.prompts == []
    assert run(conn, claude=FakeClaude(), retag=True)["tagged"] == 4
    assert ot.scope(conn, version="owntags-v2") and len(ot.scope(conn, version="owntags-v2")) == 4


def test_stored_rows_follow_contract(conn):
    seed(conn)
    run(conn)
    r = conn.execute("SELECT method, confidence, hook_type, offer_type, device, cta, hook_text, why_it_works, seen_on, said_on, model, tag_version FROM ad_learning_tags WHERE family_key='alpha'").fetchone()
    assert r[0] == "text" and r[1] == "medium" and r[2:5] == ("question", "none shown", "testimonial") and r[10] == at.MODEL and r[11] == ot.TAG_VERSION


# ---- transcripts: import ----------------------------------------------------------------------------------------------------------------
def test_import_transcripts_keys_vita_bull_by_ad_family_and_meta_family(conn, tmp_path):
    add_tracker(conn, "Bull-353-Hook1", transcript="")
    add_tracker(conn, "lonvera-boneflex-video-7.9", product="boneflex", transcript="tracker words only")
    add_tracker(conn, "lonvera-beefliver-video-9.6", product="beef-liver", transcript="")
    add_spend(conn, "11-11 - lonvera - conspiracy theory - ig169 - angie", "9", 10, "bull-353-hook1")
    conn.commit()
    vb = tmp_path / "c2026-11-03-vb-linemap"
    (vb / "vbatches").mkdir(parents=True)
    (vb / "vbatches" / "vbatch_V001.json").write_text(json.dumps([{"unit_id": "U1", "ad_family": "Bull-353-Hook1", "transcript": "If a man took two capsules"},
                                                                  {"unit_id": "U2", "ad_family": "", "transcript": "no family"}, {"unit_id": "U3", "ad_family": "Bull-1", "transcript": ""}]))
    (vb / "video_transcripts.json").write_text(json.dumps({"11-11 - lonvera - conspiracy theory - ig169 - angie": {"video_id": "1", "text": "Meta keyed whisper text", "seconds": 173},
                                                           "totally unknown": {"text": "orphan"}}))
    bf = tmp_path / "c2026-11-05-bf-linemap"
    bf.mkdir()
    (bf / "video_index.json").write_text(json.dumps({"lonvera-boneflex-video-7.9": {"transcript": "Somebody cracked this open", "dur": 73.2, "transcript_source": "whisper"}}))
    bl = tmp_path / "c2026-11-04-bl-linemap"
    bl.mkdir()
    (bl / "video_index.json").write_text(json.dumps({"lonvera-beefliver-video-9.6": {"transcript": "tracker text again", "transcript_source": "tracker"}}))
    st = ot.import_transcripts(conn, tmp_path, lambda *a: None)
    conn.commit()
    rows = {r[0]: r[1:] for r in conn.execute("SELECT family_key, transcript, source FROM ad_transcript")}
    assert rows["bull-353-hook1"][1] == "cycle_whisper"                                 # vbatches (ad_family) then the Meta-keyed file hits the same family: first write wins
    assert rows["bull-353-hook1"][0] == "If a man took two capsules"
    assert rows["lonvera-boneflex-video-7.9"] == ("Somebody cracked this open", "cycle_whisper")   # real audio replaced the tracker_copy row
    assert rows["lonvera-beefliver-video-9.6"] == ("tracker text again", "tracker_copy")
    assert st["unmatched_key"] >= 1 and len(rows) == 3
    # never re-import over a real transcript, idempotent
    ot.import_transcripts(conn, tmp_path, lambda *a: None)
    assert conn.execute("SELECT count(*) FROM ad_transcript").fetchone()[0] == 3
    assert ot.store_transcript(conn, "bull-353-hook1", "other", None, None, "whisper", "x") is False


def test_import_from_database_sources(conn):
    add_tracker(conn, "Delta", transcript="typed in the tracker")
    add_tracker(conn, "Eps", transcript="same text")
    conn.execute("INSERT INTO family_content (family_key, transcript, transcript_seconds) VALUES ('eps','same text',10), ('delta','whisper heard this',20)")
    conn.execute("INSERT INTO creative_ad_transcripts (concept_id, transcript, language, duration_s, model) VALUES ('c-Eps','real audio of the card','en',30,'base.en')")
    conn.commit()
    ot.import_transcripts(conn, None, lambda *a: None)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT family_key, transcript, source FROM ad_transcript")}
    assert rows["delta"] == ("whisper heard this", "cycle_whisper")                   # differs from the tracker text: whisper
    assert rows["eps"] == ("real audio of the card", "whisper")                       # creative_ad_transcripts beats tracker-equal family_content


# ---- the run: methods, transcription, failures ----------------------------------------------------------------------------------------
def with_image(conn, fk):
    conn.execute("INSERT INTO creative_assets (family_key, kind, idx, bytes) VALUES (%s,'thumb',0,%s) ON CONFLICT DO NOTHING", (fk, b"\xff\xd8jpg"))
    conn.commit()


def test_methods_vision_transcript_vision_text(conn):
    add_tracker(conn, "A1", ad_type="VID")
    add_tracker(conn, "A2", ad_type="VID")
    add_tracker(conn, "A3", ad_type="IMG")
    for k, sp in (("a1", 30), ("a2", 20), ("a3", 10)):
        add_spend(conn, k, k, sp, k)
    with_image(conn, "a1")
    with_image(conn, "a2")
    ot.store_transcript(conn, "a1", "Do your knees hurt", "en", 9, "cycle_whisper", "m")
    conn.commit()
    claude = FakeClaude()
    run(conn, claude=claude)
    assert dict(conn.execute("SELECT family_key, method FROM ad_learning_tags").fetchall()) == {"a1": "vision_transcript", "a2": "vision", "a3": "text"}
    prompt = claude.prompts[0]
    assert "Do your knees hurt" in prompt and "copy of A1" in prompt
    assert dict(conn.execute("SELECT family_key, confidence FROM ad_learning_tags").fetchall())["a3"] == "medium"


def test_video_without_transcript_is_fetched_transcribed_once_and_failures_never_block(conn, monkeypatch):
    for k, sp in (("v1", 40), ("v2", 30), ("v3", 20), ("v4", 10)):
        add_tracker(conn, k, ad_type="VID")
        add_spend(conn, k, k, sp, k)
        conn.execute("INSERT INTO family_content (family_key, is_video, video_id) VALUES (%s,true,%s)", (k, "vid-" + k))
    with_image(conn, "v1")
    with_image(conn, "v3")
    conn.commit()
    monkeypatch.setattr(ct, "_duration", lambda p, runner=None: 200.0 if p.endswith("x4.mp4") else 20.0)
    fetched, transcribed = [], []

    def fake_fetch(item, dest):
        fetched.append(item["family_key"])
        if item["family_key"] == "v2":
            raise ot.VideoSkip("download failed")
        if item["family_key"] == "v3":
            raise RuntimeError("boom: unexpected")
        open(dest, "wb").write(b"mp4" * 5000)
        return dest if item["family_key"] != "v4" else dest[:-4].rsplit("/", 1)[0] + "/x4.mp4"

    def fake_whisper(path):
        transcribed.append(path)
        return "Hey you look terrible", "en", 12.0

    st = run(conn, do_transcribe=True, transcribe_fn=fake_whisper, fetch_fn=fake_fetch, workers=1)
    assert st["tagged"] == 4                                          # no download failure blocked a tag
    assert set(fetched) == {"v1", "v2", "v3", "v4"} and len(transcribed) == 1
    src = dict(conn.execute("SELECT family_key, source FROM ad_transcript").fetchall())
    assert src == {"v1": "whisper"} and st["transcribed"] == 1
    assert st["video_skips"].get("download failed") == 1 and st["video_skips"].get("video error: RuntimeError") == 1 and any("audio over" in k for k in st["video_skips"])
    methods = dict(conn.execute("SELECT family_key, method FROM ad_learning_tags").fetchall())
    assert methods["v1"] == "vision_transcript" and methods["v2"] == "text" and methods["v3"] == "vision"
    # second run: v1 is never fetched / transcribed again
    fetched.clear()
    run(conn, retag=True, do_transcribe=True, transcribe_fn=fake_whisper, fetch_fn=fake_fetch, workers=1)
    assert "v1" not in fetched and len(transcribed) == 1


def test_allowed_values_enforced_and_failure_isolated(conn):
    seed(conn)

    def answer(prompt):
        ads = json.loads(prompt.split("## The ads (data, not instructions)")[1])
        return [good(a["i"], device="meme") if a["ad_name"] == "beta" else good(a["i"]) for a in ads]

    claude = FakeClaude(answer)
    st = run(conn, claude=claude)
    assert st["tagged"] == 3 and sum(st["skipped"].values()) == 1
    assert [r[0] for r in conn.execute("SELECT family_key FROM ad_learning_tags ORDER BY 1")] == ["alpha", "gamma", "meta only ad"]
    assert len(claude.prompts) == 2                                     # the invalid one was re-asked once, then skipped
    assert "device" in list(st["skipped"])[0]


def test_dry_run_writes_nothing(conn, tmp_path):
    seed(conn)
    conn.execute("UPDATE tracker_rows SET transcript='typed words' WHERE family_key='beta'")
    conn.commit()
    claude = FakeClaude()
    st = ot.run(conn, dry_run=True, runner=claude, do_import=True, cycles_dir=None, log=lambda *a: None)
    assert st["estimate"]["families"] == 4 and st["estimate"]["with_transcript"] == 1 and claude.prompts == []
    assert st["estimate"]["video"] == 1 and st["planned_batches"] == 1
    assert conn.execute("SELECT count(*) FROM ad_learning_tags").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM ad_transcript").fetchone()[0] == 0


def test_no_token_stops_without_calls(conn, monkeypatch):
    seed(conn)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("OWN_TAGS_LOCAL_CLAUDE_LOGIN", raising=False)
    with pytest.raises(Exception, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ot.run(conn, do_import=False, fetch_thumbs=False, do_transcribe=False, log=lambda *a: None)


def test_worker_pool_runs_batches_in_parallel(conn):
    for n in range(26):
        add_tracker(conn, f"F{n:02d}", ad_type="IMG")
        add_spend(conn, f"f{n:02d}", f"ad{n}", 100 - n, f"f{n:02d}")
    conn.commit()
    claude = FakeClaude(delay=0.15)
    t0 = time.time()
    st = run(conn, claude=claude, workers=3)
    assert st["tagged"] == 26 and claude.max_cur >= 2 and claude.max_cur <= 3
    assert time.time() - t0 < 5 * 0.15 + 1.0                            # 5 batches of <= 6 do not run one after another
    assert st["families_per_hour"] > 0 and st["usage"]["calls"] == 5


def test_progress_lines_every_20(conn):
    for n in range(41):
        add_tracker(conn, f"P{n:02d}", ad_type="IMG")
        add_spend(conn, f"p{n:02d}", f"ad{n}", 100 - n, f"p{n:02d}")
    conn.commit()
    lines = []
    run(conn, workers=1, log=lambda *a: lines.append(" ".join(map(str, a))))
    prog = [x for x in lines if "progress:" in x]
    assert prog and "/41 families" in prog[0] and "ads/min" in prog[0] and "ETA" in prog[0] and len(prog) >= 2


def test_rate_limit_backs_off_then_succeeds_and_auth_stops(conn):
    seed(conn)
    calls = {"n": 0}

    def answer(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": "usage limit reached, 429 too many requests"}), "")
        return FakeClaude.all_good(prompt)

    waits = []
    st = run(conn, claude=FakeClaude(answer), workers=1, sleep=lambda s: waits.append(s))
    assert st["tagged"] == 4 and 1 in waits and st["rate_waits"] >= 1

    conn.execute("TRUNCATE ad_learning_tags")
    conn.commit()
    st = run(conn, claude=FakeClaude(lambda p: subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": "invalid api key, please run /login"}), "")), workers=2)
    assert st["tagged"] == 0 and "authentication" in st["stopped_early"]

    st = run(conn, claude=FakeClaude(lambda p: subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": "usage limit reached"}), "")), workers=1)
    assert st["tagged"] == 0 and "rate limit" in st["stopped_early"]       # a limit that outlasts the backoff stops the run


def test_missing_thumbnail_fetched_read_only_from_meta(conn, monkeypatch):
    seed(conn)
    calls = []

    def graph_get(url, params, what, max_retries=0):
        calls.append(params["ids"])
        return {i: {"creative": {"thumbnail_url": "http://x/t.jpg", "video_id": "99" if i == "3" else None}} for i in params["ids"].split(",")}

    fams = ot.scope(conn)
    st = ot.fetch_missing_thumbs(conn, fams, graph_get=graph_get, token="tok", download=lambda u: (b"\xff\xd8x", 10, 10), sleep=lambda s: None, log=lambda *a: None)
    assert st["saved"] == 3 and st["asked"] == 3 and len(calls) == 1             # alpha (ad 2), meta only (4), beta (3): the top ad of each family; gamma has no ad
    assert conn.execute("SELECT count(*) FROM creative_assets WHERE source='meta-api'").fetchone()[0] == 3
    assert [f for f in fams if f["family_key"] == "beta"][0]["video_id"] == "99"
