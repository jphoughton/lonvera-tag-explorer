"""QA round 2: strict integer parsing (no Unicode digits), copy_only_line, /media ETag + ranges, the `scope` field, and that the ad queries stay
restricted to the selection's Meta families (the plan-level guard behind the slow cluster + icp drills)."""
import re

import pytest

from app import queries as q

from .helpers import get_ok

UNICODE_DIGITS = ["%C2%B2", "%C2%B3", "%E2%91%A0", "%D9%A3", "%EF%BC%91", "1%C2%B2", "%E0%A5%A7"]      # ², ³, ①, Arabic-Indic 3, fullwidth 1, 1², Devanagari 1
BAD_INTS = UNICODE_DIGITS + ["", "-1", "%2B1", "1.0", "1e1", "0x1", "12345", "abc"]       # (surrounding spaces are stripped like every other parameter)


# ------------------------------------------------------------------------------------------------------------------ (1) integers
@pytest.mark.parametrize("bad", BAD_INTS)
def test_tree_depth_rejects_everything_but_small_ascii_integers(client, bad):
    r = client.get(f"/api/products/vita-bull/tree?depth={bad}")
    if bad == "":                                  # an empty value is the default (0)
        assert r.status_code == 200
        return
    assert r.status_code == 400 and r.get_json()["error"] == "depth must be 0, 1, 2 or 3", bad


@pytest.mark.parametrize("bad", [b for b in BAD_INTS if b != ""])
def test_stage_daily_days_rejects_bad_integers(client, bad):
    r = client.get(f"/api/products/vita-bull/stage-daily?days={bad}")
    assert r.status_code == 400 and "days must be an integer from 1 to 366" in r.get_json()["error"], bad


@pytest.mark.parametrize("bad", BAD_INTS)
@pytest.mark.parametrize("url", ["/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/tree?depth=1",
                                 "/api/products/vita-bull/ads", "/api/products/vita-bull/campaigns", "/api/products?envelope=1"])
def test_spark_days_rejects_bad_integers(client, url, bad):
    sep = "&" if "?" in url else "?"
    r = client.get(f"{url}{sep}spark=spend&spark_days={bad}")
    if bad == "":
        assert r.status_code == 200                # empty = the default 30
        return
    assert r.status_code == 400 and "spark_days must be an integer from 1 to 60" in r.get_json()["error"], (url, bad)


@pytest.mark.parametrize("good,expect", [("0", 0), ("3", 3), ("0003", 3)])
def test_tree_depth_accepts_ascii(client, good, expect):
    assert get_ok(client, f"/api/products/vita-bull/tree?depth={good}")["depth"] == expect


def test_integer_ranges(client):
    assert client.get("/api/products/vita-bull/tree?depth=4").status_code == 400
    assert client.get("/api/products/vita-bull/stage-daily?days=0").status_code == 400
    assert client.get("/api/products/vita-bull/stage-daily?days=367").status_code == 400
    assert client.get("/api/products/vita-bull/stage-daily?days=366").status_code == 200
    assert client.get("/api/products/vita-bull/families?spark=spend&spark_days=0").status_code == 400
    assert client.get("/api/products/vita-bull/families?spark=spend&spark_days=61").status_code == 400
    assert get_ok(client, "/api/products/vita-bull/families?spark=spend&spark_days=60")["total"]["spark"].__len__() == 60


@pytest.mark.parametrize("bad", UNICODE_DIGITS)
def test_path_ids_and_dates_reject_unicode_digits(client, bad):
    for url in (f"/api/ads/{bad}", f"/media/{bad}", f"/api/ads/{bad}/preview"):
        assert client.get(url).status_code == 404, url
    assert client.get(f"/api/products/vita-bull/rollup?from={bad}026-10-01&to=2026-10-02").status_code == 400
    assert client.get("/api/products/vita-bull/rollup?from=%D9%A2%D9%A0%D9%A2%D9%A6-10-01&to=2026-10-02").status_code == 400      # ٢٠٢٦-10-01


def test_huge_path_ids_are_a_clean_404(client):
    assert client.get("/api/ads/99999999999999999999999").status_code == 404
    assert client.get("/api/ads/9999999999999999").status_code == 404                 # fits the route, not an id: 404 from the lookup, never a 500
    assert client.get("/media/9999999999999999").status_code == 404


# ------------------------------------------------------------------------------------------------------------------ (3) copy_only_line
LINES = [{"line_code": "L27", "icp": "ICP Two", "pain_point": "Pain C", "emotional_trigger": "Relief"},
         {"line_code": "L28", "icp": "ICP Three", "pain_point": "Pain D", "emotional_trigger": None}]


def test_copy_only_resolves_codes_and_full_text():
    full = "ICP Two - Pain C (Relief)"
    assert q._copy_only("L27", LINES) == {"line_code": "L27", "label": full}
    assert q._copy_only(" l27 ", LINES) == {"line_code": "L27", "label": full}                       # code match ignores case and spaces
    assert q._copy_only(full, LINES) == {"line_code": "L27", "label": full}
    assert q._copy_only("L28", LINES) == {"line_code": "L28", "label": "ICP Three - Pain D"}
    assert q._copy_only("L99", LINES) == {"line_code": None, "label": "L99"}                         # unknown code stays as typed
    assert q._copy_only("Something else", LINES) == {"line_code": None, "label": "Something else"}
    assert q._copy_only("  ", LINES) is None and q._copy_only(None, LINES) is None


def test_ad_detail_copy_only_line_http(client, pg):
    row = pg.execute("SELECT t.id FROM ad_tags a JOIN tracker_rows t ON t.id = a.tracker_row_id WHERE a.copy_only_line IS NOT NULL LIMIT 1").fetchone()[0]
    d = get_ok(client, f"/api/ads/{row}")
    code = d["tag"]["copy_only_line"]["line_code"]
    assert re.fullmatch(r"L\d+", code) and d["tag"]["copy_only_line"]["label"] == "ICP Two - Pain C (Relief)"       # stored as text: resolved to its code
    pg.execute("UPDATE ad_tags SET copy_only_line = %s WHERE tracker_row_id = %s AND is_current", (code, row))
    pg.commit()
    try:
        d = get_ok(client, f"/api/ads/{row}")                                                                         # stored as the code: same answer
        assert d["tag"]["copy_only_line"] == {"line_code": code, "label": "ICP Two - Pain C (Relief)"}
    finally:
        pg.execute("UPDATE ad_tags SET copy_only_line = 'ICP Two - Pain C (Relief)' WHERE tracker_row_id = %s AND is_current", (row,))
        pg.commit()


# ------------------------------------------------------------------------------------------------------------------ (4) /media
def test_media_etag_304_and_ranges(client, pg):
    aid, size = pg.execute("SELECT id, length(bytes) FROM creative_assets WHERE kind = 'thumb' AND family_key = 'vb-fam-a'").fetchone()
    r = client.get(f"/media/{aid}")
    assert r.status_code == 200 and r.mimetype == "image/png" and len(r.data) == size
    etag = r.headers["ETag"]
    assert etag.startswith('"') and r.headers["Accept-Ranges"] == "bytes" and "max-age" in r.headers["Cache-Control"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    n = client.get(f"/media/{aid}", headers={"If-None-Match": etag})
    assert n.status_code == 304 and n.data == b"" and n.headers["ETag"] == etag and "max-age" in n.headers["Cache-Control"]
    assert client.get(f"/media/{aid}", headers={"If-None-Match": '"nope"'}).status_code == 200
    assert client.get(f"/media/{aid}", headers={"If-None-Match": f'"zzz", {etag}'}).status_code == 304
    part = client.get(f"/media/{aid}", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.data) == 10 and part.headers["Content-Range"] == f"bytes 0-9/{size}"
    assert part.data == r.data[:10]
    assert client.get(f"/media/{aid}", headers={"Range": f"bytes={size + 5}-"}).status_code == 416
    assert client.get("/media/9999999").status_code == 404
    assert client.get("/media/9999999", headers={"If-None-Match": etag}).status_code == 404


def test_media_etag_follows_the_content(client, pg):
    aid = pg.execute("SELECT id FROM creative_assets WHERE family_key = 'vb-fam-b'").fetchone()[0]
    first = client.get(f"/media/{aid}").headers["ETag"]
    old = pg.execute("SELECT bytes FROM creative_assets WHERE id = %s", (aid,)).fetchone()[0]
    pg.execute("UPDATE creative_assets SET bytes = %s WHERE id = %s", (bytes(old) + b"\x00", aid))
    pg.commit()
    try:
        assert client.get(f"/media/{aid}").headers["ETag"] != first
        assert client.get(f"/media/{aid}", headers={"If-None-Match": first}).status_code == 200
    finally:
        pg.execute("UPDATE creative_assets SET bytes = %s WHERE id = %s", (old, aid))
        pg.commit()


# ------------------------------------------------------------------------------------------------------------------ (5) scope
TR, BOTH, UM = "tracker", "tracker+unmatched", "unmatched"
SCOPES = [
    ("/api/products?envelope=1", "scope", TR), ("/api/products/vita-bull/rollup", "scope", TR), ("/api/products/vita-bull/rollup?level=icp&cluster=Alpha Cluster", "scope", TR),
    ("/api/products/vita-bull/families", "scope", TR), ("/api/products/vita-bull/tree?depth=2", "scope", TR),
    ("/api/products/vita-bull/ads", "scope", BOTH), ("/api/products/vita-bull/ads?window=30d", "scope", BOTH),
    ("/api/products/vita-bull/ads?campaign_id=C100", "scope", BOTH),
    ("/api/products/vita-bull/ads?cluster=Alpha Cluster", "scope", TR), ("/api/products/vita-bull/ads?family_key=vb-fam-a", "scope", TR),
    ("/api/products/vita-bull/ads?campaign_id=C100&cluster=Alpha Cluster", "scope", TR),
    ("/api/products/vita-bull/campaigns", "scope", BOTH), ("/api/products/vita-bull/unmatched", "scope", UM),
    ("/api/products/vita-bull/trend", "data_scope", TR), ("/api/products/vita-bull/trend?cluster=Alpha Cluster", "data_scope", TR),
    ("/api/products/vita-bull/trend?family_key=vb-fam-a", "data_scope", TR), ("/api/products/vita-bull/trend?campaign_id=C100", "data_scope", BOTH),
    ("/api/products/vita-bull/trend?campaign_id=C100&icp=ICP One", "data_scope", TR), ("/api/ads/1000", "scope", TR),
]


@pytest.mark.parametrize("url,key,want", SCOPES)
@pytest.mark.parametrize("active", ["", "&active=1"])
def test_scope_field(client, url, key, want, active):
    sep = "&" if "?" in url else "?"
    d = get_ok(client, url + (sep + active.lstrip("&") if active else ""))
    assert d[key] == want
    if key == "data_scope":
        assert isinstance(d["scope"], dict)                       # the trend's own filter object keeps its name


def test_scope_header_on_bare_arrays(client):
    r = client.get("/api/products")
    assert r.headers["X-Data-Scope"] == TR and isinstance(r.get_json(), list)
    r = client.get("/api/products/vita-bull/stage-daily")
    assert r.headers["X-Data-Scope"] == BOTH and isinstance(r.get_json(), list)


# ------------------------------------------------------------------------------------------------------------------ plan-level guards
def captured_sql(client, monkeypatch, url):
    seen = []
    real_rows = q.rows_of
    monkeypatch.setattr(q, "rows_of", lambda conn, sql, params=None: (seen.append(sql), real_rows(conn, sql, params))[1])
    assert client.get(url).status_code == 200
    return [re.sub(r"--[^\n]*", "", s) for s in seen]


@pytest.mark.parametrize("url", ["/api/products/vita-bull/ads?cluster=Alpha Cluster&icp=ICP One&window=30d",
                                 "/api/products/vita-bull/ads?cluster=Alpha Cluster&icp=ICP One&window=launch&stage=testing,scaling",
                                 "/api/products/vita-bull/ads?window=180d&compare=prev", "/api/products/vita-bull/ads?window=launch"])
def test_ads_read_only_the_selected_families(client, monkeypatch, url):
    main = next(s for s in captured_sql(client, monkeypatch, url) if "o AS MATERIALIZED" in s)
    assert "fkeys AS MATERIALIZED" in main and "tracker_family_key = ANY(%(fks)s)" in main
    scans = [m.start() for m in re.finditer(r"FROM ad_daily ad\b", main)]
    assert scans or url.endswith("window=launch")                                                  # since launch with every stage reads lifetime rows only
    for at in scans:
        assert "meta_family_key IN (SELECT meta_family_key FROM fkeys)" in main[at:at + 120], main[at:at + 160]      # never a whole-window scan of ad_daily
    assert "tr AS" not in main and "ad_tags" not in main                                              # the selection is resolved first, in its own statement


def test_windowed_ctes_are_materialized(client, monkeypatch):
    for url in ("/api/products/vita-bull/families?window=launch&stage=testing,scaling&cluster=Alpha Cluster&icp=ICP One",
                "/api/products/vita-bull/rollup?level=pain&cluster=Alpha Cluster&icp=ICP One&window=launch&stage=testing,scaling"):
        main = next(s for s in captured_sql(client, monkeypatch, url) if "win AS" in s)
        assert "win AS MATERIALIZED" in main and "fr AS MATERIALIZED" in main and "tr AS MATERIALIZED" in main, url
