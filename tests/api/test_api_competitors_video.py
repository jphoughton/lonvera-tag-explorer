"""GET /api/competitors/video/<library_id> (app/competitors_media.py): full body, HTTP Range (206 slices with the right headers, suffix and open-ended ranges, 416 for a bad range), 404 absent / non-digit,
401 anonymous, read-only; and video_url in the competitors payload (null without a stored video, present with one, only for video ads, null when the table is absent)."""
import pytest

DATA = bytes(range(256)) * 4            # 1024 bytes, every byte value known
W1 = "2026-11-16"


@pytest.fixture()
def vid(pg):
    pg.execute("DELETE FROM competitor_ad_video")
    pg.execute("INSERT INTO competitor_ad_video (library_id, content_type, duration_s, width, height, bytes, size_bytes) VALUES ('123456789012345','video/mp4',12,360,640,%s,%s)", (DATA, len(DATA)))
    pg.commit()
    yield
    pg.execute("DELETE FROM competitor_ad_video")
    pg.commit()


def get(client, rng=None, lid="123456789012345"):
    return client.get(f"/api/competitors/video/{lid}", headers={"Range": rng} if rng else {})


def test_full_response_headers(client, vid):
    r = get(client)
    assert r.status_code == 200 and r.data == DATA and r.mimetype == "video/mp4"
    assert r.headers["Accept-Ranges"] == "bytes" and r.headers["Content-Length"] == "1024" and r.headers["Cache-Control"] == "private, max-age=86400"


def test_range_requests_return_206_with_the_right_slice(client, vid):
    r = get(client, "bytes=0-99")
    assert r.status_code == 206 and r.data == DATA[0:100] and r.headers["Content-Range"] == "bytes 0-99/1024" and r.headers["Content-Length"] == "100" and r.headers["Accept-Ranges"] == "bytes"
    r = get(client, "bytes=1000-")
    assert r.status_code == 206 and r.data == DATA[1000:] and r.headers["Content-Range"] == "bytes 1000-1023/1024"
    r = get(client, "bytes=-24")                                                         # suffix: the last 24 bytes (Safari asks for the tail)
    assert r.status_code == 206 and r.data == DATA[-24:] and r.headers["Content-Range"] == "bytes 1000-1023/1024"
    r = get(client, "bytes=1020-5000")                                                   # an end past the file is clamped
    assert r.status_code == 206 and r.data == DATA[1020:] and r.headers["Content-Range"] == "bytes 1020-1023/1024"
    r = get(client, "bytes=0-0")                                                         # Safari probes with 0-1
    assert r.status_code == 206 and r.data == DATA[:1] and r.headers["Content-Length"] == "1"
    assert get(client, "bytes=-5000").data == DATA                                       # a suffix longer than the file is the whole file (206)


def test_unsatisfiable_ranges_are_416(client, vid):
    for rng in ("bytes=2000-3000", "bytes=1024-", "bytes=500-100", "bytes=-0", "bytes=-"):
        r = get(client, rng)
        assert r.status_code == 416 and r.headers["Content-Range"] == "bytes */1024", rng
    assert get(client, "items=0-5").status_code == 200                                    # other units are ignored, the whole file is served


def test_absent_and_non_digit_ids_are_404_json(client, vid):
    assert get(client, lid="999").status_code == 404 and get(client, lid="999").get_json() == {"error": "not found"}
    for bad in ("12ab", "abc", "1%202", "..", "123456789012345.mp4"):
        r = get(client, lid=bad)
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, bad


def test_anonymous_is_401(anon, vid):
    assert anon.get("/api/competitors/video/123456789012345").status_code == 401


def test_read_only(client, vid):
    for m in ("post", "put", "patch", "delete"):
        assert getattr(client, m)("/api/competitors/video/123456789012345", json={}).status_code == 405


# ---- video_url in the competitors payload ----
@pytest.fixture()
def ads(pg):
    pg.execute("DELETE FROM competitor_ads"); pg.execute("DELETE FROM competitor_brands"); pg.execute("DELETE FROM competitor_ad_video")
    pg.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match, last_ok_at) VALUES ('Acme','capsules',ARRAY['acme'],'acme', now())")
    for lid, days, fmt in (("111", 200, "video"), ("222", 150, "video"), ("333", 120, "image")):
        pg.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, variant_count, format, primary_text, hook, landing_url, concept_key, seen_on) "
                   "VALUES (%s,%s,'Acme',%s::date - %s,%s,1,%s,%s,%s,'acme.com',%s,%s)", (W1, lid, W1, days, days, fmt, f"hook {lid}", f"hook {lid}", f"Acme:{lid}", W1))
    pg.commit()
    yield pg
    for t in ("competitor_ads", "competitor_brands", "competitor_ad_video"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


def cards(client):
    d = client.get("/api/competitors").get_json()
    g = client.get("/api/competitors/Acme").get_json()
    return {a["library_id"]: a for a in d["spotlight"]}, {a["library_id"]: a for a in g["gallery"]}, {t["library_id"]: t for t in d["brands"][0]["thumbs"]}


def test_video_url_is_null_without_a_stored_video(client, ads):
    sp, gal, th = cards(client)
    assert sp and all(a["video_url"] is None for a in sp.values()) and all(a["video_url"] is None for a in gal.values()) and all(t["video_url"] is None for t in th.values())


def test_video_url_present_only_for_a_video_ad_with_a_stored_copy(client, ads):
    for lid in ("111", "333"):                                                          # 333 is an image ad: a stray row never yields a video_url
        ads.execute("INSERT INTO competitor_ad_video (library_id, content_type, bytes, size_bytes) VALUES (%s,'video/mp4',%s,3)", (lid, b"abc"))
    ads.commit()
    sp, gal, th = cards(client)
    assert gal["111"]["video_url"] == "/api/competitors/video/111" and gal["222"]["video_url"] is None and gal["333"]["video_url"] is None
    assert sp["111"]["video_url"] == "/api/competitors/video/111" and th["111"]["video_url"] == "/api/competitors/video/111"
    assert client.get(gal["111"]["video_url"]).data == b"abc"


def test_video_url_is_null_and_nothing_errors_when_the_table_is_absent(client, ads):
    ads.execute("ALTER TABLE competitor_ad_video RENAME TO competitor_ad_video_off")
    ads.commit()
    try:
        r = client.get("/api/competitors/Acme")
        assert r.status_code == 200 and all(a["video_url"] is None for a in r.get_json()["gallery"])
        assert client.get("/api/competitors").status_code == 200
    finally:
        ads.execute("ALTER TABLE competitor_ad_video_off RENAME TO competitor_ad_video")
        ads.commit()
