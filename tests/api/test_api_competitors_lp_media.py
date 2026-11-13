"""GET /api/competitors/lp-media/<lp_id> (app/competitors_media.py): stored screenshot + headers, week selection (default latest), 404 absent / non-hex / bad week, 401 anonymous, read-only."""
import pytest

JPEG_OLD = b"\xff\xd8\xff\xe0oldshot\xff\xd9"
JPEG_NEW = b"\xff\xd8\xff\xe0newshot\xff\xd9"
LP = "0123456789abcdef"


@pytest.fixture()
def shots(pg):
    pg.execute("DELETE FROM competitor_lp_media")
    for wk, b in (("2026-11-02", JPEG_OLD), ("2026-11-09", JPEG_NEW)):
        pg.execute("INSERT INTO competitor_lp_media (week_start, lp_id, content_type, width, height, bytes) VALUES (%s,%s,'image/jpeg',640,450,%s)", (wk, LP, b))
    pg.commit()
    yield
    pg.execute("DELETE FROM competitor_lp_media")
    pg.commit()


def test_default_is_latest_week_with_cache_header(client, shots):
    r = client.get(f"/api/competitors/lp-media/{LP}")
    assert r.status_code == 200 and r.data == JPEG_NEW and r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "private, max-age=86400"


def test_week_param_selects_the_week(client, shots):
    r = client.get(f"/api/competitors/lp-media/{LP}?week=2026-11-02")
    assert r.status_code == 200 and r.data == JPEG_OLD and r.headers["Cache-Control"] == "private, max-age=86400"
    assert client.get(f"/api/competitors/lp-media/{LP}?week=2026-10-26").status_code == 404


def test_absent_non_hex_and_bad_week_are_404_json(client, shots):
    for url in ("/api/competitors/lp-media/ffffffffffffffff", f"/api/competitors/lp-media/{LP}?week=nope", f"/api/competitors/lp-media/{LP}?week=2026-13-40"):
        r = client.get(url)
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, url
    for bad in ("123", "0123456789abcdeg", "0123456789ABCDEF", "0123456789abcdef0", ".."):
        r = client.get(f"/api/competitors/lp-media/{bad}")
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, bad


def test_anonymous_is_401(anon, shots):
    assert anon.get(f"/api/competitors/lp-media/{LP}").status_code == 401


def test_read_only(client, shots):
    for m in ("post", "put", "patch", "delete"):
        assert getattr(client, m)(f"/api/competitors/lp-media/{LP}", json={}).status_code == 405
