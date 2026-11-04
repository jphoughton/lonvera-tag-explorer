"""Beef Liver cycle assets/content: 2x2 contact sheets -> 4 frames, thumb fallbacks, text-only families, the blank-family 'id:' twin, product scoping
(other products' assets untouched), idempotency, content (transcript the tagger read, post link, is_video)."""
import io
import json

import pytest
from PIL import Image

from etl import load_assets, sheets_io, tracker_sync
from conftest import trow, write_snapshot

COLORS = [(250, 0, 0), (0, 250, 0), (0, 0, 250), (250, 250, 0)]


def jpeg(path, size, color=(10, 20, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=95)


def sheet(path, w=720, h=1280):
    im = Image.new("RGB", (w, h))
    for i, col in enumerate(COLORS):
        im.paste(Image.new("RGB", (w // 2, h // 2), col), ((i % 2) * (w // 2), (i // 2) * (h // 2)))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=95)


@pytest.fixture
def cycle(tmp_path):
    cd = tmp_path / "c2026-11-04-bl-linemap"
    jpeg(cd / "media/0001.jpg", (1000, 1000), (200, 10, 10))            # static thumb (wider than 640: must shrink)
    jpeg(cd / "media/0003.jpg", (405, 720), (10, 200, 10))              # video cover thumb
    jpeg(cd / "media/0005.jpg", (720, 720), (10, 10, 200))              # drive static thumb (drive_index only)
    sheet(cd / "media/0003_v.jpg")
    sheet(cd / "media/0004_v.jpg", 720, 1292)                           # video with no thumb anywhere: fallback thumb from frame 1
    mi = {"stat": {"n": 1, "ad_id": "11", "video": False, "video_id": "", "thumb": "media/0001.jpg", "story": "https://www.facebook.com/1/posts/2/"},
          "famv": {"n": 3, "ad_id": "33", "video": True, "video_id": "999", "thumb": "media/0003.jpg", "story": ""},
          "nothumb": {"n": 4, "ad_id": "", "video": False, "video_id": "", "thumb": "", "story": ""},
          "driveonly": {"n": 5, "ad_id": "", "video": False, "video_id": "", "thumb": "", "story": ""},
          "textonly": {"n": 6, "ad_id": "", "video": False, "video_id": "", "thumb": "", "story": ""},
          "vbfam": {"n": 7, "ad_id": "", "video": False, "video_id": "", "thumb": "media/0001.jpg", "story": ""}}
    (cd / "media_index.json").write_text(json.dumps(mi))
    (cd / "drive_index.json").write_text(json.dumps({"driveonly": {"drive_id": "d", "mime": "image/png", "name": "x.png", "size": 1, "thumb": "media/0005.jpg"}}))
    (cd / "video_index.json").write_text(json.dumps({"famv": {"src": "drive", "frames": "media/0003_v.jpg", "dur": 30, "transcript": "she says iron", "transcript_source": "whisper"},
                                                      "nothumb": {"src": "yt", "frames": "media/0004_v.jpg", "dur": 10, "transcript": "", "transcript_source": ""}}))
    (cd / "meta_copy.json").write_text(json.dumps({"famv": {"body": "Body text", "title": "Title", "desc": "60 Day", "video": "True", "story": "161_122", "ad_id": "33"},
                                                   "stat": {"body": "Static body", "video": "False"}}))
    return cd


def seed(conn, tmp_path):
    trs = [trow("famv", "FamV", "BeefLiver", Type="VID", Transcript="tracker transcript"), trow("famv", "", "BeefLiver", Type="VID"),      # twin: family_key 'id:famv'
           trow("stat", "Stat", "BeefLiver", Type="IMG"), trow("nothumb", "NoThumb", "BeefLiver", Type="VID"),
           trow("driveonly", "DriveOnly", "BeefLiver", Type="IMG"), trow("textonly", "TextOnly", "BeefLiver", Type="IMG"),
           trow("vbfam", "VbFam", "VitaBull", Type="IMG")]
    snap = tmp_path / "snap"
    snap.mkdir()
    write_snapshot(snap, trs, [("2026-05-06", "x", 1, 0, 0, 0)])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))


def px(data, xy):
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel(xy)


def test_contact_sheet_split_and_scoping(conn, tmp_path, cycle):
    seed(conn, tmp_path)
    conn.execute("INSERT INTO creative_assets (family_key, kind, idx, bytes, source) VALUES ('vbfam','thumb',0,'\\x01'::bytea,'cycle-2026-11-03')")
    conn.execute("INSERT INTO family_content (family_key, meta_body, source) VALUES ('vbfam','vb body','cycle-2026-11-03')")
    conn.commit()
    st = load_assets.load(conn, cycle, product="beef-liver")
    assert st["source"] == "cycle-2026-11-04" and st["unmatched_families"] == ["vbfam"] and st["dual_family_keys"] == 1
    # frames: 4 tiles cut from the 2x2 sheet in reading order, for the named family and its blank-family twin
    for fk in ("famv", "id:famv"):
        fr = conn.execute("SELECT idx, bytes, width, height FROM creative_assets WHERE family_key=%s AND kind='frame' ORDER BY idx", (fk,)).fetchall()
        assert [f[0] for f in fr] == [0, 1, 2, 3] and all((f[2], f[3]) == (360, 640) for f in fr)
        for (idx, data, _, _), col in zip(fr, COLORS):
            got = px(bytes(data), (180, 320))
            assert all(abs(a - b) < 12 for a, b in zip(got, col)), (fk, idx, got, col)
    # thumbs: shrunk to 640, drive-only thumb used, fallback = frame 1 of the sheet, text-only family has nothing
    t = {r[0]: r for r in conn.execute("SELECT family_key, width, source FROM creative_assets WHERE kind='thumb'").fetchall()}
    assert t["stat"][1] == 640 and t["stat"][2] == "cycle-2026-11-04" and t["driveonly"][1] == 640
    assert t["nothumb"][2] == "cycle-2026-11-04-frame" and st["fallback_thumbs"] == 1
    assert "textonly" not in t and "id:textonly" not in t and st["no_image_families"] == 1
    fb = conn.execute("SELECT bytes FROM creative_assets WHERE family_key='nothumb' AND kind='thumb'").fetchone()[0]
    assert all(abs(a - b) < 12 for a, b in zip(px(bytes(fb), (100, 100)), COLORS[1]))                  # frame 1 = top-right tile (green)
    # another product's family is never touched
    assert conn.execute("SELECT count(*), min(source), bool_and(bytes = '\\x01'::bytea) FROM creative_assets WHERE family_key='vbfam'").fetchone() == (1, "cycle-2026-11-03", True)
    assert conn.execute("SELECT meta_body, source FROM family_content WHERE family_key='vbfam'").fetchone() == ("vb body", "cycle-2026-11-03")


def test_content_and_idempotency(conn, tmp_path, cycle):
    seed(conn, tmp_path)
    st = load_assets.load(conn, cycle, product="beef-liver")
    c = {r[0]: r for r in conn.execute("SELECT family_key, meta_body, meta_title, transcript, story_url, is_video, ad_id, video_id, source FROM family_content").fetchall()}
    assert c["famv"][1:3] == ("Body text", "Title") and c["famv"][3] == "she says iron" and c["famv"][8] == "cycle-2026-11-04:whisper"   # the transcript the tagger read, not the tracker's
    assert c["famv"][4] == "https://www.facebook.com/161/posts/122/" and c["famv"][5] is True and c["famv"][6] == "33" and c["famv"][7] == "999"          # post link from meta_copy 'page_post'
    assert c["id:famv"][3] == "she says iron" and c["id:famv"][5] is True
    assert c["stat"][4] == "https://www.facebook.com/1/posts/2/" and c["stat"][5] is False                  # 'False' string is not truthy
    assert c["nothumb"][5] is True and c["textonly"][5] is False                                            # video from the tracker type / video index
    assert "vbfam" not in c
    before = (conn.execute("SELECT family_key, kind, idx, md5(bytes) FROM creative_assets ORDER BY 1,2,3").fetchall(),
              conn.execute("SELECT * FROM family_content ORDER BY family_key").fetchall())
    st2 = load_assets.load(conn, cycle, product="beef-liver")
    after = (conn.execute("SELECT family_key, kind, idx, md5(bytes) FROM creative_assets ORDER BY 1,2,3").fetchall(),
             conn.execute("SELECT * FROM family_content ORDER BY family_key").fetchall())
    assert before == after and st2["thumbs"] == st["thumbs"] and st2["frames"] == st["frames"]


def test_existing_content_is_kept_when_the_cycle_has_none(conn, tmp_path, cycle):
    seed(conn, tmp_path)
    conn.execute("INSERT INTO family_content (family_key, meta_body, story_url, ad_id, source) VALUES ('textonly','old body','https://old/post','7','assets_pull')")
    conn.commit()
    load_assets.load(conn, cycle, product="beef-liver")
    r = conn.execute("SELECT meta_body, story_url, ad_id, is_video, source FROM family_content WHERE family_key='textonly'").fetchone()
    assert r == ("old body", "https://old/post", "7", False, "cycle-2026-11-04")


def test_helpers():
    assert load_assets.source_for(load_assets.Path("/x/c2026-11-04-bl-linemap")) == "cycle-2026-11-04"
    assert load_assets.source_for(load_assets.Path("/x/whatever")) == load_assets.SOURCE
    assert load_assets.family_keys_for("a", {"a", "id:a", "b"}) == ["a", "id:a"]
    assert load_assets.family_keys_for("a", {"id:a"}) == ["id:a"] and load_assets.family_keys_for("z", {"a"}) == []
    assert [load_assets._truthy(v) for v in ("True", "False", "", True, False, None, "1")] == [True, False, False, True, False, False, True]
    assert load_assets.story_url({"story": "https://s"}, {"story": "1_2"}) == "https://s" and load_assets.story_url({}, {"story": "1_2"}) == "https://www.facebook.com/1/posts/2/"
    assert load_assets.story_url({}, {}) is None


def test_family_keys_shared_with_another_product_keep_their_existing_assets(conn, tmp_path, cycle):
    """Assets and content are keyed by family_key alone. A key that also belongs to another product keeps what is already there (the other cycle's image);
    a shared key with nothing yet is filled in."""
    mi = json.loads((cycle / "media_index.json").read_text())
    mi["shared"] = {"n": 8, "ad_id": "8", "video": False, "video_id": "", "thumb": "media/0001.jpg", "story": "https://s/old"}
    mi["sharednew"] = {"n": 9, "ad_id": "9", "video": False, "video_id": "", "thumb": "media/0001.jpg", "story": ""}
    (cycle / "media_index.json").write_text(json.dumps(mi))
    trs = [trow("shared", "Shared", "BeefLiver", Type="IMG"), trow("shared", "Shared", "VitaBull", Type="IMG"),
           trow("sharednew", "SharedNew", "BeefLiver", Type="IMG"), trow("sharednew", "SharedNew", "VitaBull", Type="IMG")]
    snap = tmp_path / "snap"
    snap.mkdir()
    write_snapshot(snap, trs, [("2026-05-06", "x", 1, 0, 0, 0)])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))
    conn.execute("INSERT INTO creative_assets (family_key, kind, idx, bytes, source) VALUES ('shared','thumb',0,'\\x02'::bytea,'cycle-2026-11-03')")
    conn.execute("INSERT INTO family_content (family_key, meta_body, source) VALUES ('shared','other product body','cycle-2026-11-03')")
    conn.commit()
    st = load_assets.load(conn, cycle, product="beef-liver")
    assert st["shared_with_other_products"] == 2
    assert conn.execute("SELECT count(*), min(source), bool_and(bytes = '\\x02'::bytea) FROM creative_assets WHERE family_key='shared'").fetchone() == (1, "cycle-2026-11-03", True)
    assert conn.execute("SELECT meta_body, source, story_url FROM family_content WHERE family_key='shared'").fetchone() == ("other product body", "cycle-2026-11-03", None)
    assert conn.execute("SELECT source FROM creative_assets WHERE family_key='sharednew'").fetchone()[0] == "cycle-2026-11-04"
    assert conn.execute("SELECT count(*) FROM family_content WHERE family_key='sharednew'").fetchone()[0] == 1
    before = conn.execute("SELECT family_key, kind, idx, md5(bytes) FROM creative_assets ORDER BY 1,2,3").fetchall()
    load_assets.load(conn, cycle, product="beef-liver")
    assert conn.execute("SELECT family_key, kind, idx, md5(bytes) FROM creative_assets ORDER BY 1,2,3").fetchall() == before
