"""The in-process response cache: hits, keys, invalidation by the ETL's footprint, no caching while a job runs, auth, TTL, size bound."""
import pytest

from app import respcache

from .conftest import PASSWORD
from .helpers import get_ok

URL = "/api/products/vita-bull/families?window=30d"


@pytest.fixture()
def cclient(app_factory):
    app = app_factory(RESPONSE_CACHE_MB=8)
    c = app.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    c.cache = app.extensions["response_cache"]
    return c


def test_hit_serves_the_same_bytes(cclient):
    a = cclient.get(URL)
    b = cclient.get(URL)
    assert a.status_code == b.status_code == 200 and a.headers["X-Cache"] == "miss" and b.headers["X-Cache"] == "hit"
    assert a.data == b.data and a.mimetype == b.mimetype == "application/json"
    assert b.headers["Cache-Control"] == "no-store" and b.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert cclient.cache.hits == 1 and cclient.cache.misses == 1


def test_key_is_path_query_and_encoding(cclient):
    cclient.get(URL)
    assert cclient.get(URL + "&stage=testing").headers["X-Cache"] == "miss"                       # another query
    assert cclient.get("/api/products/vita-bull/families?window=30d").headers["X-Cache"] == "hit"
    assert cclient.get("/api/products/vita-bull/families?window=30d").headers.get("Content-Encoding") is None
    g1 = cclient.get(URL, headers={"Accept-Encoding": "gzip"})
    assert g1.headers["X-Cache"] == "miss"                                                         # gzip clients have their own entry
    assert cclient.get(URL, headers={"Accept-Encoding": "gzip"}).headers["X-Cache"] == "hit"
    assert cclient.get("/api/products/vita-bull/families?stage=all&window=30d").headers["X-Cache"] == "miss"     # (different text, different key)
    assert cclient.get("/api/products/vita-bull/families?window=30d").headers["X-Cache"] == "hit"


def test_gzip_hit_is_gzip(cclient):
    import gzip
    big = "/api/products/vita-bull/ads?window=launch"
    a = cclient.get(big, headers={"Accept-Encoding": "gzip"})
    b = cclient.get(big, headers={"Accept-Encoding": "gzip"})
    assert b.headers["X-Cache"] == "hit"
    assert a.headers.get("Content-Encoding") == b.headers.get("Content-Encoding")
    raw = b.data if b.headers.get("Content-Encoding") != "gzip" else gzip.decompress(b.data)
    assert raw == (a.data if a.headers.get("Content-Encoding") != "gzip" else gzip.decompress(a.data))


def test_errors_status_and_other_routes_are_not_cached(cclient):
    for _ in range(2):
        assert cclient.get("/api/products/vita-bull/tree?depth=9").status_code == 400
    assert cclient.cache._bytes == 0
    for url in ("/api/status", "/healthz", "/api/products/nope/families"):
        cclient.get(url)
        assert cclient.get(url).headers.get("X-Cache") is None
    assert cclient.get("/media/999999").headers.get("X-Cache") is None


def test_etl_footprint_invalidates(cclient, pg):
    cclient.get(URL)
    assert cclient.get(URL).headers["X-Cache"] == "hit"
    pg.execute("INSERT INTO app_meta (key, value) VALUES ('cache_test', '1')")
    pg.commit()
    try:
        assert cclient.get(URL).headers["X-Cache"] == "miss"                                       # app_meta changed (a refresh stamps it)
        assert cclient.get(URL).headers["X-Cache"] == "hit"
        pg.execute("UPDATE app_meta SET value = '2' WHERE key = 'cache_test'")
        pg.commit()
        assert cclient.get(URL).headers["X-Cache"] == "miss"
    finally:
        pg.execute("DELETE FROM app_meta WHERE key = 'cache_test'")
        pg.commit()
    assert cclient.get(URL).headers["X-Cache"] == "miss"                                           # ... and so does its removal


def test_new_etl_run_invalidates_and_a_running_job_blocks_caching(cclient, pg):
    cclient.get(URL)
    assert cclient.get(URL).headers["X-Cache"] == "hit"
    rid = pg.execute("INSERT INTO etl_runs (job, started_at, status) VALUES ('tracker_sync', now(), 'running') RETURNING id").fetchone()[0]
    pg.commit()
    try:
        for _ in range(2):                                                                         # a job is running: always computed, never stored
            r = cclient.get(URL)
            assert r.status_code == 200 and r.headers.get("X-Cache") is None
        pg.execute("UPDATE etl_runs SET finished_at = now(), status = 'ok' WHERE id = %s", (rid,))
        pg.commit()
        assert cclient.get(URL).headers["X-Cache"] == "miss"                                       # finished: new footprint
        assert cclient.get(URL).headers["X-Cache"] == "hit"
    finally:
        pg.execute("DELETE FROM etl_runs WHERE id = %s", (rid,))
        pg.commit()


def test_data_changes_show_up_after_the_footprint_moves(cclient, pg):
    """an edit alone is not seen (that is the point of the cache); the ETL finishing is"""
    url = "/api/products?envelope=1&window=30d"
    row = pg.execute("SELECT family_key, day, stage FROM family_daily WHERE day >= '2026-10-10' ORDER BY family_key, day, stage LIMIT 1").fetchone()
    edit = "UPDATE family_daily SET spend = spend + %s WHERE (family_key, day, stage) = (%s, %s, %s)"
    before = get_ok(cclient, url)["total"]["m"]["spend"]
    pg.execute(edit, (100, *row))
    pg.commit()
    rid = None
    try:
        assert cclient.get(url).get_json()["total"]["m"]["spend"] == before            # still cached
        rid = pg.execute("INSERT INTO etl_runs (job, started_at, finished_at, status) VALUES ('build_metrics', now(), now(), 'ok') RETURNING id").fetchone()[0]
        pg.commit()
        assert cclient.get(url).get_json()["total"]["m"]["spend"] == pytest.approx(before + 100, abs=0.011)
    finally:
        pg.execute(edit, (-100, *row))
        if rid:
            pg.execute("DELETE FROM etl_runs WHERE id = %s", (rid,))
        pg.commit()


def test_auth_comes_first(app_factory, cclient):
    cclient.get(URL)                                                                                # warm
    anon = cclient.application.test_client()
    assert anon.get(URL).status_code == 401                                                         # a cached answer is not served to a stranger
    assert anon.get(URL).headers.get("X-Cache") is None


def test_ttl_and_size_bound():
    t = [0.0]
    c = respcache.ResponseCache(100, ttl_sec=10, clock=lambda: t[0])
    c.put(("a",), "v1", {"Content-Type": "x"}, b"x" * 60)
    assert c.get(("a",), "v1")[4] == b"x" * 60 and c.get(("a",), "v2") is None                       # another version never hits (and drops the entry)
    c.put(("a",), "v1", {}, b"x" * 60)
    c.put(("b",), "v1", {}, b"y" * 60)                                                              # 120 > 100: the least recently used goes
    assert c.get(("a",), "v1") is None and c.get(("b",), "v1") is not None and c.size_bytes == 60
    t[0] = 11
    assert c.get(("b",), "v1") is None and c.size_bytes == 0                                         # expired
    c.put(("big",), "v1", {}, b"z" * 101)
    assert c.size_bytes == 0                                                                        # never stores more than the whole budget
    off = respcache.ResponseCache(0)
    assert not off.enabled


def test_cache_off_by_default_in_tests(client):
    assert client.get(URL).headers.get("X-Cache") is None
