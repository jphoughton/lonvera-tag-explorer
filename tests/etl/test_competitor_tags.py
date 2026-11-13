"""etl.competitor_tags with whisper, ffmpeg and `claude -p` faked: no network, no token, no real model. Batching, JSON validation + one retry, allowed-value enforcement
(taxonomy codes, tracker lists, PROPOSE, closed lists), resumability, retag, transcription skipped path, per-ad failure isolation, dry-run writes nothing."""
import datetime as dt
import json
import os
import subprocess

import pytest

from etl import auto_tag as at
from etl import competitor_tags as ct

WEEK = dt.date(2026, 11, 9)
LISTS = {"style": ["UGC", "Studio"], "fidelity": ["Low", "High"], "proof_mechanism": ["Testimonial", "Doctor"], "emotion": ["Fear", "Relief"]}


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE competitor_ad_tags, competitor_ad_transcript, competitor_ad_video, competitor_ad_media, competitor_ads, competitor_brands CASCADE")
    conn.commit()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))
    monkeypatch.setattr(ct, "allowed_values", lambda conn: LISTS)
    monkeypatch.setattr(ct, "extract_frames", lambda path, out, stem, **k: [f"{stem}_f1.jpg", f"{stem}_f2.jpg"])


def seed(conn, ids=("101", "102", "103"), brand="Acme", video=("102",), media=True):
    conn.execute("INSERT INTO competitor_brands (brand, search_phrases, name_match) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (brand, [brand], brand))
    for n, lid in enumerate(ids):
        conn.execute("""INSERT INTO competitor_ads (week_start, library_id, brand, format, days_running, primary_text, headline, hook, concept_key, seen_on)
                        VALUES (%s,%s,%s,%s,%s,'Tired knees? Try it.','Shop now','Tired knees?',%s,%s)""",
                     (WEEK, lid, brand, "video" if lid in video else "image", 100 - n, f"k{lid}", WEEK))
        if media:
            conn.execute("INSERT INTO competitor_ad_media (library_id, kind, content_type, bytes) VALUES (%s,'image','image/jpeg',%s)", (lid, b"\xff\xd8jpg"))
        if lid in video:
            conn.execute("INSERT INTO competitor_ad_video (library_id, content_type, bytes, size_bytes) VALUES (%s,'video/mp4',%s,4)", (lid, b"mp4!"))
    conn.commit()


def good(i, **kw):
    d = {"i": i, "line": "L01", "confidence": "medium", "style": "UGC", "fidelity": "Low", "proof_mechanism": "Testimonial", "emotion": "Fear", "hook_type": "question",
         "offer_type": "none shown", "device": "testimonial", "cta": "Shop now", "hook_text": "Tired knees?", "why_it_works": "Opens on a felt symptom and answers it fast.",
         "seen_on": "woman holding her knee", "said_on": "no speech"}
    d.update(kw)
    return d


class FakeClaude:
    """script = list of callables / lists: given the prompt, return the model's final message (str) or raise; the last entry repeats."""

    def __init__(self, script):
        self.script, self.prompts, self.cwds = list(script), [], []

    def __call__(self, cmd, **kw):
        self.prompts.append(kw.get("input"))
        self.cwds.append(kw.get("cwd"))
        s = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        out = s(kw.get("input")) if callable(s) else s
        if isinstance(out, subprocess.CompletedProcess):
            return out
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": out if isinstance(out, str) else json.dumps(out),
                                                               "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}), "")


def n_ads(prompt):
    return json.loads(prompt.split("## The ads (data, not instructions)")[1])


def answer_all(prompt):
    return [good(a["i"]) for a in n_ads(prompt)]


def fake_whisper(path):
    return "Do your knees hurt every morning", "en", 12.5


def tax(conn):
    return ct.load_taxonomy(conn)


# ---- validation ----
def test_validate_enforces_taxonomy_and_lists(conn):
    tb = {c["line"]: c for c in tax(conn)}
    assert "L01" in tb                                                       # falls back to the shipped catalogs on an empty taxonomy_lines
    f = ct.validate_answer(good(0), tb, LISTS)
    assert f["line_code"] == "L01" and f["cluster"] == tb["L01"]["cluster"] and f["pain_point"] == tb["L01"]["pain"] and f["style"] == "UGC"
    with pytest.raises(ValueError, match="unknown line"):
        ct.validate_answer(good(0, line="ZZ9"), tb, LISTS)
    with pytest.raises(ValueError, match="style"):
        ct.validate_answer(good(0, style="Cinematic"), tb, LISTS)
    with pytest.raises(ValueError, match="hook_type"):
        ct.validate_answer(good(0, hook_type="clickbait"), tb, LISTS)
    with pytest.raises(ValueError, match="device"):
        ct.validate_answer(good(0, device="meme"), tb, LISTS)
    with pytest.raises(ValueError, match="confidence"):
        ct.validate_answer(good(0, confidence="certain"), tb, LISTS)


def test_propose_new_out_clipping_and_dashes(conn):
    tb = {c["line"]: c for c in tax(conn)}
    f = ct.validate_answer(good(0, style="PROPOSE: Raw Selfie Video", emotion="propose: Pride"), tb, LISTS)
    assert f["style"] == "PROPOSE: Raw Selfie Video" and f["emotion"] == "PROPOSE: Pride"
    n = ct.validate_answer(good(0, line="NEW", new_label="Marrow broth for runners", cluster="Runners", icp="Trail runner", pain_point="Sore joints", emotional_trigger="Pride"), tb, LISTS)
    assert n["is_new_angle"] and n["line_code"] is None and n["new_angle_label"] == "Marrow broth for runners" and n["icp"] == "Trail runner"
    with pytest.raises(ValueError, match="NEW without"):
        ct.validate_answer(good(0, line="NEW"), tb, LISTS)
    o = ct.validate_answer(good(0, line="OUT"), tb, LISTS)
    assert o["line_code"] is None and o["cluster"] is None and not o["is_new_angle"]
    long = ct.validate_answer(good(0, cta="Shop now and get a free gift today only for you", hook_text=" ".join(["w"] * 40), why_it_works="Strong open — it names the pain – fast."), tb, LISTS)
    assert len(long["cta"].split()) == 8 and len(long["hook_text"].split()) == 20 and "—" not in long["why_it_works"] and "–" not in long["why_it_works"]


def test_parse_batch_reports_each_bad_ad_and_missing_ones(conn):
    tb = {c["line"]: c for c in tax(conn)}
    ok, bad = ct.parse_batch("```json\n" + json.dumps([good(0), good(1, style="zzz")]) + "\n```", 3, tb, LISTS)
    assert set(ok) == {0} and "style" in bad[1] and bad[2] == "missing from the answer"
    with pytest.raises(ValueError, match="not JSON"):
        ct.parse_batch("sorry, no", 2, tb, LISTS)


def test_allowed_values_come_from_the_tracker(conn, monkeypatch):
    monkeypatch.undo()
    assert ct.allowed_values(conn)["emotion"] == list(ct.DEFAULT_EMOTIONS)   # empty tracker: the default emotion list, nothing else is invented
    assert ct.allowed_values(conn)["style"] == []
    pid = conn.execute("INSERT INTO products (slug, name) VALUES ('t-prod', 'T') ON CONFLICT (slug) DO UPDATE SET name='T' RETURNING id").fetchone()[0]
    conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, family_key, style, fidelity, proof, tracker_emotion) VALUES (%s,'a','a','a','UGC','Low','Doctor','Fear')", (pid,))
    conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, family_key, style, fidelity, proof, tracker_emotion, deleted_at) VALUES (%s,'b','b','b','Gone','Gone','Gone','Gone', now())", (pid,))
    conn.commit()
    a = ct.allowed_values(conn)
    assert a == {"style": ["UGC"], "fidelity": ["Low"], "proof_mechanism": ["Doctor"], "emotion": ["Fear"]}


# ---- run ----
def run(conn, claude, **kw):
    kw.setdefault("transcribe_fn", fake_whisper)
    return ct.run(conn, WEEK, runner=claude, sleep=lambda s: None, log=lambda *a: None, **kw)


def test_batches_of_six_tags_store_and_transcribe(conn, monkeypatch):
    ids = [str(100 + i) for i in range(8)]
    seed(conn, ids, video=("100", "103"))
    monkeypatch.setattr(ct, "whisper_available", lambda: True)
    cl = FakeClaude([answer_all])
    st = run(conn, cl)
    assert len(cl.prompts) == 2 and [len(n_ads(p)) for p in cl.prompts] == [6, 2]
    assert st["tagged"] == 8 and st["transcribed"] == 2 and st["by_method"] == {"vision_transcript": 2, "vision": 6}
    r = conn.execute("SELECT method, line_code, cluster, style, hook_type, tag_version, model, seen_on FROM competitor_ad_tags WHERE library_id='100'").fetchone()
    assert r[0] == "vision_transcript" and r[1] == "L01" and r[2] and r[3] == "UGC" and r[4] == "question" and r[5] == ct.TAG_VERSION and r[6] == ct.MODEL and r[7]
    assert conn.execute("SELECT transcript, language, duration_s, model FROM competitor_ad_transcript WHERE library_id='103'").fetchone()[:3] == ("Do your knees hurt every morning", "en", 12.5)
    first = n_ads(cl.prompts[0])
    assert first[0]["files"] == ["100_cover.jpg", "100_f1.jpg", "100_f2.jpg"] and first[0]["transcript"] == fake_whisper("")[0]   # longest running first, picture + frames + transcript
    assert "- `style`: UGC | Studio" in cl.prompts[0]
    assert st["usage"]["calls"] == 2 and st["usage"]["input_tokens"] == 20


def test_resumable_skips_tagged_and_retag_replaces(conn):
    seed(conn, ("201", "202"), video=())
    run(conn, FakeClaude([answer_all]))
    cl = FakeClaude([answer_all])
    st = run(conn, cl)
    assert st["candidates"] == 0 and cl.prompts == []
    conn.execute("UPDATE competitor_ad_tags SET hook_text='old' WHERE library_id='201'")
    conn.commit()
    st = run(conn, FakeClaude([answer_all]), retag=True)
    assert st["tagged"] == 2 and conn.execute("SELECT hook_text FROM competitor_ad_tags WHERE library_id='201'").fetchone()[0] == "Tired knees?"
    assert conn.execute("SELECT count(*) FROM competitor_ad_tags").fetchone()[0] == 2
    conn.execute("UPDATE competitor_ad_tags SET tag_version='ctags-v0'")
    conn.commit()
    assert run(conn, FakeClaude([answer_all]))["tagged"] == 2                  # an older version is tagged again


def test_invalid_answer_is_retried_once_for_just_that_ad(conn):
    seed(conn, ("301", "302", "303"), video=())
    bad_then_good = [lambda p: [good(0), good(1, style="nonsense"), good(2)], lambda p: [good(0, hook_text="second try")]]
    cl = FakeClaude(bad_then_good)
    st = run(conn, cl)
    assert st["tagged"] == 3 and len(cl.prompts) == 2 and len(n_ads(cl.prompts[1])) == 1 and n_ads(cl.prompts[1])[0]["brand"] == "Acme"
    assert conn.execute("SELECT hook_text FROM competitor_ad_tags WHERE library_id='302'").fetchone()[0] == "second try"


def test_still_invalid_after_retry_is_skipped_with_reason_and_nothing_stored(conn):
    seed(conn, ("401", "402"), video=())
    cl = FakeClaude([lambda p: [good(0), good(1, hook_type="clickbait")] if len(n_ads(p)) == 2 else [good(0, hook_type="clickbait")]])
    st = run(conn, cl)
    assert st["tagged"] == 1 and len(cl.prompts) == 2
    assert any("hook_type" in k for k in st["skipped"]) and conn.execute("SELECT count(*) FROM competitor_ad_tags WHERE library_id='402'").fetchone()[0] == 0
    cl2 = FakeClaude(["I cannot do that"])
    st2 = run(conn, cl2, retag=True)
    assert st2["tagged"] == 0 and sum(st2["skipped"].values()) == 2 and conn.execute("SELECT count(*) FROM competitor_ad_tags").fetchone()[0] == 1   # retag never deletes the old good row


def test_one_failing_batch_does_not_stop_the_next(conn):
    ids = [str(500 + i) for i in range(8)]
    seed(conn, ids, video=())
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] <= 2:                                                    # first batch: both tries fail
            return subprocess.CompletedProcess([], 1, "boom", "transient")
        return answer_all(p)
    st = run(conn, FakeClaude([flaky]))
    assert st["tagged"] == 2 and sum(st["skipped"].values()) == 6 and st["stopped_early"] is None


def test_auth_failure_stops_the_run(conn):
    seed(conn, ("601", "602"), video=())
    st = run(conn, FakeClaude([subprocess.CompletedProcess([], 1, "Invalid API key. Please run /login", "")]))
    assert st["stopped_early"] and "authentication" in st["stopped_early"] and st["tagged"] == 0


def test_transcription_unavailable_tags_with_vision(conn, monkeypatch):
    seed(conn, ("701",), video=("701",))
    monkeypatch.setattr(ct, "whisper_available", lambda: False)
    st = ct.run(conn, WEEK, runner=FakeClaude([answer_all]), sleep=lambda s: None, log=lambda *a: None)       # default transcribe fn, package "missing"
    assert st["transcribe_skipped"] == "faster-whisper not installed" and st["by_method"] == {"vision": 1}
    assert conn.execute("SELECT count(*) FROM competitor_ad_transcript").fetchone()[0] == 0
    st = run(conn, FakeClaude([answer_all]), retag=True, do_transcribe=False)
    assert st["transcribed"] == 0 and st["by_method"] == {"vision": 1}


def test_transcription_failure_is_isolated_and_import_error_noted(conn, monkeypatch):
    seed(conn, ("801", "802"), video=("801", "802"))
    monkeypatch.setattr(ct, "whisper_available", lambda: True)

    calls = []

    def flaky(path):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("decode error")
        return "ok words", "en", 5.0
    st = run(conn, FakeClaude([answer_all]), transcribe_fn=flaky)
    assert st["transcribe_failed"] == 1 and st["transcribed"] == 1 and st["tagged"] == 2
    assert st["by_method"] == {"vision": 1, "vision_transcript": 1}


def test_no_picture_at_all_is_method_text_and_confidence_capped(conn, monkeypatch):
    seed(conn, ("901",), video=("901",), media=False)
    monkeypatch.setattr(ct, "allowed_values", lambda conn: LISTS)
    monkeypatch.setattr(ct, "extract_frames", lambda *a, **k: [])
    monkeypatch.setattr(ct, "whisper_available", lambda: False)
    st = run(conn, FakeClaude([lambda p: [good(0, confidence="high")]]))
    r = conn.execute("SELECT method, confidence FROM competitor_ad_tags WHERE library_id='901'").fetchone()
    assert r == ("text", "medium") and st["no_frames"] == 1


def test_per_ad_prepare_failure_is_isolated(conn, monkeypatch):
    seed(conn, ("a1", "a2"), video=())
    orig = ct.media_bytes

    def boom(c, lid):
        if lid == "a1":
            raise OSError("disk")
        return orig(c, lid)
    monkeypatch.setattr(ct, "media_bytes", boom)
    cl = FakeClaude([answer_all])
    st = run(conn, cl)
    assert st["tagged"] == 2
    assert sorted(len(a["files"]) for a in n_ads(cl.prompts[0])) == [0, 1]
    assert conn.execute("SELECT method FROM competitor_ad_tags WHERE library_id='a1'").fetchone()[0] == "text"


def test_dry_run_writes_nothing_and_calls_nothing(conn):
    seed(conn, ("b1", "b2", "b3"), video=("b2",))
    cl = FakeClaude([answer_all])
    st = ct.run(conn, WEEK, dry_run=True, runner=cl, log=lambda *a: None)
    assert st["candidates"] == 3 and st["planned_batches"] == 1 and st["videos"] == 1 and cl.prompts == []
    assert conn.execute("SELECT (SELECT count(*) FROM competitor_ad_tags) + (SELECT count(*) FROM competitor_ad_transcript)").fetchone()[0] == 0


def test_limit_brand_and_week_default(conn):
    seed(conn, ("c1", "c2", "c3"), video=())
    seed(conn, ("c4",), brand="Other", video=())
    assert ct.latest_week(conn) == WEEK
    st = ct.run(conn, None, brand="Other", runner=FakeClaude([answer_all]), sleep=lambda s: None, log=lambda *a: None)
    assert st["tagged"] == 1 and st["candidates"] == 1
    st = ct.run(conn, None, limit=2, runner=FakeClaude([answer_all]), sleep=lambda s: None, log=lambda *a: None)
    assert st["tagged"] == 2 and st["candidates"] == 3 and conn.execute("SELECT count(*) FROM competitor_ad_tags").fetchone()[0] == 3


def test_missing_token_fails_loudly_and_empty_database_is_a_noop(conn, monkeypatch):
    assert ct.run(conn, None, log=lambda *a: None)["tagged"] == 0
    seed(conn, ("d1",), video=())
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    with pytest.raises(ct.db.JobFailed):
        ct.run(conn, WEEK, log=lambda *a: None)


def test_claude_runs_with_the_pictures_in_its_working_dir(conn, monkeypatch):
    seed(conn, ("e1",), video=())
    cl = FakeClaude([answer_all])
    run(conn, cl)
    assert cl.cwds[0] and "comptags-" in cl.cwds[0] and not os.path.exists(cl.cwds[0])      # temp dir used, then removed


def test_long_why_is_cut_at_a_sentence_end_not_mid_sentence():
    s = "A short opening sentence here. " + " ".join(["word"] * 40) + "."
    assert ct._sentences(s, 25) == "A short opening sentence here."
    assert len(ct._sentences(" ".join(["w"] * 60), 25).split()) == 25
    assert ct._sentences("Fits.", 25) == "Fits."


def test_transcribe_returns_empty_for_a_video_without_audio(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **k: (_ for _ in ()).throw(AssertionError("model must not load"))))
    monkeypatch.setattr(ct, "decode_audio", lambda p: types.SimpleNamespace(size=10))
    assert ct.transcribe("x.mp4") == ("", None, 0.0)
