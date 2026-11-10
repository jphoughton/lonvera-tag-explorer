"""etl.drive_io with Drive faked: no network, no credentials. Link parsing table, a single file, folder listing (sorted, direct children only, capped), non-media and
oversize files skipped with reasons, streamed download, and the plain-English permission message that names the service account."""
import pytest

from etl import drive_io as dio

SA = "sheet-writer@example-project.iam.gserviceaccount.com"
ID = "1AbCdEfGhIjKlMnOpQrStUv"
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    import json
    monkeypatch.setenv("GOOGLE_SA_JSON", json.dumps({"client_email": SA, "private_key": "x"}))


class Resp:
    def __init__(self, j=None, code=200, chunks=()):
        self._j, self.status_code, self._chunks = j, code, chunks

    def json(self):
        return self._j

    def iter_content(self, chunk_size=0):
        return iter(self._chunks)


class Fake:
    def __init__(self, resp):
        self.resp, self.calls = list(resp), []

    def get(self, url, params=None, stream=False, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.resp.pop(0)


def f(name, mime="image/png", size=1000, i=None):
    return {"id": i or "id_" + name, "name": name, "mimeType": mime, "size": str(size)}


@pytest.mark.parametrize("url,want", [
    (f"https://drive.google.com/file/d/{ID}/view?usp=sharing", ("file", ID)),
    (f"https://drive.google.com/file/u/0/d/{ID}/view", ("file", ID)),
    (f"https://drive.google.com/open?id={ID}", ("file", ID)),
    (f"https://drive.google.com/uc?id={ID}&export=download", ("file", ID)),
    (f"https://drive.google.com/drive/folders/{ID}?usp=sharing", ("folder", ID)),
    (f"https://drive.google.com/drive/u/0/folders/{ID}", ("folder", ID)),
    (f"  https://drive.google.com/drive/folders/{ID}  ", ("folder", ID)),
])
def test_parse_link_forms(url, want):
    assert dio.parse_link(url) == want


@pytest.mark.parametrize("url", ["", "hello", "https://example.com/file/d/" + ID, "https://drive.google.com/drive/my-drive", "https://drive.google.com/open?id=short",
                                 "https://drive.google.com/file/d/short/view"])
def test_parse_link_rejects_other_links(url):
    with pytest.raises(dio.DriveError) as e:
        dio.parse_link(url)
    assert "not a Google Drive link" in str(e.value)


def test_file_link_gives_one_file_with_supports_all_drives():
    s = Fake([Resp(f("a.mp4", "video/mp4", 5 * MB, i=ID))])
    out = dio.list_media(f"https://drive.google.com/file/d/{ID}/view", session=s)
    assert out == {"files": [{"id": ID, "name": "a.mp4", "mime": "video/mp4", "size": 5 * MB, "kind": "video"}], "skipped": []}
    assert s.calls[0][1]["supportsAllDrives"] == "true"


def test_folder_sorted_by_name_and_capped():
    kids = [f(f"{n}.png") for n in ("b", "d", "a", "c", "e")] + [f("notes.pdf", "application/pdf"), {**f("sub", dio.FOLDER_MIME), "size": None}]
    s = Fake([Resp({"id": ID, "name": "F", "mimeType": dio.FOLDER_MIME}), Resp({"files": kids})])
    out = dio.list_media(f"https://drive.google.com/drive/folders/{ID}", max_files=3, session=s)
    assert [x["name"] for x in out["files"]] == ["a.png", "b.png", "c.png"]
    reasons = {x["name"]: x["reason"] for x in out["skipped"]}
    assert reasons["d.png"] == "over the limit of 3 files per launch" and reasons["e.png"].startswith("over the limit")
    assert reasons["notes.pdf"] == "not an image or video" and reasons["sub"] == "not an image or video"
    for _, params in s.calls:
        assert params["supportsAllDrives"] == "true"
    assert "includeItemsFromAllDrives" in s.calls[1][1] and "'%s' in parents" % ID in s.calls[1][1]["q"]


def test_folder_pages_are_followed():
    s = Fake([Resp({"mimeType": dio.FOLDER_MIME, "name": "F", "id": ID}), Resp({"files": [f("b.png")], "nextPageToken": "T"}), Resp({"files": [f("a.png")]})])
    out = dio.list_media(f"https://drive.google.com/drive/folders/{ID}", session=s)
    assert [x["name"] for x in out["files"]] == ["a.png", "b.png"] and s.calls[2][1]["pageToken"] == "T"


def test_size_limits():
    s = Fake([Resp({"mimeType": dio.FOLDER_MIME, "name": "F", "id": ID}),
              Resp({"files": [f("ok.png", size=30 * MB), f("big.png", size=30 * MB + 1), f("ok.mp4", "video/mp4", 500 * MB), f("huge.mp4", "video/mp4", 500 * MB + 1)]})])
    out = dio.list_media(f"https://drive.google.com/drive/folders/{ID}", session=s)
    assert [x["name"] for x in out["files"]] == ["ok.mp4", "ok.png"]
    r = {x["name"]: x["reason"] for x in out["skipped"]}
    assert "over 30 MB" in r["big.png"] and "over 500 MB" in r["huge.mp4"]


def test_single_oversize_file_is_skipped_not_listed():
    s = Fake([Resp(f("big.png", size=40 * MB, i=ID))])
    out = dio.list_media(f"https://drive.google.com/file/d/{ID}/view", session=s)
    assert out["files"] == [] and out["skipped"][0]["name"] == "big.png"


def test_open_link_to_a_folder_is_treated_as_folder():
    s = Fake([Resp({"id": ID, "name": "F", "mimeType": dio.FOLDER_MIME}), Resp({"files": [f("a.png")]})])
    assert len(dio.list_media(f"https://drive.google.com/open?id={ID}", session=s)["files"]) == 1


@pytest.mark.parametrize("code", [403, 404])
def test_permission_error_names_service_account(code):
    s = Fake([Resp({"error": {}}, code)])
    with pytest.raises(dio.DriveError) as e:
        dio.list_media(f"https://drive.google.com/drive/folders/{ID}", session=s)
    assert SA in str(e.value) and "Share it with" in str(e.value)
    s = Fake([Resp({"error": {}}, code)])
    with pytest.raises(dio.DriveError) as e:
        dio.download(ID, "/nonexistent/x", session=s)
    assert SA in str(e.value)


def test_other_http_error_is_plain():
    with pytest.raises(dio.DriveError) as e:
        dio.list_media(f"https://drive.google.com/file/d/{ID}/view", session=Fake([Resp({}, 500)]))
    assert "HTTP 500" in str(e.value) and SA not in str(e.value)


def test_download_streams_to_disk(tmp_path):
    dest = tmp_path / "out.bin"
    s = Fake([Resp(code=200, chunks=[b"abc", b"", b"def"])])
    assert dio.download(ID, dest, session=s) == 6 and dest.read_bytes() == b"abcdef"
    assert s.calls[0][1]["alt"] == "media" and s.calls[0][1]["supportsAllDrives"] == "true"


def test_scope_is_read_only():
    assert dio.SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]
    assert dio.service_account_email() == SA
