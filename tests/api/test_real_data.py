"""Internal-consistency checks on the REAL database (skipped when it is empty, unreachable or not migrated yet).

CONTRACT v1.5: the Graph API is the only source of numbers and there is no tie-out to the Google Sheet. So these tests assert that the API agrees
with itself at every level (tree == rollup == families == ads), that totals are the sum of rows, shares add up to 1, stage lists partition the
unfiltered total, and that latency stays inside the budget. Nothing here hardcodes a business figure; the only structural counts are the
Vita Bull tracker rows (1,464) and unique ads (1,434, or 1,435 while a blank-family row shares a name with a real family).
"""
import os
import random
import time
from urllib.parse import quote

import psycopg
import pytest

from app import create_app

REAL_URL = os.environ.get("TAGEXP_REAL_DB", "postgresql://localhost/tagexp_dev")
BUDGET_MS = 300


def _usable() -> bool:
    try:
        with psycopg.connect(REAL_URL, connect_timeout=3) as c:
            n = c.execute("SELECT count(*) FROM ad_metrics").fetchone()[0]
            cols = {r[0] for r in c.execute("""SELECT table_name || '.' || column_name FROM information_schema.columns
                WHERE (table_name, column_name) IN (('family_daily','impressions'), ('ad_metrics','impressions_launch'), ('meta_ad_lifetime','link_clicks'))""")}
            return n > 0 and len(cols) == 3
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _usable(), reason="real database empty, unreachable, or migrations 003/004 not applied")


@pytest.fixture(scope="module")
def rc():
    app = create_app({"DATABASE_URL": REAL_URL, "APP_PASSWORD": "pw", "SECRET_KEY": "k", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4})
    c = app.test_client()
    assert c.post("/api/login", json={"password": "pw"}).status_code == 204
    yield c
    app.extensions["pool"].close()


def get(rc, url, budget=3000):          # consistency tests only guard against pathologies; test_latency_budget enforces BUDGET_MS
    t = time.perf_counter()
    r = rc.get(url)
    ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:300])
    assert ms < budget, f"{url} took {ms:.0f} ms (budget {budget} ms)"
    return r.get_json()


def q(s):
    return quote(s, safe="")


def n0(x):
    """unknown traffic is null in the API; sums treat it as nothing"""
    return x or 0


def strip_share(m):
    import copy
    m = copy.deepcopy(m)
    m.pop("spend_share", None)
    if "cmp" in m:
        m["cmp"].pop("spend_share", None)
        m["delta"].pop("spend_share_pp", None)
    return m


def lifetime_is_api() -> bool:
    with psycopg.connect(REAL_URL) as c:
        n, bad = c.execute("SELECT count(*), count(*) FILTER (WHERE source <> 'meta_api') FROM meta_ad_lifetime").fetchone()
    return n > 0 and bad == 0


def daily_in_sync() -> bool:
    """/ads reads ad_daily and /families reads family_daily: equal only after build_metrics ran following the last pull."""
    with psycopg.connect(REAL_URL) as c:
        fd = c.execute("SELECT COALESCE(sum(spend), 0) FROM family_daily").fetchone()[0]
        ad = c.execute("""SELECT COALESCE(sum(d.spend), 0) FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key
                          WHERE m.tracker_family_key IS NOT NULL""").fetchone()[0]
        running = c.execute("SELECT count(*) FROM etl_runs WHERE status = 'running' AND started_at > now() - interval '12 hours'").fetchone()[0]
    return abs(fd - ad) <= 1 and running == 0


EXTRAS = ["", "window=30d", "window=7d&stage=testing,scaling", "window=14d&compare=prev", "window=30d&compare=7d&stage=scaling,unclassified"]


# ------------------------------------------------------------------------------------------------------ structure
def test_vita_bull_structure(rc):
    p = next(x for x in get(rc, "/api/products") if x["slug"] == "vita-bull")
    assert p["rows"] == 1464
    assert p["m"]["unique_ads"] in (1434, 1435)
    assert p["tag_set"] == "linemap" and p["tag_label"] == "Line-mapped tags"
    tree = get(rc, "/api/products/vita-bull/tree?root=cluster&depth=3", budget=600)
    assert sum(1 for r in tree["rows"] if r["level"] == 2) >= 37             # every Communication Overview line is listed, with or without ads
    assert sum(1 for r in tree["rows"] if r["level"] == 3 and r["counted_here"]) == p["m"]["unique_ads"]      # every family has exactly one first row


def test_all_products_listed(rc):
    prods = get(rc, "/api/products")
    assert [p["slug"] for p in prods][:3] == ["vita-bull", "beef-liver", "boneflex"]
    assert sum(p["m"]["spend_share"] or 0 for p in prods) == pytest.approx(1.0, abs=1e-4)


# ------------------------------------------------------------------------------------------------------ tree == rollup == families
@pytest.mark.parametrize("extra", EXTRAS)
def test_tree_equals_rollup_and_families(rc, extra):
    for slug in ("vita-bull", "beef-liver"):
        t = get(rc, f"/api/products/{slug}/tree?root=cluster&depth=3&{extra}", budget=900)
        roll = get(rc, f"/api/products/{slug}/rollup?level=cluster&{extra}")
        assert t["total"]["m"] == roll["total"]["m"]
        rows = t["rows"]
        lvl0 = [r for r in rows if r["level"] == 0]
        assert [r["m"] for r in lvl0] == [r["m"] for r in roll["rows"]]
        kids = lambda key: [r for r in rows if r["parent_key"] == key]      # noqa: E731
        rng = random.Random(7)
        for c in rng.sample(lvl0, min(3, len(lvl0))):                        # a sample of clusters, all their descendants
            icp_roll = get(rc, f"/api/products/{slug}/rollup?level=icp&cluster={q(c['cluster'])}&{extra}")["rows"]
            lvl1 = kids(c["key"])
            assert [r["label"] for r in lvl1] == [r["label"] for r in icp_roll]
            for t1, r1 in zip(lvl1, icp_roll):
                assert strip_share(t1["m"]) == strip_share(r1["m"]) and t1["child_count"] == r1["child_count"]
                pain_roll = get(rc, f"/api/products/{slug}/rollup?level=pain&cluster={q(c['cluster'])}&icp={q(t1['icp'])}&{extra}")["rows"]
                lvl2 = kids(t1["key"])
                assert [r["label"] for r in lvl2] == [r["label"] for r in pain_roll]
                for t2, r2 in zip(lvl2, pain_roll):
                    assert strip_share(t2["m"]) == strip_share(r2["m"])
                    fam_roll = get(rc, f"/api/products/{slug}/families?cluster={q(c['cluster'])}&icp={q(t1['icp'])}&pain={q(t2['pain'])}&{extra}", budget=600)["rows"]
                    lvl3 = kids(t2["key"])
                    assert [r["family_key"] for r in lvl3] == [r["family_key"] for r in fam_roll]
                    for t3, r3 in zip(lvl3, fam_roll):
                        assert strip_share(t3["m"]) == strip_share(r3["m"]) and t3["counted_here"] == r3["counted_here"]


@pytest.mark.parametrize("extra", EXTRAS)
def test_totals_are_sums_of_rows_and_shares_add_up(rc, extra):
    cmp = "compare" in extra
    for slug in ("vita-bull", "beef-liver", "boneflex"):
        d = get(rc, f"/api/products/{slug}/rollup?level=cluster&{extra}")
        t, rows = d["total"]["m"], [r["m"] for r in d["rows"]]
        for k in ("spend", "purchases", "revenue", "impressions", "link_clicks", "live_now", "active_ads"):
            assert n0(t[k]) == pytest.approx(sum(n0(r[k]) for r in rows), abs=0.02 + 0.005 * len(rows)), (slug, k)
        if t["spend"]:
            assert sum(r["spend_share"] for r in rows) == pytest.approx(1.0, abs=1e-4)
            assert t["spend_share"] == 1.0
        if cmp and t["cmp"]["spend"]:
            assert sum(r["cmp"]["spend_share"] for r in rows) == pytest.approx(1.0, abs=1e-4)
            assert t["cmp"]["spend"] == pytest.approx(sum(r["cmp"]["spend"] for r in rows), abs=0.02 + 0.005 * len(rows))
        if t["spend"] and t["revenue"]:
            assert t["roas"] == pytest.approx(t["revenue"] / t["spend"], abs=1e-3)
    fams = get(rc, f"/api/products/vita-bull/families?{extra}", budget=600)
    assert fams["total"]["m"]["spend"] == pytest.approx(sum(r["m"]["spend"] for r in fams["rows"]), abs=0.02 + 0.005 * len(fams["rows"]))
    assert sum(r["m"]["spend_share"] or 0 for r in fams["rows"]) == pytest.approx(1.0 if fams["total"]["m"]["spend"] else 0.0, abs=1e-3)
    assert fams["total"]["m"]["unique_ads"] == len(fams["rows"])


# ------------------------------------------------------------------------------------------------------ stage lists
@pytest.mark.parametrize("window", ["window=launch", "window=30d", "window=7d"])
def test_stage_lists_partition_the_unfiltered_total(rc, window):
    whole = get(rc, f"/api/products/vita-bull/rollup?{window}")["total"]["m"]
    parts = [get(rc, f"/api/products/vita-bull/rollup?{window}&stage={s}")["total"]["m"] for s in ("testing", "scaling", "unclassified")]
    for k in ("spend", "revenue", "impressions", "link_clicks"):
        assert sum(n0(p[k]) for p in parts) == pytest.approx(n0(whole[k]), abs=0.05), (window, k)
    assert sum(p["purchases"] for p in parts) == pytest.approx(whole["purchases"], abs=2 + 0.5 * whole["unique_ads"]), window     # per-family rounding
    pair = get(rc, f"/api/products/vita-bull/rollup?{window}&stage=testing,scaling")["total"]["m"]
    assert pair["spend"] == pytest.approx(parts[0]["spend"] + parts[1]["spend"], abs=0.05)
    assert get(rc, f"/api/products/vita-bull/rollup?{window}&stage=testing,scaling,unclassified")["total"]["m"] == whole
    assert whole["stage"]["testing"]["spend"] + whole["stage"]["scaling"]["spend"] + whole["stage"]["unclassified"]["spend"] == pytest.approx(whole["spend"], abs=0.05)


def test_fixed_7d_fields_equal_window_7d(rc):
    w = {r["family_key"]: r["m"] for r in get(rc, "/api/products/vita-bull/families?window=7d", budget=600)["rows"]}
    for k, m in w.items():
        assert m["spend"] == pytest.approx(m["spend_7d"], abs=0.02), k
        assert m["purchases"] == pytest.approx(m["purchases_7d"], abs=0.02), k


# ------------------------------------------------------------------------------------------------------ compare + traffic sanity
def test_compare_math_and_traffic_sanity(rc):
    d = get(rc, "/api/products/vita-bull/families?window=14d&compare=prev", budget=600)
    for r in d["rows"][:300]:
        m = r["m"]
        assert m["delta"]["spend"] == pytest.approx(m["spend"] - m["cmp"]["spend"], abs=0.011)
        assert (m["delta"]["spend_pct"] is None) == (m["cmp"]["spend"] == 0)
        if m["impressions"] is None:
            assert m["ctr"] is None and m["cpm"] is None                            # unknown impressions -> every ratio derived from them is null
        if m["ctr"] is not None:
            assert 0 <= m["ctr"] <= 1 and m["impressions"] >= n0(m["link_clicks"]) * 0.999
        if m["cpc"] is not None:
            assert m["cpc"] >= 0
        if m["ctr"] is not None and m["cmp"]["ctr"] is not None:
            assert m["delta"]["ctr_pp"] == pytest.approx((m["ctr"] - m["cmp"]["ctr"]) * 100, abs=2e-3)
    t = d["total"]["m"]
    assert t["cmp"]["spend"] == pytest.approx(sum(r["m"]["cmp"]["spend"] for r in d["rows"]), abs=0.02 + 0.005 * len(d["rows"]))
    assert d["compare_info"]["days"] == 14 and d["window_info"]["days"] == 14


# ------------------------------------------------------------------------------------------------------ ads == families
@pytest.mark.parametrize("window", ["window=30d", "window=7d&compare=prev", "window=launch"])
def test_ad_rows_add_up_to_family_figures(rc, window):
    if not daily_in_sync():
        pytest.skip("ETL pull in progress or family_daily older than ad_daily: run `python -m etl.build_metrics` after the pull")
    if "launch" in window and not lifetime_is_api():
        pytest.skip("meta_ad_lifetime still holds sheet-era rows: since-launch is not final until etl.lifetime_pull ran")
    ads = get(rc, f"/api/products/vita-bull/ads?{window}", budget=2500)
    fams = get(rc, f"/api/products/vita-bull/families?{window}", budget=600)
    tracker_side = [r for r in ads["rows"] if r["on_tracker"]]
    f = fams["total"]["m"]
    tol = 1.0 if "launch" not in window else 0.5 * len(fams["rows"]) + 1
    assert sum(r["spend"] for r in tracker_side) == pytest.approx(f["spend"], abs=1.0)
    assert sum(r["revenue"] for r in tracker_side) == pytest.approx(f["revenue"], abs=1.0)
    assert sum(r["purchases"] for r in tracker_side) == pytest.approx(f["purchases"], abs=tol)
    assert sum(n0(r["impressions"]) for r in tracker_side) == pytest.approx(n0(f["impressions"]), abs=1)
    assert sum(n0(r["link_clicks"]) for r in tracker_side) == pytest.approx(n0(f["link_clicks"]), abs=1)
    if "compare" in window:
        assert sum(r["cmp"]["spend"] for r in tracker_side) == pytest.approx(f["cmp"]["spend"], abs=1.0)
    rng = random.Random(3)
    counted = [r for r in fams["rows"] if r["counted_here"] and not r["family_key"].startswith("id:") and r["m"]["spend"] > 0]
    for r in rng.sample(counted, min(25, len(counted))):                       # families drilled the way the UI does (tree row -> /ads)
        one = get(rc, f"/api/products/vita-bull/ads?family_key={q(r['family_key'])}&cluster={q(r['cluster'])}&icp={q(r['icp'])}&pain={q(r['pain'])}&{window}")
        assert one["total"]["spend"] == pytest.approx(r["m"]["spend"], abs=0.02), r["family_key"]
        assert n0(one["total"]["impressions"]) == pytest.approx(n0(r["m"]["impressions"]), abs=1), r["family_key"]
        assert n0(one["total"]["link_clicks"]) == pytest.approx(n0(r["m"]["link_clicks"]), abs=1), r["family_key"]
        assert sum(a["spend_share"] or 0 for a in one["rows"]) == pytest.approx(1.0, abs=1e-3)


def test_ads_launch_stage_list_partitions_per_family(rc):
    if not lifetime_is_api() or not daily_in_sync():
        pytest.skip("needs the API lifetime pull and a rebuilt family_daily")
    top = [r for r in get(rc, "/api/products/vita-bull/families?window=launch", budget=600)["rows"] if r["counted_here"] and r["m"]["spend"] > 500][:8]
    for r in top:
        base = f"/api/products/vita-bull/ads?family_key={q(r['family_key'])}&cluster={q(r['cluster'])}&icp={q(r['icp'])}&pain={q(r['pain'])}&window=launch"
        whole = get(rc, base)["total"]["spend"]
        parts = sum(get(rc, f"{base}&stage={s}")["total"]["spend"] for s in ("testing", "scaling", "unclassified"))
        assert parts == pytest.approx(whole, abs=0.05) == pytest.approx(r["m"]["spend"], abs=0.05), r["family_key"]


# ------------------------------------------------------------------------------------------------------ latency
URLS = [
    "/api/products", "/api/products?window=7d&compare=prev", "/api/products/vita-bull/rollup?level=cluster&window=30d&compare=prev&stage=testing,scaling",
    "/api/products/vita-bull/families", "/api/products/vita-bull/families?window=14d&compare=7d",
    "/api/products/vita-bull/ads?window=30d", "/api/products/vita-bull/campaigns?window=30d&compare=prev", "/api/products/vita-bull/stage-daily?days=90",
    "/api/products/vita-bull/unmatched?window=30d", "/api/products/vita-bull/tree?root=cluster&depth=3",
    "/api/products/vita-bull/tree?root=cluster&depth=3&window=30d&compare=prev&stage=scaling,unclassified",
    "/api/products/beef-liver/tree?root=cluster&depth=3&window=7d", "/api/products/boneflex/families", "/api/products/beef-liver/rollup?level=cluster",
]


@pytest.mark.parametrize("url", URLS)
def test_latency_budget(rc, url):
    get(rc, url, budget=5000)         # warm
    get(rc, url, budget=BUDGET_MS)    # < 300 ms
