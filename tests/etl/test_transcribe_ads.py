"""etl.transcribe_ads: Ready to launch cards' Drive videos are downloaded and transcribed once into creative_ad_transcripts; images, non-Drive links and finished ads are skipped; one bad file does not stop the
run; a run is capped; nothing is overwritten; a dry run downloads nothing; a no-op before migration 249."""
import datetime as dt

import pytest
from psycopg.rows import dict_row, tuple_row

from etl import drive_io, transcribe_ads as ta

MONDAY = dt.date(2026, 11, 16)
STORY = "Brief\nVariations\nOriginal: https://drive.google.com/file/d/ORIG123456789/view\nHook 2: https://drive.google.com/file/d/HOOK2123456789/view\nHook 3: https://f.io/abcdef\n"


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
    conn.execute("TRUNCATE creative_ad_transcripts, ad_transcript")
    conn.commit()
    yield
    conn.rollback()
    conn.row_factory = tuple_row


def card(conn, status="ready", story=STORY, tids=("Lonvera-Boneflex-Video-1.1", "Lonvera-Boneflex-Video-1.1-hook2", "Lonvera-Boneflex-Video-1.1-hook3"), dropped=False, curl=None, key="k"):
    return conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, tracker_concept_ids, status, story, creative_url, dropped_at, position)
                           VALUES (%s,'t','boneflex','Madre',%s,%s,%s,%s,%s,%s,1) RETURNING id""", (key, MONDAY, list(tids), status, story, curl, dt.datetime.now() if dropped else None)).fetchone()[0]


def go(conn, **kw):
    conn.commit()
    conn.row_factory = dict_row
    try:
        return ta.run(conn, **kw)
    finally:
        conn.row_factory = tuple_row


def fake_fetch(kinds=None):
    kinds = kinds or {}
    def f(fid, dest):
        k = kinds.get(fid, "video")
        if k == "boom":
            raise drive_io.DriveError("Drive says that file is missing or private.")
        return k
    return f


def fake_tr(path):
    return {"duration_s": 31.5, "language": "en", "text": "words from the audio"}


def rows(conn):
    return conn.execute("SELECT concept_id, assignment_id, label, file_id, transcript, model, created_by FROM creative_ad_transcripts ORDER BY concept_id").fetchall()


def test_variations_tie_each_link_to_the_tracker_concept_id():
    v = ta.variations({"tracker_concept_ids": ["B-1", "B-1-hook2", "B-1-hook3"], "story": STORY, "creative_url": None})
    assert [(x["concept_id"], x["label"]) for x in v] == [("B-1", "Original"), ("B-1-hook2", "Hook 2"), ("B-1-hook3", "Hook 3")]


def test_a_card_without_variation_lines_uses_its_creative_link_as_the_original():
    v = ta.variations({"tracker_concept_ids": ["B-1"], "story": "no variations here", "creative_url": "https://drive.google.com/file/d/AAAAAAAAAAAA/view"})
    assert v == [{"concept_id": "B-1", "label": "Original", "url": "https://drive.google.com/file/d/AAAAAAAAAAAA/view"}]


def test_stores_drive_videos_once_and_skips_other_hosts(conn):
    aid = card(conn)
    st = go(conn, fetch_fn=fake_fetch(), transcribe_fn=fake_tr)
    assert st["stored"] == 2 and st["not_drive"] == 1 and st["errors"] == []
    got = rows(conn)
    assert [r[0] for r in got] == ["Lonvera-Boneflex-Video-1.1", "Lonvera-Boneflex-Video-1.1-hook2"]
    assert got[0][1] == aid and got[0][2] == "Original" and got[0][3] == "ORIG123456789" and got[0][4] == "words from the audio" and got[0][5] == ta.MODEL and got[0][6] == ta.CREATED_BY
    again = go(conn, fetch_fn=fake_fetch(), transcribe_fn=lambda p: pytest.fail("must not transcribe twice"))
    assert again["stored"] == 0 and again["already"] == 2 and len(rows(conn)) == 2


def test_only_ready_cards_that_are_not_dropped(conn):
    card(conn, status="in_review", key="a")
    card(conn, dropped=True, key="b")
    st = go(conn, fetch_fn=fake_fetch(), transcribe_fn=fake_tr)
    assert st["cards"] == 0 and st["stored"] == 0


def test_images_are_skipped_and_one_error_does_not_stop_the_rest(conn):
    card(conn)
    st = go(conn, fetch_fn=fake_fetch({"ORIG123456789": "image", "HOOK2123456789": "boom"}), transcribe_fn=fake_tr)
    assert st["images_skipped"] == 1 and st["stored"] == 0 and len(st["errors"]) == 1 and "private" in st["errors"][0]["why"]
    st2 = go(conn, fetch_fn=fake_fetch({"ORIG123456789": "video", "HOOK2123456789": "boom"}), transcribe_fn=fake_tr)
    assert st2["stored"] == 1                                          # the failed one is retried every run, the good one lands


def test_silent_video_is_stored_as_no_speech(conn):
    card(conn)
    st = go(conn, fetch_fn=fake_fetch(), transcribe_fn=lambda p: {"duration_s": 9.0, "language": None, "text": ta.NO_SPEECH})
    assert st["no_speech"] == 2 and rows(conn)[0][4] == "[no speech]"


def test_the_run_is_capped_and_the_rest_waits(conn):
    card(conn)
    st = go(conn, limit=1, fetch_fn=fake_fetch(), transcribe_fn=fake_tr)
    assert st["stored"] == 1 and st["deferred"] == 1 and len(rows(conn)) == 1


def test_dry_run_downloads_and_writes_nothing(conn):
    card(conn)
    st = go(conn, dry_run=True, fetch_fn=lambda *a: pytest.fail("no download in a dry run"), transcribe_fn=fake_tr)
    assert st["to_do"] == 2 and rows(conn) == []


def test_an_existing_transcript_is_never_overwritten(conn):
    card(conn)
    conn.execute("INSERT INTO creative_ad_transcripts (concept_id, transcript) VALUES ('Lonvera-Boneflex-Video-1.1','old words')")
    go(conn, fetch_fn=fake_fetch(), transcribe_fn=fake_tr)
    assert dict((r[0], r[4]) for r in rows(conn))["Lonvera-Boneflex-Video-1.1"] == "old words"


def test_no_op_before_the_table_exists(conn):
    conn.execute("ALTER TABLE creative_ad_transcripts RENAME TO creative_ad_transcripts_gone")
    try:
        st = go(conn, fetch_fn=fake_fetch(), transcribe_fn=fake_tr)
        assert st["table_missing"] is True
    finally:
        conn.rollback()
        conn.execute("ALTER TABLE IF EXISTS creative_ad_transcripts_gone RENAME TO creative_ad_transcripts")
        conn.commit()


# ---- Meta route: ad families with spend since 2026-11-16 and no transcript ----------------------------------------------------------------

def meta_ad(conn, fam, spend, day="2026-11-18", ad_type="VID", is_video=True, cid=None, video_id="V1", ad_id=None, track=True):
    ad_id = ad_id or f"ad-{fam}"
    conn.execute("INSERT INTO meta_ads (ad_id, meta_family_key, ad_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ad_id, fam, fam))
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES (%s,%s,%s,%s,%s,'meta_api')", (ad_id, day, fam, fam, spend))
    if track:
        conn.execute("INSERT INTO tracker_rows (row_key, concept_id, ad_family, family_key, sheet_row, ad_type) VALUES (%s,%s,%s,%s,1,%s)", (fam, cid or fam.title(), fam, fam, ad_type))
    if is_video is not None:
        conn.execute("INSERT INTO family_content (family_key, is_video, video_id, ad_id) VALUES (%s,%s,%s,%s)", (fam, is_video, video_id, ad_id))
    conn.commit()


def fake_meta(it, dest):
    return "video"


def test_meta_families_are_taken_by_spend_and_stored_under_the_tracker_concept_id(conn):
    meta_ad(conn, "austin-small", 50, cid="Austin-Small")
    meta_ad(conn, "austin-big", 900, cid="Austin-Big")
    meta_ad(conn, "austin-mid", 300, cid="Austin-Mid")
    conn.commit()
    conn.row_factory = dict_row
    todo, st = ta.meta_candidates(conn, set())
    conn.row_factory = tuple_row
    assert [t["concept_id"] for t in todo] == ["Austin-Big", "Austin-Mid", "Austin-Small"] and st["meta_candidates"] == 3
    out = go(conn, limit=2, meta_fetch_fn=fake_meta, transcribe_fn=fake_tr)
    assert out["meta_stored"] == 2 and out["stored"] == 2 and out["deferred"] == 1
    assert [r[0] for r in rows(conn)] == ["Austin-Big", "Austin-Mid"]
    assert conn.execute("SELECT source FROM ad_transcript WHERE family_key = 'austin-big'").fetchone()[0] == "whisper"


def test_meta_skips_old_spend_statics_and_already_transcribed(conn):
    meta_ad(conn, "old-ad", 500, day="2026-11-10")
    meta_ad(conn, "static-ad", 400, ad_type="IMG", is_video=False)
    meta_ad(conn, "done-ad", 300, cid="Done-Ad")
    meta_ad(conn, "live-ad", 200, cid="Live-Ad")
    conn.execute("INSERT INTO creative_ad_transcripts (concept_id, transcript) VALUES ('done-AD','already there')")
    conn.commit()
    st = go(conn, dry_run=True, meta_fetch_fn=lambda *a: pytest.fail("no download in a dry run"))
    assert st["meta_candidates"] == 1 and st["meta_static"] == 1 and st["meta_already"] == 1 and st["to_do"] == 1


def test_meta_fetch_failure_is_counted_and_does_not_stop_the_rest(conn):
    meta_ad(conn, "bad-ad", 900, cid="Bad-Ad")
    meta_ad(conn, "good-ad", 100, cid="Good-Ad")
    def f(it, dest):
        if it["concept_id"] == "Bad-Ad":
            raise RuntimeError("download failed")
        return "video"
    st = go(conn, meta_fetch_fn=f, transcribe_fn=fake_tr)
    assert st["meta_fetch_failed"] == 1 and st["meta_stored"] == 1 and st["errors"] == [] and [r[0] for r in rows(conn)] == ["Good-Ad"]
    again = go(conn, meta_fetch_fn=f, transcribe_fn=fake_tr)
    assert again["meta_candidates"] == 1 and again["meta_fetch_failed"] == 1 and again["meta_stored"] == 0       # retried next run


def test_meta_family_without_tracker_row_uses_its_family_key(conn):
    meta_ad(conn, "untracked-ad", 120, track=False)
    st = go(conn, meta_fetch_fn=fake_meta, transcribe_fn=fake_tr)
    assert st["meta_stored"] == 1 and [r[0] for r in rows(conn)] == ["untracked-ad"]
