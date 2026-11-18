"""etl.asset_sync: every media link on a ticket ends up once in Lonvera - Assets/<Product>/<Images|Videos>; md5 duplicates are skipped; reruns copy nothing; failures retry; the Drive client can only add."""
import datetime as dt
import json

import pytest
from psycopg.rows import dict_row, tuple_row

from etl import asset_sync as A

MONDAY = dt.date(2026, 11, 9)


class FakeDrive:
    """In-memory Drive: tree of folders/files; records every call."""

    def __init__(self):
        self.files = {}                                   # id -> dict(id,name,mimeType,md5Checksum,size,parent)
        self.calls = []
        self.fail_copy = set()
        n = 0

    def add(self, fid, name, mime, parent=None, md5=None, size=100):
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime, "md5Checksum": md5, "size": str(size), "parent": parent}
        return fid

    def folder(self, fid, name, parent=None):
        return self.add(fid, name, A.FOLDER, parent)

    def get(self, fid):
        if fid not in self.files:
            raise A.LinkError("Drive says the file is missing or private", permanent=True)
        return self.files[fid]

    def children(self, fid, fields=""):
        return [f for f in self.files.values() if f["parent"] == fid]

    def find_folder(self, parent, name):
        return next((f["id"] for f in self.children(parent) if f["mimeType"] == A.FOLDER and f["name"].lower() == name.lower()), None)

    def create_folder(self, parent, name):
        self.calls.append(("create_folder", name))
        return self.folder(f"new{len(self.files)}", name, parent)

    def copy(self, fid, parent, name, description):
        self.calls.append(("copy", fid))
        if fid in self.fail_copy:
            raise A.LinkError("Drive HTTP 500")
        src = self.files[fid]
        return self.add(f"c{len(self.files)}", name, src["mimeType"], parent, src["md5Checksum"], src["size"])

    def upload(self, path, name, parent, mime, description):
        self.calls.append(("upload", name))
        return self.add(f"u{len(self.files)}", name, mime, parent, "x", 1)

    def download(self, fid, dest):
        open(dest, "wb").write(b"x")


@pytest.fixture
def env(conn):
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
    conn.execute("TRUNCATE asset_sync_known_md5")
    conn.execute("DELETE FROM app_meta WHERE key = 'asset_sync_index_at'")
    conn.commit()
    d = FakeDrive()
    d.folder(A.ROOT_ID, "Lonvera - Assets")
    for i, p in enumerate(("Vita Bull", "Beef Liver", "Boneflex", "Other")):
        d.folder(f"p{i}", p, A.ROOT_ID)
        d.folder(f"p{i}i", "Images", f"p{i}")
        d.folder(f"p{i}v", "Videos", f"p{i}")
    d.add("existing", "old.mp4", "video/mp4", "p0v", md5="DUP")
    d.add("srcfolder0", "Shoot", A.FOLDER, "elsewhere")
    d.add("vid0000001", "hook1.mp4", "video/mp4", "srcfolder0", md5="m1")
    d.add("v2", "dupe.mp4", "video/mp4", "srcfolder0", md5="DUP")
    d.add("i1", "still.png", "image/png", "srcfolder0", md5="m3")
    d.add("sub", "More", A.FOLDER, "srcfolder0")
    d.add("i2", "deep.jpg", "image/jpeg", "sub", md5="m4")
    d.add("doc", "notes", "application/vnd.google-apps.document", "srcfolder0")
    d.add("lone000001", "single.mp4", "video/mp4", None, md5="m5")
    yield conn, d
    conn.rollback()
    conn.row_factory = tuple_row


def ticket(conn, product, creative=None, variations=()):
    return conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, creative_url, variations, position)
        VALUES (%s,'t',%s,'Madre',%s,%s,%s,1) RETURNING id""", (f"k{product}{creative}{len(variations)}", product, MONDAY, creative, json.dumps(list(variations)))).fetchone()[0]


def go(conn, d, **kw):
    conn.row_factory = dict_row
    try:
        return A.run(conn, d, **kw)
    finally:
        conn.row_factory = tuple_row


def test_classify_link_forms():
    c = A.classify
    assert c("https://drive.google.com/file/d/ABCDEFGHIJKLM/view?usp=sharing") == "drive_file"
    assert c("https://drive.google.com/file/d/ABCDEFGHIJKLM/preview") == "drive_file"
    assert c("https://drive.google.com/open?id=ABCDEFGHIJKLM") == "drive_file"
    assert c("https://drive.google.com/uc?id=ABCDEFGHIJKLM&export=download") == "drive_file"
    assert c("https://drive.google.com/drive/folders/ABCDEFGHIJKLM") == "drive_folder"
    assert c("https://docs.google.com/presentation/d/ABC/edit") == "google_doc"
    assert c("https://www.figma.com/design/1d4E/Ad-Assets?node-id=1") == "figma"
    assert c("https://next.frame.io/share/a/view/b") == "frameio" and c("https://f.io/PqUBuzGI") == "frameio"
    assert c("https://www.loom.com/share/abc") == "loom" and c("https://vimeo.com/123") == "vimeo" and c("https://youtu.be/x") == "youtube"
    assert c("https://www.dropbox.com/scl/fi/x/a.mp4?dl=0") == "dropbox"
    assert c("https://www.facebook.com/ads/api/preview_iframe.php?d=1") == "facebook_preview"
    assert c("https://www.facebook.com/watch/?v=123") == "facebook_post"
    assert c("https://cdn.example.com/a/b.mp4") == "direct"


def test_extract_urls_from_iframe_and_text():
    t = '<iframe src="https://drive.google.com/file/d/ABCDEFGHIJKLM/preview?a=1&amp;b=2" width=1></iframe> and https://vimeo.com/1.'
    assert A.extract_urls(t) == ["https://drive.google.com/file/d/ABCDEFGHIJKLM/preview?a=1&b=2", "https://vimeo.com/1"]


def test_dropbox_forced_to_dl1():
    assert A.dropbox_direct("https://www.dropbox.com/s/abc/a.mp4?dl=0").endswith("dl=1")
    with pytest.raises(A.LinkError):
        A.dropbox_direct("https://www.dropbox.com/scl/fo/abc/x?dl=0")


def test_folder_copy_routing_dedupe_and_idempotent(env):
    conn, d = env
    tid = ticket(conn, "beef-liver", "https://drive.google.com/drive/folders/srcfolder0")
    conn.commit()
    st = go(conn, d)
    assert (st["copied"], st["duplicate"]) == (3, 1)                                      # hook1, still, deep copied; dupe.mp4 skipped by md5
    dest = {f["name"]: f["parent"] for f in d.files.values() if f["id"].startswith("c")}
    assert dest == {"hook1.mp4": "p1v", "still.png": "p1i", "deep.jpg": "p1i"}
    rows = {r[0]: r[1] for r in conn.execute("SELECT source_name, status FROM asset_sync_files").fetchall()}
    assert rows["dupe.mp4"] == "duplicate" and rows["hook1.mp4"] == "synced"
    assert conn.execute("SELECT status FROM asset_sync_links").fetchone()[0] == "done"
    n = len(d.calls)
    st2 = go(conn, d)
    assert st2["copied"] == 0 and len(d.calls) == n                                       # second run copies nothing (folder links are only re-listed after 6 hours)


def test_file_links_unknown_product_other_and_vendor_forms(env):
    conn, d = env
    ticket(conn, "mystery", "https://drive.google.com/file/d/lone000001/view", [{"label": "Hook 2", "url": "https://drive.google.com/open?id=v1v1v1v1v1v1"}])
    d.add("v1v1v1v1v1v1", "x.mp4", "video/mp4", "e", md5="m9")
    d.add("lone000001", "single.mp4", "video/mp4", None, md5="m5")
    conn.commit()
    st = go(conn, d)
    assert st["copied"] == 2
    assert {f["parent"] for f in d.files.values() if f["id"].startswith("c")} == {"p3v"}      # Other/Videos


def test_failure_is_retried_next_run(env):
    conn, d = env
    ticket(conn, "boneflex", "https://drive.google.com/file/d/lone000001/view")
    conn.commit()
    d.fail_copy.add("lone000001")
    d.download = lambda fid, dest: (_ for _ in ()).throw(A.LinkError("Drive HTTP 500"))
    st = go(conn, d)
    assert st["failed"] == 1 and st["copied"] == 0
    assert conn.execute("SELECT status FROM asset_sync_links").fetchone()[0] == "failed"
    d.fail_copy.clear()
    st2 = go(conn, d)
    assert st2["copied"] == 1
    assert conn.execute("SELECT status, attempts FROM asset_sync_links").fetchone() == ("done", 0)


def test_unresolvable_and_skipped_types_are_recorded_with_reasons(env):
    conn, d = env
    ticket(conn, "vita-bull", "https://www.figma.com/design/1d4E/Ad-Assets?node-id=1", [{"label": "a", "url": "https://docs.google.com/presentation/d/ABC/edit"}, {"label": "b", "url": "https://drive.google.com/file/d/gone0000000000/view"}])
    conn.commit()
    st = go(conn, d)
    r = {x[0]: x[1:] for x in conn.execute("SELECT link_type, status, reason FROM asset_sync_links").fetchall()}
    assert r["figma"][0] == "skipped" and r["google_doc"][0] == "skipped" and r["drive_file"][0] == "unresolvable" and "private" in r["drive_file"][1]
    assert st["by_type"]["drive_file"]["unresolvable"] == 1 and st["copied"] == 0


def test_downloaded_links_dropbox_direct_ytdlp_and_cap(env, tmp_path):
    conn, d = env
    ticket(conn, "vita-bull", "https://www.dropbox.com/s/abc/a.mp4?dl=0", [
        {"label": "a", "url": "https://cdn.example.com/b.mov"}, {"label": "b", "url": "https://vimeo.com/123456"}, {"label": "c", "url": "https://cdn.example.com/huge.mp4"},
        {"label": "d", "url": "https://www.facebook.com/ads/api/preview_iframe.php?d=1"}])
    conn.commit()
    seen = []

    def dl(url, dest):
        seen.append(url)
        if "huge" in url:
            raise A.TooBig("900 MB is over the 150 MB cap")
        open(dest, "wb").write(b"abc")
        return {"name": url.rsplit("/", 1)[-1].split("?")[0], "mime": "video/mp4", "size": 3, "md5": "md-" + url[-6:]}

    def ytdlp(url, tmp):
        p = f"{tmp}/yt_9.mp4"
        open(p, "wb").write(b"vid")
        return p

    def fbp(url, dest):
        open(dest, "wb").write(b"i")
        return {"name": "preview.mp4", "mime": "video/mp4", "size": 1, "md5": "md-fb"}

    st = go(conn, d, dl=dl, ytdlp=ytdlp, fbp=fbp)
    assert any(u.endswith("a.mp4?dl=1") for u in seen)
    assert st["copied"] == 4 and st["skipped"] == 1
    assert st["by_type"]["vimeo"]["copied"] == 1 and st["by_type"]["dropbox"]["copied"] == 1
    assert sorted(c[0] for c in d.calls if c[0] in ("copy", "upload")) == ["upload"] * 4
    r = dict(conn.execute("SELECT url, status FROM asset_sync_links").fetchall())
    assert r["https://cdn.example.com/huge.mp4"] == "skipped"


def test_budget_stops_the_run_and_resumes(env):
    conn, d = env
    ticket(conn, "beef-liver", "https://drive.google.com/file/d/lone000001/view")
    ticket(conn, "beef-liver", "https://drive.google.com/file/d/vid0000001/view")
    conn.commit()
    assert go(conn, d, max_copies=1)["copied"] == 1
    assert go(conn, d, max_copies=1)["copied"] == 1
    assert go(conn, d)["copied"] == 0


def test_drive_client_can_only_add():
    banned = ("delete", "remove", "trash", "move", "rename", "update", "share", "permission")
    assert not [n for n in dir(A.Drive) if not n.startswith("_") and any(b in n for b in banned)]


def test_the_live_script_runs_the_sync_non_fatally():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[2] / "scripts/deploy/run_live.sh").read_text()
    assert "python -m etl.asset_sync ||" in t and 'exit "$live_rc"' in t


def test_the_sync_tags_a_ticket_image_or_video_from_its_files_and_never_overwrites_a_person(env):
    conn, d = env
    d.add("img0000001", "photo.png", "image/png", None, md5="m9")
    vid = ticket(conn, "beef-liver", "https://drive.google.com/drive/folders/srcfolder0")                    # folder holds videos and images: video
    img = ticket(conn, "boneflex", "https://drive.google.com/file/d/img0000001/view")                                # one still: image
    mine = ticket(conn, "boneflex", "https://drive.google.com/file/d/vid0000001/view?x=1")                   # a person chose image: stays
    conn.execute("UPDATE creative_priorities_assignments SET media_type = 'image', media_type_source = 'manual' WHERE id = %s", (mine,))
    conn.commit()
    st = go(conn, d)
    got = {r[0]: r[1:] for r in conn.execute("SELECT id, media_type, media_type_source, media_type_basis FROM creative_priorities_assignments").fetchall()}
    assert got[vid] == ("video", "auto", "asset_files") and got[img] == ("image", "auto", "asset_files")
    assert got[mine] == ("image", "manual", None) and st["media_tagged"] == 2
    go(conn, d)                                                                                              # second run changes nothing
    assert {r[0]: r[1] for r in conn.execute("SELECT id, media_type FROM creative_priorities_assignments").fetchall()} == {vid: "video", img: "image", mine: "image"}
