"""GET /api/competitors/media/<library_id> (app/competitors_media.py): stored bytes + headers, 404 absent, 404 non-digit, 401 anonymous, read-only."""
import pytest

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes\xff\xd9"


@pytest.fixture()
def media(pg):
    pg.execute("DELETE FROM competitor_ad_media")
    pg.execute("INSERT INTO competitor_ad_media (library_id, kind, content_type, width, height, bytes) VALUES ('123456789012345','image','image/jpeg',480,480,%s)", (JPEG,))
    pg.commit()
    yield
    pg.execute("DELETE FROM competitor_ad_media")
    pg.commit()


def test_serves_the_stored_bytes_with_cache_header(client, media):
    r = client.get("/api/competitors/media/123456789012345")
    assert r.status_code == 200 and r.data == JPEG
    assert r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "private, max-age=86400"


def test_absent_and_non_digit_ids_are_404_json(client, media):
    assert client.get("/api/competitors/media/999").status_code == 404
    assert client.get("/api/competitors/media/999").get_json() == {"error": "not found"}
    for bad in ("12ab", "abc", "1%202", "..", "123456789012345.jpg"):
        r = client.get(f"/api/competitors/media/{bad}")
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, bad


def test_anonymous_is_401(anon, media):
    assert anon.get("/api/competitors/media/123456789012345").status_code == 401


def test_read_only_and_brand_route_unaffected(client, media):
    for m in ("post", "put", "patch", "delete"):
        assert getattr(client, m)("/api/competitors/media/123456789012345", json={}).status_code == 405
    assert client.get("/api/competitors/Nobody").status_code == 404           # the brand route still answers for other paths
