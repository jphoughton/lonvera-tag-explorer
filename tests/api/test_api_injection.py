"""Every string parameter is bound, never interpolated: hostile values return empty results or 400, never 500, and change nothing."""
import pytest

from .helpers import get_ok

EVIL = [
    "' OR 1=1; DROP TABLE tracker_rows; --",
    "\"; DELETE FROM ad_daily; --",
    "x' UNION SELECT password FROM pg_shadow --",
    "Robert'); DROP TABLE products;--",
    "%27%20OR%20%271%27%3D%271",
    "1; SELECT pg_sleep(5)",
    "\\'; TRUNCATE family_daily; --",
    "a\x00b",
    "\U0001f600" * 3,
    "${jndi:ldap://x}",
    "%",
    "_",
    "' || (SELECT 1) || '",
]
TABLES = ("tracker_rows", "ad_daily", "products", "family_daily", "ad_metrics", "creative_assets", "ad_tags", "meta_campaigns")


def counts(pg):
    out = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    pg.rollback()
    return out


def hit(client, url):
    r = client.get(url)
    assert r.status_code in (200, 400, 404), (url, r.status_code, r.get_data(as_text=True)[:200])
    return r


@pytest.mark.parametrize("evil", EVIL)
def test_string_params_are_inert(client, pg, evil):
    before = counts(pg)
    from urllib.parse import quote
    e = quote(evil, safe="") if "%" not in evil else evil
    for url in (
        f"/api/products/vita-bull/rollup?level=cluster&cluster={e}", f"/api/products/vita-bull/rollup?level=icp&cluster={e}",
        f"/api/products/vita-bull/rollup?level=pain&cluster={e}&icp={e}", f"/api/products/vita-bull/rollup?level={e}",
        f"/api/products/vita-bull/families?cluster={e}", f"/api/products/vita-bull/families?cluster={e}&icp={e}&pain={e}",
        f"/api/products/vita-bull/families?pain={e}&window=7d&stage=testing",
        f"/api/products/vita-bull/ads?cluster={e}", f"/api/products/vita-bull/ads?icp={e}", f"/api/products/vita-bull/ads?pain={e}",
        f"/api/products/vita-bull/ads?family_key={e}", f"/api/products/vita-bull/ads?campaign_id={e}", f"/api/products/vita-bull/ads?family_key={e}&campaign_id={e}&window=30d",
        f"/api/products/beef-liver/rollup?level=pain&cluster={e}&icp={e}", f"/api/products/beef-liver/families?cluster={e}",
        f"/api/products/{e}/rollup", f"/api/products/{e}/families", f"/api/products/{e}/ads", f"/api/products/{e}/campaigns",
        f"/api/products/{e}/unmatched", f"/api/products/{e}/stage-daily", f"/api/products/{e}/tree",
        f"/api/products/vita-bull/campaigns?window={e}", f"/api/products/vita-bull/rollup?window={e}", f"/api/products/vita-bull/rollup?from={e}&to={e}",
        f"/api/products/vita-bull/rollup?window=7d&compare={e}", f"/api/products/vita-bull/rollup?window=7d&cfrom={e}&cto={e}",
        f"/api/products/vita-bull/rollup?stage={e}", f"/api/products/vita-bull/tree?root={e}", f"/api/products/vita-bull/tree?depth={e}",
        f"/api/products/vita-bull/stage-daily?days={e}", f"/api/ads/{e}", f"/media/{e}", f"/api/products?window={e}",
    ):
        hit(client, url)
    # rows filtered by a bogus tag value are empty, not "everything"
    for url in (f"/api/products/vita-bull/families?cluster={e}", f"/api/products/vita-bull/ads?cluster={e}"):
        if evil not in ("%", "_"):
            r = client.get(url)
            assert (r.status_code == 400) if "\x00" in evil else (r.status_code == 200 and r.get_json()["rows"] == []), url   # NUL bytes -> 400
    r = client.get(f"/api/products/vita-bull/rollup?level=icp&cluster={e}")
    assert (r.status_code == 400) if "\x00" in evil else (r.status_code == 200 and r.get_json()["rows"] == [])
    assert counts(pg) == before


def test_like_wildcards_are_not_patterns(client):
    """'%' and '_' are literal characters in equality filters, not LIKE wildcards."""
    for wild in ("%25", "_", "Alpha%25", "Alph_ Cluster", "%25Cluster"):
        assert get_ok(client, f"/api/products/vita-bull/families?cluster={wild}")["rows"] == []
        assert get_ok(client, f"/api/products/vita-bull/ads?cluster={wild}")["rows"] == []
        assert get_ok(client, f"/api/products/vita-bull/rollup?level=icp&cluster={wild}")["rows"] == []


def test_overlong_values_and_odd_encodings(client):
    long = "A" * 5000
    assert hit(client, f"/api/products/vita-bull/families?cluster={long}").status_code == 200
    assert hit(client, "/api/products/vita-bull/families?cluster=%ff%fe").status_code in (200, 400)
    assert hit(client, "/api/products/vita-bull/families?cluster=a&cluster=b").status_code == 200       # repeated param -> first value wins
    assert client.get("/api/products/vita-bull/rollup?window=7d&window=bogus").status_code == 200
