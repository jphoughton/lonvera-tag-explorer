"""Playable competitor video copies in etl.competitor_ads, all mocked (no network, ffmpeg and ffprobe replaced by a fake runner; one real-ffmpeg test runs only when ffmpeg exists): card -> mp4 url,
selection reuse (curated slice, videos only, stored skipped), encode wrapper (stream copy, re-encode, lower bitrate, 6 MB cap, no-ffmpeg fallback, skip reasons), capture isolation, size-cap guard and
eviction order, prune (21 days), the run() hook, and --video-backfill (resumable, competitor_ads untouched)."""
import datetime as dt
import json
import shutil
import subprocess

import pytest

from etl import competitor_ads as ca

TODAY = dt.date(2026, 11, 8)
WEEK = dt.date(2026, 11, 2)
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE competitor_ads, competitor_concept_class, competitor_brands, competitor_ad_media, competitor_ad_video CASCADE")
    conn.commit()
    monkeypatch.setattr(ca, "BRAND_PAUSE", 0)
    monkeypatch.setattr(ca, "MEDIA_PAUSE", 0)
    monkeypatch.setattr(ca, "VIDEO_PAUSE", 0)


def ad(lid, brand="Acme", concept=None, days=10, fmt="video", url="https://video.xx.fbcdn.net/v.mp4"):
    return {"brand": brand, "library_id": str(lid), "concept_key": concept or f"c{lid}", "days_running": days, "format": fmt,
            "start_date": TODAY - dt.timedelta(days=days), "video_url": url}


PROBE_H264 = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 360, "height": 640}, {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "29.5"}}
PROBE_HEVC = {"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 720, "height": 1280}, {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "29.5"}}


class FakeFF:
    """Stands in for subprocess.run: ffprobe answers `probe`; each ffmpeg call writes the next size of `sizes` to its output file (last argument), or fails when the size is None."""

    def __init__(self, probe, sizes, probe_rc=0):
        self.probe, self.sizes, self.probe_rc, self.calls = probe, list(sizes), probe_rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0].endswith("ffprobe"):
            return subprocess.CompletedProcess(cmd, self.probe_rc, json.dumps(self.probe), "")
        size = self.sizes.pop(0)
        if size is None:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        with open(cmd[-1], "wb") as f:
            f.write(b"v" * size)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def ffmpeg_calls(self):
        return [c for c in self.calls if c[0].endswith("ffmpeg")]


# ---- card -> url ----
def test_pick_video_and_parse_card_carry_the_mp4_link():
    assert ca.pick_video({"vsrc": ["blob:https://x", "https://video.xx.fbcdn.net/a.mp4?oe=1"]}) == "https://video.xx.fbcdn.net/a.mp4?oe=1"
    assert ca.pick_video({"vsrc": []}) is None and ca.pick_video({}) is None
    lines = ["Active", "Library ID: 77", "Started running on Oct 1, 2026", "Acme Co", "Sponsored", "Copy here", "SHOP.EXAMPLE.COM", "Shop the blend", "Shop now"]
    c = ca.parse_card({"text": "\n".join(lines), "hrefs": [], "vids": 1, "imgs": [], "posters": [], "vsrc": ["https://video.xx.fbcdn.net/a.mp4"]})
    assert c["format"] == "video" and c["video_url"] == "https://video.xx.fbcdn.net/a.mp4"
    assert ca.parse_card({"text": "\n".join(lines), "hrefs": [], "vids": 0, "imgs": [], "posters": []})["video_url"] is None


# ---- selection ----
def test_select_video_reuses_the_curated_slice_and_keeps_videos_only():
    rows = [ad(i, days=300 - i, concept=f"c{i}", fmt="video" if i % 2 == 0 else "image") for i in range(30)]
    sel = ca.select_video(rows, set(), TODAY)
    chosen = ca.select_media(rows, set(), TODAY, 10 ** 9)
    assert [a["library_id"] for a in sel] == [a["library_id"] for a in chosen if a["format"] == "video"]
    assert len(chosen) == 12 and len(sel) == 6                                          # 12 longest-running concepts, half are video: no deeper video is pulled in
    sel2 = ca.select_video(rows, {"0", "2"}, TODAY)
    assert [a["library_id"] for a in sel2] == ["4", "6", "8", "10"] and ca.select_video(rows, set(), TODAY, cap=0) == []
    fresh = [ad(500 + i, days=i, concept=f"n{i}") for i in range(10)]                    # new this week: up to 8 per brand
    assert len(ca.select_video(rows + fresh, set(), TODAY)) == 6 + 8


# ---- encode wrapper ----
def test_encode_stream_copies_a_h264_aac_source_trimmed_with_faststart():
    ff = FakeFF(PROBE_H264, [900_000])
    data, dur, w, h = ca.encode_video(MP4, runner=ff, ffmpeg="/usr/bin/ffmpeg")
    assert len(data) == 900_000 and (dur, w, h) == (29.5, 360, 640)
    cmd = ff.ffmpeg_calls()[0]
    assert cmd[cmd.index("-t") + 1] == "30" and "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy" and "+faststart" in cmd and "libx264" not in cmd
    assert len(ff.ffmpeg_calls()) == 1


def test_encode_reencodes_other_codecs_and_lowers_the_bitrate_once():
    ff = FakeFF(PROBE_HEVC, [7_000_000, 5_000_000])                                     # first try over the 6 MB cap, second (lower bitrate) fits
    data, *_ = ca.encode_video(MP4, runner=ff, ffmpeg="/usr/bin/ffmpeg")
    calls = ff.ffmpeg_calls()
    assert len(data) == 5_000_000 and len(calls) == 2 and "copy" not in calls[0]
    assert calls[0][calls[0].index("-b:v") + 1] == "650k" and calls[0][calls[0].index("-b:a") + 1] == "64k" and "scale='min(480,iw)':-2" in calls[0]
    assert calls[1][calls[1].index("-b:v") + 1] == "300k"


def test_encode_copy_over_the_cap_falls_back_to_reencode_then_skips_with_a_reason():
    ff = FakeFF(PROBE_H264, [8_000_000, 5_500_000])
    data, *_ = ca.encode_video(MP4, runner=ff, ffmpeg="/usr/bin/ffmpeg")
    assert len(data) == 5_500_000 and "copy" not in ff.ffmpeg_calls()[1]
    with pytest.raises(ValueError, match="over the size cap"):
        ca.encode_video(MP4, runner=FakeFF(PROBE_H264, [8_000_000, 7_000_000, 6_500_000]), ffmpeg="/usr/bin/ffmpeg")
    with pytest.raises(ValueError, match="ffmpeg failed"):
        ca.encode_video(MP4, runner=FakeFF(PROBE_HEVC, [None, None]), ffmpeg="/usr/bin/ffmpeg")
    with pytest.raises(ValueError, match="not a video"):
        ca.encode_video(b"<html>", runner=FakeFF(PROBE_H264, [], probe_rc=1), ffmpeg="/usr/bin/ffmpeg")


def test_encode_without_ffmpeg_keeps_a_small_original_and_skips_the_rest(monkeypatch):
    monkeypatch.setattr(ca.shutil, "which", lambda n: None)
    assert ca.encode_video(MP4) == (MP4, None, None, None)
    with pytest.raises(ValueError, match="no ffmpeg and original over"):
        ca.encode_video(MP4 + b"\x00" * 6_000_000)
    with pytest.raises(ValueError, match="not an mp4"):
        ca.encode_video(b"\x00" * 50)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_encode_with_the_real_ffmpeg_trims_to_30_seconds(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=15:duration=40", "-f", "lavfi", "-i", "sine=duration=40", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    data, dur, w, h = ca.encode_video(src.read_bytes())
    assert 28 <= dur <= 31 and (w, h) == (360, 640) and len(data) <= ca.VIDEO_MAX_BYTES and data[4:8] == b"ftyp"
    assert data.find(b"moov") < data.find(b"mdat")                                       # moov first: streams and seeks


# ---- capture ----
def fake_encode(data):
    if data == b"junk":
        raise ValueError("not a video: ffprobe failed")
    return b"E" * 1000, 12.0, 360, 640


def test_capture_counts_reasons_failures_and_stores_the_rest(conn):
    def fetch(url):
        if "bad" in url:
            raise OSError("boom")
        return b"junk" if "junk" in url else MP4
    picks = [ad(1), ad(2, url="https://x.fbcdn.net/bad"), ad(3, url="https://x.fbcdn.net/junk"), ad(4, url=None), ad(5)]
    st = ca.new_video_stats()
    logs = []
    ca.capture_videos(conn, picks, st, 300, fetch, fake_encode, lambda s: None, logs.append)
    assert (st["selected"], st["stored"], st["failed"], st["no_video"], st["skipped"], st["bytes"]) == (5, 2, 1, 1, {"not a video: ffprobe failed": 1}, 2000)
    rows = conn.execute("SELECT library_id, content_type, duration_s, width, height, size_bytes FROM competitor_ad_video ORDER BY 1").fetchall()
    assert [(r[0], r[1], float(r[2]), r[3], r[4], r[5]) for r in rows] == [("1", "video/mp4", 12.0, 360, 640, 1000), ("5", "video/mp4", 12.0, 360, 640, 1000)]
    assert any("boom" in l for l in logs) and any("skipped" in l for l in logs)


def test_fetch_video_refuses_other_hosts():
    for u in ("http://video.xx.fbcdn.net/a.mp4", "https://evil.example.com/a.mp4", "file:///etc/passwd"):
        with pytest.raises(ValueError, match="refused host"):
            ca.fetch_video(u)


# ---- cap guard + eviction ----
def seed_ads(conn, rows):
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme') ON CONFLICT DO NOTHING")
    for lid, wk, days in rows:
        conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, concept_key, format, seen_on) VALUES (%s,%s,'Acme',%s,%s,%s,'video',%s)",
                     (wk, lid, TODAY, days, f"k{lid}", TODAY))
    conn.commit()


def put(conn, lid, size, age_days=0):
    ca.store_video(conn, lid, b"x" * size, 5.0, 1, 1)
    conn.execute("UPDATE competitor_ad_video SET fetched_at = now() - make_interval(days => %s) WHERE library_id=%s", (age_days, lid))
    conn.commit()


def test_capture_stops_at_the_cap_and_evicts_older_weeks_first(conn):
    seed_ads(conn, [("1", WEEK, 400), ("2", WEEK, 10), ("9", WEEK - dt.timedelta(days=7), 50)])
    put(conn, "9", 2_000, age_days=8)                                                   # old-week video: evictable
    put(conn, "2", 2_000)                                                               # latest week: protected while capturing
    st = ca.new_video_stats()
    ca.capture_videos(conn, [ad("1")], st, 0.004, lambda u: MP4, fake_encode, lambda s: None, lambda m: None)    # cap 4000 bytes, table holds 4000
    assert st["stored"] == 1 and st["capped"] == 0
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()} == {"1", "2"}      # '9' made room
    st = ca.new_video_stats()
    ca.capture_videos(conn, [ad("3"), ad("4")], st, 0.003, lambda u: MP4, fake_encode, lambda s: None, lambda m: None)   # full of latest-week videos: nothing to evict
    assert st["stored"] == 0 and st["capped"] == 2 and st["selected"] == 0


def test_enforce_cap_keeps_the_longest_running_ads_of_the_latest_week_last(conn):
    seed_ads(conn, [("1", WEEK, 500), ("2", WEEK, 100), ("3", WEEK, 20), ("8", WEEK - dt.timedelta(days=7), 30)])
    for lid, age in (("1", 0), ("2", 0), ("3", 0), ("8", 3)):
        put(conn, lid, 1_000, age)
    assert ca.enforce_video_cap(conn, 0.0035, keep_latest=True) == 1                    # only the old-week video goes
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()} == {"1", "2", "3"}
    assert ca.enforce_video_cap(conn, 0.0015, keep_latest=False) == 2                   # shortest-running first: 3 (20 days), then 2 (100 days)
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()} == {"1"}
    assert ca.enforce_video_cap(conn, 300, keep_latest=False) == 0


def test_prune_after_21_days_keeps_latest_week_and_recent_rows(conn):
    seed_ads(conn, [("1", WEEK, 5)])
    for lid, age in (("1", 40), ("2", 40), ("3", 20)):
        put(conn, lid, 10, age)
    assert ca.prune_video(conn) == 1                                                    # '2': older than 21 days and not in the latest week
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()} == {"1", "3"}


# ---- run() hook ----
def card(lib, adv, start, text, vsrc, vids=1):
    lines = ["​", "Active", f"Library ID: {lib}", f"Started running on {start} · Total active time 30 days", "Platforms", "​", "Open Dropdown", "See ad details", adv, "Sponsored",
             *text.split("\n"), "SHOP.EXAMPLE.COM", "Shop the blend", "Shop now"]
    return {"text": "\n".join(lines), "hrefs": [], "vids": vids, "imgs": [], "posters": [], "vsrc": [vsrc] if vsrc else []}


class FakeBrowser:
    def __init__(self, cards, one=None):
        self.cards, self.one, self.loaded = cards, one or {}, []

    def load(self, url, max_cards=150):
        return self.cards, ""

    def load_one(self, lib):
        self.loaded.append(lib)
        r = self.one.get(lib)
        if isinstance(r, Exception):
            raise r
        return r or []


def test_run_stores_videos_isolates_crashes_and_honours_no_video(conn):
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme')")
    conn.commit()
    cards = [card("111", "Acme Co", "Oct 1, 2026", "Old copy", "https://video.xx.fbcdn.net/a.mp4"), card("222", "Acme Co", "Nov 6, 2026", "Fresh copy here", "https://video.xx.fbcdn.net/b.mp4"),
             card("333", "Acme Co", "Oct 2, 2026", "An image ad", None, vids=0)]
    fetched = []
    def fetch_v(u):
        fetched.append(u)
        return MP4
    kw = dict(week=WEEK, do_classify=False, sleep=lambda s: None, today=TODAY, fetch=lambda u: (_ for _ in ()).throw(OSError("no thumbs")), fetch_vid=fetch_v, encode=fake_encode)
    st = ca.run(conn, "Acme", browser=FakeBrowser(cards), **kw)
    assert st["ads"] == 3 and st["video"]["stored"] == 2 and st["video"]["failed"] == 0 and len(fetched) == 2
    assert {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()} == {"111", "222"}
    fetched.clear()
    st = ca.run(conn, "Acme", browser=FakeBrowser(cards), **kw)
    assert fetched == [] and st["video"]["stored"] == 0                                 # stored ones are not downloaded again
    conn.execute("TRUNCATE competitor_ad_video")
    conn.commit()
    mp = pytest.MonkeyPatch()
    mp.setattr(ca, "select_video", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("selection exploded")))
    try:
        st = ca.run(conn, "Acme", browser=FakeBrowser(cards), **kw)
    finally:
        mp.undo()
    assert st["ads"] == 3 and not st["failed_brands"] and conn.execute("SELECT count(*) FROM competitor_ads WHERE week_start=%s", (WEEK,)).fetchone()[0] == 3
    st = ca.run(conn, "Acme", browser=FakeBrowser(cards), do_video=False, **kw)
    assert "video" not in st and conn.execute("SELECT count(*) FROM competitor_ad_video").fetchone()[0] == 0
    st = ca.run(conn, "Acme", browser=FakeBrowser(cards), do_media=False, **kw)         # --no-media skips both
    assert "video" not in st and "media" not in st


# ---- backfill ----
def seed_rows(conn, n=15):
    conn.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match) VALUES ('Acme','x',ARRAY['acme'],'acme') ON CONFLICT DO NOTHING")
    for i in range(n):
        conn.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, concept_key, format, seen_on) VALUES (%s,%s,'Acme',%s,%s,%s,%s,%s)",
                     (WEEK, str(900 + i), TODAY - dt.timedelta(days=200 - i), 200 - i, f"k{i}", "image" if i == 14 else "video", TODAY))
    conn.commit()


def test_backfill_selects_videos_from_stored_rows_is_resumable_and_leaves_ads_untouched(conn):
    seed_rows(conn)
    before = conn.execute("SELECT * FROM competitor_ads ORDER BY library_id").fetchall()
    one = {str(900 + i): [card(str(900 + i), "Acme Co", "Oct 1, 2026", "copy", f"https://video.xx.fbcdn.net/{i}.mp4")] for i in range(15)}
    one["903"] = []                                                                      # gone from the library
    one["904"] = ca.BrandFailed("browser error: wall")
    one["905"] = [card("905", "Acme Co", "Oct 1, 2026", "copy", None)]                   # a card whose video has no link
    b = FakeBrowser([], one)
    logs = []
    st = ca.backfill_video(conn, b, today=TODAY, fetch=lambda u: MP4, encode=fake_encode, sleep=lambda s: None, log=logs.append, progress_every=5)
    assert len(b.loaded) == 12 and "914" not in b.loaded                                 # curated 12 of 15, the image ad never asked for
    assert (st["stored"], st["gone"], st["failed"], st["no_video"]) == (9, 1, 1, 1) and st["selected"] == 12
    assert sum("backfill:" in l and "/12" in l for l in logs) >= 2
    assert conn.execute("SELECT * FROM competitor_ads ORDER BY library_id").fetchall() == before
    b2 = FakeBrowser([], dict(one))
    st2 = ca.backfill_video(conn, b2, today=TODAY, fetch=lambda u: MP4, encode=fake_encode, sleep=lambda s: None, log=lambda m: None)
    assert sorted(b2.loaded) == ["903", "904", "905"] and st2["stored"] == 0             # only the three unstored of the same selection are retried


def test_backfill_stops_at_the_cap_and_reports_it(conn):
    seed_rows(conn)
    one = {str(900 + i): [card(str(900 + i), "Acme Co", "Oct 1, 2026", "copy", f"https://video.xx.fbcdn.net/{i}.mp4")] for i in range(15)}
    big = lambda d: (b"E" * 1_000_000, 10.0, 360, 640)
    st = ca.backfill_video(conn, FakeBrowser([], one), max_mb=2.5, today=TODAY, fetch=lambda u: MP4, encode=big, sleep=lambda s: None, log=lambda m: None)
    total = conn.execute("SELECT coalesce(sum(size_bytes),0) FROM competitor_ad_video").fetchone()[0]
    assert total <= 2_500_000 and st["capped"] > 0 and st["stored"] >= 2
    # the longest-running ads were captured first, so they are the ones kept
    kept = {r[0] for r in conn.execute("SELECT library_id FROM competitor_ad_video").fetchall()}
    assert "900" in kept and "901" in kept
