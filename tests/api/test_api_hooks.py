"""Ad hooks (migration 010): the families table carries the hook of each creative's primary tracker row, the ad detail a full hook block; both are None
without a hook, the API still answers before the table exists, and a hooks load (an etl_runs job) is picked up by the response cache like a tag load."""
import time
from urllib.parse import urlencode

import psycopg
import pytest
from psycopg.rows import dict_row

from app import queries as q

from .conftest import DB_URL, PASSWORD
from .helpers import get_ok

FAMILIES = "/api/products/vita-bull/families"
INSERT = """INSERT INTO ad_hooks (tracker_row_id, hook, spoken, on_screen, hook_source, confidence, note, version, extractor)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


@pytest.fixture()
def hooked(pg):
    """hooks on 1000 (first tracker row of VB-Fam-A), 1001 (its second row) and 1002 (VB-Fam-B); gone again after the test"""
    pg.execute("DELETE FROM ad_hooks")
    pg.execute(INSERT, (1000, "Your doctor will not tell you this about zinc", "So here is the thing about zinc", "ZINC AFTER 40", "both", "high", "clear audio",
                        "hooks-v1-2026-11-05", "claude-sonnet-5-5"))
    pg.execute(INSERT, (1001, "second row hook", None, None, "copy", "low", None, "hooks-v1-2026-11-05", None))
    pg.execute(INSERT, (1002, "Static headline only", None, "STATIC HEADLINE", "on_screen", "medium", None, "hooks-v1-2026-11-05", None))
    pg.commit()
    yield
    pg.execute("DELETE FROM ad_hooks")
    pg.commit()


def family(client, key, url=FAMILIES):
    return next(r for r in get_ok(client, url)["rows"] if r["family_key"] == key)


def test_families_carry_the_hook_of_the_primary_row(client, hooked):
    a = family(client, "vb-fam-a")
    assert a["ad_ids"] == [1000, 1001] and a["primary_id"] == 1000
    assert (a["hook"], a["hook_source"], a["hook_confidence"]) == ("Your doctor will not tell you this about zinc", "both", "high")      # not the 2nd row's hook
    b = family(client, "vb-fam-b")
    assert (b["hook"], b["hook_source"], b["hook_confidence"]) == ("Static headline only", "on_screen", "medium")
    d = family(client, "vb-fam-d")                                                                          # no hook loaded for this one
    assert (d["hook"], d["hook_source"], d["hook_confidence"]) == (None, None, None)
    assert set(a) >= {"hook", "hook_source", "hook_confidence"} and "spoken" not in a                       # the table gets the line, not the whole block


def test_a_group_shows_the_hook_of_its_own_first_row(client, hooked):
    """VB-Fam-A has two tracker rows tagged to different lines: under each pain point the creative is that row's group, and shows that row's hook"""
    sel = {"cluster": "Alpha Cluster", "icp": "ICP One"}
    a = family(client, "vb-fam-a", FAMILIES + "?" + urlencode({**sel, "pain": "Pain A"}))
    assert (a["ad_ids"], a["hook"]) == ([1000], "Your doctor will not tell you this about zinc")
    b = family(client, "vb-fam-a", FAMILIES + "?" + urlencode({**sel, "pain": "Pain B"}))
    assert (b["ad_ids"], b["primary_id"], b["hook"], b["hook_source"], b["hook_confidence"]) == ([1001], 1001, "second row hook", "copy", "low")
    for url in (FAMILIES + "?window=7d&compare=prev", FAMILIES + "?stage=testing", FAMILIES + "?active=1"):      # every variant of the query carries the fields
        rows = get_ok(client, url)["rows"]
        assert rows and all({"hook", "hook_source", "hook_confidence"} <= set(r) for r in rows), url


def test_other_products_are_untouched(client, hooked):
    rows = get_ok(client, "/api/products/beef-liver/families")["rows"]
    assert rows and all(r["hook"] is None and r["hook_source"] is None and r["hook_confidence"] is None for r in rows)


def test_without_hooks_the_fields_are_none(client, pg):
    pg.execute("DELETE FROM ad_hooks")
    pg.commit()
    rows = get_ok(client, FAMILIES)["rows"]
    assert rows and all(r["hook"] is None and r["hook_source"] is None and r["hook_confidence"] is None for r in rows)
    d = get_ok(client, "/api/ads/1000")
    assert "hook" in d and d["hook"] is None


def test_detail_has_the_whole_hook_block(client, hooked):
    d = get_ok(client, "/api/ads/1000")
    assert d["hook"] == {"hook": "Your doctor will not tell you this about zinc", "spoken": "So here is the thing about zinc", "on_screen": "ZINC AFTER 40",
                         "hook_source": "both", "confidence": "high", "note": "clear audio", "version": "hooks-v1-2026-11-05"}
    second = get_ok(client, "/api/ads/1001")["hook"]                                                       # a row of its own, not the creative's primary row
    assert second == {"hook": "second row hook", "spoken": None, "on_screen": None, "hook_source": "copy", "confidence": "low", "note": None,
                      "version": "hooks-v1-2026-11-05"}
    assert get_ok(client, "/api/ads/1003")["hook"] is None                                                 # no hook loaded for this row
    assert get_ok(client, "/api/ads/1000?window=7d&compare=prev")["hook"]["hook"] == d["hook"]["hook"]    # windows do not touch it
    assert get_ok(client, "/api/ads/1000")["tag"]["version"] == "linemap-v2-2026-11-03"                    # and the tag block is as before


def test_before_migration_010_the_api_still_answers(client, hooked, monkeypatch):
    monkeypatch.setattr(q, "_CAPS_AT", time.monotonic() + 3600)                                            # do not re-probe the schema during the test
    monkeypatch.setitem(q._CAPS, "hooks", False)
    rows = get_ok(client, FAMILIES)["rows"]
    assert rows and all(r["hook"] is None for r in rows)
    assert get_ok(client, "/api/ads/1000")["hook"] is None


def test_the_schema_probe_sees_the_table(fx):
    with psycopg.connect(DB_URL, row_factory=dict_row) as c:
        assert q.refresh_caps(c, force=True)["hooks"] is True


@pytest.fixture()
def cclient(app_factory):
    app = app_factory(RESPONSE_CACHE_MB=8)
    c = app.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


def test_a_hooks_load_shows_up_through_the_response_cache(cclient, pg):
    """cached answers do not know about a plain edit; the load_hooks etl_runs job (what etl.load_hooks writes) moves the footprint, exactly like load_tags"""
    pg.execute("DELETE FROM ad_hooks")
    pg.commit()
    rid = None
    try:
        for url, pick in ((FAMILIES, lambda d: next(r for r in d["rows"] if r["family_key"] == "vb-fam-a")["hook"]), ("/api/ads/1000", lambda d: d["hook"])):
            assert pick(get_ok(cclient, url)) is None
            assert cclient.get(url).headers["X-Cache"] == "hit"
        pg.execute(INSERT, (1000, "fresh hook", None, None, "spoken", "high", None, "hooks-v1", None))
        pg.commit()
        assert next(r for r in cclient.get(FAMILIES).get_json()["rows"] if r["family_key"] == "vb-fam-a")["hook"] is None                    # still the cached answer
        rid = pg.execute("INSERT INTO etl_runs (job, started_at, finished_at, status) VALUES ('load_hooks', now(), now(), 'ok') RETURNING id").fetchone()[0]
        pg.commit()
        r = cclient.get(FAMILIES)
        assert r.headers["X-Cache"] == "miss" and next(x for x in r.get_json()["rows"] if x["family_key"] == "vb-fam-a")["hook"] == "fresh hook"
        d = cclient.get("/api/ads/1000")
        assert d.headers["X-Cache"] == "miss" and d.get_json()["hook"]["hook"] == "fresh hook"
        assert cclient.get(FAMILIES).headers["X-Cache"] == "hit"
    finally:
        pg.execute("DELETE FROM ad_hooks")
        if rid:
            pg.execute("DELETE FROM etl_runs WHERE id = %s", (rid,))
        pg.commit()
